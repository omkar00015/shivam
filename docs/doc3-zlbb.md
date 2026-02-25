**ZLBB & LEG STATE ENGINE**

**Deterministic Market Structure Generator**

**1. PURPOSE OF THIS DOCUMENT**

Define:

✔ exact ZLBB mathematics\
✔ how bands are formed\
✔ when price is considered extended\
✔ how legs start\
✔ how legs end\
✔ how band walks override reversals\
✔ how squeezes behave\
✔ how retro validation works\
✔ what is stored\
✔ what is illegal

This engine converts candles → **structure**.

**2. INPUT CONTRACT**

From Document 2.

For each timeframe:

completed bars only

ATR available

**3. ZERO LAG EMA (ZLEMA)**

**3.1 Parameters**

period = 20

lag = (period - 1) / 2 = 9.5 → round to 10

**3.2 Lag Adjusted Price**

lag_adjusted_price\[i\] = close\[i\] + (close\[i\] - close\[i-lag\])

**3.3 ZLEMA Formula**

Standard EMA applied to lag adjusted price.

alpha = 2 / (period + 1)

ZLEMA\[i\] = alpha \* lag_adjusted_price\[i\] + (1 - alpha) \*
ZLEMA\[i-1\]

**4. STANDARD DEVIATION & BANDS**

**4.1 Sigma**

Rolling standard deviation of close over **20** bars.

**4.2 Band Multiplier**

multiplier = 2.0

**4.3 Bands**

upper_band = ZLEMA + multiplier × sigma

lower_band = ZLEMA - multiplier × sigma

**5. BANDWIDTH**

bandwidth = (upper - lower) / ZLEMA

Stored for percentile logic.

**6. ZLBB MODES**

At any moment instance is in:

NORMAL

BAND_WALK

SQUEEZE_BREAKOUT

**7. LEG STATE MACHINE**

**7.1 States**

SEEKING_DIRECTION

IN_BULL_LEG

IN_BEAR_LEG

**8. LEG START CONDITIONS**

**8.1 Bull Leg Start**

Occurs when:

close \>= upper_band - EPSILON

**8.2 Bear Leg Start**

Occurs when:

close \<= lower_band + EPSILON

If both false → remain SEEKING.

**9. LEG CONTINUATION**

While in bull leg:

If price remains above ZLEMA → continue.

Same mirrored for bear.

**10. NORMAL REVERSAL CONDITION**

A bull leg reversal candidate occurs when:

close \<= ZLEMA - EPSILON

Bear mirrored.

BUT this is NOT enough.\
Band walk may block it.

**11. BAND WALK ENGINE**

**11.1 Entry into Band Walk**

When during leg:

\>= 3 consecutive closes outside same band

**11.2 Pullback Measurement**

Let:

leg_high = highest price since leg start

current_low = lowest after peak

retracement = (leg_high - current_low) / (leg_high - leg_start)

**11.3 Reversal Permission**

If:

retracement \< 0.25

→ ignore reversal → continue leg.

If:

retracement ≥ 0.25

→ reversal allowed.

Binary.

**12. LEG COMPLETION**

When reversal confirmed:

Create Leg object.

**12.1 Required Calculations**

displacement = abs(end_price - start_price)

bar_count = number of bars in leg

cumulative_range = sum(high - low)

efficiency = displacement / cumulative_range

momentum = displacement / bar_count

**13. LEG QUALITY SCORE**

Used later for SR weight.

Assign points:

  -----------------------------------------------------------------------
  **Condition**                                             **Points**
  --------------------------------------------------------- -------------
  displacement ≥ 1.0 ATR                                    +1

  efficiency ≥ 0.5                                          +1

  momentum ≥ median of last 10                              +1

  closed beyond band decisively                             +1
  -----------------------------------------------------------------------

quality ∈ \[0..4\]

**14. NEW LEG INITIALIZATION**

After completion:

Switch state → opposite.

Set new start at reversal bar.

**15. SQUEEZE DETECTION**

**15.1 Entry**

If:

bandwidth_percentile ≤ 20

**15.2 Exit**

When percentile \> 20.

**15.3 Behavior**

First breakout bar after squeeze:

If close outside band → immediate leg start.

Ignore seeking.

**16. RETROACTIVE VALIDATION**

Occurs only AFTER opposite leg completes.

**Purpose**

Determine if near-miss should be separate leg.

**16.1 Scoring (6 rules)**

Add 1 each:

P1: reached ≥ 60% of band distance

P2: displacement ≥ 0.5 ATR

P3: stayed ≥ 5 bars

P4: distance from ZLEMA ≥ 1 sigma

P5: reversal reached opposite band

P6: reversal efficiency ≥ 0.5

If:

score ≥ 3 → create retro leg

**16.2 Tag**

origin = RETRO

**17. WHAT IS A DECISIVE CLOSE**

To avoid wick ambiguity.

**Bull decisive:**

body ≥ 60% of range

close_position ≥ 0.75

**Bear mirrored.**

**18. MULTI-TF OWNERSHIP RULE**

Lower TF produces legs for higher TF SR.

Example:

15m → builds 1H structure

Mapping fixed in config.

**19. STATE STORAGE**

Each ZLBB instance must persist:

current mode

current state

current leg start

running extremes

completed legs

**20. WHAT IS ILLEGAL**

❌ intrabar reversal\
❌ wick-only break\
❌ ignoring band walk\
❌ retro affecting history\
❌ using incomplete candle

**OUTPUT OF THIS DOCUMENT**

After this layer we have:

✔ confirmed legs\
✔ objective turning points\
✔ quality metrics\
✔ directional history

Now SR can be born.
