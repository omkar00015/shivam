**ADDENDUM B (v2)**

**Support & Resistance Detection System**

Multi-Timeframe SR Zones, ZLBB Leg Extremes, Midpoint Discovery &
Fakeout Setups

Integrating: Prasad Top-Down Methodology + ZLBB Leg System + 50%
Midpoint Theorem

Supplement to: Algorithmic Trading Framework v1.0 + Addendum A v2 \|
February 2026

**B1. Structural Primitive: ZLBB Leg Extremes**

The foundational primitive for SR detection is the ZLBB leg extreme
(defined in Addendum A). Every completed ZLBB leg produces two data
points: the leg high (highest bar high during the leg) and the leg low
(lowest bar low during the leg). These extremes are the turning points
where price was statistically stretched (at a Bollinger Band) and
reversed with enough conviction to break equilibrium (ZLEMA close
confirmation).

**Why Not N-Bar Swings?** N-bar swing points (where a bar\'s high/low is
the highest/lowest within N bars on either side) use a fixed lookback
parameter that does not adapt to volatility. A 5-bar swing misses
structure in volatile markets and over-detects in quiet markets. The
same N produces inconsistent quality across trending vs. ranging
conditions. Every turning point an N-bar swing detects is already
captured by ZLBB leg extremes or by the rejection counting method. N-bar
swings add parameter-tuning burden for zero additional signal. They are
excluded from this system entirely.

**What ZLBB Leg Extremes Provide:** Volatility-adaptive turning points
(bands widen/contract with the market), structural meaning (each extreme
represents a genuine reversal confirmed by ZLEMA break), automatic
regime handling (Band Walk mode uses 25% retracement, Normal mode uses
band-to-band, Squeeze mode uses relaxed thresholds), and natural spacing
at market-appropriate intervals.

**B2. The Russian Doll Top-Down SR Method (From Prasad)**

Each higher timeframe provides context and each lower timeframe provides
precision. The algorithm implements this hierarchy with specific rules
for what to detect at each level.

**B2.1 Timeframe Hierarchy**

  ------------------------------------------------------------------------------------------
  **Step**   **Timeframe**   **Purpose**     **What to Detect**   **Priority Ranking**
  ---------- --------------- --------------- -------------------- --------------------------
  1          3-Month         Macro           Zones with maximum   1st: Multi-candle
                             structure.      candle rejections.   rejection clusters. 2nd:
                             Institutional   Opening/closing      Open/Close clusters. 3rd:
                             reference       price clusters. Long Wick extremes.
                             zones.          wicks showing        
                                             rejection.           

  2          Monthly         Refinement of   Align monthly levels Quarterly + Monthly
                             quarterly       with quarterly       overlap = HIGH PRIORITY.
                             zones.          zones. Monthly       Monthly-only = medium.
                                             open/close clusters. 
                                             Strong monthly       
                                             rejections.          

  3          Weekly          Directional     Weekly structure     Used for bias + SR only
                             bias. Buy-only  highs/lows. Weekly   when aligned with Steps
                             or sell-only.   rejection zones.     1-2.
                                             Weekly opens aligned 
                                             with HTF zones.      

  4          Daily           Structure and   Daily open price     Daily open always an SR
                             trap zone       (key level). Daily   level. Daily SR valid only
                             planning.       SR zones. Rejections near HTF zone.
                                             near HTF levels.     

  5          4-Hour          Execution zone  Maximum rejection    Final execution zones.
                             finalization.   areas. Wick          Must align with at least
                                             clusters. Failed     one higher TF.
                                             breakouts. Mark      
                                             zones not lines.     

  6          1-Hour          Trade           1H rejection zones   1H SR valid only when
                             management TF.  aligned with         within 0.5 ATR of a 4H+
                             Generates SR    4H/Daily zones. 1H   zone. Standalone 1H SR
                             for intraday    open/close clusters. used for intermediate
                             structure.      1H leg extremes.     targets and position
                                             Wick clusters near   management.
                                             HTF levels.          

  7          15-Minute       Entry timing    No fresh SR marking. Entry TF only. All SR
                             ONLY. No new SR Execute setups       comes from Steps 1-6.
                             generation.     identified at 1H+    
                                             zones. ZLBB leg      
                                             detection for entry  
                                             timing.              
  ------------------------------------------------------------------------------------------

**B2.2 Core Principles**

**P1: SR are zones, not lines.** Every SR level has width. Zone width is
ATR-relative to the generating timeframe, clamped by instrument-specific
min/max bounds (Section B3.5).

**P2: Higher TF = Stronger level.** A quarterly zone will hold against
daily-level pressure. The algo weights zones by source timeframe.

**P3: Confluence amplifies strength exponentially.** A price appearing
as SR on quarterly AND monthly AND weekly is exponentially stronger than
a single-TF zone. Scoring reflects this (Section B3.4).

**P4: Rejections are the primary signal.** The number of times price has
reacted at a level is the strongest validity indicator. Opens/closes are
secondary. Wicks are tertiary.

**P5: Price reacts because of liquidity and trapped participants.** SR
zones are where stop-losses cluster and institutions place orders.
Fakeout setups exploit this.

**P6: Lower TF only for execution.** The 15-minute chart does not
generate new SR. It is used solely to time entries at SR identified on
1H and above. The 1H chart is the lowest SR-generating timeframe.
Exception: 50% midpoint rule (Section B5) may generate midpoints visible
on 15min.

**B3. Algorithmic SR Zone Detection**

The algorithm uses three complementary detection methods, then merges,
applies time-decay, adds bonus signals, and scores the results. No N-bar
swing logic exists in this system.

**B3.1 Method 1: ZLBB Leg Extreme Clustering**

Primary SR detection method. Per the paired-TF ZLBB architecture
(Addendum A, Section A3.2), legs on each SR timeframe are detected by
running ZLBB on the NEXT LOWER timeframe. The extremes of completed legs
represent volatility-adaptive structural turning points. When multiple
extremes cluster near the same price, that price is an SR zone.

> LEG EXTREME SOURCE (from Addendum A paired architecture):
>
> 1H SR ← leg extremes from ZLBB_15min (15min ZLBB band-to-band = 1H
> leg)
>
> 4H SR ← leg extremes from ZLBB_1H (1H ZLBB band-to-band = 4H leg)
>
> Daily SR ← leg extremes from ZLBB_4H (4H ZLBB band-to-band = Daily
> leg)
>
> Weekly SR ← leg extremes from ZLBB_Daily (Daily band-to-band = Weekly
> leg)
>
> Monthly SR ← leg extremes from ZLBB_Weekly (Weekly band-to-band =
> Monthly leg)
>
> Quarterly SR ← leg extremes from ZLBB_Monthly (Monthly band-to-band =
> Quarterly leg)
>
> FOR each target_tf in \[1H, 4H, Daily, Weekly, Monthly, Quarterly\]:
>
> 1\. Collect all completed leg extremes from the paired ZLBB instance:
>
> 1H SR: from ZLBB_15min, last 200 15min bars (3.5 days)
>
> 4H SR: from ZLBB_1H, last 200 1H bars (8 days)
>
> Daily SR: from ZLBB_4H, last 200 4H bars (33 days)
>
> Weekly SR: from ZLBB_Daily, last 120 daily bars (6 months)
>
> Monthly SR: from ZLBB_Weekly, last 52 weekly bars (1 year)
>
> Quarterly SR: from ZLBB_Monthly, last 24 monthly bars (2 years)
>
> 2\. Separate into two lists:
>
> resistance_candidates\[\] = all leg_highs
>
> support_candidates\[\] = all leg_lows
>
> 3\. Define clustering threshold:
>
> cluster_width = 0.25 x ATR(timeframe, 14)
>
> 4\. Cluster algorithm (applied separately to resistance and support):
>
> a\) Sort candidates by price ascending
>
> b\) Initialize: cluster_start = candidate\[0\]
>
> c\) Walk through sorted list:
>
> if candidate\[i\] - cluster_start \<= cluster_width:
>
> → Add to current cluster
>
> else:
>
> → Finalize current cluster, start new one
>
> d\) After walk, finalize last cluster
>
> 5\. For each cluster with \>= 2 extremes:
>
> zone_center = weighted_mean(extremes, weight = recency)
>
> zone_upper = max(extremes) + zone_buffer
>
> zone_lower = min(extremes) - zone_buffer
>
> touch_count = count(extremes)
>
> source_tf = current timeframe
>
> signal_type = LEG_EXTREME
>
> sr_type = SUPPORT \| RESISTANCE \| BOTH (if both highs and lows
> present)
>
> recency = bar_index of most recent extreme in cluster

**B3.2 Method 2: Open/Close Price Clustering**

Prasad emphasizes opening and closing prices as institutional reference
points. This is especially important on quarterly and monthly timeframes
where open prices act as magnets for price.

> FOR each timeframe in \[3-Month, Monthly, Weekly, Daily\]:
>
> 1\. Collect all Open and Close prices within lookback:
>
> 3-Month: 8 bars (6 years)
>
> Monthly: 24 bars (2 years)
>
> Weekly: 52 bars (1 year)
>
> Daily: 120 bars (6 months)
>
> 2\. Cluster using identical algorithm as Method 1
>
> cluster_width = 0.25 x ATR(timeframe, 14)
>
> 3\. For each cluster with \>= 2 open/close prices:
>
> Create SR zone (same structure as Method 1)
>
> signal_type = OPEN_CLOSE
>
> 4\. SPECIAL: Current period opens (always SR, no clustering needed):
>
> current_day_open → SR zone, width = 0.15 x ATR(Daily, 14)
>
> current_week_open → SR zone, width = 0.15 x ATR(Weekly, 14)
>
> current_month_open → SR zone, width = 0.15 x ATR(Monthly, 14)
>
> signal_type = CURRENT_PERIOD_OPEN
>
> These auto-expire when the period closes.

**Session Opens (Forex/Crypto Specific)**

> 5\. SESSION OPEN SR (intraday only, expires at session close):
>
> FOREX SESSION OPENS:
>
> asia_open = price at 7:00 PM EST (5:30 AM IST)
>
> london_open = price at 3:00 AM EST (1:30 PM IST)
>
> ny_open = price at 8:00 AM EST (6:30 PM IST)
>
> CRYPTO SESSION OPENS:
>
> cme_open = price at CME futures open (6:00 PM EST Sunday)
>
> cme_close = price at CME futures close (5:00 PM EST Friday)
>
> For each session open:
>
> zone_center = session_open_price
>
> zone_width = 0.10 x ATR(4H, 14)
>
> signal_type = SESSION_OPEN
>
> expires_at = session close time
>
> strength = base +2 (moderate, short-lived)

**B3.3 Method 3: Rejection Counting**

Prasad ranks rejection count as the highest-priority signal for SR
validity. A rejection is any bar that approached a price level and was
repelled, leaving a significant wick.

> FOR each timeframe in \[1H, 4H, Daily, Weekly, Monthly, 3-Month\]:
>
> 1\. For each bar in lookback window, compute:
>
> upper_wick = High - max(Open, Close)
>
> lower_wick = min(Open, Close) - Low
>
> bar_range = High - Low
>
> 2\. A bar qualifies as a REJECTION if ALL conditions met:
>
> a\) wick_length \> 0.50 x bar_range
>
> (wick is majority of the bar)
>
> b\) bar_range \> 0.25 x ATR(timeframe, 14)
>
> (bar is not trivially small)
>
> c\) body_ratio \< 0.40
>
> (body is small relative to range, confirming rejection not trend bar)
>
> 3\. Collect rejection prices:
>
> Upper rejection: price = bar.High
>
> (sellers rejected buyers at this price)
>
> Lower rejection: price = bar.Low
>
> (buyers rejected sellers at this price)
>
> 4\. Cluster rejection prices:
>
> cluster_width = 0.25 x ATR(timeframe, 14)
>
> (same clustering algorithm as Method 1)
>
> 5\. For each cluster with \>= 3 rejections:
>
> Create SR zone
>
> rejection_count = count(rejections in cluster)
>
> signal_type = REJECTION
>
> sr_type = RESISTANCE if upper rejections, SUPPORT if lower,
>
> BOTH if mixed

**B3.4 Zone Merging, Time-Decay, and Strength Scoring**

**Step A: Merge Overlapping Zones**

> 1\. Collect ALL zones from Methods 1, 2, 3 across all timeframes
>
> 2\. Sort by zone_center price ascending
>
> 3\. Walk through sorted zones:
>
> if zone\[i+1\].zone_lower \<= zone\[i\].zone_upper:
>
> → Overlap detected. Merge:
>
> merged_center = weighted_mean(centers, weights = touch_count)
>
> merged_upper = max(all zone_uppers in merged group)
>
> merged_lower = min(all zone_lowers in merged group)
>
> Inherit ALL metadata: source TFs, touch counts,
>
> signal types, rejection counts, recency values
>
> else:
>
> → No overlap. Separate zone.

**Step B: Apply Time-Decay to Each Touch/Rejection**

A rejection from 2 years ago should not carry the same weight as one
from last week. Each individual touch or rejection\'s contribution to
the score is multiplied by a decay factor based on how many bars ago it
occurred on its native timeframe.

> FOR each touch/rejection in a zone:
>
> bars_ago = current_bar_index - touch_bar_index (on the touch\'s native
> TF)
>
> decay_multiplier = decay_rate \^ bars_ago
>
> DECAY RATES PER TIMEFRAME:
>
> 3-Month: 0.97 per bar (half-life ≈ 23 bars = 69 months)
>
> Monthly: 0.98 per bar (half-life ≈ 34 bars = 34 months)
>
> Weekly: 0.99 per bar (half-life ≈ 69 bars = 69 weeks)
>
> Daily: 0.995 per bar (half-life ≈ 138 bars = 138 days)
>
> 4H: 0.998 per bar (half-life ≈ 346 bars = 58 days)
>
> 1H: 0.999 per bar (half-life ≈ 693 bars = 29 days)
>
> EXAMPLE:
>
> A weekly rejection 20 bars ago:
>
> decay = 0.99\^20 = 0.818 (retains 82% of original contribution)
>
> A weekly rejection 100 bars ago:
>
> decay = 0.99\^100 = 0.366 (retains 37% of original contribution)
>
> Decayed contribution = base_score x decay_multiplier

**Step C: Strength Scoring (with Decay Applied)**

  --------------------------------------------------------------------------------------
  **Factor**       **Base    **Decay Applied?** **Cap**   **Rationale**
                   Score**                                
  ---------------- --------- ------------------ --------- ------------------------------
  3-Month TF       +5        No (TF weight is   ---       Highest authority timeframe.
  contributed                static)                      

  Monthly TF       +4        No                 ---       Second highest authority.
  contributed                                             

  Weekly TF        +3        No                 ---       Strong directional structure.
  contributed                                             

  Daily TF         +2        No                 ---       Moderate authority.
  contributed                                             

  4H TF            +1.5      No                 ---       Execution-level, higher than
  contributed                                             pure entry TF.

  1H TF            +0.75     No                 ---       Lowest SR-generating TF. Valid
  contributed                                             for intraday structure.

  Per rejection    +1 each   YES: each x        Cap sum   Prasad primary signal.
                             decay_multiplier   at +5     

  Per ZLBB leg     +0.75     YES: each x        Cap sum   Structural turn validation.
  extreme          each      decay_multiplier   at +3     

  Open/Close       +2        YES: use most      ---       Institutional reference.
  cluster present            recent item\'s               
                             decay                        

  Current period   +3        No (it\'s current  ---       Active magnet.
  open (D/W/M) in            by definition)               
  zone                                                    

  Session open in  +2        No (expires at     ---       Short-lived intraday
  zone                       session close)               reference.
  (Forex/Crypto)                                          

  Zone acted as    +3        No                 ---       SR flip = strongest levels.
  BOTH Support AND                                        
  Resistance                                              

  Round number     +2        No                 ---       Psychological/algorithmic
  bonus (see B3.6)                                        clustering.

  Untested         +2        YES: decays over   ---       Powerful unvalidated zones.
  breakout origin            time until first             
  (see B3.7)                 test                         
  --------------------------------------------------------------------------------------

> STRENGTH TIERS:
>
> Score \>= 15 → S-TIER (Institutional. Highest conviction.)
>
> Score 10-14 → A-TIER (Strong. Full-size trades.)
>
> Score 5-9 → B-TIER (Moderate. Reduced size or extra confirmation
> needed.)
>
> Score \< 5 → C-TIER (Weak. Skip unless no better levels.)

**B3.5 Zone Width: Volatility-Clamped Calculation**

Zone width must scale with volatility but be bounded. During squeezes,
ATR is compressed and zones become too narrow, causing price to
\"break\" zones that are actually holding. During expansion, zones
become too wide. The solution is to clamp the width within
instrument-specific bounds.

> ZONE WIDTH FORMULA:
>
> raw_width = 0.25 x ATR(timeframe, 14)
>
> zone_half_width = max(min_width, min(raw_width, max_width))
>
> zone_upper = zone_center + zone_half_width
>
> zone_lower = zone_center - zone_half_width
>
> ZONE BUFFER (padding beyond the cluster extremes):
>
> raw_buffer = 0.10 x ATR(timeframe, 14)
>
> zone_buffer = max(min_buffer, min(raw_buffer, max_buffer))

  -------------------------------------------------------------------------------------------
  **Instrument   **Min Width       **Max Width       **Min      **Max      **Example
  Type**         (points/pips)**   (points/pips)**   Buffer**   Buffer**   Instruments**
  -------------- ----------------- ----------------- ---------- ---------- ------------------
  Major Forex    5 pips            30 pips           2 pips     10 pips    EUR/USD, GBP/USD,
  Pairs                                                                    USD/JPY

  Minor/Exotic   8 pips            50 pips           3 pips     15 pips    EUR/GBP, USD/ZAR,
  Forex                                                                    GBP/JPY

  BTC/USDT       \$50              \$500             \$20       \$150      Bitcoin spot/perp

  ETH/USDT       \$5               \$50              \$2        \$15       Ethereum spot/perp

  Gold (XAU/USD) \$3               \$25              \$1        \$8        Gold spot

  S&P 500 / ES   5 pts             30 pts            2 pts      10 pts     Index futures
  -------------------------------------------------------------------------------------------

**Squeeze Adjustment:** When Entry TF ZLBB bandwidth_percentile \<= 20
(squeeze state from Addendum A), multiply min_width by 1.5. This
prevents zones from becoming unrealistically tight during low-volatility
periods.

> if squeeze_active:
>
> effective_min_width = min_width x 1.5
>
> else:
>
> effective_min_width = min_width

**B3.6 Round Number / Psychological Level Bonus**

Round numbers are inherently significant because human traders and
algorithms cluster orders at these prices. If a detected zone\'s center
falls near a psychologically significant round number, it receives a +2
strength bonus.

> ROUND NUMBER DETECTION:
>
> For a zone with zone_center = P:
>
> Check if P is within 0.10 x ATR(zone\'s native TF, 14) of a round
> number.
>
> If yes: round_number_bonus = +2
>
> If no: round_number_bonus = 0

  -------------------------------------------------------------------------------
  **Instrument**   **Major Round    **Minor Round**  **Detection Formula**
                   (strongest)**                     
  ---------------- ---------------- ---------------- ----------------------------
  EUR/USD          X.X000 (e.g.,    X.XX00 (e.g.,    round_price = round(P /
                   1.1000)          1.1100) and      0.0100) \* 0.0100; distance
                                    X.XX50 (e.g.,    = abs(P - round_price);
                                    1.1050)          is_round = distance \<= 0.10
                                                     \* ATR

  GBP/USD          X.X000           X.XX00, X.XX50   Same formula as EUR/USD

  USD/JPY          XXX.00 (e.g.,    XXX.50 (e.g.,    round_price = round(P /
                   150.00)          150.50)          0.50) \* 0.50; distance =
                                                     abs(P - round_price)

  BTC/USDT         X0000 (e.g.,     X5000 (e.g.,     round_price = round(P /
                   70000)           65000) and X000  1000) \* 1000; distance =
                                    (e.g., 71000)    abs(P - round_price)

  ETH/USDT         X000 (e.g.,      X500 (e.g.,      round_price = round(P / 100)
                   3000)            2500) and X00    \* 100; distance = abs(P -
                                    (e.g., 2700)     round_price)

  Gold (XAU/USD)   XX00 (e.g.,      XX50 (e.g.,      round_price = round(P / 50)
                   2800)            2850)            \* 50; distance = abs(P -
                                                     round_price)

  S&P 500          X000 (e.g.,      X00 (e.g.,       round_price = round(P / 50)
                   6000)            6100), X50       \* 50; distance = abs(P -
                                    (e.g., 6050)     round_price)
  -------------------------------------------------------------------------------

**Major vs Minor:** Both get +2. The distinction is for human reference.
Algorithmically, any round number within the ATR threshold qualifies. In
practice, major rounds tend to cluster more signal types (rejections +
open/closes) and naturally score higher through other factors.

**B3.7 Untested Breakout Origin Detection**

When price breaks away from a consolidation with a strong displacement
and never retests the origin, that origin is a powerful SR zone. It
represents the last price where the market agreed on value before an
imbalance took over. When price eventually returns, it tends to react
strongly.

> DETECTION ALGORITHM:
>
> 1\. Identify breakout legs:
>
> A completed ZLBB leg where:
>
> leg_displacement \> 2.0 x ATR(trigger_tf, 14)
>
> AND leg_efficiency \> 0.60
>
> AND leg_quality \>= B-GRADE (from Addendum A scoring)
>
> 2\. Define the origin zone:
>
> For a BULL breakout leg:
>
> origin_center = leg_start_price (the low where the leg began)
>
> For a BEAR breakout leg:
>
> origin_center = leg_start_price (the high where the leg began)
>
> zone_width = use standard clamped width (B3.5)
>
> 3\. Check if untested:
>
> Scan all bars AFTER the breakout leg ended:
>
> untested = True
>
> FOR each subsequent bar:
>
> if bar\'s wick entered the origin zone:
>
> untested = False
>
> break
>
> 4\. If untested = True:
>
> Create SR zone:
>
> signal_type = UNTESTED_ORIGIN
>
> base_strength = +2
>
> Apply time-decay: strength decays at the trigger TF rate
>
> sr_type = SUPPORT if bull breakout origin, RESISTANCE if bear
>
> 5\. When price first returns to the zone:
>
> → Zone state transitions from FRESH to TESTED
>
> → This is a high-priority RECLAIM OF ZONE setup (Section B6)
>
> → Strength bonus: +1 for first test of untested origin

**Expiration:** Untested origin zones that remain untested for \> 500
bars on their native TF are expired. The further price moves from the
origin without testing, the less likely it is to return. The time-decay
naturally reduces their score over time.

**B3.8 SR Density as Directional Bias Signal**

The density of SR zones above and below current price reveals the path
of least resistance. Dense SR = choppy movement, price will struggle.
Sparse SR = clean space, price can move freely. The algorithm computes
this and feeds it to the phase classifier as a directional bias input.

> COMPUTATION (on each new 4H bar):
>
> 1\. Count zones above current price within scan_range:
>
> scan_range = 3.0 x ATR(Daily, 14)
>
> zones_above = count of B-TIER+ zones where zone_lower \> current_price
>
> AND zone_lower \<= current_price + scan_range
>
> weighted_density_above = sum(zone.strength_score) for all zones_above
>
> 2\. Count zones below current price within scan_range:
>
> zones_below = count of B-TIER+ zones where zone_upper \< current_price
>
> AND zone_upper \>= current_price - scan_range
>
> weighted_density_below = sum(zone.strength_score) for all zones_below
>
> 3\. Compute density ratio:
>
> if weighted_density_above == 0 AND weighted_density_below == 0:
>
> density_bias = 0 (neutral)
>
> else:
>
> density_ratio = weighted_density_above / max(weighted_density_below,
> 1)
>
> 4\. Interpret:
>
> density_ratio \> 2.0 → HEAVY_ABOVE (path of least resistance is DOWN)
>
> density_ratio \> 1.5 → MODERATE_ABOVE (slight downward bias)
>
> 0.67 \< ratio \< 1.5 → NEUTRAL (balanced)
>
> density_ratio \< 0.67 → MODERATE_BELOW (slight upward bias)
>
> density_ratio \< 0.5 → HEAVY_BELOW (path of least resistance is UP)
>
> 5\. Distance to next SR as target quality:
>
> dist_to_next_above = nearest_sr_above.zone_lower - current_price
>
> dist_to_next_below = current_price - nearest_sr_below.zone_upper
>
> if dist_to_next_above \> 1.5 x ATR(trigger_tf, 14):
>
> upside_clean = True (clean space to target, high-quality long setup)
>
> else:
>
> upside_clean = False (next SR is close, target may not be reached)
>
> (same logic for downside)

**Integration:** density_bias feeds into the main framework phase
classifier as an additional input. upside_clean and downside_clean feed
into the setup scanner to filter out trades where the target SR is too
close to justify the stop distance (R:R check). A trade where the target
SR is \< 1.5 ATR away but the stop is 1 ATR is a sub-1.5:1 R:R and
should be skipped.

**B4. SR Zone Lifecycle State Machine**

  --------------------------------------------------------------------------
  **State**   **Definition**    **Transitions**       **Scoring Impact**
  ----------- ----------------- --------------------- ----------------------
  FRESH       Newly detected    If price enters zone  Base score only. No
              zone. Price has   and reverses: →       test bonus.
              not returned to   TESTED. If price      
              test it after     closes beyond zone    
              detection.        for threshold bars: → 
                                BROKEN.               

  TESTED      Price has         Each successful test: +1 per successful test
              approached and    touch_count++,        (decayed). Each test
              reacted (reversed recalculate score. If validates the zone.
              or paused) at     broken: → BROKEN.     
              least once.                             

  BROKEN      Price closed      If price returns and  Strength reduced by
              beyond zone for   zone acts as opposite 50%. Zone is weakened
              consecutive bar   SR: → FLIPPED. If no  but tracked for flip
              threshold on      return: → EXPIRED.    potential.
              zone\'s native                          
              TF.                                     

  FLIPPED     Zone changed      Track as TESTED with  +3 bonus. Flipped
              polarity. Former  new polarity. If      zones are the
              resistance now    broken again: →       strongest levels.
              support or vice   BROKEN.               
              versa.                                  

  EXPIRED     No price          Remove from active    Removed from scoring.
              interaction for   list. Can be          
              extended period.  re-detected if price  
                                returns to area.      
  --------------------------------------------------------------------------

**B4.1 Quantified Transition Rules**

> BROKEN THRESHOLD (consecutive bars closing beyond zone on native TF):
>
> 1H zones: 4 consecutive bars (4 hours)
>
> 4H zones: 3 consecutive bars (12 hours)
>
> Daily zones: 3 consecutive bars (3 days)
>
> Weekly zones: 2 consecutive bars (2 weeks)
>
> Monthly zones: 2 consecutive bars (2 months)
>
> Quarterly: 2 consecutive bars (6 months)
>
> EXPIRATION (bars without any interaction on native TF):
>
> 1H zones: 400 bars (approx 17 trading days)
>
> 4H zones: 200 bars (approx 33 days)
>
> Daily zones: 120 bars (approx 6 months)
>
> Weekly zones: 104 bars (approx 2 years)
>
> Monthly zones: 60 bars (approx 5 years)
>
> Quarterly: Never expire.
>
> \"INTERACTION\" DEFINED:
>
> A bar interacts with a zone if:
>
> bar.Low \<= zone.zone_upper AND bar.High \>= zone.zone_lower
>
> i.e., the bar\'s range overlaps with the zone.
>
> \"SUCCESSFUL TEST\" DEFINED:
>
> A bar enters the zone (interaction = true)
>
> AND the NEXT bar reverses direction (closes outside zone on the side
>
> price entered from, or shows a rejection bar within the zone).
>
> If price enters the zone and continues through → not a test, potential
> break.

**B5. The 50% Midpoint SR Discovery**

> **DISCOVERY: Between any two consecutive SR levels on a given
> timeframe, an SR level exists at the 50% midpoint, visible on the next
> lower timeframe. This is a fractal property of market structure.**

**B5.1 Why It Works**

Between two institutional SR levels, price negotiates fair value. The
50% midpoint represents the equilibrium of that range. Smaller timeframe
participants reference this as a decision point: longs take partial
profits, shorts initiate positions, and vice versa. This creates
measurable reactions on the LTF. The midpoint is also where Fibonacci
50% retracement sits, a level widely watched by traders.

**B5.2 Qualification Criteria (Tightened)**

Not every midpoint is valid. The following criteria must ALL be met
before creating a midpoint SR zone:

> QUALIFICATION RULES:
>
> 1\. PARENT ZONE TIER:
>
> Both parent zones must be B-TIER or higher (score \>= 5).
>
> Midpoints between C-TIER zones are unreliable.
>
> Both parent zones must be the SAME tier or adjacent tiers.
>
> (S+A ok, A+B ok, S+C NOT ok)
>
> 2\. RANGE TRAVERSAL:
>
> The range between the two parent zones must have been
>
> traversed at least 2 times (price moved from one parent SR
>
> to the other at least 2 complete times).
>
> Detection: count completed ZLBB legs that span \> 60% of the range.
>
> 3\. MINIMUM RANGE SIZE:
>
> Distance between parent zone centers \> 2.0 x ATR(parent TF, 14).
>
> If the range is too small, the midpoint is too close to both
>
> parents and adds noise rather than signal.
>
> 4\. NO EXISTING SR AT MIDPOINT:
>
> If a zone already exists within 0.25 x ATR of the midpoint
>
> (detected by Methods 1-3), do NOT create a midpoint zone.
>
> The existing zone is already capturing this level.

**B5.3 Midpoint Zone Creation**

> IF all qualification rules pass:
>
> mid_price = (parent_upper.zone_center + parent_lower.zone_center) / 2
>
> zone_center = mid_price
>
> zone_width = 0.15 x ATR(parent_timeframe, 14) (tighter than standard)
>
> zone_upper = mid_price + zone_width
>
> zone_lower = mid_price - zone_width
>
> source_tf = one timeframe BELOW the parent
>
> signal_type = MIDPOINT
>
> sr_type = BOTH (midpoints act as both S and R)
>
> STRENGTH INHERITANCE:
>
> base_strength = average(parent_upper.score, parent_lower.score) x 0.60
>
> (inherits 60% of parent average strength)
>
> OPTIONAL VALIDATION (recommended for live trading):
>
> Check LTF data: count bars that show reaction within midpoint zone
>
> in last 50 bars on the midpoint\'s source_tf.
>
> if reactions \>= 2:
>
> midpoint_validated = True (keep full inherited strength)
>
> elif reactions == 1:
>
> midpoint_validated = Partial (reduce strength by 25%)
>
> else:
>
> midpoint_validated = False (reduce strength by 50%)

**B5.4 Timeframe Mapping**

  ------------------------------------------------------------------------
  **Parent SR       **Midpoint        **Zone Width**    **Lookback for
  Timeframe**       Visible On**                        Validation**
  ----------------- ----------------- ----------------- ------------------
  Monthly SR pair   Weekly / Daily    0.15 x            Last 12 weekly
                                      ATR(Monthly)      bars or 60 daily
                                                        bars

  Weekly SR pair    Daily / 4H        0.15 x            Last 30 daily bars
                                      ATR(Weekly)       or 120 4H bars

  Daily SR pair     4H / 1H           0.15 x ATR(Daily) Last 50 4H bars

  4H SR pair        1H                0.15 x ATR(4H)    Last 100 1H bars

  1H SR pair        15min (visible on 0.15 x ATR(1H)    Last 100 15min
                    entry TF)                           bars
  ------------------------------------------------------------------------

**B5.5 Recursion Limits**

> RECURSIVE MIDPOINTS:
>
> Level 0: Original HTF SR zones (full strength)
>
> Level 1: Midpoint between Level 0 (60% of parent avg)
>
> Level 2: Midpoint between L0 and L1 (60% of Level 1 = 36% original)
>
> Level 3+: NOT GENERATED. Noise \> signal.
>
> Default: max_recursion_depth = 1 (only Level 1 midpoints)
>
> Optional: max_recursion_depth = 2 (for very liquid instruments)
>
> Never exceed 2.

**B6. Fakeout Entry Setups (Algorithmic Definitions)**

All setups from the Prasad book share a common theme: price approaches
an SR zone, creates a trap (fakeout/liquidity grab), and reverses. Each
setup below includes precise OHLCV conditions for algorithmic detection.

**Setup 1: Pre-Market Fakeout**

  -------------------------------------------------------------------------
  **Component**   **Algorithmic Definition**
  --------------- ---------------------------------------------------------
  Precondition    current_time is within pre-market window. Forex:
                  6:00-8:00 AM EST. Crypto: 30 min before CME open. Price
                  is within 1.0 x ATR(1H, 14) of any B-TIER+ SR zone. HTF
                  phase_bias is established (weekly/daily classification).

  Trigger         A bar penetrates the SR zone boundary: bar.High \>
                  zone.zone_upper (resistance break) OR bar.Low \<
                  zone.zone_lower (support break). Penetration can be wick
                  only.

  Confirmation    Within the next 3 bars: a bar closes BACK inside the
                  range. For bearish fakeout: bar.Close \< zone.zone_upper.
                  For bullish fakeout: bar.Close \> zone.zone_lower. The
                  close must be decisive: close_position (bar
                  classification from main framework) must be in the bottom
                  40% (bearish) or top 40% (bullish) of the bar.

  Entry           Enter on close of confirmation bar. Direction = opposite
                  of breakout.

  Stop Loss       fakeout_extreme + (0.10 x ATR(entry_tf, 14)). For sells:
                  above fakeout high. For buys: below fakeout low.

  Target          Next SR zone (nearest_sr_below for sells,
                  nearest_sr_above for buys). Minimum R:R check:
                  target_distance / stop_distance \>= 2.0. If R:R \< 2.0,
                  skip.

  ZLBB Confluence If fakeout occurs at Trigger TF ZLBB outer band
                  (exhaustion zone): strength +2. If Double Band
                  Exhaustion: strength +4 (highest conviction).
  -------------------------------------------------------------------------

**Setup 2: Candle Closing Back in Range**

  -------------------------------------------------------------------------
  **Component**   **Algorithmic Definition**
  --------------- ---------------------------------------------------------
  Precondition    Price is at a B-TIER+ SR zone. Not time-restricted.

  Trigger         On LTF (15min): a bar breaks beyond SR zone boundary.

  Confirmation    On HTF (1H or 4H): the SAME candle or the NEXT candle
                  closes back inside the range. Quantified: within 1-2 HTF
                  bars of the 15min breakout, the HTF bar.Close is back
                  inside the zone. HTF close \> LTF breakout = breakout is
                  noise.

  Entry           Enter after HTF candle closes back inside range.
                  Direction = opposite of breakout.

  Stop Loss       breakout_extreme + (0.10 x ATR(entry_tf, 14)).

  Target          Next SR zone. R:R \>= 2.0.

  Key Metric      Speed of rejection: if HTF candle that closes back in
                  range has body_ratio \> 0.50 and closes in the
                  trend-direction 40% of the bar, conviction is high.
  -------------------------------------------------------------------------

**Setup 3: Reclaim of the Zone**

  -------------------------------------------------------------------------
  **Component**   **Algorithmic Definition**
  --------------- ---------------------------------------------------------
  Precondition    An SR zone has state = BROKEN (price closed beyond it for
                  threshold bars). Price has moved away: distance from zone
                  \> 0.5 x ATR(zone native TF, 14).

  Trigger         Price returns to the broken zone. A bar enters the zone
                  (bar.Low \<= zone.zone_upper AND bar.High \>=
                  zone.zone_lower).

  Confirmation    Within 3 bars of re-entry: a bar closes showing zone
                  acceptance. For prior resistance now support: bar.Close
                  \> zone.zone_center. For prior support now resistance:
                  bar.Close \< zone.zone_center. The bar should be a
                  trend-direction bar (body_ratio \> 0.40, close in
                  direction of reclaim).

  Entry           Enter on close of confirmation bar. Direction = same as
                  original breakout (continuation).

  Stop Loss       Beyond the zone boundary (below zone for long, above for
                  sell) + (0.10 x ATR). If the zone is thick, stop at
                  zone_lower - buffer for longs.

  Target          Next SR zone in trend direction. R:R \>= 1.5 (lower
                  threshold because this is a continuation with higher win
                  rate).

  Zone State      On confirmation: zone state → FLIPPED. strength += 3.
  Update          

  Untested Origin If the zone was flagged as UNTESTED_ORIGIN (B3.7), this
  Bonus           first reclaim test is particularly strong. strength += 1
                  additional.
  -------------------------------------------------------------------------

**Setup 4: Bull/Bear Engulfing at SR**

  -------------------------------------------------------------------------
  **Component**   **Algorithmic Definition**
  --------------- ---------------------------------------------------------
  Precondition    Price is at B-TIER+ SR zone.

  Bear-Bull       bar\[N\] is a bear bar (Close \< Open). bar\[N+1\] is a
  Trigger (at     bull bar where: bar\[N+1\].Open \<= bar\[N\].Close AND
  Resistance)     bar\[N+1\].Close \>= bar\[N\].Open (full engulfing) AND
                  bar\[N+1\].Close \> zone.zone_upper (closes above
                  resistance) AND bar\[N+1\].body \> bar\[N\].body
                  (engulfing bar is larger).

  Bull-Bear       bar\[N\] is a bull bar. bar\[N+1\] is a bear bar with:
  Trigger (at     bar\[N+1\].Open \>= bar\[N\].Close AND bar\[N+1\].Close
  Support)        \<= bar\[N\].Open AND bar\[N+1\].Close \< zone.zone_lower
                  AND bar\[N+1\].body \> bar\[N\].body.

  Entry           On break of engulfing bar\'s high (Bear-Bull buy) or low
                  (Bull-Bear sell). Entry_price = bar\[N+1\].High + 0.01
                  ATR (for buy) or bar\[N+1\].Low - 0.01 ATR (for sell).

  Stop Loss       Below engulfing candle low (buy) or above its high
                  (sell). Distance = bar\[N+1\] range + 0.10 ATR buffer.

  Target          Next SR zone. R:R \>= 2.0.
  -------------------------------------------------------------------------

**Setup 5: Weak Candle at SR (Continuation)**

  --------------------------------------------------------------------------
  **Component**    **Algorithmic Definition**
  ---------------- ---------------------------------------------------------
  Precondition     Price is at SR zone. HTF phase = TREND (bull or bear).
                   This is WITH-TREND only.

  Trigger          A bar forms at the SR zone with: body_ratio \< 0.40
                   (small body) OR bar_range \< 0.50 x ATR(entry_tf, 14)
                   (small overall). Candle color is IRRELEVANT.

  Interpretation   Counter-trend side attempted to hold SR but produced no
                   strong rejection. Weakness = continuation. Liquidity
                   grabbed.

  Entry            Enter on break of weak candle\'s high (bull trend) or low
                   (bear trend). In trend direction.

  Stop Loss        Below the PREVIOUS candle\'s low (buy) or above its high
                   (sell). Trail to below breakout candle once it closes.
                   Initial stop = prior_bar.Low - 0.10 ATR (buy).

  Target           Next SR zone in trend direction.

  Best Context     Channel Trend or early Band Walk phase (Addendum A).
                   Strong trend momentum makes continuation highly probable.
  --------------------------------------------------------------------------

**Setup 6: SP Candle (Liquidity Grab Reversal)**

  -------------------------------------------------------------------------
  **Component**   **Algorithmic Definition**
  --------------- ---------------------------------------------------------
  Precondition    Price is near B-TIER+ SR zone. Best on 1H chart (trade
                  TF). 15min used for entry precision.

  Trigger (Buy)   bar\[N+1\].Low \< bar\[N\].Low (breaks prior low,
                  grabbing liquidity) AND bar\[N+1\].Close \>
                  bar\[N\].Close (closes above prior close) AND
                  bar\[N+1\].body_ratio \> 0.50 (strong momentum close, not
                  a doji) AND bar\[N+1\].close_position \> 0.60 (closed in
                  upper portion of its range).

  Trigger (Sell)  bar\[N+1\].High \> bar\[N\].High AND bar\[N+1\].Close \<
                  bar\[N\].Close AND bar\[N+1\].body_ratio \> 0.50 AND
                  bar\[N+1\].close_position \< 0.40.

  Entry           On break of SP candle\'s high (buy) or low (sell).
                  Entry_price = SP.High + 0.01 ATR or SP.Low - 0.01 ATR.

  Stop Loss       Below SP candle low (buy) or above its high (sell). stop
                  = SP.Low - 0.10 ATR (buy).

  Target          Next SR zone. R:R \>= 2.0. Strong momentum expected.
  -------------------------------------------------------------------------

**Setup 7: 0.70-0.786 Fibonacci Retracement**

  -------------------------------------------------------------------------
  **Component**   **Algorithmic Definition**
  --------------- ---------------------------------------------------------
  Precondition    HTF phase_bias is clear. A completed ZLBB leg exists.

  Fib Zone        For BULL setup (buying the pullback): fib_70 = leg_high -
  Calculation     (0.70 x leg_displacement), fib_786 = leg_high - (0.786 x
                  leg_displacement). The zone is \[fib_786, fib_70\]. For
                  BEAR setup: fib_70 = leg_low + (0.70 x leg_displacement),
                  fib_786 = leg_low + (0.786 x leg_displacement). Zone =
                  \[fib_70, fib_786\].

  Trigger         Price enters the 0.70-0.786 zone from the retracement
                  side. For bull: price drops into the zone from above.

  Confirmation    A bar closes inside the 0.70-0.786 zone showing
                  acceptance: bar.Close is within the zone AND bar shows no
                  strong continuation wick beyond the zone (wick beyond
                  0.786 is \< 0.25 x bar_range).

  Entry           Enter after confirmation candle. LTF precision entry
                  recommended: look for ZLBB band touch, SR reaction, or
                  rejection bar within the zone.

  Stop Loss       Below the swing low (buy) or above swing high (sell).
                  stop = leg_low - 0.10 ATR (buy setup).

  Target          Prior swing high/low (the leg extreme), or next SR zone.
                  R:R \>= 2.0.

  ZLBB Confluence The 0.70-0.786 zone frequently aligns with the Trigger TF
                  ZLEMA. If zone overlaps with ZLEMA ± 0.25σ: confluence =
                  True, strength += 2.
  -------------------------------------------------------------------------

**Setup 8: Second 15-Min Candle of the 1-Hour Candle**

  -------------------------------------------------------------------------
  **Component**   **Algorithmic Definition**
  --------------- ---------------------------------------------------------
  Precondition    HTF phase_bias is clear. Price is at or near a B-TIER+ SR
                  zone. Current time is within a high-volume session
                  (London or NY).

  Trigger (Buy)   Examine the four 15-min candles composing the current 1H
                  bar. The first 2-3 candles are weak: average body_ratio
                  \< 0.40 OR combined range \< 0.50 x ATR(15min, 14) OR net
                  direction is slightly bearish despite HTF bullish bias.
                  This indicates accumulation/hesitation during the first
                  30-45 minutes.

  Confirmation    The 3rd or 4th 15-min candle breaks the high of the first
                  candle: bar\[3or4\].High \> bar\[1\].High. AND it is
                  bullish with body_ratio \> 0.40 and close_position \>
                  0.60.

  Entry           Enter on break of first 15-min candle\'s high.
                  Entry_price = bar\[1\].High + 0.01 ATR.

  Stop Loss       Below the low of the 1H candle being formed. stop =
                  min(all 15min lows in this 1H bar) - 0.10 ATR.

  Target          Next SR zone. This is an early entry before the 1H candle
                  completes, giving better R:R.

  Invert for Sell First 2-3 15-min candles are weak bullish. Final 15-min
                  candle breaks first 15-min low with bearish momentum.
  -------------------------------------------------------------------------

**Setup 9: Retracement After Confirmation Candle**

  -------------------------------------------------------------------------
  **Component**   **Algorithmic Definition**
  --------------- ---------------------------------------------------------
  Precondition    A confirmation candle has already formed at an SR zone
                  (any of Setups 1-8 triggered and confirmed). Position
                  building scenario.

  Trigger         After the confirmation candle closes, the NEXT candle
                  retraces. For a bull confirmation: next bar pulls back
                  (bar.Low \< confirmation.Close). The retracement creates
                  a wick in the counter direction.

  Entry           Drop to LTF (15min). Look for a setup-within-setup: SR
                  reaction at the retracement level, ZLBB band touch,
                  Fibonacci 0.50-0.70 of the confirmation bar range, or a
                  rejection bar.

  Position        This is an ADD to an existing position, not a fresh
  Building        entry. The initial entry was on the confirmation candle.
                  This retracement entry improves average price.

  Stop Loss       Below the confirmation candle\'s low (buy) or above its
                  high (sell). Same as the original trade.

  Target          Same as the original trade target.

  Quantified      Valid retracement: pulls back 30-70% of the confirmation
  Retracement     candle\'s range. Less than 30% = not enough pullback for
                  meaningful entry improvement. More than 70% = the
                  confirmation may be failing.
  -------------------------------------------------------------------------

**B7. Integration with Main Framework & Addendum A**

  -----------------------------------------------------------------------
  **System Component** **SR System Enhancement**
  -------------------- --------------------------------------------------
  Main FW: Decision    REPLACED by B3 three-method detection + merging +
  Tree Step 1          time-decay scoring.

  Main FW: Decision    Uses scored zone list. Proximity check on 1H bars:
  Tree Step 4          within 1.0 ATR(1H, 14) of A/S-TIER, or within 0.5
                       ATR of B-TIER. 15min bars only check proximity,
                       never generate SR.

  Main FW: Decision    9 Prasad setups (B6) scanned on 1H bars at SR
  Tree Step 5          zones. 15min used for entry precision within
                       setups.

  Main FW: Risk        SR density (B3.8) validates R:R. If upside_clean =
  Framework            False, longs are skipped or reduced size.

  Addendum A: Leg      Leg extremes feed Method 1 (B3.1). Each completed
  Detection            leg generates SR data.

  Addendum A:          Double Band Exhaustion at S-TIER zone = highest
  Exhaustion Zones     conviction in entire system.

  Addendum A: Squeeze  Squeeze breakouts originating at SR zones are
  Breakout             highest-quality entries.

  Addendum A: Band     Band walk termination near an SR zone = reclaim
  Walk / Channel Trend setup trigger.

  50% Midpoints (B5)   Feed into main SR list with inherited strength.
                       Provide intermediate targets.

  SR Density (B3.8)    Feeds directional bias to phase classifier. Feeds
                       target quality to R:R checker.
  -----------------------------------------------------------------------

**B8. Implementation Requirements**

**B8.1 Data Requirements**

  ------------------------------------------------------------------------
  **Timeframe**     **Lookback**         **Purpose**
  ----------------- -------------------- ---------------------------------
  3-Month           8 bars (6 years)     Macro SR. Longest institutional
                                         memory.

  Monthly           24 bars (2 years)    Major SR refinement.

  Weekly            52 bars (1 year)     Directional bias + SR.

  Daily             120 bars (6 months)  Structure + daily opens.

  4H                200 bars (33 days)   Execution zone SR.

  1H                200 bars (8 trading  Intraday SR for trade management.
                    days)                Lowest SR-generating TF.

  15min             Per Addendum A       Entry timing ONLY. No SR
                                         generation.
  ------------------------------------------------------------------------

**B8.2 Computation Schedule**

> ON NEW 3-MONTH BAR:
>
> Full SR rebuild from quarterly data (Methods 1-3).
>
> ON NEW MONTHLY BAR:
>
> Rebuild monthly SR. Merge with quarterly.
>
> ON NEW WEEKLY BAR:
>
> Rebuild weekly SR. Merge with M/Q.
>
> ON NEW DAILY BAR:
>
> Rebuild daily SR. Merge with W/M/Q.
>
> Compute midpoints for all consecutive zone pairs.
>
> Update zone lifecycle states (test/break/expire).
>
> Compute SR density and directional bias.
>
> Update session opens.
>
> ON NEW 4H BAR:
>
> Rebuild 4H SR. Merge with D/W/M/Q.
>
> Compute 4H midpoints.
>
> Apply time-decay to all touches/rejections.
>
> Score all zones. Assign tiers.
>
> Detect untested breakout origins.
>
> Compute round number bonuses.
>
> Compute SR density and directional bias.
>
> Feed final scored zone list to main framework.
>
> ON NEW 1H BAR:
>
> Rebuild 1H SR (Method 1: leg extremes, Method 3: rejections).
>
> Merge 1H zones with 4H/D/W/M/Q zones.
>
> Compute 1H midpoints between consecutive 1H zone pairs.
>
> Update 1H zone lifecycle states (test/break/expire).
>
> Refresh nearest_sr_above and nearest_sr_below.
>
> Run setup scanner against all active zones.
>
> ON NEW 15MIN BAR:
>
> No SR rebuild. Check proximity + run entry trigger detection.
>
> Execute ZLBB leg detection (Addendum A) for entry timing.

**B8.3 State Variables**

  -----------------------------------------------------------------------------------
  **Variable**           **Type**            **Description**
  ---------------------- ------------------- ----------------------------------------
  sr_zones\[\]           List\<SRZone\>      All active zones: center, upper, lower,
                                             strength, tier, state, source_tfs\[\],
                                             touch_count, signal_types\[\], sr_type,
                                             is_midpoint, is_session_open,
                                             is_current_period_open,
                                             round_number_bonus, lifecycle_state.

  nearest_sr_above       SRZone \| null      Closest zone above price (by
                                             zone_lower).

  nearest_sr_below       SRZone \| null      Closest zone below price (by
                                             zone_upper).

  price_in_zone          Boolean             True if price overlaps any zone.

  active_zone            SRZone \| null      Zone price is currently inside.

  density_bias           Enum{HEAVY_ABOVE,   SR density directional signal.
                         MODERATE_ABOVE,     
                         NEUTRAL,            
                         MODERATE_BELOW,     
                         HEAVY_BELOW}        

  upside_clean           Boolean             True if \> 1.5 ATR to next SR above.

  downside_clean         Boolean             True if \> 1.5 ATR to next SR below.

  untested_origins\[\]   List\<SRZone\>      Breakout origin zones not yet retested.

  session_opens\[\]      List\<SRZone\>      Current session open prices
                                             (auto-expire).
  -----------------------------------------------------------------------------------

**B8.4 Tunable Parameters**

  ----------------------------------------------------------------------------------
  **Parameter**      **Default**   **Range**      **Affects**
  ------------------ ------------- -------------- ----------------------------------
  Cluster Width (ATR 0.25          0.15-0.40      How close extremes cluster into
  mult)                                           one zone

  Zone Buffer (ATR   0.10          0.05-0.20      Padding around zone boundaries
  mult)                                           

  Min Leg Extreme    2             2-4            Method 1 cluster minimum
  Touches                                         

  Min Rejections     3             2-5            Method 3 cluster minimum

  Rejection Wick     0.50 x range  0.40-0.60      What qualifies as a rejection bar
  Threshold                                       

  Rejection Body Cap 0.40          0.30-0.50      Filter out trend bars from
                     body_ratio                   rejections

  Decay Rate (3M)    0.97          0.95-0.99      Speed of quarterly touch decay

  Decay Rate         0.98          0.96-0.99      Monthly touch decay
  (Monthly)                                       

  Decay Rate         0.99          0.98-0.995     Weekly touch decay
  (Weekly)                                        

  Decay Rate (Daily) 0.995         0.99-0.998     Daily touch decay

  Decay Rate (4H)    0.998         0.995-0.999    4H touch decay

  Decay Rate (1H)    0.999         0.998-0.9995   1H touch decay

  Midpoint Strength  60%           40%-80%        How much strength midpoints get
  Inherit                                         from parents

  Midpoint Min Range 2.0 x ATR     1.5-3.0        Min distance between parents for
                                                  midpoint

  Midpoint Max       1             1-2            Recursion depth for midpoints
  Recursion                                       

  Broken Bar         3             2-5            Bars to declare 4H zone BROKEN
  Threshold (4H)                                  

  Broken Bar         4             3-6            Bars to declare 1H zone BROKEN
  Threshold (1H)                                  

  Proximity (ATR     1.0           0.5-1.5        Distance to activate setup scanner
  mult)                                           

  SR Density Scan    3.0 x         2.0-5.0        How far to count zones for density
  Range              ATR(Daily)                   

  Untested Origin    2.0 x ATR     1.5-3.0        Min leg size for breakout origin
  Displacement                                    

  Untested Origin    500 bars      300-700        Bars before untested origin
  Expiry                                          expires

  Squeeze Width      1.5           1.25-2.0       How much to widen min_width during
  Multiplier                                      squeeze
  ----------------------------------------------------------------------------------
