import pathlib, sys
_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
import asyncio, os
from dotenv import load_dotenv
load_dotenv()
async def main():
    from src.persistence.state_manager import StateManager
    sm = StateManager(os.environ["DATABASE_URL"])
    await sm.connect()
    # Check what snapshots exist
    import asyncpg
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])

    rows = await conn.fetch("""
        SELECT instrument, component, snapshot_at
        FROM system_state
        ORDER BY snapshot_at DESC
        LIMIT 20
    """)
    print("=== LATEST SNAPSHOTS ===")
    for r in rows:
        print(f"  {r['instrument']} | {r['component']} | {r['snapshot_at']}")

    # Check bar count per timeframe
    print("\n=== BAR COUNTS ===")
    tfs = ['15m', '1H', '4H', '1D']
    for tf in tfs:
        count = await sm.get_bar_count("BTCUSDT", tf)
        print(f"  BTCUSDT/{tf}: {count} bars")

    # Check recent bars timestamp
    recent = await sm.load_bars("BTCUSDT", "15m", after=None, limit=5, newest_first=True)
    print(f"\n=== MOST RECENT 15m BARS ===")
    for b in recent:
        print(f"  {b['timestamp_start']} close={b['close']}")

    await conn.close()
    await sm.close()
asyncio.run(main())
