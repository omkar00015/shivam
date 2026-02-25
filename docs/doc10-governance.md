**GOVERNANCE, DRIFT DETECTION & SAFETY CONTROLS**

**Self-Monitoring & Survival Framework**

**1. PURPOSE OF THIS DOCUMENT**

Define:

✔ how the system measures its own health\
✔ when behavior deviates from expectation\
✔ when to reduce risk\
✔ when to stop\
✔ how anomalies are handled\
✔ how humans are informed

Goal:

Fail small, never catastrophically.

**2. GOVERNANCE PHILOSOPHY**

We assume:

models degrade

markets change

unexpected things happen

Therefore:

The system must continuously question itself.

**3. PERFORMANCE BASELINE**

Before live deployment, compute historical benchmarks.

Examples per setup type:

winrate

average R

max drawdown

time in trade

slippage

Store as reference.

**4. ROLLING PERFORMANCE MONITOR**

Continuously compute same metrics on last **N trades**.

Default:

N = 50

**5. DRIFT DETECTION RULES**

**5.1 Expectancy Drift**

If:

live_expectancy \< historical_expectancy × 0.70

→ WARNING.

If:

\< 0.50

→ CRITICAL.

**5.2 Winrate Collapse**

If drop \> 20 percentage points.

**5.3 Drawdown Spike**

If rolling DD \> historical worst × 1.25.

**5.4 Slippage Expansion**

If actual slippage \> expected × 2.

**6. GOVERNANCE STATES**

NORMAL

CAUTION

DEFENSIVE

HALTED

**6.1 NORMAL**

Full operation.

**6.2 CAUTION**

Reduce size 25%.

**6.3 DEFENSIVE**

Reduce size 50%.\
Allow only A/S tiers.

**6.4 HALTED**

No new trades.\
Manage open only.

**7. STATE TRANSITIONS**

**NORMAL → CAUTION**

Any single WARNING.

**CAUTION → DEFENSIVE**

Repeated warning within 20 trades.

**DEFENSIVE → HALTED**

CRITICAL trigger.

**Recovery**

Requires metrics normalize for ≥ 30 trades.

**8. ANOMALY DETECTION**

Non-performance issues.

Trigger halt if:

indicator values NaN

ATR zero

zones disappear unexpectedly

unexpected rapid phase flips

**9. BEHAVIORAL CONSISTENCY CHECK**

Compare live trade frequency to historical.

If deviation \> 50% → investigate.

**10. MARKET STRESS DETECTOR**

If:

current ATR \> 3 × 30-day median

Move to DEFENSIVE automatically.

**11. LATENCY MONITOR**

If execution delay exceeds threshold repeatedly → CAUTION.

**12. CONSECUTIVE LOSS GUARD**

If:

5 losses in a row

→ CAUTION.

If:

8

→ DEFENSIVE.

**13. DAILY LOSS LIMIT**

If daily realized ≤ -3R:

No more entries today.

**14. MONTHLY KILL SWITCH**

If monthly DD exceeds preset → HALT.

**15. GOVERNANCE OUTPUT**

System must expose:

current state

active warnings

risk multiplier

**16. HUMAN NOTIFICATION**

On CAUTION or worse → alert.

**17. WHAT GOVERNANCE CANNOT DO**

It may:

✔ reduce\
✔ pause

It may NOT:

❌ force trades\
❌ override risk\
❌ change entries

**18. PARAMETER FREEZE**

Governance thresholds fixed during trading.

Change requires version bump.

**19. LOGGING REQUIREMENTS**

All state changes must record:

reason

metric values

previous state

**OUTPUT OF THIS DOCUMENT**

Your system is now:

✔ self-aware\
✔ capable of self-defense\
✔ protected against decay\
✔ capital-preserving
