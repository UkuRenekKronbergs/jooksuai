"""Fill missing HR on a Strava bulk-export CSV from a Polar Flow export.

Usage:
    python scripts/enrich_strava_with_polar.py \
        --strava-csv  ~/Downloads/strava_andmed/activities.csv \
        --polar-dir   ~/Downloads/polar_andmed \
        --output      ~/Downloads/strava_andmed/activities_with_polar_hr.csv

By default we skip activities that already have HR. Pass ``--overwrite`` to
replace existing values (useful if you trust Polar more than what Strava
captured from a third-party app).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from jooksuai.data.polar import enrich_strava_csv


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else "")
    parser.add_argument("--strava-csv", type=Path, required=True, help="Path to Strava activities.csv")
    parser.add_argument("--polar-dir", type=Path, required=True, help="Directory of Polar JSON exports")
    parser.add_argument("--output", type=Path, required=True, help="Where to write the enriched CSV")
    parser.add_argument("--tolerance-min", type=int, default=5, help="Match window in minutes (default 5)")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing HR values")
    parser.add_argument("--all-types", action="store_true", help="Process all activity types, not just Run")
    args = parser.parse_args()

    stats = enrich_strava_csv(
        strava_csv=args.strava_csv,
        polar_dir=args.polar_dir,
        output_csv=args.output,
        tolerance_min=args.tolerance_min,
        overwrite_existing_hr=args.overwrite,
        runs_only=not args.all_types,
    )

    print(f"Strava rows scanned:        {stats.total_rows}")
    print(f"Of those, runs considered:  {stats.runs}")
    print(f"  matched & HR filled:      {stats.matched}")
    print(f"  already had HR (skipped): {stats.already_had_hr}")
    print(f"  no Polar match:           {stats.no_match}")
    print(f"Output written to:          {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
