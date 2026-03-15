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
from Modules.Flashscore.fs_live_streamer import live_score_streamer
from Modules.FootballCom.fb_manager import run_odds_harvesting, run_automated_booking
from Scripts.recommend_bets import get_recommendations
from Core.Intelligence.prediction_pipeline import run_predictions, get_weekly_fixtures
from Scripts.enrich_leagues import main as run_league_enricher
from Modules.Assets.asset_manager import sync_team_assets, sync_league_assets, sync_region_flags


# Configuration
DEFAULT_CYCLE_HOURS = int(os.getenv('LEO_CYCLE_WAIT_HOURS', 6))
LOCK_FILE = "leo.lock"


# ============================================================
# PAGE FUNCTIONS — Each is a self-contained async operation
# ============================================================

# ============================================================
# STARTUP — Ensures DB + Supabase tables exist, full sync
# ============================================================

from Core.System.pipeline import (  # noqa: page functions
    run_startup_sync, auto_remediate,
    run_prologue_p1, run_prologue_p2, run_prologue_p3,
    run_chapter_1_p1, run_chapter_1_p2, run_chapter_1_p3,
    run_chapter_2_p1, run_chapter_2_p2,
    execute_scheduled_tasks, dispatch,
)


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
        limit = getattr(args, '_limit_count', None)  # Use --limit for days if needed

        # ── Season scope resolution ──────────────────────────────────────────────
        # --train-season accepts: "current", "all", an integer offset (e.g. "1"),
        # or an explicit season label (e.g. "2024/2025"). Digit strings are coerced
        # to int so trainer._get_season_dates() receives the correct type.
        train_season = getattr(args, 'train_season', 'current')
        if isinstance(train_season, str) and train_season.isdigit():
            train_season = int(train_season)

        league_id = getattr(args, 'league', None)
        if league_id:
            print(f"  [RL] League-specific training: {league_id} (Phase {phase}, season={train_season!r})")
            trainer.load()
            trainer.train_from_fixtures(
                phase=phase, cold=cold, limit_days=limit,
                resume=resume, target_season=train_season,
            )
        else:
            print(f"  [RL] Starting Phase {phase} training (season={train_season!r})...")
            if phase > 1:
                trainer.load()
            trainer.train_from_fixtures(
                phase=phase, cold=cold, limit_days=limit,
                resume=resume, target_season=train_season,
            )
        print("  [SUCCESS] RL training session complete.")

    elif getattr(args, 'push_models', False):
        print("\n  --- LEO: Push Models → Supabase Storage ---")
        from Data.Access.model_sync import ModelSync
        ModelSync(
            skip_large=getattr(args, 'skip_large', False),
            all_checkpoints=getattr(args, 'all_checkpoints', False)
        ).push()

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

    elif args.diagnose_rl:
        print("\n  --- LEO: RL Decision Inspector ---")
        from Scripts.rl_diagnose import main as run_rl_diagnose
        run_rl_diagnose(args)


# ============================================================
# DISPATCH — Routes CLI args to the appropriate functions
# ============================================================

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
                      args.diagnose_rl,
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

            print("\n[Data Quality] Filling Country Codes...")
            from Data.Access.db_helpers import fill_all_country_codes
            from Data.Access.league_db import get_connection as _get_conn_dq
            _conn_dq = _get_conn_dq()
            cc_total = fill_all_country_codes(_conn_dq)
            print(f"  [CC] {cc_total} country_code rows resolved")
            
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
