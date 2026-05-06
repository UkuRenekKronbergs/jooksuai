"""Tests for Polar → Strava HR enrichment."""

from __future__ import annotations

import csv
import json
from datetime import datetime

from jooksuai.data.polar import (
    enrich_strava_csv,
    load_polar_sessions,
    parse_polar_session,
)


def _write_polar_session(path, *, start_local, tz_offset_min, hr_avg, hr_max, sport_id="1"):
    payload = {
        "identifier": {"id": path.stem},
        "startTime": start_local,
        "stopTime": start_local,
        "durationMillis": 3_600_000,
        "distanceMeters": 10000,
        "hrAvg": hr_avg,
        "hrMax": hr_max,
        "timezoneOffsetMinutes": tz_offset_min,
        "sport": {"id": sport_id},
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_parse_polar_session_normalises_to_utc():
    payload = {
        "startTime": "2026-04-19T11:01:48",
        "durationMillis": 6_300_000,
        "distanceMeters": 13748,
        "hrAvg": 131,
        "hrMax": 179,
        "timezoneOffsetMinutes": 120,
        "sport": {"id": "1"},
    }
    s = parse_polar_session(payload)
    assert s is not None
    # Local 11:01:48 with offset +120 → UTC 09:01:48
    assert s.start_utc == datetime(2026, 4, 19, 9, 1, 48)
    assert s.hr_avg == 131
    assert s.hr_max == 179
    assert s.distance_km == 13.748


def test_parse_polar_session_handles_missing_fields():
    assert parse_polar_session({}) is None
    # No HR is fine — we still keep the session, callers decide what to do.
    s = parse_polar_session({"startTime": "2026-04-19T11:01:48", "timezoneOffsetMinutes": 120})
    assert s is not None
    assert s.hr_avg is None
    assert s.duration_min == 0.0


def test_load_polar_sessions_skips_corrupt_files(tmp_path):
    good = tmp_path / "training-session-good.json"
    _write_polar_session(good, start_local="2026-04-19T11:01:48", tz_offset_min=120, hr_avg=140, hr_max=180)
    bad = tmp_path / "training-session-bad.json"
    bad.write_text("not json {{{", encoding="utf-8")

    sessions = load_polar_sessions(tmp_path)
    assert len(sessions) == 1
    assert sessions[0].hr_avg == 140


_STRAVA_CSV = """\
Activity ID,Activity Date,Activity Name,Activity Type,Elapsed Time,Distance,Max Heart Rate,Average Heart Rate
1001,"Apr 19, 2026, 9:01:49 AM",Lunch Run,Run,3600,10.0,,
1002,"Apr 18, 2026, 7:00:00 AM",Morning Run,Run,2400,8.0,170,150
1003,"Apr 17, 2026, 6:30:00 AM",Bike,Ride,5400,30.0,,
"""


def test_enrich_strava_csv_fills_only_missing_hr(tmp_path):
    strava = tmp_path / "activities.csv"
    strava.write_text(_STRAVA_CSV, encoding="utf-8")
    polar_dir = tmp_path / "polar"
    polar_dir.mkdir()
    # Polar session matching the first Strava run (within tolerance).
    _write_polar_session(
        polar_dir / "training-session-1.json",
        start_local="2026-04-19T11:01:50", tz_offset_min=120, hr_avg=141, hr_max=178,
    )
    # Polar session matching the second Strava run — but Strava already has HR,
    # so we should NOT overwrite by default.
    _write_polar_session(
        polar_dir / "training-session-2.json",
        start_local="2026-04-18T09:00:01", tz_offset_min=120, hr_avg=999, hr_max=999,
    )

    out = tmp_path / "out.csv"
    stats = enrich_strava_csv(strava, polar_dir, out)

    assert stats.runs == 2  # two Run rows
    assert stats.matched == 1  # only the empty-HR one got filled
    assert stats.already_had_hr == 1
    assert stats.no_match == 0

    rows = list(csv.DictReader(out.open(encoding="utf-8")))
    assert rows[0]["Average Heart Rate"] == "141"
    assert rows[0]["Max Heart Rate"] == "178"
    # Already-set row preserved
    assert rows[1]["Average Heart Rate"] == "150"


def test_enrich_strava_csv_overwrite_replaces_hr(tmp_path):
    strava = tmp_path / "activities.csv"
    strava.write_text(_STRAVA_CSV, encoding="utf-8")
    polar_dir = tmp_path / "polar"
    polar_dir.mkdir()
    _write_polar_session(
        polar_dir / "training-session-2.json",
        start_local="2026-04-18T09:00:01", tz_offset_min=120, hr_avg=148, hr_max=170,
    )
    out = tmp_path / "out.csv"
    stats = enrich_strava_csv(strava, polar_dir, out, overwrite_existing_hr=True)
    assert stats.matched == 1
    rows = list(csv.DictReader(out.open(encoding="utf-8")))
    assert rows[1]["Average Heart Rate"] == "148"


def test_enrich_strava_csv_writes_utf8_bom(tmp_path):
    """Excel on Windows needs the BOM to autodetect UTF-8 — without it, ä → Ã¤."""
    strava = tmp_path / "activities.csv"
    strava.write_text(_STRAVA_CSV, encoding="utf-8")
    polar_dir = tmp_path / "polar"
    polar_dir.mkdir()
    out = tmp_path / "out.csv"
    enrich_strava_csv(strava, polar_dir, out)
    assert out.read_bytes()[:3] == b"\xef\xbb\xbf"


def test_enrich_strava_csv_roundtrips_bom(tmp_path):
    """Re-running enrichment on its own output must not double the BOM."""
    strava = tmp_path / "activities.csv"
    strava.write_text(_STRAVA_CSV, encoding="utf-8-sig")  # input already has BOM
    polar_dir = tmp_path / "polar"
    polar_dir.mkdir()
    out = tmp_path / "out.csv"
    stats = enrich_strava_csv(strava, polar_dir, out)
    raw = out.read_bytes()
    assert raw[:3] == b"\xef\xbb\xbf"
    assert raw[3:6] != b"\xef\xbb\xbf"  # no second BOM
    assert stats.runs == 2  # column header was parsed despite BOM


def test_enrich_strava_csv_outside_tolerance_no_match(tmp_path):
    strava = tmp_path / "activities.csv"
    strava.write_text(_STRAVA_CSV, encoding="utf-8")
    polar_dir = tmp_path / "polar"
    polar_dir.mkdir()
    # 30 min away from the first Run — outside default tolerance.
    _write_polar_session(
        polar_dir / "training-session-1.json",
        start_local="2026-04-19T11:31:00", tz_offset_min=120, hr_avg=141, hr_max=178,
    )
    out = tmp_path / "out.csv"
    stats = enrich_strava_csv(strava, polar_dir, out, tolerance_min=5)
    assert stats.matched == 0
    assert stats.no_match == 1  # the empty-HR run found no match
