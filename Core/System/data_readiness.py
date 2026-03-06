# data_readiness.py: Pre-flight data completeness checks for Leo.py Prologue.
# Part of LeoBook Core — System
#
# Functions: check_leagues_ready(), check_seasons_ready(), check_rl_ready()
# Called by Prologue P1-P3 to gate pipeline execution.

import os
import json
import logging
import asyncio
import time
from typing import Tuple, Dict

from Core.Utils.constants import now_ng
from Data.Access.league_db import init_db, query_all

logger = logging.getLogger(__name__)

# Path to leagues.json (source of truth for expected leagues)
_LEAGUES_JSON = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '..', '..', 'Config', 'leagues.json'
)


def _get_expected_league_count() -> int:
    """Count leagues defined in leagues.json."""
    try:
        with open(_LEAGUES_JSON, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, list):
            return len(data)
        elif isinstance(data, dict):
            return sum(len(v) if isinstance(v, list) else 1 for v in data.values())
    except Exception:
        pass
    return 0


def check_leagues_ready(conn=None) -> Tuple[bool, Dict]:
    """Check if leagues >= 90% of leagues.json AND teams >= 5 per processed league.

    Returns:
        (is_ready, stats_dict)
    """
    conn = conn or init_db()
    expected = _get_expected_league_count()
    actual_leagues = conn.execute("SELECT COUNT(*) FROM leagues").fetchone()[0]
    processed = conn.execute("SELECT COUNT(*) FROM leagues WHERE processed = 1").fetchone()[0]
    team_count = conn.execute("SELECT COUNT(*) FROM teams").fetchone()[0]

    # Threshold: 90% of expected leagues
    threshold = int(expected * 0.9) if expected > 0 else 1000
    leagues_ok = actual_leagues >= threshold

    # Teams: at least 5 per processed league (rough check)
    teams_per_league = (team_count / max(processed, 1)) if processed > 0 else 0
    teams_ok = teams_per_league >= 5 or team_count >= 5000

    is_ready = leagues_ok and teams_ok

    stats = {
        "expected_leagues": expected,
        "actual_leagues": actual_leagues,
        "threshold": threshold,
        "processed_leagues": processed,
        "team_count": team_count,
        "teams_per_league": round(teams_per_league, 1),
        "leagues_ok": leagues_ok,
        "teams_ok": teams_ok,
        "ready": is_ready,
    }

    if is_ready:
        print(f"  [P1 ✓] Leagues: {actual_leagues}/{expected} ({actual_leagues*100//max(expected,1)}%), "
              f"Teams: {team_count} ({teams_per_league:.0f}/league)")
    else:
        print(f"  [P1 ✗] BELOW THRESHOLD — Leagues: {actual_leagues}/{threshold} needed, "
              f"Teams: {team_count} ({teams_per_league:.0f}/league)")

    return is_ready, stats


def check_seasons_ready(conn=None, min_seasons: int = 2) -> Tuple[bool, Dict]:
    """Check if processed leagues have >= min_seasons of historical fixture data.

    Returns:
        (is_ready, stats_dict)
    """
    conn = conn or init_db()

    # Count distinct seasons per league in fixtures
    rows = conn.execute("""
        SELECT league_id, COUNT(DISTINCT season) as season_count
        FROM schedules
        WHERE season IS NOT NULL AND season != ''
        GROUP BY league_id
    """).fetchall()

    total_leagues = len(rows)
    leagues_with_enough = sum(1 for r in rows if r[1] >= min_seasons)
    pct = (leagues_with_enough * 100 // max(total_leagues, 1)) if total_leagues > 0 else 0

    # Ready if >= 80% of leagues with fixtures have enough seasons
    is_ready = pct >= 80 and total_leagues > 0

    stats = {
        "total_leagues_with_fixtures": total_leagues,
        "leagues_with_enough_seasons": leagues_with_enough,
        "min_seasons_required": min_seasons,
        "percentage": pct,
        "ready": is_ready,
    }

    if is_ready:
        print(f"  [P2 ✓] Seasons: {leagues_with_enough}/{total_leagues} leagues "
              f"have {min_seasons}+ seasons ({pct}%)")
    else:
        print(f"  [P2 ✗] BELOW THRESHOLD — Only {leagues_with_enough}/{total_leagues} leagues "
              f"have {min_seasons}+ seasons ({pct}%)")

    return is_ready, stats


def check_rl_ready() -> Tuple[bool, Dict]:
    """Check if RL model and adapters are trained.

    Returns:
        (is_ready, stats_dict)
    """
    models_dir = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        '..', '..', 'Data', 'Store', 'models'
    )
    base_model = os.path.join(models_dir, 'leobook_base.pth')
    registry_file = os.path.join(models_dir, 'adapter_registry.json')

    has_base = os.path.exists(base_model)
    has_registry = os.path.exists(registry_file)

    adapter_count = 0
    if has_registry:
        try:
            with open(registry_file, 'r') as f:
                reg = json.load(f)
            adapter_count = len(reg.get('leagues', {}))
        except Exception:
            pass

    is_ready = has_base and has_registry and adapter_count > 0

    stats = {
        "has_base_model": has_base,
        "has_registry": has_registry,
        "adapter_count": adapter_count,
        "ready": is_ready,
    }

    if is_ready:
        print(f"  [P3 ✓] RL: Base model + {adapter_count} league adapters ready")
    else:
        missing = []
        if not has_base:
            missing.append("base model")
        if not has_registry or adapter_count == 0:
            missing.append("league adapters")
        print(f"  [P3 ✗] RL NOT READY — Missing: {', '.join(missing)}")

    return is_ready, stats


async def auto_remediate(check: str, conn=None, timeout_minutes: int = 30) -> bool:
    """Auto-fix data readiness failures with a time budget.

    Args:
        check: 'leagues', 'seasons', or 'rl'
        timeout_minutes: Max time to spend on remediation (default 30).

    Returns:
        True if remediation completed, False if timed out or failed.
    """
    if check == "leagues":
        print("  [AUTO] Running league enrichment + search dict rebuild...")
        try:
            from Scripts.enrich_leagues import main as run_enricher
            await asyncio.wait_for(run_enricher(), timeout=timeout_minutes * 60)
            from Scripts.build_search_dict import main as run_search_dict
            await asyncio.wait_for(run_search_dict(), timeout=timeout_minutes * 60)
            return True
        except asyncio.TimeoutError:
            print(f"  [AUTO] Enrichment exceeded {timeout_minutes}min budget -- proceeding with available data.")
            return False
        except Exception as e:
            logger.error(f"  [AUTO] League enrichment failed: {e}")
            print(f"  [AUTO] Failed: {e}")
            return False

    elif check == "seasons":
        print("  [AUTO] Running historical season enrichment (2 seasons)...")
        try:
            from Scripts.enrich_leagues import main as run_enricher
            await asyncio.wait_for(run_enricher(num_seasons=2), timeout=timeout_minutes * 60)
            from Scripts.build_search_dict import main as run_search_dict
            await asyncio.wait_for(run_search_dict(), timeout=timeout_minutes * 60)
            return True
        except asyncio.TimeoutError:
            print(f"  [AUTO] Season enrichment exceeded {timeout_minutes}min budget -- proceeding with available data.")
            return False
        except Exception as e:
            logger.error(f"  [AUTO] Season enrichment failed: {e}")
            print(f"  [AUTO] Failed: {e}")
            return False

    elif check == "rl":
        print("  [AUTO] Running RL training...")
        try:
            from Core.Intelligence.rl.trainer import RLTrainer
            trainer = RLTrainer()
            trainer.train_from_fixtures()
            print("  [AUTO] RL training complete")
            return True
        except Exception as e:
            logger.error(f"  [AUTO] RL training failed: {e}")
            print(f"  [AUTO] RL training failed: {e}")
            return False

    return False
