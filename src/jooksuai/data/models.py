"""Core data models.

Intentionally simple dataclasses rather than Pydantic — the boundary conversions
(CSV/JSON/Strava API) are all in the loader modules, so the domain stays flat.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class TrainingActivity:
    id: str
    activity_date: date
    activity_type: str
    distance_km: float
    duration_min: float
    avg_hr: int | None = None
    max_hr_observed: int | None = None
    avg_pace_min_per_km: float | None = None
    elevation_gain_m: float | None = None
    rpe: int | None = None
    notes: str | None = None

    def is_run(self) -> bool:
        return self.activity_type.lower() in {"run", "trailrun", "virtualrun"}


@dataclass(frozen=True)
class DailySubjective:
    """Daily user-reported inputs that complement objective training data."""

    entry_date: date
    rpe_yesterday: int | None = None
    sleep_hours: float | None = None
    stress_level: int | None = None  # 1-5, user-reported
    illness: bool = False
    notes: str | None = None


@dataclass(frozen=True)
class AthleteProfile:
    name: str
    age: int
    sex: str  # "M" or "F"
    max_hr: int
    resting_hr: int
    training_years: int
    season_goal: str = ""
    personal_bests: dict[str, str] = field(default_factory=dict)

    @property
    def hr_reserve(self) -> int:
        return max(self.max_hr - self.resting_hr, 1)
