# pipeline.py: Leo page functions — the async operations called by Leo.py dispatch.
# Part of LeoBook Core — System
# Extracted from Leo.py (P11). Each function is a self-contained async chapter/page.
# Imported by: Leo.py

from typing import Optional
from Core.System.lifecycle import log_state, state
from Core.System.scheduler import TaskScheduler, TASK_WEEKLY_ENRICHMENT, TASK_DAY_BEFORE_PREDICT, TASK_RL_TRAINING
from Core.System.data_readiness import check_leagues_ready, check_seasons_ready
from Core.Intelligence.aigo_suite import AIGOSuite
from Data.Access.db_helpers import init_csvs, log_audit_event
from Data.Access.sync_manager import SyncManager, run_full_sync
from Data.Access.league_db import init_db
from Modules.Flashscore.fs_live_streamer import live_score_streamer
from Modules.FootballCom.fb_manager import run_odds_harvesting, run_automated_booking
from Scripts.recommend_bets import get_recommendations
from Core.Intelligence.prediction_pipeline import run_predictions
from Scripts.enrich_leagues import main as run_league_enricher
from Data.Access.asset_manager import sync_team_assets, sync_league_assets, sync_region_flags


# ============================================================
# STARTUP
# ============================================================

async def run_startup_sync():
    """Startup: Ensure local DB exists, then push-only sync.
    Auto-bootstraps from Supabase if local DB is missing or empty."""
    log_state(chapter="Startup", action="DB Initialization & Push-Only Sync")
    try:
        print("\n" + "=" * 60)
        print("  STARTUP: Database Initialization & Push-Only Sync")
        print("=" * 60)

        init_csvs()
        conn = init_db()

        try:
            sched_count = conn.execute("SELECT COUNT(*) FROM schedules").fetchone()[0]
        except Exception:
            sched_count = 0

        if sched_count == 0:
            print("     [!] Local DB empty - will bootstrap from Supabase automatically")

        sync_mgr = SyncManager()
        await sync_mgr.sync_on_startup()

        log_audit_event("STARTUP", "DB initialized and push-only sync completed.", status="success")
        print("  [Startup] Complete")
        return True
    except Exception as e:
        print(f"  [Error] Startup sync failed: {e}")
        log_audit_event("STARTUP", f"Failed: {e}", status="failed")
        return False


# ============================================================
# AUTO-REMEDIATION
# ============================================================

async def auto_remediate(target: str):
    """Auto-triggers the correct enrichment or training path to fix readiness gaps."""
    print(f"\n  [AUTO] Triggering auto-remediation for: {target}")
    try:
        if target == "leagues":
            await run_league_enricher(limit=100)
        elif target == "seasons":
            await run_league_enricher(num_seasons=2)
        elif target == "rl":
            from Core.Intelligence.rl.trainer import RLTrainer
            trainer = RLTrainer()
            trainer.train_from_fixtures()
        print(f"  [AUTO] Remediation cycle completed.")
    except Exception as e:
        print(f"  [AUTO] League enrichment failed: {e}")


# ============================================================
# PROLOGUE — Data Readiness Gates
# ============================================================

async def run_prologue_p1():
    """Prologue P1: Verify leagues >= 90% of leagues.json AND teams >= 5 per league."""
    log_state(chapter="Prologue P1", action="Data Readiness: Leagues & Teams")
    try:
        print("\n" + "=" * 60)
        print("  PROLOGUE P1: Data Readiness - Leagues & Teams")
        print("=" * 60)

        ready, stats = check_leagues_ready()
        if not ready:
            await auto_remediate("leagues")
            ready, stats = check_leagues_ready()

        log_audit_event("PROLOGUE_P1",
                        f"Leagues: {stats['actual_leagues']}/{stats['expected_leagues']}, "
                        f"Teams: {stats['team_count']}",
                        status="success" if ready else "partial_failure")
    except Exception as e:
        print(f"  [Error] Prologue P1 failed: {e}")
        log_audit_event("PROLOGUE_P1", f"Failed: {e}", status="failed")


async def run_prologue_p2():
    """Prologue P2: Verify >= 2 seasons of historical fixtures per league."""
    log_state(chapter="Prologue P2", action="Data Readiness: Historical Seasons")
    try:
        print("\n" + "=" * 60)
        print("  PROLOGUE P2: Data Readiness - Historical Seasons")
        print("=" * 60)

        ready, stats = check_seasons_ready()
        if not ready:
            await auto_remediate("seasons")
            ready, stats = check_seasons_ready()

        log_audit_event(
            "PROLOGUE_P2",
            f"Seasons: {stats.get('total_seasons', 0)} computed | "
            f"RL tier: {stats.get('rl_tier', 'UNKNOWN')} | "
            f"Gaps: {stats.get('critical_gaps', 0)} critical",
            status="success" if ready else "partial_failure"
        )
    except Exception as e:
        print(f"  [Error] Prologue P2 failed: {e}")
        log_audit_event("PROLOGUE_P2", f"Failed: {e}", status="failed")


async def run_prologue_p3():
    """Prologue P3: RL Adapter Check [DISABLED]."""
    print("\n" + "=" * 60)
    print("  PROLOGUE P3: RL Adapter Check [DISABLED]")
    print("=" * 60)
    print("    Skipping RL readiness check as requested.")


# ============================================================
# CHAPTER 1 — Prediction Pipeline
# ============================================================

@AIGOSuite.aigo_retry(max_retries=2, delay=3.0)
async def run_chapter_1_p1(p):
    """Chapter 1 Page 1: URL Resolution & Odds Harvesting."""
    log_state(chapter="Ch1 P1", action="URL Resolution & Odds Harvesting")
    try:
        print("\n" + "=" * 60)
        print("  CHAPTER 1 PAGE 1: URL Resolution & Odds Harvesting")
        print("=" * 60)

        await run_odds_harvesting(p)
        log_audit_event("CH1_P1", "URL resolution and odds harvesting completed.", status="success")
        return True
    except Exception as e:
        print(f"  [Error] Chapter 1 Page 1 failed: {e}")
        log_audit_event("CH1_P1", f"Failed: {e}", status="failed")
        return False


@AIGOSuite.aigo_retry(max_retries=2, delay=3.0)
async def run_chapter_1_p2(p=None, scheduler: TaskScheduler = None,
                           refresh: bool = False, target_dates: Optional[list] = None):
    """Chapter 1 Page 2: Predictions (Rule Engine + RL Ensemble)."""
    log_state(chapter="Ch1 P2", action="Predictions")
    try:
        print("\n" + "=" * 60)
        print("  CHAPTER 1 PAGE 2: Predictions (Pure DB Computation)")
        print("=" * 60)
        predictions = await run_predictions(scheduler=scheduler)
        count = len(predictions) if predictions else 0
        log_audit_event("CH1_P2", f"Predictions completed: {count} generated.", status="success")
    except Exception as e:
        print(f"  [Error] Chapter 1 Page 2 failed: {e}")
        log_audit_event("CH1_P2", f"Failed: {e}", status="failed")


@AIGOSuite.aigo_retry(max_retries=2, delay=2.0)
async def run_chapter_1_p3():
    """Chapter 1 Page 3: Recommendations & Final Sync."""
    log_state(chapter="Ch1 P3", action="Recommendations & Final Sync")
    try:
        print("\n" + "=" * 60)
        print("  CHAPTER 1 PAGE 3: Recommendations & Final Sync")
        print("=" * 60)

        await get_recommendations(save_to_file=True)

        sync_ok = await run_full_sync(session_name="Chapter 1 Final")
        if not sync_ok:
            print("  [AIGO] Sync parity issues detected. Logged for review.")
            log_audit_event("CH1_P3_SYNC", "Sync parity issues detected.", status="partial_failure")

        log_audit_event("CH1_P3", "Recommendations and final sync completed.", status="success")
    except Exception as e:
        print(f"  [Error] Chapter 1 Page 3 failed: {e}")
        log_audit_event("CH1_P3", f"Failed: {e}", status="failed")


# ============================================================
# CHAPTER 2 — Betting Automation
# ============================================================

@AIGOSuite.aigo_retry(max_retries=2, delay=5.0)
async def run_chapter_2_p1(p):
    """Chapter 2 Page 1: Automated Booking on Football.com."""
    log_state(chapter="Ch2 P1", action="Automated Booking (Football.com)")
    try:
        print("\n" + "=" * 60)
        print("  CHAPTER 2 PAGE 1: Automated Booking")
        print("=" * 60)
        await run_automated_booking(p)
        await run_full_sync(session_name="Ch2 P1 Booking")
        log_audit_event("CH2_P1", "Automated booking phase completed.", status="success")
    except Exception as e:
        print(f"  [Error] Chapter 2 Page 1 failed: {e}")
        log_audit_event("CH2_P1", f"Failed: {e}", status="failed")


@AIGOSuite.aigo_retry(max_retries=2, delay=5.0)
async def run_chapter_2_p2(p):
    """Chapter 2 Page 2: Funds Balance & Withdrawal Check."""
    log_state(chapter="Ch2 P2", action="Funds & Withdrawal Check")
    try:
        print("\n" + "=" * 60)
        print("  CHAPTER 2 PAGE 2: Funds & Withdrawal Check")
        print("=" * 60)
        from Core.System.withdrawal_checker import (
            check_triggers, propose_withdrawal, calculate_proposed_amount,
            get_latest_win, check_withdrawal_approval, execute_withdrawal
        )
        async with await p.chromium.launch(headless=True) as check_browser:
            from Modules.FootballCom.navigator import extract_balance
            check_page = await check_browser.new_page()
            state["current_balance"] = await extract_balance(check_page)

        if await check_triggers():
            proposed_amount = calculate_proposed_amount(state["current_balance"], get_latest_win())
            await propose_withdrawal(proposed_amount)

        if await check_withdrawal_approval():
            from Core.System.withdrawal_checker import pending_withdrawal
            await execute_withdrawal(pending_withdrawal["amount"])

        log_audit_event("CH2_P2",
                        f"Withdrawal check completed. Balance: {state.get('current_balance', 'N/A')}",
                        status="success")
        await run_full_sync(session_name="Ch2 P2 Withdrawal")
    except Exception as e:
        print(f"  [Warning] Chapter 2 Page 2 failed: {e}")
        log_audit_event("CH2_P2", f"Failed: {e}", status="failed")


# ============================================================
# SCHEDULED TASK EXECUTOR
# ============================================================

async def execute_scheduled_tasks(scheduler: TaskScheduler, p=None):
    """Execute all pending scheduled tasks."""
    pending = scheduler.get_pending_tasks()
    if not pending:
        return

    print(f"\n  [Scheduler] Executing {len(pending)} pending task(s)...")

    for task in pending:
        try:
            if task.task_type == TASK_WEEKLY_ENRICHMENT:
                print(f"  [Scheduler] Running weekly enrichment (task: {task.task_id})")
                await run_league_enricher(weekly=True)
                scheduler.complete_task(task.task_id)

            elif task.task_type == TASK_DAY_BEFORE_PREDICT:
                fid = task.params.get('fixture_id')
                print(f"  [Scheduler] Day-before prediction for fixture {fid}")
                await run_predictions(scheduler=scheduler)
                scheduler.complete_task(task.task_id)

            elif task.task_type == TASK_RL_TRAINING:
                print(f"  [Scheduler] Running RL training (task: {task.task_id})")
                await auto_remediate("rl")
                scheduler.complete_task(task.task_id)

        except Exception as e:
            print(f"  [Scheduler] Task {task.task_id} failed: {e}")
            scheduler.complete_task(task.task_id, status="failed")

    scheduler.cleanup_old(days=7)


# ============================================================
# DISPATCH — Routes CLI args to the appropriate functions
# ============================================================

async def dispatch(args):
    """Route CLI arguments to the correct execution path."""
    from playwright.async_api import async_playwright
    init_csvs()

    async with async_playwright() as p:
        if args.prologue:
            if args.page == 1:
                await run_prologue_p1()
            elif args.page == 2:
                await run_prologue_p2()
            elif args.page == 3:
                await run_prologue_p3()
            else:
                await run_prologue_p1()
                await run_prologue_p2()
                await run_prologue_p3()
            return

        if args.chapter == 1:
            if args.page == 1:
                await run_chapter_1_p1(p)
            elif args.page == 2:
                await run_chapter_1_p2(p,
                    refresh=getattr(args, 'refresh', False) or getattr(args, 'all', False),
                    target_dates=getattr(args, 'date', None))
            elif args.page == 3:
                await run_chapter_1_p3()
            else:
                await run_chapter_1_p1(p)
                await run_chapter_1_p2(p,
                    refresh=getattr(args, 'refresh', False) or getattr(args, 'all', False),
                    target_dates=getattr(args, 'date', None))
                await run_chapter_1_p3()
            return

        if args.chapter == 2:
            from Core.System.guardrails import run_all_pre_bet_checks, is_dry_run
            from Data.Access.league_db import get_connection
            conn = get_connection()
            ok, reason = run_all_pre_bet_checks(conn, state.get("current_balance", 0))
            if not ok:
                print(f"  [GUARDRAIL] Chapter 2 BLOCKED: {reason}")
                log_audit_event("GUARDRAIL_BLOCK", reason, status="blocked")
                return
            if args.page == 1:
                await run_chapter_2_p1(p)
            elif args.page == 2:
                await run_chapter_2_p2(p)
            else:
                await run_chapter_2_p1(p)
                await run_chapter_2_p2(p)
            return

    print("[ERROR] Unknown dispatch target.")


__all__ = [
    "run_startup_sync", "auto_remediate",
    "run_prologue_p1", "run_prologue_p2", "run_prologue_p3",
    "run_chapter_1_p1", "run_chapter_1_p2", "run_chapter_1_p3",
    "run_chapter_2_p1", "run_chapter_2_p2",
    "execute_scheduled_tasks", "dispatch",
]
