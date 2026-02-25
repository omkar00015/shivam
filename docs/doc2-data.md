**DATA, TIMEFRAME AGGREGATION & INDICATOR FABRIC**

**Deterministic Mathematical Infrastructure**

**1. PURPOSE OF THIS DOCUMENT**

Define:

✔ how raw data enters\
✔ how higher timeframes are created\
✔ when bars become valid\
✔ how ATR is computed\
✔ how indicators are updated\
✔ how caching behaves\
✔ how incomplete information is treated

This document ensures:

every machine builds the SAME market.

**2. SOURCE OF TRUTH**

The only external input is:

15-minute OHLCV bars

All higher timeframes MUST be derived from these.

Native exchange HTF data is illegal.

**3. UNIVERSAL BAR OBJECT**

Every timeframe uses the same structure.

Bar:

timestamp_start

timestamp_end

timeframe

open

high

low

close

volume

is_complete

**Immutability**

Once is_complete = True → bar never changes.

**4. TIMEFRAME AGGREGATION LAW**

**4.1 Conversion Table**

  -----------------------------------------------------------------------
  **Target TF**             **15m bars required**
  ------------------------- ---------------------------------------------
  1H                        4

  4H                        16

  1D                        96

  1W                        672

  1M                        calendar

  3M                        calendar
  -----------------------------------------------------------------------

**4.2 OHLC Rules (MANDATORY)**

For any aggregation window:

open = first bar open

high = max(highs)

low = min(lows)

close = last bar close

volume = sum

timestamp_start = first bar start

timestamp_end = last bar end

No alternatives.

**4.3 Alignment Law**

Bars must align to fixed universal boundaries.

**Examples**

**1H**

00:00, 01:00, 02:00 \...

**4H**

00, 04, 08, 12, 16, 20

**Daily**

00:00 UTC

If first 15m bar of dataset starts mid-cycle →\
aggregation begins only at next valid boundary.

**4.4 Completion Law**

HTF bar becomes usable ONLY when its last 15m child closes.

Before that:

is_complete = False

Such bars are invisible to structure engines.

**5. MISSING DATA POLICY**

Let expected count = N.

**If missing \> 25%**

Discard entire HTF bar.

**If 10--25%**

Mark:

is_reliable = False

Cannot generate setups.

**If \< 10%**

Fill using previous close.

**6. INCOMPLETE PERIOD POLICY**

Incomplete bars may exist **only** for display.

They cannot influence:

-   ZLBB

-   legs

-   SR

-   phase

-   setups

Modules must filter:

if not bar.is_complete → ignore

**7. ATR SPECIFICATION (CRITICAL)**

Everything uses ATR.

So we define it precisely.

**7.1 True Range**

For bar i:

TR = max(

high - low,

abs(high - previous_close),

abs(low - previous_close)

)

**7.2 Initialization**

First ATR value = simple mean of first **14 TRs**.

**7.3 Recursion (Wilder)**

ATR\[i\] = (ATR\[i-1\] × 13 + TR\[i\]) / 14

**7.4 Timeframe Rule**

ATR must be computed **on the aggregated bars of that timeframe**.

Never scale 15m ATR.

**7.5 Precision**

Store ATR with 6 decimals.

**8. INDICATOR UPDATE LAW**

Indicators update ONLY when their timeframe bar closes.

**Example**

If new 15m bar closes but 1H did not:

update ZLBB_15m

DO NOT update ZLBB_1H

**9. ZLBB INPUT CONTRACT**

ZLBB receives:

list of completed bars only

Incomplete filtered.

**10. CACHING LAW (PERFORMANCE WITHOUT DRIFT)**

**10.1 Recalculation Trigger**

Recompute indicator ONLY when TF closes.

**10.2 Otherwise**

Return last computed value.

**10.3 Cache Lifetime**

  -----------------------------------------------------------------------
  **TF**                                **TTL**
  ------------------------------------- ---------------------------------
  15m                                   15m

  1H                                    1h

  4H                                    4h

  Daily                                 24h
  -----------------------------------------------------------------------

**10.4 Cache Integrity**

Cached values must contain:

last_bar_timestamp

If mismatch → recompute.

**11. BANDWIDTH PERCENTILE RULE**

Heavy computation → compute sparsely.

Recalculate percentile only when:

4H bar closes

Use rolling last **100 completed TF bars**.

**12. DATA VALIDATION ENGINE**

Every new bar must pass.

Reject and halt if:

high \< low

close outside range

negative volume

NaN / Inf

Warn (not halt):

range \> 5 × recent ATR

(spike flag)

**13. NUMERICAL CONSISTENCY RULE**

All calculations must be performed in same order across runs.

No parallel mutation of shared state.

**14. STARTUP REBUILD REQUIREMENT**

On restart:

load 15m history

reaggregate

recompute ATR

recompute indicators

Never trust cached higher TF from disk.

**15. OUTPUT OF THIS LAYER**

After this layer, system must have:

✔ consistent TF bars\
✔ validated data\
✔ ATR\
✔ indicator states

Only then structure begins.

**WHAT THIS DOCUMENT PREVENTS**

✔ different candles on different machines\
✔ misaligned sessions\
✔ ATR disagreements\
✔ partial candle leakage\
✔ indicator drift\
✔ restart inconsistency
