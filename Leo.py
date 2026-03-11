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
from Modules.Flashscore.fs_live_streamer import live_score_streamer
from Modules.FootballCom.fb_manager import run_odds_harvesting, run_automated_booking
from Scripts.recommend_bets import get_recommendations
from Core.Intelligence.prediction_pipeline import run_predictions, get_weekly_fixtures
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
    """Startup: Ensure local DB exists, then push-only sync.
    Auto-bootstraps from Supabase if local DB is missing or empty."""
    log_state(chapter="Startup", action="DB Initialization & Push-Only Sync")
    try:
        print("\n" + "=" * 60)
        print("  STARTUP: Database Initialization & Push-Only Sync")
        print("=" * 60)

        # Initialize local SQLite DB (creates tables if missing)
        init_csvs()
        conn = init_db()

        # Check if DB is effectively empty (auto-bootstrap detection)
        try:
            sched_count = conn.execute("SELECT COUNT(*) FROM schedules").fetchone()[0]
        except Exception:
            sched_count = 0

        if sched_count == 0:
            print("     [!] Local DB empty — will bootstrap from Supabase automatically")

        # Push-only sync (bootstraps empty tables from Supabase)
        sync_mgr = SyncManager()
        await sync_mgr.sync_on_startup()

        log_audit_event("STARTUP", "DB initialized and push-only sync completed.", status="success")
        print("  [Startup] ✓ Complete")
        return True
    except Exception as e:
        print(f"  [Error] Startup sync failed: {e}")
        log_audit_event("STARTUP", f"Failed: {e}", status="failed")
        return False


# ============================================================
# PROLOGUE — Data Readiness Gates (P1-P3)
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
        # Non-blocking; gates will re-check and fail if still not ready

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
    [DISABLED] RL is currently bypassed to focus strictly on Rule Engine."""
    print("\n" + "=" * 60)
    print("  PROLOGUE P3: RL Adapter Check [DISABLED]")
    print("=" * 60)
    print("    Skipping RL readiness check as requested.")


# ============================================================
# CHAPTER 1 — Prediction Pipeline
# ============================================================

@AIGOSuite.aigo_retry(max_retries=2, delay=3.0)
async def run_chapter_1_p1(p):
    """Chapter 1 Page 1: Smart SearchDict + URL Resolution & Odds Harvesting.
    V7: Schedules already exist from enrichment. No Flashscore browser scraping.
    1. Smart SearchDict: enrich only this week's teams that need it
    2. Football.com URL resolution + odds extraction (no login)"""
    log_state(chapter="Ch1 P1", action="URL Resolution & Odds Harvesting")
    try:
        print("\n" + "=" * 60)
        print("  CHAPTER 1 PAGE 1: URL Resolution & Odds Harvesting")
        print("=" * 60)

        # --- Smart SearchDict: DISABLED (enrichment done separately) ---
        print("    [SearchDict] Skipped (disabled).")

        # --- Football.com Odds Harvesting (no login) ---
        await run_odds_harvesting(p)
        log_audit_event("CH1_P1", "URL resolution and odds harvesting completed.", status="success")
        return True
    except Exception as e:
        print(f"  [Error] Chapter 1 Page 1 failed: {e}")
        print(f"  [Session] Football.com session marked unhealthy — Chapter 2 will be skipped.")
        log_audit_event("CH1_P1", f"Failed: {e}", status="failed")
        return False


@AIGOSuite.aigo_retry(max_retries=2, delay=3.0)
async def run_chapter_1_p2(p=None, scheduler: TaskScheduler = None,
                          refresh: bool = False, target_dates: list = None):
    """Chapter 1 Page 2: Predictions (Rule Engine + RL Ensemble).
    V7: Pure DB computation — NO browser. H2H + form + standings from schedules table.
    Max 1 prediction per team per 7 days — remaining scheduled for day-before."""
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
    """Chapter 1 Page 3: Recommendations & Final Sync.
    V7: Rank predictions by reliability, flag top for Ch2, then watermark delta sync."""
    log_state(chapter="Ch1 P3", action="Recommendations & Final Sync")
    try:
        print("\n" + "=" * 60)
        print("  CHAPTER 1 PAGE 3: Recommendations & Final Sync")
        print("=" * 60)

        # 1. Generate recommendations (ranked by recommendation_score)
        await get_recommendations(save_to_file=True)

        # 2. Watermark delta sync (single sync for the entire chapter)
        sync_ok = await run_full_sync(session_name="Chapter 1 Final")
        if not sync_ok:
            print("  [AIGO] Sync parity issues detected. Logged for review.")
            log_audit_event("CH1_P3_SYNC", "Sync parity issues detected.", status="partial_failure")

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
        print("\n  --- LEO: Force Push-Only Sync ---")
        await run_full_sync(session_name="Manual Sync")
        print("  [SUCCESS] Sync complete.")

    elif getattr(args, 'reset_sync', None):
        print(f"\n  --- LEO: Reset Sync Watermark [{args.reset_sync}] ---")
        conn = init_db()
        table = args.reset_sync.lower()
        conn.execute("DELETE FROM _sync_watermarks WHERE table_name = ?", (table,))
        conn.commit()
        print(f"  [SUCCESS] Watermark for '{table}' reset. Run with --sync to push all rows.")

    elif getattr(args, 'pull', False):
        print("\n  --- LEO: FORCE FULL PULL -- Supabase -> local SQLite ---")
        init_db()
        sync_mgr = SyncManager()
        from Data.Access.sync_manager import TABLE_CONFIG
        print("   [SYNC] Force Full Pull -- Supabase -> local SQLite...")
        total = 0
        for table_key in TABLE_CONFIG:
            pulled = await sync_mgr.batch_pull(table_key)
            total += pulled
        print(f"\n  [SUCCESS] Total pulled: {total:,} rows across {len(TABLE_CONFIG)} tables")

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

    elif args.streamer:
        print("\n  --- LEO: Live Score Streamer ---")
        async with async_playwright() as p:
            await live_score_streamer(p)

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
        limit = getattr(args, '_limit_count', None)
        offset = getattr(args, '_limit_offset', 0)
        reset = getattr(args, 'reset_leagues', False) or getattr(args, 'reset', False)
        refresh = getattr(args, 'refresh_leagues', False) or getattr(args, 'refresh', False)
        num_seasons = getattr(args, 'seasons', 0)
        all_seasons = getattr(args, 'all_seasons', False)
        target_season = getattr(args, 'season', None)
        await run_league_enricher(limit=limit, offset=offset, reset=reset,
                                  num_seasons=num_seasons, all_seasons=all_seasons,
                                  target_season=target_season, refresh=refresh)

    elif args.upgrade_crests:
        print("\n  --- LEO: Upgrade Team Crests to HQ Logos ---")
        limit = getattr(args, 'limit', None)
        upgrade_all_crests(limit=limit)

    elif args.train_rl:
        print("\n  --- LEO: RL Model Training ---")
        from Core.Intelligence.rl.trainer import RLTrainer
        trainer = RLTrainer()
        phase = getattr(args, 'phase', 1)
        cold = getattr(args, 'cold', False)
        resume = getattr(args, 'resume', False)
        limit = getattr(args, '_limit_count', None) # Use --limit for days if needed
        
        league_id = getattr(args, 'league', None)
        if league_id:
            print(f"  [RL] League-specific training: {league_id} (Phase {phase})")
            trainer.load()
            trainer.train_from_fixtures(phase=phase, cold=cold, limit_days=limit, resume=resume)
        else:
            print(f"  [RL] Starting Phase {phase} training...")
            if phase > 1:
                trainer.load()
            trainer.train_from_fixtures(phase=phase, cold=cold, limit_days=limit, resume=resume)
        print("  [SUCCESS] RL training session complete.")

    elif getattr(args, 'push_models', False):
        print("\n  --- LEO: Push Models → Supabase Storage ---")
        from Data.Access.model_sync import ModelSync
        ModelSync(skip_large=getattr(args, 'skip_large', False)).push()

    elif getattr(args, 'pull_models', False):
        print("\n  --- LEO: Pull Models ← Supabase Storage ---")
        from Data.Access.model_sync import ModelSync
        ModelSync().pull()

    elif args.backtest_rl:
        print("\n  --- LEO: RL Walk-Forward Backtest ---")
        from Core.Intelligence.rl.backtest import WalkForwardBacktester
        from Core.Utils.constants import now_ng
        conn = init_db()
        bt_end = args.bt_end or now_ng().strftime("%Y-%m-%d")
        bt = WalkForwardBacktester(
            conn,
            train_days=args.bt_train_days,
            eval_days=1,
        )
        summary = bt.run(args.bt_start, bt_end)
        bt._write_report(args.bt_output)
        print(f"  [Backtest] Report written to {args.bt_output}")

    elif args.paper_summary:
        print("\n  --- LEO: Paper Trading Log Summary ---")
        from Data.Access.db_helpers import get_paper_trading_summary
        from Core.Utils.constants import now_ng
        conn = init_db()
        summary = get_paper_trading_summary(conn)
        now_str = now_ng().strftime("%Y-%m-%d %H:%M WAT")

        print(f"\n  {'═' * 50}")
        print(f"  PAPER TRADING LOG SUMMARY")
        print(f"  As of: {now_str}")
        print(f"  {'═' * 50}")
        print(f"\n  Total trades logged:    {summary['total_trades']}")
        print(f"  Reviewed (settled):     {summary['reviewed_trades']}")
        print(f"  Pending outcome:        {summary['pending_review']}")
        print(f"\n  ACCURACY")
        print(f"    All trades:           {summary['accuracy']:.1f}%")
        print(f"    Gated trades only:    {summary['gated_accuracy']:.1f}%")
        print(f"\n  SIMULATED P&L (NGN)")
        print(f"    Total P&L:            ₦{summary['total_simulated_pl']:,.2f}")
        print(f"    ROI:                  {summary['roi']:.1f}%")
        print(f"    Avg stake:            ₦{summary['avg_stake']:,.0f}")

        # Top 5 markets by count
        sorted_markets = sorted(
            summary.get('by_market', {}).items(),
            key=lambda x: x[1]['count'], reverse=True
        )[:5]
        if sorted_markets:
            print(f"\n  TOP 5 MARKETS (by trade count)")
            print(f"    {'market_key':<18}| {'trades':>6} | {'accuracy':>8} | {'total_pl':>12}")
            print(f"    {'─'*18}|{'─'*8}|{'─'*10}|{'─'*14}")
            for mk, stats in sorted_markets:
                print(f"    {mk:<18}| {stats['count']:>6} | {stats['accuracy']:>7.1f}% | ₦{stats['total_pl']:>10,.2f}")

        print(f"\n  {'═' * 50}")
        print(f"  ⚠ SIMULATED RESULTS. No real money was staked.")
        print(f"  ⚠ P&L uses live odds where available, synthetic")
        print(f"    odds as fallback. Treat as directional only.")
        print(f"  {'═' * 50}")


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

    # Should not reach here if --prologue or --chapter was set
    print("[ERROR] Unknown dispatch target.")


# ============================================================
# MAIN — Full autonomous cycle loop (v7.0)
# ============================================================

@AIGOSuite.aigo_retry(max_retries=2, delay=60.0, use_aigo=False)
async def main():
    """Entry point for the Autonomous Supervisor."""
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
        from Core.System.supervisor import Supervisor
        supervisor = Supervisor()
        await supervisor.run()
    finally:
        if os.path.exists(LOCK_FILE): 
            os.remove(LOCK_FILE)




# ============================================================
# ENTRY POINT — CLI Dispatcher
# ============================================================

if __name__ == "__main__":
    args = parse_args()
    log_file, original_stdout, original_stderr = setup_terminal_logging(args)

    # Determine which mode to run
    is_utility = any([args.sync, getattr(args, 'pull', False),
                      getattr(args, 'reset_sync', None),
                      args.recommend, args.accuracy,
                      args.search_dict, args.review,
                      args.rule_engine, args.streamer,
                      args.assets,
                      args.logos, args.enrich_leagues, args.upgrade_crests,
                      args.train_rl, args.backtest_rl, args.paper_summary,
                      getattr(args, 'push_models', False),
                      getattr(args, 'pull_models', False)])
    is_granular = args.prologue or args.chapter is not None

    try:
        if args.dry_run:
            from Core.System.guardrails import enable_dry_run
            enable_dry_run()

        # ── STARTUP: DB + Supabase sync (must complete before streamer) ──
        if args.data_quality:
            from Core.System.data_quality import DataQualityScanner
            from Core.System.gap_resolver import GapResolver
            print("\n[Data Quality] Running Diagnostics...")
            report_path = DataQualityScanner.produce_gap_report()
            print(f"  [Scanner] Report generated: {report_path}")
            
            print("\n[Data Quality] Resolving Immediate Gaps...")
            stats = GapResolver.resolve_immediate()
            
            print("\n[Data Quality] Staging Enrichment Gaps...")
            all_gaps = []
            for table in ("leagues", "teams", "schedules"):
                all_gaps.extend(DataQualityScanner.scan_table(table))
            staged = GapResolver.stage_enrichment(all_gaps)
            
            print("\n[Data Quality] Refreshing Season Completeness...")
            from Data.Access.season_completeness import SeasonCompletenessTracker
            SeasonCompletenessTracker.bulk_compute_all()
            
            print("\n[Data Quality] Validating IDs (Placeholders/Duplicates)...")
            from Core.System.data_quality import InvalidIDScanner
            from Core.System.gap_resolver import InvalidIDResolver
            for table, id_col in [("leagues", "fs_league_id"), ("teams", "team_id")]:
                invalids = InvalidIDScanner.scan_invalid_ids(table, id_col)
                if invalids:
                    print(f"  [{table}] Detected {len(invalids)} invalid IDs.")
                    fixed = InvalidIDResolver.attempt_local_resolution(table, invalids)
                    staged = InvalidIDResolver.stage_invalid_ids(table, invalids)
                    print(f"    - Resolved locally: {fixed}")
                    print(f"    - Staged CRITICAL:  {staged}")
            
            sys.exit(0)

        if args.season_completeness:
            from Data.Access.season_completeness import SeasonCompletenessTracker
            from Data.Access.league_db import get_connection
            print("\n[Season Completeness] Refreshing metrics...")
            SeasonCompletenessTracker.bulk_compute_all()
            
            conn = get_connection()
            rows = conn.execute("""
                SELECT league_id, season, total_expected_matches, total_scanned_matches, 
                       completeness_pct, season_status 
                FROM season_completeness 
                ORDER BY completeness_pct ASC
            """).fetchall()
            
            print("\n" + "=" * 80)
            print(f"{'LEAGUE_ID':<15} | {'SEASON':<8} | {'EXP':<4} | {'SCAN':<4} | {'%':<6} | {'STATUS'}")
            print("-" * 80)
            for r in rows:
                print(f"{r[0]:<15} | {r[1]:<8} | {r[2]:<4} | {r[3]:<4} | {r[4]:>5.1f}% | {r[5]}")
            print("=" * 80)
            sys.exit(0)

        if args.set_expected_matches:
            from Data.Access.league_db import get_connection
            league_id, season, count = args.set_expected_matches
            conn = get_connection()
            conn.execute("""
                INSERT INTO season_completeness (league_id, season, total_expected_matches)
                VALUES (?, ?, ?)
                ON CONFLICT(league_id, season) DO UPDATE SET total_expected_matches = excluded.total_expected_matches
            """, (league_id, season, int(count)))
            conn.commit()
            print(f"  [Completeness] Set expected matches for {league_id} {season} to {count}")
            sys.exit(0)

        if is_utility:
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
