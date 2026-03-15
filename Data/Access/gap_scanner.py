# gap_scanner.py: Column-level gap scanner for LeoBook.
# Part of LeoBook Data — Access Layer
#
# Scans all three enrichment tables (leagues, teams, schedules) for missing or
# invalid data at the individual cell level. Tracks every gap back to its
# originating (league_id, season) pair so the enrichment pipeline can re-process
# only the specific leagues and seasons that are actually incomplete.
#
# Usage:
#   from Data.Access.gap_scanner import GapScanner
#   report = GapScanner(conn).scan()
#   report.print_report()
#   targets = report.leagues_needing_enrichment()

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Set

from Data.Access.gap_models import (
    ColumnSpec, ColumnGap, LeagueSeasonGapSummary, GapReport,
    REQUIRED_COLUMNS, _SEVERITY_ORDER,
)

__all__ = [
    "GapScanner",
    "GapReport",
    "LeagueSeasonGapSummary",
    "ColumnGap",
    "ColumnSpec",
    "REQUIRED_COLUMNS",
    "INTERNATIONAL_LEAGUE_PREFIXES",
    "_is_international_league",
    "scan_and_print",
    "get_enrichment_targets",
]

logger = logging.getLogger(__name__)


# ── International league detection ────────────────────────────────────────────────
INTERNATIONAL_LEAGUE_PREFIXES: frozenset = frozenset({
    "1_1", "1_2", "1_3", "1_5", "1_6", "1_7", "1_8",
})


def _is_international_league(league_id: str) -> bool:
    """Return True if the league is a continental/world tournament.

    International leagues have no meaningful country_code — continent is
    their correct geographic identifier. country_code gaps on these leagues
    and their teams are suppressed from gap reporting.
    """
    if not league_id:
        return False
    parts = league_id.split("_")
    if len(parts) < 2:
        return False
    prefix = f"{parts[0]}_{parts[1]}"
    return prefix in INTERNATIONAL_LEAGUE_PREFIXES


# ═══════════════════════════════════════════════════════════════════════════════
#  GapScanner
# ═══════════════════════════════════════════════════════════════════════════════

class GapScanner:
    """Scans leagues, teams, and schedules for missing or invalid column values.

    Usage:
        conn = get_connection()
        report = GapScanner(conn).scan()
        report.print_report()
    """

    # Columns whose values must start with "http" to be considered valid.
    _URL_REQUIRED_COLUMNS: Set[str] = {
        "leagues.crest",
        "teams.crest",
        "schedules.home_crest",
        "schedules.away_crest",
        "leagues.region_url",
    }

    # These schedule columns are allowed to be NULL for scheduled/upcoming fixtures.
    _NULLABLE_FOR_SCHEDULED: Set[str] = {
        "schedules.home_score",
        "schedules.away_score",
        "schedules.time",
    }

    def __init__(self, conn) -> None:
        self._conn = conn
        self._league_meta: Dict[str, Dict] = {}
        self._team_to_leagues: Dict[int, List[str]] = {}

    # ── Public entry point ────────────────────────────────────────────────────

    def scan(
        self,
        tables: Optional[List[str]] = None,
        severity_filter: Optional[List[str]] = None,
    ) -> GapReport:
        """Run the full gap scan across all three tables."""
        from datetime import datetime
        start_ts = datetime.now()
        tables_to_scan = tables or ["leagues", "teams", "schedules"]
        sev_filter: Optional[Set[str]] = set(severity_filter) if severity_filter else None

        logger.info("[GapScanner] Starting scan — tables: %s", tables_to_scan)

        self._load_league_metadata()

        if "teams" in tables_to_scan:
            self._load_team_league_mappings()

        all_gaps: List[ColumnGap] = []
        summary_by_league: Dict[str, LeagueSeasonGapSummary] = {}

        def _add_gap(gap: ColumnGap) -> None:
            if sev_filter and gap.severity not in sev_filter:
                return
            all_gaps.append(gap)
            if gap.league_id not in summary_by_league:
                meta = self._league_meta.get(gap.league_id, {})
                summary_by_league[gap.league_id] = LeagueSeasonGapSummary(
                    league_id=gap.league_id,
                    league_name=meta.get("name", gap.league_id),
                    league_url=meta.get("url", ""),
                    country_code=meta.get("country_code", ""),
                    continent=meta.get("continent", ""),
                )
            summary_by_league[gap.league_id].add_gap(gap)

        if "leagues" in tables_to_scan:
            for gap in self._scan_leagues_table():
                _add_gap(gap)

        if "teams" in tables_to_scan:
            for gap in self._scan_teams_table():
                _add_gap(gap)

        if "schedules" in tables_to_scan:
            for gap in self._scan_schedules_table():
                _add_gap(gap)

        gaps_by_table: Dict[str, int] = {}
        gaps_by_severity: Dict[str, int] = {}
        gaps_by_column: Dict[str, int] = {}

        for gap in all_gaps:
            gaps_by_table[gap.table] = gaps_by_table.get(gap.table, 0) + 1
            gaps_by_severity[gap.severity] = gaps_by_severity.get(gap.severity, 0) + 1
            col_key = f"{gap.table}.{gap.column}"
            gaps_by_column[col_key] = gaps_by_column.get(col_key, 0) + 1

        duration_ms = int((datetime.now() - start_ts).total_seconds() * 1000)
        total_gaps = len(all_gaps)

        logger.info(
            "[GapScanner] Scan complete in %dms — %d gaps across %d leagues",
            duration_ms, total_gaps, len(summary_by_league)
        )

        return GapReport(
            scanned_at=start_ts,
            summary_by_league=summary_by_league,
            all_gaps=all_gaps,
            total_gaps=total_gaps,
            scan_duration_ms=duration_ms,
            gaps_by_table=gaps_by_table,
            gaps_by_severity=gaps_by_severity,
            gaps_by_column=gaps_by_column,
        )

    # ── Pre-loaders ───────────────────────────────────────────────────────────

    def _load_league_metadata(self) -> None:
        """Cache all league metadata for gap -> league name resolution."""
        try:
            rows = self._conn.execute(
                "SELECT league_id, name, url, country_code, continent FROM leagues"
            ).fetchall()
            for row in rows:
                lid = row["league_id"] if hasattr(row, "keys") else row[0]
                self._league_meta[lid] = {
                    "name":         row["name"]         if hasattr(row, "keys") else row[1],
                    "url":          row["url"]          if hasattr(row, "keys") else row[2],
                    "country_code": row["country_code"] if hasattr(row, "keys") else row[3],
                    "continent":    row["continent"]    if hasattr(row, "keys") else row[4],
                }
        except Exception as e:
            logger.warning("[GapScanner] Could not load league metadata: %s", e)

    def _load_team_league_mappings(self) -> None:
        """Cache team rowid -> [league_id, ...] for resolving team gaps."""
        import json
        try:
            rows = self._conn.execute(
                "SELECT id, league_ids FROM teams WHERE league_ids IS NOT NULL AND league_ids != ''"
            ).fetchall()
            for row in rows:
                row_id     = row["id"]         if hasattr(row, "keys") else row[0]
                league_ids_raw = row["league_ids"] if hasattr(row, "keys") else row[1]
                try:
                    ids = json.loads(league_ids_raw) if isinstance(league_ids_raw, str) else league_ids_raw
                    self._team_to_leagues[row_id] = ids if isinstance(ids, list) else [str(ids)]
                except (json.JSONDecodeError, TypeError):
                    pass
        except Exception as e:
            logger.warning("[GapScanner] Could not load team->league mappings: %s", e)

    # ── Table scanners ────────────────────────────────────────────────────────

    def _scan_leagues_table(self) -> List[ColumnGap]:
        """Scan the leagues table for missing/invalid column values."""
        gaps: List[ColumnGap] = []
        specs = REQUIRED_COLUMNS.get("leagues", [])
        if not specs:
            return gaps

        for spec in specs:
            is_url_col = (
                spec.url_column
                or f"leagues.{spec.name}" in self._URL_REQUIRED_COLUMNS
            )

            try:
                if is_url_col:
                    rows = self._conn.execute(f"""
                        SELECT id, league_id, {spec.name}
                        FROM leagues
                        WHERE ({spec.name} IS NULL
                            OR {spec.name} = ''
                            OR {spec.name} NOT LIKE 'http%')
                          AND url IS NOT NULL AND url != ''
                    """).fetchall()
                else:
                    rows = self._conn.execute(f"""
                        SELECT id, league_id, {spec.name}
                        FROM leagues
                        WHERE ({spec.name} IS NULL OR {spec.name} = '')
                          AND url IS NOT NULL AND url != ''
                    """).fetchall()
            except Exception as e:
                logger.warning("[GapScanner] leagues.%s scan failed: %s", spec.name, e)
                continue

            for row in rows:
                row_id      = self._row(row, 0)
                league_id   = self._row(row, 1) or ""
                current_val = self._row(row, 2)

                # Suppress country_code gaps for international/continental leagues.
                # continent is their correct geographic identifier, not country_code.
                if spec.name == "country_code" and _is_international_league(league_id):
                    continue

                gaps.append(ColumnGap(
                    table="leagues",
                    column=spec.name,
                    severity=spec.severity,
                    row_id=row_id,
                    league_id=league_id,
                    season=None,
                    current_value=current_val,
                    extra={"league_id": league_id},
                ))

        logger.debug("[GapScanner] leagues: %d gaps found", len(gaps))
        return gaps

    def _scan_teams_table(self) -> List[ColumnGap]:
        """Scan the teams table and resolve each gap back to its parent leagues."""
        gaps: List[ColumnGap] = []
        specs = REQUIRED_COLUMNS.get("teams", [])
        if not specs:
            return gaps

        for spec in specs:
            is_url_col = (
                spec.url_column
                or f"teams.{spec.name}" in self._URL_REQUIRED_COLUMNS
            )

            try:
                if is_url_col:
                    rows = self._conn.execute(f"""
                        SELECT id, name, country_code, league_ids, {spec.name}
                        FROM teams
                        WHERE ({spec.name} IS NULL
                            OR {spec.name} = ''
                            OR {spec.name} NOT LIKE 'http%')
                    """).fetchall()
                else:
                    rows = self._conn.execute(f"""
                        SELECT id, name, country_code, league_ids, {spec.name}
                        FROM teams
                        WHERE ({spec.name} IS NULL OR {spec.name} = '')
                    """).fetchall()
            except Exception as e:
                logger.warning("[GapScanner] teams.%s scan failed: %s", spec.name, e)
                continue

            for row in rows:
                row_id      = self._row(row, 0)
                team_name   = self._row(row, 1) or ""
                country     = self._row(row, 2) or ""
                current_val = self._row(row, 4)

                parent_league_ids = self._team_to_leagues.get(row_id, [])

                if not parent_league_ids:
                    parent_league_ids = self._resolve_team_leagues_via_schedules(
                        team_name, country
                    )

                if not parent_league_ids:
                    parent_league_ids = ["__orphaned__"]

                # Suppress country_code gaps for teams exclusively in international leagues.
                if spec.name == "country_code":
                    all_international = all(
                        _is_international_league(lid) for lid in parent_league_ids
                        if lid != "__orphaned__"
                    )
                    if all_international:
                        continue

                for league_id in parent_league_ids:
                    gaps.append(ColumnGap(
                        table="teams",
                        column=spec.name,
                        severity=spec.severity,
                        row_id=row_id,
                        league_id=league_id,
                        season=None,
                        current_value=current_val,
                        extra={
                            "team_name":   team_name,
                            "country_code": country,
                        },
                    ))

        logger.debug("[GapScanner] teams: %d gaps found", len(gaps))
        return gaps

    def _scan_schedules_table(self) -> List[ColumnGap]:
        """Scan the schedules table, tracking each gap to its (league_id, season)."""
        gaps: List[ColumnGap] = []
        specs = REQUIRED_COLUMNS.get("schedules", [])
        if not specs:
            return gaps

        for spec in specs:
            is_url_col = (
                spec.url_column
                or f"schedules.{spec.name}" in self._URL_REQUIRED_COLUMNS
            )
            col_key = f"schedules.{spec.name}"
            nullable_for_scheduled = col_key in self._NULLABLE_FOR_SCHEDULED

            try:
                if is_url_col:
                    where = f"""
                        ({spec.name} IS NULL
                         OR {spec.name} = ''
                         OR {spec.name} NOT LIKE 'http%')
                    """
                else:
                    where = f"({spec.name} IS NULL OR {spec.name} = '')"

                if nullable_for_scheduled:
                    where += " AND match_status != 'scheduled'"

                rows = self._conn.execute(f"""
                    SELECT id, fixture_id, league_id, season,
                           home_team_name, away_team_name, {spec.name}
                    FROM schedules
                    WHERE {where}
                """).fetchall()

            except Exception as e:
                logger.warning("[GapScanner] schedules.%s scan failed: %s", spec.name, e)
                continue

            for row in rows:
                row_id       = self._row(row, 0)
                fixture_id   = self._row(row, 1) or ""
                league_id    = self._row(row, 2) or ""
                season       = self._row(row, 3) or ""
                home_name    = self._row(row, 4) or ""
                away_name    = self._row(row, 5) or ""
                current_val  = self._row(row, 6)

                gaps.append(ColumnGap(
                    table="schedules",
                    column=spec.name,
                    severity=spec.severity,
                    row_id=row_id,
                    league_id=league_id,
                    season=season or None,
                    current_value=current_val,
                    extra={
                        "fixture_id":    fixture_id,
                        "home_team":     home_name,
                        "away_team":     away_name,
                    },
                ))

        logger.debug("[GapScanner] schedules: %d gaps found", len(gaps))
        return gaps

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _resolve_team_leagues_via_schedules(
        self, team_name: str, country_code: str
    ) -> List[str]:
        """Find which league_ids a team has played in (via schedules table)."""
        try:
            rows = self._conn.execute("""
                SELECT DISTINCT league_id
                FROM schedules
                WHERE (home_team_name = ? OR away_team_name = ?)
                LIMIT 20
            """, (team_name, team_name)).fetchall()
            return [self._row(r, 0) for r in rows if self._row(r, 0)]
        except Exception:
            return []

    @staticmethod
    def _row(row, idx: int):
        """Safe column accessor for both sqlite3.Row and plain tuple."""
        try:
            if hasattr(row, "keys"):
                keys = list(row.keys())
                return row[keys[idx]]
            return row[idx]
        except (IndexError, KeyError):
            return None


# ═══════════════════════════════════════════════════════════════════════════════
#  Convenience functions
# ═══════════════════════════════════════════════════════════════════════════════

def scan_and_print(conn, tables: Optional[List[str]] = None) -> GapReport:
    """One-liner: scan and immediately print the report. Returns the report."""
    report = GapScanner(conn).scan(tables=tables)
    report.print_report()
    return report


def get_enrichment_targets(
    conn,
    min_severity: str = "important",
    limit: Optional[int] = None,
) -> List[Dict]:
    """Scan and return enrichment targets directly. Convenience wrapper."""
    report = GapScanner(conn).scan()
    return report.leagues_needing_enrichment(
        min_severity=min_severity,
        limit=limit,
    )