"""Garmin Connect GPX-export fallback parser.

Project Plan §5 Risk 2 Plan B: "Kui Garmin murdub, on fallback Garmin Connect
eksportfail (GPX/FIT) — loen need kohalikust kaustast eraldi parseriga."

We parse the GPX format (XML) since it's open and stable; FIT is a binary
format that needs the `fitparse` C-extension and ships less reliably on
Windows. Garmin Connect lets the athlete export individual activities as
GPX from the activity detail page → ⚙ → Export GPX.

GPX track structure we rely on:

    <gpx>
      <metadata><time>2026-05-15T05:30:00Z</time></metadata>
      <trk>
        <name>Easy morning run</name>
        <type>running</type>
        <trkseg>
          <trkpt lat="..." lon="...">
            <ele>...</ele>
            <time>...</time>
            <extensions>
              <ns3:TrackPointExtension>
                <ns3:hr>148</ns3:hr>
              </ns3:TrackPointExtension>
            </extensions>
          </trkpt>
          ...

Privacy: per the plan, we keep GPS points out of the LLM context. The parser
*does* read lat/lon to compute distance via the haversine formula, but the
returned `TrainingActivity` exposes only aggregated fields (no point list).
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from pathlib import Path

from .models import TrainingActivity

_GPX_NS = {
    "gpx": "http://www.topografix.com/GPX/1/1",
    "tpx": "http://www.garmin.com/xmlschemas/TrackPointExtension/v1",
    "tpx2": "http://www.garmin.com/xmlschemas/TrackPointExtension/v2",
}

# Garmin's GPX `<type>` field uses lowercase singular words. We accept anything
# starting with "run" so trail_run / virtual_run / treadmill_running also pass.
_RUN_TYPES = ("run", "running", "trail_run", "trail_running",
              "virtual_run", "treadmill_running")


def parse_gpx(path: str | Path) -> TrainingActivity | None:
    """Parse a single Garmin GPX file into a TrainingActivity.

    Returns None if the file isn't a run (skipping bike/swim exports) or if
    no track points are present. Raises on malformed XML — callers loading a
    folder should catch and skip.
    """
    tree = ET.parse(str(path))
    root = tree.getroot()
    trk = root.find("gpx:trk", _GPX_NS)
    if trk is None:
        return None

    type_el = trk.find("gpx:type", _GPX_NS)
    activity_type = (type_el.text or "").strip().lower() if type_el is not None else "run"
    if activity_type and not any(activity_type.startswith(t) for t in _RUN_TYPES):
        return None

    name_el = trk.find("gpx:name", _GPX_NS)
    name = name_el.text.strip() if (name_el is not None and name_el.text) else None

    points = []
    for trkseg in trk.findall("gpx:trkseg", _GPX_NS):
        for trkpt in trkseg.findall("gpx:trkpt", _GPX_NS):
            lat = float(trkpt.attrib["lat"])
            lon = float(trkpt.attrib["lon"])
            time_el = trkpt.find("gpx:time", _GPX_NS)
            ele_el = trkpt.find("gpx:ele", _GPX_NS)
            hr = _extract_hr(trkpt)
            time_dt = _parse_iso(time_el.text) if (time_el is not None and time_el.text) else None
            ele = float(ele_el.text) if (ele_el is not None and ele_el.text) else None
            points.append((time_dt, lat, lon, ele, hr))

    if not points:
        return None

    return _summarize(Path(path).stem, points, name=name)


def parse_gpx_folder(folder: str | Path) -> list[TrainingActivity]:
    """Parse every `.gpx` file in a folder. Skips files that aren't runs or fail
    to parse — the goal is "load everything that works", not strict validation."""
    folder = Path(folder)
    activities: list[TrainingActivity] = []
    for path in sorted(folder.glob("*.gpx")):
        try:
            activity = parse_gpx(path)
        except ET.ParseError:
            continue
        if activity:
            activities.append(activity)
    return activities


def _extract_hr(trkpt: ET.Element) -> int | None:
    """Pull the heart-rate from the Garmin TrackPointExtension namespace.

    Garmin uses two versions (v1, v2). We search both. If neither is present
    the point is HR-less — common for runs done without a chest strap.
    """
    ext = trkpt.find("gpx:extensions", _GPX_NS)
    if ext is None:
        return None
    for ns in ("tpx", "tpx2"):
        for elem in ext.iter():
            tag = elem.tag.split("}", 1)[-1]
            if tag == "hr" and elem.text:
                try:
                    return int(float(elem.text))
                except ValueError:
                    continue
    return None


def _parse_iso(text: str) -> datetime:
    """Garmin GPX times are ISO-8601 UTC with a `Z` suffix."""
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _summarize(
    activity_id: str,
    points: list[tuple[datetime | None, float, float, float | None, int | None]],
    name: str | None,
) -> TrainingActivity:
    """Aggregate a track-point list into the same TrainingActivity our SQLite
    store expects. Distance via haversine, duration via first/last timestamp."""
    times = [p[0] for p in points if p[0] is not None]
    start = min(times) if times else None
    end = max(times) if times else None
    duration_s = (end - start).total_seconds() if (start and end) else 0.0

    total_m = 0.0
    elev_gain = 0.0
    prev_lat = prev_lon = prev_ele = None
    for _t, lat, lon, ele, _hr in points:
        if prev_lat is not None and prev_lon is not None:
            total_m += _haversine_m(prev_lat, prev_lon, lat, lon)
        if prev_ele is not None and ele is not None and ele > prev_ele:
            elev_gain += ele - prev_ele
        prev_lat, prev_lon, prev_ele = lat, lon, ele

    hrs = [p[4] for p in points if p[4] is not None]
    avg_hr = int(sum(hrs) / len(hrs)) if hrs else None
    max_hr = max(hrs) if hrs else None

    distance_km = total_m / 1000.0
    duration_min = duration_s / 60.0
    avg_pace = round(duration_min / distance_km, 2) if distance_km > 0 else None
    activity_date: date = (start or datetime.now()).date()

    return TrainingActivity(
        id=activity_id,
        activity_date=activity_date,
        activity_type="Run",
        distance_km=round(distance_km, 2),
        duration_min=round(duration_min, 1),
        avg_hr=avg_hr,
        max_hr_observed=max_hr,
        avg_pace_min_per_km=avg_pace,
        elevation_gain_m=round(elev_gain, 1) if elev_gain else None,
        notes=name,
    )


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two WGS84 points, in metres.

    Standard haversine; accuracy is fine at running scales (<50 km segments).
    """
    R = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def load_garmin_folder(folder: str | Path) -> Iterable[TrainingActivity]:
    """Public name used by the UI / CLI. Alias for `parse_gpx_folder`."""
    return parse_gpx_folder(folder)
