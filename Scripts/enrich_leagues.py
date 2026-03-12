# enrich_leagues.py: Extract Flashscore league pages -> SQLite database.
# Part of LeoBook Scripts — Data Collection
#
# ── Enrichment Modes ─────────────────────────────────────────────────────────
#   (default)    Column-level gap scan — only leagues/seasons with missing cells
#   --refresh    Re-process stale leagues (>7 days old)
#   --reset      Full reset — re-enrich ALL leagues from scratch
#   --scan-only  Print the gap report and exit without enriching
#
# ── Season targeting ─────────────────────────────────────────────────────────
#   --season N      Season by offset (0=current, 1=most-recent-past, ...)
#   --seasons N     Last N past seasons
#   --all-seasons   All available seasons
#
#   Historical season discovery uses the /archive/ page only.
#   Handles both split-season (2023/2024) and calendar-year (2024) leagues.
#
# ── Gap scan (default mode) ──────────────────────────────────────────────────
#   Uses Data.Access.gap_scanner.GapScanner which checks ALL required columns
#   in leagues, teams, and schedules tables — not just whether a league row
#   exists. Each gap is tracked to its (league_id, season) so the enricher
#   targets only the broken seasons, not the entire league history.
#
#   After enriching each league the scanner re-checks its gaps and logs a
#   "before / after" delta so you can see exactly what was fixed.
#
# ── Crest URL strategy ───────────────────────────────────────────────────────
#   1. Image downloaded to local disk (Data/Store/crests/...)
#   2. Uploaded to Supabase Storage -> public URL obtained
#   3. Supabase URL written to teams.crest AND immediately back-filled into
#      schedules.home_crest / schedules.away_crest via _backfill_schedule_crests()
#   4. propagate_crest_urls() runs BEFORE each sync checkpoint
#
# Usage:
#   python -m Scripts.enrich_leagues                    # Gap scan (default)
#   python -m Scripts.enrich_leagues --scan-only        # Print gaps, exit
#   python -m Scripts.enrich_leagues --limit 5          # First 5 leagues with gaps
#   python -m Scripts.enrich_leagues --limit 501-1000   # Range
#   python -m Scripts.enrich_leagues --season 1         # Most recent past season
#   python -m Scripts.enrich_leagues --seasons 5        # Last 5 past seasons
#   python -m Scripts.enrich_leagues --refresh          # Stale (>7 days)
#   python -m Scripts.enrich_leagues --reset            # Full reset

import asyncio
import argparse
import json
import logging
import os
import re
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, date
from typing import Dict, List, Optional, Set, Tuple

import requests
from playwright.async_api import async_playwright, Page

# ── Project imports ───────────────────────────────────────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from Core.Utils.constants import now_ng
from Core.Intelligence.aigo_suite import AIGOSuite
from Core.Intelligence.selector_manager import SelectorManager
from Data.Access.league_db import (
    init_db, get_connection, upsert_league, upsert_team, upsert_fixture,
    bulk_upsert_fixtures, mark_league_processed, get_unprocessed_leagues,
    get_leagues_with_gaps, get_stale_leagues,
    get_league_db_id, get_team_id,
)
from Data.Access.gap_scanner import GapScanner, GapReport
from Core.Browser.site_helpers import fs_universal_popup_dismissal

logger = logging.getLogger(__name__)

# ── Selectors ─────────────────────────────────────────────────────────────────
selector_mgr = SelectorManager()
CONTEXT_LEAGUE = "fs_league_page"

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEAGUES_JSON = os.path.join(BASE_DIR, "Data", "Store", "leagues.json")
CRESTS_DIR = os.path.join("Data", "Store", "crests")
LEAGUE_CRESTS_DIR = os.path.join(CRESTS_DIR, "leagues")
TEAM_CRESTS_DIR = os.path.join(CRESTS_DIR, "teams")

# ── Config ────────────────────────────────────────────────────────────────────
MAX_CONCURRENCY = 5
MAX_SHOW_MORE = 50
DOWNLOAD_WORKERS = 8
REQUEST_TIMEOUT = 15

# ── Hydration & scroll tuning ─────────────────────────────────────────────────
HYDRATION_STABLE_FOR: float = 2.0
HYDRATION_MAX_WAIT: float = 30.0
SHOW_MORE_ROW_WAIT: float = 8.0
HYDRATION_POLL_INTERVAL: float = 0.4
SCROLL_MAX_STEPS: int = 40
SCROLL_STEP_WAIT: float = 0.6
SCROLL_NO_NEW_ROWS_LIMIT: int = 3

# ── Globals ───────────────────────────────────────────────────────────────────
executor = ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS)


# ═══════════════════════════════════════════════════════════════════════════════
#  Image Download
# ═══════════════════════════════════════════════════════════════════════════════

def _download_image(url: str, dest_path: str) -> str:
    if not url or url.startswith("data:"):
        return ""
    abs_dest = os.path.join(BASE_DIR, dest_path) if not os.path.isabs(dest_path) else dest_path
    if os.path.exists(abs_dest):
        return dest_path
    try:
        os.makedirs(os.path.dirname(abs_dest), exist_ok=True)
        resp = requests.get(url, timeout=REQUEST_TIMEOUT, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
            "Referer": "https://www.flashscore.com/",
        })
        if resp.status_code == 200 and len(resp.content) > 100:
            with open(abs_dest, "wb") as f:
                f.write(resp.content)
            return dest_path
    except Exception:
        pass
    return ""


def _slugify(name: str) -> str:
    s = name.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_]+", "_", s)
    return s.strip("_")


def schedule_image_download(url: str, dest_path: str):
    return executor.submit(_download_image, url, dest_path)


# ═══════════════════════════════════════════════════════════════════════════════
#  Dynamic Hydration Helpers
# ═══════════════════════════════════════════════════════════════════════════════

async def _wait_for_rows_stable(
    page: Page, row_selector: str,
    stable_for: float = HYDRATION_STABLE_FOR,
    max_wait: float = HYDRATION_MAX_WAIT,
) -> int:
    deadline = time.monotonic() + max_wait
    last_count = -1
    stable_since: Optional[float] = None
    while time.monotonic() < deadline:
        try:
            count = await page.locator(row_selector).count()
        except Exception:
            count = 0
        now = time.monotonic()
        if count != last_count:
            last_count = count
            stable_since = now
        elif stable_since is not None and (now - stable_since) >= stable_for:
            return last_count
        await asyncio.sleep(HYDRATION_POLL_INTERVAL)
    return last_count


async def _wait_for_page_hydration(
    page: Page, selectors: dict,
    max_wait: float = HYDRATION_MAX_WAIT,
) -> int:
    container_sel = selectors.get("main_container", "")
    row_sel = selectors.get("match_row", "[id^='g_1_']")
    phase1_budget = max_wait / 2.0
    phase1_start = time.monotonic()
    if container_sel:
        try:
            await page.wait_for_selector(container_sel, timeout=int(phase1_budget * 1000))
        except Exception:
            pass
    phase1_elapsed = time.monotonic() - phase1_start
    phase2_budget = max(2.0, max_wait - phase1_elapsed)
    return await _wait_for_rows_stable(page, row_sel,
                                       stable_for=HYDRATION_STABLE_FOR,
                                       max_wait=phase2_budget)


# ═══════════════════════════════════════════════════════════════════════════════
#  Scroll-to-Load Helper
# ═══════════════════════════════════════════════════════════════════════════════

async def _scroll_to_load(
    page: Page, row_selector: str,
    max_steps: int = SCROLL_MAX_STEPS,
    step_wait: float = SCROLL_STEP_WAIT,
    no_new_rows_limit: int = SCROLL_NO_NEW_ROWS_LIMIT,
) -> int:
    scroll_js = """() => {
        const h = window.innerHeight || document.documentElement.clientHeight || 1080;
        window.scrollBy({ top: h, behavior: 'instant' });
        return { scrollY: window.scrollY, innerHeight: window.innerHeight,
                 bodyHeight: document.body.scrollHeight };
    }"""
    last_count = 0
    no_new_streak = 0
    total_scrolled = 0
    for _ in range(max_steps):
        before = await page.locator(row_selector).count()
        try:
            pos = await page.evaluate(scroll_js)
        except Exception:
            break
        total_scrolled += 1
        await asyncio.sleep(step_wait)
        deadline = time.monotonic() + step_wait
        after = before
        while time.monotonic() < deadline:
            try:
                after = await page.locator(row_selector).count()
            except Exception:
                break
            if after > before:
                break
            await asyncio.sleep(HYDRATION_POLL_INTERVAL)
        no_new_streak = 0 if after > before else no_new_streak + 1
        last_count = after
        at_bottom = (
            pos.get("scrollY", 0) + pos.get("innerHeight", 0)
            >= pos.get("bodyHeight", 1) - 50
        )
        if at_bottom or no_new_streak >= no_new_rows_limit:
            break
    try:
        await page.evaluate("() => window.scrollTo({ top: 0, behavior: 'instant' })")
    except Exception:
        pass
    if total_scrolled:
        print(f"      [Scroll] {total_scrolled} steps -> {last_count} rows visible")
    return last_count


# ═══════════════════════════════════════════════════════════════════════════════
#  Crest URL Back-fill
# ═══════════════════════════════════════════════════════════════════════════════

def _backfill_schedule_crests(conn, league_id: str, season: str, country_code: str) -> int:
    """Overwrite empty/local-path crests in schedules with the Supabase URL from teams.

    Runs immediately after team crest uploads so no local path ever reaches
    the Supabase DB via SyncManager.
    """
    conn.execute("""
        UPDATE schedules
        SET home_crest = (
            SELECT t.crest FROM teams t
            WHERE t.name = schedules.home_team_name
              AND t.country_code = ?
              AND t.crest LIKE 'http%'
            LIMIT 1
        )
        WHERE league_id = ? AND season = ?
          AND home_team_name IS NOT NULL
          AND (home_crest IS NULL OR home_crest = '' OR home_crest NOT LIKE 'http%')
          AND EXISTS (
              SELECT 1 FROM teams t
              WHERE t.name = schedules.home_team_name
                AND t.country_code = ?
                AND t.crest LIKE 'http%'
          )
    """, (country_code, league_id, season, country_code))
    home_updated = conn.execute("SELECT changes()").fetchone()[0]

    conn.execute("""
        UPDATE schedules
        SET away_crest = (
            SELECT t.crest FROM teams t
            WHERE t.name = schedules.away_team_name
              AND t.country_code = ?
              AND t.crest LIKE 'http%'
            LIMIT 1
        )
        WHERE league_id = ? AND season = ?
          AND away_team_name IS NOT NULL
          AND (away_crest IS NULL OR away_crest = '' OR away_crest NOT LIKE 'http%')
          AND EXISTS (
              SELECT 1 FROM teams t
              WHERE t.name = schedules.away_team_name
                AND t.country_code = ?
                AND t.crest LIKE 'http%'
          )
    """, (country_code, league_id, season, country_code))
    away_updated = conn.execute("SELECT changes()").fetchone()[0]

    total = home_updated + away_updated
    if total:
        conn.commit()
    return total


# ── Supabase storage ──────────────────────────────────────────────────────────
_supabase_storage = None
_supabase_url = ""
_uploaded_crests: Set[str] = set()


def _init_supabase_storage():
    global _supabase_storage, _supabase_url
    if _supabase_storage is not None:
        return _supabase_storage, _supabase_url
    try:
        from Data.Access.supabase_client import get_supabase_client
        client = get_supabase_client()
        if client:
            _supabase_storage = client.storage
            _supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
            try:
                existing = [b.name for b in _supabase_storage.list_buckets()]
                for bucket in ("league-crests", "team-crests"):
                    if bucket not in existing:
                        _supabase_storage.create_bucket(bucket, options={"public": True})
            except Exception:
                pass
            return _supabase_storage, _supabase_url
    except Exception:
        pass
    _supabase_storage = False
    return None, ""


def upload_crest_to_supabase(local_path: str, bucket: str, remote_name: str) -> str:
    key = f"{bucket}/{remote_name}"
    if key in _uploaded_crests:
        storage, sb_url = _init_supabase_storage()
        return f"{sb_url}/storage/v1/object/public/{key}" if sb_url else ""
    storage, sb_url = _init_supabase_storage()
    if not storage or not sb_url:
        return ""
    abs_path = os.path.join(BASE_DIR, local_path) if not os.path.isabs(local_path) else local_path
    if not os.path.exists(abs_path):
        return ""
    try:
        with open(abs_path, "rb") as f:
            storage.from_(bucket).upload(
                path=remote_name, file=f,
                file_options={"cache-control": "3600", "upsert": "true"}
            )
        _uploaded_crests.add(key)
        return f"{sb_url}/storage/v1/object/public/{bucket}/{remote_name}"
    except Exception:
        return ""


# ═══════════════════════════════════════════════════════════════════════════════
#  Step 1: Seed leagues from JSON
# ═══════════════════════════════════════════════════════════════════════════════

def seed_leagues_from_json(conn):
    print(f"\n  [Seed] Reading {LEAGUES_JSON}...")
    with open(LEAGUES_JSON, "r", encoding="utf-8") as f:
        leagues = json.load(f)
    count = 0
    for lg in leagues:
        upsert_league(conn, {
            "league_id":    lg["league_id"],
            "country_code": lg.get("country_code"),
            "continent":    lg.get("continent"),
            "name":         lg["name"],
            "url":          lg.get("url"),
        })
        count += 1
    print(f"  [Seed] [OK] Upserted {count} leagues.")


# ═══════════════════════════════════════════════════════════════════════════════
#  JS Extraction Scripts
# ═══════════════════════════════════════════════════════════════════════════════

EXTRACT_MATCHES_JS = r"""(ctx) => {
    const matches = [];
    const s = ctx.selectors;
    const startYear = ctx.startYear || new Date().getFullYear();
    const endYear = ctx.endYear || startYear;
    const isSplitSeason = ctx.isSplitSeason || false;
    const tab = ctx.tab || 'results';

    function inferYear(day, month) {
        if (!isSplitSeason) return startYear;
        return month >= 7 ? startYear : endYear;
    }

    const container = document.querySelector(s.main_container)?.parentElement || document.body;
    const allEls = container.querySelectorAll(`${s.match_round}, ${s.match_row}`);
    let currentRound = '';

    allEls.forEach(el => {
        if (el.matches(s.match_round)) { currentRound = el.innerText.trim(); return; }
        const rowId = el.getAttribute('id') || '';
        if (!rowId || !rowId.startsWith('g_1_')) return;
        const fixtureId = rowId.replace('g_1_', '');
        const timeEl = row.querySelector(s.match_time);
        let matchTime = '', matchDate = '', extraTag = '';
        if (timeEl) {
            const stageInTime = timeEl.querySelector(`${s.match_stage_block}, ${s.match_stage_pkv}, ${s.match_stage}`);
            if (stageInTime) extraTag = stageInTime.innerText.trim();
            let raw = '';
            for (const node of timeEl.childNodes) {
                if (node.nodeType === 3) raw += node.textContent;
                else if (node.classList && node.classList.contains('lineThrough')) raw += node.textContent;
            }
            raw = raw.trim();
            if (!raw) raw = timeEl.innerText.trim().replace(/FRO|Postp\.?|Canc\.?|Abn\.?/gi, '').trim();
            const fullM = raw.match(/(\d{2})\.(\d{2})\.(\d{4})\s+(\d{2}):(\d{2})/);
            if (fullM) {
                matchDate = `${fullM[3]}-${fullM[2]}-${fullM[1]}`; matchTime = `${fullM[4]}:${fullM[5]}`;
            } else {
                const shortM = raw.match(/(\d{2})\.(\d{2})\.\s*(\d{2}):(\d{2})/);
                if (shortM) {
                    const year = inferYear(parseInt(shortM[1]), parseInt(shortM[2]));
                    matchDate = `${year}-${shortM[2]}-${shortM[1]}`; matchTime = `${shortM[3]}:${shortM[4]}`;
                } else {
                    const jt = raw.match(/(\d{2}):(\d{2})/);
                    if (jt) matchTime = `${jt[1]}:${jt[2]}`;
                }
            }
        }
        const row = el;
        const homeEl = row.querySelector(s.home_participant);
        const homeName = homeEl ? (homeEl.querySelector(s.participant_name) || homeEl).innerText.trim().replace(/\s*\(.*?\)\s*$/, '') : '';
        const awayEl = row.querySelector(s.away_participant);
        const awayName = awayEl ? (awayEl.querySelector(s.participant_name) || awayEl).innerText.trim().replace(/\s*\(.*?\)\s*$/, '') : '';
        const homeScoreEl = row.querySelector(s.match_score_home);
        const awayScoreEl = row.querySelector(s.match_score_away);
        const homeScore = homeScoreEl && homeScoreEl.innerText.trim() !== '-' ? parseInt(homeScoreEl.innerText.trim()) : null;
        const awayScore = awayScoreEl && awayScoreEl.innerText.trim() !== '-' ? parseInt(awayScoreEl.innerText.trim()) : null;
        let matchStatus = '';
        const stageEl = row.querySelector(`${s.match_stage_block}, ${s.match_stage}`);
        if (stageEl && !stageEl.closest(s.match_time)) matchStatus = stageEl.innerText.trim();
        else if (homeScoreEl) {
            const state = homeScoreEl.getAttribute('data-state') || '';
            const isFinal = homeScoreEl.className.includes('isFinal') || homeScoreEl.className.includes('Final');
            if (state === 'final' || isFinal || homeScore !== null) matchStatus = 'FT';
        }
        const homeImg = row.querySelector(s.match_logo_home);
        const awayImg = row.querySelector(s.match_logo_away);
        const homeCrest = homeImg ? (homeImg.src || homeImg.getAttribute('data-src') || '') : '';
        const awayCrest = awayImg ? (awayImg.src || awayImg.getAttribute('data-src') || '') : '';
        let homeTeamId = '', awayTeamId = '', homeTeamUrl = '', awayTeamUrl = '';
        let linkEl = row.querySelector(s.match_link);
        if (!linkEl) linkEl = document.querySelector(`a[aria-describedby="${rowId}"]`);
        const mLink = linkEl ? linkEl.getAttribute('href') : '';
        if (mLink && mLink.includes('/match/football/')) {
            const parts = mLink.replace(/^(.*\/match\/football\/)/, '').split('/').filter(p => p && !p.startsWith('?'));
            if (parts.length >= 2) {
                const hSeg = parts[0], aSeg = parts[1];
                homeTeamId = hSeg.substring(hSeg.lastIndexOf('-') + 1);
                awayTeamId = aSeg.substring(aSeg.lastIndexOf('-') + 1);
                const hSlug = hSeg.substring(0, hSeg.lastIndexOf('-'));
                const aSlug = aSeg.substring(0, aSeg.lastIndexOf('-'));
                if (hSlug && homeTeamId) homeTeamUrl = `https://www.flashscore.com/team/${hSlug}/${homeTeamId}/`;
                if (aSlug && awayTeamId) awayTeamUrl = `https://www.flashscore.com/team/${aSlug}/${awayTeamId}/`;
            }
        }
        matches.push({ fixture_id: fixtureId, date: matchDate, time: matchTime,
            home_team_name: homeName, away_team_name: awayName,
            home_team_id: homeTeamId, away_team_id: awayTeamId,
            home_team_url: homeTeamUrl, away_team_url: awayTeamUrl,
            home_score: homeScore, away_score: awayScore,
            match_status: matchStatus, home_crest_url: homeCrest, away_crest_url: awayCrest,
            league_stage: currentRound, extra: extraTag || null,
            url: `/match/${fixtureId}/#/match-summary`, match_link: mLink || ''
        });
    });
    return matches;
}"""

EXTRACT_SEASON_JS = r"""(selectors) => {
    const s = selectors;
    for (const sel of s.season_info.split(',').map(x => x.trim())) {
        const el = document.querySelector(sel);
        if (el) { const m = el.innerText.trim().match(/(\d{4}(?:\/\d{4})?)/); if (m) return m[1]; }
    }
    for (const b of document.querySelectorAll(s.breadcrumb_text)) {
        const m = b.innerText.match(/(\d{4}(?:\/\d{4})?)/); if (m) return m[1];
    }
    return '';
}"""

EXTRACT_CREST_JS = r"""(selectors) => {
    const img = document.querySelector(selectors.league_crest);
    return img ? (img.src || img.getAttribute('data-src') || '') : '';
}"""

EXTRACT_FS_LEAGUE_ID_JS = r"""() => {
    if (window.leaguePageHeaderData?.tournamentStageId) return window.leaguePageHeaderData.tournamentStageId;
    if (window.tournament_id) return window.tournament_id;
    if (window.config?.tournamentStage) return window.config.tournamentStage;
    const pathM = (window.location.pathname || '').match(/-([A-Za-z0-9]{6,10})\/?$/);
    if (pathM) return pathM[1];
    for (const link of document.querySelectorAll('a[href*="/standings/"], a[href*="/results/"]')) {
        const m = (link.getAttribute('href') || '').match(/\/([A-Za-z0-9]{6,10})\/standings\//);
        if (m) return m[1];
    }
    const hashM = (window.location.hash || '').match(/#\/([A-Za-z0-9]{6,10})\//);
    if (hashM) return hashM[1];
    return '';
}"""

EXTRACT_ARCHIVE_JS = r"""(selectors) => {
    const seasons = [], seen = new Set();
    for (const sel of [selectors.archive_links, selectors.archive_table_links, 'a[href*="/football/"]']) {
        if (!sel) continue;
        for (const a of document.querySelectorAll(sel)) {
            const href = a.getAttribute('href') || '';
            const splitM = href.match(/\/football\/([^/]+)\/([^/]+-(\d{4})-(\d{4}))\/?/);
            if (splitM && !seen.has(splitM[2])) {
                seen.add(splitM[2]);
                seasons.push({ slug: splitM[2], country: splitM[1],
                    start_year: parseInt(splitM[3]), end_year: parseInt(splitM[4]),
                    is_split: true, label: `${splitM[3]}/${splitM[4]}`,
                    url: href.startsWith('http') ? href : 'https://www.flashscore.com' + href });
            }
            const calM = href.match(/\/football\/([^/]+)\/([^/]+-(\d{4}))\/?$/);
            if (calM && !seen.has(calM[2])) {
                if (![...seen].some(s => s.startsWith(calM[2] + '-'))) {
                    seen.add(calM[2]);
                    seasons.push({ slug: calM[2], country: calM[1],
                        start_year: parseInt(calM[3]), end_year: parseInt(calM[3]),
                        is_split: false, label: calM[3],
                        url: href.startsWith('http') ? href : 'https://www.flashscore.com' + href });
                }
            }
        }
    }
    seasons.sort((a, b) => b.start_year - a.start_year || b.end_year - a.end_year);
    return seasons;
}"""


# ═══════════════════════════════════════════════════════════════════════════════
#  Season Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def parse_season_string(season_str: str) -> dict:
    if not season_str:
        year = now_ng().year
        return {"startYear": year, "endYear": year, "isSplitSeason": False}
    m = re.match(r"(\d{4})[/\-](\d{4})", season_str)
    if m:
        return {"startYear": int(m.group(1)), "endYear": int(m.group(2)), "isSplitSeason": True}
    m = re.match(r"(\d{4})", season_str)
    if m:
        year = int(m.group(1))
        return {"startYear": year, "endYear": year, "isSplitSeason": False}
    year = datetime.now().year
    return {"startYear": year, "endYear": year, "isSplitSeason": False}


@AIGOSuite.aigo_retry(max_retries=2, delay=3.0)
async def get_archive_seasons(page: Page, league_url: str) -> List[Dict]:
    """Navigate to /archive/ and return all available past seasons, most-recent-first."""
    archive_url = league_url.rstrip("/") + "/archive/"
    print(f"    [Archive] {archive_url}")
    try:
        await page.goto(archive_url, wait_until="domcontentloaded", timeout=60000)
        await fs_universal_popup_dismissal(page)
        selectors = selector_mgr.get_all_selectors_for_context(CONTEXT_LEAGUE)
        link_sel = (
            selectors.get("archive_links")
            or selectors.get("archive_table_links")
            or "a[href*='/football/']"
        )
        try:
            await page.wait_for_selector(link_sel, timeout=20000)
        except Exception:
            pass
        seasons = await page.evaluate(EXTRACT_ARCHIVE_JS, selectors)
        print(f"    [Archive] Found {len(seasons)} past seasons")
        return seasons or []
    except Exception as e:
        print(f"    [Archive] Failed: {e}")
        return []


def _select_seasons_from_archive(
    archive_seasons: List[Dict],
    target_season: Optional[int],
    num_seasons: int,
    all_seasons: bool,
    target_season_labels: Optional[List[str]] = None,
) -> List[Dict]:
    """Select seasons from the archive list.

    Args:
        target_season_labels: If provided (from gap scanner), select only these
                              specific season strings (e.g. ["2022/2023", "2023/2024"]).
                              Takes precedence over num_seasons / all_seasons.
    """
    if not archive_seasons:
        return []

    # Gap-scanner mode: only re-process specific broken seasons
    if target_season_labels:
        label_set = set(target_season_labels)
        matched = [s for s in archive_seasons if s["label"] in label_set]
        if matched:
            return matched
        # Graceful fallback — season labels may differ slightly; log and continue
        print(f"    [Archive] WARNING: none of the target seasons {label_set} matched "
              f"archive labels {[s['label'] for s in archive_seasons[:5]]}. "
              f"Falling back to first {len(label_set)} seasons.")
        return archive_seasons[:max(1, len(label_set))]

    if all_seasons:
        return archive_seasons
    if target_season is not None and target_season >= 1:
        idx = target_season - 1
        if idx < len(archive_seasons):
            return [archive_seasons[idx]]
        print(f"    [Archive] Offset {target_season} out of range — "
              f"only {len(archive_seasons)} past seasons found")
        return []
    if num_seasons > 0:
        return archive_seasons[:num_seasons]
    return []


# ═══════════════════════════════════════════════════════════════════════════════
#  Gap Verification
# ═══════════════════════════════════════════════════════════════════════════════

def verify_league_gaps_closed(
    conn, league_id: str, before_gaps: int, idx: int, total: int
) -> Tuple[int, int]:
    """Re-scan a single league after enrichment and report before/after delta.

    Returns:
        (remaining_gaps, closed_gaps)
    """
    try:
        report = GapScanner(conn).scan(
            # Scan all tables but filter to only this league via post-processing
        )
        league_summary = report.summary_by_league.get(league_id)
        after_gaps = league_summary.total_gaps if league_summary else 0
        closed = max(0, before_gaps - after_gaps)

        if closed > 0 or after_gaps > 0:
            status = "[✓]" if after_gaps == 0 else "[~]"
            print(f"  [{idx}/{total}] {status} Gap delta for {league_id}: "
                  f"{before_gaps} -> {after_gaps} "
                  f"({closed} closed"
                  + (f", {after_gaps} remaining" if after_gaps else "")
                  + ")")

        return after_gaps, closed
    except Exception as e:
        logger.warning("[GapVerify] Failed for %s: %s", league_id, e)
        return 0, 0


# ═══════════════════════════════════════════════════════════════════════════════
#  Core Extraction
# ═══════════════════════════════════════════════════════════════════════════════

@AIGOSuite.aigo_retry(max_retries=2, delay=2.0)
async def _expand_show_more(page: Page, max_clicks: int = MAX_SHOW_MORE):
    clicks = 0
    btn_sel = selector_mgr.get_selector(CONTEXT_LEAGUE, "show_more_matches")
    row_sel = selector_mgr.get_selector(CONTEXT_LEAGUE, "match_row") or "[id^='g_1_']"
    while clicks < max_clicks:
        try:
            btn = page.locator(btn_sel)
            if await btn.count() > 0 and await btn.first.is_visible(timeout=3000):
                before = await page.locator(row_sel).count()
                await btn.first.click()
                waited = 0.0
                arrived = False
                while waited < SHOW_MORE_ROW_WAIT:
                    await asyncio.sleep(HYDRATION_POLL_INTERVAL)
                    waited += HYDRATION_POLL_INTERVAL
                    if await page.locator(row_sel).count() > before:
                        arrived = True
                        break
                clicks += 1
                if not arrived:
                    break
            else:
                break
        except Exception:
            break
    if clicks:
        print(f"      [Expand] Clicked 'Show more' {clicks}x")


@AIGOSuite.aigo_retry(max_retries=2, delay=3.0)
async def extract_tab(
    page: Page, league_url: str, tab: str, conn,
    league_id: str, season: str, country_code: str,
    region_league: str = "",
    gap_columns: Optional[Set[str]] = None,
) -> int:
    """Navigate to a league tab, load all rows, extract and persist.

    Args:
        gap_columns: If provided (from gap scanner), only re-process specific
                     column groups. Currently used for logging/targeted logging.
                     The full extraction always runs — partial column extraction
                     would risk leaving other columns stale.

    Loading sequence:
      1. goto + popup dismissal
      2. _wait_for_page_hydration()   — initial row render
      3. _scroll_to_load()            — lazy-loaded rows below fold
      4. _expand_show_more()          — pagination exhaustion
      5. EXTRACT_MATCHES_JS           — scrape all rows
      6. bulk_upsert_fixtures()       — write (crest cols intentionally empty)
      7. upload_crest_to_supabase()   — upload + store URL in teams.crest
      8. _backfill_schedule_crests()  — copy URL into schedules
    """
    url = league_url.rstrip("/") + f"/{tab}/"
    print(f"    [{tab.upper()}] {url}")
    if gap_columns:
        print(f"      [Targeting gaps] {', '.join(sorted(gap_columns))}")

    tab_selectors = selector_mgr.get_all_selectors_for_context(CONTEXT_LEAGUE)
    row_sel: str = tab_selectors.get("match_row", "[id^='g_1_']")

    try:
        resp = await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await fs_universal_popup_dismissal(page)
        if resp and resp.status >= 400:
            print(f"    [{tab.upper()}] HTTP {resp.status} — not available")
            return 0
        if tab not in page.url.rstrip("/"):
            print(f"    [{tab.upper()}] Redirected — season not available")
            return 0

        initial = await _wait_for_page_hydration(page, tab_selectors)
        if initial:
            print(f"      [Hydrate] {initial} rows initially")
        scrolled = await _scroll_to_load(page, row_sel)
        if scrolled > initial:
            print(f"      [Scroll] +{scrolled - initial} rows revealed")
    except Exception as e:
        print(f"    [{tab.upper()}] Nav failed: {e}")
        return 0

    await _expand_show_more(page)

    season_ctx = parse_season_string(season)
    season_ctx["tab"] = tab
    season_ctx["selectors"] = tab_selectors

    try:
        matches_raw = await page.evaluate(EXTRACT_MATCHES_JS, season_ctx)
    except Exception as e:
        print(f"    [{tab.upper()}] JS extraction failed: {e}")
        return 0

    if not matches_raw:
        print(f"    [{tab.upper()}] No matches found")
        return 0

    fixture_rows: List[Dict] = []
    crest_futures: List[Tuple] = []   # (future, team_name, dest_path)
    today = date.today()

    for m in matches_raw:
        home_name = m.get("home_team_name", "")
        away_name = m.get("away_team_name", "")
        if not home_name or not away_name:
            continue

        home_team_id  = m.get("home_team_id",  "")
        away_team_id  = m.get("away_team_id",  "")
        home_team_url = m.get("home_team_url", "")
        away_team_url = m.get("away_team_url", "")

        if home_name:
            td = {"name": home_name, "country_code": country_code, "league_ids": [league_id]}
            if home_team_id:  td["team_id"] = home_team_id
            if home_team_url: td["url"]     = home_team_url
            upsert_team(conn, td)
        if away_name:
            td = {"name": away_name, "country_code": country_code, "league_ids": [league_id]}
            if away_team_id:  td["team_id"] = away_team_id
            if away_team_url: td["url"]     = away_team_url
            upsert_team(conn, td)

        for team_name, crest_url_key in ((home_name, "home_crest_url"), (away_name, "away_crest_url")):
            crest_url = m.get(crest_url_key, "")
            if crest_url and not crest_url.startswith("data:"):
                dest = os.path.join(TEAM_CRESTS_DIR, f"{_slugify(team_name)}.png")
                crest_futures.append((
                    schedule_image_download(crest_url, dest),
                    team_name,
                    dest,
                ))

        status = m.get("match_status", "")
        extra  = m.get("extra")
        if status:
            su = status.upper()
            if   "FT" in su or "FINISHED" in su: status = "finished"
            elif "AET" in su:  status = "finished";  extra = extra or "AET"
            elif "PEN" in su:  status = "finished";  extra = extra or "PEN"
            elif "POST" in su: status = "postponed"; extra = extra or "Postp"
            elif "CANC" in su: status = "cancelled"; extra = extra or "Canc"
            elif "ABD"  in su or "ABAN" in su: status = "abandoned"; extra = extra or "Abn"
            elif "LIVE" in su or "'" in status: status = "live"
            elif "HT"   in su: status = "halftime"
            elif status == "-": status = "scheduled"

        match_date_str = m.get("date", "")
        if match_date_str and not status:
            try:
                if datetime.strptime(match_date_str, "%Y-%m-%d").date() > today:
                    status = "scheduled"
            except ValueError:
                pass
        if not status:
            status = "scheduled" if tab == "fixtures" else "finished"

        if extra:
            eu = extra.upper().strip()
            if   "FRO"  in eu: extra = "FRO"
            elif "POSTP" in eu: extra = "Postp"
            elif "CANC"  in eu: extra = "Canc"
            elif "ABN"   in eu or "ABAN" in eu: extra = "Abn"

        fixture_rows.append({
            "fixture_id":     m.get("fixture_id", ""),
            "date":           match_date_str,
            "time":           m.get("time", ""),
            "league_id":      league_id,
            "home_team_id":   home_team_id,
            "home_team_name": home_name,
            "away_team_id":   away_team_id,
            "away_team_name": away_name,
            "home_score":     m.get("home_score"),
            "away_score":     m.get("away_score"),
            "extra":          extra,
            "league_stage":   m.get("league_stage", ""),
            "match_status":   status,
            "season":         season,
            "home_crest":     "",     # filled by _backfill_schedule_crests
            "away_crest":     "",
            "url":            f"https://www.flashscore.com/match/{m.get('fixture_id', '')}/#/match-summary",
            "region_league":  region_league,
            "match_link":     m.get("match_link", ""),
        })

    if fixture_rows:
        bulk_upsert_fixtures(conn, fixture_rows)

    downloaded = 0
    for fut, team_name, dest in crest_futures:
        try:
            local = fut.result(timeout=30)
            if local:
                downloaded += 1
                sb_url = upload_crest_to_supabase(local, "team-crests", f"{_slugify(team_name)}.png")
                crest_val = sb_url if sb_url else local
                conn.execute(
                    "UPDATE teams SET crest = ? WHERE name = ? AND country_code = ?",
                    (crest_val, team_name, country_code)
                )
        except Exception:
            pass
    if downloaded:
        conn.commit()

    if fixture_rows:
        backfilled = _backfill_schedule_crests(conn, league_id, season, country_code)
        if backfilled:
            print(f"      [Crests] Back-filled {backfilled} schedule rows")

    print(f"    [{tab.upper()}] [OK] {len(fixture_rows)} matches, {downloaded} crests")
    return len(fixture_rows)


async def enrich_single_league(
    context,
    league: Dict,
    conn,
    idx: int,
    total: int,
    num_seasons: int = 0,
    all_seasons: bool = False,
    target_season: Optional[int] = None,
    # Gap-scanner parameters:
    seasons_with_gaps: Optional[List[str]] = None,
    gap_columns: Optional[Set[str]] = None,
    needs_full_re_enrich: bool = False,
) -> None:
    """Process a single league: metadata, current season, and targeted past seasons.

    Gap-scanner mode:
      - seasons_with_gaps: specific seasons to re-process (from gap report)
      - gap_columns: which columns triggered the gap (for logging)
      - needs_full_re_enrich: if True (critical league-level gaps), re-scrape
        metadata page in addition to match tabs

    Standard mode (--season / --seasons / --all-seasons) behaves as before.
    """
    league_id    = league["league_id"]
    name         = league["name"]
    url          = league.get("url", "")
    country_code = league.get("country_code", "")
    continent    = league.get("continent", "")

    print(f"\n{'='*60}")
    print(f"  [{idx}/{total}] {name} ({league_id})")
    if seasons_with_gaps:
        print(f"  Gap target seasons: {', '.join(seasons_with_gaps)}")
    if gap_columns:
        print(f"  Gap columns: {', '.join(sorted(gap_columns))}")
    print(f"{'='*60}")

    if not url:
        print(f"  [SKIP] No URL")
        mark_league_processed(conn, league_id)
        return

    page = await context.new_page()
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await fs_universal_popup_dismissal(page)

        selectors = selector_mgr.get_all_selectors_for_context(CONTEXT_LEAGUE)
        breadcrumb_sel = selectors.get("breadcrumb_links", ".breadcrumb__link")
        try:
            await page.wait_for_selector(breadcrumb_sel, timeout=15000)
        except Exception:
            await asyncio.sleep(2)

        fs_league_id = await page.evaluate(EXTRACT_FS_LEAGUE_ID_JS)
        if fs_league_id:
            print(f"    [FS ID] {fs_league_id}")

        # Region resolution
        region_name = ""
        region_url_href = ""
        url_parts = url.rstrip("/").split("/")
        try:
            fb_idx = url_parts.index("football")
            if fb_idx + 1 < len(url_parts):
                slug = url_parts[fb_idx + 1]
                region_name     = slug.replace("-", " ").title()
                region_url_href = f"https://www.flashscore.com/football/{slug}/"
        except ValueError:
            pass

        breadcrumb_region = await page.evaluate("""(s) => {
            const links = document.querySelectorAll(s.breadcrumb_links);
            if (links.length >= 2) return links[1].innerText.trim();
            return '';
        }""", selectors)
        if breadcrumb_region and breadcrumb_region.upper() != "FOOTBALL":
            region_name = breadcrumb_region

        if not region_url_href:
            region_url_href = await page.evaluate("""(s) => {
                const links = document.querySelectorAll(s.breadcrumb_links);
                const el = links.length >= 2 ? links[1] : links[0];
                if (!el) return '';
                const href = el.getAttribute('href') || '';
                return href.startsWith('http') ? href : (href ? 'https://www.flashscore.com' + href : '');
            }""", selectors)

        region_flag_url = await page.evaluate("""(s) => {
            const links = document.querySelectorAll(s.breadcrumb_links);
            const target = links.length >= 2 ? links[1] : links[0];
            if (!target) return '';
            const img = document.querySelector(s.region_flag_img) || target.querySelector('img');
            return img ? (img.src || img.getAttribute('data-src') || '') : '';
        }""", selectors)

        region_flag_path = ""
        if region_flag_url and not region_flag_url.startswith("data:"):
            flag_dest = os.path.join(CRESTS_DIR, "flags",
                                     f"{_slugify(region_name or country_code or 'unknown')}.png")
            try:
                os.makedirs(os.path.join(BASE_DIR, os.path.dirname(flag_dest)), exist_ok=True)
                r = schedule_image_download(region_flag_url, flag_dest).result(timeout=10)
                if r:
                    region_flag_path = r
            except Exception:
                pass

        crest_url  = await page.evaluate(EXTRACT_CREST_JS, selectors)
        crest_path = ""
        if crest_url and not crest_url.startswith("data:"):
            local_dest  = os.path.join(LEAGUE_CRESTS_DIR, f"{_slugify(league_id)}.png")
            try:
                r = schedule_image_download(crest_url, local_dest).result(timeout=15)
                if r:
                    sb_url     = upload_crest_to_supabase(r, "league-crests", f"{_slugify(league_id)}.png")
                    crest_path = sb_url if sb_url else r
                    print(f"    [Crest] {'Supabase' if sb_url else 'local'}: {os.path.basename(local_dest)}")
            except Exception:
                print(f"    [Crest] [!] Download failed")

        season = await page.evaluate(EXTRACT_SEASON_JS, selectors)
        print(f"    [Season] {season or '(not found)'}")

        region_league = f"{continent}: {name}" if continent else name

        upsert_league(conn, {
            "league_id":      league_id,
            "fs_league_id":   fs_league_id or None,
            "name":           name,
            "country_code":   country_code,
            "continent":      continent,
            "crest":          crest_path,
            "current_season": season,
            "url":            url,
            "region":         region_name or None,
            "region_flag":    region_flag_path or None,
            "region_url":     region_url_href or None,
        })

        total_matches = 0

        # ── Decide which seasons to process ──────────────────────────────
        # Gap-scanner mode: seasons_with_gaps takes precedence over CLI flags.
        # It contains only the seasons where actual gaps were detected.
        # If also needs_full_re_enrich, we always include the current season.

        process_current = True   # always process current season unless gap-targeted

        if seasons_with_gaps:
            # Gap mode: check if the current season is in the gap list
            current_is_gap = season and season in seasons_with_gaps
            past_gap_seasons = [s for s in seasons_with_gaps if s != season]

            if current_is_gap or needs_full_re_enrich:
                f_count = await extract_tab(
                    page, url, "fixtures", conn, league_id, season, country_code,
                    region_league=region_league, gap_columns=gap_columns,
                )
                r_count = await extract_tab(
                    page, url, "results", conn, league_id, season, country_code,
                    region_league=region_league, gap_columns=gap_columns,
                )
                total_matches += f_count + r_count
            else:
                # Current season has no gaps — skip it
                process_current = False

            if past_gap_seasons:
                archive_seasons = await get_archive_seasons(page, url)
                to_process = _select_seasons_from_archive(
                    archive_seasons,
                    target_season=None,
                    num_seasons=0,
                    all_seasons=False,
                    target_season_labels=past_gap_seasons,
                )
                print(f"    [Gap] Re-processing {len(to_process)} past seasons with gaps")
                for s_meta in to_process:
                    r = await extract_tab(
                        page, s_meta["url"], "results", conn,
                        league_id, s_meta["label"], country_code,
                        region_league=region_league, gap_columns=gap_columns,
                    )
                    f = await extract_tab(
                        page, s_meta["url"], "fixtures", conn,
                        league_id, s_meta["label"], country_code,
                        region_league=region_league, gap_columns=gap_columns,
                    )
                    total_matches += r + f

        else:
            # Standard mode (--season / --seasons / --all-seasons / reset)
            f_count = await extract_tab(
                page, url, "fixtures", conn, league_id, season, country_code,
                region_league=region_league,
            )
            r_count = await extract_tab(
                page, url, "results", conn, league_id, season, country_code,
                region_league=region_league,
            )
            total_matches += f_count + r_count

            need_past = (
                (target_season is not None and target_season >= 1)
                or num_seasons > 0
                or all_seasons
            )
            if need_past:
                archive_seasons = await get_archive_seasons(page, url)
                to_process = _select_seasons_from_archive(
                    archive_seasons,
                    target_season=target_season,
                    num_seasons=num_seasons,
                    all_seasons=all_seasons,
                )
                print(f"    [Archive] Processing {len(to_process)} past season(s)")
                for s_idx, s_meta in enumerate(to_process, 1):
                    print(f"\n    [Season {s_idx}/{len(to_process)}] "
                          f"{s_meta['label']} ({'split' if s_meta.get('is_split') else 'calendar'})")
                    r = await extract_tab(
                        page, s_meta["url"], "results", conn,
                        league_id, s_meta["label"], country_code,
                        region_league=region_league,
                    )
                    f = await extract_tab(
                        page, s_meta["url"], "fixtures", conn,
                        league_id, s_meta["label"], country_code,
                        region_league=region_league,
                    )
                    total_matches += r + f

        mark_league_processed(conn, league_id)
        print(f"\n  [{idx}/{total}] [OK] {name} — {total_matches} matches total")

    except Exception as e:
        print(f"\n  [{idx}/{total}] [FAIL] {name}: {e}")
        traceback.print_exc()
    finally:
        await page.close()


# ═══════════════════════════════════════════════════════════════════════════════
#  Main
# ═══════════════════════════════════════════════════════════════════════════════

async def main(
    limit: Optional[int] = None,
    offset: int = 0,
    reset: bool = False,
    num_seasons: int = 0,
    all_seasons: bool = False,
    weekly: bool = False,
    target_season: Optional[int] = None,
    refresh: bool = False,
    scan_only: bool = False,
    min_severity: str = "important",
) -> None:
    print("\n" + "=" * 60)
    print("  FLASHSCORE LEAGUE ENRICHMENT -> SQLite")
    print("=" * 60)

    conn = init_db()
    print(f"  [DB] {os.path.abspath(conn.execute('PRAGMA database_list').fetchone()[2])}")

    await drain_enrichment_queue(conn)

    if reset:
        conn.execute("UPDATE leagues SET processed = 0")
        conn.commit()
        print("  [DB] Reset all leagues to unprocessed")

    seed_leagues_from_json(conn)

    # ── Build enrichment target list ──────────────────────────────────────
    scan_mode = ""
    # Maps league_id -> gap metadata from the scanner (for gap mode)
    gap_targets_by_id: Dict[str, Dict] = {}

    if reset:
        raw_leagues = get_unprocessed_leagues(conn)
        scan_mode   = "FULL RESET"
        leagues     = raw_leagues

    elif refresh or weekly:
        raw_leagues = get_stale_leagues(conn, days=7)
        scan_mode   = "STALE REFRESH (>7 days)"
        leagues     = raw_leagues

    else:
        # ── Default: column-level gap scan ───────────────────────────────
        scan_mode = "COLUMN GAP SCAN"
        print(f"\n  [GapScan] Scanning leagues, teams, schedules for missing data...")
        report = GapScanner(conn).scan()
        report.print_report()

        if scan_only:
            print("  [scan-only] Exiting without enrichment.")
            conn.close()
            return

        if not report.has_gaps:
            print("  [Done] All columns fully enriched. Nothing to do.")
            conn.close()
            return

        raw_targets = report.leagues_needing_enrichment(min_severity=min_severity)
        gap_targets_by_id = {t["league_id"]: t for t in raw_targets}

        # Convert gap targets into the same shape as legacy league rows
        leagues = []
        for t in raw_targets:
            leagues.append({
                "league_id":    t["league_id"],
                "name":         t["name"],
                "url":          t["url"],
                "country_code": t["country_code"],
                "continent":    t["continent"],
            })

        # If --season / --seasons were also passed, merge in leagues that are
        # missing the requested historical depth (season-depth gaps)
        if num_seasons > 0 or target_season is not None or all_seasons:
            try:
                from Data.Access.league_db import get_leagues_missing_seasons
                min_needed = num_seasons if num_seasons > 0 else 2
                if target_season is not None:
                    min_needed = max(min_needed, target_season + 1)
                history_leagues = get_leagues_missing_seasons(conn, min_seasons=min_needed)
                existing_ids    = {lg["league_id"] for lg in leagues}
                added = 0
                for lg in history_leagues:
                    if lg["league_id"] not in existing_ids:
                        leagues.append(lg)
                        added += 1
                if added:
                    print(f"  [Scan] +{added} leagues missing {min_needed}+ historical seasons")
            except Exception:
                pass

    if offset > 0:
        leagues = leagues[offset:]
    if limit:
        leagues = leagues[:limit]

    if not leagues:
        print(f"\n  [Done] No leagues need enrichment ({scan_mode}).")
        if scan_mode == "FULL RESET":
            print("  Hint: Did you mean --reset?")
        conn.close()
        return

    total = len(leagues)

    mode_label = "current season"
    if all_seasons:
        mode_label = "ALL seasons (via archive)"
    elif num_seasons > 0:
        mode_label = f"last {num_seasons} past seasons"
    elif target_season is not None:
        mode_label = "current" if target_season == 0 else f"offset #{target_season}"
    elif gap_targets_by_id:
        mode_label = "targeted gap seasons only"

    sync_interval    = max(1, total // 5)
    sync_checkpoints = set(range(sync_interval, total + 1, sync_interval))

    print(f"\n  [Enrich] {total} leagues ({scan_mode}, {mode_label}, concurrency={MAX_CONCURRENCY})")
    print(f"  [Sync]   Checkpoints at: {sorted(sync_checkpoints)}")

    os.makedirs(os.path.join(BASE_DIR, LEAGUE_CRESTS_DIR), exist_ok=True)
    os.makedirs(os.path.join(BASE_DIR, TEAM_CRESTS_DIR), exist_ok=True)

    sync_mgr = None
    try:
        from Data.Access.sync_manager import SyncManager, TABLE_CONFIG
        sync_mgr = SyncManager()
    except Exception:
        pass

    completed_count = 0

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
            timezone_id="Africa/Lagos",
        )

        sem           = asyncio.Semaphore(MAX_CONCURRENCY)
        crash_counter = 0

        async def _worker(league: Dict, idx: int) -> None:
            nonlocal completed_count, context, browser, crash_counter
            async with sem:
                league_id   = league["league_id"]
                gap_target  = gap_targets_by_id.get(league_id, {})
                before_gaps = gap_target.get("gap_summary", {}).get("total", 0)

                # Extract gap-scanner parameters if available
                s_with_gaps        = gap_target.get("seasons_with_gaps") or []
                g_columns: Set[str] = set(gap_target.get("gap_summary", {}).get("by_column", {}).keys())
                needs_full         = gap_target.get("needs_full_re_enrich", False)

                try:
                    await enrich_single_league(
                        context=context,
                        league=league,
                        conn=conn,
                        idx=idx,
                        total=total,
                        num_seasons=num_seasons,
                        all_seasons=all_seasons,
                        target_season=target_season,
                        seasons_with_gaps=s_with_gaps or None,
                        gap_columns=g_columns or None,
                        needs_full_re_enrich=needs_full,
                    )
                    crash_counter = 0

                    # Verify gaps closed for this league
                    if before_gaps > 0:
                        verify_league_gaps_closed(conn, league_id, before_gaps, idx, total)

                except Exception as e:
                    err = str(e).lower()
                    if "crashed" in err or "target closed" in err:
                        crash_counter += 1
                        if crash_counter >= 2:
                            print(f"\n  [Recovery] Browser crashed {crash_counter}x — recycling...")
                            try: await context.close()
                            except Exception: pass
                            try: await browser.close()
                            except Exception: pass
                            browser = await p.chromium.launch(headless=True)
                            context = await browser.new_context(
                                user_agent=(
                                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                                ),
                                viewport={"width": 1920, "height": 1080},
                                timezone_id="Africa/Lagos",
                            )
                            crash_counter = 0
                            print("  [Recovery] Fresh browser ready.")

                completed_count += 1

                if completed_count in sync_checkpoints:
                    pct = int((completed_count / total) * 100)
                    print(f"\n  [Checkpoint] {pct}% ({completed_count}/{total})")

                    # Propagate crest URLs BEFORE sync — never push local paths
                    try:
                        from Data.Access.db_helpers import propagate_crest_urls
                        propagate_crest_urls()
                        print(f"  [Crests] URL propagation done before {pct}% sync")
                    except Exception as e:
                        print(f"  [Crests] Propagation failed: {e}")

                    if sync_mgr and sync_mgr.supabase:
                        try:
                            print(f"  [Sync] Cloud sync at {pct}%...")
                            for tkey in ("schedules", "teams", "leagues"):
                                cfg = TABLE_CONFIG.get(tkey)
                                if cfg:
                                    await sync_mgr._sync_table(tkey, cfg)
                            print(f"  [Sync] Done at {pct}%")
                        except Exception as e:
                            print(f"  [Sync] Failed: {e}")

        tasks = [_worker(lg, i) for i, lg in enumerate(leagues, 1)]
        await asyncio.gather(*tasks)

        await context.close()
        await browser.close()

    # ── Final passes ──────────────────────────────────────────────────────
    try:
        from Core.System.gap_resolver import GapResolver
        GapResolver.resolve_immediate()
    except Exception as e:
        print(f"  [GapResolver] {e}")

    try:
        from Data.Access.season_completeness import SeasonCompletenessTracker
        SeasonCompletenessTracker.bulk_compute_all()
    except Exception as e:
        print(f"  [Completeness] {e}")

    try:
        from Data.Access.db_helpers import propagate_crest_urls
        propagate_crest_urls()
        print("  [Crests] Final URL propagation done")
    except Exception as e:
        print(f"  [Crests] Final propagation failed: {e}")

    # ── Final gap scan — show what remains ───────────────────────────────
    print(f"\n  [GapScan] Post-enrichment verification...")
    final_report = GapScanner(conn).scan()
    final_report.print_report()
    if final_report.has_gaps:
        remaining = final_report.total_gaps
        critical  = final_report.critical_gap_count
        print(f"  [!] {remaining} gaps remain ({critical} critical). "
              f"Re-run to continue.")

    league_count  = conn.execute("SELECT COUNT(*) FROM leagues").fetchone()[0]
    fixture_count = conn.execute("SELECT COUNT(*) FROM schedules").fetchone()[0]
    team_count    = conn.execute("SELECT COUNT(*) FROM teams").fetchone()[0]
    processed     = conn.execute("SELECT COUNT(*) FROM leagues WHERE processed=1").fetchone()[0]

    print(f"\n{'='*60}")
    print(f"  ENRICHMENT COMPLETE")
    print(f"{'='*60}")
    print(f"  Leagues:  {league_count} total, {processed} processed")
    print(f"  Fixtures: {fixture_count}")
    print(f"  Teams:    {team_count}")
    print(f"  Remaining gaps: {final_report.total_gaps}")
    print(f"{'='*60}\n")

    try:
        from Core.System.data_readiness import check_leagues_ready, check_seasons_ready
        check_leagues_ready(conn=conn)
        check_seasons_ready(conn=conn)
    except Exception as e:
        print(f"  [Cache] {e}")

    conn.close()
    executor.shutdown(wait=False)


# ═══════════════════════════════════════════════════════════════════════════════
#  Enrichment Queue Drain
# ═══════════════════════════════════════════════════════════════════════════════

async def drain_enrichment_queue(conn) -> None:
    from Core.System.gap_resolver import GapResolver
    GapResolver._ensure_queue_table()
    pending = conn.execute("""
        SELECT * FROM enrichment_queue
        WHERE status = 'PENDING' AND priority = 1
        LIMIT 50
    """).fetchall()
    if not pending:
        return
    print(f"\n  [Queue] Draining {len(pending)} CRITICAL items...")
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        for item in pending:
            item_id    = item["id"]
            table      = item["table_name"]
            row_id     = item["row_id"]
            col        = item["column_name"]
            lookup_key = json.loads(item["lookup_key"])
            label      = lookup_key.get("league_name") or lookup_key.get("team_name") or "Unknown"
            print(f"    - Resolving {table} ID for: {label}")
            new_id = await resolve_id_via_search(context, table, lookup_key)
            if new_id:
                conn.execute(f"UPDATE {table} SET {col} = ? WHERE id = ?", (new_id, row_id))
                conn.execute(
                    "UPDATE enrichment_queue SET status='RESOLVED', resolved_at=? WHERE id=?",
                    (datetime.now().isoformat(), item_id)
                )
                print(f"      [✓] {new_id}")
            else:
                attempts = (item["attempts"] or 0) + 1
                conn.execute(
                    "UPDATE enrichment_queue SET attempts=?, status=? WHERE id=?",
                    (attempts, "FAILED" if attempts >= 3 else "PENDING", item_id)
                )
                print(f"      [✗] Attempt {attempts}/3")
            conn.commit()
        await browser.close()


async def resolve_id_via_search(context, table: str, lookup_key: Dict) -> Optional[str]:
    page = await context.new_page()
    try:
        if table == "leagues":
            query = f"{lookup_key.get('country_name') or lookup_key.get('country_code')} {lookup_key.get('league_name')}"
        else:
            query = f"{lookup_key.get('team_name')} {lookup_key.get('country_code')}"
        await page.goto(
            f"https://www.flashscore.com/search/?q={query.replace(' ', '+')}",
            timeout=60000
        )
        try:
            await page.wait_for_selector(".search__content", timeout=15000)
        except Exception:
            return None
        sel = ".search__section--leagues a" if table == "leagues" else ".search__section--participant a"
        el  = await page.query_selector(sel)
        if el:
            href  = await el.get_attribute("href")
            parts = [p for p in (href or "").split("/") if p]
            if parts:
                return parts[-1]
        return None
    except Exception as e:
        print(f"      [SearchErr] {e}")
        return None
    finally:
        await page.close()


# ═══════════════════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Enrich Flashscore leagues -> SQLite (column-level gap scan by default)"
    )
    parser.add_argument("--limit",       type=str, default=None, metavar="N or START-END",
                        help="Limit leagues to process: single number or range (501-1000)")
    parser.add_argument("--reset",       action="store_true",
                        help="Reset all leagues to unprocessed and re-enrich everything")
    parser.add_argument("--refresh",     action="store_true",
                        help="Re-process stale leagues (>7 days old)")
    parser.add_argument("--seasons",     type=int, default=0, metavar="N",
                        help="Number of past seasons to extract (e.g. 5)")
    parser.add_argument("--season",      type=int, default=None, metavar="N",
                        help="Season offset: 0=current, 1=most recent past, 2=second past, ...")
    parser.add_argument("--all-seasons", action="store_true",
                        help="Extract all available seasons via /archive/")
    parser.add_argument("--scan-only",   action="store_true",
                        help="Run gap scan and print report, then exit without enriching")
    parser.add_argument("--min-severity", default="important",
                        choices=["critical", "important", "enrichable"],
                        help="Minimum gap severity to include in enrichment targets (default: important)")
    args = parser.parse_args()

    limit_count = None
    offset      = 0
    if args.limit:
        if "-" in args.limit:
            start, end  = args.limit.split("-", 1)
            offset      = int(start.strip()) - 1
            limit_count = int(end.strip()) - offset
        else:
            limit_count = int(args.limit)

    asyncio.run(main(
        limit=limit_count,
        offset=offset,
        reset=args.reset,
        num_seasons=args.seasons,
        all_seasons=args.all_seasons,
        target_season=args.season,
        refresh=args.refresh,
        scan_only=args.scan_only,
        min_severity=args.min_severity,
    ))