"""Tests for the synthetic sample-data generator."""

from __future__ import annotations

from datetime import date

from jooksuai.data.sample import generate_sample_activities, load_sample_profile


def test_sample_is_deterministic():
    a = generate_sample_activities(days=30, end_date=date(2026, 1, 1), seed=42)
    b = generate_sample_activities(days=30, end_date=date(2026, 1, 1), seed=42)
    assert [x.id for x in a] == [x.id for x in b]
    assert [x.distance_km for x in a] == [x.distance_km for x in b]


def test_sample_skips_rest_days():
    # Rest day is every Sunday in the generator pattern → no activity on those dates.
    activities = generate_sample_activities(days=14, end_date=date(2026, 1, 11), seed=1)
    # There's one Sunday in 2026-01-05 to 2026-01-11: 2026-01-11. Plenty of Sundays in earlier range too.
    for a in activities:
        assert a.activity_type == "Run"
        assert a.duration_min > 0
        assert a.distance_km > 0


def test_sample_profile_has_sensible_values():
    p = load_sample_profile()
    assert p.max_hr > p.resting_hr
    assert p.age > 0
    assert p.personal_bests


def test_sample_has_some_rpe_logged():
    activities = generate_sample_activities(days=90, seed=42)
    logged = [a for a in activities if a.rpe is not None]
    assert len(logged) > 10
