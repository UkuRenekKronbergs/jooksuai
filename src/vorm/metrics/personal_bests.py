"""Personal best detection and progression over time.

A "PB" here is the fastest activity whose distance falls within ±tolerance of
a standard race distance (1k, 3k, 5k, 10k, half, marathon). This catches both
real races and tempo/threshold workouts at race distance — which is fine,
because if you're running a 5k flat-out in training, that *is* your current
5k fitness ceiling.

Lap-level PBs (e.g. fastest 5k split inside a 10k race) would need raw GPS
samples; that's a future module.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

import pandas as pd

from ..data.models import TrainingActivity

# Standard race distances in metres-precise km. Marathon and half use the
# canonical IAAF lengths so a 42.20 km activity matches.
PB_DISTANCES: list[tuple[str, float]] = [
    ("1 km", 1.0),
    ("3 km", 3.0),
    ("5 km", 5.0),
    ("10 km", 10.0),
    ("Poolmaraton", 21.0975),
    ("Maraton", 42.195),
]

DEFAULT_TOLERANCE = 0.05  # ±5% — accommodates GPS drift and rounded splits.


@dataclass(frozen=True)
class PersonalBest:
    distance_label: str
    nominal_distance_km: float
    activity_distance_km: float
    duration_min: float
    activity_date: date
    activity_id: str
    activity_notes: str | None = None

    @property
    def pace_min_per_km(self) -> float:
        if self.activity_distance_km <= 0:
            return 0.0
        return self.duration_min / self.activity_distance_km

    @property
    def time_formatted(self) -> str:
        total_seconds = int(round(self.duration_min * 60))
        h, rem = divmod(total_seconds, 3600)
        m, s = divmod(rem, 60)
        if h > 0:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"


def find_personal_bests(
    activities: Iterable[TrainingActivity],
    *,
    distances: list[tuple[str, float]] | None = None,
    tolerance: float = DEFAULT_TOLERANCE,
) -> list[PersonalBest]:
    """Return one PB per standard distance, fastest activity wins.

    Skips distances with no qualifying activity instead of returning a stub
    record — callers should treat absence as "no PB at this distance yet".
    """
    distances = distances or PB_DISTANCES
    items = [a for a in activities if a.is_run() and a.distance_km > 0 and a.duration_min > 0]
    pbs: list[PersonalBest] = []
    for label, nominal in distances:
        candidates = [
            a for a in items
            if abs(a.distance_km - nominal) / nominal <= tolerance
        ]
        if not candidates:
            continue
        best = min(candidates, key=lambda a: a.duration_min)
        pbs.append(
            PersonalBest(
                distance_label=label,
                nominal_distance_km=nominal,
                activity_distance_km=best.distance_km,
                duration_min=best.duration_min,
                activity_date=best.activity_date,
                activity_id=best.id,
                activity_notes=best.notes,
            )
        )
    return pbs


def progression_at_distance(
    activities: Iterable[TrainingActivity],
    nominal_km: float,
    *,
    tolerance: float = DEFAULT_TOLERANCE,
) -> pd.DataFrame:
    """Every qualifying attempt + a running-minimum "PB so far" trace.

    Output columns:
    - ``date`` — activity date (Timestamp)
    - ``duration_min`` — actual duration of that attempt
    - ``pace_min_per_km`` — observed pace
    - ``pb_so_far_min`` — running minimum duration (step function: each row
      either equals or beats the previous)
    - ``pb_so_far_pace`` — pace corresponding to ``pb_so_far_min`` over the
      nominal distance (so the line is comparable across attempts whose
      actual distance varies within the tolerance band)
    - ``activity_id``, ``activity_notes`` — for hover tooltips
    """
    candidates = [
        a for a in activities
        if a.is_run() and a.distance_km > 0 and a.duration_min > 0
        and abs(a.distance_km - nominal_km) / nominal_km <= tolerance
    ]
    if not candidates:
        return pd.DataFrame(
            columns=["date", "duration_min", "pace_min_per_km",
                     "pb_so_far_min", "pb_so_far_pace",
                     "activity_id", "activity_notes"]
        )
    candidates.sort(key=lambda a: a.activity_date)
    df = pd.DataFrame([
        {
            "date": pd.Timestamp(a.activity_date),
            "duration_min": a.duration_min,
            "pace_min_per_km": a.duration_min / a.distance_km,
            "activity_id": a.id,
            "activity_notes": a.notes or "",
        }
        for a in candidates
    ])
    df["pb_so_far_min"] = df["duration_min"].cummin()
    df["pb_so_far_pace"] = df["pb_so_far_min"] / nominal_km
    return df


__all__ = [
    "DEFAULT_TOLERANCE",
    "PB_DISTANCES",
    "PersonalBest",
    "find_personal_bests",
    "progression_at_distance",
]
