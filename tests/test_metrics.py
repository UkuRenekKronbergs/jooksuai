"""Tests for load metrics — hand-verified reference values.

The Banister TRIMP values come from plugging numbers into the formula manually;
if you need to change them, derive the new expected value on paper first.
"""

from __future__ import annotations

import math
from datetime import timedelta

import pytest

from jooksuai.data.models import TrainingActivity
from jooksuai.metrics.load import (
    ACWR_DANGER_HIGH,
    ACWR_SWEET_SPOT,
    acwr_series,
    build_load_timeseries,
    compute_monotony,
    compute_strain,
    summarize_load,
    trimp,
)

# --- TRIMP ----------------------------------------------------------------

def test_trimp_male_hand_value():
    # Hand calc: hr_ratio = 100/140 = 0.7142857
    # weight = 0.64 * exp(1.92 * 0.7142857) = 0.64 * exp(1.3714286) ≈ 0.64 * 3.9403 ≈ 2.5218
    # trimp = 60 * 0.7142857 * 2.5218 ≈ 108.07
    value = trimp(duration_min=60, avg_hr=150, resting_hr=50, max_hr=190, sex="M")
    assert math.isclose(value, 108.07, abs_tol=0.5)


def test_trimp_female_has_different_weight():
    # For the same HR, the female coefficient (0.86, 1.67) gives a smaller weight
    # when hr_ratio is in the typical aerobic range, but they converge at the top.
    male = trimp(60, 150, 50, 190, sex="M")
    female = trimp(60, 150, 50, 190, sex="F")
    assert male != pytest.approx(female)


def test_trimp_zero_duration():
    assert trimp(0, 150, 50, 190) == 0.0


def test_trimp_falls_back_to_rpe_when_hr_missing():
    # duration * rpe * multiplier (default 1.0)
    assert trimp(45, None, 50, 190, fallback_rpe=7) == 45 * 7


def test_trimp_returns_zero_when_hr_and_rpe_missing():
    assert trimp(45, None, 50, 190) == 0.0


def test_trimp_pace_fallback_at_threshold_returns_duration():
    # Pace == threshold → IF = 1.0 → load = duration × 1² = duration
    value = trimp(
        duration_min=60, avg_hr=None, resting_hr=50, max_hr=190,
        fallback_pace_min_per_km=3.5, threshold_pace_min_per_km=3.5,
    )
    assert math.isclose(value, 60.0)


def test_trimp_pace_fallback_faster_than_threshold_higher_load():
    # 20% faster than threshold → IF = 1.2 → load = duration × 1.44
    value = trimp(
        duration_min=30, avg_hr=None, resting_hr=50, max_hr=190,
        fallback_pace_min_per_km=2.92, threshold_pace_min_per_km=3.5,
    )
    assert math.isclose(value, 30 * (3.5 / 2.92) ** 2, rel_tol=1e-3)


def test_trimp_pace_fallback_slower_than_threshold_lower_load():
    # Easy pace 5:00/km against threshold 3:30/km → IF = 0.7 → load = duration × 0.49
    value = trimp(
        duration_min=60, avg_hr=None, resting_hr=50, max_hr=190,
        fallback_pace_min_per_km=5.0, threshold_pace_min_per_km=3.5,
    )
    assert math.isclose(value, 60 * (3.5 / 5.0) ** 2, rel_tol=1e-3)


def test_trimp_hr_takes_priority_over_pace():
    # When both HR and pace are available, HR wins (more accurate).
    hr_value = trimp(60, 150, 50, 190, fallback_pace_min_per_km=5.0, threshold_pace_min_per_km=3.5)
    pace_only = trimp(60, None, 50, 190, fallback_pace_min_per_km=5.0, threshold_pace_min_per_km=3.5)
    assert hr_value != pytest.approx(pace_only)


def test_trimp_pace_fallback_without_threshold_falls_through_to_rpe():
    value = trimp(
        duration_min=45, avg_hr=None, resting_hr=50, max_hr=190,
        fallback_pace_min_per_km=4.0, threshold_pace_min_per_km=None,
        fallback_rpe=6,
    )
    assert value == 45 * 6


def test_trimp_clamps_hr_above_max():
    # avg_hr above max shouldn't explode — hr_ratio clamped to 1.0
    extreme = trimp(30, 250, 50, 190, sex="M")
    boundary = trimp(30, 190, 50, 190, sex="M")
    assert math.isclose(extreme, boundary, abs_tol=0.01)


# --- Load timeseries -------------------------------------------------------

def test_build_load_timeseries_fills_rest_days(profile, today):
    activities = [
        TrainingActivity(id="a1", activity_date=today - timedelta(days=2), activity_type="Run", distance_km=10, duration_min=50, avg_hr=140),
        TrainingActivity(id="a2", activity_date=today, activity_type="Run", distance_km=8, duration_min=40, avg_hr=150),
    ]
    # Pin `end` so the test isn't dependent on real wall-clock today.
    series = build_load_timeseries(activities, profile, end=today)
    # Should include today-2, today-1 (rest), today
    assert len(series) == 3
    assert series.iloc[1] == 0.0  # rest day


def test_build_load_timeseries_empty_returns_empty(profile):
    series = build_load_timeseries([], profile)
    assert series.empty


# --- ACWR -----------------------------------------------------------------

def test_acwr_flat_load_converges_to_one(profile, easy_run_factory, today):
    # 40 days of identical easy runs → acute mean == chronic mean → ACWR == 1.0
    activities = easy_run_factory(days=40)
    series = build_load_timeseries(activities, profile, end=today)
    ratios = acwr_series(series)
    assert math.isclose(ratios["acwr"].iloc[-1], 1.0, abs_tol=0.01)


def test_acwr_spike_pushes_ratio_high(profile, easy_run_factory, today):
    base = easy_run_factory(days=28, avg_hr=130, duration_min=30)
    # Now add a big spike in the last 7 days: extra long intense runs.
    spike = [
        TrainingActivity(
            id=f"spike-{i}",
            activity_date=today - timedelta(days=i),
            activity_type="Run",
            distance_km=18,
            duration_min=100,
            avg_hr=170,
            notes="spike",
        )
        for i in range(7)
    ]
    series = build_load_timeseries(base + spike, profile)
    ratios = acwr_series(series)
    assert ratios["acwr"].iloc[-1] > ACWR_DANGER_HIGH


def test_acwr_empty_returns_empty_df(profile):
    series = build_load_timeseries([], profile)
    assert acwr_series(series).empty


# --- Monotony -------------------------------------------------------------

def test_monotony_zero_variance_returns_none(profile, easy_run_factory, today):
    # Identical loads every day → std = 0 → monotony undefined
    activities = easy_run_factory(days=7, avg_hr=140, duration_min=45)
    series = build_load_timeseries(activities, profile, end=today)
    assert compute_monotony(series) is None


def test_monotony_varied_loads_computes_positive(profile, today):
    # Week with varied loads
    activities = [
        TrainingActivity(
            id=f"v{i}",
            activity_date=today - timedelta(days=i),
            activity_type="Run",
            distance_km=10,
            duration_min=45 + i * 5,
            avg_hr=140 + i * 2,
        )
        for i in range(7)
    ]
    series = build_load_timeseries(activities, profile)
    m = compute_monotony(series)
    assert m is not None and m > 0


def test_strain_is_weekly_load_times_monotony(profile, today):
    activities = [
        TrainingActivity(
            id=f"s{i}",
            activity_date=today - timedelta(days=i),
            activity_type="Run",
            distance_km=10,
            duration_min=45 + i * 5,
            avg_hr=140 + i * 2,
        )
        for i in range(7)
    ]
    series = build_load_timeseries(activities, profile)
    strain = compute_strain(series)
    monotony = compute_monotony(series)
    assert strain is not None and monotony is not None
    assert math.isclose(strain, series.tail(7).sum() * monotony, rel_tol=1e-9)


# --- Summary --------------------------------------------------------------

def test_summarize_load_sweet_spot(profile, easy_run_factory):
    activities = easy_run_factory(days=40)
    summary = summarize_load(activities, profile)
    assert summary.acwr is not None
    assert ACWR_SWEET_SPOT[0] <= summary.acwr <= ACWR_SWEET_SPOT[1]
    assert summary.acwr_zone == "optimaalne"


def test_summarize_load_empty(profile):
    summary = summarize_load([], profile)
    assert summary.acwr is None
    assert summary.acute_7d == 0.0
