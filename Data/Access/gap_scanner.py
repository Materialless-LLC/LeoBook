# Data/Access/gap_scanner.py
# LeoBook — Column-Level Gap Scanner
#
# Scans all three enrichment tables (leagues, teams, schedules) for missing or
# invalid data at the individual cell level. Tracks every gap back to its
# originating (league_id, season) pair so the enrichment pipeline can re-process
# only the specific leagues and seasons that are actually incomplete — not the
# entire dataset.
#
# ── What counts as a gap? ────────────────────────────────────────────────────
#   - NULL value
#   - Empty string ""
#   - For crest/image URL columns: any value that does NOT start with "http"
#     (i.e. a local path like "Data/Store/crests/teams/arsenal.png")
#   - For score columns: NULL is acceptable for upcoming fixtures, so scores
#     are excluded from gap detection on scheduled matches.
#
# ── Severity tiers ───────────────────────────────────────────────────────────
#   critical   — blocks match resolution or prediction pipeline. Must fix.
#   important  — degrades app UX or crest display. Should fix.
#   enrichable — nice to have; can be back-filled without a browser session.
#
# ── Output ───────────────────────────────────────────────────────────────────
#   GapScanner(conn).scan() → GapReport
#
#   GapReport exposes:
#     .leagues_needing_enrichment()  → list consumable by enrich_leagues.main()
#     .print_report()                → human-readable CLI summary
#     .to_dict()                     → JSON-serialisable dict for logging
#     .has_gaps                      → bool
#     .total_gaps                    → int
#
# Usage:
#   from Data.Access.gap_scanner import GapScanner
#   report = GapScanner(conn).scan()
#   report.print_report()
#   targets = report.leagues_needing_enrichment()

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple

__all__ = [
    "GapScanner",
    "GapReport",
    "LeagueSeasonGapSummary",
    "ColumnGap",
    "ColumnSpec",
    "REQUIRED_COLUMNS",
]

logger = logging.getLogger(__name__)

# ── Column specification ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class ColumnSpec:
    """Describes a single required column and how to validate it."""
    name: str
    severity: str                   # "critical" | "important" | "enrichable"
    url_column: bool = False        # if True: value must start with "http"
    nullable_when: str = ""        # e.g. "match_status=scheduled" — skip gap for these rows
    description: str = ""


# ── Required column definitions per table ────────────────────────────────────

REQUIRED_COLUMNS: Dict[str, List[ColumnSpec]] = {
    "leagues": [
        ColumnSpec("league_id",      "critical",   description="Flashscore league slug"),
        ColumnSpec("name",           "critical",   description="League display name"),
        ColumnSpec("url",            "critical",   description="Flashscore league URL"),
        ColumnSpec("country_code",   "critical",   description="ISO country code"),
        ColumnSpec("fs_league_id",   "important",  description="Flashscore internal tournament ID"),
        ColumnSpec("region",         "important",  description="Human-readable region/country name"),
        ColumnSpec("region_url",     "enrichable", description="Region landing page URL"),
        ColumnSpec("crest",          "important",  url_column=True, description="League crest — must be Supabase URL"),
        ColumnSpec("current_season", "important",  description="E.g. '2024/2025' or '2024'"),
    ],
    "teams": [
        ColumnSpec("name",         "critical",   description="Team display name"),
        ColumnSpec("country_code", "critical",   description="ISO country code"),
        ColumnSpec("team_id",      "important",  description="Flashscore team ID"),
        ColumnSpec("crest",        "important",  url_column=True, description="Team crest — must be Supabase URL"),
        ColumnSpec("url",          "enrichable", description="Flashscore team page URL"),
    ],
    "schedules": [
        ColumnSpec("fixture_id",     "critical",   description="Flashscore fixture ID"),
        ColumnSpec("date",           "critical",   description="Match date YYYY-MM-DD"),
        ColumnSpec("league_id",      "critical",   description="Parent league slug"),
        ColumnSpec("season",         "critical",   description="Season string"),
        ColumnSpec("home_team_name", "critical",   description="Home team name"),
        ColumnSpec("away_team_name", "critical",   description="Away team name"),
        ColumnSpec("match_status",   "important",  description="finished/scheduled/live/etc."),
        ColumnSpec("home_team_id",   "important",  description="Home team Flashscore ID"),
        ColumnSpec("away_team_id",   "important",  description="Away team Flashscore ID"),
        ColumnSpec("home_crest",     "important",  url_column=True, description="Home team crest — must be Supabase URL"),
        ColumnSpec("away_crest",     "important",  url_column=True, description="Away team crest — must be Supabase URL"),
        ColumnSpec("time",           "enrichable", description="Match kick-off time HH:MM"),
        ColumnSpec("league_stage",   "enrichable", description="Round/group label"),
    ],
}

# Severity order for sorting (lower = more urgent)
_SEVERITY_ORDER = {"critical": 0, "important": 1, "enrichable": 2}


# ── Gap record ────────────────────────────────────────────────────────────────

@dataclass
class ColumnGap:
    """A single missing or invalid cell detected during scanning."""
    table: str
    column: str
    severity: str
    row_id: int                       # SQLite rowid
    league_id: str                    # parent league_id (resolved for teams)
    season: Optional[str]             # None for league-level gaps
    current_value: Optional[str]      # what is actually stored (for context)
    extra: Dict = field(default_factory=dict)  # e.g. fixture_id, team_name

    @property
    def is_critical(self) -> bool:
        return self.severity == "critical"

    @property
    def is_url_gap(self) -> bool:
        """True if the column has a value but it's a local path, not a URL."""
        return (
            self.current_value is not None
            and self.current_value != ""
            and not self.current_value.startswith("http")
        )


# ── Per-league summary ────────────────────────────────────────────────────────

@dataclass
class LeagueSeasonGapSummary:
    """Aggregated gap information for a single (league_id) across all its seasons."""
    league_id: str
    league_name: str
    league_url: str
    country_code: str
    continent: str

    # Seasons that need targeted re-enrichment (from schedules/teams gaps)
    seasons_with_gaps: List[str] = field(default_factory=list)

    # Column-level counts: {column_name: gap_count}
    gap_counts_by_column: Dict[str, int] = field(default_factory=dict)

    # Severity totals: {severity: count}
    severity_counts: Dict[str, int] = field(default_factory=lambda: {
        "critical": 0, "important": 0, "enrichable": 0
    })

    total_gaps: int = 0
    has_league_level_gaps: bool = False   # gaps in leagues table itself
    has_team_gaps: bool = False
    has_schedule_gaps: bool = False
    needs_full_re_enrich: bool = False    # True if league-level critical gaps found

    def add_gap(self, gap: ColumnGap) -> None:
        col_key = f"{gap.table}.{gap.column}"
        self.gap_counts_by_column[col_key] = self.gap_counts_by_column.get(col_key, 0) + 1
        self.severity_counts[gap.severity] = self.severity_counts.get(gap.severity, 0) + 1
        self.total_gaps += 1

        if gap.table == "leagues":
            self.has_league_level_gaps = True
            if gap.is_critical:
                self.needs_full_re_enrich = True
        elif gap.table == "teams":
            self.has_team_gaps = True
        elif gap.table == "schedules":
            self.has_schedule_gaps = True
            if gap.season and gap.season not in self.seasons_with_gaps:
                self.seasons_with_gaps.append(gap.season)

    def to_enrichment_target(self) -> Dict:
        """Return a dict consumable by enrich_leagues.enrich_single_league()."""
        return {
            "league_id":          self.league_id,
            "name":               self.league_name,
            "url":                self.league_url,
            "country_code":       self.country_code,
            "continent":          self.continent,
            "seasons_with_gaps":  sorted(set(self.seasons_with_gaps)),
            "needs_full_re_enrich": self.needs_full_re_enrich,
            "gap_summary": {
                "total":          self.total_gaps,
                "critical":       self.severity_counts.get("critical", 0),
                "important":      self.severity_counts.get("important", 0),
                "enrichable":     self.severity_counts.get("enrichable", 0),
                "by_column":      self.gap_counts_by_column,
                "has_league_gaps":   self.has_league_level_gaps,
                "has_team_gaps":     self.has_team_gaps,
                "has_schedule_gaps": self.has_schedule_gaps,
            },
        }


# ── Gap report ────────────────────────────────────────────────────────────────

@dataclass
class GapReport:
    """Full scan result. All gaps across leagues, teams, and schedules tables."""
    scanned_at: datetime
    summary_by_league: Dict[str, LeagueSeasonGapSummary]  # keyed by league_id
    all_gaps: List[ColumnGap]
    total_gaps: int
    scan_duration_ms: int

    # Quick-access breakdowns
    gaps_by_table: Dict[str, int] = field(default_factory=dict)
    gaps_by_severity: Dict[str, int] = field(default_factory=dict)
    gaps_by_column: Dict[str, int] = field(default_factory=dict)

    @property
    def has_gaps(self) -> bool:
        return self.total_gaps > 0

    @property
    def critical_gap_count(self) -> int:
        return self.gaps_by_severity.get("critical", 0)

    def leagues_needing_enrichment(
        self,
        min_severity: str = "important",
        limit: Optional[int] = None,
    ) -> List[Dict]:
        """Return enrichment targets sorted by severity urgency.

        Args:
            min_severity: Minimum severity to include.
                          "critical" — only leagues blocking the pipeline.
                          "important" — also includes crest/status gaps (default).
                          "enrichable" — include all gaps including minor ones.
            limit: Cap the number of leagues returned.

        Returns:
            List of dicts consumable by enrich_leagues.enrich_single_league().
            Each dict includes 'seasons_with_gaps' so the enricher can target
            only the broken seasons rather than the full league history.
        """
        severity_threshold = _SEVERITY_ORDER.get(min_severity, 1)

        targets = []
        for league_id, summary in self.summary_by_league.items():
            max_sev = min(
                _SEVERITY_ORDER.get(s, 9)
                for s, cnt in summary.severity_counts.items()
                if cnt > 0
            ) if summary.total_gaps > 0 else 9

            if max_sev <= severity_threshold:
                targets.append(summary.to_enrichment_target())

        # Sort: critical-only leagues first, then by total gap count desc
        targets.sort(
            key=lambda t: (
                0 if t["gap_summary"]["critical"] > 0 else 1,
                -t["gap_summary"]["total"],
            )
        )

        if limit:
            targets = targets[:limit]

        return targets

    def gaps_for_league_season(
        self, league_id: str, season: Optional[str] = None
    ) -> List[ColumnGap]:
        """Return all gaps for a specific league (and optionally season)."""
        return [
            g for g in self.all_gaps
            if g.league_id == league_id
            and (season is None or g.season == season)
        ]

    def print_report(self, show_row_details: bool = False) -> None:
        """Print a structured CLI gap report."""
        border = "=" * 70
        print(f"\n{border}")
        print(f"  GAP SCAN REPORT  —  {self.scanned_at.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"  Scan duration: {self.scan_duration_ms}ms")
        print(border)

        if not self.has_gaps:
            print("  [✓] All columns fully enriched. No gaps found.")
            print(f"{border}\n")
            return

        print(f"  Total gaps: {self.total_gaps:,}")
        print(f"  ├─ Critical:   {self.gaps_by_severity.get('critical',   0):>6,}")
        print(f"  ├─ Important:  {self.gaps_by_severity.get('important',  0):>6,}")
        print(f"  └─ Enrichable: {self.gaps_by_severity.get('enrichable', 0):>6,}")

        print(f"\n  By table:")
        for table in ("leagues", "teams", "schedules"):
            cnt = self.gaps_by_table.get(table, 0)
            if cnt:
                print(f"  ├─ {table:<12} {cnt:>6,} gaps")

        print(f"\n  By column (top 15):")
        sorted_cols = sorted(self.gaps_by_column.items(), key=lambda x: -x[1])
        for col, cnt in sorted_cols[:15]:
            bar = "█" * min(40, max(1, cnt * 40 // max(self.gaps_by_column.values())))
            print(f"  ├─ {col:<35} {cnt:>6,}  {bar}")

        print(f"\n  Leagues with gaps ({len(self.summary_by_league)}):")
        sorted_leagues = sorted(
            self.summary_by_league.values(),
            key=lambda s: (
                0 if s.severity_counts.get("critical", 0) > 0 else 1,
                -s.total_gaps,
            )
        )

        for s in sorted_leagues[:50]:
            crit  = s.severity_counts.get("critical", 0)
            imp   = s.severity_counts.get("important", 0)
            enr   = s.severity_counts.get("enrichable", 0)
            flags = []
            if s.has_league_level_gaps:  flags.append("LEAGUE")
            if s.has_team_gaps:          flags.append("TEAMS")
            if s.has_schedule_gaps:      flags.append(f"SCHED({len(s.seasons_with_gaps)}s)")
            flag_str = " ".join(flags)

            sev_str = ""
            if crit:  sev_str += f" C:{crit}"
            if imp:   sev_str += f" I:{imp}"
            if enr:   sev_str += f" E:{enr}"

            print(f"  ├─ {s.league_id:<30} [{sev_str.strip():>15}]  {flag_str}")
            if s.seasons_with_gaps:
                seasons_str = ", ".join(s.seasons_with_gaps[:5])
                if len(s.seasons_with_gaps) > 5:
                    seasons_str += f" (+{len(s.seasons_with_gaps)-5} more)"
                print(f"  │   seasons: {seasons_str}")

        if len(sorted_leagues) > 50:
            print(f"  └─ ... and {len(sorted_leagues) - 50} more leagues")

        if show_row_details:
            print(f"\n  Sample gaps (first 20):")
            for gap in self.all_gaps[:20]:
                val_str = repr(gap.current_value)[:40] if gap.current_value else "NULL"
                print(f"  ├─ [{gap.severity.upper():<10}] {gap.table}.{gap.column:<25} "
                      f"row={gap.row_id}  val={val_str}")

        print(f"{border}\n")

    def to_dict(self) -> Dict:
        """JSON-serialisable representation for audit logging."""
        return {
            "scanned_at":       self.scanned_at.isoformat(),
            "scan_duration_ms": self.scan_duration_ms,
            "total_gaps":       self.total_gaps,
            "gaps_by_table":    self.gaps_by_table,
            "gaps_by_severity": self.gaps_by_severity,
            "gaps_by_column":   self.gaps_by_column,
            "leagues_with_gaps": [
                s.to_enrichment_target()
                for s in self.summary_by_league.values()
            ],
        }


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
    # An empty string OR a local path ("Data/...") counts as a gap.
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
        "schedules.time",     # kick-off time sometimes only known closer to date
    }

    def __init__(self, conn) -> None:
        """
        Args:
            conn: Open SQLite connection (row_factory = sqlite3.Row recommended).
        """
        self._conn = conn
        self._league_meta: Dict[str, Dict] = {}  # league_id -> {name, url, country_code, continent}
        self._team_to_leagues: Dict[int, List[str]] = {}  # team rowid -> [league_id, ...]

    # ── Public entry point ────────────────────────────────────────────────────

    def scan(
        self,
        tables: Optional[List[str]] = None,
        severity_filter: Optional[List[str]] = None,
    ) -> GapReport:
        """Run the full gap scan across all three tables.

        Args:
            tables:          Subset of ["leagues", "teams", "schedules"] to scan.
                             Defaults to all three.
            severity_filter: Only record gaps at these severity levels.
                             Defaults to all severities.

        Returns:
            GapReport with all detected gaps, grouped by league and season.
        """
        start_ts = datetime.now()
        tables_to_scan = tables or ["leagues", "teams", "schedules"]
        sev_filter: Optional[Set[str]] = set(severity_filter) if severity_filter else None

        logger.info("[GapScanner] Starting scan — tables: %s", tables_to_scan)

        # Pre-load league metadata so we can resolve league names for every gap
        self._load_league_metadata()

        # Pre-load team -> league mappings (only if scanning teams or schedules)
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

        # Aggregate totals
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
                    # Gap = NULL, empty, or not an http URL
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

                # Resolve which leagues this team belongs to
                parent_league_ids = self._team_to_leagues.get(row_id, [])

                if not parent_league_ids:
                    # Fall back: find via schedules
                    parent_league_ids = self._resolve_team_leagues_via_schedules(
                        team_name, country
                    )

                if not parent_league_ids:
                    # Orphaned team — attribute to a sentinel league_id
                    parent_league_ids = ["__orphaned__"]

                for league_id in parent_league_ids:
                    gaps.append(ColumnGap(
                        table="teams",
                        column=spec.name,
                        severity=spec.severity,
                        row_id=row_id,
                        league_id=league_id,
                        season=None,   # team gaps are not season-specific
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

            # Some columns are only required for finished matches
            nullable_for_scheduled = col_key in self._NULLABLE_FOR_SCHEDULED

            try:
                if is_url_col:
                    # Gap = NULL, empty, or not a http URL
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