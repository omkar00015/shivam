**SYSTEM AMENDMENT DOCUMENT**

Patch v1.1 \| Deterministic Multi-Timeframe Algo Trading System

February 2026 \| Covers: C1--C7, C4, M1--M7, M8 Removal, D1, E1, E3, E4,
E5

**Amendment Index**

  -----------------------------------------------------------------------------------
  **ID**   **Severity**   **Affects        **One-Line Summary**
                          Document(s)**    
  -------- -------------- ---------------- ------------------------------------------
  C1       CRITICAL       Doc 3, Doc 3.1,  Doc 3.1 is the sole authoritative leg
                          Add. A           detection engine

  C2       CRITICAL       Doc 4, Doc 6     BROKEN zone exception --- Breakout Retest
                                           requires FLIPPED state

  C3       CRITICAL       Doc 4, Doc 6     Breakout confirmation, failed breakout,
                                           retest setup --- fully specified

  C5       CRITICAL       Doc 2            Gold gap bars: use real previous_close for
                                           TR, not filled value

  C6       CRITICAL       Doc 5            All phase score conditions quantified with
                                           numeric thresholds

  C7       CRITICAL       Doc 4            Midpoint SR: once validated = normal SR;
                                           minimum inter-zone distance

  M1       MODERATE       Doc 5            Phase stickiness: score recalcs every 15m,
                                           label changes on 2 x 1H closes

  M2       MODERATE       Doc 6            Bearish Engulfing formally defined as
                                           mirror of Bullish

  M3       MODERATE       Doc 6            Bear Pullback Continuation fully specified
                                           by symmetry

  M4       MODERATE       Doc 4            BROKEN confirmation uses 15-min candles
                                           (per C3 window rules)

  M5       MODERATE       Doc 4            Zone width: Section 7 formula is sole
                                           definition; Section 5 retired

  M6       MODERATE       Doc 8            expected_R defined as: target_distance /
                                           stop_distance

  M7       MODERATE       Add. A           Band walk mode takes precedence over
                                           squeeze mode once active

  M8       MODERATE       Doc 6            Setup 5 (Failed Weakness/Strength) removed
                                           from system

  C4       CRITICAL       Doc 4, Doc 11    Retro SR init as TESTED; lifecycle as
                                           strength modifier; Doc 11 Stage 3 removed

  D1       DESIGN         Doc 2            1W/1M/3M fetched from source; not
                                           aggregated from 15m

  E1       EDGE CASE      Doc 3.1, Doc 4   Single-bar spike: treated as rejection
                                           point, not a leg

  E3       EDGE CASE      Doc 9            Minimum historical depth requirements
                                           frozen per timeframe

  E4       EDGE CASE      Doc 5            Minimum 3 completed legs required before
                                           phase is trusted

  E5       DESIGN         Doc 8            Correlation concept removed entirely; BTC
                                           and Gold treated independently
  -----------------------------------------------------------------------------------

> **C1 \| Authoritative Leg Detection Engine**

**CRITICAL \| Affects: Doc 3, Doc 3.1, Addendum A**

**Decision**

Document 3.1 (Leg Determination Engine --- Full Expansion Reversal
Model) is the sole authoritative leg detection engine for the 15-minute
timeframe. It supersedes the leg detection sections of Document 3
(Sections 7--16) and replaces any conflicting definitions in Addendum A
that specify a different leg start or end threshold on the 15m TF.

**Hierarchy of Authority**

  ---------------------------------------------------------------------------
  **Document**    **Status**      **Role After This Amendment**
  --------------- --------------- -------------------------------------------
  Doc 3.1         AUTHORITATIVE   Full leg detection logic for 15m → 1H SR
                                  feed

  Doc 3 §3--§6    RETAINED        ZLBB mathematics, band computation, modes
                                  (NORMAL/BAND_WALK/SQUEEZE)

  Doc 3 §7--§16   SUPERSEDED      Leg state machine in Doc 3 is replaced by
                                  Doc 3.1 in full

  Addendum A §A4  SUPERSEDED on   Band walk and squeeze behaviour for the 15m
                  15m only        instance follows Doc 3.1 extensions below
  ---------------------------------------------------------------------------

**Authoritative Leg Rules (from Doc 3.1 --- no changes needed)**

> Bull Leg Start: bar.low \<= lower_1sigma (1σ below ZLEMA)\
> Bull eligible: leg_high \>= upper_1sigma (reached +1σ)\
> Bull Complete: eligible=True AND bar.low \<= lower_1sigma AND
> bar.close \< ZLEMA\
> \
> Bear Leg Start: bar.high \>= upper_1sigma (1σ above ZLEMA)\
> Bear eligible: leg_low \<= lower_1sigma (reached -1σ)\
> Bear Complete: eligible=True AND bar.high \>= upper_1sigma AND
> bar.close \> ZLEMA

**Band Walk Extension (addendum to Doc 3.1 --- NEW)**

Doc 3.1 does not address band walk. The following rules extend it
without contradiction:

> **▶ Band Walk Entry: \>= 3 consecutive bar closes outside the same 2σ
> band while a leg is IN_BULL_LEG or IN_BEAR_LEG.**
>
> **▶ Once band_walk_active = True: the eligible condition is already
> True (band was reached). Do NOT require return to starting 1σ while in
> band walk.**
>
> **▶ Band Walk Reversal Permission: retracement of current sub-leg \>=
> 25% of leg displacement since leg start. Binary. Below 25% → ignore
> reversal signal.**
>
> **▶ Sub-leg boundary: when ZLEMA cross occurs during a band walk, the
> current sub-leg ends and a new sub-leg begins. The parent leg
> continues.**
>
> **▶ Band walk exits when: retracement \>= 25% AND a close beyond the
> opposite 1σ occurs AND close \< ZLEMA (for bull) or \> ZLEMA (for
> bear).**

**Squeeze Extension (addendum to Doc 3.1 --- NEW)**

> **▶ Squeeze active: bandwidth_percentile \<= 20 (rolling 100-bar
> window on 15m).**
>
> **▶ Squeeze breakout: first bar after squeeze that closes outside any
> 2σ band immediately starts a new leg at the 1σ threshold check
> (eligible = True is granted immediately since band was exceeded).**
>
> **▶ Minimum displacement for a squeeze leg: 0.25 x ATR(15m, 14). If
> below → merge with prior leg.**
>
> **C2 \| BROKEN Zone Exception for Breakout Retest**

**CRITICAL \| Affects: Doc 6 §3.2 and §7**

**Problem Being Solved**

Doc 6 §3.2 Global Pre-Filter rejects all BROKEN zones before any setup
logic runs. This makes Setup 2 (Breakout Retest) impossible, because
that setup explicitly requires a previously BROKEN zone as its
precondition.

**Amended Rule --- Doc 6 §3.2**

Replace the existing Zone State Rule in §3.2 with the following:

> ZONE STATE RULE (amended):\
> \
> Reject zone if state is: EXPIRED or FROZEN.\
> \
> Exception A --- BROKEN zones:\
> A BROKEN zone is eligible ONLY for Setup 2 (Breakout Retest).\
> It is ineligible for all other setups.\
> A BROKEN zone that has transitioned to FLIPPED is eligible for all
> setups\
> consistent with its new polarity.\
> \
> Exception B --- FLIPPED zones:\
> A FLIPPED zone is treated as a normal FRESH zone of opposite
> polarity.\
> Old resistance that flipped = new support. Apply full setup logic.

**Lifecycle Alignment**

  ---------------------------------------------------------------------------
  **Zone State**  **Setup 1      **Setup 2        **Rejection     **Range
                  (Pullback)**   (Breakout        (Doc 6.1)**     Fade**
                                 Retest)**                        
  --------------- -------------- ---------------- --------------- -----------
  FRESH           YES            NO               YES             YES

  TESTED          YES            NO               YES             YES

  BREAK_PENDING   NO             YES --- watch    NO              NO
  (new)                          window                           

  BROKEN          NO             YES --- eligible NO              NO
  (confirmed)                                                     

  FLIPPED         YES (new       NO               YES (new        YES (new
                  polarity)                       polarity)       polarity)

  FROZEN          NO             NO               NO              NO

  EXPIRED         NO             NO               NO              NO
  ---------------------------------------------------------------------------

Note: BREAK_PENDING is defined in C3 below.

> **C3 \| Breakout / Failed Breakout / Retest --- Full Specification**

**CRITICAL \| Affects: Doc 4 §8, Doc 6 §7**

**New Zone Substates**

Two new substates are added to the Zone Lifecycle Engine (Doc 4 §8):

  --------------------------------------------------------------------------
  **Substate**       **Definition**                          **Tradable?**
  ------------------ --------------------------------------- ---------------
  BREAK_PENDING      1--3 candle closes beyond zone boundary Setup 2 watch
                     (not yet confirmed breakout)            only

  BROKEN_CONFIRMED   4+ consecutive closes beyond boundary   Setup 2
                     --- full breakout confirmed             eligible
  --------------------------------------------------------------------------

**Successful Breakout Rule**

**Resistance Breakout (Bullish)**

> Trigger: candle closes above zone_high\
> Confirmation: price closes ABOVE zone_high for 4 or more CONSECUTIVE
> 15-minute candles\
> On 1st close above zone_high → state = BREAK_PENDING\
> If 4 consecutive closes above zone_high achieved → state =
> BROKEN_CONFIRMED\
> Zone polarity flips: zone becomes new support (state = FLIPPED)
> immediately after BROKEN_CONFIRMED

**Support Breakout (Bearish)**

> Mirror logic:\
> Trigger: candle closes below zone_low\
> Confirmation: 4+ consecutive 15-minute closes below zone_low →
> BROKEN_CONFIRMED → FLIPPED\
> Flipped zone becomes new resistance

**Failed Breakout Rule**

> Definition: price enters BREAK_PENDING but fails to sustain the
> breakout.\
> \
> FAILED if: within the 4-candle BREAK_PENDING window,\
> price closes back INSIDE the zone (between zone_low and zone_high) for
> \>= 2 consecutive candles.\
> \
> On failed breakout:\
> state reverts to TESTED (touch_count + 1, false_break_count + 1)\
> BREAK_PENDING is cleared\
> Zone is strengthened --- false breakout is evidence of zone validity\
> \
> Confirmation of failed breakout:\
> Price closes below zone_low (for a bullish false break at resistance)\
> This is a Rejection Fakeout setup trigger (see Doc 6.1)

**Breakout Retest Setup (Setup 2 --- Amended)**

> Precondition: zone is in state BROKEN_CONFIRMED or FLIPPED\
> \
> Trigger: price returns to the zone boundaries after confirmed
> breakout\
> For bullish breakout (zone now support): price pulls back to
> zone_high\
> For bearish breakout (zone now resistance): price rallies back to
> zone_low\
> \
> Time Constraint: retest must occur within 2 completed 1H legs after
> confirmation.\
> Beyond 2 legs → setup invalid, zone retested but no Breakout Retest
> candidate created.\
> \
> Confirmation: price gets rejected from the retested boundary in the
> direction of the original breakout.\
> Rejection = rejection bar (wick \>= 50% of range, close in upper/lower
> 50%) OR\
> decisive close away from zone boundary (\>= 0.5 x ATR(15m))\
> \
> Invalidation:\
> Price closes back through the original zone boundary → zone reassessed
> for failed breakout\
> Time window (2 x 1H legs) exceeded → setup cancelled, not re-armed\
> \
> Stop: beyond the zone center (opposite zone boundary + 0.10 x ATR
> buffer)\
> Target: next significant opposing zone in breakout direction
>
> **C5 \| Gold Gap Handling for ATR**

**CRITICAL \| Affects: Doc 2 §5 and §7**

**Problem**

Gold (XAU/USD) does not trade on weekends. When using previous_close to
fill weekend bars, the gap from Friday\'s close to Monday\'s open is
hidden. The True Range on the first real Monday candle, when computed
against a filled (fake) Friday-close bar, severely underestimates the
actual weekend gap volatility. This corrupts ATR for all modules that
depend on it.

**Amended Rule --- Doc 2 §5 (Missing Data Policy)**

> INSTRUMENT-SPECIFIC GAP RULE:\
> \
> For instruments with non-continuous sessions (Gold, traditional
> assets):\
> \
> 1. Do NOT create synthetic 15m bars for market-closed periods.\
> (Unlike BTC which trades 24/7, Gold session gaps are real, not missing
> data.)\
> \
> 2. The first real 15m bar after a market closure (e.g., Monday 23:00
> UTC Gold open)\
> must compute True Range as:\
> \
> TR\[first_real_bar\] = max(\
> bar.high - bar.low,\
> abs(bar.high - last_real_close), // gap-aware\
> abs(bar.low - last_real_close) // gap-aware\
> )\
> \
> where last_real_close = close of the last bar where is_complete=True\
> before the market closure. NOT the value from any synthetic filled
> bar.\
> \
> 3. BTC is exempt. BTC bars are always continuous --- previous_close
> fill applies\
> only for missing data within an active session (\< 10% threshold, Doc
> 2 §5).

**Session Definitions (Freeze in Config)**

  ------------------------------------------------------------------------
  **Instrument**   **Session Type**           **Gap Handling**
  ---------------- -------------------------- ----------------------------
  BTC/USDT         24/7 continuous            Fill missing bars with
                                              previous_close per Doc 2 §5

  Gold (XAU/USD)   Weekday session, closes    Gap-aware TR on Monday first
                   Friday \~22:00 UTC         bar

  Gold             Daily holiday closures     Same gap-aware TR rule for
                                              any session-open bar after
                                              closure
  ------------------------------------------------------------------------

> **C6 \| Phase Score Conditions --- Full Quantification**

**CRITICAL \| Affects: Doc 5 §5, §7**

**Bull Trend Score --- Amended (Doc 5 §5.1)**

  -------------------------------------------------------------------------------
  **Condition**            **Points**   **Quantified Rule**
  ------------------------ ------------ -----------------------------------------
  HH + HL structure        +20          In last 5 completed 1H legs: at least 2
  present                               consecutive bull legs where
                                        end_price\[n\] \> end_price\[n-2\] AND
                                        start_price\[n\] \> start_price\[n-2\]

  Majority bull legs \>=   +15          count(direction=BULL) / total legs in
  60%                                   last 5 \>= 0.60

  Average efficiency \>=   +15          mean(displacement / cumulative_range)
  0.5                                   across last 5 legs \>= 0.50

  Pullbacks \<= 50%        +10          For each bear leg in last 5: displacement
                                        \< 0.50 x preceding bull leg
                                        displacement. Must hold for \>= 60% of
                                        bear legs.

  Price above ZLEMA        +10          In last 20 x 1H candles: count(close \>
  majority                              ZLEMA) / 20 \>= 0.60

  Band walk occurred       +10          band_walk_active=True occurred at least
  recently                              once in last 10 x 1H bars

  Resistance breaks        +10          In last 5 x 1H legs: count of bull legs
  succeeded                             that closed above a prior resistance zone
                                        \>= 2

  Bear rejection failed    +10          In last 5 x 1H legs: count of bear legs
                                        where leg_displacement \< 0.50 x ATR(1H)
                                        AND leg ended at a support zone \>= 2
  -------------------------------------------------------------------------------

**Bear Trend Score --- Amended (Doc 5 §5.2, mirror)**

  -------------------------------------------------------------------------------
  **Condition**            **Points**   **Quantified Rule**
  ------------------------ ------------ -----------------------------------------
  LL + LH structure        +20          In last 5 legs: bear leg end_price\[n\]
  present                               \< end_price\[n-2\] AND bear
                                        start_price\[n\] \< start_price\[n-2\]
                                        for \>= 2 consecutive bear legs

  Majority bear legs \>=   +15          count(direction=BEAR) / total \>= 0.60
  60%                                   

  Average efficiency \>=   +15          Same formula, bear legs
  0.5                                   

  Pullbacks \<= 50%        +10          Bull retracement legs \< 50% of preceding
                                        bear leg displacement for \>= 60% of
                                        cases

  Price below ZLEMA        +10          count(close \< ZLEMA) / 20 \>= 0.60 in
  majority                              last 20 x 1H candles

  Band walk occurred       +10          Bearish band_walk_active=True in last 10
  recently                              x 1H bars

  Support breaks succeeded +10          count of bear legs closing below a prior
                                        support zone \>= 2 in last 5 legs

  Bull rejection failed    +10          count of bull legs with displacement \<
                                        0.50 x ATR(1H) ending at resistance \>= 2
                                        in last 5
  -------------------------------------------------------------------------------

**Distribution Score --- Amended (Doc 5 §7)**

  -------------------------------------------------------------------------------
  **Condition**            **Points**   **Quantified Rule**
  ------------------------ ------------ -----------------------------------------
  Bull efficiency          +20          efficiency\[bull_leg_n\] \<
  declining 2 legs                      efficiency\[bull_leg_n-1\] for 2
                                        consecutive bull legs in last 5

  Bear bar ratio rising    +15          In last 20 x 1H bars: bear bar count
                                        increasing in rolling 5-bar windows (3 of
                                        4 windows show increase)

  Deeper pullbacks         +15          Most recent bear leg displacement \> 0.60
                                        x most recent bull leg displacement

  Upper wicks increasing   +10          In last 5 x 1H bull candles:
                                        mean(upper_wick / range) \> 0.40

  Multiple failures to     +20          count of bull legs in last 5 that failed
  expand                                to close above prior resistance zone \>=
                                        2

  Near HTF resistance      +20          Distance to nearest HTF zone (4H or
                                        above) \<= 1.5 x ATR(trigger TF = 1H)
  -------------------------------------------------------------------------------

**Accumulation Score (mirror of Distribution --- Doc 5 §8)**

All conditions are mirrored symmetrically. Near HTF support \<= 1.5 x
ATR(1H). Bear efficiency declining. Shallow rallies. Lower wicks
increasing. Multiple failures to break support.

> **C7 \| Midpoint SR --- Validated = Normal SR + Minimum Distance
> Rule**

**CRITICAL \| Affects: Doc 4 §4.4, §6**

**Amended Midpoint SR Rules (Doc 4 §4.4)**

> MIDPOINT SR LIFECYCLE:\
> \
> Step 1 --- Creation:\
> Midpoint SR created when parent leg displacement \>= 2 x ATR (average
> of leg start/end ATR).\
> Initial state: CANDIDATE (not yet tradable).\
> Tag: is_midpoint = True.\
> Timeframe: MINIMUM 1H. Do not create midpoint SR on 15m or below.\
> \
> Step 2 --- Validation Gate:\
> The CANDIDATE state requires one of the following on the IMMEDIATE
> LOWER TF\
> (e.g., for a 4H midpoint SR, validation occurs on the 1H chart):\
> A: \>= 2 rejection bars within the midpoint zone on that LTF, OR\
> B: price stays within the midpoint zone for \>= 5 consecutive LTF
> candles.\
> \
> Step 3 --- On Validation:\
> is_midpoint flag remains True (for audit/origin tracking).\
> State promoted to FRESH.\
> The zone is now treated identically to any FRESH SR zone.\
> It participates in clustering, merging, tier assignment, and setup
> detection.\
> Strength: starts at +1 (from §4.4) but can accumulate via
> touch_count.\
> \
> Step 4 --- Expiry if Not Validated:\
> If validation conditions are not met within 50 LTF bars of creation,\
> the CANDIDATE is discarded silently. Not logged as a broken zone.

**Minimum Inter-Zone Distance Rule (NEW --- Doc 4 §6.x)**

> PURPOSE: Prevent chart clutter and conflicting setups from zones that
> are too close.\
> \
> RULE: After all clustering and merging is complete, the final set of
> SR zones\
> must satisfy a minimum distance between any two adjacent zones.\
> \
> Minimum gap formula:\
> min_gap = max(0.40% of current_price, 0.25 x ATR(SR native TF))\
> \
> Application:\
> Sort all validated SR zones ascending by center price.\
> For each consecutive pair (SR_i, SR_i+1):\
> If abs(SR_i+1.center - SR_i.center) \< min_gap:\
> Merge the weaker into the stronger (by strength score).\
> If equal strength: retain the one from the higher TF.\
> If same TF: retain the newer one.\
> \
> This check runs as a post-clustering pass, after Section 6.7.\
> \
> Do NOT apply minimum gap between zones of different TF origins\
> if the higher-TF zone already passed this filter --- only same-TF
> zones compete.
>
> **M1 \| Phase Stickiness --- TF Clarification**

**MODERATE \| Affects: Doc 5 §12**

**Amended Rule (Doc 5 §12)**

> PHASE STICKINESS (clarified):\
> \
> Phase score is RECALCULATED on every 15-minute candle close.\
> → This includes the 8 intermediate 15m closes within a 1H bar.\
> → Updated scores are STORED but NOT applied to the active phase
> label.\
> \
> The active phase LABEL changes only when:\
> The new winning phase leads for \>= 2 CONSECUTIVE 1H bar closes.\
> (i.e., the 1H bar must close with the new phase winning, twice in a
> row.)\
> \
> During the interim period (score changed, label not yet changed):\
> System continues operating under the OLD phase label.\
> Phase confidence uses the OLD phase margin.\
> A TRANSITION flag is set to True if the new score leader margin \>
> 10.\
> \
> On TRANSITION=True during this interim:\
> Size reduction 50% applies.\
> Stronger setup confirmation required.
>
> **M2 \| Bearish Engulfing --- Formal Definition**

**MODERATE \| Affects: Doc 4 §4.3, Doc 6 §6.4**

**Bullish Engulfing (retained)**

> **▶ Bullish Engulfing: current bar close \>= high of the previous bar
> (entire prior candle, including wick, engulfed from below)**

**Bearish Engulfing (NEW --- mirror)**

> **▶ Bearish Engulfing: current bar close \<= low of the previous bar
> (entire prior candle, including wick, engulfed from above)**

For SR rejection bar validity (Doc 4 §4.3):

> • Bullish Engulfing: reversal valid if price does not close below the
> engulfing bar\'s low in next 3 candles, AND price closes above the
> engulfing bar\'s high.
>
> • Bearish Engulfing: reversal valid if price does not close above the
> engulfing bar\'s high in next 3 candles, AND price closes below the
> engulfing bar\'s low.
>
> **M3 \| Bear Pullback Continuation --- Full Specification**

**MODERATE \| Affects: Doc 6 §6**

**Amended Setup 1 --- Bear Direction**

> PULLBACK CONTINUATION (BEAR) --- Fully Specified:\
> \
> Allowed Phase: TREND_BEAR\
> \
> Precondition:\
> trend_bear active\
> zone is RESISTANCE (former support or supply zone)\
> last impulse bearish (last completed leg is BEAR with quality \>= 2)\
> bullish pullback is underway (current leg is IN_BULL_LEG on 15m)\
> \
> Trigger:\
> Price retraces up into the resistance zone (price enters zone from
> below).\
> Proximity rule applies: distance \<= 2 x ATR(1H) from zone boundary.\
> \
> Confirmation (one of):\
> A: Rejection bar --- upper wick \>= 50% of candle range AND close in
> lower 50% of range\
> B: Bearish close below prior minor low (lower high formed)\
> C: Bearish Engulfing pattern at zone\
> \
> Candidate Creation:\
> Entry reference = confirmation low (the low of the confirmation
> candle).\
> Stop = zone_high + 0.10 x ATR(15m) buffer.\
> Target = next significant support zone below.\
> Minimum R:R \>= 2.0 (TREND phase requirement).\
> \
> Expiry: if not triggered within 5 x 15m bars after candidate creation
> → cancel.
>
> **M4 \| BROKEN Zone Confirmation --- 15-Minute Candles**

**MODERATE \| Affects: Doc 4 §8.3**

> AMENDED §8.3 --- BREAK_PENDING and BROKEN_CONFIRMED:\
> \
> All candle counts in the breakout confirmation sequence use 15-MINUTE
> candles.\
> \
> BREAK_PENDING: 1st close beyond zone boundary on 15m.\
> BROKEN_CONFIRMED: 4 CONSECUTIVE 15-minute closes beyond zone
> boundary.\
> FAILED BREAKOUT: price closes back inside zone for \>= 2 consecutive
> 15m candles\
> within the 4-candle BREAK_PENDING window.\
> \
> Rationale: Entry TF is 15m. All structural decisions that feed setup
> creation\
> use 15m closes. Using 1H candles here would cause a 4-hour lag in
> recognizing\
> failed breakouts, allowing the system to hold phantom setups for
> hours.
>
> **M5 \| Zone Width --- Section 7 is the Sole Formula**

**MODERATE \| Affects: Doc 4 §5 and §7**

> RETIRED: Doc 4 §5 (Initial Zone Width using 0.15 x ATR) is REMOVED.\
> \
> SOLE DEFINITION: Doc 4 §7 (Range-Based SR Zone Determination) is the
> only\
> zone width formula. It applies after all clustering and merging is
> complete.\
> \
> zone_half_width = min(0.10 x effective_range, c x ATR_exec)\
> where effective_range = min(range_left, range_right)\
> and ATR_exec = ATR(15m, 14)\
> and c = 1.5 (default)\
> \
> Pre-cluster / provisional zone width: not needed. Zones are points
> until\
> clustering is complete, at which point §7 computes the final width.\
> Implementation note: store raw SR levels (center price only) during
> clustering.\
> Assign zone boundaries only after §6.7 clustering pass + §C7 min-gap
> pass.
>
> **M6 \| expected_R Definition**

**MODERATE \| Affects: Doc 8 §5.1**

> DEFINITION:\
> \
> expected_R = target_distance / stop_distance\
> \
> target_distance = abs(target_price - entry_reference_price)\
> stop_distance = abs(stop_price - entry_reference_price)\
> \
> Both are computed at the time of candidate creation in Doc 6.\
> target_price = center of first opposing SR zone in trade direction.\
> If no opposing zone exists → use measured move (1.5 x stop_distance).\
> \
> expected_R is FROZEN at candidate creation time. It does not update if
> price moves.\
> This ensures ranking scores are deterministic and consistent between
> machines.
>
> **M7 \| Band Walk Precedence Over Squeeze Mode**

**MODERATE \| Affects: Addendum A §A10 Step 5**

> AMENDED PRIORITY ORDER (Addendum A §A10 Step 5):\
> \
> if band_walk_active == True:\
> mode = BAND_WALK ← takes unconditional precedence\
> \
> elif squeeze_active AND band_touched:\
> mode = SQUEEZE_BREAKOUT\
> \
> elif check_band_walk_entry_conditions():\
> mode = BAND_WALK ← new band walk starting\
> \
> else:\
> mode = NORMAL\
> \
> RATIONALE: A squeeze that resolves into sustained band-hugging
> (band_walk_active)\
> has already confirmed the breakout direction. Reverting to
> SQUEEZE_BREAKOUT\
> mode (1-bar minimum, relaxed thresholds) would prematurely end the leg
> on a\
> normal pullback. Band walk mode with 25% retracement is the correct
> filter.\
> \
> TRANSITION RULE: band_walk_active is cleared only when the full leg
> completes.\
> It is NOT cleared by a temporary squeeze re-entry during the band
> walk.
>
> **M8 \| Setup 5 --- Failed Weakness/Strength --- REMOVED**

**MODERATE \| Affects: Doc 6 §10**

> Setup 5 (Failed Weakness / Failed Strength) is REMOVED from the
> system.\
> \
> Reason: The setup was critically underspecified (no confirmation
> rules,\
> no zone requirement, no phase requirement, no stop definition, no
> expiry).\
> An underspecified setup in a deterministic system is a logical
> contradiction.\
> \
> The setup taxonomy in Doc 6 §4 is now:\
> 1. Pullback Continuation\
> 2. Breakout Retest\
> 3. Rejection and Reclaim of the Zone (Fakeout --- see Doc 6.1)\
> 4. Range Fade\
> \
> If Failed Weakness/Strength is to be reintroduced, it must be fully
> specified\
> with the same rigor as the other setups (precondition, trigger,
> confirmation,\
> stop, target, expiry, invalidation, allowed phases, zone
> requirement).\
> This requires a new amendment at that time.
>
> **C4 \| Retroactive SR Zone Initialization + Lifecycle as Strength
> Modifier**

**CRITICAL \| Affects: Doc 4 §8, Doc 4 §12, Doc 11 §6**

**Part 1 --- Retro SR Zone Initialization (Doc 4 §8)**

A retroactive leg is confirmed only when the opposite leg completes.
That opposite leg completing IS the reversal from the retro extreme. By
the time the SR zone is created, price has already moved significantly
away from the retro level --- meaning one confirmed rejection is already
baked in. The zone is not untested; it has market evidence of one
reaction.

> AMENDED INITIALIZATION RULE:\
> \
> When an SR zone is created from a retro leg (origin = RETRO):\
> lifecycle = TESTED (not FRESH)\
> touch_count = 1 (the retro reversal counts as the first confirmed
> touch)\
> \
> All other SR zone properties (strength, tier, zone width, clustering)
> are\
> computed identically to a normal SR zone.\
> \
> Restart safety: retro leg detection is fully deterministic from
> historical closes.\
> Any restart that loads sufficient history reproduces identical retro
> legs and\
> identical SR zones. No special handling required.

**Part 2 --- Lifecycle as Strength Modifier (Doc 4 §12 --- amended)**

Lifecycle state must not rank zones against each other through a
separate filter. Instead, lifecycle evidence is encoded directly into
the zone\'s strength score. This means zone strength --- which already
determines tier and ranking --- naturally reflects both structural
origin and market-proven behaviour.

**Strength Modifier Table**

  ------------------------------------------------------------------------
  **Lifecycle      **Base        **Per Confirmed Touch   **Maximum Total
  State**          Bonus**       (post-state)**          from Lifecycle**
  ---------------- ------------- ----------------------- -----------------
  FRESH            +0            +1 per touch            +3

  TESTED           +0            +1 per touch            +3

  FLIPPED          +2 flat on    +1 per touch after flip +5
                   flip                                  

  RETRO origin     Initializes   +1 per subsequent touch +3
                   as TESTED,                            
                   touch_count =                         
                   1                                     
  ------------------------------------------------------------------------

**Rules**

> **▶ A confirmed touch = price enters the zone AND exits without
> closing beyond the opposite boundary. touch_count increments by 1.**
>
> **▶ Touch bonus is capped at +3 regardless of lifecycle state. A zone
> with 4+ touches is absorbing orders and approaching exhaustion ---
> unlimited touch rewards would incorrectly elevate exhausted zones.**
>
> **▶ FLIPPED base bonus of +2 is applied immediately when state
> transitions to FLIPPED. Subsequent touches after the flip accumulate
> +1 each, up to the shared cap of +3 touch bonus. Maximum total from
> lifecycle for a FLIPPED zone = +5.**
>
> **▶ These modifiers are ADDITIVE to the zone\'s base strength (from
> leg quality, open/close clusters, rejection bars, midpoint). Tier
> classification uses total strength including modifiers.**

**Example**

> Zone created from a bull leg extreme (leg quality = 2):\
> Base strength = 2 + 2 = 4 (Doc 4 §4.1 formula: 2 + leg_quality)\
> State: FRESH, touch_count = 0, lifecycle bonus = 0\
> Total strength = 4 → Tier B\
> \
> After 2 confirmed touches:\
> touch_count = 2, lifecycle bonus = +2\
> Total strength = 6 → Tier A\
> \
> Zone then gets broken and flips:\
> FLIPPED base bonus = +2 (added on flip event)\
> touch_count resets to 0 post-flip for the new polarity\
> Total strength = 4 + 2 (prior touches, capped) + 2 (flip) = 8 → Tier
> S\
> \
> After 2 more touches in flipped state:\
> touch_count post-flip = 2, bonus = +2\
> Total strength = 4 + 2 + 2 + 2 = 10 → Tier S (capped at zone max)

**Part 3 --- Doc 11 Filter Stage 3 Removed**

> REMOVED FROM DOC 11:\
> \
> Filter Stage 3 (Lifecycle Priority: FRESH \> TESTED \> FLIPPED) is
> DELETED.\
> \
> Lifecycle information is now encoded in zone strength (Part 2 above).\
> Zone strength already flows into Filter Stage 2 (Zone Authority /
> Tier) and\
> the portfolio ranking score in Doc 8 §5.1.\
> \
> No information is lost. The ranking is now driven by market evidence\
> rather than an arbitrary state ordering.\
> \
> Doc 11 Filter Stages after this amendment:\
> Stage 1: Direction Permission (HTF bias)\
> Stage 2: Zone Authority (tier, which now encodes lifecycle
> implicitly)\
> Stage 3: \[REMOVED\]\
> Stage 4 → now Stage 3: Structural Clarity (stop distance)\
> Stage 5 → now Stage 4: Expected Return\
> Stage 6 → now Stage 5: Phase Confidence\
> Stage 7 → now Stage 6: Time Priority\
> Final: Lexicographic ID
>
> **D1 \| HTF Data Fetch Policy --- 1W / 1M / 3M**

**DESIGN \| Affects: Doc 2 §2, §4.1 --- New Section Added**

**Decision**

Aggregating 1W, 1M and 3M candles from 15-minute source data is
operationally impractical. It requires 5+ years of 15m bars at startup,
introduces calendar boundary complexity for monthly and quarterly bars,
and provides no accuracy benefit over exchange-native candles for purely
structural reference timeframes. These three timeframes are not on the
execution path --- they feed SR zone context only.

**Amended Source of Truth (Doc 2 §2)**

> TIERED DATA ARCHITECTURE:\
> \
> Tier 1 --- Self-Aggregated (execution-critical, from 15m source):\
> 15m → 1H → 4H → 1D\
> All four are derived from 15-minute OHLCV bars per Doc 2 §4.\
> Full determinism. One source. No external dependency.\
> \
> Tier 2 --- Exchange-Fetched (structural reference only):\
> 1W → 1M → 3M\
> Fetched directly from the data source API.\
> Used exclusively for HTF SR zone construction.\
> NOT used for leg detection, phase scoring, or entry timing.\
> \
> Native exchange HTF data remains ILLEGAL for Tier 1 timeframes.\
> For Tier 2, it is the REQUIRED and ONLY permitted source.

**Source Configuration (Frozen in Config)**

  ---------------------------------------------------------------------------
  **Instrument**   **Tier 2 Source**  **Endpoint /          **Notes**
                                      Interval**            
  ---------------- ------------------ --------------------- -----------------
  BTC/USDT         Binance API        GET /api/v3/klines    3M constructed
                                      --- intervals: 1w, 1M from 3 x 1M bars
                                                            fetched

  Gold (XAU/USD)   Paid data provider Weekly, Monthly       Provider frozen
                   (e.g. Polygon.io / intervals             in config. Change
                   Refinitiv)                               requires version
                                                            bump.
  ---------------------------------------------------------------------------

**Fetch Rules**

> **▶ Source per instrument is frozen in system config. Switching source
> requires a version bump and full restart.**
>
> **▶ On every system restart: re-fetch Tier 2 data from source. Never
> trust disk-cached 1W/1M/3M candles.**
>
> **▶ Fetched candles must pass the same OHLCV validation as aggregated
> candles (Doc 2 §12): high \>= low, close within range, no negative
> volume, no NaN/Inf.**
>
> **▶ 3M candles: constructed by merging 3 consecutive monthly candles
> (Jan+Feb+Mar, Apr+May+Jun, etc.). Open = first month open. High = max.
> Low = min. Close = last month close. Volume = sum.**

**Cross-Validation Rule**

> INTEGRITY CHECK --- 1W vs 1D:\
> \
> After fetching 1W candles, validate against self-aggregated 1D data:\
> \
> abs(weekly_candle.close - friday_1D_candle.close) \<= EPSILON\
> \
> If check fails for any week:\
> Flag: DATA_INCONSISTENCY on that week\'s candle\
> Do not use flagged candle for SR zone construction\
> Log and alert. Do not halt --- other weeks remain valid.\
> \
> This catches source divergence, exchange data corrections,\
> and timezone boundary mismatches before they corrupt SR zones.

**Calendar Boundary Definitions (Frozen)**

  -----------------------------------------------------------------------------
  **Timeframe**   **Candle Open**     **Candle Close**       **Source Handles**
  --------------- ------------------- ---------------------- ------------------
  1W              Monday 00:00 UTC    Sunday 23:59:59 UTC    Binance native
                                                             weekly boundary

  1M              1st of month 00:00  Last day of month      Exchange native
                  UTC                 23:59:59 UTC           monthly boundary

  3M              Jan/Apr/Jul/Oct 1st Mar/Jun/Sep/Dec last   Constructed from 3
                  00:00 UTC           day 23:59:59 UTC       x monthly candles
  -----------------------------------------------------------------------------

> **E1 \| Single-Bar Spike Handling**

**EDGE CASE \| Affects: Doc 3.1, Doc 4 §4**

**Problem**

On BTC, liquidation cascades can traverse the full ZLBB band range in a
single 15-minute candle. Under the Doc 3.1 leg engine, a bar whose high
\>= upper_1sigma AND whose low \<= lower_1sigma in the same candle
satisfies both a bear leg start and a bull leg start simultaneously ---
creating two phantom 1-bar legs pointing in opposite directions with SR
zones at both extremes. These are not structural legs; they are
liquidity sweeps.

**Detection Rule**

> A single-bar spike is identified when ALL of the following are true:\
> \
> bar_count = 1 (leg has not progressed beyond the triggering bar)\
> bar.high \>= upper_1sigma AND bar.low \<= lower_1sigma\
> (the single bar touches both sigma extremes)\
> \
> OR:\
> \
> A leg completes with bar_count = 1 AND leg quality score = 0\
> (no efficiency, no displacement duration, no decisive close)

**Handling Rule**

> WHEN a single-bar spike is detected:\
> \
> 1. DO NOT record it as a leg.\
> It is excluded from leg history, phase scoring leg counts, and leg
> quality.\
> \
> 2. DO record the spike extremes as REJECTION POINTS:\
> spike.high → treated as a bearish rejection point (supply)\
> spike.low → treated as a bullish rejection point (demand)\
> \
> 3. These rejection points feed Doc 4 §4.3 (Rejection Bars) as SR
> sources:\
> Strength: +2 (standard rejection bar source strength)\
> Zone width: computed via Doc 4 §7 (range-based, post-clustering)\
> Lifecycle: starts as FRESH, touch_count = 0\
> Tag: origin = SPIKE_REJECTION\
> \
> 4. State machine handling:\
> If state was SEEKING when spike occurred → return to SEEKING.\
> If state was IN_BULL_LEG or IN_BEAR_LEG → continue that leg.\
> The spike bar does NOT interrupt an in-progress leg.\
> \
> RATIONALE: A flash spike is a liquidity sweep and rejection by
> definition.\
> Treating its extremes as rejection SR sources is more accurate than\
> treating them as structural leg turning points.
>
> **E3 \| Minimum Historical Depth Requirements**

**EDGE CASE \| Affects: Doc 9 --- New Section**

**Problem**

Doc 9 (Persistence, Restart & Replay Integrity) does not specify how far
back history must go on restart. Without a minimum, ZLBB may initialize
on fewer than 20 bars, ATR may be unreliable, and SR zones from higher
timeframes may be absent. The system would operate silently on
incomplete structure.

**Minimum History Requirements (New --- Doc 9)**

  --------------------------------------------------------------------------------
  **Timeframe**   **ZLBB     **Min Bars **Warmup   **Total    **Approx History**
                  Period**   for ZLBB** Buffer**   Min Bars** 
  --------------- ---------- ---------- ---------- ---------- --------------------
  15m             20         20         +20        40         10 hours

  1H              20         20         +20        40         40 hours

  4H              20         20         +20        40         7 days

  1D              20         20         +20        40         40 days

  1W (fetched)    20         20         +20        40         10 months

  1M (fetched)    20         20         +20        40         40 months

  3M (fetched)    20         20         +20        40         10 years
  --------------------------------------------------------------------------------

> STARTUP VALIDATION RULE (New --- Doc 9):\
> \
> On restart, after rebuilding all timeframes from history:\
> \
> FOR EACH timeframe:\
> IF available_bars \< total_min_bars:\
> Mark that timeframe as INSUFFICIENT_HISTORY\
> Do NOT generate SR zones from that timeframe\
> Do NOT use that timeframe for phase scoring\
> Log the gap and alert operator\
> \
> System may still trade on available timeframes.\
> It must NEVER silently operate on under-initialized indicators.\
> \
> Practical minimum for full system operation:\
> 5 years of history across all instruments.\
> Without this, 3M ZLBB cannot initialize and quarterly SR zones are
> absent.\
> \
> Warmup buffer rationale:\
> First 20 bars of any ZLBB are initialization (simple mean ATR, EMA
> warmup).\
> These bars produce unreliable band values.\
> The warmup buffer ensures the first USED bar has 20 prior bars of
> stable output.
>
> **E4 \| Minimum Legs Before Phase Is Trusted**

**EDGE CASE \| Affects: Doc 5 §4**

**Problem**

At system startup or after a prolonged squeeze with no completed legs,
the phase engine may have fewer than 3 legs on the trigger TF (1H).
Scoring conditions such as \'majority bull legs \>= 60% of last 5\'
become statistically meaningless with 1 or 2 data points. A single bull
leg scores 100% majority --- triggering TREND_BULL with maximum
confidence on one data point.

**Amended Rule (Doc 5 §4)**

> MINIMUM LEG REQUIREMENT:\
> \
> Full phase scoring activates ONLY when:\
> completed_legs on trigger TF (1H) \>= 3\
> \
> While completed_legs \< 3:\
> phase = TRANSITION (forced, regardless of any score)\
> is_transition = True\
> confidence_margin = 0\
> All TRANSITION-mode restrictions apply:\
> - Size reduced 50%\
> - Stronger setup confirmation required\
> - Phase-dependent setups (Pullback, Range Fade) are SUSPENDED\
> - Only Rejection/Fakeout setups (Doc 6.1) are permitted\
> (these are zone-based and do not require phase confirmation)\
> \
> On 3rd leg completing:\
> Full phase scoring activates immediately on that candle close.\
> TRANSITION is lifted if a dominant phase winner exists (margin \>=
> 10).\
> If no dominant winner → remain in TRANSITION per normal rules.
>
> **E5 \| Correlation Concept Removed**

**DESIGN \| Affects: Doc 8 §7**

> REMOVED FROM DOC 8:\
> \
> Doc 8 §7 (Correlation Control) is DELETED in its entirety.\
> \
> BTC/USDT and Gold (XAU/USD) are treated as fully independent
> instruments\
> at all times. The system makes no assumption about their correlation.\
> \
> Rationale:\
> Correlation between instruments is dynamic and regime-dependent.\
> Encoding it as a static rule creates false precision.\
> Each instrument\'s own risk limits (Doc 8 §3) are sufficient
> controls.\
> No correlation-based position sizing, filtering, or ranking is
> permitted.\
> \
> The following are also removed as a consequence:\
> - Cross-instrument direction conflict checks\
> - Joint governance response rules (ATR stress on one = DEFENSIVE on
> both)\
> - Any reference to \'same directional thesis\' across instruments\
> \
> Each instrument runs its own independent governance state (Doc 10).\
> A governance event on BTC does not affect Gold and vice versa.

**Amendment Document v1.1 --- End**

All critical, moderate, design and edge case patches applied. System is
ready for implementation planning. Next step: build production data
layer (Doc 2) with tiered data architecture and Gold gap handling.
