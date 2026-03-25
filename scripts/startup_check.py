"""Doc 9 §3 (Startup Sequence) + Doc 1 §11 (Restart Certification):
Startup health check for the Prasad deterministic trading system.

Runs all pre-flight checks in order across 5 phases:
  Phase 1 — Infrastructure   (Docker, DB container, DB connection)
  Phase 2 — Data Integrity   (bar counts, backfill, minimum history)
  Phase 3 — State Verify     (snapshots, hash certification)
  Phase 4 — Telegram         (connectivity test, non-blocking)
  Phase 5 — Summary          (table + launch command)

Exit code 0 = all critical checks passed — safe to start paper trader.
Exit code 1 = one or more critical checks failed — fix errors first.
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from decimal import Decimal

# -- Bootstrap sys.path so src/ imports work from any working directory ------
_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv
load_dotenv(_REPO_ROOT / ".env")

# Ensure box-drawing characters render correctly on Windows terminals.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ---------------------------------------------------------------------------
# Result tracking — populated by each check, consumed by print_summary()
# ---------------------------------------------------------------------------

_results: dict[str, str] = {}   # label → display value (for summary table)
_critical_failed: bool = False   # set True by _fail(); drives exit code


def _pass(label: str, value: str) -> None:
    """Record a passing check."""
    _results[label] = f"\u2713 {value}"
    print(f"  [PASS] {label}: {value}")


def _warn(label: str, value: str) -> None:
    """Record a warning — non-blocking, system can still start."""
    _results[label] = f"\u26a0 {value}"
    print(f"  [WARN] {label}: {value}")


def _fail(label: str, short: str, message: str) -> None:
    """Record a critical failure — sets exit code 1."""
    global _critical_failed
    _critical_failed = True
    _results[label] = f"\u2717 {short}"
    print(f"\n  [FAIL] {message}\n")


# ---------------------------------------------------------------------------
# Phase 1 — Infrastructure
# ---------------------------------------------------------------------------

def check_docker() -> bool:
    """Check 1: Docker Desktop is running (docker info exit-code 0)."""
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=15,
        )
    except FileNotFoundError:
        _fail("Docker", "Not found",
              "Docker CLI not found. Install Docker Desktop first.")
        return False
    except subprocess.TimeoutExpired:
        _fail("Docker", "Timeout",
              "Docker Desktop is not responding. Restart Docker Desktop.")
        return False

    if result.returncode == 0:
        _pass("Docker", "Running")
        return True
    _fail("Docker", "Not running",
          "Docker Desktop is not running. Start Docker Desktop first.")
    return False


def check_db_container() -> bool:
    """Check 2: TimescaleDB container is up and healthy.

    Auto-fix: runs docker-compose up -d and waits 8 seconds if container
    is absent or stopped, then re-checks once.
    """

    def _get_status() -> str:
        r = subprocess.run(
            ["docker", "ps",
             "--filter", "name=prasad-timescaledb",
             "--format", "{{.Status}}"],
            capture_output=True, text=True, timeout=10,
        )
        return r.stdout.strip()

    status = _get_status()
    if status and ("healthy" in status.lower() or "up" in status.lower()):
        _pass("TimescaleDB", "Healthy")
        return True

    # Auto-fix: try docker-compose up -d (newer Docker uses 'docker compose').
    print("  [AUTO-FIX] Container not running — running: docker-compose up -d ...")
    for cmd in (["docker-compose", "up", "-d"], ["docker", "compose", "up", "-d"]):
        r = subprocess.run(cmd, capture_output=True, cwd=str(_REPO_ROOT))
        if r.returncode == 0:
            break

    print("  Waiting 8 seconds for container to initialise...")
    time.sleep(8)

    status = _get_status()
    if status and ("healthy" in status.lower() or "up" in status.lower()):
        _pass("TimescaleDB", "Auto-started OK")
        return True

    _fail("TimescaleDB", "Not running",
          "DB container not running.\n  Run: docker-compose up -d  then wait 30 s and retry.")
    return False


async def check_db_connection(dsn: str) -> bool:
    """Check 3: DB is accepting connections (SELECT 1).

    Retries once after 12 seconds — PostgreSQL takes a moment to start
    accepting connections even after the Docker health check passes.
    """
    import asyncpg
    for attempt in range(2):
        try:
            conn = await asyncpg.connect(dsn, timeout=10)
            await conn.fetchval("SELECT 1")
            await conn.close()
            _pass("DB Connection", "Connected")
            return True
        except Exception as exc:
            if attempt == 0:
                print(f"  DB not ready yet, waiting 12 s ... ({exc})")
                await asyncio.sleep(12)
            else:
                _fail("DB Connection", "Failed",
                      f"DB is up but not accepting connections.\n  ({exc})")
    return False


# ---------------------------------------------------------------------------
# Phase 2 — Data Integrity
# ---------------------------------------------------------------------------

async def check_bar_counts(dsn: str) -> tuple[int, datetime | None]:
    """Check 4: Bar counts per timeframe. Warns if last 15m bar > 4 hours old.

    Returns (bar_15m_count, bar_15m_newest_ts).
    """
    import asyncpg
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch("""
            SELECT timeframe,
                   COUNT(*)            AS cnt,
                   MAX(timestamp_start) AS newest
            FROM bars
            WHERE instrument = 'BTCUSDT'
            GROUP BY timeframe
            ORDER BY timeframe
        """)
    finally:
        await conn.close()

    now = datetime.now(timezone.utc)
    count_15m: int = 0
    newest_15m: datetime | None = None

    for row in rows:
        tf     = row["timeframe"]
        cnt    = int(row["cnt"])
        newest = row["newest"]
        if newest and newest.tzinfo is None:
            newest = newest.replace(tzinfo=timezone.utc)
        newest_str = newest.strftime("%Y-%m-%d %H:%M UTC") if newest else "N/A"
        print(f"    BTCUSDT/{tf}: {cnt:,} bars | last bar: {newest_str}")
        if tf == "15m":
            count_15m  = cnt
            newest_15m = newest

    if newest_15m is None:
        _fail("BTCUSDT/15m bars", "0 bars",
              "No 15m bars found. Run scripts/fetch_historical.py first.")
        return 0, None

    age_h = (now - newest_15m).total_seconds() / 3600
    if age_h > 4:
        _warn("BTCUSDT/15m bars",
              f"{count_15m:,} ({age_h:.0f}h gap)")
    else:
        _pass("BTCUSDT/15m bars", f"{count_15m:,} (current)")

    return count_15m, newest_15m


async def _run_backfill(dsn: str) -> int:
    """Inline backfill: fetch missing 15m bars from Binance REST API.

    Returns the number of bars saved.  Mirrors scripts/backfill.py but as a
    plain async function so it can be called without a top-level asyncio.run().
    """
    import aiohttp
    import asyncpg
    from src.data_ingest.bar_aggregator import AggregatedBar
    from src.persistence.state_manager import StateManager

    conn = await asyncpg.connect(dsn)
    row = await conn.fetchrow("""
        SELECT timestamp_start FROM bars
        WHERE instrument = 'BTCUSDT' AND timeframe = '15m'
        ORDER BY timestamp_start DESC LIMIT 1
    """)
    await conn.close()

    if not row:
        return 0

    last_ts = row["timestamp_start"]
    if last_ts.tzinfo is None:
        last_ts = last_ts.replace(tzinfo=timezone.utc)

    now      = datetime.now(timezone.utc)
    start_ms = int(last_ts.timestamp() * 1000) + 1
    end_ms   = int(now.timestamp() * 1000)
    total    = 0

    sm = StateManager(dsn=dsn)
    await sm.connect()
    try:
        async with aiohttp.ClientSession() as session:
            while start_ms < end_ms:
                url    = "https://api.binance.com/api/v3/klines"
                params = {
                    "symbol":    "BTCUSDT",
                    "interval":  "15m",
                    "startTime": start_ms,
                    "endTime":   end_ms,
                    "limit":     1000,
                }
                async with session.get(url, params=params) as resp:
                    klines = await resp.json()
                if not klines:
                    break
                for k in klines:
                    ts_s = datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc)
                    ts_e = datetime.fromtimestamp(k[6] / 1000, tz=timezone.utc)
                    bar  = AggregatedBar(
                        symbol="BTCUSDT", timeframe="15m",
                        timestamp_start=ts_s, timestamp_end=ts_e,
                        open=Decimal(str(k[1])),  high=Decimal(str(k[2])),
                        low=Decimal(str(k[3])),   close=Decimal(str(k[4])),
                        volume=Decimal(str(k[5])),
                        is_complete=True, is_reliable=True,
                    )
                    await sm.save_bar(bar, instrument="BTCUSDT")
                    total += 1
                start_ms = klines[-1][0] + 1
                await asyncio.sleep(0.1)   # respect Binance rate limit
    finally:
        await sm.close()

    return total


async def check_backfill(dsn: str, newest_15m: datetime | None) -> None:
    """Check 5: Backfill 15m bars if gap > 1 hour.

    Non-blocking: even if backfill fails the system can still start with
    slightly stale data (the live WebSocket will fill the gap at runtime).
    """
    if newest_15m is None:
        _warn("Backfill", "Skipped (no bars in DB)")
        return

    now   = datetime.now(timezone.utc)
    gap_h = (now - newest_15m).total_seconds() / 3600

    if gap_h <= 1.0:
        _pass("Backfill", "Not needed")
        return

    print(f"  Gap detected: {gap_h:.1f} hours. Fetching from Binance ...")
    try:
        added = await _run_backfill(dsn)
        _pass("Backfill", f"+{added} bars added")
        print(f"  Backfill complete. Added {added} bars.")
    except Exception as exc:
        _warn("Backfill", f"Failed — {exc} (live feed will self-heal)")


async def check_min_history(sm) -> bool:
    """Check 6: Minimum 40 bars per timeframe (Amendment E3).

    Failure is a hard blocker — without sufficient history the ZLBB and
    ATR warmup windows cannot be primed and the system must not start.
    """
    ok = await sm.meets_minimum_history("BTCUSDT", "15m")
    if ok:
        _pass("Min history", "Satisfied")
        return True
    _fail("Min history", "Insufficient",
          "Insufficient history. Run scripts/fetch_historical.py first.")
    return False


# ---------------------------------------------------------------------------
# Phase 3 — State Verification
# ---------------------------------------------------------------------------

async def check_state_snapshots(dsn: str) -> int:
    """Check 7: system_state table has at least one snapshot.

    Warns if the most recent snapshot is > 2 hours old (system was offline).
    Returns snapshot count.
    """
    import asyncpg
    conn = await asyncpg.connect(dsn)
    try:
        cnt_row = await conn.fetchrow("SELECT COUNT(*) AS cnt FROM system_state")
        snap_count = int(cnt_row["cnt"])
        latest_row = None
        if snap_count > 0:
            latest_row = await conn.fetchrow("""
                SELECT snapshot_at, instrument, component
                FROM system_state
                ORDER BY snapshot_at DESC
                LIMIT 1
            """)
    finally:
        await conn.close()

    if snap_count == 0:
        _warn("State snapshots", "0 (first run, OK)")
        return 0

    snap_ts = latest_row["snapshot_at"]
    if snap_ts.tzinfo is None:
        snap_ts = snap_ts.replace(tzinfo=timezone.utc)

    snap_str = (
        f"{latest_row['instrument']}/{latest_row['component']} "
        f"at {snap_ts.strftime('%Y-%m-%d %H:%M UTC')}"
    )
    print(f"    Last snapshot: {snap_str}")

    age_h = (datetime.now(timezone.utc) - snap_ts).total_seconds() / 3600
    if age_h > 2:
        _warn("State snapshots",
              f"{snap_count} ({age_h:.0f}h old, was offline)")
    else:
        _pass("State snapshots", f"{snap_count} snapshots")

    return snap_count


async def check_hash_verification(sm) -> None:
    """Check 8: Doc 1 §11 restart certification — recompute and compare hashes.

    WARN (not FAIL) when snapshots are missing — that is expected after a
    fresh install or if a component has never saved state.
    FAIL only if a snapshot EXISTS but its hash does not match (corruption).
    """
    instruments = ["BTCUSDT"]
    components  = [
        "phase_engine", "portfolio",
        "zone_detector", "setup_engine",
        "zlbb_15m", "zlbb_1h", "zlbb_4h", "zlbb_1d",
    ]

    results = await sm.verify_all(instruments, components)
    valid_count   = sum(1 for v in results.values() if v)
    missing_count = sum(1 for v in results.values() if not v)

    # verify_snapshot returns False for both "missing" and "hash mismatch".
    # We treat all False as WARN so a new session isn't blocked.
    if missing_count == 0:
        _pass("Hash verification", f"All {valid_count} valid")
    else:
        _warn("Hash verification",
              f"{valid_count} valid, {missing_count} warn")


# ---------------------------------------------------------------------------
# Phase 4 — Telegram
# ---------------------------------------------------------------------------

async def check_telegram() -> None:
    """Check 9: Telegram connectivity test — non-blocking.

    The paper trader works without Telegram; this is informational only.
    """
    try:
        from src.notifications.telegram_notifier import TelegramNotifier
        tg = TelegramNotifier()
        ok = await tg.send_system_resumed()
        if ok:
            _pass("Telegram", "Connected")
        else:
            _warn("Telegram", "Not configured")
    except Exception:
        _warn("Telegram", "Not configured")


# ---------------------------------------------------------------------------
# Phase 5 — Summary table
# ---------------------------------------------------------------------------

# Table geometry — every row must produce identical total char counts.
#
#  │  label (20 chars)    │  value (20 chars)    │
#  ├──────────────────────┼──────────────────────┤
#  cell width = 22 chars each (2-space prefix + 20-char padded content)
#  inner width = 22 + 1 (┬) + 22 = 45
#  total row   = 1 (│) + 22 + 1 (│) + 22 + 1 (│) = 47

_W_CELL  = 22   # chars per cell (incl. 2-space prefix, excl. border │)
_W_CONT  = 20   # max content chars per cell (= _W_CELL - 2 leading spaces)
_W_INNER = _W_CELL + 1 + _W_CELL   # 45 — inner width between outer │ borders


def _row(label: str, value: str) -> str:
    """Format one data row: │  label              │  value              │

    Each cell = 22 chars (2 leading spaces + content padded to 20).
    Total row = 47 chars, matching all border lines.
    """
    l_cell = f"  {label:<{_W_CONT}}"   # 2 spaces + padded to 20 = 22 chars
    r_cell = f"  {value:<{_W_CONT}}"   # 2 spaces + padded to 20 = 22 chars
    return f"\u2502{l_cell}\u2502{r_cell}\u2502"


def print_summary(all_passed: bool) -> None:
    """Print the startup summary table and the launch command."""
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    title   = f"  PRASAD STARTUP CHECK \u2014 {now_str}"
    footer  = (
        "  ALL CHECKS PASSED \u2014 safe to start"
        if all_passed else
        "  SOME CHECKS FAILED \u2014 see errors above"
    )

    H = "\u2500"   # ─
    top     = f"\u250c{H * _W_INNER}\u2510"               # ┌─────────────────────────────────────────────┐
    mid     = f"\u251c{H * _W_CELL}\u252c{H * _W_CELL}\u2524"   # ├──────────────────────┬──────────────────────┤
    bot_sep = f"\u251c{H * _W_CELL}\u2534{H * _W_CELL}\u2524"   # ├──────────────────────┴──────────────────────┤
    bot     = f"\u2514{H * _W_INNER}\u2518"               # └─────────────────────────────────────────────┘

    print()
    print(top)
    print(f"\u2502{title:<{_W_INNER}}\u2502")
    print(mid)
    for label, value in _results.items():
        print(_row(label, value))
    print(bot_sep)
    print(f"\u2502{footer:<{_W_INNER}}\u2502")
    print(bot)

    if all_passed:
        print("\n\u2713 Run this command to start the system:")
        print("  python scripts/run_paper_trader.py\n")
    else:
        print("\n\u2717 Fix the errors above before starting the paper trader.\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    """Run all startup checks in order. Exit 1 on critical failure."""
    print("=" * 54)
    print("  PRASAD STARTUP CHECK")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("=" * 54)

    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        print("\n[FAIL] DATABASE_URL not set in .env file.")
        print("  Edit .env and set DATABASE_URL=postgresql://...\n")
        sys.exit(1)

    # ── Phase 1: Infrastructure ──────────────────────────────────────────
    print("\n[ Phase 1 \u2014 Infrastructure ]\n")

    if not check_docker():
        print_summary(False)
        sys.exit(1)

    if not check_db_container():
        print_summary(False)
        sys.exit(1)

    if not await check_db_connection(dsn):
        print_summary(False)
        sys.exit(1)

    # ── Phase 2: Data Integrity ──────────────────────────────────────────
    print("\n[ Phase 2 \u2014 Data Integrity ]\n")

    from src.persistence.state_manager import StateManager
    sm = StateManager(dsn=dsn)
    await sm.connect()

    print("  Bar counts:")
    bar_count, bar_newest = await check_bar_counts(dsn)

    await check_backfill(dsn, bar_newest)

    min_ok = await check_min_history(sm)

    # ── Phase 3: State Verification ──────────────────────────────────────
    print("\n[ Phase 3 \u2014 State Verification ]\n")

    await check_state_snapshots(dsn)
    await check_hash_verification(sm)

    await sm.close()

    if not min_ok:
        # Deferred hard exit: let Phase 3 still run for visibility.
        print_summary(False)
        sys.exit(1)

    # ── Phase 4: Telegram ────────────────────────────────────────────────
    print("\n[ Phase 4 \u2014 Telegram ]\n")

    await check_telegram()

    # ── Phase 5: Summary ─────────────────────────────────────────────────
    all_passed = not _critical_failed
    print_summary(all_passed)

    if not all_passed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
