# LeoBook Algorithm & Codebase Reference

> **Version**: 7.2 · **Last Updated**: 2026-03-06 · **Architecture**: Autonomous High-Velocity Architecture (Task Scheduler + Data Readiness Gates + Neural RL)

This document maps the **execution flow** of [Leo.py](Leo.py) to specific files and functions.

---

## Autonomous Orchestration (v7.0)

Leo.py is an **autonomous orchestrator** powered by a **dynamic Task Scheduler** (`Core/System/scheduler.py`). It no longer relies on a static 6h loop; instead, it wakes up at target task times or operates at default intervals.

```
Leo.py (Orchestrator) v7.2
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
│   │   └── Data Leak Guard (Max 1/team/week — prevents stale-data predictions)
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
   - **Remediation**: Triggers `enrich_leagues.py` (Full Mode) with 30-minute timeout.
2. **Gate P2 (History)**: Checks for historical fixture coverage.
   - **Threshold**: ≥ 80% of leagues with fixtures have 2+ distinct seasons.
   - **Remediation**: Triggers `enrich_leagues.py --seasons 2` with 30-minute timeout. If enrichment exceeds the budget, proceeds with available data.
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

## Data Leak Guard (Max 1 Prediction/Team/Week)

**Purpose**: Prevent data leakage in RL inference. This is NOT a business frequency cap — it is a technical safeguard.

- The prediction model is built on recent form (last 10 matches). Predicting a team's future match before their most recent pending match resolves would create a data leak — the result of match N influences match N+1's prediction.
- If Team A plays on Monday and Thursday:
  - Monday match: Predicted during Monday's cycle.
  - Thursday match: Scheduled as `day_before_predict`. On Wednesday (24h before), the Scheduler wakes Leo to predict using Monday's result for fresh form encoding.
- **Enforcement**: At the team-prediction layer in Chapter 1 P2. Surplus matches are queued by the Scheduler, not discarded.

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
- **Cold-Start**: New leagues/teams get a generic adapter; the model defaults to conservative predictions.
- **Fine-Tune Threshold**: After 50+ matches, an adapter becomes eligible for fine-tuning.
- **Training**: Chronological day-by-day walk-through using only historical data (future dates excluded). PPO with composite rewards and clipped gradients.
- **Circuit Breaker**: SearchDict LLM enrichment skips remaining batches if all providers (Gemini + Grok) are offline.

---

*Last updated: March 6, 2026 (v7.2 — Data Leak Guard + 30-min Remediation Timeout + LoRA Lifecycle + Safety Guardrails)*
*LeoBook Engineering Team — Materialless LLC*
