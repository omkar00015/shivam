# Prasad — Restart Guide

## Every Time You Start Your Laptop

### Step 1 — Start Docker Desktop

Open Docker Desktop from the taskbar or Start menu.
Wait until the whale icon in the system tray stops animating (~30 seconds).

### Step 2 — Run the startup check

Open a terminal in the project root and run:

```bash
python scripts/startup_check.py
```

The script checks every component in order and prints a summary table:

```
┌───────────────────────────────────────────────┐
│  PRASAD STARTUP CHECK — 2026-03-04 19:00 UTC  │
├───────────────────┬───────────────────────────┤
│  Docker           │  ✓ Running                │
│  TimescaleDB      │  ✓ Healthy                │
│  DB Connection    │  ✓ Connected              │
│  BTCUSDT/15m bars │  175,854 (current)        │
│  Backfill         │  +27 bars added           │
│  Min history      │  ✓ Satisfied (>= 40 bars) │
│  State snapshots  │  ✓ 103 snapshots          │
│  Hash verification│  ✓ All valid              │
│  Telegram         │  ✓ Connected              │
├───────────────────┴───────────────────────────┤
│  ALL CHECKS PASSED — safe to start            │
└───────────────────────────────────────────────┘

✓ Run this command to start the system:
  python scripts/run_paper_trader.py
```

**If any check fails**, the script prints a clear error message and exits with
code 1. Fix the reported issue before proceeding.

### Step 3 — Start the paper trader

```bash
python scripts/run_paper_trader.py
```

This starts:
- The Binance WebSocket live feed (BTC/USDT 1-minute bars)
- The 13-step trading pipeline (fires on every 15-minute candle close)
- The dashboard API server on **http://localhost:8000**

Leave this terminal open. Press `Ctrl+C` to stop cleanly.

### Step 4 — Open the dashboard

Navigate to **http://localhost:8000** in any browser.

The dashboard shows the live candlestick chart, SR zone overlays, leg
formation markers, phase scores, and active setup candidates.

---

## Troubleshooting

### "Docker Desktop is not running"

Docker Desktop must be fully started before running the startup check.
Open it from the Start menu and wait for the whale icon to stop animating.

### "DB container not running"

The startup check auto-runs `docker-compose up -d`. If it still fails:

```bash
docker-compose up -d
# wait 30 seconds, then re-run
python scripts/startup_check.py
```

### "DB is up but not accepting connections"

The container started but PostgreSQL isn't ready yet. Wait 10–15 seconds
and re-run the startup check.

### "Insufficient history"

The 15m bar table has fewer than 40 bars. This happens on a fresh install.
Fetch historical data first:

```bash
python scripts/fetch_historical.py
```

### "Gap detected: X hours — backfill running"

Normal after the laptop was off overnight or over a weekend. The startup
check fetches the missing bars automatically from Binance. No action needed.

### "Telegram not configured or unreachable"

The paper trader works without Telegram. To enable notifications, set
`TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in the `.env` file.

### WinError 10048 (port 8000 in use)

A previous run left a TIME_WAIT socket. The paper trader has a 2-second
sleep built in to handle this. If the error persists:

```bash
# Kill all Python processes (Windows)
taskkill /IM python.exe /F
# Then restart
python scripts/run_paper_trader.py
```

### Dashboard shows empty / no chart data

The paper trader pre-populates the dashboard immediately on startup from the
warmup replay. If the chart is empty, the paper trader is not running —
start it with Step 3 above.

---

## Port reference

| Service          | Port  | URL                    |
|------------------|-------|------------------------|
| Dashboard API    | 8000  | http://localhost:8000  |
| TimescaleDB      | 5432  | (internal, asyncpg)    |

---

## Quick reference — commands

```bash
# Full startup (Steps 2-3 combined)
python scripts/startup_check.py && python scripts/run_paper_trader.py

# Backfill DB manually (if needed)
python scripts/backfill.py

# Run all tests
pytest tests/unit/ -v
pytest tests/determinism/ -v
```
