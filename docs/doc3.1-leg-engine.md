**📘 DOCUMENT 3**

**LEG DETERMINATION ENGINE (15m TF)**

**Full Expansion Reversal Model**

**1️⃣ PURPOSE**

This engine detects **full statistical volatility cycles** on 15m
timeframe.

A leg represents:

Expansion from one 1σ extreme of ZLEMA\
to the opposite 1σ extreme\
followed by structural confirmation.

Only full expansion reversals are considered valid legs.

Micro reversals and mean-only crosses are ignored.

Completed 15m legs are used as structural swings for 1H SR construction.

**2️⃣ DATA INPUTS (15m ONLY)**

All calculations use 15m candles.

Per bar:

open

high

low

close

ZLEMA

sigma

Derived levels:

upper_1sigma = ZLEMA + sigma

lower_1sigma = ZLEMA - sigma

No other timeframe data is used in this engine.

**3️⃣ STATE MACHINE**

Possible states:

SEEKING

IN_BULL_LEG

IN_BEAR_LEG

Internal variables:

leg_start_price

leg_high

leg_low

eligible

bar_count

**4️⃣ LEG START CONDITIONS (SYMMETRIC)**

Leg always starts at a statistical extreme.

**4.1 Bull Leg Start**

Condition:

bar.low \<= lower_1sigma

Action:

state = IN_BULL_LEG

leg_start_price = bar.low

leg_low = bar.low

leg_high = bar.high

eligible = False

bar_count = 1

Interpretation:

Price has expanded at least 1 standard deviation below ZLEMA.\
This marks statistical downside extreme and start of potential upward
expansion.

**4.2 Bear Leg Start**

Condition:

bar.high \>= upper_1sigma

Action:

state = IN_BEAR_LEG

leg_start_price = bar.high

leg_high = bar.high

leg_low = bar.low

eligible = False

bar_count = 1

Interpretation:

Price has expanded at least 1 standard deviation above ZLEMA.\
This marks statistical upside extreme and start of potential downward
expansion.

**5️⃣ LEG TRACKING**

**5.1 Bull Leg Tracking**

While state == IN_BULL_LEG:

leg_high = max(leg_high, bar.high)

leg_low = min(leg_low, bar.low)

bar_count += 1

Bull leg becomes eligible for reversal only when:

leg_high \>= upper_1sigma

Then:

eligible = True

This confirms full expansion from −1σ to +1σ.

**5.2 Bear Leg Tracking**

While state == IN_BEAR_LEG:

leg_low = min(leg_low, bar.low)

leg_high = max(leg_high, bar.high)

bar_count += 1

Bear leg becomes eligible for reversal only when:

leg_low \<= lower_1sigma

Then:

eligible = True

This confirms full expansion from +1σ to −1σ.

**6️LEG COMPLETION RULE (FULL CYCLE CONFIRMATION)**

A leg completes only when:

1️⃣ It has already reached the opposite 1σ level (eligible == True)\
AND\
2️⃣ Price re-expands back to the starting-side 1σ\
AND\
3️⃣ Close crosses ZLEMA confirming structural reversal

**6.1 Bull Leg Completion**

Bull leg completes when:

eligible == True

AND

bar.low \<= lower_1sigma

AND

bar.close \< ZLEMA

When confirmed:

confirmed_leg_end = leg_high

Transition:

state = IN_BEAR_LEG

leg_start_price = confirmed_leg_end

leg_high = bar.high

leg_low = bar.low

eligible = False

bar_count = 1

Interpretation:

Price completed full expansion from −1σ → +1σ → back to −1σ with mean
break confirmation.

End of bull leg = highest high achieved before confirmed reversal.

**6.2 Bear Leg Completion**

Bear leg completes when:

eligible == True

AND

bar.high \>= upper_1sigma

AND

bar.close \> ZLEMA

When confirmed:

confirmed_leg_end = leg_low

Transition:

state = IN_BULL_LEG

leg_start_price = confirmed_leg_end

leg_low = bar.low

leg_high = bar.high

eligible = False

bar_count = 1

Interpretation:

Price completed full expansion from +1σ → −1σ → back to +1σ with mean
break confirmation.

End of bear leg = lowest low achieved before confirmed reversal.

**7️⃣ LEG EXTREME DEFINITIONS**

Bull Leg:

start_price = lowest low reached during leg

end_price = highest high reached before completion

Bear Leg:

start_price = highest high reached during leg

end_price = lowest low reached before completion

Leg end becomes the structural swing extreme.

**8️⃣ STRUCTURAL PROPERTIES**

**8.1 Full Volatility Wave Only**

A leg requires:

−1σ → +1σ → −1σ (bull leg)

+1σ → −1σ → +1σ (bear leg)

Partial moves are ignored.

**8.2 No Micro Reversals**

Mean-only crosses without full opposite 1σ expansion do not terminate a
leg.

**8.3 Strong Trend Handling**

If price trends upward and never returns to lower_1sigma:

Bull leg remains open.

No reversal swing is marked.

This is intentional.

**8.4 Multiple 1σ Hits**

Highest high / lowest low is always updated until completion.

There is no tentative leg end.

**9️⃣ OUTPUT OBJECT (TO 1H ENGINE)**

Upon completion:

Leg {

direction

start_price

end_price

displacement = abs(end_price - start_price)

bar_count

}

Only completed legs are emitted.

Incomplete legs are ignored.

**🔟 MODEL CHARACTER**

This is a:

Full statistical volatility cycle detector.

It is:

✔ Symmetric\
✔ Deterministic\
✔ Mean-reversion confirmed\
✔ Noise-resistant\
✔ Suitable for HTF SR mapping

This document is internally consistent with your clarified logic.
