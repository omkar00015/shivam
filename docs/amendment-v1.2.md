**SYSTEM AMENDMENT DOCUMENT**

Patch v1.2 \| Deterministic Multi-Timeframe Algo Trading System

February 2026 \| Fixes gaps identified in v1.1 review

Amendment Index

  --------- -------------- -------------- --------------------------------------------------------- ---------
  **ID**    **Severity**   **Affects**    **One-Line Summary**                                      **Ref**

  **P1**    **CRITICAL**   Doc 3.1, C1    Band walk gate missing from Doc 3.1 leg completion        1a

  **P2**    **CRITICAL**   C3, C2         BROKEN_CONFIRMED minimum persistence before FLIPPED       1b

  **P3**    **MODERATE**   C4             Pre-flip touch bonus carry-through on FLIPPED clarified   1c

  **P4**    **MODERATE**   Doc 6 §7.1     BREAKOUT phase reference mapped to TREND\_\*              1d

  **P5**    **CRITICAL**   Doc 5 §6       Balance score fully quantified (was missed in C6)         2a

  **P6**    **MODERATE**   Doc 3.1, E1    Leg quality score formally defined                        2b

  **P7**    **DESIGN**     C3             Minimum breakout distance for close threshold             2c

  **P8**    **MODERATE**   M1             Phase stickiness: same-phase consecutive check rule       2d

  **P9**    **CRITICAL**   Doc 6 §11      Range Fade (Setup 4) fully specified                      3a

  **P10**   **MODERATE**   Doc 5 §14-15   Density & exhaustion integration quantified               3b

  **P11**   **DESIGN**     Doc 4 §8       Full zone lifecycle state transition diagram              3c

  **P12**   **MODERATE**   C1             Band walk sub-leg SR emission policy                      3e

  **P13**   **DESIGN**     E3             Warmup buffer increased to 60 bars                        4a

  **P14**   **DESIGN**     D1             Cross-validation EPSILON defined                          4c
  --------- -------------- -------------- --------------------------------------------------------- ---------

P1 \| Band Walk Gate in Doc 3.1 Leg Completion

**CRITICAL** \| Affects: Doc 3.1 §6, Amendment C1

Problem

Amendment C1 defines band walk extension rules (25% retracement threshold, sub-leg boundaries at ZLEMA crosses, band walk exit conditions). However, Doc 3.1 §6 leg completion conditions always require bar.low \<= lower_1sigma AND bar.close \< ZLEMA (for bull legs). There is no gate in Doc 3.1 that suspends these completion conditions when band_walk_active == True. This means the standard completion logic will terminate a bull leg on any pullback that touches -1σ during a band walk, even if the 25% retracement threshold has not been met.

Amended Rule --- Doc 3.1 §6 (New Gate)

> BAND WALK COMPLETION GATE (insert before §6.1 and §6.2):
>
> IF band_walk_active == True:
>
> SUSPEND normal leg completion checks (§6.1 / §6.2).
>
> Instead, leg completion is governed by C1 Band Walk Exit:
>
> BAND WALK EXIT (leg completes) when ALL of:
>
> retracement \>= 25% of leg displacement since leg start
>
> AND close beyond opposite 1σ band
>
> AND close \< ZLEMA (for bull) or \> ZLEMA (for bear)
>
> IF band_walk_active == False:
>
> Normal §6.1 / §6.2 applies unchanged.
>
> TRANSITION: When band walk exit triggers leg completion,
>
> band_walk_active is set to False.
>
> State transitions to opposite leg per §6.1/§6.2 transition rules.
>
> This gate ensures band walk mode is not silently overridden
>
> by the standard -1σ completion check during extended trends.

P2 \| BROKEN_CONFIRMED Minimum Persistence

**CRITICAL** \| Affects: C3 Breakout Rule, C2 Setup 2 Eligibility

Problem

C3 defines BROKEN_CONFIRMED as 4+ consecutive closes beyond the zone boundary, after which the zone transitions to FLIPPED. In fast markets, the 4th confirmation close and an immediate price retest can occur on consecutive bars, allowing a state transition from BREAK_PENDING → BROKEN_CONFIRMED → FLIPPED in effectively zero time. The BROKEN_CONFIRMED state becomes unobservable, and Setup 2 (Breakout Retest) which requires BROKEN_CONFIRMED or FLIPPED as precondition, has no time window to arm.

Amended Rule

> BROKEN_CONFIRMED PERSISTENCE (amend C3):
>
> When 4th consecutive close confirms breakout:
>
> state = BROKEN_CONFIRMED
>
> confirmed_at = current_bar_timestamp
>
> Setup 2 watch window OPENS immediately.
>
> Transition to FLIPPED requires:
>
> state == BROKEN_CONFIRMED
>
> AND bars_since_confirmed \>= 1
>
> (i.e., at least 1 additional 15m bar must close after confirmation)
>
> On FLIPPED transition:
>
> Polarity inverts.
>
> Setup 2 remains eligible (C2 allows both BROKEN_CONFIRMED and FLIPPED).
>
> The 2-leg retest time window (C3) starts from confirmed_at.
>
> RATIONALE: Without this, the BROKEN_CONFIRMED state is transient
>
> and Setup 2 can never arm in volatile breakout scenarios.

P3 \| Pre-Flip Touch Bonus Carry-Through

**MODERATE** \| Affects: C4 Part 2 Strength Modifier

Problem

C4\'s worked example shows a zone accumulating +2 touch bonus pre-flip, then retaining that bonus after the FLIPPED transition (+2 flip bonus + 2 prior touches = +4 from lifecycle). However, C4 also states touch_count resets to 0 post-flip for the new polarity. This creates ambiguity: the +2 pre-flip touch bonus appears to persist as a permanent addition to base strength, but there is no explicit rule stating this.

Amended Rule

> TOUCH BONUS ON FLIP (clarification to C4 Part 2):
>
> When a zone transitions to FLIPPED:
>
> 1\. Pre-flip touch bonus is FROZEN into the zone\'s strength permanently.
>
> frozen_touch_bonus = min(touch_count_pre_flip, 3)
>
> This value does NOT decay and does NOT reset.
>
> 2\. touch_count resets to 0 for the new polarity.
>
> Post-flip touches accumulate a SEPARATE bonus, capped at +3.
>
> 3\. FLIPPED base bonus of +2 is applied as per C4.
>
> 4\. Total lifecycle modifier =
>
> frozen_touch_bonus + flip_bonus(+2) + post_flip_touch_bonus
>
> Maximum theoretical = 3 + 2 + 3 = 8
>
> IMPLEMENTATION NOTE:
>
> Store frozen_touch_bonus as a separate field on the zone object.
>
> It is set once on FLIPPED transition and never modified again.

P4 \| BREAKOUT Phase Reference Resolved

**MODERATE** \| Affects: Doc 6 §7.1

Problem

Doc 6 §7.1 lists \'TREND or BREAKOUT\' as allowed phases for Setup 2 (Breakout Retest). Doc 5 defines five phases: TREND_BULL, TREND_BEAR, BALANCE, DISTRIBUTION, ACCUMULATION. There is no BREAKOUT phase. This is a dangling reference from an earlier design iteration.

Amended Rule

> SETUP 2 ALLOWED PHASES (replace Doc 6 §7.1):
>
> Setup 2 (Breakout Retest) is permitted in:
>
> TREND_BULL (for bullish breakout retests)
>
> TREND_BEAR (for bearish breakout retests)
>
> TRANSITION (with 50% size reduction per Doc 5 §11)
>
> Setup 2 is NOT permitted in: BALANCE, DISTRIBUTION, ACCUMULATION.
>
> RATIONALE: Breakout retests are continuation trades.
>
> They require directional conviction from the phase engine.
>
> BALANCE/DIST/ACCUM phases imply range-bound conditions
>
> where retesting a broken boundary is likely to fail.

P5 \| Balance Score --- Full Quantification

**CRITICAL** \| Affects: Doc 5 §6

Problem

Amendment C6 quantified Trend Bull, Trend Bear, Distribution, and Accumulation scores. Balance scoring (Doc 5 §6) was not amended and still uses qualitative phrases: \'overlap heavily\', \'alternating direction frequent\', \'near midpoint majority\', \'both sides defended\'. These are not implementable in a deterministic system.

Amended Balance Score (Doc 5 §6)

  ----------------------------------- ------------ ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **Condition**                       **Points**   **Quantified Rule**

  Last 3 legs overlap heavily         **+20**      For the last 3 completed 1H legs: compute overlap_ratio = length_of_intersection(leg_range\[n\], leg_range\[n-1\]) / min(leg_range\[n\].length, leg_range\[n-1\].length). Must be \>= 0.60 for at least 2 of the 2 consecutive pairs.

  Breakouts fail \>= 2                **+20**      In last 5 completed 1H legs: count of legs that entered BREAK_PENDING on any SR zone but reverted to TESTED (failed breakout per C3) \>= 2.

  Average leg displacement \< 1 ATR   **+15**      mean(displacement) across last 5 completed 1H legs \< 1.0 x ATR(1H, 14).

  Alternating direction frequent      **+15**      In last 5 completed 1H legs: count of direction changes (BULL→BEAR or BEAR→BULL) \>= 3. Minimum = 5 legs required.

  Price near midpoint majority        **+15**      Define range_midpoint = (max_high + min_low) / 2 across last 5 legs. In last 20 x 1H candles: count(abs(close - range_midpoint) \<= 0.50 x ATR(1H)) / 20 \>= 0.50.

  Both sides defended                 **+15**      In last 5 completed 1H legs: at least 1 bull leg ended at/near a resistance zone (within zone boundary) AND at least 1 bear leg ended at/near a support zone. Both must have occurred.
  ----------------------------------- ------------ ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

Max = 100. Scoring uses same rolling 5-leg window as Trend/Distribution/Accumulation.

P6 \| Leg Quality Score --- Formal Definition

**MODERATE** \| Affects: Doc 3.1 §9 Output, Doc 4 §4.1, E1

Problem

Multiple documents reference \'leg quality\' or \'leg_quality\' but no document provides a formal scoring formula. Doc 4 §4.1 uses it for SR base strength (2 + leg_quality). E1 references quality = 0 for spike detection. Addendum A defines retroactive quality parameters but these are distinct from the general quality score.

Leg Quality Score Formula (New --- Doc 3.1 §9.1)

  ---------------------- ------------ --------------------------------------------------------------------------------------------------------------------------------------------------
  **Component**          **Points**   **Condition**

  **Displacement**       **+1**       displacement \>= 1.0 x ATR(15m, 14) at leg start

  **Duration**           **+1**       bar_count \>= 5 (leg lasted at least 5 x 15m bars)

  **Efficiency**         **+1**       efficiency = displacement / cumulative_range \>= 0.35

  **Decisive close**     **+1**       Completion bar close is in the outer 25% of its range in the reversal direction (e.g., for bull leg completing: close in lower 25% of bar range)
  ---------------------- ------------ --------------------------------------------------------------------------------------------------------------------------------------------------

> leg_quality = sum of all satisfied component points.
>
> Range: \[0, 4\].
>
> FROZEN at leg completion. Does not change retroactively.
>
> cumulative_range = sum of abs(bar.high - bar.low) for all bars in the leg.
>
> ATR(15m, 14) is evaluated at the bar where the leg STARTED.
>
> Updated Doc 3.1 §9 Output Object:
>
> Leg {
>
> direction, start_price, end_price, displacement,
>
> bar_count, quality, efficiency
>
> }
>
> E1 ALIGNMENT: A single-bar spike with bar_count=1 touching both
>
> ±1σ will score: displacement +1 (likely large), duration 0,
>
> efficiency 0 (range = displacement for 1 bar, but close is
>
> ambiguous), decisive close 0/1. quality=0 path in E1 triggers
>
> when ALL components fail, which is the normal case for a doji
>
> spike. The dual-sigma-touch rule remains the primary detection.

P7 \| Minimum Breakout Close Distance

**DESIGN** \| Affects: C3 Breakout Confirmation

Problem

C3 breakout confirmation requires \'4 consecutive 15-minute closes beyond zone boundary\'. A close that is 0.01 above zone_high technically qualifies but represents noise, not structural acceptance. In BTC, spread and slippage alone can cause close prices to oscillate by fractions of a dollar around a boundary.

Amended Rule

> BREAKOUT CLOSE THRESHOLD (amend C3):
>
> A candle \'closes beyond zone boundary\' when:
>
> For resistance breakout (bullish):
>
> bar.close \> zone_high + breakout_epsilon
>
> For support breakout (bearish):
>
> bar.close \< zone_low - breakout_epsilon
>
> breakout_epsilon = 0.02 x ATR(15m, 14)
>
> This epsilon applies to:
>
> \- BREAK_PENDING entry (1st close)
>
> \- Each of the 4 consecutive confirmation closes
>
> \- Failed breakout detection (close back inside zone
>
> ignores the epsilon; zone_low \<= close \<= zone_high is \'inside\')
>
> RATIONALE: 2% of ATR(15m) is approximately 1-3 USD on BTC
>
> at typical volatility. This filters noise closes at boundary
>
> without meaningfully delaying real breakout detection.

P8 \| Phase Stickiness --- Same-Phase Consecutive Check

**MODERATE** \| Affects: M1 Phase Stickiness Rule

Problem

M1 requires the new winning phase to lead for 2 consecutive 1H bar closes. If the score leader flips between the two 1H closes (e.g., TREND_BULL leads at 1H close #1, then BALANCE leads at an intermediate 15m recalc, then TREND_BULL leads again at 1H close #2), it is ambiguous whether the consecutive counter resets.

Amended Rule

> PHASE LABEL TRANSITION (clarification to M1):
>
> The 2-consecutive-1H-close check evaluates the SAME phase:
>
> pending_new_phase = None
>
> consecutive_count = 0
>
> On each 1H bar close:
>
> Evaluate all phase scores.
>
> Let current_winner = phase with highest score.
>
> Let margin = current_winner.score - second_best.score
>
> IF current_winner != active_phase AND margin \>= 10:
>
> IF current_winner == pending_new_phase:
>
> consecutive_count += 1
>
> ELSE:
>
> pending_new_phase = current_winner
>
> consecutive_count = 1
>
> IF consecutive_count \>= 2:
>
> active_phase = pending_new_phase
>
> pending_new_phase = None
>
> consecutive_count = 0
>
> ELSE:
>
> pending_new_phase = None
>
> consecutive_count = 0
>
> CRITICAL: The same phase must win at BOTH consecutive 1H closes.
>
> If a different phase wins at the second close, the counter resets.
>
> Intermediate 15m score recalculations do NOT affect this counter.

P9 \| Range Fade (Setup 4) --- Full Specification

**CRITICAL** \| Affects: Doc 6 §11

Problem

Doc 6 §11 (Range Fade) has three lines of specification: allowed phase, trigger, and confirmation. M8 removed Setup 5 (Failed Weakness/Strength) for being \'critically underspecified\'. By the same standard, Range Fade is equally underspecified. An underspecified setup in a deterministic system is a logical contradiction (M8\'s own rationale). Range Fade must be fully specified or removed.

Range Fade --- Full Specification (replace Doc 6 §11)

Allowed Phase

BALANCE only. Setup is suspended in all other phases.

Precondition

> ALL of the following must be true:
>
> phase == BALANCE (with margin \>= 10 over second-best)
>
> zone is at range boundary:
>
> RESISTANCE zone near range high, OR
>
> SUPPORT zone near range low
>
> zone tier: S, A, or B
>
> zone lifecycle: FRESH or TESTED (not BROKEN, FLIPPED, FROZEN, EXPIRED)
>
> distance to zone \<= 2 x ATR(1H) \[standard proximity rule\]

Trigger

> SELL FADE (at resistance):
>
> Price enters resistance zone from below.
>
> bar.high \>= zone_low (price reaches into the zone).
>
> BUY FADE (at support):
>
> Price enters support zone from above.
>
> bar.low \<= zone_high (price reaches into the zone).

Confirmation (one of)

> For SELL FADE at resistance:
>
> A: Rejection bar --- upper wick \>= 50% of candle range
>
> AND close in lower 50% of range.
>
> B: Bearish Engulfing at zone (per M2 definition).
>
> C: 2 consecutive closes moving away from zone_high
>
> (close\[n\] \< close\[n-1\] \< zone_high).
>
> For BUY FADE at support (mirror):
>
> A: Rejection bar --- lower wick \>= 50%, close in upper 50%.
>
> B: Bullish Engulfing at zone.
>
> C: 2 consecutive closes moving away from zone_low.

Candidate Creation

> SELL FADE:
>
> entry_reference = confirmation bar low
>
> stop = zone_high + 0.10 x ATR(15m) buffer
>
> target = nearest opposing support zone (range low boundary)
>
> If no opposing zone: target = entry - 1.5 x stop_distance
>
> BUY FADE:
>
> entry_reference = confirmation bar high
>
> stop = zone_low - 0.10 x ATR(15m) buffer
>
> target = nearest opposing resistance zone (range high boundary)
>
> If no opposing zone: target = entry + 1.5 x stop_distance
>
> Minimum R:R \>= 1.5 (BALANCE phase --- reduced from TREND\'s 2.0).

Expiry

> If not triggered within 5 x 15m bars after candidate creation → cancel.

Invalidation

> SELL FADE invalidated if:
>
> Price closes above zone_high + 0.10 x ATR(15m) (stop level).
>
> Zone transitions to BROKEN or BREAK_PENDING.
>
> Phase changes away from BALANCE.
>
> BUY FADE invalidated if:
>
> Price closes below zone_low - 0.10 x ATR(15m).
>
> Zone transitions to BROKEN or BREAK_PENDING.
>
> Phase changes away from BALANCE.

P10 \| Density & Exhaustion Integration --- Quantified

**MODERATE** \| Affects: Doc 5 §14, §15

Problem

Doc 5 §14 says \'If supply density \> demand by threshold: increase bear-related scores by +10.\' The threshold is undefined. Doc 5 §15 (Exhaustion Integration) uses similarly vague conditions.

Amended §14 --- Density Integration

> DENSITY BIAS MODIFIER (replace Doc 5 §14):
>
> Compute density_ratio for each price region:
>
> supply_count = count of resistance zones within 2 x ATR(1H) above current price
>
> demand_count = count of support zones within 2 x ATR(1H) below current price
>
> Weighted by tier: S = 3, A = 2, B = 1
>
> supply_weight = sum(tier_weight for each resistance zone in range)
>
> demand_weight = sum(tier_weight for each support zone in range)
>
> IF supply_weight \>= demand_weight + 3:
>
> bear_score_modifier = +10
>
> bull_score_modifier = -5
>
> ELIF demand_weight \>= supply_weight + 3:
>
> bull_score_modifier = +10
>
> bear_score_modifier = -5
>
> ELSE:
>
> No modifier applied.
>
> Threshold of 3 weighted points prevents noise flipping.
>
> Modifiers are applied AFTER base score computation, BEFORE normalization.

Amended §15 --- Exhaustion Integration

> EXHAUSTION PENALTY (replace Doc 5 §15):
>
> For TREND_BULL:
>
> Evaluate the most recent bull leg in the last 5 completed 1H legs.
>
> IF that bull leg\'s quality \<= 1 AND efficiency \< 0.35:
>
> Apply penalty: trend_bull_score -= 10
>
> For TREND_BEAR:
>
> Evaluate the most recent bear leg in the last 5 completed 1H legs.
>
> IF that bear leg\'s quality \<= 1 AND efficiency \< 0.35:
>
> Apply penalty: trend_bear_score -= 10
>
> Penalty is applied ONCE per scoring cycle (not cumulative).
>
> If no bull/bear leg exists in the last 5 legs, no penalty applies.

P11 \| Zone Lifecycle --- Complete State Transition Diagram

**DESIGN** \| Affects: Doc 4 §8

Problem

Amendments C2, C3, C4, and M4 add BREAK_PENDING, BROKEN_CONFIRMED, and FLIPPED substates, plus CANDIDATE for midpoint SR (C7). But no document provides a single complete state transition diagram with all trigger conditions. Without this, implementers must cross-reference 4+ amendments to build the FSM.

Complete Zone Lifecycle State Machine

  ---------------------- ----------------------- ------------------------------------------------------------------------------------------------------------------------ -------------------
  **From State**         **To State**            **Trigger Condition**                                                                                                    **Source**

  **CANDIDATE**          **FRESH**               \>= 2 rejections OR \>= 5 LTF candles within zone (midpoint only)                                                        C7

  **CANDIDATE**          **\[discarded\]**       50 LTF bars elapsed without validation                                                                                   C7

  **FRESH**              **TESTED**              First confirmed touch (price enters zone, exits without breaking)                                                        Doc 4 §8

  **FRESH**              **BREAK_PENDING**       1st 15m close beyond zone boundary + epsilon                                                                             C3, P7

  **TESTED**             **TESTED**              Additional touch (touch_count += 1, capped at strength +3)                                                               C4

  **TESTED**             **BREAK_PENDING**       1st 15m close beyond zone boundary + epsilon                                                                             C3, P7

  **BREAK_PENDING**      **BROKEN_CONFIRMED**    4 consecutive 15m closes beyond boundary + epsilon                                                                       C3, M4

  **BREAK_PENDING**      **TESTED**              Failed breakout: \>= 2 consecutive closes back inside zone within 4-bar window (touch_count +1, false_break_count +1)    C3

  **BROKEN_CONFIRMED**   **FLIPPED**             After \>= 1 additional bar: polarity inverts                                                                             C3, P2

  **FLIPPED**            **TESTED (new pol.)**   First touch after flip (treated as normal zone of new polarity)                                                          C2, C4

  **TESTED**             **FROZEN**              Zone has not been touched for frozen_threshold x 1H bars (default: 100)                                                  Doc 4 §8

  **FROZEN**             **EXPIRED**             Zone has not been touched for expired_threshold x 1H bars (default: 200) OR price has moved \> 5 x ATR(native TF) away   Doc 4 §8
  ---------------------- ----------------------- ------------------------------------------------------------------------------------------------------------------------ -------------------

FROZEN and EXPIRED Threshold Definitions (New)

> These thresholds were referenced but never quantified:
>
> FROZEN_THRESHOLD:
>
> Zone has not been touched for \>= 100 bars on its native TF.
>
> Approximately: 100 hours on 1H, \~17 days on 4H.
>
> FROZEN zones are ineligible for all setups.
>
> EXPIRED_THRESHOLD:
>
> Zone has not been touched for \>= 200 bars on its native TF,
>
> OR price has moved \> 5 x ATR(native TF) away from zone center.
>
> EXPIRED zones are permanently removed from the active SR map.
>
> They remain in the historical record for audit only.
>
> Both thresholds are configurable in system config.
>
> Changing them requires a version bump.

P12 \| Band Walk Sub-Leg SR Emission Policy

**MODERATE** \| Affects: C1 Band Walk Extension, Doc 4 §4

Problem

C1 defines sub-leg boundaries at ZLEMA crosses during band walk. Sub-legs have their own extremes (highs/lows between ZLEMA crosses). It is unspecified whether these sub-leg extremes generate SR zones. If they do, band walk periods will flood the SR map with many close-proximity zones (the min-gap filter in C7 would merge most of them, but the computation is wasted). If they don\'t, structural turning points within trends are lost.

Policy Rule

> BAND WALK SUB-LEG SR EMISSION:
>
> Sub-leg extremes during band walk DO NOT automatically create SR zones.
>
> EXCEPTION: A sub-leg extreme creates an SR zone ONLY IF:
>
> sub_leg.displacement \>= 1.0 x ATR(15m, 14)
>
> AND sub_leg.bar_count \>= 3
>
> When created:
>
> origin = BAND_WALK_SUB_LEG
>
> strength = 1 + sub_leg_quality (using P6 formula)
>
> lifecycle = FRESH
>
> Participates in normal clustering and min-gap filtering.
>
> RATIONALE: Most band walk sub-legs are minor pullbacks within
>
> a strong trend. Only sub-legs with meaningful displacement and
>
> duration represent structural reactions worth tracking.
>
> The displacement threshold prevents SR zone flooding.

P13 \| Warmup Buffer Increased to 60 Bars

**DESIGN** \| Affects: E3 Minimum Historical Depth

Problem

E3 specifies 40 total bars (20 ZLBB period + 20 warmup). With period=20, the first 20 bars are EMA initialization using a simple mean. The 21st bar is the first \'real\' ZLEMA output, but sigma (rolling 20-bar std dev) at bar 21 uses 20 close values of which the first \~10 had no stable ZLEMA influence. ATR(14) similarly needs its own warmup. The 40-bar total leaves only 20 bars of genuinely stable output, which is marginal for leg detection (a single leg can span 20+ bars in low volatility).

Amended Minimum Bars

  --------- ----------------- -------------- ------------ --------------- -----------------------
  **TF**    **ZLBB Period**   **Min ZLBB**   **Warmup**   **Total Min**   **Approx History**

  15m       20                20             +40          60              15 hours

  1H        20                20             +40          60              60 hours

  4H        20                20             +40          60              10 days

  1D        20                20             +40          60              60 days

  1W        20                20             +40          60              15 months

  1M        20                20             +40          60              5 years

  3M        20                20             +40          60              15 years
  --------- ----------------- -------------- ------------ --------------- -----------------------

The additional 20-bar warmup ensures ATR, sigma, and ZLEMA are all fully stabilized before the first bar is used for leg detection. For 3M, this requires 15 years of quarterly data which may not be fully available for all instruments. In that case, E3\'s INSUFFICIENT_HISTORY rules apply.

P14 \| Cross-Validation EPSILON Defined

**DESIGN** \| Affects: D1 Cross-Validation Rule

Problem

D1\'s cross-validation rule compares weekly candle close vs Friday 1D candle close with \'abs(\...) \<= EPSILON\'. EPSILON is undefined. Too tight and minor exchange rounding causes false positives. Too loose and real data corruption goes undetected.

Amended Rule

> CROSS-VALIDATION EPSILON (amend D1):
>
> For BTC/USDT:
>
> EPSILON = max(0.01% of current_price, \$1.00)
>
> At \$100,000 BTC: EPSILON = \$10.00
>
> At \$50,000 BTC: EPSILON = \$5.00
>
> For Gold (XAU/USD):
>
> EPSILON = max(0.01% of current_price, \$0.10)
>
> At \$2,000 Gold: EPSILON = \$0.20
>
> RATIONALE: 0.01% accounts for floating-point rounding and
>
> minor exchange tick-size differences between daily and weekly
>
> candle aggregation. The absolute floor prevents EPSILON from
>
> becoming unreasonably small at low price levels.
>
> EPSILON values are stored in system config per instrument.

Amendment Document v1.2 --- End

All 14 patches address gaps identified during v1.1 review. This document should be applied on top of v1.1. No v1.1 amendments are revoked; all P-series patches are additive clarifications, quantifications, or new specifications for previously underspecified components.

**Patch Priority for Implementation:**

  --------------------------- ---------------------- -------------------------------------------------------------------------------------------------------------------------
  **Priority**                **Patches**            **Rationale**

  **BLOCK (before coding)**   P1, P2, P5, P6, P9     These fix logic errors or fill gaps that would cause non-deterministic behavior or dead code paths. Cannot be deferred.

  **BEFORE LIVE**             P3, P4, P8, P10, P11   These clarify ambiguities that could cause machine-to-machine discrepancies. Must be resolved before live trading.

  **CONFIG FREEZE**           P7, P12, P13, P14      Design decisions and thresholds. Can be adjusted during backtesting phase but must be frozen before production.
  --------------------------- ---------------------- -------------------------------------------------------------------------------------------------------------------------

Next step: Merge v1.1 + v1.2 into consolidated specification documents and begin Phase 1 implementation (Data Layer + ZLBB Engine).
