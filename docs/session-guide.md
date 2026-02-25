# Session Guide — What To Type Into Claude Code

This file tells you exactly what to type into Claude Code for each coding session.
Just copy-paste the prompt for each session. Claude Code does the rest.

---

## PHASE 1: Foundation & Data Layer (Week 1)

### Session 1: Core Types & Enums
**Copy-paste this into Claude Code:**
```
Read docs/doc1-constitution.md, docs/doc2-data.md, and docs/module-map.md.

Create src/core/types.py with these frozen dataclasses using Decimal for all prices:
- Bar (timestamp_start, timestamp_end, timeframe, open, high, low, close, volume, is_complete, is_reliable)
- ZLBBState (zlema, sigma, upper_1sigma, lower_1sigma, upper_2sigma, lower_2sigma, bandwidth, bandwidth_percentile)
- Leg (id, direction, start_price, end_price, displacement, bar_count, quality, efficiency, start_ts, end_ts, origin, is_spike)
- SRZone (id, center, zone_high, zone_low, strength, tier, state, polarity, timeframe, touch_count, false_break_count, frozen_touch_bonus, is_midpoint, origin, created_at, last_touched_at, confirmed_at)
- SetupCandidate (id, setup_type, direction, zone_id, entry_reference, stop_price, target_price, expected_r, expiry_time, created_at, phase_at_creation, confidence_score)
- Trade (id, instrument, direction, entry_price, stop_price, size, setup_type, zone_id, opened_at, status)

Create src/core/enums.py with enums for:
- Timeframe (M15, H1, H4, D1, W1, MO, Q3)
- Direction (BULL, BEAR)
- Phase (TREND_BULL, TREND_BEAR, BALANCE, DISTRIBUTION, ACCUMULATION, TRANSITION)
- ZoneState (CANDIDATE, FRESH, TESTED, BREAK_PENDING, BROKEN_CONFIRMED, FLIPPED, FROZEN, EXPIRED)
- Polarity (SUPPORT, RESISTANCE)
- Tier (S, A, B, C)
- SetupType (PULLBACK, BREAKOUT_RETEST, REJECTION_FAKEOUT, RANGE_FADE)
- LegOrigin (NORMAL, RETRO, BAND_WALK_SUB_LEG)
- GovState (NORMAL, CAUTION, DEFENSIVE, HALTED)

Create src/core/constants.py with EPSILON = Decimal('1E-9') and other constants from Doc 1 §4.

Create __init__.py files in all src/ subdirectories.

Write basic tests in tests/unit/test_types.py to verify all types can be created.
Run the tests.
```

### Session 2: Bar Validator
**Copy-paste this:**
```
Read docs/doc2-data.md section 12 (Data Validation Engine).

Implement src/data/bar_validator.py:
- validate_bar(bar: Bar) -> Bar that checks: high >= low, close within range, no negative volume, no NaN/Inf
- Raise DataIntegrityError (create in src/core/exceptions.py) on failure
- Warn (don't halt) if range > 5x recent ATR (spike flag from Doc 2 §12)
- Filter function: is_usable(bar) -> bool that checks is_complete == True

Write tests in tests/unit/test_bar_validator.py with:
- Valid bar passes
- high < low fails
- Negative volume fails
- Incomplete bar filtered
- Spike detection warning

Run all tests.
```

### Session 3: Timeframe Aggregator
**Copy-paste this:**
```
Read docs/doc2-data.md sections 4 and 5 (Timeframe Aggregation and Missing Data).
Also check docs/amendment-v1.1.md section D1 (Tiered Data Architecture).

Implement src/data/aggregator.py:
- aggregate_bars(bars_15m: list[Bar], target_tf: Timeframe) -> list[Bar]
- Tier 1 only: 15m → 1H (4 bars), 4H (16 bars), 1D (96 bars)
- Rules from Doc 2 §4.2: open=first, high=max, low=min, close=last, volume=sum
- Alignment from Doc 2 §4.3: 1H at :00, 4H at 00/04/08/12/16/20, 1D at 00:00 UTC
- Completion from Doc 2 §4.4: HTF bar usable only when last 15m child closes
- Missing data policy from Doc 2 §5: >25% missing = discard, 10-25% = unreliable, <10% = fill

Write thorough tests including:
- 4 bars aggregate to correct 1H candle
- Alignment boundary handling
- Missing bar scenarios
- Incomplete bar filtering

Run all tests.
```

### Session 4: ATR Computation
**Copy-paste this:**
```
Read docs/doc2-data.md section 7 (ATR Specification).
Read docs/amendment-v1.1.md section C5 (Gold Gap Handling).

Implement src/data/atr.py:
- compute_true_range(bar: Bar, prev_close: Decimal, is_gap_bar: bool = False) -> Decimal
  Doc 2 §7.1: TR = max(H-L, |H-prev_C|, |L-prev_C|)
  C5: For Gold gap bars, prev_close must be last_real_close, not synthetic fill
- compute_atr(bars: list[Bar], period: int = 14) -> list[Decimal]
  Doc 2 §7.2: First ATR = simple mean of first 14 TRs
  Doc 2 §7.3: ATR[i] = (ATR[i-1] × 13 + TR[i]) / 14
- Store with 6 decimal precision (Doc 2 §7.5)

Implement src/data/gap_handler.py:
- detect_gap(bar: Bar, prev_bar: Bar, instrument: str) -> bool
  Gold: gap if time difference > 2x bar period
  BTC: exempt (24/7 continuous)
- get_last_real_close(bars: list[Bar]) -> Decimal

Write tests including:
- ATR matches manual calculation to 6 decimals
- Gold Monday gap bar uses Friday close (not synthetic)
- BTC has no gaps
- Edge case: first 14 bars initialization

Run all tests. This is critical — ATR feeds everything else.
```

---

## PHASE 2: ZLBB & Leg Detection (Week 2)

### Session 5: ZLEMA & ZLBB Bands
**Copy-paste this:**
```
Read docs/doc3-zlbb.md sections 1-5 (ZLBB math).
Read docs/doc2-data.md section 11 (Bandwidth Percentile).

Implement src/indicators/zlema.py:
- compute_zlema(closes: list[Decimal], period: int = 20) -> list[Decimal]
  Doc 3 §3: lag = floor((period-1)/2), lag_adjusted = close + (close - close[lag])
  EMA with alpha = 2/(period+1) applied to lag_adjusted prices

Implement src/indicators/zlbb.py:
- compute_zlbb(bars: list[Bar], period: int = 20, mult: Decimal = Decimal('2')) -> list[ZLBBState]
  Doc 3 §4: sigma = stddev(close, period), bands = ZLEMA ± mult*sigma
  Doc 3 §5: bandwidth = (upper - lower) / ZLEMA

Implement src/indicators/bandwidth_percentile.py:
- compute_percentile(bandwidths: list[Decimal], window: int = 100) -> Decimal
  Doc 2 §11: Rolling percentile rank, recompute on 4H close only

Write tests comparing output to manually computed values for 50 bars.
Run all tests.
```

### Session 6: Leg Detection Engine
**Copy-paste this:**
```
Read docs/doc3.1-leg-engine.md (the AUTHORITATIVE source for leg detection).
Read docs/amendment-v1.1.md section C1 (Band Walk Extension).
Read docs/amendment-v1.2.md sections P1 (Band Walk Gate) and P6 (Leg Quality Score).

Implement src/legs/leg_engine.py:
- LegStateMachine class with states: SEEKING, IN_BULL_LEG, IN_BEAR_LEG
- Use explicit transition dict, not if/elif chains
- process_bar(bar, zlbb_state) -> list[Leg]
- Full expansion reversal model from Doc 3.1

Implement src/legs/band_walk.py:
- Band walk detection: 3+ consecutive closes outside same band
- 25% retracement threshold for reversal permission
- P1: Band walk gate that SUSPENDS normal completion checks

Implement src/legs/leg_quality.py:
- compute_quality(leg_data) -> int (0-4 score from P6)
- Components: displacement >= 1 ATR (+1), bar_count >= 5 (+1),
  efficiency >= 0.35 (+1), decisive close (+1)

Implement src/legs/spike_filter.py:
- E1: Single-bar spike detection (touches both ±1σ in one bar)
- Spikes NOT recorded as legs, but extremes become rejection SR points

Write comprehensive tests. Test band walk does NOT terminate on standard -1σ touch.
Run all tests.
```

---

## PHASE 3: SR System (Week 3-4)

### Session 7: SR Zone Sources
**Copy-paste this:**
```
Read docs/doc4-sr-system.md sections 1-4.
Read docs/amendment-v1.1.md section C7 (Midpoint SR).
Read docs/amendment-v1.2.md section P12 (Band Walk Sub-Leg SR).

Implement src/sr/sources.py:
- Method 1: Leg extremes (Doc 4 §4.1) — each completed leg's high/low
- Method 2: Open/close cluster rejection (Doc 4 §4.2)
- Method 3: Midpoint SR (C7) — when leg displacement >= 2×ATR
- Method 4: Spike rejection points (E1)
- Band walk sub-leg SR policy (P12): only if displacement >= 1 ATR and bar_count >= 3

Write tests for each source method.
Run all tests.
```

### Session 8: Clustering, Zone Width, Min Gap
**Copy-paste this:**
```
Read docs/doc4-sr-system.md section 6 (Clustering & Merging).
Read docs/amendment-v1.1.md sections M5 (Zone Width) and C7 (Min Gap).

Implement src/sr/clustering.py:
- cluster_zones(zones, atr, threshold=Decimal('0.25')) -> list[SRZone]
- Weighted average merge by displacement strength (Doc 4 §6.4)
- Sequential scan, deterministic (sort ascending first)

Implement src/sr/zone_width.py:
- compute_zone_width(center, atr) -> tuple[Decimal, Decimal]
- M5: half_width = clamp(0.15 × ATR, min_width, max_width)

Implement src/sr/min_gap.py:
- enforce_min_gap(zones, price, atr) -> list[SRZone]
- C7: min distance = max(0.40% price, 0.25 × ATR)
- Weaker zone merges into stronger

Write tests. Run all tests.
```

### Session 9: Zone Lifecycle FSM
**Copy-paste this:**
```
Read docs/amendment-v1.2.md section P11 (Complete State Transition Diagram).
Read docs/amendment-v1.1.md sections C2, C3, C4.
Read docs/amendment-v1.2.md sections P2 (BROKEN persistence) and P3 (flip bonus).

Implement src/sr/lifecycle.py:
- ZoneLifecycleFSM class with explicit transition dict from P11
- States: CANDIDATE, FRESH, TESTED, BREAK_PENDING, BROKEN_CONFIRMED, FLIPPED, FROZEN, EXPIRED
- C3 breakout: 4 consecutive closes beyond boundary + epsilon (P7)
- P2: BROKEN_CONFIRMED persists >= 1 bar before FLIPPED transition
- C3 failed breakout: 2 closes back inside within 4-bar window
- P3: frozen_touch_bonus set once on FLIPPED, never modified

Implement src/sr/strength.py:
- compute_strength(zone) -> int using C4 modifiers
- Lifecycle modifiers: FRESH/TESTED +0 base, +1 per touch (cap +3)
- FLIPPED: +2 base, frozen_touch_bonus carries, post-flip touches separate

Write tests for every state transition in P11 table.
Run all tests.
```

### Session 10: SR Tiers & Density
**Copy-paste this:**
```
Read docs/doc4-sr-system.md tier classification section.
Read docs/amendment-v1.2.md section P10 (Density Quantified).

Implement src/sr/tier.py:
- assign_tier(zone) -> Tier based on strength score
- S, A, B, C classification

Implement src/sr/density.py:
- compute_density_bias(zones, current_price, atr) -> tuple[int, int]
- P10: supply_weight vs demand_weight within 2×ATR
- Weighted by tier: S=3, A=2, B=1
- Returns (bull_modifier, bear_modifier)

Implement src/sr/midpoint.py:
- C7: CANDIDATE validation (2 rejections or 5 consecutive candles)
- 50 LTF bar expiry for unvalidated candidates

Write tests. Run all tests.
```

---

## PHASE 4: Phase & Setup Engine (Week 4-5)

### Session 11: Phase Scoring
**Copy-paste this:**
```
Read docs/doc5-phase-engine.md.
Read docs/amendment-v1.1.md section C6 (Phase Quantification).
Read docs/amendment-v1.2.md section P5 (Balance Score Quantified).

Implement src/phase/scorer.py with 5 scoring functions:
- score_trend_bull(legs, sr_map, zlbb) -> int (C6 quantified conditions)
- score_trend_bear(legs, sr_map, zlbb) -> int (mirror)
- score_balance(legs, sr_map) -> int (P5 — 6 quantified conditions)
- score_distribution(legs, sr_map) -> int (C6 conditions)
- score_accumulation(legs, sr_map) -> int (mirror)

Each uses rolling 5-leg window. All conditions must be numeric thresholds.
Include P10 density modifiers and exhaustion penalty from P10.

Write tests with synthetic leg data for each phase.
Run all tests.
```

### Session 12: Phase Stickiness, Override, Minimum Legs
**Copy-paste this:**
```
Read docs/doc5-phase-engine.md sections 12-13.
Read docs/amendment-v1.1.md section M1 (Phase Stickiness).
Read docs/amendment-v1.2.md section P8 (Same-Phase Consecutive Check).
Read docs/amendment-v1.1.md section E4 (Minimum Legs Before Phase Trusted).

Implement src/phase/stickiness.py:
- M1 + P8: Phase label changes only when SAME new phase leads for 2 consecutive 1H closes
- Track pending_new_phase and consecutive_count

Implement src/phase/htf_override.py:
- Doc 5 §13: If analysis TF strongly opposite (>= +20 margin), restrict lower TF

Implement src/phase/minimum_legs.py:
- E4: If completed_legs < 3, force TRANSITION, suspend Pullback/Range Fade

Write tests. Phase should NOT flip-flop.
Run all tests.
```

### Session 13-14: Setup Engine (4 Setups)
**Copy-paste this for Session 13:**
```
Read docs/doc6-setup-engine.md.
Read docs/amendment-v1.1.md sections C2 (BROKEN zone exception), M3 (Bear Pullback), M8 (Setup 5 removed).

Implement src/setups/pre_filter.py:
- C2: BROKEN zones eligible ONLY for Setup 2
- Global pre-filter: zone tier, lifecycle, proximity

Implement src/setups/pullback.py:
- Setup 1: Pullback Continuation (Doc 6 §6 + M3)
- Allowed phases: TREND_BULL, TREND_BEAR
- Precondition, trigger, confirmation, candidate creation, expiry

Implement src/setups/breakout_retest.py:
- Setup 2: Breakout Retest (C3 + P4 allowed phases)
- Allowed phases: TREND_BULL, TREND_BEAR, TRANSITION (50% size)
- Must occur within 2 completed 1H legs after BROKEN_CONFIRMED

Write tests for each setup. Run all tests.
```

**Copy-paste this for Session 14:**
```
Read docs/doc6-setup-engine.md section for rejection setups.
Read docs/amendment-v1.2.md section P9 (Range Fade Full Specification).

Implement src/setups/rejection_fakeout.py:
- Setup 3: Rejection/Reclaim (Doc 6.1 fakeout setups)
- Zone reclaim model

Implement src/setups/range_fade.py:
- Setup 4: Range Fade (P9 — fully specified)
- BALANCE phase only
- Full precondition, trigger, confirmation (3 methods), candidate, stop, target, expiry, invalidation

Implement src/setups/candidate.py:
- SetupCandidate creation with R:R calculation
- M6: expected_R = target_distance / stop_distance
- Minimum R:R gate: 2.0 for TREND, 1.5 for BALANCE

Write tests. Range Fade must ONLY fire in BALANCE phase.
Run all tests.
```

---

## PHASE 5: Execution & Portfolio (Week 5-6)

### Session 15: Entry & Risk Gate
**Copy-paste this:**
```
Read docs/doc7-entry-risk.md.

Implement src/execution/entry_trigger.py:
- Doc 7 §3: Entry only on candle CLOSE, never intrabar
- close >= entry_reference + EPSILON for longs

Implement src/execution/risk_gate.py:
- Doc 7 §8: Final authority. Reject if R:R insufficient, stop > 1.5 ATR,
  portfolio risk > cap, system paused

Implement src/execution/position_sizer.py:
- Doc 7 §7: size = account_risk / abs(entry - stop), rounded DOWN

Write tests. Run all tests.
```

### Session 16: Exit Manager & Order Manager
**Copy-paste this:**
```
Read docs/doc7-entry-risk.md sections 11-14.
Read the execution engine sections from the addendum.

Implement src/execution/exit_manager.py:
- 3-tier exit: 40/40/20 allocation
- Tier 1: first target (mechanical)
- Tier 2: second target or trailing conversion
- Tier 3: runner with trailing stop
- Breakeven at +1R

Implement src/execution/order_manager.py:
- Market orders for momentum setups
- Limit orders for mean reversion (cancel after 3 bars)
- Slippage tolerance: 0.5 ATR max

Write tests. Run all tests.
```

### Session 17: Portfolio & Conflict Resolution
**Copy-paste this:**
```
Read docs/doc8-portfolio.md and docs/doc11-conflict.md.
Read docs/amendment-v1.1.md section E5 (Correlation Removed).

Implement src/portfolio/allocator.py:
- Ranking engine: score and select best candidates
- Max 5 positions system-wide, 1 per instrument per direction

Implement src/portfolio/conflict_resolver.py:
- Doc 11 filter stages (amended by C4 — lifecycle now in strength score)
- Priority scoring from addendum: setup_tier + sr_tier + quality + confluence

Implement src/portfolio/pyramiding.py:
- Max 2 adds, 50% size on pyramids
- Only if >= 1R profit, same phase, no degradation

Write tests. Run all tests.
```

---

## PHASE 6: Governance & Persistence (Week 6-7)

### Session 18: Governance & Safety
**Copy-paste this:**
```
Read docs/doc10-governance.md.

Implement src/governance/state_machine.py:
- 4 states: NORMAL → CAUTION → DEFENSIVE → HALTED
- Transition triggers from Doc 10 §7

Implement src/governance/drift_detector.py:
- Doc 10 §5: expectancy drift, winrate collapse, drawdown spike

Implement src/governance/pause_conditions.py:
- 5 pause triggers from the execution addendum
- Volatility shock, whipsaw, consecutive stops, drawdown, data quality

Implement src/governance/daily_limits.py:
- Doc 10 §13: daily -3R limit, monthly kill switch

Write tests. Run all tests.
```

### Session 19: Persistence & Restart
**Copy-paste this:**
```
Read docs/doc9-persistence.md.
Read docs/amendment-v1.2.md section P13 (Warmup Buffer).
Read docs/amendment-v1.1.md section E3 (Minimum History).

Implement src/persistence/state_snapshot.py:
- Save full system state after each cycle

Implement src/persistence/rebuild.py:
- Full restart rebuild from raw 15m bars
- P13: 60 bars minimum per TF for warmup

Implement src/persistence/hash_cert.py:
- Hash state after rebuild, compare with previous
- Mismatch = halt trading

Implement src/persistence/event_ledger.py:
- Append-only log of all decisions

Write tests. Kill process mid-cycle, restart, verify hash matches.
Run all tests.
```

---

## PHASE 7: Integration & Testing (Week 7-8)

### Session 20: Pipeline Wiring
**Copy-paste this:**
```
Read docs/doc1-constitution.md section 5 (Execution Order).

Implement src/engine/pipeline.py:
- TradingPipeline class with run_cycle(bar, instrument_state)
- 13 steps in exact order from Doc 1 §5
- Wire all modules together

Implement src/engine/clock.py:
- 15m candle close trigger

Implement src/engine/instrument_runner.py:
- Per-instrument state container

Write an integration test that feeds 100 bars through the full pipeline.
Run all tests.
```

### Session 21: Backtest Runner
**Copy-paste this:**
```
Read docs/doc12-research.md section 2 (Same Logic as Live).

Implement scripts/backtest.py:
- Uses the SAME TradingPipeline class as live (Doc 12 §2)
- Feeds historical bars one at a time
- No look-ahead bias

Implement scripts/validate_determinism.py:
- Run pipeline twice on same data
- Hash every output at every step
- Any diff = FAIL

Run determinism validation on 500 synthetic bars.
```

### Session 22: Docker & Telegram
**Copy-paste this:**
```
Create docker-compose.yaml with:
- TimescaleDB service
- Engine service
- Ingester service

Create Dockerfile for the engine.

Implement src/ingestion/binance_ws.py (BTC WebSocket listener)
Implement src/notifications/telegram.py (signal delivery)

Create config/system_config.yaml and config/instruments/btc_usdt.yaml

Test Docker build locally.
```

---

## Tips

- **One session = one prompt above.** Don't combine sessions.
- **If Claude Code's context fills up:** Type `/clear` and re-paste the session prompt.
- **If tests fail:** Just say "Run the tests and fix the failures."
- **If you're confused:** Say "Explain what you just did in simple terms."
- **To check progress:** Say "Read docs/session-guide.md and tell me which sessions are done based on what files exist."
- **Always commit working code** before starting a new session.
