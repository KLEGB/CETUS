"""Write Moosas-compatible solar gain schedules from hourly radiation gains."""

from __future__ import annotations

import re
from pathlib import Path


def sanitize_token(value: str) -> str:
    """Convert a building id into a filesystem-safe token."""
    return re.sub(r"[^A-Za-z0-9]+", "_", str(value)).strip("_")


def schedule_names(building_id: str) -> dict[str, str]:
    """Return all schedule names used for one building id."""
    token = sanitize_token(building_id)
    return {
        "summer_daily": f"RAD_{token}_SUMMER_DAILY",
        "summer_weekly": f"RAD_{token}_SUMMER_WEEKLY",
        "summer_hourly": f"RAD_{token}_SUMMER_HOURLY",
        "winter_daily": f"RAD_{token}_WINTER_DAILY",
        "winter_weekly": f"RAD_{token}_WINTER_WEEKLY",
        "winter_hourly": f"RAD_{token}_WINTER_HOURLY",
    }


def build_schedule_text(building_id: str, hourly_wh: list[float]) -> str:
    """Build Daily or Hourly solar schedule text for one building."""
    if len(hourly_wh) not in (24, 8760):
        raise ValueError("Schedule must contain exactly 24 or 8760 values.")
    names = schedule_names(building_id)
    header = [
        "! RAD building solar schedule",
        f"! building_id={building_id}",
        "! values unit: Wh",
    ]
    if len(hourly_wh) == 8760:
        values = ",".join(f"{value:.6f}" for value in hourly_wh)
        lines = header + [
            f"{names['summer_hourly']},Hourly,{values}",
            f"{names['winter_hourly']},Hourly,{values}",
        ]
    else:
        values = ",".join(f"{value:.6f}" for value in hourly_wh)
        lines = header + [
            f"{names['summer_daily']},Daily,{values}",
            (
                f"{names['summer_weekly']},Weekly,{names['summer_daily']},{names['summer_daily']},"
                f"{names['summer_daily']},{names['summer_daily']},{names['summer_daily']},"
                f"{names['summer_daily']},{names['summer_daily']}"
            ),
            f"{names['winter_daily']},Daily,{values}",
            (
                f"{names['winter_weekly']},Weekly,{names['winter_daily']},{names['winter_daily']},"
                f"{names['winter_daily']},{names['winter_daily']},{names['winter_daily']},"
                f"{names['winter_daily']},{names['winter_daily']}"
            ),
        ]
    return "\n".join(lines) + "\n"


def write_schedule_file(output_dir: Path, building_id: str, hourly_wh: list[float]) -> Path:
    """Write one building schedule file and return its path."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{sanitize_token(building_id)}.sch"
    path.write_text(build_schedule_text(building_id, hourly_wh), encoding="utf-8")
    return path


sanitizeToken = sanitize_token
scheduleNames = schedule_names
buildScheduleText = build_schedule_text
writeScheduleFile = write_schedule_file


if __name__ == "__main__":
    print(buildScheduleText("test", [0.0] * 24).splitlines()[0])
