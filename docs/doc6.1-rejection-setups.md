**SR Rejection Framework:**

(Full Zone Reclaim Model)

🔹 Problem Statement

We want to detect true rejection from an SR zone.

We do NOT want:

-   Weak bounces

-   Slow drifts

-   Partial reclaim

-   Noise wicks

We want:

✔ Liquidity sweep\
✔ Breakout failure\
✔ Strong reclaim of structure

Only then we consider reversal.

🔹 Definitions

Let:

zone_low

zone_high

Zone width:

zone_width = zone_high - zone_low

Time window:

max_window = 4 candles (15m TF)

🟢 Bear Fakeout (Support Rejection)

This detects bullish reversal from support.

1.  Penetration (Liquidity Sweep)

A candle must trade below support:

bar.low \<= zone_low

Mark this candle as t0.

Without penetration → no setup.

2\. Failure to Accept

Within next 4 candles (t0 to t0+4):

At least one candle must close back inside zone:

close \>= zone_low

If candles keep closing below zone → real breakdown.

3\. Full Zone Reclaim (Displacement Confirmation)

Within same 4-candle window:

A candle must:

close \> zone_high

This confirms:

-   Entire zone reclaimed

-   Breakdown fully rejected

-   Buyers displaced price beyond resistance edge

✅ Entry Condition

Entry is taken after close \> zone_high.

This ensures:

-   Confirmation

-   No early guessing

-   Structural acceptance above zone

🔴 Bull Fakeout (Resistance Rejection)

Mirror logic.

1️⃣ Penetration

bar.high \>= zone_high

Mark as t0.

2️⃣ Failure to Accept

Within 4 candles:

close \<= zone_high

3️⃣ Full Zone Reclaim Downward

Within 4 candles:

close \< zone_low

📌 Why Full Reclaim Is Powerful

This ensures:

-   Fake breakout trapped traders

-   Opposite side fully regained control

-   Market re-entered prior range

-   Liquidity event confirmed

It eliminates:

❌ Half-hearted reactions\
❌ Noise bounces\
❌ Sideways chop

📐 Structural Behavior This Captures

This setup captures:

Liquidity Sweep → Trap → Structural Shift

It does NOT capture:

Micro pullbacks\
Transitional compression\
Random volatility spikes

Which aligns perfectly with your HTF leg logic.

⚠️ One Important Design Decision

Because you require full reclaim:

Some reversals will be missed.

But:

Signal quality will dramatically increase.

This is consistent with your philosophy:\
Only mark strong expansions.

🔒 Determinism Check

This model is:

✔ Binary\
✔ Time-bound\
✔ No subjective interpretation\
✔ No volatility dependency\
✔ No ambiguous midpoint logic

It is fully algorithmic.

**\
📘 Invalidation Logic**:\
Invalidation must be **objective, binary, and immediate**.

We define invalidation at two levels:

1.  Setup invalidation (before entry)

2.  Trade invalidation (after entry)

No ambiguity.

**1️⃣ Setup Invalidation (Before Entry)**

This applies during the 4-bar rejection window.

**🟢 Bear Fakeout Setup (Support Reversal)**

Setup becomes INVALID if:

**A) Acceptance Below Zone**

If any candle closes below zone_low AND remains below without reclaim
within 4 bars:

close \< zone_low

AND

no close \> zone_high within window

→ Breakdown is accepted\
→ Setup cancelled

**B) Time Expiry**

If within 4 candles after penetration:

no close \> zone_high

→ No full reclaim\
→ Setup invalid

**C) Opposite Break Structure**

If before reclaim:

price closes below recent swing low (leg low)

→ Structure broken\
→ Setup invalid

**🔴 Bull Fakeout Setup (Resistance Reversal)**

Mirror logic.

Invalid if:

close \> zone_high

AND

no close \< zone_low within 4 bars

OR

No reclaim within 4 bars

OR

Break of recent swing high

**2️⃣ Trade Invalidation (After Entry)**

Now assume entry happened after full reclaim.

**🟢 Long Entry (Bear Fakeout)**

Trade invalid if:

**A) Zone Failure**

close \< zone_low

After entry.

This means:

Support failed again\
Rejection was false

→ Immediate exit

**B) Displacement Failure**

If after entry:

Price fails to make at least:

0.5 × zone_width

in favorable direction within N bars (e.g., 6 bars)

→ Exit (no momentum)

Optional but recommended.

**🔴 Short Entry (Bull Fakeout)**

Mirror:

close \> zone_high

→ Immediate exit

**📌 Core Philosophy**

Setup invalidation = failure to confirm\
Trade invalidation = structural failure

Everything based on:

-   Zone boundaries

-   Close price

-   Time window

-   Structure

No ATR\
No discretion\
No subjective interpretation

**✅ This Gives You**

✔ Deterministic exits\
✔ Clear failure detection\
✔ No emotional holding\
✔ Clean FSM transitions
