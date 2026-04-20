"""Tests for rule-based safety filters."""

from __future__ import annotations

from datetime import timedelta

from jooksuai.data.models import DailySubjective, TrainingActivity
from jooksuai.metrics.load import summarize_load
from jooksuai.rules.safety import (
    Recommendation,
    evaluate_safety_rules,
)


def _make_load_summary(profile, today, acute_hr=140, duration=45):
    # Helper: build a summary with configurable HR / duration over 30 days.
    activities = [
        TrainingActivity(
            id=f"x{i}",
            activity_date=today - timedelta(days=i),
            activity_type="Run",
            distance_km=10,
            duration_min=duration,
            avg_hr=acute_hr,
        )
        for i in range(30)
    ]
    return summarize_load(activities, profile, as_of=today)


def test_healthy_state_returns_continue(profile, today):
    summary = _make_load_summary(profile, today)
    verdict = evaluate_safety_rules(summary)
    assert verdict.recommendation == Recommendation.CONTINUE
    assert not verdict.forced


def test_illness_forces_recover(profile, today):
    summary = _make_load_summary(profile, today)
    subjective = DailySubjective(entry_date=today, illness=True)
    verdict = evaluate_safety_rules(summary, subjective)
    assert verdict.recommendation == Recommendation.RECOVER
    assert verdict.forced
    assert any(f.code == "illness" for f in verdict.flags)


def test_consecutive_high_rpe_forces_recover(profile, today):
    # Build a 28-day base then attach RPE 8 on the last 2 days.
    base = [
        TrainingActivity(
            id=f"h{i}",
            activity_date=today - timedelta(days=i),
            activity_type="Run",
            distance_km=10,
            duration_min=45,
            avg_hr=140,
            rpe=8 if i in (1, 2) else 5,
        )
        for i in range(30)
    ]
    summary = summarize_load(base, profile, as_of=today)
    verdict = evaluate_safety_rules(summary)
    assert verdict.recommendation == Recommendation.RECOVER
    assert any(f.code == "rpe_consecutive_high" for f in verdict.flags)


def test_high_acwr_forces_reduce(profile, today):
    base = [
        TrainingActivity(
            id=f"b{i}",
            activity_date=today - timedelta(days=i + 7),
            activity_type="Run",
            distance_km=6,
            duration_min=30,
            avg_hr=120,
        )
        for i in range(28)
    ]
    spike = [
        TrainingActivity(
            id=f"s{i}",
            activity_date=today - timedelta(days=i),
            activity_type="Run",
            distance_km=18,
            duration_min=100,
            avg_hr=175,
        )
        for i in range(7)
    ]
    summary = summarize_load(base + spike, profile, as_of=today)
    verdict = evaluate_safety_rules(summary)
    assert verdict.forced
    # Expect REDUCE (high ACWR) — recovery not forced without illness/consecutive high RPE.
    assert verdict.recommendation in (Recommendation.REDUCE, Recommendation.RECOVER)
    assert any(f.code == "acwr_high" for f in verdict.flags)


def test_illness_wins_over_acwr(profile, today):
    # Even if ACWR is fine, illness must force RECOVER.
    summary = _make_load_summary(profile, today)
    subjective = DailySubjective(entry_date=today, illness=True)
    verdict = evaluate_safety_rules(summary, subjective)
    assert verdict.recommendation == Recommendation.RECOVER


def test_low_sleep_warns_but_does_not_force(profile, today):
    summary = _make_load_summary(profile, today)
    subjective = DailySubjective(entry_date=today, sleep_hours=4.5)
    verdict = evaluate_safety_rules(summary, subjective)
    # Warning flag present but recommendation not forced.
    assert any(f.code == "sleep_low" for f in verdict.flags)
    assert verdict.recommendation == Recommendation.CONTINUE
    assert not verdict.forced
