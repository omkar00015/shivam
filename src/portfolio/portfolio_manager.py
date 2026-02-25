"""Doc 8 (Portfolio Allocation) + Doc 10 §12-§13 (Consecutive Loss / Daily Loss)
+ Doc 1 §7 (Tie-breaking) + Amendment v1.1 E5 (BTC/Gold fully independent).

CAPITAL COMPETITION (Doc 8 §4-§6):
  When multiple SetupCandidates compete for capital on the same bar:
    1. Pre-filter: reject instrument already at max position count
    2. Risk budget check: reject if adding trade exceeds 6% portfolio cap
    3. Rank survivors by Doc 1 §7 tie-breaking (6 rules)
    4. Allocate in rank order until budget exhausted; rest → cancelled

POSITION LIMITS (Doc 8 §3):
  Max simultaneous open trades: 2 (one per instrument — E5: fully independent)
  Max portfolio risk:            6% of equity  (hard veto, Doc 8 §3.2)
  Max risk per trade:            1% of equity  (Doc 8 §3.1)
  Max risk per instrument:       3% of equity  (Doc 8 §3.3 per-direction limit)

PAUSE CONDITIONS (Doc 10 §12-§13 + user spec):
  - Daily loss > 3% of equity at session start → pause until next session open (auto)
  - Consecutive losses >= 3 → pause, manual resume required
  - Drawdown from peak > 10% → pause, manual resume required
  - Data integrity failure (Level 0) → pause immediately (manual resume)

RESUME CONDITIONS (user spec):
  - Daily-loss pause: auto-resumes at next session open via reset_daily()
  - Consecutive-loss / drawdown pause: manual_resume() only

Doc 1 §7 TIE-BREAKING ORDER:
  1. Higher timeframe zone origin
  2. Higher zone strength
  3. Smaller stop distance
  4. Larger expected_R
  5. Earlier created_at
  6. Lexicographic candidate_id

All arithmetic uses Decimal. No float anywhere. All timestamps UTC.
"""

from __future__ import annotations

import datetime
import hashlib
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum, auto
from typing import Optional, Sequence

from src.execution.entry_executor import Trade
from src.setup.setup_engine import CandidateStatus, Direction, SetupCandidate, SetupType

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_EPSILON = Decimal("1E-9")
_D_ZERO  = Decimal("0")
_D_ONE   = Decimal("1")

# Risk limits (Doc 8 §3)
_MAX_PORTFOLIO_RISK_PCT = Decimal("0.06")   # 6% hard cap
_MAX_TRADE_RISK_PCT     = Decimal("0.01")   # 1% per trade (default)
_MAX_INSTRUMENT_RISK_PCT = Decimal("0.03")  # 3% per instrument (per-direction guard §3.3)

# Max open trades (Doc 8 + E5: one per instrument, max 2 total)
_MAX_OPEN_TRADES = 2
_MAX_PER_INSTRUMENT = 1

# Pause thresholds (Doc 10 §12-§13 + user spec)
_DAILY_LOSS_PCT_LIMIT    = Decimal("0.03")   # > 3% daily loss → pause
_CONSECUTIVE_LOSS_LIMIT  = 3                  # >= 3 consecutive losses → pause
_DRAWDOWN_PCT_LIMIT      = Decimal("0.10")   # > 10% drawdown from peak → pause

# Timeframe weights for tie-breaking (Doc 1 §7, rule 1)
_TF_WEIGHTS: dict[str, int] = {
    "3M": 7, "1M": 6, "1W": 5, "1D": 4, "4H": 3, "1H": 2, "15m": 1,
}


# ---------------------------------------------------------------------------
# Pause reason enum
# ---------------------------------------------------------------------------

class PauseReason(Enum):
    """Why the system was paused. Determines resume conditions."""
    DAILY_LOSS        = auto()   # auto-resumes via reset_daily()
    CONSECUTIVE_LOSS  = auto()   # manual resume required
    DRAWDOWN          = auto()   # manual resume required
    DATA_INTEGRITY    = auto()   # manual resume required


# ---------------------------------------------------------------------------
# PortfolioState — snapshot of current state
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PortfolioState:
    """Doc 8: Immutable snapshot of portfolio state at one point in time.

    All Decimal fields. Exposes derived metrics for governance and display.
    """
    equity:                Decimal
    peak_equity:           Decimal
    open_trades:           tuple                  # tuple[Trade, ...] — frozen
    risk_used_pct:         Decimal                # sum of active risk / equity
    current_drawdown_pct:  Decimal                # (peak - equity) / peak
    daily_pnl:             Decimal                # realised P&L today
    daily_starting_equity: Decimal                # equity at session open
    consecutive_losses:    int
    system_paused:         bool
    pause_reason:          Optional[PauseReason]
    total_trades:          int
    winning_trades:        int
    losing_trades:         int


# ---------------------------------------------------------------------------
# Internal mutable state (kept inside PortfolioManager)
# ---------------------------------------------------------------------------

@dataclass
class _State:
    """Mutable internal state. Not exposed directly — use get_state()."""
    equity:                Decimal
    peak_equity:           Decimal
    open_trades:           dict   # instrument -> Trade  (max 1 per instrument)
    daily_pnl:             Decimal
    daily_starting_equity: Decimal
    consecutive_losses:    int
    system_paused:         bool
    pause_reason:          Optional[PauseReason]
    total_trades:          int
    winning_trades:        int
    losing_trades:         int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _zone_timeframe(zone_id: str) -> str:
    """Infer timeframe from zone_id for tie-breaking (Doc 1 §7, rule 1).

    Scans all '_'-separated tokens for a known TF label, returning the
    highest-weight one found.  This handles both simple '<TF>_<hash>'
    zone IDs (from SetupEngine) and instrument-prefixed ones such as
    '<INSTRUMENT>_<TF>_<hash>' (pipeline-tagged IDs).

    Falls back to '15m' if no TF token is found.
    """
    best_tf     = "15m"
    best_weight = _TF_WEIGHTS.get("15m", 1)
    for part in zone_id.split("_"):
        w = _TF_WEIGHTS.get(part)
        if w is not None and w > best_weight:
            best_tf     = part
            best_weight = w
    return best_tf


def _tier_break_sort_key(
    c: SetupCandidate,
    zone_strength_map: dict[str, int],
) -> tuple:
    """Doc 1 §7: Six-level deterministic sort key (lower = higher priority).

    Negation on rule 1/2/4 so that min() returns the highest-priority candidate.
    Rule 3 (smaller stop) already sorts ascending — no negation needed.
    """
    tf_weight = _TF_WEIGHTS.get(_zone_timeframe(c.zone_id), 1)
    strength  = zone_strength_map.get(c.zone_id, 0)
    stop_dist = abs(c.entry_price - c.stop_price)
    return (
        -tf_weight,       # Rule 1: higher TF wins (negate for min())
        -strength,        # Rule 2: higher strength wins (negate)
        stop_dist,        # Rule 3: smaller stop wins
        -c.expected_R,    # Rule 4: larger R wins (negate)
        c.created_at,     # Rule 5: earlier creation wins
        c.candidate_id,   # Rule 6: lexicographic
    )


def _instrument_risk_pct(instrument: str, open_trades: dict, equity: Decimal) -> Decimal:
    """Doc 8 §3.3: Current risk % for one instrument across all open trades."""
    if equity <= _EPSILON:
        return _D_ONE
    total = sum(
        (t.risk_amount for t in open_trades.values() if t.instrument == instrument),
        _D_ZERO,
    )
    return total / equity


def _portfolio_risk_pct(open_trades: dict, equity: Decimal) -> Decimal:
    """Doc 8 §3.2: Current total risk as fraction of equity."""
    if equity <= _EPSILON:
        return _D_ONE
    total = sum((t.risk_amount for t in open_trades.values()), _D_ZERO)
    return total / equity


# ---------------------------------------------------------------------------
# PortfolioManager
# ---------------------------------------------------------------------------

class PortfolioManager:
    """Doc 8 + Doc 10 §12-§13 + Doc 1 §7 + Amendment E5.

    Stateful manager for capital allocation, risk budgeting, and pause logic.
    One instance per system (covers all instruments).

    Usage::

        pm = PortfolioManager(initial_equity=Decimal("100000"))

        # On each bar, before EntryExecutor runs:
        approved = pm.allocate(
            candidates=setup_engine.get_active_candidates(),
            zone_strength_map={"1H_zone001": 8, ...},
            instrument="BTCUSDT",
        )

        # After EntryExecutor fills a trade:
        pm.record_fill(trade)

        # After a trade closes:
        pm.record_close(trade, pnl=Decimal("150"))

        # At session open (new trading day):
        pm.reset_daily()
    """

    def __init__(
        self,
        initial_equity: Decimal,
        max_open_trades: int = _MAX_OPEN_TRADES,
        max_per_instrument: int = _MAX_PER_INSTRUMENT,
        max_portfolio_risk_pct: Decimal = _MAX_PORTFOLIO_RISK_PCT,
        max_instrument_risk_pct: Decimal = _MAX_INSTRUMENT_RISK_PCT,
        max_trade_risk_pct: Decimal = _MAX_TRADE_RISK_PCT,
        consecutive_loss_limit: int = _CONSECUTIVE_LOSS_LIMIT,
        daily_loss_pct_limit: Decimal = _DAILY_LOSS_PCT_LIMIT,
        drawdown_pct_limit: Decimal = _DRAWDOWN_PCT_LIMIT,
    ) -> None:
        self._max_open_trades        = max_open_trades
        self._max_per_instrument     = max_per_instrument
        self._max_portfolio_risk_pct = max_portfolio_risk_pct
        self._max_instrument_risk_pct = max_instrument_risk_pct
        self._max_trade_risk_pct     = max_trade_risk_pct
        self._consecutive_loss_limit = consecutive_loss_limit
        self._daily_loss_pct_limit   = daily_loss_pct_limit
        self._drawdown_pct_limit     = drawdown_pct_limit

        self._state = _State(
            equity=initial_equity,
            peak_equity=initial_equity,
            open_trades={},                    # instrument -> Trade
            daily_pnl=_D_ZERO,
            daily_starting_equity=initial_equity,
            consecutive_losses=0,
            system_paused=False,
            pause_reason=None,
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
        )

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def allocate(
        self,
        candidates: Sequence[SetupCandidate],
        zone_strength_map: Optional[dict[str, int]] = None,
        bar_timestamp: Optional[datetime.datetime] = None,
    ) -> list[SetupCandidate]:
        """Doc 8 §5-§6: Rank and allocate capital to competing candidates.

        Returns an ordered list of approved SetupCandidates (in fill order).
        Candidates not in the returned list should be treated as rejected.

        Args:
            candidates:        WAITING_ENTRY candidates from SetupEngine.
            zone_strength_map: zone_id → strength score (for tie-breaking).
                               If None, all strengths treated as 0.
            bar_timestamp:     Current bar timestamp (for logging).

        Returns:
            list[SetupCandidate] in priority order (highest → lowest).
        """
        s = self._state

        # System paused → no allocations
        if s.system_paused:
            logger.info("PortfolioManager: system_paused — no allocations.")
            return []

        # Filter only WAITING_ENTRY candidates
        eligible = [c for c in candidates if c.status == CandidateStatus.WAITING_ENTRY]
        if not eligible:
            return []

        strength_map = zone_strength_map or {}

        # --- Pre-filter (Doc 8 §6 step 1-2) ---
        pre_filtered: list[SetupCandidate] = []
        for cand in eligible:
            # Instrument already has open trade?
            if self._instrument_at_max(cand):
                logger.debug(
                    "Candidate %s rejected: instrument %s already at max position.",
                    cand.candidate_id, cand.zone_id,
                )
                continue

            # Would exceed total portfolio risk cap?
            trade_risk = s.equity * self._max_trade_risk_pct
            if self._would_exceed_portfolio_cap(trade_risk):
                logger.debug(
                    "Candidate %s rejected: portfolio risk cap would be exceeded.",
                    cand.candidate_id,
                )
                continue

            # Would exceed per-instrument risk limit?
            instr = self._instrument_from_candidate(cand)
            instr_risk_now = _instrument_risk_pct(instr, s.open_trades, s.equity)
            new_instr_risk = instr_risk_now + self._max_trade_risk_pct
            if new_instr_risk > self._max_instrument_risk_pct + _EPSILON:
                logger.debug(
                    "Candidate %s rejected: instrument risk cap (%.2f%%) exceeded.",
                    cand.candidate_id, float(new_instr_risk * 100),
                )
                continue

            # Max simultaneous open trades?
            if len(s.open_trades) >= self._max_open_trades:
                logger.debug(
                    "Candidate %s rejected: max_open_trades=%d reached.",
                    cand.candidate_id, self._max_open_trades,
                )
                continue

            pre_filtered.append(cand)

        if not pre_filtered:
            return []

        # --- Rank by Doc 1 §7 tie-breaking (step 3) ---
        pre_filtered.sort(key=lambda c: _tier_break_sort_key(c, strength_map))

        # --- Allocate in rank order until budget exhausted (step 4) ---
        approved: list[SetupCandidate] = []
        # Track tentative allocations to avoid double-booking within this batch
        tentative_instruments: set[str] = set(s.open_trades.keys())
        tentative_risk_used   = _portfolio_risk_pct(s.open_trades, s.equity)
        trade_risk_pct = self._max_trade_risk_pct

        for cand in pre_filtered:
            instr = self._instrument_from_candidate(cand)

            # Already tentatively allocated this instrument in this batch?
            if instr in tentative_instruments:
                logger.debug(
                    "Candidate %s skipped: instrument %s already tentatively allocated.",
                    cand.candidate_id, instr,
                )
                continue

            # Portfolio risk check (incorporating tentative)
            if tentative_risk_used + trade_risk_pct > self._max_portfolio_risk_pct + _EPSILON:
                logger.debug(
                    "Candidate %s rejected: portfolio risk cap (tentative=%.2f%%) exceeded.",
                    cand.candidate_id, float(tentative_risk_used * 100),
                )
                continue

            approved.append(cand)
            tentative_instruments.add(instr)
            tentative_risk_used += trade_risk_pct

        return approved

    def record_fill(self, trade: Trade) -> None:
        """Doc 8 §9: Register a filled trade. Reserves its risk.

        Must be called by the pipeline after EntryExecutor confirms a fill.
        """
        instr = trade.instrument
        self._state.open_trades[instr] = trade
        logger.info(
            "PortfolioManager: fill recorded — %s %s %s size=%s",
            trade.instrument, trade.direction.name, trade.trade_id, trade.position_size,
        )

    def record_close(self, trade: Trade, pnl: Decimal) -> None:
        """Doc 8 §15 + Doc 10 §12-§13: Record trade close, update equity and pause checks.

        Args:
            trade: The closed Trade object.
            pnl:   Realised P&L in quote currency (positive = profit, negative = loss).
                   Must be Decimal.
        """
        s = self._state

        # Remove from open trades
        instr = trade.instrument
        s.open_trades.pop(instr, None)

        # Update equity
        s.equity += pnl

        # Update daily P&L
        s.daily_pnl += pnl

        # Update counters
        s.total_trades += 1
        if pnl > _EPSILON:
            s.winning_trades += 1
            s.consecutive_losses = 0           # reset on win
        else:
            s.losing_trades += 1
            s.consecutive_losses += 1

        # Update peak equity and drawdown
        if s.equity > s.peak_equity:
            s.peak_equity = s.equity

        # --- Pause condition checks (Doc 10 §12-§13 + user spec) ---

        # 1. Daily loss > 3% of daily_starting_equity
        if s.daily_starting_equity > _EPSILON:
            daily_loss_pct = -s.daily_pnl / s.daily_starting_equity
            if daily_loss_pct > self._daily_loss_pct_limit + _EPSILON:
                if not s.system_paused:
                    self._trigger_pause(PauseReason.DAILY_LOSS, daily_loss_pct)
                return   # skip further checks once paused

        # 2. Consecutive losses >= limit
        if s.consecutive_losses >= self._consecutive_loss_limit:
            if not s.system_paused:
                self._trigger_pause(PauseReason.CONSECUTIVE_LOSS, s.consecutive_losses)
            return

        # 3. Drawdown > 10% from peak
        if s.peak_equity > _EPSILON:
            drawdown = (s.peak_equity - s.equity) / s.peak_equity
            if drawdown > self._drawdown_pct_limit + _EPSILON:
                if not s.system_paused:
                    self._trigger_pause(PauseReason.DRAWDOWN, drawdown)

    def pause_data_integrity(self) -> None:
        """Doc 1 §6.1 Level-0: Pause immediately on data integrity failure."""
        self._trigger_pause(PauseReason.DATA_INTEGRITY, Decimal("0"))

    def reset_daily(self) -> None:
        """Doc 10 §13: Reset daily P&L tracking at new session open.

        Auto-resumes a DAILY_LOSS pause (only that pause type).
        """
        s = self._state
        s.daily_starting_equity = s.equity
        s.daily_pnl             = _D_ZERO

        if s.system_paused and s.pause_reason == PauseReason.DAILY_LOSS:
            logger.info("PortfolioManager: daily reset — DAILY_LOSS pause lifted.")
            s.system_paused = False
            s.pause_reason  = None

    def manual_resume(self) -> bool:
        """User-triggered resume.

        Returns True if resume was applied (system is now unpaused).
        Returns False if the current pause requires automatic conditions
        (DAILY_LOSS pauses auto-resume via reset_daily; manual_resume
        cannot override them here — it returns False to signal "not yet").

        Manual resume is allowed for: CONSECUTIVE_LOSS, DRAWDOWN, DATA_INTEGRITY.
        DAILY_LOSS returns False — use reset_daily() instead.
        """
        s = self._state
        if not s.system_paused:
            return True   # not paused — nothing to resume

        if s.pause_reason == PauseReason.DAILY_LOSS:
            # Must auto-resume via reset_daily(), not manual
            logger.info("PortfolioManager: manual_resume refused — DAILY_LOSS auto-resumes at session open.")
            return False

        # CONSECUTIVE_LOSS, DRAWDOWN, DATA_INTEGRITY → manual resume allowed
        logger.info(
            "PortfolioManager: manual_resume accepted — clearing %s pause.",
            s.pause_reason.name if s.pause_reason else "UNKNOWN",
        )
        s.system_paused = False
        s.pause_reason  = None
        s.consecutive_losses = 0   # reset on manual resume
        return True

    def can_trade(self) -> bool:
        """Doc 8 + Doc 10: Returns False if system is paused."""
        return not self._state.system_paused

    def get_state(self) -> PortfolioState:
        """Return immutable snapshot of current portfolio state."""
        s = self._state
        risk_used = _portfolio_risk_pct(s.open_trades, s.equity)
        drawdown = (
            (s.peak_equity - s.equity) / s.peak_equity
            if s.peak_equity > _EPSILON
            else _D_ZERO
        )
        return PortfolioState(
            equity=s.equity,
            peak_equity=s.peak_equity,
            open_trades=tuple(s.open_trades.values()),
            risk_used_pct=risk_used,
            current_drawdown_pct=drawdown,
            daily_pnl=s.daily_pnl,
            daily_starting_equity=s.daily_starting_equity,
            consecutive_losses=s.consecutive_losses,
            system_paused=s.system_paused,
            pause_reason=s.pause_reason,
            total_trades=s.total_trades,
            winning_trades=s.winning_trades,
            losing_trades=s.losing_trades,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _instrument_from_candidate(self, cand: SetupCandidate) -> str:
        """Infer instrument from zone_id prefix or default.

        Convention: zone_ids starting with 'BTC' or 'XAU' carry instrument.
        Fallback: use zone_id itself as instrument key for test isolation.
        For real usage, the caller should pass instrument-tagged zone IDs.

        The zone_id format '<TF>_<hash>' does not encode instrument, so we
        track by zone_id's instrument segment if present, or by a separate
        instrument lookup. In practice the pipeline passes instrument-specific
        candidates, and zone_ids are scoped per instrument. We use a configurable
        instrument_tag embedded in zone_id prefix: '<INSTRUMENT>_<TF>_<hash>'.

        For backwards compatibility with single-underscore zone_ids (tests and
        existing SetupEngine output), we treat each unique zone_id's first
        token as instrument if it's a known instrument, otherwise fall back to
        the zone_id itself as a unique instrument key (one per unique zone =
        one trade per zone rule upheld).
        """
        # Zone IDs from existing SetupEngine are '<TF>_<hash>'.
        # We need to isolate candidates per instrument. The caller can pass a
        # zone_strength_map keyed by instrument-aware zone_ids, but for the
        # instrument extraction we rely on the zone_id's TF prefix and then
        # use an instrument field that the pipeline should attach.
        # Since SetupCandidate doesn't carry an instrument field in this design,
        # we use zone_id prefix logic: if the caller uses '<INSTRUMENT>_<TF>_<hash>'
        # we extract INSTRUMENT; otherwise treat zone_id as opaque and use it
        # as a slot key.
        parts = cand.zone_id.split("_")
        known_instruments = {"BTC", "BTCUSDT", "XAU", "XAUUSD", "GOLD"}
        if parts[0].upper() in known_instruments:
            return parts[0].upper()
        # Fall back: instrument = first two parts if second is a known TF
        if len(parts) >= 2 and parts[1] in _TF_WEIGHTS:
            return parts[0]
        # Default: treat zone_id prefix as instrument (for test compatibility)
        # Use zone_id itself — one trade per zone, instrument = zone_id key
        return cand.zone_id

    def _instrument_at_max(self, cand: SetupCandidate) -> bool:
        """True if the candidate's instrument already has max open trades."""
        instr = self._instrument_from_candidate(cand)
        count = sum(1 for t in self._state.open_trades.values() if t.instrument == instr)
        return count >= self._max_per_instrument

    def _would_exceed_portfolio_cap(self, new_risk_amount: Decimal) -> bool:
        """True if adding new_risk_amount would breach the portfolio risk cap."""
        s = self._state
        if s.equity <= _EPSILON:
            return True
        current_risk = sum((t.risk_amount for t in s.open_trades.values()), _D_ZERO)
        total_risk = current_risk + new_risk_amount
        return total_risk / s.equity > self._max_portfolio_risk_pct + _EPSILON

    def _trigger_pause(self, reason: PauseReason, metric_value: object) -> None:
        """Set system_paused=True with the given reason."""
        s = self._state
        s.system_paused = True
        s.pause_reason  = reason
        logger.warning(
            "PortfolioManager: PAUSED — reason=%s metric=%.4f",
            reason.name,
            float(metric_value) if metric_value is not None else 0.0,
        )
