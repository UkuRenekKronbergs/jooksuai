"""Synthetic training-data generator.

The public GitHub repo can't ship anyone's real Strava history, but the app
needs *something* to demo against. This module builds ~90 days of plausible
middle-distance training data using a deterministic RNG so tests and screenshots
stay reproducible.

Design of the fake athlete: a ~17-min 5k runner doing a typical block:
- 5-6 runs per week
- long run on Sundays
- one interval session mid-week
- one tempo session
- rest of the week easy aerobic
- 4-week cycle with a deload every 4th week (lower volume)
"""

from __future__ import annotations

import random
from datetime import date, timedelta

from .models import AthleteProfile, TrainingActivity

_WEEKLY_PATTERN = [
    # (name, intensity, base_distance_km, base_duration_min, hr_fraction_of_reserve)
    ("Easy aerobic", "easy", 10.0, 50.0, 0.60),
    ("Tempo 4x1km", "threshold", 12.0, 55.0, 0.82),
    ("Easy aerobic", "easy", 8.0, 40.0, 0.58),
    ("VO2 intervals 8x400m", "vo2", 11.0, 50.0, 0.90),
    ("Recovery", "recovery", 6.0, 32.0, 0.50),
    ("Rest", "rest", 0.0, 0.0, 0.0),
    ("Long run", "long", 18.0, 85.0, 0.65),
]


def load_sample_profile() -> AthleteProfile:
    return AthleteProfile(
        name="Näidissportlane",
        age=26,
        sex="M",
        max_hr=195,
        resting_hr=48,
        training_years=8,
        season_goal="5000 m PB sügishooajal (alla 16:00)",
        personal_bests={
            "1500m": "4:05",
            "3000m": "8:55",
            "5000m": "16:20",
            "10000m": "34:40",
        },
    )


def generate_sample_activities(
    *,
    days: int = 90,
    end_date: date | None = None,
    seed: int = 42,
    athlete: AthleteProfile | None = None,
) -> list[TrainingActivity]:
    """Generate a deterministic sequence of synthetic running activities.

    Days without a run (rest days, illness) are omitted rather than represented
    as zero-duration entries — matches how Strava actually behaves.
    """
    if end_date is None:
        end_date = date.today()
    if athlete is None:
        athlete = load_sample_profile()

    rng = random.Random(seed)
    start = end_date - timedelta(days=days - 1)

    activities: list[TrainingActivity] = []

    for offset in range(days):
        day = start + timedelta(days=offset)
        weekday = day.weekday()
        pattern = _WEEKLY_PATTERN[weekday]
        name, intensity, base_km, base_min, hr_frac = pattern

        if intensity == "rest":
            continue

        week_of_block = (offset // 7) % 4
        load_multiplier = 0.7 if week_of_block == 3 else 1.0

        # simulate a one-week illness dip once per ~60 days
        if (offset // 60) > 0 and 5 <= (offset % 60) <= 10:
            if rng.random() < 0.35:
                continue

        jitter = rng.uniform(0.92, 1.08)
        distance = round(base_km * load_multiplier * jitter, 2)
        duration = round(base_min * load_multiplier * jitter, 1)
        hr = int(athlete.resting_hr + hr_frac * athlete.hr_reserve + rng.randint(-3, 3))
        pace = round(duration / distance, 2) if distance > 0 else None
        elev = round(rng.uniform(20, 150) * (duration / 40), 1)

        # simulate the athlete occasionally logging RPE (~50% of days)
        rpe = None
        if rng.random() < 0.5:
            rpe_base = {"easy": 3, "recovery": 2, "long": 5, "tempo": 7, "threshold": 7, "vo2": 8}
            rpe = rpe_base.get(intensity, 4) + rng.choice([-1, 0, 0, 1])
            rpe = max(1, min(10, rpe))

        activities.append(
            TrainingActivity(
                id=f"sample-{day.isoformat()}",
                activity_date=day,
                activity_type="Run",
                distance_km=distance,
                duration_min=duration,
                avg_hr=hr,
                max_hr_observed=hr + rng.randint(5, 15),
                avg_pace_min_per_km=pace,
                elevation_gain_m=elev,
                rpe=rpe,
                notes=name,
            )
        )

    return activities
