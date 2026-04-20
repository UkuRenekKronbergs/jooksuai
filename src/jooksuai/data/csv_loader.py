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

_STRAVA_RENAME = {
    "Activity ID": "id",
    "Activity Date": "activity_date",
    "Activity Type": "activity_type",
    "Distance": "distance_km",
    "Elapsed Time": "duration_sec",
    "Moving Time": "duration_min_maybe_sec",
    "Average Heart Rate": "avg_hr",
    "Max Heart Rate": "max_hr_observed",
    "Elevation Gain": "elevation_gain_m",
    "Activity Name": "notes",
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
        distance_val = row.get("distance_km")
        # Strava export uses meters when Distance column lacks units; detect > 500 = meters.
        distance_km = float(distance_val) / 1000 if distance_val and float(distance_val) > 500 else float(distance_val or 0)
        duration_sec = row.get("duration_sec")
        duration_min = float(duration_sec) / 60 if duration_sec else 0.0
        records.append(
            TrainingActivity(
                id=str(row["id"]),
                activity_date=_to_date(row["activity_date"]),
                activity_type=str(row.get("activity_type") or "Run"),
                distance_km=distance_km,
                duration_min=duration_min,
                avg_hr=_opt_int(row.get("avg_hr")),
                max_hr_observed=_opt_int(row.get("max_hr_observed")),
                elevation_gain_m=_opt_float(row.get("elevation_gain_m")),
                notes=_opt_str(row.get("notes")),
            )
        )
    return records


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
