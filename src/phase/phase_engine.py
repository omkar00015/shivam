"""Doc 5 + Amendment v1.1 (C6, M1) + Amendment v1.2 (P5, P8, P10):
Market Phase & Regime Engine — deterministic, no float.

Five phases: TREND_BULL | TREND_BEAR | BALANCE | DISTRIBUTION | ACCUMULATION
No BREAKOUT phase (P4: dangling reference removed).

Scoring windows: last 5 completed 1H legs (rolling).
Scores clamped to [0, 100] before comparison.

Phase stickiness (M1 + P8):
  - Scores recalculated every 15m bar close (stored, not applied).
  - Active phase LABEL changes only when:
      The new winning phase leads for >= 2 CONSECUTIVE 1H bar closes.
  - P8: consecutive_count only increments if the SAME phase wins both closes.
        A different phase winning at the second 1H close RESETS the counter.

Density + Exhaustion modifiers applied AFTER base scoring (P10):
  - supply_weight vs demand_weight within 2×ATR(1H), weighted by tier S=3/A=2/B=1
  - IF supply_weight >= demand_weight + 3: bear +10, bull -5
  - IF demand_weight >= supply_weight + 3: bull +10, bear -5
  - Exhaustion: if most recent bull leg quality<=1 AND efficiency<0.35 → bull -10
    (mirror for bear)

INPUTS (per scoring call):
  - legs: list[CompletedLeg]        # last N 1H legs (caller maintains rolling deque)
  - zones: list[SRZone]             # from ZoneDetector.get_active_zones()
  - current_price: Decimal
  - atr_1h: Decimal                 # ATR(1H, 14)
  - density_bias: DensityBias       # from ZoneDetector.density_bias
  - recent_1h_bars: list[AggregatedBar]  # last 20 1H bars (for ZLEMA / wicks checks)
  - recent_zlbb_states: list[ZLBBState] # corresponding ZLBB states (for ZLEMA / band_walk)

OUTPUTS:
  - active_phase: Phase
  - phase_confidence: Decimal (0.0–1.0)
  - transition_flag: bool
  - size_reduction: Decimal (1.0 normal, 0.5 on transition)
  - all_scores: dict[Phase, int]

All arithmetic uses Decimal. No float anywhere.
"""

import logging
from collections import deque
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from enum import Enum, auto
from typing import Optional, Sequence

from src.data_ingest.bar_aggregator import AggregatedBar
from src.indicators.zlbb import CompletedLeg, LegDirection, ZLBBState
from src.sr.zone_detector import DensityBias, SRZone, Tier, ZonePolarity, ZoneState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_EPSILON        = Decimal("1E-9")
_D_ZERO         = Decimal("0")
_D_ONE          = Decimal("1")
_D_HUNDRED      = Decimal("100")

_LEG_WINDOW     = 5     # rolling window of legs for scoring
_H1_BAR_WINDOW  = 20    # 1H bars used for ZLEMA majority / wick checks
_BW_BAR_WINDOW  = 10    # recent 1H bars for band_walk detection

# Dominance threshold (Doc 5 §10)
_DOMINANCE_MARGIN = 10  # best >= second + 10 to accept

# Phase change requires consecutive 1H closes (M1 / P8)
_CONSECUTIVE_REQUIRED = 2

# Density modifier thresholds (P10)
_DENSITY_TIER_DIFF = 3          # supply_weight - demand_weight >= 3 triggers modifier
_DENSITY_BULL_MOD  = 10         # +10 to bull when demand dominant
_DENSITY_BEAR_MOD  = 10         # +10 to bear when supply dominant
_DENSITY_PENALTY   = 5          # -5 to opposing side

# Exhaustion penalty (P10 / §15)
_EXHAUSTION_QUALITY_MAX = 1     # quality <= 1
_EXHAUSTION_EFF_MAX     = Decimal("0.35")  # efficiency < 0.35
_EXHAUSTION_PENALTY     = 10    # -10 to trend score

# Scoring thresholds
_TREND_EFF_MIN        = Decimal("0.50")   # average efficiency >= 0.5
_PULLBACK_RATIO_MAX   = Decimal("0.50")   # bear pullback < 50% of preceding bull
_PULLBACK_PASS_FRAC   = Decimal("0.60")   # >= 60% of bear legs must pass
_BULL_MAJORITY_MIN    = Decimal("0.60")   # >= 60% bull legs
_ZLEMA_ABOVE_FRAC     = Decimal("0.60")   # >= 60% of 1H bars: close > ZLEMA

# Distribution / Accumulation
_DIST_EFF_THRESHOLD   = Decimal("0.65")   # no leg efficiency > 0.65 for BALANCE
_DIST_PULLBACK_MIN    = Decimal("0.60")   # deeper pullbacks threshold
_DIST_WICK_MIN        = Decimal("0.40")   # upper wicks mean > 0.40
_DIST_FAIL_COUNT_MIN  = 2                 # count of expansion failures
_DIST_PROXIMITY_MULT  = Decimal("1.5")    # zone within 1.5×ATR

# Balance scoring
_BAL_OVERLAP_MIN      = Decimal("0.60")   # overlap_ratio >= 0.60
_BAL_DISP_ATR_MAX     = Decimal("1.0")    # mean displacement < 1.0×ATR(1H)
_BAL_MIDPOINT_FRAC    = Decimal("0.50")   # >= 50% of bars near midpoint
_BAL_MIDPOINT_ATR     = Decimal("0.50")   # within 0.50×ATR of midpoint
_BAL_ALT_MIN          = 3                 # >= 3 direction changes in 5 legs

# C6: Resistance/support break and rejection thresholds
_BREAK_COUNT_MIN        = 2                  # >= 2 legs broke through zone
_REJECTION_DISP_MAX     = Decimal("0.50")    # bear/bull leg disp < 0.50×ATR = failed leg
_REJECTION_COUNT_MIN    = 2                  # >= 2 failed legs at zone = rejection confirmed


# ---------------------------------------------------------------------------
# Phase enum
# ---------------------------------------------------------------------------

class Phase(Enum):
    """Doc 5 §3.1: Five market phases (no BREAKOUT — P4)."""
    TREND_BULL   = auto()
    TREND_BEAR   = auto()
    BALANCE      = auto()
    DISTRIBUTION = auto()
    ACCUMULATION = auto()


# ---------------------------------------------------------------------------
# PhaseResult: immutable output snapshot
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PhaseResult:
    """Output of PhaseEngine.update() or score()."""
    active_phase:     Phase
    phase_confidence: Decimal    # 0.0–1.0
    transition_flag:  bool
    size_reduction:   Decimal    # 1.0 or 0.5
    all_scores:       dict       # Phase → int; not truly frozen but shallow copy


# ---------------------------------------------------------------------------
# Internal scoring helpers
# ---------------------------------------------------------------------------

def _leg_range_length(leg: CompletedLeg) -> Decimal:
    """Absolute price range of leg (high – low). Uses displacement as proxy."""
    return leg.displacement  # displacement = abs(end - start) = range proxy


def _overlap_ratio(a: CompletedLeg, b: CompletedLeg) -> Decimal:
    """P5: Overlap ratio between two consecutive legs.

    overlap_ratio = length_of_intersection / min(range_a, range_b)
    Each leg's range is [min(start,end), max(start,end)].
    """
    a_lo = min(a.start_price, a.end_price)
    a_hi = max(a.start_price, a.end_price)
    b_lo = min(b.start_price, b.end_price)
    b_hi = max(b.start_price, b.end_price)

    intersect = max(_D_ZERO, min(a_hi, b_hi) - max(a_lo, b_lo))
    range_a   = a_hi - a_lo
    range_b   = b_hi - b_lo
    min_range = min(range_a, range_b)
    if min_range <= _EPSILON:
        return _D_ZERO
    return intersect / min_range


def _tier_weight(tier: Tier) -> int:
    """P10: Tier weights for density modifier."""
    return {Tier.S: 3, Tier.A: 2, Tier.B: 1}.get(tier, 0)


def _compute_density_modifier(
    zones: Sequence[SRZone],
    current_price: Decimal,
    atr_1h: Decimal,
) -> tuple[int, int]:
    """P10: Compute (bull_modifier, bear_modifier) from zone density near price.

    Scan range: 2×ATR(1H) above and below.
    Weighted by tier: S=3, A=2, B=1. B-TIER+ zones only.
    """
    if atr_1h <= _EPSILON:
        return 0, 0
    scan = Decimal("2") * atr_1h
    supply_weight = 0
    demand_weight = 0
    active_states = {ZoneState.FRESH, ZoneState.TESTED, ZoneState.FLIPPED, ZoneState.BREAK_PENDING}
    for z in zones:
        if z.state not in active_states:
            continue
        tw = _tier_weight(z.tier)
        if tw == 0:
            continue  # C-tier excluded
        # Resistance above price within scan
        if z.polarity == ZonePolarity.RESISTANCE:
            if z.zone_low > current_price - _EPSILON:
                dist = z.zone_low - current_price
                if dist <= scan + _EPSILON:
                    supply_weight += tw
        # Support below price within scan
        elif z.polarity == ZonePolarity.SUPPORT:
            if z.zone_high < current_price + _EPSILON:
                dist = current_price - z.zone_high
                if dist <= scan + _EPSILON:
                    demand_weight += tw

    bull_mod = bear_mod = 0
    if supply_weight >= demand_weight + _DENSITY_TIER_DIFF:
        bear_mod = _DENSITY_BEAR_MOD
        bull_mod = -_DENSITY_PENALTY
    elif demand_weight >= supply_weight + _DENSITY_TIER_DIFF:
        bull_mod = _DENSITY_BULL_MOD
        bear_mod = -_DENSITY_PENALTY
    return bull_mod, bear_mod


# ---------------------------------------------------------------------------
# Score: TREND_BULL
# ---------------------------------------------------------------------------

def score_trend_bull(
    legs: Sequence[CompletedLeg],
    zones: Sequence[SRZone],
    current_price: Decimal,
    atr_1h: Decimal,
    density_bias: DensityBias,
    recent_1h_bars: Sequence[AggregatedBar],
    recent_zlbb: Sequence[ZLBBState],
) -> int:
    """Doc 5 §5.1 + Amendment v1.1 C6: TREND_BULL score [0, 100].

    Eight conditions (max = 100):
    +20: HH+HL — 2 consecutive bull legs where end[n] > end[n-1] AND start[n] > start[n-1]
    +15: majority bull legs >= 60% of last 5
    +15: average efficiency >= 0.50 across last 5 legs
    +10: pullbacks <= 50% — bear leg disp < 0.50× preceding bull; holds for >= 60% bear legs
    +10: price above ZLEMA majority — count(close > ZLEMA) / 20 >= 0.60 in last 20 1H bars
    +10: band walk occurred recently — any bull leg in last 10 legs has is_band_walk=True
    +10: resistance breaks succeeded — >= 2 bull legs in last 5 where end_price > a resistance zone_low
    +10: bear rejection failed — >= 2 bear legs where displacement < 0.50×ATR(1H) AND ended at/inside a support zone
    """
    if not legs:
        return 0
    n  = min(len(legs), _LEG_WINDOW)
    window = list(legs[-n:])
    score = 0

    # +20: HH+HL structure — 2 consecutive bull legs with higher highs AND higher lows
    bull_legs = [l for l in window if l.direction == LegDirection.BULL]
    if len(bull_legs) >= 2:
        hh_hl_count = 0
        for k in range(1, len(bull_legs)):
            prev = bull_legs[k - 1]
            curr = bull_legs[k]
            if (curr.end_price > prev.end_price + _EPSILON        # higher high
                    and curr.start_price > prev.start_price + _EPSILON):  # higher low
                hh_hl_count += 1
        if hh_hl_count >= 2:
            score += 20

    # +15: majority bull legs >= 60%
    bull_count = sum(1 for l in window if l.direction == LegDirection.BULL)
    if bull_count / len(window) >= float(_BULL_MAJORITY_MIN):
        score += 15

    # +15: average efficiency >= 0.50
    if window:
        avg_eff = sum(l.efficiency for l in window) / Decimal(len(window))
        if avg_eff >= _TREND_EFF_MIN - _EPSILON:
            score += 15

    # +10: pullbacks <= 50% (bear legs < 50% of immediately preceding bull leg)
    bear_legs_in_window = [l for l in window if l.direction == LegDirection.BEAR]
    if bear_legs_in_window:
        pass_count = 0
        for bl in bear_legs_in_window:
            # Find immediately preceding bull leg in window
            bl_idx = window.index(bl)
            preceding_bull = next(
                (window[j] for j in range(bl_idx - 1, -1, -1)
                 if window[j].direction == LegDirection.BULL),
                None,
            )
            if preceding_bull is None:
                continue
            if bl.displacement < _PULLBACK_RATIO_MAX * preceding_bull.displacement - _EPSILON:
                pass_count += 1
        valid_bear = sum(1 for bl in bear_legs_in_window
                         if any(window[j].direction == LegDirection.BULL
                                for j in range(window.index(bl) - 1, -1, -1)))
        if valid_bear > 0 and pass_count / valid_bear >= float(_PULLBACK_PASS_FRAC):
            score += 10

    # +10: price above ZLEMA majority — count(close > ZLEMA) / n >= 0.60 in last 20 1H bars
    if recent_1h_bars and recent_zlbb:
        pairs = list(zip(recent_1h_bars[-_H1_BAR_WINDOW:], recent_zlbb[-_H1_BAR_WINDOW:]))
        if pairs:
            above_count = sum(
                1 for bar, zs in pairs
                if bar.close > zs.zlema + _EPSILON
            )
            if above_count / len(pairs) >= float(_ZLEMA_ABOVE_FRAC):
                score += 10

    # +10: bull band walk occurred recently — any bull leg in last 10 legs has is_band_walk=True
    recent_legs_bw = list(legs[-_BW_BAR_WINDOW:])
    if any(l.is_band_walk and l.direction == LegDirection.BULL for l in recent_legs_bw):
        score += 10

    # +10: resistance breaks succeeded — >= 2 bull legs in last 5 where end_price > a resistance zone
    active_states_set = {ZoneState.FRESH, ZoneState.TESTED, ZoneState.FLIPPED, ZoneState.BREAK_PENDING}
    res_zones = [
        z for z in zones
        if z.polarity == ZonePolarity.RESISTANCE and z.state in active_states_set
    ]
    if res_zones:
        res_break_count = sum(
            1 for l in window
            if l.direction == LegDirection.BULL
            and any(l.end_price > z.zone_low - _EPSILON for z in res_zones)
        )
        if res_break_count >= _BREAK_COUNT_MIN:
            score += 10

    # +10: bear rejection failed — >= 2 bear legs with displacement < 0.50×ATR(1H)
    #      AND leg end_price is at or inside a support zone
    if atr_1h > _EPSILON and bear_legs_in_window:
        sup_zones = [
            z for z in zones
            if z.polarity == ZonePolarity.SUPPORT and z.state in active_states_set
        ]
        rejection_count = sum(
            1 for bl in bear_legs_in_window
            if bl.displacement < _REJECTION_DISP_MAX * atr_1h - _EPSILON
            and any(z.zone_low - _EPSILON <= bl.end_price <= z.zone_high + _EPSILON for z in sup_zones)
        )
        if rejection_count >= _REJECTION_COUNT_MIN:
            score += 10

    return min(score, 100)


# ---------------------------------------------------------------------------
# Score: TREND_BEAR (mirror of TREND_BULL)
# ---------------------------------------------------------------------------

def score_trend_bear(
    legs: Sequence[CompletedLeg],
    zones: Sequence[SRZone],
    current_price: Decimal,
    atr_1h: Decimal,
    density_bias: DensityBias,
    recent_1h_bars: Sequence[AggregatedBar],
    recent_zlbb: Sequence[ZLBBState],
) -> int:
    """Doc 5 §5.2 + Amendment v1.1 C6 (mirror): TREND_BEAR score [0, 100].

    Eight conditions (max = 100):
    +20: LL+LH — 2 consecutive bear legs where end[n] < end[n-1] AND start[n] < start[n-1]
    +15: majority bear legs >= 60%
    +15: average efficiency >= 0.50
    +10: pullbacks <= 50% — bull leg disp < 0.50× preceding bear; holds for >= 60% bull legs
    +10: price below ZLEMA majority — count(close < ZLEMA) / 20 >= 0.60 in last 20 1H bars
    +10: band walk occurred recently — any bear leg in last 10 legs has is_band_walk=True
    +10: support breaks succeeded — >= 2 bear legs in last 5 where end_price < a support zone_high
    +10: bull rejection failed — >= 2 bull legs where displacement < 0.50×ATR(1H) AND ended at/inside a resistance zone
    """
    if not legs:
        return 0
    n = min(len(legs), _LEG_WINDOW)
    window = list(legs[-n:])
    score = 0

    # +20: LL+LH structure
    bear_legs = [l for l in window if l.direction == LegDirection.BEAR]
    if len(bear_legs) >= 2:
        ll_lh_count = 0
        for k in range(1, len(bear_legs)):
            prev = bear_legs[k - 1]
            curr = bear_legs[k]
            if (curr.end_price < prev.end_price - _EPSILON        # lower low
                    and curr.start_price < prev.start_price - _EPSILON):  # lower high
                ll_lh_count += 1
        if ll_lh_count >= 2:
            score += 20

    # +15: majority bear legs >= 60%
    bear_count = sum(1 for l in window if l.direction == LegDirection.BEAR)
    if bear_count / len(window) >= float(_BULL_MAJORITY_MIN):
        score += 15

    # +15: average efficiency >= 0.50
    if window:
        avg_eff = sum(l.efficiency for l in window) / Decimal(len(window))
        if avg_eff >= _TREND_EFF_MIN - _EPSILON:
            score += 15

    # +10: pullbacks <= 50% (bull retracements < 50% of preceding bear leg)
    bull_legs_in_window = [l for l in window if l.direction == LegDirection.BULL]
    if bull_legs_in_window:
        pass_count = 0
        for bl in bull_legs_in_window:
            bl_idx = window.index(bl)
            preceding_bear = next(
                (window[j] for j in range(bl_idx - 1, -1, -1)
                 if window[j].direction == LegDirection.BEAR),
                None,
            )
            if preceding_bear is None:
                continue
            if bl.displacement < _PULLBACK_RATIO_MAX * preceding_bear.displacement - _EPSILON:
                pass_count += 1
        valid_bull = sum(1 for bl in bull_legs_in_window
                         if any(window[j].direction == LegDirection.BEAR
                                for j in range(window.index(bl) - 1, -1, -1)))
        if valid_bull > 0 and pass_count / valid_bull >= float(_PULLBACK_PASS_FRAC):
            score += 10

    # +10: price below ZLEMA majority — count(close < ZLEMA) / n >= 0.60 in last 20 1H bars
    if recent_1h_bars and recent_zlbb:
        pairs = list(zip(recent_1h_bars[-_H1_BAR_WINDOW:], recent_zlbb[-_H1_BAR_WINDOW:]))
        if pairs:
            below_count = sum(
                1 for bar, zs in pairs
                if bar.close < zs.zlema - _EPSILON
            )
            if below_count / len(pairs) >= float(_ZLEMA_ABOVE_FRAC):
                score += 10

    # +10: bear band walk occurred recently — any bear leg in last 10 legs has is_band_walk=True
    recent_legs_bw = list(legs[-_BW_BAR_WINDOW:])
    if any(l.is_band_walk and l.direction == LegDirection.BEAR for l in recent_legs_bw):
        score += 10

    # +10: support breaks succeeded — >= 2 bear legs in last 5 where end_price < a support zone_high
    active_states_set = {ZoneState.FRESH, ZoneState.TESTED, ZoneState.FLIPPED, ZoneState.BREAK_PENDING}
    sup_zones = [
        z for z in zones
        if z.polarity == ZonePolarity.SUPPORT and z.state in active_states_set
    ]
    if sup_zones:
        sup_break_count = sum(
            1 for l in window
            if l.direction == LegDirection.BEAR
            and any(l.end_price < z.zone_high + _EPSILON for z in sup_zones)
        )
        if sup_break_count >= _BREAK_COUNT_MIN:
            score += 10

    # +10: bull rejection failed — >= 2 bull legs with displacement < 0.50×ATR(1H)
    #      AND leg end_price is at or inside a resistance zone
    if atr_1h > _EPSILON and bull_legs_in_window:
        res_zones = [
            z for z in zones
            if z.polarity == ZonePolarity.RESISTANCE and z.state in active_states_set
        ]
        rejection_count = sum(
            1 for bl in bull_legs_in_window
            if bl.displacement < _REJECTION_DISP_MAX * atr_1h - _EPSILON
            and any(z.zone_low - _EPSILON <= bl.end_price <= z.zone_high + _EPSILON for z in res_zones)
        )
        if rejection_count >= _REJECTION_COUNT_MIN:
            score += 10

    return min(score, 100)


# ---------------------------------------------------------------------------
# Score: BALANCE (P5)
# ---------------------------------------------------------------------------

def score_balance(
    legs: Sequence[CompletedLeg],
    zones: Sequence[SRZone],
    current_price: Decimal,
    atr_1h: Decimal,
    density_bias: DensityBias,
    recent_1h_bars: Sequence[AggregatedBar],
    recent_zlbb: Sequence[ZLBBState],
) -> int:
    """Doc 5 §6 + Amendment v1.2 P5: BALANCE score [0, 100].

    +25: alternating direction frequent — >= 3 direction changes in last 5 legs
    +20: no leg efficiency > 0.65 in last 5 (no strong directional legs)
    +20: mean leg displacement <= 0.75×ATR(1H) (small legs)
         [P5 says < 1.0×ATR; user spec says <= 0.75×ATR — use user spec]
    +20: SR zones exist both above AND below price within 1.5×ATR (both sides defended)
    +15: density_bias is NEUTRAL
    """
    if not legs:
        return 0
    n = min(len(legs), _LEG_WINDOW)
    window = list(legs[-n:])
    score = 0

    # +25: alternating direction — >= 3 direction changes in last 5 legs
    if len(window) >= 2:
        changes = sum(
            1 for k in range(1, len(window))
            if window[k].direction != window[k - 1].direction
        )
        if changes >= _BAL_ALT_MIN:
            score += 25

    # +20: no leg efficiency > 0.65 (all legs are balanced, not trending)
    if all(l.efficiency <= _DIST_EFF_THRESHOLD + _EPSILON for l in window):
        score += 20

    # +20: mean displacement <= 0.75×ATR(1H)
    if atr_1h > _EPSILON and window:
        avg_disp = sum(l.displacement for l in window) / Decimal(len(window))
        if avg_disp <= Decimal("0.75") * atr_1h + _EPSILON:
            score += 20

    # +20: SR zones both above AND below price within 1.5×ATR
    if atr_1h > _EPSILON:
        prox = _DIST_PROXIMITY_MULT * atr_1h
        active_states = {ZoneState.FRESH, ZoneState.TESTED, ZoneState.FLIPPED, ZoneState.BREAK_PENDING}
        has_above = any(
            z.polarity == ZonePolarity.RESISTANCE
            and z.state in active_states
            and z.zone_low >= current_price - _EPSILON
            and z.zone_low <= current_price + prox + _EPSILON
            for z in zones
        )
        has_below = any(
            z.polarity == ZonePolarity.SUPPORT
            and z.state in active_states
            and z.zone_high <= current_price + _EPSILON
            and z.zone_high >= current_price - prox - _EPSILON
            for z in zones
        )
        if has_above and has_below:
            score += 20

    # +15: density_bias is NEUTRAL
    if density_bias == DensityBias.NEUTRAL:
        score += 15

    return min(score, 100)


# ---------------------------------------------------------------------------
# Score: DISTRIBUTION (top-forming)
# ---------------------------------------------------------------------------

def score_distribution(
    legs: Sequence[CompletedLeg],
    zones: Sequence[SRZone],
    current_price: Decimal,
    atr_1h: Decimal,
    density_bias: DensityBias,
    recent_1h_bars: Sequence[AggregatedBar],
    recent_zlbb: Sequence[ZLBBState],
) -> int:
    """Doc 5 §7 + Amendment C6: DISTRIBUTION score [0, 100].

    +25: at least 1 S or A-tier RESISTANCE zone within 1.5×ATR above price
    +20: last 2 completed bull legs show declining highs (each high < previous)
    +20: majority legs bull but with declining efficiency (trend over last 4 legs)
    +20: density_bias is MODERATE_ABOVE or HEAVY_ABOVE
    +15: average bull leg quality < 2 in last 5 legs
    """
    if not legs:
        return 0
    n = min(len(legs), _LEG_WINDOW)
    window = list(legs[-n:])
    score = 0

    # +25: S or A-tier RESISTANCE within 1.5×ATR above
    if atr_1h > _EPSILON:
        prox = _DIST_PROXIMITY_MULT * atr_1h
        active_states = {ZoneState.FRESH, ZoneState.TESTED, ZoneState.FLIPPED, ZoneState.BREAK_PENDING}
        near_res = any(
            z.polarity == ZonePolarity.RESISTANCE
            and z.tier in (Tier.S, Tier.A)
            and z.state in active_states
            and z.zone_low >= current_price - _EPSILON
            and z.zone_low <= current_price + prox + _EPSILON
            for z in zones
        )
        if near_res:
            score += 25

    # +20: last 2 bull legs show declining highs
    bull_legs = [l for l in window if l.direction == LegDirection.BULL]
    if len(bull_legs) >= 2:
        declining = all(
            bull_legs[k].end_price < bull_legs[k - 1].end_price - _EPSILON
            for k in range(1, len(bull_legs))
        )
        if declining:
            score += 20

    # +20: majority legs bull but efficiency declining over last 4 legs
    # "Majority bull" = > 50% of window are bull legs
    # "Declining efficiency" = last 2 consecutive bull legs: eff[n] < eff[n-1]
    bull_count = sum(1 for l in window if l.direction == LegDirection.BULL)
    if bull_count / len(window) > 0.5 and len(bull_legs) >= 2:
        # Check efficiency declining over last 2 bull legs
        if bull_legs[-1].efficiency < bull_legs[-2].efficiency - _EPSILON:
            score += 20

    # +20: density_bias is MODERATE_ABOVE or HEAVY_ABOVE
    if density_bias in (DensityBias.MODERATE_ABOVE, DensityBias.HEAVY_ABOVE):
        score += 20

    # +15: average bull leg quality < 2
    if bull_legs:
        avg_quality = sum(l.quality for l in bull_legs) / len(bull_legs)
        if avg_quality < 2:
            score += 15

    return min(score, 100)


# ---------------------------------------------------------------------------
# Score: ACCUMULATION (mirror of DISTRIBUTION)
# ---------------------------------------------------------------------------

def score_accumulation(
    legs: Sequence[CompletedLeg],
    zones: Sequence[SRZone],
    current_price: Decimal,
    atr_1h: Decimal,
    density_bias: DensityBias,
    recent_1h_bars: Sequence[AggregatedBar],
    recent_zlbb: Sequence[ZLBBState],
) -> int:
    """Doc 5 §8 + Amendment C6 (mirror): ACCUMULATION score [0, 100].

    +25: at least 1 S or A-tier SUPPORT zone within 1.5×ATR below price
    +20: last 2 bear legs show rising lows (each low > previous low)
    +20: majority legs bear but with declining efficiency
    +20: density_bias is MODERATE_BELOW or HEAVY_BELOW
    +15: average bear leg quality < 2 in last 5 legs
    """
    if not legs:
        return 0
    n = min(len(legs), _LEG_WINDOW)
    window = list(legs[-n:])
    score = 0

    # +25: S or A-tier SUPPORT within 1.5×ATR below
    if atr_1h > _EPSILON:
        prox = _DIST_PROXIMITY_MULT * atr_1h
        active_states = {ZoneState.FRESH, ZoneState.TESTED, ZoneState.FLIPPED, ZoneState.BREAK_PENDING}
        near_sup = any(
            z.polarity == ZonePolarity.SUPPORT
            and z.tier in (Tier.S, Tier.A)
            and z.state in active_states
            and z.zone_high <= current_price + _EPSILON
            and z.zone_high >= current_price - prox - _EPSILON
            for z in zones
        )
        if near_sup:
            score += 25

    # +20: last 2 bear legs show rising lows
    bear_legs = [l for l in window if l.direction == LegDirection.BEAR]
    if len(bear_legs) >= 2:
        rising_lows = all(
            bear_legs[k].end_price > bear_legs[k - 1].end_price + _EPSILON
            for k in range(1, len(bear_legs))
        )
        if rising_lows:
            score += 20

    # +20: majority legs bear but efficiency declining
    bear_count = sum(1 for l in window if l.direction == LegDirection.BEAR)
    if bear_count / len(window) > 0.5 and len(bear_legs) >= 2:
        if bear_legs[-1].efficiency < bear_legs[-2].efficiency - _EPSILON:
            score += 20

    # +20: density_bias is MODERATE_BELOW or HEAVY_BELOW
    if density_bias in (DensityBias.MODERATE_BELOW, DensityBias.HEAVY_BELOW):
        score += 20

    # +15: average bear leg quality < 2
    if bear_legs:
        avg_quality = sum(l.quality for l in bear_legs) / len(bear_legs)
        if avg_quality < 2:
            score += 15

    return min(score, 100)


# ---------------------------------------------------------------------------
# Score all five phases in one call
# ---------------------------------------------------------------------------

def score_all(
    legs: Sequence[CompletedLeg],
    zones: Sequence[SRZone],
    current_price: Decimal,
    atr_1h: Decimal,
    density_bias: DensityBias,
    recent_1h_bars: Sequence[AggregatedBar],
    recent_zlbb: Sequence[ZLBBState],
) -> dict:
    """Compute all five phase scores and return dict[Phase, int].

    Applies P10 density modifier and exhaustion penalty AFTER base scoring.
    """
    kwargs = dict(
        legs=legs,
        zones=zones,
        current_price=current_price,
        atr_1h=atr_1h,
        density_bias=density_bias,
        recent_1h_bars=recent_1h_bars,
        recent_zlbb=recent_zlbb,
    )
    raw: dict[Phase, int] = {
        Phase.TREND_BULL:   score_trend_bull(**kwargs),
        Phase.TREND_BEAR:   score_trend_bear(**kwargs),
        Phase.BALANCE:      score_balance(**kwargs),
        Phase.DISTRIBUTION: score_distribution(**kwargs),
        Phase.ACCUMULATION: score_accumulation(**kwargs),
    }

    # P10: density modifier (computed from zones, not from DensityBias enum directly)
    bull_mod, bear_mod = _compute_density_modifier(zones, current_price, atr_1h)
    raw[Phase.TREND_BULL]   = max(0, min(100, raw[Phase.TREND_BULL]   + bull_mod))
    raw[Phase.TREND_BEAR]   = max(0, min(100, raw[Phase.TREND_BEAR]   + bear_mod))
    raw[Phase.DISTRIBUTION] = max(0, min(100, raw[Phase.DISTRIBUTION] + bear_mod))
    raw[Phase.ACCUMULATION] = max(0, min(100, raw[Phase.ACCUMULATION] + bull_mod))

    # P10 §15: exhaustion penalty
    window = list(legs[-_LEG_WINDOW:]) if legs else []
    # Bull exhaustion
    recent_bull = next((l for l in reversed(window) if l.direction == LegDirection.BULL), None)
    if recent_bull and recent_bull.quality <= _EXHAUSTION_QUALITY_MAX and recent_bull.efficiency < _EXHAUSTION_EFF_MAX:
        raw[Phase.TREND_BULL] = max(0, raw[Phase.TREND_BULL] - _EXHAUSTION_PENALTY)
    # Bear exhaustion
    recent_bear = next((l for l in reversed(window) if l.direction == LegDirection.BEAR), None)
    if recent_bear and recent_bear.quality <= _EXHAUSTION_QUALITY_MAX and recent_bear.efficiency < _EXHAUSTION_EFF_MAX:
        raw[Phase.TREND_BEAR] = max(0, raw[Phase.TREND_BEAR] - _EXHAUSTION_PENALTY)

    return raw


def _build_result(
    active_phase: "Phase",
    scores: dict,
    pending_new_phase: Optional["Phase"],
) -> "PhaseResult":
    """Build PhaseResult from scores + stickiness state."""
    sorted_phases = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    best_phase, best_score = sorted_phases[0]
    second_score = sorted_phases[1][1] if len(sorted_phases) > 1 else 0

    margin = best_score - second_score
    transition_flag = (best_phase != active_phase and margin >= _DOMINANCE_MARGIN)
    size_reduction = Decimal("0.5") if transition_flag else _D_ONE

    # confidence = winning / (winning + second) using active phase's score
    active_score = scores.get(active_phase, 0)
    denom = active_score + second_score if active_score + second_score > 0 else 1
    confidence = Decimal(active_score) / Decimal(denom)
    # Clamp to [0, 1]
    confidence = max(_D_ZERO, min(_D_ONE, confidence))

    return PhaseResult(
        active_phase=active_phase,
        phase_confidence=confidence,
        transition_flag=transition_flag,
        size_reduction=size_reduction,
        all_scores=dict(scores),
    )


# ---------------------------------------------------------------------------
# PhaseEngine — stateful class
# ---------------------------------------------------------------------------

class PhaseEngine:
    """Doc 5 + M1 + P8: Streaming phase regime engine.

    One instance per instrument.

    Usage::

        engine = PhaseEngine()

        # On every 15m bar close:
        result = engine.update(
            bar=bar_15m,
            legs=recent_legs,
            zones=detector.get_active_zones(),
            current_price=bar_15m.close,
            atr_1h=atr_1h_calc.current_atr,
            density_bias=detector.density_bias,
            recent_1h_bars=last_20_1h_bars,
            recent_zlbb=last_20_zlbb_states,
            is_1h_close=True,   # True only when a 1H bar just closed
        )

    State machine (M1 + P8):
      - On every call: recompute scores, emit result with CURRENT active_phase label.
      - On 1H close ONLY: run stickiness logic to potentially advance pending_new_phase.
      - Phase label changes ONLY after 2 consecutive 1H closes where SAME phase wins.
    """

    def __init__(self, initial_phase: Optional[Phase] = None) -> None:
        self._active_phase:     Phase = initial_phase or Phase.BALANCE
        self._pending_phase:    Optional[Phase] = None
        self._consecutive_count: int = 0
        # Last computed scores (stored every 15m, applied to label only on 1H)
        self._last_scores: dict = {p: 0 for p in Phase}

    @property
    def active_phase(self) -> Phase:
        return self._active_phase

    @property
    def last_scores(self) -> dict:
        return dict(self._last_scores)

    def update(
        self,
        bar: AggregatedBar,
        legs: Sequence[CompletedLeg],
        zones: Sequence[SRZone],
        current_price: Decimal,
        atr_1h: Decimal,
        density_bias: DensityBias,
        recent_1h_bars: Sequence[AggregatedBar],
        recent_zlbb: Sequence[ZLBBState],
        is_1h_close: bool = False,
    ) -> PhaseResult:
        """Process one bar (15m). On 1H close, also run stickiness logic.

        M1: scores recalculated every 15m. Label changes only after 2×1H closes.
        P8: consecutive_count only increments when the SAME new phase leads again.
        """
        if not bar.is_complete:
            return _build_result(self._active_phase, self._last_scores, self._pending_phase)

        # 1. Compute scores on every bar
        scores = score_all(
            legs=legs,
            zones=zones,
            current_price=current_price,
            atr_1h=atr_1h,
            density_bias=density_bias,
            recent_1h_bars=recent_1h_bars,
            recent_zlbb=recent_zlbb,
        )
        self._last_scores = scores

        # 2. Stickiness logic — only on 1H bar close (M1 / P8)
        if is_1h_close:
            self._advance_stickiness(scores)

        return _build_result(self._active_phase, scores, self._pending_phase)

    def score_only(
        self,
        legs: Sequence[CompletedLeg],
        zones: Sequence[SRZone],
        current_price: Decimal,
        atr_1h: Decimal,
        density_bias: DensityBias,
        recent_1h_bars: Sequence[AggregatedBar],
        recent_zlbb: Sequence[ZLBBState],
    ) -> PhaseResult:
        """Compute scores WITHOUT advancing the stickiness FSM. Pure query."""
        scores = score_all(
            legs=legs,
            zones=zones,
            current_price=current_price,
            atr_1h=atr_1h,
            density_bias=density_bias,
            recent_1h_bars=recent_1h_bars,
            recent_zlbb=recent_zlbb,
        )
        return _build_result(self._active_phase, scores, self._pending_phase)

    # ------------------------------------------------------------------
    # Internal stickiness FSM (M1 + P8)
    # ------------------------------------------------------------------

    def _advance_stickiness(self, scores: dict) -> None:
        """M1 + P8: Advance pending phase state on each 1H bar close.

        P8 pseudocode (exact from amendment):
          IF current_winner != active_phase AND margin >= 10:
            IF current_winner == pending_new_phase:
              consecutive_count += 1
            ELSE:
              pending_new_phase = current_winner
              consecutive_count = 1
            IF consecutive_count >= 2:
              active_phase = pending_new_phase
              pending_new_phase = None; consecutive_count = 0
          ELSE:
            pending_new_phase = None; consecutive_count = 0
        """
        sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        if not sorted_scores:
            return
        current_winner, best_score = sorted_scores[0]
        second_score = sorted_scores[1][1] if len(sorted_scores) > 1 else 0
        margin = best_score - second_score

        if current_winner != self._active_phase and margin >= _DOMINANCE_MARGIN:
            if current_winner == self._pending_phase:
                self._consecutive_count += 1
            else:
                self._pending_phase      = current_winner
                self._consecutive_count  = 1
            if self._consecutive_count >= _CONSECUTIVE_REQUIRED:
                logger.info(
                    "Phase change: %s → %s (margin=%d, consecutive=%d)",
                    self._active_phase.name, self._pending_phase.name,
                    margin, self._consecutive_count,
                )
                self._active_phase      = self._pending_phase
                self._pending_phase     = None
                self._consecutive_count = 0
        else:
            # P8: current phase wins or margin too small → reset pending
            self._pending_phase      = None
            self._consecutive_count  = 0
