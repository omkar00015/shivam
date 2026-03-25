"""Doc 2 §3: BTC/USDT 1-minute kline ingestion via Binance WebSocket.

Emits one Bar per closed 1m candle (kline.x = true).
No aggregation — raw 1m bars only. Aggregation is bar_aggregator.py's job.
BTC is 24/7 continuous (Amendment v1.1 C5): no gap handling needed here.
"""

import asyncio
import datetime
import json
import logging
import sys
from dataclasses import dataclass
from decimal import Decimal
from typing import AsyncIterator, Awaitable, Callable

import websockets

logger = logging.getLogger(__name__)

# Binance WebSocket endpoint for BTC/USDT 1m kline stream
_WS_URL = "wss://stream.binance.com:9443/ws/btcusdt@kline_1m"

# Reconnect backoff: start at 1s, double each attempt, cap at 60s
_BACKOFF_INITIAL_S = 1
_BACKOFF_MAX_S = 60


@dataclass(frozen=True)
class Bar:
    """Doc 2 §3: Universal bar object. Immutable once is_complete=True.

    All price fields use Decimal — never float (CLAUDE.md non-negotiable rule).
    Timestamps are UTC-aware datetimes (CLAUDE.md: always UTC, never naive).
    """

    symbol: str
    timestamp_start: datetime.datetime  # UTC
    timestamp_end: datetime.datetime    # UTC
    timeframe: str                      # e.g. "1m"
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    is_complete: bool


def _parse_kline_message(raw: dict) -> Bar | None:
    """Doc 2 §3: Parse a Binance kline WebSocket message into a Bar.

    Returns a Bar only when kline.x (is_closed) is True.
    Returns None for in-progress klines — those are not usable (Doc 2 §4.4).

    Prices are parsed as Decimal from the raw string values Binance sends,
    ensuring no float intermediate representation.
    """
    if raw.get("e") != "kline":
        return None

    k = raw["k"]
    if not k.get("x", False):
        # Candle not yet closed — discard (Doc 2 §4.4, §6)
        return None

    # Binance sends timestamps as milliseconds since epoch (UTC)
    ts_start = datetime.datetime.fromtimestamp(
        k["t"] / 1000, tz=datetime.timezone.utc
    )
    ts_end = datetime.datetime.fromtimestamp(
        k["T"] / 1000, tz=datetime.timezone.utc
    )

    return Bar(
        symbol=k["s"],
        timestamp_start=ts_start,
        timestamp_end=ts_end,
        timeframe="1m",
        open=Decimal(k["o"]),
        high=Decimal(k["h"]),
        low=Decimal(k["l"]),
        close=Decimal(k["c"]),
        volume=Decimal(k["v"]),
        is_complete=True,
    )


async def stream_bars(
    on_reconnect: Callable[[], Awaitable[None]] | None = None,
) -> AsyncIterator[Bar]:
    """Doc 2 §2: Yield closed 1m Bar objects from Binance WebSocket.

    Auto-reconnects with exponential backoff (1s → 2s → 4s → … → 60s max)
    on any connection error or abnormal close. Reconnect attempts are logged.

    BTC/USDT is 24/7 continuous (Amendment v1.1 C5): previous_close fill
    for missing bars within an active session is handled downstream.
    This layer emits only what the exchange sends.

    Args:
        on_reconnect: Optional async callback invoked after every reconnect
            (NOT on the initial connect). Use this to backfill DB gaps that
            accumulated during the disconnection window before resuming live
            bar processing.  Example::

                async def do_backfill():
                    from backfill import run_backfill
                    await run_backfill()

                async for bar in stream_bars(on_reconnect=do_backfill):
                    process(bar)
    """
    backoff_s = _BACKOFF_INITIAL_S
    _first_connect = True  # suppress on_reconnect on the very first connection

    while True:
        try:
            logger.info("Connecting to Binance WebSocket: %s", _WS_URL)
            async with websockets.connect(_WS_URL) as ws:
                logger.info("Connected to Binance WebSocket.")
                backoff_s = _BACKOFF_INITIAL_S  # reset on successful connect

                if not _first_connect and on_reconnect is not None:
                    logger.info("Reconnected after disconnect — running gap backfill.")
                    await on_reconnect()
                _first_connect = False

                async for raw_message in ws:
                    try:
                        data = json.loads(raw_message)
                    except json.JSONDecodeError:
                        logger.warning("Received non-JSON message; skipping.")
                        continue

                    bar = _parse_kline_message(data)
                    if bar is not None:
                        yield bar

        except (
            websockets.exceptions.ConnectionClosed,
            websockets.exceptions.WebSocketException,
            OSError,
        ) as exc:
            logger.warning(
                "WebSocket disconnected: %s. Reconnecting in %ds…",
                exc,
                backoff_s,
            )
        except Exception as exc:  # noqa: BLE001 — catch-all before backoff
            logger.error(
                "Unexpected error in WebSocket stream: %s. Reconnecting in %ds…",
                exc,
                backoff_s,
            )

        await asyncio.sleep(backoff_s)
        backoff_s = min(backoff_s * 2, _BACKOFF_MAX_S)


async def _main() -> None:
    """Acceptance criterion 1: connect and print bar dicts to stdout."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )
    async for bar in stream_bars():
        # Print as dict; Decimal converts cleanly via str()
        print(
            {
                "symbol": bar.symbol,
                "timestamp_start": bar.timestamp_start.isoformat(),
                "timestamp_end": bar.timestamp_end.isoformat(),
                "timeframe": bar.timeframe,
                "open": str(bar.open),
                "high": str(bar.high),
                "low": str(bar.low),
                "close": str(bar.close),
                "volume": str(bar.volume),
                "is_complete": bar.is_complete,
            },
            flush=True,
        )


if __name__ == "__main__":
    asyncio.run(_main())
