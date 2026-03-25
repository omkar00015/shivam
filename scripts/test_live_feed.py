import pathlib, sys
_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
import asyncio
from dotenv import load_dotenv
load_dotenv()
async def test_feed():
    from src.data_ingest.binance_ws import stream_bars
    print("Connecting to Binance WebSocket...")
    print("Waiting for first closed 1m bar (may take up to 60 seconds)...")
    print("Press Ctrl+C to stop after you see the first bar.\n")
    bar_count = 0
    async for bar in stream_bars():
        bar_count += 1
        print(f"Bar {bar_count}: {bar.symbol} | "
              f"open={bar.open} high={bar.high} "
              f"low={bar.low} close={bar.close} | "
              f"volume={bar.volume} | "
              f"ts={bar.timestamp_start}")
        print(f"  Types: open={type(bar.open).__name__} "
              f"close={type(bar.close).__name__}")

        if bar_count >= 3:
            print("\n3 bars received. Feed is working.")
            break
asyncio.run(test_feed())
