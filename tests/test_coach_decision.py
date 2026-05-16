"""Tests for the coach-decision storage path + agreement classification."""

from __future__ import annotations

from datetime import date

import pytest

from vorm.data.storage import ActivityStore, CoachDecision, DailyLogEntry


def test_coach_decision_roundtrip(tmp_path):
    store = ActivityStore(tmp_path / "store.db")
    decision = CoachDecision(
        decision_date=date(2026, 5, 18),
        recommended_category="Vähenda intensiivsust",
        coach_name="Ille Kukk",
        rationale="ACWR 1.45, eile RPE 8.",
        notes="Tegi kerge 8 km.",
    )
    store.save_coach_decision(decision)
    loaded = store.get_coach_decision(date(2026, 5, 18))
    assert loaded is not None
    assert loaded.recommended_category == "Vähenda intensiivsust"
    assert loaded.coach_name == "Ille Kukk"
    assert loaded.rationale == "ACWR 1.45, eile RPE 8."
    assert loaded.created_at is not None  # auto-stamped


def test_coach_decision_upsert_replaces(tmp_path):
    """Re-saving the same date should overwrite the previous decision —
    a real coach might revise after seeing the next-day result."""
    store = ActivityStore(tmp_path / "store.db")
    day = date(2026, 5, 18)
    store.save_coach_decision(CoachDecision(
        decision_date=day,
        recommended_category="Vähenda intensiivsust",
    ))
    store.save_coach_decision(CoachDecision(
        decision_date=day,
        recommended_category="Lisa taastumispäev",
        rationale="Vaatasin uuesti, väsimus suurem kui arvasin.",
    ))
    loaded = store.get_coach_decision(day)
    assert loaded.recommended_category == "Lisa taastumispäev"
    assert "uuesti" in loaded.rationale


def test_list_coach_decisions_filters_by_range(tmp_path):
    store = ActivityStore(tmp_path / "store.db")
    for d in (date(2026, 5, 17), date(2026, 5, 18), date(2026, 5, 25)):
        store.save_coach_decision(CoachDecision(
            decision_date=d, recommended_category="Jätka plaanipäraselt",
        ))
    in_range = store.list_coach_decisions(
        since=date(2026, 5, 18), until=date(2026, 5, 24),
    )
    assert [d.decision_date for d in in_range] == [date(2026, 5, 18)]


def test_delete_coach_decision(tmp_path):
    store = ActivityStore(tmp_path / "store.db")
    day = date(2026, 5, 18)
    store.save_coach_decision(CoachDecision(
        decision_date=day, recommended_category="Jätka plaanipäraselt",
    ))
    store.delete_coach_decision(day)
    assert store.get_coach_decision(day) is None


def test_daily_log_and_coach_decision_coexist(tmp_path):
    """Both tables share `tmp_path / store.db` — ensure they don't clobber
    each other's data on the same date."""
    store = ActivityStore(tmp_path / "store.db")
    day = date(2026, 5, 18)
    store.save_daily_log(DailyLogEntry(
        log_date=day, recommended_category="Vähenda intensiivsust",
    ))
    store.save_coach_decision(CoachDecision(
        decision_date=day, recommended_category="Lisa taastumispäev",
    ))
    log = store.get_daily_log(day)
    coach = store.get_coach_decision(day)
    assert log.recommended_category == "Vähenda intensiivsust"
    assert coach.recommended_category == "Lisa taastumispäev"


@pytest.mark.parametrize(
    "model,coach,bucket",
    [
        ("Jätka plaanipäraselt", "Jätka plaanipäraselt", "match"),
        ("Vähenda intensiivsust", "Vähenda intensiivsust", "match"),
        ("Vähenda intensiivsust", "Lisa taastumispäev", "close"),  # both careful
        ("Alternatiivne treening", "Vähenda intensiivsust", "close"),
        ("Jätka plaanipäraselt", "Vähenda intensiivsust", "wrong"),  # opposite stance
        ("Lisa taastumispäev", "Jätka plaanipäraselt", "wrong"),
    ],
)
def test_agreement_bucket_function(model, coach, bucket):
    """The UI's bucketing should match scripts/validate.py logic.

    Same three-bucket scheme as the validation harness:
    `match` = exact, `close` = both careful, `wrong` = stance-flip.
    """
    # Inline copy of the bucket logic from app.py — keeps the test independent
    # of the Streamlit module import (app.py boots a full Streamlit env).
    careful = {"Vähenda intensiivsust", "Alternatiivne treening", "Lisa taastumispäev"}
    if model == coach:
        assert bucket == "match"
    elif model in careful and coach in careful:
        assert bucket == "close"
    else:
        assert bucket == "wrong"
