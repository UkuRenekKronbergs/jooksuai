"""Data models for training plan generation.

Kept independent from `data.models` because TrainingActivity (what actually
happened) and PlannedSession (what the coach prescribed) differ in which
fields are required — plans have prescriptions (target pace, intensity zone)
rather than measurements (actual HR, actual pace).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass(frozen=True)
class PlanGoal:
    """The race/event the plan is building towards."""

    event_name: str
    distance_km: float
    target_time_minutes: float  # stored in minutes to keep arithmetic simple
    event_date: date

    @property
    def target_pace_min_per_km(self) -> float:
        if self.distance_km <= 0:
            return 0.0
        return self.target_time_minutes / self.distance_km

    def target_time_formatted(self) -> str:
        """Human-readable `M:SS` or `H:MM:SS`."""
        total_seconds = int(round(self.target_time_minutes * 60))
        h, rem = divmod(total_seconds, 3600)
        m, s = divmod(rem, 60)
        if h > 0:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"


@dataclass(frozen=True)
class PlannedSession:
    """A single prescribed training session.

    `intensity_zone` uses the common 5-zone model (Z1 recovery, Z2 aerobic,
    Z3 tempo, Z4 threshold, Z5 VO2max) — matches what most Estonian running
    coaches use, keeps the prompt output tractable.
    """

    session_date: date
    session_type: str
    duration_min: float
    intensity_zone: str  # Z1–Z5, or "Rest"
    target_pace_min_per_km: float | None = None
    distance_km: float | None = None
    description: str = ""

    def is_rest(self) -> bool:
        return self.session_type.lower() in {"rest", "puhkus"} or self.intensity_zone.lower() == "rest"


@dataclass(frozen=True)
class WeekPlan:
    week_number: int
    week_start: date
    phase: str  # "base" | "build" | "peak" | "taper" | "race"
    target_volume_km: float
    sessions: list[PlannedSession] = field(default_factory=list)
    notes: str = ""

    @property
    def total_duration_min(self) -> float:
        return sum(s.duration_min for s in self.sessions)


@dataclass(frozen=True)
class TrainingPlan:
    goal: PlanGoal
    generated_at: datetime
    model: str
    weeks: list[WeekPlan] = field(default_factory=list)
    overview: str = ""  # coach-style summary of the plan's philosophy
    raw_text: str = ""  # verbatim LLM output, for audit

    @property
    def total_weeks(self) -> int:
        return len(self.weeks)

    @property
    def all_sessions(self) -> list[PlannedSession]:
        return [s for w in self.weeks for s in w.sessions]
