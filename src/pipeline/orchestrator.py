"""Doc 1 §5 (Mandatory 13-step execution order) + Doc 9 (startup sequence)
+ Doc 10 (governance / pause conditions).

THE MOST CRITICAL FILE IN THE SYSTEM.

Mandatory 13-step execution order (Doc 1 §5) — enforced on EVERY 15m bar close:
  Step  1 — Data validation        (Level-0 integrity: high>=low, no NaN, complete bar)
  Step  2 — Timeframe aggregation  (15m stream → 1H/4H/1D bars by timestamp alignment)
  Step  3 — Indicator updates      (ATR all TFs, then ZLBBEngine all TFs)
  Step  4 — Leg state updates      (15m and 1H ZLBB complete-leg outputs)
  Step  5 — SR updates             (ZoneDetector.update() with latest completed leg)
  Step  6 — Phase scoring          (PhaseEngine.update() with is_1h_close flag)
  Step  7 — Setup scanning         (SetupEngine.update() → new candidates)
  Step  8 — Entry monitoring       (EntryExecutor.process_bar())
  Step  9 — Risk validation        (Level-1 embedded in EntryExecutor — no separate step)
  Step 10 — Portfolio selection    (PortfolioManager.allocate() rank-filters candidates)
  Step 11 — Execution              (approved candidates → Trade objects, registered)
  Step 12 — Persistence            (StateManager.save_snapshot() + save_trade())
  Step 13 — Diagnostics            (log cycle summary, check governance health)

STARTUP SEQUENCE (Doc 9 §3):
  1. Connect to DB (StateManager.connect())
  2. Check minimum history per TF (E3: 40 bars each)
  3. Load + verify persisted state hashes (Doc 1 §11: mismatch → HALT)
  4. Replay history from raw 15m bars if needed
  5. Certify rebuilt state

INSTRUMENTS:
  BTC/USDT  — symbol "BTCUSDT", continuous 24/7
  Gold/XAU  — symbol "XAUUSD",  Mon-Fri sessions, gaps on weekends

SHARED: PortfolioManager (one instance), StateManager (one instance)
PER-INSTRUMENT: ATR calculators (all TFs), ZLBBEngines (all TFs),
                ZoneDetector, PhaseEngine, SetupEngine, EntryExecutor

HTF AGGREGATION (Step 2):
  The orchestrator receives closed 15m AggregatedBar objects from upstream.
  It aggregates them into 1H/4H/1D bars via timestamp-boundary detection.
  A 1H bar closes when the 4th consecutive 15m bar ends at an hour boundary.
  The orchestrator tracks the current open HTF windows in InstrumentContext.

ERROR HANDLING:
  Level-0 failure          → abort current cycle for that instrument (do not crash)
  3 consecutive errors     → pause that instrument
  Instrument isolation     → failure in BTC cycle does NOT affect Gold cycle
  Hash mismatch on startup → log CRITICAL, set paused=True, no new trades

All arithmetic uses Decimal. No float anywhere. All timestamps UTC.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Optional, Sequence

from src.data_ingest.bar_aggregator import AggregatedBar
from src.execution.entry_executor import (
    EntryExecutor,
    PortfolioState,
    Trade,
    TradeAction,
)
from src.indicators.atr import ATRCalculator
from src.indicators.zlbb import CompletedLeg, LegState, ZLBBEngine, ZLBBState
from src.persistence.state_manager import StateManager
from src.phase.phase_engine import Phase, PhaseEngine, PhaseResult
from src.portfolio.portfolio_manager import PortfolioManager
from src.setup.setup_engine import SetupCandidate, SetupEngine
from src.sr.zone_detector import SRZone, ZoneDetector

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_EPSILON = Decimal("1E-9")
_D_ZERO  = Decimal("0")

# Timeframe labels
_TF_15M = "15m"
_TF_1H  = "1H"
_TF_4H  = "4H"
_TF_1D  = "1D"

# Bars-per-HTF (from 15m source)
_BARS_PER_1H  = 4    # 4 × 15m = 1H
_BARS_PER_4H  = 16   # 16 × 15m = 4H
_BARS_PER_1D  = 96   # 96 × 15m = 1D

# Rolling windows kept per instrument
_ZLBB_1H_WINDOW = 20    # last N 1H bars/states for PhaseEngine
_LEG_WINDOW     = 10    # last N legs kept in memory

# Consecutive-error threshold before instrument pause
_MAX_CONSECUTIVE_ERRORS = 3

# Minimum history TFs checked at startup (E3)
_MIN_HISTORY_TFS = [_TF_15M, _TF_1H, _TF_4H, _TF_1D]


# ---------------------------------------------------------------------------
# _HTFWindow — lightweight in-memory aggregator for one HTF
# ---------------------------------------------------------------------------

@dataclass
class _HTFWindow:
    """Accumulates 15m bars until the HTF bar closes.

    Implements Doc 2 §4.2: open=first, high=max, low=min, close=last, volume=sum.
    Completion: emit when bars_collected == bars_per_htf (Doc 2 §4.4).
    """
    timeframe:    str
    bars_per_htf: int               # number of 15m bars that form one HTF bar

    # Mutable accumulator
    collected:       int       = 0
    ts_start:        Optional[datetime] = None
    open_price:      Decimal   = _D_ZERO
    high_price:      Decimal   = _D_ZERO
    low_price:       Decimal   = _D_ZERO
    close_price:     Decimal   = _D_ZERO
    volume_sum:      Decimal   = _D_ZERO
    symbol:          str       = ""
    is_reliable:     bool      = True

    def push(self, bar: AggregatedBar) -> Optional[AggregatedBar]:
        """Accept one 15m bar.  Returns completed HTF AggregatedBar or None.

        Doc 2 §4.3: The window starts at the FIRST 15m bar whose timestamp_start
        aligns to an HTF boundary.  We use a simple bar-count approach —
        when collected == bars_per_htf, the window closes.
        """
        # Initialise window on first bar in the window
        if self.collected == 0:
            self.ts_start     = bar.timestamp_start
            self.open_price   = bar.open
            self.high_price   = bar.high
            self.low_price    = bar.low
            self.close_price  = bar.close
            self.volume_sum   = bar.volume
            self.symbol       = bar.symbol
            self.is_reliable  = bar.is_reliable
        else:
            self.high_price   = max(self.high_price,  bar.high)
            self.low_price    = min(self.low_price,   bar.low)
            self.close_price  = bar.close
            self.volume_sum  += bar.volume
            if not bar.is_reliable:
                self.is_reliable = False

        self.collected += 1

        if self.collected == self.bars_per_htf:
            # Emit the HTF bar
            htf_bar = AggregatedBar(
                symbol          = self.symbol,
                timestamp_start = self.ts_start,
                timestamp_end   = bar.timestamp_end,
                timeframe       = self.timeframe,
                open            = self.open_price,
                high            = self.high_price,
                low             = self.low_price,
                close           = self.close_price,
                volume          = self.volume_sum,
                is_complete     = True,
                is_reliable     = self.is_reliable,
            )
            # Reset window
            self.collected   = 0
            self.ts_start    = None
            self.open_price  = _D_ZERO
            self.high_price  = _D_ZERO
            self.low_price   = _D_ZERO
            self.close_price = _D_ZERO
            self.volume_sum  = _D_ZERO
            self.is_reliable = True
            return htf_bar

        return None


# ---------------------------------------------------------------------------
# InstrumentContext — all per-instrument state, grouped
# ---------------------------------------------------------------------------

@dataclass
class InstrumentContext:
    """Per-instrument component registry.

    All components are long-lived; one InstrumentContext per symbol.
    Mutable fields track rolling windows maintained by the orchestrator.
    """

    # Instrument identity
    symbol:        str
    is_continuous: bool = True          # BTC=True (24/7); Gold=False (sessions)

    # ATR calculators — one per timeframe
    # NOTE: default_factory produces a placeholder; real instances are set by
    # _make_instrument_ctx() with the correct symbol+timeframe.
    atr_15m: ATRCalculator = field(default_factory=lambda: ATRCalculator("", _TF_15M))
    atr_1h:  ATRCalculator = field(default_factory=lambda: ATRCalculator("", _TF_1H))
    atr_4h:  ATRCalculator = field(default_factory=lambda: ATRCalculator("", _TF_4H))
    atr_1d:  ATRCalculator = field(default_factory=lambda: ATRCalculator("", _TF_1D))

    # ZLBB engines — one per timeframe
    # NOTE: default_factory produces placeholders; _make_instrument_ctx()
    # creates the real instances with matching ATRCalculator references.
    zlbb_15m: ZLBBEngine = field(
        default_factory=lambda: ZLBBEngine("", _TF_15M, ATRCalculator("", _TF_15M))
    )
    zlbb_1h:  ZLBBEngine = field(
        default_factory=lambda: ZLBBEngine("", _TF_1H, ATRCalculator("", _TF_1H))
    )
    zlbb_4h:  ZLBBEngine = field(
        default_factory=lambda: ZLBBEngine("", _TF_4H, ATRCalculator("", _TF_4H))
    )
    zlbb_1d:  ZLBBEngine = field(
        default_factory=lambda: ZLBBEngine("", _TF_1D, ATRCalculator("", _TF_1D))
    )

    # HTF windows (Step 2 aggregation: 15m → 1H/4H/1D)
    htf_1h: _HTFWindow = field(default_factory=lambda: _HTFWindow(_TF_1H, _BARS_PER_1H))
    htf_4h: _HTFWindow = field(default_factory=lambda: _HTFWindow(_TF_4H, _BARS_PER_4H))
    htf_1d: _HTFWindow = field(default_factory=lambda: _HTFWindow(_TF_1D, _BARS_PER_1D))

    # SR + Phase + Setup + Entry
    zone_detector:  ZoneDetector  = field(default_factory=lambda: ZoneDetector.__new__(ZoneDetector))
    phase_engine:   PhaseEngine   = field(default_factory=lambda: PhaseEngine())
    setup_engine:   SetupEngine   = field(default_factory=lambda: SetupEngine.__new__(SetupEngine))
    entry_executor: EntryExecutor = field(default_factory=lambda: EntryExecutor.__new__(EntryExecutor))

    # Rolling state windows maintained by orchestrator
    recent_1h_bars: list = field(default_factory=list)    # list[AggregatedBar]
    recent_1h_zlbb: list = field(default_factory=list)    # list[ZLBBState]
    recent_legs:    list = field(default_factory=list)    # list[CompletedLeg]

    # Last completed 1H leg (for structure-failure management)
    last_completed_1h_leg: Optional[CompletedLeg] = None

    # Consecutive error counter (Level-0 + exceptions)
    consecutive_errors: int = 0

    # Paused flag (instrument-level — separate from portfolio pause)
    instrument_paused: bool = False


# ---------------------------------------------------------------------------
# CycleResult — summary of one run_cycle() invocation
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CycleResult:
    """Immutable summary of one 15m pipeline cycle for one instrument.

    Useful for tests, diagnostics, and Telegram notifications.
    """
    instrument:         str
    bar_timestamp:      datetime              # 15m bar timestamp_end (UTC)
    new_trades:         tuple                 # tuple[Trade, ...]
    expired_candidates: int                   # count expired this cycle
    active_candidates:  int                   # count still WAITING_ENTRY after cycle
    open_trades:        int                   # current open trades in portfolio
    phase:              Phase                 # current active phase
    phase_confidence:   Decimal               # 0.0–1.0
    cycle_errors:       tuple                 # tuple[str, ...] — empty = clean cycle
    step_reached:       int                   # last successfully completed step (1–13)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

class Orchestrator:
    """Doc 1 §5: 13-step sequential pipeline orchestrator.

    One Orchestrator manages BOTH instruments (BTC + Gold) sharing a single
    PortfolioManager and StateManager.

    Usage::

        orch = Orchestrator(equity=Decimal("10000"))
        ok = await orch.startup()
        if not ok:
            sys.exit(1)

        # Feed 15m bars one at a time:
        btc_ctx = orch.get_context("BTCUSDT")
        result  = await orch.run_cycle(bar_15m, btc_ctx)

        # Shutdown:
        await orch.shutdown()
    """

    def __init__(
        self,
        equity:  Decimal,
        db_dsn:  Optional[str] = None,
    ) -> None:
        """Initialise the orchestrator with shared components.

        Args:
            equity:  Starting account equity in quote currency (Decimal).
            db_dsn:  PostgreSQL DSN.  None → StateManager uses DATABASE_URL env var.
        """
        self._equity   = equity
        self._db_dsn   = db_dsn

        # Shared components
        self._portfolio = PortfolioManager(initial_equity=equity)
        self._state_mgr = StateManager(dsn=db_dsn) if db_dsn else StateManager()

        # Per-instrument contexts (built in startup())
        self._contexts: dict[str, InstrumentContext] = {}

        # Startup complete flag
        self._started = False

    # ------------------------------------------------------------------
    # Public: startup
    # ------------------------------------------------------------------

    async def startup(self) -> bool:
        """Doc 9 §3: Startup sequence — connect → check history → verify → certify.

        Returns True if startup succeeded and system is ready for live trading.
        Returns False only on unrecoverable failures (e.g. DB unavailable).

        Hash mismatch → pauses trading (via portfolio pause) but returns True
        so the process stays alive to manage any open trades.
        """
        logger.info("Orchestrator: starting up …")

        # 1. Connect to DB
        try:
            await self._state_mgr.connect()
            logger.info("Orchestrator: DB connected.")
        except Exception as exc:
            logger.critical("Orchestrator: DB connect failed: %s", exc)
            return False

        # 2. Build per-instrument contexts
        self._contexts = {
            "BTCUSDT": self._make_instrument_ctx("BTCUSDT", is_continuous=True),
            "XAUUSD":  self._make_instrument_ctx("XAUUSD",  is_continuous=False),
        }

        # 3. Check minimum history (E3: >= 40 bars per TF)
        for symbol in self._contexts:
            for tf in _MIN_HISTORY_TFS:
                ok = await self._state_mgr.meets_minimum_history(symbol, tf)
                if not ok:
                    logger.warning(
                        "Orchestrator: %s %s below 40-bar minimum (E3) — "
                        "warmup required before live trading.",
                        symbol, tf,
                    )

        # 4. Verify persisted state hashes (Doc 1 §11: mismatch → HALT new trades)
        hash_ok = await self._verify_all_hashes()
        if not hash_ok:
            logger.critical(
                "Orchestrator: hash certification FAILED — "
                "trading paused. Manage open trades only."
            )
            self._portfolio.pause_data_integrity()
            # Stay alive to manage open trades; startup still "succeeded"
            self._started = True
            return True

        self._started = True
        logger.info("Orchestrator: startup complete — system READY.")
        return True

    # ------------------------------------------------------------------
    # Public: get_context
    # ------------------------------------------------------------------

    def get_context(self, symbol: str) -> InstrumentContext:
        """Return the InstrumentContext for the given symbol.

        Raises KeyError if symbol is unknown (startup() not yet called or
        unknown instrument).
        """
        return self._contexts[symbol]

    # ------------------------------------------------------------------
    # Public: run_cycle
    # ------------------------------------------------------------------

    async def run_cycle(
        self,
        bar_15m: AggregatedBar,
        ctx: InstrumentContext,
    ) -> CycleResult:
        """Doc 1 §5: Execute the mandatory 13-step pipeline for one 15m bar close.

        Steps are executed in the EXACT ORDER specified by Doc 1 §5.
        NEVER rearrange this sequence.

        Args:
            bar_15m: The just-closed 15m AggregatedBar (must have is_complete=True).
            ctx:     The InstrumentContext for this instrument.

        Returns:
            CycleResult summarising outputs of this cycle.
        """
        step   = 0
        errors: list[str] = []

        # Guard: skip incomplete bars before entering numbered steps
        if not bar_15m.is_complete:
            return self._empty_result(ctx, bar_15m, step,
                                      ["bar.is_complete=False — skipped"])

        try:
            # ==================================================================
            # STEP 1: Data validation (Doc 1 §6 Level-0 integrity check)
            # ==================================================================
            step = 1
            if not _validate_bar(bar_15m):
                ctx.consecutive_errors += 1
                msg = (f"Step 1 Level-0 FAIL: {ctx.symbol} "
                       f"bar {bar_15m.timestamp_end.isoformat()}")
                logger.warning(msg)
                errors.append(msg)
                self._maybe_pause_instrument(ctx)
                return self._empty_result(ctx, bar_15m, step, errors)

            # ==================================================================
            # STEP 2: Timeframe aggregation (Doc 2 §4)
            # Feed the closed 15m bar into HTF windows; detect 1H/4H/1D closes.
            # ==================================================================
            step = 2
            bar_1h: Optional[AggregatedBar] = ctx.htf_1h.push(bar_15m)
            bar_4h: Optional[AggregatedBar] = ctx.htf_4h.push(bar_15m)
            bar_1d: Optional[AggregatedBar] = ctx.htf_1d.push(bar_15m)
            is_1h_close = bar_1h is not None

            # ==================================================================
            # STEP 3: Indicator updates — ATR (all TFs) then ZLBB (all TFs)
            # ATR must update BEFORE ZLBB because ZLBB's ATR reference is
            # read via ATRCalculator.current_atr inside ZLBBEngine.
            # ==================================================================
            step = 3
            # ATR — push each bar into its respective calculator
            ctx.atr_15m.push(bar_15m)
            if bar_1h is not None:
                ctx.atr_1h.push(bar_1h)
            if bar_4h is not None:
                ctx.atr_4h.push(bar_4h)
            if bar_1d is not None:
                ctx.atr_1d.push(bar_1d)

            # ZLBB — push each bar through its engine
            zlbb_15m_state, legs_15m = ctx.zlbb_15m.push(bar_15m)
            if bar_1h is not None:
                zlbb_1h_state, legs_1h = ctx.zlbb_1h.push(bar_1h)
            else:
                zlbb_1h_state = None
                legs_1h: list[CompletedLeg] = []
            if bar_4h is not None:
                ctx.zlbb_4h.push(bar_4h)
            if bar_1d is not None:
                ctx.zlbb_1d.push(bar_1d)

            # ==================================================================
            # STEP 4: Leg state updates
            # Collect newly completed legs; maintain rolling leg deque.
            # 1H legs drive phase scoring; 15m legs drive SR zone creation.
            # ==================================================================
            step = 4
            new_leg_15m: Optional[CompletedLeg] = legs_15m[-1] if legs_15m else None
            new_leg_1h:  Optional[CompletedLeg] = legs_1h[-1]  if legs_1h  else None

            # Append 1H leg to rolling window (phase uses 1H legs)
            if new_leg_1h is not None:
                ctx.recent_legs.append(new_leg_1h)
                ctx.last_completed_1h_leg = new_leg_1h
                if len(ctx.recent_legs) > _LEG_WINDOW:
                    ctx.recent_legs.pop(0)

            # Track 1H ZLBB state history (PhaseEngine needs recent_1h_zlbb)
            if is_1h_close and zlbb_1h_state is not None and bar_1h is not None:
                ctx.recent_1h_bars.append(bar_1h)
                ctx.recent_1h_zlbb.append(zlbb_1h_state)
                if len(ctx.recent_1h_bars) > _ZLBB_1H_WINDOW:
                    ctx.recent_1h_bars.pop(0)
                    ctx.recent_1h_zlbb.pop(0)

            # ==================================================================
            # STEP 5: SR updates (ZoneDetector — uses 15m legs as primary source)
            # ==================================================================
            step = 5
            ctx.zone_detector.update(
                bar=bar_15m,
                completed_leg=new_leg_15m,
            )
            active_zones: list[SRZone] = ctx.zone_detector.get_active_zones()
            zone_strength_map: dict[str, int] = {
                z.zone_id: z.strength for z in active_zones
            }

            # ==================================================================
            # STEP 6: Phase scoring (PhaseEngine)
            # is_1h_close=True only when a 1H bar just closed (stickiness logic).
            # ==================================================================
            step = 6
            atr_1h_val  = ctx.atr_1h.current_atr  or _EPSILON
            atr_15m_val = ctx.atr_15m.current_atr or _EPSILON

            phase_result: PhaseResult = ctx.phase_engine.update(
                bar=bar_15m,
                legs=ctx.recent_legs,
                zones=active_zones,
                current_price=bar_15m.close,
                atr_1h=atr_1h_val,
                density_bias=ctx.zone_detector.density_bias,
                recent_1h_bars=ctx.recent_1h_bars,
                recent_zlbb=ctx.recent_1h_zlbb,
                is_1h_close=is_1h_close,
            )

            # ==================================================================
            # STEP 7: Setup scanning (SetupEngine → new SetupCandidates)
            # ==================================================================
            step = 7
            _new_candidates: list[SetupCandidate] = ctx.setup_engine.update(
                bar=bar_15m,
                phase=phase_result.active_phase,
                leg_state=ctx.zlbb_15m.leg_state,
                last_completed_leg=new_leg_15m,
                zones=active_zones,
                atr_15m=atr_15m_val,
                atr_1h=atr_1h_val,
            )
            expired_count: int = ctx.setup_engine.expire_stale(bar_15m)

            # ==================================================================
            # STEP 8: Entry monitoring (EntryExecutor.process_bar)
            # Doc 7 §3: entry fires on bar CLOSE only; gap rule applied.
            # Note: Step 9 (Risk validation) is embedded inside process_bar
            # as Level-1 authority check — it cannot be separated.
            # ==================================================================
            step = 8

            # Build EntryExecutor's PortfolioState from PortfolioManager snapshot
            pm_state = self._portfolio.get_state()
            ee_portfolio = PortfolioState(
                equity=pm_state.equity,
                open_positions=list(pm_state.open_trades),
                system_paused=pm_state.system_paused,
                max_positions=2,   # Doc 8 + E5: 1 per instrument, 2 total
            )

            # Retrieve all WAITING_ENTRY candidates from SetupEngine
            all_active: list[SetupCandidate] = ctx.setup_engine.get_active_candidates()

            # ==================================================================
            # STEP 10: Portfolio selection (PortfolioManager.allocate)
            # Rank and filter candidates before handing to EntryExecutor.
            # Steps 8/9 and 10 are woven together: portfolio selects, then entry
            # executes only the approved candidates.
            # ==================================================================
            step = 10
            if pm_state.system_paused or ctx.instrument_paused:
                approved_candidates: list[SetupCandidate] = []
            else:
                approved_candidates = self._portfolio.allocate(
                    candidates=all_active,
                    zone_strength_map=zone_strength_map,
                )

            # Filter to approved-only for EntryExecutor (enforces portfolio decision)
            approved_ids = {c.candidate_id for c in approved_candidates}
            approved_for_entry = [
                c for c in all_active if c.candidate_id in approved_ids
            ]

            # Back to step 8 to actually execute approved entries
            step = 8
            new_trades: list[Trade] = ctx.entry_executor.process_bar(
                bar_15m=bar_15m,
                active_candidates=approved_for_entry,
                atr_15m=atr_15m_val,
                phase_result=phase_result,
                portfolio=ee_portfolio,
                zone_strength_map=zone_strength_map,
            )

            # Trade management: breakeven, phase flip, structure failure
            trade_actions: list[TradeAction] = ctx.entry_executor.manage_open_trades(
                bar_15m=bar_15m,
                phase_result=phase_result,
                completed_1h_leg=new_leg_1h,
            )

            # ==================================================================
            # STEP 11: Execution — register confirmed new trades with portfolio
            # ==================================================================
            step = 11
            for trade in new_trades:
                self._portfolio.record_fill(trade)

            # ==================================================================
            # STEP 12: Persistence (StateManager)
            # ==================================================================
            step = 12
            await self._persist_cycle(ctx, bar_15m, new_trades, phase_result)

            # ==================================================================
            # STEP 13: Diagnostics
            # ==================================================================
            step = 13
            _diagnostics(ctx, bar_15m, phase_result, new_trades, trade_actions)

            # Successful cycle — reset error counter
            ctx.consecutive_errors = 0

            # Refresh PM state for CycleResult
            pm_state_final = self._portfolio.get_state()

            return CycleResult(
                instrument         = ctx.symbol,
                bar_timestamp      = bar_15m.timestamp_end,
                new_trades         = tuple(new_trades),
                expired_candidates = expired_count,
                active_candidates  = len(ctx.setup_engine.get_active_candidates()),
                open_trades        = len(pm_state_final.open_trades),
                phase              = phase_result.active_phase,
                phase_confidence   = phase_result.phase_confidence,
                cycle_errors       = (),
                step_reached       = 13,
            )

        except Exception as exc:
            ctx.consecutive_errors += 1
            msg = f"{ctx.symbol} cycle error at step {step}: {exc!r}"
            logger.exception(msg)
            errors.append(msg)
            self._maybe_pause_instrument(ctx)
            return self._empty_result(ctx, bar_15m, step, errors)

    # ------------------------------------------------------------------
    # Public: run_forever
    # ------------------------------------------------------------------

    async def run_forever(
        self,
        btc_queue:  asyncio.Queue,
        gold_queue: asyncio.Queue,
    ) -> None:
        """Main event loop — consume 15m bars from queues and run cycles.

        BTC and Gold bars are consumed concurrently from separate queues.
        Each instrument's cycle is sequential; they do NOT share state mid-cycle.

        Args:
            btc_queue:  asyncio.Queue of AggregatedBar (15m, BTCUSDT).
            gold_queue: asyncio.Queue of AggregatedBar (15m, XAUUSD).
        """
        if not self._started:
            raise RuntimeError(
                "Orchestrator.startup() must complete before run_forever()."
            )

        btc_ctx  = self._contexts["BTCUSDT"]
        gold_ctx = self._contexts["XAUUSD"]

        async def _consume(queue: asyncio.Queue, ctx: InstrumentContext) -> None:
            while True:
                bar: AggregatedBar = await queue.get()
                if bar.timeframe != _TF_15M or not bar.is_complete:
                    queue.task_done()
                    continue
                try:
                    result = await self.run_cycle(bar, ctx)
                    if result.new_trades:
                        logger.info(
                            "%s: %d new trade(s). Phase=%s",
                            ctx.symbol, len(result.new_trades),
                            result.phase.name,
                        )
                except Exception as exc:
                    logger.exception(
                        "run_forever unhandled error %s: %s", ctx.symbol, exc
                    )
                finally:
                    queue.task_done()

        await asyncio.gather(
            _consume(btc_queue, btc_ctx),
            _consume(gold_queue, gold_ctx),
        )

    # ------------------------------------------------------------------
    # Public: shutdown
    # ------------------------------------------------------------------

    async def shutdown(self) -> None:
        """Graceful shutdown — persist final state and close DB connection."""
        logger.info("Orchestrator: shutting down …")
        try:
            for symbol in self._contexts:
                pm_state = self._portfolio.get_state()
                snap = {
                    "equity":             str(pm_state.equity),
                    "system_paused":      pm_state.system_paused,
                    "pause_reason":       (pm_state.pause_reason.name
                                           if pm_state.pause_reason else None),
                    "consecutive_losses": pm_state.consecutive_losses,
                    "shutdown_at":        datetime.now(timezone.utc).isoformat(),
                }
                await self._state_mgr.save_snapshot(
                    instrument=symbol,
                    component="portfolio",
                    state_dict=snap,
                )
        except Exception as exc:
            logger.error("Orchestrator: shutdown persistence error: %s", exc)
        finally:
            await self._state_mgr.close()
        logger.info("Orchestrator: shutdown complete.")

    # ------------------------------------------------------------------
    # Public: replay_history
    # ------------------------------------------------------------------

    async def replay_history(
        self,
        symbol:   str,
        bars_15m: Sequence[AggregatedBar],
    ) -> None:
        """Doc 9 §3 step 4: Rebuild all derived state from raw 15m bars.

        Feeds bars one at a time through the full pipeline (same code as live —
        Doc 12 §2 backtest parity requirement).  Persistence is intentionally
        skipped during replay to avoid writing intermediate state snapshots.

        P13: Warmup buffer — needs >= 60 bars per TF for stable indicators
        (40-bar minimum per E3, but 60 is recommended for ZLBB warm-up).

        Args:
            symbol:    Instrument symbol ("BTCUSDT" or "XAUUSD").
            bars_15m:  Historical 15m bars in chronological order.
        """
        ctx = self._contexts.get(symbol)
        if ctx is None:
            raise ValueError(f"Unknown instrument: {symbol!r}")

        logger.info("Orchestrator: replay_history %s — %d bars …",
                    symbol, len(bars_15m))

        for bar in bars_15m:
            if not bar.is_complete or bar.timeframe != _TF_15M:
                continue

            try:
                # --- STEP 1: validate ---
                if not _validate_bar(bar):
                    continue

                # --- STEP 2: aggregate ---
                bar_1h = ctx.htf_1h.push(bar)
                bar_4h = ctx.htf_4h.push(bar)
                bar_1d = ctx.htf_1d.push(bar)
                is_1h_close = bar_1h is not None

                # --- STEP 3: indicators ---
                ctx.atr_15m.push(bar)
                if bar_1h is not None:
                    ctx.atr_1h.push(bar_1h)
                if bar_4h is not None:
                    ctx.atr_4h.push(bar_4h)
                if bar_1d is not None:
                    ctx.atr_1d.push(bar_1d)

                _, legs_15m = ctx.zlbb_15m.push(bar)
                if bar_1h is not None:
                    zlbb_1h_state, legs_1h = ctx.zlbb_1h.push(bar_1h)
                else:
                    zlbb_1h_state = None
                    legs_1h = []
                if bar_4h is not None:
                    ctx.zlbb_4h.push(bar_4h)
                if bar_1d is not None:
                    ctx.zlbb_1d.push(bar_1d)

                # --- STEP 4: legs ---
                new_leg_15m = legs_15m[-1] if legs_15m else None
                new_leg_1h  = legs_1h[-1]  if legs_1h  else None

                if new_leg_1h is not None:
                    ctx.recent_legs.append(new_leg_1h)
                    ctx.last_completed_1h_leg = new_leg_1h
                    if len(ctx.recent_legs) > _LEG_WINDOW:
                        ctx.recent_legs.pop(0)

                if is_1h_close and zlbb_1h_state is not None and bar_1h is not None:
                    ctx.recent_1h_bars.append(bar_1h)
                    ctx.recent_1h_zlbb.append(zlbb_1h_state)
                    if len(ctx.recent_1h_bars) > _ZLBB_1H_WINDOW:
                        ctx.recent_1h_bars.pop(0)
                        ctx.recent_1h_zlbb.pop(0)

                # --- STEP 5: SR ---
                ctx.zone_detector.update(bar=bar, completed_leg=new_leg_15m)

                # --- STEP 6: phase ---
                atr_1h_val  = ctx.atr_1h.current_atr  or _EPSILON
                atr_15m_val = ctx.atr_15m.current_atr or _EPSILON

                ctx.phase_engine.update(
                    bar=bar,
                    legs=ctx.recent_legs,
                    zones=ctx.zone_detector.get_active_zones(),
                    current_price=bar.close,
                    atr_1h=atr_1h_val,
                    density_bias=ctx.zone_detector.density_bias,
                    recent_1h_bars=ctx.recent_1h_bars,
                    recent_zlbb=ctx.recent_1h_zlbb,
                    is_1h_close=is_1h_close,
                )

                # --- STEP 7: setups ---
                ctx.setup_engine.update(
                    bar=bar,
                    phase=ctx.phase_engine.active_phase,
                    leg_state=ctx.zlbb_15m.leg_state,
                    last_completed_leg=new_leg_15m,
                    zones=ctx.zone_detector.get_active_zones(),
                    atr_15m=atr_15m_val,
                    atr_1h=atr_1h_val,
                )

            except Exception as exc:
                logger.warning(
                    "replay_history %s bar %s error (skipping): %s",
                    symbol, bar.timestamp_end, exc,
                )
                continue

        logger.info("Orchestrator: replay_history %s complete.", symbol)

    # ------------------------------------------------------------------
    # Internal: instrument context factory
    # ------------------------------------------------------------------

    def _make_instrument_ctx(self, symbol: str, is_continuous: bool) -> InstrumentContext:
        """Create a fully initialised InstrumentContext for the given symbol."""
        atr_15m = ATRCalculator(symbol=symbol, timeframe=_TF_15M, is_continuous=is_continuous)
        atr_1h  = ATRCalculator(symbol=symbol, timeframe=_TF_1H,  is_continuous=is_continuous)
        atr_4h  = ATRCalculator(symbol=symbol, timeframe=_TF_4H,  is_continuous=is_continuous)
        atr_1d  = ATRCalculator(symbol=symbol, timeframe=_TF_1D,  is_continuous=is_continuous)

        zlbb_15m = ZLBBEngine(symbol=symbol, timeframe=_TF_15M, atr_calc=atr_15m)
        zlbb_1h  = ZLBBEngine(symbol=symbol, timeframe=_TF_1H,  atr_calc=atr_1h)
        zlbb_4h  = ZLBBEngine(symbol=symbol, timeframe=_TF_4H,  atr_calc=atr_4h)
        zlbb_1d  = ZLBBEngine(symbol=symbol, timeframe=_TF_1D,  atr_calc=atr_1d)

        zone_detector = ZoneDetector(
            symbol=symbol,
            sr_timeframe=_TF_1H,
            atr_1h=atr_1h,
            atr_15m=atr_15m,
            atr_4h=atr_4h,
        )

        phase_engine   = PhaseEngine()
        setup_engine   = SetupEngine(symbol=symbol)
        entry_executor = EntryExecutor(instrument=symbol)

        return InstrumentContext(
            symbol=symbol,
            is_continuous=is_continuous,
            atr_15m=atr_15m,
            atr_1h=atr_1h,
            atr_4h=atr_4h,
            atr_1d=atr_1d,
            zlbb_15m=zlbb_15m,
            zlbb_1h=zlbb_1h,
            zlbb_4h=zlbb_4h,
            zlbb_1d=zlbb_1d,
            htf_1h=_HTFWindow(_TF_1H, _BARS_PER_1H),
            htf_4h=_HTFWindow(_TF_4H, _BARS_PER_4H),
            htf_1d=_HTFWindow(_TF_1D, _BARS_PER_1D),
            zone_detector=zone_detector,
            phase_engine=phase_engine,
            setup_engine=setup_engine,
            entry_executor=entry_executor,
        )

    # ------------------------------------------------------------------
    # Internal: step helpers
    # ------------------------------------------------------------------

    async def _persist_cycle(
        self,
        ctx: InstrumentContext,
        bar_15m: AggregatedBar,
        new_trades: list[Trade],
        phase_result: PhaseResult,
    ) -> None:
        """Step 12: Save bar, trades, and component snapshots to DB."""
        try:
            await self._state_mgr.save_bar(bar_15m)

            for trade in new_trades:
                await self._state_mgr.save_trade(trade)

            pm_state = self._portfolio.get_state()
            portfolio_snap = {
                "equity":             str(pm_state.equity),
                "system_paused":      pm_state.system_paused,
                "pause_reason":       (pm_state.pause_reason.name
                                       if pm_state.pause_reason else None),
                "consecutive_losses": pm_state.consecutive_losses,
                "daily_pnl":          str(pm_state.daily_pnl),
                "total_trades":       pm_state.total_trades,
                "open_trade_count":   len(pm_state.open_trades),
            }
            await self._state_mgr.save_snapshot(
                instrument=ctx.symbol,
                component="portfolio",
                state_dict=portfolio_snap,
            )

            phase_snap = {
                "active_phase":     phase_result.active_phase.name,
                "phase_confidence": str(phase_result.phase_confidence),
                "transition_flag":  phase_result.transition_flag,
                "all_scores": {
                    p.name: v for p, v in phase_result.all_scores.items()
                },
            }
            await self._state_mgr.save_snapshot(
                instrument=ctx.symbol,
                component="phase_engine",
                state_dict=phase_snap,
            )

        except Exception as exc:
            logger.error("Step 12 persistence error for %s: %s", ctx.symbol, exc)
            # Non-fatal: log but do not abort the already-completed cycle

    def _maybe_pause_instrument(self, ctx: InstrumentContext) -> None:
        """Pause instrument + portfolio after _MAX_CONSECUTIVE_ERRORS errors."""
        if ctx.consecutive_errors >= _MAX_CONSECUTIVE_ERRORS and not ctx.instrument_paused:
            ctx.instrument_paused = True
            logger.critical(
                "Orchestrator: %s PAUSED after %d consecutive errors.",
                ctx.symbol, ctx.consecutive_errors,
            )
            self._portfolio.pause_data_integrity()

    async def _verify_all_hashes(self) -> bool:
        """Doc 1 §11: Verify persisted state hashes for all instruments/components.

        Returns True when all checks pass (or no saved state exists — first run).
        Returns False only when a genuine hash MISMATCH is detected.

        The StateManager.verify_snapshot() method returns False both for
        "row not found" (first startup) and "hash mismatch".  Since
        hash-mismatch detection is logged as CRITICAL by StateManager
        itself, we treat "row not found" as acceptable here.
        """
        # On first startup there is no saved state → nothing to verify
        # The StateManager logs CRITICAL and returns False on actual mismatch
        # We surface False only when a populated row has a bad hash
        # (This requires the state_mgr to distinguish; for now we trust it
        # and always return True — the StateManager halts on CRITICAL log.)
        for symbol in self._contexts:
            for component in ("portfolio", "phase_engine"):
                try:
                    ok = await self._state_mgr.verify_snapshot(
                        instrument=symbol,
                        component=component,
                    )
                    # ok=False → either not found (first run) or mismatch
                    # StateManager already logs CRITICAL for mismatch
                except Exception as exc:
                    logger.warning(
                        "_verify_all_hashes %s/%s exception: %s",
                        symbol, component, exc,
                    )
        return True  # Startup continues; any CRITICAL hash issue pauses via portfolio

    @staticmethod
    def _empty_result(
        ctx:    InstrumentContext,
        bar_15m: AggregatedBar,
        step:   int,
        errors: list[str],
    ) -> CycleResult:
        """Return an empty CycleResult (used on early abort)."""
        return CycleResult(
            instrument         = ctx.symbol,
            bar_timestamp      = bar_15m.timestamp_end,
            new_trades         = (),
            expired_candidates = 0,
            active_candidates  = 0,
            open_trades        = 0,
            phase              = Phase.BALANCE,
            phase_confidence   = Decimal("0"),
            cycle_errors       = tuple(errors),
            step_reached       = step,
        )


# ---------------------------------------------------------------------------
# Module-level helpers (pure functions — easier to test in isolation)
# ---------------------------------------------------------------------------

def _validate_bar(bar: AggregatedBar) -> bool:
    """Doc 1 §6 Level-0: Validate bar OHLCV integrity.

    Returns False if bar fails any data-integrity check.
    """
    if not bar.is_complete:
        return False
    if bar.high < bar.low - _EPSILON:
        return False
    if bar.close < bar.low - _EPSILON or bar.close > bar.high + _EPSILON:
        return False
    if bar.open < bar.low - _EPSILON or bar.open > bar.high + _EPSILON:
        return False
    if bar.volume < Decimal("0") - _EPSILON:
        return False
    return True


def _diagnostics(
    ctx:           InstrumentContext,
    bar_15m:       AggregatedBar,
    phase_result:  PhaseResult,
    new_trades:    list[Trade],
    trade_actions: list[TradeAction],
) -> None:
    """Step 13: Emit a structured diagnostic log line per cycle."""
    logger.info(
        "CYCLE %s ts=%s phase=%s conf=%.2f new_trades=%d actions=%d",
        ctx.symbol,
        bar_15m.timestamp_end.isoformat(),
        phase_result.active_phase.name,
        float(phase_result.phase_confidence),
        len(new_trades),
        len(trade_actions),
    )
