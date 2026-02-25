# Claude Code Session Guide

## What this project is
Prasad Deterministic Multi-Timeframe Algo Trading System for BTC/USDT and Gold.
Spec docs are in /docs/ — read them before writing any code.

## Rules you must follow
1. Every function must be deterministic — same input = same output, always.
2. Use Python Decimal for all price/ATR arithmetic, never float.
3. Do NOT build anything not in the spec docs.
4. Do NOT touch files outside the current session's scope.

## Amendment hierarchy (read in this order)
1. All Doc_1 through Doc_12 in /docs/
2. System_Amendment_v1_1.md (base patches C1-C7, M1-M7, D1, E1, E3-E5)
3. System_Amendment_v1_2.md (patches P1-P14 on top of v1.1)

## Dashboard file
dashboard.jsx is a VISUAL REFERENCE ONLY. It shows what the system should
produce eventually. Do not wire it up until Phase 3+.

## Session 1 scope — ONLY these files
src/data_ingest/binance_ws.py
src/data_ingest/gold_api.py  
src/data_ingest/bar_aggregator.py

Do not touch anything else.
```

---

## Step 2 — Open Claude Code and give it Session 1 prompt

Once Claude Code is open and you've given it the folder, paste this as your **first message**:
```
Read CLAUDE_CODE_GUIDE.md first, then read docs/Doc_2_Data_Aggregation_ATR_Indicator_Fabric.md 
and docs/System_Amendment_v1_1.md sections on gap handling and missing data policy.

Then implement src/data_ingest/binance_ws.py with these requirements:
- Connect to Binance WebSocket for BTC/USDT 1m kline stream
- On each closed bar (kline.x = true), emit a completed Bar object
- Bar object fields: symbol, timestamp, open, high, low, close, volume, is_complete=True
- Auto-reconnect on disconnect with exponential backoff (1s, 2s, 4s, max 60s)
- Log reconnect attempts
- Use Decimal for all price fields
- No bar aggregation here — just raw 1m bars out

Acceptance criteria:
1. Connects and prints bar dicts to stdout
2. If connection drops, reconnects automatically
3. All prices are Decimal, not float

Do not implement gold_api.py or bar_aggregator.py yet.