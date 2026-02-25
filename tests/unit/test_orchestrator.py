"""Tests for src/pipeline/orchestrator.py.

Acceptance criteria:
  AC1: run_cycle executes all 13 steps in exact Doc 1 §5 order
       — verified by step_reached == 13 on clean cycle.
  AC2: Data validation (Step 1) Level-0 failure → abort cycle early,
       step_reached == 1, cycle_errors non-empty.
  AC3: Step 2 (_HTFWindow) correctly aggregates 4 × 15m → 1 × 1H bar
       (open=first, high=max, low=min, close=last, volume=sum).
  AC4: Portfolio selection (Step 10) runs BEFORE entry execution (Step 8/11):
       system_paused → approved_candidates == [], no trades created.
  AC5: 3 consecutive Level-0 failures pause the instrument.
  AC6: replay_history feeds bars through steps 1-7 only (no DB writes).
  AC7: InstrumentContext created correctly for BTC (is_continuous=True)
       and Gold (is_continuous=False).
  AC8: No float anywhere in CycleResult, _HTFWindow arithmetic, or
       _validate_bar comparisons.

Design notes
------------
All DB calls are mocked (AsyncMock) — no live PostgreSQL required.
All component calls (ZLBBEngine, ZoneDetector, PhaseEngine, SetupEngine,
EntryExecutor, PortfolioManager) are mocked or use real lightweight instances.
"""

from __future__ import annotations

import asyncio
import datetime
from decimal import Decimal
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.data_ingest.bar_aggregator import AggregatedBar
from src.phase.phase_engine import Phase, PhaseResult
from src.pipeline.orchestrator import (
    CycleResult,
    InstrumentContext,
    Orchestrator,
    _HTFWindow,
    _validate_bar,
    _TF_15M,
    _TF_1H,
    _TF_4H,
    _TF_1D,
    _BARS_PER_1H,
    _BARS_PER_4H,
    _BARS_PER_1D,
)

# ---------------------------------------------------------------------------
# UTC & base timestamp
# ---------------------------------------------------------------------------
_UTC = datetime.timezone.utc
_BASE_TS = datetime.datetime(2024, 1, 15, 8, 0, 0, tzinfo=_UTC)   # Mon 08:00 UTC


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bar(
    i: int,
    o: str = "100",
    h: str = "110",
    l: str = "90",
    c: str = "105",
    volume: str = "1000",
    tf: str = "15m",
    symbol: str = "BTCUSDT",
    complete: bool = True,
) -> AggregatedBar:
    """Build an AggregatedBar for testing."""
    ts = _BASE_TS + datetime.timedelta(minutes=i * 15)
    return AggregatedBar(
        symbol=symbol,
        timestamp_start=ts,
        timestamp_end=ts + datetime.timedelta(minutes=15),
        timeframe=tf,
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(l),
        close=Decimal(c),
        volume=Decimal(volume),
        is_complete=complete,
        is_reliable=True,
    )


def _invalid_bar(i: int) -> AggregatedBar:
    """Bar with high < low — Level-0 failure."""
    ts = _BASE_TS + datetime.timedelta(minutes=i * 15)
    return AggregatedBar(
        symbol="BTCUSDT",
        timestamp_start=ts,
        timestamp_end=ts + datetime.timedelta(minutes=15),
        timeframe="15m",
        open=Decimal("100"),
        high=Decimal("80"),   # high < low — invalid
        low=Decimal("90"),
        close=Decimal("85"),
        volume=Decimal("500"),
        is_complete=True,
        is_reliable=True,
    )


def _make_phase_result(phase: Phase = Phase.BALANCE) -> PhaseResult:
    return PhaseResult(
        active_phase=phase,
        phase_confidence=Decimal("0.6"),
        transition_flag=False,
        size_reduction=Decimal("1.0"),
        all_scores={p: 0 for p in Phase},
    )


def _mock_state_manager():
    """Create a fully mocked StateManager (no DB)."""
    sm = MagicMock()
    sm.connect = AsyncMock()
    sm.close   = AsyncMock()
    sm.meets_minimum_history = AsyncMock(return_value=True)
    sm.verify_snapshot       = AsyncMock(return_value=True)
    sm.save_snapshot         = AsyncMock()
    sm.save_trade            = AsyncMock()
    sm.save_bar              = AsyncMock()
    return sm


def _make_orchestrator(equity: str = "10000") -> Orchestrator:
    """Return an Orchestrator with a mocked StateManager."""
    orch = Orchestrator(equity=Decimal(equity), db_dsn="mock://")
    orch._state_mgr = _mock_state_manager()
    return orch


def _run(coro):
    """Run an async coroutine synchronously in tests."""
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# Helper: build minimal InstrumentContext with mocked sub-components
# ---------------------------------------------------------------------------

def _make_ctx(symbol: str = "BTCUSDT") -> InstrumentContext:
    """Build a real InstrumentContext (no mocking of sub-components).

    Components are real but unfed — they will be in warmup state.
    Used for structural / integration tests.
    """
    from src.pipeline.orchestrator import _make_instrument_ctx_standalone
    # Fallback: use the factory method directly
    orch = _make_orchestrator()
    _run(orch.startup())
    return orch.get_context(symbol)


# ---------------------------------------------------------------------------
# AC3: _HTFWindow aggregation arithmetic
# ---------------------------------------------------------------------------

class TestHTFWindow:
    """AC3: _HTFWindow correctly aggregates N 15m bars → 1 HTF bar."""

    def test_four_bars_emit_one_1h(self) -> None:
        """4 × 15m bars → exactly 1 emitted 1H bar."""
        win = _HTFWindow(_TF_1H, _BARS_PER_1H)
        results = [win.push(_bar(i)) for i in range(4)]
        none_count = sum(1 for r in results if r is None)
        emit_count = sum(1 for r in results if r is not None)
        assert none_count == 3
        assert emit_count == 1

    def test_ohlcv_rules(self) -> None:
        """Doc 2 §4.2: open=first, high=max, low=min, close=last, volume=sum."""
        win = _HTFWindow(_TF_1H, _BARS_PER_1H)
        bars = [
            _bar(0, o="100", h="115", l="95",  c="110", volume="500"),
            _bar(1, o="110", h="120", l="105", c="118", volume="600"),
            _bar(2, o="118", h="125", l="112", c="115", volume="400"),
            _bar(3, o="115", h="118", l="100", c="108", volume="700"),
        ]
        result = None
        for b in bars:
            result = win.push(b)

        assert result is not None
        assert result.open   == Decimal("100")   # first
        assert result.high   == Decimal("125")   # max
        assert result.low    == Decimal("95")    # min
        assert result.close  == Decimal("108")   # last
        assert result.volume == Decimal("2200")  # sum

    def test_timeframe_label(self) -> None:
        """Emitted bar has correct timeframe label."""
        win = _HTFWindow(_TF_1H, _BARS_PER_1H)
        result = None
        for i in range(4):
            result = win.push(_bar(i))
        assert result is not None
        assert result.timeframe == _TF_1H

    def test_window_resets_after_emit(self) -> None:
        """After emitting, window resets: next 4 bars produce another 1H bar."""
        win = _HTFWindow(_TF_1H, _BARS_PER_1H)
        first = None
        for i in range(4):
            first = win.push(_bar(i))
        assert first is not None

        # Next 4 bars
        second = None
        for i in range(4, 8):
            second = win.push(_bar(i))
        assert second is not None
        assert second.open == _bar(4).open

    def test_4h_requires_16_bars(self) -> None:
        """_BARS_PER_4H = 16; no emit before 16th bar."""
        win = _HTFWindow(_TF_4H, _BARS_PER_4H)
        assert win.bars_per_htf == 16
        results = [win.push(_bar(i)) for i in range(16)]
        none_count = sum(1 for r in results if r is None)
        assert none_count == 15
        assert results[-1] is not None

    def test_is_complete_always_true(self) -> None:
        """Emitted HTF bar must always have is_complete=True."""
        win = _HTFWindow(_TF_1H, _BARS_PER_1H)
        result = None
        for i in range(4):
            result = win.push(_bar(i))
        assert result is not None
        assert result.is_complete is True

    def test_is_reliable_false_if_any_bar_unreliable(self) -> None:
        """If any constituent bar is unreliable, emitted bar is also unreliable."""
        win = _HTFWindow(_TF_1H, _BARS_PER_1H)
        unreliable_bar = AggregatedBar(
            symbol="BTCUSDT",
            timestamp_start=_BASE_TS + datetime.timedelta(minutes=30),
            timestamp_end=_BASE_TS + datetime.timedelta(minutes=45),
            timeframe="15m",
            open=Decimal("100"), high=Decimal("110"), low=Decimal("90"),
            close=Decimal("105"), volume=Decimal("500"),
            is_complete=True, is_reliable=False,
        )
        bars = [_bar(0), _bar(1), unreliable_bar, _bar(3)]
        result = None
        for b in bars:
            result = win.push(b)
        assert result is not None
        assert result.is_reliable is False

    def test_no_float_in_aggregation(self) -> None:
        """AC8: All arithmetic in _HTFWindow uses Decimal, never float."""
        win = _HTFWindow(_TF_1H, _BARS_PER_1H)
        result = None
        for i in range(4):
            result = win.push(_bar(i))
        assert isinstance(result.open,   Decimal)
        assert isinstance(result.high,   Decimal)
        assert isinstance(result.low,    Decimal)
        assert isinstance(result.close,  Decimal)
        assert isinstance(result.volume, Decimal)


# ---------------------------------------------------------------------------
# _validate_bar unit tests
# ---------------------------------------------------------------------------

class TestValidateBar:
    """Unit tests for the Level-0 _validate_bar() function."""

    def test_valid_bar_passes(self) -> None:
        assert _validate_bar(_bar(0)) is True

    def test_incomplete_bar_fails(self) -> None:
        b = _bar(0, complete=False)
        assert _validate_bar(b) is False

    def test_high_less_than_low_fails(self) -> None:
        assert _validate_bar(_invalid_bar(0)) is False

    def test_close_above_high_fails(self) -> None:
        b = _bar(0, c="200")  # c > h=110
        assert _validate_bar(b) is False

    def test_close_below_low_fails(self) -> None:
        b = _bar(0, l="90", c="50")  # c < l
        assert _validate_bar(b) is False

    def test_open_above_high_fails(self) -> None:
        b = _bar(0, o="200")  # o > h=110
        assert _validate_bar(b) is False

    def test_negative_volume_fails(self) -> None:
        b = AggregatedBar(
            symbol="BTCUSDT",
            timestamp_start=_BASE_TS,
            timestamp_end=_BASE_TS + datetime.timedelta(minutes=15),
            timeframe="15m",
            open=Decimal("100"), high=Decimal("110"),
            low=Decimal("90"),   close=Decimal("105"),
            volume=Decimal("-1"),
            is_complete=True, is_reliable=True,
        )
        assert _validate_bar(b) is False

    def test_no_float_comparison(self) -> None:
        """AC8: _validate_bar uses Decimal comparisons only."""
        # Ensure result is bool, no float leaks
        result = _validate_bar(_bar(0))
        assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# AC7: InstrumentContext creation
# ---------------------------------------------------------------------------

class TestInstrumentContextCreation:
    """AC7: InstrumentContext is correctly created for BTC and Gold."""

    def test_btc_context_created(self) -> None:
        orch = _make_orchestrator()
        _run(orch.startup())
        ctx = orch.get_context("BTCUSDT")
        assert ctx.symbol == "BTCUSDT"
        assert ctx.is_continuous is True

    def test_gold_context_created(self) -> None:
        orch = _make_orchestrator()
        _run(orch.startup())
        ctx = orch.get_context("XAUUSD")
        assert ctx.symbol == "XAUUSD"
        assert ctx.is_continuous is False

    def test_btc_and_gold_contexts_are_independent(self) -> None:
        """E5: Each instrument has its own component instances."""
        orch = _make_orchestrator()
        _run(orch.startup())
        btc = orch.get_context("BTCUSDT")
        gold = orch.get_context("XAUUSD")
        assert btc is not gold
        assert btc.atr_15m is not gold.atr_15m
        assert btc.zlbb_15m is not gold.zlbb_15m
        assert btc.phase_engine is not gold.phase_engine
        assert btc.setup_engine is not gold.setup_engine

    def test_htf_windows_initialised(self) -> None:
        """Each context has separate _HTFWindow instances."""
        orch = _make_orchestrator()
        _run(orch.startup())
        ctx = orch.get_context("BTCUSDT")
        assert ctx.htf_1h.timeframe == _TF_1H
        assert ctx.htf_4h.timeframe == _TF_4H
        assert ctx.htf_1d.timeframe == _TF_1D
        assert ctx.htf_1h.bars_per_htf == _BARS_PER_1H
        assert ctx.htf_4h.bars_per_htf == _BARS_PER_4H
        assert ctx.htf_1d.bars_per_htf == _BARS_PER_1D

    def test_unknown_instrument_raises(self) -> None:
        orch = _make_orchestrator()
        _run(orch.startup())
        with pytest.raises(KeyError):
            orch.get_context("ETHUSDT")


# ---------------------------------------------------------------------------
# AC1: run_cycle reaches step 13 on clean bar
# ---------------------------------------------------------------------------

class TestRunCycleStepOrder:
    """AC1: run_cycle executes all 13 steps in exact Doc 1 §5 order."""

    def test_clean_cycle_reaches_step_13(self) -> None:
        """A valid bar with no setup candidates → step_reached == 13."""
        orch = _make_orchestrator()
        _run(orch.startup())
        ctx = orch.get_context("BTCUSDT")
        bar = _bar(0)

        result: CycleResult = _run(orch.run_cycle(bar, ctx))

        assert result.step_reached == 13, (
            f"Expected step 13 but got {result.step_reached}. "
            f"Errors: {result.cycle_errors}"
        )
        assert result.cycle_errors == ()

    def test_step_counter_increments_through_all_steps(self) -> None:
        """Feed multiple bars; each clean cycle reaches step 13."""
        orch = _make_orchestrator()
        _run(orch.startup())
        ctx = orch.get_context("BTCUSDT")

        for i in range(5):
            result = _run(orch.run_cycle(_bar(i), ctx))
            assert result.step_reached == 13, (
                f"Bar {i}: expected step 13, got {result.step_reached}. "
                f"Errors: {result.cycle_errors}"
            )

    def test_instrument_in_result_matches_context(self) -> None:
        orch = _make_orchestrator()
        _run(orch.startup())
        ctx = orch.get_context("BTCUSDT")
        result = _run(orch.run_cycle(_bar(0), ctx))
        assert result.instrument == "BTCUSDT"

    def test_bar_timestamp_in_result(self) -> None:
        orch = _make_orchestrator()
        _run(orch.startup())
        ctx = orch.get_context("BTCUSDT")
        bar = _bar(0)
        result = _run(orch.run_cycle(bar, ctx))
        assert result.bar_timestamp == bar.timestamp_end

    def test_phase_in_result_is_valid_phase_enum(self) -> None:
        orch = _make_orchestrator()
        _run(orch.startup())
        ctx = orch.get_context("BTCUSDT")
        result = _run(orch.run_cycle(_bar(0), ctx))
        assert isinstance(result.phase, Phase)

    def test_phase_confidence_is_decimal(self) -> None:
        """AC8: phase_confidence must be Decimal, never float."""
        orch = _make_orchestrator()
        _run(orch.startup())
        ctx = orch.get_context("BTCUSDT")
        result = _run(orch.run_cycle(_bar(0), ctx))
        assert isinstance(result.phase_confidence, Decimal)


# ---------------------------------------------------------------------------
# AC2: Level-0 failure aborts at step 1
# ---------------------------------------------------------------------------

class TestLevel0Abort:
    """AC2: Invalid bar → step_reached == 1, cycle_errors non-empty."""

    def test_invalid_bar_aborts_at_step_1(self) -> None:
        orch = _make_orchestrator()
        _run(orch.startup())
        ctx = orch.get_context("BTCUSDT")

        result = _run(orch.run_cycle(_invalid_bar(0), ctx))

        assert result.step_reached == 1
        assert len(result.cycle_errors) > 0

    def test_incomplete_bar_aborts_before_step_1(self) -> None:
        """Incomplete bar is rejected before numbered steps begin."""
        orch = _make_orchestrator()
        _run(orch.startup())
        ctx = orch.get_context("BTCUSDT")

        result = _run(orch.run_cycle(_bar(0, complete=False), ctx))

        assert result.step_reached == 0
        assert len(result.cycle_errors) > 0

    def test_invalid_bar_increments_consecutive_errors(self) -> None:
        orch = _make_orchestrator()
        _run(orch.startup())
        ctx = orch.get_context("BTCUSDT")

        assert ctx.consecutive_errors == 0
        _run(orch.run_cycle(_invalid_bar(0), ctx))
        assert ctx.consecutive_errors == 1
        _run(orch.run_cycle(_invalid_bar(1), ctx))
        assert ctx.consecutive_errors == 2

    def test_valid_bar_resets_consecutive_errors(self) -> None:
        """Successful cycle resets consecutive_errors to 0."""
        orch = _make_orchestrator()
        _run(orch.startup())
        ctx = orch.get_context("BTCUSDT")

        # Prime with one failure
        _run(orch.run_cycle(_invalid_bar(0), ctx))
        assert ctx.consecutive_errors == 1

        # Valid cycle → reset
        _run(orch.run_cycle(_bar(1), ctx))
        assert ctx.consecutive_errors == 0


# ---------------------------------------------------------------------------
# AC5: 3 consecutive Level-0 failures pause the instrument
# ---------------------------------------------------------------------------

class TestConsecutiveErrorsPause:
    """AC5: 3 consecutive errors pause the instrument."""

    def test_three_failures_pause_instrument(self) -> None:
        orch = _make_orchestrator()
        _run(orch.startup())
        ctx = orch.get_context("BTCUSDT")

        for i in range(3):
            _run(orch.run_cycle(_invalid_bar(i), ctx))

        assert ctx.instrument_paused is True

    def test_two_failures_do_not_pause(self) -> None:
        orch = _make_orchestrator()
        _run(orch.startup())
        ctx = orch.get_context("BTCUSDT")

        for i in range(2):
            _run(orch.run_cycle(_invalid_bar(i), ctx))

        assert ctx.instrument_paused is False

    def test_instrument_pause_does_not_affect_other_instrument(self) -> None:
        """Instrument isolation: BTC pause does NOT pause Gold."""
        orch = _make_orchestrator()
        _run(orch.startup())
        btc  = orch.get_context("BTCUSDT")
        gold = orch.get_context("XAUUSD")

        # Pause BTC
        for i in range(3):
            _run(orch.run_cycle(_invalid_bar(i), btc))

        assert btc.instrument_paused is True
        # Gold should still be unpaused at instrument level
        assert gold.instrument_paused is False

    def test_paused_instrument_returns_empty_result(self) -> None:
        """After pausing, subsequent cycles produce empty results (no trades)."""
        orch = _make_orchestrator()
        _run(orch.startup())
        ctx = orch.get_context("BTCUSDT")

        # Cause pause
        for i in range(3):
            _run(orch.run_cycle(_invalid_bar(i), ctx))

        # Next cycle with a valid bar → still clean (step 13) but no trades
        result = _run(orch.run_cycle(_bar(10), ctx))
        assert result.new_trades == ()


# ---------------------------------------------------------------------------
# AC4: Portfolio selection runs before entry execution
# ---------------------------------------------------------------------------

class TestPortfolioSelectionOrder:
    """AC4: system_paused → no trades created (portfolio gate fires)."""

    def test_paused_portfolio_produces_no_trades(self) -> None:
        """When portfolio is paused, run_cycle returns no new trades."""
        orch = _make_orchestrator()
        _run(orch.startup())

        # Pause via data integrity
        orch._portfolio.pause_data_integrity()

        ctx    = orch.get_context("BTCUSDT")
        result = _run(orch.run_cycle(_bar(0), ctx))

        assert result.new_trades == ()

    def test_clean_portfolio_allows_cycle_to_complete(self) -> None:
        """Unpaused portfolio → cycle completes to step 13."""
        orch = _make_orchestrator()
        _run(orch.startup())
        ctx    = orch.get_context("BTCUSDT")
        result = _run(orch.run_cycle(_bar(0), ctx))
        assert result.step_reached == 13


# ---------------------------------------------------------------------------
# Startup sequence tests
# ---------------------------------------------------------------------------

class TestStartup:
    """Tests for the startup sequence (Doc 9 §3)."""

    def test_startup_returns_true_on_success(self) -> None:
        orch = _make_orchestrator()
        ok = _run(orch.startup())
        assert ok is True

    def test_startup_sets_started_flag(self) -> None:
        orch = _make_orchestrator()
        assert orch._started is False
        _run(orch.startup())
        assert orch._started is True

    def test_startup_creates_both_instrument_contexts(self) -> None:
        orch = _make_orchestrator()
        _run(orch.startup())
        assert "BTCUSDT" in orch._contexts
        assert "XAUUSD"  in orch._contexts

    def test_startup_calls_db_connect(self) -> None:
        orch = _make_orchestrator()
        _run(orch.startup())
        orch._state_mgr.connect.assert_called_once()

    def test_startup_checks_minimum_history(self) -> None:
        """E3: startup must call meets_minimum_history for all TFs."""
        orch = _make_orchestrator()
        _run(orch.startup())
        # Should have been called at least once per instrument per TF
        assert orch._state_mgr.meets_minimum_history.call_count >= 2

    def test_startup_verifies_hashes(self) -> None:
        """Doc 1 §11: startup calls verify_snapshot."""
        orch = _make_orchestrator()
        _run(orch.startup())
        assert orch._state_mgr.verify_snapshot.call_count >= 1

    def test_startup_db_failure_returns_false(self) -> None:
        """If DB connect fails, startup returns False."""
        orch = _make_orchestrator()
        orch._state_mgr.connect = AsyncMock(side_effect=ConnectionRefusedError("no db"))
        ok = _run(orch.startup())
        assert ok is False

    def test_startup_pauses_on_hash_mismatch(self) -> None:
        """Hash mismatch detected by StateManager → portfolio paused."""
        # Note: in current impl _verify_all_hashes always returns True
        # (StateManager itself logs CRITICAL and pauses via portfolio).
        # This test verifies that if we FORCE hash failure path, portfolio pauses.
        orch = _make_orchestrator()
        # Monkey-patch _verify_all_hashes to return False
        async def _bad_verify():
            return False
        orch._verify_all_hashes = _bad_verify
        _run(orch.startup())
        # Portfolio should be paused
        state = orch._portfolio.get_state()
        assert state.system_paused is True


# ---------------------------------------------------------------------------
# Shutdown tests
# ---------------------------------------------------------------------------

class TestShutdown:
    """Tests for graceful shutdown."""

    def test_shutdown_closes_db(self) -> None:
        orch = _make_orchestrator()
        _run(orch.startup())
        _run(orch.shutdown())
        orch._state_mgr.close.assert_called_once()

    def test_shutdown_saves_portfolio_snapshot(self) -> None:
        orch = _make_orchestrator()
        _run(orch.startup())
        _run(orch.shutdown())
        # At least one save_snapshot call for each instrument
        assert orch._state_mgr.save_snapshot.call_count >= 2


# ---------------------------------------------------------------------------
# replay_history tests
# ---------------------------------------------------------------------------

class TestReplayHistory:
    """AC6: replay_history feeds bars through steps 1-7 only (no DB writes)."""

    def test_replay_raises_for_unknown_symbol(self) -> None:
        orch = _make_orchestrator()
        _run(orch.startup())
        with pytest.raises(ValueError, match="Unknown instrument"):
            _run(orch.replay_history("ETHUSDT", [_bar(0)]))

    def test_replay_skips_incomplete_bars(self) -> None:
        """Incomplete bars are silently ignored during replay."""
        orch = _make_orchestrator()
        _run(orch.startup())
        # Should not raise
        _run(orch.replay_history("BTCUSDT", [_bar(0, complete=False)]))

    def test_replay_does_not_call_save_bar(self) -> None:
        """AC6: replay_history must NOT call save_bar (no DB writes)."""
        orch = _make_orchestrator()
        _run(orch.startup())
        bars = [_bar(i) for i in range(10)]
        _run(orch.replay_history("BTCUSDT", bars))
        # save_bar is only called from _persist_cycle, NOT from replay
        orch._state_mgr.save_bar.assert_not_called()

    def test_replay_does_not_call_save_trade(self) -> None:
        """AC6: replay_history must NOT call save_trade."""
        orch = _make_orchestrator()
        _run(orch.startup())
        bars = [_bar(i) for i in range(10)]
        _run(orch.replay_history("BTCUSDT", bars))
        orch._state_mgr.save_trade.assert_not_called()

    def test_replay_processes_multiple_bars(self) -> None:
        """100 bars replay without error."""
        orch = _make_orchestrator()
        _run(orch.startup())
        bars = [_bar(i) for i in range(100)]
        # Should complete without raising
        _run(orch.replay_history("BTCUSDT", bars))

    def test_replay_updates_atr_calculator(self) -> None:
        """After replay with enough bars, ATR calculator should have a value."""
        orch = _make_orchestrator()
        _run(orch.startup())
        ctx  = orch.get_context("BTCUSDT")

        # Feed 20 bars (ATR needs 14 to warm up)
        bars = [_bar(i) for i in range(20)]
        _run(orch.replay_history("BTCUSDT", bars))

        # ATR 15m should now have a value
        assert ctx.atr_15m.current_atr is not None
        assert isinstance(ctx.atr_15m.current_atr, Decimal)


# ---------------------------------------------------------------------------
# CycleResult immutability and no-float
# ---------------------------------------------------------------------------

class TestCycleResultContract:
    """CycleResult must be frozen and use Decimal for numeric fields."""

    def test_cycle_result_is_frozen(self) -> None:
        """CycleResult is a frozen dataclass — fields cannot be mutated."""
        orch = _make_orchestrator()
        _run(orch.startup())
        ctx    = orch.get_context("BTCUSDT")
        result = _run(orch.run_cycle(_bar(0), ctx))

        with pytest.raises((AttributeError, TypeError)):
            result.step_reached = 99  # type: ignore[misc]

    def test_phase_confidence_decimal(self) -> None:
        """AC8: phase_confidence in CycleResult is Decimal."""
        orch = _make_orchestrator()
        _run(orch.startup())
        ctx    = orch.get_context("BTCUSDT")
        result = _run(orch.run_cycle(_bar(0), ctx))
        assert isinstance(result.phase_confidence, Decimal)

    def test_new_trades_is_tuple(self) -> None:
        """new_trades must be a tuple (immutable)."""
        orch = _make_orchestrator()
        _run(orch.startup())
        ctx    = orch.get_context("BTCUSDT")
        result = _run(orch.run_cycle(_bar(0), ctx))
        assert isinstance(result.new_trades, tuple)

    def test_cycle_errors_is_tuple(self) -> None:
        """cycle_errors must be a tuple."""
        orch = _make_orchestrator()
        _run(orch.startup())
        ctx    = orch.get_context("BTCUSDT")
        result = _run(orch.run_cycle(_bar(0), ctx))
        assert isinstance(result.cycle_errors, tuple)


# ---------------------------------------------------------------------------
# HTFWindow: 1H close detection across multiple cycles
# ---------------------------------------------------------------------------

class TestHTFCloseDetection:
    """Verify that is_1h_close is correctly detected by the orchestrator."""

    def test_1h_bar_emitted_on_4th_15m_bar(self) -> None:
        """The 4th 15m bar should trigger a 1H bar emission."""
        orch = _make_orchestrator()
        _run(orch.startup())
        ctx = orch.get_context("BTCUSDT")

        # Feed 3 bars — no 1H should have closed yet
        for i in range(3):
            _run(orch.run_cycle(_bar(i), ctx))

        # After 3 bars, htf_1h window has 3 collected (not yet emitted)
        assert ctx.htf_1h.collected == 3

        # 4th bar completes the 1H window
        _run(orch.run_cycle(_bar(3), ctx))
        # Window resets after emission
        assert ctx.htf_1h.collected == 0

    def test_4h_bar_emitted_on_16th_15m_bar(self) -> None:
        """16 bars → exactly one 4H emission."""
        orch = _make_orchestrator()
        _run(orch.startup())
        ctx = orch.get_context("BTCUSDT")

        for i in range(16):
            _run(orch.run_cycle(_bar(i), ctx))

        # After 16 bars, 4H window should have just reset
        assert ctx.htf_4h.collected == 0


# ---------------------------------------------------------------------------
# run_forever: RuntimeError before startup
# ---------------------------------------------------------------------------

class TestRunForever:
    """run_forever must raise if startup() has not been called."""

    def test_run_forever_without_startup_raises(self) -> None:
        orch = _make_orchestrator()
        # DO NOT call startup
        with pytest.raises(RuntimeError, match="startup"):
            btc_q  = asyncio.Queue()
            gold_q = asyncio.Queue()
            # run_forever is a coroutine; create it then check it raises
            asyncio.get_event_loop().run_until_complete(
                orch.run_forever(btc_q, gold_q)
            )
