**ADDENDUM A (v2)**

**Dual-Timeframe Zero Lag Bollinger Bands**

Objective Leg Detection, Band Walk Logic, Exhaustion Zones & Squeeze
Detection

Supplement to: Algorithmic Trading Framework v1.0 \| February 2026

**A1. Purpose & Integration Point**

The main framework (Sections 3-5) relies on swing-point-based leg
detection and subjective exhaustion identification. This addendum
introduces Dual-Timeframe Zero Lag Bollinger Bands (DT-ZLBB) as an
objective, mathematically defined system for three critical functions:

**Function 1 --- Leg Boundary Detection:** A traversal from one Entry TF
ZLBB band to the opposite band defines a single leg on the Trigger TF.
This replaces N-bar swing detection with a volatility-adaptive boundary
that automatically adjusts to market conditions.

**Function 2 --- Exhaustion Zone Identification:** When price reaches
the outer Trigger TF ZLBB band (plotted on the Entry TF chart), it
signals a zone where the current move is statistically extended.

**Function 3 --- Trend Regime Classification:** The ZLBB system
identifies three distinct trend regimes --- Band Walk (strong
trend/breakout), Channel Trend (maturing trend), and Balance (range) ---
based on how price interacts with the bands.

**Key Advantage Over Standard BB:** Standard Bollinger Bands lag due to
the SMA basis. The ZLEMA basis removes approximately half the lookback
period of lag, meaning band crossings align more closely with actual
turning points rather than lagging behind them.

**A2. Zero Lag Bollinger Band (ZLBB) Calculation**

The ZLBB replaces the standard SMA with a Zero Lag Exponential Moving
Average (ZLEMA) as the center line. All other Bollinger Band mechanics
remain identical.

**Step 1: Compute the Lag-Adjusted Price**

> lag = floor((period - 1) / 2)
>
> lag_adjusted_price = close + (close - close\[lag\])

Where close\[lag\] is the closing price \"lag\" bars ago. This doubles
the recent price movement to counteract smoothing delay.

**Step 2: Compute the ZLEMA (Middle Band)**

> ZLEMA = EMA(lag_adjusted_price, period)

Period = 20 for both timeframes.

**Step 3: Compute Standard Deviation**

> σ = stddev(close, period)

Standard deviation is computed on raw closing prices, not lag-adjusted
prices. This preserves the volatility measurement while only the center
line benefits from lag reduction.

**Step 4: Compute Bands**

> Upper Band = ZLEMA + (D × σ)
>
> Lower Band = ZLEMA - (D × σ)

D = 2 (standard deviation multiplier). Tunable per instrument during
backtesting.

**A3. Dual-Timeframe Configuration (Entry System)**

For ENTRY TIMING, two ZLBB instances run simultaneously on the 15min
chart. One is computed from 15min data, the other from 4H data. Both are
visible together, creating a nested envelope system. This dual-TF pair
is specifically for trade entry and exhaustion detection.

  -----------------------------------------------------------------------
  **Parameter**     **Entry TF ZLBB**          **Trigger TF ZLBB**
  ----------------- -------------------------- --------------------------
  Timeframe Basis   15min candles (Entry TF)   4H candles (Trigger TF)

  Plotted On        Entry TF chart (15min)     Entry TF chart (15min)

  Period            20                         20

  Std Dev           2                          2
  Multiplier                                   

  Role              Leg boundary detection.    Exhaustion zone / macro
                    Band-to-band traversal = 1 envelope. Outer bands =
                    leg on Trigger TF.         statistical extremes of
                                               the larger move.

  Visual Appearance Tight bands hugging price  Wide bands forming the
                                               outer envelope
  -----------------------------------------------------------------------

**A3.1 HTF ZLBB Update Logic**

The Trigger TF ZLBB (4H) is computed using 4H OHLCV data but plotted on
the 15min chart. Between 4H candle updates, the values remain constant
(step function). On each new 4H bar close, the bands update.

**Implementation:** On each 15min bar: (a) always recompute 15min ZLBB,
(b) only recompute 4H ZLBB when a new 4H bar has completed (timestamp
crosses 4H boundary). Between updates, carry forward previous 4H ZLBB
values.

**A3.2 Paired-Timeframe ZLBB Architecture (Leg Detection for SR)**

The core discovery of this system is: a ZLBB band-to-band traversal on a
lower timeframe defines one complete leg on the next higher timeframe.
This principle scales fractally across all timeframe pairs. To detect
legs on every SR-generating timeframe, the system creates ZLBB instances
on the LOWER TF of each pair, and attributes the completed legs to the
HIGHER TF.

**A3.2.1 The Pairing Principle**

> RULE: ZLBB on TF_lower defines legs visible on TF_higher.
>
> When price traverses from one ZLBB band to the opposite on TF_lower:
>
> → That traversal = one complete leg on TF_higher.
>
> → The leg extreme (high of highest bar or low of lowest bar
>
> during the traversal) is a structural turning point on TF_higher.
>
> → That leg extreme feeds SR zone detection for TF_higher.
>
> All A4 leg detection logic applies to the LOWER TF data:
>
> → Normal Mode: ZLEMA rejection/break confirmation, 3-bar min
>
> → Band Walk Mode: 25% retracement, 1 counter-trend bar
>
> → Squeeze Breakout Mode: relaxed thresholds
>
> → The ZLEMA rejection rule applies on the lower TF:
>
> If price touches the opposite band, pulls back to ZLEMA,
>
> and gets REJECTED (wick crosses ZLEMA but closes back
>
> on the band side), the prior move is still one leg
>
> and the continuation to the opposite band starts a new leg.
>
> Only when price BREAKS ZLEMA (closes beyond it) is the
>
> leg confirmed complete.

**A3.2.2 Complete Pairing Table**

  -------------------------------------------------------------------------------------------------
  **ZLBB     **Legs        **TF      **ZLBB     **D**   **Leg          **Legs Feed   **Additional
  Computed   Attributed To Ratio**   Period**           Detection      SR For**      Role**
  On (Lower  (Higher TF)**                              Modes**                      
  TF)**                                                                              
  ---------- ------------- --------- ---------- ------- -------------- ------------- --------------
  15min      1-Hour        4:1       20         2.0     Normal + Band  1H SR zones   Also serves as
                                                        Walk + Squeeze (Addendum B)  Entry TF for
                                                        (full A4)                    trade timing

  1-Hour     4-Hour        4:1       20         2.0     Normal + Band  4H SR zones   ---
                                                        Walk + Squeeze (Addendum B)  
                                                        (full A4)                    

  4-Hour     Daily         6:1       20         2.0     Normal + Band  Daily SR      Also serves as
                                                        Walk + Squeeze zones         Trigger TF
                                                        (full A4)      (Addendum B)  exhaustion
                                                                                     envelope on
                                                                                     15min chart

  Daily      Weekly        5:1       20         2.0     Normal + Band  Weekly SR     ---
                                                        Walk + Squeeze zones         
                                                        (full A4)      (Addendum B)  

  Weekly     Monthly       \~4.3:1   20         2.0     Normal + Band  Monthly SR    ---
                                                        Walk (Squeeze  zones         
                                                        rare but       (Addendum B)  
                                                        valid)                       

  Monthly    3-Month       3:1       20         2.0     Normal mode    Quarterly SR  ---
             (Quarterly)                                only (min leg  zones         
                                                        bars = 2).     (Addendum B)  
                                                        Band                         
                                                        Walk/Squeeze                 
                                                        impractical                  
                                                        with 24-bar                  
                                                        lookback.                    
  -------------------------------------------------------------------------------------------------

**A3.2.3 How It Works --- Concrete Example**

> EXAMPLE: Detecting legs on the 4-Hour timeframe
>
> 1\. Compute ZLBB on 1H candles (period=20, D=2.0)
>
> 2\. On each new 1H bar, run leg detection (A4 logic) using 1H ZLBB:
>
> a\) Price touches 1H ZLBB lower band → potential bull leg start
>
> b\) Price traverses upward across multiple 1H bars
>
> c\) Price touches 1H ZLBB upper band → tentative leg end
>
> d\) Price pulls back toward 1H ZLEMA:
>
> \- If ZLEMA REJECTION (wick crosses but close stays above):
>
> → Bull leg NOT complete. Prior move continues.
>
> → If price then reaches lower band, record:
>
> completed bull leg (start to upper band touch)
>
> \+ new bear leg (upper band to lower band)
>
> \- If ZLEMA BREAK (close below ZLEMA):
>
> → Bull leg CONFIRMED complete.
>
> → Record leg extreme (highest high during the leg)
>
> 3\. The completed leg\'s extreme is a 4H-level structural turning
> point
>
> 4\. This extreme feeds into Addendum B Method 1 for 4H SR detection
>
> The same logic applies to every pair in the table above.

**A3.2.4 ZLBB Instance List**

The system creates exactly 6 ZLBB instances. Each instance is computed
on one timeframe and its legs are attributed to the next higher
timeframe. Each instance maintains its own independent state machine.

  ---------------------------------------------------------------------------------------
  **Instance     **Computed   **Legs      **Lookback   **Bandwidth   **Independent
  ID**           On**         For**       (bars)**     Lookback**    State**
  -------------- ------------ ----------- ------------ ------------- --------------------
  ZLBB_15min     15min        1H legs     200 bars     100 bars      Full: ZLEMA, bands,
                 candles                  (3.5 days)                 σ, bandwidth,
                                                                     leg_state, leg_mode,
                                                                     band_walk, squeeze,
                                                                     completed_legs\[\]

  ZLBB_1H        1H candles   4H legs     200 bars (8  100 bars      Full: same as above
                                          days)                      

  ZLBB_4H        4H candles   Daily legs  200 bars (33 100 bars      Full: same as above
                                          days)                      

  ZLBB_Daily     Daily        Weekly legs 120 bars (6  100 bars      Full: same as above
                 candles                  months)                    

  ZLBB_Weekly    Weekly       Monthly     52 bars (1   52 bars       Full: same as above
                 candles      legs        year)                      

  ZLBB_Monthly   Monthly      Quarterly   24 bars (2   24 bars       Normal mode only.
                 candles      legs        years)                     Min leg bars = 2.
  ---------------------------------------------------------------------------------------

**A3.2.5 Computation Schedule**

> ON NEW MONTHLY BAR:
>
> Recompute ZLBB_Monthly (bands, ZLEMA, σ, bandwidth).
>
> Run leg detection on ZLBB_Monthly. If leg completes:
>
> → Record leg extreme as Quarterly-level turning point.
>
> → Feed extreme to Addendum B Method 1 for Quarterly SR.
>
> ON NEW WEEKLY BAR:
>
> Recompute ZLBB_Weekly.
>
> Run leg detection on ZLBB_Weekly. If leg completes:
>
> → Record as Monthly-level turning point → Monthly SR.
>
> ON NEW DAILY BAR:
>
> Recompute ZLBB_Daily.
>
> Run leg detection on ZLBB_Daily. If leg completes:
>
> → Record as Weekly-level turning point → Weekly SR.
>
> ON NEW 4H BAR:
>
> Recompute ZLBB_4H.
>
> Run leg detection on ZLBB_4H. If leg completes:
>
> → Record as Daily-level turning point → Daily SR.
>
> Also: update 4H exhaustion envelope values for 15min chart.
>
> ON NEW 1H BAR:
>
> Recompute ZLBB_1H.
>
> Run leg detection on ZLBB_1H. If leg completes:
>
> → Record as 4H-level turning point → 4H SR.
>
> ON NEW 15MIN BAR:
>
> Recompute ZLBB_15min.
>
> Run leg detection on ZLBB_15min. If leg completes:
>
> → Record as 1H-level turning point → 1H SR.
>
> Also: check 4H exhaustion zones (paired with ZLBB_4H).
>
> Also: execute entry timing logic for trade setups.

**A3.2.6 Parameters Per Instance**

  ----------------------------------------------------------------------------------------
  **Parameter**       **15min**   **1H**   **4H**   **Daily**   **Weekly**   **Monthly**
  ------------------- ----------- -------- -------- ----------- ------------ -------------
  Period              20          20       20       20          20           20

  Std Dev Mult (D)    2.0         2.0      2.0      2.0         2.0          2.0

  Min Leg Bars        3           3        3        3           3            2
  (Normal)                                                                   

  Min Leg Bars        1           1        1        1           1            1
  (Squeeze)                                                                  

  Min Displacement    0.5x ATR    0.5x ATR 0.5x ATR 0.5x ATR    0.5x ATR     0.5x ATR
  (Normal)                                                                   

  Min Displacement    0.25x ATR   0.25x    0.25x    0.25x ATR   0.25x ATR    0.25x ATR
  (Squeeze)                       ATR      ATR                               

  Band Walk Proximity 0.5σ        0.5σ     0.5σ     0.5σ        0.5σ         N/A

  Band Walk Entry     5           5        5        5           5            N/A
  Bars                                                                       

  Band Walk           25%         25%      25%      25%         25%          N/A
  Retracement %                                                              

  Squeeze Percentile  20th        20th     20th     20th        20th         N/A

  Bandwidth Lookback  100         100      100      100         52           24

  Legs Attributed To  1H          4H       Daily    Weekly      Monthly      Quarterly
  ----------------------------------------------------------------------------------------

**A3.2.7 Exhaustion Envelope Pairing**

The 15min+4H entry pair from Section A3 remains the primary exhaustion
detection system. However, the paired architecture enables exhaustion
detection at every level:

> EXHAUSTION PAIRING (each lower TF checks the higher TF\'s ZLBB bands):
>
> ZLBB_15min checks against ZLBB_4H bands → Entry-level exhaustion
>
> ZLBB_1H checks against ZLBB_Daily bands → Intraday exhaustion
>
> ZLBB_4H checks against ZLBB_Weekly bands → Swing-level exhaustion
>
> ZLBB_Daily checks against ZLBB_Monthly bands → Position-level
> exhaustion
>
> For entry decisions, only 15min vs 4H exhaustion is used.
>
> Higher-level exhaustion feeds into the phase classifier and
>
> risk management (tighten stops, reduce position size when
>
> multiple exhaustion levels align).

**A4. Leg Detection Logic (Applied Per ZLBB Instance)**

This section defines the complete leg detection logic that runs
identically on every ZLBB instance (ZLBB_15min through ZLBB_Monthly,
with Monthly-specific relaxations noted). The logic has three operating
modes: Normal Mode (band-to-band traversal with ZLEMA rejection
confirmation), Band Walk Mode (strong trend with sub-leg detection via
25% retracement), and Squeeze Breakout Mode (first leg from BB squeeze).
The mode is determined by market context on the instance\'s timeframe
and the algorithm switches between them automatically. Completed legs
are attributed to the NEXT HIGHER timeframe per the pairing table in
A3.2.2.

**A4.1 Normal Mode --- ZLEMA Rejection Confirmed Legs**

This is the default leg detection mode. It addresses the premature
leg-end problem where price touches a band, pulls back 2-3 bars, then
continues in the original direction. The solution uses the ZLEMA as a
structural confirmation filter.

**Core Rule**

A leg is only considered complete when price reaches the opposite band
AND demonstrates structural commitment to the reversal by either: (a)
getting rejected from the ZLEMA on the pullback and then reaching the
opposite band, or (b) closing decisively beyond the ZLEMA toward the
opposite band.

**Detailed Logic for a Bull Leg (Lower Band → Upper Band)**

> 1\. LEG START: Bar wick touches or penetrates the Entry TF ZLBB Lower
> Band.
>
> → Record leg_start_price = bar.low
>
> → Set state = IN_BULL_LEG
>
> → Set bar_count = 0
>
> 2\. TRACKING: On each subsequent bar, increment bar_count.
>
> → Track leg_high = max of all highs since leg start.
>
> 3\. POTENTIAL LEG END: Bar wick touches or penetrates Upper Band.
>
> → This is NOT yet confirmed as leg end.
>
> → Set tentative_end = true, tentative_end_price = bar.high
>
> 4\. CONFIRMATION --- TWO PATHS:
>
> PATH A --- ZLEMA Rejection (trend continues):
>
> Price pulls back toward ZLEMA after upper band touch.
>
> A bar\'s wick touches or crosses ZLEMA, but CLOSES ABOVE ZLEMA.
>
> → ZLEMA rejected. Bull leg is NOT complete.
>
> → Discard tentative_end. Continue tracking IN_BULL_LEG.
>
> → If price subsequently reaches Lower Band, the full
>
> move from Lower → Upper → ZLEMA rejection → Lower
>
> counts as: completed bull leg (start to upper band touch)
>
> \+ completed bear leg (upper band touch to lower band).
>
> PATH B --- ZLEMA Break (reversal confirmed):
>
> Price pulls back and a bar CLOSES BELOW ZLEMA.
>
> → Bull leg is CONFIRMED complete.
>
> → Record leg end at tentative_end_price.
>
> → New bear leg begins from tentative_end_price.
>
> → state = IN_BEAR_LEG
>
> 5\. MINIMUM LEG SIZE:
>
> → A completed leg must contain \>= 3 bars (bar_count \>= 3).
>
> → A completed leg displacement must be \> 0.5x ATR.
>
> → If either condition fails, merge with previous leg.

For Bear Legs (Upper Band → Lower Band): invert all conditions. ZLEMA
rejection = bar wick touches ZLEMA but closes BELOW ZLEMA (bear
continues). ZLEMA break = bar closes ABOVE ZLEMA (bear leg confirmed
complete).

**ZLEMA Rejection Definition (Precise)**

  ------------------------------------------------------------------------
  **Scenario**     **Wick Condition** **Close           **Result**
                                      Condition**       
  ---------------- ------------------ ----------------- ------------------
  Bull Leg ---     Bar low touches or Bar closes ABOVE  Bull leg
  ZLEMA Rejection  crosses below      ZLEMA             continues. Upper
                   ZLEMA                                band touch was NOT
                                                        the end.

  Bull Leg ---     Bar low touches or Bar closes BELOW  Bull leg CONFIRMED
  ZLEMA Break      crosses below      ZLEMA             complete. Bear leg
                   ZLEMA                                begins.

  Bear Leg ---     Bar high touches   Bar closes BELOW  Bear leg
  ZLEMA Rejection  or crosses above   ZLEMA             continues. Lower
                   ZLEMA                                band touch was NOT
                                                        the end.

  Bear Leg ---     Bar high touches   Bar closes ABOVE  Bear leg CONFIRMED
  ZLEMA Break      or crosses above   ZLEMA             complete. Bull leg
                   ZLEMA                                begins.
  ------------------------------------------------------------------------

**Why This Works:** The ZLEMA is the equilibrium line. If price touches
the opposite band but cannot break the equilibrium on the pullback, the
original trend side still controls. Only when equilibrium is lost (close
beyond ZLEMA) is the leg truly over. The 3-bar minimum prevents
micro-noise from registering as legs.

**A4.2 Band Walk Mode --- Strong Trend Sub-Leg Detection**

In strong trends, price \"walks\" along one band for extended periods.
The Normal Mode logic would see this as one continuous leg, but it is
actually multiple impulse waves. Band Walk Mode detects and segments
these sub-legs using a 25% retracement rule.

**Band Walk Detection**

  ------------------------------------------------------------------------
  **Criterion**    **Bull Band Walk**          **Bear Band Walk**
  ---------------- --------------------------- ---------------------------
  Entry Condition  Price stays within 0.5σ of  Price stays within 0.5σ of
                   the Upper Band for N        the Lower Band for N
                   consecutive bars (default   consecutive bars (default
                   N=5)                        N=5)

  \"Within 0.5σ\"  bar.high \>= Upper Band -   bar.low \<= Lower Band +
                   (0.5 × σ)                   (0.5 × σ)

  Activation       Set band_walk_active =      Set band_walk_active =
                   true, band_walk_direction = true, band_walk_direction =
                   BULL                        BEAR

  Typical Context  Usually corresponds to a    Usually corresponds to a
                   breakout on the Trigger TF  breakdown on the Trigger TF
                   / Analysis TF               / Analysis TF
  ------------------------------------------------------------------------

**Sub-Leg Completion During Band Walk (25% Retracement Rule)**

During a band walk, individual impulse waves (sub-legs) are segmented
using a retracement threshold instead of opposite-band touches (which
won\'t happen during a walk).

> BULL BAND WALK SUB-LEG LOGIC:
>
> 1\. Track sub_leg_start = price at start of current sub-leg
>
> Track sub_leg_high = highest high since sub_leg_start
>
> Track sub_leg_bar_count = bars since sub_leg_start
>
> 2\. On each bar, update sub_leg_high.
>
> 3\. Compute retracement:
>
> sub_leg_displacement = sub_leg_high - sub_leg_start
>
> retracement_level = sub_leg_high - (0.25 × sub_leg_displacement)
>
> 4\. SUB-LEG COMPLETION CHECK (ALL must be true):
>
> a\) bar.low \<= retracement_level
>
> (wick crossing below 25% retracement is sufficient)
>
> b\) At least 1 bear bar exists in the retracement move
>
> (confirms sellers participated, not just a wick)
>
> c\) sub_leg_bar_count \>= 3
>
> (minimum bars for a valid sub-leg)
>
> 5\. When all conditions met:
>
> → Record completed bull sub-leg (sub_leg_start to sub_leg_high)
>
> → Compute all leg metrics (displacement, efficiency, momentum)
>
> → Start new sub-leg: sub_leg_start = current bar.low
>
> → The new up sub-leg begins only when price reverses the
>
> retracement and resumes upward with a bull bar closing
>
> above the prior bar\'s high.
>
> 6\. RETRACEMENT SUB-LEG:
>
> The retracement itself (from sub_leg_high to retracement low)
>
> is recorded as a bear sub-leg within the band walk.
>
> These retracement sub-legs are flagged as \"band_walk_pullback\"
>
> and are used in Effort vs Result comparison.

For Bear Band Walk: invert all conditions. Track sub_leg_low instead of
high. Retracement upward by 25% with at least 1 bull bar completes the
sub-leg.

**Band Walk Termination**

The band walk ends and the trend transitions to Channel Trend Phase when
price makes a decisive close beyond the ZLEMA to the opposite side of
the walk direction.

  ------------------------------------------------------------------------
  **Termination    **Bull Band Walk**          **Bear Band Walk**
  Condition**                                  
  ---------------- --------------------------- ---------------------------
  Primary Rule     A bar CLOSES BELOW the      A bar CLOSES ABOVE the
                   Entry TF ZLEMA              Entry TF ZLEMA

  Interpretation   Bulls have lost the         Bears have lost the
                   equilibrium. The strong     equilibrium. The strong
                   trend phase is over. Price  trend phase is over.
                   will now oscillate within   
                   the bands (channel trend).  

  Action on        Set band_walk_active =      Same, with bear direction.
  Termination      false. Transition to        
                   CHANNEL_TREND phase.        
                   Complete the final sub-leg. 
                   Record total band walk      
                   metrics.                    

  What Does NOT    Wick crosses below ZLEMA    Wick crosses above ZLEMA
  Terminate        but close remains above.    but close remains below.
                   This is normal within a     
                   band walk.                  
  ------------------------------------------------------------------------

**Band Walk Exhaustion Counter**

Band walks tend to produce 3-5 impulse sub-legs before transitioning to
channel trend. As the sub-leg count increases, the probability of
termination increases. The algorithm tracks this:

> band_walk_sub_leg_count = number of completed impulse sub-legs
>
> if band_walk_sub_leg_count \>= 3:
>
> → Flag: BAND_WALK_MATURE (increased probability of termination)
>
> → Compare successive sub-leg efficiency ratios for degradation
>
> → If efficiency declining across sub-legs: BAND_WALK_EXHAUSTING

**Integration with Main Framework:** The Band Walk Exhaustion Counter
maps directly to Axiom 4 (trends degrade before they end). When
BAND_WALK_EXHAUSTING is flagged, the main framework\'s weakness/strength
scorer receives a boost, increasing the composite score toward the
distribution/accumulation threshold.

**A4.3 Squeeze Breakout Mode --- Highest Priority Legs**

Legs emerging from a Bollinger Band squeeze are often the highest
quality and most explosive. This mode detects squeezes and flags the
first leg out of them for special treatment.

**Squeeze Detection**

> bandwidth = (Upper Band - Lower Band) / ZLEMA
>
> bandwidth_percentile = percentile_rank(bandwidth, lookback=100)
>
> if bandwidth_percentile \<= 20:
>
> → squeeze_active = true
>
> → squeeze_start_bar = current bar (if newly entering squeeze)

**Squeeze Breakout Leg**

> When squeeze_active = true AND price touches either band:
>
> if bar.high \>= entry_zlbb.upper_band:
>
> → squeeze_breakout_direction = BULL
>
> → Begin tracking breakout leg
>
> if bar.low \<= entry_zlbb.lower_band:
>
> → squeeze_breakout_direction = BEAR
>
> → Begin tracking breakout leg
>
> Set squeeze_active = false
>
> Set leg_quality = SQUEEZE_BREAKOUT (highest priority)

**Special Rules for Squeeze Breakout Legs**

  ------------------------------------------------------------------------
  **Rule**         **Specification**           **Rationale**
  ---------------- --------------------------- ---------------------------
  Minimum Bar      Relaxed to 1 bar (instead   Squeeze breakouts can be a
  Count            of 3)                       single explosive bar that
                                               immediately hits the
                                               opposite band. Do not delay
                                               recognition.

  Minimum          Relaxed to 0.25x ATR        Squeeze conditions mean ATR
  Displacement     (instead of 0.5x)           is already compressed. A
                                               smaller move is still
                                               significant relative to the
                                               squeeze range.

  Scanner Priority Flagged as HIGHEST_PRIORITY Squeeze breakout legs have
                   in the setup scanner        the best expectancy. The
                                               algorithm should prioritize
                                               these over all other
                                               setups.

  Band Walk        If the breakout leg leads   Squeeze → Band Walk is the
  Transition       directly into a band walk   classic explosive breakout
                   (price stays near the       pattern.
                   breakout band), transition  
                   to Band Walk Mode           
                   immediately.                

  Failed Squeeze   If price touches one band   This is a powerful reversal
  Breakout         from squeeze then reverses  signal. Traders who entered
                   to the opposite band within the breakout direction are
                   5 bars, flag as             trapped.
                   FAILED_SQUEEZE_BREAKOUT.    
  ------------------------------------------------------------------------

**A4.4 Mode Selection State Machine**

The algorithm automatically selects the appropriate leg detection mode
based on market context. Only one mode is active at a time.

> MODE PRIORITY (highest to lowest):
>
> 1\. SQUEEZE_BREAKOUT (if squeeze was active and band touched)
>
> 2\. BAND_WALK (if band walk conditions are met)
>
> 3\. NORMAL (default)
>
> TRANSITIONS:
>
> NORMAL → SQUEEZE_BREAKOUT:
>
> When squeeze_active = true AND price touches a band
>
> SQUEEZE_BREAKOUT → BAND_WALK:
>
> When breakout leg stays within 0.5σ of the breakout band
>
> for N consecutive bars after the breakout
>
> SQUEEZE_BREAKOUT → NORMAL:
>
> When breakout leg completes (reaches opposite band)
>
> without entering band walk
>
> BAND_WALK → CHANNEL_TREND (Phase Transition):
>
> When price closes beyond ZLEMA to opposite side
>
> → Leg detection reverts to NORMAL mode
>
> → Phase classifier receives CHANNEL_TREND signal
>
> NORMAL → BAND_WALK:
>
> When price stays within 0.5σ of a band for N bars
>
> (can enter band walk without a squeeze preceding it)
>
> BAND_WALK → NORMAL:
>
> Only via CHANNEL_TREND transition. Band walk does not
>
> revert directly to Normal without phase transition.

**A4.5 Retroactive Leg Validation (Near-Band Reversal)**

Normal Mode requires a band touch to register a tentative leg end. This
creates a gap: if price crosses above ZLEMA, moves strongly toward the
upper band but never touches it, then reverses through ZLEMA all the way
to the lower band, the system would record zero completed legs for the
entire up move. The up-leg is lost and the down-leg\'s start point is
ambiguous. Retroactive Leg Validation solves this by letting the
market\'s subsequent behavior resolve the ambiguity.

**The Problem (Concrete Example)**

> Upper Band ═══════════════════════════════════════════
>
> X ← price reaches here (close to band)
>
> / but NEVER touches band
>
> /
>
> / \\
>
> ZLEMA ────────────────/──────\\──────────────────────
>
> / \\
>
> / \\
>
> / \\
>
> Lower Band ═══════A══════════════B════════════════════
>
> Current logic: A → X is NOT a completed leg (no upper band touch).
>
> X → B is NOT a completed leg (leg start undefined).
>
> Result: The entire A → X → B sequence is one unresolved mess.
>
> Correct interpretation: A → X is a bull leg. X → B is a bear leg.
>
> X was a genuine turning point even without touching the band.

**The Solution: Retroactive Validation**

When price fails to reach the opposite band but then reverses through
ZLEMA and completes a leg in the opposite direction, the system
retroactively evaluates whether the prior move qualifies as a completed
leg. The reversal itself is what resolves the ambiguity --- the system
never guesses in real-time.

**Trigger Condition**

> RETROACTIVE VALIDATION TRIGGERS WHEN ALL ARE TRUE:
>
> 1\. A leg is in progress (e.g., IN_BULL_LEG after touching lower band)
>
> 2\. Price crossed above ZLEMA (confirmed bull side)
>
> 3\. Price NEVER touched the upper band (no tentative_end registered)
>
> 4\. Price reverses: a bar CLOSES BELOW ZLEMA (ZLEMA break)
>
> 5\. Subsequently, the reversal completes:
>
> EITHER price touches the lower band (full opposite leg)
>
> OR price closes below ZLEMA and a new ZLEMA break occurs
>
> in the opposite direction (confirming the reversal was real)
>
> At this point, the system asks: was the prior up-move a valid leg?

**Qualification Score**

The prior move is evaluated against 6 parameters. Each parameter
contributes 0 or 1 to a composite score. The move must score \>= 3 to be
retroactively validated as a completed leg.

  ----------------------------------------------------------------------------------------------------------
  **Parameter**   **Calculation**                 **Threshold**           **Score**   **Rationale**
  --------------- ------------------------------- ----------------------- ----------- ----------------------
  P1: Band        proximity_ratio = (leg_high -   proximity_ratio \>=     +1          The closer to the
  Proximity       ZLEMA_at_high) /                0.60 (price covered at              band, the more likely
                  (upper_band_at_high -           least 60% of the                    the band would have
                  ZLEMA_at_high). Measures how    distance from ZLEMA to              been reached. 60%
                  much of the ZLEMA-to-band       band)                               means price was well
                  distance price covered. 0.0 =                                       into the upper half of
                  at ZLEMA, 1.0 = at band.                                            the envelope.

  P2: Leg         leg_displacement = leg_high -   leg_displacement_atr    +1          Ensures the move was
  Displacement    leg_start (absolute move from   \>= 0.50 (at least half             structurally
                  leg start to highest point).    an ATR of movement)                 meaningful, not a
                  Normalized:                                                         minor wiggle above
                  leg_displacement_atr =                                              ZLEMA.
                  leg_displacement /                                                  
                  ATR(instance_tf, 14).                                               

  P3: Distance    zlema_distance = (leg_high -    zlema_distance \>= 1.0σ +1          If price got \> 1.0σ
  Above ZLEMA     ZLEMA_at_high) / sigma_at_high. (price reached the                  above ZLEMA, it was
                  Measures how many standard      upper half of the band              statistically extended
                  deviations above ZLEMA the high envelope, beyond 1 std              even without touching
                  reached.                        dev)                                the 2σ band. This is
                                                                                      significant.

  P4: Duration    bars_above_zlema = count of     bars_above_zlema \>= 5  +1          A sustained move above
  Above ZLEMA     consecutive bars where Close \>                                     ZLEMA indicates
                  ZLEMA during the up-move                                            structural commitment.
                  (before the ZLEMA break                                             A 1-2 bar spike is
                  reversal).                                                          noise; 5+ bars is a
                                                                                      genuine leg.

  P5: Reversal    Does the reversal leg reach the reversal_reaches_band   +1          If the market
  Completion      opposite band?                  == True                             completes a full
                  reversal_reaches_band = True if                                     reversal to the
                  price subsequently touches the                                      opposite band, the
                  lower band.                                                         turning point at X was
                                                                                      genuine. The reversal
                                                                                      itself validates the
                                                                                      high.

  P6: Reversal    reversal_efficiency =           reversal_efficiency \>= +1          A fast, efficient
  Efficiency      abs(reversal_displacement) /    0.50 (reversal is                   reversal confirms the
                  cumulative_range_of_reversal.   reasonably direct, not              high was a real
                  How clean and decisive is the   choppy)                             turning point. A slow,
                  reversal from the high?                                             choppy decline
                                                                                      suggests the high was
                                                                                      not structurally
                                                                                      important.
  ----------------------------------------------------------------------------------------------------------

**Decision Logic**

> total_score = P1 + P2 + P3 + P4 + P5 + P6
>
> if total_score \>= 4:
>
> → VALIDATED (HIGH CONFIDENCE)
>
> → Retroactively record:
>
> Bull leg: start = original leg_start, end = leg_high
>
> Bear leg: start = leg_high, end = current position (ongoing)
>
> → Leg quality: score normally via A8, no penalty.
>
> if total_score == 3:
>
> → VALIDATED (MODERATE CONFIDENCE)
>
> → Retroactively record same as above.
>
> → Leg quality: flag as NEAR_MISS. Apply -1 penalty to A8
>
> leg quality score (the turning point was less definitive).
>
> if total_score \<= 2:
>
> → NOT VALIDATED
>
> → Do NOT record the up-move as a separate leg.
>
> → Merge entire move into the subsequent leg:
>
> The leg from original start to the eventual lower band touch
>
> is recorded as a single, complex, inefficient leg.
>
> → This merged leg will naturally score low on efficiency
>
> (displacement/cumulative_range will be poor).

**Timing: When Validation Executes**

> CRITICAL: Retroactive validation is NOT real-time.
>
> The validation check runs ONLY AFTER the reversal is confirmed:
>
> CASE A: Reversal reaches opposite band
>
> The reversal leg completes normally (touches lower band).
>
> At this point, run retroactive validation on the prior up-move.
>
> If validated: record 2 completed legs (up + down).
>
> If not: record 1 merged leg (start to lower band).
>
> CASE B: Reversal completes via second ZLEMA break
>
> Price breaks below ZLEMA (ending the up-move), moves down,
>
> then breaks back above ZLEMA (ending the down-move) without
>
> touching the lower band. This second ZLEMA break triggers
>
> retroactive validation for BOTH the prior up-move AND
>
> the down-move (the down-move itself is a candidate for
>
> retroactive validation since it didn\'t reach the lower band).
>
> Apply the same 6-parameter scoring to each.
>
> CASE C: Chain of near-misses
>
> In ranging/choppy markets, price may oscillate between
>
> ZLEMA and each band without touching either band.
>
> Each ZLEMA break triggers retroactive validation of the
>
> prior move. The qualification score naturally filters:
>
> low-displacement oscillations will score \< 3 and be merged.
>
> Only genuine swings will pass.

**For Bear Legs (Inverted)**

All logic is symmetrical. For a potential bear leg that fails to reach
the lower band: replace leg_high with leg_low, upper band with lower
band, \"above ZLEMA\" with \"below ZLEMA\", and invert all direction
references. The same 6 parameters apply with the same thresholds.

**Integration with A4.1 Normal Mode**

Retroactive Leg Validation is an extension of Normal Mode, not a
separate mode. It triggers within the Normal Mode state machine when
specific conditions are met. The A4.4 Mode Selection logic is unchanged
--- Retroactive Validation operates inside Normal Mode.

> UPDATED A4.1 NORMAL MODE FLOW:
>
> 1\. LEG START: Band touch (unchanged)
>
> 2\. TRACKING: Monitor price movement (unchanged)
>
> 3\. POTENTIAL LEG END: Band touch → tentative_end (unchanged)
>
> 4\. CONFIRMATION: ZLEMA rejection/break (unchanged)
>
> 5\. NEW --- NEAR-MISS DETECTION:
>
> If price is IN_BULL_LEG AND has crossed above ZLEMA
>
> AND tentative_end is NOT set (no upper band touch):
>
> Track: near_miss_candidate = {
>
> leg_high, ZLEMA_at_high, upper_band_at_high, sigma_at_high,
>
> bars_above_zlema, leg_start, leg_displacement
>
> }
>
> 6\. NEW --- ZLEMA BREAK WITHOUT BAND TOUCH:
>
> If price CLOSES BELOW ZLEMA while near_miss_candidate exists:
>
> → Do NOT immediately validate. Store the candidate.
>
> → Begin tracking the reversal move (potential bear leg).
>
> → When reversal completes (opposite band touch OR second
>
> ZLEMA break): run retroactive validation scoring.
>
> → Based on score: split into 2 legs OR merge into 1.

**State Variables Added**

  ----------------------------------------------------------------------------------------
  **Variable**                      **Type**      **Description**
  --------------------------------- ------------- ----------------------------------------
  near_miss_candidate               Object \|     Stored when price crosses above ZLEMA
                                    null          during a bull leg but hasn\'t touched
                                                  the upper band. Contains: leg_high,
                                                  ZLEMA_at_high, band_at_high,
                                                  sigma_at_high, bars_above_zlema,
                                                  leg_start, leg_displacement.

  awaiting_retroactive_validation   Boolean       True when a near-miss candidate exists
                                                  and price has broken back through ZLEMA.
                                                  The system is waiting for the reversal
                                                  to complete before validating.

  reversal_start                    Float         Price at which the reversal began (the
                                                  near-miss high/low). Used to compute
                                                  reversal efficiency.

  reversal_bars                     List\<Bar\>   Bars accumulated during the reversal,
                                                  used to compute reversal_efficiency and
                                                  reversal_displacement.
  ----------------------------------------------------------------------------------------

**Tunable Parameters Added**

  -------------------------------------------------------------------------------
  **Parameter**      **Default**   **Range**   **Affects**
  ------------------ ------------- ----------- ----------------------------------
  Proximity Ratio    0.60          0.50 --     How close to band is \'close
  Threshold (P1)                   0.75        enough\'. Lower = more lenient.

  Leg Displacement   0.50 x ATR    0.30 --     Minimum move size for near-miss
  Threshold (P2)                   0.75 x ATR  legs.

  ZLEMA Distance     1.0σ          0.75 --     How far above ZLEMA price must
  Threshold (P3)                   1.5σ        reach.

  Duration Threshold 5 bars        3 -- 8 bars Minimum bars above ZLEMA.
  (P4)                                         

  Reversal           0.50          0.35 --     How clean the reversal must be.
  Efficiency                       0.65        
  Threshold (P6)                               

  Validation Score   4             3 -- 5      Score for high-confidence
  Threshold (High)                             validation.

  Validation Score   3             2 -- 4      Minimum score to validate at all.
  Threshold (Min)                              
  -------------------------------------------------------------------------------

**A5. Channel Trend Phase**

Channel Trend is a new sub-phase introduced by the ZLBB system. It sits
between Band Walk (strong trend) and Balance (range) in the market
cycle. It represents a maturing trend where price still makes
directional progress but now respects both bands of the Entry TF ZLBB.

**A5.1 Definition**

  ------------------------------------------------------------------------
  **Attribute**    **Bull Channel Trend**      **Bear Channel Trend**
  ---------------- --------------------------- ---------------------------
  Transition From  Bull Band Walk terminates   Bear Band Walk terminates
                   (close below ZLEMA)         (close above ZLEMA)

  Price Behavior   Price oscillates between    Price oscillates between
                   Upper and Lower Entry TF    bands with bearish bias:
                   ZLBB bands with a bullish   lower highs touching Upper
                   bias: higher lows touching  Band, lower lows touching
                   Lower Band, higher highs    Lower Band.
                   touching Upper Band.        

  Leg Detection    Normal Mode resumes. Each   Same, with bear context.
                   band-to-band traversal =    
                   one leg, confirmed by ZLEMA 
                   break.                      

  Trend Direction  Still bullish (price making Still bearish, slower pace.
                   directional progress        
                   upward), but at a slower    
                   pace than band walk.        

  Key Difference   Price makes net directional Same.
  from Balance     progress across successive  
                   legs. Not range-bound.      

  Key Difference   Price now reaches both      Same.
  from Band Walk   bands. Not walking one      
                   band.                       
  ------------------------------------------------------------------------

**A5.2 Channel Trend → Balance Transition**

Channel Trend transitions to Balance when directional progress stops.
Detection:

> Compare the last 3 completed legs:
>
> if net_displacement across 3 legs ≈ 0 (within 0.25 ATR):
>
> → Phase = BALANCE
>
> if successive impulse legs show declining displacement
>
> AND pullback legs show increasing displacement:
>
> → Phase transitioning toward BALANCE

**Full Market Cycle via ZLBB:** Squeeze → Squeeze Breakout → Band Walk
(strong trend) → Channel Trend (maturing trend) → Balance (range) →
Squeeze (cycle repeats). Not every cycle includes all phases, but this
is the canonical sequence.

**A6. Exhaustion Zone Detection via Trigger TF ZLBB**

The Trigger TF ZLBB outer bands define statistically extreme zones on
the larger timeframe. When price reaches these bands, the current move
is extended and has a higher probability of pausing, reversing, or
transitioning.

**A6.1 Zone Definitions**

  ------------------------------------------------------------------------
  **Zone**      **Condition**      **Interpretation**   **Action**
  ------------- ------------------ -------------------- ------------------
  Upper         Price wick touches Bull move            No new longs. Seek
  Exhaustion    or exceeds the     statistically        short setups or
                Trigger TF ZLBB    extended. Upside     signs of weakness.
                upper band         momentum likely to   Tighten stops on
                                   slow.                existing longs.

  Lower         Price wick touches Bear move            No new shorts.
  Exhaustion    or exceeds the     statistically        Seek long setups
                Trigger TF ZLBB    extended. Downside   or signs of
                lower band         momentum likely to   strength. Tighten
                                   slow.                stops on existing
                                                        shorts.

  Mid Zone      Price near Trigger Fair value on the    Rely on Entry TF
                TF ZLEMA (±0.5σ)   larger timeframe. No ZLBB and phase
                                   directional bias     classification for
                                   from HTF envelope.   direction.

  Expansion     Price between      Active trend on      Trade with trend.
  Zone          ZLEMA and outer    larger timeframe.    Entry TF legs in
                band, Trigger TF   Room to continue.    trend direction
                bandwidth                               are valid.
                expanding                               
  ------------------------------------------------------------------------

**A6.2 Double Band Exhaustion (Confluence Signal)**

The highest-conviction exhaustion signal occurs when BOTH ZLBB instances
signal exhaustion simultaneously:

> DOUBLE_EXHAUSTION_BULL:
>
> (bar.high \>= entry_zlbb.upper) AND (bar.high \>= trigger_zlbb.upper)
>
> → Move exhausted on BOTH local and macro timeframe.
>
> → Strongest reversal/pause signal in the system.
>
> → Override: No new longs. Actively seek reversal signals.
>
> DOUBLE_EXHAUSTION_BEAR:
>
> (bar.low \<= entry_zlbb.lower) AND (bar.low \<= trigger_zlbb.lower)
>
> → Strongest signal that down move is exhausted.
>
> → Override: No new shorts. Actively seek reversal signals.

**A7. Phase Detection Enhancement via Bandwidth**

**A7.1 Bandwidth Metrics**

  ------------------------------------------------------------------------
  **Metric**        **Calculation**         **Usage**
  ----------------- ----------------------- ------------------------------
  Entry TF          (Upper - Lower) / ZLEMA Normalized local volatility.
  Bandwidth                                 Low = squeeze/balance. High =
                                            trend/breakout.

  Trigger TF        (Upper - Lower) / ZLEMA Normalized macro volatility.
  Bandwidth                                 Same interpretation at larger
                                            scale.

  Bandwidth Ratio   Entry TF Bandwidth /    How much of macro volatility
                    Trigger TF Bandwidth    is expressed locally. Low =
                                            LTF quiet within HTF move.
                                            High = LTF matching HTF
                                            (breakout/climax).

  Squeeze Detection bandwidth_percentile    Precedes breakouts. Maps to
                    \<= 20 (vs 100-bar      BALANCE approaching BREAKOUT.
                    lookback)               
  ------------------------------------------------------------------------

**A7.2 Phase Classifier Augmentation**

**BALANCE confirmation:** Main classifier detects BALANCE AND Entry TF
bandwidth contracting (declining over N bars) → confidence in BALANCE
increases. If at squeeze level, flag as BALANCE_SQUEEZE (high breakout
probability).

**TREND confirmation:** Main classifier detects TREND AND Trigger TF
bandwidth expanding → confidence in trend continuation increases. If
Trigger TF bandwidth contracting while in TREND → early signal of phase
transition (distribution/accumulation).

**BREAKOUT confirmation:** Entry TF bandwidth expanding sharply (\> 1.5x
its 10-bar average) while price exits the Trigger TF ZLBB mid-zone →
quantitative breakout confirmation supplementing main framework Section
5.1.

**BAND_WALK identification:** Entry TF bandwidth high + Trigger TF
bandwidth expanding + price within 0.5σ of one band → strong trend
phase. Usually corresponds to HTF breakout.

**CHANNEL_TREND identification:** Entry TF bandwidth moderate + price
respecting both bands + net directional progress across legs → maturing
trend phase.

**A8. Leg Quality Scoring**

Not all legs are equal. Each completed leg receives a quality score that
determines its weight in the Effort vs. Result engine and its priority
in the setup scanner.

  ---------------------------------------------------------------------------
  **Factor**           **Score          **Rationale**
                       Contribution**   
  -------------------- ---------------- -------------------------------------
  Detection Mode:      +3               Highest quality legs emerge from
  Squeeze Breakout                      squeezes. Maximum energy release.

  Detection Mode: Band +2               Band walk legs are trend-aligned and
  Walk Sub-Leg                          structurally strong.

  Detection Mode:      +1               Base quality. No special context.
  Normal                                

  Trigger TF ZLBB      +2 if leg        Legs aligned with HTF momentum have
  Alignment            direction        higher follow-through probability.
                       matches Trigger  
                       TF ZLBB trend    
                       (price moving    
                       toward Trigger   
                       TF outer band)   

  Trigger TF ZLBB      -1 if leg        Counter-HTF legs are lower
  Opposition           direction        probability and often just pullbacks.
                       opposes Trigger  
                       TF ZLBB trend    
                       (price moving    
                       back toward      
                       Trigger TF       
                       ZLEMA)           

  Efficiency Ratio \>  +1               High-efficiency legs indicate clean,
  0.6                                   decisive moves.

  Efficiency Ratio \<  -1               Low-efficiency legs indicate choppy,
  0.3                                   uncertain moves.

  Preceded by Failed   +2 (if leg       Trapped trader fuel creates
  Signal               follows a failed high-conviction follow-through.
                       breakout, failed 
                       weakness,        
                       spring, or       
                       upthrust)        
  ---------------------------------------------------------------------------

> leg_quality_score = sum of all applicable factors
>
> Quality Tiers:
>
> Score \>= 5 → A-GRADE (trade with full position size)
>
> Score 3-4 → B-GRADE (trade with standard position size)
>
> Score 1-2 → C-GRADE (trade with reduced position size or skip)
>
> Score \<= 0 → D-GRADE (skip --- noise or counter-trend)

**A9. Integration Map to Main Framework**

  -------------------------------------------------------------------------------
  **Main Framework **Section**   **DT-ZLBB Enhancement**  **Replaces or
  Component**                                             Supplements?**
  ---------------- ------------- ------------------------ -----------------------
  Swing Detection  3.2           Entry TF ZLBB band       Supplements. ZLBB
  (Module 4)                     touches define swing     primary, swing points
                                 extremes, confirmed by   as validation.
                                 ZLEMA break              

  Leg Construction 3.2           Three-mode leg           Replaces. ZLBB legs are
  (Module 6)                     detection: Normal (ZLEMA more objective and
                                 confirmed), Band Walk    volatility-adaptive.
                                 (25% retracement),       
                                 Squeeze Breakout         
                                 (relaxed thresholds)     

  Exhaustion       3.3           Trigger TF ZLBB outer    Supplements. Adds
  Detection                      bands + Double Band      quantitative threshold
                                 Exhaustion confluence    to visual signals.
                                 signal                   

  Phase            2.1           Bandwidth metrics + Band Supplements. Adds new
  Classification                 Walk / Channel Trend /   sub-phases and
  (Module 7)                     Squeeze phase            confidence scoring.
                                 identification           

  Breakout         5.1           Squeeze detection        Supplements. Additional
  Qualification                  (bandwidth percentile) + filter for breakout
                                 bandwidth expansion +    quality.
                                 Trigger TF band exit     

  Entry Trigger    5             Double band exhaustion,  Supplements. Adds
  (Module 11)                    squeeze breakout legs,   high-conviction
                                 failed squeeze breakout  confluence conditions.

  Effort vs.       3.2           Leg metrics from         Enhances. Better leg
  Result (Module                 ZLBB-defined legs with   boundaries + quality
  9)                             quality scoring; band    weighting.
                                 walk sub-leg degradation 
                                 tracking                 

  Market Cycle     2.3           Squeeze → Breakout →     Enhances. Adds
                                 Band Walk → Channel      granularity to the
                                 Trend → Balance →        phase transition model.
                                 Squeeze (full cycle)     
  -------------------------------------------------------------------------------

**A10. Implementation Requirements**

**A10.1 State Variables**

State is maintained at two levels: (a) per-instance state for each of
the 6 ZLBB instances (1H, 4H, Daily, Weekly, Monthly, 15min), and (b)
cross-instance state for the entry system.

**Per-Instance State (one copy per ZLBB instance)**

  ---------------------------------------------------------------------------------
  **Variable**              **Type**              **Description**
  ------------------------- --------------------- ---------------------------------
  zlbb                      Object{upper, lower,  Current ZLBB band values for this
                            zlema, sigma,         TF instance.
                            bandwidth}            

  leg_mode                  Enum{NORMAL,          Current leg detection mode for
                            BAND_WALK,            this TF.
                            SQUEEZE_BREAKOUT}     

  leg_state                 Enum{SEEKING_START,   Current leg state within the
                            IN_BULL_LEG,          active mode.
                            IN_BEAR_LEG}          

  tentative_end             Object{active, price, Pending leg end awaiting ZLEMA
                            bar_index} \| null    confirmation (Normal mode).

  current_leg               Leg object (partial)  Accumulating metrics for the leg
                                                  in progress.

  completed_legs\[\]        List\<Leg\>           All completed legs for this TF.
                                                  Leg extremes feed Addendum B SR
                                                  detection.

  band_walk_active          Boolean               True when in Band Walk mode on
                                                  this TF.

  band_walk_direction       Enum{BULL, BEAR} \|   Direction of active band walk.
                            null                  

  band_walk_sub_leg_count   Integer               Count of completed impulse
                                                  sub-legs in current band walk.

  sub_leg_high /            Float                 Extreme of the current band walk
  sub_leg_low                                     sub-leg.

  squeeze_active            Boolean               True when bandwidth at squeeze
                                                  level for this TF.

  bandwidth_percentile      Float (0-100)         Current bandwidth vs lookback.

  phase_zlbb                Enum{SQUEEZE,         ZLBB-derived phase for this TF.
                            BREAKOUT, BAND_WALK,  
                            CHANNEL_TREND,        
                            BALANCE}              
  ---------------------------------------------------------------------------------

**Cross-Instance State (Entry System + Pairing)**

  ---------------------------------------------------------------------------------
  **Variable**             **Type**               **Description**
  ------------------------ ---------------------- ---------------------------------
  entry_zlbb               Reference to           Alias for 15min ZLBB. Used for
                           ZLBB_15min instance    entry leg detection. Its
                                                  completed legs are 1H-level
                                                  turning points.

  trigger_zlbb             Reference to ZLBB_4H   Alias for 4H ZLBB. Used for
                           instance               exhaustion zones on 15min chart.
                                                  Its completed legs are
                                                  Daily-level turning points.

  double_exhaustion_bull   Boolean                Both 15min and 4H bands signal
                                                  upper exhaustion simultaneously.

  double_exhaustion_bear   Boolean                Both 15min and 4H bands signal
                                                  lower exhaustion simultaneously.

  zlbb_instances{}         Dict\<TF,              All 6 ZLBB instances keyed by
                           ZLBBInstance\>         computation TF: {15min, 1H, 4H,
                                                  Daily, Weekly, Monthly}. Each
                                                  instance\'s completed_legs\[\]
                                                  are attributed to the next higher
                                                  TF per the pairing table
                                                  (A3.2.2).

  sr_leg_extremes{}        Dict\<TF,              Leg extremes organized by the TF
                           List\<LegExtreme\>\>   they are attributed to: {1H: from
                                                  ZLBB_15min, 4H: from ZLBB_1H,
                                                  Daily: from ZLBB_4H, Weekly: from
                                                  ZLBB_Daily, Monthly: from
                                                  ZLBB_Weekly, Quarterly: from
                                                  ZLBB_Monthly}. This is the direct
                                                  input to Addendum B Method 1.
  ---------------------------------------------------------------------------------

**A10.2 Computation Order Per Entry TF Bar**

> 1\. UPDATE ZLBB VALUES
>
> if new_trigger_tf_bar: recompute trigger_zlbb
>
> else: carry forward trigger_zlbb
>
> Compute entry_zlbb from latest Entry TF OHLCV
>
> 2\. COMPUTE BANDWIDTH METRICS
>
> bandwidth = entry_zlbb.bandwidth
>
> bandwidth_percentile = percentile_rank(bandwidth, 100)
>
> bandwidth_ratio = entry_zlbb.bandwidth / trigger_zlbb.bandwidth
>
> 3\. CHECK SQUEEZE STATE
>
> squeeze_active = (bandwidth_percentile \<= 20)
>
> 4\. CHECK EXHAUSTION ZONES
>
> double_exhaustion_bull = bar.high \>= entry_zlbb.upper
>
> AND bar.high \>= trigger_zlbb.upper
>
> double_exhaustion_bear = bar.low \<= entry_zlbb.lower
>
> AND bar.low \<= trigger_zlbb.lower
>
> 5\. SELECT LEG DETECTION MODE
>
> if squeeze_active AND band_touched: mode = SQUEEZE_BREAKOUT
>
> elif band_walk_active: mode = BAND_WALK
>
> elif check_band_walk_entry_conditions(): mode = BAND_WALK (new)
>
> else: mode = NORMAL
>
> 6\. RUN LEG DETECTION (per active mode)
>
> Execute mode-specific logic (A4.1 / A4.2 / A4.3)
>
> if leg completed:
>
> compute leg metrics
>
> compute leg quality score (A8)
>
> append to legs\[\]
>
> 7\. UPDATE ZLBB PHASE
>
> Determine phase_zlbb from bandwidth + mode + leg behavior
>
> 8\. FEED INTO MAIN FRAMEWORK
>
> Pass updated state to main framework decision tree (Step 4+)

**A10.3 Tunable Parameters**

  -----------------------------------------------------------------------------
  **Parameter**      **Default**   **Range to    **Affects**
                                   Test**        
  ------------------ ------------- ------------- ------------------------------
  ZLEMA Period       20            14 -- 30      Band width, ZLEMA
                                                 responsiveness

  Std Dev Multiplier 2.0           1.5 -- 2.5    Band width, signal sensitivity
  (D)                                            

  Min Leg Bars       3             2 -- 5        Noise filtering vs. signal
  (Normal)                                       delay

  Min Leg Bars       1             1 -- 3        Capture explosive moves vs.
  (Squeeze Breakout)                             noise

  Min Leg            0.5x ATR      0.3 -- 1.0x   Leg significance threshold
  Displacement                     ATR           
  (Normal)                                       

  Min Leg            0.25x ATR     0.15 -- 0.5x  Squeeze leg significance
  Displacement                     ATR           
  (Squeeze)                                      

  Band Walk          0.5           0.3 -- 0.75   How close to band = band walk
  Proximity (σ                                   
  multiple)                                      

  Band Walk Entry    5             3 -- 8        Bars near band to trigger band
  Bars (N)                                       walk

  Band Walk          25%           20% -- 35%    Sub-leg completion sensitivity
  Retracement %                                  

  Squeeze Percentile 20            10 -- 30      Squeeze detection sensitivity
  Threshold                                      

  Bandwidth Lookback 100           50 -- 200     Percentile calculation window
  -----------------------------------------------------------------------------

**A10.4 Whipsaw Mitigation**

Because ZLBB is more responsive than standard BB, it is more susceptible
to whipsaws. The following filters are built into the detection logic:

**Filter 1 --- ZLEMA Confirmation (Normal Mode):** Band touches are
tentative until confirmed by ZLEMA break. This is the primary whipsaw
filter. A touch-and-bounce that doesn\'t break ZLEMA is not a leg end.

**Filter 2 --- Minimum Bar Count:** 3-bar minimum in Normal mode
prevents micro-oscillations from registering as legs. Relaxed to 1 bar
only for squeeze breakouts.

**Filter 3 --- Minimum Displacement:** Legs below the ATR-relative
displacement threshold are merged with the previous leg, not recorded
independently.

**Filter 4 --- Confirmation Bar for Exhaustion:** For Double Band
Exhaustion signals, require the next bar to confirm (rejection bar or
close back inside the band). A single wick touch without follow-through
is downgraded from signal to watch.

**Filter 5 --- Phase Context Override:** Counter-trend signals during
strong band walks are ignored. If band_walk_active = true and a signal
opposes the walk direction, it must meet a higher bar (e.g., ZLEMA
close) to be acted upon.

**Filter 6 --- Band Walk Sub-Leg Validation:** 25% retracement must
include at least 1 bear bar (in bull walk) or 1 bull bar (in bear walk).
Pure wick retracements without a counter-trend bar body are not sub-leg
completions.
