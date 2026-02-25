**PERSISTENCE, RESTART & REPLAY INTEGRITY**

**Deterministic Continuity Framework**

**1. PURPOSE OF THIS DOCUMENT**

Define:

✔ what must be saved\
✔ what must be rebuilt\
✔ how restart works\
✔ how drift is detected\
✔ how mismatches are handled\
✔ what constitutes truth

Goal:

A restart must behave as if it never happened.

**2. PHILOSOPHY**

There is only ONE ultimate truth:

historical 15m bars

Everything else is derived.

Therefore after restart, system must be able to reconstruct the world
from bars.

**3. TWO TYPES OF STATE**

**3.1 Rebuildable State**

Must be recalculated after restart.

Examples:

-   ZLBB

-   legs

-   SR zones

-   phase

-   setups

**3.2 External State**

Cannot be derived from candles.

Examples:

-   account equity

-   open positions

-   fills

-   order IDs

Must be loaded from broker or DB.

**4. WHAT MUST BE PERSISTED**

At minimum snapshot at each cycle:

last processed 15m timestamp

open trades

reserved risk

zone ownership locks

governance mode

Everything else can be rebuilt.

**5. RESTART PROCEDURE (MANDATORY)**

Upon boot.

**Step 1 --- Load history**

Obtain full 15m dataset from last safe anchor (e.g., 1 year).

**Step 2 --- Rebuild hierarchy**

aggregate TFs

compute ATR

compute ZLBB

build legs

build zones

compute phase

scan setups

**Step 3 --- Load external state**

open trades

portfolio exposure

locks

**Step 4 --- Synchronization check**

Ensure:

live price consistent with last processed bar

**6. HASH CERTIFICATION**

After rebuild, create hash from:

last N legs

active zones

current phase

active setups

Compare with last saved hash.

If mismatch:

HALT TRADING

raise alert

No tolerance.

**7. INTRADAY CRASH SCENARIO**

If crash occurs mid-bar:

Since structure depends on closed candles →\
no structural loss.

Resume on next close.

**8. PARTIAL EXECUTION RECOVERY**

After restart:

Ask broker for:

open positions

average entry

remaining size

Trust broker.

**9. DUPLICATE ORDER PREVENTION**

On restart, system must reconcile:

pending vs filled

before sending anything new.

**10. EVENT LEDGER (HIGHLY IMPORTANT)**

Every structural event should be appended.

Examples:

LEG_COMPLETED

ZONE_CREATED

ZONE_TESTED

ZONE_BROKEN

SETUP_CREATED

TRADE_OPENED

TRADE_CLOSED

Current world = replay(events).

**11. BACKTEST VS LIVE PARITY**

Backtest must use same rebuild path.

If code differs → invalid.

**12. TIME SYNCHRONIZATION**

All timestamps UTC.

Never local.

**13. SNAPSHOT FREQUENCY**

Save snapshot every:

15m cycle

Never less.

**14. DATA REVISION PROTECTION**

If historical vendor revises old candles:

System must rebuild from revision point.

**15. GOVERNANCE ON MISMATCH**

If hash fails:

no new trades

existing trades managed only

human notification

**16. PERFORMANCE DURING REBUILD**

Rebuild must be optimized but **must not approximate**.

Accuracy \> speed.

**17. ILLEGAL**

❌ trusting cached HTF\
❌ partial rebuild\
❌ ignoring mismatch\
❌ silent corrections

**OUTPUT OF THIS DOCUMENT**

Now your system is:

✔ restart proof\
✔ crash tolerant\
✔ reproducible\
✔ auditable\
✔ legally defensible
