"""Doc 2 §4: Timeframe aggregation from 1m Bar stream.

Receives closed 1m Bar objects from binance_ws.py and emits a completed
AggregatedBar whenever a higher-timeframe window closes.

Supported output timeframes: 15m, 1H, 4H, 1D, 1W, 1M, 3M.

Key spec rules implemented:
  Doc 2 §4.2  — OHLCV: open=first, high=max, low=min, close=last, volume=sum
  Doc 2 §4.3  — Alignment Law: accumulate only from valid boundary starts
  Doc 2 §4.4  — Completion Law: emit only when last sub-bar closes
  Doc 2 §5    — Missing Data Policy: fill/mark/discard by gap fraction
  Amendment v1.1 C5 — BTC: fill missing 1m bars; Gold: real gaps, do NOT fill
  Amendment v1.1 D1 — 1W/1M/3M for BTC ideally fetched natively; this module
                      handles them from the stream for completeness (fallback).

All arithmetic uses Decimal. No float anywhere.
"""

import calendar
import datetime
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterator

from src.data_ingest.binance_ws import Bar

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Missing-data thresholds (Doc 2 §5)
# ---------------------------------------------------------------------------
_FILL_THRESHOLD = Decimal("0.10")     # < 10%  → fill with prev_close
_RELIABLE_THRESHOLD = Decimal("0.25") # 10-25% → is_reliable = False
# > 25% → discard entire HTF bar


# ---------------------------------------------------------------------------
# AggregatedBar — extends Bar concept with is_reliable (Doc 2 §5)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AggregatedBar:
    """Doc 2 §3 + §5: Immutable aggregated bar for any timeframe.

    is_complete = True always (only emitted when window closes, Doc 2 §4.4).
    is_reliable = False when 10-25% of constituent 1m bars were missing
                  and filled with prev_close (Doc 2 §5).
    All price fields are Decimal — never float.
    """

    symbol: str
    timestamp_start: datetime.datetime   # UTC, aligned to TF boundary
    timestamp_end: datetime.datetime     # UTC, last sub-bar end
    timeframe: str                       # "15m", "1H", "4H", "1D", "1W", "1M", "3M"
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    is_complete: bool                    # always True when emitted
    is_reliable: bool                    # False if 10-25% bars were synthetic


# ---------------------------------------------------------------------------
# Alignment helpers (Doc 2 §4.3)
# ---------------------------------------------------------------------------

def _boundary_15m(dt: datetime.datetime) -> datetime.datetime:
    """Return the 15m boundary that dt falls on, or None if dt is not aligned."""
    if dt.second != 0 or dt.microsecond != 0:
        return None
    if dt.minute % 15 != 0:
        return None
    return dt.replace(second=0, microsecond=0)


def _boundary_1h(dt: datetime.datetime) -> datetime.datetime | None:
    """Doc 2 §4.3: 1H bars start at :00 of each hour."""
    if dt.minute == 0 and dt.second == 0 and dt.microsecond == 0:
        return dt
    return None


def _boundary_4h(dt: datetime.datetime) -> datetime.datetime | None:
    """Doc 2 §4.3: 4H bars start at 00, 04, 08, 12, 16, 20 UTC."""
    if dt.minute == 0 and dt.second == 0 and dt.microsecond == 0:
        if dt.hour % 4 == 0:
            return dt
    return None


def _boundary_1d(dt: datetime.datetime) -> datetime.datetime | None:
    """Doc 2 §4.3: Daily bars start at 00:00 UTC."""
    if dt.hour == 0 and dt.minute == 0 and dt.second == 0 and dt.microsecond == 0:
        return dt
    return None


def _boundary_1w(dt: datetime.datetime) -> datetime.datetime | None:
    """Amendment v1.1 D1: Weekly bars start Monday 00:00 UTC."""
    if (dt.weekday() == 0  # Monday
            and dt.hour == 0 and dt.minute == 0
            and dt.second == 0 and dt.microsecond == 0):
        return dt
    return None


def _boundary_1mo(dt: datetime.datetime) -> datetime.datetime | None:
    """Monthly bars start on the 1st of the month at 00:00 UTC."""
    if (dt.day == 1 and dt.hour == 0 and dt.minute == 0
            and dt.second == 0 and dt.microsecond == 0):
        return dt
    return None


def _boundary_3mo(dt: datetime.datetime) -> datetime.datetime | None:
    """3M bars start Jan/Apr/Jul/Oct 1st at 00:00 UTC (Amendment v1.1 D1)."""
    if (dt.month in (1, 4, 7, 10) and dt.day == 1
            and dt.hour == 0 and dt.minute == 0
            and dt.second == 0 and dt.microsecond == 0):
        return dt
    return None


# Map timeframe label → boundary checker
_BOUNDARY_CHECKERS: dict[str, callable] = {
    "15m": _boundary_15m,
    "1H":  _boundary_1h,
    "4H":  _boundary_4h,
    "1D":  _boundary_1d,
    "1W":  _boundary_1w,
    "1M":  _boundary_1mo,
    "3M":  _boundary_3mo,
}

# ---------------------------------------------------------------------------
# Expected 1m bar counts per window (Doc 2 §4.1, calendar-based for 1W+)
# Fixed-count timeframes:
# ---------------------------------------------------------------------------
_FIXED_1M_COUNTS: dict[str, int] = {
    "15m": 15,
    "1H":  60,
    "4H":  240,
    "1D":  1440,
    "1W":  10080,  # 7 * 1440
}
# 1M and 3M counts are calendar-dependent — computed at window-close time.


def _expected_1m_count_for_window(
    tf: str,
    window_start: datetime.datetime,
    window_end: datetime.datetime,
) -> int:
    """Doc 2 §4.1: Return expected number of 1m bars in the window."""
    if tf in _FIXED_1M_COUNTS:
        return _FIXED_1M_COUNTS[tf]
    # Calendar-based: exact minutes between boundaries
    delta = window_end - window_start
    return int(delta.total_seconds() // 60)


def _next_month_start(dt: datetime.datetime) -> datetime.datetime:
    """Return the first moment of the month after dt."""
    if dt.month == 12:
        return dt.replace(year=dt.year + 1, month=1, day=1,
                          hour=0, minute=0, second=0, microsecond=0)
    return dt.replace(month=dt.month + 1, day=1,
                      hour=0, minute=0, second=0, microsecond=0)


def _next_quarter_start(dt: datetime.datetime) -> datetime.datetime:
    """Return the first moment of the quarter after dt (Jan/Apr/Jul/Oct)."""
    quarter_starts = (1, 4, 7, 10)
    for m in quarter_starts:
        if dt.month < m:
            return dt.replace(month=m, day=1, hour=0, minute=0,
                              second=0, microsecond=0)
    return dt.replace(year=dt.year + 1, month=1, day=1,
                      hour=0, minute=0, second=0, microsecond=0)


# ---------------------------------------------------------------------------
# Window state — one per active timeframe
# ---------------------------------------------------------------------------

@dataclass
class _WindowState:
    """Mutable accumulator for one open HTF window."""

    tf: str
    symbol: str
    window_start: datetime.datetime      # UTC boundary where window opened
    window_end_expected: datetime.datetime  # UTC moment the window closes
    bars: list[Bar]                      # received 1m bars, in arrival order
    filled_count: int                    # synthetic fill bars added


# ---------------------------------------------------------------------------
# BarAggregator
# ---------------------------------------------------------------------------

class BarAggregator:
    """Doc 2 §4: Stateful aggregator. Feed 1m bars; receive HTF bars.

    Usage:
        agg = BarAggregator(symbol="BTCUSDT", is_continuous=True)
        for bar_1m in source:
            for htf_bar in agg.push(bar_1m):
                downstream_process(htf_bar)

    is_continuous=True  → BTC mode: fill short gaps with prev_close (Doc 2 §5).
    is_continuous=False → Gold mode: session gaps are real, do not fill (C5).
    """

    # Ordered list of timeframes to aggregate, from smallest to largest.
    # Each larger TF is built from the same 1m stream (not from 15m bars),
    # which is correct: boundaries are checked independently per TF.
    TIMEFRAMES: tuple[str, ...] = ("15m", "1H", "4H", "1D", "1W", "1M", "3M")

    def __init__(self, symbol: str, is_continuous: bool = True) -> None:
        """Doc 2 §4: Initialise aggregator.

        Args:
            symbol:        Instrument symbol, e.g. "BTCUSDT".
            is_continuous: True for BTC (24/7, fill gaps). False for Gold.
        """
        self._symbol = symbol
        self._is_continuous = is_continuous
        # One window slot per TF; None = waiting for first valid boundary.
        self._windows: dict[str, _WindowState | None] = {
            tf: None for tf in self.TIMEFRAMES
        }
        self._prev_close: Decimal | None = None  # last confirmed 1m close

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def push(self, bar: Bar) -> list[AggregatedBar]:
        """Doc 2 §4: Process one closed 1m bar. Return any completed HTF bars.

        Multiple HTF bars can complete on the same 1m bar (e.g., at midnight
        UTC both a 1H, 4H, and 1D bar close simultaneously).
        Returns them ordered smallest → largest timeframe.
        """
        if not bar.is_complete:
            # Doc 2 §6: incomplete bars are invisible to structure engines.
            return []

        emitted: list[AggregatedBar] = []

        for tf in self.TIMEFRAMES:
            result = self._push_to_window(tf, bar)
            if result is not None:
                emitted.append(result)

        # Update prev_close after processing all TFs (so fill logic uses
        # the close from the bar just received, not the current bar).
        self._prev_close = bar.close
        return emitted

    # ------------------------------------------------------------------
    # Internal per-TF window logic
    # ------------------------------------------------------------------

    def _push_to_window(self, tf: str, bar: Bar) -> AggregatedBar | None:
        """Handle one 1m bar for one TF window. Return AggregatedBar or None."""
        checker = _BOUNDARY_CHECKERS[tf]
        state = self._windows[tf]

        if state is None:
            # No open window yet — wait for a valid boundary start.
            # Doc 2 §4.3: aggregation begins only at next valid boundary.
            boundary = checker(bar.timestamp_start)
            if boundary is None:
                return None  # mid-cycle arrival, skip
            # Valid boundary found — open a new window.
            state = self._open_window(tf, bar)
            self._windows[tf] = state
            state.bars.append(bar)
            return None  # window just opened, not complete yet

        # --- Gap detection (BTC only, Doc 2 §5 + Amendment v1.1 C5) ---
        if self._is_continuous and self._prev_close is not None:
            gap_bars = self._fill_gap_if_needed(state, bar)
            # gap_bars were already appended to state.bars inside the helper

        # Append the real bar.
        state.bars.append(bar)

        # --- Check if this bar closes the window ---
        if self._window_is_complete(tf, state, bar):
            completed = self._close_window(tf, state, bar)
            self._windows[tf] = None  # reset; next bar opens a new window
            return completed

        return None

    def _open_window(self, tf: str, first_bar: Bar) -> _WindowState:
        """Doc 2 §4.3: Open a new window aligned to the boundary of first_bar."""
        ws = first_bar.timestamp_start
        we = self._compute_window_end(tf, ws)
        return _WindowState(
            tf=tf,
            symbol=first_bar.symbol,
            window_start=ws,
            window_end_expected=we,
            bars=[],
            filled_count=0,
        )

    def _compute_window_end(
        self, tf: str, window_start: datetime.datetime
    ) -> datetime.datetime:
        """Return the datetime at which this window closes (exclusive end)."""
        if tf == "15m":
            return window_start + datetime.timedelta(minutes=15)
        if tf == "1H":
            return window_start + datetime.timedelta(hours=1)
        if tf == "4H":
            return window_start + datetime.timedelta(hours=4)
        if tf == "1D":
            return window_start + datetime.timedelta(days=1)
        if tf == "1W":
            return window_start + datetime.timedelta(weeks=1)
        if tf == "1M":
            return _next_month_start(window_start)
        if tf == "3M":
            return _next_quarter_start(window_start)
        raise ValueError(f"Unknown timeframe: {tf}")

    def _window_is_complete(
        self, tf: str, state: _WindowState, bar: Bar
    ) -> bool:
        """Doc 2 §4.4: Window closes when bar.timestamp_start reaches window_end."""
        # The window closes when we receive the first 1m bar whose start
        # equals or exceeds the window's expected end. That means the last
        # bar inside the window has just been received (bar.timestamp_start
        # is the last minute of the window = window_end - 1 minute).
        # More precisely: window_end is the *open* of the first bar OUTSIDE
        # the window. So the window is complete when the current bar's
        # timestamp_start + 1 minute == window_end.
        next_bar_start = bar.timestamp_start + datetime.timedelta(minutes=1)
        return next_bar_start >= state.window_end_expected

    def _fill_gap_if_needed(
        self, state: _WindowState, incoming: Bar
    ) -> list[Bar]:
        """Doc 2 §5 + Amendment v1.1 C5: Fill short gaps for continuous instruments.

        If the incoming bar is not the immediately next 1m bar after the last
        received bar in this window, synthesise fill bars (prev_close fill).
        Gap bars have open=high=low=close=prev_close, volume=0.

        Only fills gaps where the gap falls INSIDE the current window.
        Bars that belong to a prior (unclosed) window period are not filled
        here — that case is handled by the discard logic in _close_window.
        """
        if not state.bars:
            return []

        last_bar = state.bars[-1]
        expected_next = last_bar.timestamp_start + datetime.timedelta(minutes=1)
        fills: list[Bar] = []

        ts = expected_next
        while ts < incoming.timestamp_start and ts < state.window_end_expected:
            fill = Bar(
                symbol=state.symbol,
                timestamp_start=ts,
                timestamp_end=ts + datetime.timedelta(minutes=1)
                             - datetime.timedelta(microseconds=1),
                timeframe="1m",
                open=self._prev_close,
                high=self._prev_close,
                low=self._prev_close,
                close=self._prev_close,
                volume=Decimal("0"),
                is_complete=True,
            )
            state.bars.append(fill)
            state.filled_count += 1
            fills.append(fill)
            ts += datetime.timedelta(minutes=1)

        if fills:
            logger.debug(
                "%s %s: filled %d gap bar(s) at %s",
                state.symbol, state.tf, len(fills), expected_next.isoformat(),
            )
        return fills

    def _close_window(
        self, tf: str, state: _WindowState, last_bar: Bar
    ) -> AggregatedBar | None:
        """Doc 2 §4.2 + §5: Aggregate and emit the completed window.

        Applies the missing-data policy:
          > 25% missing → discard (return None, log warning)
          10-25% missing → emit with is_reliable=False
          < 10% missing → emit with is_reliable=True (fill already applied)
        """
        expected_count = _expected_1m_count_for_window(
            tf, state.window_start, state.window_end_expected
        )
        # filled_count tracks synthetic bars injected for BTC gaps.
        # Those are "missing" real bars — use them directly rather than
        # computing expected_count - len(bars), which would be 0 after filling.
        missing = state.filled_count
        missing_frac = (
            Decimal(missing) / Decimal(expected_count)
            if expected_count > 0
            else Decimal("0")
        )

        # Doc 2 §5: > 25% missing → discard
        if missing_frac > _RELIABLE_THRESHOLD:
            logger.warning(
                "%s %s window %s: %.1f%% bars missing (>25%%) — discarding.",
                state.symbol, tf,
                state.window_start.isoformat(),
                float(missing_frac * 100),
            )
            return None

        is_reliable = missing_frac <= _FILL_THRESHOLD  # False if 10-25%
        if not is_reliable:
            logger.warning(
                "%s %s window %s: %.1f%% bars missing (10-25%%) — is_reliable=False.",
                state.symbol, tf,
                state.window_start.isoformat(),
                float(missing_frac * 100),
            )

        bars = state.bars
        # Doc 2 §4.2: OHLCV aggregation rules — mandatory, no alternatives.
        agg_open   = bars[0].open
        agg_high   = max(b.high for b in bars)
        agg_low    = min(b.low  for b in bars)
        agg_close  = bars[-1].close
        agg_volume = sum((b.volume for b in bars), Decimal("0"))

        return AggregatedBar(
            symbol=state.symbol,
            timestamp_start=state.window_start,
            timestamp_end=last_bar.timestamp_start
                         + datetime.timedelta(minutes=1)
                         - datetime.timedelta(microseconds=1),
            timeframe=tf,
            open=agg_open,
            high=agg_high,
            low=agg_low,
            close=agg_close,
            volume=agg_volume,
            is_complete=True,
            is_reliable=is_reliable,
        )


# ---------------------------------------------------------------------------
# Batch helper (for backtest / replay use — Doc 12 §2: same logic as live)
# ---------------------------------------------------------------------------

def aggregate_bars_batch(
    bars_1m: list[Bar],
    symbol: str,
    is_continuous: bool = True,
) -> dict[str, list[AggregatedBar]]:
    """Doc 2 §4: Aggregate a list of 1m bars into all HTF timeframes.

    Returns a dict mapping timeframe label → list of completed AggregatedBars.
    Useful for backtest replay and startup history rebuild (Doc 9 §14).
    """
    agg = BarAggregator(symbol=symbol, is_continuous=is_continuous)
    result: dict[str, list[AggregatedBar]] = {tf: [] for tf in BarAggregator.TIMEFRAMES}
    for bar in bars_1m:
        for htf_bar in agg.push(bar):
            result[htf_bar.timeframe].append(htf_bar)
    return result
