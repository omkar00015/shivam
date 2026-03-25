"""Tests for zone_detector.py bug fixes.

Fix A: source_bonus accumulation cap
Fix B: lifecycle state preservation through clustering
Fix C: BROKEN_CONFIRMED -> FLIPPED transition verification

All tests use Decimal for prices and UTC datetimes.
"""

import datetime
from datetime import timezone
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
    _finalize_cluster,
    _LIFECYCLE_PRIORITY,
    _MAX_SOURCE_BONUS_TOTAL,
)

UTC = datetime.timezone.utc
BASE_TS = datetime.datetime(2026, 3, 1, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_atr(value: str, symbol: str = "BTCUSDT", tf: str = "15m") -> ATRCalculator:
    """Create an ATRCalculator with a pre-seeded current_atr value."""
    calc = ATRCalculator(symbol=symbol, timeframe=tf, is_continuous=True)
    calc._current_atr = Decimal(value)
    return calc


def _make_bar(
    ts_minutes_offset: int,
    open_: Decimal,
    high: Decimal,
    low: Decimal,
    close: Decimal,
    volume: Decimal = Decimal("100"),
) -> AggregatedBar:
    """Create a synthetic 15m bar at a given minute offset from epoch."""
    ts_start = BASE_TS + datetime.timedelta(minutes=ts_minutes_offset)
    ts_end = ts_start + datetime.timedelta(minutes=15) - datetime.timedelta(microseconds=1)
    return AggregatedBar(
        symbol="BTCUSDT",
        timestamp_start=ts_start,
        timestamp_end=ts_end,
        timeframe="15m",
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        is_complete=True,
        is_reliable=True,
    )


def _make_detector(atr_value: str = "200") -> ZoneDetector:
    """Create a ZoneDetector with fixed ATR calculators."""
    return ZoneDetector(
        symbol="BTCUSDT",
        sr_timeframe="1H",
        atr_1h=_make_atr(atr_value, tf="1H"),
        atr_15m=_make_atr(atr_value, tf="15m"),
        atr_4h=_make_atr(atr_value, tf="4H"),
    )


def _make_zone(
    center: Decimal,
    polarity: ZonePolarity = ZonePolarity.SUPPORT,
    state: ZoneState = ZoneState.FRESH,
    base_strength: int = 5,
    source_bonus: int = 0,
    displacement: Decimal = Decimal("500"),
    touch_count: int = 0,
    frozen_touch_bonus: int = 0,
    confirmed_at: datetime.datetime | None = None,
    bars_since_confirmed: int = 0,
    break_direction: str | None = None,
) -> _ZoneMutable:
    """Create a _ZoneMutable for test injection into a detector."""
    import hashlib
    zone_id = hashlib.sha256(
        f"{center}|{polarity.name}|1H|{BASE_TS.isoformat()}".encode()
    ).hexdigest()[:16]
    return _ZoneMutable(
        zone_id=zone_id,
        center=center,
        zone_high=center + Decimal("100"),
        zone_low=center - Decimal("100"),
        polarity=polarity,
        origin=ZoneOrigin.LEG_EXTREME,
        timeframe="1H",
        is_midpoint=False,
        base_strength=base_strength,
        source_bonus=source_bonus,
        displacement=displacement,
        state=state,
        created_at=BASE_TS,
        touch_count=touch_count,
        frozen_touch_bonus=frozen_touch_bonus,
        confirmed_at=confirmed_at,
        bars_since_confirmed=bars_since_confirmed,
        break_direction=break_direction,
    )


# ---------------------------------------------------------------------------
# Test 1: Fix A — source_bonus is capped
# ---------------------------------------------------------------------------

class TestSourceBonusCap:
    """Fix A: source_bonus never exceeds _MAX_SOURCE_BONUS_TOTAL (6)."""

    def test_source_bonus_capped_at_max(self) -> None:
        """Feed 100+ bars with OC clusters near the same price;
        verify no zone's source_bonus exceeds 6."""
        detector = _make_detector("200")
        price = Decimal("67000")

        # Generate 120 bars with opens and closes clustered tightly around `price`
        for i in range(120):
            bar = _make_bar(
                ts_minutes_offset=i * 15,
                open_=price - Decimal("5"),
                high=price + Decimal("50"),
                low=price - Decimal("50"),
                close=price + Decimal("5"),
            )
            detector.update(bar, completed_leg=None)

        # Assert: all zones have source_bonus <= _MAX_SOURCE_BONUS_TOTAL
        for z in detector._zones:
            assert z.source_bonus <= _MAX_SOURCE_BONUS_TOTAL, (
                f"Zone {z.zone_id} source_bonus={z.source_bonus} exceeds cap {_MAX_SOURCE_BONUS_TOTAL}"
            )
            # With capped bonus, total strength should be reasonable (< 50)
            assert z.total_strength() < 50, (
                f"Zone {z.zone_id} total_strength={z.total_strength()} is unreasonably high"
            )

    def test_source_bonus_per_register_call(self) -> None:
        """Directly verify the cap logic in _register_oc_zone by injecting
        a zone with source_bonus already at cap."""
        detector = _make_detector("200")
        zone = _make_zone(
            center=Decimal("67000"),
            polarity=ZonePolarity.SUPPORT,
            source_bonus=_MAX_SOURCE_BONUS_TOTAL,  # already at cap
        )
        detector._zones.append(zone)

        # Call _register_oc_zone near the same center — should NOT increase bonus
        detector._register_oc_zone(
            center=Decimal("67005"),
            polarity=ZonePolarity.SUPPORT,
            timestamp=BASE_TS + datetime.timedelta(hours=1),
            native_atr=Decimal("200"),
        )
        assert zone.source_bonus == _MAX_SOURCE_BONUS_TOTAL


# ---------------------------------------------------------------------------
# Test 2: Fix B — FLIPPED state survives clustering
# ---------------------------------------------------------------------------

class TestFlippedSurvivesClustering:
    """Fix B: A FLIPPED zone is not overwritten by a FRESH zone during clustering."""

    def test_flipped_state_survives_rebuild(self) -> None:
        """Inject a FLIPPED zone; feed a bar that creates a FRESH zone
        near the same price. The surviving zone must retain FLIPPED state."""
        detector = _make_detector("200")

        # Create a FLIPPED zone at 67000
        flipped_zone = _make_zone(
            center=Decimal("67000"),
            polarity=ZonePolarity.RESISTANCE,
            state=ZoneState.FLIPPED,
            base_strength=5,
            source_bonus=2,
            touch_count=2,
            frozen_touch_bonus=3,
        )
        detector._zones.append(flipped_zone)

        # Feed a bar with open/close near 67000 that will create FRESH OC zones
        # The OC detection needs >= 3 recent bars to detect clusters
        for i in range(5):
            bar = _make_bar(
                ts_minutes_offset=i * 15,
                open_=Decimal("67005"),
                high=Decimal("67100"),
                low=Decimal("66900"),
                close=Decimal("66995"),
            )
            detector.update(bar, completed_leg=None)

        # After clustering, the zone at ~67000 must still be FLIPPED
        flipped_zones = [z for z in detector._zones if z.state == ZoneState.FLIPPED]
        assert len(flipped_zones) >= 1, (
            f"Expected at least 1 FLIPPED zone, got {len(flipped_zones)}. "
            f"States: {[z.state.name for z in detector._zones]}"
        )

    def test_finalize_cluster_prefers_flipped_over_fresh(self) -> None:
        """Direct test of _finalize_cluster: FLIPPED zone beats FRESH
        even with lower total_strength."""
        flipped = _make_zone(
            center=Decimal("67000"),
            polarity=ZonePolarity.RESISTANCE,
            state=ZoneState.FLIPPED,
            base_strength=3,
            source_bonus=0,
        )
        fresh = _make_zone(
            center=Decimal("67010"),
            polarity=ZonePolarity.RESISTANCE,
            state=ZoneState.FRESH,
            base_strength=10,
            source_bonus=6,
        )
        result = _finalize_cluster([flipped, fresh])
        assert result.state == ZoneState.FLIPPED, (
            f"Expected FLIPPED template, got {result.state.name}"
        )


# ---------------------------------------------------------------------------
# Test 3: Fix C — BROKEN_CONFIRMED transitions to FLIPPED
# ---------------------------------------------------------------------------

class TestBrokenConfirmedToFlipped:
    """Fix C: Verify BROKEN_CONFIRMED -> FLIPPED happens after 1 additional bar (P2)."""

    def test_broken_confirmed_transitions_to_flipped(self) -> None:
        """Create a BROKEN_CONFIRMED zone with bars_since_confirmed=0.
        Feed one bar. Zone must become FLIPPED with inverted polarity."""
        detector = _make_detector("200")

        zone = _make_zone(
            center=Decimal("67000"),
            polarity=ZonePolarity.SUPPORT,
            state=ZoneState.BROKEN_CONFIRMED,
            base_strength=5,
            touch_count=2,
            confirmed_at=BASE_TS,
            bars_since_confirmed=0,
            break_direction="down",
        )
        detector._zones.append(zone)

        # Feed one bar — any bar, doesn't need to touch zone
        bar = _make_bar(
            ts_minutes_offset=15,
            open_=Decimal("68000"),
            high=Decimal("68100"),
            low=Decimal("67900"),
            close=Decimal("68050"),
        )
        detector.update(bar, completed_leg=None)

        # Zone should now be FLIPPED with inverted polarity
        assert zone.state == ZoneState.FLIPPED, (
            f"Expected FLIPPED, got {zone.state.name}"
        )
        assert zone.polarity == ZonePolarity.RESISTANCE, (
            f"Expected polarity inverted to RESISTANCE, got {zone.polarity.name}"
        )


# ---------------------------------------------------------------------------
# Test 4: Full lifecycle regression — FRESH -> ... -> FLIPPED
# ---------------------------------------------------------------------------

class TestFullLifecycleToFlipped:
    """Regression: Zone goes through complete lifecycle to FLIPPED state."""

    def test_full_lifecycle_to_flipped(self) -> None:
        """Create a SUPPORT zone at 67000. Feed 4 consecutive bars closing below
        zone_low (triggering breakout). Verify BROKEN_CONFIRMED. Feed 1 more bar.
        Verify FLIPPED with inverted polarity (now RESISTANCE).

        Zone width note: with a single zone and ATR(15m)=200, the half_width
        is 1.5 × ATR = 300, so zone_low = 67000 - 300 = 66700. Breakout
        epsilon = 0.02 × 200 = 4, so closes must be below 66696.
        """
        detector = _make_detector("200")

        zone = _make_zone(
            center=Decimal("67000"),
            polarity=ZonePolarity.SUPPORT,
            state=ZoneState.TESTED,
            base_strength=5,
            touch_count=1,
        )
        detector._zones.append(zone)

        # Zone width recalc: half_width = 1.5 × ATR(15m) = 1.5 × 200 = 300
        # zone_low = 67000 - 300 = 66700
        # Breakout epsilon = 0.02 × ATR(15m) = 0.02 × 200 = 4
        # Closes must be below zone_low - breakout_epsilon = 66700 - 4 = 66696

        # 4 consecutive bars closing below boundary → BROKEN_CONFIRMED
        for i in range(4):
            bar = _make_bar(
                ts_minutes_offset=i * 15,
                open_=Decimal("66650"),
                high=Decimal("66680"),
                low=Decimal("66580"),
                close=Decimal("66600"),  # well below 66696
            )
            detector.update(bar, completed_leg=None)

        assert zone.state == ZoneState.BROKEN_CONFIRMED, (
            f"Expected BROKEN_CONFIRMED after 4 closes, got {zone.state.name}"
        )

        # 1 more bar → FLIPPED (P2: persist >= 1 bar before FLIPPED)
        flip_bar = _make_bar(
            ts_minutes_offset=60,
            open_=Decimal("66620"),
            high=Decimal("66660"),
            low=Decimal("66570"),
            close=Decimal("66630"),
        )
        detector.update(flip_bar, completed_leg=None)

        assert zone.state == ZoneState.FLIPPED, (
            f"Expected FLIPPED after persistence bar, got {zone.state.name}"
        )
        assert zone.polarity == ZonePolarity.RESISTANCE, (
            f"Expected inverted polarity RESISTANCE, got {zone.polarity.name}"
        )


# ---------------------------------------------------------------------------
# Test 5: Existing pipeline doesn't break — basic zone creation
# ---------------------------------------------------------------------------

class TestExistingZoneDetection:
    """Regression: Basic zone creation from leg extremes still works."""

    def test_leg_extreme_creates_zone(self) -> None:
        """Feed a CompletedLeg (bull, quality=3, displacement=500).
        Verify a RESISTANCE zone is created at the leg's end_price
        with base_strength = 2 + quality = 5."""
        detector = _make_detector("200")

        leg = CompletedLeg(
            direction=LegDirection.BULL,
            start_price=Decimal("66500"),
            end_price=Decimal("67000"),
            displacement=Decimal("500"),
            bar_count=8,
            efficiency=Decimal("0.45"),
            cumulative_range=Decimal("1111"),
            quality=3,
            atr_at_start=Decimal("200"),
            timestamp_start=BASE_TS,
            timestamp_end=BASE_TS + datetime.timedelta(hours=2),
            is_band_walk=False,
        )

        bar = _make_bar(
            ts_minutes_offset=0,
            open_=Decimal("66900"),
            high=Decimal("67100"),
            low=Decimal("66800"),
            close=Decimal("67000"),
        )
        detector.update(bar, completed_leg=leg)

        # Find the leg-extreme zone
        leg_zones = [
            z for z in detector._zones
            if z.origin == ZoneOrigin.LEG_EXTREME
            and z.polarity == ZonePolarity.RESISTANCE
        ]
        assert len(leg_zones) >= 1, "Expected at least 1 RESISTANCE zone from bull leg"

        z = leg_zones[0]
        assert z.base_strength == 5, (  # 2 + quality(3) = 5
            f"Expected base_strength=5, got {z.base_strength}"
        )
        assert z.state in (ZoneState.FRESH, ZoneState.TESTED), (
            f"Expected FRESH or TESTED, got {z.state.name}"
        )


# ---------------------------------------------------------------------------
# Test: _LIFECYCLE_PRIORITY and _MAX_SOURCE_BONUS_TOTAL are importable
# ---------------------------------------------------------------------------

class TestConstantsExportable:
    """Verify constants added for Fix A/B are publicly importable."""

    def test_lifecycle_priority_dict(self) -> None:
        assert _LIFECYCLE_PRIORITY[ZoneState.FLIPPED] == 6
        assert _LIFECYCLE_PRIORITY[ZoneState.FRESH] == 2
        assert _LIFECYCLE_PRIORITY[ZoneState.EXPIRED] == 0

    def test_max_source_bonus_total(self) -> None:
        assert _MAX_SOURCE_BONUS_TOTAL == 6
