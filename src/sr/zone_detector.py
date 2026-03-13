"""Doc 4 + Amendment v1.1 (C2,C3,C4,M4,M5,C7) + Amendment v1.2 (P2,P3,P7,P11):
SR Zone Detector — streaming, deterministic, no float.

Authority hierarchy (strictly enforced):
  Doc 4 §6       — clustering threshold = 0.5 × ATR(1H)  [overrides Addendum B]
  Doc 4 §7 + M5  — zone width formula (§5 RETIRED)
  Amendment M4   — breakout candle count uses 15m candles only
  Addendum B     — used ONLY for: rejection counting (B3.3), density (B3.8)

SR Sources implemented (Doc 4 §4):
  §4.1  Leg Extremes          base = 2 + leg_quality
  §4.2  Open/Close Clusters   +2 if ≥ 3 opens/closes within 0.15 × ATR(native_tf)
  §4.3  Rejection Bars        +2 if wick ≥ 50% of range + reversal in 3 bars (Addendum B3.3)
  §4.4  Midpoints             +1, CANDIDATE state until validated (C7)

Clustering (Doc 4 §6):
  merge_threshold = 0.5 × ATR(1H)
  merged_center   = weighted average by displacement
  Post-clustering min-gap: max(0.40% price, 0.25 × ATR(native_tf))

Zone Width (Amendment v1.1 M5 — §7 sole formula, §5 RETIRED):
  After clustering + min-gap pass only.
  effective_range = min(range_left, range_right)
  zone_half_width = min(0.10 × effective_range, 1.5 × ATR(15m))
  Edge zones: first → range_right; last → range_left

Lifecycle (P11 complete FSM):
  States: CANDIDATE → FRESH → TESTED ↔ BREAK_PENDING → BROKEN_CONFIRMED → FLIPPED
          TESTED → FROZEN → EXPIRED

Density (Addendum B §B3.8):
  scan_range = 3.0 × ATR(4H)
  B-TIER+ zones only; weighted by strength sum
  Output: HEAVY_ABOVE / MODERATE_ABOVE / NEUTRAL / MODERATE_BELOW / HEAVY_BELOW

All arithmetic uses Decimal. No float anywhere.
"""

import datetime
import hashlib
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum, auto
from typing import Optional

from src.data_ingest.bar_aggregator import AggregatedBar
from src.indicators.atr import ATRCalculator
from src.indicators.zlbb import CompletedLeg, LegDirection

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_EPSILON = Decimal("1E-9")
_D_ONE   = Decimal("1")
_D_ZERO  = Decimal("0")

# Zone width formula (M5 / §7)
_WIDTH_STRUCT_FRAC  = Decimal("0.10")   # 10% of effective_range
_WIDTH_ATR_MULT     = Decimal("1.5")    # cap: 1.5 × ATR(15m)

# Clustering (Doc 4 §6)
_CLUSTER_THRESH_MULT = Decimal("0.5")   # 0.5 × ATR(1H)

# Min-gap (C7)
_MIN_GAP_PCT         = Decimal("0.004") # 0.40% of price
_MIN_GAP_ATR_MULT    = Decimal("0.25")  # 0.25 × ATR(native TF)

# Zone strength — source bonuses
_OPEN_CLOSE_BONUS    = Decimal("2")
_REJECTION_BONUS     = Decimal("2")
_MIDPOINT_BONUS      = Decimal("1")

# Lifecycle thresholds (P11)
_BREAK_CONSEC_NEEDED    = 4    # consecutive closes beyond boundary → BROKEN_CONFIRMED (C3/M4)
_FAILED_BREAK_CONSEC    = 2    # consecutive closes back inside within window → revert TESTED
_FAILED_BREAK_WINDOW    = 4    # 4-bar window for failed breakout detection (C3)
_BROKEN_CONFIRM_PERSIST = 1    # bars after BROKEN_CONFIRMED before FLIPPED (P2)
_FROZEN_BARS            = 100  # bars without touch → FROZEN (P11)
_EXPIRED_BARS           = 200  # bars without touch → EXPIRED (P11)
_EXPIRED_ATR_DIST       = Decimal("5")  # price > 5×ATR from center → EXPIRED (P11)

# Decay thresholds (P11 / §12)
_DECAY_BARS = 50  # every 50 TF bars without touch → strength -1

# Touch bonus cap
_TOUCH_BONUS_CAP = 3  # max +3 per touch accumulation

# Source bonus caps (Doc 4 §4.2: each source type contributes once)
_MAX_SOURCE_BONUS_PER_TYPE = 2   # max bonus from any single source type
_MAX_SOURCE_BONUS_TOTAL = 6      # max total source_bonus (3 types × 2 each)

# Candidate validation (C7)
_CANDIDATE_REJECTION_NEEDED = 2
_CANDIDATE_LTF_CANDLES      = 5
_CANDIDATE_EXPIRY_BARS      = 50

# Density (Addendum B §B3.8)
_DENSITY_SCAN_ATR_MULT = Decimal("3.0")
_DENSITY_HEAVY_ABOVE   = Decimal("2.0")
_DENSITY_MODERATE_ABOVE = Decimal("1.5")
_DENSITY_MODERATE_BELOW = Decimal("0.67")
_DENSITY_HEAVY_BELOW    = Decimal("0.5")

# Breakout epsilon (P7)
_BREAKOUT_EPSILON_MULT = Decimal("0.02")  # 0.02 × ATR(15m)

# Open/close cluster (§4.2)
_CLUSTER_OC_ATR_MULT    = Decimal("0.15")
_CLUSTER_OC_MIN_CANDLES = 3

# Rejection bar (§4.3 / Addendum B §B3.3)
_REJECTION_WICK_MIN_FRAC  = Decimal("0.50")   # wick ≥ 50% of total range
_REJECTION_BODY_MAX_RATIO = Decimal("0.40")   # body_ratio < 0.40
_REJECTION_RANGE_MIN_MULT = Decimal("0.25")   # bar_range > 0.25 × ATR

# Midpoint (§4.4)
_MIDPOINT_DISP_MIN_MULT = Decimal("2")  # displacement ≥ 2 × ATR


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class ZoneState(Enum):
    """Doc 4 §8 + C2/C3/C4/C7 + P11: Complete zone lifecycle states."""
    CANDIDATE        = auto()  # midpoint — unvalidated (C7)
    FRESH            = auto()  # newly created, not yet touched
    TESTED           = auto()  # at least one confirmed touch
    BREAK_PENDING    = auto()  # 1-3 closes beyond boundary (C3)
    BROKEN_CONFIRMED = auto()  # 4 consecutive closes beyond (C3)
    FLIPPED          = auto()  # polarity inverted after BROKEN_CONFIRMED (C3)
    FROZEN           = auto()  # no touch for FROZEN_BARS (P11)
    EXPIRED          = auto()  # permanently removed (P11)


# Lifecycle state priority for clustering template selection (Fix B)
# Higher = more advanced lifecycle = preferred as merge template
_LIFECYCLE_PRIORITY: dict[ZoneState, int] = {
    ZoneState.FLIPPED: 6,
    ZoneState.BROKEN_CONFIRMED: 5,
    ZoneState.BREAK_PENDING: 4,
    ZoneState.TESTED: 3,
    ZoneState.FRESH: 2,
    ZoneState.CANDIDATE: 1,
    ZoneState.FROZEN: 0,
    ZoneState.EXPIRED: 0,
}


class ZonePolarity(Enum):
    """Zone polarity."""
    SUPPORT    = auto()
    RESISTANCE = auto()


class ZoneOrigin(Enum):
    """How the zone was created."""
    LEG_EXTREME      = auto()
    OPEN_CLOSE       = auto()
    REJECTION_BAR    = auto()
    MIDPOINT         = auto()
    BAND_WALK_SUB_LEG = auto()


class Tier(Enum):
    """Doc 4 §7 + Addendum B: Tier by strength score."""
    S = auto()  # ≥ 15
    A = auto()  # 10-14
    B = auto()  # 5-9
    C = auto()  # < 5


class DensityBias(Enum):
    """Addendum B §B3.8: Directional density signal."""
    HEAVY_ABOVE    = auto()
    MODERATE_ABOVE = auto()
    NEUTRAL        = auto()
    MODERATE_BELOW = auto()
    HEAVY_BELOW    = auto()


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class _ZoneMutable:
    """Doc 4 §15 + P11: Mutable SR zone state container.

    All price values Decimal. Timestamps UTC-aware datetimes.
    """
    zone_id:          str           # deterministic sha256 from creation params
    center:           Decimal       # price center (raw → updated post-clustering)
    zone_high:        Decimal       # center + half_width
    zone_low:         Decimal       # center - half_width
    polarity:         ZonePolarity
    origin:           ZoneOrigin
    timeframe:        str           # native SR timeframe (e.g. "1H")
    is_midpoint:      bool

    # Strength
    base_strength:    int           # 2 + leg_quality for leg extremes; 2 for others
    source_bonus:     int           # open/close: +2, rejection: +2, midpoint: +1
    touch_count:      int = 0       # touches since FRESH (or post-flip)
    false_break_count: int = 0
    frozen_touch_bonus: int = 0    # P3: pre-flip touches, frozen on FLIPPED, never reset

    # Lifecycle
    state:            ZoneState = ZoneState.FRESH
    created_at:       Optional[datetime.datetime] = None
    last_touch_time:  Optional[datetime.datetime] = None
    confirmed_at:     Optional[datetime.datetime] = None  # when BROKEN_CONFIRMED set (P2)
    bars_since_touch: int = 0      # bars counted since last touch (for decay + FROZEN)
    bars_since_confirmed: int = 0  # P2: bars since BROKEN_CONFIRMED

    # BREAK_PENDING sub-state
    break_consecutive:  int = 0    # closes beyond boundary in current streak
    inside_consecutive: int = 0    # consecutive closes back inside (failed break)
    break_direction:    Optional[str] = None  # "up" | "down"
    break_window_bars:  int = 0    # bars since BREAK_PENDING entered (for 4-bar window)

    # CANDIDATE sub-state (C7 / midpoint)
    candidate_rejection_count: int = 0
    candidate_ltf_candle_count: int = 0
    candidate_bars_elapsed:    int = 0

    # Displacement (used for weighted clustering merge)
    displacement:     Decimal = Decimal("0")

    # Computed tier (updated after every strength change)
    tier:             Tier = Tier.C

    def total_strength(self) -> int:
        """Compute total strength = base + source_bonus + touch_bonus + frozen_touch_bonus.

        Touch bonus capped at +3.  Decay already applied to base_strength.
        """
        touch_bonus = min(self.touch_count, _TOUCH_BONUS_CAP)
        return self.base_strength + self.source_bonus + touch_bonus + self.frozen_touch_bonus

    def recompute_tier(self) -> None:
        """Assign tier from total_strength (Addendum B §B3.4 tiers)."""
        s = self.total_strength()
        if s >= 15:
            self.tier = Tier.S
        elif s >= 10:
            self.tier = Tier.A
        elif s >= 5:
            self.tier = Tier.B
        else:
            self.tier = Tier.C


@dataclass(frozen=True)
class SRZone:
    """Immutable public snapshot of a zone. Emitted by get_active_zones()."""
    zone_id:      str
    center:       Decimal
    zone_high:    Decimal
    zone_low:     Decimal
    polarity:     ZonePolarity
    origin:       ZoneOrigin
    timeframe:    str
    is_midpoint:  bool
    state:        ZoneState
    tier:         Tier
    strength:     int
    touch_count:  int
    false_break_count: int
    created_at:   Optional[datetime.datetime]
    last_touch_time: Optional[datetime.datetime]


# ---------------------------------------------------------------------------
# Helper: deterministic zone ID
# ---------------------------------------------------------------------------

def _make_zone_id(
    center: Decimal,
    polarity: ZonePolarity,
    timeframe: str,
    created_at: datetime.datetime,
) -> str:
    """Deterministic SHA-256 zone ID (Doc 1 §4 — never uuid4)."""
    content = f"{center}|{polarity.name}|{timeframe}|{created_at.isoformat()}"
    return hashlib.sha256(content.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Helper: tier assignment (standalone, for post-cluster use)
# ---------------------------------------------------------------------------

def _assign_tier(strength: int) -> Tier:
    """Addendum B §B3.4 / user spec: S≥15, A=10-14, B=5-9, C<5."""
    if strength >= 15:
        return Tier.S
    if strength >= 10:
        return Tier.A
    if strength >= 5:
        return Tier.B
    return Tier.C


# ---------------------------------------------------------------------------
# Source detection helpers
# ---------------------------------------------------------------------------

def _detect_open_close_cluster(
    bars: list[AggregatedBar],
    atr: Decimal,
    native_tf: str,
) -> list[tuple[Decimal, ZonePolarity]]:
    """Doc 4 §4.2: Open/Close cluster → SR center if ≥ 3 opens/closes within 0.15 × ATR.

    Returns list of (price, polarity) pairs where polarity is BOTH but we emit
    SUPPORT+RESISTANCE pair. We return centers only.
    """
    if atr <= _EPSILON:
        return []
    threshold = _CLUSTER_OC_ATR_MULT * atr
    prices: list[Decimal] = []
    for bar in bars:
        prices.append(bar.open)
        prices.append(bar.close)
    prices.sort()

    # Sequential cluster pass
    result: list[Decimal] = []
    i = 0
    while i < len(prices):
        cluster_start = prices[i]
        cluster_prices = [cluster_start]
        j = i + 1
        while j < len(prices) and prices[j] - cluster_start <= threshold + _EPSILON:
            cluster_prices.append(prices[j])
            j += 1
        if len(cluster_prices) >= _CLUSTER_OC_MIN_CANDLES:
            center = sum(cluster_prices, _D_ZERO) / Decimal(len(cluster_prices))
            result.append(center)
        i = j if j > i else i + 1

    # Emit as BOTH polarities (open/close acts as both support and resistance)
    pairs: list[tuple[Decimal, ZonePolarity]] = []
    for c in result:
        pairs.append((c, ZonePolarity.SUPPORT))
        pairs.append((c, ZonePolarity.RESISTANCE))
    return pairs


def _detect_rejection_bars(
    bars: list[AggregatedBar],
    atr: Decimal,
) -> list[tuple[Decimal, ZonePolarity]]:
    """Doc 4 §4.3 + Addendum B §B3.3: Rejection bar detection.

    Hammer or Inverted Hammer: wick ≥ 50% of total range AND reversal within 3 bars.
    Also checks Addendum B conditions: bar_range > 0.25 × ATR, body_ratio < 0.40.
    Requires ≥ 3 rejections in a cluster to create a zone (Addendum B §B3.3).

    Returns list of (price, polarity): price = bar.low (support) or bar.high (resistance).
    """
    if atr <= _EPSILON or not bars:
        return []
    min_range = _REJECTION_RANGE_MIN_MULT * atr

    # Collect per-bar rejection price candidates with reversal validation
    rejection_prices_sup: list[Decimal] = []   # hammer lows (support)
    rejection_prices_res: list[Decimal] = []   # inverted hammer highs (resistance)

    for idx, bar in enumerate(bars):
        bar_range = bar.high - bar.low
        if bar_range < min_range or bar_range <= _EPSILON:
            continue
        body = abs(bar.close - bar.open)
        body_ratio = body / bar_range
        if body_ratio >= _REJECTION_BODY_MAX_RATIO:
            continue  # Addendum B: body must be small

        upper_wick = bar.high - max(bar.open, bar.close)
        lower_wick = min(bar.open, bar.close) - bar.low

        # Bullish rejection (hammer): lower wick ≥ 50%
        if lower_wick >= _REJECTION_WICK_MIN_FRAC * bar_range - _EPSILON:
            # Validate: price doesn't go below bar.low in next 3 bars AND
            # closes above bar.high (Doc 4 §4.3)
            reversal_ok = False
            for k in range(idx + 1, min(idx + 4, len(bars))):
                if bars[k].low < bar.low - _EPSILON:
                    reversal_ok = False
                    break
                if bars[k].close > bar.high - _EPSILON:
                    reversal_ok = True
            if reversal_ok:
                rejection_prices_sup.append(bar.low)

        # Bearish rejection (inverted hammer): upper wick ≥ 50%
        elif upper_wick >= _REJECTION_WICK_MIN_FRAC * bar_range - _EPSILON:
            # Validate: price doesn't go above bar.high AND closes below bar.low
            reversal_ok = False
            for k in range(idx + 1, min(idx + 4, len(bars))):
                if bars[k].high > bar.high + _EPSILON:
                    reversal_ok = False
                    break
                if bars[k].close < bar.low + _EPSILON:
                    reversal_ok = True
            if reversal_ok:
                rejection_prices_res.append(bar.high)

    # Cluster rejection prices; require ≥ 3 per cluster (Addendum B §B3.3)
    cluster_thresh = _CLUSTER_OC_ATR_MULT * atr  # 0.15 × ATR for rejection clustering
    result: list[tuple[Decimal, ZonePolarity]] = []

    for price_list, polarity in [
        (sorted(rejection_prices_sup), ZonePolarity.SUPPORT),
        (sorted(rejection_prices_res), ZonePolarity.RESISTANCE),
    ]:
        i = 0
        while i < len(price_list):
            cluster_start = price_list[i]
            cluster = [cluster_start]
            j = i + 1
            while j < len(price_list) and price_list[j] - cluster_start <= cluster_thresh + _EPSILON:
                cluster.append(price_list[j])
                j += 1
            if len(cluster) >= 3:  # §B3.3 requires ≥ 3 rejections
                center = sum(cluster, _D_ZERO) / Decimal(len(cluster))
                result.append((center, polarity))
            i = j if j > i else i + 1

    return result


# ---------------------------------------------------------------------------
# Clustering (Doc 4 §6)
# ---------------------------------------------------------------------------

def _cluster_merge(
    zones: list["_ZoneMutable"],
    merge_threshold: Decimal,
) -> list["_ZoneMutable"]:
    """Doc 4 §6.3-§6.7: Sequential ascending-sort cluster merge.

    Two zones merge if |center_i - center_j| ≤ merge_threshold.
    Merged center = strength-weighted average by displacement.
    Merged strength = sum; touch_count = sum; frozen_touch_bonus = max.
    """
    if not zones:
        return []
    # Sort by center ascending (§6.7 step 1)
    zones_sorted = sorted(zones, key=lambda z: z.center)

    merged: list["_ZoneMutable"] = []
    cluster: list["_ZoneMutable"] = [zones_sorted[0]]

    for z in zones_sorted[1:]:
        # Distance from current zone to last in cluster (sequential check §6.7)
        if z.center - cluster[-1].center <= merge_threshold + _EPSILON:
            cluster.append(z)
        else:
            merged.append(_finalize_cluster(cluster))
            cluster = [z]
    merged.append(_finalize_cluster(cluster))
    return merged


def _finalize_cluster(cluster: list["_ZoneMutable"]) -> "_ZoneMutable":
    """Doc 4 §6.4: Weighted-average merge of a cluster of zones.

    Center = displacement-weighted average.
    Strength = sum (base_strength from all members).
    touch_count = sum. frozen_touch_bonus = max (preserve strongest pre-flip bonus).

    Fix B: Template selection prefers advanced lifecycle state (FLIPPED > TESTED > FRESH)
    via tuple key (lifecycle_priority, total_strength). This ensures a FLIPPED zone
    is never overwritten by a stronger FRESH zone during clustering.
    """
    if len(cluster) == 1:
        return cluster[0]

    # Weighted center (by displacement)
    total_disp = sum(z.displacement for z in cluster)
    if total_disp <= _EPSILON:
        # Fall back to simple mean if no displacement info
        center = sum(z.center for z in cluster) / Decimal(len(cluster))
    else:
        center = sum(z.center * z.displacement for z in cluster) / total_disp

    # Fix B: Prefer advanced lifecycle state, then strength as tiebreaker
    template = max(
        cluster,
        key=lambda z: (_LIFECYCLE_PRIORITY.get(z.state, 0), z.total_strength()),
    )

    # Fix B: Defensive guard — carry forward lifecycle fields from most advanced zone
    most_advanced = max(cluster, key=lambda z: _LIFECYCLE_PRIORITY.get(z.state, 0))
    if _LIFECYCLE_PRIORITY.get(most_advanced.state, 0) > _LIFECYCLE_PRIORITY.get(template.state, 0):
        template.state = most_advanced.state
        template.touch_count = max(template.touch_count, most_advanced.touch_count)
        template.false_break_count = max(template.false_break_count, most_advanced.false_break_count)
        template.frozen_touch_bonus = max(template.frozen_touch_bonus, most_advanced.frozen_touch_bonus)
        template.last_touch_time = most_advanced.last_touch_time or template.last_touch_time
        template.confirmed_at = most_advanced.confirmed_at or template.confirmed_at
        template.bars_since_touch = min(template.bars_since_touch, most_advanced.bars_since_touch)
        template.break_consecutive = most_advanced.break_consecutive
        template.break_direction = most_advanced.break_direction
        template.break_window_bars = most_advanced.break_window_bars
        template.bars_since_confirmed = most_advanced.bars_since_confirmed

    template.center = center
    # Merge strength: sum base_strength, accumulate touch counts
    template.base_strength = sum(z.base_strength for z in cluster)
    template.source_bonus  = min(sum(z.source_bonus for z in cluster), _MAX_SOURCE_BONUS_TOTAL)
    template.touch_count   = sum(z.touch_count for z in cluster)
    template.false_break_count = sum(z.false_break_count for z in cluster)
    template.frozen_touch_bonus = max(z.frozen_touch_bonus for z in cluster)
    template.displacement = max(z.displacement for z in cluster)
    template.recompute_tier()
    return template


# ---------------------------------------------------------------------------
# Min-gap enforcement (C7)
# ---------------------------------------------------------------------------

def _enforce_min_gap(
    zones: list["_ZoneMutable"],
    current_price: Decimal,
    native_atr: Decimal,
) -> list["_ZoneMutable"]:
    """Amendment v1.1 C7: min gap = max(0.40% price, 0.25 × ATR).

    If two zones are closer than min_gap, weaker is merged into stronger.
    """
    if not zones or native_atr <= _EPSILON or current_price <= _EPSILON:
        return zones

    min_gap = max(_MIN_GAP_PCT * current_price, _MIN_GAP_ATR_MULT * native_atr)
    zones_sorted = sorted(zones, key=lambda z: z.center)
    result: list["_ZoneMutable"] = [zones_sorted[0]]

    for z in zones_sorted[1:]:
        prev = result[-1]
        if z.center - prev.center < min_gap - _EPSILON:
            # Merge weaker into stronger
            if z.total_strength() > prev.total_strength():
                # z wins — replace prev
                z.base_strength  += prev.base_strength
                z.source_bonus   += prev.source_bonus
                z.touch_count    += prev.touch_count
                z.displacement    = max(z.displacement, prev.displacement)
                z.frozen_touch_bonus = max(z.frozen_touch_bonus, prev.frozen_touch_bonus)
                z.recompute_tier()
                result[-1] = z
            else:
                # prev wins — absorb z
                prev.base_strength  += z.base_strength
                prev.source_bonus   += z.source_bonus
                prev.touch_count    += z.touch_count
                prev.displacement    = max(prev.displacement, z.displacement)
                prev.frozen_touch_bonus = max(prev.frozen_touch_bonus, z.frozen_touch_bonus)
                prev.recompute_tier()
        else:
            result.append(z)
    return result


# ---------------------------------------------------------------------------
# Zone width computation (M5 / §7)
# ---------------------------------------------------------------------------

def _compute_zone_widths(
    zones: list["_ZoneMutable"],
    atr_15m: Decimal,
) -> None:
    """Amendment v1.1 M5 — §7 sole formula: compute zone boundaries in-place.

    Must be called AFTER clustering and min-gap pass.
    effective_range = min(range_left, range_right)
    zone_half_width = min(0.10 × effective_range, 1.5 × ATR(15m))
    Edge zones: first → use range_right; last → range_left.
    """
    if not zones or atr_15m <= _EPSILON:
        # fallback: use ATR-based width
        for z in zones:
            hw = _WIDTH_ATR_MULT * atr_15m if atr_15m > _EPSILON else Decimal("1")
            z.zone_high = z.center + hw
            z.zone_low  = z.center - hw
        return

    centers = [z.center for z in zones]
    n = len(centers)

    for i, z in enumerate(zones):
        if n == 1:
            # Only one zone — use ATR cap directly
            half_width = _WIDTH_ATR_MULT * atr_15m
        elif i == 0:
            # Edge: first zone — use range_right
            effective_range = centers[1] - centers[0]
            half_width = min(
                _WIDTH_STRUCT_FRAC * effective_range,
                _WIDTH_ATR_MULT * atr_15m,
            )
        elif i == n - 1:
            # Edge: last zone — use range_left
            effective_range = centers[n - 1] - centers[n - 2]
            half_width = min(
                _WIDTH_STRUCT_FRAC * effective_range,
                _WIDTH_ATR_MULT * atr_15m,
            )
        else:
            range_left  = centers[i] - centers[i - 1]
            range_right = centers[i + 1] - centers[i]
            effective_range = min(range_left, range_right)
            half_width = min(
                _WIDTH_STRUCT_FRAC * effective_range,
                _WIDTH_ATR_MULT * atr_15m,
            )
        # Guard: half_width must be positive
        if half_width <= _EPSILON:
            half_width = _WIDTH_ATR_MULT * atr_15m
        z.zone_high = z.center + half_width
        z.zone_low  = z.center - half_width


# ---------------------------------------------------------------------------
# Lifecycle FSM helpers
# ---------------------------------------------------------------------------

def _is_close_beyond_boundary(
    bar_close: Decimal,
    zone: "_ZoneMutable",
    breakout_epsilon: Decimal,
) -> Optional[str]:
    """P7: True if bar.close is decisively beyond zone boundary.

    Returns "up" if above zone_high + epsilon, "down" if below zone_low - epsilon,
    else None.
    """
    if bar_close > zone.zone_high + breakout_epsilon + _EPSILON:
        return "up"
    if bar_close < zone.zone_low - breakout_epsilon - _EPSILON:
        return "down"
    return None


def _is_close_inside_zone(bar_close: Decimal, zone: "_ZoneMutable") -> bool:
    """C3: Close is back inside zone (between zone_low and zone_high, inclusive)."""
    return zone.zone_low <= bar_close <= zone.zone_high


def _bar_touches_zone(bar: AggregatedBar, zone: "_ZoneMutable") -> bool:
    """Addendum B §B4.1: Bar interacts with zone if bar range overlaps zone range."""
    return bar.low <= zone.zone_high + _EPSILON and bar.high >= zone.zone_low - _EPSILON


def _bar_enters_zone(bar: AggregatedBar, zone: "_ZoneMutable") -> bool:
    """Price enters zone: high or low is within zone bounds."""
    return bar.high >= zone.zone_low - _EPSILON and bar.low <= zone.zone_high + _EPSILON


def _step_lifecycle(
    zone: "_ZoneMutable",
    bar: AggregatedBar,
    breakout_epsilon: Decimal,
    bars_elapsed: int,  # bars since last touch (used for decay / freeze / expire)
    current_price: Decimal,
    native_atr: Decimal,
) -> None:
    """P11: Advance zone lifecycle state machine for one bar.

    Mutates zone in place.  All logic follows P11 table + C3 + P2 + P7.
    """
    state = zone.state

    # CANDIDATE (C7): validate or expire
    if state == ZoneState.CANDIDATE:
        _step_candidate(zone, bar)
        return

    if state == ZoneState.EXPIRED:
        return  # terminal

    # Count bars for decay / freeze / expire
    touches = _bar_touches_zone(bar, zone)
    if touches:
        zone.bars_since_touch = 0
        zone.last_touch_time = bar.timestamp_start
    else:
        zone.bars_since_touch += 1

    # P2: BROKEN_CONFIRMED persistence counter
    if state == ZoneState.BROKEN_CONFIRMED:
        zone.bars_since_confirmed += 1
        if zone.bars_since_confirmed >= _BROKEN_CONFIRM_PERSIST:
            # Transition to FLIPPED (P2)
            _transition_to_flipped(zone, bar)
        return

    # FROZEN: check expire
    if state == ZoneState.FROZEN:
        _check_expire(zone, current_price, native_atr)
        return

    # EXPIRED: skip (handled above)

    # BREAK_PENDING: evaluate continuation or revert
    if state == ZoneState.BREAK_PENDING:
        _step_break_pending(zone, bar, breakout_epsilon)
        return

    # FLIPPED: treat as normal FRESH/TESTED zone of opposite polarity
    # No special handling needed for FLIPPED beyond what FRESH/TESTED does
    if state == ZoneState.FLIPPED:
        _step_fresh_or_tested(zone, bar, breakout_epsilon)
        return

    # FRESH or TESTED
    if state in (ZoneState.FRESH, ZoneState.TESTED):
        _step_fresh_or_tested(zone, bar, breakout_epsilon)
        _apply_decay_and_freeze(zone, current_price, native_atr)
        return


def _step_candidate(zone: "_ZoneMutable", bar: AggregatedBar) -> None:
    """C7: CANDIDATE → FRESH on ≥2 rejections OR ≥5 LTF candles within zone.

    CANDIDATE → discarded after 50 LTF bars.
    """
    zone.candidate_bars_elapsed += 1
    if _bar_enters_zone(bar, zone):
        zone.candidate_ltf_candle_count += 1
        # Count rejection: upper or lower wick ≥ 50% of range
        bar_range = bar.high - bar.low
        if bar_range > _EPSILON:
            upper_wick = bar.high - max(bar.open, bar.close)
            lower_wick = min(bar.open, bar.close) - bar.low
            wick_max = max(upper_wick, lower_wick)
            if wick_max >= _REJECTION_WICK_MIN_FRAC * bar_range - _EPSILON:
                zone.candidate_rejection_count += 1

    # Validation criteria (C7)
    if (zone.candidate_rejection_count >= _CANDIDATE_REJECTION_NEEDED
            or zone.candidate_ltf_candle_count >= _CANDIDATE_LTF_CANDLES):
        zone.state = ZoneState.FRESH
        logger.debug("Zone %s CANDIDATE→FRESH", zone.zone_id)
        return

    # Expiry (C7: 50 LTF bars)
    if zone.candidate_bars_elapsed >= _CANDIDATE_EXPIRY_BARS:
        zone.state = ZoneState.EXPIRED
        logger.debug("Zone %s CANDIDATE→EXPIRED (timeout)", zone.zone_id)


def _step_fresh_or_tested(
    zone: "_ZoneMutable",
    bar: AggregatedBar,
    breakout_epsilon: Decimal,
) -> None:
    """P11: FRESH/TESTED/FLIPPED state transitions.

    → BREAK_PENDING: on 1st close beyond boundary + epsilon
    → TESTED (from FRESH): first touch
    → TESTED (additional): touch_count += 1
    """
    direction = _is_close_beyond_boundary(bar.close, zone, breakout_epsilon)
    if direction is not None:
        # FRESH/TESTED → BREAK_PENDING (C3, P7)
        prev_state_name = zone.state.name
        zone.state             = ZoneState.BREAK_PENDING
        zone.break_consecutive  = 1
        zone.inside_consecutive = 0
        zone.break_direction    = direction
        zone.break_window_bars  = 1
        logger.info(
            "Zone %s %s -> BREAK_PENDING dir=%s center=%s",
            zone.zone_id, prev_state_name, direction, zone.center,
        )
        return

    # Touch handling (enters zone without breaking)
    if _bar_enters_zone(bar, zone):
        if zone.state == ZoneState.FRESH:
            # FRESH → TESTED on first confirmed touch
            zone.state        = ZoneState.TESTED
            zone.touch_count += 1
            zone.last_touch_time = bar.timestamp_start
            zone.bars_since_touch = 0
            logger.debug("Zone %s FRESH→TESTED touch_count=%d", zone.zone_id, zone.touch_count)
        else:
            # TESTED/FLIPPED: additional touch (C4)
            zone.touch_count += 1
            zone.last_touch_time = bar.timestamp_start
            zone.bars_since_touch = 0
            zone.recompute_tier()
            logger.debug("Zone %s TESTED touch_count=%d", zone.zone_id, zone.touch_count)


def _step_break_pending(
    zone: "_ZoneMutable",
    bar: AggregatedBar,
    breakout_epsilon: Decimal,
) -> None:
    """C3 + P2: BREAK_PENDING → BROKEN_CONFIRMED or TESTED (failed break)."""
    zone.break_window_bars += 1

    direction = _is_close_beyond_boundary(bar.close, zone, breakout_epsilon)
    same_direction = (direction is not None and direction == zone.break_direction)

    if same_direction:
        zone.break_consecutive += 1
        zone.inside_consecutive = 0
        if zone.break_consecutive >= _BREAK_CONSEC_NEEDED:
            # BREAK_PENDING → BROKEN_CONFIRMED (C3, M4)
            zone.state                = ZoneState.BROKEN_CONFIRMED
            zone.confirmed_at         = bar.timestamp_start
            zone.bars_since_confirmed = 0
            logger.info(
                "Zone %s BREAK_PENDING -> BROKEN_CONFIRMED (dir=%s, %d consecutive closes) center=%s",
                zone.zone_id, zone.break_direction, zone.break_consecutive, zone.center,
            )
        return

    # Check if back inside zone (failed break C3)
    if _is_close_inside_zone(bar.close, zone):
        zone.inside_consecutive += 1
        zone.break_consecutive   = 0
        if (zone.inside_consecutive >= _FAILED_BREAK_CONSEC
                and zone.break_window_bars <= _FAILED_BREAK_WINDOW):
            # Failed breakout → revert to TESTED (C3)
            zone.state              = ZoneState.TESTED
            zone.touch_count       += 1
            zone.false_break_count += 1
            zone.break_consecutive  = 0
            zone.inside_consecutive = 0
            zone.break_window_bars  = 0
            zone.break_direction    = None
            zone.last_touch_time    = bar.timestamp_start
            zone.bars_since_touch   = 0
            zone.recompute_tier()
            logger.debug("Zone %s failed break → TESTED (false_break_count=%d)",
                         zone.zone_id, zone.false_break_count)
        return

    # Neither inside nor same direction: count as neutral bar within window
    # Reset inside streak but don't change break streak
    zone.inside_consecutive = 0

    # If window expired without BROKEN_CONFIRMED, also revert (C3: 4-bar window)
    if zone.break_window_bars >= _FAILED_BREAK_WINDOW and zone.break_consecutive < _BREAK_CONSEC_NEEDED:
        zone.state              = ZoneState.TESTED
        zone.touch_count       += 1
        zone.false_break_count += 1
        zone.break_consecutive  = 0
        zone.inside_consecutive = 0
        zone.break_window_bars  = 0
        zone.break_direction    = None
        zone.last_touch_time    = bar.timestamp_start
        zone.bars_since_touch   = 0
        zone.recompute_tier()
        logger.debug("Zone %s BREAK_PENDING window expired → TESTED", zone.zone_id)


def _transition_to_flipped(zone: "_ZoneMutable", bar: AggregatedBar) -> None:
    """C3 + P2 + P3: Transition BROKEN_CONFIRMED → FLIPPED.

    Inverts polarity. Freezes pre-flip touch bonus. Resets touch_count.
    Adds flip bonus of +2 (C4).
    """
    # P3: freeze pre-flip touch bonus
    zone.frozen_touch_bonus = min(zone.touch_count, _TOUCH_BONUS_CAP)

    # Capture old polarity for logging before inversion
    old_polarity_name = zone.polarity.name

    # Invert polarity
    zone.polarity = (
        ZonePolarity.RESISTANCE
        if zone.polarity == ZonePolarity.SUPPORT
        else ZonePolarity.SUPPORT
    )

    # Reset post-flip touch count (P3)
    zone.touch_count = 0

    # Apply FLIPPED base bonus +2 (C4)
    zone.base_strength += 2

    zone.state           = ZoneState.FLIPPED
    zone.last_touch_time = bar.timestamp_start
    zone.bars_since_touch = 0
    zone.recompute_tier()
    logger.info(
        "Zone %s BROKEN_CONFIRMED -> FLIPPED center=%s polarity=%s->%s frozen_bonus=%d",
        zone.zone_id, zone.center, old_polarity_name, zone.polarity.name, zone.frozen_touch_bonus,
    )


def _apply_decay_and_freeze(
    zone: "_ZoneMutable",
    current_price: Decimal,
    native_atr: Decimal,
) -> None:
    """Doc 4 §12: Strength decay every 50 TF bars without touch.

    FROZEN at 100 bars; EXPIRED at 200 bars or price > 5×ATR away (P11).
    """
    if zone.state in (ZoneState.FROZEN, ZoneState.EXPIRED, ZoneState.CANDIDATE):
        return
    if zone.bars_since_touch > 0 and zone.bars_since_touch % _DECAY_BARS == 0:
        zone.base_strength = max(0, zone.base_strength - 1)
        zone.recompute_tier()
        logger.debug("Zone %s strength decay → %d", zone.zone_id, zone.total_strength())

    _check_freeze_and_expire(zone, current_price, native_atr)


def _check_freeze_and_expire(
    zone: "_ZoneMutable",
    current_price: Decimal,
    native_atr: Decimal,
) -> None:
    """Check and apply FROZEN / EXPIRED transitions (P11)."""
    if zone.state in (ZoneState.FROZEN, ZoneState.EXPIRED, ZoneState.CANDIDATE):
        return
    if zone.bars_since_touch >= _FROZEN_BARS:
        zone.state = ZoneState.FROZEN
        logger.debug("Zone %s → FROZEN (bars_since_touch=%d)", zone.zone_id, zone.bars_since_touch)
    _check_expire(zone, current_price, native_atr)


def _check_expire(
    zone: "_ZoneMutable",
    current_price: Decimal,
    native_atr: Decimal,
) -> None:
    """P11: FROZEN → EXPIRED at 200 bars or price > 5×ATR from center."""
    if zone.state == ZoneState.EXPIRED:
        return
    price_dist = abs(current_price - zone.center)
    far_away = native_atr > _EPSILON and price_dist > _EXPIRED_ATR_DIST * native_atr
    if zone.bars_since_touch >= _EXPIRED_BARS or far_away:
        zone.state = ZoneState.EXPIRED
        logger.debug("Zone %s → EXPIRED (bars=%d far=%s)", zone.zone_id, zone.bars_since_touch, far_away)


# ---------------------------------------------------------------------------
# Density engine (Addendum B §B3.8)
# ---------------------------------------------------------------------------

def _compute_density_bias(
    zones: list["_ZoneMutable"],
    current_price: Decimal,
    atr_4h: Decimal,
) -> DensityBias:
    """Addendum B §B3.8: Density bias from weighted strength of B-TIER+ zones.

    scan_range = 3.0 × ATR(4H, 14).
    B-TIER+ only; weighted by total strength.
    """
    if atr_4h <= _EPSILON:
        return DensityBias.NEUTRAL

    scan_range = _DENSITY_SCAN_ATR_MULT * atr_4h
    tradable_tiers = {Tier.S, Tier.A, Tier.B}
    active_states  = {ZoneState.FRESH, ZoneState.TESTED, ZoneState.FLIPPED, ZoneState.BREAK_PENDING}

    density_above = Decimal("0")
    density_below = Decimal("0")

    for z in zones:
        if z.tier not in tradable_tiers:
            continue
        if z.state not in active_states:
            continue
        strength = Decimal(z.total_strength())
        # Zone above current price: zone_low > current_price and within scan range
        if z.zone_low > current_price - _EPSILON and z.zone_low <= current_price + scan_range + _EPSILON:
            density_above += strength
        # Zone below current price: zone_high < current_price and within scan range
        elif z.zone_high < current_price + _EPSILON and z.zone_high >= current_price - scan_range - _EPSILON:
            density_below += strength

    if density_above == _D_ZERO and density_below == _D_ZERO:
        return DensityBias.NEUTRAL

    denom = density_below if density_below > _EPSILON else _D_ONE
    ratio = density_above / denom

    if ratio > _DENSITY_HEAVY_ABOVE:
        return DensityBias.HEAVY_ABOVE
    if ratio > _DENSITY_MODERATE_ABOVE:
        return DensityBias.MODERATE_ABOVE
    if ratio < _DENSITY_HEAVY_BELOW:
        return DensityBias.HEAVY_BELOW
    if ratio < _DENSITY_MODERATE_BELOW:
        return DensityBias.MODERATE_BELOW
    return DensityBias.NEUTRAL


# ---------------------------------------------------------------------------
# ZoneDetector — main public class
# ---------------------------------------------------------------------------

class ZoneDetector:
    """Doc 4 + Amendment v1.1/v1.2: Streaming SR zone detector.

    One instance per (instrument, timeframe-pair).

    Usage::

        detector = ZoneDetector(
            symbol="BTCUSDT",
            sr_timeframe="1H",
            atr_1h=atr_1h_calc,
            atr_15m=atr_15m_calc,
            atr_4h=atr_4h_calc,
        )
        # On each completed 15m bar:
        state_15m, legs = zlbb_engine.push(bar_15m)
        for leg in legs:
            detector.update(bar_15m, completed_leg=leg)
        # Update without new leg (lifecycle only):
        detector.update(bar_15m, completed_leg=None)

        zones = detector.get_active_zones(min_tier="B")
        bias  = detector.density_bias

    All arithmetic uses Decimal.  No float anywhere.
    """

    def __init__(
        self,
        symbol:        str,
        sr_timeframe:  str,             # e.g. "1H"
        atr_1h:        ATRCalculator,   # for clustering threshold (§6)
        atr_15m:       ATRCalculator,   # for zone width cap (M5/§7) + breakout epsilon (P7)
        atr_4h:        ATRCalculator,   # for density scan range (B3.8)
        native_atr:    Optional[ATRCalculator] = None,  # native SR TF ATR (defaults to atr_1h)
        recent_bars:   Optional[list[AggregatedBar]] = None,  # for open/close + rejection detection
    ) -> None:
        """Initialise detector.

        Args:
            symbol:       Instrument symbol.
            sr_timeframe: SR generation timeframe label (e.g. "1H").
            atr_1h:       ATRCalculator for 1H — used for clustering threshold.
            atr_15m:      ATRCalculator for 15m — used for zone width cap + epsilon.
            atr_4h:       ATRCalculator for 4H  — used for density scan range.
            native_atr:   ATRCalculator matching sr_timeframe. Defaults to atr_1h.
            recent_bars:  Recent bars for open/close cluster and rejection detection.
        """
        self._symbol       = symbol
        self._sr_tf        = sr_timeframe
        self._atr_1h       = atr_1h
        self._atr_15m      = atr_15m
        self._atr_4h       = atr_4h
        self._native_atr   = native_atr or atr_1h
        self._recent_bars: list[AggregatedBar] = list(recent_bars or [])

        # Active zones (mutable state containers)
        self._zones: list[_ZoneMutable] = []

        # Density bias — updated on each update()
        self._density_bias: DensityBias = DensityBias.NEUTRAL

        # Bar counter for bookkeeping
        self._bar_count: int = 0

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def density_bias(self) -> DensityBias:
        """Addendum B §B3.8: Current directional density signal."""
        return self._density_bias

    def update(
        self,
        bar: AggregatedBar,
        completed_leg: Optional[CompletedLeg] = None,
    ) -> None:
        """Process one completed 15m bar. Optionally registers a new completed leg.

        Steps:
          1. Fetch current ATR values.
          2. Register new zone from completed_leg (if any) — §4.1.
          3. Register zones from open/close clusters on new bar — §4.2.
          4. Register zones from rejection bars — §4.3.
          5. Run clustering pass (§6) + min-gap (C7) + zone width (M5/§7).
          6. Step lifecycle FSM for all zones (P11).
          7. Remove EXPIRED zones.
          8. Recompute density bias (B3.8).
        """
        if not bar.is_complete:
            return

        self._bar_count += 1
        self._recent_bars.append(bar)
        # Keep a rolling window (200 bars) for rejection/OC detection
        if len(self._recent_bars) > 200:
            self._recent_bars = self._recent_bars[-200:]

        atr_1h  = self._atr_1h.current_atr
        atr_15m = self._atr_15m.current_atr
        atr_4h  = self._atr_4h.current_atr
        nat_atr = self._native_atr.current_atr

        # Cannot process without ATR
        if atr_15m is None or atr_1h is None:
            return

        breakout_epsilon = _BREAKOUT_EPSILON_MULT * atr_15m  # P7

        current_price = bar.close

        # 1. Register new SR from completed leg (§4.1)
        if completed_leg is not None:
            self._register_leg_extreme(completed_leg, bar.timestamp_start, atr_15m)

        # 2. Open/close clusters (§4.2)
        if len(self._recent_bars) >= _CLUSTER_OC_MIN_CANDLES and nat_atr is not None:
            oc_candidates = _detect_open_close_cluster(
                self._recent_bars, nat_atr, self._sr_tf
            )
            for price, polarity in oc_candidates:
                self._register_oc_zone(price, polarity, bar.timestamp_start, nat_atr or atr_1h)

        # 3. Rejection bars (§4.3)
        if len(self._recent_bars) >= 4 and nat_atr is not None:
            rej_candidates = _detect_rejection_bars(self._recent_bars, nat_atr)
            for price, polarity in rej_candidates:
                self._register_rejection_zone(price, polarity, bar.timestamp_start, nat_atr or atr_1h)

        # 4. Re-cluster + min-gap + zone width (only when ATR is ready)
        if nat_atr is not None:
            self._rebuild_zones(current_price, atr_1h, atr_15m, nat_atr)

        # 5. Step lifecycle FSM for every active zone
        native_atr_val = nat_atr or atr_1h
        for z in self._zones:
            _step_lifecycle(z, bar, breakout_epsilon, z.bars_since_touch,
                            current_price, native_atr_val)

        # 6. Remove EXPIRED zones
        self._zones = [z for z in self._zones if z.state != ZoneState.EXPIRED]

        # 7. Density bias
        atr_4h_val = atr_4h or atr_1h
        self._density_bias = _compute_density_bias(self._zones, current_price, atr_4h_val)

    def get_active_zones(
        self,
        sr_type: Optional[ZonePolarity] = None,
        min_tier: str = "C",
    ) -> list[SRZone]:
        """Return immutable snapshots of active zones sorted by strength descending.

        Args:
            sr_type:  Filter by polarity (SUPPORT / RESISTANCE). None = all.
            min_tier: Minimum tier. "S", "A", "B", or "C".
        """
        tier_order = {"S": 0, "A": 1, "B": 2, "C": 3}
        min_tier_val = tier_order.get(min_tier.upper(), 3)
        active_states = {
            ZoneState.FRESH, ZoneState.TESTED, ZoneState.BREAK_PENDING,
            ZoneState.BROKEN_CONFIRMED, ZoneState.FLIPPED,
        }
        result: list[SRZone] = []
        for z in self._zones:
            if z.state not in active_states:
                continue
            if sr_type is not None and z.polarity != sr_type:
                continue
            tier_rank = tier_order.get(z.tier.name, 3)
            if tier_rank > min_tier_val:
                continue
            result.append(SRZone(
                zone_id=z.zone_id,
                center=z.center,
                zone_high=z.zone_high,
                zone_low=z.zone_low,
                polarity=z.polarity,
                origin=z.origin,
                timeframe=z.timeframe,
                is_midpoint=z.is_midpoint,
                state=z.state,
                tier=z.tier,
                strength=z.total_strength(),
                touch_count=z.touch_count,
                false_break_count=z.false_break_count,
                created_at=z.created_at,
                last_touch_time=z.last_touch_time,
            ))
        result.sort(key=lambda z: z.strength, reverse=True)
        return result

    def add_retro_zone(
        self,
        center: Decimal,
        polarity: ZonePolarity,
        displacement: Decimal,
        quality: int,
        created_at: datetime.datetime,
    ) -> None:
        """C4 Part 1: Register a retrospective leg zone as TESTED (touch_count=1).

        Used when rebuilding from history.
        """
        nat_atr = self._native_atr.current_atr or self._atr_1h.current_atr
        if nat_atr is None:
            return
        zone = self._make_leg_zone(
            center=center,
            polarity=polarity,
            displacement=displacement,
            quality=quality,
            created_at=created_at,
        )
        zone.state        = ZoneState.TESTED   # C4: retro → TESTED
        zone.touch_count  = 1                  # C4: start with one touch
        zone.recompute_tier()
        self._zones.append(zone)

    # ------------------------------------------------------------------
    # Internal zone registration helpers
    # ------------------------------------------------------------------

    def _register_leg_extreme(
        self,
        leg: CompletedLeg,
        timestamp: datetime.datetime,
        atr_15m: Decimal,
    ) -> None:
        """Doc 4 §4.1: Register SR zone from completed leg extreme.

        Bull leg → high → resistance candidate.
        Bear leg → low  → support candidate.
        Base strength = 2 + leg_quality (§4.1).
        """
        # Midpoint candidate (§4.4): only if displacement ≥ 2 × ATR
        midpoint_price: Optional[Decimal] = None
        if leg.displacement >= _MIDPOINT_DISP_MIN_MULT * leg.atr_at_start - _EPSILON:
            midpoint_price = (leg.start_price + leg.end_price) / 2

        if leg.direction == LegDirection.BULL:
            center  = leg.end_price   # high of bull leg
            polarity = ZonePolarity.RESISTANCE
        else:
            center  = leg.end_price   # low of bear leg
            polarity = ZonePolarity.SUPPORT

        zone = self._make_leg_zone(
            center=center,
            polarity=polarity,
            displacement=leg.displacement,
            quality=leg.quality,
            created_at=timestamp,
        )
        self._zones.append(zone)

        # Midpoint as CANDIDATE (§4.4, C7)
        if midpoint_price is not None:
            mid_polarity = (ZonePolarity.SUPPORT if polarity == ZonePolarity.RESISTANCE
                            else ZonePolarity.RESISTANCE)
            mid_zone = _ZoneMutable(
                zone_id=_make_zone_id(midpoint_price, mid_polarity, self._sr_tf, timestamp),
                center=midpoint_price,
                zone_high=midpoint_price,   # width computed later
                zone_low=midpoint_price,
                polarity=mid_polarity,
                origin=ZoneOrigin.MIDPOINT,
                timeframe=self._sr_tf,
                is_midpoint=True,
                base_strength=2,
                source_bonus=int(_MIDPOINT_BONUS),
                displacement=leg.displacement,
                state=ZoneState.CANDIDATE,
                created_at=timestamp,
            )
            mid_zone.recompute_tier()
            self._zones.append(mid_zone)

    def _make_leg_zone(
        self,
        center: Decimal,
        polarity: ZonePolarity,
        displacement: Decimal,
        quality: int,
        created_at: datetime.datetime,
    ) -> "_ZoneMutable":
        """Create a raw leg-extreme zone (width = 0 until rebuild_zones computes it)."""
        zone = _ZoneMutable(
            zone_id=_make_zone_id(center, polarity, self._sr_tf, created_at),
            center=center,
            zone_high=center,
            zone_low=center,
            polarity=polarity,
            origin=ZoneOrigin.LEG_EXTREME,
            timeframe=self._sr_tf,
            is_midpoint=False,
            base_strength=2 + quality,
            source_bonus=0,
            displacement=displacement,
            state=ZoneState.FRESH,
            created_at=created_at,
        )
        zone.recompute_tier()
        return zone

    def _register_oc_zone(
        self,
        center: Decimal,
        polarity: ZonePolarity,
        timestamp: datetime.datetime,
        native_atr: Decimal,
    ) -> None:
        """Doc 4 §4.2: Register open/close cluster zone (+2 strength).

        Fix A: source_bonus capped at _MAX_SOURCE_BONUS_TOTAL (6) to prevent
        unbounded accumulation from repeated cluster detections on each bar.
        """
        # Check if a zone already exists nearby (within 0.15×ATR)
        proximity = _CLUSTER_OC_ATR_MULT * native_atr
        for z in self._zones:
            if abs(z.center - center) <= proximity + _EPSILON and z.polarity == polarity:
                if z.source_bonus < _MAX_SOURCE_BONUS_TOTAL:
                    z.source_bonus = min(
                        z.source_bonus + int(_OPEN_CLOSE_BONUS),
                        _MAX_SOURCE_BONUS_TOTAL,
                    )
                    z.recompute_tier()
                return  # Augment existing zone (or skip if already capped)

        zone = _ZoneMutable(
            zone_id=_make_zone_id(center, polarity, self._sr_tf, timestamp),
            center=center,
            zone_high=center,
            zone_low=center,
            polarity=polarity,
            origin=ZoneOrigin.OPEN_CLOSE,
            timeframe=self._sr_tf,
            is_midpoint=False,
            base_strength=2,
            source_bonus=int(_OPEN_CLOSE_BONUS),
            displacement=_D_ZERO,
            state=ZoneState.FRESH,
            created_at=timestamp,
        )
        zone.recompute_tier()
        self._zones.append(zone)

    def _register_rejection_zone(
        self,
        center: Decimal,
        polarity: ZonePolarity,
        timestamp: datetime.datetime,
        native_atr: Decimal,
    ) -> None:
        """Doc 4 §4.3: Register rejection bar zone (+2 strength).

        Fix A: source_bonus capped at _MAX_SOURCE_BONUS_TOTAL (6).
        """
        proximity = _CLUSTER_OC_ATR_MULT * native_atr
        for z in self._zones:
            if abs(z.center - center) <= proximity + _EPSILON and z.polarity == polarity:
                if z.source_bonus < _MAX_SOURCE_BONUS_TOTAL:
                    z.source_bonus = min(
                        z.source_bonus + int(_REJECTION_BONUS),
                        _MAX_SOURCE_BONUS_TOTAL,
                    )
                    z.recompute_tier()
                return  # Augment (or skip if already capped)

        zone = _ZoneMutable(
            zone_id=_make_zone_id(center, polarity, self._sr_tf, timestamp),
            center=center,
            zone_high=center,
            zone_low=center,
            polarity=polarity,
            origin=ZoneOrigin.REJECTION_BAR,
            timeframe=self._sr_tf,
            is_midpoint=False,
            base_strength=2,
            source_bonus=int(_REJECTION_BONUS),
            displacement=_D_ZERO,
            state=ZoneState.FRESH,
            created_at=timestamp,
        )
        zone.recompute_tier()
        self._zones.append(zone)

    # ------------------------------------------------------------------
    # Rebuild: clustering + min-gap + zone width
    # ------------------------------------------------------------------

    def _rebuild_zones(
        self,
        current_price: Decimal,
        atr_1h: Decimal,
        atr_15m: Decimal,
        nat_atr: Decimal,
    ) -> None:
        """Doc 4 §6 + C7 + M5/§7: Cluster → min-gap → compute zone widths.

        Operates separately on SUPPORT and RESISTANCE zones to avoid
        cross-polarity merging.  CANDIDATE zones are not clustered
        (they remain isolated until validated).
        """
        # Separate zones by polarity, keeping CANDIDATE zones aside
        candidates = [z for z in self._zones if z.state == ZoneState.CANDIDATE]
        actives    = [z for z in self._zones if z.state != ZoneState.CANDIDATE]

        # Split actives by polarity
        supports    = [z for z in actives if z.polarity == ZonePolarity.SUPPORT]
        resistances = [z for z in actives if z.polarity == ZonePolarity.RESISTANCE]

        merge_thresh = _CLUSTER_THRESH_MULT * atr_1h  # Doc 4 §6: 0.5 × ATR(1H)

        supports    = _cluster_merge(supports, merge_thresh)
        resistances = _cluster_merge(resistances, merge_thresh)

        # Min-gap pass (C7): separate passes per polarity
        supports    = _enforce_min_gap(supports, current_price, nat_atr)
        resistances = _enforce_min_gap(resistances, current_price, nat_atr)

        # Zone width (M5/§7): per-polarity sorted list
        all_active = sorted(supports + resistances, key=lambda z: z.center)
        _compute_zone_widths(all_active, atr_15m)

        # Candidate zones get default width from ATR
        for z in candidates:
            hw = _WIDTH_ATR_MULT * atr_15m
            z.zone_high = z.center + hw
            z.zone_low  = z.center - hw

        self._zones = all_active + candidates
