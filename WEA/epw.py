from __future__ import annotations

import calendar
import csv
import math
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


MODULE_DIR = Path(__file__).resolve().parent
REPO_ROOT = MODULE_DIR.parent
RAD_DIR = REPO_ROOT / "RAD"
WEATHER_BIN_DIR = RAD_DIR / "bin" / "weather"
DATA_DIR = RAD_DIR / "data"
OUTPUT_WEATHER_DIR = REPO_ROOT / "outputs" / "weather"
EPW2WEA_EXE = WEATHER_BIN_DIR / "epw2wea.exe"
GENDAYMTX_EXE = WEATHER_BIN_DIR / "gendaymtx.exe"
SUN_POSITION_CSV = DATA_DIR / "sun_position.csv"

TREGENZA_COEFFICIENTS = [
    0.0435449227,
    0.0416418006,
    0.0473984151,
    0.0406730411,
    0.0428934136,
    0.0445221864,
    0.0455168385,
    0.0344199465,
]
TREGENZA_PATCHES_PER_ROW = [30, 30, 24, 24, 18, 12, 6, 1]
NAN_TOKENS = {"nan", "+nan", "-nan", "nan(ind)", "+nan(ind)", "-nan(ind)"}
CLASSIC_WEATHER_COLUMNS = [
    "station_id",
    "unused",
    "hoy",
    "dry_bulb_temperature",
    "humidity_ratio",
    "global_horizontal_radiation",
    "diffuse_horizontal_radiation",
    "ground_temperature",
    "sky_temperature",
    "wind_speed",
    "wind_direction",
    "pressure",
    "unknown",
]


@dataclass(frozen=True)
class EpwLocation:
    station_id: str = ""
    city: str = ""
    state: str = ""
    latitude: float = 0.0
    longitude: float = 0.0
    altitude: float = 0.0
    pressure: float = 0.0
    climate_zone: int | None = None


def _monthly_average_temperature(dry_bulb_temperatures: np.ndarray) -> tuple[float, float]:
    month_means: list[float] = []
    cursor = 0
    for month in range(1, 13):
        hours = calendar.monthrange(2025, month)[1] * 24
        month_slice = dry_bulb_temperatures[cursor : cursor + hours]
        month_means.append(float(np.mean(month_slice)))
        cursor += hours
    return min(month_means), max(month_means)


def _daily_average_temperature(dry_bulb_temperatures: np.ndarray) -> np.ndarray:
    if dry_bulb_temperatures.shape[0] != 8760:
        raise ValueError(f"Expected 8760 hourly dry-bulb values, got {dry_bulb_temperatures.shape[0]}")
    return np.mean(dry_bulb_temperatures.reshape((365, 24)), axis=1)


def classify_climate_zone(dry_bulb_temperatures: np.ndarray) -> int:
    coldest_month_mean, hottest_month_mean = _monthly_average_temperature(dry_bulb_temperatures)
    daily_means = _daily_average_temperature(dry_bulb_temperatures)
    days_le_5 = int(np.sum(daily_means <= 5.0))
    days_ge_25 = int(np.sum(daily_means >= 25.0))

    if coldest_month_mean <= -10.0 and days_le_5 >= 145:
        return 1
    if -10.0 < coldest_month_mean <= 0.0 and 90 <= days_le_5 <= 144:
        return 2
    if 0.0 < coldest_month_mean <= 10.0 and days_le_5 < 90 and hottest_month_mean >= 28.0 and days_ge_25 >= 40:
        return 3
    if coldest_month_mean > 10.0 and hottest_month_mean >= 28.0 and days_ge_25 >= 100:
        return 4
    if 0.0 < coldest_month_mean <= 13.0 and hottest_month_mean < 28.0 and days_ge_25 < 40:
        return 5

    raise ValueError(
        "Unable to classify EPW climate zone with current rules: "
        f"coldest_month_mean={coldest_month_mean:.2f}, hottest_month_mean={hottest_month_mean:.2f}, "
        f"days_le_5={days_le_5}, days_ge_25={days_ge_25}"
    )


def ensure_local_weather_binaries() -> None:
    missing = [path for path in [EPW2WEA_EXE, GENDAYMTX_EXE, SUN_POSITION_CSV] if not path.exists()]
    if missing:
        missing_text = "\n".join(str(path) for path in missing)
        raise FileNotFoundError(
            "Weather dependencies are missing under RAD/bin/weather or RAD/data:\n"
            f"{missing_text}"
        )


def load_sun_vectors() -> np.ndarray:
    ensure_local_weather_binaries()
    vectors = np.loadtxt(SUN_POSITION_CSV, delimiter=",", dtype=float)
    if vectors.shape != (145, 3):
        raise ValueError(f"Unexpected sun_position.csv shape: {vectors.shape}")
    return vectors


def parse_mm_dd(mm_dd: str) -> tuple[int, int]:
    try:
        month_text, day_text = mm_dd.split("-", 1)
        month = int(month_text)
        day = int(day_text)
    except Exception as exc:
        raise ValueError(f"Invalid date '{mm_dd}'. Expected mm-dd.") from exc

    max_day = calendar.monthrange(2025, month)[1]
    if day < 1 or day > max_day:
        raise ValueError(f"Invalid date '{mm_dd}'. Day must be between 1 and {max_day}.")
    return month, day


def day_hour_indices(mm_dd: str) -> list[int]:
    month, day = parse_mm_dd(mm_dd)
    days_before = sum(calendar.monthrange(2025, current_month)[1] for current_month in range(1, month))
    start = (days_before + day - 1) * 24
    return list(range(start, start + 24))


def day_hoy_range(month: int, day: int) -> tuple[int, int]:
    max_day = calendar.monthrange(2025, month)[1]
    if day < 1 or day > max_day:
        raise ValueError(f"Invalid day {day} for month {month}")
    start_hoy = (sum(calendar.monthrange(2025, current_month)[1] for current_month in range(1, month)) + day - 1) * 24
    return start_hoy, start_hoy + 23


def epw2location(epw_path: Path) -> EpwLocation:
    with epw_path.open("r", encoding="utf-8", errors="ignore", newline="") as handle:
        first_row = next(csv.reader([handle.readline().strip()]))
        for _ in range(7):
            handle.readline()
        climate_rows = [next(csv.reader([handle.readline().strip()])) for _ in range(8760)]

    pressures = np.array([float(row[9]) for row in climate_rows], dtype=float)
    dry_bulb_temperatures = np.array([float(row[6]) for row in climate_rows], dtype=float)
    try:
        climate_zone = classify_climate_zone(dry_bulb_temperatures)
    except ValueError:
        climate_zone = None
    return EpwLocation(
        station_id=str(first_row[5]).strip(),
        city=str(first_row[1]).strip(),
        state=str(first_row[2]).strip(),
        latitude=float(first_row[6]),
        longitude=float(first_row[7]),
        altitude=float(first_row[8]),
        pressure=float(np.mean(pressures)),
        climate_zone=climate_zone,
    )


def load_epw_rows(epw_path: Path) -> tuple[list[str], list[list[str]], list[str]]:
    with epw_path.open("r", encoding="utf-8", errors="ignore", newline="") as handle:
        reader = csv.reader(handle)
        location_row = next(reader)
        design_conditions = next(reader)
        typical_periods = next(reader)
        ground_temperatures = next(reader)
        holidays = next(reader)
        comments_1 = next(reader)
        comments_2 = next(reader)
        data_periods = next(reader)
        climate_rows = [row for row in reader if row]

    if len(climate_rows) < 8760:
        raise ValueError(f"EPW does not contain 8760 hourly rows: {epw_path}")
    return location_row, climate_rows[:8760], ground_temperatures


def load_epw_file(epw_path: Path) -> tuple[list[list[str]], list[list[str]]]:
    with epw_path.open("r", encoding="utf-8", errors="ignore", newline="") as handle:
        reader = csv.reader(handle)
        header_rows = [next(reader) for _ in range(8)]
        climate_rows = [row for row in reader if row]

    if len(climate_rows) < 8760:
        raise ValueError(f"EPW does not contain 8760 hourly rows: {epw_path}")
    return header_rows, climate_rows[:8760]


def load_epw_radiation(epw_path: Path) -> tuple[np.ndarray, np.ndarray]:
    _, climate_rows, _ = load_epw_rows(epw_path)
    global_horizontal = np.array([float(row[13]) for row in climate_rows], dtype=float)
    diffuse_horizontal = np.array([float(row[15]) for row in climate_rows], dtype=float)
    return global_horizontal, diffuse_horizontal


def _monthly_ground_temperatures(ground_row: list[str]) -> np.ndarray:
    values = ground_row[6:18]
    if len(values) < 12:
        raise ValueError("EPW ground temperature row does not contain 12 monthly values")
    repeated = []
    for month_index, value in enumerate(values, start=1):
        repeated.extend([round(float(value), 2)] * calendar.monthrange(2025, month_index)[1] * 24)
    return np.array(repeated, dtype=float)


def _humidity_ratio_from_dew_point(dew_point_c: np.ndarray) -> np.ndarray:
    td = dew_point_c.astype(float)
    ratio = (
        3.703
        + 0.286 * td
        + 9.164e-3 * np.power(td, 2)
        + 1.446e-4 * np.power(td, 3)
        + 1.741e-6 * np.power(td, 4)
        + 5.195e-8 * np.power(td, 5)
    )
    return np.round(ratio, 2)


def saturation_vapor_pressure_pa(temperature_c: np.ndarray | float) -> np.ndarray:
    temperature = np.asarray(temperature_c, dtype=float)
    return 610.94 * np.exp((17.625 * temperature) / (temperature + 243.04))


def humidity_ratio_from_rh(
    dry_bulb_temperature_c: np.ndarray | float,
    relative_humidity_fraction: np.ndarray | float,
    pressure_pa: np.ndarray | float,
) -> np.ndarray:
    dry_bulb = np.asarray(dry_bulb_temperature_c, dtype=float)
    relative_humidity = np.clip(np.asarray(relative_humidity_fraction, dtype=float), 0.0, 1.0)
    pressure = np.asarray(pressure_pa, dtype=float)
    actual_vapor_pressure = relative_humidity * saturation_vapor_pressure_pa(dry_bulb)
    denominator = np.maximum(pressure - actual_vapor_pressure, 1e-6)
    return 0.622 * actual_vapor_pressure / denominator


def relative_humidity_from_humidity_ratio(
    dry_bulb_temperature_c: np.ndarray | float,
    humidity_ratio: np.ndarray | float,
    pressure_pa: np.ndarray | float,
) -> np.ndarray:
    dry_bulb = np.asarray(dry_bulb_temperature_c, dtype=float)
    ratio = np.maximum(np.asarray(humidity_ratio, dtype=float), 0.0)
    pressure = np.asarray(pressure_pa, dtype=float)
    actual_vapor_pressure = ratio * pressure / np.maximum(0.622 + ratio, 1e-6)
    saturation_pressure = np.maximum(saturation_vapor_pressure_pa(dry_bulb), 1e-6)
    return np.clip(actual_vapor_pressure / saturation_pressure, 0.0, 1.0)


def dew_point_from_humidity_ratio(
    humidity_ratio: np.ndarray | float,
    pressure_pa: np.ndarray | float,
) -> np.ndarray:
    ratio = np.maximum(np.asarray(humidity_ratio, dtype=float), 0.0)
    pressure = np.asarray(pressure_pa, dtype=float)
    actual_vapor_pressure = np.maximum(ratio * pressure / np.maximum(0.622 + ratio, 1e-6), 1e-6)
    gamma = np.log(np.maximum(actual_vapor_pressure / 610.94, 1e-9))
    denominator = np.maximum(17.625 - gamma, 1e-6)
    return 243.04 * gamma / denominator


def _sky_temperature_from_hir(horizontal_infrared_radiation: np.ndarray) -> np.ndarray:
    stefan_boltzmann = 5.67e-8
    hir = np.maximum(horizontal_infrared_radiation.astype(float), 0.0)
    sky_temperature = np.power(hir / stefan_boltzmann, 0.25) - 273.15
    return np.round(sky_temperature, 2)


def _wind_direction_bucket(degrees: np.ndarray, wind_speed: np.ndarray) -> np.ndarray:
    direction = degrees.astype(float).copy()
    direction[direction == 999] = 0
    buckets = np.round(direction / 360.0 * 16.0, 0).astype(int)
    buckets[(wind_speed != 0) & (buckets == 0)] = 16
    return buckets


def _classic_weather_dataframe(epw_path: Path) -> pd.DataFrame:
    location_row, climate_rows, ground_row = load_epw_rows(epw_path)
    station_id = str(location_row[5]).strip().rstrip("I")
    climate = np.array(climate_rows, dtype=object)

    dry_bulb = climate[:, 6].astype(float)
    dew_point = climate[:, 7].astype(float)
    pressure = climate[:, 9].astype(float)
    hir = climate[:, 12].astype(float)
    global_horizontal = climate[:, 13].astype(float)
    diffuse_horizontal = climate[:, 15].astype(float)
    wind_direction = climate[:, 20].astype(float)
    wind_speed = climate[:, 21].astype(float)
    ground_temperature = _monthly_ground_temperatures(ground_row)

    frame = pd.DataFrame(
        {
            "station_id": [station_id] * len(climate_rows),
            "unused": [0] * len(climate_rows),
            "hoy": np.arange(len(climate_rows), dtype=int),
            "dry_bulb_temperature": np.round(dry_bulb, 2),
            "humidity_ratio": _humidity_ratio_from_dew_point(dew_point),
            "global_horizontal_radiation": np.round(global_horizontal, 2),
            "diffuse_horizontal_radiation": np.round(diffuse_horizontal, 2),
            "ground_temperature": ground_temperature,
            "sky_temperature": _sky_temperature_from_hir(hir),
            "wind_speed": np.round(wind_speed, 2),
            "wind_direction": _wind_direction_bucket(wind_direction, wind_speed),
            "pressure": np.round(pressure, 2),
            "unknown": [9999999] * len(climate_rows),
        },
        columns=CLASSIC_WEATHER_COLUMNS,
    )
    return frame


def _normalize_hoy_window(length: int, start_hoy: int | None, end_hoy: int | None) -> tuple[int, int]:
    start = 0 if start_hoy is None else int(start_hoy)
    end = length - 1 if end_hoy is None else int(end_hoy)
    if start < 0 or end < 0 or start > end:
        raise ValueError(f"Invalid HOY window: start_hoy={start_hoy}, end_hoy={end_hoy}")
    if end >= length:
        raise ValueError(f"Requested end_hoy {end} exceeds available range 0..{length - 1}")
    return start, end


def build_weather_csv(
    epw_path: str | Path,
    *,
    output_path: str | Path | None = None,
    start_hoy: int | None = None,
    end_hoy: int | None = None,
) -> Path:
    epw_path = Path(epw_path).resolve()
    weather_frame = _classic_weather_dataframe(epw_path)
    start, end = _normalize_hoy_window(len(weather_frame), start_hoy, end_hoy)
    weather_frame = weather_frame.iloc[start : end + 1].copy()

    if output_path is None:
        OUTPUT_WEATHER_DIR.mkdir(parents=True, exist_ok=True)
        output_path = OUTPUT_WEATHER_DIR / f"{epw_path.stem}.csv"
    else:
        output_path = Path(output_path).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)

    weather_frame.to_csv(output_path, index=False, header=False)
    return output_path


def _run_epw2wea(epw_path: Path, temp_dir: Path, station_id: str) -> Path:
    wea_path = temp_dir / f"{station_id}.wea"
    with wea_path.open("w", encoding="utf-8") as _:
        pass
    subprocess.run(
        [str(EPW2WEA_EXE), str(epw_path), str(wea_path)],
        check=True,
        cwd=str(WEATHER_BIN_DIR),
        capture_output=True,
        text=True,
    )
    if not wea_path.exists() or wea_path.stat().st_size == 0:
        raise RuntimeError(f"epw2wea did not create a valid WEA file: {wea_path}")
    return wea_path


def _parse_gendaymtx_output(mtx_path: Path) -> np.ndarray:
    metadata: dict[str, str] = {}
    rows: list[list[float]] = []
    with mtx_path.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            metadata_match = re.match(r"([A-Z]+)=(.+)", stripped)
            if metadata_match:
                metadata[metadata_match.group(1)] = metadata_match.group(2).strip()
                continue

            parts = stripped.split()
            if len(parts) != 3:
                continue
            try:
                parsed_row: list[float] = []
                for token in parts:
                    token_lower = token.lower()
                    parsed_row.append(0.0 if token_lower in NAN_TOKENS else float(token))
                rows.append(parsed_row)
            except ValueError:
                continue

    rgb = np.array(rows, dtype=float)
    if rgb.size == 0:
        raise RuntimeError(f"gendaymtx produced no matrix rows: {mtx_path}")

    patch_count = load_sun_vectors().shape[0]
    hour_count = int(metadata.get("NCOLS", "8760"))
    nrows = int(metadata.get("NROWS", str(patch_count)))
    expected = nrows * hour_count
    if rgb.shape[0] != expected:
        raise RuntimeError(f"Unexpected gendaymtx matrix size: {rgb.shape[0]}, expected {expected}")

    broadband = 0.265074126 * rgb[:, 0] + 0.670114631 * rgb[:, 1] + 0.064811243 * rgb[:, 2]
    matrix = broadband.reshape((nrows, hour_count))
    if nrows == patch_count + 1:
        # gendaymtx may prepend one ground row before the Tregenza sky patches.
        matrix = matrix[1:, :]
    elif nrows != patch_count:
        raise RuntimeError(f"Unexpected gendaymtx row count: {nrows}, expected {patch_count} or {patch_count + 1}")

    coefficients = np.repeat(TREGENZA_COEFFICIENTS, TREGENZA_PATCHES_PER_ROW).reshape((patch_count, 1))
    return matrix * coefficients * hour_count / 1000.0


def fix_radiation(calculated_kwh_m2: np.ndarray, observed_global_horizontal_wh_m2: np.ndarray) -> np.ndarray:
    expected_shape = (load_sun_vectors().shape[0], 8760)
    if calculated_kwh_m2.shape != expected_shape:
        raise ValueError(f"Unexpected sky matrix shape: {calculated_kwh_m2.shape}")
    patch_projection = load_sun_vectors()[:, 2]
    calculated_global = np.sum(calculated_kwh_m2.T * patch_projection, axis=1)
    observed_kwh = observed_global_horizontal_wh_m2 / 1000.0
    factors = np.ones_like(observed_kwh, dtype=float)
    mask = np.abs(calculated_global) > 1e-9
    factors[mask] = observed_kwh[mask] / calculated_global[mask]
    return calculated_kwh_m2 * factors.reshape((1, 8760))


def build_hourly_sky_matrix(epw_path: str | Path) -> tuple[EpwLocation, np.ndarray]:
    ensure_local_weather_binaries()
    epw_path = Path(epw_path).resolve()
    location = epw2location(epw_path)
    observed_global, _ = load_epw_radiation(epw_path)
    with tempfile.TemporaryDirectory(prefix="wea_weather_", dir=str(MODULE_DIR)) as temp_dir_str:
        temp_dir = Path(temp_dir_str)
        wea_path = _run_epw2wea(epw_path, temp_dir, location.station_id)
        mtx_path = temp_dir / f"{location.station_id}.mtx"
        with mtx_path.open("w", encoding="utf-8") as stdout_handle:
            subprocess.run(
                [str(GENDAYMTX_EXE), "-m", "1", "-D", "-n", "-O1", str(wea_path)],
                check=True,
                cwd=str(WEATHER_BIN_DIR),
                stdout=stdout_handle,
                stderr=subprocess.PIPE,
                text=True,
            )
        matrix = _parse_gendaymtx_output(mtx_path)
    return location, fix_radiation(matrix, observed_global)


def build_weather_matrix(epw_path: str | Path, *, output_path: str | Path | None = None) -> Path:
    epw_path = Path(epw_path).resolve()
    if output_path is None:
        OUTPUT_WEATHER_DIR.mkdir(parents=True, exist_ok=True)
        output_path = OUTPUT_WEATHER_DIR / f"{epw_path.stem}_mtx.npy"
    else:
        output_path = Path(output_path).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
    _, matrix = build_hourly_sky_matrix(epw_path)
    np.save(output_path, matrix)
    return Path(output_path)
