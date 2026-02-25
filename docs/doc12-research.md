**RESEARCH, VALIDATION & EVOLUTION FRAMEWORK**

**Controlled Improvement Architecture**

**1. PURPOSE OF THIS DOCUMENT**

Define:

✔ how new ideas are tested\
✔ how parameters may change\
✔ how performance claims are verified\
✔ how upgrades are approved\
✔ how live vs research remain comparable\
✔ how curve fitting is prevented

Goal:

Improve safely.\
Never accidentally break the machine.

**2. GOLDEN RULE**

Live system and research system must run **identical logic**.

If separate code paths exist → research invalid.

**3. VERSIONING LAW**

Every parameter set must have:

strategy_version

indicator_version

rules_version

All trades record these.

**4. WHAT CAN BE MODIFIED**

Allowed:

✔ thresholds\
✔ weights\
✔ expiry windows\
✔ ranking constants

Not allowed without redesign:

❌ structural definitions\
❌ authority ladder\
❌ candle finality\
❌ deterministic rules

**5. CHANGE PIPELINE**

Every modification must pass:

Hypothesis → Backtest → Stress → Shadow → Limited Capital → Full Deploy

No skipping.

**6. HYPOTHESIS STAGE**

Research must define:

what is changed

why

expected impact

risk of failure

If reason unclear → reject.

**7. BACKTEST VALIDATION**

Minimum requirements:

multiple years

multiple regimes

transaction costs

slippage model

Must compare:

new vs current

**8. OUT OF SAMPLE TEST**

At least 25% of data unseen during design.

Performance must hold.

**9. PARAMETER STABILITY TEST**

Vary parameters ±10%.

If system collapses → fragile → reject.

**10. MONTE CARLO TEST**

Shuffle trades.

Evaluate drawdown distribution.

If unacceptable → reject.

**11. EDGE CONCENTRATION CHECK**

If majority of profit comes from tiny subset → unstable.

**12. SHADOW TRADING PHASE**

Run live without capital.

Compare:

expected fills

realistic fills

frequency

behavior

**13. LIMITED CAPITAL DEPLOYMENT**

Start small.

Increase only after statistical confirmation.

**14. LIVE ACCEPTANCE CRITERIA**

Upgrade approved only if:

live metrics align with research within tolerance

**15. ROLLBACK MECHANISM**

If new version underperforms:

Immediate revert to prior stable version.

**16. EVOLUTION SPEED LIMIT**

No frequent switching.

Minimum runtime between changes:

100 trades or 3 months

**17. DOCUMENTATION REQUIREMENT**

Every change must update:

-   rule ID

-   affected modules

-   expected effect

**18. AVOIDING CURVE FITTING**

Disallow micro-optimization to improve recent performance.

Research must show structural rationale.

**19. LONG TERM HEALTH METRIC**

Track decay of:

edge per setup

by regime

by volatility

If degrading → research priority.

**20. HUMAN GOVERNANCE ROLE**

Humans may:

✔ approve\
✔ reject\
✔ pause

They may NOT manually trigger trades.

**21. ARCHIVAL**

All historical versions must remain reproducible.

**OUTPUT OF THIS DOCUMENT**

Now your system is capable of:

✔ safe growth\
✔ controlled evolution\
✔ institutional credibility\
✔ audit defense\
✔ longevity
