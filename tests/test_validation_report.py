"""Smoke tests for the §4 validation-report PDF generator.

The PDF binary itself isn't worth assert-ing structure on — these tests just
prove the function runs without exception across the realistic input shapes
(empty data, daily-logs only, full set with coach decisions).
"""

from __future__ import annotations

from datetime import date

import pytest

from vorm.data.models import AthleteProfile
from vorm.data.storage import CoachDecision, DailyLogEntry
from vorm.validation import build_validation_report


@pytest.fixture
def profile():
    return AthleteProfile(
        name="Test Runner",
        age=30, sex="M",
        max_hr=190, resting_hr=50,
        training_years=8,
        season_goal="sub-35 10 km",
    )


def test_report_runs_with_empty_logs_and_decisions(profile):
    pdf = build_validation_report(profile, [], [])
    assert pdf.startswith(b"%PDF-"), "Output must be a real PDF document"
    assert len(pdf) > 1000  # has actual content beyond the header


def test_report_runs_with_only_daily_logs(profile):
    logs = [
        DailyLogEntry(
            log_date=date(2026, 5, 10 + i),
            recommended_category="Jätka plaanipäraselt",
            usefulness=4,
            followed="yes",
        )
        for i in range(5)
    ]
    pdf = build_validation_report(profile, logs, [])
    assert pdf.startswith(b"%PDF-")


def test_report_handles_full_dataset_with_coach_matches(profile):
    logs = [
        DailyLogEntry(
            log_date=date(2026, 5, 18 + i),
            recommended_category="Vähenda intensiivsust",
            usefulness=4, persuasiveness=5, followed="yes",
            next_session_feeling=4,
        )
        for i in range(3)
    ]
    decisions = [
        # 1 match, 1 close, 1 wrong
        CoachDecision(
            decision_date=date(2026, 5, 18),
            recommended_category="Vähenda intensiivsust",
        ),
        CoachDecision(
            decision_date=date(2026, 5, 19),
            recommended_category="Lisa taastumispäev",
        ),
        CoachDecision(
            decision_date=date(2026, 5, 20),
            recommended_category="Jätka plaanipäraselt",
        ),
    ]
    pdf = build_validation_report(profile, logs, decisions)
    assert pdf.startswith(b"%PDF-")
    assert len(pdf) > 1500  # extra tables added content


def test_report_handles_estonian_diacritics(profile):
    """The athlete name and category strings carry õäöü — ensure no
    encoding error blows up the build."""
    logs = [DailyLogEntry(
        log_date=date(2026, 5, 18),
        recommended_category="Vähenda intensiivsust",
        notes="Tegin lühikese 8 km, jõudsin vaid pärast õhtut.",
    )]
    pdf = build_validation_report(
        AthleteProfile(
            name="Õun Märk",
            age=28, sex="M", max_hr=185, resting_hr=55,
            training_years=4,
        ),
        logs,
        [],
    )
    assert pdf.startswith(b"%PDF-")
