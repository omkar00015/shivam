**PORTFOLIO ALLOCATION & CAPITAL COMPETITION**

**Deterministic Capital Distribution Framework**

**1. PURPOSE OF THIS DOCUMENT**

Define:

✔ maximum exposure\
✔ simultaneous trade handling\
✔ ranking of opportunities\
✔ conflict resolution\
✔ correlation control\
✔ scaling logic\
✔ capital reservation\
✔ throttling during stress

After this, strategy behaves like a **fund**, not a signal generator.

**2. INPUT CONTRACT**

From Document 7:

approved trade requests

expected R

stop distance

zone tier

setup type

phase confidence

From account:

equity

current open risk

max allowed

**3. GLOBAL RISK LIMITS**

**3.1 Per Trade**

Default:

1% equity

**3.2 Total Open Risk**

≤ 6% equity

Hard veto.

**3.3 Per Direction Limit**

No more than:

4% same direction

Prevents pile-up.

**4. CAPITAL COMPETITION SCENARIO**

Occurs when:

sum(new risks) + open risk \> cap

Now we must choose.

**5. RANKING ENGINE**

Each candidate receives score.

**5.1 Formula**

score =

expectancy_weight × expected_R

\+ tier_weight

\+ phase_confidence

\+ freshness_bonus

**5.2 Default Weights**

expectancy_weight = 2

tier_weight:

S = 3

A = 2

B = 1

phase_confidence = dominance_margin / 10

freshness_bonus = 1 if fresh else 0

**6. SELECTION PROCESS**

Sort descending by score.

Take until risk cap reached.

Reject rest.

Rejected candidates → cancelled.

No waiting list.

**7. CORRELATION CONTROL**

If multiple setups rely on **same zone** or same directional thesis.

Allow only highest ranked.

**8. ZONE OWNERSHIP LOCK**

After trade opens at a zone:

No additional trades allowed from that zone until:

lifecycle changes OR trade closes

**9. CAPITAL RESERVATION**

When trade approved:

Reserve its risk immediately.

Prevents race conditions.

**10. DYNAMIC THROTTLING**

If Governance (later doc) signals stress:

Reduce new trade size by:

50%

But ranking still applies.

**11. SCALING PRIORITY**

If two trades equal score:

Apply Constitution tie-breaker.

**12. HEDGING RULE**

If long & short triggered simultaneously in same instrument:

Only one allowed.

Choose higher rank.

**13. PYRAMIDING POLICY (Optional)**

If enabled:

Add only if:

existing trade ≥ +1R

AND new zone independent

Still must pass ranking.

**14. MAX TRADES PER DAY**

Optional stability control.

Example:

≤ 10

After that → ignore new.

**15. CAPITAL RELEASE**

Upon exit:

Release reserved risk.

Immediately available.

**16. AUDIT REQUIREMENTS**

For every rejected trade, log:

rank

reason not selected

risk at time

Helps future optimization.

**17. ILLEGAL**

❌ manual favoritism\
❌ first come first serve\
❌ random selection\
❌ exceeding caps

**OUTPUT OF THIS LAYER**

Now system knows:

✔ who deserves capital\
✔ how risk is distributed\
✔ what gets rejected\
✔ exposure limits\
✔ portfolio discipline
