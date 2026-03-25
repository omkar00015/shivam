import pathlib, sys
_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
import asyncio, os, time
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
load_dotenv()
import aiohttp
async def fetch_binance_klines(session, symbol, interval, start_ms, end_ms, limit=1000):
    url = "https://api.binance.com/api/v3/klines"
    params = {
        "symbol": symbol,
        "interval": interval,
        "startTime": start_ms,
        "endTime": end_ms,
        "limit": limit
    }
    async with session.get(url, params=params) as resp:
        return await resp.json()
async def run_backfill() -> None:
    """Core backfill logic: find the gap between the last DB bar and now,
    fetch missing 15m bars from Binance, and write them to TimescaleDB.

    Safe to import and await directly — no asyncio.run() inside.
    Called both from the __main__ entry point and from the WebSocket
    reconnect hook in run_paper_trader.py.
    """
    import logging
    log = logging.getLogger(__name__)

    from src.persistence.state_manager import StateManager
    from src.data_ingest.bar_aggregator import AggregatedBar
    sm = StateManager(os.environ["DATABASE_URL"])
    await sm.connect()
    import asyncpg
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])

    # Find last bar in DB
    row = await conn.fetchrow("""
        SELECT timestamp_start FROM bars
        WHERE instrument='BTCUSDT' AND timeframe='15m'
        ORDER BY timestamp_start DESC LIMIT 1
    """)
    await conn.close()

    if not row:
        log.warning("No bars in DB — skipping backfill (run fetch_historical.py first).")
        await sm.close()
        return

    last_ts = row["timestamp_start"]
    if last_ts.tzinfo is None:
        last_ts = last_ts.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    gap_hours = (now - last_ts).total_seconds() / 3600
    log.info("Backfill: last bar %s, gap %.1f h", last_ts, gap_hours)

    if gap_hours < 0.5:
        log.info("DB is up to date — no backfill needed.")
        await sm.close()
        return

    # Fetch from Binance in 1000-bar chunks
    start_ms = int(last_ts.timestamp() * 1000) + 1
    end_ms = int(now.timestamp() * 1000)

    total_saved = 0
    async with aiohttp.ClientSession() as session:
        while start_ms < end_ms:
            log.info(
                "Backfill: fetching from %s…",
                datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
            )
            klines = await fetch_binance_klines(
                session, "BTCUSDT", "15m", start_ms, end_ms, limit=1000
            )
            if not klines:
                break

            for k in klines:
                ts_start = datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc)
                ts_end   = datetime.fromtimestamp(k[6] / 1000, tz=timezone.utc)
                bar = AggregatedBar(
                    symbol="BTCUSDT",
                    timeframe="15m",
                    timestamp_start=ts_start,
                    timestamp_end=ts_end,
                    open=Decimal(str(k[1])),
                    high=Decimal(str(k[2])),
                    low=Decimal(str(k[3])),
                    close=Decimal(str(k[4])),
                    volume=Decimal(str(k[5])),
                    is_complete=True,
                    is_reliable=True,
                )
                await sm.save_bar(bar, instrument="BTCUSDT")
                total_saved += 1

            last_kline_ts = klines[-1][0]
            start_ms = last_kline_ts + 1
            time.sleep(0.1)  # rate limit

    log.info("Backfill complete — saved %d new bars.", total_saved)

    # Log the new DB frontier
    import asyncpg as _asyncpg
    conn2 = await _asyncpg.connect(os.environ["DATABASE_URL"])
    row2 = await conn2.fetchrow("""
        SELECT timestamp_start, close FROM bars
        WHERE instrument='BTCUSDT' AND timeframe='15m'
        ORDER BY timestamp_start DESC LIMIT 1
    """)
    log.info("DB now current to: %s  close=%s", row2["timestamp_start"], row2["close"])
    await conn2.close()
    await sm.close()


async def run_full_gap_fill() -> None:
    """Find and fill ALL internal gaps in the BTCUSDT/15m bars table.

    Uses the SQL LAG window function to locate every span > 20 minutes where
    bars are missing, then fetches those bars from Binance REST and inserts
    them with ON CONFLICT DO NOTHING — safe to re-run at any time.

    Prints a per-gap summary line and a final total.
    """
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    log = logging.getLogger(__name__)

    import asyncpg
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])

    # 1. Identify every internal gap > 20 minutes via LAG window function.
    #    Ordered ascending so we process oldest gaps first.
    gap_rows = await conn.fetch("""
        SELECT prev_ts, curr_ts, gap_minutes FROM (
            SELECT
                timestamp_start AS curr_ts,
                LAG(timestamp_start) OVER (ORDER BY timestamp_start) AS prev_ts,
                EXTRACT(EPOCH FROM (
                    timestamp_start
                    - LAG(timestamp_start) OVER (ORDER BY timestamp_start)
                )) / 60 AS gap_minutes
            FROM bars
            WHERE instrument = 'BTCUSDT' AND timeframe = '15m'
        ) t
        WHERE gap_minutes > 20
        ORDER BY prev_ts
    """)

    if not gap_rows:
        print("No gaps found — DB is clean.")
        await conn.close()
        return

    print(f"Found {len(gap_rows)} gap(s) to fill.")

    total_inserted = 0

    async with aiohttp.ClientSession() as session:
        for i, gap in enumerate(gap_rows, 1):
            prev_ts = gap["prev_ts"]
            curr_ts = gap["curr_ts"]
            gap_minutes = gap["gap_minutes"]

            if prev_ts.tzinfo is None:
                prev_ts = prev_ts.replace(tzinfo=timezone.utc)
            if curr_ts.tzinfo is None:
                curr_ts = curr_ts.replace(tzinfo=timezone.utc)

            # Fetch bars strictly between the two boundary timestamps.
            start_ms = int(prev_ts.timestamp() * 1000) + 1
            end_ms   = int(curr_ts.timestamp() * 1000) - 1

            klines = await fetch_binance_klines(
                session, "BTCUSDT", "15m", start_ms, end_ms, limit=1000
            )

            fetched = len(klines) if klines else 0
            inserted = 0

            for k in (klines or []):
                ts_start = datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc)
                ts_end   = datetime.fromtimestamp(k[6] / 1000, tz=timezone.utc)
                status = await conn.execute(
                    """
                    INSERT INTO bars
                        (instrument, timeframe, timestamp_start, timestamp_end,
                         open, high, low, close, volume, is_complete, is_reliable)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                    ON CONFLICT DO NOTHING
                    """,
                    "BTCUSDT", "15m", ts_start, ts_end,
                    Decimal(str(k[1])), Decimal(str(k[2])),
                    Decimal(str(k[3])), Decimal(str(k[4])),
                    Decimal(str(k[5])),
                    True, True,
                )
                # asyncpg returns "INSERT 0 1" on success, "INSERT 0 0" on conflict
                inserted += int(status.split()[-1])

            total_inserted += inserted
            prev_str = prev_ts.strftime("%Y-%m-%d %H:%M")
            curr_str = curr_ts.strftime("%Y-%m-%d %H:%M")
            print(
                f"Gap {i}: {prev_str} -> {curr_str}  "
                f"fetched={fetched} bars  inserted={inserted}"
            )
            time.sleep(0.1)  # rate-limit between gap requests

    await conn.close()
    print(f"\nTotal bars inserted: {total_inserted}")


async def main() -> None:
    """Entry point when run as a script: configure logging, then run_backfill."""
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    await run_backfill()


if __name__ == "__main__":
    asyncio.run(main())
