"""Doc 10 (Governance, Drift Detection & Safety Controls).

System-level safety controls that sit ABOVE the PortfolioManager.
Run after every pipeline cycle, before the next cycle starts.

GOVERNANCE CHECKS (performed on every call to check()):
  1. Drift detection   — rolling 20-trade window; win_rate / avg_R vs baseline
  2. Weekly loss limit — > 5% of equity → pause instruments until Monday
  3. Monthly loss limit— > 10% of equity → pause all, require manual resume
  4. Max drawdown      — > 15% from all-time peak → pause all, require manual resume
  5. Setup suppression — same zone+direction within 24h → suppress (Doc 7 §16)
  6. Data-gap pauses   — BTC > 10 min, Gold > 30 min → per-instrument pause
  7. DB failure guard  — 3 consecutive write failures → pause all

GOVERNANCE STATES (Doc 10 §6):
  NORMAL    — full operation
  CAUTION   — reduce size 25% (size_multiplier=0.75)
  DEFENSIVE — reduce size 50% (size_multiplier=0.50); S/A tiers only
  HALTED    — no new trades (allow_trading=False)

STATE TRANSITIONS (Doc 10 §7):
  NORMAL    → CAUTION   : any single WARNING (winrate drop > 20pp, or expectancy < 0.70×)
  CAUTION   → DEFENSIVE : repeated warning within 20 trades, OR drift persists > 10 cycles
  DEFENSIVE → HALTED    : CRITICAL trigger (expectancy < 0.50×)
  Recovery              : metrics normalise for ≥ 30 trades

All arithmetic uses Decimal. No float anywhere. All timestamps UTC.
Deterministic: given identical inputs, produces identical outputs.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from enum import Enum, auto
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_EPSILON = Decimal("1E-9")
_D_ZERO  = Decimal("0")
_D_ONE   = Decimal("1")

# Rolling window size for drift detection (Doc 10 §4)
_DRIFT_WINDOW = 20

# Drift thresholds (Doc 10 §5)
_DRIFT_WARNING_RATIO  = Decimal("0.70")   # live < baseline × 0.70 → WARNING
_DRIFT_CRITICAL_RATIO = Decimal("0.50")   # live < baseline × 0.50 → CRITICAL
_WINRATE_DROP_WARNING = Decimal("0.20")   # 20 percentage-point drop → WARNING

# How many consecutive drift-alert cycles before escalation to DEFENSIVE
_DRIFT_CONSECUTIVE_HALT = 10   # > 10 → pause (user spec)

# Weekly/monthly loss limits (user spec)
_WEEKLY_LOSS_LIMIT  = Decimal("0.05")   # 5%  of equity
_MONTHLY_LOSS_LIMIT = Decimal("0.10")   # 10% of equity

# Max drawdown from all-time peak (user spec)
_MAX_DRAWDOWN_LIMIT = Decimal("0.15")   # 15%

# Setup suppression window (Doc 7 §16): 24 hours in seconds
_SUPPRESSION_WINDOW_SECONDS = 86400

# Data-gap thresholds (user spec)
_BTC_GAP_THRESHOLD_SECONDS  = 600    # 10 minutes
_GOLD_GAP_THRESHOLD_SECONDS = 1800   # 30 minutes

# DB failure threshold (user spec)
_DB_FAILURE_THRESHOLD = 3

# Consecutive loss thresholds (Doc 10 §12)
_CONSECUTIVE_LOSS_CAUTION   = 5
_CONSECUTIVE_LOSS_DEFENSIVE = 8

# State transition: recovery requires normalised metrics for ≥ N trades (Doc 10 §7)
_RECOVERY_TRADE_COUNT = 30

# Size multipliers per governance state
_SIZE_MULT: dict[str, Decimal] = {
    "NORMAL":    Decimal("1.0"),
    "CAUTION":   Decimal("0.75"),   # Doc 10 §6.2: reduce 25%
    "DEFENSIVE": Decimal("0.50"),   # Doc 10 §6.3: reduce 50%
    "HALTED":    Decimal("0.50"),   # no new trades regardless
}


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class GovernanceGovState(Enum):
    """Doc 10 §6: Four governance operating states."""
    NORMAL    = auto()
    CAUTION   = auto()
    DEFENSIVE = auto()
    HALTED    = auto()


class _PauseReason(Enum):
    """Internal: reasons why all-instruments trading was paused."""
    DRIFT_PERSISTENT    = auto()   # drift alert > 10 cycles → HALTED
    WEEKLY_LOSS         = auto()   # auto-resumes Monday
    MONTHLY_LOSS        = auto()   # manual resume required
    MAX_DRAWDOWN        = auto()   # manual resume required
    DB_FAILURES         = auto()   # auto-resumes after db_success × 1
    MANUAL              = auto()   # explicit manual pause


# ---------------------------------------------------------------------------
# GovernanceState — immutable snapshot
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GovernanceState:
    """Immutable snapshot of governance engine state at one point in time.

    Returned by GovernanceEngine.get_state().
    All numeric fields are Decimal.
    """
    # Governance operating state
    gov_state: GovernanceGovState

    # Drift detection
    drift_alert_active:            bool
    drift_alert_consecutive_cycles: int
    live_win_rate:                 Decimal   # 0.0 – 1.0; None → insufficient trades → -1
    live_avg_r:                    Decimal   # may be negative
    historical_win_rate:           Decimal
    historical_avg_r:              Decimal

    # Weekly / monthly / drawdown tracking
    weekly_pnl:          Decimal
    monthly_pnl:         Decimal
    all_time_peak_equity: Decimal
    max_drawdown_pct:    Decimal   # (peak - current) / peak

    # DB safety
    consecutive_db_failures: int

    # Pause state
    system_paused:    bool
    pause_reasons:    tuple   # tuple[str, ...]
    instrument_paused: dict   # str → bool  ("BTCUSDT", "XAUUSD")

    # Consecutive losses (Doc 10 §12)
    consecutive_losses: int

    # Recovery tracking
    trades_since_last_warning: int


# ---------------------------------------------------------------------------
# GovernanceAction — output of check()
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GovernanceAction:
    """Immutable result of GovernanceEngine.check().

    Consumed by the Orchestrator each cycle to gate trading.
    """
    allow_trading:      bool       # False → do not open new positions
    size_multiplier:    Decimal    # 1.0 normal, 0.75 caution, 0.5 defensive
    paused_instruments: frozenset  # frozenset[str] of instrument symbols paused
    alerts:             tuple      # tuple[str, ...] human-readable alerts


# ---------------------------------------------------------------------------
# Internal mutable state
# ---------------------------------------------------------------------------

@dataclass
class _State:
    """All mutable internal state — kept private inside GovernanceEngine."""

    # Governance state machine
    gov_state: GovernanceGovState = GovernanceGovState.NORMAL

    # Rolling trade window (deque of (pnl_r, won) tuples)
    # pnl_r: realised P&L expressed in R multiples (pnl / risk_amount)
    # won:   bool
    trade_window: deque = field(default_factory=lambda: deque(maxlen=_DRIFT_WINDOW))

    # Historical baseline (set externally via set_baseline or default)
    historical_win_rate: Decimal = Decimal("0.45")   # 45% default
    historical_avg_r:    Decimal = Decimal("0.80")   # 0.8R default

    # Drift
    drift_alert_active:             bool = False
    drift_alert_consecutive_cycles: int  = 0

    # Weekly / monthly P&L
    weekly_pnl:  Decimal = _D_ZERO
    monthly_pnl: Decimal = _D_ZERO

    # Period tracking (week/month boundary by bar_timestamp)
    current_week_monday:    Optional[datetime] = None   # Monday 00:00 UTC
    current_month_start:    Optional[datetime] = None   # 1st 00:00 UTC

    # Equity tracking
    all_time_peak_equity: Decimal = _D_ZERO

    # DB safety
    consecutive_db_failures: int = 0

    # Per-instrument pauses (keys: instrument symbols)
    instrument_paused: dict = field(default_factory=dict)

    # Global pause
    system_paused: bool = False
    pause_reasons:  list = field(default_factory=list)   # list[str]

    # Setup suppression: (zone_id, direction_name) → UTC datetime of last fire
    suppressed_setups: dict = field(default_factory=dict)

    # Consecutive losses (Doc 10 §12)
    consecutive_losses: int = 0

    # Recovery tracking
    trades_since_last_warning: int = 0

    # Weekly pause bookkeeping
    weekly_paused_until: Optional[datetime] = None   # Monday reset time

    # Monthly pause flag (manual resume required)
    monthly_paused: bool = False

    # Max drawdown pause flag (manual resume required)
    drawdown_paused: bool = False


# ---------------------------------------------------------------------------
# GovernanceEngine
# ---------------------------------------------------------------------------

class GovernanceEngine:
    """Doc 10: System-level safety controller — sits above PortfolioManager.

    Usage::

        engine = GovernanceEngine()
        engine.set_baseline(historical_win_rate=Decimal("0.45"),
                            historical_avg_r=Decimal("0.80"))

        # After each pipeline cycle:
        action = engine.check(
            cycle_result=result,
            current_equity=Decimal("10000"),
            bar_timestamp=bar.timestamp_end,
        )
        if not action.allow_trading:
            # skip new entries

        # After each trade closes:
        engine.record_trade_result(trade=trade, pnl=pnl)

        # When a data gap is detected:
        engine.record_data_gap("BTCUSDT", gap_seconds=700)

        # After DB operations:
        engine.record_db_success()   # on successful write
        engine.record_db_failure()   # on failed write
    """

    def __init__(
        self,
        historical_win_rate: Decimal = Decimal("0.45"),
        historical_avg_r:    Decimal = Decimal("0.80"),
    ) -> None:
        """Initialise governance engine.

        Args:
            historical_win_rate: Baseline win rate from backtests (0.0–1.0).
            historical_avg_r:    Baseline average R per trade from backtests.
        """
        self._state = _State(
            historical_win_rate = historical_win_rate,
            historical_avg_r    = historical_avg_r,
            instrument_paused   = {"BTCUSDT": False, "XAUUSD": False},
        )

    # ------------------------------------------------------------------
    # Public: set_baseline (can be called after construction)
    # ------------------------------------------------------------------

    def set_baseline(
        self,
        historical_win_rate: Decimal,
        historical_avg_r:    Decimal,
    ) -> None:
        """Update historical performance baseline.

        Args:
            historical_win_rate: Fraction of trades that are winners (0–1).
            historical_avg_r:    Average R-multiple per completed trade.
        """
        s = self._state
        s.historical_win_rate = historical_win_rate
        s.historical_avg_r    = historical_avg_r
        logger.info(
            "GovernanceEngine: baseline set — win_rate=%.3f avg_R=%.3f",
            float(historical_win_rate), float(historical_avg_r),
        )

    # ------------------------------------------------------------------
    # Public: check — main governance gate called every cycle
    # ------------------------------------------------------------------

    def check(
        self,
        current_equity: Decimal,
        bar_timestamp:  datetime,
        consecutive_losses_ext: int = 0,   # from PortfolioManager
    ) -> GovernanceAction:
        """Doc 10 §6-§7: Run all governance checks and return a GovernanceAction.

        Call this AFTER every pipeline cycle, BEFORE starting the next cycle.

        Args:
            current_equity:         Current account equity (Decimal).
            bar_timestamp:          Close timestamp of the just-processed bar (UTC).
            consecutive_losses_ext: Consecutive-loss count from PortfolioManager
                                    (used for Doc 10 §12 state transitions).

        Returns:
            GovernanceAction describing what the next cycle is allowed to do.
        """
        s = self._state
        alerts: list[str] = []

        # --- 0. Period boundary resets (weekly / monthly) ---
        self._maybe_reset_periods(bar_timestamp)

        # --- 0b. Check weekly pause auto-resume ---
        self._maybe_resume_weekly(bar_timestamp)

        # --- 1. Update all-time peak equity ---
        if current_equity > s.all_time_peak_equity:
            s.all_time_peak_equity = current_equity

        # --- 2. Drawdown check (15% from all-time peak) ---
        drawdown = _D_ZERO
        if s.all_time_peak_equity > _EPSILON:
            drawdown = (s.all_time_peak_equity - current_equity) / s.all_time_peak_equity

        if drawdown > _MAX_DRAWDOWN_LIMIT + _EPSILON and not s.drawdown_paused:
            s.drawdown_paused = True
            msg = (f"Max drawdown {_pct(drawdown)} exceeds 15% limit — "
                   "PAUSED (manual resume required)")
            logger.critical(msg)
            self._add_pause_reason(msg)
            s.system_paused = True
            alerts.append(msg)

        # --- 3. Monthly loss check ---
        if s.monthly_pnl < _D_ZERO:
            monthly_loss_pct = (-s.monthly_pnl) / max(s.all_time_peak_equity, _EPSILON)
            if monthly_loss_pct > _MONTHLY_LOSS_LIMIT + _EPSILON and not s.monthly_paused:
                s.monthly_paused = True
                msg = (f"Monthly loss {_pct(monthly_loss_pct)} exceeds 10% limit — "
                       "PAUSED (manual resume required)")
                logger.critical(msg)
                self._add_pause_reason(msg)
                s.system_paused = True
                alerts.append(msg)

        # --- 4. Weekly loss check ---
        if s.weekly_pnl < _D_ZERO:
            weekly_loss_pct = (-s.weekly_pnl) / max(s.all_time_peak_equity, _EPSILON)
            if weekly_loss_pct > _WEEKLY_LOSS_LIMIT + _EPSILON:
                if not any("weekly" in r.lower() for r in s.pause_reasons):
                    # Compute resume time: next Monday 00:00 UTC
                    next_monday = _next_monday(bar_timestamp)
                    s.weekly_paused_until = next_monday
                    msg = (f"Weekly loss {_pct(weekly_loss_pct)} exceeds 5% limit — "
                           f"PAUSED until {next_monday.isoformat()}")
                    logger.critical(msg)
                    self._add_pause_reason(msg)
                    s.system_paused = True
                    alerts.append(msg)

        # --- 5. Consecutive loss state machine (Doc 10 §12) ---
        self._update_consec_loss_state(consecutive_losses_ext, alerts)

        # --- 6. Drift detection ---
        self._update_drift(alerts)

        # --- 7. Drift → HALTED if persistent > 10 cycles ---
        if s.drift_alert_active:
            s.drift_alert_consecutive_cycles += 1
            if s.drift_alert_consecutive_cycles > _DRIFT_CONSECUTIVE_HALT:
                if s.gov_state != GovernanceGovState.HALTED:
                    prev = s.gov_state.name
                    s.gov_state = GovernanceGovState.HALTED
                    msg = (f"Drift alert persisted for "
                           f"{s.drift_alert_consecutive_cycles} cycles — "
                           f"HALTED (was {prev})")
                    logger.critical(msg)
                    self._add_pause_reason(msg)
                    s.system_paused = True
                    alerts.append(msg)
        else:
            s.drift_alert_consecutive_cycles = 0

        # --- 8. DB failure check ---
        if s.consecutive_db_failures >= _DB_FAILURE_THRESHOLD:
            if not any("db" in r.lower() for r in s.pause_reasons):
                msg = (f"{s.consecutive_db_failures} consecutive DB failures — "
                       "PAUSED until DB recovers")
                logger.critical(msg)
                self._add_pause_reason(msg)
                s.system_paused = True
                alerts.append(msg)

        # --- 9. Compute allow_trading and size_multiplier ---
        # HALTED state always blocks new trades, regardless of system_paused flag.
        # HALTED can be set by _update_drift (CRITICAL) without setting system_paused,
        # so we must also check gov_state here.
        allow_trading = (
            not s.system_paused
            and s.gov_state != GovernanceGovState.HALTED
        )
        size_mult     = _SIZE_MULT.get(s.gov_state.name, _D_ONE)

        # Paused instruments (data gaps)
        paused_instrs = frozenset(
            sym for sym, paused in s.instrument_paused.items() if paused
        )

        # If global pause also blocks instrument-level trading
        if s.system_paused:
            paused_instrs = frozenset(s.instrument_paused.keys())

        return GovernanceAction(
            allow_trading      = allow_trading,
            size_multiplier    = size_mult,
            paused_instruments = paused_instrs,
            alerts             = tuple(alerts),
        )

    # ------------------------------------------------------------------
    # Public: record_trade_result
    # ------------------------------------------------------------------

    def record_trade_result(self, pnl_r: Decimal, won: bool) -> None:
        """Update rolling trade window with one completed trade result.

        Args:
            pnl_r: Realised P&L in R-multiples (pnl / risk_amount).
                   Positive for winners, negative for losers.
            won:   True if the trade was a winner (pnl > 0).
        """
        s = self._state
        s.trade_window.append((pnl_r, won))

        # Update consecutive losses
        if won:
            s.consecutive_losses = 0
        else:
            s.consecutive_losses += 1

        # Update recovery counter
        s.trades_since_last_warning += 1

        # Weekly / monthly P&L tracking
        s.weekly_pnl  += pnl_r
        s.monthly_pnl += pnl_r

        logger.debug(
            "GovernanceEngine: trade recorded pnl_r=%s won=%s "
            "window_size=%d consecutive_losses=%d",
            pnl_r, won, len(s.trade_window), s.consecutive_losses,
        )

    # ------------------------------------------------------------------
    # Public: record_trade_result_with_amounts (alternative API)
    # ------------------------------------------------------------------

    def record_trade_result_with_amounts(
        self,
        pnl:         Decimal,
        risk_amount: Decimal,
        won:         bool,
    ) -> None:
        """Convenience: compute R-multiple from raw pnl and risk_amount.

        Args:
            pnl:         Realised P&L in quote currency.
            risk_amount: Amount risked on this trade (> 0).
            won:         True if the trade was a winner.
        """
        if risk_amount > _EPSILON:
            pnl_r = pnl / risk_amount
        else:
            pnl_r = pnl
        self.record_trade_result(pnl_r=pnl_r, won=won)

    # ------------------------------------------------------------------
    # Public: record_data_gap
    # ------------------------------------------------------------------

    def record_data_gap(self, instrument: str, gap_seconds: int) -> None:
        """Pause an instrument if its data gap exceeds the threshold.

        Doc user spec:
          BTC  gap > 10 min → pause BTCUSDT only
          Gold gap > 30 min → pause XAUUSD only

        Args:
            instrument:  Symbol key ("BTCUSDT" or "XAUUSD").
            gap_seconds: Duration of the detected data gap in seconds.
        """
        s = self._state
        threshold = (
            _BTC_GAP_THRESHOLD_SECONDS
            if instrument in ("BTCUSDT", "BTC")
            else _GOLD_GAP_THRESHOLD_SECONDS
        )
        if gap_seconds > threshold:
            sym = "BTCUSDT" if instrument in ("BTCUSDT", "BTC") else "XAUUSD"
            if not s.instrument_paused.get(sym, False):
                s.instrument_paused[sym] = True
                msg = (f"Data gap {gap_seconds}s on {sym} exceeds threshold — "
                       "instrument paused")
                logger.warning(msg)

    # ------------------------------------------------------------------
    # Public: resume_instrument
    # ------------------------------------------------------------------

    def resume_instrument(self, instrument: str) -> None:
        """Resume a data-gap-paused instrument.

        Args:
            instrument: Symbol key ("BTCUSDT" or "XAUUSD").
        """
        sym = "BTCUSDT" if instrument in ("BTCUSDT", "BTC") else "XAUUSD"
        self._state.instrument_paused[sym] = False
        logger.info("GovernanceEngine: %s data-gap pause lifted.", sym)

    # ------------------------------------------------------------------
    # Public: record_db_failure / record_db_success
    # ------------------------------------------------------------------

    def record_db_failure(self) -> None:
        """Increment consecutive DB failure counter.

        When the counter reaches _DB_FAILURE_THRESHOLD, the next check()
        call will pause all trading.
        """
        self._state.consecutive_db_failures += 1
        logger.warning(
            "GovernanceEngine: DB failure #%d",
            self._state.consecutive_db_failures,
        )

    def record_db_success(self) -> None:
        """Reset consecutive DB failure counter on a successful DB write."""
        s = self._state
        if s.consecutive_db_failures > 0:
            logger.info(
                "GovernanceEngine: DB success — clearing %d failure(s).",
                s.consecutive_db_failures,
            )
        s.consecutive_db_failures = 0

        # If DB was the only pause reason, auto-resume
        db_reasons = [r for r in s.pause_reasons if "db" in r.lower()]
        if db_reasons and s.system_paused:
            non_db_reasons = [r for r in s.pause_reasons if "db" not in r.lower()]
            s.pause_reasons = non_db_reasons
            if not s.pause_reasons:
                s.system_paused = False
                logger.info("GovernanceEngine: DB paused lifted — trading resumed.")

    # ------------------------------------------------------------------
    # Public: is_setup_suppressed
    # ------------------------------------------------------------------

    def is_setup_suppressed(
        self,
        zone_id:       str,
        direction:     str,
        bar_timestamp: datetime,
    ) -> bool:
        """Doc 7 §16: Return True if this zone+direction fired within 24h.

        Args:
            zone_id:       SR zone identifier.
            direction:     Trade direction ("LONG" or "SHORT").
            bar_timestamp: Current bar timestamp (UTC).

        Returns:
            True if the setup was suppressed (fired too recently).
        """
        key = (zone_id, direction)
        last_fire = self._state.suppressed_setups.get(key)
        if last_fire is None:
            return False
        elapsed = bar_timestamp - last_fire
        return elapsed.total_seconds() < _SUPPRESSION_WINDOW_SECONDS

    # ------------------------------------------------------------------
    # Public: suppress_setup
    # ------------------------------------------------------------------

    def suppress_setup(
        self,
        zone_id:       str,
        direction:     str,
        bar_timestamp: datetime,
    ) -> None:
        """Doc 7 §16: Record that this zone+direction fired at bar_timestamp.

        The next call to is_setup_suppressed within 24h will return True.

        Args:
            zone_id:       SR zone identifier.
            direction:     Trade direction ("LONG" or "SHORT").
            bar_timestamp: Timestamp of the firing bar (UTC).
        """
        key = (zone_id, direction)
        self._state.suppressed_setups[key] = bar_timestamp
        logger.debug(
            "GovernanceEngine: suppress_setup zone=%s dir=%s ts=%s",
            zone_id, direction, bar_timestamp.isoformat(),
        )

    # ------------------------------------------------------------------
    # Public: manual_resume
    # ------------------------------------------------------------------

    def manual_resume(self, reason: str = "") -> bool:
        """Manually resume trading after a governance pause.

        WEEKLY_LOSS pauses auto-resume on Monday — manual_resume is blocked for them.
        MONTHLY_LOSS and MAX_DRAWDOWN require this method.
        DB_FAILURE pauses auto-resume via record_db_success().

        Args:
            reason: Optional string describing why the resume was granted.

        Returns:
            True if the resume was applied. False if there is nothing to resume
            or if a weekly pause is blocking (must wait for Monday).
        """
        s = self._state
        if not s.system_paused:
            return True   # nothing to resume

        # Weekly pause cannot be manually overridden
        weekly_reasons = [r for r in s.pause_reasons if "weekly" in r.lower()]
        non_weekly_reasons = [r for r in s.pause_reasons if "weekly" not in r.lower()]

        if weekly_reasons and not non_weekly_reasons:
            logger.info(
                "GovernanceEngine: manual_resume refused — "
                "weekly pause must auto-resume on Monday."
            )
            return False

        # Resume all non-weekly pauses
        s.monthly_paused    = False
        s.drawdown_paused   = False
        s.system_paused     = False
        s.pause_reasons     = weekly_reasons   # keep weekly if still active
        if weekly_reasons:
            s.system_paused = True   # still paused by weekly

        # Reset governance state to NORMAL on manual resume
        if not s.system_paused:
            s.gov_state = GovernanceGovState.NORMAL
            s.drift_alert_active = False
            s.drift_alert_consecutive_cycles = 0
            s.consecutive_losses = 0
            s.trades_since_last_warning = 0

        logger.info(
            "GovernanceEngine: manual_resume accepted (reason=%r) — "
            "paused=%s state=%s",
            reason, s.system_paused, s.gov_state.name,
        )
        return not s.system_paused

    # ------------------------------------------------------------------
    # Public: reset_weekly (called on Monday 00:00 UTC)
    # ------------------------------------------------------------------

    def reset_weekly(self, bar_timestamp: datetime) -> None:
        """Reset weekly P&L tracking and lift weekly pause if applicable.

        Normally called automatically by check() when the period boundary is crossed.
        Exposed publicly for testing.
        """
        s = self._state
        s.weekly_pnl = _D_ZERO
        s.current_week_monday = _week_monday(bar_timestamp)

        # Lift weekly pause
        weekly_reasons = [r for r in s.pause_reasons if "weekly" in r.lower()]
        if weekly_reasons:
            s.pause_reasons = [r for r in s.pause_reasons if "weekly" not in r.lower()]
            s.weekly_paused_until = None
            if not s.pause_reasons:
                s.system_paused = False
                logger.info("GovernanceEngine: weekly pause lifted on Monday reset.")

    # ------------------------------------------------------------------
    # Public: get_state
    # ------------------------------------------------------------------

    def get_state(self) -> GovernanceState:
        """Return an immutable snapshot of the current governance state."""
        s = self._state
        live_wr, live_ar = self._compute_live_metrics()

        drawdown = _D_ZERO
        if s.all_time_peak_equity > _EPSILON:
            # Note: we don't have current_equity here; caller uses check() for that.
            # Return the last-computed drawdown as 0 (drawdown is computed in check).
            drawdown = _D_ZERO   # drawdown is checked in check(), not stored separately

        return GovernanceState(
            gov_state                       = s.gov_state,
            drift_alert_active              = s.drift_alert_active,
            drift_alert_consecutive_cycles  = s.drift_alert_consecutive_cycles,
            live_win_rate                   = live_wr,
            live_avg_r                      = live_ar,
            historical_win_rate             = s.historical_win_rate,
            historical_avg_r                = s.historical_avg_r,
            weekly_pnl                      = s.weekly_pnl,
            monthly_pnl                     = s.monthly_pnl,
            all_time_peak_equity            = s.all_time_peak_equity,
            max_drawdown_pct                = drawdown,
            consecutive_db_failures         = s.consecutive_db_failures,
            system_paused                   = s.system_paused,
            pause_reasons                   = tuple(s.pause_reasons),
            instrument_paused               = dict(s.instrument_paused),
            consecutive_losses              = s.consecutive_losses,
            trades_since_last_warning       = s.trades_since_last_warning,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _compute_live_metrics(self) -> tuple[Decimal, Decimal]:
        """Compute live win_rate and avg_R from the rolling window.

        Returns (win_rate, avg_r). Returns (-1, -1) if window is empty.
        """
        s = self._state
        window = list(s.trade_window)
        if not window:
            return Decimal("-1"), Decimal("-1")

        n       = Decimal(len(window))
        wins    = sum(1 for _, won in window if won)
        win_rate = Decimal(wins) / n

        total_r = sum(r for r, _ in window)
        avg_r   = total_r / n

        return win_rate, avg_r

    def _update_drift(self, alerts: list[str]) -> None:
        """Doc 10 §5: Run drift detection. Update gov_state and drift_alert_active.

        Drift rules:
          WARNING: live_win_rate < historical × 0.70  (expectancy also)
          WARNING: live_win_rate drops > 20 pp
          CRITICAL: live expectancy < historical × 0.50
        """
        s = self._state
        win_rate, avg_r = self._compute_live_metrics()

        if win_rate == Decimal("-1"):
            # Not enough trades yet — no drift
            s.drift_alert_active = False
            return

        base_wr = s.historical_win_rate
        base_ar = s.historical_avg_r

        warning  = False
        critical = False

        # Check 1: win rate drops > 20 percentage points (Doc 10 §5.2)
        if base_wr - win_rate > _WINRATE_DROP_WARNING + _EPSILON:
            warning = True
            alerts.append(
                f"Drift WARNING: win_rate={_pct(win_rate)} "
                f"dropped >{_pct(_WINRATE_DROP_WARNING)} from baseline {_pct(base_wr)}"
            )

        # Check 2: live win_rate < baseline × 0.70 (Doc 10 §5.1 expectancy drift)
        if base_wr > _EPSILON and win_rate < base_wr * _DRIFT_WARNING_RATIO - _EPSILON:
            warning = True
            alerts.append(
                f"Drift WARNING: win_rate={_pct(win_rate)} "
                f"< {_pct(_DRIFT_WARNING_RATIO)}× baseline {_pct(base_wr)}"
            )

        # Check 3: live avg_R < baseline × 0.70
        if base_ar > _EPSILON and avg_r < base_ar * _DRIFT_WARNING_RATIO - _EPSILON:
            warning = True
            alerts.append(
                f"Drift WARNING: avg_R={avg_r:.4f} "
                f"< {_pct(_DRIFT_WARNING_RATIO)}× baseline {base_ar:.4f}"
            )

        # Check 4: CRITICAL — live avg_R < baseline × 0.50
        if base_ar > _EPSILON and avg_r < base_ar * _DRIFT_CRITICAL_RATIO - _EPSILON:
            critical = True
            alerts.append(
                f"Drift CRITICAL: avg_R={avg_r:.4f} "
                f"< {_pct(_DRIFT_CRITICAL_RATIO)}× baseline {base_ar:.4f}"
            )

        # Check 5: CRITICAL — live win_rate < baseline × 0.50
        if base_wr > _EPSILON and win_rate < base_wr * _DRIFT_CRITICAL_RATIO - _EPSILON:
            critical = True
            alerts.append(
                f"Drift CRITICAL: win_rate={_pct(win_rate)} "
                f"< {_pct(_DRIFT_CRITICAL_RATIO)}× baseline {_pct(base_wr)}"
            )

        # Update drift alert
        s.drift_alert_active = warning or critical

        # State machine transitions (Doc 10 §7)
        if critical:
            if s.gov_state not in (GovernanceGovState.HALTED,):
                prev = s.gov_state.name
                s.gov_state = GovernanceGovState.HALTED
                s.trades_since_last_warning = 0
                logger.critical(
                    "GovernanceEngine: CRITICAL drift → HALTED (was %s)", prev
                )
        elif warning:
            if s.gov_state == GovernanceGovState.NORMAL:
                s.gov_state = GovernanceGovState.CAUTION
                s.trades_since_last_warning = 0
                logger.warning("GovernanceEngine: WARNING drift → CAUTION")
            elif s.gov_state == GovernanceGovState.CAUTION:
                # Repeated warning within 20 trades → DEFENSIVE (Doc 10 §7)
                if s.trades_since_last_warning <= _DRIFT_WINDOW:
                    s.gov_state = GovernanceGovState.DEFENSIVE
                    s.trades_since_last_warning = 0
                    logger.warning(
                        "GovernanceEngine: repeated WARNING within %d trades → DEFENSIVE",
                        _DRIFT_WINDOW,
                    )
        else:
            # No warning — check for recovery (Doc 10 §7: ≥ 30 trades normalised)
            if s.gov_state != GovernanceGovState.NORMAL:
                if s.trades_since_last_warning >= _RECOVERY_TRADE_COUNT:
                    prev = s.gov_state.name
                    s.gov_state = GovernanceGovState.NORMAL
                    logger.info(
                        "GovernanceEngine: metrics normalised after %d trades "
                        "— recovered to NORMAL (was %s)",
                        s.trades_since_last_warning, prev,
                    )

    def _update_consec_loss_state(
        self, consecutive_losses_ext: int, alerts: list[str]
    ) -> None:
        """Doc 10 §12: Apply consecutive-loss triggers to governance state.

        Uses the external count from PortfolioManager (which tracks it more
        precisely per completed trade).  The governance engine merges this
        with its own internal tracking for state machine purposes.
        """
        s = self._state
        losses = max(s.consecutive_losses, consecutive_losses_ext)

        if losses >= _CONSECUTIVE_LOSS_DEFENSIVE:
            if s.gov_state not in (GovernanceGovState.DEFENSIVE, GovernanceGovState.HALTED):
                prev = s.gov_state.name
                s.gov_state = GovernanceGovState.DEFENSIVE
                msg = (f"Consecutive losses={losses} ≥ {_CONSECUTIVE_LOSS_DEFENSIVE} "
                       f"→ DEFENSIVE (was {prev})")
                logger.warning(msg)
                alerts.append(msg)
                s.trades_since_last_warning = 0
        elif losses >= _CONSECUTIVE_LOSS_CAUTION:
            if s.gov_state == GovernanceGovState.NORMAL:
                prev = s.gov_state.name
                s.gov_state = GovernanceGovState.CAUTION
                msg = (f"Consecutive losses={losses} ≥ {_CONSECUTIVE_LOSS_CAUTION} "
                       f"→ CAUTION (was {prev})")
                logger.warning(msg)
                alerts.append(msg)
                s.trades_since_last_warning = 0

    def _maybe_reset_periods(self, bar_timestamp: datetime) -> None:
        """Check if a weekly or monthly boundary was crossed; reset counters."""
        s = self._state
        bar_monday = _week_monday(bar_timestamp)
        bar_month_start = bar_timestamp.replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )

        # First call: initialise period start
        if s.current_week_monday is None:
            s.current_week_monday = bar_monday
        if s.current_month_start is None:
            s.current_month_start = bar_month_start

        # Weekly reset (new Monday)
        if bar_monday > s.current_week_monday:
            self.reset_weekly(bar_timestamp)

        # Monthly reset (new month)
        if bar_month_start > s.current_month_start:
            s.monthly_pnl = _D_ZERO
            s.current_month_start = bar_month_start
            s.monthly_paused = False   # new month resets monthly block (manual)
            monthly_reasons = [r for r in s.pause_reasons if "monthly" in r.lower()]
            if monthly_reasons:
                s.pause_reasons = [r for r in s.pause_reasons
                                   if "monthly" not in r.lower()]
                if not s.pause_reasons:
                    s.system_paused = False
            logger.info(
                "GovernanceEngine: monthly P&L reset at %s",
                bar_timestamp.isoformat(),
            )

    def _maybe_resume_weekly(self, bar_timestamp: datetime) -> None:
        """Auto-resume a weekly pause if we've crossed into the next week."""
        s = self._state
        if s.weekly_paused_until is not None:
            if bar_timestamp >= s.weekly_paused_until:
                # Monday has arrived — lift weekly pause
                s.weekly_paused_until = None
                weekly_reasons = [r for r in s.pause_reasons if "weekly" in r.lower()]
                if weekly_reasons:
                    s.pause_reasons = [r for r in s.pause_reasons
                                       if "weekly" not in r.lower()]
                    if not s.pause_reasons:
                        s.system_paused = False
                        logger.info("GovernanceEngine: weekly pause auto-resumed.")

    def _add_pause_reason(self, reason: str) -> None:
        """Add a pause reason if not already present."""
        s = self._state
        if reason not in s.pause_reasons:
            s.pause_reasons.append(reason)


# ---------------------------------------------------------------------------
# Module-level pure helpers
# ---------------------------------------------------------------------------

def _pct(d: Decimal) -> str:
    """Format a Decimal fraction as a percentage string (no float)."""
    return f"{d * 100:.2f}%"


def _week_monday(dt: datetime) -> datetime:
    """Return the Monday 00:00:00 UTC that the given datetime falls in."""
    # weekday(): Monday=0, Sunday=6
    days_since_monday = dt.weekday()
    monday = dt - timedelta(days=days_since_monday)
    return monday.replace(hour=0, minute=0, second=0, microsecond=0,
                          tzinfo=timezone.utc)


def _next_monday(dt: datetime) -> datetime:
    """Return the next Monday 00:00:00 UTC after the given datetime."""
    days_until_monday = (7 - dt.weekday()) % 7
    if days_until_monday == 0:
        days_until_monday = 7   # if today IS Monday, return next Monday
    next_mon = dt + timedelta(days=days_until_monday)
    return next_mon.replace(hour=0, minute=0, second=0, microsecond=0,
                            tzinfo=timezone.utc)
