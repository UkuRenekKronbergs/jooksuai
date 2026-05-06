"""Tests for the SQLite ActivityStore — focus on round-trip fidelity."""

from __future__ import annotations

from datetime import date

from vorm.data.models import AthleteProfile, TrainingActivity
from vorm.data.storage import ActivityStore


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
