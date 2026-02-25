**ADDENDUM C**

**Execution Engine & Conflict Resolution**

Order Execution, Position Management, Signal Priority & System Pause
Logic

*Supplement to: Algorithmic Trading Framework v1.0 + Addenda A & B \|
February 2026*

C1. Order Execution Logic

The algorithm uses a decision tree to determine market vs limit orders
based on setup urgency and price action context. Speed and price
optimization are balanced according to setup type.

C1.1 Order Type Selection by Setup Category

  ----------------------- ---------------- -------------------------------
  **Setup Category**      **Order Type**   **Rationale**

  Squeeze Breakout,       MARKET ORDER     Momentum setups. Speed \>
  Failed Breakout,                         price. Slippage acceptable (0.5
  Spring, Upthrust                         ATR max).

  SR Fakeout, Reclaim of  LIMIT ORDER      SR-based mean reversion. Wait
  Zone, Engulfing at SR                    for price to come to you. Place
                                           at zone_center ± 0.10 ATR.
                                           Cancel if not filled within 3
                                           bars.

  Weak Candle             STOP LIMIT       Enter on confirmation of
  Continuation                             continuation. Trigger: break of
                                           weak_candle high/low. Limit:
                                           trigger ± 0.05 ATR.
  ----------------------- ---------------- -------------------------------

C1.2 Limit Order Cancellation Logic

ON each new Entry TF bar while limit order active:

-   IF bars_since_order_placed \> timeout_threshold: Cancel limit order,
    mark setup as MISSED (log for analysis), do NOT chase with market
    order

-   IF price moved \> 0.5 ATR away from limit_price: Cancel limit order,
    setup invalidated (momentum shifted)

-   IF Entry TF bar closes beyond the SR zone in entry direction: Cancel
    limit order, price broke through zone, setup failed

C1.3 Slippage Tolerance

MARKET ORDERS:

Max acceptable slippage = 0.5 ATR (Entry TF, 14 periods)

IF actual_fill_price - intended_entry_price \> slippage_tolerance: Log
as EXCESSIVE_SLIPPAGE, recalculate position size with actual fill. If
new stop_distance \> 1.5 ATR: ABORT trade immediately.

C2. Partial Exit Rules

C2.1 Exit Tier System

Every trade has a 3-tier exit structure. All exits are managed on the
Trigger TF, not Entry TF.

**POSITION ALLOCATION:**

-   Tier 1: 40% of position → First target (mechanical)

-   Tier 2: 40% of position → Second target (mechanical)

-   Tier 3: 20% of position → Runner (trailing stop)

NEVER exit entire position at once unless: Stop hit, Phase change
invalidates setup, Time stop triggered, or System pause condition met.

C2.2 Tier 1 Exit (First Target)

  -------------------------- --------------------------------------------
  **Setup Type**             **Target 1 Calculation**

  Range Fade, SR Fakeout     range_POC (Point of Control). If distance \<
                             1.0 ATR: target_1 = 0.5 × (entry to
                             opposite_range_edge)

  Pullback, Retracement      prior_swing_extreme (the high/low where
                             pullback started)

  Breakout, Reclaim of Zone  entry_price + (1.5 × initial_stop_distance)

  Squeeze Breakout, Failed   entry_price + (2.0 × initial_stop_distance).
  Signal                     High-conviction setups.
  -------------------------- --------------------------------------------

C2.3 Tier 2 Exit (Second Target)

  -------------------------- --------------------------------------------
  **Setup Type**             **Target 2 Calculation**

  Range Fade                 opposite_range_edge

  Trend, Breakout, Reclaim   next_HTF_SR_zone. If distance \< 2.0 ATR:
                             entry + (3.0 × stop_distance)

  Squeeze Breakout, Failed   entry_price + (3.5 × initial_stop_distance)
  Signal                     
  -------------------------- --------------------------------------------

**DYNAMIC ADJUSTMENT:**

-   IF Trigger_TF_ZLBB enters BAND_WALK mode in trade direction: Cancel
    Tier 2 limit order, convert to trailing stop. Band walks produce
    extended moves.

-   IF price reaches within 0.25 ATR of target_2 but reverses: Move
    target_2 to entry_price + (2.0 × stop_distance). Don\'t let a winner
    turn into a loser.

C2.4 Tier 3 Exit (Runner with Trailing Stop)

TRAILING STOP ACTIVATION:

Activated after BOTH Tier 1 and Tier 2 have filled OR when Trigger TF
enters BAND_WALK mode (even if Tier 2 not filled).

  ----------------------- -----------------------------------------------
  **Profit Level**        **Trail Stop Placement**

  1.0R ≤ profit \< 2.0R   trail_stop = entry_price (breakeven, lock in
                          zero loss)

  2.0R ≤ profit \< 3.0R   trail_stop = entry + (1.0 ×
                          initial_stop_distance). Lock in 1R profit.

  3.0R ≤ profit \< 5.0R   trail_stop = highest_favorable_close - (1.0 ×
                          ATR). Trail by 1 ATR.

  profit ≥ 5.0R           trail_stop = highest_favorable_close - (0.75 ×
                          ATR). Tighten trail.
  ----------------------- -----------------------------------------------

**ALTERNATIVE TRAIL (for BAND_WALK scenarios):**

IF Trigger_TF_leg_mode == BAND_WALK in trade direction: trail_stop =
Trigger_TF_ZLEMA. If price closes beyond ZLEMA (against trade
direction): EXIT immediately at market, band walk terminated.

C3. Position Pyramiding & Scaling Rules

C3.1 When Scaling Is Allowed

**PYRAMIDING ENABLED ONLY IF ALL CONDITIONS MET:**

-   Current phase remains same as entry phase (no TREND → DISTRIBUTION
    transition)

-   Open position showing ≥ 1.0R profit

-   Trigger_TF_leg_efficiency ≥ prior leg efficiency (no degradation
    detected)

-   current_total_open_risk + new_position_risk ≤ 6%

-   New signal is A-GRADE or higher

C3.2 Pyramiding Position Sizing

INITIAL POSITION: position_size_1 = account_risk_per_trade /
initial_stop_distance

PYRAMID ADD: position_size_2 = 0.50 × position_size_1 (Risk less on
adds)

MAX PYRAMIDS: 2 adds maximum per trade. Total: 1 initial + 2 adds = 3
position lots max.

C4. Maximum Positions Per Instrument

C4.1 Position Limits

MAX OPEN POSITIONS SYSTEM-WIDE: 5 positions total across all instruments
and directions

MAX POSITIONS PER INSTRUMENT: 1 position per direction (1 long OR 1
short, not both). Exception: Can hold both if one is closing (Tier 3
runner).

MAX POSITIONS IN SAME DIRECTION: BTC long + Gold long allowed
(uncorrelated). BTC long + ETH long NOT allowed (high correlation).
Treat crypto as single instrument class for this rule.

C5. Entry Timing Conflict Resolution

C5.1 The Conflict Matrix

When multiple signals fire on the same bar, the algorithm uses this
hierarchy:

**PHASE 1: ELIMINATION FILTERS (Binary Yes/No)**

-   HTF ALIGNMENT CHECK: If signal_direction opposes HTF_phase,
    ELIMINATE signal (Exception: Fade setup at extreme HTF SR, S-tier or
    A-tier only)

-   EXHAUSTION OVERRIDE: If DOUBLE_EXHAUSTION_BULL active, ELIMINATE all
    long signals, ALLOW short signals at resistance

-   BAND WALK FILTER: If Entry_TF in BAND_WALK_BULL, ELIMINATE short
    signals (counter-trend)

-   MINIMUM QUALITY GATE: If setup_quality \< B-GRADE, ELIMINATE signal

C5.2 Phase 2: Priority Scoring

Each remaining signal gets a composite score = setup_tier_points +
sr_tier_points + leg_quality_points + confluence_bonus +
failed_signal_bonus

  ----------------------------------- -----------------------------------
  **Component**                       **Points**

  **SETUP TIER POINTS**               

  Squeeze Breakout                    +10

  Failed Signal                       +9
  (Spring/Upthrust/Failed Breakout)   

  SR Fakeout                          +8

  **SR TIER POINTS**                  

  S-tier SR zone                      +5

  A-tier SR zone                      +3

  B-tier SR zone                      +1

  **CONFLUENCE BONUS**                

  Double Band Exhaustion zone         +3

  Trigger_TF_ZLBB outer band          +2

  **FAILED SIGNAL BONUS**             

  Failed weakness (bull trend)        +4

  Untested origin first retest        +3
  ----------------------------------- -----------------------------------

C6. System Pause Conditions

C6.1 Pause Condition 1: Volatility Shock

TRIGGER: ATR(Trigger_TF, 14) \> 3.0 × ATR(Trigger_TF, 60). Current
volatility \> 3x the longer-term average indicates market in extreme
regime, edge likely degraded.

PAUSE DURATION: Until ATR(Trigger_TF, 14) \< 2.0 × ATR(Trigger_TF, 60).
Minimum 24 Trigger TF bars (96 hours for 4H chart).

EXISTING POSITIONS: Move all stops to breakeven immediately. Do NOT
trail stops tighter (volatility can whipsaw). Allow targets to hit
normally. No new adds/pyramiding.

C6.2 Pause Condition 2: Whipsaw Sequence

TRIGGER: 5+ consecutive ZLBB legs on Entry TF where each leg
displacement \< 0.75 ATR AND efficiency_ratio \< 0.35 AND no leg
completed a full band-to-band traversal.

Interpretation: Choppy, directionless market. ZLBB system generating
noise, not signal.

C6.3 Pause Condition 3: Consecutive Stop-Outs

TRIGGER: 4 consecutive stopped-out trades (full losses). Interpretation:
System edge temporarily broken, phase misclassification or parameter
drift.

PAUSE DURATION: Manual review required, minimum 48 hours.

C6.4 Pause Condition 4: Drawdown Threshold

TRIGGER: current_drawdown ≥ 12% from peak equity

Calculation: peak_equity = max(account_equity) over trailing 60 calendar
days; current_drawdown = (peak_equity - current_equity) / peak_equity

PAUSE DURATION: Until account recovers to \< 10% drawdown OR 14 calendar
days pass (whichever comes first). After 14 days, resume with REDUCED
RISK.

C6.5 Pause Condition 5: Data Quality Issues

TRIGGER: Missing bars detected (gap in timestamps \> 2× bar period),
ZLBB computation fails (NaN or Inf values), SR zone count drops to 0
(reconstruction failure), or Negative ATR values (data corruption).

IMMEDIATE ACTION: HALT all trading immediately. Close all positions at
market (emergency exit). Alert operator.

C7. Instrument-Specific Configurations

C7.1 BTC/USDT Profile

INSTRUMENT: BTC/USDT (Perpetual or Spot)

-   ZLBB PARAMETERS: period: 20, std_dev_mult: 2.0

-   SR ZONE WIDTH: min_width: \$50, max_width: \$500, buffer_min: \$20,
    buffer_max: \$150

-   POSITION SIZING: max_position_value: 5% of account equity (BTC
    volatile, limit exposure)

-   ORDER EXECUTION: preferred_order_type: LIMIT (deep liquidity, fills
    reliably), limit_timeout: 3 Entry TF bars

-   MARKET HOURS: trading: 24/7, weekend_gaps: None (crypto continuous)

C7.2 Gold (XAU/USD) Profile

INSTRUMENT: Gold Spot (XAU/USD)

-   ZLBB PARAMETERS: period: 20, std_dev_mult: 2.0

-   SR ZONE WIDTH: min_width: \$3, max_width: \$25, buffer_min: \$1,
    buffer_max: \$8

-   POSITION SIZING: max_position_value: 4% of account equity

-   MARKET HOURS: trading: 23 hours (Sunday 6PM EST - Friday 5PM EST).
    Close all positions before Friday 4:30PM EST. Do NOT carry positions
    over weekend.

C8. Implementation Requirements

C8.1 Implementation Checklist

Before live deployment, verify:

-   Order execution logic tested (market, limit, stop-limit)

-   Partial exit tiers implemented (40/40/20 split)

-   Trailing stop logic validated on historical data

-   Pyramiding rules tested (max 2 adds, risk cap respected)

-   Position limits enforced (5 total, 1 per instrument per direction)

-   Conflict resolution matrix tested with simultaneous signals

-   All 5 pause conditions monitored in real-time

-   Pause/resume state machine functioning

-   Reduced risk mode activates correctly post-drawdown

-   BTC and Gold instrument profiles loaded

-   State persistence/recovery tested

**FINAL VALIDATION: Forward test on paper account for 30 days minimum.
Track all pause conditions, conflicts, and exits. Verify expectancy
matches backtest within 20%.**

C8.2 Integration Map

This addendum integrates with:

-   Main Framework: Risk Framework - Order execution logic, slippage
    tolerance, 3-tier exits replace generic target/stop rules

-   Main Framework: Decision Tree Step 5 - Signal priority scoring and
    conflict resolution replace simple setup selection

-   Addendum A: Leg Detection - Leg quality grades feed into conflict
    resolution scoring. Band Walk mode triggers Tier 2 exit conversion.

-   Addendum A: Exhaustion Zones - Double Band Exhaustion triggers early
    exit override. Blocks counter-trend signals in Phase 1 filters.

-   Addendum B: SR Tiers - SR tier (S/A/B) contributes to composite
    signal score. Influences order type selection.

-   Addendum B: Fakeout Setups - All 9 setups assigned tier points for
    conflict resolution. Order type mapped per setup category.

**END ADDENDUM C**
