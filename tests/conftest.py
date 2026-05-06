"""Shared test fixtures."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from vorm.data.models import AthleteProfile, TrainingActivity


@pytest.fixture
def profile() -> AthleteProfile:
    return AthleteProfile(
        name="Test",
        age=25,
        sex="M",
        max_hr=190,
        resting_hr=50,
        training_years=5,
        season_goal="",
        personal_bests={},
    )


@pytest.fixture
def today() -> date:
    return date(2026, 4, 20)


@pytest.fixture
def easy_run_factory(today):
    """Produce identical easy runs on consecutive days for deterministic tests."""

    def _make(days: int, avg_hr: int = 140, duration_min: float = 45.0, distance_km: float = 9.0, start: date | None = None, rpe: int | None = None) -> list[TrainingActivity]:
        start = start or today - timedelta(days=days - 1)
        return [
            TrainingActivity(
                id=f"test-{i}",
                activity_date=start + timedelta(days=i),
                activity_type="Run",
                distance_km=distance_km,
                duration_min=duration_min,
                avg_hr=avg_hr,
                rpe=rpe,
                notes="Easy",
            )
            for i in range(days)
        ]

    return _make
