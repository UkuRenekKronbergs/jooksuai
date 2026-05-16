"""Tests for the HR-zone distribution chart helpers.

The chart itself is a Plotly figure (visual), but the classification helpers
are pure functions and exercise the real branch logic — those are what we
exercise here.
"""

from __future__ import annotations

from datetime import date

from vorm.data.models import AthleteProfile, TrainingActivity
from vorm.ui.charts import (
    _classify_zone_by_hr,
    _classify_zone_by_pace,
    hr_zone_distribution_chart,
)


def _profile(max_hr=190, rest=50, threshold_pace=4.0):
    return AthleteProfile(
        name="X", age=30, sex="M", max_hr=max_hr, resting_hr=rest,
        training_years=5, threshold_pace_min_per_km=threshold_pace,
    )


def test_classify_zone_by_hr_buckets_match_karvonen_reserve():
    p = _profile(max_hr=190, rest=50)  # HR reserve = 140
    # 50% of reserve = 50 + 0.5*140 = 120 → Z1 (below 60%)
    assert _classify_zone_by_hr(120, p) == 1
    # 65% of reserve = 50 + 0.65*140 = 141 → Z2
    assert _classify_zone_by_hr(141, p) == 2
    # 75% of reserve = 50 + 0.75*140 = 155 → Z3
    assert _classify_zone_by_hr(155, p) == 3
    # 85% of reserve = 50 + 0.85*140 = 169 → Z4
    assert _classify_zone_by_hr(169, p) == 4
    # 95% of reserve = 50 + 0.95*140 = 183 → Z5
    assert _classify_zone_by_hr(183, p) == 5


def test_classify_zone_by_pace_slower_than_threshold_is_lower_zone():
    threshold = 4.0  # min/km
    # 20% slower → Z1
    assert _classify_zone_by_pace(4.8, threshold) == 1
    # 10% slower → Z2
    assert _classify_zone_by_pace(4.4, threshold) == 2
    # at threshold → Z3
    assert _classify_zone_by_pace(4.0, threshold) == 3
    # 3% faster → Z4
    assert _classify_zone_by_pace(3.88, threshold) == 4
    # 10% faster → Z5
    assert _classify_zone_by_pace(3.6, threshold) == 5


def test_classify_zone_by_hr_degrades_safely_on_invalid_reserve():
    """Bad profile data (max_hr <= rest) shouldn't crash — default to Z2."""
    bad = _profile(max_hr=50, rest=80)
    assert _classify_zone_by_hr(150, bad) == 2


def test_hr_zone_chart_handles_empty_input():
    fig = hr_zone_distribution_chart([], _profile())
    assert fig is not None
    # No traces, but layout title should signal the empty state
    assert "andmed" in fig.layout.title.text.lower() or "tsoon" in fig.layout.title.text.lower()


def test_hr_zone_chart_aggregates_by_iso_week():
    p = _profile()
    activities = [
        TrainingActivity(
            id="a1", activity_date=date(2026, 5, 11),  # Monday
            activity_type="Run", distance_km=10.0, duration_min=50.0, avg_hr=130,
        ),
        TrainingActivity(
            id="a2", activity_date=date(2026, 5, 13),  # Wednesday (same week)
            activity_type="Run", distance_km=8.0, duration_min=40.0, avg_hr=170,
        ),
        TrainingActivity(
            id="a3", activity_date=date(2026, 5, 18),  # next week
            activity_type="Run", distance_km=15.0, duration_min=75.0, avg_hr=150,
        ),
    ]
    fig = hr_zone_distribution_chart(activities, p)
    # Each zone present in the data becomes a Bar trace
    assert len(fig.data) >= 1
    # All traces have the same x axis (week starts)
    weeks = set()
    for trace in fig.data:
        weeks.update(trace.x.tolist())
    assert len(weeks) == 2  # two distinct weeks


def test_hr_zone_chart_skips_non_runs_and_missing_data():
    p = _profile()
    activities = [
        TrainingActivity(
            id="a1", activity_date=date(2026, 5, 11),
            activity_type="Ride", distance_km=30.0, duration_min=90.0, avg_hr=140,
        ),  # not a run — skipped
        TrainingActivity(
            id="a2", activity_date=date(2026, 5, 13),
            activity_type="Run", distance_km=8.0, duration_min=40.0,
            avg_hr=None, avg_pace_min_per_km=None,
        ),  # no HR, no pace — skipped
    ]
    fig = hr_zone_distribution_chart(activities, p)
    # All inputs filtered out → empty-state title
    assert "ei" in fig.layout.title.text.lower() or "puudu" in fig.layout.title.text.lower()
