"""Tests for the planning module's pure-logic parts (parser, models).

LLM orchestration is exercised via a stubbed JSON fixture rather than a live
API call — that stays deterministic and keeps CI free of network deps / keys.
"""

from __future__ import annotations

import json
from datetime import date, datetime

import pytest

from vorm.planning.generator import _plan_from_json
from vorm.planning.models import PlanGoal, PlannedSession, TrainingPlan, WeekPlan


@pytest.fixture
def goal() -> PlanGoal:
    return PlanGoal(
        event_name="Tallinna 10 km",
        distance_km=10.0,
        target_time_minutes=34.5,  # 34:30
        event_date=date(2026, 9, 6),
    )


def test_plan_goal_target_pace():
    g = PlanGoal("Test", 10.0, 40.0, date(2026, 6, 1))
    assert g.target_pace_min_per_km == 4.0  # 40 min / 10 km


def test_plan_goal_formatted_time_minutes_only():
    g = PlanGoal("Test", 10.0, 34.5, date(2026, 6, 1))
    assert g.target_time_formatted() == "34:30"


def test_plan_goal_formatted_time_hours():
    g = PlanGoal("Marathon", 42.195, 145.0, date(2026, 6, 1))  # 2:25:00
    assert g.target_time_formatted() == "2:25:00"


def test_planned_session_is_rest_detects_variants():
    rest_by_type = PlannedSession(date(2026, 5, 1), "Rest", 0, "Z1")
    rest_by_zone = PlannedSession(date(2026, 5, 1), "Easy", 0, "Rest")
    not_rest = PlannedSession(date(2026, 5, 1), "Easy", 30, "Z2")
    assert rest_by_type.is_rest()
    assert rest_by_zone.is_rest()
    assert not not_rest.is_rest()


def test_week_plan_total_duration():
    w = WeekPlan(
        week_number=1, week_start=date(2026, 5, 1), phase="base", target_volume_km=40,
        sessions=[
            PlannedSession(date(2026, 5, 1), "Easy", 30, "Z2"),
            PlannedSession(date(2026, 5, 2), "Tempo", 45, "Z4"),
            PlannedSession(date(2026, 5, 3), "Rest", 0, "Rest"),
        ],
    )
    assert w.total_duration_min == 75


def test_training_plan_all_sessions_flattens_weeks(goal):
    w1 = WeekPlan(1, date(2026, 5, 1), "base", 40, sessions=[
        PlannedSession(date(2026, 5, 1), "Easy", 30, "Z2"),
        PlannedSession(date(2026, 5, 2), "Rest", 0, "Rest"),
    ])
    w2 = WeekPlan(2, date(2026, 5, 8), "base", 45, sessions=[
        PlannedSession(date(2026, 5, 8), "Easy", 30, "Z2"),
    ])
    plan = TrainingPlan(goal=goal, generated_at=datetime.now(), model="test", weeks=[w1, w2])
    assert len(plan.all_sessions) == 3
    assert plan.total_weeks == 2


# --- Parser tests --------------------------------------------------------

_SAMPLE_JSON = {
    "overview": "Klassikaline 12-nädalane build-up...",
    "weeks": [
        {
            "week_number": 1,
            "week_start": "2026-05-01",
            "phase": "base",
            "target_volume_km": 50,
            "notes": "Base nädal, fokusseeri aeroobsele mahule.",
            "sessions": [
                {
                    "session_date": "2026-05-01",
                    "session_type": "Easy aerobic",
                    "duration_min": 45,
                    "intensity_zone": "Z2",
                    "target_pace_min_per_km": 4.8,
                    "distance_km": 9.4,
                    "description": "Rahulik aeroobne jooks.",
                },
                {
                    "session_date": "2026-05-02",
                    "session_type": "Rest",
                    "duration_min": 0,
                    "intensity_zone": "Rest",
                    "target_pace_min_per_km": None,
                    "distance_km": None,
                    "description": "",
                },
            ],
        }
    ],
}


def test_parse_plan_from_json_builds_full_structure(goal):
    plan = _plan_from_json(_SAMPLE_JSON, goal=goal, model="test-model", raw_text=json.dumps(_SAMPLE_JSON))
    assert plan.total_weeks == 1
    assert plan.overview.startswith("Klassikaline")
    assert plan.model == "test-model"
    w = plan.weeks[0]
    assert w.phase == "base"
    assert w.target_volume_km == 50
    assert len(w.sessions) == 2
    first = w.sessions[0]
    assert first.session_date == date(2026, 5, 1)
    assert first.intensity_zone == "Z2"
    assert first.target_pace_min_per_km == 4.8
    rest = w.sessions[1]
    assert rest.is_rest()
    assert rest.target_pace_min_per_km is None


def test_parse_plan_handles_missing_optional_fields(goal):
    minimal = {
        "overview": "",
        "weeks": [
            {
                "week_number": 1,
                "week_start": "2026-05-01",
                "phase": "base",
                "target_volume_km": 40,
                "sessions": [
                    {
                        "session_date": "2026-05-01",
                        "session_type": "Easy",
                        "duration_min": 30,
                        "intensity_zone": "Z2",
                    }
                ],
            }
        ],
    }
    plan = _plan_from_json(minimal, goal=goal, model="test", raw_text="")
    s = plan.weeks[0].sessions[0]
    assert s.target_pace_min_per_km is None
    assert s.description == ""


def test_parse_plan_empty_weeks(goal):
    plan = _plan_from_json({"overview": "x", "weeks": []}, goal=goal, model="test", raw_text="")
    assert plan.total_weeks == 0
    assert plan.all_sessions == []


def test_parse_plan_recovers_from_flat_training_plan_key(goal):
    """Open models sometimes emit a flat `training_plan` array — we should group it into weeks."""
    flat_shape = {
        "overview": "Some plan",
        "training_plan": [
            {"session_date": "2026-05-01", "session_type": "Easy", "duration_min": 45, "intensity_zone": "Z2", "distance_km": 9},
            {"session_date": "2026-05-02", "session_type": "Tempo", "duration_min": 55, "intensity_zone": "Z4", "distance_km": 12},
            {"session_date": "2026-05-05", "session_type": "Easy", "duration_min": 40, "intensity_zone": "Z2", "distance_km": 8},
            # Second week
            {"session_date": "2026-05-09", "session_type": "Long", "duration_min": 90, "intensity_zone": "Z2", "distance_km": 20},
            {"session_date": "2026-05-11", "session_type": "Intervals", "duration_min": 60, "intensity_zone": "Z5", "distance_km": 12},
        ],
    }
    plan = _plan_from_json(flat_shape, goal=goal, model="test", raw_text="")
    assert plan.total_weeks == 2
    assert len(plan.all_sessions) == 5
    # Week 1 should contain the first 3 sessions
    assert len(plan.weeks[0].sessions) == 3
    assert plan.weeks[0].week_start == date(2026, 5, 1)
    # Week 2 should contain the last 2
    assert len(plan.weeks[1].sessions) == 2
    # Volume computed from distances
    assert plan.weeks[0].target_volume_km == 29  # 9 + 12 + 8
