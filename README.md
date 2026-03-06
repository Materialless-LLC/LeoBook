# LeoBook

**Developer**: Materialless LLC
**Chief Engineer**: Emenike Chinenye James
**Powered by**: Multi-Key Gemini Rotation (25+ Keys, 6 Models) · xAI Grok API (Optional)
**Architecture**: Autonomous High-Velocity Architecture v7.2 (Data Readiness Gates + Task Scheduler + Neural RL)

---

## What Is LeoBook?

LeoBook is an **autonomous sports prediction and betting system** with two halves:

| Component | Tech | Purpose |
|-----------|------|---------|
| `Leo.py` | Python 3.12 + Playwright + PyTorch | Autonomous data extraction, rule-based + neural RL prediction, odds harvesting, automated bet placement, and dynamic task scheduling |
| `leobookapp/` | Flutter/Dart | Cross-platform dashboard with "Telegram-grade" UI density, Liquid Glass aesthetics, and real-time streaming |

**Leo.py** is an **autonomous orchestrator** powered by a **dynamic Task Scheduler** (`Core/System/scheduler.py`). It no longer relies on a static 6h loop; instead, it wakes up at target task times (e.g., Weekly Enrichment at Monday 2:26am) or operates at default intervals. The system enforces **Data Readiness Gates** (Prologue P1-P3) to ensure data integrity before predictions. **Standings** are now computed on-the-fly via a Postgres VIEW in Supabase. Cloud sync uses **watermark-based delta detection** — only rows modified since the last sync are compared, not full table scans.

For the complete file inventory and step-by-step execution trace, see [LeoBook_Technical_Master_Report.md](LeoBook_Technical_Master_Report.md).

---

## System Architecture (v7.0 Autonomous Pipeline)

```
Leo.py (Orchestrator)
├── Startup (Initialization):
│   └── Push-Only Sync → Supabase (auto-bootstrap if local DB empty)
├── Task Scheduler:
│   └── Execute Pending Tasks (Weekly Enrichment, Day-before Predictions)
├── Prologue (Data Readiness Gates):
│   ├── P1: League/Team Threshold Check (90% Coverage)
│   ├── P2: Historical Seasons Check (2+ distinct seasons including current)
│   └── P3: RL Adapter Readiness Check
├── Chapter 1 (Prediction Pipeline):
│   ├── Ch1 P1: URL Resolution & Odds Harvesting (Football.com)
│   ├── Ch1 P2: Predictions (Pure DB — Rule Engine + Neural RL Ensemble, no browser)
│   │   └── Smart Scheduling: Max 1 per team/week (remaining vs Scheduler)
│   └── Ch1 P3: Recommendations & Final Chapter Sync
├── Chapter 2 (Betting Automation):
│   ├── Ch2 P1: Automated Booking (Football.com)
│   └── Ch2 P2: Funds & Withdrawal Check
└── Live Streamer: Isolated parallel task — Live Scores + Outcome Review + Accuracy Report (Waits for Bootstrap Sync)
```

### Key Subsystems

- **Autonomous Task Scheduler**: Manages recurring tasks (Weekly enrichment, Monday 2:26am) and time-sensitive tasks (Day-before match predictions).
- **Data Readiness Gates**: Automated pre-flight checks with **Auto-Remediation** (30-minute timeout) — if leagues, historical seasons, or RL adapters are missing, Leo.py triggers the relevant enrichment/training scripts automatically. If remediation times out, the system proceeds with available data.
- **Standings VIEW**: High-performance standings computed directly from the `schedules` table via Postgres UNION ALL views. Zero storage, always fresh.
- **Data Leak Guard**: Max 1 prediction per team per week. This is NOT a frequency cap — it prevents the model from predicting future matches before prerequisite match results are known. Surplus matches are queued by the Scheduler.
- **Neural RL Engine** (`Core/Intelligence/rl/`): SharedTrunk + LoRA league adapters + league-conditioned team adapters. PPO training with chronological walkthrough and composite rewards.

### Core Modules

- **`Core/Intelligence/`** — AI engine (rule-based prediction, **neural RL engine**, adaptive learning, AIGO self-healing)
- **`Core/System/`** — **Task Scheduler**, **Data Readiness Checker**, lifecycle, withdrawal
- **`Core/Browser/`** — Playwright-based AIGO extractors
- **`Modules/Flashscore/`** — Schedule extraction, live score streaming, match data processing
- **`Modules/FootballCom/`** — Betting platform automation (login, odds, booking, withdrawal)
- **`Data/Access/`** — **Computed Standings**, Supabase sync, outcome review
- **`Scripts/`** — Weekly enrichment, search dictionary builder, recommendation engine
- **`leobookapp/`** — Flutter dashboard (Liquid Glass + Proportional Scaling)

---

## Supported Betting Markets

1X2 · Double Chance · Draw No Bet · BTTS · Over/Under · Goal Ranges · Correct Score · Clean Sheet · Asian Handicap · Combo Bets · Team O/U

---

## Project Structure

```
LeoBook/
├── Leo.py                  # Autonomous Orchestrator
├── RULEBOOK.md             # Developer rules (MANDATORY)
├── Core/
│   ├── System/             # Task Scheduler, Data Readiness, Lifecycle
│   ├── Intelligence/       # RL Engine, AIGO, Learning
│   ├── Browser/            # Playwright extractors
│   └── Utils/              # Constants, now_ng utilities
├── Modules/
│   ├── Flashscore/         # Live streamer, match processing
│   └── FootballCom/        # Betting automation
├── Scripts/
│   ├── enrich_leagues.py   # Weekly enrichment mode
│   ├── recommend_bets.py   # Recommendation engine
│   └── build_search_dict.py # LLM enrichment
├── Data/
│   ├── Access/             # DB Helpers, Sync, Computed Standings
│   ├── Store/              # Local SQLite (leobook.db)
│   └── Supabase/           # Postgres VIEW definitions
└── leobookapp/             # Flutter Frontend
```

---

## LeoBook App (Flutter)

The app implements a **Telegram-inspired high-density aesthetic** optimized for visual clarity and real-time data response.

- **Proportional Scaling System** — Custom system ensures perfect parity across all device sizes.
- **Computed Standings** — The app queries the `computed_standings` VIEW for live-accurate tables. 
- **Liquid Glass UI** — Premium frosted-glass design with micro-radii (14dp).
- **4-Tab Match System** — Real-time 2.5hr status propagation and Supabase streaming.
- **Double Chance Accuracy** — Supports pattern-based OR logic for team outcomes.

---

## Quick Start (v7.0)

### Backend (Leo.py)

```bash
# Setup
pip install -r requirements.txt
pip install -r requirements-rl.txt  # Core RL/AI dependencies
playwright install chromium
bash .devcontainer/setup.sh         # Auto-config system environment

# Execution
python Leo.py              # Autonomous Orchestrator (Full dynamic cycle)
python Leo.py --sync        # Push local changes to Supabase
python Leo.py --pull        # Pull ALL from Supabase → local SQLite (recovery)
python Leo.py --prologue    # Data readiness check (P1-P3)
python Leo.py --chapter 1   # Prediction pipeline (Odds → Predict → Sync)
python Leo.py --chapter 2   # Betting automation
python Leo.py --review      # Outcome review (Finished matches)
python Leo.py --recommend   # Recommendations generation
python Leo.py --streamer    # Standalone Live Multi-Tasker (Scores/Review/Reports)
python Leo.py --enrich-leagues            # Smart gap scan (only leagues with missing data)
python Leo.py --enrich-leagues --limit 5  # Gap scan first 5 leagues
python Leo.py --enrich-leagues --limit 501-1000 # Range-based gap scan
python Leo.py --enrich-leagues --refresh   # Re-process stale leagues (>7 days old)
python Leo.py --enrich-leagues --reset     # Full reset: re-enrich ALL leagues
python Leo.py --enrich-leagues --season 1  # Target ONLY the most recent past season
python Leo.py --enrich-leagues --seasons 2 # Extract last 2 seasons per league
python Leo.py --train-rl               # Chronological RL model training
python Leo.py --rule-engine --backtest # Progressive backtest with default engine
python Leo.py --help                    # Comprehensive CLI command catalog
```

---

## Environment Variables

| Variable | Purpose |
|----------|---------|
| `GEMINI_API_KEY` | Multi-key rotation for AI analysis |
| `SUPABASE_URL` | Supabase endpoint |
| `SUPABASE_SERVICE_KEY` | Backend service key (Admin) |
| `FB_PHONE` / `FB_PASSWORD` | Betting platform credentials |
| `LEO_CYCLE_WAIT_HOURS` | Default sleep between autonomous tasks (default: 6) |

---

## Documentation

| Document | Purpose |
|----------|---------|
| [RULEBOOK.md](RULEBOOK.md) | **MANDATORY** — Engineering standards & philosophy |
| [PROJECT_STAIRWAY.md](PROJECT_STAIRWAY.md) | Capital compounding strategy — the "why" behind LeoBook |
| [LeoBook_Technical_Master_Report.md](LeoBook_Technical_Master_Report.md) | File inventory, execution flow, safety guardrails, observability |
| [leobook_algorithm.md](leobook_algorithm.md) | Algorithm reference (RuleEngine + Neural RL) |
| [AIGO_Learning_Guide.md](AIGO_Learning_Guide.md) | Self-healing extraction pipeline |

---

*Last updated: March 6, 2026 (v7.2 — Push-Only Sync + Safety Guardrails Design + 13-Discrepancy Audit Resolution + PROJECT_STAIRWAY)*
*LeoBook Engineering Team — Materialless LLC*
