import pathlib, sys
_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
import asyncio
import os
import logging
import threading
from decimal import Decimal
from datetime import datetime, timezone
from dotenv import load_dotenv
load_dotenv()

import logging.handlers

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

# Persistent file logging — all modules → logs/prasad.log (rotating 10 MB × 5)
os.makedirs("logs", exist_ok=True)
file_handler = logging.handlers.RotatingFileHandler(
    "logs/prasad.log", maxBytes=10 * 1024 * 1024, backupCount=5
)
file_handler.setFormatter(logging.Formatter(
    "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
))
logging.getLogger().addHandler(file_handler)

# Signals-only log — candidate detections + trade fills → logs/signals.log
signal_logger = logging.getLogger("signals")
sig_handler = logging.handlers.RotatingFileHandler(
    "logs/signals.log", maxBytes=5 * 1024 * 1024, backupCount=5
)
sig_handler.setFormatter(logging.Formatter(
    "%(asctime)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
))
signal_logger.addHandler(sig_handler)
signal_logger.setLevel(logging.INFO)

log = logging.getLogger("paper_trader")

# ---------------------------------------------------------------------------
# Fire-and-forget task registry
# Holds strong references so GC cannot cancel in-flight Telegram sends.
# ---------------------------------------------------------------------------
_tg_tasks: set = set()

def _tg_fire(coro) -> None:
    """Schedule a Telegram coroutine as a non-blocking fire-and-forget task.

    Uses a module-level set to hold a strong reference until the task
    completes, preventing silent GC-cancellation (asyncio best practice).
    """
    task = asyncio.create_task(coro)
    _tg_tasks.add(task)
    task.add_done_callback(_tg_tasks.discard)


def _dict_to_aggregated_bar(row: dict, symbol: str, timeframe: str):
    """Convert a load_bars() dict row to an AggregatedBar for replay_history()."""
    from src.data_ingest.bar_aggregator import AggregatedBar
    ts_start = row["timestamp_start"]
    if not isinstance(ts_start, datetime):
        ts_start = datetime.fromisoformat(str(ts_start))
    if ts_start.tzinfo is None:
        ts_start = ts_start.replace(tzinfo=timezone.utc)
    ts_end = row.get("timestamp_end", ts_start)
    if not isinstance(ts_end, datetime):
        ts_end = datetime.fromisoformat(str(ts_end))
    if ts_end.tzinfo is None:
        ts_end = ts_end.replace(tzinfo=timezone.utc)
    return AggregatedBar(
        symbol=symbol,
        timestamp_start=ts_start,
        timestamp_end=ts_end,
        timeframe=timeframe,
        open=Decimal(str(row["open"])),
        high=Decimal(str(row["high"])),
        low=Decimal(str(row["low"])),
        close=Decimal(str(row["close"])),
        volume=Decimal(str(row["volume"])),
        is_complete=True,
        is_reliable=bool(row.get("is_reliable", True)),
    )


def _start_dashboard_api() -> None:
    """Run the FastAPI dashboard server in a daemon background thread.

    Because STATE is a module-level dict in src.api.dashboard_api, both this
    thread and the asyncio trading loop share the SAME dict object (same
    Python process, same interpreter). This is why the API server MUST be
    started here, not as a separate script — separate scripts = separate
    processes = separate copies of STATE with no shared memory.

    daemon=True: thread is killed automatically when the main process exits.
    log_level="warning": suppresses uvicorn's per-request INFO lines so they
    don't pollute the trading engine's log output.

    time.sleep(2): gives Windows TIME_WAIT sockets from prior runs a moment
    to clear before uvicorn attempts to bind, preventing [WinError 10048].
    """
    import time
    time.sleep(2)
    import uvicorn
    from src.api.dashboard_api import app
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning", reload=False)


async def main():
    from src.persistence.state_manager import StateManager
    from src.pipeline.orchestrator import Orchestrator
    from src.notifications.telegram_notifier import TelegramNotifier

    log.info("=== PRASAD PAPER TRADER STARTING ===")

    # Start dashboard API in a background daemon thread.
    # Must happen before any trading logic so the API is ready immediately.
    api_thread = threading.Thread(target=_start_dashboard_api, daemon=True, name="dashboard-api")
    api_thread.start()
    log.info("Dashboard API started on http://localhost:8000/")
    log.info("Mode: PAPER (no real orders)")

    # Reads TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID from env automatically.
    # If tokens are absent it logs a warning and all sends return False safely.
    tg = TelegramNotifier()

    db_dsn = os.environ["DATABASE_URL"]

    # --- Pre-checks and warmup bar load via temporary StateManager ---
    sm = StateManager(dsn=db_dsn)
    await sm.connect()
    log.info("DB connected.")

    # History check
    btc_count = await sm.get_bar_count("BTCUSDT", "15m")
    log.info(f"History: BTCUSDT/15m = {btc_count} bars")
    if not await sm.meets_minimum_history("BTCUSDT", "15m"):
        log.error("Insufficient history. Run fetch_historical.py first.")
        await sm.close()
        return

    # Load warmup bars (last 500 x 15m = ~5 days)
    log.info("Loading warmup bars (last 500 x 15m)...")
    warmup_bars_raw = await sm.load_bars(
        "BTCUSDT", "15m", after=None, limit=500, newest_first=True
    )
    await sm.close()
    log.info(f"Warmup bars loaded: {len(warmup_bars_raw)}")
    log.info(f"Warmup range: {warmup_bars_raw[0]['timestamp_start']} → {warmup_bars_raw[-1]['timestamp_start']}")

    # Convert raw dicts to AggregatedBar objects (required by replay_history)
    warmup_bars = [
        _dict_to_aggregated_bar(r, "BTCUSDT", "15m") for r in warmup_bars_raw
    ]

    # --- Build Orchestrator (owns its own DB connection) ---
    log.info("Initializing Orchestrator...")
    orch = Orchestrator(equity=Decimal("10000"), db_dsn=db_dsn)

    # Startup — connects to DB, checks history, verifies state hashes
    log.info("Running startup sequence...")
    ready = await orch.startup()
    if not ready:
        log.error("Orchestrator startup failed. Check logs.")
        return

    # Notify: system is live and ready (fire-and-forget — non-blocking)
    _tg_fire(tg.send_system_resumed())
    log.info("Telegram: system-resumed notification queued.")

    # Replay warmup bars to prime indicators (ZLBB, ATR, legs, SR, phase)
    log.info(f"Replaying {len(warmup_bars)} warmup bars to prime indicators...")
    await orch.replay_history("BTCUSDT", warmup_bars)
    log.info("Warmup replay complete.")

    # Log restored open trades (critical for VPS restart verification)
    try:
        pm_state = orch._portfolio.get_state()
        restored = len(pm_state.open_trades)
        log.info(f"[STARTUP] Open trades restored from DB: {restored}")
        if restored > 0:
            for t in pm_state.open_trades:
                log.info(
                    f"[STARTUP] Restored trade: {t.trade_id} | "
                    f"entry={t.entry_price_actual} | stop={t.stop_price}"
                )
    except Exception as _restore_err:
        log.warning(f"[STARTUP] Could not inspect open trades: {_restore_err}")

    # Get per-instrument context (created by startup())
    btc_ctx = orch.get_context("BTCUSDT")

    # Pre-populate dashboard STATE immediately so the chart shows data without
    # waiting for the first live 15m cycle to close.  Uses a synthetic
    # CycleResult-shaped namespace drawn from the fully-primed context.
    try:
        from types import SimpleNamespace
        from src.api.dashboard_api import update_state as _update_state
        _phase_obj  = btc_ctx.phase_engine.active_phase
        _candidates = btc_ctx.setup_engine.get_active_candidates() if hasattr(btc_ctx, "setup_engine") else []
        _mock_result = SimpleNamespace(
            phase=_phase_obj,
            phase_confidence=0.0,   # placeholder; overwritten on first real cycle
            cycle_errors=[],
            step_reached=0,
            active_candidates=len(_candidates),
            open_trades=0,
            new_trades=[],
        )
        _update_state(_mock_result, btc_ctx, warmup_bars_raw)
        log.info(
            f"Dashboard STATE pre-populated: phase={btc_ctx.phase_engine.active_phase.name}, "
            f"sr_zones={len(btc_ctx.zone_detector.get_active_zones())}, "
            f"bars={len(warmup_bars_raw)}"
        )
    except Exception as _pre_err:
        log.warning(f"Dashboard pre-populate skipped: {_pre_err}")

    log.info("Starting live feed loop...")
    log.info("Press Ctrl+C to stop.\n")

    from src.data_ingest.binance_ws import stream_bars
    from src.data_ingest.bar_aggregator import BarAggregator
    aggregator = BarAggregator(symbol="BTCUSDT", is_continuous=True)
    cycle_count = 0
    seen_candidate_ids: set[str] = set()
    seen_trade_ids: set[str] = set()

    async def do_backfill() -> None:
        """Gap backfill hook — called by stream_bars() on every reconnect."""
        from backfill import run_backfill  # scripts/ is on sys.path when running this file
        await run_backfill()
        log.info("Gap backfill complete after reconnect.")

    try:
        async for bar_1m in stream_bars(on_reconnect=do_backfill):
            completed = aggregator.push(bar_1m)
            for bar_15m in completed:
                if bar_15m.timeframe != "15m":
                    continue
                cycle_count += 1
                log.info(f"--- Cycle {cycle_count} | {bar_15m.timestamp_start} ---")
                try:
                    result = await orch.run_cycle(bar_15m, btc_ctx)

                    # Append live bar to warmup buffer so the chart stays current.
                    # warmup_bars_raw is a list[dict]; append current bar as a dict.
                    warmup_bars_raw.append({
                        "timestamp_start": bar_15m.timestamp_start,
                        "timestamp_end":   bar_15m.timestamp_end,
                        "open":   bar_15m.open,
                        "high":   bar_15m.high,
                        "low":    bar_15m.low,
                        "close":  bar_15m.close,
                        "volume": bar_15m.volume,
                    })
                    # Update dashboard state (if dashboard API is running)
                    from src.api.dashboard_api import update_state
                    update_state(result, btc_ctx, warmup_bars_raw)

                    log.info(f"Phase: {result.phase} | Confidence: {result.phase_confidence:.2f}")
                    log.info(f"Active candidates: {result.active_candidates}")
                    log.info(f"New trades: {len(result.new_trades)}")
                    log.info(f"Open trades: {result.open_trades}")

                    # --- Candidate detection notifications ---
                    from src.api.dashboard_api import STATE as _dash_state
                    for c in _dash_state.get("candidates_detail", []):
                        cid = c.get("candidate_id") or c.get("zone_id")
                        if cid and cid not in seen_candidate_ids:
                            seen_candidate_ids.add(cid)
                            signal_logger.info(
                                f"NEW CANDIDATE | {c.get('setup_type')} | "
                                f"{c.get('direction')} | zone={c.get('zone_id')} | "
                                f"entry={c.get('entry_price')} | stop={c.get('stop_price')} | "
                                f"target={c.get('target_price')} | R={c.get('expected_R')}"
                            )
                            _tg_fire(tg.send_message(
                                "NEW CANDIDATE DETECTED\n"
                                f"Setup: {c.get('setup_type')}\n"
                                f"Direction: {c.get('direction')}\n"
                                f"Entry: {c.get('entry_price')}\n"
                                f"Stop: {c.get('stop_price')}\n"
                                f"Target: {c.get('target_price')}\n"
                                f"R:R: {c.get('expected_R')}\n"
                                f"Zone: {c.get('zone_id')}\n"
                                f"Phase: {_dash_state.get('phase')}"
                            ))

                    # --- Trade fill notifications ---
                    for trade in result.new_trades:
                        seen_trade_ids.add(trade.trade_id)
                        log.info(f"Trade filled: {trade.instrument} {trade.direction}")
                        signal_logger.info(
                            f"TRADE FILLED | {trade.direction.name} | "
                            f"entry={trade.entry_price_actual} | stop={trade.stop_price} | "
                            f"setup={trade.setup_type.name}"
                        )
                        _tg_fire(tg.send_trade_filled(trade))

                    # --- Trade close detection ---
                    # CycleResult.open_trades is an int count; get actual IDs
                    # from the portfolio manager if available.
                    if hasattr(btc_ctx, "portfolio") and hasattr(btc_ctx.portfolio, "open_trade_ids"):
                        current_trade_ids = set(btc_ctx.portfolio.open_trade_ids)
                    else:
                        current_trade_ids = seen_trade_ids.copy()
                    closed_ids = seen_trade_ids - current_trade_ids
                    for tid in closed_ids:
                        seen_trade_ids.discard(tid)
                        signal_logger.info(f"TRADE CLOSED | trade_id={tid}")
                        _tg_fire(tg.send_message(
                            f"TRADE CLOSED\ntrade_id: {tid}\nCheck dashboard for result."
                        ))

                    # Fire-and-forget: warn on any cycle errors (non-fatal)
                    if result.cycle_errors:
                        error_summary = " | ".join(result.cycle_errors)
                        log.warning(f"Cycle errors: {result.cycle_errors}")
                        _tg_fire(tg.send_instrument_paused("BTCUSDT", error_summary))

                except Exception as e:
                    log.error(f"Cycle error: {e}", exc_info=True)

    except (KeyboardInterrupt, asyncio.CancelledError):
        log.info("Shutdown signal received — sending Telegram notification...")
        # Direct await (NOT fire-and-forget): must complete before process exits.
        await tg.send_system_paused(
            reason="Operator stop (KeyboardInterrupt)",
            details=f"Paper trader shut down cleanly after {cycle_count} cycles.",
        )
        log.info("Shutting down orchestrator...")
        await orch.shutdown()
        log.info("Shutdown complete.")


asyncio.run(main())
