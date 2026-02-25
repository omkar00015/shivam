**MARKET PHASE & REGIME ENGINE**

**Environment Classification & Trade Permission System**

**1. PURPOSE OF THIS DOCUMENT**

Define:

✔ how trend is recognized\
✔ how balance is recognized\
✔ how distribution / accumulation are detected\
✔ how degradation is measured\
✔ how transitions occur\
✔ how permissions are granted\
✔ how flip-flop is prevented

This engine answers:

**What type of behavior is allowed RIGHT NOW?**

**2. INPUT CONTRACT**

From previous layers we receive:

recent legs

leg metrics

SR map

density

ATR

bandwidth

All computed from CLOSED candles only.

**3. PHASE ARCHITECTURE**

We do not use binary states.

We compute competing probabilities (scores).

**3.1 Phases**

TREND_BULL

TREND_BEAR

BALANCE

DISTRIBUTION

ACCUMULATION

Each has:

score ∈ \[0, 100\]

**4. SCORING WINDOWS**

Use rolling window:

last 5 completed legs on trigger timeframe(trigger TF 1H for 15 min
Entry TF)

If fewer exist → use available.

**5. TREND SCORING ENGINE**

**5.1 Bull Trend Score Components**

  -----------------------------------------------------------------------
  **Condition**                                             **Points**
  --------------------------------------------------------- -------------
  HH + HL structure present                                 +20

  majority bull legs ≥ 60%                                  +15

  average efficiency ≥ 0.5                                  +15

  pullbacks ≤ 50%                                           +10

  price above ZLEMA majority                                +10

  band walk occurred recently                               +10

  resistance breaks succeeded                               +10

  bear rejection failed                                     +10
  -----------------------------------------------------------------------

Max = 100.

**5.2 Bear mirrored.**

**6. BALANCE SCORING**

Market rotating inside boundaries.

Add points if:

  ---------------------------------------------------------------------------
  **Condition**                                                  **Points**
  -------------------------------------------------------------- ------------
  last 3 legs overlap heavily                                    +20

  breakouts fail ≥ 2                                             +20

  average leg displacement \< 1 ATR(1H, location not confirmed)  +15

  alternating direction frequent                                 +15

  price near midpoint majority                                   +15

  both sides defended                                            +15
  ---------------------------------------------------------------------------

**7. DISTRIBUTION (TOP FORMING)**

Bull losing power near resistance.

  -----------------------------------------------------------------------
  **Condition**                                             **Points**
  --------------------------------------------------------- -------------
  bull efficiency declining 2 legs                          +20

  bear bar ratio rising                                     +15

  deeper pullbacks                                          +15

  upper wicks increasing                                    +10

  multiple failures to expand                               +20

  near HTF resistance                                       +20
  -----------------------------------------------------------------------

**8. ACCUMULATION**

Mirror of distribution.

**9. NORMALIZATION**

After computing raw scores:

Clamp to \[0, 100\].

**10. DOMINANCE RULE (FROM CONSTITUTION)**

Let:

best = max

second = next

If:

best ≥ second + 10

→ accept.

Else → TRANSITION.

**11. TRANSITION BEHAVIOR**

In transition:

reduce size by 50%

require stronger setups

Some setups may be banned.

**12. PHASE STICKINESS (ANTI-WHIPSAW)**

Once a phase is selected, it cannot change until:

new winner persists for ≥ 2 TF closes

Prevents rapid flip.

**13. HTF OVERRIDE**

If analysis timeframe strongly opposite (≥ +20 margin):

Lower TF cannot trade counter except at S/A tier.

**14. DENSITY INTEGRATION**

If supply density \> demand by threshold:

increase bear-related scores by +10.

Vice versa.

**15. EXHAUSTION INTEGRATION**

If last leg quality low AND efficiency falling that leg should be up leg
in a bull trend and down leg in a bear leg, if not use the latest bull
and bear legs in those 5 legs, respectively:

penalize trend by -10.

**16. PERMISSION MATRIX**

Each setup must declare allowed phases.

Example:

  -----------------------------------------------------------------------
  **Setup**                       **Allowed**
  ------------------------------- ---------------------------------------
  pullback                        TREND

  Rejection Setup                 BALANCE

  Spring                          ACCUMULATION

  Upthrust                        Distribution
  -----------------------------------------------------------------------

If mismatch → Level 2 veto.

**17. PHASE OUTPUT OBJECT**

phase

confidence_margin

scores\[\]

is_transition

**18. ILLEGAL**

❌ intrabar evaluation\
❌ instant flips\
❌ ignoring HTF\
❌ manual bias

**OUTPUT OF THIS LAYER**

System now knows:

✔ who is in control\
✔ maturity\
✔ degradation\
✔ permission for longs/shorts\
✔ confidence

Now we can hunt opportunities.
