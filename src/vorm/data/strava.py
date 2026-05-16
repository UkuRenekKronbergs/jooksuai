"""Strava API client.

Thin wrapper around `stravalib` to fetch the last N days of activities. We
refresh the access token on every call because tokens expire after 6 hours
and we want the Streamlit app to Just Work after a long idle.

Per the project plan (privacy): we pull the aggregate fields only — no GPS
streams or raw HR time-series leave the device.

This module exposes two entry points:

- `fetch_recent_activities()` — pull a fresh window from Strava every time.
  Simple, but burns API quota; useful for one-shot scripts.
- `fetch_with_cache()` — **delta-sync** against a local SQLite store. On a
  warm cache, only the day after the latest cached activity is asked of the
  API. This is the Plan B from Project Plan §5 Risk 2: "Lokaalne SQLite
  vahemälu tagab, et iga treening päritakse ainult üks kord."
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

from .models import TrainingActivity
from .storage import ActivityStore

STRAVA_AUTHORIZE_URL = "https://www.strava.com/oauth/authorize"
STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"
STRAVA_ACTIVITY_SCOPE = "read,activity:read_all"


class StravaNotConfigured(RuntimeError):
    pass


@dataclass(frozen=True)
class StravaOAuthToken:
    refresh_token: str
    access_token: str
    expires_at: int | None = None
    athlete_id: str | None = None
    athlete_name: str = ""
    scope: str = ""


@dataclass(frozen=True)
class StravaSyncResult:
    """Bookkeeping returned by `fetch_with_cache()`.

    `fetched_from_api` excludes already-cached activities, so the caller can
    surface a meaningful "synced N new, X cached" line. `latest_cached_date`
    is the most recent activity date *after* the sync — useful for offline
    indicators in the UI.
    """

    activities: list[TrainingActivity]
    fetched_from_api: int
    cache_hits: int
    latest_cached_date: date | None
    api_called: bool
    refresh_token: str | None = None


def build_authorization_url(
    *,
    client_id: str,
    redirect_uri: str,
    state: str,
    scope: str = STRAVA_ACTIVITY_SCOPE,
    approval_prompt: str = "auto",
) -> str:
    """Build the Strava OAuth URL for a browser-based authorization flow."""
    if not client_id:
        raise StravaNotConfigured("Strava Client ID puudub.")
    if not redirect_uri:
        raise StravaNotConfigured("Strava tagasisuuna URL-i ei õnnestunud määrata.")
    if not str(client_id).isdigit():
        raise StravaNotConfigured("Strava Client ID peab olema number.")
    params = {
        "client_id": str(client_id).strip(),
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "approval_prompt": approval_prompt,
        "scope": scope,
        "state": state,
    }
    return f"{STRAVA_AUTHORIZE_URL}?{urlencode(params)}"


def exchange_code_for_token(
    *,
    client_id: str,
    client_secret: str,
    code: str,
    timeout: int = 30,
) -> StravaOAuthToken:
    """Exchange Strava's temporary authorization code for reusable tokens."""
    if not (client_id and client_secret and code):
        raise StravaNotConfigured("Strava Client ID, Client Secret ja kood on kohustuslikud.")
    if not str(client_id).isdigit():
        raise StravaNotConfigured("Strava Client ID peab olema number.")
    try:
        import requests  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError("requests pole installitud — jooksuta `pip install -r requirements.txt`.") from exc

    response = requests.post(
        STRAVA_TOKEN_URL,
        data={
            "client_id": str(client_id).strip(),
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
        },
        timeout=timeout,
    )
    if not response.ok:
        raise RuntimeError(
            f"Strava token-vahetus ebaõnnestus: {response.status_code} {response.text}"
        )
    payload = response.json()
    refresh_token = payload.get("refresh_token")
    access_token = payload.get("access_token")
    if not (refresh_token and access_token):
        raise RuntimeError("Strava vastusest puudus access_token või refresh_token.")

    athlete = payload.get("athlete") or {}
    athlete_name = " ".join(
        part for part in (athlete.get("firstname"), athlete.get("lastname")) if part
    )
    return StravaOAuthToken(
        refresh_token=str(refresh_token),
        access_token=str(access_token),
        expires_at=payload.get("expires_at"),
        athlete_id=str(athlete["id"]) if athlete.get("id") is not None else None,
        athlete_name=athlete_name,
        scope=str(payload.get("scope") or ""),
    )


def fetch_recent_activities(
    *,
    client_id: str | None,
    client_secret: str | None,
    refresh_token: str | None,
    days: int = 60,
) -> list[TrainingActivity]:
    """Pull `days` worth of activities from Strava without consulting the cache."""
    if not (client_id and client_secret and refresh_token):
        raise StravaNotConfigured(
            "Strava credentials missing — set STRAVA_CLIENT_ID / _SECRET / _REFRESH_TOKEN in .env"
        )
    client, _latest_refresh_token = _authed_client(client_id, client_secret, refresh_token)
    after = datetime.now(UTC) - timedelta(days=days)
    return list(_pull_runs_after(client, after))


def fetch_with_cache(
    *,
    client_id: str | None,
    client_secret: str | None,
    refresh_token: str | None,
    cache_db: Path,
    days: int = 60,
    today: date | None = None,
) -> StravaSyncResult:
    """Cache-aware fetch. Asks Strava only for the delta since the latest cache row.

    Semantics:

    - The local SQLite store holds every activity ever fetched. We never delete.
    - On call, we look up the latest cached `activity_date`. The Strava query
      uses `after = max(latest_cached - 1 day, window_start)` — a one-day overlap
      lets us pick up edits to the most recent activity (Strava lets athletes
      edit name/distance for a while after upload).
    - On a cold cache, we fall back to a full `days`-day window.
    - On any API failure (network, 429, etc.), the cache is still returned —
      stale > empty.

    Returns the union of (still-fresh cache + newly-pulled rows), trimmed to
    the requested `days` window.
    """
    if not (client_id and client_secret and refresh_token):
        raise StravaNotConfigured(
            "Strava credentials missing — set STRAVA_CLIENT_ID / _SECRET / _REFRESH_TOKEN in .env"
        )

    today = today or datetime.now(UTC).date()
    window_start = today - timedelta(days=days)
    store = ActivityStore(cache_db)
    latest_cached = store.latest_activity_date()

    # One-day backstep so we re-fetch the most recent day in case the athlete
    # tweaked it on Strava after upload.
    sync_from = window_start
    if latest_cached and latest_cached > window_start:
        sync_from = latest_cached - timedelta(days=1)

    fresh: list[TrainingActivity] = []
    api_called = False
    latest_refresh_token: str | None = None
    try:
        client, latest_refresh_token = _authed_client(client_id, client_secret, refresh_token)
        fresh = list(
            _pull_runs_after(client, datetime.combine(sync_from, datetime.min.time(), tzinfo=UTC))
        )
        api_called = True
    except Exception:
        # Network / 429 / stravalib hiccup: do *not* lose the cached activities.
        # The caller can log / surface this; we return what we have.
        if not store.list_activities(since=window_start):
            raise

    fetched_from_api = store.upsert_activities(fresh)
    all_in_window = store.list_activities(since=window_start, until=today)
    cache_hits = max(len(all_in_window) - fetched_from_api, 0)
    return StravaSyncResult(
        activities=all_in_window,
        fetched_from_api=fetched_from_api,
        cache_hits=cache_hits,
        latest_cached_date=store.latest_activity_date(),
        api_called=api_called,
        refresh_token=latest_refresh_token,
    )


def _authed_client(client_id: str, client_secret: str, refresh_token: str):
    try:
        from stravalib import Client
    except ImportError as exc:
        raise RuntimeError(
            "stravalib not installed — run `pip install stravalib`"
        ) from exc

    client = Client()
    token_response = client.refresh_access_token(
        client_id=int(client_id),
        client_secret=client_secret,
        refresh_token=refresh_token,
    )
    client.access_token = token_response["access_token"]
    return client, token_response.get("refresh_token")


def _pull_runs_after(client, after: datetime):
    for a in client.get_activities(after=after):
        if not _is_running(a.type):
            continue
        yield _map_activity(a)


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
