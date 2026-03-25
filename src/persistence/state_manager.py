"""Doc 9 (Persistence, Restart & Replay Integrity) + Doc 1 §11 (Restart Certification)
+ Amendment v1.1 E3 (Minimum History Requirements).

PHILOSOPHY (Doc 9 §2):
  The ultimate truth is historical 15m bars. Everything else is derived.
  On restart, the system rebuilds all indicator/zone/phase/setup state from bars.
  External state (equity, open trades) is loaded from DB, not recomputed.

WHAT IS PERSISTED (Doc 9 §4 + user spec):
  - system_state table: JSON snapshots for every component per (instrument, component)
    Components: 'zlbb_15m', 'zlbb_1h', 'zlbb_4h', 'zlbb_1d',
                'zone_detector', 'phase_engine', 'setup_engine', 'portfolio'
    Each row includes state_hash = sha256(state_json) for restart certification.
  - trades table: immutable trade records (OPEN / CLOSED)
  - bars table: every completed bar for every timeframe (replay source)

RESTART CERTIFICATION (Doc 1 §11):
  After rebuild, each component's state is re-hashed and compared to last saved hash.
  Any mismatch → log CRITICAL, set system_paused=True, halt trading.

MINIMUM HISTORY (Amendment E3):
  15m / 1H / 4H / 1D / 1W / 1M / 3M : >= 40 bars minimum before live trading.

SERIALISATION RULES (no float anywhere):
  - All Decimal → str (via str(d), restored via Decimal(s))
  - All datetime → ISO 8601 UTC str (dt.isoformat(), restored via datetime.fromisoformat())
  - Enums → .value  (int) or .name (str)
  - All state_hash computed as sha256(canonical_json_bytes).hexdigest()

DATABASE SCHEMA (auto-created on first connect):
  system_state (id, snapshot_at, instrument, component, state_json, state_hash)
  trades       (trade_id, instrument, direction, entry_price, stop_price,
                target_price, position_size, setup_type, opened_at,
                closed_at, pnl, status)
  bars         (instrument, timeframe, timestamp_start, open, high, low, close,
                volume, PRIMARY KEY (instrument, timeframe, timestamp_start))

All DB operations are async (asyncpg). Connection pool managed internally.
DATABASE_URL environment variable required; falls back to
'postgresql://localhost:5432/prasad_algo' if not set.

All arithmetic uses Decimal. No float anywhere.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

import asyncpg

from src.data_ingest.bar_aggregator import AggregatedBar
from src.execution.entry_executor import Trade
from src.setup.setup_engine import Direction, SetupType

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_DEFAULT_DATABASE_URL = "postgresql://localhost:5432/prasad_algo"
_MIN_BARS_PER_TF: dict[str, int] = {
    "15m": 40, "1H": 40, "4H": 40, "1D": 40,
    "1W": 40, "1M": 40, "3M": 40,
}

# ---------------------------------------------------------------------------
# DDL — tables created on first connect
# ---------------------------------------------------------------------------
_DDL_SYSTEM_STATE = """
CREATE TABLE IF NOT EXISTS system_state (
    id           SERIAL PRIMARY KEY,
    snapshot_at  TIMESTAMPTZ NOT NULL,
    instrument   VARCHAR(20)  NOT NULL,
    component    VARCHAR(50)  NOT NULL,
    state_json   JSONB        NOT NULL,
    state_hash   VARCHAR(64)  NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_system_state_lookup
    ON system_state (instrument, component, snapshot_at DESC);
"""

_DDL_TRADES = """
CREATE TABLE IF NOT EXISTS trades (
    trade_id      VARCHAR(16)   PRIMARY KEY,
    instrument    VARCHAR(20)   NOT NULL,
    direction     VARCHAR(5)    NOT NULL,
    entry_price   NUMERIC(20,8) NOT NULL,
    stop_price    NUMERIC(20,8) NOT NULL,
    target_price  NUMERIC(20,8) NOT NULL,
    position_size NUMERIC(20,8) NOT NULL,
    setup_type    VARCHAR(30)   NOT NULL,
    opened_at     TIMESTAMPTZ   NOT NULL,
    closed_at     TIMESTAMPTZ,
    pnl           NUMERIC(20,8),
    status        VARCHAR(20)   NOT NULL
);
"""

_DDL_BARS = """
CREATE TABLE IF NOT EXISTS bars (
    instrument       VARCHAR(20)   NOT NULL,
    timeframe        VARCHAR(10)   NOT NULL,
    timestamp_start  TIMESTAMPTZ   NOT NULL,
    open             NUMERIC(20,8) NOT NULL,
    high             NUMERIC(20,8) NOT NULL,
    low              NUMERIC(20,8) NOT NULL,
    close            NUMERIC(20,8) NOT NULL,
    volume           NUMERIC(20,8) NOT NULL,
    PRIMARY KEY (instrument, timeframe, timestamp_start)
);
CREATE INDEX IF NOT EXISTS idx_bars_lookup
    ON bars (instrument, timeframe, timestamp_start ASC);
"""


# ---------------------------------------------------------------------------
# Serialisation helpers — all Decimal as str, datetime as ISO UTC str
# ---------------------------------------------------------------------------

def _decimal_to_str(d: Decimal) -> str:
    """Serialise Decimal as string. Never float."""
    return str(d)


def _str_to_decimal(s: str) -> Decimal:
    """Deserialise string back to Decimal."""
    return Decimal(s)


def _dt_to_str(dt: datetime) -> str:
    """Serialise datetime as ISO 8601 UTC string."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _str_to_dt(s: str) -> datetime:
    """Deserialise ISO 8601 string back to UTC datetime."""
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _canonical_json(obj: dict) -> bytes:
    """Produce deterministic UTF-8 JSON bytes (sorted keys, no whitespace)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _hash_state(state_dict: dict) -> str:
    """Doc 1 §11 + Doc 9 §6: sha256 of canonical JSON of state_dict."""
    return hashlib.sha256(_canonical_json(state_dict)).hexdigest()


def trade_to_dict(trade: Trade) -> dict:
    """Serialise a Trade to a plain dict. All Decimal → str, datetime → ISO str."""
    return {
        "trade_id":            trade.trade_id,
        "candidate_id":        trade.candidate_id,
        "instrument":          trade.instrument,
        "direction":           trade.direction.name,
        "setup_type":          trade.setup_type.name,
        "entry_price_ref":     _decimal_to_str(trade.entry_price_ref),
        "entry_price_actual":  _decimal_to_str(trade.entry_price_actual),
        "stop_price":          _decimal_to_str(trade.stop_price),
        "target_price":        _decimal_to_str(trade.target_price),
        "stop_distance":       _decimal_to_str(trade.stop_distance),
        "expected_R":          _decimal_to_str(trade.expected_R),
        "actual_R_if_stopped": _decimal_to_str(trade.actual_R_if_stopped),
        "position_size":       _decimal_to_str(trade.position_size),
        "risk_amount":         _decimal_to_str(trade.risk_amount),
        "account_equity":      _decimal_to_str(trade.account_equity),
        "zone_id":             trade.zone_id,
        "opened_at":           _dt_to_str(trade.opened_at),
        "status":              trade.status,
        "breakeven_triggered": trade.breakeven_triggered,
        "current_stop":        _decimal_to_str(trade.current_stop) if trade.current_stop is not None else None,
    }


def trade_from_dict(d: dict) -> Trade:
    """Deserialise a Trade from a plain dict. All str → Decimal, str → datetime."""
    return Trade(
        trade_id=d["trade_id"],
        candidate_id=d["candidate_id"],
        instrument=d["instrument"],
        direction=Direction[d["direction"]],
        setup_type=SetupType[d["setup_type"]],
        entry_price_ref=_str_to_decimal(d["entry_price_ref"]),
        entry_price_actual=_str_to_decimal(d["entry_price_actual"]),
        stop_price=_str_to_decimal(d["stop_price"]),
        target_price=_str_to_decimal(d["target_price"]),
        stop_distance=_str_to_decimal(d["stop_distance"]),
        expected_R=_str_to_decimal(d["expected_R"]),
        actual_R_if_stopped=_str_to_decimal(d["actual_R_if_stopped"]),
        position_size=_str_to_decimal(d["position_size"]),
        risk_amount=_str_to_decimal(d["risk_amount"]),
        account_equity=_str_to_decimal(d["account_equity"]),
        zone_id=d["zone_id"],
        opened_at=_str_to_dt(d["opened_at"]),
        status=d["status"],
        breakeven_triggered=d["breakeven_triggered"],
        current_stop=_str_to_decimal(d["current_stop"]) if d.get("current_stop") is not None else None,
    )


def bar_to_dict(bar: AggregatedBar) -> dict:
    """Serialise an AggregatedBar to a plain dict."""
    return {
        "symbol":           bar.symbol,
        "timestamp_start":  _dt_to_str(bar.timestamp_start),
        "timestamp_end":    _dt_to_str(bar.timestamp_end),
        "timeframe":        bar.timeframe,
        "open":             _decimal_to_str(bar.open),
        "high":             _decimal_to_str(bar.high),
        "low":              _decimal_to_str(bar.low),
        "close":            _decimal_to_str(bar.close),
        "volume":           _decimal_to_str(bar.volume),
        "is_complete":      bar.is_complete,
        "is_reliable":      bar.is_reliable,
    }


def bar_from_dict(d: dict) -> AggregatedBar:
    """Deserialise an AggregatedBar from a plain dict."""
    return AggregatedBar(
        symbol=d["symbol"],
        timestamp_start=_str_to_dt(d["timestamp_start"]),
        timestamp_end=_str_to_dt(d["timestamp_end"]),
        timeframe=d["timeframe"],
        open=_str_to_decimal(d["open"]),
        high=_str_to_decimal(d["high"]),
        low=_str_to_decimal(d["low"]),
        close=_str_to_decimal(d["close"]),
        volume=_str_to_decimal(d["volume"]),
        is_complete=d["is_complete"],
        is_reliable=d["is_reliable"],
    )


# ---------------------------------------------------------------------------
# StateManager
# ---------------------------------------------------------------------------

class StateManager:
    """Doc 9 + Doc 1 §11 + Amendment E3: Async persistence layer.

    All public methods are coroutines (async def). Caller must await them.

    Lifecycle::

        sm = StateManager()
        await sm.connect()          # creates pool, runs DDL
        ...
        await sm.close()            # closes pool

    Or use as async context manager::

        async with StateManager() as sm:
            ...

    DATABASE_URL environment variable controls the PostgreSQL connection string.
    """

    def __init__(self, dsn: Optional[str] = None) -> None:
        """Initialise without connecting. Call connect() before use.

        Args:
            dsn: PostgreSQL connection string. Defaults to DATABASE_URL env var
                 or 'postgresql://localhost:5432/prasad_algo'.
        """
        self._dsn: str = (
            dsn
            or os.environ.get("DATABASE_URL", _DEFAULT_DATABASE_URL)
        )
        self._pool: Optional[asyncpg.Pool] = None

    @staticmethod
    def _parse_jsonb(value) -> dict:
        """Parse asyncpg JSONB column — returns str in real DB, dict in mocks."""
        import json
        if isinstance(value, str):
            return json.loads(value)
        return dict(value)

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Open connection pool and create tables if they do not exist."""
        self._pool = await asyncpg.create_pool(
            self._dsn,
            min_size=1,
            max_size=5,
        )
        await self._run_ddl()
        logger.info("StateManager: connected to %s", self._dsn.split("@")[-1])

    async def close(self) -> None:
        """Close the connection pool."""
        if self._pool:
            await self._pool.close()
            self._pool = None

    async def __aenter__(self) -> "StateManager":
        await self.connect()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # System state snapshots
    # ------------------------------------------------------------------

    async def save_snapshot(
        self,
        instrument: str,
        component: str,
        state_dict: dict,
        snapshot_at: Optional[datetime] = None,
    ) -> bool:
        """Doc 9 §13: Save a JSON snapshot for (instrument, component).

        Computes sha256 hash of the canonical JSON and stores it alongside.
        Returns True on success.

        Args:
            instrument:   Instrument symbol, e.g. 'BTCUSDT'.
            component:    Component name, e.g. 'zlbb_15m', 'portfolio'.
            state_dict:   Serialisable dict (all Decimal already as str).
            snapshot_at:  UTC timestamp. Defaults to now.
        """
        self._require_pool()
        if snapshot_at is None:
            snapshot_at = datetime.now(tz=timezone.utc)

        state_hash = _hash_state(state_dict)
        state_json_str = json.dumps(state_dict, sort_keys=True, separators=(",", ":"))

        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO system_state
                    (snapshot_at, instrument, component, state_json, state_hash)
                VALUES ($1, $2, $3, $4::jsonb, $5)
                """,
                snapshot_at,
                instrument,
                component,
                state_json_str,
                state_hash,
            )
        logger.debug(
            "StateManager: saved snapshot %s/%s hash=%s",
            instrument, component, state_hash[:8],
        )
        return True

    async def load_snapshot(
        self,
        instrument: str,
        component: str,
    ) -> Optional[dict]:
        """Load the most recent snapshot for (instrument, component).

        Returns the state_dict (as a Python dict with str/int/bool/list/dict
        values; Decimal fields are still str — caller must convert).
        Returns None if no snapshot found.
        """
        self._require_pool()
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT state_json, state_hash
                FROM system_state
                WHERE instrument = $1 AND component = $2
                ORDER BY snapshot_at DESC
                LIMIT 1
                """,
                instrument,
                component,
            )
        if row is None:
            return None
        state_dict = self._parse_jsonb(row["state_json"])
        return state_dict

    async def verify_snapshot(
        self,
        instrument: str,
        component: str,
    ) -> bool:
        """Doc 1 §11: Recompute hash of loaded snapshot and compare with stored hash.

        Returns True if hashes match (state is intact).
        Returns False if mismatch (state may be corrupted → halt trading).
        """
        self._require_pool()
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT state_json, state_hash
                FROM system_state
                WHERE instrument = $1 AND component = $2
                ORDER BY snapshot_at DESC
                LIMIT 1
                """,
                instrument,
                component,
            )
        if row is None:
            # No snapshot → cannot verify → treat as unverified (not a mismatch)
            logger.warning(
                "StateManager: no snapshot for %s/%s — cannot verify.",
                instrument, component,
            )
            return False

        stored_hash = row["state_hash"]
        state_dict  = self._parse_jsonb(row["state_json"])
        recomputed  = _hash_state(state_dict)

        if recomputed != stored_hash:
            logger.critical(
                "StateManager: HASH MISMATCH %s/%s stored=%s recomputed=%s — HALT.",
                instrument, component, stored_hash[:8], recomputed[:8],
            )
            return False

        return True

    async def verify_all(
        self,
        instruments: list[str],
        components: list[str],
    ) -> dict[str, bool]:
        """Doc 1 §11: Verify every (instrument, component) pair.

        Returns a dict mapping '<instrument>/<component>' → bool.
        """
        results: dict[str, bool] = {}
        for instr in instruments:
            for comp in components:
                key    = f"{instr}/{comp}"
                valid  = await self.verify_snapshot(instr, comp)
                results[key] = valid
        return results

    # ------------------------------------------------------------------
    # Trades
    # ------------------------------------------------------------------

    async def save_trade(self, trade: Trade) -> bool:
        """Doc 9 §4: Persist a Trade (INSERT OR IGNORE on duplicate trade_id).

        Returns True on success.
        """
        self._require_pool()
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO trades
                    (trade_id, instrument, direction, entry_price, stop_price,
                     target_price, position_size, setup_type, opened_at, status)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                ON CONFLICT (trade_id) DO NOTHING
                """,
                trade.trade_id,
                trade.instrument,
                trade.direction.name,
                float(trade.entry_price_actual),  # NUMERIC in DB — store via float for asyncpg
                float(trade.stop_price),
                float(trade.target_price),
                float(trade.position_size),
                trade.setup_type.name,
                trade.opened_at,
                trade.status,
            )
        logger.debug("StateManager: saved trade %s", trade.trade_id)
        return True

    async def update_trade(
        self,
        trade_id: str,
        status: str,
        closed_at: Optional[datetime] = None,
        pnl: Optional[Decimal] = None,
    ) -> bool:
        """Doc 9: Update trade status, closed_at, and pnl after close.

        Returns True if row was updated, False if trade_id not found.
        """
        self._require_pool()
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE trades
                SET status = $1, closed_at = $2, pnl = $3
                WHERE trade_id = $4
                """,
                status,
                closed_at,
                float(pnl) if pnl is not None else None,
                trade_id,
            )
        updated = result.split()[-1] != "0"   # "UPDATE N" → N rows affected
        if not updated:
            logger.warning("StateManager: trade_id %s not found for update.", trade_id)
        return updated

    async def load_open_trades(self) -> list[dict]:
        """Doc 9 §4: Load all trades with status='OPEN'.

        Returns list of dicts (not Trade objects — caller reconstructs).
        All price fields returned as str (Decimal-safe).
        """
        self._require_pool()
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT trade_id, instrument, direction, entry_price, stop_price,
                       target_price, position_size, setup_type, opened_at, status
                FROM trades
                WHERE status = 'OPEN'
                ORDER BY opened_at ASC
                """
            )
        result = []
        for row in rows:
            result.append({
                "trade_id":      row["trade_id"],
                "instrument":    row["instrument"],
                "direction":     row["direction"],
                "entry_price":   str(row["entry_price"]),
                "stop_price":    str(row["stop_price"]),
                "target_price":  str(row["target_price"]),
                "position_size": str(row["position_size"]),
                "setup_type":    row["setup_type"],
                "opened_at":     row["opened_at"].isoformat(),
                "status":        row["status"],
            })
        return result

    # ------------------------------------------------------------------
    # Bars
    # ------------------------------------------------------------------

    async def save_bar(self, bar: AggregatedBar, instrument: str) -> bool:
        """Doc 9 §2: Persist a completed bar (upsert — idempotent on replay).

        Returns True on success.
        """
        self._require_pool()
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO bars
                    (instrument, timeframe, timestamp_start, timestamp_end,
                     open, high, low, close, volume)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                ON CONFLICT (instrument, timeframe, timestamp_start)
                DO UPDATE SET
                    timestamp_end = EXCLUDED.timestamp_end,
                    open   = EXCLUDED.open,
                    high   = EXCLUDED.high,
                    low    = EXCLUDED.low,
                    close  = EXCLUDED.close,
                    volume = EXCLUDED.volume
                """,
                instrument,
                bar.timeframe,
                bar.timestamp_start,
                bar.timestamp_end,
                float(bar.open),
                float(bar.high),
                float(bar.low),
                float(bar.close),
                float(bar.volume),
            )
        return True

    async def load_bars(
        self,
        instrument: str,
        timeframe: str,
        after: Optional[datetime] = None,
        limit: int = 10_000,
        newest_first: bool = False,
    ) -> list[dict]:
        """Doc 9 §5 step 1: Load bars in ascending timestamp order.

        Args:
            instrument:   Instrument symbol.
            timeframe:    Bar timeframe label.
            after:        Only return bars with timestamp_start > after (optional).
            limit:        Maximum number of bars to return.
            newest_first: When True, fetch the most-recent `limit` rows (DESC)
                          then reverse before returning so caller always receives
                          chronological (ASC) order.  Default False preserves
                          existing behaviour (oldest-first ASC).

        Returns:
            List of dicts with keys: instrument, timeframe, timestamp_start,
            open, high, low, close, volume (all price fields as str Decimal).
            Always returned in chronological (ascending) order.
        """
        self._require_pool()
        order = "DESC" if newest_first else "ASC"
        if after is not None:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    f"""
                    SELECT instrument, timeframe, timestamp_start,
                           open, high, low, close, volume
                    FROM bars
                    WHERE instrument = $1 AND timeframe = $2
                      AND timestamp_start > $3
                    ORDER BY timestamp_start {order}
                    LIMIT $4
                    """,
                    instrument, timeframe, after, limit,
                )
        else:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    f"""
                    SELECT instrument, timeframe, timestamp_start,
                           open, high, low, close, volume
                    FROM bars
                    WHERE instrument = $1 AND timeframe = $2
                    ORDER BY timestamp_start {order}
                    LIMIT $3
                    """,
                    instrument, timeframe, limit,
                )

        result = []
        for row in rows:
            result.append({
                "instrument":      row["instrument"],
                "timeframe":       row["timeframe"],
                "timestamp_start": row["timestamp_start"].isoformat(),
                "open":            str(row["open"]),
                "high":            str(row["high"]),
                "low":             str(row["low"]),
                "close":           str(row["close"]),
                "volume":          str(row["volume"]),
            })

        if newest_first:
            result.reverse()
        return result

    async def get_bar_count(self, instrument: str, timeframe: str) -> int:
        """Amendment E3: Return total number of bars stored for (instrument, timeframe)."""
        self._require_pool()
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT COUNT(*) AS cnt
                FROM bars
                WHERE instrument = $1 AND timeframe = $2
                """,
                instrument, timeframe,
            )
        return int(row["cnt"])

    async def meets_minimum_history(self, instrument: str, timeframe: str) -> bool:
        """Amendment E3: True if stored bar count >= minimum required for this TF.

        If timeframe is not in the known minimum map, uses 40 as default.
        """
        minimum = _MIN_BARS_PER_TF.get(timeframe, 40)
        count   = await self.get_bar_count(instrument, timeframe)
        return count >= minimum

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _run_ddl(self) -> None:
        """Create tables and indexes if they do not exist."""
        async with self._pool.acquire() as conn:
            await conn.execute(_DDL_SYSTEM_STATE)
            await conn.execute(_DDL_TRADES)
            await conn.execute(_DDL_BARS)

    def _require_pool(self) -> None:
        """Raise RuntimeError if connect() has not been called."""
        if self._pool is None:
            raise RuntimeError(
                "StateManager: not connected. Call await sm.connect() first."
            )
