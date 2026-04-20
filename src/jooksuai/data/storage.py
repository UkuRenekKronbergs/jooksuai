"""SQLite cache for training activities.

Per the plan (Risk 2, B-plan): Strava rate-limits to ~100 requests / 15 min, so
we cache every fetched activity locally and only ask the API for the delta.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from datetime import date
from pathlib import Path

from .models import AthleteProfile, TrainingActivity

_SCHEMA = """
CREATE TABLE IF NOT EXISTS training_activities (
    id                   TEXT PRIMARY KEY,
    activity_date        TEXT NOT NULL,
    activity_type        TEXT NOT NULL,
    distance_km          REAL NOT NULL,
    duration_min         REAL NOT NULL,
    avg_hr               INTEGER,
    max_hr_observed      INTEGER,
    avg_pace_min_per_km  REAL,
    elevation_gain_m     REAL,
    rpe                  INTEGER,
    notes                TEXT
);

CREATE INDEX IF NOT EXISTS idx_activities_date
    ON training_activities (activity_date);

CREATE TABLE IF NOT EXISTS athlete_profile (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    payload     TEXT NOT NULL
);
"""


class ActivityStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def upsert_activities(self, activities: Iterable[TrainingActivity]) -> int:
        rows = [
            (
                a.id,
                a.activity_date.isoformat(),
                a.activity_type,
                a.distance_km,
                a.duration_min,
                a.avg_hr,
                a.max_hr_observed,
                a.avg_pace_min_per_km,
                a.elevation_gain_m,
                a.rpe,
                a.notes,
            )
            for a in activities
        ]
        if not rows:
            return 0
        with self._conn() as conn:
            conn.executemany(
                """
                INSERT INTO training_activities (
                    id, activity_date, activity_type, distance_km, duration_min,
                    avg_hr, max_hr_observed, avg_pace_min_per_km,
                    elevation_gain_m, rpe, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    activity_date=excluded.activity_date,
                    activity_type=excluded.activity_type,
                    distance_km=excluded.distance_km,
                    duration_min=excluded.duration_min,
                    avg_hr=excluded.avg_hr,
                    max_hr_observed=excluded.max_hr_observed,
                    avg_pace_min_per_km=excluded.avg_pace_min_per_km,
                    elevation_gain_m=excluded.elevation_gain_m,
                    rpe=COALESCE(excluded.rpe, training_activities.rpe),
                    notes=COALESCE(excluded.notes, training_activities.notes)
                """,
                rows,
            )
        return len(rows)

    def list_activities(
        self, since: date | None = None, until: date | None = None
    ) -> list[TrainingActivity]:
        query = "SELECT * FROM training_activities WHERE 1=1"
        params: list = []
        if since:
            query += " AND activity_date >= ?"
            params.append(since.isoformat())
        if until:
            query += " AND activity_date <= ?"
            params.append(until.isoformat())
        query += " ORDER BY activity_date ASC"
        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()
        return [_row_to_activity(r) for r in rows]

    def latest_activity_date(self) -> date | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT MAX(activity_date) AS d FROM training_activities"
            ).fetchone()
        if row and row["d"]:
            return date.fromisoformat(row["d"])
        return None

    def set_rpe(self, activity_id: str, rpe: int, notes: str | None = None) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE training_activities SET rpe = ?, notes = COALESCE(?, notes) WHERE id = ?",
                (rpe, notes, activity_id),
            )

    def save_profile(self, profile: AthleteProfile) -> None:
        payload = json.dumps(
            {
                "name": profile.name,
                "age": profile.age,
                "sex": profile.sex,
                "max_hr": profile.max_hr,
                "resting_hr": profile.resting_hr,
                "training_years": profile.training_years,
                "season_goal": profile.season_goal,
                "personal_bests": profile.personal_bests,
            }
        )
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO athlete_profile (id, payload) VALUES (1, ?) "
                "ON CONFLICT(id) DO UPDATE SET payload = excluded.payload",
                (payload,),
            )

    def load_profile(self) -> AthleteProfile | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT payload FROM athlete_profile WHERE id = 1"
            ).fetchone()
        if not row:
            return None
        data = json.loads(row["payload"])
        return AthleteProfile(**data)


def _row_to_activity(row: sqlite3.Row) -> TrainingActivity:
    return TrainingActivity(
        id=row["id"],
        activity_date=date.fromisoformat(row["activity_date"]),
        activity_type=row["activity_type"],
        distance_km=row["distance_km"],
        duration_min=row["duration_min"],
        avg_hr=row["avg_hr"],
        max_hr_observed=row["max_hr_observed"],
        avg_pace_min_per_km=row["avg_pace_min_per_km"],
        elevation_gain_m=row["elevation_gain_m"],
        rpe=row["rpe"],
        notes=row["notes"],
    )
