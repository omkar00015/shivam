# Module Map — Which Doc → Which Code File

| Spec Document | Python Module | What It Does |
|---|---|---|
| Doc 1 (Constitution) | `src/core/types.py`, `src/core/enums.py`, `src/core/constants.py` | Shared types, enums, EPSILON, tick sizes |
| Doc 2 (Data & ATR) | `src/data/bar_validator.py`, `src/data/aggregator.py`, `src/data/atr.py`, `src/data/gap_handler.py` | Raw data handling, TF aggregation, ATR |
| Doc 2 §11 | `src/indicators/bandwidth_percentile.py` | Bandwidth percentile ranking |
| Doc 3 (ZLBB Math) | `src/indicators/zlema.py`, `src/indicators/zlbb.py` | ZLEMA and band calculations |
| Doc 3.1 + C1 + P1 | `src/legs/leg_engine.py`, `src/legs/band_walk.py` | Leg detection FSM with band walk gate |
| E1 | `src/legs/spike_filter.py` | Single-bar spike detection |
| P6 | `src/legs/leg_quality.py` | Leg quality score (0-4) |
| Doc 4 (SR System) | `src/sr/sources.py`, `src/sr/clustering.py`, `src/sr/zone_width.py`, `src/sr/min_gap.py` | SR zone creation and merging |
| C7 | `src/sr/midpoint.py` | Midpoint SR validation |
| P11 + C2 + C3 + P2 + P3 | `src/sr/lifecycle.py`, `src/sr/strength.py` | Zone lifecycle FSM and strength scoring |
| Add.B (SR Tiers) | `src/sr/tier.py` | S/A/B/C tier classification |
| P10 | `src/sr/density.py` | Density bias computation |
| Doc 5 + C6 + P5 | `src/phase/scorer.py` | All 5 phase scoring functions |
| M1 + P8 | `src/phase/stickiness.py` | Phase label transition logic |
| Doc 5 §13 | `src/phase/htf_override.py` | HTF phase override |
| E4 | `src/phase/minimum_legs.py` | Forced TRANSITION when < 3 legs |
| Doc 6 + C2 | `src/setups/pre_filter.py` | Global setup pre-filter |
| Doc 6 §6 + M3 | `src/setups/pullback.py` | Pullback Continuation setup |
| C3 + P4 | `src/setups/breakout_retest.py` | Breakout Retest setup |
| Doc 6.1 | `src/setups/rejection_fakeout.py` | Rejection/Fakeout setup |
| P9 | `src/setups/range_fade.py` | Range Fade setup (fully specified) |
| Doc 6 §12 | `src/setups/candidate.py` | SetupCandidate creation |
| Doc 7 | `src/execution/entry_trigger.py`, `src/execution/risk_gate.py`, `src/execution/position_sizer.py` | Entry, risk checks, sizing |
| Add.C §C1-C2 | `src/execution/order_manager.py`, `src/execution/exit_manager.py` | Orders and 3-tier exits |
| Doc 8 + Doc 11 | `src/portfolio/allocator.py`, `src/portfolio/conflict_resolver.py` | Portfolio selection and conflict resolution |
| Add.C §C3 | `src/portfolio/pyramiding.py` | Pyramid add rules |
| Doc 10 | `src/governance/drift_detector.py`, `src/governance/state_machine.py`, `src/governance/daily_limits.py` | Governance and safety |
| Add.C §C6 | `src/governance/pause_conditions.py` | 5 pause triggers |
| Doc 9 | `src/persistence/state_snapshot.py`, `src/persistence/rebuild.py`, `src/persistence/hash_cert.py`, `src/persistence/event_ledger.py` | Persistence and restart |
| E3 + P13 | `src/persistence/history_validator.py` | Minimum history depth checks |
| Doc 1 §5 | `src/engine/pipeline.py` | The 13-step sequential pipeline |
| Doc 12 | `scripts/backtest.py` | Backtest runner (same code as live) |
