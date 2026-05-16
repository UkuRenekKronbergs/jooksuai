"""SQLite cache for training activities.

Per the plan (Risk 2, B-plan): Strava rate-limits to ~100 requests / 15 min, so
we cache every fetched activity locally and only ask the API for the delta.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from .models import AthleteProfile, StravaConnection, TrainingActivity


def _resolve_writable_db_path(preferred: Path) -> Path:
    """Use the caller's preferred path when possible; fall back to a temp
    directory if the filesystem rejects mkdir.

    Streamlit Cloud and similar sandboxed hosts ship with read-only
    site-packages paths. If `CACHE_DIR` (computed at import-time) happens to
    resolve into one of those, the original `mkdir(parents=True)` raised
    PermissionError and crashed the app on first request. Falling back to
    `tempfile.gettempdir()` keeps the demo alive — at the cost of ephemeral
    state, which is already the case on those hosts anyway.
    """
    try:
        preferred.parent.mkdir(parents=True, exist_ok=True)
        return preferred
    except (PermissionError, OSError):
        fallback_dir = Path(tempfile.gettempdir()) / "vorm-cache"
        fallback_dir.mkdir(parents=True, exist_ok=True)
        return fallback_dir / preferred.name


@dataclass(frozen=True)
class DailyLogEntry:
    """One day's record for Project Plan §4.3 daily-usage tracking."""

    log_date: date
    recommended_category: str
    rationale_excerpt: str | None = None
    usefulness: int | None = None  # 1-5
    persuasiveness: int | None = None  # 1-5
    followed: str | None = None  # 'yes' | 'no' | 'partial'
    next_session_feeling: int | None = None  # 1-5
    notes: str | None = None
    created_at: datetime | None = None


@dataclass(frozen=True)
class CoachDecision:
    """One blind decision from the validating coach for Project Plan §4.2.

    The coach records their independent recommendation for the same day the
    model also produced one — comparison is computed in the UI layer.
    """

    decision_date: date
    recommended_category: str
    coach_name: str = "Ille Kukk"
    rationale: str | None = None
    notes: str | None = None
    created_at: datetime | None = None


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

-- Daily usage log — feeds Project Plan §4.3 "Isiklik igapäevane kasutus".
-- One row per day the athlete used the tool. `usefulness`, `persuasiveness`,
-- `followed` are 1–5 / yes/no/partial scales.
CREATE TABLE IF NOT EXISTS daily_log (
    log_date              TEXT PRIMARY KEY,
    recommended_category  TEXT NOT NULL,
    rationale_excerpt     TEXT,
    usefulness            INTEGER,   -- 1-5
    persuasiveness        INTEGER,   -- 1-5
    followed              TEXT,      -- 'yes' | 'no' | 'partial'
    next_session_feeling  INTEGER,   -- 1-5 subjective how the next session went
    notes                 TEXT,
    created_at            TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS strava_connection (
    id             INTEGER PRIMARY KEY CHECK (id = 1),
    client_id      TEXT NOT NULL,
    client_secret  TEXT NOT NULL,
    refresh_token  TEXT NOT NULL,
    athlete_id     TEXT,
    athlete_name   TEXT,
    scope          TEXT,
    updated_at     TEXT NOT NULL
);

-- Coach decisions — Project Plan §4.2 blind comparison validation.
-- One row per (athlete-day) since the local store is single-tenant.
CREATE TABLE IF NOT EXISTS coach_decisions (
    decision_date         TEXT PRIMARY KEY,
    coach_name            TEXT NOT NULL DEFAULT 'Ille Kukk',
    recommended_category  TEXT NOT NULL,
    rationale             TEXT,
    notes                 TEXT,
    created_at            TEXT NOT NULL
);
"""


class ActivityStore:
    def __init__(self, db_path: Path):
        self.db_path = _resolve_writable_db_path(db_path)
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
                "threshold_pace_min_per_km": profile.threshold_pace_min_per_km,
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

    # --- Strava OAuth connection -----------------------------------------

    def save_strava_connection(self, connection: StravaConnection) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO strava_connection (
                    id, client_id, client_secret, refresh_token,
                    athlete_id, athlete_name, scope, updated_at
                ) VALUES (1, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    client_id = excluded.client_id,
                    client_secret = excluded.client_secret,
                    refresh_token = excluded.refresh_token,
                    athlete_id = excluded.athlete_id,
                    athlete_name = excluded.athlete_name,
                    scope = excluded.scope,
                    updated_at = excluded.updated_at
                """,
                (
                    connection.client_id,
                    connection.client_secret,
                    connection.refresh_token,
                    connection.athlete_id,
                    connection.athlete_name,
                    connection.scope,
                    datetime.now(UTC).isoformat(),
                ),
            )

    def load_strava_connection(self) -> StravaConnection | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM strava_connection WHERE id = 1"
            ).fetchone()
        return _row_to_strava_connection(row) if row else None

    def delete_strava_connection(self) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM strava_connection WHERE id = 1")

    # --- Daily usage log (Project Plan §4.3) ------------------------------

    def save_daily_log(self, entry: DailyLogEntry) -> None:
        """Upsert one day's log row. Re-saving the same `log_date` replaces it."""
        followed = entry.followed
        if followed not in (None, "yes", "no", "partial"):
            raise ValueError(f"followed must be yes/no/partial/None, got {followed!r}")
        for field_name, value in (
            ("usefulness", entry.usefulness),
            ("persuasiveness", entry.persuasiveness),
            ("next_session_feeling", entry.next_session_feeling),
        ):
            if value is not None and not (1 <= value <= 5):
                raise ValueError(f"{field_name} must be in 1..5 or None, got {value!r}")
        created = (entry.created_at or datetime.now(UTC)).isoformat()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO daily_log (
                    log_date, recommended_category, rationale_excerpt,
                    usefulness, persuasiveness, followed,
                    next_session_feeling, notes, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(log_date) DO UPDATE SET
                    recommended_category = excluded.recommended_category,
                    rationale_excerpt    = excluded.rationale_excerpt,
                    usefulness           = excluded.usefulness,
                    persuasiveness       = excluded.persuasiveness,
                    followed             = excluded.followed,
                    next_session_feeling = excluded.next_session_feeling,
                    notes                = excluded.notes,
                    created_at           = excluded.created_at
                """,
                (
                    entry.log_date.isoformat(),
                    entry.recommended_category,
                    entry.rationale_excerpt,
                    entry.usefulness,
                    entry.persuasiveness,
                    followed,
                    entry.next_session_feeling,
                    entry.notes,
                    created,
                ),
            )

    def list_daily_logs(
        self, since: date | None = None, until: date | None = None
    ) -> list[DailyLogEntry]:
        query = "SELECT * FROM daily_log WHERE 1=1"
        params: list = []
        if since:
            query += " AND log_date >= ?"
            params.append(since.isoformat())
        if until:
            query += " AND log_date <= ?"
            params.append(until.isoformat())
        query += " ORDER BY log_date ASC"
        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()
        return [_row_to_daily_log(r) for r in rows]

    def get_daily_log(self, day: date) -> DailyLogEntry | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM daily_log WHERE log_date = ?", (day.isoformat(),)
            ).fetchone()
        return _row_to_daily_log(row) if row else None

    # --- Coach decisions (Project Plan §4.2) -----------------------------

    def save_coach_decision(self, decision: CoachDecision) -> None:
        created = (decision.created_at or datetime.now(UTC)).isoformat()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO coach_decisions (
                    decision_date, coach_name, recommended_category,
                    rationale, notes, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(decision_date) DO UPDATE SET
                    coach_name           = excluded.coach_name,
                    recommended_category = excluded.recommended_category,
                    rationale            = excluded.rationale,
                    notes                = excluded.notes,
                    created_at           = excluded.created_at
                """,
                (
                    decision.decision_date.isoformat(),
                    decision.coach_name,
                    decision.recommended_category,
                    decision.rationale,
                    decision.notes,
                    created,
                ),
            )

    def get_coach_decision(self, day: date) -> CoachDecision | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM coach_decisions WHERE decision_date = ?",
                (day.isoformat(),),
            ).fetchone()
        return _row_to_coach_decision(row) if row else None

    def list_coach_decisions(
        self, since: date | None = None, until: date | None = None,
    ) -> list[CoachDecision]:
        query = "SELECT * FROM coach_decisions WHERE 1=1"
        params: list = []
        if since:
            query += " AND decision_date >= ?"
            params.append(since.isoformat())
        if until:
            query += " AND decision_date <= ?"
            params.append(until.isoformat())
        query += " ORDER BY decision_date ASC"
        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()
        return [_row_to_coach_decision(r) for r in rows]

    def delete_coach_decision(self, day: date) -> None:
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM coach_decisions WHERE decision_date = ?",
                (day.isoformat(),),
            )


def _row_to_daily_log(row: sqlite3.Row) -> DailyLogEntry:
    created_at = None
    if row["created_at"]:
        try:
            created_at = datetime.fromisoformat(row["created_at"])
        except ValueError:
            created_at = None
    return DailyLogEntry(
        log_date=date.fromisoformat(row["log_date"]),
        recommended_category=row["recommended_category"],
        rationale_excerpt=row["rationale_excerpt"],
        usefulness=row["usefulness"],
        persuasiveness=row["persuasiveness"],
        followed=row["followed"],
        next_session_feeling=row["next_session_feeling"],
        notes=row["notes"],
        created_at=created_at,
    )


def _row_to_coach_decision(row: sqlite3.Row) -> CoachDecision:
    created_at: datetime | None = None
    raw = row["created_at"] if "created_at" in row.keys() else None
    if raw:
        try:
            created_at = datetime.fromisoformat(raw)
        except ValueError:
            created_at = None
    return CoachDecision(
        decision_date=date.fromisoformat(row["decision_date"]),
        recommended_category=row["recommended_category"],
        coach_name=row["coach_name"] or "Ille Kukk",
        rationale=row["rationale"],
        notes=row["notes"],
        created_at=created_at,
    )


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


def _row_to_strava_connection(row: sqlite3.Row) -> StravaConnection:
    return StravaConnection(
        client_id=row["client_id"],
        client_secret=row["client_secret"],
        refresh_token=row["refresh_token"],
        athlete_id=row["athlete_id"],
        athlete_name=row["athlete_name"] or "",
        scope=row["scope"] or "",
    )
