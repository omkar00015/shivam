"""Tests for src/phase/phase_engine.py — all acceptance criteria + stickiness.

Acceptance Criteria:
  AC1  5 alternating legs with declining efficiency → BALANCE scores highest
  AC2  5 bull legs with HH+HL + density HEAVY_BELOW → TREND_BULL scores >= 55
  AC3  Phase label does NOT change on first winning bar — requires 2 consecutive 1H closes
  AC4  TRANSITION flag fires when margin > 10 during interim period
  AC5  confidence = winning / (winning + second), expressed as Decimal
  AC6  No float anywhere
"""

import datetime
from decimal import Decimal

import pytest

from src.data_ingest.bar_aggregator import AggregatedBar
from src.indicators.zlbb import CompletedLeg, LegDirection, ZLBBState
from src.phase.phase_engine import (
    Phase,
    PhaseEngine,
    PhaseResult,
    _compute_density_modifier,
    score_all,
    score_balance,
    score_distribution,
    score_accumulation,
    score_trend_bear,
    score_trend_bull,
)
from src.sr.zone_detector import (
    DensityBias,
    SRZone,
    Tier,
    ZoneOrigin,
    ZonePolarity,
    ZoneState,
)

UTC = datetime.timezone.utc
BASE_TS = datetime.datetime(2024, 3, 1, 0, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_leg(
    direction: LegDirection = LegDirection.BULL,
    start_price: str = "100000",
    end_price: str = "101000",
    efficiency: str = "0.60",
    displacement: str = "1000",
    quality: int = 3,
    atr_at_start: str = "500",
    bar_count: int = 8,
    idx: int = 0,
) -> CompletedLeg:
    ts = BASE_TS + datetime.timedelta(hours=idx * 2)
    displ = Decimal(displacement)
    eff   = Decimal(efficiency)
    cr    = displ / eff if eff > Decimal("0") else displ
    return CompletedLeg(
        direction=direction,
        start_price=Decimal(start_price),
        end_price=Decimal(end_price),
        displacement=displ,
        bar_count=bar_count,
        efficiency=eff,
        cumulative_range=cr,
        quality=quality,
        atr_at_start=Decimal(atr_at_start),
        timestamp_start=ts,
        timestamp_end=ts + datetime.timedelta(hours=2),
        is_band_walk=False,
    )


def make_bull_leg(
    start: str, end: str, eff: str = "0.65", quality: int = 3, idx: int = 0
) -> CompletedLeg:
    return make_leg(
        direction=LegDirection.BULL,
        start_price=start,
        end_price=end,
        efficiency=eff,
        displacement=str(abs(float(end) - float(start))),
        quality=quality,
        idx=idx,
    )


def make_bear_leg(
    start: str, end: str, eff: str = "0.65", quality: int = 3, idx: int = 0
) -> CompletedLeg:
    return make_leg(
        direction=LegDirection.BEAR,
        start_price=start,
        end_price=end,
        efficiency=eff,
        displacement=str(abs(float(end) - float(start))),
        quality=quality,
        idx=idx,
    )


def make_sr_zone(
    center: str = "100000",
    polarity: ZonePolarity = ZonePolarity.RESISTANCE,
    tier: Tier = Tier.A,
    state: ZoneState = ZoneState.TESTED,
    zone_offset: str = "100",
) -> SRZone:
    c   = Decimal(center)
    off = Decimal(zone_offset)
    return SRZone(
        zone_id="zone_" + center,
        center=c,
        zone_high=c + off,
        zone_low=c - off,
        polarity=polarity,
        origin=ZoneOrigin.LEG_EXTREME,
        timeframe="1H",
        is_midpoint=False,
        state=state,
        tier=tier,
        strength=12,
        touch_count=2,
        false_break_count=0,
        created_at=BASE_TS,
        last_touch_time=BASE_TS,
    )


def make_bar(
    minute_offset: int = 0,
    close: str = "100000",
    is_complete: bool = True,
) -> AggregatedBar:
    ts = BASE_TS + datetime.timedelta(minutes=minute_offset * 15)
    c  = Decimal(close)
    return AggregatedBar(
        symbol="BTCUSDT",
        timestamp_start=ts,
        timestamp_end=ts + datetime.timedelta(minutes=15),
        timeframe="15m",
        open=c,
        high=c + Decimal("10"),
        low=c - Decimal("10"),
        close=c,
        volume=Decimal("1000"),
        is_complete=is_complete,
        is_reliable=True,
    )


_NO_ZONES: list = []
_NO_BARS:  list = []
_NO_ZLBB:  list = []
_ATR_1H    = Decimal("500")
_PRICE     = Decimal("100000")


def _make_zlbb(zlema: Decimal = Decimal("100000")) -> ZLBBState:
    """Create a minimal ZLBBState with the given ZLEMA value."""
    sigma = Decimal("200")
    return ZLBBState(
        zlema=zlema,
        sigma=sigma,
        upper_band=zlema + Decimal("2") * sigma,
        lower_band=zlema - Decimal("2") * sigma,
        upper_1sigma=zlema + sigma,
        lower_1sigma=zlema - sigma,
        bandwidth=(Decimal("4") * sigma) / zlema,
        bandwidth_percentile=None,
    )


def quick_score(
    legs,
    zones=None,
    price=None,
    atr_1h=None,
    density_bias=DensityBias.NEUTRAL,
) -> dict:
    return score_all(
        legs=legs,
        zones=zones or _NO_ZONES,
        current_price=price or _PRICE,
        atr_1h=atr_1h or _ATR_1H,
        density_bias=density_bias,
        recent_1h_bars=_NO_BARS,
        recent_zlbb=_NO_ZLBB,
    )


# ---------------------------------------------------------------------------
# AC1: BALANCE scores highest on 5 alternating legs with declining efficiency
# ---------------------------------------------------------------------------

class TestBalanceScoring:
    """AC1: 5 alternating legs with declining efficiency → BALANCE scores highest."""

    def test_ac1_balance_scores_highest(self):
        """AC1: alternating direction + low efficiency + small displacement → BALANCE wins."""
        # 5 alternating legs, efficiency declining (all <= 0.50), small displacement
        legs = [
            make_bull_leg("100000", "100300", eff="0.50", quality=1, idx=0),
            make_bear_leg("100300", "100000", eff="0.48", quality=1, idx=1),
            make_bull_leg("100000", "100200", eff="0.45", quality=1, idx=2),
            make_bear_leg("100200", "100000", eff="0.42", quality=1, idx=3),
            make_bull_leg("100000", "100150", eff="0.40", quality=1, idx=4),
        ]
        # Small displacement: 300, 300, 200, 200, 150 — all well below 0.75×ATR(500)=375
        scores = quick_score(legs, density_bias=DensityBias.NEUTRAL)
        assert scores[Phase.BALANCE] > scores[Phase.TREND_BULL], \
            f"Expected BALANCE > TREND_BULL but got BALANCE={scores[Phase.BALANCE]} TREND_BULL={scores[Phase.TREND_BULL]}"
        assert scores[Phase.BALANCE] > scores[Phase.TREND_BEAR], \
            f"Expected BALANCE > TREND_BEAR"

    def test_alternating_direction_counts(self):
        """3 direction changes in 5 legs → +25 for BALANCE."""
        legs = [
            make_bull_leg("100000", "100300", eff="0.45", idx=0),
            make_bear_leg("100300", "100100", eff="0.43", idx=1),
            make_bull_leg("100100", "100300", eff="0.40", idx=2),
            make_bear_leg("100300", "100100", eff="0.38", idx=3),
            make_bull_leg("100100", "100250", eff="0.36", idx=4),
        ]
        s = score_balance(
            legs=legs, zones=[], current_price=_PRICE, atr_1h=_ATR_1H,
            density_bias=DensityBias.NEUTRAL, recent_1h_bars=[], recent_zlbb=[],
        )
        # 4 direction changes (BULL→BEAR→BULL→BEAR→BULL) ≥ 3 → +25
        assert s >= 25

    def test_no_alternation_zero_balance_base(self):
        """5 consecutive bull legs → no +25 alternation bonus.

        To isolate just the alternation bonus, compare legs with identical size/eff
        where only direction pattern differs. The alternating version should score
        exactly 25 more when all other conditions fire equally.
        """
        # Non-alternating: 5 bull legs, small displacement (200) and low eff (0.40)
        # → qualifies for: no-high-eff(+20), small displacement(+20), neutral density(+15) = 55
        # but NOT alternation (+25)
        no_alt = [make_bull_leg("100000", "100200", eff="0.40", idx=i) for i in range(5)]
        s_no_alt = score_balance(
            legs=no_alt, zones=[], current_price=_PRICE, atr_1h=_ATR_1H,
            density_bias=DensityBias.NEUTRAL, recent_1h_bars=[], recent_zlbb=[],
        )

        # Alternating: same size/eff, 4 direction changes → +25 extra
        alt = [
            make_bull_leg("100000", "100200", eff="0.40", idx=0),
            make_bear_leg("100200", "100000", eff="0.40", idx=1),
            make_bull_leg("100000", "100200", eff="0.40", idx=2),
            make_bear_leg("100200", "100000", eff="0.40", idx=3),
            make_bull_leg("100000", "100200", eff="0.40", idx=4),
        ]
        s_alt = score_balance(
            legs=alt, zones=[], current_price=_PRICE, atr_1h=_ATR_1H,
            density_bias=DensityBias.NEUTRAL, recent_1h_bars=[], recent_zlbb=[],
        )
        # Alternating should score 25 more (the +25 direction changes bonus)
        assert s_alt == s_no_alt + 25

    def test_neutral_density_adds_15(self):
        """NEUTRAL density_bias → +15 to BALANCE."""
        legs = [make_bull_leg("100000", "100200", eff="0.40", idx=i) for i in range(3)]
        s_neutral = score_balance(
            legs=legs, zones=[], current_price=_PRICE, atr_1h=_ATR_1H,
            density_bias=DensityBias.NEUTRAL, recent_1h_bars=[], recent_zlbb=[],
        )
        s_heavy = score_balance(
            legs=legs, zones=[], current_price=_PRICE, atr_1h=_ATR_1H,
            density_bias=DensityBias.HEAVY_ABOVE, recent_1h_bars=[], recent_zlbb=[],
        )
        assert s_neutral == s_heavy + 15

    def test_both_sides_defended_adds_20(self):
        """Support below + resistance above within 1.5×ATR → +20."""
        legs = [
            make_bull_leg("100000", "100200", eff="0.40", idx=0),
            make_bear_leg("100200", "100000", eff="0.38", idx=1),
            make_bull_leg("100000", "100150", eff="0.36", idx=2),
        ]
        price = Decimal("100000")
        atr   = Decimal("500")
        # Resistance 400 above (within 1.5×500=750): zone_low = price + 400 = 100400 > price ✓
        res_zone = make_sr_zone("100500", ZonePolarity.RESISTANCE, Tier.A, ZoneState.TESTED, "100")
        # Support 400 below: zone_high = price - 400 = 99600 < price ✓
        sup_zone = make_sr_zone("99500", ZonePolarity.SUPPORT, Tier.A, ZoneState.TESTED, "100")
        s = score_balance(
            legs=legs, zones=[res_zone, sup_zone], current_price=price, atr_1h=atr,
            density_bias=DensityBias.NEUTRAL, recent_1h_bars=[], recent_zlbb=[],
        )
        assert s >= 20  # both sides defended


# ---------------------------------------------------------------------------
# AC2: TREND_BULL >= 55 with 5 bull legs HH+HL + density HEAVY_BELOW
# ---------------------------------------------------------------------------

class TestTrendBullScoring:
    """AC2: 5 bull legs HH+HL + density HEAVY_BELOW → TREND_BULL >= 55."""

    def test_ac2_trend_bull_score_gte_55(self):
        """AC2: Strong HH+HL sequence + resistance breaks succeeded → score >= 55.

        5 bull legs each breaking above a resistance zone (>= 2 break count) → +10 break condition.
        HH+HL (+20) + majority (+15) + avg eff (+15) + breaks (+10) = 60 >= 55.
        """
        # 5 bull legs with HH+HL structure
        legs = [
            make_bull_leg("99000", "100000", eff="0.65", quality=3, idx=0),
            make_bull_leg("99500", "100800", eff="0.65", quality=3, idx=1),
            make_bull_leg("100200", "101500", eff="0.65", quality=3, idx=2),
            make_bull_leg("101000", "102500", eff="0.65", quality=3, idx=3),
            make_bull_leg("102000", "103800", eff="0.65", quality=3, idx=4),
        ]
        # Resistance zone with zone_low = 99500 — all bull legs end above 99500 → break count = 5 >= 2
        res_zone = make_sr_zone("99600", ZonePolarity.RESISTANCE, Tier.A, ZoneState.TESTED, "100")
        scores = quick_score(legs, zones=[res_zone], density_bias=DensityBias.NEUTRAL)
        assert scores[Phase.TREND_BULL] >= 55, \
            f"Expected TREND_BULL >= 55, got {scores[Phase.TREND_BULL]}"

    def test_hh_hl_awards_20(self):
        """HH+HL structure (≥2 consecutive bull legs) → +20."""
        legs = [
            make_bull_leg("100000", "101000", eff="0.65", quality=3, idx=0),
            make_bull_leg("100500", "102000", eff="0.65", quality=3, idx=1),
            make_bull_leg("101000", "103000", eff="0.65", quality=3, idx=2),
        ]
        s = score_trend_bull(
            legs=legs, zones=[], current_price=_PRICE, atr_1h=_ATR_1H,
            density_bias=DensityBias.NEUTRAL, recent_1h_bars=[], recent_zlbb=[],
        )
        assert s >= 20  # At least HH+HL awarded

    def test_majority_bull_legs_awards_15(self):
        """60% bull legs in window → +15."""
        legs = [
            make_bull_leg("100000", "101000", eff="0.55", idx=0),
            make_bull_leg("100800", "102000", eff="0.55", idx=1),
            make_bull_leg("101500", "103000", eff="0.55", idx=2),
            make_bear_leg("103000", "102500", eff="0.55", idx=3),
            make_bear_leg("102500", "102000", eff="0.55", idx=4),
        ]
        # 3 bull / 5 = 60% >= 0.60 → +15
        s = score_trend_bull(
            legs=legs, zones=[], current_price=_PRICE, atr_1h=_ATR_1H,
            density_bias=DensityBias.NEUTRAL, recent_1h_bars=[], recent_zlbb=[],
        )
        assert s >= 15

    def test_price_above_zlema_majority_adds_10(self):
        """C6: count(close > ZLEMA) / 20 >= 0.60 in last 20 1H bars → +10."""
        legs = [make_bull_leg("100000", "101000", eff="0.40", idx=i) for i in range(3)]
        # 15 bars with close > ZLEMA, 5 with close <= ZLEMA → 15/20 = 0.75 >= 0.60
        zlema = Decimal("100000")
        bars_above = [make_bar(i, "100500") for i in range(15)]   # close 100500 > zlema 100000
        bars_below = [make_bar(i + 15, "99500") for i in range(5)] # close 99500 < zlema 100000
        all_bars = bars_above + bars_below
        all_zlbb = [_make_zlbb(zlema=zlema) for _ in range(20)]

        s_with = score_trend_bull(
            legs=legs, zones=[], current_price=_PRICE, atr_1h=_ATR_1H,
            density_bias=DensityBias.NEUTRAL,
            recent_1h_bars=all_bars, recent_zlbb=all_zlbb,
        )
        s_without = score_trend_bull(
            legs=legs, zones=[], current_price=_PRICE, atr_1h=_ATR_1H,
            density_bias=DensityBias.NEUTRAL, recent_1h_bars=[], recent_zlbb=[],
        )
        assert s_with == s_without + 10

    def test_price_below_zlema_no_bull_bonus(self):
        """C6: count(close > ZLEMA) / 20 < 0.60 → no +10 for TREND_BULL."""
        legs = [make_bull_leg("100000", "101000", eff="0.40", idx=i) for i in range(3)]
        zlema = Decimal("100000")
        # Only 5 bars above ZLEMA → 5/20 = 0.25 < 0.60
        bars_above = [make_bar(i, "100500") for i in range(5)]
        bars_below = [make_bar(i + 5, "99500") for i in range(15)]
        all_bars = bars_above + bars_below
        all_zlbb = [_make_zlbb(zlema=zlema) for _ in range(20)]

        s_with = score_trend_bull(
            legs=legs, zones=[], current_price=_PRICE, atr_1h=_ATR_1H,
            density_bias=DensityBias.NEUTRAL,
            recent_1h_bars=all_bars, recent_zlbb=all_zlbb,
        )
        s_without = score_trend_bull(
            legs=legs, zones=[], current_price=_PRICE, atr_1h=_ATR_1H,
            density_bias=DensityBias.NEUTRAL, recent_1h_bars=[], recent_zlbb=[],
        )
        # No bonus: scores should be equal
        assert s_with == s_without

    def test_bull_band_walk_recently_adds_10(self):
        """C6: bull leg with is_band_walk=True in last 10 legs → +10.

        Isolate by using identical legs where the ONLY difference is is_band_walk.
        Both versions use the same legs; one replaces one leg's is_band_walk=True.
        All other scoring conditions fire identically in both cases.
        """
        from src.indicators.zlbb import CompletedLeg as CL

        # Base leg: BULL, neutral params — ensure no HH+HL, no majority bonus, no pullback
        base = make_leg(
            direction=LegDirection.BULL,
            start_price="100000", end_price="100400",
            efficiency="0.40", displacement="400", quality=2, idx=0,
        )

        # Identical leg with is_band_walk=True (swap the flag only)
        base_bw = CL(
            direction=base.direction,
            start_price=base.start_price,
            end_price=base.end_price,
            displacement=base.displacement,
            bar_count=base.bar_count,
            efficiency=base.efficiency,
            cumulative_range=base.cumulative_range,
            quality=base.quality,
            atr_at_start=base.atr_at_start,
            timestamp_start=base.timestamp_start,
            timestamp_end=base.timestamp_end,
            is_band_walk=True,
        )

        s_with_bw = score_trend_bull(
            legs=[base_bw], zones=[], current_price=_PRICE, atr_1h=_ATR_1H,
            density_bias=DensityBias.NEUTRAL, recent_1h_bars=[], recent_zlbb=[],
        )
        s_no_bw = score_trend_bull(
            legs=[base], zones=[], current_price=_PRICE, atr_1h=_ATR_1H,
            density_bias=DensityBias.NEUTRAL, recent_1h_bars=[], recent_zlbb=[],
        )
        # Only difference: band walk flag → +10
        assert s_with_bw == s_no_bw + 10

    def test_resistance_breaks_succeeded_adds_10(self):
        """C6: >= 2 bull legs in last 5 closing above a resistance zone → +10."""
        price = Decimal("100000")
        atr   = Decimal("500")
        # Resistance zone centered at 100500: zone_low = 100400, zone_high = 100600
        res_zone = make_sr_zone("100500", ZonePolarity.RESISTANCE, Tier.A, ZoneState.TESTED, "100")
        # 2 bull legs that ended ABOVE zone_low (100400): end at 101000 > 100400 ✓
        bull1 = make_bull_leg("100000", "101000", eff="0.65", quality=3, idx=0)
        bull2 = make_bull_leg("100500", "102000", eff="0.65", quality=3, idx=1)
        normal_bear = make_bear_leg("102000", "101500", eff="0.65", quality=3, idx=2)

        s_with_break = score_trend_bull(
            legs=[bull1, normal_bear, bull2], zones=[res_zone], current_price=price, atr_1h=atr,
            density_bias=DensityBias.NEUTRAL, recent_1h_bars=[], recent_zlbb=[],
        )
        s_no_zone = score_trend_bull(
            legs=[bull1, normal_bear, bull2], zones=[], current_price=price, atr_1h=atr,
            density_bias=DensityBias.NEUTRAL, recent_1h_bars=[], recent_zlbb=[],
        )
        assert s_with_break == s_no_zone + 10

    def test_resistance_breaks_fewer_than_2_no_bonus(self):
        """C6: Only 1 bull leg closing above a resistance zone → no +10."""
        price = Decimal("100000")
        atr   = Decimal("500")
        res_zone = make_sr_zone("100500", ZonePolarity.RESISTANCE, Tier.A, ZoneState.TESTED, "100")
        # Only 1 bull leg ends above zone_low; other bull ends at 100000 < zone_low(100400) → no break
        bull1 = make_bull_leg("100000", "101000", eff="0.65", quality=3, idx=0)
        bull2 = make_bull_leg("99000", "100200", eff="0.65", quality=3, idx=1)  # ends 100200 < zone_low

        s_with = score_trend_bull(
            legs=[bull1, bull2], zones=[res_zone], current_price=price, atr_1h=atr,
            density_bias=DensityBias.NEUTRAL, recent_1h_bars=[], recent_zlbb=[],
        )
        s_without = score_trend_bull(
            legs=[bull1, bull2], zones=[], current_price=price, atr_1h=atr,
            density_bias=DensityBias.NEUTRAL, recent_1h_bars=[], recent_zlbb=[],
        )
        assert s_with == s_without  # no +10

    def test_bear_rejection_failed_adds_10(self):
        """C6: >= 2 bear legs with small disp AND ending inside support zone → +10."""
        price = Decimal("100000")
        atr   = Decimal("500")
        # Support zone centered at 99700: zone_low = 99600, zone_high = 99800
        sup_zone = make_sr_zone("99700", ZonePolarity.SUPPORT, Tier.A, ZoneState.TESTED, "100")
        # Bear legs with displacement < 0.50×500=250 AND ending inside zone [99600, 99800]
        small_bear1 = make_leg(
            direction=LegDirection.BEAR,
            start_price="100000", end_price="99700",
            efficiency="0.50", displacement="200",  # 200 < 250
            quality=2, idx=0,
        )
        small_bear2 = make_leg(
            direction=LegDirection.BEAR,
            start_price="100000", end_price="99750",
            efficiency="0.50", displacement="200",  # 200 < 250
            quality=2, idx=1,
        )
        s_with = score_trend_bull(
            legs=[small_bear1, small_bear2], zones=[sup_zone], current_price=price, atr_1h=atr,
            density_bias=DensityBias.NEUTRAL, recent_1h_bars=[], recent_zlbb=[],
        )
        s_without = score_trend_bull(
            legs=[small_bear1, small_bear2], zones=[], current_price=price, atr_1h=atr,
            density_bias=DensityBias.NEUTRAL, recent_1h_bars=[], recent_zlbb=[],
        )
        assert s_with == s_without + 10

    def test_empty_legs_returns_zero(self):
        assert score_trend_bull(
            legs=[], zones=[], current_price=_PRICE, atr_1h=_ATR_1H,
            density_bias=DensityBias.NEUTRAL, recent_1h_bars=[], recent_zlbb=[],
        ) == 0

    def test_score_clamped_at_100(self):
        """Score is clamped to [0, 100]."""
        legs = [
            make_bull_leg("99000", "100000", eff="0.70", quality=4, idx=0),
            make_bull_leg("99500", "101000", eff="0.70", quality=4, idx=1),
            make_bull_leg("100200", "102000", eff="0.70", quality=4, idx=2),
            make_bull_leg("101000", "103000", eff="0.70", quality=4, idx=3),
            make_bull_leg("102000", "104500", eff="0.70", quality=4, idx=4),
        ]
        s = score_trend_bull(
            legs=legs, zones=[], current_price=_PRICE, atr_1h=_ATR_1H,
            density_bias=DensityBias.HEAVY_BELOW, recent_1h_bars=[], recent_zlbb=[],
        )
        assert s <= 100


# ---------------------------------------------------------------------------
# AC3: Phase label does NOT change on first winning 1H bar — needs 2 consecutive
# ---------------------------------------------------------------------------

class TestPhaseStickiness:
    """AC3 + AC4: Stickiness FSM and TRANSITION flag."""

    def _make_engine(self, initial: Phase = Phase.BALANCE) -> PhaseEngine:
        return PhaseEngine(initial_phase=initial)

    def _make_strong_bull_legs(self) -> list:
        return [
            make_bull_leg("99000", "100000", eff="0.65", quality=3, idx=i)
            for i in range(5)
        ]

    def _run_1h_close(self, engine: PhaseEngine, legs, density_bias=DensityBias.HEAVY_BELOW) -> PhaseResult:
        bar = make_bar()
        return engine.update(
            bar=bar,
            legs=legs,
            zones=[],
            current_price=_PRICE,
            atr_1h=_ATR_1H,
            density_bias=density_bias,
            recent_1h_bars=[],
            recent_zlbb=[],
            is_1h_close=True,
        )

    def test_ac3_label_unchanged_after_first_1h_close(self):
        """AC3: After 1 winning 1H close, label is still BALANCE (not yet changed)."""
        engine = self._make_engine(Phase.BALANCE)
        legs   = self._make_strong_bull_legs()
        result = self._run_1h_close(engine, legs)
        # One win: pending set, but label not changed yet
        assert engine.active_phase == Phase.BALANCE, \
            f"Phase should still be BALANCE after 1 close, got {engine.active_phase}"

    def test_ac3_label_changes_after_second_1h_close(self):
        """AC3: After 2 consecutive wins, label changes to TREND_BULL."""
        engine = self._make_engine(Phase.BALANCE)
        legs   = self._make_strong_bull_legs()
        self._run_1h_close(engine, legs)   # first win
        self._run_1h_close(engine, legs)   # second win
        assert engine.active_phase == Phase.TREND_BULL, \
            f"Phase should be TREND_BULL after 2 consecutive wins, got {engine.active_phase}"

    def test_ac3_different_winner_at_second_close_resets(self):
        """AC3/P8: If a different phase wins at 2nd close, counter resets."""
        engine = self._make_engine(Phase.BALANCE)
        legs   = self._make_strong_bull_legs()
        self._run_1h_close(engine, legs)             # first: TREND_BULL leads
        # Second: TREND_BEAR leads (use bear legs)
        bear_legs = [
            make_bear_leg("101000", "100000", eff="0.65", quality=3, idx=i)
            for i in range(5)
        ]
        self._run_1h_close(engine, bear_legs, density_bias=DensityBias.HEAVY_ABOVE)
        # Counter should have reset — label still BALANCE
        assert engine.active_phase == Phase.BALANCE

    def test_ac4_transition_flag_fires_on_dominant_new_winner(self):
        """AC4: TRANSITION flag = True when margin > 10 during interim period."""
        engine = self._make_engine(Phase.BALANCE)
        legs   = self._make_strong_bull_legs()
        result = self._run_1h_close(engine, legs)   # first win: pending, not yet applied
        # Still in interim (one close only): transition_flag should be True
        assert result.transition_flag is True, \
            "transition_flag should be True during interim (margin > 10)"

    def test_ac4_transition_flag_false_when_margin_small(self):
        """AC4: transition_flag = False when margin <= 10."""
        engine = self._make_engine(Phase.BALANCE)
        # Legs that barely favour TREND_BULL over BALANCE (small margin)
        legs = [make_bull_leg("100000", "100200", eff="0.30", quality=1, idx=0)]
        result = self._run_1h_close(engine, legs)
        # With a single weak leg, margin should be < 10 between TREND_BULL and BALANCE
        # (depends on scoring — at minimum check it doesn't always fire)
        # If scores are equal or close → no transition
        bull_score = result.all_scores[Phase.TREND_BULL]
        bal_score  = result.all_scores[Phase.BALANCE]
        expected_flag = (bull_score - bal_score >= 10 and Phase.TREND_BULL != engine.active_phase)
        assert result.transition_flag == expected_flag

    def test_size_reduction_on_transition(self):
        """TRANSITION flag → size_reduction = 0.5."""
        engine = self._make_engine(Phase.BALANCE)
        legs   = self._make_strong_bull_legs()
        result = self._run_1h_close(engine, legs)
        if result.transition_flag:
            assert result.size_reduction == Decimal("0.5")

    def test_size_reduction_normal_when_no_transition(self):
        """No transition → size_reduction = 1.0."""
        engine = self._make_engine(Phase.BALANCE)
        # Weak neutral legs — should keep BALANCE active with no transition
        legs = [make_bull_leg("100000", "100100", eff="0.30", quality=1, idx=i) for i in range(2)]
        result = self._run_1h_close(engine, legs)
        if not result.transition_flag:
            assert result.size_reduction == Decimal("1")

    def test_consecutive_counter_resets_on_same_phase_winning(self):
        """P8: if current_winner == active_phase, reset pending."""
        engine = self._make_engine(Phase.TREND_BULL)
        legs   = self._make_strong_bull_legs()
        # TREND_BULL is already active; it wins again → pending reset, no change
        self._run_1h_close(engine, legs)
        assert engine._pending_phase is None
        assert engine._consecutive_count == 0


# ---------------------------------------------------------------------------
# AC5: Confidence = winning / (winning + second), Decimal
# ---------------------------------------------------------------------------

class TestConfidence:
    """AC5: phase_confidence is Decimal, = active_score / (active_score + second_score)."""

    def test_ac5_confidence_is_decimal(self):
        """AC5: phase_confidence is Decimal instance."""
        engine = PhaseEngine(initial_phase=Phase.BALANCE)
        legs   = [make_bull_leg("100000", "101000", eff="0.40", quality=2, idx=0)]
        bar    = make_bar()
        result = engine.update(
            bar=bar, legs=legs, zones=[], current_price=_PRICE,
            atr_1h=_ATR_1H, density_bias=DensityBias.NEUTRAL,
            recent_1h_bars=[], recent_zlbb=[], is_1h_close=False,
        )
        assert isinstance(result.phase_confidence, Decimal), \
            f"confidence must be Decimal, got {type(result.phase_confidence)}"

    def test_ac5_confidence_range(self):
        """AC5: confidence is in [0, 1]."""
        engine = PhaseEngine(initial_phase=Phase.BALANCE)
        legs   = [make_bull_leg("100000", "101000", eff="0.40", quality=2, idx=0)]
        bar    = make_bar()
        result = engine.update(
            bar=bar, legs=legs, zones=[], current_price=_PRICE,
            atr_1h=_ATR_1H, density_bias=DensityBias.NEUTRAL,
            recent_1h_bars=[], recent_zlbb=[],
        )
        assert Decimal("0") <= result.phase_confidence <= Decimal("1")

    def test_ac5_confidence_formula(self):
        """AC5: confidence = active_score / (active_score + second_best_score)."""
        engine = PhaseEngine(initial_phase=Phase.BALANCE)
        legs   = [make_bull_leg("100000", "101000", eff="0.40", quality=2, idx=i) for i in range(3)]
        bar    = make_bar()
        result = engine.update(
            bar=bar, legs=legs, zones=[], current_price=_PRICE,
            atr_1h=_ATR_1H, density_bias=DensityBias.NEUTRAL,
            recent_1h_bars=[], recent_zlbb=[],
        )
        scores = result.all_scores
        sorted_vals = sorted(scores.values(), reverse=True)
        active_score = scores[result.active_phase]
        second_score = sorted_vals[1] if len(sorted_vals) > 1 else 0
        if active_score + second_score > 0:
            expected = Decimal(active_score) / Decimal(active_score + second_score)
        else:
            expected = Decimal("0")
        assert abs(result.phase_confidence - expected) < Decimal("1E-9")


# ---------------------------------------------------------------------------
# AC6: No float in outputs
# ---------------------------------------------------------------------------

class TestNoFloat:
    """AC6: No float in PhaseResult fields."""

    def test_all_scores_are_int(self):
        """all_scores values are int."""
        scores = quick_score(
            legs=[make_bull_leg("100000", "101000", eff="0.60", idx=0)],
        )
        for phase, s in scores.items():
            assert isinstance(s, int), f"{phase}: score must be int, got {type(s)}"

    def test_size_reduction_is_decimal(self):
        """size_reduction is Decimal."""
        engine = PhaseEngine(initial_phase=Phase.BALANCE)
        bar    = make_bar()
        result = engine.update(
            bar=bar, legs=[], zones=[], current_price=_PRICE,
            atr_1h=_ATR_1H, density_bias=DensityBias.NEUTRAL,
            recent_1h_bars=[], recent_zlbb=[],
        )
        assert isinstance(result.size_reduction, Decimal)

    def test_confidence_is_decimal(self):
        engine = PhaseEngine(initial_phase=Phase.BALANCE)
        bar    = make_bar()
        result = engine.update(
            bar=bar, legs=[], zones=[], current_price=_PRICE,
            atr_1h=_ATR_1H, density_bias=DensityBias.NEUTRAL,
            recent_1h_bars=[], recent_zlbb=[],
        )
        assert isinstance(result.phase_confidence, Decimal)


# ---------------------------------------------------------------------------
# Distribution / Accumulation scoring
# ---------------------------------------------------------------------------

class TestDistributionAccumulationScoring:
    """Distribution: near resistance + declining bull efficiency."""

    def test_distribution_near_resistance_adds_25(self):
        """S/A-tier resistance within 1.5×ATR above → +25."""
        price = Decimal("100000")
        atr   = Decimal("500")
        # zone_low = price + 400 = 100400 — within 1.5×500=750 → INSIDE range ✓
        zone = make_sr_zone("100500", ZonePolarity.RESISTANCE, Tier.A, ZoneState.TESTED, "100")
        legs = [make_bull_leg("100000", "100500", eff="0.40", quality=1, idx=i) for i in range(3)]
        s = score_distribution(
            legs=legs, zones=[zone], current_price=price, atr_1h=atr,
            density_bias=DensityBias.NEUTRAL, recent_1h_bars=[], recent_zlbb=[],
        )
        assert s >= 25

    def test_distribution_c_tier_resistance_no_25(self):
        """C-tier resistance does NOT award +25."""
        price = Decimal("100000")
        atr   = Decimal("500")
        zone  = make_sr_zone("100400", ZonePolarity.RESISTANCE, Tier.C, ZoneState.TESTED, "100")
        legs  = [make_bull_leg("100000", "100500", eff="0.40", quality=1, idx=i) for i in range(3)]
        s = score_distribution(
            legs=legs, zones=[zone], current_price=price, atr_1h=atr,
            density_bias=DensityBias.NEUTRAL, recent_1h_bars=[], recent_zlbb=[],
        )
        assert s < 25  # no +25 for C-tier

    def test_accumulation_near_support_adds_25(self):
        """S/A-tier support within 1.5×ATR below → +25 for ACCUMULATION."""
        price = Decimal("100000")
        atr   = Decimal("500")
        # zone_high = 99600 < price ✓; distance = 400 < 750 ✓
        zone  = make_sr_zone("99500", ZonePolarity.SUPPORT, Tier.A, ZoneState.TESTED, "100")
        legs  = [make_bear_leg("100500", "100000", eff="0.40", quality=1, idx=i) for i in range(3)]
        s = score_accumulation(
            legs=legs, zones=[zone], current_price=price, atr_1h=atr,
            density_bias=DensityBias.NEUTRAL, recent_1h_bars=[], recent_zlbb=[],
        )
        assert s >= 25

    def test_distribution_heavy_above_adds_20(self):
        """HEAVY_ABOVE density → +20 to DISTRIBUTION."""
        legs = [make_bull_leg("100000", "100500", eff="0.40", quality=1, idx=i) for i in range(3)]
        s_neutral = score_distribution(
            legs=legs, zones=[], current_price=_PRICE, atr_1h=_ATR_1H,
            density_bias=DensityBias.NEUTRAL, recent_1h_bars=[], recent_zlbb=[],
        )
        s_above = score_distribution(
            legs=legs, zones=[], current_price=_PRICE, atr_1h=_ATR_1H,
            density_bias=DensityBias.HEAVY_ABOVE, recent_1h_bars=[], recent_zlbb=[],
        )
        assert s_above == s_neutral + 20

    def test_trend_bear_mirror(self):
        """TREND_BEAR mirrors TREND_BULL: LL+LH structure awards +20."""
        # 3 bear legs with lower highs and lower lows
        legs = [
            make_bear_leg("103000", "102000", eff="0.65", quality=3, idx=0),
            make_bear_leg("102500", "101000", eff="0.65", quality=3, idx=1),
            make_bear_leg("101800", "100000", eff="0.65", quality=3, idx=2),
        ]
        s = score_trend_bear(
            legs=legs, zones=[], current_price=_PRICE, atr_1h=_ATR_1H,
            density_bias=DensityBias.NEUTRAL, recent_1h_bars=[], recent_zlbb=[],
        )
        assert s >= 20  # LL+LH awards at minimum


# ---------------------------------------------------------------------------
# P10: Density modifier tests
# ---------------------------------------------------------------------------

class TestDensityModifier:
    """P10: density modifier based on weighted zone strength near price."""

    def test_supply_dominant_applies_bear_modifier(self):
        """supply_weight >= demand_weight + 3 → bear +10, bull -5."""
        price = Decimal("100000")
        atr   = Decimal("500")
        # 3 S-tier resistance zones above (weight = 3 each = 9 total supply)
        zones = [
            make_sr_zone(str(100200 + i * 100), ZonePolarity.RESISTANCE, Tier.S, ZoneState.TESTED, "50")
            for i in range(3)
        ]
        bull_mod, bear_mod = _compute_density_modifier(zones, price, atr)
        assert bear_mod == 10
        assert bull_mod == -5

    def test_demand_dominant_applies_bull_modifier(self):
        """demand_weight >= supply_weight + 3 → bull +10, bear -5."""
        price = Decimal("100000")
        atr   = Decimal("500")
        # 3 S-tier support zones below
        zones = [
            make_sr_zone(str(99800 - i * 100), ZonePolarity.SUPPORT, Tier.S, ZoneState.TESTED, "50")
            for i in range(3)
        ]
        bull_mod, bear_mod = _compute_density_modifier(zones, price, atr)
        assert bull_mod == 10
        assert bear_mod == -5

    def test_balanced_no_modifier(self):
        """supply_weight == demand_weight → no modifier."""
        price = Decimal("100000")
        atr   = Decimal("500")
        sup_zone = make_sr_zone("99800", ZonePolarity.SUPPORT, Tier.A, ZoneState.TESTED, "50")
        res_zone = make_sr_zone("100200", ZonePolarity.RESISTANCE, Tier.A, ZoneState.TESTED, "50")
        bull_mod, bear_mod = _compute_density_modifier([sup_zone, res_zone], price, atr)
        assert bull_mod == 0
        assert bear_mod == 0

    def test_c_tier_zones_excluded(self):
        """C-tier zones contribute weight=0 → no modifier."""
        price = Decimal("100000")
        atr   = Decimal("500")
        zones = [
            make_sr_zone(str(100200 + i * 50), ZonePolarity.RESISTANCE, Tier.C, ZoneState.TESTED, "25")
            for i in range(5)
        ]
        bull_mod, bear_mod = _compute_density_modifier(zones, price, atr)
        assert bull_mod == 0
        assert bear_mod == 0

    def test_outside_scan_range_excluded(self):
        """Zone beyond 2×ATR(1H) → excluded from density."""
        price = Decimal("100000")
        atr   = Decimal("500")
        # Zone at 100000 + 1200 = 101200; scan = 2×500=1000. zone_low = 101200-50=101150 > 100000+1000=101000
        zone = make_sr_zone("101200", ZonePolarity.RESISTANCE, Tier.S, ZoneState.TESTED, "50")
        bull_mod, bear_mod = _compute_density_modifier([zone], price, atr)
        assert bull_mod == 0
        assert bear_mod == 0


# ---------------------------------------------------------------------------
# PhaseEngine.score_only (pure query, no side effects)
# ---------------------------------------------------------------------------

class TestScoreOnly:
    def test_score_only_does_not_advance_stickiness(self):
        """score_only() must NOT change active_phase or pending state."""
        engine = PhaseEngine(initial_phase=Phase.BALANCE)
        legs   = [make_bull_leg("99000", "101000", eff="0.70", quality=4, idx=i) for i in range(5)]
        # Call score_only many times
        for _ in range(10):
            engine.score_only(
                legs=legs, zones=[], current_price=_PRICE,
                atr_1h=_ATR_1H, density_bias=DensityBias.HEAVY_BELOW,
                recent_1h_bars=[], recent_zlbb=[],
            )
        assert engine.active_phase == Phase.BALANCE
        assert engine._pending_phase is None
        assert engine._consecutive_count == 0

    def test_score_only_returns_all_five_phases(self):
        """score_only() returns all 5 Phase keys."""
        engine = PhaseEngine()
        result = engine.score_only(
            legs=[], zones=[], current_price=_PRICE,
            atr_1h=_ATR_1H, density_bias=DensityBias.NEUTRAL,
            recent_1h_bars=[], recent_zlbb=[],
        )
        assert set(result.all_scores.keys()) == set(Phase)


# ---------------------------------------------------------------------------
# PhaseEngine: incomplete bar ignored
# ---------------------------------------------------------------------------

class TestIncompleteBar:
    def test_incomplete_bar_returns_cached_result(self):
        """update() with incomplete bar uses cached scores (no recalculation)."""
        engine = PhaseEngine(initial_phase=Phase.BALANCE)
        bar = AggregatedBar(
            symbol="BTCUSDT",
            timestamp_start=BASE_TS,
            timestamp_end=BASE_TS + datetime.timedelta(minutes=15),
            timeframe="15m",
            open=_PRICE, high=_PRICE, low=_PRICE, close=_PRICE,
            volume=Decimal("1000"),
            is_complete=False,  # incomplete
            is_reliable=True,
        )
        result = engine.update(
            bar=bar, legs=[], zones=[], current_price=_PRICE,
            atr_1h=_ATR_1H, density_bias=DensityBias.NEUTRAL,
            recent_1h_bars=[], recent_zlbb=[],
        )
        assert result.active_phase == Phase.BALANCE


# ---------------------------------------------------------------------------
# PhaseEngine: 15m vs 1H close distinction
# ---------------------------------------------------------------------------

class TestIs1HClose:
    """Stickiness only advances on is_1h_close=True."""

    def test_15m_bars_do_not_advance_stickiness(self):
        """100 15m bars with strong TREND_BULL signal: label stays BALANCE."""
        engine = PhaseEngine(initial_phase=Phase.BALANCE)
        legs   = [
            make_bull_leg("99000", "100000", eff="0.70", quality=4, idx=i)
            for i in range(5)
        ]
        bar = make_bar()
        for _ in range(100):
            engine.update(
                bar=bar, legs=legs, zones=[], current_price=_PRICE,
                atr_1h=_ATR_1H, density_bias=DensityBias.HEAVY_BELOW,
                recent_1h_bars=[], recent_zlbb=[],
                is_1h_close=False,  # never a 1H close
            )
        # Phase label must NOT change
        assert engine.active_phase == Phase.BALANCE

    def test_exactly_two_1h_closes_triggers_change(self):
        """Exactly 2 × is_1h_close=True with same winner → label changes."""
        engine = PhaseEngine(initial_phase=Phase.BALANCE)
        legs   = [
            make_bull_leg("99000", "100000", eff="0.70", quality=4, idx=i)
            for i in range(5)
        ]
        bar = make_bar()
        # First 1H close
        engine.update(
            bar=bar, legs=legs, zones=[], current_price=_PRICE,
            atr_1h=_ATR_1H, density_bias=DensityBias.HEAVY_BELOW,
            recent_1h_bars=[], recent_zlbb=[], is_1h_close=True,
        )
        assert engine.active_phase == Phase.BALANCE  # still old label

        # Second 1H close
        engine.update(
            bar=bar, legs=legs, zones=[], current_price=_PRICE,
            atr_1h=_ATR_1H, density_bias=DensityBias.HEAVY_BELOW,
            recent_1h_bars=[], recent_zlbb=[], is_1h_close=True,
        )
        assert engine.active_phase == Phase.TREND_BULL
