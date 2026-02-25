# CLAUDE.md — Prasad Deterministic Algo Trading System

## What This Project Is
A rules-based algorithmic trading system for BTC/USDT and Gold (XAU/USD).
Runs 24/7, generates signals via Telegram. Zero ML. Pure price action.
The #1 requirement: **given identical candles, produce identical trades** (Doc 1 §2).

## Architecture
- Single-process Python pipeline, triggered every 15-minute candle close
- 13-step sequential execution cycle — NEVER parallel (Doc 1 §5)
- TimescaleDB for data storage
- Docker for deployment
- Separate services: data ingester, trading engine, Telegram notifier

## Non-Negotiable Coding Rules
- Use `decimal.Decimal` for ALL prices, ATR, and ratios. NEVER use Python float.
- Comparison: use EPSILON = Decimal('1E-9'). Never `==` for prices.
- Only CLOSED candles. Always check `is_complete == True`. Filter at data layer.
- Every function has a docstring referencing its spec: `"""Doc 2 §7.3: Wilder ATR recursion."""`
- Deterministic IDs only: `hashlib.sha256(content).hexdigest()[:16]`. Never `uuid4()`.
- All timestamps UTC. Use `datetime.timezone.utc`. Never naive datetimes.
- Frozen dataclasses for all value types. Mutable state only in explicitly marked containers.
- Explicit state machines (dict-based transition tables). Never nested if/elif chains for FSMs.

## Spec Documents
All specifications are in `/docs/`. These are the AUTHORITATIVE source for all logic.
**ALWAYS read the relevant doc before implementing any module.**
**ALWAYS check amendment-v1.1.md and amendment-v1.2.md for patches to that section.**

Key docs:
- doc1-constitution.md → Global laws, execution order, authority hierarchy
- doc2-data.md → Data ingestion, timeframe aggregation, ATR formula
- doc3-zlbb.md + doc3.1-leg-engine.md → ZLBB bands + leg detection FSM
- doc4-sr-system.md → SR zones, clustering, lifecycle, strength, tiers
- doc5-phase-engine.md → Market phase scoring (5 phases)
- doc6-setup-engine.md → 4 setup types (Pullback, Breakout Retest, Rejection, Range Fade)
- doc7-entry-risk.md → Entry trigger, position sizing, risk gate
- doc8-portfolio.md + doc11-conflict.md → Portfolio allocation + conflict resolution
- doc9-persistence.md → State snapshots, restart, hash certification
- doc10-governance.md → Drift detection, safety states

## Module Map
See @docs/module-map.md for which doc maps to which Python file.

## Session Guide
See @docs/session-guide.md for the step-by-step coding plan.

## How To Build
Follow the session guide. One module per session. Always:
1. Read the spec doc first
2. Check amendments for patches
3. Write code + tests together
4. Run tests, fix failures
5. Commit working code

## Testing Commands
```bash
pytest tests/unit/ -v                    # Unit tests
pytest tests/determinism/ -v             # Determinism checks
pytest tests/edge_cases/ -v              # Amendment edge cases
python scripts/validate_determinism.py   # Full pipeline determinism
```

## Style
- Python 3.12+, type hints on everything
- `from decimal import Decimal` at top of every module
- Imports: stdlib → third-party → local, alphabetized within groups
- No wildcard imports, no mutable globals, no print() in production code
