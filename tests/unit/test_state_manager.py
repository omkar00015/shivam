"""Tests for src/persistence/state_manager.py.

Testing strategy
----------------
The StateManager wraps asyncpg (PostgreSQL).  Unit tests MUST NOT require a
live database.  We therefore test the module at two levels:

  Layer A — Pure-logic helpers (no DB required, synchronous):
    _hash_state, _canonical_json, _decimal_to_str, _str_to_decimal,
    _dt_to_str, _str_to_dt, trade_to_dict, trade_from_dict,
    bar_to_dict, bar_from_dict.

  Layer B — StateManager methods with a mocked asyncpg pool:
    All public async methods are tested with unittest.mock.AsyncMock so the
    test suite runs without PostgreSQL.  We verify:
      - correct SQL is executed (argument shapes)
      - correct data transformations (serialisation round-trips)
      - hash verification logic (pass / fail)
      - minimum-history threshold logic

Acceptance criteria:
  AC1: save_snapshot + load_snapshot round-trips state correctly (Decimal as str).
  AC2: Hash mismatch on load → verify_snapshot returns False.
  AC3: save_bar + load_bars returns bars in ascending timestamp order.
  AC4: meets_minimum_history: < 40 bars → False, >= 40 → True.
  AC5: save_trade + load_open_trades returns only OPEN status trades.
  AC6: update_trade sets closed_at and pnl correctly.
  AC7: All Decimal fields survive round-trip as Decimal (not float).
  AC8: No float anywhere in state_dict / serialisation helpers.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import hashlib
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.data_ingest.bar_aggregator import AggregatedBar
from src.execution.entry_executor import Trade
from src.persistence.state_manager import (
    StateManager,
    _canonical_json,
    _decimal_to_str,
    _dt_to_str,
    _hash_state,
    _str_to_decimal,
    _str_to_dt,
    bar_from_dict,
    bar_to_dict,
    trade_from_dict,
    trade_to_dict,
)
from src.setup.setup_engine import Direction, SetupType

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_UTC = datetime.timezone.utc
_T0  = datetime.datetime(2024, 3, 15, 10, 0, 0, tzinfo=_UTC)
_T1  = _T0 + datetime.timedelta(minutes=15)


def _make_trade(
    trade_id: str = "abc1234567890123",
    instrument: str = "BTCUSDT",
    direction: Direction = Direction.LONG,
    status: str = "OPEN",
) -> Trade:
    return Trade(
        trade_id=trade_id,
        candidate_id="cand001",
        instrument=instrument,
        direction=direction,
        setup_type=SetupType.PULLBACK_CONTINUATION,
        entry_price_ref=Decimal("100000"),
        entry_price_actual=Decimal("100050"),
        stop_price=Decimal("99500"),
        target_price=Decimal("103000"),
        stop_distance=Decimal("550"),
        expected_R=Decimal("5.454545"),
        actual_R_if_stopped=Decimal("5.36363"),
        position_size=Decimal("2"),
        risk_amount=Decimal("100"),
        account_equity=Decimal("10000"),
        zone_id="1H_zone001",
        opened_at=_T0,
        status=status,
        breakeven_triggered=False,
        current_stop=Decimal("99500"),
    )


def _make_bar(
    symbol: str = "BTCUSDT",
    timeframe: str = "15m",
    ts: datetime.datetime = _T0,
) -> AggregatedBar:
    return AggregatedBar(
        symbol=symbol,
        timestamp_start=ts,
        timestamp_end=ts + datetime.timedelta(minutes=15),
        timeframe=timeframe,
        open=Decimal("99900"),
        high=Decimal("100200"),
        low=Decimal("99800"),
        close=Decimal("100050"),
        volume=Decimal("123.456"),
        is_complete=True,
        is_reliable=True,
    )


# ---------------------------------------------------------------------------
# Layer A: Pure-logic helper tests (no DB, synchronous)
# ---------------------------------------------------------------------------

class TestDecimalSerialisation:
    """AC7/AC8: Decimal ↔ str round-trip, no float."""

    def test_decimal_to_str_preserves_precision(self) -> None:
        d = Decimal("100000.12345678")
        assert _decimal_to_str(d) == "100000.12345678"

    def test_str_to_decimal_restores_exact_value(self) -> None:
        s = "99500.00000001"
        assert _str_to_decimal(s) == Decimal("99500.00000001")

    def test_round_trip_identity(self) -> None:
        d = Decimal("1E-9")
        assert _str_to_decimal(_decimal_to_str(d)) == d

    def test_no_float_in_decimal_to_str(self) -> None:
        """_decimal_to_str must return str, never float."""
        result = _decimal_to_str(Decimal("3.14"))
        assert isinstance(result, str)
        assert "." in result

    def test_large_decimal(self) -> None:
        d = Decimal("9999999999.99999999")
        assert _str_to_decimal(_decimal_to_str(d)) == d


class TestDatetimeSerialisation:
    """Datetime ↔ ISO UTC string round-trip."""

    def test_dt_to_str_produces_iso_string(self) -> None:
        s = _dt_to_str(_T0)
        assert "2024-03-15" in s
        assert "10:00:00" in s

    def test_str_to_dt_restores_utc(self) -> None:
        dt = _str_to_dt("2024-03-15T10:00:00+00:00")
        assert dt.tzinfo is not None
        assert dt.year == 2024 and dt.month == 3 and dt.day == 15

    def test_naive_dt_gets_utc_on_serialise(self) -> None:
        naive = datetime.datetime(2024, 1, 1, 0, 0, 0)
        s = _dt_to_str(naive)
        restored = _str_to_dt(s)
        assert restored.tzinfo is not None

    def test_round_trip_preserves_timestamp(self) -> None:
        dt = _T0
        assert _str_to_dt(_dt_to_str(dt)) == dt


class TestCanonicalJson:
    """_canonical_json produces sorted, deterministic bytes."""

    def test_key_order_is_sorted(self) -> None:
        """Keys in canonical JSON must appear in sorted (ascending) order."""
        obj = {"z": "last", "a": "first", "m": "middle"}
        raw = _canonical_json(obj).decode()
        # Parse preserving insertion order via object_pairs_hook
        pairs  = json.loads(raw, object_pairs_hook=list)
        actual_keys = [k for k, _ in pairs]
        assert actual_keys == sorted(actual_keys)

    def test_same_dict_produces_same_bytes(self) -> None:
        obj = {"price": "100000", "side": "LONG", "qty": "2"}
        assert _canonical_json(obj) == _canonical_json(obj)

    def test_different_dicts_produce_different_bytes(self) -> None:
        a = {"x": "1"}
        b = {"x": "2"}
        assert _canonical_json(a) != _canonical_json(b)

    def test_returns_bytes(self) -> None:
        assert isinstance(_canonical_json({"k": "v"}), bytes)


class TestHashState:
    """_hash_state returns a 64-char lowercase hex sha256."""

    def test_hash_is_64_chars(self) -> None:
        h = _hash_state({"price": "100000"})
        assert len(h) == 64

    def test_hash_is_hex(self) -> None:
        h = _hash_state({"price": "100000"})
        int(h, 16)  # must not raise

    def test_same_state_same_hash(self) -> None:
        s = {"active_phase": "TREND_BULL", "score": "75"}
        assert _hash_state(s) == _hash_state(s)

    def test_mutated_state_different_hash(self) -> None:
        s1 = {"active_phase": "TREND_BULL"}
        s2 = {"active_phase": "TREND_BEAR"}
        assert _hash_state(s1) != _hash_state(s2)


class TestTradeSerialisation:
    """AC1/AC7: trade_to_dict ↔ trade_from_dict round-trip."""

    def test_round_trip_identity(self) -> None:
        trade = _make_trade()
        d     = trade_to_dict(trade)
        back  = trade_from_dict(d)
        assert back.trade_id           == trade.trade_id
        assert back.entry_price_actual == trade.entry_price_actual
        assert back.stop_price         == trade.stop_price
        assert back.direction          == trade.direction
        assert back.setup_type         == trade.setup_type

    def test_all_decimal_fields_are_str_in_dict(self) -> None:
        """AC8: No float anywhere in serialised dict."""
        d = trade_to_dict(_make_trade())
        decimal_keys = [
            "entry_price_ref", "entry_price_actual", "stop_price",
            "target_price", "stop_distance", "expected_R",
            "actual_R_if_stopped", "position_size", "risk_amount",
            "account_equity", "current_stop",
        ]
        for k in decimal_keys:
            assert isinstance(d[k], str), f"Field {k!r} is not str: {d[k]!r}"

    def test_restored_decimal_fields_are_decimal(self) -> None:
        """AC7: After round-trip, Decimal fields are Decimal, not float."""
        trade = _make_trade()
        back  = trade_from_dict(trade_to_dict(trade))
        assert isinstance(back.entry_price_actual, Decimal)
        assert isinstance(back.stop_price,         Decimal)
        assert isinstance(back.position_size,      Decimal)
        assert isinstance(back.expected_R,         Decimal)

    def test_opened_at_round_trip(self) -> None:
        trade = _make_trade()
        back  = trade_from_dict(trade_to_dict(trade))
        assert back.opened_at == trade.opened_at
        assert back.opened_at.tzinfo is not None

    def test_direction_enum_round_trip(self) -> None:
        trade = _make_trade(direction=Direction.SHORT)
        back  = trade_from_dict(trade_to_dict(trade))
        assert back.direction == Direction.SHORT

    def test_none_current_stop_round_trips(self) -> None:
        """current_stop=None must survive round-trip."""
        trade = Trade(
            trade_id="abc1234567890123",
            candidate_id="c01",
            instrument="BTCUSDT",
            direction=Direction.LONG,
            setup_type=SetupType.FAKEOUT,
            entry_price_ref=Decimal("100000"),
            entry_price_actual=Decimal("100000"),
            stop_price=Decimal("99500"),
            target_price=Decimal("103000"),
            stop_distance=Decimal("500"),
            expected_R=Decimal("6"),
            actual_R_if_stopped=Decimal("6"),
            position_size=Decimal("2"),
            risk_amount=Decimal("100"),
            account_equity=Decimal("10000"),
            zone_id="1H_z001",
            opened_at=_T0,
            status="OPEN",
            breakeven_triggered=False,
            current_stop=None,
        )
        back = trade_from_dict(trade_to_dict(trade))
        assert back.current_stop is None


class TestBarSerialisation:
    """bar_to_dict ↔ bar_from_dict round-trip."""

    def test_round_trip_identity(self) -> None:
        bar  = _make_bar()
        back = bar_from_dict(bar_to_dict(bar))
        assert back.symbol          == bar.symbol
        assert back.open            == bar.open
        assert back.close           == bar.close
        assert back.timeframe       == bar.timeframe
        assert back.timestamp_start == bar.timestamp_start

    def test_price_fields_are_str_in_dict(self) -> None:
        """AC8: All price fields serialised as str."""
        d = bar_to_dict(_make_bar())
        for key in ("open", "high", "low", "close", "volume"):
            assert isinstance(d[key], str), f"Field {key!r} not str: {d[key]!r}"

    def test_restored_prices_are_decimal(self) -> None:
        """AC7: After round-trip, price fields are Decimal."""
        bar  = _make_bar()
        back = bar_from_dict(bar_to_dict(bar))
        assert isinstance(back.open,  Decimal)
        assert isinstance(back.close, Decimal)
        assert isinstance(back.volume, Decimal)


# ---------------------------------------------------------------------------
# Layer B: StateManager with mocked asyncpg pool
# ---------------------------------------------------------------------------

def _mock_pool() -> MagicMock:
    """Build a MagicMock that behaves like an asyncpg connection pool."""
    pool   = MagicMock()
    conn   = AsyncMock()
    # pool.acquire() returns an async context manager yielding conn
    acquire_ctx = MagicMock()
    acquire_ctx.__aenter__ = AsyncMock(return_value=conn)
    acquire_ctx.__aexit__  = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=acquire_ctx)
    pool.close   = AsyncMock()
    return pool, conn


def _make_sm(pool: Any) -> StateManager:
    """Return a connected StateManager with the given mock pool."""
    sm = StateManager(dsn="postgresql://mock/test")
    sm._pool = pool
    return sm


# ---- helpers for running coroutines synchronously in tests -----------------

def _run(coro):
    """Run a coroutine to completion (compatible with any event loop state)."""
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# AC1: save_snapshot + load_snapshot round-trip
# ---------------------------------------------------------------------------

class TestSnapshotRoundTrip:
    """AC1: save_snapshot stores state; load_snapshot retrieves it."""

    def test_save_snapshot_executes_insert(self) -> None:
        """save_snapshot must call conn.execute with INSERT SQL."""
        pool, conn = _mock_pool()
        conn.execute = AsyncMock()
        sm = _make_sm(pool)

        state = {"active_phase": "TREND_BULL", "score": "75", "atr": "350.123456"}
        result = _run(sm.save_snapshot("BTCUSDT", "phase_engine", state, snapshot_at=_T0))

        assert result is True
        conn.execute.assert_called_once()
        call_args = conn.execute.call_args[0]
        # First arg is the SQL
        assert "INSERT INTO system_state" in call_args[0]
        # state_hash arg present (6th positional)
        assert len(call_args) >= 5

    def test_load_snapshot_returns_dict(self) -> None:
        """load_snapshot returns the stored state_json as a dict."""
        pool, conn = _mock_pool()
        state   = {"active_phase": "BALANCE", "score": "42"}
        state_hash = _hash_state(state)
        # fetchrow returns a mock row
        conn.fetchrow = AsyncMock(return_value={
            "state_json": state,
            "state_hash": state_hash,
        })
        sm = _make_sm(pool)

        result = _run(sm.load_snapshot("BTCUSDT", "phase_engine"))
        assert result == state

    def test_load_snapshot_none_when_not_found(self) -> None:
        """load_snapshot returns None when no row exists."""
        pool, conn = _mock_pool()
        conn.fetchrow = AsyncMock(return_value=None)
        sm = _make_sm(pool)

        result = _run(sm.load_snapshot("BTCUSDT", "phase_engine"))
        assert result is None

    def test_state_decimal_values_as_str(self) -> None:
        """AC1/AC8: Decimal values in state dict must be stored as str, not float."""
        pool, conn = _mock_pool()
        conn.execute = AsyncMock()
        sm = _make_sm(pool)

        # State with Decimal-derived strings
        state = {
            "current_atr": _decimal_to_str(Decimal("350.123456")),
            "prev_close":  _decimal_to_str(Decimal("100000")),
        }
        _run(sm.save_snapshot("BTCUSDT", "atr_15m", state))

        # Check the JSON string passed to execute contains no bare floats
        call_args = conn.execute.call_args[0]
        json_arg = call_args[4]   # state_json_str
        parsed   = json.loads(json_arg)
        for key, val in parsed.items():
            assert isinstance(val, str), f"Value for {key!r} is {type(val).__name__}, expected str"


# ---------------------------------------------------------------------------
# AC2: Hash mismatch → verify_snapshot returns False
# ---------------------------------------------------------------------------

class TestVerifySnapshot:
    """AC2: verify_snapshot compares recomputed hash with stored hash."""

    def test_matching_hash_returns_true(self) -> None:
        """State + its correct hash → True."""
        pool, conn = _mock_pool()
        state = {"phase": "TREND_BULL", "atr": "350"}
        good_hash = _hash_state(state)
        conn.fetchrow = AsyncMock(return_value={
            "state_json": state,
            "state_hash": good_hash,
        })
        sm = _make_sm(pool)
        result = _run(sm.verify_snapshot("BTCUSDT", "phase_engine"))
        assert result is True

    def test_mismatched_hash_returns_false(self) -> None:
        """AC2: State with wrong stored hash → False (mismatch)."""
        pool, conn = _mock_pool()
        state = {"phase": "TREND_BULL", "atr": "350"}
        wrong_hash = "a" * 64   # deliberately wrong
        conn.fetchrow = AsyncMock(return_value={
            "state_json": state,
            "state_hash": wrong_hash,
        })
        sm = _make_sm(pool)
        result = _run(sm.verify_snapshot("BTCUSDT", "phase_engine"))
        assert result is False

    def test_no_snapshot_returns_false(self) -> None:
        """No snapshot found → False (cannot verify absence of data)."""
        pool, conn = _mock_pool()
        conn.fetchrow = AsyncMock(return_value=None)
        sm = _make_sm(pool)
        result = _run(sm.verify_snapshot("BTCUSDT", "phase_engine"))
        assert result is False

    def test_tampered_state_returns_false(self) -> None:
        """State that was modified after save → recomputed hash differs → False."""
        state    = {"phase": "TREND_BULL", "atr": "350"}
        orig_hash = _hash_state(state)
        # Simulate tampered state (different value in DB)
        tampered = {"phase": "TREND_BEAR", "atr": "350"}   # phase changed
        pool, conn = _mock_pool()
        conn.fetchrow = AsyncMock(return_value={
            "state_json": tampered,
            "state_hash": orig_hash,   # hash is for original, not tampered
        })
        sm = _make_sm(pool)
        result = _run(sm.verify_snapshot("BTCUSDT", "phase_engine"))
        assert result is False


# ---------------------------------------------------------------------------
# AC3: save_bar + load_bars in ascending order
# ---------------------------------------------------------------------------

class TestBars:
    """AC3: Bars round-trip and are returned in ascending timestamp order."""

    def test_save_bar_executes_upsert(self) -> None:
        """save_bar calls conn.execute with INSERT ... ON CONFLICT."""
        pool, conn = _mock_pool()
        conn.execute = AsyncMock()
        sm = _make_sm(pool)
        bar = _make_bar()

        result = _run(sm.save_bar(bar, instrument="BTCUSDT"))
        assert result is True
        conn.execute.assert_called_once()
        sql = conn.execute.call_args[0][0]
        assert "INSERT INTO bars" in sql
        assert "ON CONFLICT" in sql

    def test_load_bars_returns_ascending_order(self) -> None:
        """AC3: load_bars rows are ordered ascending by timestamp_start."""
        pool, conn = _mock_pool()
        ts1 = _T0
        ts2 = _T0 + datetime.timedelta(minutes=15)
        ts3 = _T0 + datetime.timedelta(minutes=30)
        # DB returns them in correct ascending order
        conn.fetch = AsyncMock(return_value=[
            {"instrument": "BTCUSDT", "timeframe": "15m",
             "timestamp_start": ts1,
             "open": 99900, "high": 100200, "low": 99800, "close": 100050, "volume": 100},
            {"instrument": "BTCUSDT", "timeframe": "15m",
             "timestamp_start": ts2,
             "open": 100050, "high": 100300, "low": 99900, "close": 100150, "volume": 110},
            {"instrument": "BTCUSDT", "timeframe": "15m",
             "timestamp_start": ts3,
             "open": 100150, "high": 100400, "low": 100000, "close": 100200, "volume": 120},
        ])
        sm = _make_sm(pool)
        rows = _run(sm.load_bars("BTCUSDT", "15m"))

        assert len(rows) == 3
        # Timestamps must be in ascending order
        ts_list = [_str_to_dt(r["timestamp_start"]) for r in rows]
        assert ts_list == sorted(ts_list)

    def test_load_bars_price_fields_are_str(self) -> None:
        """AC8: All price fields in returned dicts are str, not float."""
        pool, conn = _mock_pool()
        conn.fetch = AsyncMock(return_value=[
            {"instrument": "BTCUSDT", "timeframe": "15m",
             "timestamp_start": _T0,
             "open": 99900, "high": 100200, "low": 99800, "close": 100050, "volume": 100},
        ])
        sm = _make_sm(pool)
        rows = _run(sm.load_bars("BTCUSDT", "15m"))
        row = rows[0]
        for field in ("open", "high", "low", "close", "volume"):
            assert isinstance(row[field], str), f"{field} is not str: {row[field]!r}"

    def test_load_bars_with_after_filter(self) -> None:
        """load_bars with after= uses different SQL branch."""
        pool, conn = _mock_pool()
        conn.fetch = AsyncMock(return_value=[])
        sm = _make_sm(pool)
        _run(sm.load_bars("BTCUSDT", "15m", after=_T0))
        conn.fetch.assert_called_once()
        sql = conn.fetch.call_args[0][0]
        assert "timestamp_start > $3" in sql


# ---------------------------------------------------------------------------
# AC4: meets_minimum_history
# ---------------------------------------------------------------------------

class TestMinimumHistory:
    """AC4: < 40 bars → False; >= 40 bars → True."""

    def test_below_minimum_returns_false(self) -> None:
        pool, conn = _mock_pool()
        conn.fetchrow = AsyncMock(return_value={"cnt": 39})
        sm = _make_sm(pool)
        result = _run(sm.meets_minimum_history("BTCUSDT", "15m"))
        assert result is False

    def test_exactly_minimum_returns_true(self) -> None:
        pool, conn = _mock_pool()
        conn.fetchrow = AsyncMock(return_value={"cnt": 40})
        sm = _make_sm(pool)
        result = _run(sm.meets_minimum_history("BTCUSDT", "15m"))
        assert result is True

    def test_above_minimum_returns_true(self) -> None:
        pool, conn = _mock_pool()
        conn.fetchrow = AsyncMock(return_value={"cnt": 200})
        sm = _make_sm(pool)
        result = _run(sm.meets_minimum_history("BTCUSDT", "1H"))
        assert result is True

    def test_zero_bars_returns_false(self) -> None:
        pool, conn = _mock_pool()
        conn.fetchrow = AsyncMock(return_value={"cnt": 0})
        sm = _make_sm(pool)
        result = _run(sm.meets_minimum_history("XAUUSD", "4H"))
        assert result is False

    def test_get_bar_count_returns_int(self) -> None:
        pool, conn = _mock_pool()
        conn.fetchrow = AsyncMock(return_value={"cnt": 57})
        sm = _make_sm(pool)
        count = _run(sm.get_bar_count("BTCUSDT", "1D"))
        assert count == 57
        assert isinstance(count, int)

    def test_minimum_is_40_for_all_known_timeframes(self) -> None:
        """Amendment E3: Every known TF has minimum of 40."""
        from src.persistence.state_manager import _MIN_BARS_PER_TF
        for tf, minimum in _MIN_BARS_PER_TF.items():
            assert minimum == 40, f"TF {tf!r} has minimum {minimum}, expected 40"


# ---------------------------------------------------------------------------
# AC5: save_trade + load_open_trades
# ---------------------------------------------------------------------------

class TestTrades:
    """AC5: Only OPEN trades returned by load_open_trades."""

    def test_save_trade_executes_insert(self) -> None:
        """save_trade calls conn.execute with INSERT SQL."""
        pool, conn = _mock_pool()
        conn.execute = AsyncMock()
        sm = _make_sm(pool)
        trade = _make_trade()

        result = _run(sm.save_trade(trade))
        assert result is True
        conn.execute.assert_called_once()
        sql = conn.execute.call_args[0][0]
        assert "INSERT INTO trades" in sql

    def test_load_open_trades_filters_by_status(self) -> None:
        """AC5: SQL must filter WHERE status = 'OPEN'."""
        pool, conn = _mock_pool()
        conn.fetch = AsyncMock(return_value=[])
        sm = _make_sm(pool)
        _run(sm.load_open_trades())
        sql = conn.fetch.call_args[0][0]
        assert "status = 'OPEN'" in sql

    def test_load_open_trades_returns_dicts(self) -> None:
        """load_open_trades returns list of dicts with price fields as str."""
        pool, conn = _mock_pool()
        conn.fetch = AsyncMock(return_value=[
            {
                "trade_id":      "abc1234567890123",
                "instrument":    "BTCUSDT",
                "direction":     "LONG",
                "entry_price":   100050,
                "stop_price":    99500,
                "target_price":  103000,
                "position_size": 2,
                "setup_type":    "PULLBACK_CONTINUATION",
                "opened_at":     _T0,
                "status":        "OPEN",
            }
        ])
        sm = _make_sm(pool)
        result = _run(sm.load_open_trades())
        assert len(result) == 1
        row = result[0]
        assert row["status"] == "OPEN"
        # Price fields must be str
        for field in ("entry_price", "stop_price", "target_price", "position_size"):
            assert isinstance(row[field], str), f"{field} not str: {row[field]!r}"

    def test_load_open_trades_empty_when_none_open(self) -> None:
        pool, conn = _mock_pool()
        conn.fetch = AsyncMock(return_value=[])
        sm = _make_sm(pool)
        result = _run(sm.load_open_trades())
        assert result == []


# ---------------------------------------------------------------------------
# AC6: update_trade sets closed_at and pnl
# ---------------------------------------------------------------------------

class TestUpdateTrade:
    """AC6: update_trade stores closed_at and pnl via UPDATE SQL."""

    def test_update_trade_passes_closed_at_and_pnl(self) -> None:
        """AC6: The UPDATE must include closed_at and pnl values."""
        pool, conn = _mock_pool()
        conn.execute = AsyncMock(return_value="UPDATE 1")
        sm = _make_sm(pool)

        closed_at = _T1
        pnl       = Decimal("350.75")

        result = _run(sm.update_trade(
            trade_id="abc1234567890123",
            status="CLOSED",
            closed_at=closed_at,
            pnl=pnl,
        ))
        assert result is True
        conn.execute.assert_called_once()
        call_args = conn.execute.call_args[0]
        # SQL should be UPDATE trades SET ...
        assert "UPDATE trades" in call_args[0]
        # closed_at passed as 2nd positional arg
        assert call_args[2] == closed_at
        # pnl passed as 3rd positional arg — converted to float for asyncpg NUMERIC
        assert call_args[3] == float(pnl)

    def test_update_trade_returns_false_when_not_found(self) -> None:
        """update_trade returns False when trade_id doesn't exist (UPDATE 0)."""
        pool, conn = _mock_pool()
        conn.execute = AsyncMock(return_value="UPDATE 0")
        sm = _make_sm(pool)
        result = _run(sm.update_trade("nonexistent", "CLOSED"))
        assert result is False

    def test_update_trade_none_pnl_allowed(self) -> None:
        """update_trade with pnl=None passes None to DB (not a float)."""
        pool, conn = _mock_pool()
        conn.execute = AsyncMock(return_value="UPDATE 1")
        sm = _make_sm(pool)
        _run(sm.update_trade("abc1234567890123", "CLOSED", closed_at=_T1, pnl=None))
        args = conn.execute.call_args[0]
        assert args[3] is None   # pnl=None passed through unchanged


# ---------------------------------------------------------------------------
# AC7/AC8: No float in state dicts, trade serialisation
# ---------------------------------------------------------------------------

class TestNoFloat:
    """AC7/AC8: Decimal survives round-trip as Decimal, never becomes float."""

    def test_trade_round_trip_decimal_fields(self) -> None:
        trade = _make_trade()
        d     = trade_to_dict(trade)
        back  = trade_from_dict(d)
        for field_name, orig_val in [
            ("entry_price_actual", trade.entry_price_actual),
            ("stop_price",         trade.stop_price),
            ("position_size",      trade.position_size),
            ("expected_R",         trade.expected_R),
            ("risk_amount",        trade.risk_amount),
        ]:
            restored = getattr(back, field_name)
            assert isinstance(restored, Decimal), f"{field_name} should be Decimal"
            assert restored == orig_val, f"{field_name} value mismatch"

    def test_no_float_in_serialised_trade_dict(self) -> None:
        d = trade_to_dict(_make_trade())
        numeric_keys = [
            "entry_price_ref", "entry_price_actual", "stop_price",
            "target_price", "stop_distance", "expected_R",
            "actual_R_if_stopped", "position_size", "risk_amount",
            "account_equity", "current_stop",
        ]
        for k in numeric_keys:
            if d.get(k) is not None:
                assert isinstance(d[k], str), f"Field {k!r} is {type(d[k]).__name__}"

    def test_no_float_in_serialised_bar_dict(self) -> None:
        d = bar_to_dict(_make_bar())
        for field in ("open", "high", "low", "close", "volume"):
            assert isinstance(d[field], str)

    def test_state_dict_values_preserved_as_str(self) -> None:
        """Generic state dict: Decimal-derived strings stay str through hash."""
        state = {
            "atr":    _decimal_to_str(Decimal("350.123456")),
            "equity": _decimal_to_str(Decimal("100000")),
        }
        h = _hash_state(state)
        # Verify round-trip: canonical JSON preserves str type
        parsed = json.loads(_canonical_json(state).decode())
        for k, v in parsed.items():
            assert isinstance(v, str), f"{k!r} is {type(v).__name__}"


# ---------------------------------------------------------------------------
# Additional: requires_pool guard
# ---------------------------------------------------------------------------

class TestRequiresPool:
    """_require_pool raises RuntimeError when connect() not called."""

    def test_save_snapshot_without_connect_raises(self) -> None:
        sm = StateManager(dsn="postgresql://mock/test")
        with pytest.raises(RuntimeError, match="not connected"):
            _run(sm.save_snapshot("BTCUSDT", "test", {}))

    def test_load_snapshot_without_connect_raises(self) -> None:
        sm = StateManager(dsn="postgresql://mock/test")
        with pytest.raises(RuntimeError, match="not connected"):
            _run(sm.load_snapshot("BTCUSDT", "test"))
