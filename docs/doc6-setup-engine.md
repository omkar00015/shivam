**SETUP ENGINE --- PRASAD MODELS ONLY**

**Deterministic Opportunity Detection Framework**

**1. PURPOSE OF THIS DOCUMENT**

Define:

✔ which setups exist\
✔ what must be true before scanning\
✔ exact trigger mechanics\
✔ how confirmation works\
✔ invalidation\
✔ expiry\
✔ conflict resolution\
✔ ranking

If not defined here → the setup does not exist.

**2. INPUT CONTRACT**

From previous layers:

phase state

phase confidence

SR map

zone tier

zone lifecycle

density bias

ATR

leg status

band mode

All from CLOSED bars.

**3. GLOBAL PRE-FILTER (APPLIES TO ALL SETUPS)**

Before ANY logic runs:

**3.1 Zone Tier Rule**

Only:

S / A / B

C ignored.

**3.2 Zone State Rule**

Reject if:

BROKEN

EXPIRED

FROZEN

**3.3 Distance Rule**

From Constitution:

distance ≤ 2 × ATR(trigger TF)

**3.4 Space Rule**

There must exist at least:

projected_R ≥ minimum_R

Else reject.

If these fail → skip zone.

**4. SETUP TAXONOMY**

Only the following families are legal.

1\. Pullback Continuation

2\. Breakout Retest

3\. Rejection and Reclaim of the Zone

5\. Failed Weakness / Failed Strength

Nothing else.

**5. COMMON SETUP STRUCTURE**

Every setup must go through:

Precondition

→ Trigger

→ Confirmation

→ Candidate Creation

**6. SETUP 1 --- PULLBACK CONTINUATION**

Trend following.

**6.1 Allowed Phases**

TREND_BULL or TREND_BEAR

**6.2 Precondition (Bull Example)**

trend_bull active

zone is support

last impulse bullish

pullback underway

**6.3 Trigger**

When price retraces Price enters zone.

**6.4 Confirmation**

One of:

rejection bar

bull close above prior minor high

micro higher low formed

**Rejection Bar Definition**

lower wick ≥ 50% of range

close in upper 50%

Bullish Engulfing Candle Definition:\
\
Close \>= upper wick of the previous candle

**6.5 Candidate Creation**

Entry reference = confirmation high.\
Stop = zone lower - buffer.

Buffer:

0.10 × ATR(entry TF)

**6.6 Expiry**

If not triggered within:

5 bars

→ cancel.

**7. SETUP 2 --- BREAKOUT RETEST**

**7.1 Allowed Phases**

TREND or BREAKOUT

**7.2 Precondition**

A zone was BROKEN.

**7.3 Trigger**

Price returns to broken boundary.

**7.4 Confirmation**

Must hold and produce decisive close away.

**7.5 Invalidation**

If closes back inside old range.

Immediate cancel.

**8. Fakeouts:** **Rejection and Reclaim of the zone: Refer Document 6.1
Rejection Setups\
\
Spring = Bullish Fakeout (fakeout from Support i.e. Range Bottom)\
Upthrust = Bearish Fakeout (fakeout from Resistence i.e. Range Top)**

**10. SETUP 5 --- FAILED WEAKNESS / STRENGTH**

**Logic**

Signal appears, but instead of continuation, opposite extreme forms.

Example bull:

weakness printed → but new high forms.

Entry = break of new high.

**11. SETUP 6 --- RANGE FADE**

**Allowed Phase**

BALANCE.

**Trigger**

Price reaches boundary.

**Confirmation**

Rejection or failure.

**12. CANDIDATE OBJECT CREATION**

When confirmed:

create SetupCandidate

status = WAITING_ENTRY

expiry_time defined

**13. MULTIPLE CANDIDATES**

Send to resolver.

Priority defined by Constitution.

**14. AUTO-CANCEL CONDITIONS**

Cancel if:

phase flips

zone lifecycle changes

opposite leg confirmed

better candidate chosen

expiry reached

**15. SETUP CONFIDENCE SCORE (FOR RANKING)**

Optional but deterministic.

Example:

  -----------------------------------------------------------------------
  **Factor**                                      **Points**
  ----------------------------------------------- -----------------------
  S tier                                          +3

  A tier                                          +2

  HTF aligned                                     +2

  strong rejection                                +1

  large space                                     +1
  -----------------------------------------------------------------------

**16. ILLEGAL**

❌ inventing new setups\
❌ trading C tier\
❌ ignoring expiry\
❌ bypassing phase\
❌ manual override

**OUTPUT OF THIS LAYER**

We now have:

✔ structured candidates\
✔ known invalidation\
✔ expiry\
✔ ranking potential
