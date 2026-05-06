"""Training-load metrics.

Implements the three families called out in the project plan:
- **TRIMP** (Banister 1975): heart-rate-weighted duration per session.
- **ACWR** (Gabbett 2016): acute (7-day) / chronic (28-day) load ratio.
- **Monotony / strain** (Foster 1998): weekly load variability.

Everything here is pure pandas/numpy — no I/O. That makes it easy to unit-test
against hand-computed expected values (see tests/test_metrics.py).
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

from ..data.models import AthleteProfile, TrainingActivity

# Gabbett reference bands for ACWR. Values below danger-low or above danger-high
# correlate with raised injury risk in the literature — used by the safety rules
# module and the UI to colour-code the number.
ACWR_SWEET_SPOT = (0.8, 1.3)
ACWR_DANGER_HIGH = 1.5
ACWR_DANGER_LOW = 0.5

# Foster monotony thresholds.
MONOTONY_HIGH = 2.0

# Default fallbacks when HR data is missing — RPE * duration heuristic (session-RPE method).
RPE_FALLBACK_MULTIPLIER = 1.0


def trimp(
    duration_min: float,
    avg_hr: int | None,
    resting_hr: int,
    max_hr: int,
    sex: str = "M",
    fallback_rpe: int | None = None,
    fallback_pace_min_per_km: float | None = None,
    threshold_pace_min_per_km: float | None = None,
) -> float:
    """Single-session training load.

    Resolution order — uses the most accurate signal available:

    1. **HR-based Banister TRIMP** (preferred): needs `avg_hr` plus rest/max HR.
    2. **Pace-based rTSS-style** (Coggan/Daniels): needs `fallback_pace_min_per_km`
       *and* `threshold_pace_min_per_km`. Returns ``duration_min × IF²`` where
       ``IF = threshold_pace / actual_pace`` clamped to [0.4, 1.4]. At threshold
       (IF=1.0) for 60 min this yields 60, comparable in magnitude to a tempo-
       intensity Banister TRIMP — so the two can mix in the same daily series.
    3. **Session-RPE** (Foster 2001): ``duration × RPE``.
    4. **Zero**: nothing usable.

    Mixed sessions in the same series degrade comparability slightly but ACWR is
    a *ratio* — units cancel — so trends remain meaningful.
    """
    if duration_min <= 0:
        return 0.0

    # 1. HR-based Banister
    if avg_hr is not None and avg_hr > 0:
        reserve = max(max_hr - resting_hr, 1)
        hr_ratio = (avg_hr - resting_hr) / reserve
        hr_ratio = max(0.0, min(hr_ratio, 1.0))
        if sex.upper() == "F":
            weight = 0.86 * math.exp(1.67 * hr_ratio)
        else:
            weight = 0.64 * math.exp(1.92 * hr_ratio)
        return duration_min * hr_ratio * weight

    # 2. Pace-based rTSS-style (HR-less fallback)
    if (
        fallback_pace_min_per_km
        and fallback_pace_min_per_km > 0
        and threshold_pace_min_per_km
        and threshold_pace_min_per_km > 0
    ):
        intensity = threshold_pace_min_per_km / max(fallback_pace_min_per_km, 0.1)
        intensity = max(0.4, min(intensity, 1.4))
        return duration_min * intensity**2

    # 3. Session-RPE
    if fallback_rpe is not None and fallback_rpe > 0:
        return duration_min * fallback_rpe * RPE_FALLBACK_MULTIPLIER

    return 0.0


def fitness_form(
    daily_load: pd.Series,
    *,
    ctl_window: int = 42,
    atl_window: int = 7,
) -> pd.DataFrame:
    """Banister fitness/fatigue/form model — TrainingPeaks-style CTL/ATL/TSB.

    - **CTL** (chronic training load, "fitness"): exp.-weighted 42-day mean.
    - **ATL** (acute training load, "fatigue"): exp.-weighted 7-day mean.
    - **TSB** (training stress balance, "form"): CTL − ATL.

    Sign of TSB tells you race readiness:

    - **TSB ≥ +25**: deeply detrained / over-rested.
    - **TSB +5 … +25**: peaked, race-ready window.
    - **TSB −10 … +5**: optimal training (productive fatigue).
    - **TSB < −30**: high overreaching risk.

    Uses ``alpha = 1/N`` matching the TrainingPeaks formulation. The series
    starts at zero, so the first ~3×CTL_window days are warm-up and shouldn't
    be trusted on their own; with a year of history the warm-up is invisible.
    """
    if daily_load.empty:
        return pd.DataFrame(columns=["ctl", "atl", "tsb"])
    ctl = daily_load.ewm(alpha=1.0 / ctl_window, adjust=False).mean()
    atl = daily_load.ewm(alpha=1.0 / atl_window, adjust=False).mean()
    return pd.DataFrame({"ctl": ctl, "atl": atl, "tsb": ctl - atl})


def estimate_rpe_from_hr(
    avg_hr: int | None,
    resting_hr: int,
    max_hr: int,
) -> int | None:
    """Synthesize a 1–10 RPE from heart-rate-reserve fraction.

    Karvonen's HRR ≈ Borg-1-10 mapping: ``RPE = round(HRR × 10)``, clipped
    to [1, 10]. HRR = (avg_hr − resting) / (max − resting). Useful as a
    fallback when the athlete didn't self-report RPE — most don't, every day.

    Returns ``None`` when ``avg_hr`` is missing, so the caller can distinguish
    "no data" from "very easy" (RPE 1).
    """
    if avg_hr is None or avg_hr <= 0:
        return None
    reserve = max(max_hr - resting_hr, 1)
    hrr = (avg_hr - resting_hr) / reserve
    hrr = max(0.0, min(hrr, 1.0))
    return max(1, min(10, round(hrr * 10)))


def build_load_timeseries(
    activities: Iterable[TrainingActivity],
    profile: AthleteProfile,
    *,
    start: date | None = None,
    end: date | None = None,
) -> pd.Series:
    """Aggregate per-session TRIMP into a continuous daily load series.

    Rest days are filled with 0 rather than skipped, so rolling windows line up
    with calendar days (Gabbett's ACWR is defined on a daily grid, not a
    per-session grid).
    """
    items = list(activities)
    if not items:
        return pd.Series(dtype=float, name="daily_load")

    threshold_pace = profile.effective_threshold_pace
    rows = [
        {
            "activity_date": a.activity_date,
            "load": trimp(
                duration_min=a.duration_min,
                avg_hr=a.avg_hr,
                resting_hr=profile.resting_hr,
                max_hr=profile.max_hr,
                sex=profile.sex,
                fallback_rpe=a.rpe,
                fallback_pace_min_per_km=a.avg_pace_min_per_km,
                threshold_pace_min_per_km=threshold_pace,
            ),
        }
        for a in items
    ]
    df = pd.DataFrame(rows)
    daily = df.groupby("activity_date", as_index=True)["load"].sum()

    series_start = start or daily.index.min()
    series_end = end or max(daily.index.max(), date.today())
    idx = pd.date_range(series_start, series_end, freq="D").date
    series = daily.reindex(idx, fill_value=0.0)
    series.name = "daily_load"
    series.index.name = "date"
    return series


def acwr_series(
    daily_load: pd.Series,
    *,
    acute_window: int = 7,
    chronic_window: int = 28,
) -> pd.DataFrame:
    """Return a DataFrame with columns `acute`, `chronic`, `acwr`.

    `chronic` uses the simple moving average for day N (inclusive of day N),
    which matches the most common implementation in the literature. Some
    studies use exponentially-weighted variants — documented as future work.
    """
    if daily_load.empty:
        return pd.DataFrame(columns=["acute", "chronic", "acwr"])

    acute = daily_load.rolling(window=acute_window, min_periods=1).mean()
    chronic = daily_load.rolling(window=chronic_window, min_periods=1).mean()
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = acute.divide(chronic).replace([np.inf, -np.inf], np.nan)
    return pd.DataFrame({"acute": acute, "chronic": chronic, "acwr": ratio})


def compute_monotony(daily_load: pd.Series, window: int = 7) -> float | None:
    """Foster monotony: mean / std of the last `window` days of load.

    Returns None if the window is too short or variance is effectively zero
    (rest block or identical daily loads — floating-point jitter means we
    can't rely on `std == 0`, so we compare to an epsilon of the mean).
    """
    if len(daily_load) < window:
        return None
    recent = daily_load.tail(window)
    mean = recent.mean()
    std = recent.std(ddof=0)
    if std <= max(mean, 1.0) * 1e-9:
        return None
    return float(mean / std)


def compute_strain(daily_load: pd.Series, window: int = 7) -> float | None:
    """Foster strain: weekly total load × monotony."""
    monotony = compute_monotony(daily_load, window=window)
    if monotony is None:
        return None
    return float(daily_load.tail(window).sum() * monotony)


@dataclass(frozen=True)
class LoadSummary:
    as_of: date
    acute_7d: float
    chronic_28d: float
    acwr: float | None
    monotony: float | None
    strain: float | None
    total_7d: float
    total_28d: float
    rpe_last_3_days: list[int | None]
    total_km_7d: float = 0.0
    total_km_28d: float = 0.0

    @property
    def acwr_zone(self) -> str:
        if self.acwr is None:
            return "tundmatu"
        if self.acwr >= ACWR_DANGER_HIGH:
            return "ülekoormuse oht"
        if self.acwr <= ACWR_DANGER_LOW:
            return "alakoormus"
        if ACWR_SWEET_SPOT[0] <= self.acwr <= ACWR_SWEET_SPOT[1]:
            return "optimaalne"
        return "piirialal"


def summarize_load(
    activities: Iterable[TrainingActivity],
    profile: AthleteProfile,
    *,
    as_of: date | None = None,
) -> LoadSummary:
    as_of = as_of or date.today()
    items = list(activities)
    daily = build_load_timeseries(items, profile, end=as_of)

    if daily.empty:
        return LoadSummary(as_of, 0.0, 0.0, None, None, None, 0.0, 0.0, [None, None, None])

    ratios = acwr_series(daily)
    latest = ratios.iloc[-1]

    by_date = {a.activity_date: a.rpe for a in items}
    rpe_last_3: list[int | None] = [
        by_date.get(as_of - timedelta(days=i)) for i in range(1, 4)
    ]
    cutoff_7 = as_of - timedelta(days=6)
    cutoff_28 = as_of - timedelta(days=27)
    total_km_7d = sum(a.distance_km for a in items if cutoff_7 <= a.activity_date <= as_of)
    total_km_28d = sum(a.distance_km for a in items if cutoff_28 <= a.activity_date <= as_of)

    return LoadSummary(
        as_of=as_of,
        acute_7d=float(latest["acute"]),
        chronic_28d=float(latest["chronic"]),
        acwr=None if pd.isna(latest["acwr"]) else float(latest["acwr"]),
        monotony=compute_monotony(daily),
        strain=compute_strain(daily),
        total_7d=float(daily.tail(7).sum()),
        total_28d=float(daily.tail(28).sum()),
        rpe_last_3_days=rpe_last_3,
        total_km_7d=float(total_km_7d),
        total_km_28d=float(total_km_28d),
    )
