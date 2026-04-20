"""Strava API client.

Thin wrapper around `stravalib` to fetch the last N days of activities. We
refresh the access token on every call because tokens expire after 6 hours
and we want the Streamlit app to Just Work after a long idle.

Per the project plan (privacy): we pull the aggregate fields only — no GPS
streams or raw HR time-series leave the device.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from .models import TrainingActivity


class StravaNotConfigured(RuntimeError):
    pass


def fetch_recent_activities(
    *,
    client_id: str | None,
    client_secret: str | None,
    refresh_token: str | None,
    days: int = 60,
) -> list[TrainingActivity]:
    if not (client_id and client_secret and refresh_token):
        raise StravaNotConfigured(
            "Strava credentials missing — set STRAVA_CLIENT_ID / _SECRET / _REFRESH_TOKEN in .env"
        )

    try:
        from stravalib import Client
    except ImportError as exc:
        raise RuntimeError("stravalib not installed — run `pip install stravalib`") from exc

    client = Client()
    token_response = client.refresh_access_token(
        client_id=int(client_id),
        client_secret=client_secret,
        refresh_token=refresh_token,
    )
    client.access_token = token_response["access_token"]

    after = datetime.now(UTC) - timedelta(days=days)
    activities: list[TrainingActivity] = []
    for a in client.get_activities(after=after):
        if not _is_running(a.type):
            continue
        activities.append(_map_activity(a))
    return activities


def _is_running(activity_type) -> bool:
    return str(activity_type).lower() in {"run", "trailrun", "virtualrun"}


def _map_activity(a) -> TrainingActivity:
    distance_km = float(a.distance) / 1000 if a.distance else 0.0
    duration_min = float(a.elapsed_time.total_seconds()) / 60 if a.elapsed_time else 0.0
    activity_date: date = (
        a.start_date_local.date()
        if hasattr(a.start_date_local, "date")
        else date.fromisoformat(str(a.start_date_local)[:10])
    )
    avg_pace = round(duration_min / distance_km, 2) if distance_km > 0 else None
    return TrainingActivity(
        id=str(a.id),
        activity_date=activity_date,
        activity_type=str(a.type),
        distance_km=round(distance_km, 2),
        duration_min=round(duration_min, 1),
        avg_hr=int(a.average_heartrate) if a.average_heartrate else None,
        max_hr_observed=int(a.max_heartrate) if a.max_heartrate else None,
        avg_pace_min_per_km=avg_pace,
        elevation_gain_m=float(a.total_elevation_gain) if a.total_elevation_gain else None,
        notes=str(a.name) if a.name else None,
    )
