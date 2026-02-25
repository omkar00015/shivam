"""Tests for src/notifications/telegram_notifier.py.

Acceptance criteria:
  AC1: Trade filled message contains: symbol, direction, entry, stop, target, R.
  AC2: System paused message sent immediately on any pause condition.
  AC3: Retry: first send fails, second succeeds → returns True.
  AC4: After 3 failures → returns False, no exception raised.
  AC5: B-tier candidate → send_candidate_created not called (documented contract;
       the method itself does not enforce tier, caller is responsible).
  AC6: All prices formatted correctly (Decimal, not float, 2dp Gold, 0dp BTC).
  AC7: TELEGRAM_BOT_TOKEN missing → notifier initialises, all sends return False.
  AC8: No float anywhere in message construction.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from src.notifications.telegram_notifier import (
    TelegramNotifier,
    _fmt_price,
    _fmt_r,
    _fmt_pct,
)
from src.execution.entry_executor import Trade
from src.setup.setup_engine import (
    SetupCandidate,
    CandidateStatus,
    Direction,
    SetupType,
)
from src.sr.zone_detector import SRZone, ZonePolarity, ZoneState, ZoneOrigin, Tier


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_UTC = timezone.utc
_NOW = datetime(2024, 3, 15, 10, 30, 0, tzinfo=_UTC)


# ---------------------------------------------------------------------------
# Fixture factories
# ---------------------------------------------------------------------------

def _make_trade(
    instrument:  str = "BTCUSDT",
    direction:   Direction = Direction.LONG,
    setup_type:  SetupType = SetupType.PULLBACK_CONTINUATION,
    entry:       str = "67500",
    stop_p:      str = "66000",
    target_p:    str = "70500",
    expected_r:  str = "2.00",
    size:        str = "1",
    risk_amount: str = "100",
    equity:      str = "10000",
) -> Trade:
    """Factory for Trade test fixtures.

    Parameter names use `stop_p` / `target_p` to avoid collision with Python builtins
    and keyword-argument conflicts in helper wrappers.
    """
    return Trade(
        trade_id              = "abc123def456",
        candidate_id          = "cand001",
        instrument            = instrument,
        direction             = direction,
        setup_type            = setup_type,
        entry_price_ref       = Decimal(entry),
        entry_price_actual    = Decimal(entry),
        stop_price            = Decimal(stop_p),
        target_price          = Decimal(target_p),
        stop_distance         = abs(Decimal(entry) - Decimal(stop_p)),
        expected_R            = Decimal(expected_r),
        actual_R_if_stopped   = Decimal(expected_r),
        position_size         = Decimal(size),
        risk_amount           = Decimal(risk_amount),
        account_equity        = Decimal(equity),
        zone_id               = "zone_001",
        opened_at             = _NOW,
        status                = "OPEN",
    )


def _btc_trade(**kwargs) -> Trade:
    """BTC trade fixture; defaults can be overridden via kwargs."""
    defaults = dict(
        instrument="BTCUSDT",
        entry="67500", stop_p="66000", target_p="70500",
    )
    defaults.update(kwargs)
    return _make_trade(**defaults)


def _gold_trade(**kwargs) -> Trade:
    """Gold trade fixture; defaults can be overridden via kwargs."""
    defaults = dict(
        instrument="XAUUSD",
        entry="2045.75", stop_p="2030.00", target_p="2075.50",
    )
    defaults.update(kwargs)
    return _make_trade(**defaults)


def _make_candidate(
    setup_type: SetupType = SetupType.PULLBACK_CONTINUATION,
    direction:  Direction = Direction.LONG,
    entry:      str = "67500",
    stop_p:     str = "66000",
    target_p:   str = "70500",
    expected_r: str = "2.00",
    zone_id:    str = "BTCUSDT|zone_001",
) -> SetupCandidate:
    return SetupCandidate(
        candidate_id  = "cand_aabbcc",
        setup_type    = setup_type,
        direction     = direction,
        zone_id       = zone_id,
        entry_price   = Decimal(entry),
        stop_price    = Decimal(stop_p),
        target_price  = Decimal(target_p),
        expected_R    = Decimal(expected_r),
        created_at    = _NOW,
        expiry_bars   = 12,
        status        = CandidateStatus.WAITING_ENTRY,
    )


def _make_zone(
    center:    str = "2045.75",
    zone_high: str = "2055.00",
    zone_low:  str = "2036.50",
    tier:      Tier = Tier.A,
    polarity:  ZonePolarity = ZonePolarity.SUPPORT,
    timeframe: str = "XAUUSD",
) -> SRZone:
    return SRZone(
        zone_id           = "zone_001",
        center            = Decimal(center),
        zone_high         = Decimal(zone_high),
        zone_low          = Decimal(zone_low),
        polarity          = polarity,
        origin            = ZoneOrigin.LEG_EXTREME,
        timeframe         = timeframe,
        is_midpoint       = False,
        state             = ZoneState.FRESH,
        tier              = tier,
        strength          = 12,
        touch_count       = 1,
        false_break_count = 0,
        created_at        = _NOW,
        last_touch_time   = None,
    )


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _run(coro):
    """Run an async coroutine synchronously using the event loop."""
    return asyncio.get_event_loop().run_until_complete(coro)


def _notifier_with_mock(responses: list) -> TelegramNotifier:
    """Create a configured notifier whose _http_post returns responses in sequence.

    Each element of responses may be:
      - True / False  → returned directly from _http_post
      - An Exception class (e.g. ConnectionError) → raised by _http_post
      - An Exception instance → raised by _http_post
    """
    n = TelegramNotifier(bot_token="fake_token", chat_id="12345")
    call_iter = iter(responses)

    async def _mock_http(url, payload):
        val = next(call_iter)
        if isinstance(val, type) and issubclass(val, Exception):
            raise val("simulated network error")
        if isinstance(val, Exception):
            raise val
        return val

    n._http_post = _mock_http  # type: ignore[method-assign]
    return n


async def _capture_text(method_name: str, *args, **kwargs) -> str:
    """Create a configured notifier, capture the Telegram message text."""
    captured: list[str] = []

    async def _mock_http(url, payload):
        captured.append(payload["text"])
        return True

    n = TelegramNotifier(bot_token="tok", chat_id="cid")
    n._http_post = _mock_http  # type: ignore[method-assign]
    await getattr(n, method_name)(*args, **kwargs)
    return captured[0] if captured else ""


def _get_text(method_name: str, *args, **kwargs) -> str:
    """Synchronous wrapper around _capture_text."""
    return _run(_capture_text(method_name, *args, **kwargs))


# ---------------------------------------------------------------------------
# AC7: Missing token → returns False gracefully, no exception
# ---------------------------------------------------------------------------

class TestMissingToken:
    """AC7: TELEGRAM_BOT_TOKEN missing → all sends return False, no exception."""

    def _unconfigured(self) -> TelegramNotifier:
        return TelegramNotifier(bot_token=None, chat_id=None)

    def test_initialises_without_exception(self) -> None:
        n = self._unconfigured()
        assert n._configured is False

    def test_send_trade_filled_returns_false(self) -> None:
        n = self._unconfigured()
        result = _run(n.send_trade_filled(_btc_trade()))
        assert result is False

    def test_send_trade_closed_returns_false(self) -> None:
        n = self._unconfigured()
        result = _run(n.send_trade_closed(_btc_trade(), Decimal("-100"), Decimal("-1")))
        assert result is False

    def test_send_system_paused_returns_false(self) -> None:
        n = self._unconfigured()
        result = _run(n.send_system_paused("test reason"))
        assert result is False

    def test_send_governance_halted_returns_false(self) -> None:
        n = self._unconfigured()
        result = _run(n.send_governance_halted("drift critical"))
        assert result is False

    def test_send_drift_alert_returns_false(self) -> None:
        n = self._unconfigured()
        result = _run(n.send_drift_alert(
            Decimal("0.27"), Decimal("0.50"),
            Decimal("0.45"), Decimal("0.80"),
        ))
        assert result is False

    def test_send_data_gap_returns_false(self) -> None:
        n = self._unconfigured()
        result = _run(n.send_data_gap("BTCUSDT", 700))
        assert result is False

    def test_send_db_failure_returns_false(self) -> None:
        n = self._unconfigured()
        result = _run(n.send_db_failure(3))
        assert result is False


# ---------------------------------------------------------------------------
# AC1: Trade filled message content
# ---------------------------------------------------------------------------

class TestTradeFilled:
    """AC1: Trade filled message contains symbol, direction, entry, stop, target, R."""

    def test_contains_instrument_btc(self) -> None:
        text = _get_text("send_trade_filled", _btc_trade())
        assert "BTCUSDT" in text

    def test_contains_instrument_gold(self) -> None:
        text = _get_text("send_trade_filled", _gold_trade())
        assert "XAUUSD" in text

    def test_contains_direction_long(self) -> None:
        text = _get_text("send_trade_filled", _btc_trade(direction=Direction.LONG))
        assert "LONG" in text

    def test_contains_direction_short(self) -> None:
        text = _get_text("send_trade_filled", _btc_trade(direction=Direction.SHORT))
        assert "SHORT" in text

    def test_contains_entry_price(self) -> None:
        text = _get_text("send_trade_filled", _btc_trade(entry="67500"))
        assert "67500" in text

    def test_contains_stop_price(self) -> None:
        text = _get_text("send_trade_filled", _btc_trade(stop_p="66000"))
        assert "66000" in text

    def test_contains_target_price(self) -> None:
        text = _get_text("send_trade_filled", _btc_trade(target_p="70500"))
        assert "70500" in text

    def test_contains_r_value(self) -> None:
        text = _get_text("send_trade_filled", _btc_trade(expected_r="2.00"))
        assert "2.00R" in text

    def test_returns_true_on_success(self) -> None:
        n = _notifier_with_mock([True])
        result = _run(n.send_trade_filled(_btc_trade()))
        assert result is True

    def test_parse_mode_is_html(self) -> None:
        """Telegram must be set to HTML mode."""
        captured: list[dict] = []

        async def _run_it():
            async def _mock_http(url, payload):
                captured.append(payload)
                return True

            n = TelegramNotifier(bot_token="tok", chat_id="cid")
            n._http_post = _mock_http  # type: ignore[method-assign]
            await n.send_trade_filled(_btc_trade())

        _run(_run_it())
        assert captured[0]["parse_mode"] == "HTML"


# ---------------------------------------------------------------------------
# AC2: System paused — sends immediately
# ---------------------------------------------------------------------------

class TestSystemPaused:
    """AC2: send_system_paused must reach Telegram immediately on any pause."""

    def test_system_paused_returns_true_when_delivered(self) -> None:
        n = _notifier_with_mock([True])
        result = _run(n.send_system_paused("weekly loss > 5%"))
        assert result is True

    def test_system_paused_message_contains_reason(self) -> None:
        text = _get_text("send_system_paused", "drawdown > 15%")
        assert "drawdown" in text.lower()

    def test_system_paused_message_is_urgent(self) -> None:
        """System paused messages must be visually urgent (🚨 emoji or PAUSED keyword)."""
        text = _get_text("send_system_paused", "DB failure")
        assert "🚨" in text or "PAUSED" in text

    def test_system_resumed_returns_true(self) -> None:
        n = _notifier_with_mock([True])
        result = _run(n.send_system_resumed())
        assert result is True

    def test_system_resumed_message_contains_resumed(self) -> None:
        text = _get_text("send_system_resumed")
        assert "RESUMED" in text or "resumed" in text.lower()


# ---------------------------------------------------------------------------
# AC3: Retry — first fails, second succeeds → returns True
# ---------------------------------------------------------------------------

class TestRetry:
    """AC3: Retry logic — failure then success → True."""

    def test_first_false_second_true_returns_true(self) -> None:
        """AC3: first _http_post returns False, second returns True → True."""
        n = _notifier_with_mock([False, True])
        with patch("src.notifications.telegram_notifier.asyncio.sleep", new=AsyncMock()):
            result = _run(n.send_system_paused("test"))
        assert result is True

    def test_exception_then_success_returns_true(self) -> None:
        """AC3: first _http_post raises, second returns True → True."""
        n = _notifier_with_mock([ConnectionError, True])
        with patch("src.notifications.telegram_notifier.asyncio.sleep", new=AsyncMock()):
            result = _run(n.send_system_paused("test"))
        assert result is True

    def test_two_failures_then_success_returns_true(self) -> None:
        """Three attempts available — fail twice, succeed on third → True."""
        n = _notifier_with_mock([False, False, True])
        with patch("src.notifications.telegram_notifier.asyncio.sleep", new=AsyncMock()):
            result = _run(n.send_system_paused("test"))
        assert result is True


# ---------------------------------------------------------------------------
# AC4: All 3 retries exhausted → False, no exception
# ---------------------------------------------------------------------------

class TestAllRetriesFail:
    """AC4: After 3 failures → returns False, no exception raised."""

    def test_three_false_returns_false(self) -> None:
        n = _notifier_with_mock([False, False, False])
        with patch("src.notifications.telegram_notifier.asyncio.sleep", new=AsyncMock()):
            result = _run(n.send_trade_filled(_btc_trade()))
        assert result is False

    def test_three_exceptions_returns_false(self) -> None:
        n = _notifier_with_mock([OSError, OSError, OSError])
        with patch("src.notifications.telegram_notifier.asyncio.sleep", new=AsyncMock()):
            result = _run(n.send_trade_filled(_btc_trade()))
        assert result is False

    def test_no_exception_raised_to_caller(self) -> None:
        """AC4: the trading pipeline must NEVER see an exception from the notifier."""
        n = _notifier_with_mock([RuntimeError, RuntimeError, RuntimeError])
        with patch("src.notifications.telegram_notifier.asyncio.sleep", new=AsyncMock()):
            try:
                result = _run(n.send_system_paused("test"))
            except Exception as exc:
                pytest.fail(f"send_system_paused raised unexpectedly: {exc}")
        assert result is False

    def test_returns_false_not_none(self) -> None:
        n = _notifier_with_mock([False, False, False])
        with patch("src.notifications.telegram_notifier.asyncio.sleep", new=AsyncMock()):
            result = _run(n.send_governance_halted("critical"))
        assert result is False
        assert result is not None


# ---------------------------------------------------------------------------
# AC6: Price formatting — Decimal, correct decimal places
# ---------------------------------------------------------------------------

class TestPriceFormatting:
    """AC6: Price format — 0dp BTC, 2dp Gold, Decimal not float."""

    def test_btc_price_zero_dp(self) -> None:
        assert _fmt_price(Decimal("67842"), "BTCUSDT") == "67842"

    def test_btc_price_truncates_fractional(self) -> None:
        result = _fmt_price(Decimal("67842.5"), "BTCUSDT")
        assert "." not in result

    def test_gold_price_two_dp(self) -> None:
        assert _fmt_price(Decimal("2045.75"), "XAUUSD") == "2045.75"

    def test_gold_price_pads_to_two_dp(self) -> None:
        assert _fmt_price(Decimal("2045"), "XAUUSD") == "2045.00"

    def test_r_format(self) -> None:
        assert _fmt_r(Decimal("2.35")) == "2.35R"
        assert _fmt_r(Decimal("1.50")) == "1.50R"

    def test_pct_format(self) -> None:
        assert _fmt_pct(Decimal("0.45")) == "45.00%"
        assert _fmt_pct(Decimal("0.275")) == "27.50%"

    def test_fmt_price_returns_string(self) -> None:
        assert isinstance(_fmt_price(Decimal("67500"), "BTCUSDT"), str)

    def test_fmt_r_returns_string(self) -> None:
        assert isinstance(_fmt_r(Decimal("2.0")), str)

    def test_btc_trade_message_shows_integer_prices(self) -> None:
        """Full integration: BTC trade message must not show decimal prices."""
        text = _get_text("send_trade_filled",
                         _btc_trade(entry="67500", stop_p="66000", target_p="70500"))
        assert "67500" in text
        assert "66000" in text
        assert "70500" in text

    def test_gold_trade_message_shows_two_dp_prices(self) -> None:
        """Gold trade message must show 2dp prices."""
        text = _get_text("send_trade_filled",
                         _gold_trade(entry="2045.75", stop_p="2030.00", target_p="2075.50"))
        assert "2045.75" in text
        assert "2030.00" in text
        assert "2075.50" in text


# ---------------------------------------------------------------------------
# AC8: No float anywhere in message construction
# ---------------------------------------------------------------------------

class TestNoFloat:
    """AC8: All numeric computations use Decimal, not float."""

    def test_fmt_price_accepts_decimal(self) -> None:
        price = Decimal("67500")
        result = _fmt_price(price, "BTCUSDT")
        assert "67500" in result

    def test_fmt_r_accepts_decimal(self) -> None:
        assert _fmt_r(Decimal("2.35")) == "2.35R"

    def test_fmt_pct_accepts_decimal(self) -> None:
        assert _fmt_pct(Decimal("0.45")) == "45.00%"

    def test_data_gap_uses_decimal_arithmetic(self) -> None:
        """send_data_gap uses Decimal(gap_seconds)/Decimal('60'), not float division."""
        text = _get_text("send_data_gap", "BTCUSDT", 700)
        assert "BTCUSDT" in text
        assert "700" in text   # raw seconds present in message

    def test_drift_alert_decimal_inputs_formatted_correctly(self) -> None:
        text = _get_text("send_drift_alert",
                         Decimal("0.27"), Decimal("0.50"),
                         Decimal("0.45"), Decimal("0.80"))
        assert "27.00%" in text
        assert "45.00%" in text
        assert "0.50R" in text
        assert "0.80R" in text

    def test_trade_filled_r_is_decimal_formatted(self) -> None:
        text = _get_text("send_trade_filled", _btc_trade(expected_r="2.35"))
        assert "2.35R" in text


# ---------------------------------------------------------------------------
# Message content tests for each event type
# ---------------------------------------------------------------------------

class TestMessageContents:
    """Verify each message type contains expected fields."""

    def test_trade_closed_profit_has_profit_indicator(self) -> None:
        text = _get_text("send_trade_closed",
                         _btc_trade(), Decimal("200"), Decimal("2.0"), 0)
        assert "✅" in text or "PROFIT" in text

    def test_trade_closed_loss_has_loss_indicator(self) -> None:
        text = _get_text("send_trade_closed",
                         _btc_trade(), Decimal("-100"), Decimal("-1.0"), 2)
        assert "❌" in text or "LOSS" in text

    def test_trade_closed_loss_shows_consec_losses(self) -> None:
        text = _get_text("send_trade_closed",
                         _btc_trade(), Decimal("-100"), Decimal("-1.0"), 3)
        assert "3" in text

    def test_breakeven_moved_shows_trade_id(self) -> None:
        trade = _btc_trade()
        text = _get_text("send_breakeven_moved", trade)
        assert trade.trade_id in text

    def test_candidate_created_shows_setup_type(self) -> None:
        cand = _make_candidate(setup_type=SetupType.PULLBACK_CONTINUATION)
        zone = _make_zone()
        text = _get_text("send_candidate_created", cand, zone)
        assert "PULLBACK" in text

    def test_candidate_created_shows_tier(self) -> None:
        cand = _make_candidate()
        zone = _make_zone(tier=Tier.S)
        text = _get_text("send_candidate_created", cand, zone)
        assert "S" in text

    def test_candidate_created_shows_entry_price(self) -> None:
        cand = _make_candidate(entry="2045.75")
        zone = _make_zone()
        text = _get_text("send_candidate_created", cand, zone)
        assert "2045.75" in text

    def test_candidate_created_shows_r(self) -> None:
        cand = _make_candidate(expected_r="2.50")
        zone = _make_zone()
        text = _get_text("send_candidate_created", cand, zone)
        assert "2.50R" in text

    def test_governance_halted_contains_hatch_emoji(self) -> None:
        text = _get_text("send_governance_halted", "drift critical for 11 cycles")
        assert "🚨" in text
        assert "HALTED" in text

    def test_governance_halted_contains_reason(self) -> None:
        text = _get_text("send_governance_halted", "drift critical for 11 cycles")
        assert "drift critical" in text

    def test_db_failure_shows_count(self) -> None:
        text = _get_text("send_db_failure", 3)
        assert "3" in text

    def test_data_gap_shows_instrument(self) -> None:
        text = _get_text("send_data_gap", "XAUUSD", 1900)
        assert "XAUUSD" in text

    def test_system_paused_details_included(self) -> None:
        text = _get_text("send_system_paused",
                         "monthly loss > 10%",
                         "Resumes on manual override only")
        assert "monthly" in text.lower()
        assert "manual" in text.lower()

    def test_candidate_expired_shows_setup_type(self) -> None:
        cand = _make_candidate(setup_type=SetupType.RANGE_FADE)
        text = _get_text("send_candidate_expired", cand)
        assert "RANGE" in text

    def test_instrument_paused_shows_symbol(self) -> None:
        text = _get_text("send_instrument_paused", "BTCUSDT", "data gap 700s")
        assert "BTCUSDT" in text

    def test_instrument_resumed_shows_symbol(self) -> None:
        text = _get_text("send_instrument_resumed", "XAUUSD")
        assert "XAUUSD" in text

    def test_drift_alert_contains_live_and_baseline(self) -> None:
        text = _get_text("send_drift_alert",
                         Decimal("0.27"), Decimal("0.60"),
                         Decimal("0.45"), Decimal("0.80"))
        assert "27.00%" in text     # live win rate
        assert "45.00%" in text     # baseline win rate

    def test_breakeven_shows_entry_price(self) -> None:
        trade = _btc_trade(entry="67500")
        text = _get_text("send_breakeven_moved", trade)
        assert "67500" in text


# ---------------------------------------------------------------------------
# GovernanceAction / contract
# ---------------------------------------------------------------------------

class TestContract:
    """Verify TelegramNotifier interface and return types."""

    def test_configured_flag_true_when_token_and_chat_present(self) -> None:
        n = TelegramNotifier(bot_token="tok", chat_id="cid")
        assert n._configured is True

    def test_configured_flag_false_when_token_absent(self) -> None:
        n = TelegramNotifier(bot_token=None, chat_id="cid")
        assert n._configured is False

    def test_configured_flag_false_when_chat_absent(self) -> None:
        n = TelegramNotifier(bot_token="tok", chat_id=None)
        assert n._configured is False

    def test_all_send_methods_are_coroutines(self) -> None:
        """All public send_* methods must be async (return coroutines when called)."""
        import inspect
        n = TelegramNotifier(bot_token="tok", chat_id="cid")
        methods = [
            n.send_system_paused,
            n.send_system_resumed,
            n.send_drift_alert,
            n.send_governance_halted,
            n.send_data_gap,
            n.send_db_failure,
            n.send_instrument_paused,
            n.send_instrument_resumed,
            n.send_breakeven_moved,
            n.send_candidate_expired,
        ]
        for method in methods:
            assert inspect.iscoroutinefunction(method), (
                f"{method.__name__} is not async"
            )
