"""Tests for the SQLite ActivityStore — focus on round-trip fidelity."""

from __future__ import annotations

from datetime import date

import pytest

from vorm.data.models import AthleteProfile, TrainingActivity
from vorm.data.storage import ActivityStore, DailyLogEntry


def test_profile_roundtrip_preserves_threshold_pace(tmp_path):
    """Regression: threshold_pace_min_per_km used to be dropped on save."""
    store = ActivityStore(tmp_path / "store.db")
    profile = AthleteProfile(
        name="Test",
        age=30,
        sex="M",
        max_hr=190,
        resting_hr=50,
        training_years=10,
        season_goal="sub-35 10 km",
        personal_bests={"10000m": "34:40"},
        threshold_pace_min_per_km=3.62,
    )
    store.save_profile(profile)
    loaded = store.load_profile()
    assert loaded is not None
    assert loaded.threshold_pace_min_per_km == 3.62
    assert loaded.season_goal == "sub-35 10 km"
    assert loaded.personal_bests == {"10000m": "34:40"}


def test_profile_roundtrip_handles_missing_threshold(tmp_path):
    store = ActivityStore(tmp_path / "store.db")
    profile = AthleteProfile(
        name="X", age=25, sex="F", max_hr=185, resting_hr=55, training_years=2,
    )
    store.save_profile(profile)
    loaded = store.load_profile()
    assert loaded is not None
    assert loaded.threshold_pace_min_per_km is None


def test_activity_upsert_and_list(tmp_path):
    store = ActivityStore(tmp_path / "store.db")
    activities = [
        TrainingActivity(
            id="a1", activity_date=date(2026, 4, 18), activity_type="Run",
            distance_km=10.0, duration_min=48.0, avg_hr=148, rpe=5,
        ),
        TrainingActivity(
            id="a2", activity_date=date(2026, 4, 20), activity_type="Run",
            distance_km=14.0, duration_min=70.0, avg_hr=160,
        ),
    ]
    assert store.upsert_activities(activities) == 2

    listed = store.list_activities()
    assert [a.id for a in listed] == ["a1", "a2"]
    assert store.latest_activity_date() == date(2026, 4, 20)

    # Re-upsert preserves logged RPE when the new payload omits it.
    refreshed = TrainingActivity(
        id="a1", activity_date=date(2026, 4, 18), activity_type="Run",
        distance_km=10.5, duration_min=49.0, avg_hr=149,
    )
    store.upsert_activities([refreshed])
    listed = store.list_activities()
    a1 = next(a for a in listed if a.id == "a1")
    assert a1.distance_km == 10.5
    assert a1.rpe == 5  # COALESCE preserved the original RPE


def test_daily_log_roundtrip(tmp_path):
    store = ActivityStore(tmp_path / "store.db")
    entry = DailyLogEntry(
        log_date=date(2026, 5, 18),
        recommended_category="Vähenda intensiivsust",
        rationale_excerpt="ACWR 1.45, RPE 8 eile",
        usefulness=4,
        persuasiveness=5,
        followed="yes",
        next_session_feeling=4,
        notes="Tegin asemel kerge 8 km.",
    )
    store.save_daily_log(entry)

    loaded = store.get_daily_log(date(2026, 5, 18))
    assert loaded is not None
    assert loaded.recommended_category == "Vähenda intensiivsust"
    assert loaded.usefulness == 4
    assert loaded.followed == "yes"
    assert loaded.notes == "Tegin asemel kerge 8 km."
    assert loaded.created_at is not None  # auto-stamped


def test_daily_log_upsert_replaces_existing(tmp_path):
    store = ActivityStore(tmp_path / "store.db")
    day = date(2026, 5, 18)
    store.save_daily_log(DailyLogEntry(
        log_date=day, recommended_category="Jätka plaanipäraselt",
        usefulness=3, followed="yes",
    ))
    # Athlete reconsiders later in the day — overwrites the same row.
    store.save_daily_log(DailyLogEntry(
        log_date=day, recommended_category="Jätka plaanipäraselt",
        usefulness=2, followed="partial", notes="Lõpetasin treeningu varakult",
    ))
    loaded = store.get_daily_log(day)
    assert loaded.usefulness == 2
    assert loaded.followed == "partial"
    assert loaded.notes == "Lõpetasin treeningu varakult"


def test_daily_log_list_filters_by_range(tmp_path):
    store = ActivityStore(tmp_path / "store.db")
    for d in (date(2026, 5, 17), date(2026, 5, 18), date(2026, 5, 25)):
        store.save_daily_log(DailyLogEntry(
            log_date=d, recommended_category="Jätka plaanipäraselt",
        ))
    in_range = store.list_daily_logs(since=date(2026, 5, 18), until=date(2026, 5, 24))
    assert [e.log_date for e in in_range] == [date(2026, 5, 18)]


def test_falls_back_to_tempdir_on_permission_error(tmp_path, monkeypatch):
    """Streamlit Cloud / read-only FS regression: when the preferred parent
    directory rejects mkdir, ActivityStore must still come up — using a
    tempfile location — rather than crashing on first request."""
    import pathlib

    real_mkdir = pathlib.Path.mkdir
    refused = tmp_path / "refused"

    def fake_mkdir(self, *args, **kwargs):
        try:
            self.relative_to(refused)
        except ValueError:
            return real_mkdir(self, *args, **kwargs)
        raise PermissionError(f"refused: {self}")

    monkeypatch.setattr(pathlib.Path, "mkdir", fake_mkdir)

    store = ActivityStore(refused / "cache" / "activities.sqlite")
    assert str(store.db_path) != str(refused / "cache" / "activities.sqlite")
    assert store.db_path.name == "activities.sqlite"
    # Round-trip a profile through the fallback store to confirm it's wired up.
    store.save_profile(AthleteProfile(
        name="X", age=25, sex="M", max_hr=190, resting_hr=50, training_years=2,
    ))
    assert store.load_profile() is not None


def test_daily_log_validates_scale_inputs(tmp_path):
    store = ActivityStore(tmp_path / "store.db")
    with pytest.raises(ValueError, match="usefulness"):
        store.save_daily_log(DailyLogEntry(
            log_date=date(2026, 5, 18),
            recommended_category="Jätka plaanipäraselt",
            usefulness=7,
        ))
    with pytest.raises(ValueError, match="followed"):
        store.save_daily_log(DailyLogEntry(
            log_date=date(2026, 5, 18),
            recommended_category="Jätka plaanipäraselt",
            followed="maybe",
        ))
