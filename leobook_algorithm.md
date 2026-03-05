# LeoBook Algorithm & Codebase Reference

> **Version**: 7.2 · **Last Updated**: 2026-03-06 · **Architecture**: Autonomous High-Velocity Architecture (Task Scheduler + Data Readiness Gates + Neural RL)

This document maps the **execution flow** of [Leo.py](Leo.py) to specific files and functions.

---

## Autonomous Orchestration (v7.0)

Leo.py is an **autonomous orchestrator** powered by a **dynamic Task Scheduler** (`Core/System/scheduler.py`). It no longer relies on a static 6h loop; instead, it wakes up at target task times or operates at default intervals.

```
Leo.py (Orchestrator) v7.0
├── Startup (Bootstrap):
│   └── Push-Only Sync → Supabase (auto-bootstrap if local DB empty)
├── Task Scheduler:
│   └── Pending Task Execution (Weekly Enrichment, Day-before Predictions)
├── Prologue (Data Readiness Gates):
│   ├── P1: Threshold Check (Leagues/Teams)
│   ├── P2: History Check (2+ Seasons)
│   └── P3: AI Readiness (RL Adapters)
├── Chapter 1 (Prediction Pipeline):
│   ├── P1: Odds Harvesting & URL Resolution
│   ├── P2: Prediction (Pure DB — Rule Engine + RL Ensemble, no browser)
│   │   └── Smart Scheduling (Max 1/team/week)
│   └── P3: Recommendations & Final Chapter Sync
├── Chapter 2 (Betting Automation):
│   ├── P1: Automated Booking (Football.com)
│   └── P2: Funds & Withdrawal Check
└── Live Streamer: Isolated parallel task (60s updates + outcome review + accuracy reports)
```

---

## Data Readiness Gates & Auto-Remediation

**Objective**: Ensure 100% data integrity before prediction resources are expended.

Leo.py implements three sequential high-level gates handled by `DataReadinessChecker` ([data_readiness.py](Core/System/data_readiness.py)):

1. **Gate P1 (Quantity)**: Checks if the local database has sufficient coverage. 
   - **Thresholds**: 90% of `leagues.json` entries must exist in the DB, and each league must have at least 5 teams. 
   - **Remediation**: Triggers `enrich_leagues.py` (Full Mode).
2. **Gate P2 (History)**: Checks for historical fixture coverage.
   - **Threshold**: Minimum of 2 completed seasons for active leagues.
   - **Remediation**: Triggers `enrich_leagues.py --seasons 2`.
3. **Gate P3 (AI)**: Checks if the Reinforcement Learning adapters are trained for the active schedule.
   - **Remediation**: Triggers `trainer.py` via `python Leo.py --train-rl`.

---

## Autonomous Task Scheduler

**Objective**: Event-driven execution of business-critical maintenance and time-sensitive predictions.

Handled by `TaskScheduler` ([scheduler.py](Core/System/scheduler.py)), supporting:

1. **Weekly Enrichment**: Scheduled every Monday at 2:26 AM. Triggers `enrich_leagues.py --weekly` (lightweight mode, `MAX_SHOW_MORE=2`).
2. **Day-Before Predictions**: When a team has multiple matches in a week, only the first is processed immediately. Subsequent matches are added to the scheduler as `day_before_predict` tasks to ensure the RL engine has the absolute latest H2H/Outcome data before predicting the next game.
3. **Dynamic Sleep**: Leo.py calculates the `next_wake_time` after every cycle. If the next task is 2 hours away, it sleeps for 2 hours. If no tasks are pending, it defaults to the `LEO_CYCLE_WAIT_HOURS` config.

---

## Smart Prediction Scheduling

**The 1-Match Rule**: To prevent stale data leakage in RL inference, a team can only have **one prediction per 7-day window** in the active pipeline.
- If Team A plays on Monday and Thursday:
  - Monday match: Predicted during Monday's cycle.
  - Thursday match: Scheduled in the database. On Wednesday (24h before), the Scheduler wakes Leo up to predict the Thursday match using the Monday match's result for fresh form encoding.

---

## Computed Standings (Postgres VIEW)

**Objective**: Eliminate sync latency and redundant storage by calculating standings dynamically.

1. **Supabase**: A Postgres VIEW `computed_standings` performs a complex `UNION ALL` + `RANK()` over the `fixtures` table.
2. **SQLite**: The `computed_standings()` helper in [league_db.py](Data/Access/league_db.py) performs a mirrored SQL query locally.
The standalone `standings` table has been deprecated and removed.

---

## Neural RL Engine (`Core/Intelligence/rl/`)

**Architecture**: SharedTrunk + LoRA league adapters + league-conditioned team adapters.
- **Primary Reward**: Prediction accuracy.
- **Constraint**: Same team produces different predictions in different competitions.

---

*Last updated: March 6, 2026 (v7.2 — Push-Only Sync + DB-Driven Predictions)*
*LeoBook Engineering Team*
