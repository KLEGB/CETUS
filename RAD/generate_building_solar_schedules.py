"""CLI helper for generating per-building solar schedules."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from RAD.workflow import run_full_workflow


def parse_args() -> argparse.Namespace:
    """Parse command line arguments for the RAD schedule workflow."""
    parser = argparse.ArgumentParser(description="Generate per-building schedule files through RAD workflow.")
    parser.add_argument("--shp", required=True, help="Input building shapefile path.")
    parser.add_argument("--epw", required=True, help="Input EPW weather path.")
    parser.add_argument("--date", default="08-20", help="Target date in mm-dd.")
    parser.add_argument("--height-field", default="height", help="Height column name.")
    parser.add_argument("--id-field", default="index", help="Building id column name.")
    parser.add_argument("--floor-height", type=float, default=4.0, help="Typical single-floor height in meters.")
    parser.add_argument("--wwr", type=float, default=0.5, help="Window-to-wall ratio used for gain area.")
    parser.add_argument("--shgc", type=float, default=0.6, help="Solar heat gain coefficient.")
    return parser.parse_args()


def main() -> None:
    """Run the complete RAD workflow from command line arguments."""
    args = parse_args()
    if args.floor_height <= 0:
        raise ValueError("--floor-height must be positive.")
    if args.wwr < 0 or args.wwr > 1:
        raise ValueError("--wwr must be between 0 and 1.")
    if args.shgc < 0 or args.shgc > 1:
        raise ValueError("--shgc must be between 0 and 1.")

    weather_path, rad_path, schedule_dir = run_full_workflow(
        args.epw,
        args.shp,
        date=args.date,
        wwr=args.wwr,
        shgc=args.shgc,
        id_field=args.id_field,
        height_field=args.height_field,
        floor_height=args.floor_height,
    )
    print(weather_path)
    print(rad_path)
    print(schedule_dir)


if __name__ == "__main__":
    main()
