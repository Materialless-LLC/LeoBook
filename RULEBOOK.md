# LeoBook Developer RuleBook v7.0

> **This document is LAW.** Every developer and AI agent working on LeoBook MUST follow these rules without exception. Violations will break the system.

---

## 1. First Principles

Before writing ANY code, ask in this exact order:

1. **Question** — Is this feature/change actually needed? What problem does it solve?
2. **Delete** — Can existing code be removed instead of adding more?
3. **Simplify** — What is the simplest possible implementation?
4. **Accelerate** — Can this run concurrently or be parallelized?
5. **Automate** — Can Leo.py orchestrate this without human intervention?

**Summary of First Principles Thinking** - **Question** every requirements and make it less dumb and **delete** those that are dumb and useless(no "incase-we-need-it" ideology), then **simplify**, **accelerate** and **automate** all those  that remain. This is MUSK do, throughout the entire **LeoBook** codebase -local and cloud.
---

## 2. Backend Architecture (Python)

### 2.1 Leo.py Is an Autonomous Orchestrator

- **`Leo.py` is an AUTONOMOUS ORCHESTRATOR** — it contains ZERO business logic.
- It is powered by a **Dynamic Task Scheduler** (`Core/System/scheduler.py`).
- Every script MUST be callable via `Leo.py` CLI flags.
- **Cycle Control**: Leo.py decides when to wake up based on `scheduler.next_wake_time()`. Static loops are forbidden.

### 2.2 Startup Bootstrapping (MANDATORY)

Every entry point (`main()`) MUST call `await run_startup_sync()`. This function ensures:
1. SQLite → Supabase **push-only sync** (only local rows modified since last watermark are pushed — ZERO reads from Supabase).
2. Auto-bootstrap: if local DB is empty, pulls all data from Supabase (one-time).
3. Local database and Supabase table existence.
Operations MUST NOT start (including live streamer) until startup sync completes successfully.

**Manual recovery**: `python Leo.py --pull` pulls ALL data from Supabase → local SQLite.

**Sync runs ONLY in Ch1 P3** (Final Sync). Ch1 P1 and Ch1 P2 do NOT sync — sync is consolidated to end of pipeline.

### 2.3 Data Readiness Gates (Prologue)

Leo.py operates via three sequential gates to ensure data integrity:

1. **Prologue P1 (Quantity Gate)**: Leagues >= 90% coverage AND Teams >= 5 per league.
2. **Prologue P2 (History Gate)**: Minimum 2+ distinct seasons of fixture data per league (seasons, not calendar years — includes current season).
3. **Prologue P3 (AI Gate)**: RL Adapters must be trained and ready.

**Auto-Remediation**: If a gate fails, Leo.py MUST attempt to trigger the relevant enrichment or training script automatically (`auto_remediate`).

### 2.4 Pipeline Structure (v7.0)

```
Startup Sync: Push-only (local → Supabase, auto-bootstrap if empty)
Task Scheduler: Execute pending tasks (Weekly Enrichment, predictions)
Prologue (Data Gates):
    P1: League/Team Thresholds (90% / 5 teams)
    P2: Historical Data Check (2+ Seasons)
    P3: AI RL Adapter Readiness
Chapter 1:
    P1: URL Resolution & Odds Harvesting (Football.com, no login)
    P2: Prediction Pipeline (Pure DB — Rule Engine + RL Ensemble, no browser)
        - Constraint: Max 1 prediction per team per week.
        - Surplus scheduled as 'day_before_predict' tasks.
    P3: Final Recommendations & Sync
Chapter 2:
    P1: Automated Booking (Football.com)
    P2: Funds & Withdrawal Check
Live Streamer: Isolated parallel task — Live Scores + Outcome Review + Accuracy Reports
```

### 2.5 Standings Table Is FORBIDDEN

- **Rule**: No persistent `standings` table allowed in SQLite or Supabase.
- **Implementation**: Standings MUST be computed on-the-fly via the `computed_standings` VIEW in Supabase or `computed_standings()` in `league_db.py`.
- **Reasoning**: Ensures zero-latency source-of-truth accuracy and removes redundant sync overhead.

### 2.6 File Headers (MANDATORY)

Every Python file MUST have this header format:

```python
# filename.py: One-line description of what this file does.
# Part of LeoBook <Component> — <Sub-component>
#
# Functions: func1(), func2(), func3()
# Called by: Leo.py (Chapter X Page Y) | other_module.py
```

### 2.7 No Dead Code

- No commented-out code blocks
- No unused imports
- No functions that are never called

### 2.8 Concurrency Rules

- **Max Concurrency**: strictly limited by `MAX_CONCURRENCY` in `.env`.
- **Sequential Integrity**: Inside each match worker, steps must remain SEQUENTIAL.
- **SQLite WAL**: Handles concurrent access. Never use manual locks for DB operations.
- **Live Streamer Isolation**: Streamer runs in its own Playwright instance with an isolated user data directory.

### 2.9 Timezone Consistency (Africa/Lagos)

- **Rule**: Every timestamp MUST use the Nigerian timezone (**Africa/Lagos**, UTC+1).
- **Tooling**: Use `Core.Utils.constants.now_ng()` for all time operations.

### 2.10 High-Velocity Data Ingestion (Selective Enrichment)

- **Rule**: When dealing with massive datasets (>1,000 leagues), developers and agents SHOULD use **selective enrichment** via range limits and season targeting.
- **Implementation**:
    - Use `--limit START-END` to process specific chunks of the league list.
    - Use `--season N` to target the most recent historical season (N=1) rather than multiple seasons at once.
- **Reasoning**: Prevents memory exhaustion in constrained environments (e.g., Codespaces) and allows for distributed processing if multiple LeoBook instances are run in parallel.

### 2.11 Selector Compliance (Zero Hardcoded Selectors)

- **Rule**: ALL CSS selectors used for web scraping MUST be defined in `Config/knowledge.json` and accessed via `Core.Intelligence.selector_manager.SelectorManager`. **Zero hardcoded selectors** in Python or JavaScript code files.
- **Implementation**:
    - Define selectors under the appropriate context key in `knowledge.json` (e.g., `fs_league_page`, `fs_match_page`).
    - In Python, use `selector_mgr.get_all_selectors_for_context(CONTEXT)` to retrieve the full selector dict.
    - In JS evaluation, pass the selectors dict as an argument and reference keys (e.g., `s.breadcrumb_links`, `s.match_link`).
- **Reasoning**: Flashscore frequently changes class names and DOM structure. Centralizing selectors in one JSON file makes updates fast and auditable.

---

## 3. Frontend Architecture (Flutter/Dart)

### 3.1 Constraints-Based Design (NO HARDCODED VALUES)

**The single most important rule:** Never use fixed `double` values (like `width: 300`) for layout-critical elements.

Use these widgets instead:
- `LayoutBuilder` — adapt widget trees based on parent `maxWidth`
- `Flexible` / `Expanded` — prevent overflow
- `FractionallySizedBox` — size as percentage
- `AspectRatio` — proportions
- `Responsive.sp(context, value)` — scaled spacing

### 3.2 Screens dispatch, Widgets render

Screens are pure dispatchers (`LayoutBuilder` / `Responsive.isDesktop()`). They contain ZERO rendering logic for components.

### 3.3 State Management

- Use `flutter_bloc` / `Cubit` exclusively.
- **NO RIVERPOD, NO GETX.**

---

## 4. Maintenance & Verification

### 4.1 Weekly Enrichment (Monday 2:26 AM)

The scheduler MUST trigger `enrich_leagues.py --weekly` every Monday. This mode is lightweight (`MAX_SHOW_MORE=2`) and focuses on schedule updates and missing metadata.

### 4.2 Before Every Commit

```bash
# Verify v7.0 Autonomous Loop
python Leo.py --help
python -c "from Core.System.scheduler import TaskScheduler; print('[OK]')"
python -c "from Core.System.data_readiness import DataReadinessChecker; print('[OK]')"
```

---

## 5. Flutter Design Specification — Liquid Glass

### 5.1 Font: Google Fonts — Lexend

| Level | Size | Weight | Spacing | Color |
|-------|------|--------|---------|-------|
| `displayLarge` | 22px | w700 (Bold) | -1.0 | `#FFFFFF` |
| `titleLarge` | 15px | w600 (SemiBold) | -0.3 | `#FFFFFF` |
| `titleMedium` | 13px | w600 | default | `#F1F5F9` |
| `bodyLarge` | 13px | w400 | default | `#F1F5F9` |
| `bodyMedium` | 11px | w400 | default | `#64748B` |

### 5.2 Color Palette

#### Brand & Primary
| Token | Hex | Usage |
|-------|-----|-------|
| `primary` / `electricBlue` | `#137FEC` | Buttons, active indicators |

#### Glass Tokens (60% translucency default)
| Token | Hex | Alpha |
|-------|-----|-------|
| `glassDark` | `#1A2332` | 60% (`0x99`) |
| `glassLight` | `#FFFFFF` | 60% |
| `glassBorderDark` | `#FFFFFF` | 10% |

### 5.3 Performance Modes (`GlassSettings`)
| Mode | Blur | Target |
|------|------|--------|
| `full` | 24σ | High-end devices |
| `medium` | 8σ | Mid-range devices |
| `none` | 0σ | Low-end devices |

---

## 6. 12-Step Problem-Solving Framework

> **MANDATORY** for all failure investigation and resolution. Follow in exact order.

| Step | Action | Rule |
|------|--------|------|
| **1. Define** | What is the problem? | Focus on understanding — no blame. |
| **2. Validate** | Is it really a problem? | Pause. Does this actually need solving? |
| **3. Expand** | What else is the problem? | Look for hidden or related issues. |
| **4. Trace** | How did it occur? | Reverse-engineer the timeline. |
| **5. Brainstorm** | ALL possible solutions. | No filtering yet. |
| **6. Evaluate** | Best solution right now? | Consider resources and time. |
| **7. Decide** | Commit to the solution. | No second-guessing. |
| **8. Assign** | Actionable steps. | Systematic and specific. |
| **9. Measure** | Define "solved". | Expected effects? |
| **10. Start** | Take first action. | Momentum matters. |
| **11. Complete** | Finish every step. | No half-measures. |
| **12. Review** | Compare outcomes. | Repeat if not solved. |

---

## 7. Decision-Making Standard

- **Sports Domain Accuracy**: Data MUST match the real-world source of truth.
- **Crest Integrity**: Team crests/logos MUST always be displayed alongside names.
- **No Hardcoded Proxy Data**: Never use placeholders (e.g., "WORLD"). Use "Unknown" if missing.
- **Sports-Informed Sorting**: Trust the database `position` column for standings.

---

*Last updated: March 6, 2026 (v7.2 — Push-Only Sync + DB-Driven Predictions + --pull CLI + gemini-3.1-flash-lite)*
*Authored by: LeoBook Engineering Team*

