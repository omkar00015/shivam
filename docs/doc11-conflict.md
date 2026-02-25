**CONFLICT RESOLUTION, EDGE ARBITRATION & DETERMINISTIC RANKING**

**The Supreme Court of Trade Selection**

**1. PURPOSE OF THIS DOCUMENT**

Define:

✔ how simultaneous setups are compared\
✔ who wins\
✔ tie resolution\
✔ long vs short conflicts\
✔ zone conflicts\
✔ portfolio pressure conflicts\
✔ ranking logic\
✔ cancellation order

After this, **no two trades are ever equal**.

**2. INPUT CONTRACT**

We receive:

approved SetupCandidates

with:

direction

expected_R

zone tier

zone strength

timeframe origin

phase confidence

creation timestamp

stop size

All have passed:

✔ constitution\
✔ risk\
✔ phase\
✔ structural validity

Now we must pick.

**3. PRINCIPLE OF RESOLUTION**

We do NOT average.

We apply a strict hierarchy.

At each step losers are removed.

**4. FILTER STAGE 1 --- DIRECTION PERMISSION**

If HTF bias strongly favors one side:

confidence ≥ 20

Remove opposite unless S-tier.

**5. FILTER STAGE 2 --- ZONE AUTHORITY**

Keep highest tier only.

If S present → drop A/B.

If A present → drop B.

**6. FILTER STAGE 3 --- LIFECYCLE PRIORITY**

[Order:]{.mark}

[FRESH \> TESTED \> FLIPPED]{.mark}

Keep best.

**7. FILTER STAGE 4 --- STRUCTURAL CLARITY**

Smaller stop = cleaner.

Keep smallest stop distance.

**8. FILTER STAGE 5 --- EXPECTED RETURN**

Keep higher expected R.

**9. FILTER STAGE 6 --- PHASE CONFIDENCE**

Keep higher dominance margin.

**10. FILTER STAGE 7 --- TIME PRIORITY**

Earlier candidate wins.

**11. FINAL TIE BREAKER**

Lexicographic ID.

Deterministic.

**12. MULTI-DIRECTION CONFLICT**

If both survive through filters:

Choose direction aligned with:

density bias

If equal → HTF trend.

**13. ZONE OVERLAP CONFLICT**

If multiple candidates from overlapping zones:

Only the highest ranked remains.

**14. SAME SETUP DUPLICATES**

Keep earliest.

Cancel others.

**15. PARTIAL CAPITAL SCENARIO**

If remaining risk capacity insufficient:

Re-run ranking among survivors with adjusted size.

Still highest first.

**16. GOVERNANCE OVERRIDE**

If in DEFENSIVE:

Only S tier allowed.

Apply filter before ranking.

**17. CANCELLATION LOGIC**

Every removed candidate must log:

lost_to = winner_id

stage_of_elimination

**18. COMPLEXITY GUARANTEE**

Because order is fixed:

Any two machines → same winner.

**19. ILLEGAL**

❌ random\
❌ averaging\
❌ manual pick\
❌ FIFO\
❌ human preference

**OUTPUT OF THIS DOCUMENT**

Now the system ensures:

✔ one winner\
✔ identical capital placement\
✔ no internal competition\
✔ reproducible portfolio
