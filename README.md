# Prasad Algo Trading System — Starter Kit

## What Is This?

This is a ready-to-go project folder for building your algorithmic trading system
using **Claude Code** (the coding tool inside Claude Desktop).

Everything is pre-organized:
- Your spec documents converted to markdown (in `/docs/`)
- A `CLAUDE.md` file that gives Claude Code your system's rules and context
- Empty Python files with instructions inside them (Claude Code fills these in)
- A step-by-step session guide so you know exactly what to type

---

## How To Set This Up (5 Minutes)

### Step 1: Install Python (if you don't have it)

Go to https://www.python.org/downloads/ and install Python 3.12 or newer.
During installation, **check the box that says "Add Python to PATH"**.

To verify, open your terminal and type:
```
python --version
```
You should see something like `Python 3.12.x`.

### Step 2: Install Git (if you don't have it)

Go to https://git-scm.com/downloads and install Git.
Just click Next through the installer with default settings.

To verify:
```
git --version
```

### Step 3: Put This Folder Somewhere

Unzip this folder and put it wherever you want. For example:
- Windows: `C:\Users\YourName\projects\prasad-algo`
- Mac: `~/projects/prasad-algo`

### Step 4: Open Terminal In This Folder

- **Windows**: Open the folder in File Explorer, click the address bar,
  type `cmd`, press Enter
- **Mac**: Open Terminal, type `cd ` (with a space), then drag the folder
  into Terminal, press Enter

### Step 5: Initialize Git

Type these commands one by one:
```
git init
git add .
git commit -m "initial project setup"
```

### Step 6: Install Python Dependencies

```
pip install -r requirements.txt
```

### Step 7: Open Claude Desktop

1. Open the Claude Desktop app on your computer
2. Click the **Code** tab (or look for "Claude Code" option)
3. It should detect this folder as a project
4. Claude will automatically read the CLAUDE.md file and know your entire system

---

## How To Use Claude Code (The Simple Version)

Once Claude Code is running in this folder, you just talk to it in English.
It reads your files, writes code, runs tests, and commits to Git.

### Your First Session

Just copy-paste this into Claude Code:

```
Read the CLAUDE.md file, then read docs/session-guide.md.
Start with Session 1: build core/types.py and core/enums.py.
```

That's it. Claude Code will:
1. Read your spec documents
2. Write the Python code
3. Create tests
4. Run the tests
5. Fix any issues
6. Commit the working code

### After Each Session

When you're done for the day:
1. Claude Code auto-saves your files
2. Your Git history preserves everything
3. Next time, just open Claude Code in this folder and say:
   "Read docs/session-guide.md and continue from where we left off"

---

## What's In This Folder

```
prasad-algo/
│
├── README.md                  ← You're reading this
├── CLAUDE.md                  ← Claude Code's "brain" for this project
├── requirements.txt           ← Python packages to install
├── pyproject.toml             ← Python project config
│
├── docs/                      ← Your system specifications
│   ├── session-guide.md       ← Step-by-step coding sessions (IMPORTANT)
│   ├── module-map.md          ← Which doc maps to which code file
│   ├── doc1-constitution.md   ← System constitution & global laws
│   ├── doc2-data.md           ← Data aggregation, ATR, indicators
│   ├── doc3-zlbb.md           ← ZLBB math & leg state engine
│   ├── doc3.1-leg-engine.md   ← Authoritative leg detection
│   ├── doc4-sr-system.md      ← Support/Resistance zones
│   ├── doc5-phase-engine.md   ← Market phase classification
│   ├── doc6-setup-engine.md   ← Trade setup detection
│   ├── doc7-entry-risk.md     ← Entry triggers & risk gate
│   ├── doc8-portfolio.md      ← Portfolio allocation
│   ├── doc9-persistence.md    ← State persistence & restart
│   ├── doc10-governance.md    ← Safety controls & drift detection
│   ├── doc11-conflict.md      ← Conflict resolution & ranking
│   ├── doc12-research.md      ← Validation & evolution framework
│   ├── amendment-v1.1.md      ← Critical fixes & patches
│   └── amendment-v1.2.md      ← Additional fixes from review
│
├── src/                       ← All Python code goes here
│   ├── core/                  ← Shared types, constants, enums
│   ├── data/                  ← Data ingestion & aggregation
│   ├── indicators/            ← ZLBB calculations
│   ├── legs/                  ← Leg detection engine
│   ├── sr/                    ← Support/Resistance system
│   ├── phase/                 ← Market phase engine
│   ├── setups/                ← Trade setup scanners
│   ├── execution/             ← Entry, exits, orders
│   ├── portfolio/             ← Portfolio & conflict resolution
│   ├── governance/            ← Safety & drift detection
│   ├── persistence/           ← State snapshots & restart
│   └── engine/                ← Main pipeline orchestrator
│
├── tests/                     ← All tests
│   ├── unit/                  ← Individual function tests
│   ├── determinism/           ← Same input = same output tests
│   └── edge_cases/            ← Amendment-specific edge cases
│
├── config/                    ← System configuration
│   ├── system_config.yaml     ← Global settings
│   └── instruments/           ← Per-instrument settings
│
├── scripts/                   ← Utility scripts
│
└── .claude/                   ← Claude Code configuration
    └── commands/              ← Custom shortcuts for Claude Code
```

---

## Troubleshooting

**"python not found"**
→ Reinstall Python and make sure "Add to PATH" is checked

**"git not found"**
→ Reinstall Git, restart your terminal

**Claude Code doesn't see the folder**
→ Make sure you opened Claude Code while inside this folder
→ Or use File > Open Folder in Claude Desktop

**Tests fail**
→ That's normal during development. Tell Claude Code: "Run the tests and fix the failures"

---

## Questions?

If you get stuck at any point, just come back to this Claude.ai chat
and ask. I have full context on your system.
