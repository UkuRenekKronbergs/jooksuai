"""Tests for CSV loader (native + Strava export formats)."""

from __future__ import annotations

from datetime import date
from io import StringIO

from jooksuai.data.csv_loader import load_activities_csv

NATIVE_CSV = """\
id,activity_date,activity_type,distance_km,duration_min,avg_hr,rpe,notes
a1,2026-04-18,Run,10.5,48.5,148,5,Easy
a2,2026-04-19,Run,14.0,65.0,160,7,Tempo
"""


STRAVA_CSV = """\
Activity ID,Activity Date,Activity Type,Distance,Elapsed Time,Average Heart Rate,Max Heart Rate,Elevation Gain,Activity Name
12345,"Apr 18, 2026, 06:00:00 AM",Run,10500,2910,148,172,55,Morning Run
12346,"Apr 19, 2026, 06:00:00 AM",Run,14000,3900,160,185,110,Tempo
"""


def test_native_csv_parses():
    activities = load_activities_csv(StringIO(NATIVE_CSV))
    assert len(activities) == 2
    assert activities[0].id == "a1"
    assert activities[0].activity_date == date(2026, 4, 18)
    assert activities[0].distance_km == 10.5
    assert activities[0].rpe == 5


def test_strava_csv_converts_meters_and_seconds():
    activities = load_activities_csv(StringIO(STRAVA_CSV))
    assert len(activities) == 2
    # 10500m → 10.5km
    assert activities[0].distance_km == 10.5
    # 2910s → 48.5 min
    assert activities[0].duration_min == 48.5
    assert activities[0].avg_hr == 148
    assert activities[0].notes == "Morning Run"
    # Pace derived from distance/duration when Average Speed missing: 48.5/10.5 ≈ 4.619
    assert activities[0].avg_pace_min_per_km == 4.619


STRAVA_CSV_WITH_SPEED = """\
Activity ID,Activity Date,Activity Type,Distance,Moving Time,Average Speed,Elevation Gain,Activity Name
99,"Apr 21, 2026, 07:00:00 AM",Run,10000,2400,4.1667,30,Speed test
"""


def test_strava_csv_derives_pace_from_average_speed():
    activities = load_activities_csv(StringIO(STRAVA_CSV_WITH_SPEED))
    assert len(activities) == 1
    # 4.1667 m/s → (1000/4.1667)/60 = 4.0 min/km
    assert activities[0].avg_pace_min_per_km == 4.0
    # Moving Time preferred over Elapsed Time: 2400 / 60 = 40 min
    assert activities[0].duration_min == 40.0


# Strava bulk export duplicates "Max Heart Rate". The first column (in the
# summary block) is usually empty; the populated one is in the per-activity
# stats block, which pandas renames to "Max Heart Rate.1".
STRAVA_CSV_DUPLICATE_MAX_HR = """\
Activity ID,Activity Date,Activity Type,Distance,Elapsed Time,Max Heart Rate,Average Cadence,Max Heart Rate,Average Heart Rate
55,"Apr 19, 2026, 9:01:49 AM",Run,10000,2400,,77,178,131
"""


def test_strava_csv_picks_max_hr_from_second_duplicate_column():
    activities = load_activities_csv(StringIO(STRAVA_CSV_DUPLICATE_MAX_HR))
    assert len(activities) == 1
    a = activities[0]
    assert a.avg_hr == 131
    assert a.max_hr_observed == 178  # from the second "Max Heart Rate" column
