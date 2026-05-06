"""Polar export → Strava enrichment.

Polar Flow exports `training-session-*.json` files containing already-computed
``hrAvg`` and ``hrMax`` per workout. Strava's bulk-export CSV often misses
``Average Heart Rate`` (especially for older activities or third-party apps that
didn't push HR back to Strava). This module reads the Polar sessions and fills
in HR on Strava activities by matching start-time within a small tolerance.

Both data sources are normalised to UTC:
- Polar ``startTime`` is naive local time; we subtract ``timezoneOffsetMinutes``
  to get UTC.
- Strava bulk-export ``Activity Date`` is already in UTC (regardless of which
  timezone the athlete was physically in).

The default match window is ±5 minutes — clock skew between Polar and Strava
is typically under 1 minute, but the tolerance covers manual edits and
slow-syncing third-party apps.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path


@dataclass(frozen=True)
class PolarSession:
    """A single Polar training session, normalised to UTC."""

    start_utc: datetime
    duration_min: float
    distance_km: float
    hr_avg: int | None
    hr_max: int | None
    sport_id: str
    source_file: str


def parse_polar_session(payload: dict, source_file: str = "") -> PolarSession | None:
    """Parse one ``training-session-*.json`` payload to a normalised record.

    Returns ``None`` if the file lacks the required fields — Polar sometimes
    emits stub sessions (manual entries, abandoned recordings) without HR.
    """
    start_raw = payload.get("startTime")
    if not start_raw:
        return None
    try:
        start_local = datetime.fromisoformat(start_raw)
    except ValueError:
        return None
    if start_local.tzinfo is not None:
        start_utc = start_local.astimezone(UTC).replace(tzinfo=None)
    else:
        offset_min = int(payload.get("timezoneOffsetMinutes") or 0)
        start_utc = start_local - timedelta(minutes=offset_min)

    duration_ms = payload.get("durationMillis")
    duration_min = float(duration_ms) / 60_000.0 if duration_ms else 0.0
    distance_m = payload.get("distanceMeters") or 0
    sport_id = str((payload.get("sport") or {}).get("id") or "")

    return PolarSession(
        start_utc=start_utc,
        duration_min=round(duration_min, 2),
        distance_km=round(float(distance_m) / 1000.0, 3),
        hr_avg=_opt_int(payload.get("hrAvg")),
        hr_max=_opt_int(payload.get("hrMax")),
        sport_id=sport_id,
        source_file=source_file,
    )


def load_polar_sessions(polar_dir: Path) -> list[PolarSession]:
    """Read every ``training-session-*.json`` in *polar_dir*.

    Files that fail to parse are skipped silently — Polar exports occasionally
    contain truncated JSON (sync-interrupted writes). Skipping is more useful
    than aborting the whole enrichment for a single bad file.
    """
    sessions: list[PolarSession] = []
    for path in sorted(polar_dir.glob("training-session-*.json")):
        try:
            with path.open(encoding="utf-8") as f:
                payload = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        record = parse_polar_session(payload, source_file=path.name)
        if record is not None:
            sessions.append(record)
    return sessions


def _parse_strava_activity_date(value: str) -> datetime | None:
    """Parse Strava bulk-export ``Activity Date`` as a naive UTC datetime."""
    if not value:
        return None
    s = value.strip().strip('"')
    for fmt in (
        "%b %d, %Y, %I:%M:%S %p",   # "Apr 19, 2026, 9:01:49 AM"
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


@dataclass(frozen=True)
class MergeStats:
    total_rows: int
    runs: int
    matched: int
    already_had_hr: int
    no_match: int


def enrich_strava_csv(
    strava_csv: Path,
    polar_dir: Path,
    output_csv: Path,
    *,
    tolerance_min: int = 5,
    overwrite_existing_hr: bool = False,
    runs_only: bool = True,
) -> MergeStats:
    """Read *strava_csv*, fill HR from Polar sessions in *polar_dir*, write *output_csv*.

    Strava's export has both ``Average Heart Rate`` and ``Max Heart Rate`` columns.
    Both are filled when a match is found.

    Pandas isn't used here — Strava bulk exports duplicate column names (two
    ``Elapsed Time`` columns, two ``Distance`` columns, etc.) which pandas
    silently disambiguates by appending ``.1``. The csv module preserves the
    raw structure, so the output round-trips byte-for-byte except for the HR
    fields we touched.
    """
    sessions = load_polar_sessions(polar_dir)
    sessions_sorted = sorted(sessions, key=lambda s: s.start_utc)

    tol = timedelta(minutes=tolerance_min)
    runs = matched = already_had_hr = no_match = 0
    total_rows = 0

    # `utf-8-sig` strips a BOM if present and is a no-op otherwise — handy
    # because Strava ships without BOM but Excel-edited copies have one.
    with strava_csv.open(encoding="utf-8-sig", newline="") as f_in:
        reader = csv.reader(f_in)
        header = next(reader)
        idx = _build_strava_index(header)
        rows = list(reader)

    for row in rows:
        total_rows += 1
        if runs_only and (idx.activity_type is None or row[idx.activity_type] != "Run"):
            continue
        runs += 1

        existing_avg = row[idx.avg_hr] if idx.avg_hr is not None else ""
        if existing_avg and not overwrite_existing_hr:
            already_had_hr += 1
            continue

        start = _parse_strava_activity_date(row[idx.activity_date])
        if start is None:
            no_match += 1
            continue

        match = _find_closest_session(sessions_sorted, start, tol)
        if match is None:
            no_match += 1
            continue

        if match.hr_avg is not None and idx.avg_hr is not None:
            row[idx.avg_hr] = str(match.hr_avg)
        if match.hr_max is not None and idx.max_hr is not None:
            row[idx.max_hr] = str(match.hr_max)
        matched += 1

    # Write with UTF-8 BOM so Excel autodetects the encoding — without it,
    # Excel falls back to CP1252 on Windows and turns `ä` into `Ã¤`. Python,
    # pandas, and the jooksuai CSV loader strip the BOM transparently.
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", encoding="utf-8-sig", newline="") as f_out:
        writer = csv.writer(f_out)
        writer.writerow(header)
        writer.writerows(rows)

    return MergeStats(
        total_rows=total_rows,
        runs=runs,
        matched=matched,
        already_had_hr=already_had_hr,
        no_match=no_match,
    )


@dataclass
class _StravaColumnIndex:
    activity_date: int
    activity_type: int | None
    avg_hr: int | None
    max_hr: int | None


def _build_strava_index(header: list[str]) -> _StravaColumnIndex:
    """Locate Strava's HR columns. The bulk export duplicates ``Max Heart Rate``;
    the second occurrence is the one in the per-activity stats block (paired
    with ``Average Heart Rate``), so we take the LAST occurrence of each name.
    """
    last: dict[str, int] = {}
    for i, name in enumerate(header):
        last[name] = i
    if "Activity Date" not in last:
        raise ValueError("Strava CSV missing 'Activity Date' column")
    return _StravaColumnIndex(
        activity_date=last["Activity Date"],
        activity_type=last.get("Activity Type"),
        avg_hr=last.get("Average Heart Rate"),
        max_hr=last.get("Max Heart Rate"),
    )


def _find_closest_session(
    sessions_sorted: list[PolarSession],
    target: datetime,
    tolerance: timedelta,
) -> PolarSession | None:
    """Linear scan — sessions list is small (a few thousand) and we iterate
    once per Strava row, so this is fast enough without bisect."""
    best: PolarSession | None = None
    best_delta: timedelta | None = None
    for s in sessions_sorted:
        delta = abs(s.start_utc - target)
        if delta > tolerance:
            continue
        if best_delta is None or delta < best_delta:
            best, best_delta = s, delta
    return best


def _opt_int(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "MergeStats",
    "PolarSession",
    "enrich_strava_csv",
    "load_polar_sessions",
    "parse_polar_session",
]
