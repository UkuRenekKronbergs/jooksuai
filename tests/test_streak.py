"""Tests for the daily-log streak counters."""

from __future__ import annotations

from datetime import date

from vorm.data.storage import DailyLogEntry
from vorm.ui.streak import longest_streak, streak_count


def _entry(d: date) -> DailyLogEntry:
    return DailyLogEntry(
        log_date=d,
        recommended_category="Jätka plaanipäraselt",
    )


def test_streak_zero_when_no_logs():
    assert streak_count([], date(2026, 5, 16)) == 0


def test_streak_counts_back_from_today():
    logs = [_entry(date(2026, 5, 14)), _entry(date(2026, 5, 15)), _entry(date(2026, 5, 16))]
    assert streak_count(logs, date(2026, 5, 16)) == 3


def test_streak_gives_one_day_grace_when_today_missing():
    """User opens app in the morning, hasn't logged today — yesterday's streak
    should still count rather than reading 0."""
    logs = [_entry(date(2026, 5, 14)), _entry(date(2026, 5, 15))]
    assert streak_count(logs, date(2026, 5, 16)) == 2


def test_streak_breaks_on_two_day_gap():
    """Once two consecutive days are missed, the streak is reset to 0."""
    logs = [_entry(date(2026, 5, 10)), _entry(date(2026, 5, 11))]
    assert streak_count(logs, date(2026, 5, 16)) == 0


def test_streak_ignores_isolated_logs_in_the_past():
    """An old entry far from today shouldn't extend the streak — only the run
    of consecutive days ending near today counts."""
    logs = [
        _entry(date(2026, 4, 1)),  # isolated
        _entry(date(2026, 5, 15)),
        _entry(date(2026, 5, 16)),
    ]
    assert streak_count(logs, date(2026, 5, 16)) == 2


def test_longest_streak_finds_best_run_in_history():
    logs = [
        # Run of 3
        _entry(date(2026, 4, 1)), _entry(date(2026, 4, 2)), _entry(date(2026, 4, 3)),
        # Run of 5 — should win
        _entry(date(2026, 5, 10)), _entry(date(2026, 5, 11)),
        _entry(date(2026, 5, 12)), _entry(date(2026, 5, 13)),
        _entry(date(2026, 5, 14)),
    ]
    assert longest_streak(logs) == 5


def test_longest_streak_single_entry():
    assert longest_streak([_entry(date(2026, 5, 16))]) == 1


def test_longest_streak_zero_on_empty():
    assert longest_streak([]) == 0
