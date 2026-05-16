"""Tests for vorm.data.strava — focus on the cache-aware delta-sync path.

We avoid hitting the real Strava API by injecting a fake `stravalib.Client`
into `sys.modules`. The fake records every `get_activities(after=...)` call so
we can assert the delta-sync semantics (cold cache → full window; warm cache →
one-day backstep; API failure → cache is still returned).
"""

from __future__ import annotations

import sys
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlparse

import pytest


@pytest.fixture
def fake_stravalib(monkeypatch):
    """Install a stub `stravalib` module that records calls and returns canned data.

    Yields the stub module so tests can program its `Client.get_activities`
    return value and inspect the `after=` argument passed to it.
    """
    fake_client = MagicMock()
    fake_client.refresh_access_token.return_value = {"access_token": "fake-token"}
    fake_client.get_activities.return_value = []
    fake_module = SimpleNamespace(Client=MagicMock(return_value=fake_client))
    monkeypatch.setitem(sys.modules, "stravalib", fake_module)
    yield SimpleNamespace(module=fake_module, client=fake_client)


def _fake_activity(*, id, day, name="Easy run", hr=140, dist_m=10000, dur_s=2700,
                   activity_type="Run"):
    """Mimic the shape of a stravalib Activity for `_map_activity`."""
    return SimpleNamespace(
        id=id,
        name=name,
        type=activity_type,
        distance=dist_m,
        elapsed_time=timedelta(seconds=dur_s),
        start_date_local=datetime.combine(day, datetime.min.time()),
        average_heartrate=hr,
        max_heartrate=hr + 12,
        total_elevation_gain=50.0,
    )


def test_cold_cache_fetches_full_window(tmp_path, fake_stravalib):
    """No cache yet → query Strava `after = today − days`."""
    from vorm.data.strava import fetch_with_cache

    today = date(2026, 5, 16)
    fake_stravalib.client.get_activities.return_value = [
        _fake_activity(id="a1", day=date(2026, 5, 1)),
        _fake_activity(id="a2", day=date(2026, 5, 10)),
    ]

    result = fetch_with_cache(
        client_id="1", client_secret="x", refresh_token="r",
        cache_db=tmp_path / "store.db",
        days=60, today=today,
    )

    assert result.api_called is True
    assert result.fetched_from_api == 2
    assert result.cache_hits == 0
    assert {a.id for a in result.activities} == {"a1", "a2"}

    after_arg = fake_stravalib.client.get_activities.call_args.kwargs["after"]
    expected_min = datetime.combine(today - timedelta(days=60), datetime.min.time(),
                                    tzinfo=UTC)
    assert after_arg == expected_min


def test_build_authorization_url_contains_scope_and_state():
    from vorm.data.strava import build_authorization_url

    url = build_authorization_url(
        client_id="123",
        redirect_uri="http://localhost:8501",
        state="vorm-strava-state",
    )

    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    assert parsed.netloc == "www.strava.com"
    assert query["client_id"] == ["123"]
    assert query["redirect_uri"] == ["http://localhost:8501"]
    assert query["response_type"] == ["code"]
    assert query["scope"] == ["read,activity:read_all"]
    assert query["state"] == ["vorm-strava-state"]


def test_exchange_code_for_token_maps_strava_response(monkeypatch):
    from vorm.data.strava import exchange_code_for_token

    calls = []

    class FakeResponse:
        ok = True

        def json(self):
            return {
                "access_token": "access",
                "refresh_token": "refresh",
                "expires_at": 1770000000,
                "scope": "read,activity:read_all",
                "athlete": {
                    "id": 42,
                    "firstname": "Uku",
                    "lastname": "Kronbergs",
                },
            }

    def fake_post(url, *, data, timeout):
        calls.append((url, data, timeout))
        return FakeResponse()

    monkeypatch.setattr("requests.post", fake_post)

    token = exchange_code_for_token(
        client_id="123",
        client_secret="secret",
        code="callback-code",
    )

    assert token.refresh_token == "refresh"
    assert token.access_token == "access"
    assert token.athlete_id == "42"
    assert token.athlete_name == "Uku Kronbergs"
    assert calls[0][1]["grant_type"] == "authorization_code"
    assert calls[0][1]["code"] == "callback-code"


def test_warm_cache_only_asks_for_delta(tmp_path, fake_stravalib):
    """Cached up to May 10 → query Strava `after = May 9` (one-day backstep)."""
    from vorm.data.strava import fetch_with_cache

    today = date(2026, 5, 16)

    # First call seeds the cache up through May 10.
    fake_stravalib.client.get_activities.return_value = [
        _fake_activity(id="a1", day=date(2026, 5, 1)),
        _fake_activity(id="a2", day=date(2026, 5, 10)),
    ]
    fetch_with_cache(
        client_id="1", client_secret="x", refresh_token="r",
        cache_db=tmp_path / "store.db",
        days=60, today=today,
    )

    # Second call: API now exposes one more activity, and an edited a2.
    fake_stravalib.client.get_activities.return_value = [
        _fake_activity(id="a2", day=date(2026, 5, 10), name="Edited name"),
        _fake_activity(id="a3", day=date(2026, 5, 14)),
    ]
    result = fetch_with_cache(
        client_id="1", client_secret="x", refresh_token="r",
        cache_db=tmp_path / "store.db",
        days=60, today=today,
    )

    # Window should now contain a1 (cached, untouched) + a2 (edited) + a3 (new).
    assert {a.id for a in result.activities} == {"a1", "a2", "a3"}
    assert result.api_called is True
    # a2 + a3 hit upsert; a1 is untouched in the API delta but lives in cache.
    assert result.fetched_from_api == 2
    assert result.cache_hits == 1

    # Verify the delta query used the one-day backstep, not the full window.
    after_arg = fake_stravalib.client.get_activities.call_args.kwargs["after"]
    expected_sync_from = datetime.combine(date(2026, 5, 9), datetime.min.time(),
                                          tzinfo=UTC)
    assert after_arg == expected_sync_from

    # The edited name should have been persisted via upsert.
    edited = next(a for a in result.activities if a.id == "a2")
    assert edited.notes == "Edited name"


def test_fetch_with_cache_surfaces_rotated_refresh_token(tmp_path, fake_stravalib):
    from vorm.data.strava import fetch_with_cache

    fake_stravalib.client.refresh_access_token.return_value = {
        "access_token": "fake-token",
        "refresh_token": "rotated-refresh",
    }
    fake_stravalib.client.get_activities.return_value = [
        _fake_activity(id="a1", day=date(2026, 5, 1)),
    ]

    result = fetch_with_cache(
        client_id="1",
        client_secret="x",
        refresh_token="old-refresh",
        cache_db=tmp_path / "store.db",
        days=60,
        today=date(2026, 5, 16),
    )

    assert result.refresh_token == "rotated-refresh"


def test_api_failure_falls_back_to_cache(tmp_path, fake_stravalib):
    """When stravalib raises, the cached activities are still returned."""
    from vorm.data.strava import fetch_with_cache

    today = date(2026, 5, 16)

    # Seed cache.
    fake_stravalib.client.get_activities.return_value = [
        _fake_activity(id="a1", day=date(2026, 5, 1)),
    ]
    fetch_with_cache(
        client_id="1", client_secret="x", refresh_token="r",
        cache_db=tmp_path / "store.db",
        days=60, today=today,
    )

    # Now the API blows up. We still want a1 back.
    fake_stravalib.client.get_activities.side_effect = RuntimeError("429 Too Many Requests")

    result = fetch_with_cache(
        client_id="1", client_secret="x", refresh_token="r",
        cache_db=tmp_path / "store.db",
        days=60, today=today,
    )

    assert result.api_called is False
    assert result.fetched_from_api == 0
    assert {a.id for a in result.activities} == {"a1"}


def test_api_failure_with_empty_cache_propagates(tmp_path, fake_stravalib):
    """If the cache is empty *and* the API fails, the error must surface — silent
    empty result would hide breakage from the user."""
    from vorm.data.strava import fetch_with_cache

    fake_stravalib.client.get_activities.side_effect = RuntimeError("network down")

    with pytest.raises(RuntimeError, match="network down"):
        fetch_with_cache(
            client_id="1", client_secret="x", refresh_token="r",
            cache_db=tmp_path / "store.db",
            days=60, today=date(2026, 5, 16),
        )


def test_non_run_activities_are_filtered(tmp_path, fake_stravalib):
    """Strava returns rides + swims etc. We only persist runs."""
    from vorm.data.strava import fetch_with_cache

    fake_stravalib.client.get_activities.return_value = [
        _fake_activity(id="run1", day=date(2026, 5, 14), activity_type="Run"),
        _fake_activity(id="bike1", day=date(2026, 5, 15), activity_type="Ride"),
        _fake_activity(id="swim1", day=date(2026, 5, 12), activity_type="Swim"),
    ]
    result = fetch_with_cache(
        client_id="1", client_secret="x", refresh_token="r",
        cache_db=tmp_path / "store.db",
        days=60, today=date(2026, 5, 16),
    )

    assert {a.id for a in result.activities} == {"run1"}


def test_missing_credentials_raises_configuration_error(tmp_path):
    """No env keys → loud, user-actionable error rather than a stravalib mystery."""
    from vorm.data.strava import StravaNotConfigured, fetch_with_cache

    with pytest.raises(StravaNotConfigured):
        fetch_with_cache(
            client_id=None, client_secret=None, refresh_token=None,
            cache_db=tmp_path / "store.db",
        )
