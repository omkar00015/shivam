"""Tests for src/portfolio/portfolio_manager.py.

Acceptance criteria:
  AC1: Two candidates same bar same instrument → only one allocated (instrument limit).
  AC2: Portfolio risk at 5.5% + new trade risk 1% → rejected (exceeds 6% cap).
  AC3: Daily loss > 3% → system_paused = True, can_trade() = False.
  AC4: Consecutive losses = 3 → system_paused = True.
  AC5: Drawdown > 10% from peak → system_paused = True.
  AC6: manual_resume() → True only for non-daily-loss pauses; False for DAILY_LOSS.
  AC7: Allocation rank follows Doc 1 §7 tie-breaking (HTF > strength > stop > R > time > id).
  AC8: No float anywhere in PortfolioState, arithmetic, or comparisons.
"""

from __future__ import annotations

import datetime
import hashlib
from decimal import Decimal
from typing import Optional

import pytest

from src.execution.entry_executor import Trade
from src.portfolio.portfolio_manager import (
    PauseReason,
    PortfolioManager,
    PortfolioState,
    _instrument_risk_pct,
    _portfolio_risk_pct,
    _tier_break_sort_key,
    _zone_timeframe,
)
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


def _cand(
    entry: str,
    stop: str,
    target: str,
    zone_id: str = "1H_zone001",
    direction: Direction = Direction.LONG,
    setup_type: SetupType = SetupType.PULLBACK_CONTINUATION,
    created_at: datetime.datetime = _T0,
    expected_r: Optional[str] = None,
) -> SetupCandidate:
    e = Decimal(entry)
    s = Decimal(stop)
    t = Decimal(target)
    stop_dist   = abs(e - s)
    target_dist = abs(t - e)
    er = Decimal(expected_r) if expected_r else (
        target_dist / stop_dist if stop_dist > Decimal("1E-9") else Decimal("0")
    )
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


def _trade(
    instrument: str = "BTCUSDT",
    direction: Direction = Direction.LONG,
    risk_amount: str = "100",
    entry: str = "100000",
    stop: str = "99500",
    zone_id: str = "1H_zone001",
    trade_id: Optional[str] = None,
) -> Trade:
    equity = Decimal("10000")
    e = Decimal(entry)
    s = Decimal(stop)
    stop_dist = abs(e - s)
    return Trade(
        trade_id=trade_id or hashlib.sha256(f"{instrument}_{zone_id}".encode()).hexdigest()[:16],
        candidate_id="cand001",
        instrument=instrument,
        direction=direction,
        setup_type=SetupType.PULLBACK_CONTINUATION,
        entry_price_ref=e,
        entry_price_actual=e,
        stop_price=s,
        target_price=e + stop_dist * Decimal("3"),
        stop_distance=stop_dist,
        expected_R=Decimal("3"),
        actual_R_if_stopped=Decimal("3"),
        position_size=Decimal("2"),
        risk_amount=Decimal(risk_amount),
        account_equity=equity,
        zone_id=zone_id,
        opened_at=_T0,
        status="OPEN",
    )


def _pm(equity: str = "10000") -> PortfolioManager:
    """Create a fresh PortfolioManager with given equity."""
    return PortfolioManager(initial_equity=Decimal(equity))


# ---------------------------------------------------------------------------
# AC1: Instrument limit — one per instrument
# ---------------------------------------------------------------------------

class TestInstrumentLimit:
    """AC1: Two candidates same instrument → only one approved."""

    def test_two_candidates_same_instrument_only_one_approved(self) -> None:
        """Two BTC candidates compete — only higher-ranked one approved."""
        # Use zone_ids that both resolve to the same instrument (BTC prefix)
        cand1 = _cand(entry="100000", stop="99500", target="103000",
                      zone_id="BTC_1H_zone001", created_at=_T0)
        cand2 = _cand(entry="100050", stop="99550", target="103050",
                      zone_id="BTC_1H_zone002", created_at=_T1,
                      setup_type=SetupType.FAKEOUT)
        pm = _pm("100000")
        approved = pm.allocate([cand1, cand2])
        assert len(approved) == 1

    def test_two_candidates_different_instruments_both_approved(self) -> None:
        """One BTC + one XAU candidate → both can be approved (E5: independent)."""
        cand_btc = _cand(entry="100000", stop="99500", target="103000",
                         zone_id="BTC_1H_zone001")
        cand_xau = _cand(entry="2000", stop="1980", target="2060",
                         zone_id="XAU_1H_zone001")
        # Use max_open_trades=2 (default)
        pm = PortfolioManager(
            initial_equity=Decimal("100000"),
            max_open_trades=2,
        )
        approved = pm.allocate([cand_btc, cand_xau])
        assert len(approved) == 2

    def test_instrument_with_existing_open_trade_blocked(self) -> None:
        """If BTC already has an open trade, BTC candidates are rejected."""
        pm = _pm("100000")
        existing = _trade(instrument="BTC", zone_id="BTC_1H_zone999")
        pm.record_fill(existing)

        cand = _cand(entry="100000", stop="99500", target="103000",
                     zone_id="BTC_1H_zone001")
        approved = pm.allocate([cand])
        assert approved == []

    def test_different_instrument_still_approved_when_one_full(self) -> None:
        """BTC full, XAU candidate → XAU still approved."""
        pm = PortfolioManager(initial_equity=Decimal("100000"), max_open_trades=2)
        btc_trade = _trade(instrument="BTC", zone_id="BTC_1H_zone001")
        pm.record_fill(btc_trade)

        xau_cand = _cand(entry="2000", stop="1980", target="2060",
                         zone_id="XAU_1H_zone001")
        approved = pm.allocate([xau_cand])
        assert len(approved) == 1
        assert approved[0].zone_id == "XAU_1H_zone001"


# ---------------------------------------------------------------------------
# AC2: Portfolio risk cap — 6% hard limit
# ---------------------------------------------------------------------------

class TestPortfolioRiskCap:
    """AC2: Portfolio risk 5.5% + 1% new = 6.5% > 6% → rejected."""

    def test_risk_cap_exceeded_rejects_candidate(self) -> None:
        """If existing risk 5.5% and new trade is 1%, total 6.5% > 6% → reject."""
        # equity = 100000. 5.5% = 5500 risk already in use.
        # new trade would add 1% = 1000 → total 6500 = 6.5% > 6%
        equity = Decimal("100000")
        # Create a PM and manually force risk by filling 5 trades of 1100 each
        # 5 × 1100 = 5500 = 5.5%
        pm = PortfolioManager(
            initial_equity=equity,
            max_open_trades=10,
            max_per_instrument=10,
            max_portfolio_risk_pct=Decimal("0.06"),
        )
        # Fill trades from 5 different instruments to avoid per-instrument limit
        for i in range(5):
            t = _trade(
                instrument=f"INSTR{i}",
                zone_id=f"INSTR{i}_1H_zone{i:03d}",
                risk_amount="1100",
            )
            pm._state.open_trades[f"INSTR{i}"] = t

        # Verify existing risk = 5500 / 100000 = 5.5%
        risk_now = _portfolio_risk_pct(pm._state.open_trades, equity)
        assert risk_now == Decimal("5500") / equity  # 5.5%

        # Now try to allocate a new candidate (would add 1000 = 1% → total 6.5%)
        cand = _cand(entry="100000", stop="99000", target="103000",
                     zone_id="NEWI_1H_zone001")
        approved = pm.allocate([cand])
        assert approved == []

    def test_risk_at_exactly_cap_minus_one_trade_allows(self) -> None:
        """If existing risk is exactly 5%, new 1% = 6% total = exactly at cap → allowed."""
        equity = Decimal("100000")
        # 5 trades × 1000 each = 5000 = 5%
        pm = PortfolioManager(
            initial_equity=equity,
            max_open_trades=10,
            max_per_instrument=10,
            max_portfolio_risk_pct=Decimal("0.06"),
        )
        for i in range(5):
            t = _trade(
                instrument=f"INSTR{i}",
                zone_id=f"INSTR{i}_1H_zone{i:03d}",
                risk_amount="1000",
            )
            pm._state.open_trades[f"INSTR{i}"] = t

        # 5000/100000 = 5%. New trade adds 1000 → 6000 = 6% → exactly at cap.
        cand = _cand(entry="100000", stop="99000", target="103000",
                     zone_id="NEWI_1H_zone001")
        # At exactly the cap (not exceeding), should be allowed
        approved = pm.allocate([cand])
        # Note: logic uses > cap + EPSILON, so exactly 6% is allowed
        assert len(approved) == 1

    def test_empty_candidates_returns_empty(self) -> None:
        """No candidates → empty allocation."""
        pm = _pm("10000")
        assert pm.allocate([]) == []

    def test_paused_system_returns_empty(self) -> None:
        """system_paused → allocate returns []."""
        pm = _pm("10000")
        pm._state.system_paused = True
        cand = _cand(entry="100000", stop="99500", target="103000")
        assert pm.allocate([cand]) == []


# ---------------------------------------------------------------------------
# AC3: Daily loss pause
# ---------------------------------------------------------------------------

class TestDailyLossPause:
    """AC3: Daily loss > 3% of equity → paused."""

    def test_daily_loss_exceeds_3pct_triggers_pause(self) -> None:
        """Losing trade that pushes daily loss > 3% → system_paused."""
        pm = _pm("10000")
        # daily_starting_equity = 10000. 3% = 300. Loss of 310 > 300 → pause.
        trade = _trade(instrument="BTCUSDT", zone_id="BTC_1H_zone001")
        pm.record_fill(trade)
        pm.record_close(trade, pnl=Decimal("-310"))

        assert pm.can_trade() is False
        state = pm.get_state()
        assert state.system_paused is True
        assert state.pause_reason == PauseReason.DAILY_LOSS

    def test_daily_loss_exactly_3pct_no_pause(self) -> None:
        """Daily loss = exactly 3% → no pause (must EXCEED 3%)."""
        pm = _pm("10000")
        trade = _trade(instrument="BTCUSDT", zone_id="BTC_1H_zone001")
        pm.record_fill(trade)
        pm.record_close(trade, pnl=Decimal("-300"))  # exactly 3%

        # Exactly 3% should NOT trigger pause (threshold is strictly > 3%)
        assert pm.can_trade() is True

    def test_daily_loss_pause_auto_resumes_on_reset_daily(self) -> None:
        """DAILY_LOSS pause auto-resumes when reset_daily() is called."""
        pm = _pm("10000")
        trade = _trade(instrument="BTCUSDT", zone_id="BTC_1H_zone001")
        pm.record_fill(trade)
        pm.record_close(trade, pnl=Decimal("-310"))
        assert pm.can_trade() is False

        # New session opens
        pm.reset_daily()
        assert pm.can_trade() is True
        assert pm.get_state().pause_reason is None

    def test_daily_loss_pause_manual_resume_returns_false(self) -> None:
        """AC6: DAILY_LOSS pause rejects manual_resume(), must use reset_daily()."""
        pm = _pm("10000")
        trade = _trade(instrument="BTCUSDT", zone_id="BTC_1H_zone001")
        pm.record_fill(trade)
        pm.record_close(trade, pnl=Decimal("-310"))
        assert pm.can_trade() is False

        result = pm.manual_resume()
        assert result is False
        assert pm.can_trade() is False  # still paused

    def test_winning_trade_does_not_pause(self) -> None:
        """Profitable trade never triggers daily loss pause."""
        pm = _pm("10000")
        trade = _trade(instrument="BTCUSDT", zone_id="BTC_1H_zone001")
        pm.record_fill(trade)
        pm.record_close(trade, pnl=Decimal("500"))
        assert pm.can_trade() is True


# ---------------------------------------------------------------------------
# AC4: Consecutive loss pause
# ---------------------------------------------------------------------------

class TestConsecutiveLossPause:
    """AC4: Consecutive losses >= 3 → system_paused."""

    def test_three_consecutive_losses_trigger_pause(self) -> None:
        """Three sequential losing trades → paused."""
        pm = _pm("100000")  # large equity so daily limit not hit
        for i in range(3):
            t = _trade(
                instrument="BTCUSDT",
                zone_id=f"BTC_1H_zone{i:03d}",
                trade_id=f"trade_{i:03d}",
            )
            pm.record_fill(t)
            pm.record_close(t, pnl=Decimal("-50"))   # small loss

        assert pm.can_trade() is False
        assert pm.get_state().pause_reason == PauseReason.CONSECUTIVE_LOSS

    def test_win_resets_consecutive_counter(self) -> None:
        """A win resets consecutive loss count — three subsequent losses needed again."""
        pm = _pm("100000")

        # Two losses, then a win
        for i in range(2):
            t = _trade(instrument="BTCUSDT", zone_id=f"BTC_1H_zone{i:03d}", trade_id=f"t{i}")
            pm.record_fill(t)
            pm.record_close(t, pnl=Decimal("-50"))

        # Win resets counter
        t_win = _trade(instrument="BTCUSDT", zone_id="BTC_1H_zone010", trade_id="twin")
        pm.record_fill(t_win)
        pm.record_close(t_win, pnl=Decimal("200"))
        assert pm.get_state().consecutive_losses == 0
        assert pm.can_trade() is True

        # Need 3 more losses to trigger pause again
        for i in range(10, 12):
            t = _trade(instrument="BTCUSDT", zone_id=f"BTC_1H_zone{i:03d}", trade_id=f"t{i}")
            pm.record_fill(t)
            pm.record_close(t, pnl=Decimal("-50"))
        assert pm.can_trade() is True   # only 2 losses since win

    def test_consecutive_loss_pause_manual_resume_allowed(self) -> None:
        """AC6: CONSECUTIVE_LOSS pause allows manual_resume()."""
        pm = _pm("100000")
        for i in range(3):
            t = _trade(instrument="BTCUSDT", zone_id=f"BTC_1H_zone{i:03d}", trade_id=f"t{i}")
            pm.record_fill(t)
            pm.record_close(t, pnl=Decimal("-50"))

        assert pm.can_trade() is False
        result = pm.manual_resume()
        assert result is True
        assert pm.can_trade() is True

    def test_two_losses_no_pause(self) -> None:
        """Two losses → no pause (need 3)."""
        pm = _pm("100000")
        for i in range(2):
            t = _trade(instrument="BTCUSDT", zone_id=f"BTC_1H_zone{i:03d}", trade_id=f"t{i}")
            pm.record_fill(t)
            pm.record_close(t, pnl=Decimal("-50"))
        assert pm.can_trade() is True


# ---------------------------------------------------------------------------
# AC5: Drawdown pause
# ---------------------------------------------------------------------------

class TestDrawdownPause:
    """AC5: Drawdown > 10% from peak → system_paused."""

    def test_drawdown_above_10pct_triggers_pause(self) -> None:
        """Equity drops > 10% below peak → paused.

        Use equity=1_000_000 so loss of 100_001 is:
          daily_loss_pct = 100001/1000000 = 10.0001% > 3% → would hit DAILY_LOSS first.

        We need a scenario where daily_loss < 3% but drawdown > 10%.
        Use PM with daily_loss_pct_limit=Decimal("0.20") (raised limit)
        so only the drawdown check fires.
        """
        pm = PortfolioManager(
            initial_equity=Decimal("100000"),
            daily_loss_pct_limit=Decimal("0.20"),   # raise daily limit to 20%
        )
        # Lose 10001 → drawdown = 10001/100000 = 10.001% > 10%
        t = _trade(instrument="BTCUSDT", zone_id="BTC_1H_zone001")
        pm.record_fill(t)
        pm.record_close(t, pnl=Decimal("-10001"))

        assert pm.can_trade() is False
        assert pm.get_state().pause_reason == PauseReason.DRAWDOWN

    def test_drawdown_exactly_10pct_no_pause(self) -> None:
        """Drawdown of exactly 10% should NOT pause (threshold is strictly > 10%)."""
        pm = PortfolioManager(
            initial_equity=Decimal("100000"),
            daily_loss_pct_limit=Decimal("0.20"),   # raise daily limit so only drawdown fires
        )
        t = _trade(instrument="BTCUSDT", zone_id="BTC_1H_zone001")
        pm.record_fill(t)
        pm.record_close(t, pnl=Decimal("-10000"))  # exactly 10%
        assert pm.can_trade() is True

    def test_peak_equity_updates_on_profit(self) -> None:
        """peak_equity increases when equity exceeds it."""
        pm = _pm("10000")
        t_win = _trade(instrument="BTCUSDT", zone_id="BTC_1H_zone001")
        pm.record_fill(t_win)
        pm.record_close(t_win, pnl=Decimal("1000"))   # equity now 11000
        assert pm.get_state().peak_equity == Decimal("11000")

    def test_drawdown_pause_manual_resume_allowed(self) -> None:
        """AC6: DRAWDOWN pause allows manual_resume()."""
        pm = PortfolioManager(
            initial_equity=Decimal("100000"),
            daily_loss_pct_limit=Decimal("0.20"),
        )
        t = _trade(instrument="BTCUSDT", zone_id="BTC_1H_zone001")
        pm.record_fill(t)
        pm.record_close(t, pnl=Decimal("-10001"))
        assert pm.can_trade() is False
        assert pm.get_state().pause_reason == PauseReason.DRAWDOWN

        result = pm.manual_resume()
        assert result is True
        assert pm.can_trade() is True

    def test_drawdown_pct_computation(self) -> None:
        """Drawdown percentage computed correctly in get_state()."""
        # Use large equity so 1000 loss < 3% daily limit (no pause interference)
        pm = _pm("100000")
        t = _trade(instrument="BTCUSDT", zone_id="BTC_1H_zone001")
        pm.record_fill(t)
        pm.record_close(t, pnl=Decimal("-1000"))   # equity = 99000
        state = pm.get_state()
        # drawdown = (100000 - 99000) / 100000 = 0.01
        assert state.current_drawdown_pct == Decimal("0.01")


# ---------------------------------------------------------------------------
# AC6: manual_resume() logic
# ---------------------------------------------------------------------------

class TestManualResume:
    """AC6: manual_resume() returns True for consec/drawdown/data, False for daily."""

    def test_manual_resume_when_not_paused(self) -> None:
        """manual_resume() returns True when not paused (idempotent)."""
        pm = _pm("10000")
        assert pm.manual_resume() is True
        assert pm.can_trade() is True

    def test_manual_resume_data_integrity_allowed(self) -> None:
        """DATA_INTEGRITY pause → manual resume allowed."""
        pm = _pm("10000")
        pm.pause_data_integrity()
        assert pm.can_trade() is False
        result = pm.manual_resume()
        assert result is True
        assert pm.can_trade() is True

    def test_manual_resume_consecutive_loss_allowed(self) -> None:
        """CONSECUTIVE_LOSS → manual resume allowed (AC6)."""
        pm = _pm("100000")
        for i in range(3):
            t = _trade(instrument="BTCUSDT", zone_id=f"BTC_1H_zone{i:03d}", trade_id=f"t{i}")
            pm.record_fill(t)
            pm.record_close(t, pnl=Decimal("-50"))
        assert pm.manual_resume() is True

    def test_manual_resume_drawdown_allowed(self) -> None:
        """DRAWDOWN → manual resume allowed (AC6)."""
        pm = PortfolioManager(
            initial_equity=Decimal("100000"),
            daily_loss_pct_limit=Decimal("0.20"),
        )
        t = _trade(instrument="BTCUSDT", zone_id="BTC_1H_zone001")
        pm.record_fill(t)
        pm.record_close(t, pnl=Decimal("-10001"))
        # Confirm it's DRAWDOWN (not DAILY_LOSS)
        assert pm.get_state().pause_reason == PauseReason.DRAWDOWN
        assert pm.manual_resume() is True

    def test_manual_resume_daily_loss_returns_false(self) -> None:
        """DAILY_LOSS → manual_resume() returns False (AC6)."""
        pm = _pm("10000")
        t = _trade(instrument="BTCUSDT", zone_id="BTC_1H_zone001")
        pm.record_fill(t)
        pm.record_close(t, pnl=Decimal("-310"))
        # daily loss > 3% of 10000
        assert pm.get_state().pause_reason == PauseReason.DAILY_LOSS
        result = pm.manual_resume()
        assert result is False
        assert pm.can_trade() is False

    def test_reset_daily_lifts_daily_loss_pause(self) -> None:
        """reset_daily() lifts DAILY_LOSS pause (the auto-resume path)."""
        pm = _pm("10000")
        t = _trade(instrument="BTCUSDT", zone_id="BTC_1H_zone001")
        pm.record_fill(t)
        pm.record_close(t, pnl=Decimal("-310"))
        assert pm.can_trade() is False
        pm.reset_daily()
        assert pm.can_trade() is True

    def test_reset_daily_does_not_lift_consecutive_loss_pause(self) -> None:
        """reset_daily() only lifts DAILY_LOSS pauses — not consecutive/drawdown."""
        pm = _pm("100000")
        for i in range(3):
            t = _trade(instrument="BTCUSDT", zone_id=f"BTC_1H_zone{i:03d}", trade_id=f"t{i}")
            pm.record_fill(t)
            pm.record_close(t, pnl=Decimal("-50"))
        assert pm.get_state().pause_reason == PauseReason.CONSECUTIVE_LOSS
        pm.reset_daily()
        # reset_daily should NOT lift a non-DAILY_LOSS pause
        assert pm.can_trade() is False


# ---------------------------------------------------------------------------
# AC7: Allocation rank order follows Doc 1 §7 tie-breaking
# ---------------------------------------------------------------------------

class TestAllocationRankOrder:
    """AC7: Allocate picks best candidate by HTF > strength > stop > R > time > id."""

    def test_htf_zone_wins_over_ltf_zone(self) -> None:
        """Rule 1: 4H zone wins over 1H zone when all else equal."""
        cand_htf = _cand(entry="100000", stop="99500", target="103000",
                         zone_id="BTC_4H_zone001", created_at=_T0)
        cand_ltf = _cand(entry="100000", stop="99500", target="103000",
                         zone_id="BTC_1H_zone001", created_at=_T0)
        pm = PortfolioManager(initial_equity=Decimal("100000"), max_open_trades=1)
        # Both are BTC → only one can be selected
        approved = pm.allocate(
            [cand_ltf, cand_htf],   # LTF first in list
            zone_strength_map={"BTC_4H_zone001": 5, "BTC_1H_zone001": 5},
        )
        assert len(approved) == 1
        assert approved[0].zone_id == "BTC_4H_zone001"

    def test_higher_strength_wins_same_tf(self) -> None:
        """Rule 2: Higher zone strength wins when TF is equal."""
        cand_strong = _cand(entry="100000", stop="99500", target="103000",
                            zone_id="BTC_1H_zone_strong", created_at=_T0)
        cand_weak   = _cand(entry="100000", stop="99500", target="103000",
                            zone_id="BTC_1H_zone_weak", created_at=_T0)
        pm = PortfolioManager(initial_equity=Decimal("100000"), max_open_trades=1)
        approved = pm.allocate(
            [cand_weak, cand_strong],
            zone_strength_map={"BTC_1H_zone_strong": 10, "BTC_1H_zone_weak": 2},
        )
        assert len(approved) == 1
        assert approved[0].zone_id == "BTC_1H_zone_strong"

    def test_smaller_stop_wins_same_strength(self) -> None:
        """Rule 3: Smaller stop distance wins when TF + strength equal."""
        # stop_dist=300 vs 500 (both same TF and strength)
        cand_tight = _cand(entry="100000", stop="99700", target="103000",
                           zone_id="BTC_1H_zone_tight", created_at=_T0)
        cand_wide  = _cand(entry="100000", stop="99500", target="103000",
                           zone_id="BTC_1H_zone_wide", created_at=_T0)
        pm = PortfolioManager(initial_equity=Decimal("100000"), max_open_trades=1)
        approved = pm.allocate(
            [cand_wide, cand_tight],
            zone_strength_map={"BTC_1H_zone_tight": 5, "BTC_1H_zone_wide": 5},
        )
        assert len(approved) == 1
        assert approved[0].zone_id == "BTC_1H_zone_tight"

    def test_larger_r_wins_same_stop(self) -> None:
        """Rule 4: Larger expected_R wins when TF + strength + stop equal."""
        # Both stop_dist=500; R_high=6, R_low=3
        cand_high_r = _cand(entry="100000", stop="99500", target="103000",
                            zone_id="BTC_1H_zone_highr", created_at=_T0, expected_r="6")
        cand_low_r  = _cand(entry="100000", stop="99500", target="101500",
                            zone_id="BTC_1H_zone_lowr",  created_at=_T0, expected_r="3")
        pm = PortfolioManager(initial_equity=Decimal("100000"), max_open_trades=1)
        approved = pm.allocate(
            [cand_low_r, cand_high_r],
            zone_strength_map={"BTC_1H_zone_highr": 5, "BTC_1H_zone_lowr": 5},
        )
        assert len(approved) == 1
        assert approved[0].zone_id == "BTC_1H_zone_highr"

    def test_earlier_creation_wins_all_else_equal(self) -> None:
        """Rule 5: Earlier created_at wins when rules 1-4 are equal."""
        cand_early = _cand(entry="100000", stop="99500", target="103000",
                           zone_id="BTC_1H_zone_early", created_at=_T0)
        cand_late  = _cand(entry="100000", stop="99500", target="103000",
                           zone_id="BTC_1H_zone_late",  created_at=_T1)
        pm = PortfolioManager(initial_equity=Decimal("100000"), max_open_trades=1)
        approved = pm.allocate(
            [cand_late, cand_early],   # late first
            zone_strength_map={"BTC_1H_zone_early": 5, "BTC_1H_zone_late": 5},
        )
        assert len(approved) == 1
        assert approved[0].zone_id == "BTC_1H_zone_early"


# ---------------------------------------------------------------------------
# AC8: No float
# ---------------------------------------------------------------------------

class TestNoFloat:
    """AC8: All arithmetic uses Decimal — no float in state or helpers."""

    def test_portfolio_state_all_decimal(self) -> None:
        """All numeric fields of PortfolioState are Decimal."""
        pm = _pm("10000")
        state = pm.get_state()
        assert isinstance(state.equity,                Decimal)
        assert isinstance(state.peak_equity,           Decimal)
        assert isinstance(state.risk_used_pct,         Decimal)
        assert isinstance(state.current_drawdown_pct,  Decimal)
        assert isinstance(state.daily_pnl,             Decimal)
        assert isinstance(state.daily_starting_equity, Decimal)

    def test_risk_pct_helper_returns_decimal(self) -> None:
        """_portfolio_risk_pct returns Decimal."""
        open_trades = {
            "BTC": _trade(instrument="BTC", zone_id="BTC_1H_z1", risk_amount="100"),
        }
        result = _portfolio_risk_pct(open_trades, Decimal("10000"))
        assert isinstance(result, Decimal)

    def test_instrument_risk_pct_helper_returns_decimal(self) -> None:
        """_instrument_risk_pct returns Decimal."""
        open_trades = {
            "BTC": _trade(instrument="BTC", zone_id="BTC_1H_z1", risk_amount="100"),
        }
        result = _instrument_risk_pct("BTC", open_trades, Decimal("10000"))
        assert isinstance(result, Decimal)

    def test_drawdown_computation_is_decimal(self) -> None:
        """After a losing trade, drawdown is a Decimal."""
        pm = _pm("10000")
        t = _trade(instrument="BTCUSDT", zone_id="BTC_1H_zone001")
        pm.record_fill(t)
        pm.record_close(t, pnl=Decimal("-500"))
        state = pm.get_state()
        assert isinstance(state.current_drawdown_pct, Decimal)


# ---------------------------------------------------------------------------
# Additional: record_fill / record_close / get_state integration
# ---------------------------------------------------------------------------

class TestStateTracking:
    """Integration: fill + close updates equity, counters, and state."""

    def test_record_fill_adds_to_open_trades(self) -> None:
        """record_fill adds trade to open_trades."""
        pm = _pm("10000")
        t = _trade(instrument="BTCUSDT", zone_id="BTC_1H_zone001")
        pm.record_fill(t)
        state = pm.get_state()
        assert len(state.open_trades) == 1

    def test_record_close_removes_from_open_trades(self) -> None:
        """record_close removes trade from open_trades."""
        pm = _pm("10000")
        t = _trade(instrument="BTCUSDT", zone_id="BTC_1H_zone001")
        pm.record_fill(t)
        pm.record_close(t, pnl=Decimal("100"))
        state = pm.get_state()
        assert len(state.open_trades) == 0

    def test_equity_updates_after_close(self) -> None:
        """Equity changes by pnl after record_close."""
        pm = _pm("10000")
        t = _trade(instrument="BTCUSDT", zone_id="BTC_1H_zone001")
        pm.record_fill(t)
        pm.record_close(t, pnl=Decimal("500"))
        assert pm.get_state().equity == Decimal("10500")

    def test_total_trade_count_increments(self) -> None:
        """total_trades increments on each close."""
        pm = _pm("100000")
        for i in range(3):
            t = _trade(instrument="BTCUSDT", zone_id=f"BTC_1H_zone{i:03d}", trade_id=f"t{i}")
            pm.record_fill(t)
            pm.record_close(t, pnl=Decimal("100"))
        assert pm.get_state().total_trades == 3

    def test_win_loss_counts_correct(self) -> None:
        """winning_trades and losing_trades incremented correctly."""
        pm = _pm("100000")
        for i, pnl in enumerate([100, -50, 200, -30]):
            t = _trade(instrument="BTCUSDT", zone_id=f"BTC_1H_zone{i:03d}", trade_id=f"t{i}")
            pm.record_fill(t)
            pm.record_close(t, pnl=Decimal(str(pnl)))
        state = pm.get_state()
        assert state.winning_trades == 2
        assert state.losing_trades  == 2

    def test_daily_pnl_accumulates(self) -> None:
        """daily_pnl is the sum of pnl since last reset_daily."""
        pm = _pm("100000")
        t1 = _trade(instrument="BTCUSDT", zone_id="BTC_1H_zone001", trade_id="t1")
        pm.record_fill(t1)
        pm.record_close(t1, pnl=Decimal("300"))
        t2 = _trade(instrument="BTCUSDT", zone_id="BTC_1H_zone002", trade_id="t2")
        pm.record_fill(t2)
        pm.record_close(t2, pnl=Decimal("-100"))
        assert pm.get_state().daily_pnl == Decimal("200")

    def test_reset_daily_resets_pnl_and_starting_equity(self) -> None:
        """reset_daily() resets daily_pnl to 0 and updates starting equity."""
        pm = _pm("10000")
        t = _trade(instrument="BTCUSDT", zone_id="BTC_1H_zone001")
        pm.record_fill(t)
        pm.record_close(t, pnl=Decimal("500"))   # equity = 10500
        pm.reset_daily()
        state = pm.get_state()
        assert state.daily_pnl             == Decimal("0")
        assert state.daily_starting_equity == Decimal("10500")

    def test_can_trade_returns_true_initially(self) -> None:
        """Fresh PM → can_trade() is True."""
        pm = _pm("10000")
        assert pm.can_trade() is True


# ---------------------------------------------------------------------------
# Additional: zone timeframe inference
# ---------------------------------------------------------------------------

class TestZoneTFInference:
    """_zone_timeframe correctly extracts TF prefix."""

    def test_4h_prefix(self) -> None:
        assert _zone_timeframe("4H_abc123") == "4H"

    def test_1h_prefix(self) -> None:
        assert _zone_timeframe("1H_def456") == "1H"

    def test_btc_1h_prefix_returns_1h(self) -> None:
        """BTC_1H_zone001 — scans all tokens, finds '1H' → returns '1H'.

        The updated _zone_timeframe scans ALL underscore-delimited parts so
        instrument-prefixed zone IDs like 'BTC_4H_zone001' correctly return
        their timeframe token ('4H'), enabling proper Doc 1 §7 tie-breaking.
        """
        result = _zone_timeframe("BTC_1H_zone001")
        assert result == "1H"

    def test_1w_prefix(self) -> None:
        assert _zone_timeframe("1W_xyz789") == "1W"

    def test_unknown_prefix_fallback(self) -> None:
        assert _zone_timeframe("unknown_zone") == "15m"
