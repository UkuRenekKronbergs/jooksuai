"""Tests for personal-best detection."""

from __future__ import annotations

import math
from datetime import date

from jooksuai.data.models import TrainingActivity
from jooksuai.metrics.personal_bests import (
    PersonalBest,
    find_personal_bests,
    progression_at_distance,
)


def _run(id_, day_offset, distance_km, duration_min, notes=""):
    return TrainingActivity(
        id=id_,
        activity_date=date(2026, 1, 1).replace() if day_offset == 0 else _shift(date(2026, 1, 1), day_offset),
        activity_type="Run",
        distance_km=distance_km,
        duration_min=duration_min,
        avg_hr=150,
        notes=notes,
    )


def _shift(d: date, days: int) -> date:
    from datetime import timedelta
    return d + timedelta(days=days)


def test_finds_5k_pb_among_multiple_attempts():
    activities = [
        _run("a1", 0, 5.0, 18.0),     # 18:00 5k
        _run("a2", 7, 4.95, 17.5),    # 17:30 5k — fastest
        _run("a3", 14, 5.05, 19.0),   # 19:00 5k
    ]
    pbs = find_personal_bests(activities)
    pb_5k = next(p for p in pbs if p.distance_label == "5 km")
    assert pb_5k.activity_id == "a2"
    assert math.isclose(pb_5k.duration_min, 17.5)


def test_pb_time_formatted_under_hour():
    pb = PersonalBest(
        distance_label="5 km", nominal_distance_km=5.0,
        activity_distance_km=5.0, duration_min=17.5,
        activity_date=date(2026, 1, 1), activity_id="x",
    )
    assert pb.time_formatted == "17:30"


def test_pb_time_formatted_over_hour():
    pb = PersonalBest(
        distance_label="Maraton", nominal_distance_km=42.195,
        activity_distance_km=42.2, duration_min=145.0,  # 2:25:00
        activity_date=date(2026, 1, 1), activity_id="x",
    )
    assert pb.time_formatted == "2:25:00"


def test_outside_tolerance_does_not_match():
    # 5.5 km is outside ±5% of 5 km (4.75–5.25).
    activities = [_run("a1", 0, 5.5, 20.0)]
    pbs = find_personal_bests(activities)
    assert not any(p.distance_label == "5 km" for p in pbs)


def test_non_run_activity_excluded():
    bike = TrainingActivity(
        id="b1", activity_date=date(2026, 1, 1),
        activity_type="Ride", distance_km=5.0, duration_min=10.0,
    )
    pbs = find_personal_bests([bike])
    assert pbs == []


def test_progression_running_minimum_step_function():
    activities = [
        _run("a1", 0, 5.0, 20.0),
        _run("a2", 7, 5.0, 19.0),
        _run("a3", 14, 5.0, 21.0),
        _run("a4", 21, 5.0, 18.5),
    ]
    df = progression_at_distance(activities, nominal_km=5.0)
    assert len(df) == 4
    assert list(df["pb_so_far_min"]) == [20.0, 19.0, 19.0, 18.5]


def test_progression_empty_when_no_matches():
    df = progression_at_distance([_run("a1", 0, 8.0, 30.0)], nominal_km=5.0)
    assert df.empty


def test_pace_property_handles_zero_distance():
    pb = PersonalBest(
        distance_label="x", nominal_distance_km=0,
        activity_distance_km=0, duration_min=10,
        activity_date=date(2026, 1, 1), activity_id="x",
    )
    assert pb.pace_min_per_km == 0.0
