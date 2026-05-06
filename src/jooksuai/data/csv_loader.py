"""Load activities from a CSV file.

Accepts either the native format (matching TrainingActivity fields) or a
Strava-export CSV with the subset of columns we need.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime
from io import StringIO
from pathlib import Path

import pandas as pd

from .models import TrainingActivity

_NATIVE_COLUMNS = {
    "id", "activity_date", "activity_type", "distance_km", "duration_min",
}

# Strava bulk-export tends to duplicate columns (pandas suffixes them with .1):
# the first batch is human-formatted, the second is raw units. We prefer the
# second when available (raw m, m/s, sec). The renamer below reflects that.
_STRAVA_RENAME = {
    "Activity ID": "id",
    "Activity Date": "activity_date",
    "Activity Type": "activity_type",
    "Distance": "distance_km",
    "Elapsed Time": "duration_sec",
    "Moving Time": "moving_time_sec",
    "Average Heart Rate": "avg_hr",
    "Max Heart Rate": "max_hr_observed",
    "Elevation Gain": "elevation_gain_m",
    "Activity Name": "notes",
    "Average Speed": "avg_speed_mps",
}


def load_activities_csv(source: str | Path | StringIO) -> list[TrainingActivity]:
    df = pd.read_csv(source)
    if _NATIVE_COLUMNS.issubset(df.columns):
        return _parse_native(df)
    return _parse_strava(df)


def _parse_native(df: pd.DataFrame) -> list[TrainingActivity]:
    records: list[TrainingActivity] = []
    for row in df.to_dict(orient="records"):
        records.append(
            TrainingActivity(
                id=str(row["id"]),
                activity_date=_to_date(row["activity_date"]),
                activity_type=str(row["activity_type"]),
                distance_km=float(row["distance_km"]),
                duration_min=float(row["duration_min"]),
                avg_hr=_opt_int(row.get("avg_hr")),
                max_hr_observed=_opt_int(row.get("max_hr_observed")),
                avg_pace_min_per_km=_opt_float(row.get("avg_pace_min_per_km")),
                elevation_gain_m=_opt_float(row.get("elevation_gain_m")),
                rpe=_opt_int(row.get("rpe")),
                notes=_opt_str(row.get("notes")),
            )
        )
    return records


def _parse_strava(df: pd.DataFrame) -> list[TrainingActivity]:
    renamed = df.rename(columns=_STRAVA_RENAME)
    records: list[TrainingActivity] = []
    for row in renamed.to_dict(orient="records"):
        if "id" not in row or pd.isna(row["id"]):
            continue

        distance_km = _resolve_distance_km(row)
        duration_min = _resolve_duration_min(row)
        avg_pace = _resolve_pace_min_per_km(row, distance_km, duration_min)

        records.append(
            TrainingActivity(
                id=str(row["id"]),
                activity_date=_to_date(row["activity_date"]),
                activity_type=str(row.get("activity_type") or "Run"),
                distance_km=distance_km,
                duration_min=duration_min,
                avg_hr=_opt_int(row.get("avg_hr")),
                max_hr_observed=_resolve_max_hr_observed(row),
                avg_pace_min_per_km=avg_pace,
                elevation_gain_m=_opt_float(row.get("elevation_gain_m")),
                notes=_opt_str(row.get("notes")),
            )
        )
    return records


def _resolve_distance_km(row: dict) -> float:
    """Pick distance from Strava's duplicated Distance / Distance.1 columns.

    Bulk-export emits Distance (km) AND Distance.1 (m); some accounts emit only
    one. Detect by magnitude — values > 500 are metres.
    """
    for key in ("distance_km", "Distance.1"):
        val = row.get(key)
        if val is None or (isinstance(val, float) and pd.isna(val)):
            continue
        try:
            f = float(val)
        except (TypeError, ValueError):
            continue
        if f <= 0:
            continue
        return f / 1000 if f > 500 else f
    return 0.0


def _resolve_duration_min(row: dict) -> float:
    """Prefer Moving Time over Elapsed Time — load shouldn't count café stops."""
    for key in ("moving_time_sec", "duration_sec", "Elapsed Time.1"):
        val = row.get(key)
        if val is None or (isinstance(val, float) and pd.isna(val)):
            continue
        try:
            f = float(val)
        except (TypeError, ValueError):
            continue
        if f > 0:
            return f / 60.0
    return 0.0


def _resolve_max_hr_observed(row: dict) -> int | None:
    """Strava bulk-export has two ``Max Heart Rate`` columns. The first is in the
    activity-summary block (often empty); the second is in the per-activity stats
    block (the real one, paired with ``Average Heart Rate``). Pandas renames the
    duplicate to ``Max Heart Rate.1`` — try the populated one first.
    """
    for key in ("Max Heart Rate.1", "max_hr_observed"):
        val = _opt_int(row.get(key))
        if val:
            return val
    return None


def _resolve_pace_min_per_km(row: dict, distance_km: float, duration_min: float) -> float | None:
    """Average pace in min/km. Try `Average Speed` (m/s) first, else compute."""
    speed = row.get("avg_speed_mps")
    if speed is not None and not (isinstance(speed, float) and pd.isna(speed)):
        try:
            f = float(speed)
            if f > 0:
                # min/km = (1000 / m_per_s) / 60
                return round((1000.0 / f) / 60.0, 3)
        except (TypeError, ValueError):
            pass
    if distance_km > 0 and duration_min > 0:
        return round(duration_min / distance_km, 3)
    return None


def write_activities_csv(activities: Iterable[TrainingActivity], destination: str | Path) -> None:
    rows = [
        {
            "id": a.id,
            "activity_date": a.activity_date.isoformat(),
            "activity_type": a.activity_type,
            "distance_km": a.distance_km,
            "duration_min": a.duration_min,
            "avg_hr": a.avg_hr,
            "max_hr_observed": a.max_hr_observed,
            "avg_pace_min_per_km": a.avg_pace_min_per_km,
            "elevation_gain_m": a.elevation_gain_m,
            "rpe": a.rpe,
            "notes": a.notes,
        }
        for a in activities
    ]
    pd.DataFrame(rows).to_csv(destination, index=False)


def _to_date(value) -> date:
    if isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()
    s = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%b %d, %Y, %I:%M:%S %p"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return datetime.fromisoformat(s.replace("Z", "+00:00")).date()


def _opt_int(value) -> int | None:
    if value is None or (isinstance(value, float) and pd.isna(value)) or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _opt_float(value) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)) or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _opt_str(value) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    s = str(value).strip()
    return s or None
