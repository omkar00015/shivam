import pathlib, sys
_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
import asyncio
from decimal import Decimal
from datetime import datetime, timezone
from dotenv import load_dotenv
load_dotenv()
async def test_warmup():
    import os
    from src.persistence.state_manager import StateManager
    from src.pipeline.orchestrator import Orchestrator, InstrumentContext
    print("=== WARMUP REPLAY TEST ===\n")
    sm = StateManager(os.environ["DATABASE_URL"])
    await sm.connect()
    # Check what we have in DB
    btc_15m_count = await sm.get_bar_count("BTCUSDT", "15m")
    print(f"Bars in DB: BTCUSDT/15m = {btc_15m_count}")
    meets = await sm.meets_minimum_history("BTCUSDT", "15m")
    print(f"Meets minimum history: {meets}")
    if not meets:
        print("FAIL: insufficient history")
        await sm.close()
        return
    # Load last 200 bars for warmup
    print("\nLoading last 200 x 15m bars for warmup...")
    bars_raw = await sm.load_bars("BTCUSDT", "15m", after=None, limit=200)
    print(f"Loaded {len(bars_raw)} bars")
    print(f"First bar: {bars_raw[0]['timestamp_start']}")
    print(f"Last bar:  {bars_raw[-1]['timestamp_start']}")
    print(f"Close type: {type(bars_raw[0]['close']).__name__}")
    print(f"Close value: {bars_raw[0]['close']}")
    # Verify all price fields are strings (for Decimal conversion)
    first = bars_raw[0]
    for field in ['open', 'high', 'low', 'close', 'volume']:
        val = first[field]
        assert isinstance(val, str), f"FAIL: {field} is {type(val).__name__}, expected str"
    print("\nAll price fields are strings — safe for Decimal() conversion.")
    # Convert to Decimal to verify round-trip
    close_decimal = Decimal(bars_raw[-1]['close'])
    print(f"Last close as Decimal: {close_decimal}")
    assert isinstance(close_decimal, Decimal), "FAIL: Decimal conversion failed"
    print("Decimal conversion: PASS")
    await sm.close()
    print("\n=== WARMUP TEST COMPLETE ===")
    print("DB layer is ready for orchestrator startup.")
    # Test newest_first
    print("\nTesting newest_first=True...")
    await sm.connect()
    recent_bars = await sm.load_bars("BTCUSDT", "15m", after=None, limit=200, newest_first=True)
    print(f"First bar: {recent_bars[0]['timestamp_start']}")
    print(f"Last bar:  {recent_bars[-1]['timestamp_start']}")
    assert recent_bars[0]['timestamp_start'] < recent_bars[-1]['timestamp_start'], \
        "FAIL: bars not in chronological order"
    print("PASS: most recent 200 bars returned in chronological order")
asyncio.run(test_warmup())
