# league_db.py: Unified SQLite database layer for ALL LeoBook data.
# Part of LeoBook Data — Access Layer
#
# This is THE SINGLE source of truth for all persistent data.
# CSV files are auto-imported on first init_db() call, then renamed to .csv.bak.

import sqlite3
import csv
import json
import os
from datetime import datetime
from typing import Optional, List, Dict, Any
from Core.Utils.constants import now_ng

DB_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "Store")
DB_PATH = os.path.join(DB_DIR, "leobook.db")


def get_connection() -> sqlite3.Connection:
    """Get a thread-safe SQLite connection with WAL mode."""
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
    CREATE TABLE IF NOT EXISTS leagues (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        league_id           TEXT UNIQUE NOT NULL,
        fs_league_id        TEXT,
        country_code        TEXT,
        continent           TEXT,
        name                TEXT NOT NULL,
        crest               TEXT,
        current_season      TEXT,
        url                 TEXT,
        processed           INTEGER DEFAULT 0,
        region              TEXT,
        region_flag         TEXT,
        region_url          TEXT,
        other_names         TEXT,
        abbreviations       TEXT,
        search_terms        TEXT,
        date_updated        TEXT,
        last_updated        TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS teams (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        team_id             TEXT UNIQUE,
        name                TEXT NOT NULL,
        league_ids          JSON,
        crest               TEXT,
        country_code        TEXT,
        url                 TEXT,
        hq_crest            INTEGER DEFAULT 0,
        country             TEXT,
        city                TEXT,
        stadium             TEXT,
        other_names         TEXT,
        abbreviations       TEXT,
        search_terms        TEXT,
        last_updated        TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS fixtures (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        fixture_id          TEXT UNIQUE,
        date                TEXT,
        time                TEXT,
        league_id           TEXT,
        home_team_id        TEXT,
        home_team_name      TEXT,
        away_team_id        TEXT,
        away_team_name      TEXT,
        home_score          INTEGER,
        away_score          INTEGER,
        extra               JSON,
        league_stage        TEXT,
        match_status        TEXT,
        season              TEXT,
        home_crest          TEXT,
        away_crest          TEXT,
        url                 TEXT,
        region_league       TEXT,
        match_link          TEXT,
        last_updated        TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS predictions (
        fixture_id          TEXT PRIMARY KEY,
        date                TEXT,
        match_time          TEXT,
        region_league       TEXT,
        home_team           TEXT,
        away_team           TEXT,
        home_team_id        TEXT,
        away_team_id        TEXT,
        prediction          TEXT,
        confidence          TEXT,
        reason              TEXT,
        xg_home             REAL,
        xg_away             REAL,
        btts                TEXT,
        over_2_5            TEXT,
        best_score          TEXT,
        top_scores          TEXT,
        home_form_n         INTEGER,
        away_form_n         INTEGER,
        home_tags           TEXT,
        away_tags           TEXT,
        h2h_tags            TEXT,
        standings_tags      TEXT,
        h2h_count           INTEGER,
        actual_score        TEXT,
        outcome_correct     TEXT,
        status              TEXT DEFAULT 'pending',
        match_link          TEXT,
        odds                TEXT,
        market_reliability_score REAL,
        home_crest_url      TEXT,
        away_crest_url      TEXT,
        recommendation_score REAL,
        h2h_fixture_ids     JSON,
        form_fixture_ids    JSON,
        standings_snapshot  JSON,
        league_stage        TEXT,
        generated_at        TEXT,
        home_score          TEXT,
        away_score          TEXT,
        last_updated        TEXT DEFAULT (datetime('now'))
    );

    -- standings: REMOVED in v7.0 — computed on-the-fly from fixtures table.
    -- See computed_standings() function and Supabase computed_standings VIEW.

    CREATE TABLE IF NOT EXISTS audit_log (
        id                  TEXT PRIMARY KEY,
        timestamp           TEXT,
        event_type          TEXT,
        description         TEXT,
        balance_before      REAL,
        balance_after       REAL,
        stake               REAL,
        status              TEXT,
        last_updated        TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS fb_matches (
        site_match_id       TEXT PRIMARY KEY,
        date                TEXT,
        time                TEXT,
        home_team           TEXT,
        away_team           TEXT,
        league              TEXT,
        url                 TEXT,
        last_extracted      TEXT,
        fixture_id          TEXT,
        matched             TEXT,
        odds                TEXT,
        booking_status      TEXT,
        booking_details     TEXT,
        booking_code        TEXT,
        booking_url         TEXT,
        status              TEXT,
        last_updated        TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS live_scores (
        fixture_id          TEXT PRIMARY KEY,
        home_team           TEXT,
        away_team           TEXT,
        home_score          TEXT,
        away_score          TEXT,
        minute              TEXT,
        status              TEXT,
        region_league       TEXT,
        match_link          TEXT,
        timestamp           TEXT,
        last_updated        TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS accuracy_reports (
        report_id           TEXT PRIMARY KEY,
        timestamp           TEXT,
        volume              INTEGER,
        win_rate            REAL,
        return_pct          REAL,
        period              TEXT,
        last_updated        TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS countries (
        code                TEXT PRIMARY KEY,
        name                TEXT,
        continent           TEXT,
        capital             TEXT,
        flag_1x1            TEXT,
        flag_4x3            TEXT,
        last_updated        TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS profiles (
        id                  TEXT PRIMARY KEY,
        email               TEXT,
        username            TEXT,
        full_name           TEXT,
        avatar_url          TEXT,
        tier                TEXT,
        credits             REAL,
        created_at          TEXT,
        updated_at          TEXT,
        last_updated        TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS custom_rules (
        id                  TEXT PRIMARY KEY,
        user_id             TEXT,
        name                TEXT,
        description         TEXT,
        is_active           INTEGER,
        logic               TEXT,
        priority            INTEGER,
        created_at          TEXT,
        updated_at          TEXT,
        last_updated        TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS rule_executions (
        id                  TEXT PRIMARY KEY,
        rule_id             TEXT,
        fixture_id          TEXT,
        user_id             TEXT,
        result              TEXT,
        executed_at         TEXT,
        last_updated        TEXT DEFAULT (datetime('now'))
    );

    -- Indexes for hot-path queries (only on columns that exist at CREATE time)
    CREATE INDEX IF NOT EXISTS idx_fixtures_league ON fixtures(league_id);
    CREATE INDEX IF NOT EXISTS idx_fixtures_date ON fixtures(date);
    CREATE INDEX IF NOT EXISTS idx_fixtures_fixture_id ON fixtures(fixture_id);
    CREATE INDEX IF NOT EXISTS idx_leagues_league_id ON leagues(league_id);
    CREATE INDEX IF NOT EXISTS idx_predictions_date ON predictions(date);
    CREATE INDEX IF NOT EXISTS idx_predictions_status ON predictions(status);
"""

# Columns that need to be added to existing tables that were created
# before the unified schema. ALTER TABLE is idempotent-safe via try/except.
_ALTER_MIGRATIONS = [
    ("leagues", "region", "TEXT"),
    ("leagues", "region_flag", "TEXT"),
    ("leagues", "region_url", "TEXT"),
    ("leagues", "other_names", "TEXT"),
    ("leagues", "abbreviations", "TEXT"),
    ("leagues", "search_terms", "TEXT"),
    ("leagues", "date_updated", "TEXT"),
    ("leagues", "fs_league_id", "TEXT"),
    ("teams", "team_id", "TEXT"),
    ("teams", "country", "TEXT"),
    ("teams", "city", "TEXT"),
    ("teams", "stadium", "TEXT"),
    ("teams", "other_names", "TEXT"),
    ("teams", "abbreviations", "TEXT"),
    ("teams", "search_terms", "TEXT"),
    ("teams", "hq_crest", "INTEGER DEFAULT 0"),
    ("fixtures", "region_league", "TEXT"),
    ("fixtures", "match_link", "TEXT"),
]

# CSV file → SQLite table mapping for auto-import.
# Key: csv filename, Value: (table_name, primary_key_column, column_rename_map)
_CSV_TABLE_MAP = {
    "schedules.csv": ("fixtures", "fixture_id", {
        "match_time": "time",
        "match_link": "url",
        "home_team": "home_team_name",
        "away_team": "away_team_name",
    }),
    "teams.csv": ("teams", "team_id", {
        "team_name": "name",
        "team_crest": "crest",
        "team_url": "url",
    }),
    "region_league.csv": ("leagues", "league_id", {
        "league": "name",
        "league_crest": "crest",
        "league_url": "url",
    }),
    "predictions.csv": ("predictions", "fixture_id", {
        "over_2.5": "over_2_5",
    }),
    "audit_log.csv": ("audit_log", "id", {}),
    "fb_matches.csv": ("fb_matches", "site_match_id", {}),
    "live_scores.csv": ("live_scores", "fixture_id", {}),
    "accuracy_reports.csv": ("accuracy_reports", "report_id", {}),
    "countries.csv": ("countries", "code", {}),
    "profiles.csv": ("profiles", "id", {}),
    "custom_rules.csv": ("custom_rules", "id", {}),
    "rule_executions.csv": ("rule_executions", "id", {}),
}


# ── Computed Standings (v7.0 — replaces the old standings table) ────────────
_COMPUTED_STANDINGS_SQL = """
    WITH match_results AS (
        SELECT
            league_id,
            home_team_id AS team_id,
            home_team_name AS team_name,
            season,
            CASE WHEN home_score > away_score THEN 3 WHEN home_score = away_score THEN 1 ELSE 0 END AS points,
            CASE WHEN home_score > away_score THEN 1 ELSE 0 END AS wins,
            CASE WHEN home_score = away_score THEN 1 ELSE 0 END AS draws,
            CASE WHEN home_score < away_score THEN 1 ELSE 0 END AS losses,
            home_score AS goals_for,
            away_score AS goals_against
        FROM fixtures
        WHERE match_status = 'finished'
          AND home_score IS NOT NULL AND away_score IS NOT NULL
          AND TYPEOF(home_score) != 'text' OR CAST(home_score AS INTEGER) = home_score

        UNION ALL

        SELECT
            league_id,
            away_team_id AS team_id,
            away_team_name AS team_name,
            season,
            CASE WHEN away_score > home_score THEN 3 WHEN away_score = home_score THEN 1 ELSE 0 END,
            CASE WHEN away_score > home_score THEN 1 ELSE 0 END,
            CASE WHEN away_score = home_score THEN 1 ELSE 0 END,
            CASE WHEN away_score < home_score THEN 1 ELSE 0 END,
            away_score,
            home_score
        FROM fixtures
        WHERE match_status = 'finished'
          AND home_score IS NOT NULL AND away_score IS NOT NULL
          AND TYPEOF(home_score) != 'text' OR CAST(home_score AS INTEGER) = home_score
    )
    SELECT
        league_id, team_id, team_name, season,
        COUNT(*) AS played,
        SUM(wins) AS wins,
        SUM(draws) AS draws,
        SUM(losses) AS losses,
        SUM(goals_for) AS goals_for,
        SUM(goals_against) AS goals_against,
        SUM(goals_for) - SUM(goals_against) AS goal_difference,
        SUM(points) AS points
    FROM match_results
    WHERE 1=1 {filters}
    GROUP BY league_id, team_id, team_name, season
    ORDER BY league_id, season, points DESC, goal_difference DESC, goals_for DESC
"""


def computed_standings(conn=None, league_id=None, season=None):
    """Compute league standings on-the-fly from the fixtures table.

    Always up-to-date, even during live matches (if scores are propagated).
    Replaces the old standings table (removed in v7.0).

    Args:
        conn: SQLite connection (optional, uses default)
        league_id: Filter by league_id (optional)
        season: Filter by season (optional)

    Returns:
        List of dicts with: league_id, team_id, team_name, season,
        played, wins, draws, losses, goals_for, goals_against,
        goal_difference, points
    """
    conn = conn or init_db()
    filters = ""
    params = []
    if league_id:
        filters += " AND league_id = ?"
        params.append(league_id)
    if season:
        filters += " AND season = ?"
        params.append(season)

    sql = _COMPUTED_STANDINGS_SQL.format(filters=filters)
    cursor = conn.execute(sql, params)
    columns = [d[0] for d in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _run_alter_migrations(conn: sqlite3.Connection):
    """Add columns to existing tables. Silently skips if column already exists."""
    for table, column, col_type in _ALTER_MIGRATIONS:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
        except sqlite3.OperationalError:
            pass  # Column already exists
    conn.commit()


def _get_table_columns(conn: sqlite3.Connection, table: str) -> List[str]:
    """Get list of column names for a table."""
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [r[1] for r in rows]


def _auto_import_csvs(conn: sqlite3.Connection):
    """One-time import: read each CSV, INSERT OR IGNORE into SQLite, rename CSV to .bak."""
    for csv_name, (table, pk, rename_map) in _CSV_TABLE_MAP.items():
        csv_path = os.path.join(DB_DIR, csv_name)
        bak_path = csv_path + ".bak"

        if not os.path.exists(csv_path) or os.path.exists(bak_path):
            continue

        table_cols = _get_table_columns(conn, table)

        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        if not rows:
            os.rename(csv_path, bak_path)
            continue

        imported = 0
        for row in rows:
            # Apply column renames
            for old_name, new_name in rename_map.items():
                if old_name in row:
                    row[new_name] = row.pop(old_name)

            # Filter to only columns that exist in the table
            filtered = {k: v for k, v in row.items() if k in table_cols}
            if not filtered:
                continue

            cols = list(filtered.keys())
            placeholders = ", ".join(["?"] * len(cols))
            col_str = ", ".join(cols)
            vals = [filtered[c] for c in cols]

            try:
                conn.execute(
                    f"INSERT OR IGNORE INTO {table} ({col_str}) VALUES ({placeholders})",
                    vals,
                )
                imported += 1
            except sqlite3.Error:
                pass  # Skip bad rows

        conn.commit()
        os.rename(csv_path, bak_path)
        print(f"  [migrate] {csv_name}: {imported}/{len(rows)} rows -> {table}")


def _create_post_alter_indexes(conn: sqlite3.Connection):
    """Create indexes on columns added by ALTER TABLE."""
    post_alter_indexes = [
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_teams_team_id_unique ON teams(team_id)",
        "CREATE INDEX IF NOT EXISTS idx_teams_team_id ON teams(team_id)",
    ]
    for sql in post_alter_indexes:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass
    conn.commit()


def _reconstruct_teams_table_if_legacy_unique_exists(conn: sqlite3.Connection):
    """
    Remove legacy UNIQUE(name, country_code) constraint from teams table.
    SQLite doesn't support DROP CONSTRAINT, so we must reconstruct the table.
    """
    try:
        # Check if the constraint exists in the schema
        res = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='teams'").fetchone()
        if not res:
            return
        sql = res[0]

        # Only reconstruct if UNIQUE(name, country_code) is present
        if "UNIQUE(name, country_code)" not in sql and "UNIQUE (name, country_code)" not in sql:
            return

        print("  [Migration] Removing legacy UNIQUE constraint from teams table...")

        # 1. Create temporary table with CORRECT schema (matching _SCHEMA_SQL + migrations)
        temp_table_sql = """
            CREATE TABLE teams_new (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                team_id             TEXT UNIQUE,
                name                TEXT NOT NULL,
                league_ids          JSON,
                crest               TEXT,
                country_code        TEXT,
                url                 TEXT,
                hq_crest            INTEGER DEFAULT 0,
                country             TEXT,
                city                TEXT,
                stadium             TEXT,
                other_names         TEXT,
                abbreviations       TEXT,
                search_terms        TEXT,
                last_updated        TEXT DEFAULT (datetime('now'))
            )
        """
        conn.execute(temp_table_sql)

        # 2. Copy data. Handle columns that might not exist in old table yet
        # (Though by this point init_db has run _run_alter_migrations)
        cursor = conn.execute("PRAGMA table_info(teams)")
        existing_cols = [row[1] for row in cursor.fetchall()]
        
        target_cols = [
            'id', 'team_id', 'name', 'league_ids', 'crest', 'country_code', 'url',
            'hq_crest', 'country', 'city', 'stadium', 'other_names', 'abbreviations',
            'search_terms', 'last_updated'
        ]
        
        # Filter target_cols to only those that exist in the old table
        cols_to_copy = [c for c in target_cols if c in existing_cols]
        cols_str = ", ".join(cols_to_copy)

        conn.execute(f"INSERT INTO teams_new ({cols_str}) SELECT {cols_str} FROM teams")

        # 3. Swap tables
        conn.execute("DROP TABLE teams")
        conn.execute("ALTER TABLE teams_new RENAME TO teams")

        # 4. Re-create indexes
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_teams_team_id_unique ON teams(team_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_teams_team_id ON teams(team_id)")

        conn.commit()
        print("  [Migration] [OK] Teams table reconstructed successfully.")

    except Exception as e:
        conn.rollback()
        print(f"  [Migration] [!] Error reconstructing teams table: {e}")



def init_db(conn: Optional[sqlite3.Connection] = None) -> sqlite3.Connection:
    """Create all tables, run migrations, auto-import CSVs. Returns the connection."""
    if conn is None:
        conn = get_connection()

    conn.executescript(_SCHEMA_SQL)
    conn.commit()

    _run_alter_migrations(conn)
    _create_post_alter_indexes(conn)
    _reconstruct_teams_table_if_legacy_unique_exists(conn)
    _auto_import_csvs(conn)

    return conn


# ---------------------------------------------------------------------------
# League operations
# ---------------------------------------------------------------------------

def upsert_league(conn: sqlite3.Connection, data: Dict[str, Any]) -> int:
    """Insert or update a league. Returns the row id."""
    now = now_ng().isoformat()
    cur = conn.execute(
        """INSERT INTO leagues (league_id, fs_league_id, country_code, continent, name, crest,
               current_season, url, region, region_flag, region_url,
               other_names, abbreviations, search_terms, date_updated, last_updated)
           VALUES (:league_id, :fs_league_id, :country_code, :continent, :name, :crest,
               :current_season, :url, :region, :region_flag, :region_url,
               :other_names, :abbreviations, :search_terms, :date_updated, :last_updated)
           ON CONFLICT(league_id) DO UPDATE SET
               fs_league_id   = COALESCE(excluded.fs_league_id, leagues.fs_league_id),
               country_code   = COALESCE(excluded.country_code, leagues.country_code),
               continent      = COALESCE(excluded.continent, leagues.continent),
               name           = COALESCE(excluded.name, leagues.name),
               crest          = COALESCE(excluded.crest, leagues.crest),
               current_season = COALESCE(excluded.current_season, leagues.current_season),
               url            = COALESCE(excluded.url, leagues.url),
               region         = COALESCE(excluded.region, leagues.region),
               region_flag    = COALESCE(excluded.region_flag, leagues.region_flag),
               region_url     = COALESCE(excluded.region_url, leagues.region_url),
               other_names    = COALESCE(excluded.other_names, leagues.other_names),
               abbreviations  = COALESCE(excluded.abbreviations, leagues.abbreviations),
               search_terms   = COALESCE(excluded.search_terms, leagues.search_terms),
               date_updated   = COALESCE(excluded.date_updated, leagues.date_updated),
               last_updated   = excluded.last_updated
        """,
        {
            "league_id": data["league_id"],
            "fs_league_id": data.get("fs_league_id"),
            "country_code": data.get("country_code"),
            "continent": data.get("continent"),
            "name": data.get("name", data.get("league", "")),
            "crest": data.get("crest", data.get("league_crest")),
            "current_season": data.get("current_season"),
            "url": data.get("url", data.get("league_url")),
            "region": data.get("region"),
            "region_flag": data.get("region_flag"),
            "region_url": data.get("region_url"),
            "other_names": data.get("other_names"),
            "abbreviations": data.get("abbreviations"),
            "search_terms": data.get("search_terms"),
            "date_updated": data.get("date_updated"),
            "last_updated": now,
        },
    )
    conn.commit()
    return cur.lastrowid


def get_league_db_id(conn: sqlite3.Connection, league_id: str) -> Optional[int]:
    """Get the auto-increment id for a league by its league_id string."""
    row = conn.execute("SELECT id FROM leagues WHERE league_id = ?", (league_id,)).fetchone()
    return row["id"] if row else None


def mark_league_processed(conn: sqlite3.Connection, league_id: str):
    """Flag a league as fully enriched."""
    conn.execute(
        "UPDATE leagues SET processed = 1, last_updated = ? WHERE league_id = ?",
        (now_ng().isoformat(), league_id),
    )
    conn.commit()


def get_unprocessed_leagues(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """Return all leagues not yet processed."""
    rows = conn.execute(
        "SELECT * FROM leagues WHERE processed = 0 ORDER BY id"
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Team operations
# ---------------------------------------------------------------------------

def upsert_team(conn: sqlite3.Connection, data: Dict[str, Any]) -> int:
    """Insert or update a team by team_id. Returns the row id."""
    now = now_ng().isoformat()
    league_ids_json = json.dumps(data.get("league_ids", []))
    team_id = data.get("team_id")

    if team_id:
        # Prefer team_id as the unique key
        cur = conn.execute(
            """INSERT INTO teams (team_id, name, league_ids, crest, country_code, url,
                   country, city, stadium, other_names, abbreviations, search_terms, last_updated)
               VALUES (:team_id, :name, :league_ids, :crest, :country_code, :url,
                   :country, :city, :stadium, :other_names, :abbreviations, :search_terms, :last_updated)
               ON CONFLICT(team_id) DO UPDATE SET
                   name           = COALESCE(excluded.name, teams.name),
                   league_ids     = excluded.league_ids,
                   crest          = COALESCE(excluded.crest, teams.crest),
                   country_code   = COALESCE(excluded.country_code, teams.country_code),
                   url            = COALESCE(excluded.url, teams.url),
                   country        = COALESCE(excluded.country, teams.country),
                   city           = COALESCE(excluded.city, teams.city),
                   stadium        = COALESCE(excluded.stadium, teams.stadium),
                   other_names    = COALESCE(excluded.other_names, teams.other_names),
                   abbreviations  = COALESCE(excluded.abbreviations, teams.abbreviations),
                   search_terms   = COALESCE(excluded.search_terms, teams.search_terms),
                   last_updated   = excluded.last_updated
            """,
            {
                "team_id": team_id,
                "name": data.get("name", data.get("team_name", "")),
                "league_ids": league_ids_json,
                "crest": data.get("crest", data.get("team_crest")),
                "country_code": data.get("country_code"),
                "url": data.get("url", data.get("team_url")),
                "country": data.get("country"),
                "city": data.get("city"),
                "stadium": data.get("stadium"),
                "other_names": data.get("other_names"),
                "abbreviations": data.get("abbreviations"),
                "search_terms": data.get("search_terms"),
                "last_updated": now,
            },
        )
    else:
        # Fallback: scraper path uses name+country_code
        cur = conn.execute(
            """INSERT INTO teams (name, league_ids, crest, country_code, url, last_updated)
               VALUES (:name, :league_ids, :crest, :country_code, :url, :last_updated)
               ON CONFLICT(team_id) DO UPDATE SET
                   league_ids   = :league_ids,
                   crest        = COALESCE(excluded.crest, teams.crest),
                   url          = COALESCE(excluded.url, teams.url),
                   last_updated = excluded.last_updated
            """,
            {
                "name": data["name"],
                "league_ids": league_ids_json,
                "crest": data.get("crest"),
                "country_code": data.get("country_code"),
                "url": data.get("url"),
                "last_updated": now,
            },
        )
    conn.commit()
    return cur.lastrowid


def get_team_id(conn: sqlite3.Connection, name: str, country_code: str = None) -> Optional[int]:
    """Look up team id by name (and optionally country_code)."""
    if country_code:
        row = conn.execute(
            "SELECT id FROM teams WHERE name = ? AND country_code = ?", (name, country_code)
        ).fetchone()
    else:
        row = conn.execute("SELECT id FROM teams WHERE name = ?", (name,)).fetchone()
    return row["id"] if row else None


# ---------------------------------------------------------------------------
# Fixture operations
# ---------------------------------------------------------------------------

def upsert_fixture(conn: sqlite3.Connection, data: Dict[str, Any]) -> int:
    """Insert or update a fixture. Returns the row id."""
    now = now_ng().isoformat()
    extra_json = json.dumps(data.get("extra")) if data.get("extra") else None
    fixture_id = data.get("fixture_id", "")

    cur = conn.execute(
        """INSERT INTO fixtures (
               fixture_id, date, time, league_id,
               home_team_id, home_team_name, away_team_id, away_team_name,
               home_score, away_score, extra, league_stage,
               match_status, season, home_crest, away_crest, url,
               region_league, match_link, last_updated
           ) VALUES (
               :fixture_id, :date, :time, :league_id,
               :home_team_id, :home_team_name, :away_team_id, :away_team_name,
               :home_score, :away_score, :extra, :league_stage,
               :match_status, :season, :home_crest, :away_crest, :url,
               :region_league, :match_link, :last_updated
           )
           ON CONFLICT(fixture_id) DO UPDATE SET
               date           = COALESCE(excluded.date, fixtures.date),
               time           = COALESCE(excluded.time, fixtures.time),
               home_score     = COALESCE(excluded.home_score, fixtures.home_score),
               away_score     = COALESCE(excluded.away_score, fixtures.away_score),
               extra          = COALESCE(excluded.extra, fixtures.extra),
               match_status   = COALESCE(excluded.match_status, fixtures.match_status),
               home_crest     = COALESCE(excluded.home_crest, fixtures.home_crest),
               away_crest     = COALESCE(excluded.away_crest, fixtures.away_crest),
               region_league  = COALESCE(excluded.region_league, fixtures.region_league),
               match_link     = COALESCE(excluded.match_link, fixtures.match_link),
               last_updated   = excluded.last_updated
        """,
        {
            "fixture_id": fixture_id,
            "date": data.get("date"),
            "time": data.get("time", data.get("match_time")),
            "league_id": data.get("league_id"),
            "home_team_id": data.get("home_team_id"),
            "home_team_name": data.get("home_team_name", data.get("home_team")),
            "away_team_id": data.get("away_team_id"),
            "away_team_name": data.get("away_team_name", data.get("away_team")),
            "home_score": data.get("home_score"),
            "away_score": data.get("away_score"),
            "extra": extra_json,
            "league_stage": data.get("league_stage"),
            "match_status": data.get("match_status"),
            "season": data.get("season"),
            "home_crest": data.get("home_crest"),
            "away_crest": data.get("away_crest"),
            "url": data.get("url"),
            "region_league": data.get("region_league"),
            "match_link": data.get("match_link"),
            "last_updated": now,
        },
    )
    conn.commit()
    return cur.lastrowid


def bulk_upsert_fixtures(conn: sqlite3.Connection, fixtures: List[Dict[str, Any]]):
    """Batch insert/update fixtures for performance."""
    now = now_ng().isoformat()
    rows = []
    for f in fixtures:
        extra_json = json.dumps(f.get("extra")) if f.get("extra") else None
        rows.append((
            f.get("fixture_id", ""), f.get("date"), f.get("time", f.get("match_time")),
            f.get("league_id"),
            f.get("home_team_id"), f.get("home_team_name", f.get("home_team")),
            f.get("away_team_id"), f.get("away_team_name", f.get("away_team")),
            f.get("home_score"), f.get("away_score"),
            extra_json, f.get("league_stage"),
            f.get("match_status"), f.get("season"),
            f.get("home_crest"), f.get("away_crest"),
            f.get("url"), f.get("region_league"), f.get("match_link"), now,
        ))
    conn.executemany(
        """INSERT INTO fixtures (
               fixture_id, date, time, league_id,
               home_team_id, home_team_name, away_team_id, away_team_name,
               home_score, away_score, extra, league_stage,
               match_status, season, home_crest, away_crest, url,
               region_league, match_link, last_updated
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(fixture_id) DO UPDATE SET
               date           = COALESCE(excluded.date, fixtures.date),
               time           = COALESCE(excluded.time, fixtures.time),
               home_score     = COALESCE(excluded.home_score, fixtures.home_score),
               away_score     = COALESCE(excluded.away_score, fixtures.away_score),
               extra          = COALESCE(excluded.extra, fixtures.extra),
               match_status   = COALESCE(excluded.match_status, fixtures.match_status),
               home_crest     = COALESCE(excluded.home_crest, fixtures.home_crest),
               away_crest     = COALESCE(excluded.away_crest, fixtures.away_crest),
               region_league  = COALESCE(excluded.region_league, fixtures.region_league),
               match_link     = COALESCE(excluded.match_link, fixtures.match_link),
               last_updated   = excluded.last_updated
        """,
        rows,
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Prediction operations
# ---------------------------------------------------------------------------

def upsert_prediction(conn: sqlite3.Connection, data: Dict[str, Any]):
    """Insert or update a prediction row."""
    now = now_ng().isoformat()
    # Normalize over_2.5 → over_2_5
    if "over_2.5" in data:
        data["over_2_5"] = data.pop("over_2.5")

    cols = [
        "fixture_id", "date", "match_time", "region_league",
        "home_team", "away_team", "home_team_id", "away_team_id",
        "prediction", "confidence", "reason",
        "xg_home", "xg_away", "btts", "over_2_5",
        "best_score", "top_scores", "home_form_n", "away_form_n",
        "home_tags", "away_tags", "h2h_tags", "standings_tags",
        "h2h_count", "actual_score", "outcome_correct",
        "status", "match_link", "odds",
        "market_reliability_score", "home_crest_url", "away_crest_url",
        "recommendation_score", "h2h_fixture_ids", "form_fixture_ids",
        "standings_snapshot", "league_stage", "generated_at",
        "home_score", "away_score", "last_updated",
    ]
    values = {c: data.get(c) for c in cols}
    values["last_updated"] = now

    # JSON-serialize complex fields
    for jf in ("h2h_fixture_ids", "form_fixture_ids", "standings_snapshot"):
        if values[jf] is not None and not isinstance(values[jf], str):
            values[jf] = json.dumps(values[jf])

    present = {k: v for k, v in values.items() if v is not None}
    col_str = ", ".join(present.keys())
    placeholders = ", ".join([f":{k}" for k in present.keys()])
    updates = ", ".join([f"{k} = excluded.{k}" for k in present.keys() if k != "fixture_id"])

    conn.execute(
        f"INSERT INTO predictions ({col_str}) VALUES ({placeholders}) "
        f"ON CONFLICT(fixture_id) DO UPDATE SET {updates}",
        present,
    )
    conn.commit()


def get_predictions(conn: sqlite3.Connection, status: str = None) -> List[Dict[str, Any]]:
    """Get predictions, optionally filtered by status."""
    if status:
        rows = conn.execute("SELECT * FROM predictions WHERE status = ?", (status,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM predictions").fetchall()
    return [dict(r) for r in rows]


def update_prediction(conn: sqlite3.Connection, fixture_id: str, updates: Dict[str, Any]):
    """Update specific fields on a prediction."""
    now = now_ng().isoformat()
    updates["last_updated"] = now
    set_clause = ", ".join([f"{k} = :{k}" for k in updates.keys()])
    updates["fixture_id"] = fixture_id
    conn.execute(f"UPDATE predictions SET {set_clause} WHERE fixture_id = :fixture_id", updates)
    conn.commit()


# ---------------------------------------------------------------------------
# Standings operations
# ---------------------------------------------------------------------------

def upsert_standing(conn: sqlite3.Connection, data: Dict[str, Any]):
    """Insert or update a standings row."""
    now = now_ng().isoformat()
    conn.execute(
        """INSERT INTO standings (standings_key, league_id, team_id, team_name,
               position, played, wins, draws, losses,
               goals_for, goals_against, goal_difference, points,
               region_league, last_updated)
           VALUES (:standings_key, :league_id, :team_id, :team_name,
               :position, :played, :wins, :draws, :losses,
               :goals_for, :goals_against, :goal_difference, :points,
               :region_league, :last_updated)
           ON CONFLICT(standings_key) DO UPDATE SET
               position       = excluded.position,
               played         = excluded.played,
               wins           = excluded.wins,
               draws          = excluded.draws,
               losses         = excluded.losses,
               goals_for      = excluded.goals_for,
               goals_against  = excluded.goals_against,
               goal_difference = excluded.goal_difference,
               points         = excluded.points,
               last_updated   = excluded.last_updated
        """,
        {
            "standings_key": data["standings_key"],
            "league_id": data.get("league_id"),
            "team_id": data.get("team_id"),
            "team_name": data.get("team_name"),
            "position": data.get("position"),
            "played": data.get("played"),
            "wins": data.get("wins"),
            "draws": data.get("draws"),
            "losses": data.get("losses"),
            "goals_for": data.get("goals_for"),
            "goals_against": data.get("goals_against"),
            "goal_difference": data.get("goal_difference"),
            "points": data.get("points"),
            "region_league": data.get("region_league"),
            "last_updated": now,
        },
    )
    conn.commit()


def get_standings(conn: sqlite3.Connection, region_league: str = None) -> List[Dict[str, Any]]:
    """Get standings, optionally filtered by region_league."""
    if region_league:
        rows = conn.execute(
            "SELECT * FROM standings WHERE region_league = ? ORDER BY position",
            (region_league,),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM standings ORDER BY region_league, position").fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

def log_audit_event(conn: sqlite3.Connection, data: Dict[str, Any]):
    """Insert an audit log entry."""
    now = now_ng().isoformat()
    conn.execute(
        """INSERT INTO audit_log (id, timestamp, event_type, description,
               balance_before, balance_after, stake, status, last_updated)
           VALUES (:id, :timestamp, :event_type, :description,
               :balance_before, :balance_after, :stake, :status, :last_updated)
        """,
        {
            "id": data.get("id", now),
            "timestamp": data.get("timestamp", now),
            "event_type": data.get("event_type"),
            "description": data.get("description"),
            "balance_before": data.get("balance_before"),
            "balance_after": data.get("balance_after"),
            "stake": data.get("stake"),
            "status": data.get("status"),
            "last_updated": now,
        },
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Live scores
# ---------------------------------------------------------------------------

def upsert_live_score(conn: sqlite3.Connection, data: Dict[str, Any]):
    """Insert or update a live score entry."""
    now = now_ng().isoformat()
    conn.execute(
        """INSERT INTO live_scores (fixture_id, home_team, away_team,
               home_score, away_score, minute, status,
               region_league, match_link, timestamp, last_updated)
           VALUES (:fixture_id, :home_team, :away_team,
               :home_score, :away_score, :minute, :status,
               :region_league, :match_link, :timestamp, :last_updated)
           ON CONFLICT(fixture_id) DO UPDATE SET
               home_score     = excluded.home_score,
               away_score     = excluded.away_score,
               minute         = excluded.minute,
               status         = excluded.status,
               timestamp      = excluded.timestamp,
               last_updated   = excluded.last_updated
        """,
        {
            "fixture_id": data["fixture_id"],
            "home_team": data.get("home_team"),
            "away_team": data.get("away_team"),
            "home_score": data.get("home_score"),
            "away_score": data.get("away_score"),
            "minute": data.get("minute"),
            "status": data.get("status"),
            "region_league": data.get("region_league"),
            "match_link": data.get("match_link"),
            "timestamp": data.get("timestamp", now),
            "last_updated": now,
        },
    )
    conn.commit()


# ---------------------------------------------------------------------------
# FB Matches
# ---------------------------------------------------------------------------

def upsert_fb_match(conn: sqlite3.Connection, data: Dict[str, Any]):
    """Insert or update an fb_matches entry."""
    now = now_ng().isoformat()
    conn.execute(
        """INSERT INTO fb_matches (site_match_id, date, time, home_team, away_team,
               league, url, last_extracted, fixture_id, matched, odds,
               booking_status, booking_details, booking_code, booking_url,
               status, last_updated)
           VALUES (:site_match_id, :date, :time, :home_team, :away_team,
               :league, :url, :last_extracted, :fixture_id, :matched, :odds,
               :booking_status, :booking_details, :booking_code, :booking_url,
               :status, :last_updated)
           ON CONFLICT(site_match_id) DO UPDATE SET
               date           = COALESCE(excluded.date, fb_matches.date),
               fixture_id     = COALESCE(excluded.fixture_id, fb_matches.fixture_id),
               matched        = COALESCE(excluded.matched, fb_matches.matched),
               odds           = COALESCE(excluded.odds, fb_matches.odds),
               booking_status = COALESCE(excluded.booking_status, fb_matches.booking_status),
               status         = COALESCE(excluded.status, fb_matches.status),
               last_updated   = excluded.last_updated
        """,
        {
            "site_match_id": data["site_match_id"],
            "date": data.get("date"),
            "time": data.get("time"),
            "home_team": data.get("home_team"),
            "away_team": data.get("away_team"),
            "league": data.get("league"),
            "url": data.get("url"),
            "last_extracted": data.get("last_extracted"),
            "fixture_id": data.get("fixture_id"),
            "matched": data.get("matched"),
            "odds": data.get("odds"),
            "booking_status": data.get("booking_status"),
            "booking_details": data.get("booking_details"),
            "booking_code": data.get("booking_code"),
            "booking_url": data.get("booking_url"),
            "status": data.get("status"),
            "last_updated": now,
        },
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Countries
# ---------------------------------------------------------------------------

def upsert_country(conn: sqlite3.Connection, data: Dict[str, Any]):
    """Insert or update a country entry."""
    now = now_ng().isoformat()
    conn.execute(
        """INSERT INTO countries (code, name, continent, capital, flag_1x1, flag_4x3, last_updated)
           VALUES (:code, :name, :continent, :capital, :flag_1x1, :flag_4x3, :last_updated)
           ON CONFLICT(code) DO UPDATE SET
               name      = COALESCE(excluded.name, countries.name),
               continent = COALESCE(excluded.continent, countries.continent),
               capital   = COALESCE(excluded.capital, countries.capital),
               flag_1x1  = COALESCE(excluded.flag_1x1, countries.flag_1x1),
               flag_4x3  = COALESCE(excluded.flag_4x3, countries.flag_4x3),
               last_updated = excluded.last_updated
        """,
        {
            "code": data["code"],
            "name": data.get("name"),
            "continent": data.get("continent"),
            "capital": data.get("capital"),
            "flag_1x1": data.get("flag_1x1"),
            "flag_4x3": data.get("flag_4x3"),
            "last_updated": now,
        },
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Accuracy reports
# ---------------------------------------------------------------------------

def upsert_accuracy_report(conn: sqlite3.Connection, data: Dict[str, Any]):
    """Insert or update an accuracy report."""
    now = now_ng().isoformat()
    conn.execute(
        """INSERT INTO accuracy_reports (report_id, timestamp, volume, win_rate,
               return_pct, period, last_updated)
           VALUES (:report_id, :timestamp, :volume, :win_rate,
               :return_pct, :period, :last_updated)
           ON CONFLICT(report_id) DO UPDATE SET
               volume     = excluded.volume,
               win_rate   = excluded.win_rate,
               return_pct = excluded.return_pct,
               last_updated = excluded.last_updated
        """,
        {
            "report_id": data["report_id"],
            "timestamp": data.get("timestamp"),
            "volume": data.get("volume"),
            "win_rate": data.get("win_rate"),
            "return_pct": data.get("return_pct"),
            "period": data.get("period"),
            "last_updated": now,
        },
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Generic query helpers
# ---------------------------------------------------------------------------

def query_all(conn: sqlite3.Connection, table: str, where: str = None,
              params: tuple = (), order_by: str = None) -> List[Dict[str, Any]]:
    """Generic SELECT * from table with optional WHERE and ORDER BY."""
    sql = f"SELECT * FROM {table}"
    if where:
        sql += f" WHERE {where}"
    if order_by:
        sql += f" ORDER BY {order_by}"
    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def count_rows(conn: sqlite3.Connection, table: str) -> int:
    """Count rows in a table."""
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
