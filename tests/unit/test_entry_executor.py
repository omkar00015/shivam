"""Tests for src/execution/entry_executor.py.

Acceptance criteria:
  AC1: Entry fires only on 15m bar CLOSE (never intrabar). Long: close >= entry - EPSILON.
  AC2: Gap rule — if open already past entry level → fill at bar.close (pessimistic).
  AC3: Risk gate rejects if R:R < min, stop_dist > 1.5×ATR, portfolio_risk > 6%, or system_paused.
  AC4: Position sizing: size = floor(equity × 1% / stop_distance).
  AC5: Trade object has all-Decimal price fields; trade_id is deterministic sha256.
  AC6: Trade management — breakeven fires at +1R; phase flip → EXIT; opposite leg → EXIT.
  AC7: Tie-breaking — multiple candidates same bar sorted by HTF > strength > smaller_stop > R > time > id.
  AC8: No float anywhere in Trade, TradeAction, or any arithmetic path.
"""

from __future__ import annotations

import datetime
import hashlib
from decimal import Decimal
from typing import Optional

import pytest

from src.data_ingest.bar_aggregator import AggregatedBar
from src.execution.entry_executor import (
    EntryExecutor,
    PortfolioState,
    RejectionReason,
    Trade,
    TradeAction,
    TradeActionType,
    _actual_fill_price,
    _bar_ohlcv_valid,
    _compute_portfolio_risk,
    _compute_stop_distance,
    _entry_triggered,
    _is_phase_compatible,
    _make_trade_id,
    _min_r_for_setup,
    _zone_already_traded,
    _zone_timeframe_from_id,
)
from src.indicators.zlbb import CompletedLeg, LegDirection
from src.phase.phase_engine import Phase, PhaseResult
from src.setup.setup_engine import (
    CandidateStatus,
    Direction,
    SetupCandidate,
    SetupType,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_UTC = datetime.timezone.utc
_T0 = datetime.datetime(2024, 1, 15, 12, 0, 0, tzinfo=_UTC)
_T1 = _T0 + datetime.timedelta(minutes=15)
_T2 = _T1 + datetime.timedelta(minutes=15)
_T3 = _T2 + datetime.timedelta(minutes=15)


def _bar(
    close: str,
    high: Optional[str] = None,
    low: Optional[str] = None,
    open_: Optional[str] = None,
    ts: datetime.datetime = _T1,
    is_complete: bool = True,
) -> AggregatedBar:
    """Build an AggregatedBar with Decimal prices."""
    c = Decimal(close)
    h = Decimal(high)  if high  else c + Decimal("50")
    l = Decimal(low)   if low   else c - Decimal("50")
    o = Decimal(open_) if open_ else c
    return AggregatedBar(
        symbol="BTCUSDT",
        timestamp_start=ts - datetime.timedelta(minutes=15),
        timestamp_end=ts,
        timeframe="15m",
        open=o,
        high=h,
        low=l,
        close=c,
        volume=Decimal("100"),
        is_complete=is_complete,
        is_reliable=True,
    )


def _candidate(
    entry: str,
    stop: str,
    target: str,
    direction: Direction = Direction.LONG,
    setup_type: SetupType = SetupType.PULLBACK_CONTINUATION,
    zone_id: str = "1H_zone001",
    created_at: datetime.datetime = _T0,
    expected_r: Optional[str] = None,
) -> SetupCandidate:
    """Build a SetupCandidate."""
    e = Decimal(entry)
    s = Decimal(stop)
    t = Decimal(target)
    stop_dist   = abs(e - s)
    target_dist = abs(t - e)
    er = Decimal(expected_r) if expected_r else (
        (target_dist / stop_dist) if stop_dist > Decimal("1E-9") else Decimal("0")
    )
    # Make deterministic candidate_id
    content = f"{setup_type.name}|{direction.name}|{zone_id}|{created_at.isoformat()}"
    cid = hashlib.sha256(content.encode()).hexdigest()[:16]
    return SetupCandidate(
        candidate_id=cid,
        setup_type=setup_type,
        direction=direction,
        zone_id=zone_id,
        entry_price=e,
        stop_price=s,
        target_price=t,
        expected_R=er,
        created_at=created_at,
        expiry_bars=5,
    )


def _portfolio(
    equity: str = "10000",
    open_positions: Optional[list] = None,
    paused: bool = False,
) -> PortfolioState:
    return PortfolioState(
        equity=Decimal(equity),
        open_positions=open_positions or [],
        system_paused=paused,
    )


def _phase_result(phase: Phase = Phase.TREND_BULL) -> PhaseResult:
    return PhaseResult(
        active_phase=phase,
        phase_confidence=Decimal("0.8"),
        transition_flag=False,
        size_reduction=Decimal("1.0"),
        all_scores={p: 0 for p in Phase},
    )


def _leg(direction: LegDirection = LegDirection.BEAR) -> CompletedLeg:
    """Build a minimal CompletedLeg for structure-failure testing.

    Matches CompletedLeg fields from src/indicators/zlbb.py:
      direction, start_price, end_price, displacement, bar_count,
      efficiency, cumulative_range, quality, atr_at_start,
      timestamp_start, timestamp_end, is_band_walk
    """
    return CompletedLeg(
        direction=direction,
        start_price=Decimal("100000"),
        end_price=Decimal("98000"),
        displacement=Decimal("2000"),
        bar_count=8,
        efficiency=Decimal("0.6"),
        cumulative_range=Decimal("3000"),
        quality=2,
        atr_at_start=Decimal("500"),
        timestamp_start=_T0,
        timestamp_end=_T1,
        is_band_walk=False,
    )


# ---------------------------------------------------------------------------
# AC1: Entry trigger law — close must reach entry_price on bar close
# ---------------------------------------------------------------------------

class TestEntryTriggerLaw:
    """AC1: Entry fires only on CLOSE, never intrabar."""

    def test_long_entry_close_exactly_at_entry(self) -> None:
        """Long triggers when close >= entry_price - EPSILON."""
        cand = _candidate(entry="100000", stop="99500", target="102000")
        bar = _bar(close="100000", high="100100", low="99600")
        assert _entry_triggered(bar, cand) is True

    def test_long_entry_close_above_entry(self) -> None:
        """Long triggers when close > entry."""
        cand = _candidate(entry="100000", stop="99500", target="102000")
        bar = _bar(close="100100", high="100200", low="99800")
        assert _entry_triggered(bar, cand) is True

    def test_long_entry_close_below_entry_no_trigger(self) -> None:
        """Long does NOT trigger when close < entry_price - EPSILON."""
        cand = _candidate(entry="100000", stop="99500", target="102000")
        bar = _bar(close="99990", high="100050", low="99800")
        assert _entry_triggered(bar, cand) is False

    def test_short_entry_close_exactly_at_entry(self) -> None:
        """Short triggers when close <= entry_price + EPSILON."""
        cand = _candidate(
            entry="100000", stop="100500", target="98000",
            direction=Direction.SHORT,
        )
        bar = _bar(close="100000", high="100200", low="99800")
        assert _entry_triggered(bar, cand) is True

    def test_short_entry_close_below_entry(self) -> None:
        """Short triggers when close < entry."""
        cand = _candidate(
            entry="100000", stop="100500", target="98000",
            direction=Direction.SHORT,
        )
        bar = _bar(close="99900", high="100100", low="99700")
        assert _entry_triggered(bar, cand) is True

    def test_short_entry_close_above_entry_no_trigger(self) -> None:
        """Short does NOT trigger when close > entry_price + EPSILON."""
        cand = _candidate(
            entry="100000", stop="100500", target="98000",
            direction=Direction.SHORT,
        )
        bar = _bar(close="100100", high="100300", low="99900")
        assert _entry_triggered(bar, cand) is False

    def test_high_touching_entry_does_not_trigger(self) -> None:
        """A bar whose HIGH touches entry but CLOSE does not must NOT trigger.
        This ensures no intrabar execution (Doc 7 §3.2).
        """
        cand = _candidate(entry="100000", stop="99500", target="102000")
        # high = 100200 (touches/exceeds entry), close = 99900 (below entry)
        bar = _bar(close="99900", high="100200", low="99700")
        assert _entry_triggered(bar, cand) is False

    def test_incomplete_bar_never_triggers(self) -> None:
        """Entry MUST NOT be attempted on an incomplete bar."""
        cand = _candidate(entry="100000", stop="99500", target="102000")
        executor = EntryExecutor("BTCUSDT")
        bar = _bar(close="100100", is_complete=False)
        trades = executor.process_bar(
            bar_15m=bar,
            active_candidates=[cand],
            atr_15m=Decimal("300"),
            phase_result=_phase_result(),
            portfolio=_portfolio(),
        )
        assert trades == []


# ---------------------------------------------------------------------------
# AC2: Gap rule — pessimistic fill at bar.close
# ---------------------------------------------------------------------------

class TestGapRule:
    """AC2: If open already past entry → fill at bar.close (pessimistic)."""

    def test_long_gap_fill_at_bar_close(self) -> None:
        """Long: open > entry_price → gap through. Fill at bar.close."""
        cand = _candidate(entry="100000", stop="99500", target="103000")
        # Open is above entry (gapped) — close is also above entry
        bar = _bar(close="100300", open_="100200", high="100400", low="100100")
        # Entry triggered (open > entry → gap rule)
        assert _entry_triggered(bar, cand) is True
        # Pessimistic fill is bar.close
        fill = _actual_fill_price(bar, cand)
        assert fill == Decimal("100300")

    def test_short_gap_fill_at_bar_close(self) -> None:
        """Short: open < entry_price → gap through. Fill at bar.close."""
        cand = _candidate(
            entry="100000", stop="100500", target="97000",
            direction=Direction.SHORT,
        )
        bar = _bar(close="99800", open_="99900", high="100050", low="99700")
        assert _entry_triggered(bar, cand) is True
        fill = _actual_fill_price(bar, cand)
        assert fill == Decimal("99800")

    def test_non_gap_fill_at_bar_close(self) -> None:
        """Non-gap long: fill is also bar.close (pessimistic model)."""
        cand = _candidate(entry="100000", stop="99500", target="103000")
        bar = _bar(close="100050", open_="99900", high="100200", low="99800")
        fill = _actual_fill_price(bar, cand)
        assert fill == Decimal("100050")

    def test_gap_fill_uses_close_not_open(self) -> None:
        """Fill must be bar.close, not bar.open, even on gap."""
        cand = _candidate(entry="100000", stop="99500", target="103000")
        # Gap: open = 100100 (already above entry 100000), close = 100200
        bar = _bar(close="100200", open_="100100", high="100300", low="100050")
        fill = _actual_fill_price(bar, cand)
        # Must be close (100200), not open (100100)
        assert fill == Decimal("100200")
        assert fill != bar.open


# ---------------------------------------------------------------------------
# AC3: Risk gate — four rejection conditions
# ---------------------------------------------------------------------------

class TestRiskGate:
    """AC3: Risk gate rejects on insufficient R:R, wide stop, portfolio risk, or paused."""

    def test_rr_below_trend_minimum_rejected(self) -> None:
        """R:R < 2.0 for TREND setup → rejected."""
        # entry=100000, stop=99000 → stop_dist=1000; target=101500 → target_dist=1500 → R=1.5
        cand = _candidate(
            entry="100000", stop="99000", target="101500",
            setup_type=SetupType.PULLBACK_CONTINUATION,
        )
        assert cand.expected_R == Decimal("1.5")
        executor = EntryExecutor("BTCUSDT")
        bar = _bar(close="100000", high="100200", low="99700")
        trades = executor.process_bar(
            bar_15m=bar,
            active_candidates=[cand],
            atr_15m=Decimal("500"),
            phase_result=_phase_result(Phase.TREND_BULL),
            portfolio=_portfolio("100000"),
        )
        assert trades == []

    def test_rr_below_balance_minimum_rejected(self) -> None:
        """R:R < 1.5 for RANGE_FADE setup → rejected."""
        # entry=100000, stop=99200 → dist=800; target=101000 → dist=1000 → R=1.25
        cand = _candidate(
            entry="100000", stop="99200", target="101000",
            setup_type=SetupType.RANGE_FADE,
        )
        assert cand.expected_R < Decimal("1.5")
        executor = EntryExecutor("BTCUSDT")
        bar = _bar(close="100000")
        trades = executor.process_bar(
            bar_15m=bar,
            active_candidates=[cand],
            atr_15m=Decimal("500"),
            phase_result=_phase_result(Phase.BALANCE),
            portfolio=_portfolio("100000"),
        )
        assert trades == []

    def test_stop_wider_than_1_5_atr_rejected(self) -> None:
        """stop_distance > 1.5 × ATR(15m) → rejected."""
        # entry=100000, stop=97000 → stop_dist=3000
        # ATR(15m) = 1500 → 1.5×ATR = 2250. 3000 > 2250 → reject.
        cand = _candidate(
            entry="100000", stop="97000", target="108000",
            setup_type=SetupType.PULLBACK_CONTINUATION,
        )
        assert cand.expected_R >= Decimal("2.0")
        executor = EntryExecutor("BTCUSDT")
        bar = _bar(close="100000", high="100200", low="99700")
        trades = executor.process_bar(
            bar_15m=bar,
            active_candidates=[cand],
            atr_15m=Decimal("1500"),  # 1.5×1500=2250 < stop_dist=3000
            phase_result=_phase_result(Phase.TREND_BULL),
            portfolio=_portfolio("100000"),
        )
        assert trades == []

    def test_portfolio_risk_exceeded_rejected(self) -> None:
        """Total portfolio risk > 6% of equity → rejected.

        Equity = 10000. 1% risk = 100. Existing 5 positions × 100 = 500 = 5%.
        Adding one more = 600 = 6% → exactly at cap (use 7 positions to exceed).
        """
        equity = Decimal("10000")
        # Build 7 fake open trades each risking 100 = 700 = 7% of 10000
        fake_trades = []
        for i in range(7):
            fake_trades.append(
                Trade(
                    trade_id=f"fake{i:03d}",
                    candidate_id=f"cand{i:03d}",
                    instrument="BTCUSDT",
                    direction=Direction.LONG,
                    setup_type=SetupType.PULLBACK_CONTINUATION,
                    entry_price_ref=Decimal("100000"),
                    entry_price_actual=Decimal("100000"),
                    stop_price=Decimal("99500"),
                    target_price=Decimal("103000"),
                    stop_distance=Decimal("500"),
                    expected_R=Decimal("6"),
                    actual_R_if_stopped=Decimal("6"),
                    position_size=Decimal("1"),
                    risk_amount=Decimal("100"),   # 1% of 10000
                    account_equity=equity,
                    zone_id=f"1H_zone{i:03d}",
                    opened_at=_T0,
                )
            )
        portfolio = PortfolioState(
            equity=equity,
            open_positions=fake_trades,
            system_paused=False,
            max_positions=20,   # high max to not block on position count
        )
        cand = _candidate(
            entry="100000", stop="99500", target="103000",
            zone_id="1H_zone999",   # unique zone
        )
        executor = EntryExecutor("BTCUSDT")
        bar = _bar(close="100000", high="100200", low="99700")
        trades = executor.process_bar(
            bar_15m=bar,
            active_candidates=[cand],
            atr_15m=Decimal("500"),
            phase_result=_phase_result(),
            portfolio=portfolio,
        )
        assert trades == []

    def test_system_paused_rejected(self) -> None:
        """system_paused=True → all entries rejected."""
        cand = _candidate(entry="100000", stop="99500", target="103000")
        executor = EntryExecutor("BTCUSDT")
        bar = _bar(close="100000")
        trades = executor.process_bar(
            bar_15m=bar,
            active_candidates=[cand],
            atr_15m=Decimal("300"),
            phase_result=_phase_result(),
            portfolio=_portfolio(paused=True),
        )
        assert trades == []

    def test_all_risk_gates_pass_trade_opens(self) -> None:
        """When all gates pass, trade is opened."""
        # entry=100000, stop=99500, target=103000 → R=6 >= 2.0
        # stop_dist=500 < 1.5×ATR(300)=450 ... actually 500>450. Use ATR=400 → 600 > 500 ✓
        cand = _candidate(entry="100000", stop="99500", target="103000")
        executor = EntryExecutor("BTCUSDT")
        bar = _bar(close="100000", high="100200", low="99700")
        trades = executor.process_bar(
            bar_15m=bar,
            active_candidates=[cand],
            atr_15m=Decimal("400"),   # 1.5×400=600 > stop_dist=500 ✓
            phase_result=_phase_result(),
            portfolio=_portfolio("100000"),
        )
        assert len(trades) == 1
        assert trades[0].status == "OPEN"

    def test_duplicate_zone_rejected(self) -> None:
        """One trade per zone per direction (Doc 7 §16) — duplicate rejected."""
        equity = Decimal("10000")
        # Existing trade on same zone + direction
        existing = Trade(
            trade_id="existing001",
            candidate_id="cand_old",
            instrument="BTCUSDT",
            direction=Direction.LONG,
            setup_type=SetupType.PULLBACK_CONTINUATION,
            entry_price_ref=Decimal("100000"),
            entry_price_actual=Decimal("100000"),
            stop_price=Decimal("99500"),
            target_price=Decimal("103000"),
            stop_distance=Decimal("500"),
            expected_R=Decimal("6"),
            actual_R_if_stopped=Decimal("6"),
            position_size=Decimal("2"),
            risk_amount=Decimal("100"),
            account_equity=equity,
            zone_id="1H_zone001",
            opened_at=_T0,
        )
        portfolio = PortfolioState(
            equity=equity,
            open_positions=[existing],
            system_paused=False,
        )
        # New candidate on same zone + direction
        cand = _candidate(entry="100050", stop="99500", target="103000", zone_id="1H_zone001")
        executor = EntryExecutor("BTCUSDT")
        bar = _bar(close="100050")
        trades = executor.process_bar(
            bar_15m=bar,
            active_candidates=[cand],
            atr_15m=Decimal("400"),
            phase_result=_phase_result(),
            portfolio=portfolio,
        )
        assert trades == []


# ---------------------------------------------------------------------------
# AC4: Position sizing
# ---------------------------------------------------------------------------

class TestPositionSizing:
    """AC4: size = floor(equity × 1% / stop_distance)."""

    def test_position_sizing_formula(self) -> None:
        """size = floor(10000 × 0.01 / 500) = floor(100/500) = floor(0.2) = 0.

        Use equity=100000, stop=500 → floor(1000/500) = floor(2.0) = 2.
        """
        executor = EntryExecutor("BTCUSDT")
        size = executor._compute_position_size(Decimal("100000"), Decimal("500"))
        assert size == Decimal("2")

    def test_position_size_floored_down(self) -> None:
        """Fractional sizes must be floored DOWN, not rounded."""
        # equity=10000, stop=700 → account_risk=100. 100/700 = 0.142... → floor = 0
        size = EntryExecutor._compute_position_size(Decimal("10000"), Decimal("700"))
        assert size == Decimal("0")

    def test_position_size_is_decimal(self) -> None:
        """Position size returned by sizing formula is a Decimal."""
        size = EntryExecutor._compute_position_size(Decimal("50000"), Decimal("250"))
        assert isinstance(size, Decimal)

    def test_position_size_zero_stop_returns_zero(self) -> None:
        """Zero stop distance → size = 0 (avoids division by zero)."""
        size = EntryExecutor._compute_position_size(Decimal("10000"), Decimal("0"))
        assert size == Decimal("0")

    def test_position_size_reflects_equity(self) -> None:
        """Larger equity → proportionally larger position size.

        equity=50000, stop=250 → floor(500/250) = 2
        equity=500000, stop=250 → floor(5000/250) = 20 = 2×10
        """
        size_small = EntryExecutor._compute_position_size(Decimal("50000"), Decimal("250"))
        size_large = EntryExecutor._compute_position_size(Decimal("500000"), Decimal("250"))
        assert size_small == Decimal("2")
        assert size_large == Decimal("20")
        assert size_large == size_small * 10

    def test_trade_size_matches_formula(self) -> None:
        """Integration: opened trade has position_size = floor(equity × 1% / stop_dist)."""
        equity = Decimal("100000")
        # entry=100000, stop=99600 → stop_dist=400
        # floor(100000*0.01/400) = floor(1000/400) = floor(2.5) = 2
        cand = _candidate(entry="100000", stop="99600", target="103200")
        executor = EntryExecutor("BTCUSDT")
        bar = _bar(close="100000", high="100200", low="99700")
        trades = executor.process_bar(
            bar_15m=bar,
            active_candidates=[cand],
            atr_15m=Decimal("400"),
            phase_result=_phase_result(),
            portfolio=_portfolio(str(equity)),
        )
        assert len(trades) == 1
        assert trades[0].position_size == Decimal("2")


# ---------------------------------------------------------------------------
# AC5: Trade object completeness and deterministic ID
# ---------------------------------------------------------------------------

class TestTradeObject:
    """AC5: Trade has all-Decimal price fields and deterministic sha256 ID."""

    def _open_one_trade(self) -> Trade:
        cand = _candidate(entry="100000", stop="99600", target="103200")
        executor = EntryExecutor("BTCUSDT")
        bar = _bar(close="100000", high="100200", low="99700")
        trades = executor.process_bar(
            bar_15m=bar,
            active_candidates=[cand],
            atr_15m=Decimal("400"),
            phase_result=_phase_result(),
            portfolio=_portfolio("100000"),
        )
        assert len(trades) == 1
        return trades[0]

    def test_all_price_fields_are_decimal(self) -> None:
        """AC8 also: No float in Trade fields."""
        trade = self._open_one_trade()
        assert isinstance(trade.entry_price_ref,    Decimal)
        assert isinstance(trade.entry_price_actual, Decimal)
        assert isinstance(trade.stop_price,         Decimal)
        assert isinstance(trade.target_price,       Decimal)
        assert isinstance(trade.stop_distance,      Decimal)
        assert isinstance(trade.expected_R,         Decimal)
        assert isinstance(trade.actual_R_if_stopped, Decimal)
        assert isinstance(trade.position_size,      Decimal)
        assert isinstance(trade.risk_amount,        Decimal)
        assert isinstance(trade.account_equity,     Decimal)

    def test_trade_id_is_16_char_hex(self) -> None:
        """Trade ID is a 16-character lowercase hex string."""
        trade = self._open_one_trade()
        assert len(trade.trade_id) == 16
        int(trade.trade_id, 16)  # must be valid hex

    def test_trade_id_is_deterministic(self) -> None:
        """Same candidate_id + timestamp → identical trade_id."""
        opened_at = _T1
        id1 = _make_trade_id("abc123", opened_at)
        id2 = _make_trade_id("abc123", opened_at)
        assert id1 == id2

    def test_trade_id_differs_for_different_inputs(self) -> None:
        """Different candidate_id or time → different trade_id."""
        id1 = _make_trade_id("abc123", _T1)
        id2 = _make_trade_id("abc123", _T2)
        id3 = _make_trade_id("xyz999", _T1)
        assert id1 != id2
        assert id1 != id3

    def test_trade_is_frozen_dataclass(self) -> None:
        """Trade must be immutable (frozen=True)."""
        trade = self._open_one_trade()
        with pytest.raises((AttributeError, TypeError)):
            trade.status = "MODIFIED"  # type: ignore[misc]

    def test_opened_at_is_bar_close_utc(self) -> None:
        """opened_at must be bar.timestamp_end (UTC)."""
        cand = _candidate(entry="100000", stop="99600", target="103200")
        executor = EntryExecutor("BTCUSDT")
        ts = datetime.datetime(2024, 6, 1, 8, 0, 0, tzinfo=_UTC)
        bar = AggregatedBar(
            symbol="BTCUSDT",
            timestamp_start=ts - datetime.timedelta(minutes=15),
            timestamp_end=ts,
            timeframe="15m",
            open=Decimal("100000"),
            high=Decimal("100200"),
            low=Decimal("99700"),
            close=Decimal("100000"),
            volume=Decimal("100"),
            is_complete=True,
            is_reliable=True,
        )
        trades = executor.process_bar(
            bar_15m=bar,
            active_candidates=[cand],
            atr_15m=Decimal("400"),
            phase_result=_phase_result(),
            portfolio=_portfolio("100000"),
        )
        assert len(trades) == 1
        assert trades[0].opened_at == ts
        assert trades[0].opened_at.tzinfo is not None


# ---------------------------------------------------------------------------
# AC6: Trade management
# ---------------------------------------------------------------------------

class TestTradeManagement:
    """AC6: manage_open_trades() produces correct actions."""

    def _open_trade(
        self,
        entry: str = "100000",
        stop: str = "99500",
        direction: Direction = Direction.LONG,
    ) -> tuple[EntryExecutor, Trade]:
        target = "103000" if direction == Direction.LONG else "97000"
        if direction == Direction.SHORT:
            cand = _candidate(
                entry=entry, stop=stop, target=target,
                direction=direction,
                setup_type=SetupType.PULLBACK_CONTINUATION,
            )
        else:
            cand = _candidate(entry=entry, stop=stop, target=target)

        executor = EntryExecutor("BTCUSDT")
        bar = _bar(close=entry, high=str(Decimal(entry) + Decimal("200")),
                   low=str(Decimal(entry) - Decimal("200")))
        trades = executor.process_bar(
            bar_15m=bar,
            active_candidates=[cand],
            atr_15m=Decimal("400"),
            phase_result=_phase_result(Phase.TREND_BULL if direction == Direction.LONG else Phase.TREND_BEAR),
            portfolio=_portfolio("100000"),
        )
        assert len(trades) == 1
        return executor, trades[0]

    def test_breakeven_triggered_at_plus_1r_long(self) -> None:
        """Long trade: when price reaches entry + stop_distance, breakeven fires."""
        executor, trade = self._open_trade()
        # entry=100000, stop=99500, stop_dist=500
        # Breakeven at: 100000 + 500 = 100500
        # Bar high = 100500 → triggers breakeven
        bar = _bar(close="100450", high="100500", low="100200")
        actions = executor.manage_open_trades(
            bar_15m=bar,
            phase_result=_phase_result(Phase.TREND_BULL),
        )
        assert len(actions) == 1
        assert actions[0].action_type == TradeActionType.MOVE_STOP_BREAKEVEN
        assert actions[0].new_stop == Decimal("100000")   # entry_price_actual

    def test_breakeven_not_triggered_before_1r(self) -> None:
        """Breakeven does NOT fire if high < entry + stop_distance."""
        executor, trade = self._open_trade()
        # entry=100000, stop_dist=500, breakeven at 100500
        # Bar high = 100400 → not yet at +1R
        bar = _bar(close="100400", high="100400", low="100100")
        actions = executor.manage_open_trades(
            bar_15m=bar,
            phase_result=_phase_result(Phase.TREND_BULL),
        )
        assert len(actions) == 1
        assert actions[0].action_type == TradeActionType.HOLD

    def test_breakeven_not_triggered_twice(self) -> None:
        """Once breakeven is set, MOVE_STOP_BREAKEVEN must NOT fire again."""
        executor, trade = self._open_trade()
        # Trigger breakeven
        bar1 = _bar(close="100500", high="100500", low="100200", ts=_T2)
        actions1 = executor.manage_open_trades(
            bar_15m=bar1,
            phase_result=_phase_result(Phase.TREND_BULL),
        )
        assert actions1[0].action_type == TradeActionType.MOVE_STOP_BREAKEVEN
        # Second bar — price still above breakeven level but flag should not re-fire
        bar2 = _bar(close="100600", high="100700", low="100400", ts=_T3)
        actions2 = executor.manage_open_trades(
            bar_15m=bar2,
            phase_result=_phase_result(Phase.TREND_BULL),
        )
        assert actions2[0].action_type == TradeActionType.HOLD

    def test_breakeven_short_trade(self) -> None:
        """Short breakeven: low reaches entry - stop_dist."""
        executor, trade = self._open_trade(
            entry="100000", stop="100500", direction=Direction.SHORT,
        )
        # entry=100000, stop=100500, stop_dist=500
        # Breakeven at: 100000 - 500 = 99500
        bar = _bar(close="99550", high="100100", low="99500")
        actions = executor.manage_open_trades(
            bar_15m=bar,
            phase_result=_phase_result(Phase.TREND_BEAR),
        )
        assert len(actions) == 1
        assert actions[0].action_type == TradeActionType.MOVE_STOP_BREAKEVEN
        assert actions[0].new_stop == Decimal("100000")

    def test_phase_flip_exits_long_trade(self) -> None:
        """Long trade exits when phase flips to TREND_BEAR (incompatible)."""
        executor, trade = self._open_trade()
        bar = _bar(close="100200", high="100400", low="99900")
        actions = executor.manage_open_trades(
            bar_15m=bar,
            phase_result=_phase_result(Phase.TREND_BEAR),  # incompatible
        )
        assert len(actions) == 1
        assert actions[0].action_type == TradeActionType.EXIT_PHASE_FLIP

    def test_phase_flip_exits_short_trade(self) -> None:
        """Short trade exits when phase flips to TREND_BULL (incompatible)."""
        executor, trade = self._open_trade(
            entry="100000", stop="100500", direction=Direction.SHORT,
        )
        bar = _bar(close="99900", high="100100", low="99700")
        actions = executor.manage_open_trades(
            bar_15m=bar,
            phase_result=_phase_result(Phase.TREND_BULL),  # incompatible
        )
        assert len(actions) == 1
        assert actions[0].action_type == TradeActionType.EXIT_PHASE_FLIP

    def test_balance_phase_compatible_with_both_directions(self) -> None:
        """BALANCE phase is compatible with LONG and SHORT — no phase exit."""
        executor_long, _ = self._open_trade()
        bar = _bar(close="100200", high="100400", low="99900")
        actions = executor_long.manage_open_trades(
            bar_15m=bar,
            phase_result=_phase_result(Phase.BALANCE),
        )
        # Should be HOLD (not EXIT_PHASE_FLIP)
        assert actions[0].action_type != TradeActionType.EXIT_PHASE_FLIP

    def test_structure_failure_opposite_leg_exits_long(self) -> None:
        """Long trade exits when a BEAR leg completes on 1H."""
        executor, trade = self._open_trade()
        bar = _bar(close="100100", high="100200", low="99900")
        bear_leg = _leg(direction=LegDirection.BEAR)
        actions = executor.manage_open_trades(
            bar_15m=bar,
            phase_result=_phase_result(Phase.TREND_BULL),
            completed_1h_leg=bear_leg,
        )
        assert len(actions) == 1
        assert actions[0].action_type == TradeActionType.EXIT_STRUCTURE_FAILURE

    def test_structure_failure_same_direction_leg_does_not_exit(self) -> None:
        """Long trade does NOT exit when a BULL leg completes (same direction)."""
        executor, trade = self._open_trade()
        bar = _bar(close="100100", high="100200", low="99900")
        bull_leg = _leg(direction=LegDirection.BULL)
        actions = executor.manage_open_trades(
            bar_15m=bar,
            phase_result=_phase_result(Phase.TREND_BULL),
            completed_1h_leg=bull_leg,
        )
        # BULL leg completing is structure confirmation for a LONG trade
        assert actions[0].action_type != TradeActionType.EXIT_STRUCTURE_FAILURE

    def test_no_completed_leg_no_structure_failure(self) -> None:
        """When no 1H leg has completed, structure failure does not fire."""
        executor, trade = self._open_trade()
        bar = _bar(close="100200", high="100400", low="99900")
        actions = executor.manage_open_trades(
            bar_15m=bar,
            phase_result=_phase_result(Phase.TREND_BULL),
            completed_1h_leg=None,
        )
        assert actions[0].action_type == TradeActionType.HOLD

    def test_exit_priority_phase_before_structure(self) -> None:
        """Phase flip takes priority over structure failure check (exits first)."""
        executor, trade = self._open_trade()
        bar = _bar(close="100100", high="100200", low="99900")
        bear_leg = _leg(direction=LegDirection.BEAR)
        # Both conditions met: incompatible phase AND opposite leg
        actions = executor.manage_open_trades(
            bar_15m=bar,
            phase_result=_phase_result(Phase.TREND_BEAR),  # incompatible with LONG
            completed_1h_leg=bear_leg,
        )
        # Phase flip is checked first (Level 2 precedes Level 3)
        assert actions[0].action_type == TradeActionType.EXIT_PHASE_FLIP


# ---------------------------------------------------------------------------
# AC7: Tie-breaking order
# ---------------------------------------------------------------------------

class TestTieBreaking:
    """AC7: Multiple candidates triggered same bar — correct priority order."""

    def _make_candidate_with_zone(
        self,
        zone_id: str,
        entry: str,
        stop: str,
        target: str,
        created_at: datetime.datetime = _T0,
    ) -> SetupCandidate:
        return _candidate(
            entry=entry, stop=stop, target=target,
            zone_id=zone_id,
            created_at=created_at,
        )

    def test_htf_zone_wins_over_ltf_zone(self) -> None:
        """Rule 1: Higher TF zone origin wins.

        1H zone vs 15m zone with same R:R and stop.
        Expect: 1H candidate trade opened.
        """
        # Both have same price levels → same stop, same R
        cand_htf = self._make_candidate_with_zone(
            zone_id="1H_zone_htf",
            entry="100000", stop="99500", target="103000",
        )
        cand_ltf = self._make_candidate_with_zone(
            zone_id="15m_zone_ltf",
            entry="100000", stop="99500", target="103000",
        )
        # Both have R = 6.0 >= 2.0
        executor = EntryExecutor("BTCUSDT")
        bar = _bar(close="100000")
        # Use max_positions=1 to force only one trade
        portfolio = PortfolioState(
            equity=Decimal("100000"),
            open_positions=[],
            system_paused=False,
            max_positions=1,
        )
        trades = executor.process_bar(
            bar_15m=bar,
            active_candidates=[cand_ltf, cand_htf],  # LTF first in list (should still lose)
            atr_15m=Decimal("400"),
            phase_result=_phase_result(),
            portfolio=portfolio,
            zone_strength_map={"1H_zone_htf": 5, "15m_zone_ltf": 5},
        )
        assert len(trades) == 1
        assert trades[0].zone_id == "1H_zone_htf"

    def test_higher_strength_wins_same_tf(self) -> None:
        """Rule 2: Same TF zone, higher strength wins."""
        cand_strong = self._make_candidate_with_zone(
            zone_id="1H_zone_strong",
            entry="100000", stop="99500", target="103000",
        )
        cand_weak = self._make_candidate_with_zone(
            zone_id="1H_zone_weak",
            entry="100000", stop="99500", target="103000",
        )
        executor = EntryExecutor("BTCUSDT")
        bar = _bar(close="100000")
        portfolio = PortfolioState(
            equity=Decimal("100000"),
            open_positions=[],
            system_paused=False,
            max_positions=1,
        )
        trades = executor.process_bar(
            bar_15m=bar,
            active_candidates=[cand_weak, cand_strong],
            atr_15m=Decimal("400"),
            phase_result=_phase_result(),
            portfolio=portfolio,
            zone_strength_map={"1H_zone_strong": 10, "1H_zone_weak": 2},
        )
        assert len(trades) == 1
        assert trades[0].zone_id == "1H_zone_strong"

    def test_smaller_stop_wins_same_strength(self) -> None:
        """Rule 3: Same TF + strength, smaller stop distance wins."""
        # Tight stop: stop=99700 → stop_dist=300
        cand_tight = self._make_candidate_with_zone(
            zone_id="1H_zone_tight",
            entry="100000", stop="99700", target="103000",
        )
        # Wide stop: stop=99500 → stop_dist=500
        cand_wide = self._make_candidate_with_zone(
            zone_id="1H_zone_wide",
            entry="100000", stop="99500", target="103000",
        )
        executor = EntryExecutor("BTCUSDT")
        bar = _bar(close="100000")
        portfolio = PortfolioState(
            equity=Decimal("100000"),
            open_positions=[],
            system_paused=False,
            max_positions=1,
        )
        trades = executor.process_bar(
            bar_15m=bar,
            active_candidates=[cand_wide, cand_tight],
            atr_15m=Decimal("600"),
            phase_result=_phase_result(),
            portfolio=portfolio,
            zone_strength_map={"1H_zone_tight": 5, "1H_zone_wide": 5},
        )
        assert len(trades) == 1
        assert trades[0].zone_id == "1H_zone_tight"

    def test_earlier_creation_wins_all_else_equal(self) -> None:
        """Rule 5: Same TF + strength + stop + R → earlier created_at wins."""
        earlier = _T0
        later   = _T0 + datetime.timedelta(minutes=30)
        cand_earlier = self._make_candidate_with_zone(
            zone_id="1H_zone_early",
            entry="100000", stop="99500", target="103000",
            created_at=earlier,
        )
        cand_later = self._make_candidate_with_zone(
            zone_id="1H_zone_late",
            entry="100000", stop="99500", target="103000",
            created_at=later,
        )
        executor = EntryExecutor("BTCUSDT")
        bar = _bar(close="100000")
        portfolio = PortfolioState(
            equity=Decimal("100000"),
            open_positions=[],
            system_paused=False,
            max_positions=1,
        )
        trades = executor.process_bar(
            bar_15m=bar,
            active_candidates=[cand_later, cand_earlier],
            atr_15m=Decimal("400"),
            phase_result=_phase_result(),
            portfolio=portfolio,
            zone_strength_map={"1H_zone_early": 5, "1H_zone_late": 5},
        )
        assert len(trades) == 1
        assert trades[0].zone_id == "1H_zone_early"


# ---------------------------------------------------------------------------
# AC8: No float anywhere
# ---------------------------------------------------------------------------

class TestNoFloat:
    """AC8: All arithmetic paths use Decimal only — no float."""

    def test_trade_fields_no_float(self) -> None:
        """Inspect all numeric fields of created Trade for float values."""
        cand = _candidate(entry="100000", stop="99600", target="103200")
        executor = EntryExecutor("BTCUSDT")
        bar = _bar(close="100000", high="100200", low="99700")
        trades = executor.process_bar(
            bar_15m=bar,
            active_candidates=[cand],
            atr_15m=Decimal("400"),
            phase_result=_phase_result(),
            portfolio=_portfolio("100000"),
        )
        assert len(trades) == 1
        trade = trades[0]
        # Check all numeric fields
        numeric_fields = [
            trade.entry_price_ref,
            trade.entry_price_actual,
            trade.stop_price,
            trade.target_price,
            trade.stop_distance,
            trade.expected_R,
            trade.actual_R_if_stopped,
            trade.position_size,
            trade.risk_amount,
            trade.account_equity,
        ]
        for val in numeric_fields:
            assert isinstance(val, Decimal), f"Found float: {val!r}"

    def test_portfolio_risk_computation_no_float(self) -> None:
        """_compute_portfolio_risk returns a Decimal."""
        portfolio = _portfolio("10000")
        result = _compute_portfolio_risk(portfolio, Decimal("100"))
        assert isinstance(result, Decimal)

    def test_position_sizing_output_is_decimal(self) -> None:
        """_compute_position_size always returns Decimal."""
        result = EntryExecutor._compute_position_size(Decimal("50000"), Decimal("300"))
        assert isinstance(result, Decimal)

    def test_stop_distance_computation_is_decimal(self) -> None:
        """_compute_stop_distance always returns Decimal."""
        cand = _candidate(entry="100000", stop="99500", target="103000")
        result = _compute_stop_distance(cand, Decimal("100000"))
        assert isinstance(result, Decimal)


# ---------------------------------------------------------------------------
# Additional: Level-0 Data Integrity gate
# ---------------------------------------------------------------------------

class TestDataIntegrityGate:
    """Level 0 (Doc 1 §6.1): Abort on invalid OHLCV data."""

    def test_high_less_than_low_aborts(self) -> None:
        """high < low → Level-0 abort, no entries attempted."""
        cand = _candidate(entry="100000", stop="99500", target="103000")
        executor = EntryExecutor("BTCUSDT")
        # Deliberately corrupted bar
        bar = AggregatedBar(
            symbol="BTCUSDT",
            timestamp_start=_T0,
            timestamp_end=_T1,
            timeframe="15m",
            open=Decimal("100000"),
            high=Decimal("99800"),   # high < low — invalid!
            low=Decimal("100200"),
            close=Decimal("100000"),
            volume=Decimal("100"),
            is_complete=True,
            is_reliable=True,
        )
        trades = executor.process_bar(
            bar_15m=bar,
            active_candidates=[cand],
            atr_15m=Decimal("400"),
            phase_result=_phase_result(),
            portfolio=_portfolio("100000"),
        )
        assert trades == []

    def test_close_outside_range_aborts(self) -> None:
        """close > high → Level-0 abort."""
        cand = _candidate(entry="101000", stop="99500", target="104000")
        executor = EntryExecutor("BTCUSDT")
        bar = AggregatedBar(
            symbol="BTCUSDT",
            timestamp_start=_T0,
            timestamp_end=_T1,
            timeframe="15m",
            open=Decimal("100000"),
            high=Decimal("100500"),
            low=Decimal("99800"),
            close=Decimal("101000"),  # close > high — invalid!
            volume=Decimal("100"),
            is_complete=True,
            is_reliable=True,
        )
        trades = executor.process_bar(
            bar_15m=bar,
            active_candidates=[cand],
            atr_15m=Decimal("400"),
            phase_result=_phase_result(),
            portfolio=_portfolio("100000"),
        )
        assert trades == []

    def test_bar_ohlcv_valid_helper(self) -> None:
        """_bar_ohlcv_valid returns True for a normal bar."""
        bar = _bar(close="100000", high="100200", low="99800")
        assert _bar_ohlcv_valid(bar) is True

    def test_bar_ohlcv_valid_incomplete_bar(self) -> None:
        """_bar_ohlcv_valid returns False for incomplete bar."""
        bar = _bar(close="100000", is_complete=False)
        assert _bar_ohlcv_valid(bar) is False


# ---------------------------------------------------------------------------
# Additional: Zone timeframe inference for tie-breaking
# ---------------------------------------------------------------------------

class TestZoneTFInference:
    """_zone_timeframe_from_id extracts TF prefix from zone_id."""

    def test_1h_prefix(self) -> None:
        assert _zone_timeframe_from_id("1H_abc123") == "1H"

    def test_4h_prefix(self) -> None:
        assert _zone_timeframe_from_id("4H_def456") == "4H"

    def test_15m_prefix(self) -> None:
        assert _zone_timeframe_from_id("15m_xyz789") == "15m"

    def test_unknown_prefix_falls_back_to_15m(self) -> None:
        assert _zone_timeframe_from_id("unknown_zone_id") == "15m"

    def test_no_separator_falls_back_to_15m(self) -> None:
        assert _zone_timeframe_from_id("zone_without_tf") == "15m"


# ---------------------------------------------------------------------------
# Additional: Phase compatibility check
# ---------------------------------------------------------------------------

class TestPhaseCompatibility:
    """_is_phase_compatible checks trade direction vs current phase."""

    def test_long_compatible_with_trend_bull(self) -> None:
        assert _is_phase_compatible(Direction.LONG, Phase.TREND_BULL) is True

    def test_long_compatible_with_balance(self) -> None:
        assert _is_phase_compatible(Direction.LONG, Phase.BALANCE) is True

    def test_long_compatible_with_accumulation(self) -> None:
        assert _is_phase_compatible(Direction.LONG, Phase.ACCUMULATION) is True

    def test_long_incompatible_with_trend_bear(self) -> None:
        assert _is_phase_compatible(Direction.LONG, Phase.TREND_BEAR) is False

    def test_long_incompatible_with_distribution(self) -> None:
        assert _is_phase_compatible(Direction.LONG, Phase.DISTRIBUTION) is False

    def test_short_compatible_with_trend_bear(self) -> None:
        assert _is_phase_compatible(Direction.SHORT, Phase.TREND_BEAR) is True

    def test_short_compatible_with_balance(self) -> None:
        assert _is_phase_compatible(Direction.SHORT, Phase.BALANCE) is True

    def test_short_compatible_with_distribution(self) -> None:
        assert _is_phase_compatible(Direction.SHORT, Phase.DISTRIBUTION) is True

    def test_short_incompatible_with_trend_bull(self) -> None:
        assert _is_phase_compatible(Direction.SHORT, Phase.TREND_BULL) is False

    def test_short_incompatible_with_accumulation(self) -> None:
        assert _is_phase_compatible(Direction.SHORT, Phase.ACCUMULATION) is False


# ---------------------------------------------------------------------------
# Additional: Min R:R by setup type
# ---------------------------------------------------------------------------

class TestMinRBySetup:
    """_min_r_for_setup returns correct floor per setup category."""

    def test_pullback_requires_2_0(self) -> None:
        assert _min_r_for_setup(SetupType.PULLBACK_CONTINUATION) == Decimal("2.0")

    def test_breakout_retest_requires_2_0(self) -> None:
        assert _min_r_for_setup(SetupType.BREAKOUT_RETEST) == Decimal("2.0")

    def test_fakeout_requires_1_5(self) -> None:
        assert _min_r_for_setup(SetupType.FAKEOUT) == Decimal("1.5")

    def test_range_fade_requires_1_5(self) -> None:
        assert _min_r_for_setup(SetupType.RANGE_FADE) == Decimal("1.5")


# ---------------------------------------------------------------------------
# Additional: Integration — full lifecycle
# ---------------------------------------------------------------------------

class TestIntegration:
    """Integration tests covering entry → breakeven → exit."""

    def test_full_lifecycle_long_trade(self) -> None:
        """Full lifecycle: entry → breakeven → structure failure exit."""
        cand = _candidate(entry="100000", stop="99500", target="103000")
        executor = EntryExecutor("BTCUSDT")

        # Bar 1: Entry fires
        bar1 = _bar(close="100000", high="100200", low="99700", ts=_T1)
        trades = executor.process_bar(
            bar_15m=bar1,
            active_candidates=[cand],
            atr_15m=Decimal("400"),
            phase_result=_phase_result(Phase.TREND_BULL),
            portfolio=_portfolio("100000"),
        )
        assert len(trades) == 1
        trade = trades[0]
        assert trade.status == "OPEN"

        # Bar 2: Not yet at +1R — HOLD
        bar2 = _bar(close="100300", high="100490", low="100100", ts=_T2)
        actions = executor.manage_open_trades(
            bar_15m=bar2,
            phase_result=_phase_result(Phase.TREND_BULL),
        )
        assert actions[0].action_type == TradeActionType.HOLD

        # Bar 3: Reaches +1R (entry=100000, stop_dist=500, breakeven_level=100500)
        # high must >= 100500 AND close must not exceed high
        bar3 = _bar(close="100500", high="100600", low="100300", ts=_T3)
        actions = executor.manage_open_trades(
            bar_15m=bar3,
            phase_result=_phase_result(Phase.TREND_BULL),
        )
        assert actions[0].action_type == TradeActionType.MOVE_STOP_BREAKEVEN
        assert actions[0].new_stop == Decimal("100000")

        # Bar 4: Opposite (BEAR) leg confirmed → structure failure exit
        bar4 = _bar(close="100400", high="100600", low="100200", ts=_T3 + datetime.timedelta(minutes=15))
        bear_leg = _leg(LegDirection.BEAR)
        actions = executor.manage_open_trades(
            bar_15m=bar4,
            phase_result=_phase_result(Phase.TREND_BULL),
            completed_1h_leg=bear_leg,
        )
        assert actions[0].action_type == TradeActionType.EXIT_STRUCTURE_FAILURE

    def test_no_double_entry_same_candidate(self) -> None:
        """Same candidate should not produce two trades across two bars."""
        cand = _candidate(entry="100000", stop="99500", target="103000")
        executor = EntryExecutor("BTCUSDT")

        # Bar 1: entry fires
        bar1 = _bar(close="100000", ts=_T1)
        t1 = executor.process_bar(
            bar_15m=bar1,
            active_candidates=[cand],
            atr_15m=Decimal("400"),
            phase_result=_phase_result(),
            portfolio=_portfolio("100000"),
        )
        assert len(t1) == 1

        # Bar 2: same candidate presented again (e.g. SetupEngine didn't expire yet)
        # The zone is now in open_positions → duplicate prevention
        portfolio_updated = PortfolioState(
            equity=Decimal("100000"),
            open_positions=t1,
            system_paused=False,
        )
        bar2 = _bar(close="100050", ts=_T2)
        t2 = executor.process_bar(
            bar_15m=bar2,
            active_candidates=[cand],
            atr_15m=Decimal("400"),
            phase_result=_phase_result(),
            portfolio=portfolio_updated,
        )
        assert t2 == []

    def test_manage_open_trades_empty_if_no_open_trades(self) -> None:
        """manage_open_trades returns [] when no open trades exist."""
        executor = EntryExecutor("BTCUSDT")
        bar = _bar(close="100000")
        actions = executor.manage_open_trades(
            bar_15m=bar,
            phase_result=_phase_result(),
        )
        assert actions == []
