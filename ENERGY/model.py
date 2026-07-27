"""ENERGY building model objects and single-building simulation helpers."""

from __future__ import annotations

import calendar
import csv
import json
import math
import sys
import tempfile
from copy import deepcopy
from dataclasses import dataclass, field, fields
from pathlib import Path
from random import Random
from typing import Any, ClassVar, Iterable

import numpy as np

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from .engine import parse_output_sections, run_energy_simulation
except ImportError:
    from engine import parse_output_sections, run_energy_simulation
from WEA import EpwLocation


MODULE_DIR = Path(__file__).resolve().parent
ARCHETYPE_MEAN_PATH = MODULE_DIR / "data" / "archetype" / "mean.csv"
ARCHETYPE_STD_PATH = MODULE_DIR / "data" / "archetype" / "std.csv"
SCHEDULE_DIR = MODULE_DIR / "data" / "schedule"

ARCHETYPE_TYPES = {"commercial", "hotel", "office", "residential", "school", "medical"}
TEMPLATE_TYPE_MAP = {
    "commercial": "COMMERCIAL",
    "hotel": "HOTEL",
    "office": "OFFICE",
    "residential": "RESIDENTIAL",
    "school": "SCHOOL",
    "medical": "HOSPITAL",
}
ARCHETYPE_BUILDING_TYPE = {
    "residential": 0,
    "office": 1,
    "hotel": 2,
    "school": 3,
    "commercial": 4,
    "medical": 6,
}
MONTH_HOURS = [calendar.monthrange(2025, month)[1] * 24 for month in range(1, 13)]
MONTH_START_HOURS = np.cumsum([0] + MONTH_HOURS)


def _extract_zone_hourly_energy(sections: dict[str, list[str]]) -> dict[int, np.ndarray]:
    """Read MoosasEnergy hourly zone rows into zone-indexed arrays."""
    hour_lines = [line for line in sections.get("!ZONE HOUR RESULT:", []) if line and not line.startswith("!")]
    if not hour_lines:
        raise ValueError("Missing !ZONE HOUR RESULT: section in energy output.")

    zone_map: dict[int, list[list[float]]] = {}
    for line in hour_lines:
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 4:
            raise ValueError(f"Invalid !ZONE HOUR RESULT row: {line}")
        zone_index = int(parts[0])
        zone_map.setdefault(zone_index, []).append([float(parts[1]), float(parts[2]), float(parts[3])])

    return {zone_index: np.asarray(values, dtype=np.float32) for zone_index, values in zone_map.items()}


def _building_hourly_load_wm2(
    roof_intensity: np.ndarray,
    middle_intensity: np.ndarray,
    ground_intensity: np.ndarray,
    effective_story_count: float,
) -> np.ndarray:
    """Convert roof/middle/ground layer intensities to representative-floor W/m2."""
    if effective_story_count <= 1.0:
        hourly_intensity = (roof_intensity + ground_intensity) / 2.0
    else:
        hourly_intensity = (
            roof_intensity
            + middle_intensity * max(effective_story_count - 2.0, 0.0)
            + ground_intensity
        ) / effective_story_count
    return hourly_intensity * 1000.0


def _resolve_weather_hoys(weather_path: Path) -> np.ndarray:
    """Read HOY values from a classic Moosas weather CSV file."""
    hoy_values: list[int] = []
    with weather_path.open("r", encoding="utf-8", errors="ignore", newline="") as handle:
        reader = csv.reader(handle)
        for row in reader:
            if len(row) < 3:
                continue
            try:
                hoy_values.append(int(float(row[2])))
            except ValueError:
                continue
    return np.asarray(hoy_values, dtype=np.int32)


def _complete_day_indices(hoys: np.ndarray) -> list[int]:
    """Return day indices whose 24 HOYs are all present in the weather file."""
    if hoys.size == 0:
        return []
    complete_days: list[int] = []
    for day_index in sorted({int(hoy // 24) for hoy in hoys.tolist()}):
        expected = np.arange(day_index * 24, day_index * 24 + 24, dtype=np.int32)
        if np.array_equal(hoys[(hoys >= expected[0]) & (hoys <= expected[-1])], expected):
            complete_days.append(day_index)
    return complete_days


def _complete_month_indices(hoys: np.ndarray) -> list[int]:
    """Return month indices whose full hourly range is present in the weather file."""
    if hoys.size == 0:
        return []
    complete_months: list[int] = []
    for month_index in range(12):
        start = MONTH_START_HOURS[month_index]
        end = MONTH_START_HOURS[month_index + 1]
        expected = np.arange(start, end, dtype=np.int32)
        actual = hoys[(hoys >= start) & (hoys < end)]
        if actual.shape[0] == expected.shape[0] and np.array_equal(actual, expected):
            complete_months.append(month_index)
    return complete_months


@dataclass
class ScheduleEntry:
    """Store one parsed Moosas schedule and its expanded hourly values."""

    name: str
    schedule_type: str
    values: list[float] | None = None
    weekly_refs: list[str] | None = None
    hourly_values: np.ndarray = field(default_factory=lambda: np.zeros(8760, dtype=np.float32))

    def to_text(self) -> str:
        """Serialize this schedule entry back to Moosas `.sch` text."""
        if self.schedule_type == "Daily":
            assert self.values is not None
            return f"{self.name},Daily," + ",".join(f"{float(value):.6f}" for value in self.values)
        if self.schedule_type == "Hourly":
            assert self.values is not None
            return f"{self.name},Hourly," + ",".join(f"{float(value):.6f}" for value in self.values)
        if self.schedule_type == "Weekly":
            assert self.weekly_refs is not None
            return f"{self.name},Weekly," + ",".join(self.weekly_refs)
        raise ValueError(f"Unsupported schedule type: {self.schedule_type}")


@dataclass
class Schedule:
    """Manage Daily, Weekly and Hourly Moosas schedules as numeric arrays."""

    entries: dict[str, ScheduleEntry] = field(default_factory=dict)

    @classmethod
    def from_text(cls, schedule_text: str) -> "Schedule":
        """Parse raw `.sch` text into schedule entries."""
        schedule = cls()
        if not schedule_text.strip():
            return schedule

        raw_rows: list[list[str]] = []
        for raw_line in schedule_text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("!"):
                continue
            raw_rows.append([item.strip() for item in line.split(",")])

        for row in raw_rows:
            if len(row) < 2 or not row[0]:
                continue
            name = row[0]
            schedule_type = row[1]
            if schedule_type == "Daily":
                values = [float(value) for value in row[2:26]]
                hourly_values = np.tile(np.asarray(values, dtype=np.float32), 365)
                schedule.entries[name] = ScheduleEntry(
                    name=name,
                    schedule_type="Daily",
                    values=values,
                    hourly_values=hourly_values.astype(np.float32),
                )
            elif schedule_type == "Hourly":
                values = [float(value) for value in row[2:8762]]
                schedule.entries[name] = ScheduleEntry(
                    name=name,
                    schedule_type="Hourly",
                    values=values,
                    hourly_values=np.asarray(values, dtype=np.float32),
                )
            elif schedule_type == "Weekly":
                schedule.entries[name] = ScheduleEntry(
                    name=name,
                    schedule_type="Weekly",
                    weekly_refs=row[2:9],
                )

        for entry in schedule.entries.values():
            if entry.schedule_type != "Weekly":
                continue
            if entry.weekly_refs is None or len(entry.weekly_refs) != 7:
                raise ValueError(f"Weekly schedule '{entry.name}' must reference exactly 7 daily schedules.")
            hourly_values = np.zeros(8760, dtype=np.float32)
            for day_index in range(365):
                daily_name = entry.weekly_refs[day_index % 7]
                daily_entry = schedule.entries.get(daily_name)
                if daily_entry is None or daily_entry.schedule_type != "Daily" or daily_entry.values is None:
                    raise ValueError(f"Weekly schedule '{entry.name}' references missing daily schedule '{daily_name}'.")
                hourly_values[day_index * 24 : (day_index + 1) * 24] = np.asarray(daily_entry.values, dtype=np.float32)
            entry.hourly_values = hourly_values
        return schedule

    @classmethod
    def fromText(cls, schedule_text: str) -> "Schedule":
        """CamelCase wrapper for parsing raw `.sch` text."""
        return cls.from_text(schedule_text)

    def merge_text(self, schedule_text: str) -> None:
        """Merge another `.sch` text stream into this schedule manager."""
        merged = Schedule.from_text(schedule_text)
        self.entries.update(merged.entries)

    def mergeText(self, schedule_text: str) -> None:
        """CamelCase wrapper for merging `.sch` text."""
        self.merge_text(schedule_text)

    def to_text(self) -> str:
        """Serialize all stored schedules back to one `.sch` text stream."""
        if not self.entries:
            return ""
        lines = [entry.to_text() for entry in self.entries.values()]
        return "\n".join(lines) + "\n"

    def toText(self) -> str:
        """CamelCase wrapper for serializing schedules."""
        return self.to_text()

    def _rebuild_weekly_hourly_values(self, name: str) -> None:
        """Refresh one Weekly schedule after any referenced Daily schedule changes."""
        entry = self.entries[name]
        if entry.schedule_type != "Weekly":
            return
        if entry.weekly_refs is None or len(entry.weekly_refs) != 7:
            raise ValueError(f"Weekly schedule '{entry.name}' must reference exactly 7 daily schedules.")
        hourly_values = np.zeros(8760, dtype=np.float32)
        for day_index in range(365):
            daily_name = entry.weekly_refs[day_index % 7]
            daily_entry = self.entries.get(daily_name)
            if daily_entry is None or daily_entry.schedule_type != "Daily" or daily_entry.values is None:
                raise ValueError(f"Weekly schedule '{entry.name}' references missing daily schedule '{daily_name}'.")
            hourly_values[day_index * 24 : (day_index + 1) * 24] = np.asarray(daily_entry.values, dtype=np.float32)
        entry.hourly_values = hourly_values

    def scale_name(self, name: str, factor: float, _visited: set[str] | None = None) -> None:
        """Scale one schedule and, for Weekly schedules, all referenced Daily schedules."""
        if name not in self.entries:
            raise KeyError(f"Schedule '{name}' does not exist.")
        if factor < 0.0:
            factor = 0.0
        visited = set() if _visited is None else _visited
        if name in visited:
            return
        visited.add(name)
        entry = self.entries[name]
        if entry.schedule_type in {"Daily", "Hourly"}:
            if entry.values is None:
                raise ValueError(f"Schedule '{name}' has no values to scale.")
            entry.values = [float(value) * factor for value in entry.values]
            entry.hourly_values = entry.hourly_values * factor
            return
        if entry.schedule_type == "Weekly":
            if entry.weekly_refs is None:
                raise ValueError(f"Weekly schedule '{name}' has no daily references.")
            for daily_name in entry.weekly_refs:
                self.scale_name(daily_name, factor, visited)
            self._rebuild_weekly_hourly_values(name)
            return
        raise ValueError(f"Unsupported schedule type: {entry.schedule_type}")

    def scaleName(self, name: str, factor: float) -> None:
        """CamelCase wrapper for scaling a named schedule."""
        self.scale_name(name, factor)

    def scale_names(self, names: Iterable[str], factor: float) -> None:
        """Scale several schedules once, sharing dependency tracking."""
        visited: set[str] = set()
        for name in dict.fromkeys(name for name in names if name in self.entries):
            self.scale_name(name, factor, visited)

    def _collect_dependency_names(self, names: set[str]) -> set[str]:
        """Collect Weekly schedule dependencies needed for a valid schedule subset."""
        expanded = set(names)
        changed = True
        while changed:
            changed = False
            for name in list(expanded):
                entry = self.entries.get(name)
                if entry is None or entry.schedule_type != "Weekly" or entry.weekly_refs is None:
                    continue
                for dep_name in entry.weekly_refs:
                    if dep_name not in expanded and dep_name in self.entries:
                        expanded.add(dep_name)
                        changed = True
        return expanded

    def subset_text(self, names: Iterable[str]) -> str:
        """Serialize selected schedules plus their dependencies."""
        requested = {name for name in names if name in self.entries}
        if not requested:
            return ""
        selected = self._collect_dependency_names(requested)
        lines = [entry.to_text() for name, entry in self.entries.items() if name in selected]
        return "\n".join(lines) + ("\n" if lines else "")

    def subsetText(self, names: Iterable[str]) -> str:
        """CamelCase wrapper for serializing a schedule subset."""
        return self.subset_text(names)

    def has_name(self, name: str) -> bool:
        """Return whether a schedule with this exact name exists."""
        return name in self.entries

    def hasName(self, name: str) -> bool:
        """CamelCase wrapper for schedule-name lookup."""
        return self.has_name(name)

    def names(self) -> list[str]:
        """Return schedule names in insertion order."""
        return list(self.entries.keys())

    def select_name(self, includes: tuple[str, ...], suffixes: tuple[str, ...]) -> str:
        """Find the first schedule whose name contains tokens and preferred suffix."""
        upper_includes = tuple(item.upper() for item in includes)
        upper_suffixes = tuple(item.upper() for item in suffixes)
        for suffix in upper_suffixes:
            for name in self.entries:
                upper_name = name.upper()
                if all(token in upper_name for token in upper_includes) and upper_name.endswith(suffix):
                    return name
        for name in self.entries:
            upper_name = name.upper()
            if all(token in upper_name for token in upper_includes):
                return name
        raise ValueError(f"Could not find a schedule name matching includes={includes}, suffixes={suffixes}.")

    def selectName(self, includes: tuple[str, ...], suffixes: tuple[str, ...]) -> str:
        """CamelCase wrapper for selecting a schedule name."""
        return self.select_name(includes, suffixes)


@dataclass
class BuildingInstance:
    """Hold one building's geometry, archetype schedules and energy results."""

    StoryHeight: float = 0.0
    height: float = 0.0
    FloorArea: float = 0.0
    PerimeterZoneArea: float = 0.0
    ExteriorWallArea: float = 0.0
    ExteriorWindowArea: float = 0.0
    RoofArea: float = 0.0
    SkylightArea: float = 0.0
    GroundFloorArea: float = 0.0
    SummerSolarGain: float | str = 0.0
    WinterSolarGain: float | str = 0.0
    WallUValue: float = 0.0
    WindowUValue: float = 0.0
    WindowSHGC: float = 0.0
    CoolingSetpointTemp: float | str = 0.0
    CoolingSetpointHumidity: float | str = 0.0
    HeatingSetpointTemp: float | str = 0.0
    CoolingEER: float = 1.0
    HeatingEER: float = 0.0
    OccupancyStartHour: int = 0
    OccupancyEndHour: int = 0
    OccupantDensity: float | str = 0.0
    FreshAirPerPerson: float | str = 0.0
    OccupantHeatGain: float | str = 0.0
    EquipmentHeatGain: float | str = 0.0
    LightingHeatGain: float | str = 0.0
    InfiltrationACH: float = 0.0
    NightVentilationACH: float = 0.0
    wwr: float = 0.5
    shapeFactor: float = 0.0
    multipler: float = 0.0
    GFA: float = 0.0
    _perimeter: float = 0.0
    load: np.ndarray = field(default_factory=lambda: np.zeros(24, dtype=np.float32))
    schedule: Schedule | None = None
    archetype: str = ""

    INPUT_FIELDS: ClassVar[tuple[str, ...]] = (
        "StoryHeight",
        "FloorArea",
        "PerimeterZoneArea",
        "ExteriorWallArea",
        "ExteriorWindowArea",
        "RoofArea",
        "SkylightArea",
        "GroundFloorArea",
        "SummerSolarGain",
        "WinterSolarGain",
        "WallUValue",
        "WindowUValue",
        "WindowSHGC",
        "CoolingSetpointTemp",
        "CoolingSetpointHumidity",
        "HeatingSetpointTemp",
        "CoolingEER",
        "HeatingEER",
        "OccupancyStartHour",
        "OccupancyEndHour",
        "OccupantDensity",
        "FreshAirPerPerson",
        "OccupantHeatGain",
        "EquipmentHeatGain",
        "LightingHeatGain",
        "InfiltrationACH",
        "NightVentilationACH",
    )
    TEMPLATE_FIELD_MAP: ClassVar[dict[str, str]] = {
        "zone_wallU": "WallUValue",
        "zone_winU": "WindowUValue",
        "zone_win_SHGC": "WindowSHGC",
        "zone_c_temp": "CoolingSetpointTemp",
        "zone_c_hum": "CoolingSetpointHumidity",
        "zone_h_temp": "HeatingSetpointTemp",
        "zone_CoolingEER": "CoolingEER",
        "zone_collingEER": "CoolingEER",
        "zone_HeatingEER": "HeatingEER",
        "zone_work_start": "OccupancyStartHour",
        "zone_work_end": "OccupancyEndHour",
        "zone_ppsm": "OccupantDensity",
        "zone_pfav": "FreshAirPerPerson",
        "zone_popheat": "OccupantHeatGain",
        "zone_equipment": "EquipmentHeatGain",
        "zone_lighting": "LightingHeatGain",
        "zone_infiltration": "InfiltrationACH",
        "zone_nightACH": "NightVentilationACH",
    }
    INTEGER_TEMPLATE_FIELDS: ClassVar[set[str]] = {"zone_work_start", "zone_work_end"}

    @staticmethod
    def _normalize_archetype(building_type: str) -> str:
        """Normalize and validate an archetype name."""
        normalized = building_type.strip().lower()
        if normalized not in ARCHETYPE_TYPES:
            raise ValueError(f"Unsupported building_type: {building_type}")
        return normalized

    @staticmethod
    def _template_rows(template_path: Path) -> list[dict[str, str]]:
        """Load one archetype CSV file as dictionaries."""
        with template_path.open("r", encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))

    @classmethod
    def _load_archetype_rows(cls) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
        """Load mean/std archetype tables together."""
        return cls._template_rows(ARCHETYPE_MEAN_PATH), cls._template_rows(ARCHETYPE_STD_PATH)

    @staticmethod
    def _coerce_value(value: Any) -> Any:
        """Convert template string values to numbers when possible."""
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return 0.0
            try:
                number = float(stripped)
            except ValueError:
                return stripped
            if number.is_integer():
                return int(number)
            return number
        return value

    @staticmethod
    def _coerce_float(value: Any) -> float:
        """Convert a template value to float and reject schedule names."""
        coerced = BuildingInstance._coerce_value(value)
        if isinstance(coerced, str):
            raise ValueError(f"Expected numeric value, got '{value}'")
        return float(coerced)

    @staticmethod
    def _format_field(value: Any) -> str:
        """Format one `.i` input field without unnecessary decimals."""
        if isinstance(value, str):
            return value
        if isinstance(value, bool):
            return "1" if value else "0"
        if isinstance(value, int):
            return str(value)
        if isinstance(value, float):
            if value.is_integer():
                return str(int(value))
            return f"{value:.10g}"
        return str(value)

    def __post_init__(self) -> None:
        """Normalize schedule/load fields after dataclass construction."""
        if isinstance(self.schedule, str):
            self.schedule = Schedule.from_text(self.schedule)
        self.load = np.asarray(self.load, dtype=np.float32)

    @classmethod
    def _select_archetype_row(
        cls,
        rows: list[dict[str, str]],
        *,
        climate_key: str,
        template_type: str,
        standard: str,
    ) -> dict[str, str]:
        """Select one row from an archetype table by climate, type and standard."""
        for row in rows:
            if (
                row["climate_zone"].strip().lower() == "climatezone2".lower()
                and row["type"].strip().upper() == template_type
                and row["standard"].strip() == standard
            ):
                return row
        raise ValueError(
            f"No template found for climate_zone={climate_key}, template_type={template_type}, standard={standard}"
        )

    @classmethod
    def _sample_template_value(
        cls,
        mean_row: dict[str, str],
        std_row: dict[str, str],
        key: str,
        rng: Random,
    ) -> float | int:
        """Sample a numeric template value from mean/std rows."""
        mean_value = cls._coerce_float(mean_row[key])
        std_value = cls._coerce_float(std_row[key])
        sampled = rng.normalvariate(mean_value, std_value) if std_value > 0.0 else mean_value
        if key == "wwr":
            if sampled < 0.0 or sampled > 1.0:
                sampled = mean_value
            return float(min(max(sampled, 0.0), 1.0))
        if sampled < 0.0:
            sampled = mean_value
        if key in cls.INTEGER_TEMPLATE_FIELDS:
            return int(min(max(round(sampled), 0), 23))
        return float(sampled)

    def _ensure_schedule_exists(self) -> Schedule:
        """Create an empty schedule manager when this building has none."""
        if self.schedule is None:
            self.schedule = Schedule()
        return self.schedule

    @staticmethod
    def _maybe_float(value: Any) -> float | None:
        """Return a float when possible; return None for schedule names."""
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return 0.0
            try:
                return float(stripped)
            except ValueError:
                return None
        if isinstance(value, (int, float)):
            return float(value)
        return None

    def _require_schedule_name(self, value: Any, field_name: str) -> None:
        """Validate that a string-valued input field points to a stored schedule."""
        if isinstance(value, str):
            schedule = self.schedule
            if schedule is None or not schedule.has_name(value):
                raise ValueError(f"{field_name} references missing schedule '{value}'.")

    def _sample_schedule_scale(self, std_value: Any, rng: Random) -> float:
        """Sample a non-negative multiplier for schedule-valued template fields."""
        std_number = self._maybe_float(std_value)
        if std_number is None or std_number <= 0.0:
            return 1.0
        return max(rng.normalvariate(1.0, std_number), 0.0)

    def _sample_numeric_or_schedule_value(
        self,
        attr_name: str,
        mean_value: Any,
        std_value: Any,
        rng: Random,
    ) -> Any:
        """Sample numeric fields or scale referenced schedule fields."""
        mean_number = self._maybe_float(mean_value)
        if mean_number is None:
            schedule_name = str(mean_value).strip()
            if not schedule_name:
                return 0.0
            self._ensure_schedule_exists()
            if self.schedule is None or not self.schedule.has_name(schedule_name):
                raise ValueError(f"Template references missing schedule '{schedule_name}' for field '{attr_name}'.")
            self.schedule.scale_name(schedule_name, self._sample_schedule_scale(std_value, rng))
            return schedule_name

        std_number = self._coerce_float(std_value)
        sampled = rng.normalvariate(mean_number, std_number) if std_number > 0.0 else mean_number
        if sampled < 0.0:
            sampled = mean_number
        if attr_name in {"OccupancyStartHour", "OccupancyEndHour"}:
            return int(min(max(round(sampled), 0), 23))
        return float(sampled)

    def _row_values(self, *, roof_area: float, ground_floor_area: float) -> list[str]:
        """Build one zone row for the three-row Moosas `.i` input."""
        values: dict[str, Any] = {field_name: getattr(self, field_name) for field_name in self.INPUT_FIELDS}
        values["RoofArea"] = roof_area
        values["GroundFloorArea"] = ground_floor_area
        for field_name in (
            "SummerSolarGain",
            "WinterSolarGain",
            "CoolingSetpointTemp",
            "CoolingSetpointHumidity",
            "HeatingSetpointTemp",
            "OccupantDensity",
            "FreshAirPerPerson",
            "OccupantHeatGain",
            "EquipmentHeatGain",
            "LightingHeatGain",
        ):
            self._require_schedule_name(values[field_name], field_name)
        return [self._format_field(values[field_name]) for field_name in self.INPUT_FIELDS]

    def _update_envelope_by_wwr(self) -> None:
        """Update wall/window area fields after WWR or geometry changes."""
        if not 0.0 <= float(self.wwr) <= 1.0:
            raise ValueError(f"wwr must be between 0 and 1, got {self.wwr}")
        if self._perimeter <= 0.0 or self.StoryHeight <= 0.0:
            return
        self.PerimeterZoneArea = min(self._perimeter * 6.0, self.FloorArea)
        facade_area = self._perimeter * self.StoryHeight
        self.ExteriorWallArea = facade_area * (1.0 - float(self.wwr))
        self.ExteriorWindowArea = facade_area * float(self.wwr)

    def toStr(self) -> str:
        """Serialize this building into MoosasEnergy's three-zone `.i` format."""
        roof_row = self._row_values(roof_area=self.FloorArea, ground_floor_area=0.0)
        middle_row = self._row_values(roof_area=0.0, ground_floor_area=0.0)
        ground_row = self._row_values(roof_area=0.0, ground_floor_area=self.FloorArea)
        return "\n".join([",".join(roof_row), ",".join(middle_row), ",".join(ground_row)])

    def writeSchedule(self) -> str:
        """Serialize all schedules attached to this building."""
        if self.schedule is None:
            return ""
        return self.schedule.to_text()

    def fromArchetype(
        self,
        climate_zone: int | str,
        building_type: str,
        standard: str = "GB55015-2021",
        rng: Random | None = None,
        **kwargs: Any,
    ) -> "BuildingInstance":
        """Update thermal properties and archetype schedules from mean/std templates."""
        archetype = self._normalize_archetype(building_type)
        climate_key = f"climatezone{int(climate_zone)}"
        template_type = TEMPLATE_TYPE_MAP[archetype]
        mean_rows, std_rows = self._load_archetype_rows()
        mean_row = self._select_archetype_row(
            mean_rows,
            climate_key=climate_key,
            template_type=template_type,
            standard=standard,
        )
        std_row = self._select_archetype_row(
            std_rows,
            climate_key=climate_key,
            template_type=template_type,
            standard=standard,
        )
        random_source = rng or Random()
        previous_wwr = float(self.wwr) if 0.0 < float(self.wwr) <= 1.0 else 0.5
        previous_schedule = deepcopy(self.schedule) if self.schedule is not None else None
        previous_summer = self.SummerSolarGain if isinstance(self.SummerSolarGain, str) else None
        previous_winter = self.WinterSolarGain if isinstance(self.WinterSolarGain, str) else None

        self.archetype = archetype
        self.schedule = Schedule.from_text(schedule_path.read_text(encoding="utf-8", errors="replace")) if (schedule_path := (SCHEDULE_DIR / f"{archetype}.sch")).exists() else None
        if schedule_path is None or not schedule_path.exists():
            raise FileNotFoundError(f"Schedule file not found: {SCHEDULE_DIR / f'{archetype}.sch'}")

        preserved_solar_names: list[str] = []
        if previous_schedule is not None:
            for solar_name in (previous_summer, previous_winter):
                if solar_name and previous_schedule.has_name(solar_name):
                    preserved_solar_names.append(solar_name)
            solar_subset_text = previous_schedule.subset_text(preserved_solar_names)
            if solar_subset_text:
                self._ensure_schedule_exists().merge_text(solar_subset_text)

        missing_solar_names = [
            solar_name
            for solar_name in (previous_summer, previous_winter)
            if solar_name and (self.schedule is None or not self.schedule.has_name(solar_name))
        ]
        if missing_solar_names:
            raise ValueError(f"Missing preserved solar schedule(s): {missing_solar_names}")

        self.SummerSolarGain = previous_summer if previous_summer else 0.0
        self.WinterSolarGain = previous_winter if previous_winter else 0.0
        sampled_wwr = float(self._sample_numeric_or_schedule_value("wwr", mean_row["wwr"], std_row["wwr"], random_source))
        self.wwr = min(max(sampled_wwr, 0.0), 1.0)
        self._update_envelope_by_wwr()
        for template_key, attr_name in self.TEMPLATE_FIELD_MAP.items():
            if template_key in mean_row and template_key in std_row:
                sampled_value = self._sample_numeric_or_schedule_value(attr_name, mean_row[template_key], std_row[template_key], random_source)
                setattr(self, attr_name, sampled_value)

        if not isinstance(self.OccupantDensity, str):
            self.OccupantDensity = self.schedule.select_name(("OCCDENS",), ("_WEEKLY", "_HOURLY", "_DAILY"))
        if not isinstance(self.EquipmentHeatGain, str):
            self.EquipmentHeatGain = self.schedule.select_name(("EQUIPHEAT",), ("_WEEKLY", "_HOURLY", "_DAILY"))
        if not isinstance(self.LightingHeatGain, str):
            self.LightingHeatGain = self.schedule.select_name(("LIGHTHEAT",), ("_WEEKLY", "_HOURLY", "_DAILY"))

        for key, value in kwargs.items():
            if not hasattr(self, key):
                raise AttributeError(f"BuildingInstance has no attribute '{key}'")
            setattr(self, key, value)
            if key == "wwr":
                self._update_envelope_by_wwr()

        if preserved_solar_names and previous_wwr > 0.0:
            scale_factor = float(self.wwr) / previous_wwr
            if self.schedule is not None:
                self.schedule.scale_names(preserved_solar_names, scale_factor)
        return self

    def includeSolarSchedule(self, sch: str) -> None:
        """Merge radiation schedule text and point solar-gain fields to its names."""
        solar_schedule = sch.strip()
        if not solar_schedule:
            return
        schedule = self._ensure_schedule_exists()
        schedule.merge_text(solar_schedule)
        self.SummerSolarGain = schedule.select_name(("SUMMER",), ("_WEEKLY", "_HOURLY", "_DAILY"))
        self.WinterSolarGain = schedule.select_name(("WINTER",), ("_WEEKLY", "_HOURLY", "_DAILY"))

    def fromAOI(
        self,
        geometry: Any,
        height: float,
        StoryHeight: float = 4.0,
        wwr: float = 0.5,
    ) -> "BuildingInstance":
        """Update geometry fields from a shapely footprint and building height."""
        if geometry is None:
            raise ValueError("geometry is required")
        if StoryHeight <= 0:
            raise ValueError("StoryHeight must be positive")
        if height <= 0:
            raise ValueError("height must be positive")
        if not 0.0 <= wwr <= 1.0:
            raise ValueError("wwr must be between 0 and 1")

        floor_area = float(geometry.area)
        perimeter = float(geometry.length)
        effective_story_count = float(height) / float(StoryHeight)

        self.StoryHeight = float(StoryHeight)
        self.height = float(height)
        self.FloorArea = floor_area
        self._perimeter = perimeter
        self.wwr = float(self.wwr) if self.archetype else float(wwr)
        self._update_envelope_by_wwr()
        self.RoofArea = floor_area
        self.SkylightArea = 0.0
        self.GroundFloorArea = floor_area
        self.shapeFactor = (perimeter * height + floor_area) / (floor_area * height)
        self.multipler = effective_story_count
        self.GFA = self.FloorArea * self.multipler
        self.load = np.zeros(24, dtype=np.float32)
        return self

    def simulation(
        self,
        location: EpwLocation,
        year: bool = False,
        month: bool = True,
        daily: bool = False,
        hourly: bool = False,
        weather_path: str | Path | None = None,
        work_dir: str | Path | None = None,
    ) -> dict[str, float | list[float] | None]:
        """Run MoosasEnergy for this building using a classic Moosas weather CSV."""
        if float(self.shapeFactor) <= 0.0:
            raise ValueError(f"Building has invalid shapeFactor={self.shapeFactor}")
        archetype = (self.archetype or "office").strip().lower()
        building_type = ARCHETYPE_BUILDING_TYPE.get(archetype)
        if building_type is None:
            raise ValueError(f"Unsupported building archetype for energy simulation: {self.archetype}")
        if weather_path is None:
            raise ValueError("BuildingInstance.simulation() requires a Moosas classic weather csv via weather_path.")

        temp_dir: tempfile.TemporaryDirectory[str] | None = None
        if work_dir is None:
            temp_dir = tempfile.TemporaryDirectory(prefix="building_energy_")
            simulation_root = Path(temp_dir.name)
        else:
            simulation_root = Path(work_dir).resolve()
            simulation_root.mkdir(parents=True, exist_ok=True)

        try:
            resolved_weather_path = Path(weather_path).resolve()

            input_path = simulation_root / "input.i"
            output_path = simulation_root / "result.o"
            schedule_path = simulation_root / "input.sch"
            for stale_path in (input_path, output_path, schedule_path):
                if stale_path.exists():
                    stale_path.unlink()

            input_path.write_text(self.toStr(), encoding="utf-8")
            schedule_text = self.writeSchedule()
            resolved_schedule_path: Path | None = None
            if schedule_text.strip():
                schedule_path.write_text(schedule_text, encoding="utf-8")
                resolved_schedule_path = schedule_path

            actual_output = run_energy_simulation(
                input_path,
                resolved_weather_path,
                output_path=output_path,
                building_type=building_type,
                latitude_radians=math.radians(float(location.latitude)),
                altitude_meters=float(location.altitude),
                shape_factor=float(self.shapeFactor),
                export_daily=daily,
                export_hourly=True,
                export_by_zone=True,
                schedule_path=resolved_schedule_path,
                cwd=simulation_root,
            )
            sections = parse_output_sections(actual_output)
            zone_hourly = _extract_zone_hourly_energy(sections)
            roof_hourly = zone_hourly.get(0)
            middle_hourly = zone_hourly.get(1)
            ground_hourly = zone_hourly.get(2)
            if roof_hourly is None or middle_hourly is None or ground_hourly is None:
                raise ValueError("Energy output does not contain the expected 3-zone hourly sections.")

            cooling_wm2 = _building_hourly_load_wm2(
                roof_hourly[:, 0],
                middle_hourly[:, 0],
                ground_hourly[:, 0],
                float(self.multipler),
            )
            heating_wm2 = _building_hourly_load_wm2(
                roof_hourly[:, 1],
                middle_hourly[:, 1],
                ground_hourly[:, 1],
                float(self.multipler),
            )
            self.load = np.asarray(cooling_wm2 - heating_wm2, dtype=np.float32)
            hourly_kwh_m2 = (self.load / 1000.0).astype(np.float32)

            hoys = _resolve_weather_hoys(resolved_weather_path)
            if hoys.shape[0] != hourly_kwh_m2.shape[0]:
                raise ValueError(
                    f"Weather HOY count {hoys.shape[0]} does not match simulated hour count {hourly_kwh_m2.shape[0]}."
                )

            result: dict[str, float | list[float] | None] = {
                "year": None,
                "month": None,
                "daily": None,
                "hourly": None,
            }

            if hourly:
                result["hourly"] = hourly_kwh_m2.tolist()

            if daily:
                day_values: list[float] = []
                for day_index in _complete_day_indices(hoys):
                    mask = (hoys >= day_index * 24) & (hoys < day_index * 24 + 24)
                    day_values.append(float(np.sum(hourly_kwh_m2[mask])))
                result["daily"] = day_values

            if month:
                month_values: list[float] = []
                for month_index in _complete_month_indices(hoys):
                    start = MONTH_START_HOURS[month_index]
                    end = MONTH_START_HOURS[month_index + 1]
                    mask = (hoys >= start) & (hoys < end)
                    month_values.append(float(np.sum(hourly_kwh_m2[mask])))
                result["month"] = month_values

            if year:
                expected_hoys = np.arange(8760, dtype=np.int32)
                if hoys.shape[0] == 8760 and np.array_equal(hoys, expected_hoys):
                    result["year"] = float(np.sum(hourly_kwh_m2))
                else:
                    result["year"] = None

            return result
        finally:
            if temp_dir is not None:
                temp_dir.cleanup()

    def toDict(self) -> dict[str, object]:
        """Serialize this building into a JSON-compatible dictionary."""
        data = {field_info.name: getattr(self, field_info.name) for field_info in fields(self)}
        data["load"] = np.asarray(self.load, dtype=np.float32).tolist()
        data["schedule"] = self.writeSchedule()
        return data

    @classmethod
    def fromDict(cls, data: dict[str, object]) -> "BuildingInstance":
        """Restore a building from a JSON-compatible dictionary."""
        kwargs = dict(data)
        if "load" in kwargs:
            kwargs["load"] = np.asarray(kwargs["load"], dtype=np.float32)
        if "schedule" in kwargs and isinstance(kwargs["schedule"], str):
            kwargs["schedule"] = Schedule.from_text(kwargs["schedule"])
        allowed_fields = {field_info.name for field_info in fields(cls)}
        return cls(**{key: value for key, value in kwargs.items() if key in allowed_fields})

    def to_dict(self) -> dict[str, object]:
        """Compatibility wrapper for legacy snake_case serialization."""
        return self.toDict()

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "BuildingInstance":
        """Compatibility wrapper for legacy snake_case deserialization."""
        return cls.fromDict(data)

    def saveJSON(self, output_path: str | Path) -> Path:
        """Save this building as a formatted JSON file."""
        output_path = Path(output_path).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return output_path

    @classmethod
    def loadJSON(cls, input_path: str | Path) -> "BuildingInstance":
        """Load one building from a JSON file."""
        return cls.from_dict(json.loads(Path(input_path).read_text(encoding="utf-8")))


if __name__ == "__main__":
    schedule_text = "TEST_DAILY,Daily," + ",".join(["1"] * 24)
    test_schedule = Schedule.fromText(schedule_text)
    test_building = BuildingInstance(schedule=test_schedule)
    print(
        {
            "scheduleCount": len(test_schedule.entries),
            "scheduleNames": test_schedule.names(),
            "buildingClass": test_building.__class__.__name__,
        }
    )
