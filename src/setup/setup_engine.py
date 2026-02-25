"""Doc 6 + Doc 6.1 + Amendment v1.1 (C2, M2, M3, M6, M8) + Amendment v1.2 (P4, P9):
Setup Engine — four deterministic setups, no ML, no float.

AUTHORITY: Doc 6.1 > Doc 6 > Amendments.

Setup Taxonomy (4 only — M8 removed Setup 5):
  1. PULLBACK_CONTINUATION  (Doc 6 §6 + M3)
  2. BREAKOUT_RETEST        (Doc 6 §7 + C2 + P4)
  3. FAKEOUT                (Doc 6.1 — Spring / Upthrust)
  4. RANGE_FADE             (Amendment v1.2 P9)

Global Pre-Filter (Doc 6 §3 + C2):
  - Tier: S, A, B only — skip C
  - State: reject EXPIRED, FROZEN
    Exception C2: BROKEN eligible ONLY for Setup 2
    FLIPPED treated as fresh zone of opposite polarity → eligible for 1, 3, 4
  - Distance: price within 2×ATR(1H) of zone boundary
  - Space: expected_R >= minimum_R (2.0 for TREND, 1.5 for BALANCE)

Setup 3 (Fakeout) is the primary entry mechanism — implement first and most carefully.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum, auto
from typing import Optional, Sequence

from src.data_ingest.bar_aggregator import AggregatedBar
from src.indicators.zlbb import CompletedLeg, LegDirection, LegState, ZLBBState
from src.phase.phase_engine import Phase, PhaseResult
from src.sr.zone_detector import SRZone, Tier, ZonePolarity, ZoneState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_EPSILON            = Decimal("1E-9")
_D_ZERO             = Decimal("0")
_D_ONE              = Decimal("1")

_PROXIMITY_ATR_MULT = Decimal("2")   # Doc 6 §3.3: distance <= 2×ATR(1H)
_STOP_BUFFER_MULT   = Decimal("0.10")  # Doc 6 §6.5, M3, P9: 0.10×ATR(15m) buffer
_ENTRY_BUFFER_MULT  = Decimal("0.01")  # Doc 6 §6.5: entry = conf high + 0.01×ATR(15m)

_MIN_R_TREND        = Decimal("2.0")   # Doc 6 §3.4: minimum R:R for TREND setups
_MIN_R_BALANCE      = Decimal("1.5")   # P9: minimum R:R for BALANCE (Range Fade)

_FAKEOUT_WINDOW     = 4               # Doc 6.1: 4-candle window for fakeout reclaim
_PULLBACK_EXPIRY    = 5               # Doc 6 §6.6: 5 bars until candidate expires
_RANGE_FADE_EXPIRY  = 5               # P9: 5 bars until candidate expires

_REJECTION_WICK_MIN = Decimal("0.50")  # wick >= 50% of bar range
_BODY_MIN_FRAC      = Decimal("0.60")  # Setup 2 confirmation: body >= 60% of range

_MEASURED_MOVE_MULT = Decimal("1.5")   # M6: fallback target = 1.5×stop_distance

# Zone states eligible for each setup (C2 table)
_ELIGIBLE_ALL    = {ZoneState.FRESH, ZoneState.TESTED, ZoneState.FLIPPED}
_ELIGIBLE_S2     = {ZoneState.BREAK_PENDING, ZoneState.BROKEN_CONFIRMED, ZoneState.FLIPPED}
# FLIPPED is also in ELIGIBLE_ALL — it gets FLIPPED polarity treatment in all setups


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class SetupType(Enum):
    """Doc 6 §4 (amended by M8): Four legal setups."""
    PULLBACK_CONTINUATION = auto()
    BREAKOUT_RETEST       = auto()
    FAKEOUT               = auto()
    RANGE_FADE            = auto()


class CandidateStatus(Enum):
    """Lifecycle of a setup candidate."""
    WAITING_ENTRY = auto()   # pending trigger
    ACTIVE        = auto()   # fakeout: in 4-bar window, watching for reclaim
    FILLED        = auto()   # entry confirmed (external — not managed here)
    EXPIRED       = auto()   # time expiry reached
    CANCELLED     = auto()   # invalidation condition fired


class Direction(Enum):
    """Trade direction."""
    LONG  = auto()
    SHORT = auto()


# ---------------------------------------------------------------------------
# SetupCandidate — frozen output object
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SetupCandidate:
    """Doc 6 §12: Immutable candidate object created at confirmation.

    expected_R is FROZEN at creation (M6). Never recomputed.
    """
    candidate_id:  str           # deterministic sha256 hash
    setup_type:    SetupType
    direction:     Direction
    zone_id:       str           # reference to SRZone.zone_id
    entry_price:   Decimal       # reference price for entry order
    stop_price:    Decimal
    target_price:  Decimal
    expected_R:    Decimal       # (target_dist / stop_dist) — frozen at creation
    created_at:    datetime
    expiry_bars:   int           # bars from creation before auto-cancel
    status:        CandidateStatus = CandidateStatus.WAITING_ENTRY
    # Setup 3 only: tracks bars elapsed in 4-bar reclaim window
    fakeout_bars_elapsed: int = 0
    fakeout_reclaimed:    bool = False


# ---------------------------------------------------------------------------
# Mutable internal tracking for fakeout window
# ---------------------------------------------------------------------------

@dataclass
class _FakeoutWatch:
    """Internal mutable tracker for a fakeout candidate's 4-bar window.

    Created when penetration occurs; converted to SetupCandidate on reclaim.
    """
    zone_id:        str
    direction:      Direction        # LONG = Bear Fakeout (Spring); SHORT = Bull Fakeout (Upthrust)
    zone_low:       Decimal
    zone_high:      Decimal
    t0_bar:         AggregatedBar    # bar that triggered penetration
    bars_elapsed:   int = 0
    had_failure_close: bool = False  # at least one close >= zone_low (bear) / <= zone_high (bull)
    cancelled:      bool = False

    def is_expired(self) -> bool:
        return self.bars_elapsed >= _FAKEOUT_WINDOW and not self.cancelled


# ---------------------------------------------------------------------------
# Shared confirmation helpers (Doc 6 §6.4 + M2 — no duplication)
# ---------------------------------------------------------------------------

def _bar_range(bar: AggregatedBar) -> Decimal:
    """Total bar range (high - low)."""
    return bar.high - bar.low


def is_bull_rejection_bar(bar: AggregatedBar) -> bool:
    """Doc 6 §6.4 / Doc 6.1: lower wick >= 50% of range AND close in upper 50%.

    Bullish rejection = price swept low then closed strong.
    """
    rng = _bar_range(bar)
    if rng <= _EPSILON:
        return False
    lower_wick = bar.close - bar.low   # from low to close
    # lower wick >= 50% of range
    if lower_wick < _REJECTION_WICK_MIN * rng - _EPSILON:
        return False
    # close in upper 50% (close >= midpoint of range)
    midpoint = bar.low + rng / Decimal("2")
    return bar.close >= midpoint - _EPSILON


def is_bear_rejection_bar(bar: AggregatedBar) -> bool:
    """Doc 6 §6.4 / M3 / Doc 6.1: upper wick >= 50% of range AND close in lower 50%.

    Bearish rejection = price swept high then closed weak.
    """
    rng = _bar_range(bar)
    if rng <= _EPSILON:
        return False
    upper_wick = bar.high - bar.close   # from close to high
    if upper_wick < _REJECTION_WICK_MIN * rng - _EPSILON:
        return False
    midpoint = bar.low + rng / Decimal("2")
    return bar.close <= midpoint + _EPSILON


def is_bullish_engulfing(bar: AggregatedBar, prev_bar: AggregatedBar) -> bool:
    """Amendment M2: current close >= high of previous bar (entire prior candle engulfed)."""
    return bar.close >= prev_bar.high - _EPSILON


def is_bearish_engulfing(bar: AggregatedBar, prev_bar: AggregatedBar) -> bool:
    """Amendment M2: current close <= low of previous bar (entire prior candle engulfed)."""
    return bar.close <= prev_bar.low + _EPSILON


def is_bull_confirmation(bar: AggregatedBar, prev_bar: Optional[AggregatedBar]) -> bool:
    """Doc 6 §6.4 + M2: Any one of three bull confirmation methods.

    A: Bull rejection bar (lower wick >= 50%, close upper 50%)
    B: Close above previous bar's high
    C: Bullish engulfing (close >= prev high — same as B per M2)
    """
    if is_bull_rejection_bar(bar):
        return True
    if prev_bar is not None:
        if bar.close > prev_bar.high + _EPSILON:          # B: close above prior high
            return True
        if is_bullish_engulfing(bar, prev_bar):            # C: engulfing (>= prev high)
            return True
    return False


def is_bear_confirmation(bar: AggregatedBar, prev_bar: Optional[AggregatedBar]) -> bool:
    """Doc 6 §6.4 + M2 + M3: Any one of three bear confirmation methods.

    A: Bear rejection bar (upper wick >= 50%, close lower 50%)
    B: Close below previous bar's low
    C: Bearish engulfing (close <= prev low)
    """
    if is_bear_rejection_bar(bar):
        return True
    if prev_bar is not None:
        if bar.close < prev_bar.low - _EPSILON:            # B: close below prior low
            return True
        if is_bearish_engulfing(bar, prev_bar):            # C: engulfing
            return True
    return False


# ---------------------------------------------------------------------------
# Candidate ID generator
# ---------------------------------------------------------------------------

def _make_candidate_id(
    setup_type: SetupType,
    direction: Direction,
    zone_id: str,
    created_at: datetime,
) -> str:
    """Doc 1 §4: Deterministic sha256 ID — never uuid4."""
    content = f"{setup_type.name}|{direction.name}|{zone_id}|{created_at.isoformat()}"
    return hashlib.sha256(content.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# expected_R computation (M6)
# ---------------------------------------------------------------------------

def _compute_expected_r(
    entry: Decimal,
    stop: Decimal,
    target: Decimal,
) -> Decimal:
    """Amendment M6: expected_R = target_distance / stop_distance.

    Both computed at creation time. NEVER recomputed after candidate is created.
    """
    stop_dist   = abs(entry - stop)
    target_dist = abs(target - entry)
    if stop_dist <= _EPSILON:
        return _D_ZERO
    return target_dist / stop_dist


# ---------------------------------------------------------------------------
# Global pre-filter helpers (Doc 6 §3 + C2)
# ---------------------------------------------------------------------------

def _zone_eligible(zone: SRZone, setup_type: SetupType) -> bool:
    """C2 + Doc 6 §3.2: Zone state eligibility by setup type."""
    if setup_type == SetupType.BREAKOUT_RETEST:
        return zone.state in _ELIGIBLE_S2
    return zone.state in _ELIGIBLE_ALL


def _zone_within_proximity(
    zone: SRZone,
    current_price: Decimal,
    atr_1h: Decimal,
) -> bool:
    """Doc 6 §3.3: distance from price to nearest zone boundary <= 2×ATR(1H)."""
    if atr_1h <= _EPSILON:
        return False
    threshold = _PROXIMITY_ATR_MULT * atr_1h
    # Distance to nearest boundary
    dist_to_high = abs(current_price - zone.zone_high)
    dist_to_low  = abs(current_price - zone.zone_low)
    return min(dist_to_high, dist_to_low) <= threshold + _EPSILON


def _passes_global_prefilter(
    zone: SRZone,
    setup_type: SetupType,
    current_price: Decimal,
    atr_1h: Decimal,
) -> bool:
    """Doc 6 §3: Apply ALL pre-filter rules. Returns False to skip zone."""
    # Tier: S, A, B only
    if zone.tier == Tier.C:
        return False
    # Zone state (C2 table)
    if not _zone_eligible(zone, setup_type):
        return False
    # Proximity
    if not _zone_within_proximity(zone, current_price, atr_1h):
        return False
    return True


def _find_target(
    zones: Sequence[SRZone],
    direction: Direction,
    entry: Decimal,
    stop: Decimal,
    atr_1h: Decimal,
) -> Decimal:
    """M6: Find first opposing SR zone as target. Fallback: measured move.

    LONG:  first RESISTANCE zone above entry
    SHORT: first SUPPORT zone below entry
    """
    active = {ZoneState.FRESH, ZoneState.TESTED, ZoneState.FLIPPED}
    stop_dist = abs(entry - stop)

    if direction == Direction.LONG:
        candidates = [
            z for z in zones
            if z.polarity == ZonePolarity.RESISTANCE
            and z.state in active
            and z.center > entry + _EPSILON
        ]
        if candidates:
            nearest = min(candidates, key=lambda z: z.center - entry)
            return nearest.center
        # Fallback measured move
        return entry + _MEASURED_MOVE_MULT * stop_dist
    else:
        candidates = [
            z for z in zones
            if z.polarity == ZonePolarity.SUPPORT
            and z.state in active
            and z.center < entry - _EPSILON
        ]
        if candidates:
            nearest = min(candidates, key=lambda z: entry - z.center)
            return nearest.center
        return entry - _MEASURED_MOVE_MULT * stop_dist


# ---------------------------------------------------------------------------
# Setup 1 — Pullback Continuation (Doc 6 §6 + M3)
# ---------------------------------------------------------------------------

def _check_pullback_continuation(
    bar: AggregatedBar,
    prev_bar: Optional[AggregatedBar],
    zone: SRZone,
    phase: Phase,
    leg_state: LegState,
    last_completed_leg: Optional[CompletedLeg],
    zones: Sequence[SRZone],
    atr_15m: Decimal,
    atr_1h: Decimal,
) -> Optional[SetupCandidate]:
    """Doc 6 §6 + M3: Pullback Continuation setup check for one zone.

    Bull:  TREND_BULL + IN_BEAR_LEG + support zone + bull rejection → LONG candidate
    Bear:  TREND_BEAR + IN_BULL_LEG + resistance zone + bear rejection → SHORT candidate
    """
    # Phase gate
    is_bull_setup = phase == Phase.TREND_BULL
    is_bear_setup = phase == Phase.TREND_BEAR
    if not is_bull_setup and not is_bear_setup:
        return None

    # Last completed leg quality >= 2
    if last_completed_leg is None or last_completed_leg.quality < 2:
        return None

    if is_bull_setup:
        # Preconditions
        if zone.polarity != ZonePolarity.SUPPORT:
            return None
        if leg_state != LegState.IN_BEAR_LEG:
            return None
        if last_completed_leg.direction != LegDirection.BULL:
            return None
        # Trigger: price enters support zone from above (bar.low touches or enters zone)
        if not (bar.low <= zone.zone_high + _EPSILON):
            return None
        # Confirmation
        if not is_bull_confirmation(bar, prev_bar):
            return None

        # Candidate creation
        entry = bar.high + _ENTRY_BUFFER_MULT * atr_15m
        stop  = zone.zone_low - _STOP_BUFFER_MULT * atr_15m
        target = _find_target(zones, Direction.LONG, entry, stop, atr_1h)
        exp_r  = _compute_expected_r(entry, stop, target)
        if exp_r < _MIN_R_TREND - _EPSILON:
            return None

        cid = _make_candidate_id(
            SetupType.PULLBACK_CONTINUATION, Direction.LONG,
            zone.zone_id, bar.timestamp_end,
        )
        logger.info("Setup1 LONG candidate %s zone=%s entry=%s R=%.2f",
                    cid, zone.zone_id, entry, float(exp_r))
        return SetupCandidate(
            candidate_id=cid,
            setup_type=SetupType.PULLBACK_CONTINUATION,
            direction=Direction.LONG,
            zone_id=zone.zone_id,
            entry_price=entry,
            stop_price=stop,
            target_price=target,
            expected_R=exp_r,
            created_at=bar.timestamp_end,
            expiry_bars=_PULLBACK_EXPIRY,
        )

    else:  # TREND_BEAR
        if zone.polarity != ZonePolarity.RESISTANCE:
            return None
        if leg_state != LegState.IN_BULL_LEG:
            return None
        if last_completed_leg.direction != LegDirection.BEAR:
            return None
        # Trigger: price enters resistance zone from below
        if not (bar.high >= zone.zone_low - _EPSILON):
            return None
        # Confirmation (M3)
        if not is_bear_confirmation(bar, prev_bar):
            return None

        entry  = bar.low - _ENTRY_BUFFER_MULT * atr_15m
        stop   = zone.zone_high + _STOP_BUFFER_MULT * atr_15m
        target = _find_target(zones, Direction.SHORT, entry, stop, atr_1h)
        exp_r  = _compute_expected_r(entry, stop, target)
        if exp_r < _MIN_R_TREND - _EPSILON:
            return None

        cid = _make_candidate_id(
            SetupType.PULLBACK_CONTINUATION, Direction.SHORT,
            zone.zone_id, bar.timestamp_end,
        )
        logger.info("Setup1 SHORT candidate %s zone=%s entry=%s R=%.2f",
                    cid, zone.zone_id, entry, float(exp_r))
        return SetupCandidate(
            candidate_id=cid,
            setup_type=SetupType.PULLBACK_CONTINUATION,
            direction=Direction.SHORT,
            zone_id=zone.zone_id,
            entry_price=entry,
            stop_price=stop,
            target_price=target,
            expected_R=exp_r,
            created_at=bar.timestamp_end,
            expiry_bars=_PULLBACK_EXPIRY,
        )


# ---------------------------------------------------------------------------
# Setup 2 — Breakout Retest (Doc 6 §7 + C2 + P4)
# ---------------------------------------------------------------------------

def _check_breakout_retest(
    bar: AggregatedBar,
    prev_bar: Optional[AggregatedBar],
    zone: SRZone,
    phase: Phase,
    zones: Sequence[SRZone],
    atr_15m: Decimal,
    atr_1h: Decimal,
) -> Optional[SetupCandidate]:
    """Doc 6 §7 + P4 + C2: Breakout Retest check for one zone.

    P4 Phases: TREND_BULL (bullish retest) or TREND_BEAR (bearish retest) only.
    C2: requires zone.state in BROKEN_CONFIRMED or FLIPPED (or BREAK_PENDING watch).
    """
    # P4: only TREND_BULL and TREND_BEAR
    if phase not in (Phase.TREND_BULL, Phase.TREND_BEAR):
        return None

    # C2: state must be BROKEN_CONFIRMED or FLIPPED
    if zone.state not in (ZoneState.BROKEN_CONFIRMED, ZoneState.FLIPPED):
        return None

    # Direction alignment: FLIPPED zone has inverted polarity after flip
    # Original resistance broken upward → now acts as support on retest (LONG)
    # Original support broken downward → now acts as resistance on retest (SHORT)
    if phase == Phase.TREND_BULL:
        # Expect bullish retest: broken resistance now support → price comes back to test from above
        if zone.polarity not in (ZonePolarity.SUPPORT, ZonePolarity.RESISTANCE):
            return None
        # Price must have returned to the broken zone boundary
        at_zone = (bar.low <= zone.zone_high + _EPSILON and bar.high >= zone.zone_low - _EPSILON)
        if not at_zone:
            return None
        # Confirmation: decisive close ABOVE zone — body >= 60% of range, close > zone center
        rng = _bar_range(bar)
        if rng <= _EPSILON:
            return None
        body = abs(bar.close - bar.open)
        if body < _BODY_MIN_FRAC * rng - _EPSILON:
            return None
        if bar.close <= zone.zone_high + _EPSILON:
            return None
        # Invalidation: closes back inside old range
        if bar.close < zone.zone_low - _EPSILON:
            return None

        entry  = bar.close
        stop   = zone.zone_low - _STOP_BUFFER_MULT * atr_15m
        target = _find_target(zones, Direction.LONG, entry, stop, atr_1h)
        exp_r  = _compute_expected_r(entry, stop, target)
        if exp_r < _MIN_R_TREND - _EPSILON:
            return None

        cid = _make_candidate_id(
            SetupType.BREAKOUT_RETEST, Direction.LONG,
            zone.zone_id, bar.timestamp_end,
        )
        logger.info("Setup2 LONG candidate %s zone=%s R=%.2f", cid, zone.zone_id, float(exp_r))
        return SetupCandidate(
            candidate_id=cid,
            setup_type=SetupType.BREAKOUT_RETEST,
            direction=Direction.LONG,
            zone_id=zone.zone_id,
            entry_price=entry,
            stop_price=stop,
            target_price=target,
            expected_R=exp_r,
            created_at=bar.timestamp_end,
            expiry_bars=_PULLBACK_EXPIRY,
        )

    else:  # TREND_BEAR
        if zone.polarity not in (ZonePolarity.SUPPORT, ZonePolarity.RESISTANCE):
            return None
        at_zone = (bar.low <= zone.zone_high + _EPSILON and bar.high >= zone.zone_low - _EPSILON)
        if not at_zone:
            return None
        rng = _bar_range(bar)
        if rng <= _EPSILON:
            return None
        body = abs(bar.close - bar.open)
        if body < _BODY_MIN_FRAC * rng - _EPSILON:
            return None
        if bar.close >= zone.zone_low - _EPSILON:
            return None
        # Invalidation: closes back inside
        if bar.close > zone.zone_high + _EPSILON:
            return None

        entry  = bar.close
        stop   = zone.zone_high + _STOP_BUFFER_MULT * atr_15m
        target = _find_target(zones, Direction.SHORT, entry, stop, atr_1h)
        exp_r  = _compute_expected_r(entry, stop, target)
        if exp_r < _MIN_R_TREND - _EPSILON:
            return None

        cid = _make_candidate_id(
            SetupType.BREAKOUT_RETEST, Direction.SHORT,
            zone.zone_id, bar.timestamp_end,
        )
        logger.info("Setup2 SHORT candidate %s zone=%s R=%.2f", cid, zone.zone_id, float(exp_r))
        return SetupCandidate(
            candidate_id=cid,
            setup_type=SetupType.BREAKOUT_RETEST,
            direction=Direction.SHORT,
            zone_id=zone.zone_id,
            entry_price=entry,
            stop_price=stop,
            target_price=target,
            expected_R=exp_r,
            created_at=bar.timestamp_end,
            expiry_bars=_PULLBACK_EXPIRY,
        )


# ---------------------------------------------------------------------------
# Setup 3 — Fakeout / Spring / Upthrust (Doc 6.1 — PRIMARY)
# ---------------------------------------------------------------------------

def _init_fakeout_watch(
    bar: AggregatedBar,
    zone: SRZone,
    phase: Phase,
) -> Optional[_FakeoutWatch]:
    """Doc 6.1: Detect penetration and start 4-bar window.

    Bear Fakeout (Spring / LONG): bar.low <= zone_low → support penetrated
      Phase: any except TREND_BEAR
    Bull Fakeout (Upthrust / SHORT): bar.high >= zone_high → resistance penetrated
      Phase: any except TREND_BULL
    """
    if zone.polarity == ZonePolarity.SUPPORT and phase != Phase.TREND_BEAR:
        if bar.low <= zone.zone_low + _EPSILON:
            return _FakeoutWatch(
                zone_id=zone.zone_id,
                direction=Direction.LONG,
                zone_low=zone.zone_low,
                zone_high=zone.zone_high,
                t0_bar=bar,
            )

    if zone.polarity == ZonePolarity.RESISTANCE and phase != Phase.TREND_BULL:
        if bar.high >= zone.zone_high - _EPSILON:
            return _FakeoutWatch(
                zone_id=zone.zone_id,
                direction=Direction.SHORT,
                zone_low=zone.zone_low,
                zone_high=zone.zone_high,
                t0_bar=bar,
            )

    return None


def _advance_fakeout_watch(
    watch: _FakeoutWatch,
    bar: AggregatedBar,
    zones: Sequence[SRZone],
    atr_15m: Decimal,
    atr_1h: Decimal,
    phase: Phase,
) -> tuple[Optional[SetupCandidate], bool]:
    """Doc 6.1: Advance a fakeout watch by one bar.

    Returns (candidate_if_confirmed, should_remove_watch).
    """
    watch.bars_elapsed += 1

    if watch.direction == Direction.LONG:
        # Bear Fakeout (Spring): support penetrated, watching for reclaim upward
        # Step 2 — Failure to Accept: at least one close >= zone_low
        if bar.close >= watch.zone_low - _EPSILON:
            watch.had_failure_close = True

        # Invalidation A: close < zone_low AND no close > zone_high yet → cancelled
        if bar.close < watch.zone_low - _EPSILON and not watch.had_failure_close:
            watch.cancelled = True
            logger.debug("Fakeout LONG watch cancelled (accepted below zone_low) zone=%s", watch.zone_id)
            return None, True

        # Step 3 — Full Reclaim: close > zone_high
        if bar.close > watch.zone_high + _EPSILON:
            # Reclaim confirmed — create candidate
            entry  = bar.close
            stop   = watch.zone_low - _STOP_BUFFER_MULT * atr_15m
            target = _find_target(zones, Direction.LONG, entry, stop, atr_1h)
            exp_r  = _compute_expected_r(entry, stop, target)
            if exp_r < _MIN_R_TREND - _EPSILON:
                logger.debug("Fakeout LONG R=%.2f < min, skipped", float(exp_r))
                return None, True

            cid = _make_candidate_id(
                SetupType.FAKEOUT, Direction.LONG,
                watch.zone_id, bar.timestamp_end,
            )
            logger.info("Setup3 Spring LONG candidate %s zone=%s R=%.2f",
                        cid, watch.zone_id, float(exp_r))
            cand = SetupCandidate(
                candidate_id=cid,
                setup_type=SetupType.FAKEOUT,
                direction=Direction.LONG,
                zone_id=watch.zone_id,
                entry_price=entry,
                stop_price=stop,
                target_price=target,
                expected_R=exp_r,
                created_at=bar.timestamp_end,
                expiry_bars=_FAKEOUT_WINDOW,
                fakeout_reclaimed=True,
            )
            return cand, True  # watch consumed

        # Invalidation B: 4 bars pass without reclaim
        if watch.bars_elapsed >= _FAKEOUT_WINDOW:
            logger.debug("Fakeout LONG watch expired zone=%s", watch.zone_id)
            return None, True

    else:  # Direction.SHORT — Bull Fakeout (Upthrust)
        # Step 2: at least one close <= zone_high
        if bar.close <= watch.zone_high + _EPSILON:
            watch.had_failure_close = True

        # Invalidation A: close > zone_high AND no close < zone_low yet
        if bar.close > watch.zone_high + _EPSILON and not watch.had_failure_close:
            watch.cancelled = True
            logger.debug("Fakeout SHORT watch cancelled (accepted above zone_high) zone=%s", watch.zone_id)
            return None, True

        # Step 3 — Full Reclaim downward: close < zone_low
        if bar.close < watch.zone_low - _EPSILON:
            entry  = bar.close
            stop   = watch.zone_high + _STOP_BUFFER_MULT * atr_15m
            target = _find_target(zones, Direction.SHORT, entry, stop, atr_1h)
            exp_r  = _compute_expected_r(entry, stop, target)
            if exp_r < _MIN_R_TREND - _EPSILON:
                logger.debug("Fakeout SHORT R=%.2f < min, skipped", float(exp_r))
                return None, True

            cid = _make_candidate_id(
                SetupType.FAKEOUT, Direction.SHORT,
                watch.zone_id, bar.timestamp_end,
            )
            logger.info("Setup3 Upthrust SHORT candidate %s zone=%s R=%.2f",
                        cid, watch.zone_id, float(exp_r))
            cand = SetupCandidate(
                candidate_id=cid,
                setup_type=SetupType.FAKEOUT,
                direction=Direction.SHORT,
                zone_id=watch.zone_id,
                entry_price=entry,
                stop_price=stop,
                target_price=target,
                expected_R=exp_r,
                created_at=bar.timestamp_end,
                expiry_bars=_FAKEOUT_WINDOW,
                fakeout_reclaimed=True,
            )
            return cand, True

        # Invalidation B: time expiry
        if watch.bars_elapsed >= _FAKEOUT_WINDOW:
            logger.debug("Fakeout SHORT watch expired zone=%s", watch.zone_id)
            return None, True

    return None, False  # still watching


# ---------------------------------------------------------------------------
# Setup 4 — Range Fade (Amendment v1.2 P9)
# ---------------------------------------------------------------------------

def _check_range_fade(
    bar: AggregatedBar,
    prev_bars: Sequence[AggregatedBar],
    prev_bar: Optional[AggregatedBar],
    zone: SRZone,
    phase: Phase,
    zones: Sequence[SRZone],
    atr_15m: Decimal,
    atr_1h: Decimal,
) -> Optional[SetupCandidate]:
    """Amendment P9: Range Fade setup check for one zone.

    BALANCE phase only. Zone must be FRESH or TESTED.
    """
    # Phase gate
    if phase != Phase.BALANCE:
        return None
    # Zone state: FRESH or TESTED only (P9 precondition)
    if zone.state not in (ZoneState.FRESH, ZoneState.TESTED):
        return None

    # SELL FADE at resistance
    if zone.polarity == ZonePolarity.RESISTANCE:
        # Trigger: bar.high >= zone_low (price reaches into zone from below)
        if not (bar.high >= zone.zone_low - _EPSILON):
            return None
        # Confirmation (one of A, B, C)
        confirmed = False
        # A: Bear rejection bar
        if is_bear_rejection_bar(bar):
            confirmed = True
        # B: Bearish engulfing
        if not confirmed and prev_bar is not None and is_bearish_engulfing(bar, prev_bar):
            confirmed = True
        # C: 2 consecutive closes moving away from zone_high downward
        if not confirmed and len(prev_bars) >= 2:
            recent = list(prev_bars[-2:])
            if (len(recent) == 2
                    and recent[1].close < recent[0].close - _EPSILON
                    and bar.close < recent[1].close - _EPSILON
                    and bar.close < zone.zone_high - _EPSILON):
                confirmed = True
        if not confirmed:
            return None

        entry  = bar.low
        stop   = zone.zone_high + _STOP_BUFFER_MULT * atr_15m
        target = _find_target(zones, Direction.SHORT, entry, stop, atr_1h)
        exp_r  = _compute_expected_r(entry, stop, target)
        if exp_r < _MIN_R_BALANCE - _EPSILON:
            return None

        cid = _make_candidate_id(
            SetupType.RANGE_FADE, Direction.SHORT,
            zone.zone_id, bar.timestamp_end,
        )
        logger.info("Setup4 SELL FADE candidate %s zone=%s R=%.2f",
                    cid, zone.zone_id, float(exp_r))
        return SetupCandidate(
            candidate_id=cid,
            setup_type=SetupType.RANGE_FADE,
            direction=Direction.SHORT,
            zone_id=zone.zone_id,
            entry_price=entry,
            stop_price=stop,
            target_price=target,
            expected_R=exp_r,
            created_at=bar.timestamp_end,
            expiry_bars=_RANGE_FADE_EXPIRY,
        )

    elif zone.polarity == ZonePolarity.SUPPORT:
        # BUY FADE at support
        # Trigger: bar.low <= zone_high (price reaches into zone from above)
        if not (bar.low <= zone.zone_high + _EPSILON):
            return None
        # Confirmation (mirror)
        confirmed = False
        if is_bull_rejection_bar(bar):
            confirmed = True
        if not confirmed and prev_bar is not None and is_bullish_engulfing(bar, prev_bar):
            confirmed = True
        if not confirmed and len(prev_bars) >= 2:
            recent = list(prev_bars[-2:])
            if (len(recent) == 2
                    and recent[1].close > recent[0].close + _EPSILON
                    and bar.close > recent[1].close + _EPSILON
                    and bar.close > zone.zone_low + _EPSILON):
                confirmed = True
        if not confirmed:
            return None

        entry  = bar.high
        stop   = zone.zone_low - _STOP_BUFFER_MULT * atr_15m
        target = _find_target(zones, Direction.LONG, entry, stop, atr_1h)
        exp_r  = _compute_expected_r(entry, stop, target)
        if exp_r < _MIN_R_BALANCE - _EPSILON:
            return None

        cid = _make_candidate_id(
            SetupType.RANGE_FADE, Direction.LONG,
            zone.zone_id, bar.timestamp_end,
        )
        logger.info("Setup4 BUY FADE candidate %s zone=%s R=%.2f",
                    cid, zone.zone_id, float(exp_r))
        return SetupCandidate(
            candidate_id=cid,
            setup_type=SetupType.RANGE_FADE,
            direction=Direction.LONG,
            zone_id=zone.zone_id,
            entry_price=entry,
            stop_price=stop,
            target_price=target,
            expected_R=exp_r,
            created_at=bar.timestamp_end,
            expiry_bars=_RANGE_FADE_EXPIRY,
        )

    return None


# ---------------------------------------------------------------------------
# SetupEngine — stateful orchestrator
# ---------------------------------------------------------------------------

class SetupEngine:
    """Doc 6 + Doc 6.1 + amendments: Streaming setup detection engine.

    One instance per instrument. Call update() on every closed 15m bar.

    Usage::

        engine = SetupEngine(symbol="BTCUSDT")

        result = engine.update(
            bar=bar_15m,
            phase=phase_result.active_phase,
            leg_state=zlbb_15m_engine.leg_state,
            last_completed_leg=most_recent_completed_leg,
            zones=zone_detector.get_active_zones(min_tier='B'),
            atr_15m=atr_15m_value,
            atr_1h=atr_1h_value,
        )
        new_candidates = result          # list[SetupCandidate]
        active = engine.get_active_candidates()
    """

    def __init__(self, symbol: str) -> None:
        self._symbol = symbol
        self._candidates: list[SetupCandidate] = []
        # Active fakeout watches (zone_id → _FakeoutWatch)
        # Multiple zones can be watched simultaneously
        self._fakeout_watches: dict[str, _FakeoutWatch] = {}
        # Bar history for consecutive-close check in Range Fade (P9 confirmation C)
        self._bar_history: list[AggregatedBar] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update(
        self,
        bar: AggregatedBar,
        phase: Phase,
        leg_state: LegState,
        last_completed_leg: Optional[CompletedLeg],
        zones: Sequence[SRZone],
        atr_15m: Decimal,
        atr_1h: Decimal,
    ) -> list[SetupCandidate]:
        """Process one closed 15m bar. Returns list of NEW candidates this bar.

        All inputs must be from CLOSED bars only. Incomplete bars are skipped.
        """
        if not bar.is_complete:
            return []

        new_candidates: list[SetupCandidate] = []
        prev_bar = self._bar_history[-1] if self._bar_history else None

        # 1. Advance open fakeout watches (Setup 3) — do this BEFORE checking new penetrations
        watches_to_remove: list[str] = []
        for zone_id, watch in list(self._fakeout_watches.items()):
            cand, remove = _advance_fakeout_watch(
                watch, bar, zones, atr_15m, atr_1h, phase
            )
            if cand is not None:
                self._candidates.append(cand)
                new_candidates.append(cand)
            if remove:
                watches_to_remove.append(zone_id)
        for zone_id in watches_to_remove:
            del self._fakeout_watches[zone_id]

        # 2. Expire stale candidates by bar count
        self._tick_expiry(bar)

        # 3. Scan zones for new setups
        current_price = bar.close
        active_zone_ids = {c.zone_id for c in self._candidates
                           if c.status == CandidateStatus.WAITING_ENTRY}

        for zone in zones:
            # Skip zones already producing an active candidate (avoid duplicates)
            if zone.zone_id in active_zone_ids:
                continue

            # --- Setup 3 Fakeout: check for new penetration ---
            if zone.zone_id not in self._fakeout_watches:
                if _passes_global_prefilter(zone, SetupType.FAKEOUT, current_price, atr_1h):
                    watch = _init_fakeout_watch(bar, zone, phase)
                    if watch is not None:
                        self._fakeout_watches[zone.zone_id] = watch
                        logger.debug("Fakeout watch started zone=%s dir=%s",
                                     zone.zone_id, watch.direction)

            # --- Setup 1 Pullback ---
            if _passes_global_prefilter(zone, SetupType.PULLBACK_CONTINUATION, current_price, atr_1h):
                cand = _check_pullback_continuation(
                    bar=bar, prev_bar=prev_bar, zone=zone, phase=phase,
                    leg_state=leg_state, last_completed_leg=last_completed_leg,
                    zones=zones, atr_15m=atr_15m, atr_1h=atr_1h,
                )
                if cand is not None and cand.zone_id not in active_zone_ids:
                    self._candidates.append(cand)
                    new_candidates.append(cand)
                    active_zone_ids.add(cand.zone_id)

            # --- Setup 2 Breakout Retest ---
            if _passes_global_prefilter(zone, SetupType.BREAKOUT_RETEST, current_price, atr_1h):
                cand = _check_breakout_retest(
                    bar=bar, prev_bar=prev_bar, zone=zone, phase=phase,
                    zones=zones, atr_15m=atr_15m, atr_1h=atr_1h,
                )
                if cand is not None and cand.zone_id not in active_zone_ids:
                    self._candidates.append(cand)
                    new_candidates.append(cand)
                    active_zone_ids.add(cand.zone_id)

            # --- Setup 4 Range Fade ---
            if _passes_global_prefilter(zone, SetupType.RANGE_FADE, current_price, atr_1h):
                cand = _check_range_fade(
                    bar=bar, prev_bars=self._bar_history, prev_bar=prev_bar,
                    zone=zone, phase=phase, zones=zones,
                    atr_15m=atr_15m, atr_1h=atr_1h,
                )
                if cand is not None and cand.zone_id not in active_zone_ids:
                    self._candidates.append(cand)
                    new_candidates.append(cand)
                    active_zone_ids.add(cand.zone_id)

        # 4. Store bar in history (keep last 10 for Range Fade C confirmation)
        self._bar_history.append(bar)
        if len(self._bar_history) > 10:
            self._bar_history.pop(0)

        return new_candidates

    def get_active_candidates(self) -> list[SetupCandidate]:
        """Return all WAITING_ENTRY candidates."""
        return [c for c in self._candidates if c.status == CandidateStatus.WAITING_ENTRY]

    def expire_stale(self, current_bar: AggregatedBar) -> int:
        """Manually trigger expiry check. Returns count of expired candidates."""
        before = sum(1 for c in self._candidates if c.status == CandidateStatus.EXPIRED)
        self._tick_expiry(current_bar)
        after  = sum(1 for c in self._candidates if c.status == CandidateStatus.EXPIRED)
        return after - before

    def cancel_by_zone(self, zone_id: str) -> int:
        """Cancel all WAITING_ENTRY candidates for a given zone. Returns count cancelled."""
        count = 0
        updated: list[SetupCandidate] = []
        for c in self._candidates:
            if c.zone_id == zone_id and c.status == CandidateStatus.WAITING_ENTRY:
                # Replace with cancelled copy (frozen dataclass — rebuild)
                updated.append(_replace_status(c, CandidateStatus.CANCELLED))
                count += 1
            else:
                updated.append(c)
        self._candidates = updated
        return count

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _tick_expiry(self, current_bar: AggregatedBar) -> None:
        """Increment bar counters for WAITING_ENTRY candidates. Expire if limit reached.

        NOTE: This simple implementation counts update() calls (bars) since creation.
        A more precise implementation would track creation bar index; this is sufficient
        for determinism since all calls are sequential.
        """
        # We track expiry by maintaining a mutable bar counter on each candidate.
        # Since SetupCandidate is frozen, we rebuild expired ones with new status.
        updated: list[SetupCandidate] = []
        for c in self._candidates:
            if c.status != CandidateStatus.WAITING_ENTRY:
                updated.append(c)
                continue
            # Increment elapsed by checking creation time vs current bar time
            elapsed = _bars_elapsed_since(c.created_at, current_bar)
            if elapsed >= c.expiry_bars:
                logger.debug("Candidate %s expired (elapsed=%d)", c.candidate_id, elapsed)
                updated.append(_replace_status(c, CandidateStatus.EXPIRED))
            else:
                updated.append(c)
        self._candidates = updated


def _bars_elapsed_since(created_at: datetime, current_bar: AggregatedBar) -> int:
    """Estimate how many 15m bars have passed since candidate was created.

    Uses wall-clock time difference / 15 minutes. Deterministic for given inputs.
    """
    from datetime import timedelta
    delta = current_bar.timestamp_end - created_at
    if delta.total_seconds() <= 0:
        return 0
    return int(delta.total_seconds() // (15 * 60))


def _replace_status(c: SetupCandidate, new_status: CandidateStatus) -> SetupCandidate:
    """Return a new SetupCandidate with updated status (frozen dataclass workaround)."""
    return SetupCandidate(
        candidate_id=c.candidate_id,
        setup_type=c.setup_type,
        direction=c.direction,
        zone_id=c.zone_id,
        entry_price=c.entry_price,
        stop_price=c.stop_price,
        target_price=c.target_price,
        expected_R=c.expected_R,
        created_at=c.created_at,
        expiry_bars=c.expiry_bars,
        status=new_status,
        fakeout_bars_elapsed=c.fakeout_bars_elapsed,
        fakeout_reclaimed=c.fakeout_reclaimed,
    )
