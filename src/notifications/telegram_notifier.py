"""Doc 12 (Monitoring) + Doc 1 §4 (no float):
TelegramNotifier — async fire-and-forget notifications for live trading.

All notifications are non-blocking: failures are logged and return False,
never raising exceptions into the trading pipeline.

Retry policy (AC4):
  - Up to 3 attempts per send.
  - 2-second exponential-back-off between attempts.
  - After 3 failures: log error, return False, do NOT raise.

Price formatting rules (AC6):
  - Gold (XAUUSD): 2 decimal places  e.g. "2045.75"
  - BTC  (BTCUSDT): 0 decimal places e.g. "67842"
  - All values use Decimal arithmetic — no float.

Configuration (from environment):
  TELEGRAM_BOT_TOKEN  — if absent, notifier initialises but logs a warning
                        and all sends return False gracefully (AC7).
  TELEGRAM_CHAT_ID    — required alongside the token.

B-tier candidate filtering (AC5):
  send_candidate_created() MUST only be called for S/A tier zones.
  This responsibility lies with the caller (orchestrator / setup engine).
  The method documents this contract but does not enforce it here.

HTML formatting: Telegram HTML mode (not markdown).
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

# aiohttp is the sole external dependency for HTTP transport.
# Imported lazily to allow test-time mocking.
try:
    import aiohttp
    _AIOHTTP_AVAILABLE = True
except ImportError:  # pragma: no cover
    _AIOHTTP_AVAILABLE = False

from src.execution.entry_executor import Trade
from src.setup.setup_engine import SetupCandidate
from src.sr.zone_detector import SRZone

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal constants
# ---------------------------------------------------------------------------
_TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}/sendMessage"
_MAX_RETRIES       = 3
_RETRY_DELAY_S     = 2.0          # seconds between retries
_EPSILON           = Decimal("1E-9")
_D_ZERO            = Decimal("0")

# Symbols whose prices are rendered at 0 dp (integer part only — BTC)
_ZERO_DP_INSTRUMENTS: frozenset[str] = frozenset({"BTCUSDT", "BTC"})
# Symbols rendered at 2 dp (Gold, Forex)
_TWO_DP_INSTRUMENTS:  frozenset[str] = frozenset({"XAUUSD", "GOLD", "EURUSD", "GBPUSD"})


# ---------------------------------------------------------------------------
# Helpers — price formatting (no float)
# ---------------------------------------------------------------------------

def _fmt_price(price: Decimal, instrument: str) -> str:
    """Format a Decimal price according to instrument convention.

    BTC/crypto → 0 decimal places (e.g. "67842").
    Gold/Forex → 2 decimal places (e.g. "2045.75").
    Unknown    → 4 decimal places as a safe default.

    AC6: All arithmetic uses Decimal. No float involved.
    """
    inst_upper = instrument.upper()
    if any(sym in inst_upper for sym in ("BTC", "ETH", "XRP")):
        # Crypto: integer display
        return str(price.quantize(Decimal("1")))
    if any(sym in inst_upper for sym in ("XAU", "GOLD", "EUR", "GBP", "AUD", "NZD")):
        return str(price.quantize(Decimal("0.01")))
    # Default: 4 dp
    return str(price.quantize(Decimal("0.0001")))


def _fmt_r(r_value: Decimal) -> str:
    """Format an R-multiple to 2 decimal places, e.g. '2.35R'."""
    return f"{r_value.quantize(Decimal('0.01'))}R"


def _fmt_pct(value: Decimal) -> str:
    """Format a ratio as percentage string, e.g. '45.00%'."""
    return f"{(value * 100).quantize(Decimal('0.01'))}%"


# ---------------------------------------------------------------------------
# TelegramNotifier
# ---------------------------------------------------------------------------

class TelegramNotifier:
    """Async Telegram notification client.

    All public methods are async and return bool (True = delivered, False = failed).
    They NEVER raise exceptions — all errors are logged and swallowed.

    Usage:
        notifier = TelegramNotifier()          # reads env vars at __init__
        await notifier.send_trade_filled(trade)

    AC7: If TELEGRAM_BOT_TOKEN is absent, __init__ logs a warning and every
         send_*() method returns False immediately without making HTTP calls.
    """

    def __init__(
        self,
        bot_token:   Optional[str] = None,
        chat_id:     Optional[str] = None,
    ) -> None:
        """Initialise notifier.  Reads TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID
        from environment if not supplied as arguments (AC7).
        """
        self._token:   Optional[str] = bot_token   or os.environ.get("TELEGRAM_BOT_TOKEN")
        self._chat_id: Optional[str] = chat_id     or os.environ.get("TELEGRAM_CHAT_ID")

        self._configured: bool = bool(self._token and self._chat_id)

        if not self._token:
            logger.warning(
                "TelegramNotifier: TELEGRAM_BOT_TOKEN not set — "
                "all notifications will be silently skipped."
            )
        elif not self._chat_id:
            logger.warning(
                "TelegramNotifier: TELEGRAM_CHAT_ID not set — "
                "all notifications will be silently skipped."
            )

    # ------------------------------------------------------------------
    # Public API — trade events
    # ------------------------------------------------------------------

    async def send_trade_filled(self, trade: Trade) -> bool:
        """💰 Notify: a candidate entry was filled and a trade is now open.

        AC1: Message must contain symbol, direction, entry, stop, target, R.
        """
        inst = trade.instrument
        direction_arrow = "🟢 LONG" if trade.direction.name == "LONG" else "🔴 SHORT"

        text = (
            f"💰 <b>TRADE FILLED — {inst}</b>\n"
            f"{direction_arrow} | {trade.setup_type.name.replace('_', ' ')}\n"
            f"\n"
            f"<b>Entry:</b>  {_fmt_price(trade.entry_price_actual, inst)}\n"
            f"<b>Stop:</b>   {_fmt_price(trade.stop_price, inst)}\n"
            f"<b>Target:</b> {_fmt_price(trade.target_price, inst)}\n"
            f"<b>R:R:</b>    {_fmt_r(trade.expected_R)}\n"
            f"<b>Size:</b>   {trade.position_size}\n"
            f"<b>Risk:</b>   {_fmt_r(Decimal('1'))} ({trade.risk_amount} USDT)"
        )
        return await self._send(text)

    async def send_trade_closed(
        self,
        trade:          Trade,
        pnl:            Decimal,
        r_achieved:     Decimal,
        consec_losses:  int = 0,
    ) -> bool:
        """✅/❌ Notify: trade closed with result.

        Profit trades use ✅; loss trades use ❌ and append consecutive-loss count.
        """
        inst      = trade.instrument
        is_profit = pnl > _D_ZERO - _EPSILON

        if is_profit:
            emoji = "✅"
            headline = f"PROFIT — {inst}"
            body = (
                f"<b>P&L:</b>   +{pnl.quantize(Decimal('0.01'))} USDT\n"
                f"<b>R earned:</b> +{_fmt_r(r_achieved)}"
            )
        else:
            emoji = "❌"
            headline = f"LOSS — {inst}"
            body = (
                f"<b>P&L:</b>   {pnl.quantize(Decimal('0.01'))} USDT\n"
                f"<b>R lost:</b>  {_fmt_r(r_achieved)}\n"
                f"<b>Consec losses:</b> {consec_losses}"
            )

        direction_arrow = "🟢 LONG" if trade.direction.name == "LONG" else "🔴 SHORT"

        text = (
            f"{emoji} <b>TRADE CLOSED — {headline}</b>\n"
            f"{direction_arrow} | {trade.setup_type.name.replace('_', ' ')}\n"
            f"\n"
            f"<b>Entry:</b>  {_fmt_price(trade.entry_price_actual, inst)}\n"
            f"{body}"
        )
        return await self._send(text)

    async def send_breakeven_moved(self, trade: Trade) -> bool:
        """🔒 Notify: stop was moved to breakeven (+1R reached)."""
        inst = trade.instrument
        text = (
            f"🔒 <b>BREAKEVEN — {inst}</b>\n"
            f"Trade <code>{trade.trade_id}</code> stop moved to entry "
            f"({_fmt_price(trade.entry_price_actual, inst)})"
        )
        return await self._send(text)

    # ------------------------------------------------------------------
    # Public API — setup events
    # ------------------------------------------------------------------

    async def send_candidate_created(
        self,
        candidate: SetupCandidate,
        zone:      SRZone,
    ) -> bool:
        """📊 Notify: a new S/A-tier setup candidate is waiting for entry.

        AC5: Callers MUST only invoke this for S or A tier zones.
              B-tier candidates should NOT be notified to reduce noise.
              This method does not enforce the tier check — that is the
              caller's responsibility (orchestrator/setup engine).
        """
        inst      = candidate.zone_id.split("|")[0] if "|" in candidate.zone_id else "?"
        direction = "LONG" if candidate.direction.name == "LONG" else "SHORT"
        tier_str  = zone.tier.name

        text = (
            f"📊 <b>NEW SETUP — {tier_str} tier</b>\n"
            f"{candidate.setup_type.name.replace('_', ' ')} | {direction}\n"
            f"\n"
            f"<b>Zone:</b>   {_fmt_price(zone.center, zone.timeframe)} "
            f"[{_fmt_price(zone.zone_low, zone.timeframe)}–"
            f"{_fmt_price(zone.zone_high, zone.timeframe)}]\n"
            f"<b>Entry:</b>  {_fmt_price(candidate.entry_price, zone.timeframe)}\n"
            f"<b>Stop:</b>   {_fmt_price(candidate.stop_price, zone.timeframe)}\n"
            f"<b>Target:</b> {_fmt_price(candidate.target_price, zone.timeframe)}\n"
            f"<b>R:R:</b>    {_fmt_r(candidate.expected_R)}\n"
            f"<b>Polarity:</b> {zone.polarity.name}"
        )
        return await self._send(text)

    async def send_candidate_expired(self, candidate: SetupCandidate) -> bool:
        """⏰ Notify: a waiting candidate expired without being filled."""
        direction = "LONG" if candidate.direction.name == "LONG" else "SHORT"
        text = (
            f"⏰ <b>CANDIDATE EXPIRED</b>\n"
            f"{candidate.setup_type.name.replace('_', ' ')} | {direction}\n"
            f"ID: <code>{candidate.candidate_id}</code>"
        )
        return await self._send(text)

    # ------------------------------------------------------------------
    # Public API — system events
    # ------------------------------------------------------------------

    async def send_system_paused(self, reason: str, details: str = "") -> bool:
        """🚨 URGENT: system paused (any reason).

        AC2: Must be sent immediately on any pause condition.
        """
        detail_line = f"\n<i>{details}</i>" if details else ""
        text = (
            f"🚨 <b>SYSTEM PAUSED</b>\n"
            f"<b>Reason:</b> {reason}"
            f"{detail_line}"
        )
        return await self._send(text)

    async def send_system_resumed(self) -> bool:
        """✅ System returned to normal operation."""
        text = "✅ <b>SYSTEM RESUMED</b>\nTrading re-enabled."
        return await self._send(text)

    async def send_drift_alert(
        self,
        win_rate:          Decimal,
        avg_r:             Decimal,
        baseline_win_rate: Decimal,
        baseline_avg_r:    Decimal,
    ) -> bool:
        """⚠️ Drift warning: live performance diverging from historical baseline."""
        text = (
            f"⚠️ <b>DRIFT ALERT</b>\n"
            f"<b>Live win rate:</b>  {_fmt_pct(win_rate)} "
            f"(baseline {_fmt_pct(baseline_win_rate)})\n"
            f"<b>Live avg R:</b>    {_fmt_r(avg_r)} "
            f"(baseline {_fmt_r(baseline_avg_r)})"
        )
        return await self._send(text)

    async def send_governance_halted(self, reason: str) -> bool:
        """🚨 URGENT: governance engine moved to HALTED state."""
        text = (
            f"🚨🚨 <b>GOVERNANCE HALTED</b> 🚨🚨\n"
            f"<b>Reason:</b> {reason}\n"
            f"<i>Manual review and resume required.</i>"
        )
        return await self._send(text)

    async def send_data_gap(self, instrument: str, gap_seconds: int) -> bool:
        """⚠️ WARNING: data gap detected for instrument."""
        gap_mins = Decimal(gap_seconds) / Decimal("60")
        text = (
            f"⚠️ <b>DATA GAP — {instrument}</b>\n"
            f"Gap duration: {gap_mins.quantize(Decimal('0.1'))} min "
            f"({gap_seconds}s)\n"
            f"<i>Instrument feed paused until data recovers.</i>"
        )
        return await self._send(text)

    async def send_db_failure(self, consecutive_count: int) -> bool:
        """🚨 URGENT: DB write failures accumulating."""
        text = (
            f"🚨 <b>DB FAILURE</b>\n"
            f"Consecutive failures: <b>{consecutive_count}</b>\n"
            f"<i>Trading paused until DB connectivity restored.</i>"
        )
        return await self._send(text)

    async def send_instrument_paused(self, instrument: str, reason: str) -> bool:
        """⚠️ Instrument-level pause (data gap, etc.)."""
        text = (
            f"⚠️ <b>INSTRUMENT PAUSED — {instrument}</b>\n"
            f"<b>Reason:</b> {reason}"
        )
        return await self._send(text)

    async def send_instrument_resumed(self, instrument: str) -> bool:
        """✅ Instrument-level resume."""
        text = f"✅ <b>INSTRUMENT RESUMED — {instrument}</b>"
        return await self._send(text)

    # ------------------------------------------------------------------
    # Internal send machinery
    # ------------------------------------------------------------------

    async def _send(self, text: str) -> bool:
        """Send a Telegram message with retry logic.

        AC3/AC4: Up to 3 attempts, 2s delay between retries.
                 Returns False (not raises) after all retries exhausted.
        AC7:     Returns False immediately if not configured.
        """
        if not self._configured:
            logger.debug("TelegramNotifier: skipping send — not configured.")
            return False

        url = _TELEGRAM_API_BASE.format(token=self._token)
        payload = {
            "chat_id":    self._chat_id,
            "text":       text,
            "parse_mode": "HTML",
        }

        last_exc: Optional[Exception] = None
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                success = await self._http_post(url, payload)
                if success:
                    return True
                # Non-exception failure (e.g. 4xx from Telegram) — log and retry
                logger.warning(
                    "TelegramNotifier: send attempt %d/%d returned failure.",
                    attempt, _MAX_RETRIES,
                )
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                logger.warning(
                    "TelegramNotifier: send attempt %d/%d raised %s: %s",
                    attempt, _MAX_RETRIES, type(exc).__name__, exc,
                )

            if attempt < _MAX_RETRIES:
                await asyncio.sleep(_RETRY_DELAY_S)

        logger.error(
            "TelegramNotifier: all %d send attempts failed. last_exc=%s",
            _MAX_RETRIES, last_exc,
        )
        return False

    async def _http_post(self, url: str, payload: dict) -> bool:
        """Execute a single HTTP POST via aiohttp.

        Extracted for easy mocking in tests (AC3/AC4): replace this method
        on the instance to simulate network responses without a real HTTP call.

        Returns True on HTTP 200 OK with result.ok==True.
        Returns False for any non-200 or Telegram error response.
        Raises on network / timeout errors (caller in _send handles these).
        """
        if not _AIOHTTP_AVAILABLE:  # pragma: no cover
            logger.error("TelegramNotifier: aiohttp not installed — cannot send.")
            return False
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return bool(data.get("ok", False))
                logger.warning(
                    "TelegramNotifier: HTTP %d from Telegram API.", resp.status
                )
                return False
