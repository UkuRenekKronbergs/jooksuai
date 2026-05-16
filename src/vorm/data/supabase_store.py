"""Supabase-backed store for per-user persistent state.

Replaces the local SQLite ActivityStore for athlete profile, daily log, and
Strava OAuth connection metadata when Supabase credentials are configured.
Each method requires an authenticated `user_id`; Row-Level Security policies
enforce per-user isolation at the database level (see `docs/supabase_schema.sql`).

The Strava activity delta-sync cache continues to use local SQLite files —
that's an HTTP cache, not the authoritative user connection.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from .models import AthleteProfile, StravaConnection
from .storage import CoachDecision, DailyLogEntry

if TYPE_CHECKING:
    from supabase import Client


class SupabaseNotConfigured(RuntimeError):
    """Raised when Supabase client construction is attempted without creds."""


@dataclass(frozen=False, eq=False)
class SupabaseStore:
    """Per-user façade over Supabase tables for profile, logs, and Strava.

    Construct via `SupabaseStore(client=..., user_id=...)`. The `client` is
    expected to already have a session bound to `user_id` (so PostgREST
    requests include the JWT that RLS policies check). See `vorm.auth`.
    """

    client: Client
    user_id: str

    # --- profile ----------------------------------------------------------

    def save_profile(self, profile: AthleteProfile) -> None:
        payload: dict[str, Any] = {
            "user_id": self.user_id,
            "name": profile.name,
            "age": profile.age,
            "sex": profile.sex,
            "max_hr": profile.max_hr,
            "resting_hr": profile.resting_hr,
            "training_years": profile.training_years,
            "season_goal": profile.season_goal or "",
            "personal_bests": profile.personal_bests or {},
            "threshold_pace_min_per_km": profile.threshold_pace_min_per_km,
        }
        self.client.table("athlete_profiles").upsert(
            payload, on_conflict="user_id"
        ).execute()

    def load_profile(self) -> AthleteProfile | None:
        resp = (
            self.client.table("athlete_profiles")
            .select("*")
            .eq("user_id", self.user_id)
            .limit(1)
            .execute()
        )
        rows = resp.data or []
        if not rows:
            return None
        return _row_to_profile(rows[0])

    # --- Strava OAuth connection -----------------------------------------

    def save_strava_connection(self, connection: StravaConnection) -> None:
        payload: dict[str, Any] = {
            "user_id": self.user_id,
            "client_id": connection.client_id,
            "client_secret": connection.client_secret,
            "refresh_token": connection.refresh_token,
            "athlete_id": connection.athlete_id,
            "athlete_name": connection.athlete_name,
            "scope": connection.scope,
        }
        self.client.table("strava_connections").upsert(
            payload, on_conflict="user_id"
        ).execute()

    def load_strava_connection(self) -> StravaConnection | None:
        resp = (
            self.client.table("strava_connections")
            .select("*")
            .eq("user_id", self.user_id)
            .limit(1)
            .execute()
        )
        rows = resp.data or []
        return _row_to_strava_connection(rows[0]) if rows else None

    def delete_strava_connection(self) -> None:
        (
            self.client.table("strava_connections")
            .delete()
            .eq("user_id", self.user_id)
            .execute()
        )

    # --- daily log --------------------------------------------------------

    def save_daily_log(self, entry: DailyLogEntry) -> None:
        # Same validation as the SQLite store — keep behaviour consistent
        # across backends so swapping doesn't change user-facing errors.
        if entry.followed not in (None, "yes", "no", "partial"):
            raise ValueError(
                f"followed must be yes/no/partial/None, got {entry.followed!r}"
            )
        for field_name, value in (
            ("usefulness", entry.usefulness),
            ("persuasiveness", entry.persuasiveness),
            ("next_session_feeling", entry.next_session_feeling),
        ):
            if value is not None and not (1 <= value <= 5):
                raise ValueError(
                    f"{field_name} must be in 1..5 or None, got {value!r}"
                )
        payload: dict[str, Any] = {
            "user_id": self.user_id,
            "log_date": entry.log_date.isoformat(),
            "recommended_category": entry.recommended_category,
            "rationale_excerpt": entry.rationale_excerpt,
            "usefulness": entry.usefulness,
            "persuasiveness": entry.persuasiveness,
            "followed": entry.followed,
            "next_session_feeling": entry.next_session_feeling,
            "notes": entry.notes,
        }
        self.client.table("daily_logs").upsert(
            payload, on_conflict="user_id,log_date"
        ).execute()

    def get_daily_log(self, day: date) -> DailyLogEntry | None:
        resp = (
            self.client.table("daily_logs")
            .select("*")
            .eq("user_id", self.user_id)
            .eq("log_date", day.isoformat())
            .limit(1)
            .execute()
        )
        rows = resp.data or []
        return _row_to_daily_log(rows[0]) if rows else None

    def list_daily_logs(
        self, since: date | None = None, until: date | None = None
    ) -> list[DailyLogEntry]:
        q = (
            self.client.table("daily_logs")
            .select("*")
            .eq("user_id", self.user_id)
        )
        if since:
            q = q.gte("log_date", since.isoformat())
        if until:
            q = q.lte("log_date", until.isoformat())
        resp = q.order("log_date", desc=False).execute()
        return [_row_to_daily_log(r) for r in (resp.data or [])]

    # --- Coach decisions (Project Plan §4.2) -----------------------------

    def save_coach_decision(self, decision: CoachDecision) -> None:
        payload: dict[str, Any] = {
            "user_id": self.user_id,
            "decision_date": decision.decision_date.isoformat(),
            "coach_name": decision.coach_name,
            "recommended_category": decision.recommended_category,
            "rationale": decision.rationale,
            "notes": decision.notes,
        }
        self.client.table("coach_decisions").upsert(
            payload, on_conflict="user_id,decision_date"
        ).execute()

    def get_coach_decision(self, day: date) -> CoachDecision | None:
        resp = (
            self.client.table("coach_decisions")
            .select("*")
            .eq("user_id", self.user_id)
            .eq("decision_date", day.isoformat())
            .limit(1)
            .execute()
        )
        rows = resp.data or []
        return _row_to_coach_decision(rows[0]) if rows else None

    def list_coach_decisions(
        self, since: date | None = None, until: date | None = None,
    ) -> list[CoachDecision]:
        q = (
            self.client.table("coach_decisions")
            .select("*")
            .eq("user_id", self.user_id)
        )
        if since:
            q = q.gte("decision_date", since.isoformat())
        if until:
            q = q.lte("decision_date", until.isoformat())
        resp = q.order("decision_date", desc=False).execute()
        return [_row_to_coach_decision(r) for r in (resp.data or [])]

    def delete_coach_decision(self, day: date) -> None:
        (
            self.client.table("coach_decisions")
            .delete()
            .eq("user_id", self.user_id)
            .eq("decision_date", day.isoformat())
            .execute()
        )


def _row_to_profile(row: dict[str, Any]) -> AthleteProfile:
    return AthleteProfile(
        name=row["name"],
        age=row["age"],
        sex=row["sex"],
        max_hr=row["max_hr"],
        resting_hr=row["resting_hr"],
        training_years=row.get("training_years") or 0,
        season_goal=row.get("season_goal") or "",
        personal_bests=row.get("personal_bests") or {},
        threshold_pace_min_per_km=row.get("threshold_pace_min_per_km"),
    )


def _row_to_strava_connection(row: dict[str, Any]) -> StravaConnection:
    return StravaConnection(
        client_id=row["client_id"],
        client_secret=row["client_secret"],
        refresh_token=row["refresh_token"],
        athlete_id=row.get("athlete_id"),
        athlete_name=row.get("athlete_name") or "",
        scope=row.get("scope") or "",
    )


def _row_to_daily_log(row: dict[str, Any]) -> DailyLogEntry:
    # Postgres returns timestamptz as ISO-8601 with `+00:00`; the older `Z`
    # suffix variant shows up if the JSON serializer trims it. Handle both.
    created_at: datetime | None = None
    raw_created = row.get("created_at")
    if isinstance(raw_created, str):
        try:
            created_at = datetime.fromisoformat(raw_created.replace("Z", "+00:00"))
        except ValueError:
            created_at = None
    return DailyLogEntry(
        log_date=date.fromisoformat(row["log_date"]),
        recommended_category=row["recommended_category"],
        rationale_excerpt=row.get("rationale_excerpt"),
        usefulness=row.get("usefulness"),
        persuasiveness=row.get("persuasiveness"),
        followed=row.get("followed"),
        next_session_feeling=row.get("next_session_feeling"),
        notes=row.get("notes"),
        created_at=created_at,
    )


def _row_to_coach_decision(row: dict[str, Any]) -> CoachDecision:
    created_at: datetime | None = None
    raw_created = row.get("created_at")
    if isinstance(raw_created, str):
        try:
            created_at = datetime.fromisoformat(raw_created.replace("Z", "+00:00"))
        except ValueError:
            created_at = None
    return CoachDecision(
        decision_date=date.fromisoformat(row["decision_date"]),
        recommended_category=row["recommended_category"],
        coach_name=row.get("coach_name") or "Ille Kukk",
        rationale=row.get("rationale"),
        notes=row.get("notes"),
        created_at=created_at,
    )
