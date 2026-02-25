"""Doc 2 §7: Wilder ATR — one instance per (instrument, timeframe).

Spec rules implemented:
  Doc 2 §7.1  — TR = max(high-low, |high-prev_close|, |low-prev_close|)
  Doc 2 §7.2  — First ATR = simple mean of first 14 TRs (seed phase)
  Doc 2 §7.3  — ATR[i] = (ATR[i-1] × 13 + TR[i]) / 14  (Wilder smoothing)
  Doc 2 §7.5  — Store with 6 decimal places
  Amendment v1.1 C5 — Gold gap-aware TR: first bar after a session gap uses
                       last_real_close, not any synthetic fill's close.

All arithmetic uses Decimal. No float anywhere.
"""

import datetime
import logging
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from src.data_ingest.bar_aggregator import AggregatedBar

logger = logging.getLogger(__name__)

# Doc 2 §7.2: ATR period
_ATR_PERIOD = 14

# Doc 2 §7.5: 6 decimal place quantisation
_PRECISION = Decimal("0.000001")

# Wilder smoothing constant denominator (period = 14 → weight = 13/14)
_WILDER_DENOM = Decimal(_ATR_PERIOD)
_WILDER_PREV_WEIGHT = Decimal(_ATR_PERIOD - 1)  # 13


def _true_range(
    bar: AggregatedBar,
    prev_close: Decimal,
) -> Decimal:
    """Doc 2 §7.1: TR = max(high-low, |high-prev_close|, |low-prev_close|).

    prev_close must already be the gap-aware value (last_real_close for Gold
    gap bars, or simply the previous bar's close for BTC).  The caller is
    responsible for supplying the correct prev_close.
    """
    hl  = bar.high - bar.low
    hpc = abs(bar.high - prev_close)
    lpc = abs(bar.low  - prev_close)
    return max(hl, hpc, lpc)


class ATRCalculator:
    """Doc 2 §7: Streaming Wilder ATR for one (instrument, timeframe) pair.

    Feed completed bars in chronological order via push().  Returns None
    until 14 bars have been received (seed phase).  Returns a Decimal ATR
    from bar 14 onward.

    Gold gap handling (Amendment v1.1 C5):
        Set is_continuous=False.  The calculator tracks last_real_close
        separately from prev_close.  When a session gap is detected
        (timestamp discontinuity > expected bar duration), the first real
        bar after the gap uses last_real_close as its prev_close for TR,
        ensuring the full weekend/holiday gap volatility is captured.

    Freeze API:
        Call freeze() to snapshot the current ATR value.  frozen_atr then
        returns that fixed value even as new bars continue to arrive and
        update current_atr.  Call unfreeze() to clear the snapshot.
    """

    def __init__(
        self,
        symbol: str,
        timeframe: str,
        is_continuous: bool = True,
        period: int = _ATR_PERIOD,
    ) -> None:
        """Initialise ATR calculator.

        Args:
            symbol:        Instrument symbol, e.g. "BTCUSDT" or "XAUUSD".
            timeframe:     Bar timeframe label, e.g. "15m", "1H".
            is_continuous: True → BTC mode (no gap detection).
                           False → Gold mode (gap-aware TR, Amendment C5).
            period:        ATR period. Default 14 per Doc 2 §7.2.
        """
        self._symbol = symbol
        self._timeframe = timeframe
        self._is_continuous = is_continuous
        self._period = period
        self._wilder_denom = Decimal(period)
        self._wilder_prev_weight = Decimal(period - 1)

        # Seed-phase TR accumulator (first `period` TRs for simple mean)
        self._seed_trs: list[Decimal] = []

        # State after seed phase
        self._current_atr: Optional[Decimal] = None

        # Previous bar close — used as prev_close for standard TR
        self._prev_close: Optional[Decimal] = None

        # Gold gap tracking (Amendment v1.1 C5)
        # last_real_close: close of the most recently received real bar
        # (never a synthetic fill; Gold aggregator never sends synthetic bars)
        self._last_real_close: Optional[Decimal] = None
        # timestamp_end of the most recently processed bar, for gap detection
        self._prev_bar_end: Optional[datetime.datetime] = None

        # Freeze state
        self._frozen_atr: Optional[Decimal] = None

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def current_atr(self) -> Optional[Decimal]:
        """Most recent ATR value, or None during the 14-bar seed phase."""
        return self._current_atr

    @property
    def frozen_atr(self) -> Optional[Decimal]:
        """ATR value captured at the last freeze() call, or None if not frozen."""
        return self._frozen_atr

    @property
    def is_ready(self) -> bool:
        """True once 14 bars have been processed and ATR is valid."""
        return self._current_atr is not None

    def freeze(self) -> None:
        """Snapshot current_atr. frozen_atr stays fixed until unfreeze()."""
        self._frozen_atr = self._current_atr

    def unfreeze(self) -> None:
        """Clear the frozen snapshot."""
        self._frozen_atr = None

    def push(self, bar: AggregatedBar) -> Optional[Decimal]:
        """Doc 2 §7: Process one completed bar and return updated ATR.

        Returns:
            Decimal ATR (6 d.p.) once 14 bars received; None during seed phase.

        The bar must be is_complete=True.  Incomplete bars are rejected
        silently and return None (Doc 2 §6: incomplete bars invisible to
        structure engines).

        TR for bar 1 (no previous close available):
            Uses high - low only, which is the industry-standard fallback and
            satisfies Doc 2 §7.1 when no prior close exists.  This means 14
            bars are sufficient to produce the first ATR value (bars 1-14
            give 14 TRs whose simple mean is the seed ATR).
        """
        if not bar.is_complete:
            return None

        # Determine the correct prev_close for this bar's TR calculation
        prev_close = self._resolve_prev_close(bar)

        if prev_close is None:
            # First bar ever — use high-low as TR (no prior close available).
            tr = bar.high - bar.low
        else:
            tr = _true_range(bar, prev_close)

        if self._current_atr is None:
            # --- Seed phase: accumulate TRs for simple mean (Doc 2 §7.2) ---
            self._seed_trs.append(tr)
            if len(self._seed_trs) == self._period:
                # Exactly `period` TRs collected → initialise with simple mean
                seed_sum = sum(self._seed_trs, Decimal("0"))
                raw_atr = seed_sum / self._wilder_denom
                self._current_atr = raw_atr.quantize(_PRECISION, rounding=ROUND_HALF_UP)
        else:
            # --- Wilder smoothing: ATR[i] = (ATR[i-1] × 13 + TR[i]) / 14 ---
            raw_atr = (
                (self._current_atr * self._wilder_prev_weight + tr)
                / self._wilder_denom
            )
            self._current_atr = raw_atr.quantize(_PRECISION, rounding=ROUND_HALF_UP)

        self._record_bar_state(bar)
        return self._current_atr

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_prev_close(self, bar: AggregatedBar) -> Optional[Decimal]:
        """Amendment v1.1 C5: Return the correct prev_close for TR computation.

        BTC (is_continuous=True):
            Always use self._prev_close (close of the immediately prior bar).

        Gold (is_continuous=False):
            Detect a session gap by comparing bar.timestamp_start against the
            expected next bar start (prev_bar_end + 1 tick).  On a gap, use
            self._last_real_close instead of self._prev_close so that the
            full gap volatility is captured in the TR.
        """
        if self._prev_close is None:
            # No bars seen yet — cannot compute TR
            return None

        if self._is_continuous:
            return self._prev_close

        # Gold: detect gap
        if self._prev_bar_end is not None and self._is_session_gap(bar):
            logger.debug(
                "%s %s: session gap detected before %s — using last_real_close=%s",
                self._symbol,
                self._timeframe,
                bar.timestamp_start.isoformat(),
                self._last_real_close,
            )
            # Amendment C5: use last_real_close, not the close of any
            # synthetic fill (Gold aggregator never fills, so _last_real_close
            # == _prev_close here, but the distinction matters for correctness
            # if the calling code ever inserts synthetic bars externally).
            return self._last_real_close

        return self._prev_close

    def _is_session_gap(self, bar: AggregatedBar) -> bool:
        """Amendment v1.1 C5: True if bar arrives after a real session gap.

        A session gap is present when the time between the previous bar's end
        and the current bar's start is greater than one bar-duration.  We
        measure gap as: bar.timestamp_start > prev_bar_end + 1 minute buffer.

        For Gold the aggregator emits bars only when real exchange data
        arrives (is_continuous=False → no synthetic fills), so any jump
        larger than a single bar period is a real market closure gap.
        """
        if self._prev_bar_end is None:
            return False
        # Allow 1-minute slack for timestamp rounding (bar_end uses -1µs)
        expected_next_start = self._prev_bar_end + datetime.timedelta(minutes=1)
        return bar.timestamp_start > expected_next_start

    def _record_bar_state(self, bar: AggregatedBar) -> None:
        """Update tracking state after processing bar."""
        self._prev_close = bar.close
        self._prev_bar_end = bar.timestamp_end
        # For Gold: last_real_close is always the close of the most recently
        # received real bar (never a synthetic fill; Gold has none).
        self._last_real_close = bar.close
