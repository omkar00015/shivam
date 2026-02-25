# Project Memory — Prasad Algo Trading System

## Project Layout
- Spec docs: `docs/doc2-data.md`, `docs/amendment-v1.1.md`, etc.
- Session 1 scope uses `src/data_ingest/` (per CLAUDE_CODE_GUIDE.md), NOT `src/ingestion/`
- `src/core/`, `src/data/`, etc. are all placeholder stubs — not yet implemented

## Key Implementation Facts

### bar_aggregator.py bug trap
- `missing_frac` must use `state.filled_count` (synthetic bars added), NOT
  `expected_count - len(state.bars)`. After gap-fill, `len(state.bars)` == expected,
  making computed missing = 0 and masking the is_reliable=False case.

### Doc 2 rules in force
- OHLCV: open=first, high=max, low=min, close=last, volume=sum (§4.2)
- Alignment: accumulate only from valid boundary starts; mid-cycle arrivals discarded (§4.3)
- Completion: emit only when next_bar_start >= window_end_expected (§4.4)
- Missing <10%: fill + is_reliable=True; 10-25%: fill + is_reliable=False; >25%: discard (§5)

### Amendment v1.1 rules
- C5: BTC is 24/7 continuous → fill missing 1m bars. Gold: real session gaps, no fill.
- D1: 1W/1M/3M ideally fetched natively from Binance (not aggregated from 1m stream).

### AggregatedBar vs Bar
- `Bar` (binance_ws.py) has no `is_reliable` field — that's aggregator-layer metadata.
- `AggregatedBar` (bar_aggregator.py) adds `is_reliable: bool`.
- Always frozen dataclasses, all prices Decimal, timestamps UTC-aware.

### atr.py seed-phase off-by-one trap
- Doc 2 §7.2 says "simple mean of first 14 TRs". Bar 1 has no prev_close.
- WRONG approach: skip TR on bar 1 → 13 TRs after bar 14 → first ATR on bar 15.
- CORRECT: bar 1 TR = high - low (industry standard when no prev_close). Bars 1-14
  give 14 TRs → first ATR on bar 14. Acceptance criteria "14th bar returns ATR" ✓.

### ATR gap-aware TR (Amendment C5)
- Gold: _resolve_prev_close() checks _is_session_gap() (timestamp > prev_end + 1min).
  On gap: returns _last_real_close (never a synthetic fill; Gold has none anyway).
- BTC: _resolve_prev_close() always returns _prev_close. No gap detection.
- _record_bar_state() always sets _last_real_close = bar.close (real bars only).

### zlbb.py engine design trap
- ZLBBEngine NEVER returns to SEEKING once first leg starts; it continuously cycles
  IN_BULL_LEG ↔ IN_BEAR_LEG. Tests must NOT assert SEEKING after any non-seed bar.
- Use _seed_engine() (29 flat close=100 bars, sigma=0 → state=None) to keep engine in
  SEEKING until ready for the actual test stimulus.
- Band walk: upper_band (2σ) grows as high closes enter the 20-bar sigma window.
  Using static `initial_upper_band + small` will fail because sigma expands to exceed close.
  To reliably trigger band walk, use escalating closes (200, 400, 600) combined with
  eligible=False (blocks normal completion) so only the 3rd bar fires via bw_exit.
- _bw_exit_bear fires when band_walk_active=True AND retracement>=25% AND close>=upper_1sigma.
  Does NOT check eligible. So even with eligible=False, once band_walk_active=True, exit fires.

### zlbb.py atr_at_start trap
- atr_at_start is stamped when leg opens (from atr_calc.frozen_atr at that moment).
  Never changes for the SAME leg object. But after leg completes a NEW leg opens with
  a fresh atr_at_start. Tests checking atr_at_start must keep the same leg open.

### ZLEMA warmup: lag=9, period=20
- ZLEMA needs lag+period = 9+20 = 29 bars before seeding. First valid index = 28 (0-indexed).
- With flat closes (sigma=0), ZLBBState is None → engine stays in SEEKING.
- Use n=29 flat bars to pre-seed, then first non-flat bar triggers both ZLEMA output and
  leg start simultaneously.

### zone_detector.py design traps
- **FROZEN vs EXPIRED simultaneous trigger**: when bars_since_touch hits 100 AND price is far
  (> 5×ATR from center), zone goes EXPIRED not FROZEN. FROZEN is a transient state only reached
  when price is still nearby. Tests checking FROZEN must keep price within 5×ATR of zone center.
- **BROKEN_CONFIRMED persistence (P2)**: bars_since_confirmed must be ≥ 1 before FLIPPED.
  BROKEN_CONFIRMED is observable for at least 1 full bar (not a transient state).
- **Clustering uses Doc 4 §6 threshold (0.5×ATR(1H))**, NOT Addendum B (0.25×ATR). Authority:
  Doc 4 overrides Addendum B.
- **Zone width (M5)**: computed ONLY after clustering + min-gap pass. Raw center stored first,
  widths computed last in _rebuild_zones().
- **CANDIDATE zones skipped in clustering**: CANDIDATE zones sit outside the clustering pass.
  They get ATR-based default width; only validated (FRESH+) zones enter _cluster_merge().
- **FLIPPED + touch**: FLIPPED zones behave as FRESH/TESTED of new polarity. They go through
  _step_fresh_or_tested() which handles touch_count increment and BREAK_PENDING transition.
- **Density uses B-TIER+ only**: C-tier zones are excluded from DensityBias computation
  (Addendum B §B3.8). Only FRESH/TESTED/FLIPPED/BREAK_PENDING states count.

### Authority hierarchy for SR system
- Doc 4 §6 (0.5×ATR(1H)) overrides Addendum B (0.25×ATR) for clustering threshold
- Amendment v1.1 M5 (§7 formula) overrides §5 for zone width; §5 is RETIRED
- Amendment v1.1 M4: breakout confirmation uses 15m candles (not native TF bars)
- P7 (breakout_epsilon = 0.02 × ATR(15m)): filters noise closes at boundary
- P2 (BROKEN_CONFIRMED ≥ 1 bar): ensures Setup 2 can arm before FLIPPED
- P3 (frozen_touch_bonus): set once on FLIPPED, never modified, = min(pre_flip_count, 3)
- Addendum B used only for: rejection counting (B3.3), density (B3.8)

### phase_engine.py design traps
- **TREND_BULL/BEAR max=100, 8 conditions each**: +20 HH+HL, +15 majority, +15 avg eff, +10 pullbacks,
  +10 ZLEMA majority (last 20 1H bars), +10 band walk recent (is_band_walk=True in last 10 legs),
  +10 breaks succeeded (>=2 legs crossed zone boundary), +10 rejection failed (>=2 small legs at zone).
- **DensityBias enum NOT a direct scorer input**: density_bias param on scorers is passed through
  but used only in BALANCE(+15 NEUTRAL), DISTRIBUTION(+20 ABOVE), ACCUMULATION(+20 BELOW).
  TREND_BULL/BEAR do NOT have a density_bias direct condition — P10 modifier applied separately in score_all().
- **Band walk uses CompletedLeg.is_band_walk**: ZLBBState has no band_walk_active field.
  Bull band walk = any leg in last 10 with is_band_walk=True AND direction==BULL.
- **Balance scoring: "no high efficiency" fires for BOTH alternating AND non-alternating legs**
  when all efficiencies <= 0.65. The +25 alternation bonus is independent of other conditions.
  Tests must compare alternating vs non-alternating DELTA, not absolute threshold.
- **Density modifier is computed from zones (P10 weighted zones), NOT from DensityBias enum**.
  DensityBias enum is used only for the density-specific +10/+5 conditions in each scorer.
  P10 _compute_density_modifier() works directly from zones list.
- **score_only() must NOT call _advance_stickiness()** — it's a pure query with no side effects.
- **is_1h_close=False by default**: stickiness FSM must not advance on 15m bars.
- **P8 reset logic**: if current_winner == active_phase (winning phase is already active),
  both pending_phase and consecutive_count reset to 0/None immediately.
- **Confidence uses ACTIVE phase score**, not winning phase score, vs second-best.
  Formula: active_score / (active_score + second_best_score).

### Phase scoring authority hierarchy
- User prompt conditions OVERRIDE doc5/C6 where they differ (user is authoritative)
- P10: density modifier threshold = 3 weighted points (supply vs demand)
- P10 §15: exhaustion penalty = -10 when quality<=1 AND efficiency<0.35
- M1: label changes only after 2 consecutive 1H closes; scores update every 15m
- P8: same DIFFERENT phase must win BOTH 1H closes; if same active phase wins → reset pending

## Files Completed
- `src/data_ingest/__init__.py`
- `src/data_ingest/binance_ws.py` — Binance WS 1m kline, reconnect, Decimal
- `src/data_ingest/bar_aggregator.py` — 1m→15m/1H/4H/1D/1W/1M/3M aggregator
- `src/indicators/atr.py` — Wilder ATR(14), Gold gap-aware TR, freeze/unfreeze API
- `src/indicators/zlbb.py` — ZLEMA(lag=9), sigma bands, leg FSM (Doc 3.1), band walk (C1/P1), quality (P6)
- `src/sr/zone_detector.py` — SR zone detector (sources, clustering, lifecycle FSM, zone width, density)
- `src/phase/phase_engine.py` — Market phase engine: 5 scorers + stickiness FSM (M1/P8/P10)
- `tests/unit/test_atr.py` — 24 tests, all passing
- `tests/unit/test_zlbb.py` — 31 tests, all passing
- `tests/unit/test_zone_detector.py` — 70 tests, all passing (125 total passing)
- `tests/unit/test_phase_engine.py` — 52 tests, all passing (170 total passing)
