"""Tests for vorm.data.garmin — GPX parser for the Strava-down fallback path."""

from __future__ import annotations

from datetime import date

import pytest

from vorm.data.garmin import parse_gpx, parse_gpx_folder


def _write_gpx(path, *, name="Easy run", activity_type="running", points=None):
    """Build a minimal GPX file matching Garmin Connect's export format."""
    points = points or [
        # (time_iso, lat, lon, ele, hr)
        ("2026-05-15T05:30:00Z", 58.3776, 26.7290, 50.0, 140),
        ("2026-05-15T05:35:00Z", 58.3800, 26.7350, 52.0, 145),
        ("2026-05-15T05:40:00Z", 58.3850, 26.7400, 55.0, 150),
    ]
    pts_xml = "\n".join(
        f'''<trkpt lat="{lat}" lon="{lon}">
              <ele>{ele}</ele>
              <time>{t}</time>
              <extensions>
                <ns3:TrackPointExtension xmlns:ns3="http://www.garmin.com/xmlschemas/TrackPointExtension/v1">
                  <ns3:hr>{hr}</ns3:hr>
                </ns3:TrackPointExtension>
              </extensions>
            </trkpt>'''
        for (t, lat, lon, ele, hr) in points
    )
    gpx = f'''<?xml version="1.0"?>
<gpx xmlns="http://www.topografix.com/GPX/1/1" version="1.1">
  <metadata><time>2026-05-15T05:30:00Z</time></metadata>
  <trk>
    <name>{name}</name>
    <type>{activity_type}</type>
    <trkseg>
{pts_xml}
    </trkseg>
  </trk>
</gpx>
'''
    path.write_text(gpx, encoding="utf-8")


def test_parse_gpx_aggregates_distance_duration_hr(tmp_path):
    gpx = tmp_path / "run1.gpx"
    _write_gpx(gpx)
    activity = parse_gpx(gpx)

    assert activity is not None
    assert activity.id == "run1"
    assert activity.activity_date == date(2026, 5, 15)
    assert activity.activity_type == "Run"
    # 3 points × ~600 m spread → roughly 1 km total. Allow loose bound; exact
    # value depends on haversine and the synthetic coords.
    assert 0.4 < activity.distance_km < 2.0
    # First to last timestamp = 10 min
    assert activity.duration_min == pytest.approx(10.0, abs=0.1)
    assert activity.avg_hr == 145  # (140 + 145 + 150) / 3
    assert activity.max_hr_observed == 150
    assert activity.notes == "Easy run"


def test_parse_gpx_skips_non_run(tmp_path):
    gpx = tmp_path / "bike.gpx"
    _write_gpx(gpx, activity_type="biking")
    assert parse_gpx(gpx) is None


def test_parse_gpx_accepts_trail_run(tmp_path):
    gpx = tmp_path / "trail.gpx"
    _write_gpx(gpx, activity_type="trail_running")
    activity = parse_gpx(gpx)
    assert activity is not None


def test_parse_gpx_handles_missing_hr(tmp_path):
    """A run logged without a chest strap → activity returned but HR=None."""
    gpx = tmp_path / "no_hr.gpx"
    gpx.write_text("""<?xml version="1.0"?>
<gpx xmlns="http://www.topografix.com/GPX/1/1" version="1.1">
  <trk><type>running</type>
    <trkseg>
      <trkpt lat="58.3776" lon="26.7290"><time>2026-05-15T05:30:00Z</time></trkpt>
      <trkpt lat="58.3800" lon="26.7350"><time>2026-05-15T05:35:00Z</time></trkpt>
    </trkseg>
  </trk>
</gpx>
""", encoding="utf-8")
    activity = parse_gpx(gpx)
    assert activity is not None
    assert activity.avg_hr is None
    assert activity.max_hr_observed is None


def test_parse_gpx_folder_collects_only_runs(tmp_path):
    _write_gpx(tmp_path / "run1.gpx")
    _write_gpx(tmp_path / "run2.gpx", name="Tempo")
    _write_gpx(tmp_path / "bike.gpx", activity_type="biking")
    (tmp_path / "broken.gpx").write_text("<not-xml>", encoding="utf-8")
    (tmp_path / "ignore.txt").write_text("not a gpx", encoding="utf-8")

    runs = parse_gpx_folder(tmp_path)
    assert {r.id for r in runs} == {"run1", "run2"}


def test_parse_gpx_returns_none_for_no_track(tmp_path):
    gpx = tmp_path / "empty.gpx"
    gpx.write_text("""<?xml version="1.0"?>
<gpx xmlns="http://www.topografix.com/GPX/1/1" version="1.1"></gpx>
""", encoding="utf-8")
    assert parse_gpx(gpx) is None
