# db_helpers.py: High-level database access layer for LeoBook.
# Part of LeoBook Data — Access Layer
#
# Thin wrapper over league_db.py (the SQLite source of truth).
# All function signatures are preserved for backward compatibility.

"""
Database Helpers Module
High-level database operations for managing match data and predictions.
All data persisted to leobook.db via league_db.py.
"""

import os
import json
import hashlib
from datetime import datetime as dt
from typing import Dict, Any, List, Optional
import uuid

from Data.Access.league_db import (
    init_db, get_connection, upsert_prediction, update_prediction,
    get_predictions, upsert_fixture, bulk_upsert_fixtures,
    upsert_standing, get_standings as _get_standings_db,
    upsert_league, upsert_team, upsert_fb_match, upsert_live_score,
    log_audit_event as _log_audit_db, upsert_country,
    upsert_accuracy_report, query_all, DB_PATH,
    upsert_match_odds_batch, get_fb_url_for_league,
)

# Module-level connection (lazy init)
_conn = None

def _get_conn():
    global _conn
    if _conn is None:
        _conn = init_db()
    return _conn


# ─── Initialization ───

def init_csvs():
    """Initialize the database. Legacy name preserved for compatibility."""
    print("     Initializing databases...")
    conn = _get_conn()
    init_readiness_cache_table(conn)

def init_readiness_cache_table(conn=None):
    """Initialize the readiness_cache table (Section 2 - Scalability)."""
    conn = conn or _get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS readiness_cache (
            gate_id TEXT PRIMARY KEY,
            is_ready INTEGER,
            details TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    print("     [Cache] Readiness cache table initialized.")


# ─── Audit Log ───

def log_audit_event(event_type: str, description: str, balance_before: Optional[float] = None,
                    balance_after: Optional[float] = None, stake: Optional[float] = None,
                    status: str = 'success'):
    """Logs a financial or system event to audit_log."""
    _log_audit_db(_get_conn(), {
        'id': str(uuid.uuid4()),
        'timestamp': dt.now().strftime("%Y-%m-%d %H:%M:%S"),
        'event_type': event_type,
        'description': description,
        'balance_before': balance_before,
        'balance_after': balance_after,
        'stake': stake,
        'status': status,
    })


# ─── Predictions ───

def save_prediction(match_data: Dict[str, Any], prediction_result: Dict[str, Any]):
    """UPSERTs a prediction into the database."""
    fixture_id = match_data.get('fixture_id') or match_data.get('id')
    if not fixture_id or fixture_id == 'unknown':
        print(f"   [Warning] Skipping prediction save: Missing unique fixture_id for "
              f"{match_data.get('home_team')} v {match_data.get('away_team')}")
        return

    date = match_data.get('date', dt.now().strftime("%Y-%m-%d"))

    row = {
        'fixture_id': fixture_id,
        'date': date,
        'match_time': match_data.get('match_time') or match_data.get('time', '00:00'),
        'region_league': match_data.get('region_league', 'Unknown'),
        'home_team': match_data.get('home_team', 'Unknown'),
        'away_team': match_data.get('away_team', 'Unknown'),
        'home_team_id': match_data.get('home_team_id', 'unknown'),
        'away_team_id': match_data.get('away_team_id', 'unknown'),
        'prediction': prediction_result.get('type', 'SKIP'),
        'confidence': prediction_result.get('confidence', 'Low'),
        'reason': " | ".join(prediction_result.get('reason', [])),
        'xg_home': str(prediction_result.get('xg_home', 0.0)),
        'xg_away': str(prediction_result.get('xg_away', 0.0)),
        'btts': prediction_result.get('btts', '50/50'),
        'over_2_5': prediction_result.get('over_2.5', prediction_result.get('over_2_5', '50/50')),
        'best_score': prediction_result.get('best_score', '1-1'),
        'top_scores': "|".join([f"{s['score']}({s['prob']})" for s in prediction_result.get('top_scores', [])]),
        'home_tags': "|".join(prediction_result.get('home_tags', [])),
        'away_tags': "|".join(prediction_result.get('away_tags', [])),
        'h2h_tags': "|".join(prediction_result.get('h2h_tags', [])),
        'standings_tags': "|".join(prediction_result.get('standings_tags', [])),
        'h2h_count': str(prediction_result.get('h2h_n', 0)),
        'home_form_n': str(prediction_result.get('home_form_n', 0)),
        'away_form_n': str(prediction_result.get('away_form_n', 0)),
        'generated_at': dt.now().isoformat(),
        'status': 'pending',
        'match_link': f"{match_data.get('match_link', '')}",
        'odds': str(prediction_result.get('odds', '')),
        'market_reliability_score': str(prediction_result.get('market_reliability', 0.0)),
        'home_crest_url': get_team_crest(match_data.get('home_team_id'), match_data.get('home_team')),
        'away_crest_url': get_team_crest(match_data.get('away_team_id'), match_data.get('away_team')),
        'recommendation_score': str(prediction_result.get('recommendation_score', 0)),
        'h2h_fixture_ids': json.dumps(prediction_result.get('h2h_fixture_ids', [])),
        'form_fixture_ids': json.dumps(prediction_result.get('form_fixture_ids', [])),
        'standings_snapshot': json.dumps(prediction_result.get('standings_snapshot', [])),
        'league_stage': match_data.get('league_stage', ''),
        'last_updated': dt.now().isoformat(),
    }

    upsert_prediction(_get_conn(), row)


def update_prediction_status(match_id: str, date: str, new_status: str, **kwargs):
    """Updates the status and optional fields for a prediction."""
    updates = {'status': new_status}
    updates.update(kwargs)
    update_prediction(_get_conn(), match_id, updates)


def backfill_prediction_entry(fixture_id: str, updates: Dict[str, str]):
    """Partially updates an existing prediction row. Only updates empty/Unknown fields."""
    if not fixture_id or not updates:
        return False

    conn = _get_conn()
    row = conn.execute("SELECT * FROM predictions WHERE fixture_id = ?", (fixture_id,)).fetchone()
    if not row:
        return False

    filtered = {}
    for key, value in updates.items():
        if value:
            current = row[key] if key in row.keys() else ''
            current = str(current).strip() if current else ''
            if not current or current in ('Unknown', 'N/A', 'unknown', 'None', ''):
                filtered[key] = value

    if filtered:
        update_prediction(conn, fixture_id, filtered)
        return True
    return False


def get_last_processed_info() -> Dict:
    """Loads last processed match info."""
    last_processed_info = {}
    conn = _get_conn()
    row = conn.execute(
        "SELECT fixture_id, date FROM predictions ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    if row:
        date_str = row['date']
        if date_str:
            try:
                last_processed_info = {
                    'date': date_str,
                    'id': row['fixture_id'],
                    'date_obj': dt.strptime(date_str, "%Y-%m-%d").date()
                }
                print(f"    [Resume] Last processed: ID {last_processed_info['id']} on {date_str}")
            except Exception:
                pass
    return last_processed_info


# ─── Schedules / Fixtures ───

def save_schedule_entry(match_info: Dict[str, Any]):
    """Saves a single schedule entry."""
    match_info['last_updated'] = dt.now().isoformat()
    # Map schedule CSV column names to fixture table columns
    mapped = {
        'fixture_id': match_info.get('fixture_id'),
        'date': match_info.get('date'),
        'time': match_info.get('match_time', match_info.get('time')),
        'league_id': match_info.get('league_id'),
        'home_team_name': match_info.get('home_team', match_info.get('home_team_name')),
        'away_team_name': match_info.get('away_team', match_info.get('away_team_name')),
        'home_team_id': match_info.get('home_team_id'),
        'away_team_id': match_info.get('away_team_id'),
        'home_score': match_info.get('home_score'),
        'away_score': match_info.get('away_score'),
        'match_status': match_info.get('match_status'),
        'region_league': match_info.get('region_league'),
        'match_link': match_info.get('match_link'),
        'league_stage': match_info.get('league_stage'),
    }
    upsert_fixture(_get_conn(), mapped)


def transform_streamer_match_to_schedule(m: Dict[str, Any]) -> Dict[str, Any]:
    """Transforms a raw match dictionary from the streamer into a standard Schedule entry."""
    now = dt.now()

    date_str = m.get('date')
    if not date_str:
        ts = m.get('timestamp')
        if ts:
            try:
                date_str = dt.fromisoformat(ts.replace('Z', '+00:00')).strftime("%Y-%m-%d")
            except Exception:
                date_str = now.strftime("%Y-%m-%d")
        else:
            date_str = now.strftime("%Y-%m-%d")

    league_id = m.get('league_id', '')
    if not league_id and m.get('region_league'):
        league_id = m['region_league'].replace(' - ', '_').replace(' ', '_').upper()

    return {
        'fixture_id': m.get('fixture_id'),
        'date': date_str,
        'match_time': m.get('match_time', '00:00'),
        'region_league': m.get('region_league', 'Unknown'),
        'league_id': league_id,
        'home_team': m.get('home_team', 'Unknown'),
        'away_team': m.get('away_team', 'Unknown'),
        'home_team_id': m.get('home_team_id', 'unknown'),
        'away_team_id': m.get('away_team_id', 'unknown'),
        'home_score': m.get('home_score', ''),
        'away_score': m.get('away_score', ''),
        'match_status': m.get('status', 'scheduled'),
        'match_link': m.get('match_link', ''),
        'league_stage': m.get('league_stage', ''),
        'last_updated': now.isoformat(),
    }


def save_schedule_batch(entries: List[Dict[str, Any]]):
    """Batch UPSERTs multiple schedule entries."""
    if not entries:
        return
    mapped = []
    for e in entries:
        mapped.append({
            'fixture_id': e.get('fixture_id'),
            'date': e.get('date'),
            'time': e.get('match_time', e.get('time')),
            'league_id': e.get('league_id'),
            'home_team_name': e.get('home_team', e.get('home_team_name')),
            'away_team_name': e.get('away_team', e.get('away_team_name')),
            'home_team_id': e.get('home_team_id'),
            'away_team_id': e.get('away_team_id'),
            'home_score': e.get('home_score'),
            'away_score': e.get('away_score'),
            'match_status': e.get('match_status'),
            'region_league': e.get('region_league'),
            'match_link': e.get('match_link'),
            'league_stage': e.get('league_stage'),
        })
    bulk_upsert_fixtures(_get_conn(), mapped)


def get_all_schedules() -> List[Dict[str, Any]]:
    """Loads all match schedules."""
    return query_all(_get_conn(), 'schedules')


# ─── Live Scores ───

def save_live_score_entry(match_info: Dict[str, Any]):
    """Saves or updates a live score entry."""
    match_info['last_updated'] = dt.now().isoformat()
    upsert_live_score(_get_conn(), match_info)


# ─── Standings ───

def save_standings(standings_data: List[Dict[str, Any]], region_league: str, league_id: str = ""):
    """UPSERTs standings data for a specific league."""
    if not standings_data:
        return

    last_updated = dt.now().isoformat()
    updated_count = 0

    for row in standings_data:
        row['region_league'] = region_league or row.get('region_league', 'Unknown')
        row['last_updated'] = last_updated

        t_id = row.get('team_id', '')
        l_id = league_id or row.get('league_id', '')
        if not l_id and region_league and " - " in region_league:
            l_id = region_league.split(" - ")[1].replace(' ', '_').upper()
        row['league_id'] = l_id

        if t_id and l_id:
            row['standings_key'] = f"{l_id}_{t_id}".upper()
            upsert_standing(_get_conn(), row)
            updated_count += 1

    if updated_count > 0:
        print(f"      [DB] UPSERTed {updated_count} standings entries for {region_league or league_id}")


def get_standings(region_league: str) -> List[Dict[str, Any]]:
    """Loads standings for a specific league."""
    return _get_standings_db(_get_conn(), region_league)


# ─── URL standardization ───

def _standardize_url(url: str, base_type: str = "flashscore") -> str:
    """Ensures URLs are absolute and follow standard patterns."""
    if not url or url == 'N/A' or url.startswith("data:"):
        return url

    if url.startswith("/"):
        url = f"https://www.flashscore.com{url}"

    if "/team/" in url and "https://www.flashscore.com/team/" not in url:
        clean_path = url.split("team/")[-1].strip("/")
        url = f"https://www.flashscore.com/team/{clean_path}/"
    elif "/team/" in url:
        if not url.endswith("/"):
            url += "/"

    if "flashscore.com" not in url and not url.startswith("http"):
        url = f"https://www.flashscore.com{url if url.startswith('/') else '/' + url}"

    return url


# ─── Region / League ───

def save_region_league_entry(info: Dict[str, Any]):
    """Saves or updates a single region-league entry."""
    league_id = info.get('league_id')
    region = info.get('region', 'Unknown')
    league = info.get('league', 'Unknown')
    if not league_id:
        league_id = f"{region}_{league}".replace(' ', '_').replace('-', '_').upper()

    upsert_league(_get_conn(), {
        'league_id': league_id,
        'name': info.get('league', info.get('name', league)), # Flexible name mapping
        'region': region,
        'region_flag': _standardize_url(info.get('region_flag', '')),
        'region_url': _standardize_url(info.get('region_url', '')),
        'crest': _standardize_url(info.get('league_crest', info.get('crest', ''))), # Flexible crest mapping
        'url': _standardize_url(info.get('league_url', info.get('url', ''))), # Flexible url mapping
        'date_updated': dt.now().isoformat(),
    })


# ─── Teams ───

def save_team_entry(team_info: Dict[str, Any]):
    """Saves or updates a single team entry with multi-league support."""
    team_id = team_info.get('team_id')
    if not team_id or team_id == 'unknown':
        return

    conn = _get_conn()

    # Check for existing entry to merge league_ids
    new_league_id = team_info.get('league_ids', team_info.get('region_league', ''))
    merged_league_ids = new_league_id

    row = conn.execute("SELECT league_ids FROM teams WHERE team_id = ?", (team_id,)).fetchone()
    if row and row['league_ids']:
        existing = row['league_ids'].split(';')
        if new_league_id and new_league_id not in existing:
            existing.append(new_league_id)
        merged_league_ids = ';'.join(filter(None, existing))

    upsert_team(conn, {
        'team_id': team_id,
        'name': team_info.get('name', team_info.get('team_name', 'Unknown')), # Flexible name mapping
        'league_ids': [merged_league_ids] if merged_league_ids else [],
        'crest': _standardize_url(team_info.get('team_crest', team_info.get('crest', ''))), # Flexible crest
        'url': _standardize_url(team_info.get('team_url', team_info.get('url', ''))), # Flexible url
        'country_code': team_info.get('country_code', team_info.get('country')), # Flex country
        'city': team_info.get('city'),
        'stadium': team_info.get('stadium'),
        'other_names': team_info.get('other_names'),
        'abbreviations': team_info.get('abbreviations'),
        'search_terms': team_info.get('search_terms'),
    })


def get_team_crest(team_id: str, team_name: str = "") -> str:
    """Retrieves the crest URL for a team."""
    if not team_id and not team_name:
        return ""

    conn = _get_conn()
    if team_id:
        row = conn.execute("SELECT crest FROM teams WHERE team_id = ?", (str(team_id),)).fetchone()
        if row and row['crest']:
            return row['crest']

    if team_name:
        row = conn.execute("SELECT crest FROM teams WHERE name = ?", (team_name,)).fetchone()
        if row and row['crest']:
            return row['crest']

    return ""


def propagate_crest_urls():
    """Propagates Supabase crest URLs from teams into schedules.
    Call after enrichment to ensure home_crest/away_crest in schedules
    point to Supabase-hosted URLs (not local file paths).
    """
    conn = _get_conn()
    h = conn.execute("""
        UPDATE schedules SET home_crest = (
            SELECT t.crest FROM teams t
            WHERE t.team_id = schedules.home_team_id AND t.crest LIKE 'http%'
        ) WHERE home_team_id IN (SELECT team_id FROM teams WHERE crest LIKE 'http%')
          AND (home_crest IS NULL OR home_crest NOT LIKE 'http%supabase%')
    """).rowcount
    a = conn.execute("""
        UPDATE schedules SET away_crest = (
            SELECT t.crest FROM teams t
            WHERE t.team_id = schedules.away_team_id AND t.crest LIKE 'http%'
        ) WHERE away_team_id IN (SELECT team_id FROM teams WHERE crest LIKE 'http%')
          AND (away_crest IS NULL OR away_crest NOT LIKE 'http%supabase%')
    """).rowcount
    conn.commit()
    if h + a > 0:
        print(f"    [Crest] Propagated Supabase URLs: {h} home + {a} away")


# ─── Country Code Resolution ───

def fill_national_team_country_codes(conn=None) -> int:
    """Pass 1 — Fill teams.country_code for national teams by matching team
    names against country.json + override aliases.

    Returns:
        Number of rows updated.
    """
    import json as _json
    import os as _os

    conn = conn or _get_conn()

    NAME_OVERRIDES: Dict[str, str] = {
        "ENGLAND":                  "gb-eng",
        "SCOTLAND":                 "gb-sct",
        "WALES":                    "gb-wls",
        "NORTHERN IRELAND":         "gb-nir",
        "IVORY COAST":              "ci",
        "DR CONGO":                 "cd",
        "ESWATINI":                 "sz",
        "UNITED ARAB EMIRATES":     "ae",
        "SOUTH KOREA":              "kr",
        "NORTH MACEDONIA":          "mk",
        "TRINIDAD AND TOBAGO":      "tt",
        "TRINIDAD & TOBAGO":        "tt",
        "BOSNIA AND HERZEGOVINA":   "ba",
        "BOSNIA":                   "ba",
        "USA":                      "us",
        "UNITED STATES":            "us",
        "CHINESE TAIPEI":           "tw",
        "HONG KONG":                "hk",
        "MACAU":                    "mo",
        "MACAO":                    "mo",
        "CAPE VERDE":               "cv",
        "NORTH KOREA":              "kp",
        "SOUTH SUDAN":              "ss",
        "PALESTINE":                "ps",
        "KOSOVO":                   "xk",
        "CURACAO":                  "cw",
        "SINT MAARTEN":             "sx",
        "ANTIGUA AND BARBUDA":      "ag",
        "SAINT KITTS AND NEVIS":    "kn",
        "SAINT LUCIA":              "lc",
        "SAINT VINCENT":            "vc",
    }

    country_json_path = _os.path.join(
        _os.path.dirname(_os.path.dirname(_os.path.dirname(__file__))),
        "Data", "Store", "country.json"
    )
    name_map: Dict[str, str] = dict(NAME_OVERRIDES)
    if _os.path.exists(country_json_path):
        try:
            with open(country_json_path, encoding="utf-8") as f:
                for entry in _json.load(f):
                    key = entry.get("name", "").upper()
                    if key and key not in name_map:
                        name_map[key] = entry["code"]
        except Exception:
            pass

    if not name_map:
        return 0

    rows = conn.execute("""
        SELECT id, name FROM teams
        WHERE country_code IS NULL OR country_code = ''
    """).fetchall()

    updated = 0
    for row in rows:
        row_id    = row[0] if not hasattr(row, "keys") else row["id"]
        team_name = row[1] if not hasattr(row, "keys") else row["name"]
        if not team_name:
            continue

        clean = team_name.strip()
        for suffix in (" U17", " U18", " U19", " U20", " U21", " U22", " U23",
                       " U16", " U15", " U14", " W", " Women", " Females"):
            if clean.upper().endswith(suffix.upper()):
                clean = clean[: -len(suffix)].strip()
                break

        iso = name_map.get(clean.upper())
        if iso:
            conn.execute(
                "UPDATE teams SET country_code = ? WHERE id = ?",
                (iso, row_id)
            )
            updated += 1

    if updated:
        conn.commit()
        print(f"    [CC] National team country_codes filled: {updated}")

    return updated


def fill_club_team_country_codes(conn=None) -> int:
    """Pass 2 — Fill teams.country_code for club teams via domestic league
    cross-reference. Safe to run repeatedly — only fills NULL/empty rows.

    Returns:
        Number of rows updated.
    """
    conn = conn or _get_conn()

    result = conn.execute("""
        UPDATE teams
        SET country_code = (
            SELECT l.country_code
            FROM schedules s
            JOIN leagues l ON s.league_id = l.league_id
            WHERE (s.home_team_id = teams.team_id OR s.away_team_id = teams.team_id)
              AND l.country_code IS NOT NULL
              AND l.country_code != ''
            ORDER BY l.country_code
            LIMIT 1
        )
        WHERE (country_code IS NULL OR country_code = '')
          AND team_id IS NOT NULL
          AND EXISTS (
              SELECT 1
              FROM schedules s
              JOIN leagues l ON s.league_id = l.league_id
              WHERE (s.home_team_id = teams.team_id OR s.away_team_id = teams.team_id)
                AND l.country_code IS NOT NULL
                AND l.country_code != ''
          )
    """)
    updated = result.rowcount
    if updated:
        conn.commit()
        print(f"    [CC] Club team country_codes filled via domestic leagues: {updated}")

    return updated


def fill_all_country_codes(conn=None) -> int:
    """Run both country_code fill passes in order.

    Pass 1 — national teams (name lookup via country.json)
    Pass 2 — club teams (domestic league cross-reference)

    Returns total rows updated across both passes.
    """
    conn = conn or _get_conn()
    total = 0
    total += fill_national_team_country_codes(conn)
    total += fill_club_team_country_codes(conn)
    return total


# ─── Football.com Registry ───

def get_site_match_id(date: str, home: str, away: str) -> str:
    """Generate a unique ID for a site match to prevent duplicates."""
    unique_str = f"{date}_{home}_{away}".lower().strip()
    return hashlib.md5(unique_str.encode()).hexdigest()


def save_site_matches(matches: List[Dict[str, Any]]):
    """UPSERTs a list of matches extracted from Football.com into the registry."""
    if not matches:
        return

    conn = _get_conn()
    last_extracted = dt.now().isoformat()

    for match in matches:
        site_id = get_site_match_id(match.get('date', ''), match.get('home', ''), match.get('away', ''))
        upsert_fb_match(conn, {
            'site_match_id': site_id,
            'date': match.get('date'),
            'time': match.get('time', 'N/A'),
            'home_team': match.get('home'),
            'away_team': match.get('away'),
            'league': match.get('league'),
            'url': match.get('url'),
            'last_extracted': last_extracted,
            'fixture_id': match.get('fixture_id', ''),
            'matched': match.get('matched', 'No_fs_match_found'),
            'booking_status': match.get('booking_status', 'pending'),
            'booking_details': match.get('booking_details', ''),
            'booking_code': match.get('booking_code', ''),
            'booking_url': match.get('booking_url', ''),
            'status': match.get('status', ''),
        })


def save_match_odds(odds_list: List[Dict[str, Any]]) -> int:
    """Persist match odds to SQLite immediately. Returns rows written."""
    return upsert_match_odds_batch(_get_conn(), odds_list)


def get_match_odds(fixture_id: str) -> List[Dict[str, Any]]:
    """Return all odds rows for a fixture ordered by rank."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM match_odds WHERE fixture_id = ? "
        "ORDER BY rank_in_list ASC",
        (fixture_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def load_site_matches(target_date: str) -> List[Dict[str, Any]]:
    """Loads all extracted site matches for a specific date."""
    return query_all(_get_conn(), 'fb_matches', 'date = ?', (target_date,))


def load_harvested_site_matches(target_date: str) -> List[Dict[str, Any]]:
    """Loads all harvested site matches for a specific date."""
    return query_all(_get_conn(), 'fb_matches',
                     "date = ? AND booking_status = 'harvested'", (target_date,))


def update_site_match_status(site_match_id: str, status: str,
                             fixture_id: Optional[str] = None,
                             details: Optional[str] = None,
                             booking_code: Optional[str] = None,
                             booking_url: Optional[str] = None,
                             matched: Optional[str] = None, **kwargs):
    """Updates the booking status, fixture_id, or booking details for a site match."""
    conn = _get_conn()
    updates = {'booking_status': status, 'status': status, 'last_updated': dt.now().isoformat()}
    if fixture_id:
        updates['fixture_id'] = fixture_id
    if details:
        updates['booking_details'] = details
    if booking_code:
        updates['booking_code'] = booking_code
    if booking_url:
        updates['booking_url'] = booking_url
    if matched:
        updates['matched'] = matched
    if 'odds' in kwargs:
        updates['odds'] = kwargs['odds']

    set_clause = ", ".join([f"{k} = :{k}" for k in updates.keys()])
    updates['site_match_id'] = site_match_id
    conn.execute(f"UPDATE fb_matches SET {set_clause} WHERE site_match_id = :site_match_id", updates)
    conn.commit()


# ─── Market Outcome Evaluator (moved to market_evaluator.py) ───
from Data.Access.market_evaluator import evaluate_market_outcome  # noqa: re-export


def _read_csv(filepath: str) -> List[Dict[str, str]]:
    """Legacy: reads from SQLite instead of CSV."""
    table_map = {
        PREDICTIONS_CSV: 'predictions',
        SCHEDULES_CSV: 'schedules',
        STANDINGS_CSV: 'standings',
        TEAMS_CSV: 'teams',
        REGION_LEAGUE_CSV: 'leagues',
        FB_MATCHES_CSV: 'fb_matches',
        AUDIT_LOG_CSV: 'audit_log',
        LIVE_SCORES_CSV: 'live_scores',
        COUNTRIES_CSV: 'countries',
        ACCURACY_REPORTS_CSV: 'accuracy_reports',
    }
    table = table_map.get(filepath)
    if table:
        return query_all(_get_conn(), table)
    return []

def _write_csv(filepath: str, data: List[Dict], fieldnames: List[str]):
    """Legacy no-op: writes go through SQLite now."""
    pass

# ─── Paper Trading ───
# ─── Paper Trade Helpers (moved to paper_trade_helpers.py) ───
from Data.Access.paper_trade_helpers import (  # noqa: re-export
    save_paper_trade,
    update_paper_trade_outcome,
    get_paper_trading_summary,
)


def _append_to_csv(filepath: str, data_row: Dict, fieldnames: List[str]):
    """Legacy no-op."""
    pass

def upsert_entry(filepath: str, data_row: Dict, fieldnames: List[str], unique_key: str):
    """Legacy: routes to appropriate SQLite upsert."""
    pass

def batch_upsert(filepath: str, data_rows: List[Dict], fieldnames: List[str], unique_key: str):
    """Legacy: routes to appropriate SQLite batch upsert."""
    pass

append_to_csv = _append_to_csv

# Legacy CSV_LOCK — no longer needed, WAL handles concurrency
import asyncio
CSV_LOCK = asyncio.Lock()

# Legacy headers dict — kept for any external code referencing it
files_and_headers = {}
