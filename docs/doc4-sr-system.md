**SUPPORT & RESISTANCE ENGINE**

**Zone Creation, Scoring, Clustering & Lifecycle**

**1. PURPOSE OF THIS DOCUMENT**

Define:

✔ where SR comes from\
✔ how wide zones are\
✔ when levels merge\
✔ how strength is calculated\
✔ how zones evolve\
✔ when they die\
✔ how overlapping authority is resolved\
✔ how density bias is derived

After this layer, the system knows:

**Where reactions are statistically expected.**

**2. INPUT CONTRACT**

From Document 3 we receive:

completed legs

leg quality

extremes

timestamps

From Document 2:

ATR per timeframe

**3. WHAT IS AN SR ZONE**

An SR zone is a **price area**, not a line.

Defined by:

center

upper boundary

lower boundary

strength

tier

lifecycle

**4. SOURCES OF SR**

Zones may originate from four mechanisms.

Each contributes weight.

**4.1 Leg Extremes (PRIMARY)**

For each completed leg:

bull leg → high becomes resistance candidate

bear leg → low becomes support candidate

**Base Strength Points**

2 + leg_quality

So quality 4 → 6 points.

**4.2 Open / Close Clusters**

If ≥ 3 opens/closes fall within:

distance ≤ 0.15 ATR\
ATR should of the respective TF.\
E.g. while assessing 1D open/closes, ATR of 1 Day should be used.

Add zone.

**Strength**

+2

**4.3 Rejection Bars\
**[Two types in Rejection Bars:]{.underline} Hammer and Inverted Hammer
and Engulfing Candles

If wick ≥ 50% of total range AND reversal occurs within next 3 bars.\
In the context of a bullish Rejection bar(Lower wick \>= 50% of total
range of the candle) conditions for valid reversals: Price should not go
below the rejection bar in next 3 Candles.\
and price should close above the high of the rejection candle.\
[OR]{.underline}\
we get an engulfing pattern and price Reverses within next 3 Bars\
In the context of a bullish Engulfing Candle (Define Engulfing Candle
appropriately)\
conditions for valid reversals: Price should not go below the Engulfing
bar in next 3 Candles.\
and price should close above the high of the Engulfing candle.

**Strength**

+2

**4.4 Midpoints of Strong Legs**

If:

displacement ≥ 2 ATR

Add midpoint.\
Here ATR means average of ATR at the beginning of the leg and at the end
of the leg.

**Strength**

+1

Tag:

is_midpoint = True

Don't finalize the midpoint zone as a valid SR. On an Immediate LTF, If
we get \>= 2 Rejections around the Midpoint Zone or Price Stays there on
that LTF for \>= 5 Candles, then only that Midpoint SR becomes Valid.
And That SR is on That LTF TF.

**5. INITIAL ZONE WIDTH**

Volatility adaptive.

half_width = clamp(0.15 × ATR, min_width, max_width)

**Defaults**

min_width = 0.05% price

max_width = 0.50% price

**6. CLUSTERING & MERGING LAW**

To prevent noise.

**Merge Condition**

If centres within:

distance ≤ 0.25 ATR

**Merge Behaviour**

New centre = weighted average by strength.

Strength = sum.

Timeframes = union.

Touches = sum.

**6.1 Problem Statement**

After projecting completed 15m legs onto 1H, multiple SR levels may form
very close to each other.

Example:

121000

121180

121250

If treated separately:

-   Chart becomes cluttered

-   Zones overlap

-   Signals conflict

-   Risk logic becomes inconsistent

So we need a deterministic way to:

Merge structurally similar nearby levels into a single SR zone.

**6.2 Core Principle**

Two SR levels are merged if they are statistically too close relative to
1H volatility.

Distance must be normalized using 1H ATR.

**6.3 Merge Condition**

Let:

SR_i

SR_j

ATR_1H = ATR(1H, N)

merge_threshold = m × ATR_1H (e.g., 0.5 × ATR_1H)

If:

abs(SR_i - SR_j) \<= merge_threshold

Then:

SR_i and SR_j belong to same cluster.

**6.4 How the Merged SR Is Calculated**

There are three deterministic options.\
You must choose one and freeze it.

Recommended (institutional grade):

**Use Weighted Average by Leg Strength**

Let each SR come from a leg with:

displacement_i

displacement_j

Then merged level:

merged_SR =

(SR_i × displacement_i +

SR_j × displacement_j)

/

(displacement_i + displacement_j)

This preserves structural importance.

**6.5 Step-by-Step Example**

Assume:

ATR_1H = 400

merge_threshold = 0.5 × ATR_1H = 200

You have three levels:

L1 = 121000 (leg displacement = 900)

L2 = 121150 (leg displacement = 600)

L3 = 121520 (leg displacement = 800)

**Step 1 --- Compare L1 and L2**

\|121000 − 121150\| = 150

150 ≤ 200 → merge.

Cluster 1 formed:

{L1, L2}

**Step 2 --- Check L3**

Distance from cluster center candidate?

Use nearest level comparison first:

\|121150 − 121520\| = 370

370 \> 200 → L3 is separate.

So:

Cluster A = {121000, 121150}\
Cluster B = {121520}

**Step 3 --- Compute Merged SR for Cluster A**

Using weighted average:

merged_SR =

(121000×900 + 121150×600) / (900+600)

Calculate numerator:

121000×900 = 108900000

121150×600 = 72690000

Total = 181590000

Denominator:

1500

Final:

merged_SR = 121060

Rounded appropriately.

So final levels:

Cluster A → 121060

Cluster B → 121520

Instead of three levels, you now have two clean structural zones.

**6.6 Why Weighted Average Is Superior**

If you used simple midpoint:

(121000 + 121150)/2 = 121075

You ignore displacement strength.

Weighted average keeps stronger leg more influential.

More deterministic.\
More institutional.

**6.7 Deterministic Clustering Procedure**

1.  Sort SR levels ascending.

2.  Iterate sequentially.

3.  If distance ≤ threshold → same cluster.

4.  If distance \> threshold → start new cluster.

5.  For each cluster → compute merged SR.

6.  Replace cluster with merged level.

No recursion.\
No dynamic resizing.\
Fully reproducible.

**6.8 What This Achieves**

✔ Reduces noise\
✔ Prevents overlapping zones\
✔ Preserves structural strength\
✔ Keeps SR count manageable\
✔ Volatility-adjusted merging

**Final Clean Summary**

Clustering & Merging Law states:

If two SR levels are closer than k × ATR(1H),\
merge them into one level using strength-weighted averaging.

-   This ensures statistically consistent structural mapping.\
    \
    ✅ Midpoint (merged) SR is used **only after validation & clustering
    is complete**

-   ✅ Remove averaging

-   ✅ Use min(range_left, range_right) for deterministic zone sizing

-   ✅ Keep volatility cap

-   ✅ No ambiguity

Below is the corrected final version.

**7. RANGE-BASED SR ZONE DETERMINATION**

**7.1 Preconditions**

This section applies **only after**:

1.  15m legs are completed and projected to 1H.

2.  Raw SR levels are generated.

3.  Clustering & merging logic is executed.

4.  Final validated merged SR levels are produced.

Only these validated merged SR levels are used for zone construction.

Unvalidated or pre-merge levels must never be used for zone sizing.

**7.2 Objective**

Convert each validated merged SR level into a tradable zone using
structural spacing between consecutive levels.

Zone width must reflect:

-   Structural geometry

-   Not pure volatility

-   Not arbitrary fixed points

**7.3 Structural Range Calculation**

Let sorted validated merged SR levels be:

SR_1, SR_2, SR_3, \..., SR_n

For each SR_i:

range_left = abs(SR_i − SR\_{i-1})

range_right = abs(SR\_{i+1} − SR_i)

**7.4 Effective Structural Range (Deterministic)**

To remove ambiguity for midpoint SR levels:

effective_range_i = min(range_left, range_right)

Rationale:

-   Prevents zone overlap

-   Keeps zones proportionate

-   Avoids over-expansion

-   Deterministic and stable

**Boundary Handling**

For edge SR levels:

If i = 1:

effective_range_1 = range_right

If i = n:

effective_range_n = range_left

**7.5 Base Zone Width**

base_half_width_i = 0.10 × effective_range_i

This defines zone size as 10% of nearest structural spacing.

**7.6 Volatility Cap (Execution Alignment)**

To prevent oversized zones in sparse structures:

Let:

ATR_exec = ATR(15m, N)

c = execution volatility cap multiplier

Final half-width:

zone_half_width_i = min(

0.10 × effective_range_i,

c × ATR_exec

)

This ensures:

-   Structural consistency

-   Execution practicality

-   Controlled maximum width

**7.7 Final Zone Boundaries**

For each validated merged SR level:

zone_high_i = SR_i + zone_half_width_i

zone_low_i = SR_i − zone_half_width_i

Zones are symmetric around the validated SR center.

**7.8 Example**

Assume:

SR_1 = 120000

SR_2 = 121000

SR_3 = 123000

ATR(15m) = 120

c = 1.5

For SR_2:

range_left = 1000

range_right = 2000

effective_range = min(1000, 2000) = 1000

Base width:

0.10 × 1000 = 100

Volatility cap:

1.5 × 120 = 180

Final half-width:

min(100, 180) = 100

Zone:

121000 ± 100

→ 120900 -- 121100

**7.9 Structural Characteristics**

This model:

✔ Uses only validated merged SR levels\
✔ Removes midpoint ambiguity\
✔ Prevents excessive widening\
✔ Avoids overlap inflation\
✔ Aligns structural geometry with execution control\
✔ Fully deterministic

**7. TIER CLASSIFICATION**

After merging, assign.

  -----------------------------------------------------------------------
  **Strength**                                   **Tier**
  ---------------------------------------------- ------------------------
  ≥ 8                                            S

  5--7                                           A

  3--4                                           B

  1--2                                           C
  -----------------------------------------------------------------------

Only B and above are tradable.

**8. LIFECYCLE ENGINE**

Zones evolve.

**8.1 States**

FRESH

TESTED

BROKEN

FLIPPED

EXPIRED

**8.2 TESTED**

Occurs when:

price enters zone AND leaves without opposite boundary break

Increment touch_count.

**8.3 BROKEN**

If candle closes **beyond opposite boundary**. For Brocken Level to be
proven, price should stay and close above that zone for at least 3
candles. If price closes inside the zone before this happens, That
Breakout is considered as a false breakout.

**8.4 FLIPPED**

After break, polarity reverses.

Old resistance → new support.

**8.5 EXPIRED**

If:

no touch for 200 TF bars

or overshadowed by stronger zone.

**9. ZONE FREEZE MECHANISM**

If repeated failures.

If within last 20 interactions:

false breaks ≥ 3

Then:

freeze_until = current_time + 10 TF bars

Frozen zones cannot generate setups.\
\
False Breakout, is a sign of the Strength for SR Zone. If we get one
that SR is valid.\
Here its frozen for risk reduction purpose. After we get a false
Breakout, Adjust that level. (How to adjust, you decide but let me know
what changes you did before finalizing)

**10. OVERLAP RESOLUTION LAW**

When zones overlap at decision time.

Apply priority:

higher timeframe

→ higher tier

→ newer

→ narrower

Winner is active.

**11. DISTANCE & PROXIMITY MEASURE**

For setup eligibility.

distance = min(abs(price - upper), abs(price - lower))

Must satisfy Constitution rule:

distance ≤ 2 × ATR(trigger TF)\
\
Below is a **code-friendly, crisp version** suitable for inserting
directly into Document 4.

**11.1 Problem Statement**

After SR zones are generated (from completed 15m legs → projected to
1H), the system must decide:

When is price close enough to a zone to activate setup logic?

Without a proximity rule:

-   Setup logic may run far from structure.

-   The system may anticipate zones prematurely.

-   Overtrading and noise increase.

-   Computational efficiency decreases.

Therefore, a deterministic proximity gate is required.

**11.2 Objective**

Activate setup evaluation **only when price is statistically near a
valid SR zone**, adjusted for current volatility.

**11.3 Distance Definition**

For each active zone:

zone_low

zone_high

current_price = close (trigger TF)

Distance is measured from the **nearest zone boundary**:

distance = min(

abs(current_price - zone_low),

abs(current_price - zone_high)

)

Distance is always non-negative.

**11.4 Volatility-Normalized Threshold**

Distance is evaluated relative to volatility.

Let:

ATR_trigger = ATR(trigger_timeframe, N)

proximity_multiplier = k (e.g., 2.0)

threshold = k × ATR_trigger

**11.5 Activation Rule**

IF distance \<= threshold:

zone_status = ACTIVE

ELSE:

zone_status = INACTIVE

Only ACTIVE zones are allowed to trigger setup evaluation.

INACTIVE zones are ignored for entry logic.

**11.6 Important Constraints**

1.  ATR used must belong to the **trigger timeframe only**.

2.  Distance must always be computed from zone boundary, not midpoint.

3.  Proximity check must occur **before setup logic execution**.

4.  Proximity does not modify zone strength; it only gates evaluation.

**11.7 Behavioral Summary**

The Proximity Filter ensures:

-   No setup scanning far from structure.

-   Volatility-adjusted activation.

-   Reduced noise and overtrading.

-   Deterministic and consistent behavior across regimes.

**11.8 Minimal Pseudocode**

def is_zone_active(zone, current_price, atr_trigger, k):

distance = min(

abs(current_price - zone.low),

abs(current_price - zone.high)

)

threshold = k \* atr_trigger

return distance \<= threshold

This completes the deterministic specification of Distance & Proximity
Measure.

**12. ZONE AUTHORITY DECAY**

Older zones weaken.

Every 50 TF bars without touch:

strength -= 1

Recompute tier.

If strength ≤ 0 → expire.

**13. DENSITY ENGINE (IMPORTANT FOR PHASE)**

Measures crowding.

**Compute within window = 3 × ATR**

density = sum(zone_strengths)

**🔵 What Is the Density Engine Measuring?**

Density Engine measures:

How many structural SR levels exist within a volatility-adjusted price
range?

It is used for:

-   Phase detection (trend vs distribution vs compression)

-   Identifying congestion

-   Measuring structural crowding

So density is **not about execution noise**.\
It is about **structural geography**.

**🔵 Therefore: Which ATR Should Be Used?**

Use:

ATR of the SR timeframe

In your architecture:

-   15m → leg detection

-   1H → SR construction

-   Density → structural phase logic

So the correct ATR for Density Engine is:

ATR(1H)

**🔵 Why NOT ATR(15m)?**

If you use ATR(15m):

-   Density becomes too sensitive

-   Small intraday volatility changes distort structural phase

-   Phase flips too frequently

-   Congestion misclassified

Density is structural → must use structural volatility.

**🔵 Why NOT Higher TF (e.g., 4H)?**

Unless your phase is defined on 4H.

You must match:

Density TF = SR TF

Because density measures clustering of SR levels created on that
timeframe.

**🔵 Deterministic Rule**

Let:

ATR_density = ATR(SR_timeframe)

density_window = k × ATR_density

Then count:

number_of_SR_levels within density_window

**🔵 Example**

Assume:

ATR(1H) = 400

k = 2

density_window = 800

Current price = 120000

You count all SR levels between:

119200 and 120800

If:

-   1--2 levels → low density (trend phase likely)

-   3--4 levels → moderate density

-   5+ levels → high density (distribution/compression)

**🔵 Why This Is Correct Architecturally**

Because:

-   Leg engine = 15m volatility physics

-   SR engine = 1H structural geography

-   Density engine = 1H structural crowding

Everything remains consistent per layer.

**🔵 Final Deterministic Answer**

For Density Engine:

Use ATR of the SR timeframe (1H in your system).

Never use:

-   Trigger TF ATR

-   Leg TF ATR

-   Mixed ATR

**Bias**

If majority above → supply heavy.\
If below → demand heavy.

Used later in phase scoring.

**14. SPACE TO NEXT LEVEL**

Critical for R:R.

space = distance to nearest stronger zone in trade direction

If insufficient → setup veto.

**15. ZONE OBJECT STORAGE**

Each must maintain:

creation_time

last_touch_time

touch_count

false_break_count

lifecycle

tier

strength

origin_sources

**16. ILLEGAL BEHAVIOR**

❌ manual drawing\
❌ invisible merges\
❌ ignoring overlap law\
❌ using zones beyond expiry\
❌ trading frozen zone

**OUTPUT OF THIS LAYER**

Now system knows:

✔ where reactions likely\
✔ how important\
✔ maturity\
✔ polarity\
✔ density bias\
✔ room to move

Environment can now be judged.
