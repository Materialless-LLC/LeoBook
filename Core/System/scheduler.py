# scheduler.py: Autonomous task scheduler for Leo.py.
# Part of LeoBook Core — System
#
# Classes: TaskScheduler
# Gives Leo.py autonomy to schedule and execute tasks dynamically.
# Tasks: weekly enrichment, day-before predictions, RL training.

import json
import uuid
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any

from Core.Utils.constants import now_ng
from Data.Access.league_db import init_db


# ── Task Types ────────────────────────────────────────────────────────────────
TASK_WEEKLY_ENRICHMENT = "weekly_enrichment"
TASK_DAY_BEFORE_PREDICT = "day_before_predict"
TASK_RL_TRAINING = "rl_training"

VALID_TASK_TYPES = {TASK_WEEKLY_ENRICHMENT, TASK_DAY_BEFORE_PREDICT, TASK_RL_TRAINING}


@dataclass
class ScheduledTask:
    """A single scheduled task."""
    task_id: str
    task_type: str
    target_time: str          # ISO format datetime
    params: Dict[str, Any]    # JSON-serializable parameters
    status: str = "pending"   # pending | running | completed | failed
    created_at: str = ""

    @property
    def target_dt(self) -> datetime:
        return datetime.fromisoformat(self.target_time)

    @property
    def is_due(self) -> bool:
        return now_ng() >= self.target_dt


class TaskScheduler:
    """Manages Leo.py's autonomous task scheduling."""

    # Weekly enrichment: Monday 2:26am WAT (Africa/Lagos)
    ENRICHMENT_DAY = 0   # Monday
    ENRICHMENT_HOUR = 2
    ENRICHMENT_MINUTE = 26

    def __init__(self):
        self.conn = init_db()
        self._ensure_table()

    def _ensure_table(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS scheduled_tasks (
                task_id       TEXT PRIMARY KEY,
                task_type     TEXT NOT NULL,
                target_time   TEXT NOT NULL,
                params        TEXT DEFAULT '{}',
                status        TEXT DEFAULT 'pending',
                created_at    TEXT DEFAULT (datetime('now'))
            )
        """)
        self.conn.commit()

    # ── Core Operations ───────────────────────────────────────────────────

    def schedule_task(self, task_type: str, target_time: datetime,
                      params: Optional[Dict] = None) -> str:
        """Schedule a new task. Returns task_id."""
        if task_type not in VALID_TASK_TYPES:
            raise ValueError(f"Invalid task type: {task_type}")

        task_id = str(uuid.uuid4())[:8]
        now = now_ng().isoformat()

        self.conn.execute(
            """INSERT OR IGNORE INTO scheduled_tasks
               (task_id, task_type, target_time, params, status, created_at)
               VALUES (?, ?, ?, ?, 'pending', ?)""",
            (task_id, task_type, target_time.isoformat(), json.dumps(params or {}), now)
        )
        self.conn.commit()
        return task_id

    def get_pending_tasks(self) -> List[ScheduledTask]:
        """Get all tasks that are due now (target_time <= now)."""
        now = now_ng().isoformat()
        rows = self.conn.execute(
            """SELECT task_id, task_type, target_time, params, status, created_at
               FROM scheduled_tasks
               WHERE status = 'pending' AND target_time <= ?
               ORDER BY target_time ASC""",
            (now,)
        ).fetchall()

        return [
            ScheduledTask(
                task_id=r[0], task_type=r[1], target_time=r[2],
                params=json.loads(r[3] or '{}'), status=r[4], created_at=r[5]
            )
            for r in rows
        ]

    def complete_task(self, task_id: str, status: str = "completed"):
        """Mark a task as completed (or failed)."""
        self.conn.execute(
            "UPDATE scheduled_tasks SET status = ? WHERE task_id = ?",
            (status, task_id)
        )
        self.conn.commit()

    def next_wake_time(self) -> Optional[datetime]:
        """Get the earliest pending task time. Returns None if no tasks."""
        row = self.conn.execute(
            """SELECT MIN(target_time) FROM scheduled_tasks
               WHERE status = 'pending'"""
        ).fetchone()
        if row and row[0]:
            return datetime.fromisoformat(row[0])
        return None

    def has_pending(self, task_type: str) -> bool:
        """Check if a pending task of this type already exists."""
        row = self.conn.execute(
            "SELECT COUNT(*) FROM scheduled_tasks WHERE task_type = ? AND status = 'pending'",
            (task_type,)
        ).fetchone()
        return row[0] > 0

    def cleanup_old(self, days: int = 7):
        """Remove completed/failed tasks older than N days."""
        cutoff = (now_ng() - timedelta(days=days)).isoformat()
        self.conn.execute(
            "DELETE FROM scheduled_tasks WHERE status IN ('completed', 'failed') AND created_at < ?",
            (cutoff,)
        )
        self.conn.commit()

    # ── Smart Scheduling ──────────────────────────────────────────────────

    def schedule_weekly_enrichment(self):
        """Schedule next Monday 2:26am enrichment if not already scheduled."""
        if self.has_pending(TASK_WEEKLY_ENRICHMENT):
            return

        now = now_ng()
        # Find next Monday
        days_ahead = self.ENRICHMENT_DAY - now.weekday()
        if days_ahead <= 0:
            days_ahead += 7
        next_monday = now.replace(
            hour=self.ENRICHMENT_HOUR, minute=self.ENRICHMENT_MINUTE,
            second=0, microsecond=0
        ) + timedelta(days=days_ahead)

        task_id = self.schedule_task(
            TASK_WEEKLY_ENRICHMENT, next_monday,
            params={"max_show_more": 2, "skip_images": True}
        )
        print(f"  [Scheduler] Weekly enrichment scheduled for {next_monday.strftime('%A %Y-%m-%d %H:%M')} (task: {task_id})")

    def schedule_day_before_predictions(self, fixtures: List[Dict]):
        """For teams with multiple fixtures in 7 days, schedule day-before predictions.

        Args:
            fixtures: List of fixture dicts with fixture_id, home_team_id, away_team_id, date
        """
        from collections import defaultdict

        # Group fixtures by team
        team_fixtures = defaultdict(list)
        for f in fixtures:
            team_fixtures[f.get('home_team_id', '')].append(f)
            team_fixtures[f.get('away_team_id', '')].append(f)

        # For each team, the earliest fixture gets predicted now.
        # Remaining get scheduled for day-before.
        scheduled_ids = set()
        predict_now_ids = set()

        for team_id, team_fx in team_fixtures.items():
            if not team_id:
                continue
            # Sort by date
            sorted_fx = sorted(team_fx, key=lambda x: x.get('date', ''))
            predict_now_ids.add(sorted_fx[0].get('fixture_id'))

            for fx in sorted_fx[1:]:
                fid = fx.get('fixture_id', '')
                if fid in scheduled_ids:
                    continue

                match_date_str = fx.get('date', '')
                if not match_date_str:
                    continue

                try:
                    match_date = datetime.strptime(match_date_str, '%Y-%m-%d')
                    # Schedule for day before at 2:26am
                    target = match_date.replace(
                        hour=self.ENRICHMENT_HOUR, minute=self.ENRICHMENT_MINUTE,
                        second=0
                    ) - timedelta(days=1)

                    if target > now_ng():
                        self.schedule_task(
                            TASK_DAY_BEFORE_PREDICT, target,
                            params={"fixture_id": fid}
                        )
                        scheduled_ids.add(fid)
                except ValueError:
                    continue

        if scheduled_ids:
            print(f"  [Scheduler] Scheduled {len(scheduled_ids)} day-before predictions")

        return predict_now_ids, scheduled_ids
