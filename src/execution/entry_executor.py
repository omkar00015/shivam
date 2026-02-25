"""Doc 7 + Doc 1 §6 (Authority Hierarchy) + Amendment v1.1 (M6, E5):
Entry Executor — deterministic capital commitment, no float.

AUTHORITY HIERARCHY (Doc 1 §6 — immutable veto order):
  Level 0: Data Integrity  → abort if bar OHLCV invalid
  Level 1: Risk Integrity  → reject if R:R low, stop wide, portfolio full, paused
  Level 2: Phase Permission → each setup has allowed phases (enforced by SetupEngine)
  Level 3: Structural Location → zone tier/state enforced by SetupEngine pre-filter
  Level 4: Setup Validity → preconditions confirmed in SetupEngine
  Level 5: Entry Trigger   → THIS MODULE: only on bar close, EPSILON comparison

ENTRY TRIGGER LAW (Doc 7 §3):
  Entry fires ONLY on 15m bar close — never intrabar.
  Long:  close >= entry_price - EPSILON
  Short: close <= entry_price + EPSILON

GAP RULE (Doc 7 §4):
  If open already past entry level (gap through) → pessimistic fill at bar.close.

RISK GATE (Doc 7 §8 = Level 1 veto):
  Reject candidate if ANY of:
    1. R:R < minimum (2.0 TREND, 1.5 BALANCE)
    2. stop_distance > 1.5 × ATR(15m)   [trigger TF = 15m]
    3. portfolio_risk > 6%  (of equity)
    4. system_paused = True

POSITION SIZING (Doc 7 §7):
  account_risk = equity × 0.01   (1% fixed risk)
  size = floor(account_risk / stop_distance)

TRADE MANAGEMENT (Doc 7 §12-§14):
  manage_open_trades() → list[TradeAction]
  - MOVE_STOP_BREAKEVEN: price reaches entry + 1×stop_distance (breakeven at +1R)
  - EXIT_PHASE_FLIP: phase not compatible with trade direction
  - EXIT_STRUCTURE_FAILURE: opposite leg confirmed (completed_1h_leg provided)

TIE-BREAKING (Doc 1 §7) — exact order:
  1. Higher timeframe zone origin  (HTF > LTF)
  2. Higher zone strength
  3. Smaller stop distance
  4. Larger expected_R
  5. Earlier created_at
  6. Lexicographic candidate_id

All arithmetic uses Decimal. No float anywhere. All timestamps UTC.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import ROUND_DOWN, Decimal
from enum import Enum, auto
from typing import Optional, Sequence

from src.data_ingest.bar_aggregator import AggregatedBar
from src.indicators.zlbb import CompletedLeg, LegDirection
from src.phase.phase_engine import Phase, PhaseResult
from src.setup.setup_engine import CandidateStatus, Direction, SetupCandidate, SetupType

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_EPSILON = Decimal("1E-9")
_D_ZERO  = Decimal("0")
_D_ONE   = Decimal("1")

# Risk gate (Doc 7 §8)
_MIN_R_TREND   = Decimal("2.0")   # PULLBACK, BREAKOUT_RETEST
_MIN_R_BALANCE = Decimal("1.5")   # RANGE_FADE, FAKEOUT
_MAX_STOP_ATR_MULT = Decimal("1.5")   # stop_dist > 1.5×ATR(15m) → reject
_PORTFOLIO_RISK_CAP = Decimal("0.06")  # 6% of equity

# Position sizing (Doc 7 §7)
_ACCOUNT_RISK_PCT = Decimal("0.01")   # 1% fixed risk per trade

# Minimum tradable lot size (instrument-specific; default 1 for crypto contracts)
_MIN_LOT_SIZE = Decimal("1")

# Timeframe weights for tie-breaking (Doc 1 §7 rule 1)
_TF_WEIGHTS: dict[str, int] = {
    "3M": 7,
    "1M": 6,
    "1W": 5,
    "1D": 4,
    "4H": 3,
    "1H": 2,
    "15m": 1,
}

# Phases compatible with LONG trades
_LONG_COMPATIBLE_PHASES = {Phase.TREND_BULL, Phase.BALANCE, Phase.ACCUMULATION}
# Phases compatible with SHORT trades
_SHORT_COMPATIBLE_PHASES = {Phase.TREND_BEAR, Phase.BALANCE, Phase.DISTRIBUTION}


# ---------------------------------------------------------------------------
# Trade — immutable record created on confirmed entry
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Trade:
    """Doc 7 §9: Immutable trade object. All price fields are Decimal.

    Created when a candidate's entry condition is satisfied on bar close.
    Once created, fields do not change (except via TradeAction messages).
    """
    trade_id:        str           # deterministic sha256[:16]
    candidate_id:    str           # source SetupCandidate
    instrument:      str
    direction:       Direction
    setup_type:      SetupType

    # Price fields (all Decimal — Doc 1 §4)
    entry_price_ref:   Decimal     # reference from SetupCandidate
    entry_price_actual: Decimal    # actual fill (may differ on gap — Doc 7 §4)
    stop_price:        Decimal
    target_price:      Decimal
    stop_distance:     Decimal     # abs(entry_actual - stop)
    expected_R:        Decimal     # frozen from SetupCandidate (M6)
    actual_R_if_stopped: Decimal   # target_dist / stop_dist at fill prices

    # Risk / sizing
    position_size:   Decimal       # floored lot count (Doc 7 §7.3)
    risk_amount:     Decimal       # equity × 1%
    account_equity:  Decimal       # equity at time of entry

    # Metadata
    zone_id:         str
    opened_at:       datetime      # UTC timestamp of triggering bar close
    status:          str = "OPEN"  # "OPEN" | "CLOSED"

    # Management state (mutable via TradeAction; stored here as snapshots)
    breakeven_triggered: bool = False
    current_stop:    Optional[Decimal] = None   # None = original stop_price


# ---------------------------------------------------------------------------
# TradeAction — management signals returned by manage_open_trades()
# ---------------------------------------------------------------------------

class TradeActionType(Enum):
    """Doc 7 §12-§14: Action types for open trade management."""
    HOLD                  = auto()
    MOVE_STOP_BREAKEVEN   = auto()   # §12: price hit +1R → stop → entry
    EXIT_PHASE_FLIP       = auto()   # §14: phase flipped hard against trade
    EXIT_STRUCTURE_FAILURE = auto()  # §14: opposite leg confirmed


@dataclass(frozen=True)
class TradeAction:
    """Immutable management decision for one open trade."""
    trade_id:    str
    action_type: TradeActionType
    new_stop:    Optional[Decimal] = None   # filled for MOVE_STOP_BREAKEVEN
    reason:      str = ""


# ---------------------------------------------------------------------------
# RejectionReason — for audit logging (Doc 7 §18)
# ---------------------------------------------------------------------------

class RejectionReason(Enum):
    """Doc 7 §8 + Doc 1 §6: Why a candidate was rejected."""
    LOW_RR            = auto()
    STOP_TOO_WIDE     = auto()
    PORTFOLIO_FULL    = auto()
    SYSTEM_PAUSED     = auto()
    BAR_INCOMPLETE    = auto()
    ENTRY_NOT_REACHED = auto()
    DUPLICATE_ZONE    = auto()


# ---------------------------------------------------------------------------
# PortfolioState — caller-provided snapshot of current portfolio
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PortfolioState:
    """Caller-supplied portfolio snapshot for risk gate checks (Doc 7 §8).

    equity:          Current account equity in quote currency (Decimal).
    open_positions:  List of currently open Trade objects.
    system_paused:   True if governance layer has paused trading.
    max_positions:   Max simultaneous positions (Doc 8). Default 5.
    """
    equity:          Decimal
    open_positions:  list = field(default_factory=list)
    system_paused:   bool = False
    max_positions:   int  = 5


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _make_trade_id(candidate_id: str, opened_at: datetime) -> str:
    """Doc 1 §4: Deterministic sha256 trade ID."""
    content = f"TRADE|{candidate_id}|{opened_at.isoformat()}"
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def _min_r_for_setup(setup_type: SetupType) -> Decimal:
    """Doc 7 §6.1: Minimum R:R by setup category."""
    if setup_type in (SetupType.RANGE_FADE, SetupType.FAKEOUT):
        return _MIN_R_BALANCE
    return _MIN_R_TREND   # PULLBACK_CONTINUATION, BREAKOUT_RETEST


def _bar_ohlcv_valid(bar: AggregatedBar) -> bool:
    """Doc 1 §6.1 Level-0: Data integrity check for bar.

    Returns False if high < low, close out of range, or bar not complete.
    """
    if not bar.is_complete:
        return False
    if bar.high < bar.low - _EPSILON:
        return False
    if bar.close < bar.low - _EPSILON or bar.close > bar.high + _EPSILON:
        return False
    if bar.volume < _D_ZERO - _EPSILON:
        return False
    return True


def _entry_triggered(
    bar: AggregatedBar,
    candidate: SetupCandidate,
) -> bool:
    """Doc 7 §3: Check if entry conditions are met on bar CLOSE only.

    Long:  close >= entry_price - EPSILON
    Short: close <= entry_price + EPSILON

    Also handles GAP rule (Doc 7 §4): if open already past entry,
    the fill is at bar.close (pessimistic). This function returns True
    if EITHER the close meets entry OR the bar gapped through entry.
    """
    entry = candidate.entry_price
    if candidate.direction == Direction.LONG:
        # Standard: close reaches entry from below
        close_triggered = bar.close >= entry - _EPSILON
        # Gap: open already above entry (price gapped over)
        gap_through = bar.open > entry + _EPSILON
        return close_triggered or gap_through
    else:  # SHORT
        close_triggered = bar.close <= entry + _EPSILON
        gap_through = bar.open < entry - _EPSILON
        return close_triggered or gap_through


def _actual_fill_price(
    bar: AggregatedBar,
    candidate: SetupCandidate,
) -> Decimal:
    """Doc 7 §4 / §10: Pessimistic fill price.

    If open already past entry (gap) → fill at bar.close (worst realistic price).
    Otherwise → fill at bar.close (marketable order fills at close per §10).
    In both cases the fill is bar.close (most conservative).
    """
    # Per Doc 7 §4 and §10: always use close as fill (pessimistic model)
    return bar.close


def _compute_portfolio_risk(
    portfolio: PortfolioState,
    new_risk_amount: Decimal,
) -> Decimal:
    """Doc 7 §8: Total portfolio risk as fraction of equity.

    Sums risk_amount across all open positions plus the proposed new trade.
    """
    if portfolio.equity <= _EPSILON:
        return _D_ONE  # no equity → 100% risk
    existing_risk = sum(
        (t.risk_amount for t in portfolio.open_positions),
        _D_ZERO,
    )
    total_risk = existing_risk + new_risk_amount
    return total_risk / portfolio.equity


def _zone_already_traded(
    portfolio: PortfolioState,
    candidate: SetupCandidate,
) -> bool:
    """Doc 7 §16: Duplicate prevention — one trade per zone per direction."""
    for t in portfolio.open_positions:
        if t.zone_id == candidate.zone_id and t.direction == candidate.direction:
            return True
    return False


def _zone_timeframe_from_id(zone_id: str) -> str:
    """Infer timeframe from zone_id prefix for tie-breaking (Doc 1 §7).

    Convention: zone_id starts with '<TF>_' (e.g. '1H_abc123def456').
    Falls back to '15m' if not parseable.
    """
    parts = zone_id.split("_", 1)
    if len(parts) == 2 and parts[0] in _TF_WEIGHTS:
        return parts[0]
    return "15m"


def _sort_key_for_candidate(c: SetupCandidate, stop_dist: Decimal) -> tuple:
    """Doc 1 §7: Six-level tie-breaking sort key (lower index = higher priority).

    Returns a tuple suitable for min() — lower values win.
    """
    tf = _zone_timeframe_from_id(c.zone_id)
    tf_weight = _TF_WEIGHTS.get(tf, 1)
    return (
        -tf_weight,              # Rule 1: higher TF wins (negate for min())
        -c.expected_R,           # Rule 4 SWAP: we need stop_dist for rule 3 at call site
        stop_dist,               # Rule 3: smaller stop wins
        c.candidate_id,          # Rule 6: lexicographic
    )


def _compute_stop_distance(
    candidate: SetupCandidate,
    fill_price: Decimal,
) -> Decimal:
    """Compute actual stop distance from fill price (may differ from reference)."""
    return abs(fill_price - candidate.stop_price)


def _tier_break_sort_key(
    c: SetupCandidate,
    fill_price: Decimal,
    zone_strength_map: dict[str, int],
) -> tuple:
    """Doc 1 §7: Full 6-level sort key using zone strength map.

    zone_strength_map: zone_id → strength (int). Caller populates from active zones.
    """
    tf = _zone_timeframe_from_id(c.zone_id)
    tf_weight = _TF_WEIGHTS.get(tf, 1)
    strength  = zone_strength_map.get(c.zone_id, 0)
    stop_dist = _compute_stop_distance(c, fill_price)

    return (
        -tf_weight,              # Rule 1: higher TF wins (negate)
        -strength,               # Rule 2: higher strength wins (negate)
        stop_dist,               # Rule 3: smaller stop wins
        -c.expected_R,           # Rule 4: larger R wins (negate)
        c.created_at,            # Rule 5: earlier creation wins
        c.candidate_id,          # Rule 6: lexicographic
    )


def _is_phase_compatible(direction: Direction, phase: Phase) -> bool:
    """Doc 7 §14 / Doc 1 §6.2: Check if current phase permits trade direction."""
    if direction == Direction.LONG:
        return phase in _LONG_COMPATIBLE_PHASES
    return phase in _SHORT_COMPATIBLE_PHASES


# ---------------------------------------------------------------------------
# EntryExecutor — main class
# ---------------------------------------------------------------------------

class EntryExecutor:
    """Doc 7: Deterministic entry trigger, risk gate, and trade management.

    One instance per instrument. Stateless between calls except for
    _open_trades (managed positions).

    Usage::

        executor = EntryExecutor(instrument="BTCUSDT")

        # On every closed 15m bar:
        new_trades = executor.process_bar(
            bar_15m=bar,
            active_candidates=setup_engine.get_active_candidates(),
            atr_15m=atr_15m.current_atr,
            phase_result=phase_engine.update(...),
            portfolio=PortfolioState(equity=Decimal("10000"), ...),
        )

        # On every bar (after process_bar):
        actions = executor.manage_open_trades(
            bar_15m=bar,
            phase_result=phase_result,
            completed_1h_leg=most_recent_1h_leg_if_just_completed,
        )
    """

    def __init__(self, instrument: str) -> None:
        self._instrument = instrument
        # Mutable state: open trade snapshots (updated via TradeAction)
        self._open_trades: dict[str, Trade] = {}   # trade_id → Trade

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def process_bar(
        self,
        bar_15m: AggregatedBar,
        active_candidates: Sequence[SetupCandidate],
        atr_15m: Decimal,
        phase_result: PhaseResult,
        portfolio: PortfolioState,
        zone_strength_map: Optional[dict[str, int]] = None,
    ) -> list[Trade]:
        """Doc 7 §3-§9: Check all active candidates against bar close.

        Returns list of newly opened Trade objects this bar.
        Applies the full authority hierarchy (Level 0 → Level 1 → Level 5).

        Args:
            bar_15m:           Closed 15m bar being processed.
            active_candidates: WAITING_ENTRY candidates from SetupEngine.
            atr_15m:           Current ATR(15m, 14) for stop-width check.
            phase_result:      Current phase output (for compatibility check).
            portfolio:         Current portfolio snapshot (equity, positions, paused).
            zone_strength_map: Optional dict zone_id → strength for tie-breaking.
                               If None, tie-breaking uses TF and R:R only.

        Returns:
            List of Trade objects created this bar (may be empty).
        """
        # --- Level 0: Data Integrity ---
        if not _bar_ohlcv_valid(bar_15m):
            logger.warning(
                "%s Level-0 data integrity FAILED for bar %s — aborting cycle.",
                self._instrument, bar_15m.timestamp_end,
            )
            return []

        # System paused — reject all entries (Level 1)
        if portfolio.system_paused:
            logger.info("%s system_paused=True — no entries this bar.", self._instrument)
            return []

        # Collect candidates that triggered entry
        triggered: list[tuple[SetupCandidate, Decimal]] = []   # (candidate, fill_price)
        for cand in active_candidates:
            if cand.status != CandidateStatus.WAITING_ENTRY:
                continue

            # Doc 7 §3: Entry trigger check (close must meet threshold)
            if not _entry_triggered(bar_15m, cand):
                continue

            fill = _actual_fill_price(bar_15m, cand)
            triggered.append((cand, fill))

        if not triggered:
            return []

        # --- Apply tie-breaking if multiple triggered same bar (Doc 1 §7) ---
        if len(triggered) > 1:
            strength_map = zone_strength_map or {}
            triggered.sort(
                key=lambda item: _tier_break_sort_key(
                    item[0], item[1], strength_map
                )
            )

        # --- Process candidates through risk gate, open trades ---
        new_trades: list[Trade] = []
        for cand, fill_price in triggered:
            trade = self._attempt_entry(
                candidate=cand,
                fill_price=fill_price,
                bar_15m=bar_15m,
                atr_15m=atr_15m,
                phase_result=phase_result,
                portfolio=portfolio,
            )
            if trade is not None:
                new_trades.append(trade)
                # Register in open trades
                self._open_trades[trade.trade_id] = trade
                # Update portfolio snapshot for subsequent candidates (same bar)
                # Build updated open list with new trade
                updated_positions = list(portfolio.open_positions) + [trade]
                portfolio = PortfolioState(
                    equity=portfolio.equity,
                    open_positions=updated_positions,
                    system_paused=portfolio.system_paused,
                    max_positions=portfolio.max_positions,
                )

        return new_trades

    def manage_open_trades(
        self,
        bar_15m: AggregatedBar,
        phase_result: PhaseResult,
        completed_1h_leg: Optional[CompletedLeg] = None,
    ) -> list[TradeAction]:
        """Doc 7 §12-§14: Manage existing open trades on each bar.

        Checks:
          1. Breakeven trigger: price reached +1R → stop moves to entry (§12)
          2. Phase flip: current phase incompatible with direction → EXIT (§14)
          3. Structure failure: opposite leg confirmed → EXIT (§14)

        Returns list of TradeAction objects (may include HOLD for unchanged trades).
        Level 0 data check: if bar invalid → return empty (do not manage on bad data).
        """
        if not _bar_ohlcv_valid(bar_15m):
            logger.warning(
                "%s Level-0 data integrity FAILED in manage_open_trades — skipping.",
                self._instrument,
            )
            return []

        actions: list[TradeAction] = []
        updated_trades: dict[str, Trade] = {}

        for trade_id, trade in self._open_trades.items():
            action, updated_trade = self._manage_one_trade(
                trade=trade,
                bar_15m=bar_15m,
                phase_result=phase_result,
                completed_1h_leg=completed_1h_leg,
            )
            actions.append(action)
            updated_trades[trade_id] = updated_trade

        self._open_trades = updated_trades
        return actions

    def close_trade(self, trade_id: str) -> Optional[Trade]:
        """External close of a trade (e.g. stop hit, target hit). Returns removed trade."""
        return self._open_trades.pop(trade_id, None)

    @property
    def open_trades(self) -> list[Trade]:
        """Current open trade snapshots."""
        return list(self._open_trades.values())

    # ------------------------------------------------------------------
    # Internal: entry attempt through authority hierarchy
    # ------------------------------------------------------------------

    def _attempt_entry(
        self,
        candidate: SetupCandidate,
        fill_price: Decimal,
        bar_15m: AggregatedBar,
        atr_15m: Decimal,
        phase_result: PhaseResult,
        portfolio: PortfolioState,
    ) -> Optional[Trade]:
        """Run a candidate through Level 1 risk gate. Return Trade or None.

        Doc 1 §6: Each level is a VETO — failure terminates and returns None.
        """
        # --- Level 1: Risk Gate (Doc 7 §8) ---

        # 1a. R:R check — use candidate's frozen expected_R (M6)
        min_r = _min_r_for_setup(candidate.setup_type)
        if candidate.expected_R < min_r - _EPSILON:
            logger.debug(
                "Candidate %s REJECTED: R:R=%.3f < %.1f",
                candidate.candidate_id, float(candidate.expected_R), float(min_r),
            )
            return None

        # 1b. Stop width check: stop_distance <= 1.5 × ATR(15m)
        stop_dist = _compute_stop_distance(candidate, fill_price)
        if stop_dist <= _EPSILON:
            logger.debug("Candidate %s REJECTED: zero stop distance", candidate.candidate_id)
            return None
        if atr_15m is not None and atr_15m > _EPSILON:
            max_stop = _MAX_STOP_ATR_MULT * atr_15m
            if stop_dist > max_stop + _EPSILON:
                logger.debug(
                    "Candidate %s REJECTED: stop_dist=%s > 1.5×ATR=%s",
                    candidate.candidate_id, stop_dist, max_stop,
                )
                return None

        # 1c. Portfolio risk capacity check
        account_risk = portfolio.equity * _ACCOUNT_RISK_PCT
        portfolio_risk_frac = _compute_portfolio_risk(portfolio, account_risk)
        if portfolio_risk_frac > _PORTFOLIO_RISK_CAP + _EPSILON:
            logger.debug(
                "Candidate %s REJECTED: portfolio_risk=%.2f%% > 6%%",
                candidate.candidate_id, float(portfolio_risk_frac * 100),
            )
            return None

        # 1d. Max simultaneous positions
        if len(portfolio.open_positions) >= portfolio.max_positions:
            logger.debug(
                "Candidate %s REJECTED: max_positions=%d reached",
                candidate.candidate_id, portfolio.max_positions,
            )
            return None

        # 1e. Duplicate zone/direction (Doc 7 §16)
        if _zone_already_traded(portfolio, candidate):
            logger.debug(
                "Candidate %s REJECTED: duplicate zone=%s dir=%s",
                candidate.candidate_id, candidate.zone_id, candidate.direction,
            )
            return None

        # --- Position sizing (Doc 7 §7) ---
        size = self._compute_position_size(portfolio.equity, stop_dist)
        if size < _MIN_LOT_SIZE - _EPSILON:
            logger.debug(
                "Candidate %s REJECTED: computed size=%s < min_lot=%s",
                candidate.candidate_id, size, _MIN_LOT_SIZE,
            )
            return None

        # --- Compute actual R at fill prices ---
        target_dist = abs(candidate.target_price - fill_price)
        actual_r = target_dist / stop_dist if stop_dist > _EPSILON else _D_ZERO

        # --- Create Trade ---
        opened_at = bar_15m.timestamp_end
        trade_id = _make_trade_id(candidate.candidate_id, opened_at)

        trade = Trade(
            trade_id=trade_id,
            candidate_id=candidate.candidate_id,
            instrument=self._instrument,
            direction=candidate.direction,
            setup_type=candidate.setup_type,
            entry_price_ref=candidate.entry_price,
            entry_price_actual=fill_price,
            stop_price=candidate.stop_price,
            target_price=candidate.target_price,
            stop_distance=stop_dist,
            expected_R=candidate.expected_R,
            actual_R_if_stopped=actual_r,
            position_size=size,
            risk_amount=account_risk,
            account_equity=portfolio.equity,
            zone_id=candidate.zone_id,
            opened_at=opened_at,
            status="OPEN",
            breakeven_triggered=False,
            current_stop=candidate.stop_price,
        )

        logger.info(
            "%s TRADE OPENED %s dir=%s setup=%s fill=%s stop=%s size=%s R=%.2f",
            self._instrument, trade_id,
            candidate.direction.name, candidate.setup_type.name,
            fill_price, candidate.stop_price, size, float(actual_r),
        )
        return trade

    # ------------------------------------------------------------------
    # Internal: position sizing
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_position_size(equity: Decimal, stop_distance: Decimal) -> Decimal:
        """Doc 7 §7: size = floor(equity × 0.01 / stop_distance).

        Rounded DOWN to nearest lot (Doc 7 §7.3).
        """
        if stop_distance <= _EPSILON:
            return _D_ZERO
        account_risk = equity * _ACCOUNT_RISK_PCT
        raw_size = account_risk / stop_distance
        # Floor to integer lots
        size = raw_size.to_integral_value(rounding=ROUND_DOWN)
        return size

    # ------------------------------------------------------------------
    # Internal: manage one open trade
    # ------------------------------------------------------------------

    def _manage_one_trade(
        self,
        trade: Trade,
        bar_15m: AggregatedBar,
        phase_result: PhaseResult,
        completed_1h_leg: Optional[CompletedLeg],
    ) -> tuple[TradeAction, Trade]:
        """Doc 7 §12-§14: Evaluate management conditions for one trade.

        Returns (TradeAction, updated_trade).
        Priority: EXIT conditions > BREAKEVEN > HOLD.
        """
        # --- Level 2: Phase flip check (Doc 7 §14) ---
        if not _is_phase_compatible(trade.direction, phase_result.active_phase):
            action = TradeAction(
                trade_id=trade.trade_id,
                action_type=TradeActionType.EXIT_PHASE_FLIP,
                reason=f"Phase {phase_result.active_phase.name} incompatible with {trade.direction.name}",
            )
            closed = _set_trade_status(trade, "CLOSED")
            logger.info(
                "%s TRADE %s EXIT_PHASE_FLIP: phase=%s dir=%s",
                self._instrument, trade.trade_id,
                phase_result.active_phase.name, trade.direction.name,
            )
            return action, closed

        # --- Level 3: Structure failure — opposite leg confirmed (Doc 7 §14) ---
        if completed_1h_leg is not None:
            opposite = self._is_opposite_leg(trade, completed_1h_leg)
            if opposite:
                action = TradeAction(
                    trade_id=trade.trade_id,
                    action_type=TradeActionType.EXIT_STRUCTURE_FAILURE,
                    reason=f"Opposite {completed_1h_leg.direction.name} leg confirmed on 1H",
                )
                closed = _set_trade_status(trade, "CLOSED")
                logger.info(
                    "%s TRADE %s EXIT_STRUCTURE_FAILURE: opposite_leg=%s",
                    self._instrument, trade.trade_id, completed_1h_leg.direction.name,
                )
                return action, closed

        # --- Doc 7 §12: Breakeven rule — move stop when price reaches +1R ---
        if not trade.breakeven_triggered:
            breakeven_hit = self._check_breakeven(trade, bar_15m)
            if breakeven_hit:
                new_stop = trade.entry_price_actual
                action = TradeAction(
                    trade_id=trade.trade_id,
                    action_type=TradeActionType.MOVE_STOP_BREAKEVEN,
                    new_stop=new_stop,
                    reason="Price reached +1R, stop moved to entry",
                )
                updated = _set_breakeven_triggered(trade, new_stop)
                logger.info(
                    "%s TRADE %s BREAKEVEN triggered, stop→%s",
                    self._instrument, trade.trade_id, new_stop,
                )
                return action, updated

        # --- No management action needed ---
        return TradeAction(
            trade_id=trade.trade_id,
            action_type=TradeActionType.HOLD,
        ), trade

    @staticmethod
    def _check_breakeven(trade: Trade, bar: AggregatedBar) -> bool:
        """Doc 7 §12: Return True if price has reached entry + 1×stop_distance.

        Long:  bar.high >= entry_actual + stop_distance - EPSILON
        Short: bar.low  <= entry_actual - stop_distance + EPSILON
        """
        if trade.direction == Direction.LONG:
            breakeven_level = trade.entry_price_actual + trade.stop_distance
            return bar.high >= breakeven_level - _EPSILON
        else:
            breakeven_level = trade.entry_price_actual - trade.stop_distance
            return bar.low <= breakeven_level + _EPSILON

    @staticmethod
    def _is_opposite_leg(trade: Trade, leg: CompletedLeg) -> bool:
        """Doc 7 §14: True if completed 1H leg is directionally opposite to trade.

        Long trade → opposite = BEAR leg completed
        Short trade → opposite = BULL leg completed
        """
        if trade.direction == Direction.LONG:
            return leg.direction == LegDirection.BEAR
        return leg.direction == LegDirection.BULL


# ---------------------------------------------------------------------------
# Frozen dataclass update helpers
# ---------------------------------------------------------------------------

def _set_trade_status(trade: Trade, status: str) -> Trade:
    """Return a copy of trade with updated status (frozen dataclass workaround)."""
    return Trade(
        trade_id=trade.trade_id,
        candidate_id=trade.candidate_id,
        instrument=trade.instrument,
        direction=trade.direction,
        setup_type=trade.setup_type,
        entry_price_ref=trade.entry_price_ref,
        entry_price_actual=trade.entry_price_actual,
        stop_price=trade.stop_price,
        target_price=trade.target_price,
        stop_distance=trade.stop_distance,
        expected_R=trade.expected_R,
        actual_R_if_stopped=trade.actual_R_if_stopped,
        position_size=trade.position_size,
        risk_amount=trade.risk_amount,
        account_equity=trade.account_equity,
        zone_id=trade.zone_id,
        opened_at=trade.opened_at,
        status=status,
        breakeven_triggered=trade.breakeven_triggered,
        current_stop=trade.current_stop,
    )


def _set_breakeven_triggered(trade: Trade, new_stop: Decimal) -> Trade:
    """Return a copy of trade with breakeven_triggered=True and updated current_stop."""
    return Trade(
        trade_id=trade.trade_id,
        candidate_id=trade.candidate_id,
        instrument=trade.instrument,
        direction=trade.direction,
        setup_type=trade.setup_type,
        entry_price_ref=trade.entry_price_ref,
        entry_price_actual=trade.entry_price_actual,
        stop_price=trade.stop_price,
        target_price=trade.target_price,
        stop_distance=trade.stop_distance,
        expected_R=trade.expected_R,
        actual_R_if_stopped=trade.actual_R_if_stopped,
        position_size=trade.position_size,
        risk_amount=trade.risk_amount,
        account_equity=trade.account_equity,
        zone_id=trade.zone_id,
        opened_at=trade.opened_at,
        status=trade.status,
        breakeven_triggered=True,
        current_stop=new_stop,
    )
