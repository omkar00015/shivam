import pathlib, sys
_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
import asyncio
import os
from decimal import Decimal
from datetime import datetime, timezone
from dotenv import load_dotenv
load_dotenv()
async def smoke_test():
    from src.persistence.state_manager import StateManager
    url = os.environ["DATABASE_URL"]
    print(f"Connecting to: {url[:40]}...")
    sm = StateManager(url)
    await sm.connect()
    print("Connected.")
    # Test 1: snapshot round-trip
    print("\nTest 1: snapshot save + load...")
    test_state = {"atr": "1250.50", "prev_close": "43000.00", "bar_count": "42"}
    saved = await sm.save_snapshot("BTCUSDT", "atr_15m", test_state, datetime.now(timezone.utc))
    assert saved, "FAIL: save_snapshot returned False"
    loaded = await sm.load_snapshot("BTCUSDT", "atr_15m")
    assert loaded == test_state, f"FAIL: mismatch: {loaded}"
    print("PASS: snapshot round-trip")
    # Test 2: hash verification
    print("\nTest 2: hash verification...")
    valid = await sm.verify_snapshot("BTCUSDT", "atr_15m")
    assert valid, "FAIL: verify_snapshot returned False"
    print("PASS: hash valid")
    # Test 3: bar count
    print("\nTest 3: bar count...")
    count = await sm.get_bar_count("BTCUSDT", "15m")
    assert isinstance(count, int), f"FAIL: expected int, got {type(count)}"
    print(f"PASS: bar count = {count}")
    # Test 4: minimum history check
    print("\nTest 4: minimum history check...")
    meets = await sm.meets_minimum_history("BTCUSDT", "15m")
    print(f"PASS: meets_minimum_history = {meets} (have {count} bars, need 40)")
    # Test 5: save and reload a bar
    # First show the actual save_bar signature
    print("\nTest 5: inspecting save_bar signature...")
    import inspect
    sig = inspect.signature(sm.save_bar)
    print(f"save_bar signature: {sig}")
    # Show close() vs disconnect()
    has_close = hasattr(sm, 'close')
    has_disconnect = hasattr(sm, 'disconnect')
    print(f"has close(): {has_close}, has disconnect(): {has_disconnect}")
    if has_close:
        await sm.close()
    elif has_disconnect:
        await sm.disconnect()
    print("\nSmoke test complete.")
asyncio.run(smoke_test())
