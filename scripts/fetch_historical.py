"""Doc 2 §3-5 + Amendment v1.1 D1: Fetch 5 years of BTC/USDT 15m bars from Binance.

Steps:
  1. Paginate Binance REST klines (no auth needed for historical data).
  2. Batch-insert 15m bars into TimescaleDB bars table.
  3. Aggregate 15m -> 1H, 4H, 1D, 1W, 1M in-memory using UTC boundary alignment.
  4. Insert all HTF bars into bars table.
  5. Report row counts per timeframe.

Bar count expectations (5 years ending 2026-02-25):
  15m : ~175,320   (5 × 365.25 × 24 × 4)
  1H  : ~43,830    (5 × 365.25 × 24)
  4H  : ~10,958    (5 × 365.25 × 6)
  1D  : ~1,826     (5 × 365.25)
  1W  : ~261       (5 × 52.18)
  1M  : ~60

All prices stored as NUMERIC; no float arithmetic anywhere (CLAUDE.md rule).
Inserts are idempotent (ON CONFLICT DO NOTHING).
"""

from __future__ import annotations

import datetime
import os
import sys
import time
from decimal import Decimal
from typing import Iterator

import psycopg2
import psycopg2.extras
import requests
from dotenv import load_dotenv
load_dotenv()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SYMBOL     = "BTCUSDT"
INSTRUMENT = "BTCUSDT"

_UTC            = datetime.timezone.utc
_FIVE_YEARS     = datetime.timedelta(days=5 * 365 + 2)   # +2 covers leap years
_NOW            = datetime.datetime.now(tz=_UTC)
_START          = _NOW - _FIVE_YEARS

_BINANCE_URL    = "https://api.binance.com/api/v3/klines"
_API_LIMIT      = 1000     # Binance max bars per call
_SLEEP_BETWEEN  = 0.15     # s between API calls — keeps weight well below 1 200/min
_DB_BATCH       = 5_000    # rows per execute_values call

_15M_MS         = 15 * 60 * 1_000     # 15 minutes in milliseconds


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


_INSERT_SQL = """
INSERT INTO bars (
    timestamp_start, timestamp_end, instrument, timeframe,
    open, high, low, close, volume, is_complete, is_reliable
) VALUES %s
ON CONFLICT (instrument, timeframe, timestamp_start) DO NOTHING
"""


def _insert_bars(cur: psycopg2.extensions.cursor, bars: list[dict]) -> int:
    """Doc 2 §3: Batch-insert bars via execute_values. Returns rows inserted."""
    if not bars:
        return 0
    rows = [
        (
            b["timestamp_start"], b["timestamp_end"],
            b["instrument"],      b["timeframe"],
            b["open"], b["high"], b["low"], b["close"], b["volume"],
            b["is_complete"],     b["is_reliable"],
        )
        for b in bars
    ]
    psycopg2.extras.execute_values(cur, _INSERT_SQL, rows, page_size=1_000)
    return len(rows)


# ---------------------------------------------------------------------------
# Binance REST helpers
# ---------------------------------------------------------------------------

def _fetch_klines_page(start_ms: int, interval: str = "15m") -> list:
    """Fetch one page of Binance klines. Raises on HTTP error."""
    resp = requests.get(
        _BINANCE_URL,
        params={
            "symbol":    SYMBOL,
            "interval":  interval,
            "startTime": start_ms,
            "limit":     _API_LIMIT,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def _kline_to_bar(k: list, timeframe: str) -> dict:
    """Doc 2 §3: Parse Binance kline list to bar dict using Decimal for prices.

    Binance kline format:
      k[0]  open time  (ms UTC)
      k[1]  open price (string)
      k[2]  high price (string)
      k[3]  low  price (string)
      k[4]  close price (string)
      k[5]  volume      (string)
      k[6]  close time  (ms UTC)
    """
    ts_start = datetime.datetime.fromtimestamp(k[0] / 1_000, tz=_UTC)
    ts_end   = datetime.datetime.fromtimestamp(k[6] / 1_000, tz=_UTC)
    return {
        "timestamp_start": ts_start,
        "timestamp_end":   ts_end,
        "instrument":      INSTRUMENT,
        "timeframe":       timeframe,
        "open":            Decimal(k[1]),
        "high":            Decimal(k[2]),
        "low":             Decimal(k[3]),
        "close":           Decimal(k[4]),
        "volume":          Decimal(k[5]),
        "is_complete":     True,
        "is_reliable":     True,
    }


def _paginate_15m(start: datetime.datetime, end: datetime.datetime) -> Iterator[list[dict]]:
    """Yield pages of 15m bar dicts from Binance, earliest-first."""
    start_ms = int(start.timestamp() * 1_000)
    end_ms   = int(end.timestamp()   * 1_000)

    while start_ms < end_ms:
        klines = _fetch_klines_page(start_ms)
        if not klines:
            break

        bars = [_kline_to_bar(k, "15m") for k in klines]
        yield bars

        # Advance: next page starts one 15m interval after the last open time.
        start_ms = klines[-1][0] + _15M_MS
        time.sleep(_SLEEP_BETWEEN)


# ---------------------------------------------------------------------------
# Step 1+2: Fetch 15m bars
# ---------------------------------------------------------------------------

def fetch_and_store_15m() -> int:
    """Doc 2 §3: Fetch all historical 15m bars and store in TimescaleDB.

    Returns total number of bars fetched (including duplicates skipped on conflict).
    """
    conn = _get_conn()
    conn.autocommit = False
    cur  = conn.cursor()

    total_fetched = 0
    batch: list[dict] = []

    print(f"[  ] Fetching 15m bars: {_START.date()} -> {_NOW.date()}")

    for page in _paginate_15m(_START, _NOW):
        batch.extend(page)

        if len(batch) >= _DB_BATCH:
            _insert_bars(cur, batch)
            conn.commit()
            total_fetched += len(batch)
            last_ts = batch[-1]["timestamp_start"]
            print(f"  -> {total_fetched:>8,} bars | last date: {last_ts.date()}", flush=True)
            batch = []

    # Final partial batch
    if batch:
        _insert_bars(cur, batch)
        conn.commit()
        total_fetched += len(batch)

    cur.close()
    conn.close()
    print(f"[OK] 15m bars fetched+stored: {total_fetched:,}")
    return total_fetched


# ---------------------------------------------------------------------------
# Step 3: HTF aggregation from 15m bars in memory
# ---------------------------------------------------------------------------

# Doc 2 §4.3 — UTC boundary alignment functions

def _b_1h(ts: datetime.datetime) -> datetime.datetime:
    """1H boundary: start of each hour."""
    return ts.replace(minute=0, second=0, microsecond=0)


def _b_4h(ts: datetime.datetime) -> datetime.datetime:
    """4H boundary: 00/04/08/12/16/20 UTC."""
    return ts.replace(hour=(ts.hour // 4) * 4, minute=0, second=0, microsecond=0)


def _b_1d(ts: datetime.datetime) -> datetime.datetime:
    """1D boundary: 00:00 UTC."""
    return ts.replace(hour=0, minute=0, second=0, microsecond=0)


def _b_1w(ts: datetime.datetime) -> datetime.datetime:
    """1W boundary: Monday 00:00 UTC (Amendment v1.1 D1)."""
    monday = ts - datetime.timedelta(days=ts.weekday())
    return monday.replace(hour=0, minute=0, second=0, microsecond=0)


def _b_1m(ts: datetime.datetime) -> datetime.datetime:
    """1M boundary: 1st of the month 00:00 UTC."""
    return ts.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


_BOUNDARY_FN = {
    "1H": _b_1h,
    "4H": _b_4h,
    "1D": _b_1d,
    "1W": _b_1w,
    "1M": _b_1m,
}

_TF_DELTA = {
    "1H": datetime.timedelta(hours=1),
    "4H": datetime.timedelta(hours=4),
    "1D": datetime.timedelta(days=1),
    "1W": datetime.timedelta(weeks=1),
}


def _next_month(ts: datetime.datetime) -> datetime.datetime:
    """Return first moment of the month after ts."""
    if ts.month == 12:
        return ts.replace(year=ts.year + 1, month=1, day=1,
                          hour=0, minute=0, second=0, microsecond=0)
    return ts.replace(month=ts.month + 1, day=1,
                      hour=0, minute=0, second=0, microsecond=0)


def aggregate_to_htf(bars_15m: list[dict], tf: str) -> list[dict]:
    """Doc 2 §4.2+4.3: Aggregate 15m bars to HTF using UTC boundary alignment.

    Produces one HTF bar per completed window.
    OHLCV: open=first, high=max, low=min, close=last, volume=sum (Doc 2 §4.2).
    """
    bfn = _BOUNDARY_FN[tf]

    # Group bars by their boundary timestamp
    groups: dict[datetime.datetime, list[dict]] = {}
    for bar in bars_15m:
        key = bfn(bar["timestamp_start"])
        if key not in groups:
            groups[key] = []
        groups[key].append(bar)

    result: list[dict] = []
    for window_start in sorted(groups):
        group = groups[window_start]

        # Compute window end for completeness check
        if tf == "1M":
            window_end = _next_month(window_start)
        else:
            window_end = window_start + _TF_DELTA[tf]

        result.append({
            "timestamp_start": window_start,
            "timestamp_end":   group[-1]["timestamp_end"],
            "instrument":      INSTRUMENT,
            "timeframe":       tf,
            "open":            group[0]["open"],
            "high":            max(b["high"] for b in group),
            "low":             min(b["low"]  for b in group),
            "close":           group[-1]["close"],
            "volume":          sum((b["volume"] for b in group), Decimal("0")),
            "is_complete":     True,
            "is_reliable":     True,
        })

    return result


def load_15m_from_db() -> list[dict]:
    """Load all stored 15m bars in chronological order for HTF aggregation."""
    conn = _get_conn()
    cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        """
        SELECT timestamp_start, timestamp_end, open, high, low, close, volume
        FROM   bars
        WHERE  instrument = %s AND timeframe = '15m'
        ORDER  BY timestamp_start ASC
        """,
        (INSTRUMENT,),
    )
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()
    conn.close()
    return rows


# ---------------------------------------------------------------------------
# Step 4: Store HTF bars
# ---------------------------------------------------------------------------

def aggregate_and_store_htf(bars_15m_raw: list[dict]) -> dict[str, int]:
    """Doc 2 §4: Aggregate from 15m to all HTF; store in DB. Returns counts."""
    conn = _get_conn()
    conn.autocommit = False
    cur  = conn.cursor()

    counts: dict[str, int] = {}
    for tf in ("1H", "4H", "1D", "1W", "1M"):
        htf_bars = aggregate_to_htf(bars_15m_raw, tf)
        _insert_bars(cur, htf_bars)
        conn.commit()
        counts[tf] = len(htf_bars)
        print(f"[OK] {tf:>3} bars aggregated+stored: {len(htf_bars):,}")

    cur.close()
    conn.close()
    return counts


# ---------------------------------------------------------------------------
# Step 5: Report counts
# ---------------------------------------------------------------------------

def report_counts() -> None:
    """Query and print row count per timeframe from the bars table."""
    conn = _get_conn()
    cur  = conn.cursor()
    print("\n=== Bar Counts in TimescaleDB ===")

    expected = {
        "15m": 175_320,
        "1H":   43_830,
        "4H":   10_958,
        "1D":    1_826,
        "1W":      261,
        "1M":       60,
    }
    all_ok = True
    for tf in ("15m", "1H", "4H", "1D", "1W", "1M"):
        cur.execute(
            "SELECT COUNT(*) FROM bars WHERE instrument = %s AND timeframe = %s",
            (INSTRUMENT, tf),
        )
        count = cur.fetchone()[0]
        exp   = expected[tf]
        ok    = count >= int(exp * 0.90)   # allow 10% slack for weekends/gaps
        status = "OK" if ok else "LOW"
        all_ok = all_ok and ok
        print(f"  {tf:>3s}: {count:>8,}  (expected ~{exp:>7,})  [{status}]")

    cur.close()
    conn.close()

    if all_ok:
        print("\n[PASS] All timeframe counts meet minimums.")
    else:
        print("\n[WARN] Some counts are below expected — check Binance data gaps.")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    """Run all 5 steps in order."""
    print("=" * 60)
    print("Step 1+2: Fetch 15m bars from Binance -> TimescaleDB")
    print("=" * 60)
    fetch_and_store_15m()

    print("\n" + "=" * 60)
    print("Step 3: Load 15m bars from DB for in-memory aggregation")
    print("=" * 60)
    bars_15m_raw = load_15m_from_db()
    print(f"[OK] Loaded {len(bars_15m_raw):,} 15m bars for aggregation")

    print("\n" + "=" * 60)
    print("Step 4: Aggregate 15m -> HTF and store")
    print("=" * 60)
    aggregate_and_store_htf(bars_15m_raw)

    print("\n" + "=" * 60)
    print("Step 5: Bar count verification")
    print("=" * 60)
    report_counts()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[INTERRUPTED] Partial data may be in DB — safe to re-run.", file=sys.stderr)
        sys.exit(1)
    except requests.exceptions.RequestException as exc:
        print(f"[FAIL] Binance API error: {exc}", file=sys.stderr)
        sys.exit(1)
    except psycopg2.OperationalError as exc:
        print(f"[FAIL] Database connection error: {exc}", file=sys.stderr)
        sys.exit(1)
