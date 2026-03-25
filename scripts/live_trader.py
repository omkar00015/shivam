"""Paper trading mode — runs the full 13-step pipeline on every 15m bar close.

Steps performed on startup:
  1. Create the `signals` table if it does not exist.
  2. Patch StateManager (save_bar signature + hypertable schema mismatch).
  3. Build Orchestrator, run startup().
  4. Load last 500 15m bars from TimescaleDB and replay for indicator warmup.
  5. Connect to Binance WebSocket (1m klines → BarAggregator → 15m bars).
  6. On each 15m close: run the 13-step pipeline, persist signals, notify Telegram.
  7. Print full pipeline log for the first completed bar and exit.

No live orders are placed (paper_trading=True).
Gold instrument is not activated (BTC/USDT only).
"""

from __future__ import annotations

import asyncio
import logging
import os
import pathlib
import sys
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

# ---------------------------------------------------------------------------
# Repo root on sys.path so `from src...` imports work when run directly
# ---------------------------------------------------------------------------
_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import psycopg2
import psycopg2.extras

from src.data_ingest.bar_aggregator import AggregatedBar, BarAggregator
from src.data_ingest.binance_ws import stream_bars
from src.notifications.telegram_notifier import TelegramNotifier
from src.persistence.state_manager import StateManager
from src.pipeline.orchestrator import CycleResult, Orchestrator

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
_DSN = os.environ.get(
    "DATABASE_URL",
    "postgresql://prasad:your_32_char_password@localhost:5432/trading",
)
_SYMBOL        = "BTCUSDT"
_WARMUP_BARS   = 500     # last N 15m bars for indicator warmup

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("live_trader")


# ---------------------------------------------------------------------------
# _FixedStateManager — patches two bugs in StateManager.save_bar()
#
#   Bug 1: Orchestrator._persist_cycle calls save_bar(bar_15m) with only one
#          argument, but the base signature requires `instrument` as a
#          mandatory second positional.  Fix: make instrument default to
#          bar.symbol.
#
#   Bug 2: The `bars` hypertable (created by scripts/init_db.py) has
#          `timestamp_end TIMESTAMPTZ NOT NULL`, `is_complete BOOLEAN NOT NULL`,
#          and `is_reliable BOOLEAN NOT NULL` columns that the base class
#          INSERT omits, causing a NOT NULL constraint violation.
#          Fix: include all columns in the upsert.
# ---------------------------------------------------------------------------

class _FixedStateManager(StateManager):
    """StateManager subclass that fixes save_bar() for the live hypertable."""

    async def save_bar(                             # type: ignore[override]
        self,
        bar: AggregatedBar,
        instrument: Optional[str] = None,           # was required; now defaults to bar.symbol
    ) -> bool:
        """Doc 9 §2: Persist a completed bar, compatible with the hypertable schema.

        Upserts all columns including timestamp_end / is_complete / is_reliable
        so the insert does not violate NOT NULL constraints on the live table.
        """
        inst = instrument if instrument is not None else bar.symbol
        self._require_pool()
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO bars (
                    instrument, timeframe, timestamp_start, timestamp_end,
                    open, high, low, close, volume,
                    is_complete, is_reliable
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                ON CONFLICT (instrument, timeframe, timestamp_start)
                DO UPDATE SET
                    timestamp_end = EXCLUDED.timestamp_end,
                    open          = EXCLUDED.open,
                    high          = EXCLUDED.high,
                    low           = EXCLUDED.low,
                    close         = EXCLUDED.close,
                    volume        = EXCLUDED.volume,
                    is_complete   = EXCLUDED.is_complete,
                    is_reliable   = EXCLUDED.is_reliable
                """,
                inst,
                bar.timeframe,
                bar.timestamp_start,
                bar.timestamp_end,
                float(bar.open),
                float(bar.high),
                float(bar.low),
                float(bar.close),
                float(bar.volume),
                bar.is_complete,
                bar.is_reliable,
            )
        return True


# ---------------------------------------------------------------------------
# Signals table — created once at startup
# ---------------------------------------------------------------------------

_CREATE_SIGNALS = """
CREATE TABLE IF NOT EXISTS signals (
    signal_id   TEXT PRIMARY KEY,
    timestamp   TIMESTAMPTZ     NOT NULL,
    instrument  TEXT            NOT NULL,
    setup_type  TEXT            NOT NULL,
    direction   TEXT            NOT NULL,
    entry_price NUMERIC(20, 8)  NOT NULL,
    stop_loss   NUMERIC(20, 8)  NOT NULL,
    take_profit NUMERIC(20, 8)  NOT NULL,
    timeframe   TEXT            NOT NULL DEFAULT '15m',
    zone_id     TEXT,
    regime      TEXT            NOT NULL,
    confidence  NUMERIC(10, 6)  NOT NULL,
    paper_trade BOOLEAN         NOT NULL DEFAULT TRUE,
    expected_r  NUMERIC(10, 4),
    created_at  TIMESTAMPTZ     NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def _create_signals_table(dsn: str) -> None:
    """Create the signals table if it does not already exist."""
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(_CREATE_SIGNALS)
    logger.info("[OK] signals table ready")
    cur.close()
    conn.close()


def _persist_signals(dsn: str, result: CycleResult) -> None:
    """Write each new trade from CycleResult to the signals table."""
    if not result.new_trades:
        return
    conn = psycopg2.connect(dsn)
    try:
        cur = conn.cursor()
        for trade in result.new_trades:
            cur.execute(
                """
                INSERT INTO signals (
                    signal_id, timestamp, instrument, setup_type, direction,
                    entry_price, stop_loss, take_profit, timeframe, zone_id,
                    regime, confidence, paper_trade, expected_r
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (signal_id) DO NOTHING
                """,
                (
                    trade.trade_id,
                    result.bar_timestamp,
                    trade.instrument,
                    trade.setup_type.name,
                    trade.direction.name,
                    trade.entry_price_actual,
                    trade.stop_price,
                    trade.target_price,
                    "15m",
                    trade.zone_id,
                    result.phase.name,
                    result.phase_confidence,
                    True,                        # paper_trade
                    trade.expected_R,
                ),
            )
        conn.commit()
        logger.info(
            "[OK] %d signal(s) persisted to DB for %s",
            len(result.new_trades), result.instrument,
        )
    finally:
        cur.close()
        conn.close()


# ---------------------------------------------------------------------------
# Warmup bar loading
# ---------------------------------------------------------------------------

def _load_warmup_bars(dsn: str, instrument: str, limit: int = 500) -> list[AggregatedBar]:
    """Load last `limit` 15m bars from TimescaleDB for indicator warmup.

    Returns bars in chronological order (oldest first).
    """
    conn = psycopg2.connect(dsn)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        """
        SELECT timestamp_start, timestamp_end,
               open, high, low, close, volume, is_complete, is_reliable
        FROM bars
        WHERE instrument = %s AND timeframe = '15m'
        ORDER BY timestamp_start DESC
        LIMIT %s
        """,
        (instrument, limit),
    )
    rows = list(reversed(cur.fetchall()))   # oldest first
    cur.close()
    conn.close()

    bars: list[AggregatedBar] = []
    for row in rows:
        ts_start = row["timestamp_start"]
        ts_end   = row["timestamp_end"]
        if ts_start.tzinfo is None:
            ts_start = ts_start.replace(tzinfo=timezone.utc)
        if ts_end.tzinfo is None:
            ts_end = ts_end.replace(tzinfo=timezone.utc)
        bars.append(AggregatedBar(
            symbol          = instrument,
            timestamp_start = ts_start,
            timestamp_end   = ts_end,
            timeframe       = "15m",
            open            = Decimal(str(row["open"])),
            high            = Decimal(str(row["high"])),
            low             = Decimal(str(row["low"])),
            close           = Decimal(str(row["close"])),
            volume          = Decimal(str(row["volume"])),
            is_complete     = bool(row["is_complete"]),
            is_reliable     = bool(row["is_reliable"]),
        ))

    logger.info("[OK] Loaded %d warmup 15m bars for %s", len(bars), instrument)
    return bars


# ---------------------------------------------------------------------------
# Telegram paper-trade notification
# ---------------------------------------------------------------------------

async def _send_paper_signal(notifier: TelegramNotifier, result: CycleResult) -> None:
    """Send a [PAPER] signal notification to Telegram for each new trade."""
    for trade in result.new_trades:
        direction = "LONG" if trade.direction.name == "LONG" else "SHORT"
        text = (
            f"[PAPER] 💡 <b>SIGNAL — {trade.instrument}</b>\n"
            f"{'🟢' if direction == 'LONG' else '🔴'} {direction} | "
            f"{trade.setup_type.name.replace('_', ' ')}\n"
            f"\n"
            f"<b>Entry:</b>   {trade.entry_price_actual}\n"
            f"<b>Stop:</b>    {trade.stop_price}\n"
            f"<b>Target:</b>  {trade.target_price}\n"
            f"<b>R:R:</b>     {trade.expected_R}R\n"
            f"<b>Zone:</b>    {trade.zone_id}\n"
            f"<b>Phase:</b>   {result.phase.name}\n"
            f"<b>Conf:</b>    {float(result.phase_confidence):.2%}"
        )
        sent = await notifier._send(text)   # noqa: SLF001
        if sent:
            logger.info("[OK] Telegram paper signal sent for trade %s", trade.trade_id)
        else:
            logger.warning("[WARN] Telegram send failed (not configured or network error)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    """Entry point — runs until first 15m bar pipeline is complete, then exits."""

    # 1. Ensure signals table exists
    _create_signals_table(_DSN)

    # 2. Build Orchestrator and inject the fixed StateManager before startup()
    #    so that _persist_cycle uses the patched save_bar() from the first cycle.
    orch = Orchestrator(equity=Decimal("10000"), db_dsn=_DSN)
    orch._state_mgr = _FixedStateManager(dsn=_DSN)   # noqa: SLF001

    # 3. Startup: connect DB, build InstrumentContexts, verify hashes
    logger.info("Starting orchestrator …")
    ok = await orch.startup()
    if not ok:
        logger.critical("Orchestrator startup failed — exiting")
        sys.exit(1)
    logger.info("[OK] Orchestrator started")

    # 4. Warmup replay — feeds historical bars through Steps 1-7 so all
    #    indicators (ATR, ZLBB, zones, phase) are fully primed before live data.
    warmup_bars = _load_warmup_bars(_DSN, _SYMBOL, _WARMUP_BARS)
    logger.info("Running warmup replay for %d bars …", len(warmup_bars))
    await orch.replay_history(_SYMBOL, warmup_bars)
    logger.info("[OK] Warmup replay complete — indicators primed")

    # 5. Telegram notifier (gracefully skips if env vars absent)
    notifier = TelegramNotifier()

    # 6. BarAggregator: buffers 1m bars and emits completed HTF bars
    aggregator = BarAggregator(symbol=_SYMBOL, is_continuous=True)
    btc_ctx    = orch.get_context(_SYMBOL)

    # Standby notification counter — send once every 16 cycles (~4 hours)
    standby_counter = 0

    # 7. Stream live 1m bars from Binance WebSocket
    logger.info("[OK] Connecting to Binance WebSocket (btcusdt@kline_1m) …")

    async for bar_1m in stream_bars():
        # Only closed 1m bars reach us (stream_bars() filters is_complete)
        completed = aggregator.push(bar_1m)

        for agg_bar in completed:
            if agg_bar.timeframe != "15m" or not agg_bar.is_complete:
                continue

            logger.info(
                "=== 15m BAR CLOSED: %s  open=%s high=%s low=%s close=%s vol=%s ===",
                agg_bar.timestamp_end.isoformat(),
                agg_bar.open, agg_bar.high, agg_bar.low, agg_bar.close, agg_bar.volume,
            )

            # 8. Run full 13-step pipeline
            result: CycleResult = await orch.run_cycle(agg_bar, btc_ctx)

            # 9. Report pipeline execution log
            logger.info(
                "--- PIPELINE RESULT ---\n"
                "  instrument      : %s\n"
                "  bar_timestamp   : %s\n"
                "  step_reached    : %d / 13\n"
                "  phase           : %s\n"
                "  phase_conf      : %.4f\n"
                "  new_trades      : %d\n"
                "  active_cands    : %d\n"
                "  expired_cands   : %d\n"
                "  open_trades     : %d\n"
                "  cycle_errors    : %s",
                result.instrument,
                result.bar_timestamp.isoformat(),
                result.step_reached,
                result.phase.name,
                float(result.phase_confidence),
                len(result.new_trades),
                result.active_candidates,
                result.expired_candidates,
                result.open_trades,
                result.cycle_errors if result.cycle_errors else "none",
            )

            # 10. Persist signals and notify Telegram
            if result.new_trades:
                _persist_signals(_DSN, result)
                await _send_paper_signal(notifier, result)
                logger.info(
                    "[PAPER] %d new signal(s) generated and persisted",
                    len(result.new_trades),
                )
            else:
                logger.info("[OK] No new signals this bar — pipeline clean")

                # Send standby notification every 4 hours (16 cycles × 15m = 4h)
                # to confirm system is alive in low-signal phases
                standby_counter += 1
                if standby_counter % 16 == 0:
                    from collections import Counter
                    zone_states: Counter[str] = Counter()
                    zones = (
                        btc_ctx.zone_detector.get_active_zones(min_tier="C")
                        if hasattr(btc_ctx, "zone_detector")
                        else []
                    )
                    for z in zones:
                        zone_states[z.state.name] += 1
                    state_summary = ", ".join(
                        f"{k}:{v}" for k, v in sorted(zone_states.items())
                    )
                    eligible_states = {"FRESH", "TESTED", "FLIPPED"}
                    eligible_count = sum(
                        1 for z in zones if z.state.name in eligible_states
                    )
                    phase_name = result.phase.name
                    setup_map = {
                        "DISTRIBUTION": "Upthrust (SHORT fakeout at resistance)",
                        "ACCUMULATION": "Spring (LONG fakeout at support)",
                        "TREND_BULL": "Pullback Continuation (LONG at support)",
                        "TREND_BEAR": "Pullback Continuation (SHORT at resistance)",
                        "BALANCE": "Range Fade + Fakeout",
                    }
                    eligible_setup = setup_map.get(phase_name, "Multiple setups")
                    asyncio.ensure_future(notifier.send_phase_standby(
                        instrument=result.instrument,
                        phase_name=phase_name,
                        phase_confidence=result.phase_confidence,
                        eligible_setup=eligible_setup,
                        eligible_zone_count=eligible_count,
                        total_zone_count=len(zones),
                        zone_state_summary=state_summary or "no zones",
                    ))

            # 11. Exit after the first completed bar (per user request)
            logger.info("=== First bar complete. Shutting down as requested. ===")
            await orch.shutdown()
            return


if __name__ == "__main__":
    asyncio.run(main())
