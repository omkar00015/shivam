**ENTRY, ORDER EXECUTION & RISK GATE**

**Capital Commitment Framework**

**1. PURPOSE OF THIS DOCUMENT**

Define:

✔ when entries trigger\
✔ how stop is finalized\
✔ how targets are derived\
✔ how R:R is computed\
✔ position sizing\
✔ slippage\
✔ gap handling\
✔ order cancellation\
✔ authority of risk

After this layer → trade exists.

**2. INPUT CONTRACT**

From Document 6:

SetupCandidate

direction

entry_reference

stop_reference

target_reference

expiry_time

From Document 2:

ATR

tick size

From portfolio:

current exposure

risk limits

**3. ENTRY TRIGGER LAW**

A trade can trigger **only on the Entry TF close**.

Never intrabar.

**3.1 Break Requirement**

For long:

close ≥ entry_reference + EPSILON

For short mirrored.

**3.2 Touch is NOT entry**

Must CLOSE beyond.

**4. GAP RULE**

If price jumps beyond entry level:

Entry price = worst price of that candle.

Example long:

entry = close

Not reference.

**5. STOP CALCULATION**

**5.1 Base Stop**

From setup.

**5.2 Buffer**

buffer = 0.10 × ATR(entry TF)

**Final**

stop = base ± buffer

Rounded to tick.

**6. TARGET CALCULATION**

Primary method:

next significant opposing zone

If none → measured move.

**6.1 Minimum R:R Law**

Trend:

≥ 2.0

Balance:

≥ 1.5

If smaller → reject.

(Level 1 veto)

**7. POSITION SIZING**

**7.1 Account Risk**

Fixed percentage.

Default:

1%

**7.2 Formula**

size = account_risk / abs(entry - stop)

**7.3 Rounding**

Rounded DOWN to nearest tradable lot.

**8. RISK ENGINE (FINAL AUTHORITY)**

Before order is sent.

Reject if any:

R:R insufficient

stop \> 1.5 ATR(trigger TF)

portfolio risk \> cap

system pause active

If rejected → candidate dead.

**9. ORDER CREATION**

When approved:

Trade object created

status = SENT

**10. FILL MODEL**

We assume pessimistic.

**Marketable**

Fill at close.

**Stop breach**

Exit at first tradable price beyond.

**11. PARTIALS & TIERS**

If system supports scaling:

Example default:

50% at 1R

25% at next zone

25% runner

**12. BREAKEVEN RULE**

When price reaches:

+1R

Stop moves to entry.

**13. TRADE MANAGEMENT AUTHORITY**

Managed by Trigger TF.

Entry TF noise ignored.

**14. EARLY EXIT CONDITIONS**

Immediate close if:

phase flips hard

opposite leg confirmed

structure failure

**15. EXPIRY OF WAITING ENTRIES**

If not triggered before expiry_time → cancel.

**16. DUPLICATE PREVENTION**

Only one trade per zone per direction unless lifecycle changes.

**17. MAX SIMULTANEOUS POSITIONS**

Defined by portfolio doc.

If limit exceeded → allocator decides.

**18. AUDIT LOGGING**

Must record:

expected entry

actual entry

slippage

size

R

reason

**19. ILLEGAL**

❌ intrabar execution\
❌ optimistic fills\
❌ bypassing risk\
❌ resizing after approval\
❌ revenge re-entry

**OUTPUT OF THIS LAYER**

Now we have:

✔ real positions\
✔ real exposure\
✔ deterministic execution
