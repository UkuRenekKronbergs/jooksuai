"""Tests for AthleteProfile helpers, especially threshold-pace derivation."""

from __future__ import annotations

import math

from vorm.data.models import AthleteProfile


def _profile(**overrides) -> AthleteProfile:
    defaults = dict(
        name="Test", age=25, sex="M", max_hr=190, resting_hr=50, training_years=5,
    )
    defaults.update(overrides)
    return AthleteProfile(**defaults)


def test_explicit_threshold_pace_wins_over_pb():
    p = _profile(threshold_pace_min_per_km=3.40, personal_bests={"10000m": "34:40"})
    assert p.effective_threshold_pace == 3.40


def test_threshold_derived_from_10k_pb():
    # 34:40 for 10 km = 3.467 min/km pace; × 1.03 ≈ 3.571
    p = _profile(personal_bests={"10000m": "34:40"})
    expected = (34 + 40 / 60) / 10 * 1.03
    assert math.isclose(p.effective_threshold_pace, expected, abs_tol=0.01)


def test_threshold_derived_from_5k_pb_when_no_10k():
    # 16:20 for 5 km = 3.267 min/km; × 1.07 ≈ 3.495
    p = _profile(personal_bests={"5000m": "16:20"})
    expected = (16 + 20 / 60) / 5 * 1.07
    assert math.isclose(p.effective_threshold_pace, expected, abs_tol=0.01)


def test_threshold_prefers_10k_over_5k_when_both_present():
    p = _profile(personal_bests={"5000m": "16:20", "10000m": "34:40"})
    ten_k_based = (34 + 40 / 60) / 10 * 1.03
    assert math.isclose(p.effective_threshold_pace, ten_k_based, abs_tol=0.01)


def test_no_pb_returns_none():
    p = _profile(personal_bests={})
    assert p.effective_threshold_pace is None


def test_malformed_pb_string_is_ignored():
    p = _profile(personal_bests={"10000m": "not a time"})
    assert p.effective_threshold_pace is None
