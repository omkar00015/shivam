"""FastAPI dashboard API for Prasad trading system.

Exposes system state as JSON endpoints polled by the single-file dashboard.

SHARED STATE PATTERN:
  STATE is a module-level dict.  run_paper_trader.py imports this module and
  calls update_state() after each cycle, mutating the dict in-place.
  FastAPI route handlers read from the same dict (same process, same import).
  Single-writer / many-reader — no lock needed for this workload.

ENDPOINTS:
  GET /api/state   — full system state snapshot (phase, zones, candidates, etc.)
  GET /api/bars    — last 100 x 15m bars for the chart
  GET /api/zones   — SR zones (also included in /api/state)
  GET /api/health  — liveness probe with staleness age

All prices are serialised as strings to preserve Decimal precision.
All datetimes are ISO-8601 UTC strings.
Enums serialise as their .name string.
"""

import pathlib
import sys

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

load_dotenv()

app = FastAPI(
    title="Prasad Dashboard API",
    description="Real-time state feed for the Prasad algo trading system.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Shared state — mutated by run_paper_trader.py via update_state()
# ---------------------------------------------------------------------------

STATE: dict = {
    # Housekeeping
    "last_update":       None,    # datetime (UTC)
    "cycle_count":       0,       # total cycles run

    # Instrument
    "instrument":        "BTCUSDT",

    # Phase
    "phase":             "UNKNOWN",
    "phase_confidence":  0.0,
    "all_scores":        {},      # {phase_name: int}

    # Pipeline health
    "system_paused":     False,
    "pause_reason":      None,
    "cycle_errors":      [],      # list[str]
    "step_reached":      0,       # 1-13

    # Candidates & trades (counts — candidates list populated below)
    "active_candidates":  0,      # int count from CycleResult
    "open_trades":        0,      # int count from CycleResult
    "candidates_detail":  [],     # list[dict] from setup_engine.get_active_candidates()
    "candidates_history": [],     # list[dict] — all candidates ever detected (append-only)

    # SR zones (top 20 by strength)
    "sr_zones":          [],      # list[dict]

    # ZLBB (1H most-recent state)
    "zlbb_15m":          {},      # {zlema, upper_band, lower_band, upper_1sigma, lower_1sigma}

    # Recent legs (last 10 completed 1H legs)
    "recent_legs":       [],      # list[dict] from ctx.recent_legs[-10:]

    # Chart bars (last 100 x 15m)
    "recent_bars":       [],      # list[dict]
}

# Load persisted candidates_history from disk (survives restarts)
_CANDIDATES_HISTORY_PATH = os.path.join("logs", "candidates_history.json")
if os.path.exists(_CANDIDATES_HISTORY_PATH):
    try:
        with open(_CANDIDATES_HISTORY_PATH) as _f:
            STATE["candidates_history"] = json.load(_f)
    except (json.JSONDecodeError, OSError):
        pass  # start fresh if file is corrupted


# ---------------------------------------------------------------------------
# JSON serialiser — handles Decimal, datetime, enums, dataclasses recursively
# ---------------------------------------------------------------------------

def _serial(obj: Any) -> Any:
    """Recursively make obj JSON-serialisable.

    Rules (in priority order):
      Decimal   → str  (preserves precision, avoids float rounding)
      datetime  → ISO-8601 str
      Enum      → .name str
      dataclass → dict of fields (via __dict__ or dataclasses.asdict)
      list/tuple → recursed list
      dict      → recursed dict
      other     → returned as-is (int, float, str, bool, None)
    """
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if hasattr(obj, "name") and hasattr(obj, "value"):
        # Enum instance (.name and .value both present)
        return obj.name
    if isinstance(obj, (list, tuple)):
        return [_serial(i) for i in obj]
    if isinstance(obj, dict):
        return {str(k): _serial(v) for k, v in obj.items()}
    if hasattr(obj, "__dataclass_fields__"):
        # Frozen or mutable dataclass — serialise via __dict__
        return {k: _serial(v) for k, v in obj.__dict__.items()}
    return obj


# ---------------------------------------------------------------------------
# update_state — called by run_paper_trader.py after every cycle
# ---------------------------------------------------------------------------

def update_state(cycle_result: Any, btc_ctx: Any, recent_bars_raw: list) -> None:
    """Update shared STATE from the latest cycle outputs.

    Called by run_paper_trader.py after every completed 15m cycle.
    Mutates STATE in-place — FastAPI handlers read it on next request.

    Args:
        cycle_result:     CycleResult returned by Orchestrator.run_cycle().
        btc_ctx:          InstrumentContext for BTCUSDT.
        recent_bars_raw:  Rolling list of bar dicts (including current live bar).
                          Each dict must have: timestamp_start, timestamp_end,
                          open, high, low, close, volume.
    """
    # -- Housekeeping --------------------------------------------------------
    STATE["last_update"]  = datetime.now(timezone.utc)
    STATE["cycle_count"] += 1

    # -- Phase ---------------------------------------------------------------
    STATE["phase"] = (
        cycle_result.phase.name
        if hasattr(cycle_result.phase, "name")
        else str(cycle_result.phase)
    )
    STATE["phase_confidence"] = float(cycle_result.phase_confidence)

    # All 5 phase scores from the engine's last_scores property
    if hasattr(btc_ctx, "phase_engine") and hasattr(btc_ctx.phase_engine, "last_scores"):
        STATE["all_scores"] = {
            k.name: v for k, v in btc_ctx.phase_engine.last_scores.items()
        }
    else:
        STATE["all_scores"] = {}

    # -- Pipeline health -----------------------------------------------------
    STATE["cycle_errors"] = list(cycle_result.cycle_errors)
    STATE["step_reached"] = int(cycle_result.step_reached)
    STATE["active_candidates"] = int(cycle_result.active_candidates)
    STATE["open_trades"]       = int(cycle_result.open_trades)

    # -- Candidates detail (from setup engine) -------------------------------
    if hasattr(btc_ctx, "setup_engine") and hasattr(btc_ctx.setup_engine, "get_active_candidates"):
        candidates = btc_ctx.setup_engine.get_active_candidates()
        STATE["candidates_detail"] = [
            {
                "candidate_id": c.candidate_id,
                "setup_type":   c.setup_type.name if hasattr(c.setup_type, "name") else str(c.setup_type),
                "direction":    c.direction.name  if hasattr(c.direction,  "name") else str(c.direction),
                "zone_id":      c.zone_id,
                "entry_price":  str(c.entry_price),
                "stop_price":   str(c.stop_price),
                "target_price": str(c.target_price),
                "expected_R":   str(c.expected_R),
                "created_at":   c.created_at.isoformat() if hasattr(c.created_at, "isoformat") else str(c.created_at),
                "expiry_bars":  int(c.expiry_bars),
                "status":       c.status.name if hasattr(c.status, "name") else str(c.status),
            }
            for c in candidates
        ]
    else:
        STATE["candidates_detail"] = []

    # -- Candidates history (append-only log of all detected candidates) ------
    _seen_ids = {
        (h.get("candidate_id"), h.get("zone_id"), h.get("setup_type"))
        for h in STATE["candidates_history"]
    }
    _new_candidates_added = False
    for cd in STATE["candidates_detail"]:
        key = (cd.get("candidate_id"), cd.get("zone_id"), cd.get("setup_type"))
        if key not in _seen_ids:
            _seen_ids.add(key)
            entry = dict(cd)
            entry["detected_at"] = datetime.now(timezone.utc).isoformat()
            STATE["candidates_history"].append(entry)
            _new_candidates_added = True

    if _new_candidates_added:
        try:
            os.makedirs("logs", exist_ok=True)
            with open(_CANDIDATES_HISTORY_PATH, "w") as _f:
                json.dump(STATE["candidates_history"], _f, default=str, indent=2)
        except OSError:
            pass  # non-fatal — history is still in memory

    # -- SR zones (top 20 by strength) ----------------------------------------
    if hasattr(btc_ctx, "zone_detector"):
        zones = btc_ctx.zone_detector.get_active_zones()
        STATE["sr_zones"] = [
            {
                "zone_id":    z.zone_id,
                "center":     str(z.center),
                "zone_high":  str(z.zone_high),
                "zone_low":   str(z.zone_low),
                "polarity":   z.polarity.name,
                "tier":       z.tier.name,
                "strength":   int(z.strength),
                "state":      z.state.name,
                "timeframe":  z.timeframe,
                "touch_count": int(z.touch_count),
                "is_midpoint": bool(z.is_midpoint),
            }
            for z in zones[:20]
        ]
    else:
        STATE["sr_zones"] = []

    # -- ZLBB state (last 1H state from rolling context window) ---------------
    # ZLBBEngine has no get_state(); the orchestrator stores the last N 1H ZLBB
    # states in ctx.recent_1h_zlbb.  We use the most recent entry.
    if hasattr(btc_ctx, "recent_1h_zlbb") and btc_ctx.recent_1h_zlbb:
        zlbb = btc_ctx.recent_1h_zlbb[-1]
        STATE["zlbb_15m"] = {
            "zlema":        str(zlbb.zlema)        if hasattr(zlbb, "zlema")        else None,
            "upper_band":   str(zlbb.upper_band)   if hasattr(zlbb, "upper_band")   else None,
            "lower_band":   str(zlbb.lower_band)   if hasattr(zlbb, "lower_band")   else None,
            "upper_1sigma": str(zlbb.upper_1sigma) if hasattr(zlbb, "upper_1sigma") else None,
            "lower_1sigma": str(zlbb.lower_1sigma) if hasattr(zlbb, "lower_1sigma") else None,
        }
    else:
        STATE["zlbb_15m"] = {}

    # -- Recent legs (last 10 completed 1H legs) -----------------------------
    if hasattr(btc_ctx, "recent_legs") and btc_ctx.recent_legs:
        STATE["recent_legs"] = [
            {
                "direction":       leg.direction.name if hasattr(leg.direction, "name") else str(leg.direction),
                "start_price":     str(leg.start_price),
                "end_price":       str(leg.end_price),
                "displacement":    str(leg.displacement),
                "bar_count":       int(leg.bar_count),
                "quality":         int(leg.quality),
                "efficiency":      str(leg.efficiency),
                "is_band_walk":    bool(leg.is_band_walk),
                "timestamp_start": (
                    leg.timestamp_start.isoformat()
                    if hasattr(leg.timestamp_start, "isoformat")
                    else str(leg.timestamp_start)
                ),
                "timestamp_end": (
                    leg.timestamp_end.isoformat()
                    if hasattr(leg.timestamp_end, "isoformat")
                    else str(leg.timestamp_end)
                ),
            }
            for leg in btc_ctx.recent_legs[-10:]
        ]
    else:
        STATE["recent_legs"] = []

    # -- Recent bars for chart (last 100) ------------------------------------
    # Serialise each bar dict — values may be Decimal or datetime objects
    def _bar_to_dict(b: Any) -> dict:
        """Convert bar (dict or AggregatedBar) to a plain JSON-safe dict."""
        if isinstance(b, dict):
            return {
                "timestamp_start": _serial(b.get("timestamp_start")),
                "timestamp_end":   _serial(b.get("timestamp_end")),
                "open":  str(b.get("open",  0)),
                "high":  str(b.get("high",  0)),
                "low":   str(b.get("low",   0)),
                "close": str(b.get("close", 0)),
                "volume": str(b.get("volume", 0)),
            }
        # AggregatedBar dataclass
        return {
            "timestamp_start": b.timestamp_start.isoformat(),
            "timestamp_end":   b.timestamp_end.isoformat(),
            "open":  str(b.open),
            "high":  str(b.high),
            "low":   str(b.low),
            "close": str(b.close),
            "volume": str(b.volume),
        }

    STATE["recent_bars"] = [
        _bar_to_dict(b) for b in recent_bars_raw[-100:]
    ]


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

@app.get("/api/state", summary="Full system state snapshot")
async def get_state() -> JSONResponse:
    """Return the complete current system state as JSON.

    All Decimal values are strings. All datetimes are ISO-8601 UTC.
    Includes: phase, all_scores, candidates_detail, sr_zones, zlbb_15m,
              cycle_errors, step_reached, system_paused.
    """
    return JSONResponse(content=_serial(STATE))


@app.get("/api/bars", summary="Recent 15m bars for chart")
async def get_bars() -> JSONResponse:
    """Return the last (up to) 100 completed 15m bars for charting.

    Each bar: {timestamp_start, timestamp_end, open, high, low, close, volume}
    All price fields are strings.
    """
    return JSONResponse(content=_serial(STATE.get("recent_bars", [])))


@app.get("/api/zones", summary="Active SR zones")
async def get_zones() -> JSONResponse:
    """Return the top 20 active SR zones sorted by strength descending.

    Each zone: {zone_id, center, zone_high, zone_low, polarity, tier,
                strength, state, timeframe, touch_count, is_midpoint}
    """
    return JSONResponse(content=_serial(STATE.get("sr_zones", [])))


@app.get("/api/candidates", summary="Candidate detection history")
async def get_candidates() -> JSONResponse:
    """Return the full append-only history of all detected candidates.

    Each entry includes a 'detected_at' ISO-8601 UTC timestamp marking when
    the candidate first appeared.
    """
    return JSONResponse(content=_serial(STATE.get("candidates_history", [])))


@app.get("/api/health", summary="Liveness probe")
async def health() -> dict:
    """Return API liveness, cycle count, phase, and trade status.

    Returns HTTP 200 always (the API is up).  Includes both
    last_update (ISO-8601) and last_update_seconds_ago for convenience.
    """
    last = STATE.get("last_update")
    seconds_ago = None
    last_iso = None
    if last and isinstance(last, datetime):
        seconds_ago = round((datetime.now(timezone.utc) - last).total_seconds(), 1)
        last_iso = last.isoformat()
    return {
        "status":                  "ok",
        "cycle":                   STATE.get("cycle_count", 0),
        "phase":                   STATE.get("phase"),
        "last_update":             last_iso,
        "last_update_seconds_ago": seconds_ago,
        "open_trades":             STATE.get("open_trades", 0),
        "system_paused":           STATE.get("system_paused", False),
    }


# ---------------------------------------------------------------------------
# Dashboard HTML — serves dashboard/index.html at root
# ---------------------------------------------------------------------------

DASHBOARD_PATH = _REPO_ROOT / "dashboard" / "index.html"


@app.get("/", summary="Trading dashboard")
async def serve_dashboard() -> FileResponse:
    """Serve the single-file trading dashboard HTML."""
    return FileResponse(str(DASHBOARD_PATH))
