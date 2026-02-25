**📘 DOCUMENT 1**

**SYSTEM CONSTITUTION & GLOBAL OPERATING LAW**

**Deterministic Multi-Timeframe Algorithmic Trading System**

**1. PURPOSE OF THIS DOCUMENT**

This constitution defines the **non-negotiable laws** governing:

-   information usage

-   authority hierarchy

-   timing of decisions

-   numerical behavior

-   conflict handling

-   system safety

-   reproducibility

Any module, rule, setup, or engineer **must obey this document**.

If later specifications contradict it → this document prevails.

**2. PRIMARY OBJECTIVE OF THE SYSTEM**

Transform OHLCV data into:

structure → environment → opportunity → execution

with one overriding requirement:

**Given identical candles, the system must always produce identical
trades.**

**3. ABSOLUTE INVARIANTS**

These are always true. No exceptions.

**3.1 Determinism Law**

Same historical bars → identical:

-   indicator values

-   legs

-   SR zones

-   phase scores

-   setup states

-   orders

No randomness allowed anywhere in decision making.

**3.2 Closed Information Law**

Only information from **closed candles** may be used.

If candle closes at time **T**, any decision made must use data **≤ T**.

Future or partial data is illegal.

**3.3 Candle Finality Law (CRITICAL)**

A timeframe may update its structure **only after its candle closes**.

During formation → frozen.

**Example**

For a 1H candle closing at 14:00:

Between 13:00 and 13:59 →\
❌ cannot update legs\
❌ cannot change phase\
❌ cannot create SR\
❌ cannot validate setups

At 14:00 → allowed.

**3.4 Historical Immutability Law**

After a candle closes:

its OHLCV and all derived outputs are permanent.

Exception: retro classification.

Retro outputs must be tagged:

origin = RETRO

Retro may NEVER modify already executed trades.

**3.5 No Silent Assumption Law**

If any module lacks sufficient clarity to compute:

raise exception

pause trading

log ambiguity

System must never guess.

**3.6 Worst Case Realism Law**

Whenever multiple price outcomes are possible:

choose the worst realistic one.

This applies to entries, exits, and slippage.

**4. NUMERICAL STANDARDIZATION POLICY**

To prevent divergence across machines.

**4.1 Tick Enforcement**

All prices must be rounded to instrument tick **immediately** after
calculation.

**4.2 Ratio Precision**

All ratios → 6 decimal places.

**4.3 Comparison Standard**

Define:

EPSILON = 1e-9

Use:

\>= threshold - EPSILON

abs(a-b) \<= EPSILON

Never raw equality.

**5. SYSTEM CLOCK & MANDATORY EXECUTION ORDER**

The engine runs **only** when a new **15-minute candle closes**.

Each cycle must follow this exact order.

No rearrangement permitted.

1\. Data validation

2\. Timeframe aggregation

3\. Indicator updates

4\. Leg state updates

5\. SR updates

6\. Phase scoring

7\. Setup scanning

8\. Entry monitoring

9\. Risk validation

10\. Portfolio selection

11\. Execution

12\. Persistence

13\. Diagnostics

**6. AUTHORITY HIERARCHY (GLOBAL VETO SYSTEM)**

When conflict occurs, higher level always wins.

Lower level cannot override.

LEVEL 0 → DATA INTEGRITY

LEVEL 1 → RISK INTEGRITY

LEVEL 2 → PHASE PERMISSION

LEVEL 3 → STRUCTURAL LOCATION

LEVEL 4 → SETUP VALIDITY

LEVEL 5 → ENTRY TRIGGER

Failure at any level → trade rejected.

**6.1 Level 0 --- Data Integrity**

If any occurs:

high \< low

close outside range

volume negative

critical gap

→ abort cycle.

**6.2 Level 1 --- Risk Integrity**

Reject if:

R:R below minimum

stop exceeds ATR limit

portfolio risk exceeded

system paused

**6.3 Level 2 --- Phase Permission**

Each setup lists valid phases.

If mismatch → reject.

**6.4 Level 3 --- Structural Location**

Must occur at approved SR tier.

Else reject.

**6.5 Level 4 --- Setup Validity**

All preconditions required.

**6.6 Level 5 --- Entry Trigger**

Timing confirmation.

**7. DOMINANCE & TIE-BREAKING LAW**

When multiple candidates survive.

Always apply in this exact order:

1\. Higher timeframe origin

2\. Higher zone strength

3\. Smaller stop distance

4\. Larger projected R

5\. Earlier creation time

6\. Lexicographic ID

No randomness permitted.

**8. PHASE DETERMINATION LAW**

We use numeric scores.

**8.1 Score Range**

Each ∈ \[0, 100\].

**8.2 Winner Requirement**

Let best & second be top two.

If:

best ≥ second + 10

→ winner accepted.

Else → TRANSITION.

**9. PROXIMITY FILTER LAW**

Only evaluate zones within:

distance ≤ 2.0 × ATR(trigger TF)

Everything else ignored.

**10. SETUP EXPIRATION LAW**

A setup dies immediately if any:

expiry time exceeded

zone invalid

phase mismatch

opposite leg confirmed

**11. RESTART CERTIFICATION LAW**

On boot:

rebuild entire system

hash state

compare with previous

If mismatch → halt trading.

**12. EVENT LOGGING LAW**

Every major decision must log:

timestamp

module

decision

inputs

**13. INFRASTRUCTURE FAILURE LAW**

If critical component unavailable:

no trade \> bad trade

Pause.

**14. CONFIGURATION FREEZE LAW**

Parameters may not change intraday.

Change requires restart & version increment.

**15. MODEL DRIFT LAW**

If live performance deviates from historical expectancy beyond threshold
→ reduce size or pause.

**16. PROHIBITED ACTIONS**

Never allowed:

❌ intrabar structural updates\
❌ future data\
❌ random selection\
❌ manual override of risk\
❌ optimistic fills\
❌ invisible assumptions

**17. WHAT THIS CONSTITUTION GUARANTEES**

If obeyed, system becomes:

✔ deterministic\
✔ replayable\
✔ auditable\
✔ machine independent\
✔ restart safe\
✔ institutionally defensible
