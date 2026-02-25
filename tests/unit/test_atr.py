"""Tests for src/indicators/atr.py — all four acceptance criteria."""

import datetime
from decimal import Decimal

import pytest

from src.data_ingest.bar_aggregator import AggregatedBar
from src.indicators.atr import ATRCalculator

UTC = datetime.timezone.utc
PREC = Decimal("0.000001")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_bar(
    hour_offset: int,
    h: float,
    l: float,
    c: float,
    o: float | None = None,
    base_ts: datetime.datetime | None = None,
    symbol: str = "BTCUSDT",
    tf: str = "1H",
    is_complete: bool = True,
) -> AggregatedBar:
    if base_ts is None:
        base_ts = datetime.datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
    ts = base_ts + datetime.timedelta(hours=hour_offset)
    open_price = o if o is not None else c
    return AggregatedBar(
        symbol=symbol,
        timestamp_start=ts,
        timestamp_end=ts + datetime.timedelta(hours=1) - datetime.timedelta(microseconds=1),
        timeframe=tf,
        open=Decimal(str(open_price)),
        high=Decimal(str(h)),
        low=Decimal(str(l)),
        close=Decimal(str(c)),
        volume=Decimal("100"),
        is_complete=is_complete,
        is_reliable=True,
    )


def prime_calculator(
    n_bars: int = 14,
    symbol: str = "BTCUSDT",
    tf: str = "1H",
    is_continuous: bool = True,
    h: float = 110,
    l: float = 90,
    c: float = 100,
) -> ATRCalculator:
    """Return an ATRCalculator primed with n_bars of identical bars."""
    calc = ATRCalculator(symbol=symbol, timeframe=tf, is_continuous=is_continuous)
    for i in range(n_bars):
        calc.push(make_bar(i, h=h, l=l, c=c, symbol=symbol, tf=tf))
    return calc


# ---------------------------------------------------------------------------
# AC1: first 13 bars → None, 14th → valid Decimal ATR
# ---------------------------------------------------------------------------

class TestSeedPhase:
    def test_first_bar_returns_none(self):
        """Bar 1 has no prev_close — cannot compute TR."""
        calc = ATRCalculator("BTCUSDT", "1H")
        result = calc.push(make_bar(0, h=110, l=90, c=100))
        assert result is None

    def test_bars_2_to_13_return_none(self):
        """Bars 2–13 are in seed phase — 13 TRs collected, mean not yet triggered."""
        calc = ATRCalculator("BTCUSDT", "1H")
        for i in range(13):
            result = calc.push(make_bar(i, h=110 + i, l=90 + i, c=100 + i))
        assert result is None

    def test_bar_14_returns_decimal_atr(self):
        """Bar 14 completes the 13-TR seed and emits first ATR."""
        calc = ATRCalculator("BTCUSDT", "1H")
        result = None
        for i in range(14):
            result = calc.push(make_bar(i, h=110 + i, l=90 + i, c=100 + i))
        assert result is not None
        assert isinstance(result, Decimal)
        assert result > Decimal("0")

    def test_atr_has_six_decimal_places(self):
        """Doc 2 §7.5: ATR stored with 6 decimal places."""
        calc = prime_calculator(14)
        atr = calc.current_atr
        assert atr is not None
        assert atr == atr.quantize(PREC)

    def test_seed_simple_mean_flat_market(self):
        """Doc 2 §7.2: seed ATR = simple mean of first 14 TRs.

        Flat market: TR = high - low = 20 every bar (bar 1 uses h-l only;
        bars 2-14 use full TR formula, all = 20).  Seed mean = 20.
        """
        calc = ATRCalculator("BTCUSDT", "1H")
        result = None
        # Bars 1–14: bar 1 TR = h-l = 20; bars 2-14 TR = 20 (flat close=100)
        for i in range(14):
            result = calc.push(make_bar(i, h=110, l=90, c=100))
        expected = Decimal("20").quantize(PREC)
        assert result == expected

    def test_wilder_smoothing_after_seed(self):
        """Doc 2 §7.3: ATR[i] = (ATR[i-1] × 13 + TR[i]) / 14."""
        calc = ATRCalculator("BTCUSDT", "1H")
        # Seed with TR=20 each bar (flat market)
        calc.push(make_bar(0, h=110, l=90, c=100))
        for i in range(1, 14):
            calc.push(make_bar(i, h=110, l=90, c=100))
        seed_atr = calc.current_atr  # = 20.000000

        # Bar 15: TR = 20 again → ATR unchanged
        result = calc.push(make_bar(14, h=110, l=90, c=100))
        expected = ((seed_atr * 13 + Decimal("20")) / 14).quantize(PREC)
        assert result == expected

    def test_wilder_smoothing_increased_volatility(self):
        """Wilder responds to a spike TR correctly."""
        calc = prime_calculator(14, h=110, l=90, c=100)
        seed_atr = calc.current_atr

        # Big spike bar: TR = 100 (h=200, l=100, prev_close=100)
        spike_bar = make_bar(14, h=200, l=100, c=150)
        result = calc.push(spike_bar)
        tr_spike = Decimal("100")  # max(100, |200-100|, |100-100|) = 100
        expected = ((seed_atr * 13 + tr_spike) / 14).quantize(PREC)
        assert result == expected
        assert result > seed_atr  # ATR rises with volatility


# ---------------------------------------------------------------------------
# AC2: freeze() / frozen_atr
# ---------------------------------------------------------------------------

class TestFreeze:
    def test_frozen_atr_none_before_freeze(self):
        calc = prime_calculator(14)
        assert calc.frozen_atr is None

    def test_freeze_captures_current_atr(self):
        calc = prime_calculator(14)
        atr_before = calc.current_atr
        calc.freeze()
        assert calc.frozen_atr == atr_before

    def test_frozen_atr_unchanged_after_new_bars(self):
        """AC2: frozen_atr stays fixed while current_atr updates."""
        calc = prime_calculator(14, h=110, l=90, c=100)
        calc.freeze()
        frozen = calc.frozen_atr

        # Push 5 highly volatile bars — current_atr changes, frozen must not
        for i in range(14, 19):
            calc.push(make_bar(i, h=500 + i * 10, l=100, c=300))

        assert calc.frozen_atr == frozen
        assert calc.current_atr != frozen

    def test_freeze_before_ready_captures_none(self):
        """freeze() before 14 bars → frozen_atr = None."""
        calc = ATRCalculator("BTCUSDT", "1H")
        calc.push(make_bar(0, h=110, l=90, c=100))
        calc.freeze()
        assert calc.frozen_atr is None

    def test_unfreeze_clears_snapshot(self):
        calc = prime_calculator(14)
        calc.freeze()
        assert calc.frozen_atr is not None
        calc.unfreeze()
        assert calc.frozen_atr is None

    def test_freeze_overwrites_previous_freeze(self):
        """Calling freeze() twice updates the snapshot."""
        calc = prime_calculator(14, h=110, l=90, c=100)
        calc.freeze()
        first_frozen = calc.frozen_atr

        # Add a volatile bar to change current_atr
        calc.push(make_bar(14, h=500, l=100, c=300))
        calc.freeze()  # snapshot the new value

        assert calc.frozen_atr != first_frozen
        assert calc.frozen_atr == calc.current_atr


# ---------------------------------------------------------------------------
# AC3: Gold gap-aware TR
# ---------------------------------------------------------------------------

class TestGoldGapAwareTR:
    """Amendment v1.1 C5: first bar after session gap uses last_real_close."""

    def _make_gold_bar(
        self,
        hour_offset: int,
        h: float,
        l: float,
        c: float,
        base_ts: datetime.datetime | None = None,
    ) -> AggregatedBar:
        if base_ts is None:
            base_ts = datetime.datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
        ts = base_ts + datetime.timedelta(hours=hour_offset)
        return AggregatedBar(
            symbol="XAUUSD",
            timestamp_start=ts,
            timestamp_end=ts + datetime.timedelta(hours=1) - datetime.timedelta(microseconds=1),
            timeframe="1H",
            open=Decimal(str(c)),
            high=Decimal(str(h)),
            low=Decimal(str(l)),
            close=Decimal(str(c)),
            volume=Decimal("10"),
            is_complete=True,
            is_reliable=True,
        )

    def test_gap_bar_uses_last_real_close(self):
        """AC3: Gold ATR uses last_real_close for TR on first bar after gap."""
        calc = ATRCalculator("XAUUSD", "1H", is_continuous=False)

        # 14 contiguous hours, all TR=10 (h=2010, l=2000, c=2000)
        for i in range(14):
            r = calc.push(self._make_gold_bar(i, h=2010, l=2000, c=2000))
        assert r is not None
        seed_atr = r  # = 10.000000
        last_real_close = Decimal("2000")

        # 48-hour gap (weekend). First real bar after gap: open=2050, h=2060, l=2045, c=2050
        gap_bar = self._make_gold_bar(62, h=2060, l=2045, c=2050)
        result = calc.push(gap_bar)

        # TR with last_real_close=2000:
        #   h-l       = 2060 - 2045 = 15
        #   |h-prev|  = |2060 - 2000| = 60
        #   |l-prev|  = |2045 - 2000| = 45
        #   TR = 60
        expected_tr = Decimal("60")
        expected_atr = ((seed_atr * 13 + expected_tr) / 14).quantize(PREC)
        assert result == expected_atr

    def test_no_gap_uses_prev_close(self):
        """Contiguous Gold bars use prev_close normally (no gap detection)."""
        calc = ATRCalculator("XAUUSD", "1H", is_continuous=False)

        # 14 flat bars, close=2000
        for i in range(14):
            calc.push(self._make_gold_bar(i, h=2010, l=2000, c=2000))
        seed_atr = calc.current_atr

        # Next bar immediately follows (hour 14), no gap
        next_bar = self._make_gold_bar(14, h=2015, l=2005, c=2010)
        result = calc.push(next_bar)

        # TR = max(10, |2015-2000|, |2005-2000|) = max(10, 15, 5) = 15
        expected_atr = ((seed_atr * 13 + Decimal("15")) / 14).quantize(PREC)
        assert result == expected_atr

    def test_btc_ignores_timestamp_gap(self):
        """BTC is_continuous=True: never uses gap-aware TR even if timestamp jumps."""
        calc = ATRCalculator("BTCUSDT", "1H", is_continuous=True)
        for i in range(14):
            calc.push(make_bar(i, h=110, l=90, c=100))
        seed_atr = calc.current_atr

        # Simulate a timestamp jump (unrealistic for BTC, but tests the flag)
        gap_bar = make_bar(100, h=160, l=140, c=150)  # far in future
        result = calc.push(gap_bar)

        # For BTC: prev_close=100, TR = max(20, |160-100|, |140-100|) = max(20,60,40) = 60
        expected_tr = Decimal("60")
        expected_atr = ((seed_atr * 13 + expected_tr) / 14).quantize(PREC)
        assert result == expected_atr


# ---------------------------------------------------------------------------
# AC4: No float anywhere
# ---------------------------------------------------------------------------

class TestNoFloat:
    def test_all_return_values_are_decimal(self):
        calc = prime_calculator(20)
        assert isinstance(calc.current_atr, Decimal)

    def test_frozen_atr_is_decimal(self):
        calc = prime_calculator(14)
        calc.freeze()
        assert isinstance(calc.frozen_atr, Decimal)

    def test_no_float_in_source(self):
        """float() must not appear anywhere in atr.py."""
        with open("src/indicators/atr.py") as f:
            src = f.read()
        float_uses = [
            line.strip()
            for line in src.splitlines()
            if "float(" in line
        ]
        assert float_uses == [], f"float() found in source: {float_uses}"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_incomplete_bar_returns_none(self):
        """Doc 2 §6: incomplete bars are invisible to structure engines."""
        calc = ATRCalculator("BTCUSDT", "1H")
        bar = make_bar(0, h=110, l=90, c=100, is_complete=False)
        assert calc.push(bar) is None

    def test_is_ready_false_before_14_bars(self):
        calc = ATRCalculator("BTCUSDT", "1H")
        for i in range(13):
            calc.push(make_bar(i, h=110, l=90, c=100))
        assert calc.is_ready is False

    def test_is_ready_true_after_14_bars(self):
        calc = prime_calculator(14)
        assert calc.is_ready is True

    def test_current_atr_none_before_ready(self):
        calc = ATRCalculator("BTCUSDT", "1H")
        assert calc.current_atr is None

    def test_monotone_convergence_in_flat_market(self):
        """Wilder ATR is stable in a flat market — no drift."""
        calc = prime_calculator(14, h=110, l=90, c=100)
        prev = calc.current_atr
        for i in range(14, 100):
            result = calc.push(make_bar(i, h=110, l=90, c=100))
            assert result == prev  # flat TR=20, ATR stays at 20.000000
            prev = result
