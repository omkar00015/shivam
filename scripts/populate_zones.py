"""Doc 4 + Amendment v1.1/v1.2: Pre-populate SR zones from historical bars.

Reads all stored 15m BTC/USDT bars from TimescaleDB, streams them through
the ZoneDetector (same code as live), and persists the resulting active zones
to a new `zones` table.

The ATR calculators for 1H and 4H are updated in lock-step with the 15m
stream: each time the 15m pointer crosses a 1H or 4H boundary, the completed
HTF window is aggregated and pushed to the corresponding ATRCalculator.
This ensures zone width and clustering thresholds match the historical value
at each bar, not the final value (correct streaming behaviour, Doc 2 §7.3).

Output: rows in the `zones` table, one per active non-EXPIRED zone.
Idempotent: truncates instrument's zones before re-populating.
"""

from __future__ import annotations

import datetime
import os
import pathlib
import sys
from decimal import Decimal
from typing import Optional

# Ensure repo root is on sys.path so 'src' is importable regardless of CWD.
_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import psycopg2
import psycopg2.extras

# Project imports
from src.data_ingest.bar_aggregator import AggregatedBar
from src.indicators.atr import ATRCalculator
from src.sr.zone_detector import ZoneDetector, SRZone, ZoneState

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
INSTRUMENT = "BTCUSDT"
SR_TIMEFRAME = "1H"      # primary SR generation timeframe (Doc 4)

_UTC = datetime.timezone.utc

# ---------------------------------------------------------------------------
# Zones table DDL
# ---------------------------------------------------------------------------

_CREATE_ZONES_TABLE = """
CREATE TABLE IF NOT EXISTS zones (
    zone_id           TEXT            PRIMARY KEY,
    instrument        TEXT            NOT NULL,
    center            NUMERIC(20, 8)  NOT NULL,
    zone_high         NUMERIC(20, 8)  NOT NULL,
    zone_low          NUMERIC(20, 8)  NOT NULL,
    polarity          TEXT            NOT NULL,
    origin            TEXT            NOT NULL,
    timeframe         TEXT            NOT NULL,
    is_midpoint       BOOLEAN         NOT NULL DEFAULT FALSE,
    state             TEXT            NOT NULL,
    tier              TEXT            NOT NULL,
    strength          INTEGER         NOT NULL,
    touch_count       INTEGER         NOT NULL DEFAULT 0,
    false_break_count INTEGER         NOT NULL DEFAULT 0,
    created_at        TIMESTAMPTZ,
    last_touch_time   TIMESTAMPTZ,
    populated_at      TIMESTAMPTZ     NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_zones_instrument_tier
    ON zones (instrument, tier, strength DESC);

CREATE INDEX IF NOT EXISTS idx_zones_instrument_state
    ON zones (instrument, state)
    WHERE state NOT IN ('EXPIRED', 'FROZEN');
"""

_INSERT_ZONE_SQL = """
INSERT INTO zones (
    zone_id, instrument, center, zone_high, zone_low,
    polarity, origin, timeframe, is_midpoint,
    state, tier, strength, touch_count, false_break_count,
    created_at, last_touch_time, populated_at
) VALUES (
    %(zone_id)s, %(instrument)s, %(center)s, %(zone_high)s, %(zone_low)s,
    %(polarity)s, %(origin)s, %(timeframe)s, %(is_midpoint)s,
    %(state)s, %(tier)s, %(strength)s, %(touch_count)s, %(false_break_count)s,
    %(created_at)s, %(last_touch_time)s, %(populated_at)s
)
ON CONFLICT (zone_id) DO UPDATE SET
    state             = EXCLUDED.state,
    tier              = EXCLUDED.tier,
    strength          = EXCLUDED.strength,
    touch_count       = EXCLUDED.touch_count,
    false_break_count = EXCLUDED.false_break_count,
    last_touch_time   = EXCLUDED.last_touch_time,
    populated_at      = EXCLUDED.populated_at;
"""


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _get_conn() -> psycopg2.extensions.connection:
    """Connect to TimescaleDB using env vars or defaults."""
    return psycopg2.connect(
        host     = os.getenv("DB_HOST",     "localhost"),
        port     = int(os.getenv("DB_PORT", "5432")),
        dbname   = os.getenv("DB_NAME",     "trading"),
        user     = os.getenv("DB_USER",     "prasad"),
        password = os.getenv("DB_PASSWORD", "your_32_char_password"),
    )


def _load_bars(
    cur: psycopg2.extensions.cursor,
    instrument: str,
    timeframe: str,
) -> list[dict]:
    """Load all completed bars in chronological order."""
    cur.execute(
        """
        SELECT timestamp_start, timestamp_end, open, high, low, close, volume
        FROM   bars
        WHERE  instrument = %s AND timeframe = %s AND is_complete = TRUE
        ORDER  BY timestamp_start ASC
        """,
        (instrument, timeframe),
    )
    return [dict(r) for r in cur.fetchall()]


def _row_to_agg_bar(row: dict, symbol: str, timeframe: str) -> AggregatedBar:
    """Convert a DB row dict to AggregatedBar (all prices Decimal, timestamps UTC)."""
    ts_start = row["timestamp_start"]
    ts_end   = row["timestamp_end"]
    if ts_start.tzinfo is None:
        ts_start = ts_start.replace(tzinfo=_UTC)
    if ts_end.tzinfo is None:
        ts_end = ts_end.replace(tzinfo=_UTC)

    return AggregatedBar(
        symbol          = symbol,
        timestamp_start = ts_start,
        timestamp_end   = ts_end,
        timeframe       = timeframe,
        open            = Decimal(str(row["open"])),
        high            = Decimal(str(row["high"])),
        low             = Decimal(str(row["low"])),
        close           = Decimal(str(row["close"])),
        volume          = Decimal(str(row["volume"])),
        is_complete     = True,
        is_reliable     = True,
    )


# ---------------------------------------------------------------------------
# HTF window accumulator (keeps ATRs in lock-step with the 15m stream)
# ---------------------------------------------------------------------------

def _htf_boundary(ts: datetime.datetime, tf: str) -> datetime.datetime:
    """Return the UTC start of the HTF window containing ts."""
    if tf == "1H":
        return ts.replace(minute=0, second=0, microsecond=0)
    if tf == "4H":
        return ts.replace(hour=(ts.hour // 4) * 4, minute=0, second=0, microsecond=0)
    raise ValueError(f"Unsupported HTF: {tf}")


def _make_agg_bar(
    window_bars: list[AggregatedBar],
    symbol: str,
    tf: str,
    window_start: datetime.datetime,
) -> AggregatedBar:
    """Doc 2 §4.2: Aggregate a list of 15m bars into one HTF bar (OHLCV rules)."""
    return AggregatedBar(
        symbol          = symbol,
        timestamp_start = window_start,
        timestamp_end   = window_bars[-1].timestamp_end,
        timeframe       = tf,
        open            = window_bars[0].open,
        high            = max(b.high for b in window_bars),
        low             = min(b.low  for b in window_bars),
        close           = window_bars[-1].close,
        volume          = sum((b.volume for b in window_bars), Decimal("0")),
        is_complete     = True,
        is_reliable     = True,
    )


class _HTFWindowTracker:
    """Accumulates 15m bars; emits a completed HTF AggregatedBar at each boundary.

    Used to keep 1H and 4H ATR calculators in sync with the 15m replay stream.
    """

    def __init__(self, symbol: str, tf: str) -> None:
        self._symbol = symbol
        self._tf = tf
        self._current_boundary: Optional[datetime.datetime] = None
        self._window: list[AggregatedBar] = []

    def push(self, bar_15m: AggregatedBar) -> Optional[AggregatedBar]:
        """Feed one 15m bar. Returns a completed HTF bar if the window closed."""
        b = _htf_boundary(bar_15m.timestamp_start, self._tf)

        if self._current_boundary is None:
            self._current_boundary = b

        if b != self._current_boundary:
            # Window boundary crossed — emit the completed HTF bar
            completed: Optional[AggregatedBar] = None
            if self._window:
                completed = _make_agg_bar(
                    self._window, self._symbol, self._tf, self._current_boundary
                )
            # Open the new window
            self._current_boundary = b
            self._window = [bar_15m]
            return completed

        self._window.append(bar_15m)
        return None


# ---------------------------------------------------------------------------
# Core streaming replay
# ---------------------------------------------------------------------------

def stream_replay(
    bars_15m: list[AggregatedBar],
    atr_15m:  ATRCalculator,
    atr_1h:   ATRCalculator,
    atr_4h:   ATRCalculator,
    detector: ZoneDetector,
) -> None:
    """Doc 12 §2: Replay 15m bars through ATRs and ZoneDetector in chronological order.

    ATR 1H and 4H are updated whenever the 15m pointer crosses a new HTF boundary,
    ensuring zone clustering thresholds reflect the historical ATR at each bar.
    """
    tracker_1h = _HTFWindowTracker(INSTRUMENT, "1H")
    tracker_4h = _HTFWindowTracker(INSTRUMENT, "4H")

    total = len(bars_15m)
    report_every = max(total // 20, 1_000)   # ~20 progress lines

    for i, bar in enumerate(bars_15m):
        # 1. Update 15m ATR
        atr_15m.push(bar)

        # 2. Update 1H ATR when a 1H window closes
        htf_1h = tracker_1h.push(bar)
        if htf_1h is not None:
            atr_1h.push(htf_1h)

        # 3. Update 4H ATR when a 4H window closes
        htf_4h = tracker_4h.push(bar)
        if htf_4h is not None:
            atr_4h.push(htf_4h)

        # 4. Feed bar to zone detector (no leg — open/close + rejection sources only)
        detector.update(bar, completed_leg=None)

        # Progress report
        if (i + 1) % report_every == 0 or i == total - 1:
            zones = detector.get_active_zones()
            atr_val = atr_15m.current_atr
            pct = (i + 1) / total * 100
            print(
                f"  {pct:5.1f}%  bar {i+1:>7,}/{total:,}  "
                f"zones={len(zones):>4}  "
                f"atr_15m={atr_val}  "
                f"date={bar.timestamp_start.date()}",
                flush=True,
            )


# ---------------------------------------------------------------------------
# Zone persistence
# ---------------------------------------------------------------------------

def _zone_to_row(zone: SRZone, instrument: str, now: datetime.datetime) -> dict:
    """Serialise SRZone to a dict suitable for the zones table."""
    return {
        "zone_id":           zone.zone_id,
        "instrument":        instrument,
        "center":            zone.center,
        "zone_high":         zone.zone_high,
        "zone_low":          zone.zone_low,
        "polarity":          zone.polarity.name,
        "origin":            zone.origin.name,
        "timeframe":         zone.timeframe,
        "is_midpoint":       zone.is_midpoint,
        "state":             zone.state.name,
        "tier":              zone.tier.name,
        "strength":          zone.strength,
        "touch_count":       zone.touch_count,
        "false_break_count": zone.false_break_count,
        "created_at":        zone.created_at,
        "last_touch_time":   zone.last_touch_time,
        "populated_at":      now,
    }


def persist_zones(
    conn: psycopg2.extensions.connection,
    zones: list[SRZone],
    instrument: str,
) -> int:
    """Write active zones to the zones table. Returns count inserted/updated."""
    now = datetime.datetime.now(tz=_UTC)
    cur = conn.cursor()

    # Clear existing zones for this instrument before re-populating
    cur.execute("DELETE FROM zones WHERE instrument = %s", (instrument,))

    for zone in zones:
        row = _zone_to_row(zone, instrument, now)
        cur.execute(_INSERT_ZONE_SQL, row)

    conn.commit()
    cur.close()
    return len(zones)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    """Run historical zone detection and persist results."""
    conn = _get_conn()
    cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # -----------------------------------------------------------------------
    # 1. Ensure zones table exists
    # -----------------------------------------------------------------------
    conn.autocommit = True
    cur.execute(_CREATE_ZONES_TABLE)
    conn.autocommit = False
    print("[OK] zones table ready")

    # -----------------------------------------------------------------------
    # 2. Load bars from DB
    # -----------------------------------------------------------------------
    print("[  ] Loading bars from DB...")
    rows_15m = _load_bars(cur, INSTRUMENT, "15m")
    cur.close()
    conn.close()

    if not rows_15m:
        print("[FAIL] No 15m bars found. Run scripts/fetch_historical.py first.", file=sys.stderr)
        sys.exit(1)

    print(f"[OK] Loaded {len(rows_15m):,} 15m bars")

    # -----------------------------------------------------------------------
    # 3. Convert rows to AggregatedBar objects
    # -----------------------------------------------------------------------
    bars_15m = [_row_to_agg_bar(r, INSTRUMENT, "15m") for r in rows_15m]
    print(f"[OK] Converted to AggregatedBar objects")

    # -----------------------------------------------------------------------
    # 4. Initialise ATR calculators
    # -----------------------------------------------------------------------
    atr_15m = ATRCalculator(symbol=INSTRUMENT, timeframe="15m", is_continuous=True)
    atr_1h  = ATRCalculator(symbol=INSTRUMENT, timeframe="1H",  is_continuous=True)
    atr_4h  = ATRCalculator(symbol=INSTRUMENT, timeframe="4H",  is_continuous=True)
    print("[OK] ATR calculators initialised")

    # -----------------------------------------------------------------------
    # 5. Initialise ZoneDetector
    # -----------------------------------------------------------------------
    detector = ZoneDetector(
        symbol       = INSTRUMENT,
        sr_timeframe = SR_TIMEFRAME,
        atr_1h       = atr_1h,
        atr_15m      = atr_15m,
        atr_4h       = atr_4h,
    )
    print(f"[OK] ZoneDetector initialised (sr_timeframe={SR_TIMEFRAME})")

    # -----------------------------------------------------------------------
    # 6. Stream replay — the main work
    # -----------------------------------------------------------------------
    print(f"\n[  ] Streaming {len(bars_15m):,} bars through ZoneDetector...")
    stream_replay(bars_15m, atr_15m, atr_1h, atr_4h, detector)

    # -----------------------------------------------------------------------
    # 7. Collect final active zones (all tiers, all polarities)
    # -----------------------------------------------------------------------
    active_zones = detector.get_active_zones(min_tier="C")
    print(f"\n[OK] Replay complete. Active zones: {len(active_zones)}")

    if active_zones:
        tiers = {}
        for z in active_zones:
            tiers[z.tier.name] = tiers.get(z.tier.name, 0) + 1
        for tier in ("S", "A", "B", "C"):
            if tier in tiers:
                print(f"  Tier {tier}: {tiers[tier]:>4} zones")

    # -----------------------------------------------------------------------
    # 8. Persist zones to DB
    # -----------------------------------------------------------------------
    print("\n[  ] Persisting zones to TimescaleDB...")
    conn2 = _get_conn()
    n_written = persist_zones(conn2, active_zones, INSTRUMENT)
    conn2.close()
    print(f"[OK] Zones written: {n_written}")

    # -----------------------------------------------------------------------
    # 9. Final report
    # -----------------------------------------------------------------------
    conn3 = _get_conn()
    cur3  = conn3.cursor()
    cur3.execute(
        "SELECT tier, COUNT(*) FROM zones WHERE instrument = %s GROUP BY tier ORDER BY tier",
        (INSTRUMENT,),
    )
    rows = cur3.fetchall()
    cur3.close()
    conn3.close()

    print("\n=== Zones in TimescaleDB ===")
    total_zones = 0
    for tier, count in rows:
        print(f"  Tier {tier}: {count:>4}")
        total_zones += count
    print(f"  Total: {total_zones:>4}")

    if total_zones > 0:
        print(f"\n[PASS] Zone table populated for {INSTRUMENT}.")
    else:
        print("\n[WARN] Zero zones detected — check ATR warmup or bar data.", file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except psycopg2.OperationalError as exc:
        print(f"[FAIL] Database connection error: {exc}", file=sys.stderr)
        sys.exit(1)
    except ImportError as exc:
        print(
            f"[FAIL] Import error: {exc}\n"
            "       Run from repo root: python scripts/populate_zones.py",
            file=sys.stderr,
        )
        sys.exit(1)
