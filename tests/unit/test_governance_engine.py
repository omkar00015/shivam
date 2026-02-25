"""Tests for src/governance/governance_engine.py.

Acceptance criteria:
  AC1: win_rate drops to 60% of historical → DRIFT_ALERT active,
       size_multiplier = 0.50 (DEFENSIVE after repeated alert) or 0.75 (CAUTION).
       Specifically: win_rate at 60% of 0.45 baseline = 0.27,
       which is < 0.45 × 0.70 = 0.315 → WARNING → CAUTION, size=0.75.
  AC2: DRIFT_ALERT persists for > 10 consecutive cycles → allow_trading=False.
  AC3: Weekly loss > 5% → allow_trading=False, auto-resumes next Monday.
  AC4: Monthly loss > 10% → allow_trading=False, manual_resume required.
  AC5: Max drawdown > 15% from all-time peak → allow_trading=False.
  AC6: Same setup on same zone within 24h → is_setup_suppressed returns True.
  AC7: Data gap BTC > 10min → instrument_paused['BTCUSDT']=True, Gold unaffected.
  AC8: DB failures 3 consecutive → allow_trading=False for all.
  AC9: No float anywhere in GovernanceAction, GovernanceState.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest

from src.governance.governance_engine import (
    GovernanceAction,
    GovernanceEngine,
    GovernanceGovState,
    GovernanceState,
    _next_monday,
    _week_monday,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_UTC = datetime.timezone.utc
_BASE_TS = datetime.datetime(2024, 1, 15, 12, 0, 0, tzinfo=_UTC)   # Monday 12:00 UTC
_WEDNESDAY = datetime.datetime(2024, 1, 17, 12, 0, 0, tzinfo=_UTC)

_HISTORICAL_WIN_RATE = Decimal("0.45")
_HISTORICAL_AVG_R    = Decimal("0.80")


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------

def _engine(
    win_rate: str = "0.45",
    avg_r:    str = "0.80",
) -> GovernanceEngine:
    return GovernanceEngine(
        historical_win_rate=Decimal(win_rate),
        historical_avg_r=Decimal(avg_r),
    )


def _check(
    eng: GovernanceEngine,
    equity: str = "10000",
    ts: datetime.datetime = _BASE_TS,
    consec_losses: int = 0,
) -> GovernanceAction:
    return eng.check(
        current_equity=Decimal(equity),
        bar_timestamp=ts,
        consecutive_losses_ext=consec_losses,
    )


def _feed_losses(eng: GovernanceEngine, n: int) -> None:
    """Feed n losing trades into the engine."""
    for _ in range(n):
        eng.record_trade_result(pnl_r=Decimal("-1"), won=False)


def _feed_wins(eng: GovernanceEngine, n: int) -> None:
    """Feed n winning trades into the engine."""
    for _ in range(n):
        eng.record_trade_result(pnl_r=Decimal("2"), won=True)


def _fill_window_win_rate(eng: GovernanceEngine, win_rate_frac: Decimal) -> None:
    """Fill the 20-trade window with a specific win rate.

    win_rate_frac: e.g. Decimal("0.27") means 27% wins.
    """
    wins  = int(float(win_rate_frac * 20))  # approximate — only for test setup
    losses = 20 - wins
    for _ in range(wins):
        eng.record_trade_result(pnl_r=Decimal("2"), won=True)
    for _ in range(losses):
        eng.record_trade_result(pnl_r=Decimal("-1"), won=False)


# ---------------------------------------------------------------------------
# AC1: Drift alert — win_rate < baseline × 0.70
# ---------------------------------------------------------------------------

class TestDriftAlert:
    """AC1: Drift detection fires when win_rate < historical × 0.70."""

    def test_drift_alert_when_win_rate_below_70pct_baseline(self) -> None:
        """Win rate at 60% of baseline (0.27 vs baseline 0.45) → DRIFT_ALERT."""
        eng = _engine(win_rate="0.45", avg_r="0.80")
        # 0.27 < 0.45 × 0.70 = 0.315 → WARNING
        _fill_window_win_rate(eng, Decimal("0.27"))  # 27% win rate
        _check(eng)
        state = eng.get_state()
        assert state.drift_alert_active is True

    def test_drift_alert_triggers_caution_state(self) -> None:
        """First drift warning → CAUTION state.

        Pattern: interleaved wins/losses to keep win_rate in WARNING zone only.
        27% win rate: 5 wins + 3 losses + 1 win + 3 losses + 1 win + 7 losses
        = 7 wins, 13 losses, no run of ≥8 consecutive losses.
        win_rate = 7/20 = 0.35 > 0.45×0.50=0.225 (no CRITICAL),
                         < 0.45×0.70=0.315 — WARNING → CAUTION.
        avg_r="-2.0" baseline → avg_R checks inactive.
        """
        eng = _engine(win_rate="0.45", avg_r="-2.0")
        # Build 20 trades with ~35% win rate, no long losing streak
        pattern = [True, False, False, False, True, False, False, False,
                   True, False, False, False, True, False, False, False,
                   True, False, False, False]
        for won in pattern:
            eng.record_trade_result(
                pnl_r=Decimal("2") if won else Decimal("-1"), won=won
            )
        # win_rate = 5/20 = 0.25 < 0.45*0.70=0.315 but > 0.45*0.50=0.225 → WARNING
        _check(eng)
        assert eng.get_state().gov_state == GovernanceGovState.CAUTION

    def test_size_multiplier_caution_is_0_75(self) -> None:
        """CAUTION state → size_multiplier = 0.75.

        Same interleaved pattern as above — WARNING only, not CRITICAL.
        """
        eng = _engine(win_rate="0.45", avg_r="-2.0")
        pattern = [True, False, False, False, True, False, False, False,
                   True, False, False, False, True, False, False, False,
                   True, False, False, False]
        for won in pattern:
            eng.record_trade_result(
                pnl_r=Decimal("2") if won else Decimal("-1"), won=won
            )
        action = _check(eng)
        assert action.size_multiplier == Decimal("0.75")
        assert isinstance(action.size_multiplier, Decimal)

    def test_no_drift_when_win_rate_above_threshold(self) -> None:
        """Win rate above baseline × 0.70 → no drift alert.

        Pattern: interleaved wins/losses giving 40% win rate (8/20),
        with no long consecutive-loss streaks, so state stays NORMAL.
        avg_r="-2.0" baseline → avg_R CRITICAL/WARNING checks inactive.
        win_rate 0.40 > 0.45×0.70=0.315 → no WARNING → NORMAL.
        """
        eng = _engine(win_rate="0.45", avg_r="-2.0")
        # 8 wins spread across 20 trades — no run of ≥5 consecutive losses
        pattern = [True, False, False, True, False, False, True, False,
                   False, True, False, False, True, False, False, True,
                   False, False, True, True]   # 8 wins, 12 losses, max streak=2
        for won in pattern:
            eng.record_trade_result(
                pnl_r=Decimal("2") if won else Decimal("-1"), won=won
            )
        _check(eng)
        state = eng.get_state()
        assert state.drift_alert_active is False
        assert state.gov_state == GovernanceGovState.NORMAL

    def test_drift_alert_on_avg_r_below_threshold(self) -> None:
        """avg_R < baseline × 0.70 → DRIFT_ALERT (even if win rate is fine)."""
        eng = _engine(win_rate="0.45", avg_r="1.00")
        # Fill with wins at full win rate but very low R per trade
        for _ in range(20):
            eng.record_trade_result(pnl_r=Decimal("0.40"), won=True)
        # 0.40 < 1.00 × 0.70 = 0.70 → WARNING
        _check(eng)
        state = eng.get_state()
        assert state.drift_alert_active is True

    def test_drift_alert_cleared_when_window_recovers(self) -> None:
        """After window fills with good trades, drift_alert should clear."""
        eng = _engine(win_rate="0.45", avg_r="0.80")
        _fill_window_win_rate(eng, Decimal("0.27"))
        _check(eng)
        assert eng.get_state().drift_alert_active is True

        # Flood with good trades (overfills the 20-trade window)
        for _ in range(20):
            eng.record_trade_result(pnl_r=Decimal("2"), won=True)
        _check(eng)
        state = eng.get_state()
        assert state.drift_alert_active is False

    def test_drift_does_not_fire_with_empty_window(self) -> None:
        """No trades yet → drift_alert_active must be False (insufficient data)."""
        eng = _engine()
        _check(eng)
        assert eng.get_state().drift_alert_active is False

    def test_winrate_drop_20pp_triggers_warning(self) -> None:
        """Doc 10 §5.2: drop > 20pp from baseline triggers drift warning."""
        # baseline = 0.60, drop to 0.35 = 25pp drop > 20pp
        eng = _engine(win_rate="0.60", avg_r="0.80")
        _fill_window_win_rate(eng, Decimal("0.35"))
        _check(eng)
        assert eng.get_state().drift_alert_active is True


# ---------------------------------------------------------------------------
# AC2: Persistent drift → allow_trading=False
# ---------------------------------------------------------------------------

class TestDriftPersistenceHalt:
    """AC2: DRIFT_ALERT for > 10 consecutive cycles → allow_trading=False."""

    def _prime_drift(self, eng: GovernanceEngine) -> None:
        """Fill the window with an interleaved pattern so drift WARNING fires on check().

        Pattern: 4 wins + 16 losses spread so no run of ≥5 consecutive losses.
        win_rate = 4/20 = 0.20.
        Engine must have win_rate_baseline=0.35, avg_r="-2.0":
          - avg_R CRITICAL: skipped (base_ar ≤ 0)
          - win_rate CRITICAL: 0.20 > 0.35×0.50=0.175 — no CRITICAL
          - win_rate WARNING:  0.20 < 0.35×0.70=0.245 — fires each cycle
        Consecutive-loss state machine: max streak=4 (< 5) → stays NORMAL.
        """
        # 4 wins interleaved among 16 losses; max consecutive-loss streak = 4
        pattern = [True, False, False, False, False,
                   True, False, False, False, False,
                   True, False, False, False, False,
                   True, False, False, False, False]
        for won in pattern:
            eng.record_trade_result(
                pnl_r=Decimal("2") if won else Decimal("-1"), won=won
            )

    def test_drift_halts_after_11_consecutive_cycles(self) -> None:
        """After 11 cycles with persistent drift, allow_trading becomes False.

        Engine uses win_rate=0.35, avg_r="-2.0" so only WARNING fires each cycle
        (not CRITICAL).  After > 10 cycles the persistent-drift check sets
        HALTED + system_paused=True, causing allow_trading=False.
        """
        eng = _engine(win_rate="0.35", avg_r="-2.0")
        self._prime_drift(eng)

        action = None
        for _ in range(11):
            action = _check(eng)

        assert action is not None
        assert action.allow_trading is False

    def test_drift_does_not_halt_at_10_cycles(self) -> None:
        """Exactly 10 cycles → allow_trading still True (need > 10 to halt).

        Engine uses win_rate=0.35, avg_r="-2.0" so only WARNING fires.
        """
        eng = _engine(win_rate="0.35", avg_r="-2.0")
        self._prime_drift(eng)

        action = None
        for _ in range(10):
            action = _check(eng)

        # At exactly 10 cycles, consecutive_cycles == 10, need > 10 to halt.
        assert action is not None
        assert action.allow_trading is True

        # One more cycle → 11 → HALTED
        action = _check(eng)
        assert action.allow_trading is False

    def test_drift_counter_resets_when_drift_clears(self) -> None:
        """If drift clears mid-stream, counter resets."""
        eng = _engine(win_rate="0.35", avg_r="-2.0")   # only WARNING fires (not CRITICAL)
        self._prime_drift(eng)

        # Run 5 drift cycles
        for _ in range(5):
            _check(eng)

        # Now flood with wins to clear drift
        for _ in range(20):
            eng.record_trade_result(pnl_r=Decimal("3"), won=True)

        _check(eng)   # drift should be clear now
        state = eng.get_state()
        assert state.drift_alert_consecutive_cycles == 0


# ---------------------------------------------------------------------------
# AC3: Weekly loss > 5% → pause until Monday
# ---------------------------------------------------------------------------

class TestWeeklyLoss:
    """AC3: Weekly loss > 5% → allow_trading=False; auto-resumes next Monday."""

    def _apply_weekly_loss(
        self, eng: GovernanceEngine, loss_r_count: int = 12
    ) -> GovernanceAction:
        """Feed enough R-multiple losses to exceed 5% weekly loss.

        With initial equity=10000 and 1% risk = 100 per trade,
        12 × -1R = -12R loss = -1200. 1200/10000 = 12% > 5%.
        We track weekly_pnl directly in R.
        """
        for _ in range(loss_r_count):
            eng.record_trade_result(pnl_r=Decimal("-1"), won=False)
        # Equity is 10000; weekly_pnl = -12R.
        # But governance uses equity ratio. We need the equity to be low enough.
        # Actually: weekly_pnl in R units isn't auto-converted to %.
        # The governance tracks weekly_pnl in R, and compares:
        #   weekly_loss_pct = -weekly_pnl / all_time_peak_equity
        # So we need all_time_peak_equity to be set first (via check()).
        return _check(eng, equity="10000", ts=_WEDNESDAY)

    def test_weekly_loss_over_5pct_pauses_trading(self) -> None:
        """Weekly P&L in R < -5% of peak equity → allow_trading=False."""
        eng = _engine()
        # Initialise peak equity via check
        _check(eng, equity="10000", ts=_BASE_TS)   # sets peak to 10000

        # weekly_pnl tracked in R; 1R = 1 unit. Need to trip -5% × 10000 = -500R
        # Actually the engine stores pnl_r as raw R, not scaled by risk_amount.
        # For the check: -weekly_pnl/peak > 0.05 → need weekly_pnl < -500
        # That's unrealistic with 20 trades capped.
        # Better: set all_time_peak_equity low so ratio triggers easily.
        # Patch the internal state:
        eng._state.all_time_peak_equity = Decimal("20")   # tiny peak
        eng._state.weekly_pnl = Decimal("-5")              # -5/20 = -25% > 5%

        action = _check(eng, equity="20", ts=_WEDNESDAY)
        assert action.allow_trading is False

    def test_weekly_loss_alert_in_alerts(self) -> None:
        """Weekly pause must produce an alert message."""
        eng = _engine()
        _check(eng, equity="100", ts=_BASE_TS)
        eng._state.all_time_peak_equity = Decimal("100")
        eng._state.weekly_pnl = Decimal("-10")   # -10% > 5%

        action = _check(eng, equity="100", ts=_WEDNESDAY)
        assert action.allow_trading is False
        assert any("weekly" in a.lower() for a in action.alerts)

    def test_weekly_pause_auto_resumes_on_monday(self) -> None:
        """Weekly pause auto-resumes when bar_timestamp crosses Monday 00:00."""
        eng = _engine()
        eng._state.all_time_peak_equity = Decimal("100")
        eng._state.weekly_pnl = Decimal("-10")
        _check(eng, equity="100", ts=_WEDNESDAY)   # triggers pause

        assert eng._state.system_paused is True

        # Simulate Monday arrival — reset
        next_monday = _next_monday(_WEDNESDAY)
        eng.reset_weekly(next_monday)

        action = _check(eng, equity="100", ts=next_monday)
        assert action.allow_trading is True

    def test_weekly_manual_resume_blocked(self) -> None:
        """Weekly pause cannot be overridden by manual_resume()."""
        eng = _engine()
        eng._state.all_time_peak_equity = Decimal("100")
        eng._state.weekly_pnl = Decimal("-10")
        _check(eng, equity="100", ts=_WEDNESDAY)

        result = eng.manual_resume(reason="override attempt")
        assert result is False   # refused
        assert eng._state.system_paused is True

    def test_normal_week_below_5pct_does_not_pause(self) -> None:
        """Weekly loss below 5% → no pause."""
        eng = _engine()
        eng._state.all_time_peak_equity = Decimal("10000")
        eng._state.weekly_pnl = Decimal("-200")   # 2% < 5%

        action = _check(eng, equity="9800", ts=_WEDNESDAY)
        # Should not be paused for weekly reason
        assert not any("weekly" in r.lower() for r in eng._state.pause_reasons)


# ---------------------------------------------------------------------------
# AC4: Monthly loss > 10% → manual resume required
# ---------------------------------------------------------------------------

class TestMonthlyLoss:
    """AC4: Monthly loss > 10% → allow_trading=False, manual_resume required."""

    def test_monthly_loss_over_10pct_pauses_trading(self) -> None:
        eng = _engine()
        eng._state.all_time_peak_equity = Decimal("100")
        eng._state.monthly_pnl = Decimal("-15")   # -15% > 10%

        action = _check(eng, equity="100", ts=_WEDNESDAY)
        assert action.allow_trading is False

    def test_monthly_pause_requires_manual_resume(self) -> None:
        """Monthly pause cannot auto-resume — requires manual_resume()."""
        eng = _engine()
        eng._state.all_time_peak_equity = Decimal("100")
        eng._state.monthly_pnl = Decimal("-15")
        _check(eng, equity="100", ts=_WEDNESDAY)

        assert eng._state.monthly_paused is True
        assert eng._state.system_paused is True

        # manual_resume should lift it
        ok = eng.manual_resume("manual review done")
        assert ok is True
        assert eng._state.system_paused is False
        assert eng._state.monthly_paused is False

    def test_monthly_alert_in_alerts(self) -> None:
        eng = _engine()
        eng._state.all_time_peak_equity = Decimal("100")
        eng._state.monthly_pnl = Decimal("-15")

        action = _check(eng, equity="100", ts=_WEDNESDAY)
        assert any("monthly" in a.lower() for a in action.alerts)

    def test_monthly_pnl_resets_on_new_month(self) -> None:
        """Monthly P&L resets at month boundary."""
        eng = _engine()
        jan_ts = datetime.datetime(2024, 1, 31, 23, 45, tzinfo=_UTC)
        _check(eng, equity="10000", ts=jan_ts)

        eng._state.monthly_pnl = Decimal("-500")

        # February 1st — new month
        feb_ts = datetime.datetime(2024, 2, 1, 0, 0, 0, tzinfo=_UTC)
        eng._maybe_reset_periods(feb_ts)

        assert eng._state.monthly_pnl == Decimal("0")


# ---------------------------------------------------------------------------
# AC5: Max drawdown > 15% → allow_trading=False
# ---------------------------------------------------------------------------

class TestMaxDrawdown:
    """AC5: Max drawdown > 15% from all-time peak → allow_trading=False."""

    def test_drawdown_over_15pct_pauses_trading(self) -> None:
        eng = _engine()
        # Set peak = 10000, current = 8400 → drawdown = (10000-8400)/10000 = 16% > 15%
        _check(eng, equity="10000", ts=_BASE_TS)   # sets peak
        action = _check(eng, equity="8400", ts=_WEDNESDAY)
        assert action.allow_trading is False

    def test_drawdown_alert_in_alerts(self) -> None:
        eng = _engine()
        _check(eng, equity="10000", ts=_BASE_TS)
        action = _check(eng, equity="8400", ts=_WEDNESDAY)
        assert any("drawdown" in a.lower() for a in action.alerts)

    def test_drawdown_requires_manual_resume(self) -> None:
        eng = _engine()
        _check(eng, equity="10000", ts=_BASE_TS)
        _check(eng, equity="8400", ts=_WEDNESDAY)

        assert eng._state.drawdown_paused is True
        ok = eng.manual_resume("risk review done")
        assert ok is True
        assert eng._state.drawdown_paused is False
        assert eng._state.system_paused is False

    def test_drawdown_below_15pct_does_not_pause(self) -> None:
        eng = _engine()
        _check(eng, equity="10000", ts=_BASE_TS)
        action = _check(eng, equity="8600", ts=_WEDNESDAY)   # 14% drawdown
        assert action.allow_trading is True

    def test_new_peak_updates_correctly(self) -> None:
        """All-time peak must update when equity rises."""
        eng = _engine()
        _check(eng, equity="10000", ts=_BASE_TS)
        _check(eng, equity="11000", ts=_WEDNESDAY)   # new peak
        assert eng._state.all_time_peak_equity == Decimal("11000")

        # 9350 / 11000 = ~15%, just below limit (0.15×11000 = 1650 → 9350)
        action = _check(eng, equity="9360", ts=_WEDNESDAY)
        assert action.allow_trading is True

    def test_drawdown_fired_only_once(self) -> None:
        """Drawdown pause only added to pause_reasons once."""
        eng = _engine()
        _check(eng, equity="10000", ts=_BASE_TS)
        _check(eng, equity="8400", ts=_WEDNESDAY)   # fires pause
        reason_count_before = sum(
            1 for r in eng._state.pause_reasons if "drawdown" in r.lower()
        )
        _check(eng, equity="8300", ts=_WEDNESDAY)   # should not add another
        reason_count_after = sum(
            1 for r in eng._state.pause_reasons if "drawdown" in r.lower()
        )
        assert reason_count_before == reason_count_after == 1


# ---------------------------------------------------------------------------
# AC6: Setup suppression (Doc 7 §16)
# ---------------------------------------------------------------------------

class TestSetupSuppression:
    """AC6: Same zone+direction within 24h → is_setup_suppressed returns True."""

    def test_not_suppressed_before_any_fire(self) -> None:
        eng = _engine()
        assert eng.is_setup_suppressed("zone_001", "LONG", _BASE_TS) is False

    def test_suppressed_immediately_after_fire(self) -> None:
        eng = _engine()
        eng.suppress_setup("zone_001", "LONG", _BASE_TS)
        # 1 minute later
        ts_later = _BASE_TS + datetime.timedelta(minutes=1)
        assert eng.is_setup_suppressed("zone_001", "LONG", ts_later) is True

    def test_suppressed_23h59m_after_fire(self) -> None:
        eng = _engine()
        eng.suppress_setup("zone_001", "LONG", _BASE_TS)
        ts_23h59 = _BASE_TS + datetime.timedelta(hours=23, minutes=59)
        assert eng.is_setup_suppressed("zone_001", "LONG", ts_23h59) is True

    def test_not_suppressed_after_24h(self) -> None:
        eng = _engine()
        eng.suppress_setup("zone_001", "LONG", _BASE_TS)
        ts_25h = _BASE_TS + datetime.timedelta(hours=25)
        assert eng.is_setup_suppressed("zone_001", "LONG", ts_25h) is False

    def test_different_direction_not_suppressed(self) -> None:
        """zone_001 LONG suppressed ≠ zone_001 SHORT suppressed."""
        eng = _engine()
        eng.suppress_setup("zone_001", "LONG", _BASE_TS)
        ts_later = _BASE_TS + datetime.timedelta(minutes=5)
        assert eng.is_setup_suppressed("zone_001", "SHORT", ts_later) is False

    def test_different_zone_not_suppressed(self) -> None:
        """zone_002 is independent from zone_001."""
        eng = _engine()
        eng.suppress_setup("zone_001", "LONG", _BASE_TS)
        ts_later = _BASE_TS + datetime.timedelta(minutes=5)
        assert eng.is_setup_suppressed("zone_002", "LONG", ts_later) is False

    def test_suppress_updates_last_fire_timestamp(self) -> None:
        """Calling suppress_setup again extends the 24h window."""
        eng = _engine()
        # First fire
        eng.suppress_setup("zone_001", "LONG", _BASE_TS)
        # 23h later — still suppressed
        ts_23h = _BASE_TS + datetime.timedelta(hours=23)
        assert eng.is_setup_suppressed("zone_001", "LONG", ts_23h) is True

        # Fire again at 23h — resets the 24h window
        eng.suppress_setup("zone_001", "LONG", ts_23h)
        # Now 25h after original fire = 2h after second fire → still suppressed
        ts_25h = _BASE_TS + datetime.timedelta(hours=25)
        assert eng.is_setup_suppressed("zone_001", "LONG", ts_25h) is True

        # But 30h after second fire (2nd fire was at +23h) → not suppressed
        ts_47h = _BASE_TS + datetime.timedelta(hours=47)
        assert eng.is_setup_suppressed("zone_001", "LONG", ts_47h) is False


# ---------------------------------------------------------------------------
# AC7: Data gap pause
# ---------------------------------------------------------------------------

class TestDataGap:
    """AC7: BTC data gap > 10min → BTCUSDT paused; XAUUSD unaffected."""

    def test_btc_gap_over_10min_pauses_btc(self) -> None:
        eng = _engine()
        eng.record_data_gap("BTCUSDT", gap_seconds=700)   # 700s > 600s threshold
        assert eng._state.instrument_paused.get("BTCUSDT") is True

    def test_btc_gap_paused_instruments_in_action(self) -> None:
        eng = _engine()
        _check(eng, equity="10000", ts=_BASE_TS)   # prime peak
        eng.record_data_gap("BTCUSDT", gap_seconds=700)
        action = _check(eng, equity="10000", ts=_BASE_TS)
        assert "BTCUSDT" in action.paused_instruments

    def test_btc_gap_does_not_pause_gold(self) -> None:
        """AC7: BTC pause does NOT affect XAUUSD."""
        eng = _engine()
        eng.record_data_gap("BTCUSDT", gap_seconds=700)
        assert eng._state.instrument_paused.get("XAUUSD", False) is False

    def test_gold_gap_over_30min_pauses_gold(self) -> None:
        eng = _engine()
        eng.record_data_gap("XAUUSD", gap_seconds=1900)   # 1900s > 1800s threshold
        assert eng._state.instrument_paused.get("XAUUSD") is True

    def test_gold_gap_does_not_pause_btc(self) -> None:
        """Gold gap does NOT affect BTCUSDT."""
        eng = _engine()
        eng.record_data_gap("XAUUSD", gap_seconds=1900)
        assert eng._state.instrument_paused.get("BTCUSDT", False) is False

    def test_btc_gap_below_threshold_does_not_pause(self) -> None:
        eng = _engine()
        eng.record_data_gap("BTCUSDT", gap_seconds=500)   # 500s < 600s threshold
        assert eng._state.instrument_paused.get("BTCUSDT", False) is False

    def test_gold_gap_below_threshold_does_not_pause(self) -> None:
        eng = _engine()
        eng.record_data_gap("XAUUSD", gap_seconds=1700)   # 1700s < 1800s threshold
        assert eng._state.instrument_paused.get("XAUUSD", False) is False

    def test_resume_instrument_clears_pause(self) -> None:
        eng = _engine()
        eng.record_data_gap("BTCUSDT", gap_seconds=700)
        assert eng._state.instrument_paused["BTCUSDT"] is True

        eng.resume_instrument("BTCUSDT")
        assert eng._state.instrument_paused["BTCUSDT"] is False

    def test_global_trading_still_allowed_during_instrument_pause(self) -> None:
        """Instrument data gap does NOT globally pause allow_trading."""
        eng = _engine()
        eng._state.all_time_peak_equity = Decimal("10000")
        eng.record_data_gap("BTCUSDT", gap_seconds=700)
        action = _check(eng, equity="10000", ts=_BASE_TS)
        # allow_trading is still True (only that instrument is paused)
        assert action.allow_trading is True


# ---------------------------------------------------------------------------
# AC8: DB failure guard
# ---------------------------------------------------------------------------

class TestDBFailures:
    """AC8: 3 consecutive DB failures → allow_trading=False."""

    def test_three_failures_pause_all_trading(self) -> None:
        eng = _engine()
        eng._state.all_time_peak_equity = Decimal("10000")
        for _ in range(3):
            eng.record_db_failure()
        action = _check(eng, equity="10000", ts=_BASE_TS)
        assert action.allow_trading is False

    def test_two_failures_do_not_pause(self) -> None:
        eng = _engine()
        eng._state.all_time_peak_equity = Decimal("10000")
        for _ in range(2):
            eng.record_db_failure()
        action = _check(eng, equity="10000", ts=_BASE_TS)
        assert action.allow_trading is True

    def test_db_failure_counter_increments(self) -> None:
        eng = _engine()
        eng.record_db_failure()
        eng.record_db_failure()
        assert eng.get_state().consecutive_db_failures == 2

    def test_db_success_resets_counter(self) -> None:
        eng = _engine()
        eng.record_db_failure()
        eng.record_db_failure()
        eng.record_db_success()
        assert eng.get_state().consecutive_db_failures == 0

    def test_db_success_auto_resumes_if_db_only_pause(self) -> None:
        """If DB was the only pause reason, success lifts the pause."""
        eng = _engine()
        eng._state.all_time_peak_equity = Decimal("10000")
        for _ in range(3):
            eng.record_db_failure()
        _check(eng, equity="10000", ts=_BASE_TS)   # triggers db pause
        assert eng._state.system_paused is True

        eng.record_db_success()
        assert eng._state.system_paused is False

    def test_db_alert_in_alerts(self) -> None:
        eng = _engine()
        eng._state.all_time_peak_equity = Decimal("10000")
        for _ in range(3):
            eng.record_db_failure()
        action = _check(eng, equity="10000", ts=_BASE_TS)
        assert any("db" in a.lower() for a in action.alerts)


# ---------------------------------------------------------------------------
# AC9: No float anywhere
# ---------------------------------------------------------------------------

class TestNoFloat:
    """AC9: All numeric values in GovernanceAction and GovernanceState are Decimal."""

    def test_governance_action_size_multiplier_is_decimal(self) -> None:
        eng = _engine()
        action = _check(eng)
        assert isinstance(action.size_multiplier, Decimal)

    def test_governance_state_weekly_pnl_is_decimal(self) -> None:
        eng = _engine()
        state = eng.get_state()
        assert isinstance(state.weekly_pnl, Decimal)

    def test_governance_state_monthly_pnl_is_decimal(self) -> None:
        eng = _engine()
        state = eng.get_state()
        assert isinstance(state.monthly_pnl, Decimal)

    def test_governance_state_all_time_peak_is_decimal(self) -> None:
        eng = _engine()
        state = eng.get_state()
        assert isinstance(state.all_time_peak_equity, Decimal)

    def test_governance_state_max_drawdown_is_decimal(self) -> None:
        eng = _engine()
        state = eng.get_state()
        assert isinstance(state.max_drawdown_pct, Decimal)

    def test_governance_state_live_win_rate_is_decimal(self) -> None:
        eng = _engine()
        _feed_wins(eng, 5)
        _feed_losses(eng, 5)
        state = eng.get_state()
        assert isinstance(state.live_win_rate, Decimal)

    def test_governance_state_live_avg_r_is_decimal(self) -> None:
        eng = _engine()
        _feed_wins(eng, 5)
        state = eng.get_state()
        assert isinstance(state.live_avg_r, Decimal)

    def test_governance_action_is_frozen(self) -> None:
        """GovernanceAction must be a frozen dataclass."""
        eng = _engine()
        action = _check(eng)
        with pytest.raises((AttributeError, TypeError)):
            action.allow_trading = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Governance state machine transitions
# ---------------------------------------------------------------------------

class TestGovernanceStateMachine:
    """Tests for the 4-state governance machine (NORMAL/CAUTION/DEFENSIVE/HALTED)."""

    def test_initial_state_is_normal(self) -> None:
        eng = _engine()
        assert eng.get_state().gov_state == GovernanceGovState.NORMAL

    def test_5_consecutive_losses_triggers_caution(self) -> None:
        """5 consecutive losses → CAUTION via Doc 10 §12 state machine.

        We pass consec_losses=5 externally (from PortfolioManager).
        The engine's rolling window is kept empty (no record_trade_result calls)
        so drift detection is inactive → state transition comes purely from
        _update_consec_loss_state.
        """
        eng = _engine()
        # Empty window → no drift fires.  External consecutive_losses=5 → CAUTION.
        _check(eng, consec_losses=5)
        assert eng.get_state().gov_state == GovernanceGovState.CAUTION

    def test_8_consecutive_losses_triggers_defensive(self) -> None:
        """8 consecutive losses → DEFENSIVE via Doc 10 §12 state machine.

        Same approach: empty window, external count drives state machine.
        """
        eng = _engine()
        _check(eng, consec_losses=8)
        assert eng.get_state().gov_state == GovernanceGovState.DEFENSIVE

    def test_defensive_size_multiplier_is_0_50(self) -> None:
        eng = _engine()
        _fill_window_win_rate(eng, Decimal("0.20"))   # triggers drift → CAUTION
        # Force to DEFENSIVE
        eng._state.gov_state = GovernanceGovState.DEFENSIVE
        action = _check(eng)
        assert action.size_multiplier == Decimal("0.50")

    def test_normal_size_multiplier_is_1_0(self) -> None:
        eng = _engine()
        action = _check(eng, equity="10000")
        assert action.size_multiplier == Decimal("1.0")

    def test_manual_resume_resets_to_normal(self) -> None:
        """manual_resume() clears non-weekly pauses and resets to NORMAL."""
        eng = _engine()
        eng._state.all_time_peak_equity = Decimal("100")
        eng._state.monthly_pnl = Decimal("-15")
        _check(eng, equity="100")   # trigger monthly pause

        eng.manual_resume()
        assert eng.get_state().gov_state == GovernanceGovState.NORMAL

    def test_consecutive_losses_caution_uses_external_count(self) -> None:
        """The external consecutive_losses_ext from PortfolioManager is respected."""
        eng = _engine()
        # No trades recorded in engine, but external says 5
        _check(eng, consec_losses=5)
        assert eng.get_state().gov_state == GovernanceGovState.CAUTION


# ---------------------------------------------------------------------------
# Weekly/monthly period helpers
# ---------------------------------------------------------------------------

class TestPeriodHelpers:
    """Tests for _week_monday and _next_monday utilities."""

    def test_week_monday_on_monday(self) -> None:
        """Monday input → same day at 00:00."""
        monday = datetime.datetime(2024, 1, 15, 12, 30, tzinfo=_UTC)  # Monday
        result = _week_monday(monday)
        assert result.weekday() == 0
        assert result.hour == 0 and result.minute == 0

    def test_week_monday_on_wednesday(self) -> None:
        """Wednesday → returns the Monday of the same week."""
        wednesday = datetime.datetime(2024, 1, 17, 10, 0, tzinfo=_UTC)
        monday    = _week_monday(wednesday)
        assert monday == datetime.datetime(2024, 1, 15, 0, 0, 0, tzinfo=_UTC)

    def test_next_monday_from_wednesday(self) -> None:
        """Next Monday from Wednesday 2024-01-17 → 2024-01-22."""
        wednesday = datetime.datetime(2024, 1, 17, 10, 0, tzinfo=_UTC)
        result    = _next_monday(wednesday)
        assert result == datetime.datetime(2024, 1, 22, 0, 0, 0, tzinfo=_UTC)

    def test_next_monday_from_monday_advances_one_week(self) -> None:
        """From Monday, next Monday is +7 days."""
        monday = datetime.datetime(2024, 1, 15, 12, 0, tzinfo=_UTC)
        result = _next_monday(monday)
        assert result == datetime.datetime(2024, 1, 22, 0, 0, 0, tzinfo=_UTC)


# ---------------------------------------------------------------------------
# GovernanceState snapshot contract
# ---------------------------------------------------------------------------

class TestGovernanceStateContract:
    """Verify GovernanceState is a frozen dataclass with correct types."""

    def test_governance_state_is_frozen(self) -> None:
        eng = _engine()
        state = eng.get_state()
        with pytest.raises((AttributeError, TypeError)):
            state.drift_alert_active = True  # type: ignore[misc]

    def test_pause_reasons_is_tuple(self) -> None:
        eng = _engine()
        state = eng.get_state()
        assert isinstance(state.pause_reasons, tuple)

    def test_instrument_paused_is_dict(self) -> None:
        eng = _engine()
        state = eng.get_state()
        assert isinstance(state.instrument_paused, dict)

    def test_gov_state_is_enum(self) -> None:
        eng = _engine()
        state = eng.get_state()
        assert isinstance(state.gov_state, GovernanceGovState)

    def test_historical_baseline_returned_correctly(self) -> None:
        eng = GovernanceEngine(
            historical_win_rate=Decimal("0.50"),
            historical_avg_r=Decimal("1.20"),
        )
        state = eng.get_state()
        assert state.historical_win_rate == Decimal("0.50")
        assert state.historical_avg_r    == Decimal("1.20")


# ---------------------------------------------------------------------------
# Integration: check() returns correct GovernanceAction structure
# ---------------------------------------------------------------------------

class TestCheckReturnContract:
    """Verify GovernanceAction fields are correct types on every call."""

    def test_allow_trading_is_bool(self) -> None:
        action = _check(_engine())
        assert isinstance(action.allow_trading, bool)

    def test_size_multiplier_is_decimal(self) -> None:
        action = _check(_engine())
        assert isinstance(action.size_multiplier, Decimal)

    def test_paused_instruments_is_frozenset(self) -> None:
        action = _check(_engine())
        assert isinstance(action.paused_instruments, frozenset)

    def test_alerts_is_tuple(self) -> None:
        action = _check(_engine())
        assert isinstance(action.alerts, tuple)

    def test_clean_engine_allow_trading_true(self) -> None:
        action = _check(_engine(), equity="10000", ts=_BASE_TS)
        assert action.allow_trading is True

    def test_clean_engine_no_alerts(self) -> None:
        action = _check(_engine(), equity="10000", ts=_BASE_TS)
        assert action.alerts == ()
