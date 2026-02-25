**ALGORITHMIC TRADING FRAMEWORK**

Price Action & Market Structure System

Based on OHLCV Data \| Wyckoff + Auction Market Theory + Price Action

Foundation Document for Algorithm Development

Version 1.0 \| February 2026

**1. CORE MARKET AXIOMS**

These are the foundational beliefs of the system. Every rule, entry, and
exit derives from these axioms. If any axiom is violated by market data,
the system pauses and re-evaluates.

**Axiom 1: Markets Move Level to Level**

Price travels from one Support/Resistance (SR) level to the next. SR
levels are areas where price has previously reversed or consolidated,
representing zones of historical supply/demand imbalance. Between
levels, price tends to move with momentum; at levels, price tends to
pause, reverse, or consolidate.

**Algorithmic Definition:** An SR level is a price zone (not a single
price) where at least 2 prior swing highs/lows or consolidation clusters
exist within a defined lookback window. The zone width is ATR-relative
(e.g., 0.25x ATR of the analysis timeframe).

**Axiom 2: Higher Timeframe Dominance with Lower Timeframe
Confirmation**

Higher timeframe (HTF) structure defines the zones where price is likely
to react. Lower timeframe (LTF) structure confirms whether that reaction
is actually happening. Neither is sufficient alone.

**HTF Role:** Sets the battlefield. Defines key SR zones, current trend
direction, and the active market phase.

**LTF Role:** Provides the trigger. Shows acceptance (price settling
into/through a level) or rejection (price reversing sharply from a
level) at HTF zones.

**Feedback Loop:** HTF identifies WHERE to look → LTF confirms WHETHER
to act → HTF validates the outcome. A trade is only valid when both
timeframes agree.

**Axiom 3: Markets Alternate Between Trend (Imbalance) and Balance
(Sideways)**

This is the fundamental market cycle. Price oscillates between two
states: displacement (one side dominates, creating directional movement)
and equilibrium (neither side dominates, creating a trading range).
Every price bar can be classified as contributing to one of these two
states.

**Axiom 4: Trends Degrade Before They End**

Trends do not end abruptly (barring exogenous shocks). They mature
through measurable degradation: successive legs cover less distance,
pullbacks deepen, and the dominant side loses its pricing power. This
degradation is the early warning system the algorithm monitors.

**Key Principle:** Count matters less than character. A trend may have 2
legs or 5 legs. What matters is whether each successive leg shows
reduced displacement relative to effort compared to the previous leg.

**Axiom 5: Effort vs. Result Is the Primary Signal**

The relationship between effort (number of bars, range of bars, and
optionally volume) and result (net displacement achieved) reveals who is
in control. When minimal effort produces significant displacement, the
dominant side is in full control. When significant effort produces
minimal displacement, control is being contested.

**Without Volume:** Effort is measured by bar count and cumulative bar
range of a leg. Result is the net displacement (close of last bar minus
open of first bar in the leg). This is the primary method.

**With Volume (Optional Confirmation):** If effort (high volume)
produces little result (small displacement), it signals absorption.
Volume divergence from price confirms what the OHLC data already
suggests.

**2. MARKET PHASE CLASSIFICATION**

Before any trade, the algorithm must classify the current market phase.
The phase determines which setups are valid, where entries exist, and
how targets and stops are managed. There are four primary phases and two
transition states.

**2.1 The Four Primary Phases**

  -----------------------------------------------------------------------------
  **Phase**          **Definition**       **Detectable Characteristics
                                          (OHLCV)**
  ------------------ -------------------- -------------------------------------
  TREND_BULL         Sustained upward     Higher highs + higher lows. Majority
                     displacement. Bulls  of bars are bull bars (close \>
                     dominate pricing.    open). Bull bars have larger bodies
                                          than bear bars. Pullbacks are shallow
                                          (\< 50% of prior leg). Price spends
                                          most time above short-term moving
                                          average.

  TREND_BEAR         Sustained downward   Lower highs + lower lows. Majority of
                     displacement. Bears  bars are bear bars (close \< open).
                     dominate pricing.    Bear bars have larger bodies than
                                          bull bars. Pullbacks are shallow.
                                          Price spends most time below
                                          short-term moving average.

  BALANCE            Neither side         Price bounded by identifiable high
  (Sideways/Range)   dominates. Price     and low. Roughly equal bull and bear
                     oscillates within a  bar distribution. Bars tend to be
                     defined range.       smaller. Failed breakouts at range
                                          edges. Price mean-reverts toward
                                          center of range.

  BREAKOUT           Transition from      Strong displacement bar(s) closing
                     balance to trend.    decisively beyond range boundary. Bar
                     One side overwhelms  range significantly above average.
                     the range boundary.  Follow-through bars in the breakout
                                          direction. Prior range held for
                                          extended period (mature range).
  -----------------------------------------------------------------------------

**2.2 Transition States**

These are not phases themselves but transitional conditions between
phases. They represent the highest-uncertainty periods where the
algorithm should reduce position size or stand aside.

  ------------------------------------------------------------------------
  **Transition**   **From → To**        **Detection Criteria**
  ---------------- -------------------- ----------------------------------
  DISTRIBUTION     Trend Bull → Balance Effort vs Result degradation in
                   or Trend Bear        bull legs. Signs of weakness
                                        (defined in Section 3). Price
                                        stalls at or near HTF resistance.
                                        Successive higher highs with
                                        diminishing displacement.

  ACCUMULATION     Trend Bear → Balance Effort vs Result degradation in
                   or Trend Bull        bear legs. Signs of strength
                                        (inverse of weakness). Price
                                        stalls at or near HTF support.
                                        Successive lower lows with
                                        diminishing displacement.
  ------------------------------------------------------------------------

**2.3 Phase Transition Flow**

The canonical market cycle follows this sequence, though phases can
repeat or skip:

**TREND BULL → Distribution → BALANCE → BREAKOUT → TREND BEAR**

↑ ↓

**BREAKOUT ← BALANCE ← Accumulation ← TREND BEAR**

**Important:** Continuation is possible. Distribution does not guarantee
reversal. It can resolve into a continuation of the bull trend
(distribution fails, bulls reassert). The same applies to accumulation.
The algorithm must wait for confirmation before assuming a phase change.

**3. MEASURABLE SIGNALS FROM OHLCV DATA**

Every signal in this system must be computable from OHLCV data. This
section defines the exact metrics the algorithm uses to detect phase
transitions and generate trade signals.

**3.1 Bar Classification**

  -----------------------------------------------------------------------
  **Bar Type**      **Definition**               **Significance**
  ----------------- ---------------------------- ------------------------
  Bull Bar          Close \> Open                Buyers dominated this
                                                 bar interval

  Bear Bar          Close \< Open                Sellers dominated this
                                                 bar interval

  Doji/Neutral      abs(Close - Open) \<         Indecision, neither side
                    threshold (e.g., 10% of bar  dominated
                    range)                       

  Strong Bull Bar   Bull bar + close in upper    Decisive buyer control,
                    25% of range + body \> 60%   no significant selling
                    of range                     pressure

  Strong Bear Bar   Bear bar + close in lower    Decisive seller control,
                    25% of range + body \> 60%   no significant buying
                    of range                     pressure

  Rejection Bar     Long lower wick (\> 50% of   Sellers tried, buyers
  (Bull)            range) + close in upper 50%  rejected; demand at lows

  Rejection Bar     Long upper wick (\> 50% of   Buyers tried, sellers
  (Bear)            range) + close in lower 50%  rejected; supply at
                                                 highs
  -----------------------------------------------------------------------

**3.2 Leg Analysis (Effort vs. Result Engine)**

A leg is a directional move between two swing points. The core
measurement compares effort expended to result achieved across
successive legs.

  -----------------------------------------------------------------------
  **Metric**         **Calculation**             **What It Measures**
  ------------------ --------------------------- ------------------------
  Leg Displacement   Close of last bar in leg -  Net result achieved by
                     Open of first bar in leg    the dominant side
                     (absolute)                  

  Leg Bar Count      Number of bars in the leg   Time/effort spent to
                                                 achieve the displacement

  Leg Cumulative     Sum of (High - Low) for     Total energy expended
  Range              each bar in the leg         (effort proxy)

  Efficiency Ratio   Leg Displacement / Leg      How efficiently effort
                     Cumulative Range            converted to result (1.0
                                                 = perfect, 0.0 = zero
                                                 progress)

  Leg Momentum       Leg Displacement / Leg Bar  Displacement per unit of
                     Count                       time

  Pullback Depth     Deepest retracement into    How much the
                     prior leg / Prior leg       counter-side can claw
                     displacement                back (\< 0.5 = healthy
                                                 trend)

  Pullback Duration  Bars in pullback / Bars in  Relative time spent
                     prior impulse leg           retracing vs. advancing
  -----------------------------------------------------------------------

**Degradation Detection:** Compare Leg\[N\] metrics to Leg\[N-1\]. If
Efficiency Ratio is declining AND Leg Momentum is declining across
successive impulse legs, the trend is degrading. This is the algorithmic
implementation of Effort vs. Result.

**3.3 Signs of Weakness (Bull Trend) / Signs of Strength (Bear Trend)**

These are the specific OHLCV-measurable conditions that signal a trend
is losing conviction. For bear trend, invert all conditions.

  -----------------------------------------------------------------------
  **Signal**         **OHLCV Measurement**           **Scoring Weight**
  ------------------ ------------------------------- --------------------
  Bear bar ratio     Count of bear bars / total bars 1
  increasing         in rolling window is rising     

  Consecutive bear   3+ consecutive bear bars appear 2
  bars               within a bull trend             

  Bear bar size      Average body size of bear bars  2
  increasing         exceeds average body size of    
                     bull bars in rolling window     

  Deeper pullbacks   Current pullback depth \>       2
                     previous pullback depth         

  Stalling at highs  Multiple bars with highs within 2
                     a tight range (\< 0.25 ATR) but 
                     no upward breakout              

  Upper wicks        Average upper wick / total      1
  growing            range increasing in rolling     
                     window                          

  Efficiency ratio   Current impulse leg efficiency  3 (primary)
  declining          \< prior impulse leg efficiency 

  Momentum declining Current impulse leg momentum \< 3 (primary)
                     prior impulse leg momentum      
  -----------------------------------------------------------------------

**Composite Score:** Sum the weighted scores. A score above a calibrated
threshold (backtest to determine) triggers a DISTRIBUTION alert. This
does not trigger a trade; it changes the phase classification, which in
turn changes valid setups.

**4. FAILED SIGNALS PLAYBOOK**

A failed signal is often more powerful than the original signal. When
market participants commit to a direction and are proven wrong, their
forced exits create acceleration in the opposite direction. The
algorithm must monitor for and trade these.

  ------------------------------------------------------------------------
  **Failed         **Detection**              **Trading Response**
  Signal**                                    
  ---------------- -------------------------- ----------------------------
  Failed Breakout  Price breaks above range   Short setup. Bulls who
  (Bull)           high, then closes back     bought the breakout are now
                   inside range within 1-3    trapped. Their stop-losses
                   bars. Wick(s) above range  (below range high) become
                   with close(s) below.       fuel for the down move.
                                              Target: range low or POC.

  Failed Breakout  Price breaks below range   Long setup. Bears who sold
  (Bear)           low, then closes back      the breakdown are trapped.
                   inside range within 1-3    Target: range high or POC.
                   bars.                      

  Failed Sign of   Signs of weakness appear   Continuation long. Bears who
  Weakness         in bull trend, but price   shorted the weakness are
                   fails to break the prior   trapped. This is a strong
                   swing low and instead      continuation signal; trend
                   makes a new high.          has more legs.

  Failed Sign of   Signs of strength appear   Continuation short. Bulls
  Strength         in bear trend, but price   who bought the strength
                   fails to break the prior   signal are trapped.
                   swing high and instead     
                   makes a new low.           

  Spring (Wyckoff) In accumulation, price     High-conviction long. This
                   briefly breaks below range is the classic Wyckoff
                   low (shaking out longs),   spring. Best R:R setup in
                   then immediately reverses  the system. Stop below the
                   back into range with       spring low.
                   strong bull bar(s).        

  Upthrust         In distribution, price     High-conviction short.
  (Wyckoff)        briefly breaks above range Classic Wyckoff upthrust.
                   high, then immediately     Stop above the upthrust
                   reverses back into range   high.
                   with strong bear bar(s).   
  ------------------------------------------------------------------------

**Core Principle:** Trapped traders create the best moves. Every failed
signal identifies a group of market participants who are now wrong and
will need to exit, providing fuel for the opposite direction.

**5. TRADE SETUPS BY MARKET PHASE**

Each market phase has a specific set of valid setups. Trading setups
from the wrong phase is the primary source of losses. The algorithm must
enforce phase-appropriate behavior.

  -------------------------------------------------------------------------------
  **Phase**   **Valid Setups**  **Entry Trigger   **Stop Logic**  **Target
                                (LTF)**                           Logic**
  ----------- ----------------- ----------------- --------------- ---------------
  TREND       Pullback to       Rejection bar at  Below the swing Next HTF SR
  (Bull)      SR/MA; Pullback   support zone on   low of the      level above.
              to prior breakout LTF; LTF makes    pullback (LTF). Minimum 2:1
              level; Failed     higher low at HTF If stop is \>   R:R. Trail stop
              sign of weakness  support; Bull bar 1.5 ATR, skip   to breakeven
              (continuation)    closing above key the trade.      after 1R
                                LTF level                         profit.

  TREND       Rally to SR/MA;   Rejection bar at  Above the swing Next HTF SR
  (Bear)      Rally to prior    resistance on     high of the     level below.
              breakdown level;  LTF; LTF makes    rally (LTF). If Minimum 2:1
              Failed sign of    lower high at HTF stop is \> 1.5  R:R. Trail stop
              strength          resistance; Bear  ATR, skip.      to breakeven
              (continuation)    bar closing below                 after 1R.
                                key LTF level                     

  BALANCE     Fade range edges; Rejection bar at  Beyond the      Opposite range
              Spring/Upthrust   range high/low on range extreme   edge or POC
              (failed breakout  LTF; Failed       (high/low +     (most-traded
              at range          breakout bar;     buffer of 0.25  price in
              boundary)         Spring/upthrust   ATR). Tight     range). Scale:
                                reversal bar      stops since     50% at POC, 50%
                                                  range edges are at opposite
                                                  well-defined.   edge.

  BREAKOUT    Retest of broken  Price retests the Below the       Measured move:
              range edge; First broken range      retest low (for range height
              pullback after    boundary and      bull breakout)  projected from
              breakout          holds (prior      or above retest breakout point.
              confirmation      resistance        high (for bear  Or next HTF SR
                                becomes support,  breakout). If   level.
                                or vice versa).   price re-enters 
                                LTF shows         range, exit     
                                rejection at      immediately.    
                                retest level.                     
  -------------------------------------------------------------------------------

**5.1 Breakout Qualification Criteria**

Not all breakouts are tradeable. The algorithm applies these filters
before accepting a breakout signal:

  ------------------------------------------------------------------------
  **Criterion**    **Measurement**            **Minimum Threshold**
  ---------------- -------------------------- ----------------------------
  Range Maturity   Number of bars the range   Longer ranges produce more
                   has existed                reliable breakouts. Minimum:
                                              20 bars on analysis TF.

  Breakout Bar     Body size of breakout bar  Body \> 0.75 ATR. Close
  Strength         relative to ATR            beyond range boundary (not
                                              just wick).

  Follow-Through   Next 1-3 bars after        At least 1 follow-through
                   breakout                   bar closing in breakout
                                              direction without
                                              re-entering range.

  Prior Trap       Spring/upthrust before     If present, increases
  (Bonus)          breakout                   conviction significantly.
                                              Not required.

  No Immediate     Distance to next HTF SR in Minimum 1.5x range height of
  Resistance       breakout direction         open space. Otherwise the
                                              breakout has no room to run.
  ------------------------------------------------------------------------

**6. MULTI-TIMEFRAME STRUCTURE**

The algorithm operates across three timeframes. Each has a distinct
role. All three must be consulted before any trade is taken.

  -------------------------------------------------------------------------
  **Timeframe    **Purpose**       **What It Determines** **Example
  Role**                                                  (Swing)**
  -------------- ----------------- ---------------------- -----------------
  Analysis TF    Strategic         Current phase, major   Daily / Weekly
  (HTF)          context. Where    SR zones, trend        
                 the big picture   direction, whether we  
                 lives.            are near a significant 
                                   level.                 

  Trigger TF     Tactical          Phase classification   4H / 1H
  (MTF)          execution. Where  at trade level, effort 
                 setups form.      vs result measurement, 
                                   leg analysis, sign of  
                                   weakness/strength      
                                   detection.             

  Entry TF (LTF) Precision entry.  Exact entry bar, stop  15min / 5min
                 Where the trigger placement, initial     
                 fires.            risk calculation.      
                                   Confirms               
                                   acceptance/rejection   
                                   at HTF/MTF levels.     
  -------------------------------------------------------------------------

**6.1 Alignment Rules**

**Rule 1 --- Never trade against the Analysis TF:** If the Daily is in
TREND_BEAR, do not take long setups on the 4H. Exception: fade setups at
extreme HTF SR levels with strong rejection.

**Rule 2 --- Trigger TF confirms the setup:** The setup must be visible
and valid on the Trigger TF before looking at the Entry TF.

**Rule 3 --- Entry TF provides timing only:** Never analyze the market
on the Entry TF. Use it solely for entry bar identification and stop
placement.

**Rule 4 --- Manage on Trigger TF:** Trailing stops, target adjustments,
and exit decisions are based on the Trigger TF structure, not the Entry
TF noise.

**7. RISK FRAMEWORK**

No strategy is complete without explicit risk management. These rules
are non-negotiable and the algorithm enforces them before any trade is
placed.

  ------------------------------------------------------------------------
  **Rule**          **Specification**           **Rationale**
  ----------------- --------------------------- --------------------------
  Risk Per Trade    1-2% of account equity.     Survival first. No single
                    Fixed before entry.         trade should threaten the
                                                account.

  Position Sizing   Position Size = (Account    Ensures consistent dollar
                    Risk) / (Entry Price - Stop risk regardless of stop
                    Price). Calculated          distance.
                    dynamically per trade.      

  Maximum Open Risk Total open risk across all  Correlated positions can
                    positions ≤ 6% of equity.   move together. Cap
                                                aggregate exposure.

  Minimum R:R       2:1 for trend trades. 1.5:1 Ensures the system is
                    for range fades (higher win profitable even with \<
                    rate expected).             50% win rate.

  Maximum Stop      Stop distance must not      If the stop is too wide,
  Width             exceed 1.5x ATR of the      the setup is not clean.
                    Trigger TF.                 Skip it.

  Breakeven Rule    Move stop to breakeven      Eliminate risk once the
                    after price moves 1R in     trade has proven its
                    your favor.                 thesis.

  Time Stop         If price has not moved 0.5R Dead trades tie up capital
                    in your favor within a      and often result in
                    defined bar count (e.g., 10 losses.
                    bars on Entry TF), exit at  
                    market.                     

  No Revenge Trades After a stop-out, the       Emotional re-entries are
                    algorithm must wait for the the #1 account killer.
                    next valid setup on the     
                    Trigger TF. No re-entry on  
                    the same bar.               
  ------------------------------------------------------------------------

**8. ALGORITHM DECISION TREE**

This is the step-by-step logic the algorithm executes on each new bar.
It is designed to be implemented as a state machine.

**STEP 1: Update SR Levels:** On each new HTF bar: recalculate SR zones
using swing high/low detection + consolidation clustering. Merge
overlapping zones. Rank by number of touches and recency.

**STEP 2: Classify Phase (HTF):** Using leg analysis and bar
characteristics on the Analysis TF, determine: TREND_BULL, TREND_BEAR,
BALANCE, BREAKOUT, DISTRIBUTION, or ACCUMULATION.

**STEP 3: Classify Phase (Trigger TF):** Repeat phase classification on
the Trigger TF. This is the operative phase for setup selection.

**STEP 4: Check Proximity to SR:** Is current price within 1 ATR of any
HTF SR zone? If NO: no setup possible, wait. If YES: proceed.

**STEP 5: Select Valid Setups:** Based on Trigger TF phase, pull the
valid setup list from Section 5. Filter out setups that conflict with
HTF phase (Rule 1 from Section 6).

**STEP 6: Evaluate Effort vs. Result:** Run leg analysis on the most
recent impulse and pullback on Trigger TF. Calculate Efficiency Ratio,
Momentum, Pullback Depth. Flag degradation if present.

**STEP 7: Wait for Entry Trigger (LTF):** Drop to Entry TF. Wait for the
specific entry bar type defined for the active setup. No trigger = no
trade.

**STEP 8: Calculate Risk:** Determine stop level (per setup rules).
Calculate position size. Verify: stop ≤ 1.5 ATR, R:R ≥ minimum, total
open risk ≤ 6%.

**STEP 9: Execute:** If all checks pass, enter the trade. Set stop-loss
order immediately. Set target order(s). Log entry with all metadata.

**STEP 10: Manage:** On each new Trigger TF bar: check breakeven rule,
time stop, trailing stop logic. On each new HTF bar: verify the macro
thesis still holds. If phase changes, tighten stops.

**9. DATA & IMPLEMENTATION REQUIREMENTS**

**9.1 OHLCV Data Requirements**

  ------------------------------------------------------------------------
  **Field**     **Usage**                   **Notes**
  ------------- --------------------------- ------------------------------
  Open          Bar classification, leg     Critical for body calculation
                start/end                   

  High          Swing detection, wick       Used for SR zone
                analysis, range calculation identification

  Low           Swing detection, wick       Used for SR zone
                analysis, range calculation identification

  Close         Bar classification,         Primary signal field
                displacement, trend         
                detection                   

  Volume        Optional confirmation only  Used for divergence detection.
                                            System works without it. Never
                                            a primary trigger.
  ------------------------------------------------------------------------

**9.2 Derived Metrics (Computed Per Bar)**

  -----------------------------------------------------------------------
  **Metric**            **Formula**
  --------------------- -------------------------------------------------
  Bar Range             High - Low

  Bar Body              abs(Close - Open)

  Upper Wick            High - max(Open, Close)

  Lower Wick            min(Open, Close) - Low

  Body Ratio            Body / Range (0 to 1)

  Close Position        (Close - Low) / Range (0 = closed at low, 1 =
                        closed at high)

  ATR(N)                Rolling average of Bar Range over N periods
                        (default N=14)

  Swing High            Bar where High \> High of N bars before AND N
                        bars after

  Swing Low             Bar where Low \< Low of N bars before AND N bars
                        after
  -----------------------------------------------------------------------

**9.3 State Variables (Maintained by Algorithm)**

  --------------------------------------------------------------------------
  **Variable**            **Type**       **Description**
  ----------------------- -------------- -----------------------------------
  current_phase_htf       Enum           Phase on Analysis TF

  current_phase_trigger   Enum           Phase on Trigger TF

  active_sr_zones\[\]     List of        Current SR levels ranked by
                          (price, width, importance
                          strength)      

  legs\[\]                List of Leg    Historical leg data with all
                          objects        metrics

  weakness_score          Float          Current composite weakness/strength
                                         score

  open_positions\[\]      List of        Active trades with entry, stop,
                          Position       target, R-multiple tracking
                          objects        

  total_open_risk         Float          Sum of risk across all open
                                         positions as % of equity
  --------------------------------------------------------------------------

**10. BACKTESTING & VALIDATION CHECKLIST**

Before deploying capital, each component must be validated individually
and then as an integrated system.

  ----------------------------------------------------------------------------
  **Component**       **Validation Method**       **Pass Criteria**
  ------------------- --------------------------- ----------------------------
  SR Zone Detection   Plot detected zones on      \> 70% of detected zones
                      historical charts. Visual + show a price reaction
                      quantitative check for      (reversal or consolidation)
                      accuracy.                   within the zone.

  Phase               Run classifier on           \> 80% agreement with manual
  Classification      historical data. Compare to phase labels.
                      manual labeling on a        
                      sample.                     

  Leg Analysis        Log all computed legs.      Metrics within 5% of manual
                      Verify swing detection and  calculation on sample data.
                      metric calculations match   
                      manual measurement.         

  Weakness/Strength   Track score at known        Score crosses threshold
  Score               transition points           before or at the start of
                      (historical                 the transition \> 65% of the
                      distribution/accumulation   time.
                      zones).                     

  Entry Signals       Paper trade or backtest     Win rate + average R:R
                      each setup independently.   produces positive expectancy
                                                  per setup.

  Risk Management     Simulate worst-case         Maximum drawdown stays
                      sequences (consecutive      within acceptable limits
                      losses).                    (suggested: \< 15% of
                                                  equity).

  Full System         Walk-forward backtest:      Positive expectancy on
                      optimize on in-sample,      out-of-sample data. Sharpe
                      validate on out-of-sample.  \> 1.0. Profit factor \>
                                                  1.5.
  ----------------------------------------------------------------------------

**10.1 Key Metrics to Track**

  -----------------------------------------------------------------------
  **Metric**                 **Target**
  -------------------------- --------------------------------------------
  Win Rate                   \> 40% (with 2:1+ R:R, this is profitable)

  Average Win / Average Loss \> 2.0

  Profit Factor              \> 1.5

  Maximum Drawdown           \< 15% of peak equity

  Sharpe Ratio               \> 1.0 (annualized)

  Average Bars in Trade      Monitor for drift; should remain stable

  Setup Distribution         Track which setups generate most profit;
                             prune losers

  Phase Classification       Ongoing monitoring vs. manual review
  Accuracy                   
  -----------------------------------------------------------------------
