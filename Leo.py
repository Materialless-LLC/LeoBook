# Leo.py: Leo.py: The central orchestrator for the LeoBook system (v3.0).
# Part of LeoBook Unknown
#
# Functions: run_prologue_p1(), run_prologue_p2(), run_prologue_p3(), run_chapter_1_p1(), run_chapter_1_p2(), run_chapter_1_p3(), run_chapter_2_p1(), run_chapter_2_p2() (+5 more)

import asyncio
import nest_asyncio
import os
import sys
from datetime import datetime as dt
from dotenv import load_dotenv
from playwright.async_api import async_playwright

# Apply nest_asyncio for nested loops
nest_asyncio.apply()

# Load environment variables
load_dotenv()

def validate_config():
    """Validate required environment variables and configurations."""
    required_vars = [
        'GROK_API_KEY',
        'GEMINI_API_KEY',
        'FB_PHONE',
        'FB_PASSWORD'
    ]
    
    missing = []
    for var in required_vars:
        if not os.getenv(var):
            missing.append(var)
    
    if missing:
        raise ValueError(f"Missing required environment variables: {', '.join(missing)}. Please check your .env file.")
    
    # Validate cycle wait hours
    cycle_hours = os.getenv('LEO_CYCLE_WAIT_HOURS', '6')
    try:
        hours = int(cycle_hours)
        if hours < 1 or hours > 24:
            raise ValueError("LEO_CYCLE_WAIT_HOURS must be between 1 and 24")
    except ValueError:
        raise ValueError("LEO_CYCLE_WAIT_HOURS must be a valid integer")
    
    print("[CONFIG] All required environment variables validated.")

# Validate configuration on startup
validate_config()

# --- Modular Imports (all logic is external) ---
from Core.System.lifecycle import (
    log_state, log_audit_state, setup_terminal_logging, parse_args, state
)
from Core.Intelligence.aigo_suite import AIGOSuite
from Core.System.withdrawal_checker import (
    check_triggers, propose_withdrawal, calculate_proposed_amount, get_latest_win,
    check_withdrawal_approval, execute_withdrawal
)
from Core.System.scheduler import (
    TaskScheduler, TASK_WEEKLY_ENRICHMENT, TASK_DAY_BEFORE_PREDICT, TASK_RL_TRAINING
)
from Core.System.data_readiness import (
    check_leagues_ready, check_seasons_ready, check_rl_ready, auto_remediate
)
from Data.Access.db_helpers import init_csvs, log_audit_event
from Data.Access.sync_manager import SyncManager, run_full_sync
from Data.Access.league_db import init_db
from Scripts.enrich_all_schedules import enrich_all_schedules
from Modules.Flashscore.manager import run_flashscore_analysis, run_flashscore_offline_repredict, run_flashscore_schedule_only
from Modules.Flashscore.fs_live_streamer import live_score_streamer
from Modules.FootballCom.fb_manager import run_odds_harvesting, run_automated_booking
from Scripts.recommend_bets import get_recommendations
from Scripts.enrich_leagues import main as run_league_enricher
from Modules.Assets.asset_manager import sync_team_assets, sync_league_assets, sync_region_flags
from Scripts.football_logos import download_all_logos
from Scripts.upgrade_crests import upgrade_all_crests

# Configuration
DEFAULT_CYCLE_HOURS = int(os.getenv('LEO_CYCLE_WAIT_HOURS', 6))
LOCK_FILE = "leo.lock"


# ============================================================
# PAGE FUNCTIONS — Each is a self-contained async operation
# ============================================================

# ============================================================
# STARTUP — Ensures DB + Supabase tables exist, full sync
# ============================================================

async def run_startup_sync():
    """Startup: Ensure local DB and Supabase tables exist, then full bi-directional sync.
    Must complete before the live streamer starts."""
    log_state(chapter="Startup", action="DB Initialization & Full Sync")
    try:
        print("\n" + "=" * 60)
        print("  STARTUP: Database Initialization & Full Sync")
        print("=" * 60)

        # Initialize local SQLite DB (creates tables if missing)
        init_csvs()
        init_db()

        # Full bi-directional sync (auto-creates Supabase tables if missing)
        sync_mgr = SyncManager()
        await sync_mgr.sync_on_startup()

        log_audit_event("STARTUP", "DB initialized and full sync completed.", status="success")
        print("  [Startup] ✓ Complete")
        return True
    except Exception as e:
        print(f"  [Error] Startup sync failed: {e}")
        log_audit_event("STARTUP", f"Failed: {e}", status="failed")
        return False


# ============================================================
# PROLOGUE — Data Readiness Gates (P1-P3)
# ============================================================

async def run_prologue_p1():
    """Prologue P1: Verify leagues >= 90% of leagues.json AND teams >= 5 per league.
    Auto-remediates via --enrich-leagues + --search-dict if below threshold."""
    log_state(chapter="Prologue P1", action="Data Readiness: Leagues & Teams")
    try:
        print("\n" + "=" * 60)
        print("  PROLOGUE P1: Data Readiness — Leagues & Teams")
        print("=" * 60)

        ready, stats = check_leagues_ready()
        if not ready:
            await auto_remediate("leagues")
            ready, stats = check_leagues_ready()

        log_audit_event("PROLOGUE_P1", f"Leagues: {stats['actual_leagues']}/{stats['expected_leagues']}, "
                        f"Teams: {stats['team_count']}", status="success" if ready else "partial_failure")
    except Exception as e:
        print(f"  [Error] Prologue P1 failed: {e}")
        log_audit_event("PROLOGUE_P1", f"Failed: {e}", status="failed")


async def run_prologue_p2():
    """Prologue P2: Verify >= 2 seasons of historical fixtures per league.
    Auto-remediates via --enrich-leagues --seasons 2 if below threshold."""
    log_state(chapter="Prologue P2", action="Data Readiness: Historical Seasons")
    try:
        print("\n" + "=" * 60)
        print("  PROLOGUE P2: Data Readiness — Historical Seasons")
        print("=" * 60)

        ready, stats = check_seasons_ready()
        if not ready:
            await auto_remediate("seasons")
            ready, stats = check_seasons_ready()

        log_audit_event("PROLOGUE_P2", f"Seasons: {stats['leagues_with_enough_seasons']}/"
                        f"{stats['total_leagues_with_fixtures']} leagues OK",
                        status="success" if ready else "partial_failure")
    except Exception as e:
        print(f"  [Error] Prologue P2 failed: {e}")
        log_audit_event("PROLOGUE_P2", f"Failed: {e}", status="failed")


async def run_prologue_p3():
    """Prologue P3: Verify RL adapters are trained for active leagues.
    Auto-remediates via --train-rl if not ready."""
    log_state(chapter="Prologue P3", action="Data Readiness: RL Adapters")
    try:
        print("\n" + "=" * 60)
        print("  PROLOGUE P3: Data Readiness — RL Adapters")
        print("=" * 60)

        ready, stats = check_rl_ready()
        if not ready:
            await auto_remediate("rl")
            ready, stats = check_rl_ready()

        log_audit_event("PROLOGUE_P3", f"RL: base={stats['has_base_model']}, "
                        f"adapters={stats['adapter_count']}",
                        status="success" if ready else "partial_failure")
    except Exception as e:
        print(f"  [Error] Prologue P3 failed: {e}")
        log_audit_event("PROLOGUE_P3", f"Failed: {e}", status="failed")


# ============================================================
# CHAPTER 1 — Prediction Pipeline
# ============================================================

@AIGOSuite.aigo_retry(max_retries=2, delay=3.0)
async def run_chapter_1_p1(p):
    """Chapter 1 Page 1: URL Resolution & Odds Harvesting (Football.com).
    Matches Flashscore fixtures to Football.com fixtures and harvests odds."""
    log_state(chapter="Ch1 P1", action="URL Resolution & Odds Harvesting")
    try:
        print("\n" + "=" * 60)
        print("  CHAPTER 1 PAGE 1: URL Resolution & Odds Harvesting")
        print("=" * 60)
        await run_odds_harvesting(p)
        await run_full_sync(session_name="Ch1 P1")
        log_audit_event("CH1_P1", "URL resolution and odds harvesting completed.", status="success")
        return True
    except Exception as e:
        print(f"  [Error] Chapter 1 Page 1 failed: {e}")
        print(f"  [Session] Football.com session marked unhealthy — Chapter 2 will be skipped.")
        log_audit_event("CH1_P1", f"Failed: {e}", status="failed")
        return False


@AIGOSuite.aigo_retry(max_retries=2, delay=3.0)
async def run_chapter_1_p2(p, scheduler: TaskScheduler = None,
                          refresh: bool = False, target_dates: list = None):
    """Chapter 1 Page 2: Predictions (Rule Engine + RL Ensemble).
    Max 1 prediction per team per 7 days — remaining scheduled for day-before."""
    log_state(chapter="Ch1 P2", action="Predictions")
    try:
        print("\n" + "=" * 60)
        print("  CHAPTER 1 PAGE 2: Predictions")
        print("=" * 60)
        await run_flashscore_analysis(p, refresh=refresh, target_dates=target_dates)
        await run_full_sync(session_name="Ch1 P2")
        log_audit_event("CH1_P2", "Predictions completed.", status="success")
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
        sync_ok = await run_full_sync(session_name="Chapter 1 Final")
        if not sync_ok:
            print("  [AIGO] Sync parity issues detected. Logged for review.")
            log_audit_event("CH1_P3_SYNC", "Sync parity issues detected.", status="partial_failure")

        await get_recommendations(save_to_file=True)
        log_audit_event("CH1_P3", "Recommendations and final sync completed.", status="success")
    except Exception as e:
        print(f"  [Error] Chapter 1 Page 3 failed: {e}")
        log_audit_event("CH1_P3", f"Failed: {e}", status="failed")


# ============================================================
# CHAPTER 2 — Betting Automation (unchanged)
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

        log_audit_event("CH2_P2", f"Withdrawal check completed. Balance: {state.get('current_balance', 'N/A')}", status="success")
        await run_full_sync(session_name="Ch2 P2 Withdrawal")
    except Exception as e:
        print(f"  [Warning] Chapter 2 Page 2 failed: {e}")
        log_audit_event("CH2_P2", f"Failed: {e}", status="failed")


# ============================================================
# SCHEDULED TASK EXECUTOR — Handles tasks from the TaskScheduler
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
                max_show = task.params.get('max_show_more', 2)
                skip_img = task.params.get('skip_images', True)
                await run_league_enricher(weekly=True)
                scheduler.complete_task(task.task_id)

            elif task.task_type == TASK_DAY_BEFORE_PREDICT:
                fid = task.params.get('fixture_id')
                if fid and p:
                    print(f"  [Scheduler] Day-before prediction for fixture {fid}")
                    await run_flashscore_analysis(p, target_fixtures=[fid])
                scheduler.complete_task(task.task_id)

            elif task.task_type == TASK_RL_TRAINING:
                print(f"  [Scheduler] Running RL training (task: {task.task_id})")
                await auto_remediate("rl")
                scheduler.complete_task(task.task_id)

        except Exception as e:
            print(f"  [Scheduler] Task {task.task_id} failed: {e}")
            scheduler.complete_task(task.task_id, status="failed")

    # Cleanup old completed/failed tasks
    scheduler.cleanup_old(days=7)


# ============================================================
# UTILITY COMMANDS — Single-shot operations, no cycle loop
# ============================================================

@AIGOSuite.aigo_retry(max_retries=2, delay=2.0)
async def run_utility(args):
    """Handle utility commands that don't require the full pipeline."""
    init_csvs()

    if args.sync:
        print("\n  --- LEO: Force Full Cloud Sync ---")
        await run_full_sync(session_name="Manual Sync")
        print("  [SUCCESS] Sync complete.")

    elif args.recommend:
        print("\n  --- LEO: Generate Recommendations ---")
        await get_recommendations(save_to_file=True)

    elif args.accuracy:
        print("\n  --- LEO: Accuracy Report ---")
        print_accuracy_report()

    elif args.search_dict:
        print("\n  --- LEO: Rebuild Search Dictionary ---")
        from Scripts.build_search_dict import main as build_search
        await build_search()

    elif args.review:
        print("\n  --- LEO: Outcome Review ---")
        async with async_playwright() as p:
            from Data.Access.outcome_reviewer import run_review_process
            await run_review_process(p)
            print_accuracy_report()

    elif args.backtest and not args.rule_engine:
        print("\n  --- LEO: Single-Pass Backtest ---")
        from Scripts.backtest_monitor import TRIGGER_FILE, CONFIG_FILE
        from Core.Intelligence.rule_config import RuleConfig
        import json
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r') as f:
                config_data = json.load(f)
            valid_keys = RuleConfig.__annotations__.keys()
            filtered = {k: v for k, v in config_data.items() if k in valid_keys}
            config = RuleConfig(**filtered)
            await run_flashscore_offline_repredict(playwright=None, custom_config=config)
        else:
            print(f"  [ERROR] Config file not found: {CONFIG_FILE}")

    elif args.streamer:
        print("\n  --- LEO: Live Score Streamer ---")
        async with async_playwright() as p:
            await live_score_streamer(p)

    elif args.schedule:
        refresh = getattr(args, 'refresh', False)
        extract_all = getattr(args, 'all', False)
        mode = "Full Deep" if extract_all else ("Refresh" if refresh else "Extract")
        print(f"\n  --- LEO: Schedule {mode} ---")
        async with async_playwright() as p:
            # If redo/all requested, we MUST refresh today effectively
            await run_flashscore_schedule_only(p, refresh=refresh or extract_all, extract_all=extract_all, target_dates=getattr(args, 'date', None))

    elif args.enrich:
        print("\n  --- LEO: Manual Metadata Enrichment ---")
        await enrich_all_schedules(extract_standings=True, league_page=True)
        await run_full_sync(session_name="Manual Enrich")

    elif args.rule_engine:
        from Core.Intelligence.rule_engine_manager import RuleEngineManager

        if args.list:
            print("\n  --- LEO: Rule Engine Registry ---")
            RuleEngineManager.print_engine_list()

        elif args.set_default:
            target = args.set_default
            # Try by name first, then by ID
            engines = RuleEngineManager.list_engines()
            engine_id = None
            for e in engines:
                if e["name"].lower() == target.lower() or e["id"] == target:
                    engine_id = e["id"]
                    break
            if engine_id and RuleEngineManager.set_default(engine_id):
                engine = RuleEngineManager.get_engine(engine_id)
                print(f"\n  ✅ Default engine set to: {engine['name']}")
                RuleEngineManager.print_engine(engine)
            else:
                print(f"\n  ❌ Engine '{target}' not found.")
                RuleEngineManager.print_engine_list()

        elif args.backtest:
            from Core.Intelligence.progressive_backtester import run_progressive_backtest
            engine_id = args.id or RuleEngineManager.get_default()["id"]
            start_date = args.from_date or "2025-08-01"
            await run_progressive_backtest(engine_id, start_date)

        else:
            # Default: show current default engine
            print("\n  --- LEO: Default Rule Engine ---")
            engine = RuleEngineManager.get_default()
            RuleEngineManager.print_engine(engine)

    elif args.assets:
        print("\n  --- LEO: Sync Team & League Assets + Region Flags ---")
        limit = getattr(args, 'limit', None)
        sync_team_assets(limit=limit)
        sync_league_assets(limit=limit)
        sync_region_flags()
        print("  [SUCCESS] Asset sync complete.")

    elif args.logos:
        print("\n  --- LEO: Download Football Logo Packs ---")
        limit = getattr(args, 'limit', None)
        download_all_logos(limit=limit)
        print("  [SUCCESS] Logo download complete.")

    elif args.enrich_leagues:
        print("\n  --- LEO: Flashscore League Enrichment ---")
        limit = getattr(args, 'limit', None)
        reset = getattr(args, 'reset_leagues', False) or getattr(args, 'reset', False)
        num_seasons = getattr(args, 'seasons', 0)
        all_seasons = getattr(args, 'all_seasons', False)
        await run_league_enricher(limit=limit, reset=reset,
                                  num_seasons=num_seasons, all_seasons=all_seasons)

    elif args.upgrade_crests:
        print("\n  --- LEO: Upgrade Team Crests to HQ Logos ---")
        limit = getattr(args, 'limit', None)
        upgrade_all_crests(limit=limit)

    elif args.train_rl:
        print("\n  --- LEO: RL Model Training ---")
        from Core.Intelligence.rl.trainer import RLTrainer
        trainer = RLTrainer()
        league_id = getattr(args, 'league', None)
        if league_id:
            print(f"  [RL] Fine-tuning league adapter: {league_id}")
            # Load existing model, then fine-tune specific league
            trainer.load()
            trainer.train_from_fixtures()  # Full retrain with league focus
        else:
            print("  [RL] Full chronological training from historical fixtures...")
            trainer.train_from_fixtures()
        print("  [SUCCESS] RL training complete.")


# ============================================================
# DISPATCH — Routes CLI args to the appropriate functions
# ============================================================

async def dispatch(args):
    """Route CLI arguments to the correct execution path."""
    init_csvs()

    async with async_playwright() as p:
        # --- Prologue ---
        if args.prologue:
            if args.page == 1:
                await run_prologue_p1()
            elif args.page == 2:
                await run_prologue_p2()
            elif args.page == 3:
                await run_prologue_p3()
            else:
                # All prologue pages sequentially (P1 + P2 + P3)
                await run_prologue_p1()
                await run_prologue_p2()
                await run_prologue_p3()
            return

        # --- Chapter ---
        if args.chapter == 1:
            if args.page == 1:
                await run_chapter_1_p1(p)
            elif args.page == 2:
                await run_chapter_1_p2(p, refresh=getattr(args, 'refresh', False) or getattr(args, 'all', False), target_dates=getattr(args, 'date', None))
            elif args.page == 3:
                await run_chapter_1_p3()
            else:
                fb_healthy = await run_chapter_1_p1(p)
                await run_chapter_1_p2(p, refresh=getattr(args, 'refresh', False) or getattr(args, 'all', False), target_dates=getattr(args, 'date', None))
                await run_chapter_1_p3()
            return

        if args.chapter == 2:
            if args.page == 1:
                await run_chapter_2_p1(p)
            elif args.page == 2:
                await run_chapter_2_p2(p)
            else:
                await run_chapter_2_p1(p)
                await run_chapter_2_p2(p)
            return

    # Should not reach here if --prologue or --chapter was set
    print("[ERROR] Unknown dispatch target.")


# ============================================================
# MAIN — Full autonomous cycle loop (v7.0)
# ============================================================

@AIGOSuite.aigo_retry(max_retries=2, delay=60.0, use_aigo=False)
async def main():
    """Full cycle: Startup → Prologue (Data Gates) → Ch1 (Predictions) → Ch2 (Betting).
    Dynamic scheduling replaces fixed 6hr sleep."""
    # Singleton Check
    if os.path.exists(LOCK_FILE):
        try:
            with open(LOCK_FILE, "r") as f:
                old_pid = int(f.read().strip())
                import psutil
                if psutil.pid_exists(old_pid):
                    print(f"   [System Error] Leo is already running (PID: {old_pid}).")
                    sys.exit(1)
        except: pass

    with open(LOCK_FILE, "w") as f:
        f.write(str(os.getpid()))

    try:
        # ── STARTUP: DB + Supabase sync (must complete before streamer) ──
        startup_ok = await run_startup_sync()
        if not startup_ok:
            print("   [FATAL] Startup sync failed. Retrying in 60s...")
            await asyncio.sleep(60)
            startup_ok = await run_startup_sync()

        # ── Initialize scheduler ──
        scheduler = TaskScheduler()
        scheduler.schedule_weekly_enrichment()  # Ensure next Monday 2:26am is scheduled
        scheduler.cleanup_old()

        async with async_playwright() as p:
            # ── Start live streamer AFTER startup sync ──
            async def _isolated_streamer():
                async with async_playwright() as streamer_pw:
                    import tempfile
                    import shutil
                    temp_dir = tempfile.mkdtemp(prefix="leo_streamer_")
                    try:
                        await live_score_streamer(streamer_pw, user_data_dir=temp_dir)
                    finally:
                        shutil.rmtree(temp_dir, ignore_errors=True)

            streamer_task = asyncio.create_task(_isolated_streamer())

            while True:
                try:
                    state["cycle_count"] += 1
                    state["cycle_start_time"] = dt.now()
                    cycle_num = state["cycle_count"]
                    log_state(chapter="Cycle Start", action=f"Starting Cycle #{cycle_num}")
                    log_audit_event("CYCLE_START", f"Cycle #{cycle_num} initiated.")

                    # ── SCHEDULED TASKS (weekly enrichment, day-before predictions) ──
                    await execute_scheduled_tasks(scheduler, p)

                    # ── PROLOGUE: Data Readiness Gates ──
                    print("\n" + "=" * 60)
                    print("  📋 PROLOGUE: Data Readiness Gates")
                    print("=" * 60)
                    await run_prologue_p1()   # Leagues >= 90% + Teams >= 5/league
                    await run_prologue_p2()   # 2+ seasons of fixtures
                    await run_prologue_p3()   # RL adapters trained

                    # ── CHAPTER 1: Prediction Pipeline ──
                    print("\n" + "=" * 60)
                    print("  ⚡ CHAPTER 1: Prediction Pipeline")
                    print("=" * 60)
                    fb_healthy = await run_chapter_1_p1(p)    # URL Resolution + Odds
                    await run_chapter_1_p2(p, scheduler=scheduler)  # Predictions
                    await run_chapter_1_p3()                   # Recommendations + Final Sync

                    # ── CHAPTER 2: Betting Automation ──
                    if fb_healthy:
                        await run_chapter_2_p1(p)
                        await run_chapter_2_p2(p)
                    else:
                        print("\n" + "=" * 60)
                        print("  CHAPTER 2: SKIPPED — Football.com session unhealthy")
                        print("=" * 60)
                        log_audit_event("CH2_SKIPPED", "Skipped: Football.com session failed.", status="skipped")

                    # ── SCHEDULE NEXT TASKS ──
                    scheduler.schedule_weekly_enrichment()  # Ensure next week is scheduled

                    # ── CYCLE COMPLETE — Dynamic sleep ──
                    log_audit_event("CYCLE_COMPLETE", f"Cycle #{cycle_num} finished.")

                    next_wake = scheduler.next_wake_time()
                    if next_wake:
                        from Core.Utils.constants import now_ng
                        sleep_secs = max(60, (next_wake - now_ng()).total_seconds())
                        sleep_hrs = sleep_secs / 3600
                        # Cap at default cycle hours if next task is too far away
                        if sleep_hrs > DEFAULT_CYCLE_HOURS:
                            sleep_secs = DEFAULT_CYCLE_HOURS * 3600
                            sleep_hrs = DEFAULT_CYCLE_HOURS
                        print(f"\n   [System] Cycle #{cycle_num} finished at {dt.now().strftime('%H:%M:%S')}. "
                              f"Next task at {next_wake.strftime('%Y-%m-%d %H:%M')}. Sleeping {sleep_hrs:.1f}h...")
                    else:
                        sleep_secs = DEFAULT_CYCLE_HOURS * 3600
                        print(f"\n   [System] Cycle #{cycle_num} finished at {dt.now().strftime('%H:%M:%S')}. "
                              f"Sleeping {DEFAULT_CYCLE_HOURS}h...")

                    await asyncio.sleep(sleep_secs)

                except Exception as e:
                    state["error_log"].append(f"{dt.now()}: {e}")
                    print(f"[ERROR] Main loop: {e}")
                    log_audit_event("CYCLE_ERROR", f"Unhandled: {e}", status="failed")
                    await asyncio.sleep(60)
    finally:
        if os.path.exists(LOCK_FILE): os.remove(LOCK_FILE)


@AIGOSuite.aigo_retry(max_retries=2, delay=10.0)
async def main_offline_repredict():
    """Run offline reprediction."""
    print("    --- LEO: Offline Reprediction Mode ---      ")
    init_csvs()
    async with async_playwright() as p:
        try:
            await run_flashscore_offline_repredict(p)
        except Exception as e:
            print(f"[ERROR] Offline repredict: {e}")


# ============================================================
# ENTRY POINT — CLI Dispatcher
# ============================================================

if __name__ == "__main__":
    args = parse_args()
    log_file, original_stdout, original_stderr = setup_terminal_logging(args)

    # Determine which mode to run
    is_utility = any([args.sync, args.recommend, args.accuracy,
                      args.search_dict, args.review, args.backtest,
                      args.rule_engine, args.streamer, args.schedule,
                      args.enrich, args.assets,
                      args.logos, args.enrich_leagues, args.upgrade_crests,
                      args.train_rl])
    is_granular = args.prologue or args.chapter is not None

    try:
        if args.offline_repredict:
            asyncio.run(main_offline_repredict())
        elif is_utility:
            asyncio.run(run_utility(args))
        elif is_granular:
            asyncio.run(dispatch(args))
        else:
            asyncio.run(main())
    except KeyboardInterrupt:
        print("\n   --- LEO: Shutting down. ---")
    finally:
        sys.stdout, sys.stderr = original_stdout, original_stderr
        log_file.close()
