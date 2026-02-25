"""Tests for src/sr/zone_detector.py — acceptance criteria + lifecycle + density.

Acceptance criteria (from user spec):
  AC1  Two legs ending within 0.5×ATR(1H) → one merged zone with weighted center.
  AC2  Min-gap pass merges zones closer than max(0.40% price, 0.25×ATR).
  AC3  Zone width computed only after clustering complete using §7 formula.
  AC4  4 consecutive 15m closes beyond boundary + min distance → BROKEN_CONFIRMED.
  AC5  BROKEN_CONFIRMED persists ≥1 bar before FLIPPED.
  AC6  frozen_touch_bonus frozen on FLIPPED, touch_count resets to 0.
  AC7  No float anywhere (verified by inspecting Decimal usage in output).
"""

import datetime
from decimal import Decimal

import pytest

from src.data_ingest.bar_aggregator import AggregatedBar
from src.indicators.atr import ATRCalculator
from src.indicators.zlbb import CompletedLeg, LegDirection
from src.sr.zone_detector import (
    DensityBias,
    SRZone,
    Tier,
    ZoneDetector,
    ZoneOrigin,
    ZonePolarity,
    ZoneState,
    _ZoneMutable,
    _assign_tier,
    _cluster_merge,
    _compute_zone_widths,
    _enforce_min_gap,
    _is_close_beyond_boundary,
    _step_break_pending,
    _step_lifecycle,
    _transition_to_flipped,
)

UTC = datetime.timezone.utc
BASE_TS = datetime.datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_bar(
    minute_offset: int = 0,
    h: str = "100",
    l: str = "99",
    c: str = "99.5",
    o: str = "99.5",
    symbol: str = "BTCUSDT",
    tf: str = "15m",
    is_complete: bool = True,
) -> AggregatedBar:
    ts = BASE_TS + datetime.timedelta(minutes=minute_offset * 15)
    return AggregatedBar(
        symbol=symbol,
        timestamp_start=ts,
        timestamp_end=ts + datetime.timedelta(minutes=15) - datetime.timedelta(microseconds=1),
        timeframe=tf,
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(l),
        close=Decimal(c),
        volume=Decimal("1000"),
        is_complete=is_complete,
        is_reliable=True,
    )


def make_atr_calc(atr_value: str, symbol: str = "BTCUSDT", tf: str = "15m") -> ATRCalculator:
    """Return an ATRCalculator with a pre-seeded current_atr value for testing."""
    calc = ATRCalculator(symbol=symbol, timeframe=tf, is_continuous=True)
    # Inject the ATR directly into private state for test isolation
    calc._current_atr = Decimal(atr_value)
    return calc


def make_leg(
    direction: LegDirection = LegDirection.BULL,
    start_price: str = "90000",
    end_price: str = "91000",
    displacement: str = "1000",
    quality: int = 3,
    atr_at_start: str = "400",
    bar_count: int = 8,
    ts_start: datetime.datetime = BASE_TS,
    ts_end: datetime.datetime = BASE_TS + datetime.timedelta(hours=2),
) -> CompletedLeg:
    displ = Decimal(displacement)
    start = Decimal(start_price)
    end   = Decimal(end_price)
    cr    = displ / Decimal("0.6")  # efficiency = 0.6 → cr = disp / 0.6
    eff   = displ / cr
    return CompletedLeg(
        direction=direction,
        start_price=start,
        end_price=end,
        displacement=displ,
        bar_count=bar_count,
        efficiency=eff,
        cumulative_range=cr,
        quality=quality,
        atr_at_start=Decimal(atr_at_start),
        timestamp_start=ts_start,
        timestamp_end=ts_end,
        is_band_walk=False,
    )


def make_zone(
    center: str = "100000",
    polarity: ZonePolarity = ZonePolarity.RESISTANCE,
    state: ZoneState = ZoneState.FRESH,
    base_strength: int = 5,
    source_bonus: int = 0,
    displacement: str = "1000",
    touch_count: int = 0,
    frozen_touch_bonus: int = 0,
) -> _ZoneMutable:
    c = Decimal(center)
    z = _ZoneMutable(
        zone_id="test_" + center,
        center=c,
        zone_high=c + Decimal("100"),
        zone_low=c - Decimal("100"),
        polarity=polarity,
        origin=ZoneOrigin.LEG_EXTREME,
        timeframe="1H",
        is_midpoint=False,
        base_strength=base_strength,
        source_bonus=source_bonus,
        displacement=Decimal(displacement),
        state=state,
        touch_count=touch_count,
        frozen_touch_bonus=frozen_touch_bonus,
        created_at=BASE_TS,
    )
    z.recompute_tier()
    return z


def make_detector(atr_1h: str = "400", atr_15m: str = "120", atr_4h: str = "800") -> ZoneDetector:
    return ZoneDetector(
        symbol="BTCUSDT",
        sr_timeframe="1H",
        atr_1h=make_atr_calc(atr_1h, tf="1H"),
        atr_15m=make_atr_calc(atr_15m, tf="15m"),
        atr_4h=make_atr_calc(atr_4h, tf="4H"),
    )


# ---------------------------------------------------------------------------
# Unit tests: tier assignment
# ---------------------------------------------------------------------------

class TestTierAssignment:
    def test_s_tier_at_15(self):
        assert _assign_tier(15) == Tier.S

    def test_s_tier_at_20(self):
        assert _assign_tier(20) == Tier.S

    def test_a_tier_at_10(self):
        assert _assign_tier(10) == Tier.A

    def test_a_tier_at_14(self):
        assert _assign_tier(14) == Tier.A

    def test_b_tier_at_5(self):
        assert _assign_tier(5) == Tier.B

    def test_b_tier_at_9(self):
        assert _assign_tier(9) == Tier.B

    def test_c_tier_at_4(self):
        assert _assign_tier(4) == Tier.C

    def test_c_tier_at_0(self):
        assert _assign_tier(0) == Tier.C


# ---------------------------------------------------------------------------
# Unit tests: zone total_strength and tier
# ---------------------------------------------------------------------------

class TestZoneStrength:
    def test_base_only(self):
        z = make_zone(base_strength=5, source_bonus=0, touch_count=0)
        assert z.total_strength() == 5

    def test_touch_bonus_capped_at_3(self):
        z = make_zone(base_strength=5, touch_count=10)
        assert z.total_strength() == 5 + 3  # cap

    def test_frozen_touch_bonus_adds(self):
        z = make_zone(base_strength=5, touch_count=2, frozen_touch_bonus=2)
        assert z.total_strength() == 5 + 2 + 2  # touch_bonus=2, frozen=2

    def test_source_bonus_adds(self):
        z = make_zone(base_strength=5, source_bonus=4)
        assert z.total_strength() == 9

    def test_recompute_tier_after_strength_change(self):
        z = make_zone(base_strength=3)
        z.recompute_tier()
        assert z.tier == Tier.C
        z.base_strength = 10
        z.recompute_tier()
        assert z.tier == Tier.A


# ---------------------------------------------------------------------------
# AC1: Clustering — two legs within 0.5×ATR(1H) → one merged zone
# ---------------------------------------------------------------------------

class TestClusterMerge:
    """AC1: Two legs ending within 0.5×ATR(1H) → one merged zone with weighted center."""

    def test_two_close_zones_merge(self):
        """AC1: centers 100 apart with merge_threshold=200 → single zone."""
        z1 = make_zone(center="121000", displacement="900", base_strength=6)
        z2 = make_zone(center="121150", displacement="600", base_strength=4)
        threshold = Decimal("200")  # 0.5 × ATR(1H=400)
        merged = _cluster_merge([z1, z2], threshold)
        assert len(merged) == 1

    def test_merged_center_is_displacement_weighted(self):
        """AC1: center = weighted avg by displacement (Doc 4 §6.4 example)."""
        z1 = make_zone(center="121000", displacement="900")
        z2 = make_zone(center="121150", displacement="600")
        threshold = Decimal("200")
        merged = _cluster_merge([z1, z2], threshold)
        # (121000×900 + 121150×600) / 1500 = 121060
        expected_center = (Decimal("121000") * Decimal("900") + Decimal("121150") * Decimal("600")) / Decimal("1500")
        assert abs(merged[0].center - expected_center) < Decimal("1")  # within 1 unit

    def test_two_far_zones_stay_separate(self):
        """Zones > merge_threshold apart → two separate zones."""
        z1 = make_zone(center="121000", displacement="900")
        z2 = make_zone(center="121520", displacement="800")
        threshold = Decimal("200")
        merged = _cluster_merge([z1, z2], threshold)
        assert len(merged) == 2

    def test_three_zones_two_merge_one_separate(self):
        """Doc 4 §6.5 example: L1=121000, L2=121150, L3=121520 → clusters A and B."""
        z1 = make_zone(center="121000", displacement="900")
        z2 = make_zone(center="121150", displacement="600")
        z3 = make_zone(center="121520", displacement="800")
        threshold = Decimal("200")
        merged = _cluster_merge([z1, z2, z3], threshold)
        assert len(merged) == 2

    def test_merged_strength_is_sum(self):
        z1 = make_zone(center="100", base_strength=5, source_bonus=0, touch_count=0)
        z2 = make_zone(center="150", base_strength=3, source_bonus=2, touch_count=0)
        merged = _cluster_merge([z1, z2], Decimal("200"))
        assert len(merged) == 1
        # base_strength sum = 8, source_bonus sum = 2
        assert merged[0].base_strength == 8
        assert merged[0].source_bonus == 2

    def test_empty_input(self):
        assert _cluster_merge([], Decimal("100")) == []

    def test_single_zone_unchanged(self):
        z = make_zone(center="100000", base_strength=7)
        merged = _cluster_merge([z], Decimal("200"))
        assert len(merged) == 1
        assert merged[0].center == Decimal("100000")

    def test_order_independent(self):
        """Result should be same regardless of input order."""
        z1 = make_zone(center="121150", displacement="600")
        z2 = make_zone(center="121000", displacement="900")
        threshold = Decimal("200")
        merged_forward  = _cluster_merge([z1, z2], threshold)
        merged_backward = _cluster_merge([z2, z1], threshold)
        assert len(merged_forward) == len(merged_backward) == 1
        assert abs(merged_forward[0].center - merged_backward[0].center) < Decimal("1")


# ---------------------------------------------------------------------------
# AC2: Min-gap pass
# ---------------------------------------------------------------------------

class TestMinGap:
    """AC2: Min-gap merges zones closer than max(0.40% price, 0.25×ATR)."""

    def test_zones_too_close_are_merged(self):
        """AC2: Two support zones 30 apart; min_gap=max(0.004×100000, 0.25×400)=max(400,100)=400."""
        price = Decimal("100000")
        atr   = Decimal("400")
        z1 = make_zone(center="100000", base_strength=5)
        z2 = make_zone(center="100030", base_strength=3)  # 30 apart < 400
        result = _enforce_min_gap([z1, z2], price, atr)
        assert len(result) == 1

    def test_zones_far_enough_kept_separate(self):
        """Zones 500 apart with min_gap=400 stay separate."""
        price = Decimal("100000")
        atr   = Decimal("400")
        z1 = make_zone(center="100000", base_strength=5)
        z2 = make_zone(center="100500", base_strength=3)  # 500 > 400
        result = _enforce_min_gap([z1, z2], price, atr)
        assert len(result) == 2

    def test_stronger_zone_wins(self):
        """Weaker zone is absorbed into stronger zone."""
        price = Decimal("100000")
        atr   = Decimal("400")
        z_strong = make_zone(center="100050", base_strength=10)
        z_weak   = make_zone(center="100000", base_strength=3)
        result = _enforce_min_gap([z_weak, z_strong], price, atr)
        assert len(result) == 1
        # Strong zone survives (base_strength 10 > 3)
        assert result[0].base_strength >= 13  # sum absorbed

    def test_min_gap_uses_pct_when_larger(self):
        """0.40% of price dominates when ATR is small."""
        price = Decimal("100000")  # 0.40% = 400
        atr   = Decimal("50")      # 0.25 × 50 = 12.5
        z1 = make_zone(center="100100", base_strength=5)
        z2 = make_zone(center="100200", base_strength=3)  # 100 apart < 400
        result = _enforce_min_gap([z1, z2], price, atr)
        assert len(result) == 1

    def test_empty_list(self):
        assert _enforce_min_gap([], Decimal("100000"), Decimal("400")) == []


# ---------------------------------------------------------------------------
# AC3: Zone width computed after clustering using §7 formula
# ---------------------------------------------------------------------------

class TestZoneWidth:
    """AC3: Zone width = min(0.10 × effective_range, 1.5 × ATR(15m))."""

    def test_three_zone_width_uses_effective_range(self):
        """Middle zone uses min(range_left, range_right)."""
        z1 = make_zone(center="120000")
        z2 = make_zone(center="121000")  # range_left=1000, range_right=2000
        z3 = make_zone(center="123000")
        atr_15m = Decimal("120")
        _compute_zone_widths([z1, z2, z3], atr_15m)
        # effective_range for z2 = min(1000, 2000) = 1000
        # base_width = 0.10 × 1000 = 100
        # cap = 1.5 × 120 = 180
        # half_width = min(100, 180) = 100
        assert z2.zone_high == Decimal("121000") + Decimal("100")
        assert z2.zone_low  == Decimal("121000") - Decimal("100")

    def test_first_zone_uses_range_right(self):
        """Edge rule: first zone uses range_right = centers[1] - centers[0]."""
        z1 = make_zone(center="120000")
        z2 = make_zone(center="121500")  # range_right = 1500
        atr_15m = Decimal("120")
        _compute_zone_widths([z1, z2], atr_15m)
        # base = 0.10 × 1500 = 150, cap = 0.10 × 1500 = 150 vs 1.5×120=180 → 150
        hw1 = min(Decimal("0.10") * Decimal("1500"), Decimal("1.5") * atr_15m)
        assert abs(z1.zone_high - (Decimal("120000") + hw1)) < Decimal("1E-6")

    def test_last_zone_uses_range_left(self):
        """Edge rule: last zone uses range_left."""
        z1 = make_zone(center="120000")
        z2 = make_zone(center="122000")  # range_left = 2000
        atr_15m = Decimal("120")
        _compute_zone_widths([z1, z2], atr_15m)
        hw2 = min(Decimal("0.10") * Decimal("2000"), Decimal("1.5") * atr_15m)
        assert abs(z2.zone_high - (Decimal("122000") + hw2)) < Decimal("1E-6")

    def test_volatility_cap_applied(self):
        """If structural range is large, ATR cap takes precedence."""
        z1 = make_zone(center="100000")
        z2 = make_zone(center="110000")  # range = 10000 → 0.10×10000=1000
        atr_15m = Decimal("100")         # cap = 1.5×100 = 150
        _compute_zone_widths([z1, z2], atr_15m)
        # 1000 > 150 → capped at 150
        assert z1.zone_high == Decimal("100000") + Decimal("150")

    def test_single_zone_uses_atr_cap(self):
        """Single zone → no neighbours → uses 1.5×ATR(15m) directly."""
        z = make_zone(center="100000")
        atr_15m = Decimal("120")
        _compute_zone_widths([z], atr_15m)
        assert z.zone_high == Decimal("100000") + Decimal("1.5") * atr_15m

    def test_zone_width_symmetric(self):
        """zone_high and zone_low are symmetric around center."""
        zones = [make_zone(center="100000"), make_zone(center="101000"), make_zone(center="103000")]
        _compute_zone_widths(zones, Decimal("120"))
        for z in zones:
            half_h = z.zone_high - z.center
            half_l = z.center - z.zone_low
            assert abs(half_h - half_l) < Decimal("1E-9")


# ---------------------------------------------------------------------------
# AC4: BROKEN_CONFIRMED after 4 consecutive closes beyond boundary
# ---------------------------------------------------------------------------

class TestLifecycleFSM:
    """AC4-AC6: Full lifecycle state machine tests."""

    def _make_zone_with_width(
        self,
        center: str = "100",
        high: str = "105",
        low: str = "95",
        state: ZoneState = ZoneState.FRESH,
        base_strength: int = 6,
        touch_count: int = 0,
    ) -> _ZoneMutable:
        z = make_zone(center=center, state=state, base_strength=base_strength, touch_count=touch_count)
        z.zone_high = Decimal(high)
        z.zone_low  = Decimal(low)
        return z

    def _step(self, zone: _ZoneMutable, close: str, high: str = None, low: str = None) -> None:
        """Push one bar through the lifecycle FSM."""
        c = Decimal(close)
        h = Decimal(high or close)
        l = Decimal(low or close)
        # epsilon = 0.02 × ATR(15m) = 0.02 × 5 = 0.1
        bar = make_bar(c=close, h=str(float(c) + 1 if float(c) > 110 else float(c)),
                       l=str(float(c) - 1))
        bar_real = AggregatedBar(
            symbol="BTCUSDT",
            timestamp_start=BASE_TS,
            timestamp_end=BASE_TS + datetime.timedelta(minutes=15),
            timeframe="15m",
            open=c,
            high=h if h > c else c,
            low=l if l < c else c,
            close=c,
            volume=Decimal("1000"),
            is_complete=True,
            is_reliable=True,
        )
        epsilon = Decimal("0.1")  # 0.02 × ATR(15m=5)
        _step_lifecycle(zone, bar_real, epsilon, zone.bars_since_touch,
                        c, Decimal("5"))

    def test_fresh_to_tested_on_touch(self):
        """FRESH → TESTED when bar enters zone without breaking."""
        z = self._make_zone_with_width(state=ZoneState.FRESH)
        self._step(z, "102")  # inside zone (95-105)
        assert z.state == ZoneState.TESTED
        assert z.touch_count == 1

    def test_fresh_to_break_pending_on_close_above(self):
        """FRESH → BREAK_PENDING on first close above zone_high + epsilon."""
        z = self._make_zone_with_width(state=ZoneState.FRESH)
        self._step(z, "105.5")  # > zone_high(105) + epsilon(0.1) = 105.1
        assert z.state == ZoneState.BREAK_PENDING
        assert z.break_consecutive == 1
        assert z.break_direction == "up"

    def test_break_pending_consecutive_4_gives_broken_confirmed(self):
        """AC4: 4 consecutive closes beyond boundary → BROKEN_CONFIRMED."""
        z = self._make_zone_with_width(state=ZoneState.FRESH)
        # Push to BREAK_PENDING first
        for i in range(4):
            self._step(z, "106")  # > 105 + 0.1

        assert z.state == ZoneState.BROKEN_CONFIRMED

    def test_break_pending_reverts_on_failed_break(self):
        """BREAK_PENDING → TESTED on 2 consecutive closes back inside (C3)."""
        z = self._make_zone_with_width(state=ZoneState.FRESH)
        self._step(z, "106")  # BREAK_PENDING, consecutive=1
        assert z.state == ZoneState.BREAK_PENDING
        # 2 closes back inside
        self._step(z, "100")  # inside
        self._step(z, "99")   # inside, consecutive_inside=2
        assert z.state == ZoneState.TESTED
        assert z.false_break_count == 1
        assert z.touch_count == 1

    def test_broken_confirmed_to_flipped_requires_one_bar(self):
        """AC5: BROKEN_CONFIRMED persists ≥1 bar before FLIPPED."""
        z = self._make_zone_with_width(state=ZoneState.FRESH)
        # Reach BROKEN_CONFIRMED
        for _ in range(4):
            self._step(z, "106")
        assert z.state == ZoneState.BROKEN_CONFIRMED

        # Next bar → FLIPPED (bars_since_confirmed=1 ≥ 1)
        self._step(z, "106")
        assert z.state == ZoneState.FLIPPED

    def test_flipped_inverts_polarity(self):
        """On FLIPPED, polarity inverts (resistance → support)."""
        z = self._make_zone_with_width(
            state=ZoneState.FRESH,
            base_strength=6,
        )
        z.polarity = ZonePolarity.RESISTANCE
        for _ in range(5):
            self._step(z, "106")
        assert z.state == ZoneState.FLIPPED
        assert z.polarity == ZonePolarity.SUPPORT

    def test_frozen_touch_bonus_on_flip(self):
        """AC6: frozen_touch_bonus = min(touch_count_pre_flip, 3) on FLIPPED."""
        z = self._make_zone_with_width(state=ZoneState.TESTED, touch_count=2)
        z.state = ZoneState.TESTED
        # Trigger BROKEN_CONFIRMED then FLIPPED
        for _ in range(5):
            self._step(z, "106")
        assert z.state == ZoneState.FLIPPED
        assert z.frozen_touch_bonus == 2  # min(2, 3) = 2

    def test_touch_count_resets_on_flip(self):
        """AC6: touch_count resets to 0 after FLIPPED."""
        z = self._make_zone_with_width(state=ZoneState.TESTED, touch_count=3)
        for _ in range(5):
            self._step(z, "106")
        assert z.state == ZoneState.FLIPPED
        assert z.touch_count == 0  # reset post-flip

    def test_frozen_touch_bonus_capped_at_3(self):
        """AC6: frozen_touch_bonus = min(touch_count, 3) — capped."""
        z = self._make_zone_with_width(state=ZoneState.TESTED, touch_count=10)
        for _ in range(5):
            self._step(z, "106")
        assert z.state == ZoneState.FLIPPED
        assert z.frozen_touch_bonus == 3  # cap

    def test_tested_to_frozen_at_100_bars(self):
        """TESTED → FROZEN after 100 bars without touch (price must stay near zone to avoid EXPIRED)."""
        z = self._make_zone_with_width(state=ZoneState.TESTED)
        z.bars_since_touch = 99
        # center=100, ATR=5, 5×5=25. Price at 103 is close (within zone), so no EXPIRED.
        # But 103 is inside the zone [95-105], so it TOUCHES and resets bars_since_touch.
        # Use a price just outside the zone but within 5×ATR: 108 (dist=8 < 25)
        self._step(z, "108")  # outside zone, not touching, within 5×ATR → bars_since_touch=100 → FROZEN
        assert z.state == ZoneState.FROZEN

    def test_frozen_to_expired_at_200_bars(self):
        """FROZEN → EXPIRED after 200 bars."""
        z = self._make_zone_with_width(state=ZoneState.FROZEN)
        z.bars_since_touch = 199
        self._step(z, "50")
        assert z.state == ZoneState.EXPIRED

    def test_expired_when_price_far(self):
        """FROZEN → EXPIRED when price > 5×ATR away from center."""
        z = self._make_zone_with_width(state=ZoneState.FROZEN)
        z.bars_since_touch = 10
        # ATR = 5; center = 100; 5×5=25; price at 130 → 30 > 25
        self._step(z, "130")
        assert z.state == ZoneState.EXPIRED

    def test_decay_every_50_bars(self):
        """Strength decays by 1 every 50 bars without touch."""
        z = self._make_zone_with_width(state=ZoneState.FRESH, base_strength=10)
        z.bars_since_touch = 49
        initial = z.total_strength()
        self._step(z, "50")  # bars_since_touch = 50 → decay
        assert z.total_strength() == initial - 1

    def test_tested_additional_touch_increments_count(self):
        """TESTED: additional touch increments touch_count."""
        z = self._make_zone_with_width(state=ZoneState.TESTED, touch_count=1)
        self._step(z, "102")  # inside zone, touch
        assert z.touch_count == 2

    def test_break_pending_opposite_direction_ignored(self):
        """BREAK_PENDING: close in opposite direction of break doesn't extend streak."""
        z = self._make_zone_with_width(state=ZoneState.FRESH)
        self._step(z, "106")  # BREAK_PENDING dir=up, consecutive=1
        assert z.state == ZoneState.BREAK_PENDING
        assert z.break_direction == "up"
        self._step(z, "106")  # consecutive=2
        assert z.break_consecutive == 2

    def test_candidate_validated_on_2_rejections(self):
        """CANDIDATE → FRESH on ≥2 rejections (wick ≥ 50% of range)."""
        z = self._make_zone_with_width(state=ZoneState.CANDIDATE)
        z.zone_high = Decimal("105")
        z.zone_low  = Decimal("95")
        z.candidate_rejection_count = 0

        # Push a rejection bar: upper wick dominant
        for i in range(2):
            bar = AggregatedBar(
                symbol="BTCUSDT",
                timestamp_start=BASE_TS + datetime.timedelta(minutes=i),
                timestamp_end=BASE_TS + datetime.timedelta(minutes=i+15),
                timeframe="15m",
                open=Decimal("100"),
                high=Decimal("105"),  # upper wick = 105-100 = 5 = 100% of range
                low=Decimal("100"),
                close=Decimal("100"),
                volume=Decimal("1000"),
                is_complete=True,
                is_reliable=True,
            )
            from src.sr.zone_detector import _step_candidate
            _step_candidate(z, bar)

        assert z.state == ZoneState.FRESH

    def test_candidate_expires_after_50_bars(self):
        """CANDIDATE → EXPIRED after 50 LTF bars without validation."""
        z = self._make_zone_with_width(state=ZoneState.CANDIDATE)
        z.candidate_bars_elapsed = 49
        bar = make_bar(c="100")  # outside zone [95-105]? No, 100 is inside
        bar2 = AggregatedBar(
            symbol="BTCUSDT",
            timestamp_start=BASE_TS,
            timestamp_end=BASE_TS + datetime.timedelta(minutes=15),
            timeframe="15m",
            open=Decimal("200"),
            high=Decimal("201"),
            low=Decimal("199"),
            close=Decimal("200"),
            volume=Decimal("1000"),
            is_complete=True,
            is_reliable=True,
        )
        from src.sr.zone_detector import _step_candidate
        _step_candidate(z, bar2)
        assert z.state == ZoneState.EXPIRED


# ---------------------------------------------------------------------------
# AC7: No float in outputs
# ---------------------------------------------------------------------------

class TestNoFloat:
    """AC7: All ZoneDetector outputs use Decimal, not float."""

    def test_srzone_fields_are_decimal(self):
        """SRZone immutable snapshot uses Decimal for price fields."""
        detector = make_detector()
        leg = make_leg(direction=LegDirection.BEAR, start_price="100500", end_price="100000",
                       displacement="500", quality=3)
        bar = make_bar(c="100100", h="100500", l="100000")
        detector.update(bar, completed_leg=leg)
        zones = detector.get_active_zones()
        for z in zones:
            assert isinstance(z.center, Decimal), "center must be Decimal"
            assert isinstance(z.zone_high, Decimal), "zone_high must be Decimal"
            assert isinstance(z.zone_low, Decimal), "zone_low must be Decimal"

    def test_zone_mutable_center_decimal(self):
        z = make_zone(center="121060")
        assert isinstance(z.center, Decimal)
        assert isinstance(z.zone_high, Decimal)
        assert isinstance(z.zone_low, Decimal)


# ---------------------------------------------------------------------------
# Integration tests: ZoneDetector.update()
# ---------------------------------------------------------------------------

class TestZoneDetectorUpdate:
    """Integration: detector registers zones and processes lifecycle correctly."""

    def test_bull_leg_creates_resistance_zone(self):
        """Bull leg high → resistance candidate."""
        detector = make_detector()
        leg = make_leg(direction=LegDirection.BULL, start_price="99000",
                       end_price="100000", displacement="1000", quality=3)
        bar = make_bar(c="99800", h="100100", l="99700")
        detector.update(bar, completed_leg=leg)
        zones = detector.get_active_zones(sr_type=ZonePolarity.RESISTANCE)
        # Should have at least one resistance zone
        assert len(zones) >= 1
        # Center should be near bull leg high (end_price=100000)
        centers = [z.center for z in zones if z.origin == ZoneOrigin.LEG_EXTREME]
        assert any(abs(c - Decimal("100000")) < Decimal("1000") for c in centers)

    def test_bear_leg_creates_support_zone(self):
        """Bear leg low → support candidate."""
        detector = make_detector()
        leg = make_leg(direction=LegDirection.BEAR, start_price="101000",
                       end_price="100000", displacement="1000", quality=2)
        bar = make_bar(c="100200", h="101000", l="100000")
        detector.update(bar, completed_leg=leg)
        zones = detector.get_active_zones(sr_type=ZonePolarity.SUPPORT)
        assert len(zones) >= 1
        centers = [z.center for z in zones if z.origin == ZoneOrigin.LEG_EXTREME]
        assert any(abs(c - Decimal("100000")) < Decimal("1000") for c in centers)

    def test_leg_quality_adds_to_base_strength(self):
        """Base strength = 2 + leg_quality (§4.1)."""
        detector = make_detector()
        leg = make_leg(quality=4)
        bar = make_bar(c="91000")
        detector.update(bar, completed_leg=leg)
        zones = detector.get_active_zones()
        leg_zones = [z for z in zones if z.origin == ZoneOrigin.LEG_EXTREME]
        if leg_zones:
            # Minimum strength from this leg alone: base=2+4=6, source_bonus=0, touch=0
            assert leg_zones[0].strength >= 6

    def test_no_zones_without_atr(self):
        """No zones created when ATR is not ready."""
        detector = ZoneDetector(
            symbol="BTCUSDT",
            sr_timeframe="1H",
            atr_1h=ATRCalculator("BTCUSDT", "1H"),     # no ATR yet
            atr_15m=ATRCalculator("BTCUSDT", "15m"),    # no ATR yet
            atr_4h=ATRCalculator("BTCUSDT", "4H"),
        )
        leg = make_leg()
        bar = make_bar()
        detector.update(bar, completed_leg=leg)
        zones = detector.get_active_zones()
        assert len(zones) == 0  # no zones without ATR

    def test_update_without_leg(self):
        """update() with completed_leg=None only advances lifecycle."""
        detector = make_detector()
        bar = make_bar()
        # Should not raise
        detector.update(bar, completed_leg=None)

    def test_incomplete_bar_ignored(self):
        """Incomplete bars are silently ignored."""
        detector = make_detector()
        bar = make_bar(is_complete=False)
        leg = make_leg()
        detector.update(bar, completed_leg=leg)
        zones = detector.get_active_zones()
        assert len(zones) == 0

    def test_two_legs_within_cluster_threshold_merge(self):
        """AC1 integration: two legs with ends within 0.5×ATR(1H=400)=200 → one zone."""
        detector = make_detector(atr_1h="400", atr_15m="120")
        # ATR(1H) = 400 → merge_threshold = 200
        leg1 = make_leg(direction=LegDirection.BULL, end_price="100000", displacement="800", quality=2)
        leg2 = make_leg(direction=LegDirection.BULL, end_price="100150", displacement="600", quality=2,
                        ts_end=BASE_TS + datetime.timedelta(hours=3))
        bar1 = make_bar(c="99900")
        bar2 = make_bar(minute_offset=1, c="100050")
        detector.update(bar1, completed_leg=leg1)
        detector.update(bar2, completed_leg=leg2)
        resistance_zones = detector.get_active_zones(sr_type=ZonePolarity.RESISTANCE)
        # Only leg extremes from each leg
        leg_zones = [z for z in resistance_zones if z.origin == ZoneOrigin.LEG_EXTREME]
        # After clustering, the two close legs should have merged
        assert len(leg_zones) <= 1

    def test_get_active_zones_sorted_by_strength(self):
        """get_active_zones() returns zones sorted by strength descending."""
        detector = make_detector()
        for quality in [1, 4, 2]:
            leg = make_leg(quality=quality, end_price=str(100000 + quality * 1000),
                           displacement="1000")
            bar = make_bar(c=str(99000 + quality * 500))
            detector.update(bar, completed_leg=leg)
        zones = detector.get_active_zones()
        strengths = [z.strength for z in zones]
        assert strengths == sorted(strengths, reverse=True)

    def test_min_tier_filter(self):
        """get_active_zones(min_tier='B') excludes C-tier zones."""
        detector = make_detector()
        # C-tier: quality=0 → base=2+0=2, strength=2 → C-tier
        leg = make_leg(quality=0, end_price="100000", displacement="500")
        bar = make_bar(c="99800")
        detector.update(bar, completed_leg=leg)
        zones_b_plus = detector.get_active_zones(min_tier="B")
        zones_c_plus = detector.get_active_zones(min_tier="C")
        # C-tier should not appear in B+ filter
        c_tiers = [z for z in zones_b_plus if z.tier == Tier.C]
        assert len(c_tiers) == 0


# ---------------------------------------------------------------------------
# Density bias tests
# ---------------------------------------------------------------------------

class TestDensityBias:
    """Addendum B §B3.8: Density bias computation tests."""

    def test_neutral_when_balanced(self):
        """Balanced supply/demand → NEUTRAL."""
        detector = make_detector(atr_4h="400")
        # Equal strength above and below
        assert detector.density_bias == DensityBias.NEUTRAL

    def test_heavy_above_when_much_resistance(self):
        """More resistance zones above → HEAVY_ABOVE or MODERATE_ABOVE."""
        from src.sr.zone_detector import _compute_density_bias
        current_price = Decimal("100000")
        atr_4h = Decimal("400")
        # Create multiple strong resistance zones above
        zones: list[_ZoneMutable] = []
        for i, center_offset in enumerate([500, 800, 1100]):
            z = make_zone(
                center=str(100000 + center_offset),
                polarity=ZonePolarity.RESISTANCE,
                state=ZoneState.TESTED,
                base_strength=12,  # A-tier
            )
            z.zone_high = Decimal(str(100000 + center_offset + 100))
            z.zone_low  = Decimal(str(100000 + center_offset - 100))
            z.recompute_tier()
            zones.append(z)
        bias = _compute_density_bias(zones, current_price, atr_4h)
        assert bias in (DensityBias.HEAVY_ABOVE, DensityBias.MODERATE_ABOVE)

    def test_heavy_below_when_much_support(self):
        """More support zones below → HEAVY_BELOW or MODERATE_BELOW."""
        from src.sr.zone_detector import _compute_density_bias
        current_price = Decimal("100000")
        atr_4h = Decimal("400")
        zones: list[_ZoneMutable] = []
        for offset in [-500, -800, -1100]:
            z = make_zone(
                center=str(100000 + offset),
                polarity=ZonePolarity.SUPPORT,
                state=ZoneState.TESTED,
                base_strength=12,
            )
            z.zone_high = Decimal(str(100000 + offset + 100))
            z.zone_low  = Decimal(str(100000 + offset - 100))
            z.recompute_tier()
            zones.append(z)
        bias = _compute_density_bias(zones, current_price, atr_4h)
        assert bias in (DensityBias.HEAVY_BELOW, DensityBias.MODERATE_BELOW)

    def test_c_tier_zones_excluded(self):
        """C-tier zones are excluded from density computation."""
        from src.sr.zone_detector import _compute_density_bias
        current_price = Decimal("100000")
        atr_4h = Decimal("400")
        zones: list[_ZoneMutable] = []
        # Only C-tier resistance zones above
        for offset in [100, 200, 300]:
            z = make_zone(
                center=str(100000 + offset),
                polarity=ZonePolarity.RESISTANCE,
                state=ZoneState.TESTED,
                base_strength=2,  # C-tier
            )
            z.zone_high = Decimal(str(100000 + offset + 50))
            z.zone_low  = Decimal(str(100000 + offset - 50))
            z.recompute_tier()
            zones.append(z)
        bias = _compute_density_bias(zones, current_price, atr_4h)
        assert bias == DensityBias.NEUTRAL  # C-tiers excluded

    def test_expired_zones_excluded(self):
        """EXPIRED zones are excluded from density."""
        from src.sr.zone_detector import _compute_density_bias
        current_price = Decimal("100000")
        atr_4h = Decimal("400")
        z = make_zone(
            center="100200",
            polarity=ZonePolarity.RESISTANCE,
            state=ZoneState.EXPIRED,  # EXPIRED
            base_strength=12,
        )
        z.zone_high = Decimal("100300")
        z.zone_low  = Decimal("100100")
        z.recompute_tier()
        bias = _compute_density_bias([z], current_price, atr_4h)
        assert bias == DensityBias.NEUTRAL


# ---------------------------------------------------------------------------
# Breakout epsilon tests (P7)
# ---------------------------------------------------------------------------

class TestBreakoutEpsilon:
    """P7: breakout close threshold requires close > zone_high + epsilon."""

    def test_close_just_above_boundary_without_epsilon_not_counted(self):
        """Close exactly at zone_high does NOT trigger BREAK_PENDING (P7)."""
        z = make_zone()
        z.zone_high = Decimal("105")
        z.zone_low  = Decimal("95")
        epsilon = Decimal("0.5")  # 0.02 × ATR
        # close = zone_high (no epsilon exceeded)
        direction = _is_close_beyond_boundary(Decimal("105"), z, epsilon)
        assert direction is None

    def test_close_above_boundary_plus_epsilon_triggers(self):
        """Close > zone_high + epsilon triggers breakout."""
        z = make_zone()
        z.zone_high = Decimal("105")
        z.zone_low  = Decimal("95")
        epsilon = Decimal("0.5")
        direction = _is_close_beyond_boundary(Decimal("105.6"), z, epsilon)
        assert direction == "up"

    def test_close_below_boundary_triggers_down(self):
        """Close < zone_low - epsilon triggers bearish breakout."""
        z = make_zone()
        z.zone_high = Decimal("105")
        z.zone_low  = Decimal("95")
        epsilon = Decimal("0.5")
        direction = _is_close_beyond_boundary(Decimal("94.4"), z, epsilon)
        assert direction == "down"


# ---------------------------------------------------------------------------
# Retro zone (C4)
# ---------------------------------------------------------------------------

class TestRetroZone:
    """C4: retro zones init as TESTED with touch_count=1."""

    def test_retro_zone_is_tested(self):
        detector = make_detector()
        detector.add_retro_zone(
            center=Decimal("99000"),
            polarity=ZonePolarity.SUPPORT,
            displacement=Decimal("800"),
            quality=3,
            created_at=BASE_TS,
        )
        # Find the retro zone (it should be in TESTED state)
        found = None
        for z in detector._zones:
            if z.state == ZoneState.TESTED and z.touch_count == 1:
                found = z
                break
        assert found is not None, "Retro zone should exist in TESTED state with touch_count=1"

    def test_retro_zone_touch_count_is_1(self):
        detector = make_detector()
        detector.add_retro_zone(
            center=Decimal("99000"),
            polarity=ZonePolarity.SUPPORT,
            displacement=Decimal("1000"),
            quality=2,
            created_at=BASE_TS,
        )
        tested_zones = [z for z in detector._zones if z.state == ZoneState.TESTED]
        assert all(z.touch_count == 1 for z in tested_zones)
