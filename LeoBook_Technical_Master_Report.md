> **Version**: 7.2 · **Last Updated**: 2026-03-06 · **Architecture**: Autonomous High-Velocity Architecture (Task Scheduler + Data Readiness Gates + Neural RL)

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Project File Map](#2-project-file-map)
3. [Leo.py — Step-by-Step Execution Flow](#3-leopy--step-by-step-execution-flow)
4. [Design & UI/UX](#4-design--uiux)
5. [Data Flow Diagram](#5-data-flow-diagram)
6. [Bet Safety Guardrails](#6-bet-safety-guardrails)
7. [Model Performance & Open Quests](#7-model-performance--open-quests)
8. [Testing Strategy](#8-testing-strategy)
9. [Observability & Logging](#9-observability--logging)

---

## 1. System Overview

LeoBook is an **autonomous sports prediction and betting system** comprised of two halves:

| Half | Technology | Purpose |
|------|-----------|---------|
| **Backend (Leo.py)** | Python 3.12 + Playwright + PyTorch | Autonomous data extraction, rule-based + neural RL prediction, odds harvesting, automated bet placement, and **dynamic task scheduling** |
| **Frontend (leobookapp)** | Flutter/Dart (flutter_bloc/Cubit) | Dashboard with "Telegram-grade" density, liquid glass aesthetics, and proportional scaling |

### Architecture Principles

- **SQLite is the single source of truth.** All data originates in `leobook.db`. Supabase is a **push-only read mirror** — the Flutter app reads from Supabase, but no data flows back from Supabase to SQLite during normal operation.
- **Push-Only Sync.** `SyncManager` uses watermark-based delta detection. Only rows modified since the last watermark are pushed. Zero reads from Supabase during sync.
- **One-Time Bootstrap.** If a local SQLite table is empty (fresh install or post-corruption), `_bootstrap_from_remote()` pulls from Supabase once, then the watermark is set to `now()` to resume push-only behavior. The `--pull` CLI command also triggers a full bootstrap.
- **Task Scheduler.** Leo.py is powered by a dynamic `scheduler.py` that wakes at target task times or default intervals — no static 6h loop.
- **Data Readiness Gates.** Prologue P1-P3 verify data completeness before predictions. Auto-remediation runs if gates fail, with a **30-minute timeout** to prevent unbounded enrichment.
- **Standings** are computed on-the-fly via a Postgres VIEW in Supabase.

### Data Extraction Strategy

LeoBook uses **two external data sources** for distinct purposes:

| Source | Purpose | Method |
|--------|---------|--------|
| **Flashscore.com** | Match data, scores, team/league metadata, historical fixtures, live scores | Playwright browser automation (headful or headless) |
| **Football.com** | Odds harvesting, bet placement (Nigeria/Ghana region) | Playwright browser automation (no-login session) |

> [!NOTE]
> Flashscore is used for data extraction only — no bets are placed there. Football.com is used exclusively for Nigeria/Ghana-region odds and bet placement. The separation is intentional: Flashscore has globally comprehensive match data; Football.com has the target betting platform.

---

## 2. Project File Map

### 2.1 Root Files

| File | Function | Called by Leo.py? |
|------|----------|:-:|
| `Leo.py` | Autonomous orchestrator — manages the entire system loop | **Entrypoint** |
| `RULEBOOK.md` | Developer rules and philosophy (MANDATORY reading for all contributors and AI agents). Enforcement: honour-based with planned pre-commit hooks. | — |
| `PROJECT_STAIRWAY.md` | The capital compounding strategy ("why LeoBook must exist") | — |
| `requirements.txt` | Core Python dependencies | — |
| `requirements-rl.txt` | PyTorch CPU + RL dependencies | — |

### 2.2 `Core/` — System Infrastructure

| Directory | Files | Purpose |
|-----------|-------|---------|
| `Core/Intelligence/` | `rule_engine.py`, `learning_engine.py`, `rule_engine_manager.py`, `aigo_engine.py`, `aigo_suite.py`, `llm_health_manager.py` | AI engine, AIGO self-healing, adaptive learning, LLM provider health monitoring |
| `Core/Intelligence/rl/` | `trainer.py`, `inference.py`, `model.py` | Neural RL engine — SharedTrunk + LoRA adapters (see [LoRA Lifecycle](#lora-adapter-lifecycle)) |
| `Core/System/` | **`scheduler.py`**, **`data_readiness.py`**, `lifecycle.py`, `monitoring.py` | **Autonomous Scheduler**, **Data Gates** (with 30-min auto-remediation timeout), CLI parsing, state logging |
| `Core/Utils/` | `constants.py` | Shared constants including `now_ng` (see [Timezone](#timezone-anchor-now_ng)) |

#### LoRA Adapter Lifecycle

The RL model uses a **SharedTrunk + LoRA adapter** architecture:

- **Cold-Start**: New leagues/teams get a generic adapter initialized with the shared trunk's weights. No historical performance data is required — the model defaults to conservative predictions.
- **Fine-Tune Threshold**: After 50+ matches are recorded for a league or team, the adapter becomes eligible for fine-tuning via `RLTrainer.train_from_fixtures()`.
- **Registry**: `AdapterRegistry` tracks all known leagues/teams and their match counts. Saved to `Data/Store/models/adapter_registry.json`.
- **Training**: Chronological walk-through (day-by-day), using only data available before each match date. Future dates are excluded. Training produces per-match PPO updates with composite rewards.

#### Timezone Anchor: `now_ng`

`now_ng` uses **West Africa Time (WAT, UTC+1)** as the system clock. This is intentional:
- Football.com operates exclusively in Nigeria/Ghana, both WAT timezone.
- Developer location is WAT.
- Cross-league timezone normalization (UTC/CET for European leagues) is a planned future enhancement.
- **Edge case to watch**: European daylight saving transitions may cause 1-hour misalignment in match time parsing during DST transition weeks.

### 2.3 `Modules/` — Domain Logic

| File | Function |
|------|----------|
| `Modules/Flashscore/fs_processor.py` | Per-match H2H + Enrichment + Search Dict |
| `Modules/Flashscore/fs_live_streamer.py` | Isolated live score streaming + outcome review + accuracy reports |
| `Modules/FootballCom/fb_manager.py` | Odds harvesting, automated booking |

### 2.4 `Data/` — Persistence Layer

| File | Function |
|------|----------|
| `Data/Access/league_db.py` | SQLite schema, `computed_standings()` helper, auto-corruption recovery |
| `Data/Access/sync_manager.py` | `SyncManager` — **push-only** watermark sync (SQLite → Supabase only) |
| `Data/Access/db_helpers.py` | High-level DB operations, team/league/prediction CRUD |
| `Data/Access/outcome_reviewer.py` | Outcome review logic |
| `Data/Access/metadata_linker.py` | Team/league metadata enrichment linking |

### 2.5 `Scripts/` — Pipeline Scripts

| File | Function |
|------|----------|
| `Scripts/enrich_leagues.py` | League metadata + Historical data enrichment |
| `Scripts/build_search_dict.py` | LLM-powered search term/abbreviation enrichment (with circuit breaker) |
| `Scripts/recommend_bets.py` | Recommendation engine |

#### Enrichment Data Extraction Strategy

| Data Point | Extraction Method | Source |
|---|---|---|
| `fs_league_id` | `window.leaguePageHeaderData.tournamentStageId` | Flashscore internal JS config |
| `region` | 2nd `.breadcrumb__link` element (index 1) | Breadcrumb navigation |
| `crest` | `img.heading__logo` `src` attribute | League page header |
| `current_season` | `.heading__info` with year regex | League page header |
| `match_link` | `<a class="eventRowLink" aria-describedby="g_1_ID">` | Match row sibling |
| `team_id` | Parsed from `eventRowLink` href segments | Match link URL path |
| `region_league` | `"{region}: {league_name}"` | Constructed from extracted region + league name |

> [!IMPORTANT]
> All CSS selectors MUST live in `Config/knowledge.json` and be accessed via `SelectorManager`. Zero hardcoded selectors in Python/JS code.

> [!WARNING]
> `region_flag` images cannot be downloaded — Flashscore uses CSS sprite backgrounds for country flags, not `<img>` tags. The `region_flag` column stores the URL for reference but the actual image is not downloadable via standard methods.

---

### 2.6 Command Line Interface

Leo.py supports two modes of operation:

**Normal Cycle**: `python Leo.py` — runs the full autonomous pipeline (Prologue → Chapter 1 → Chapter 2 → Sleep → repeat).

**Utility Commands** (single-shot, no cycle loop):

| Command | Purpose |
|---------|---------|
| `--enrich-leagues` | **Gap-scan mode**: detects NULL/missing fields across the DB and fetches only those gaps. Does NOT reset existing data. |
| `--enrich-leagues --limit START-END` | **Range-reset mode**: reprocesses the specified ordinal range regardless of prior state. START-END refers to position in the enrichment queue, not database row IDs. |
| `--enrich-leagues --season N` | Target a specific past season (1 = latest past season) |
| `--enrich-leagues --seasons N` | Cumulative: fetch the last N seasons |
| `--enrich-leagues --weekly` | Lightweight refresh for incremental updates |
| `--sync` | Force push-only sync (SQLite → Supabase) |
| `--pull` | Full bootstrap pull (Supabase → SQLite). Deletes and recreates local DB tables. |
| `--search-dict` | Rebuild search dictionary via LLM enrichment |
| `--prologue` | Run all Prologue pages (P1+P2+P3) |

---

## 3. Leo.py — Step-by-Step Execution Flow (v7.2)

Leo.py orchestrates the cycle with autonomous task management:

### Startup Flow (Bootstrap)
1. **Singleton Check**: Creates `leo.lock` at runtime. If `leo.lock` exists from a prior run, startup halts to prevent duplicate instances. Lock is released on clean shutdown. If Leo.py crashes without releasing the lock, the stale lock file must be manually deleted.
2. **Startup Sync**: Call `run_startup_sync()` — push-only sync to ensure Supabase parity. If local DB is empty/missing, auto-bootstraps from Supabase.
3. **Streamer Ignition**: Spawn isolated streamer task AFTER sync completes.

### Autonomous Cycle Loop
1. **Task Scheduler Check**: Execute pending tasks (`weekly_enrichment`, `day_before_predict`, `rl_training`).
2. **Prologue (Data Readiness Gates)**:
    - **P1: Quantity Gate**: Leagues ≥ 90% of `leagues.json` AND teams ≥ 5 per processed league.
    - **P2: History Gate**: ≥ 80% of leagues with fixtures have 2+ distinct seasons (including current). Auto-remediation triggers enrichment with a **30-minute timeout** — if enrichment exceeds this budget, the system proceeds with available data.
    - **P3: AI Gate**: RL base model + league adapters exist. Auto-triggers `RLTrainer.train_from_fixtures()` if not ready.
    - *All gates log `partial_failure` and proceed to Chapter 1 even if remediation doesn't fully resolve the gap.*
3. **Chapter 1: Prediction Pipeline**:
    - **P1**: URL Resolution & Odds Harvesting from Football.com (no sync).
    - **P2**: Predictions (Rule Engine + Neural RL Ensemble, pure DB — no browser). **Data Leak Guard**: Max 1 prediction per team per week. This is NOT a business frequency cap — it prevents the model from using future match data to predict earlier matches. A team's next match can only be predicted once their most recent match result is known. Surplus matches are queued by the Scheduler for prediction once prerequisite data is available.
    - **P3**: Final Chapter Sync (push-only watermark delta) & Recommendation Generation.
4. **Chapter 2: Betting & Funds**:
    - **P1**: Automated Booking on Football.com (see [Safety Guardrails](#6-bet-safety-guardrails)).
    - **P2**: Funds balance + withdrawal check.
5. **Cycle Complete — Dynamic Sleep**:
    - Log completion.
    - Consult `scheduler.next_wake_time()`.
    - Sleep until next task or default interval.

---

## 4. Design & UI/UX

The LeoBook Flutter app (`leobookapp/`) reads exclusively from Supabase. It is a pure read client — no data flows from the app back to the backend.

---

## 5. Data Flow Diagram

```mermaid
flowchart LR
    subgraph SOURCES ["External Sources"]
        FS[("Flashscore.com<br/>Match Data + Live Scores")]
        FB[("Football.com<br/>Odds + Bet Placement")]
    end

    subgraph LEO ["Leo.py Orchestrator"]
        direction TB
        SCHED["Task Scheduler"]
        GATES["Data Gates P1-P3<br/>(30-min timeout)"]
        PREDICT["Predict<br/>(Rule Engine + RL)"]
        BOOK["Place Bets"]
        STREAM["Live Streamer<br/>(isolated)"]
    end

    subgraph DATA ["Data Layer"]
        DB[("leobook.db<br/>(SQLite — Source of Truth)")]
        SB[("Supabase Cloud<br/>(Push-Only Read Mirror)")]
        RL[("RL Adapters<br/>(Neural Weights)")]
    end

    FS --> GATES --> DB
    DB -->|"push-only sync"| SB
    SCHED --> GATES
    DB --> PREDICT --> DB
    PREDICT -.-> RL
    DB --> BOOK --> FB
    STREAM --> DB
    DB -->|"push live scores"| SB
    SB -->|"read only"| APP["LeoBook App<br/>(Flutter)"]
```

> [!NOTE]
> Data ownership is strictly directional:
> - **Leo.py → SQLite**: all writes (predictions, schedules, live scores, teams, leagues)
> - **SQLite → Supabase**: push-only sync (watermark delta, no reads)
> - **Supabase → Flutter App**: read only (computed views)
> - **Supabase → SQLite**: only during `--pull` bootstrap (one-time or manual)

---

## 6. Bet Safety Guardrails

> [!CAUTION]
> Chapter 2 (automated bet placement) involves real money. The following guardrails are the **intended safety architecture**. Items marked [PLANNED] are not yet implemented in code.

### Implemented

| Guardrail | Status | Description |
|-----------|--------|-------------|
| **Audit Logging** | ✅ Implemented | Every bet cycle writes to `audit_log` table in both SQLite and Supabase. Includes balance_before, balance_after, stake, event_type, and status. |
| **Confidence Gating** | ✅ Implemented | Predictions below a confidence threshold are marked `SKIP` and never progress to betting. |
| **Max 1/team/week** | ✅ Implemented | Data leak guard prevents stale-data predictions. |

### Planned (Not Yet in Code)

| Guardrail | Priority | Description |
|-----------|----------|-------------|
| **Dry-Run Mode** | CRITICAL | `--dry-run` flag that logs what WOULD be bet without placing real bets. Must be the default mode until full pipeline validation is complete. |
| **Max Stake Cap** | CRITICAL | Hard ceiling on any single bet amount. Tied to the Project Stairway step table (₦1,000 base → ₦2,186,000 max at step 7). |
| **Daily Loss Limit** | HIGH | If cumulative daily losses exceed a configurable threshold, halt all betting for the remainder of the day. |
| **Kill Switch** | HIGH | Emergency stop: a flag file (`STOP_BETTING`) that, when present, prevents all bet placement regardless of system state. |
| **Staircase State Machine** | HIGH | Tracks current step (1-7), current stake, win/loss history. Resets to ₦1,000 on any loss. See `PROJECT_STAIRWAY.md` for the mathematical framework. |
| **Balance Sanity Check** | MEDIUM | Before placing a bet, verify that the Football.com account balance ≥ intended stake. Abort if insufficient. |

---

## 7. Model Performance & Open Quests

> [!IMPORTANT]
> LeoBook is in **active development — modular testing phase**. No end-to-end pipeline test has been completed. The numbers below are preliminary and will be updated after full pipeline validation.

### Current State

| Metric | Value | Notes |
|--------|-------|-------|
| RL Training Accuracy | Pending retrain | Previous run had tensor shape bug (now fixed). Retraining required. |
| Rule Engine Accuracy | Untested at scale | Individual rule components work; aggregate accuracy unknown. |
| Calibration | Unmeasured | Does a 38% confidence prediction win 38% of the time? Unknown. |
| Per-Step Win Probability | Unmeasured | The foundational number for Project Stairway viability. |

### Open Quests (To Be Answered by Pipeline Testing)

1. What is LeoBook's actual per-step win probability on selected bets at ~4.0 odds?
2. How does win probability vary by league, sport, and season stage?
3. Is the optimal step target a single match at 4.0, or a 2-3 match accumulator multiplying to 4.0?
4. What is the empirically observed calibration of the model?
5. What is the expected number of cycles before a full 7-step staircase completes?
6. What is the optimal confidence threshold gate for bet placement?

These questions will be answered by data, not assumption.

---

## 8. Testing Strategy

### Current State

| Layer | Status | Notes |
|-------|--------|-------|
| Unit Tests | ❌ Not implemented | No `tests/` directory exists yet |
| Widget Tests | ❌ Not implemented | Flutter app has no test coverage |
| Integration Tests | ❌ Not implemented | No end-to-end pipeline test |
| CI/CD | ❌ Not configured | No GitHub Actions or equivalent |

### Planned Testing Architecture

| Layer | Scope | Tools |
|-------|-------|-------|
| **Unit** | Repositories, rule engine, reward computation, feature encoder | `pytest` + `mocktail` |
| **Integration** | Full pipeline: Enrichment → Prediction → Recommendation → Sync | `pytest` with SQLite test fixtures |
| **E2E** | Dry-run bet placement cycle with mock Football.com responses | `playwright` + `pytest` |
| **Flutter** | Widget tests + golden tests for UI components | `flutter_test` + `patrol` |
| **Regression** | Accuracy tracking across commits — detect prediction quality regressions | Custom script + `accuracy_reports` table |

### Priority Order for Implementation
1. Rule engine unit tests (highest value: validates prediction correctness)
2. Sync manager integration tests (validates data integrity)
3. Dry-run bet placement E2E test (validates safety before real money)
4. Flutter widget tests

---

## 9. Observability & Logging

### Terminal Logging

- **Setup**: `setup_terminal_logging()` in `lifecycle.py` configures log format and handlers.
- **Log Location**: `Data/Log/Terminal/` — session-stamped log files (`leo_session_YYYYMMDD_HHMMSS.log`).
- **State Snapshots**: `log_state()` writes structured state transitions (chapter/phase/action) for debugging.

### Audit Logging

- **Target**: `audit_log` table in both SQLite and Supabase.
- **Scope**: Every bet cycle, balance change, and system event is recorded with timestamp, balance_before/after, stake, and status.
- **Function**: `log_audit_event()` in `db_helpers.py`.

### LLM Health Monitoring

- **Manager**: `llm_health_manager.py` tracks Gemini key rotation (25+ keys × 6 models) and Grok API health.
- **Circuit Breaker**: `build_search_dict.py` checks `health_manager._gemini_active` before each batch — if all providers are dead, skips remaining work instantly instead of iterating through thousands of guaranteed failures.

### Monitoring

- **File**: `Core/System/monitoring.py` — cycle health checks, anomaly detection.
- **Future**: Structured JSON-line logging for easier querying and alerting.

---

*Last updated: March 6, 2026 (v7.2 — Push-Only Sync, Safety Guardrails, Observability, 13-Discrepancy Resolution)*
*LeoBook Engineering Team — Materialless LLC*
