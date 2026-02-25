"""Doc 3 §3-§6 + Doc 3.1 + Amendment v1.1 C1 + Amendment v1.2 P1/P6: ZLBB engine.

Computes ZLEMA, sigma bands, and detects completed legs via the Full Expansion
Reversal Model.  One ZLBBEngine instance per (instrument, timeframe) pair.

Spec rules implemented:
  Doc 3 §3     — ZLEMA: lag=9, lag_adjusted_price, alpha=2/(20+1)
  Doc 3 §4     — sigma = rolling stddev(close, 20); upper/lower 2σ bands
  Doc 3 §5     — bandwidth = (upper - lower) / ZLEMA
  Doc 3.1 §4   — Bull start: bar.low <= lower_1sigma; Bear: bar.high >= upper_1sigma
  Doc 3.1 §5   — Tracking: leg_high, leg_low, eligible flag
  Doc 3.1 §6   — Completion: full −1σ → +1σ → −1σ cycle (eligible + re-cross + ZLEMA)
  Amend C1     — Band walk: >=3 consecutive closes outside 2σ band; 25% retracement gate
  Amend P1     — Band walk completion gate: suspend §6.1/§6.2 while band_walk_active=True
  Amend C1     — Squeeze: bandwidth_percentile <= 20 → eligible=True immediately on breakout
  Amend P6     — Leg quality score: 4 components (displacement, duration, efficiency, close)

All arithmetic uses Decimal.  No float anywhere.
"""

import logging
import math as _math
from collections import deque
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum, auto
from typing import Optional

from src.data_ingest.bar_aggregator import AggregatedBar
from src.indicators.atr import ATRCalculator

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants (Doc 3 §3 / user spec)
# ---------------------------------------------------------------------------
_PERIOD = 20
_LAG    = 9                              # floor((20-1)/2) = 9  (user spec overrides doc)
_ALPHA  = Decimal(2) / Decimal(_PERIOD + 1)   # 2/21
_ONE_MINUS_ALPHA = Decimal(1) - _ALPHA
_BAND_MULT = Decimal("2")               # 2σ bands (Doc 3 §4.2)
_SQUEEZE_PCT_THRESHOLD = Decimal("20")  # bandwidth_percentile <= 20 (Doc 3 §15.1)
_BAND_WALK_MIN_BARS = 3                 # consecutive closes outside same band (C1)
_BAND_WALK_RETRACE  = Decimal("0.25")  # 25% retracement gate (C1, P1)
_EPSILON = Decimal("1E-9")             # comparison epsilon (CLAUDE.md)
_D_QUARTER = Decimal("0.25")


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class LegState(Enum):
    """Doc 3.1 §3: Leg detection state machine states."""
    SEEKING     = auto()
    IN_BULL_LEG = auto()
    IN_BEAR_LEG = auto()


class LegDirection(Enum):
    """Doc 3.1 §4: Leg directional label."""
    BULL = auto()
    BEAR = auto()


# ---------------------------------------------------------------------------
# Output dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ZLBBState:
    """Doc 3 §3-§5: ZLBB indicator snapshot for one bar.

    All values are Decimal.  bandwidth_percentile may be None until the
    100-bar window is filled.
    """
    zlema:               Decimal
    sigma:               Decimal
    upper_band:          Decimal          # ZLEMA + 2σ
    lower_band:          Decimal          # ZLEMA - 2σ
    upper_1sigma:        Decimal          # ZLEMA + 1σ  (leg detection threshold)
    lower_1sigma:        Decimal          # ZLEMA - 1σ
    bandwidth:           Decimal          # (upper - lower) / ZLEMA
    bandwidth_percentile: Optional[Decimal]  # None until 100-bar window ready


@dataclass(frozen=True)
class CompletedLeg:
    """Doc 3.1 §9 + Amendment P6: Immutable completed leg output.

    Emitted by ZLBBEngine.push() when a leg closes.
    atr_at_start is frozen from the ATRCalculator at leg start time.
    """
    direction:        LegDirection
    start_price:      Decimal
    end_price:        Decimal
    displacement:     Decimal           # abs(end_price - start_price)
    bar_count:        int
    efficiency:       Decimal           # displacement / cumulative_range
    cumulative_range: Decimal           # sum of (high - low) per bar in leg
    quality:          int               # 0-4  (P6)
    atr_at_start:     Decimal           # frozen ATR at leg start
    timestamp_start:  object            # datetime.datetime (typed as object to avoid import)
    timestamp_end:    object
    is_band_walk:     bool              # True if completed via band-walk exit


# ---------------------------------------------------------------------------
# Internal mutable leg accumulator
# ---------------------------------------------------------------------------

@dataclass
class _LegAcc:
    """Mutable state for one open leg."""
    direction:        LegDirection
    start_price:      Decimal
    leg_high:         Decimal
    leg_low:          Decimal
    eligible:         bool
    bar_count:        int
    cumulative_range: Decimal
    atr_at_start:     Decimal
    timestamp_start:  object            # datetime.datetime

    # Band walk sub-state (C1)
    band_walk_active:       bool = False
    bw_consecutive_outside: int  = 0   # consecutive closes outside same 2σ band
    bw_outside_side:        Optional[str] = None   # "upper" | "lower"


# ---------------------------------------------------------------------------
# ZLEMA helper (pure function — testable in isolation)
# ---------------------------------------------------------------------------

def compute_zlema_series(
    closes: list[Decimal],
    period: int = _PERIOD,
    lag: int    = _LAG,
) -> list[Optional[Decimal]]:
    """Doc 3 §3.2-§3.3: ZLEMA over a full list of closes.

    Returns a list of the same length as closes.  Entries are None during
    warm-up (< period + lag bars needed before the EMA seeds).

    lag_adjusted_price[i] = close[i] + (close[i] - close[i - lag])
    ZLEMA[i]              = alpha * lap[i] + (1-alpha) * ZLEMA[i-1]
    Seed                  = simple mean of first `period` LAPs.
    """
    n      = len(closes)
    alpha  = Decimal(2) / Decimal(period + 1)
    one_m  = Decimal(1) - alpha
    result: list[Optional[Decimal]] = [None] * n
    zlema: Optional[Decimal] = None

    for i in range(n):
        if i < lag:
            # Cannot compute lag_adjusted_price yet
            continue
        lap = closes[i] + (closes[i] - closes[i - lag])
        if zlema is None:
            # Need `period` LAPs to seed
            # Collect from the earliest available i
            seed_start = lag                     # first index with valid LAP
            if i < seed_start + period - 1:
                continue                         # not enough LAPs yet
            # Seed: mean of LAPs from seed_start to i (inclusive)
            laps = [closes[j] + (closes[j] - closes[j - lag])
                    for j in range(seed_start, i + 1)]
            if len(laps) < period:
                continue
            zlema = sum(laps[-period:], Decimal("0")) / Decimal(period)
        else:
            zlema = alpha * lap + one_m * zlema
        result[i] = zlema

    return result


# ---------------------------------------------------------------------------
# ZLBBEngine
# ---------------------------------------------------------------------------

class ZLBBEngine:
    """Doc 3 §3-§6 + Doc 3.1 + C1/P1/P6: Streaming ZLBB + leg detector.

    One instance per (instrument, timeframe).  Call push(bar) for each
    completed bar in chronological order.

    Returns (ZLBBState | None, list[CompletedLeg]).
    ZLBBState is None during warm-up.  The completed-leg list is empty unless
    a leg closed on this bar.
    """

    def __init__(
        self,
        symbol:    str,
        timeframe: str,
        atr_calc:  ATRCalculator,
        period:    int = _PERIOD,
        lag:       int = _LAG,
    ) -> None:
        self._symbol    = symbol
        self._timeframe = timeframe
        self._atr_calc  = atr_calc
        self._period    = period
        self._lag       = lag
        self._alpha          = Decimal(2) / Decimal(period + 1)
        self._one_minus_alpha = Decimal(1) - self._alpha

        # Close history: keep enough for lag and period computation
        # Need at least lag + period values before seeding ZLEMA.
        self._closes: list[Decimal] = []

        # Streaming ZLEMA (None until seeded)
        self._zlema: Optional[Decimal] = None
        self._seeded: bool = False      # True once ZLEMA has been seeded

        # Bandwidth history for percentile (Doc 2 §11: rolling 100-bar window)
        self._bandwidths: deque[Decimal] = deque(maxlen=100)
        self._bandwidth_percentile: Optional[Decimal] = None

        # Leg state machine
        self._leg_state: LegState   = LegState.SEEKING
        self._leg: Optional[_LegAcc] = None

        # Squeeze state
        self._squeeze_active: bool = False

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def leg_state(self) -> LegState:
        return self._leg_state

    @property
    def current_leg(self) -> Optional[_LegAcc]:
        """The in-progress leg accumulator, or None when SEEKING."""
        return self._leg

    def push(
        self, bar: AggregatedBar
    ) -> tuple[Optional[ZLBBState], list[CompletedLeg]]:
        """Doc 3 §3-§6 + Doc 3.1: Process one completed bar.

        Returns (ZLBBState | None, list[CompletedLeg]).
        ZLBBState is None during warm-up or when sigma=0.
        """
        if not bar.is_complete:
            return None, []

        self._closes.append(bar.close)

        # ---- ZLEMA -------------------------------------------------------
        zlema = self._update_zlema()
        if zlema is None:
            return None, []

        # ---- Sigma (rolling population stddev, Doc 3 §4.1) ---------------
        sigma = self._compute_sigma()
        if sigma is None or sigma <= _EPSILON:
            return None, []

        # ---- Bands --------------------------------------------------------
        upper_band   = zlema + _BAND_MULT * sigma
        lower_band   = zlema - _BAND_MULT * sigma
        upper_1sigma = zlema + sigma
        lower_1sigma = zlema - sigma
        bandwidth    = (upper_band - lower_band) / zlema

        # ---- Bandwidth percentile (Doc 2 §11) ----------------------------
        self._bandwidths.append(bandwidth)
        self._bandwidth_percentile = self._calc_percentile(bandwidth)

        # ---- Squeeze detection (Doc 3 §15) --------------------------------
        pct = self._bandwidth_percentile
        self._squeeze_active = (
            pct is not None and pct <= _SQUEEZE_PCT_THRESHOLD
        )

        # ---- Leg state machine -------------------------------------------
        completed = self._step_fsm(
            bar, zlema, upper_band, lower_band, upper_1sigma, lower_1sigma
        )

        state = ZLBBState(
            zlema=zlema,
            sigma=sigma,
            upper_band=upper_band,
            lower_band=lower_band,
            upper_1sigma=upper_1sigma,
            lower_1sigma=lower_1sigma,
            bandwidth=bandwidth,
            bandwidth_percentile=self._bandwidth_percentile,
        )
        return state, completed

    # ------------------------------------------------------------------
    # ZLEMA (Doc 3 §3) — streaming incremental update
    # ------------------------------------------------------------------

    def _update_zlema(self) -> Optional[Decimal]:
        """Doc 3 §3.3: Incremental ZLEMA update.

        Seed = simple mean of first `period` lag-adjusted prices.
        After seeding: ZLEMA[i] = alpha * LAP[i] + (1-alpha) * ZLEMA[i-1].
        Returns None during warm-up.
        """
        n = len(self._closes)

        # Need at least lag+1 closes before we can form a LAP
        if n < self._lag + 1:
            return None

        i   = n - 1
        lap = self._closes[i] + (self._closes[i] - self._closes[i - self._lag])

        if not self._seeded:
            # Collect seed_period LAPs starting from index lag (first valid LAP)
            seed_start = self._lag
            if i < seed_start + self._period - 1:
                return None   # not enough LAPs yet
            # Build seed from the most recent `period` LAPs ending at i
            laps = [
                self._closes[j] + (self._closes[j] - self._closes[j - self._lag])
                for j in range(i - self._period + 1, i + 1)
            ]
            self._zlema  = sum(laps, Decimal("0")) / Decimal(self._period)
            self._seeded = True
            return self._zlema

        # Incremental update
        self._zlema = self._alpha * lap + self._one_minus_alpha * self._zlema
        return self._zlema

    # ------------------------------------------------------------------
    # Sigma (population stddev over last `period` closes)
    # ------------------------------------------------------------------

    def _compute_sigma(self) -> Optional[Decimal]:
        """Doc 3 §4.1: Rolling population stddev of close over `period` bars."""
        closes = self._closes
        if len(closes) < self._period:
            return None
        window = closes[-self._period:]
        n    = Decimal(self._period)
        mean = sum(window, Decimal("0")) / n
        var  = sum((c - mean) ** 2 for c in window) / n
        if var <= _EPSILON:
            return Decimal("0")
        # Newton's method for sqrt in Decimal (no float division of result)
        seed = Decimal(str(_math.sqrt(float(var))))
        s = seed
        for _ in range(6):
            s = (s + var / s) / 2
        return s

    # ------------------------------------------------------------------
    # Bandwidth percentile (Doc 2 §11)
    # ------------------------------------------------------------------

    def _calc_percentile(self, current_bw: Decimal) -> Optional[Decimal]:
        """Doc 2 §11: Rank current bandwidth among last <=100 bandwidths."""
        history = list(self._bandwidths)
        if len(history) < 2:
            return None
        below = sum(1 for bw in history if bw < current_bw)
        return (Decimal(below) / Decimal(len(history))) * Decimal("100")

    # ------------------------------------------------------------------
    # Leg state machine dispatcher
    # ------------------------------------------------------------------

    def _step_fsm(
        self,
        bar: AggregatedBar,
        zlema: Decimal,
        upper_band: Decimal,
        lower_band: Decimal,
        upper_1sigma: Decimal,
        lower_1sigma: Decimal,
    ) -> list[CompletedLeg]:
        if self._leg_state == LegState.SEEKING:
            leg = self._try_start_leg(bar, upper_1sigma, lower_1sigma)
            if leg is not None:
                self._leg = leg
                self._leg_state = (
                    LegState.IN_BULL_LEG
                    if leg.direction == LegDirection.BULL
                    else LegState.IN_BEAR_LEG
                )
            return []

        if self._leg_state == LegState.IN_BULL_LEG:
            result = self._tick_bull(
                bar, zlema, upper_band, lower_band, upper_1sigma, lower_1sigma
            )
            return [result] if result else []

        if self._leg_state == LegState.IN_BEAR_LEG:
            result = self._tick_bear(
                bar, zlema, upper_band, lower_band, upper_1sigma, lower_1sigma
            )
            return [result] if result else []

        return []   # unreachable

    # ------------------------------------------------------------------
    # Leg start (Doc 3.1 §4)
    # ------------------------------------------------------------------

    def _try_start_leg(
        self,
        bar: AggregatedBar,
        upper_1sigma: Decimal,
        lower_1sigma: Decimal,
    ) -> Optional[_LegAcc]:
        """Doc 3.1 §4: Detect leg start.

        Priority: bear start (bar.high >= upper_1sigma) checked first, then
        bull start (bar.low <= lower_1sigma).  A bar touching both is treated
        as a spike (E1) but the FSM still transitions — bear takes priority.
        """
        # ATR must be ready to stamp atr_at_start
        self._atr_calc.freeze()
        atr = self._atr_calc.frozen_atr
        if atr is None:
            return None   # ATR not yet warmed up

        bear = bar.high >= upper_1sigma - _EPSILON
        bull = bar.low  <= lower_1sigma + _EPSILON

        if bear:
            return _LegAcc(
                direction=LegDirection.BEAR,
                start_price=bar.high,
                leg_high=bar.high,
                leg_low=bar.low,
                eligible=self._squeeze_active,   # C1 squeeze: immediate eligible
                bar_count=1,
                cumulative_range=bar.high - bar.low,
                atr_at_start=atr,
                timestamp_start=bar.timestamp_start,
            )

        if bull:
            return _LegAcc(
                direction=LegDirection.BULL,
                start_price=bar.low,
                leg_high=bar.high,
                leg_low=bar.low,
                eligible=self._squeeze_active,
                bar_count=1,
                cumulative_range=bar.high - bar.low,
                atr_at_start=atr,
                timestamp_start=bar.timestamp_start,
            )

        return None

    # ------------------------------------------------------------------
    # Bull leg tick (Doc 3.1 §5.1 + §6.1 + C1 + P1)
    # ------------------------------------------------------------------

    def _tick_bull(
        self,
        bar: AggregatedBar,
        zlema: Decimal,
        upper_band: Decimal,
        lower_band: Decimal,
        upper_1sigma: Decimal,
        lower_1sigma: Decimal,
    ) -> Optional[CompletedLeg]:
        leg = self._leg

        # Tracking (Doc 3.1 §5.1)
        leg.leg_high = max(leg.leg_high, bar.high)
        leg.leg_low  = min(leg.leg_low,  bar.low)
        leg.bar_count += 1
        leg.cumulative_range += bar.high - bar.low

        # Eligible: has price reached +1σ? (Doc 3.1 §5.1)
        if not leg.eligible and leg.leg_high >= upper_1sigma - _EPSILON:
            leg.eligible = True

        # Band walk tracking (C1)
        self._track_band_walk(bar, leg, upper_band, lower_band)

        # P1 gate: band walk active → use band-walk exit
        if leg.band_walk_active:
            return self._bw_exit_bull(bar, leg, zlema, lower_1sigma)

        # Normal §6.1 completion
        return self._normal_complete_bull(bar, leg, zlema, lower_1sigma)

    def _normal_complete_bull(
        self,
        bar: AggregatedBar,
        leg: _LegAcc,
        zlema: Decimal,
        lower_1sigma: Decimal,
    ) -> Optional[CompletedLeg]:
        """Doc 3.1 §6.1: eligible AND low<=lower_1sigma AND close<ZLEMA."""
        if not leg.eligible:
            return None
        if bar.low > lower_1sigma + _EPSILON:
            return None
        if bar.close >= zlema - _EPSILON:
            return None
        return self._emit_bull(bar, leg, via_band_walk=False)

    def _bw_exit_bull(
        self,
        bar: AggregatedBar,
        leg: _LegAcc,
        zlema: Decimal,
        lower_1sigma: Decimal,
    ) -> Optional[CompletedLeg]:
        """P1 + C1: Band walk exit — retracement>=25% AND close<=lower_1sigma AND close<ZLEMA."""
        denom = leg.leg_high - leg.start_price
        if denom <= _EPSILON:
            return None
        retrace = (leg.leg_high - bar.close) / denom
        if retrace < _BAND_WALK_RETRACE:
            return None
        if bar.close > lower_1sigma + _EPSILON:
            return None
        if bar.close >= zlema - _EPSILON:
            return None
        return self._emit_bull(bar, leg, via_band_walk=True)

    def _emit_bull(
        self,
        bar: AggregatedBar,
        leg: _LegAcc,
        via_band_walk: bool,
    ) -> CompletedLeg:
        """Finalise bull leg and immediately open a new bear leg."""
        end_price = leg.leg_high   # Doc 3.1 §7
        displ     = end_price - leg.start_price
        cr        = leg.cumulative_range
        eff       = displ / cr if cr > _EPSILON else Decimal("0")
        q         = self._quality(leg, bar, displ, eff, LegDirection.BULL)

        completed = CompletedLeg(
            direction=LegDirection.BULL,
            start_price=leg.start_price,
            end_price=end_price,
            displacement=displ,
            bar_count=leg.bar_count,
            efficiency=eff,
            cumulative_range=cr,
            quality=q,
            atr_at_start=leg.atr_at_start,
            timestamp_start=leg.timestamp_start,
            timestamp_end=bar.timestamp_start,
            is_band_walk=via_band_walk,
        )

        logger.debug(
            "%s %s BULL leg done start=%s end=%s bars=%d q=%d bw=%s",
            self._symbol, self._timeframe,
            leg.start_price, end_price, leg.bar_count, q, via_band_walk,
        )

        # Doc 3.1 §6.1 transition: new bear leg from end_price
        self._atr_calc.freeze()
        new_atr = self._atr_calc.frozen_atr or leg.atr_at_start
        self._leg = _LegAcc(
            direction=LegDirection.BEAR,
            start_price=end_price,
            leg_high=bar.high,
            leg_low=bar.low,
            eligible=False,
            bar_count=1,
            cumulative_range=bar.high - bar.low,
            atr_at_start=new_atr,
            timestamp_start=bar.timestamp_start,
        )
        self._leg_state = LegState.IN_BEAR_LEG
        return completed

    # ------------------------------------------------------------------
    # Bear leg tick (Doc 3.1 §5.2 + §6.2 + C1 + P1)
    # ------------------------------------------------------------------

    def _tick_bear(
        self,
        bar: AggregatedBar,
        zlema: Decimal,
        upper_band: Decimal,
        lower_band: Decimal,
        upper_1sigma: Decimal,
        lower_1sigma: Decimal,
    ) -> Optional[CompletedLeg]:
        leg = self._leg

        # Tracking (Doc 3.1 §5.2)
        leg.leg_high = max(leg.leg_high, bar.high)
        leg.leg_low  = min(leg.leg_low,  bar.low)
        leg.bar_count += 1
        leg.cumulative_range += bar.high - bar.low

        # Eligible: has price reached -1σ? (Doc 3.1 §5.2)
        if not leg.eligible and leg.leg_low <= lower_1sigma + _EPSILON:
            leg.eligible = True

        # Band walk tracking (C1)
        self._track_band_walk(bar, leg, upper_band, lower_band)

        # P1 gate
        if leg.band_walk_active:
            return self._bw_exit_bear(bar, leg, zlema, upper_1sigma)

        return self._normal_complete_bear(bar, leg, zlema, upper_1sigma)

    def _normal_complete_bear(
        self,
        bar: AggregatedBar,
        leg: _LegAcc,
        zlema: Decimal,
        upper_1sigma: Decimal,
    ) -> Optional[CompletedLeg]:
        """Doc 3.1 §6.2: eligible AND high>=upper_1sigma AND close>ZLEMA."""
        if not leg.eligible:
            return None
        if bar.high < upper_1sigma - _EPSILON:
            return None
        if bar.close <= zlema + _EPSILON:
            return None
        return self._emit_bear(bar, leg, via_band_walk=False)

    def _bw_exit_bear(
        self,
        bar: AggregatedBar,
        leg: _LegAcc,
        zlema: Decimal,
        upper_1sigma: Decimal,
    ) -> Optional[CompletedLeg]:
        """P1 + C1: Band walk exit — retracement>=25% AND close>=upper_1sigma AND close>ZLEMA."""
        denom = leg.start_price - leg.leg_low
        if denom <= _EPSILON:
            return None
        retrace = (bar.close - leg.leg_low) / denom
        if retrace < _BAND_WALK_RETRACE:
            return None
        if bar.close < upper_1sigma - _EPSILON:
            return None
        if bar.close <= zlema + _EPSILON:
            return None
        return self._emit_bear(bar, leg, via_band_walk=True)

    def _emit_bear(
        self,
        bar: AggregatedBar,
        leg: _LegAcc,
        via_band_walk: bool,
    ) -> CompletedLeg:
        """Finalise bear leg and immediately open a new bull leg."""
        end_price = leg.leg_low    # Doc 3.1 §7
        displ     = leg.start_price - end_price
        cr        = leg.cumulative_range
        eff       = displ / cr if cr > _EPSILON else Decimal("0")
        q         = self._quality(leg, bar, displ, eff, LegDirection.BEAR)

        completed = CompletedLeg(
            direction=LegDirection.BEAR,
            start_price=leg.start_price,
            end_price=end_price,
            displacement=displ,
            bar_count=leg.bar_count,
            efficiency=eff,
            cumulative_range=cr,
            quality=q,
            atr_at_start=leg.atr_at_start,
            timestamp_start=leg.timestamp_start,
            timestamp_end=bar.timestamp_start,
            is_band_walk=via_band_walk,
        )

        logger.debug(
            "%s %s BEAR leg done start=%s end=%s bars=%d q=%d bw=%s",
            self._symbol, self._timeframe,
            leg.start_price, end_price, leg.bar_count, q, via_band_walk,
        )

        # Doc 3.1 §6.2 transition: new bull leg from end_price
        self._atr_calc.freeze()
        new_atr = self._atr_calc.frozen_atr or leg.atr_at_start
        self._leg = _LegAcc(
            direction=LegDirection.BULL,
            start_price=end_price,
            leg_high=bar.high,
            leg_low=bar.low,
            eligible=False,
            bar_count=1,
            cumulative_range=bar.high - bar.low,
            atr_at_start=new_atr,
            timestamp_start=bar.timestamp_start,
        )
        self._leg_state = LegState.IN_BULL_LEG
        return completed

    # ------------------------------------------------------------------
    # Band walk state tracker (C1)
    # ------------------------------------------------------------------

    def _track_band_walk(
        self,
        bar: AggregatedBar,
        leg: _LegAcc,
        upper_band: Decimal,
        lower_band: Decimal,
    ) -> None:
        """Amendment v1.1 C1: Update band walk consecutive-close counter.

        Activation: 3+ consecutive closes outside the SAME 2σ band.
        Once activated, band_walk_active stays True until the leg completes.
        The streak counter only matters for activation; post-activation the
        completion gate (P1) takes over.
        """
        above = bar.close > upper_band + _EPSILON
        below = bar.close < lower_band - _EPSILON

        if above:
            side = "upper"
        elif below:
            side = "lower"
        else:
            side = None

        if not leg.band_walk_active:
            if side is not None and side == leg.bw_outside_side:
                leg.bw_consecutive_outside += 1
            elif side is not None:
                leg.bw_outside_side        = side
                leg.bw_consecutive_outside = 1
            else:
                leg.bw_outside_side        = None
                leg.bw_consecutive_outside = 0

            if leg.bw_consecutive_outside >= _BAND_WALK_MIN_BARS:
                leg.band_walk_active = True
                logger.debug(
                    "%s %s band_walk_active after %d consecutive outside closes",
                    self._symbol, self._timeframe, leg.bw_consecutive_outside,
                )

    # ------------------------------------------------------------------
    # Leg quality score (Amendment P6)
    # ------------------------------------------------------------------

    def _quality(
        self,
        leg: _LegAcc,
        completion_bar: AggregatedBar,
        displacement: Decimal,
        efficiency: Decimal,
        direction: LegDirection,
    ) -> int:
        """Amendment P6: quality score 0-4.

        +1 displacement >= 1.0 * atr_at_start
        +1 bar_count >= 5
        +1 efficiency >= 0.35
        +1 decisive close (completion bar close in outer 25% in reversal dir)
        """
        score = 0

        # Component 1: displacement
        if displacement >= leg.atr_at_start - _EPSILON:
            score += 1

        # Component 2: duration
        if leg.bar_count >= 5:
            score += 1

        # Component 3: efficiency
        if efficiency >= Decimal("0.35") - _EPSILON:
            score += 1

        # Component 4: decisive close (P6)
        bar_range = completion_bar.high - completion_bar.low
        if bar_range > _EPSILON:
            if direction == LegDirection.BULL:
                # Reversal is downward — close in lower 25% of bar
                threshold = completion_bar.low + _D_QUARTER * bar_range
                if completion_bar.close <= threshold + _EPSILON:
                    score += 1
            else:
                # Reversal is upward — close in upper 25% of bar
                threshold = completion_bar.high - _D_QUARTER * bar_range
                if completion_bar.close >= threshold - _EPSILON:
                    score += 1

        return score
