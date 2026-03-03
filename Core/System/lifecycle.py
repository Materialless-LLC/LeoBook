# lifecycle.py: lifecycle.py: Global state management, CLI parsing, and application lifecycle control.
# Part of LeoBook Core — System
#
# Functions: log_state(), log_audit_state(), setup_terminal_logging(), parse_args()

import os
import sys
import argparse
import uuid
from pathlib import Path
from datetime import datetime as dt
from Core.Utils.constants import DEFAULT_STAKE
from Core.Utils.utils import Tee

_current_dir = Path(__file__).parent.absolute()
LOG_DIR = _current_dir.parent.parent / "Data" / "Logs"

state = {
    "cycle_start_time": None, 
    "cycle_count": 0,
    "current_chapter": "Startup",
    "last_action": "Init",
    "next_expected": "Startup Checks",
    "why_this_step": "System initialization",
    "expected_outcome": "Ready to start",
    "ai_server_ready": False,
    "llm_needed_for_this_cycle": False, 
    "pending_count": 0,
    "booked_this_cycle": 0,
    "failed_this_cycle": 0,
    "current_balance": 0.0,
    "last_win_amount": 5000.0 * DEFAULT_STAKE, # Scalable
    "error_log": []
}

def log_state(chapter=None, action=None, next_step=None, why=None, expect=None):
    """Updates and prints the current system state."""
    global state
    if chapter: state["current_chapter"] = chapter
    if action: state["last_action"] = action
    if next_step: state["next_expected"] = next_step
    if why: state["why_this_step"] = why
    if expect: state["expected_outcome"] = expect
    
    print(f"   [STATE] {state['current_chapter']} | Done: {state['last_action']} | Next: {state['next_expected']} | Why: {state['why_this_step']}")

def log_audit_state(chapter: str, action: str, details: str = ""):
    """Central state logger — prints to console and appends to audit_log.csv"""
    timestamp = dt.now().strftime("%Y-%m-%d %H:%M:%S")
    message = f"[{timestamp}] [STATE] {chapter} | Action: {action} | {details}"
    print(message)
    
    from Data.Access.db_helpers import append_to_csv
    append_to_csv("audit_log.csv", {
        "id": str(uuid.uuid4()),
        "timestamp": timestamp,
        "event_type": "STATE",
        "description": f"{chapter} - {action} - {details}",
        "balance_before": "",
        "balance_after": "",
        "stake": "",
        "status": "INFO"
    })

def setup_terminal_logging(args):
    """Sets up Tee logging to file with dynamic prefixes."""
    # Set timeout
    if args:
        os.environ["PLAYWRIGHT_TIMEOUT"] = "3600000"

    # Determine prefix
    prefix = "leo_session"
    if args:
        if args.sync: prefix = "leo_sync_session"
        elif args.recommend: prefix = "leo_recommend_session"
        elif args.accuracy: prefix = "leo_accuracy_session"
        elif args.search_dict: prefix = "leo_search_session"
        elif args.review: prefix = "leo_review_session"
        elif args.backtest: prefix = "leo_backtest_session"
        elif args.offline_repredict: prefix = "leo_offline_repredict_session"
        elif args.rule_engine: prefix = "leo_rule_engine_session"
        elif args.streamer: prefix = "leo_streamer_session"
        elif args.enrich: prefix = "leo_enrich_session"
        elif args.schedule: prefix = "leo_schedule_session"
        elif args.prologue: prefix = "leo_prologue_session"
        elif args.chapter: prefix = f"leo_chapter{args.chapter}_session"
        elif args.assets: prefix = "leo_assets_session"
        elif args.logos: prefix = "leo_logos_session"
        elif args.enrich_leagues: prefix = "leo_enrich_leagues_session"
        elif args.upgrade_crests: prefix = "leo_upgrade_crests_session"

    TERMINAL_LOG_DIR = LOG_DIR / "Terminal"
    TERMINAL_LOG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = dt.now().strftime("%Y%m%d_%H%M%S")
    log_file_path = TERMINAL_LOG_DIR / f"{prefix}_{timestamp}.log"

    log_file = open(log_file_path, "w", encoding="utf-8")
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    sys.stdout = Tee(original_stdout, log_file)
    sys.stderr = Tee(original_stderr, log_file)
    
    return log_file, original_stdout, original_stderr

def parse_args():
    """
    Unified CLI for LeoBook. Leo.py is the single entry point.

    Usage examples:
      python Leo.py                       # Full cycle (Prologue → Ch1 → Ch2 → Ch3, loop)
      python Leo.py --prologue            # All prologue pages only
      python Leo.py --prologue --page 1   # Prologue Page 1 only (Sync + Review)
      python Leo.py --chapter 1           # Full Chapter 1
      python Leo.py --chapter 1 --page 2  # Ch1 Page 2 only (Odds Harvesting)
      python Leo.py --sync                # Force full cloud sync
      python Leo.py --recommend           # Generate recommendations only
      python Leo.py --accuracy            # Print accuracy report
    """
    parser = argparse.ArgumentParser(
        description="LeoBook Prediction System — Unified Orchestrator (v3.0)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python Leo.py                            Full cycle (loop)
  python Leo.py --prologue                 All prologue pages (P1+P2)
  python Leo.py --prologue --page 1        Prologue P1: Cloud Handshake & Review
  python Leo.py --prologue --page 2        Prologue P2: Accuracy & Sync
  python Leo.py --chapter 1                Full Chapter 1 (Extraction → Odds → Sync)
  python Leo.py --chapter 1 --page 1       Ch1 P1: Flashscore Extraction & Analysis
  python Leo.py --chapter 1 --page 2       Ch1 P2: Odds Harvesting
  python Leo.py --chapter 1 --page 3       Ch1 P3: Final Sync & Recommendations
  python Leo.py --chapter 2                Full Chapter 2 (Booking & Withdrawal)
  python Leo.py --chapter 2 --page 1       Ch2 P1: Automated Booking
  python Leo.py --chapter 2 --page 2       Ch2 P2: Funds & Withdrawal Check
  python Leo.py --chapter 3                Chapter 3: Monitoring & Oversight
  python Leo.py --sync                     Force full cloud sync
  python Leo.py --recommend                Generate and display recommendations only
  python Leo.py --accuracy                 Print accuracy report only
  python Leo.py --search-dict              Rebuild the search dictionary from SQLite
  python Leo.py --review                   Run outcome review process only
  python Leo.py --backtest                 Run a single-pass backtest check
  python Leo.py --offline-repredict        Offline reprediction mode
  python Leo.py --streamer                 Run the live score streamer independently
  python Leo.py --schedule                 Extract schedules only (no predictions)
  python Leo.py --schedule --refresh       Re-extract schedules starting from today
  python Leo.py --schedule --all           Extract today's schedules + H2H + standings
  python Leo.py --schedule --refresh --all Extract 7 days of schedules + H2H + standings
  python Leo.py --enrich                   Manual metadata enrichment (gap-fill)
  python Leo.py --rule-engine              Show default rule engine info (combine with --list, --set-default, --backtest)
  python Leo.py --rule-engine --list       List all saved rule engines
  python Leo.py --rule-engine --backtest   Progressive backtest default engine
  python Leo.py --rule-engine --backtest --id ENGINE_ID   Backtest a specific engine
  python Leo.py --rule-engine --backtest --from-date 2025-08-01   Set start date
  python Leo.py --rule-engine --set-default "James' Law"   Set engine as default
  python Leo.py --assets                   Sync all team and league assets
  python Leo.py --assets --limit 10         Sync assets with a limit
  python Leo.py --logos                     Download all football team logo packs
  python Leo.py --logos --limit 5           Download first 5 league logo packs
  python Leo.py --enrich-leagues            Extract Flashscore league pages -> SQLite
  python Leo.py --enrich-leagues --limit 5  Extract first 5 unprocessed leagues
  python Leo.py --enrich-leagues --reset    Reset and extract all leagues
  python Leo.py --enrich-leagues --seasons 2 Extract last 2 seasons per league
  python Leo.py --enrich-leagues --all-seasons Extract all available seasons
  python Leo.py --train-rl               Train RL model from historical fixtures
  python Leo.py --train-rl --league ID   Fine-tune a specific league adapter
        """
    )
    # --- Granular Chapter / Page Selection ---
    parser.add_argument('--prologue', action='store_true',
                       help='Run all Prologue pages (P1+P2)')
    parser.add_argument('--chapter', type=int, choices=[1, 2, 3], metavar='N',
                       help='Run a specific chapter (1, 2, or 3)')
    parser.add_argument('--page', type=int, choices=[1, 2, 3], metavar='N',
                       help='Run a specific page within --prologue or --chapter')

    # --- Utility Commands ---
    parser.add_argument('--sync', action='store_true',
                       help='Force a full cloud sync (bi-directional)')
    parser.add_argument('--recommend', action='store_true',
                       help='Generate and display recommendations only')
    parser.add_argument('--accuracy', action='store_true',
                       help='Print accuracy report only')
    parser.add_argument('--search-dict', action='store_true',
                       help='Rebuild the search dictionary from SQLite')
    parser.add_argument('--review', action='store_true',
                       help='Run outcome review process only')
    parser.add_argument('--backtest', action='store_true',
                       help='Run a single-pass backtest check')
    parser.add_argument('--offline-repredict', action='store_true',
                       help='Run offline reprediction using stored data')
    parser.add_argument('--streamer', action='store_true',
                       help='Run the live score streamer independently')
    parser.add_argument('--schedule', action='store_true',
                       help='Extract match schedules only (no predictions)')
    parser.add_argument('--refresh', action='store_true',
                       help='Force re-extract from today (use with --schedule)')
    parser.add_argument('--all', action='store_true',
                       help='Also extract H2H + standings per match (use with --schedule)')
    parser.add_argument('--enrich', action='store_true',
                       help='Run manual metadata enrichment (gap-fill for historical data)')
    parser.add_argument('--assets', action='store_true',
                       help='Sync team and league assets (crests/logos) to Supabase Storage')
    parser.add_argument('--limit', type=int, metavar='N',
                       help='Limit the number of items processed (useful for testing)')
    parser.add_argument('--logos', action='store_true',
                       help='Download football team logo packs from football-logos.cc')
    parser.add_argument('--enrich-leagues', action='store_true',
                       help='Extract Flashscore league pages -> SQLite')
    parser.add_argument('--reset-leagues', action='store_true',
                       help='Reset all leagues to unprocessed (use with --enrich-leagues)')
    parser.add_argument('--seasons', type=int, default=0, metavar='N',
                       help='Number of past seasons to extract (use with --enrich-leagues)')
    parser.add_argument('--all-seasons', action='store_true',
                       help='Extract all available seasons (use with --enrich-leagues)')
    parser.add_argument('--upgrade-crests', action='store_true',
                        help='Upgrade team crests to high-quality logos from Modules/Assets/logos')

    # --- RL Training ---
    parser.add_argument('--train-rl', action='store_true',
                        help='Train/retrain the RL model from historical fixtures')
    parser.add_argument('--league', type=str, metavar='ID',
                        help='Fine-tune a specific league adapter (use with --train-rl)')

    # --- Rule Engine Management ---
    parser.add_argument('--rule-engine', action='store_true',
                       help='Show default rule engine info (combine with --list, --set-default, --backtest)')
    parser.add_argument('--list', action='store_true',
                       help='List all saved rule engines (use with --rule-engine)')
    parser.add_argument('--set-default', type=str, metavar='NAME',
                       help='Set a rule engine as default by name or ID (use with --rule-engine)')
    parser.add_argument('--id', type=str, metavar='ENGINE_ID',
                       help='Target a specific engine by ID (use with --rule-engine --backtest)')
    parser.add_argument('--from-date', type=str, metavar='DATE',
                       help='Start date for backtest YYYY-MM-DD (use with --rule-engine --backtest)')
    parser.add_argument('--date', type=str, nargs='+', metavar='DATE',
                       help='Specific date(s) to process (DD.MM.YYYY)')

    # --- Validation ---
    args = parser.parse_args()
    if args.page and not args.prologue and args.chapter is None:
        parser.error("--page requires --prologue or --chapter")
    if args.list and not args.rule_engine:
        parser.error("--list requires --rule-engine")
    if args.set_default and not args.rule_engine:
        parser.error("--set-default requires --rule-engine")
    if args.refresh and not args.schedule:
        parser.error("--refresh requires --schedule")
    if getattr(args, 'all', False) and not args.schedule:
        parser.error("--all requires --schedule")
    if args.league and not args.train_rl:
        parser.error("--league requires --train-rl")
    return args

