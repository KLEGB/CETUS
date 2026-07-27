from __future__ import annotations

import calendar
import concurrent.futures
import csv
import json
import math
import os
import random
import re
import shutil
import sys
import warnings
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import wasserstein_distance

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.chdir(PROJECT_ROOT)

from ENERGY.model import ARCHETYPE_MEAN_PATH, ARCHETYPE_STD_PATH, BuildingInstance
from WEA import EpwLocation, epw2location


# -----------------------------------------------------------------------------
# Script configuration (edit here, then run: python calibration/energyModel/calibrate_energy.py)
# -----------------------------------------------------------------------------
BLD_TYPE = "A"
INSTANCE_NUMBER = 1000
RUN_N_TRIALS = 400
RUN_N_STARTUP_TRIALS = 40
RUN_WORKERS = 4
RUN_STUDY_NAME = "energy_calibration_A"
RUN_STORAGE: str | None = None
RUN_SEED = 42

LOSS_METRIC_W1 = "W1"
LOSS_METRIC_DCS = "DCS"
LOSS_METRIC_MEAN = "MEAN"
LOSS_METRIC_CHOICES = {LOSS_METRIC_W1, LOSS_METRIC_DCS, LOSS_METRIC_MEAN}
LOSS_METRIC = LOSS_METRIC_W1

STD_MIN = 0.01
STD_MAX = 0.4
MEAN_BOUNDS_RATIO = 0.30
SOLAR_SCHEDULE_SCALE = 0.001
CALIBRATION_MONTHS = (5, 6, 7, 8, 9)
BASELINE_MONTHS = (1, 3, 4, 10, 11, 12)
CLIMATE_ZONE = 2
STANDARD = "GB55015-2021"

DATA_ROOT = PROJECT_ROOT.parent
GROUP_DATA_CSV = DATA_ROOT / "processed" / "GroupData.csv"
TRUTH_CSV = DATA_ROOT / "processed" / "ElecDataInt.csv"
PACK_ROOT = DATA_ROOT / "neighborhood" / "pack"
EPW_FILE = DATA_ROOT / "epw" / "Beijing_3h_linearupsample_2011EPW.epw"

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

MEAN_MIN_CSV = Path("calibration/energyModel/mean_min.csv")
MEAN_MAX_CSV = Path("calibration/energyModel/mean_max.csv")
RUN_OUTPUT_CSV = Path(f"calibration/energyModel/calibrated_energy_{BLD_TYPE}.csv")
RUN_LOGS_ROOT = Path("calibration/energyModel/logs")
RUN_TMP_DIR = Path("calibration/energyModel/tmp")

GROUP_ID_COLUMN = "GroupID"
GROUP_TYPE_COLUMN = "BuildingTy"
TRUTH_VALUE_COLUMN = "y_int"
TRUTH_YEAR_COLUMN = "year"
TRUTH_MONTH_COLUMN = "month"
WEATHER_PATTERN = "Beijing_3h_linearupsample_{year}EPW_uhi.csv"

BLD_TYPE_TO_ARCHETYPE = {
    "A": "office",
    "B": "commercial",
    "C": "hotel",
    "E": "medical",
    "G": "school",
}
BLD_TYPE_TO_TEMPLATE_TYPE = {
    "A": "OFFICE",
    "B": "COMMERCIAL",
    "C": "HOTEL",
    "E": "HOSPITAL",
    "G": "SCHOOL",
}
IDENTIFIER_COLUMNS = ("climate_zone", "standard", "type")
EXCLUDED_PARAM_COLUMNS = {"zone_equipment_elec"}
RESULT_COLUMNS = ("params", "min", "max", "calibrated", "mean", "std", "p05", "p50", "p95")
MONTH_HOURS = [calendar.monthrange(2025, month)[1] * 24 for month in range(1, 13)]
MONTH_START_HOURS = np.cumsum([0] + MONTH_HOURS)


@dataclass(frozen=True)
class BuildingSample:
    building_json_path: Path
    year: int
    weather_path: Path
    pack_id: str


def _import_optuna():
    try:
        import optuna
        from optuna.samplers import TPESampler
    except ImportError as exc:
        raise RuntimeError(
            "optuna is required to run calibration. Install dependencies with: pip install -r calibration/requirements.txt"
        ) from exc
    return optuna, TPESampler


def _sanitize_tag(value: str) -> str:
    allowed = [char if char.isalnum() or char in {"_", "-"} else "_" for char in value]
    return "".join(allowed).strip("_") or "study"


def _create_log_dir(logs_root: str | Path, study_name: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_dir = Path(logs_root) / f"{_sanitize_tag(study_name)}_{timestamp}"
    log_dir.mkdir(parents=True, exist_ok=False)
    (log_dir / "trial_outputs").mkdir(parents=True, exist_ok=True)
    return log_dir


def _json_default(value: Any) -> Any:
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    return value


def _append_trial_log(log_dir: Path, record: dict[str, Any]) -> None:
    with (log_dir / "trial_log.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=True, default=_json_default) + "\n")


def _read_csv_drop_index(path: str | Path) -> pd.DataFrame:
    frame = pd.read_csv(path, encoding="utf-8-sig")
    unnamed = [column for column in frame.columns if str(column).startswith("Unnamed:") or str(column).startswith("H")]
    return frame.drop(columns=unnamed, errors="ignore")


def _numeric_columns(frame: pd.DataFrame) -> list[str]:
    columns: list[str] = []
    for column in frame.columns:
        if column in IDENTIFIER_COLUMNS or column in EXCLUDED_PARAM_COLUMNS:
            continue
        values = pd.to_numeric(frame[column], errors="coerce")
        if values.notna().all():
            columns.append(str(column))
    return columns


def generate_default_mean_bounds(
    mean_path: str | Path = ARCHETYPE_MEAN_PATH,
    min_path: str | Path = MEAN_MIN_CSV,
    max_path: str | Path = MEAN_MAX_CSV,
    bounds_ratio: float = MEAN_BOUNDS_RATIO,
) -> tuple[Path, Path]:
    mean_frame = pd.read_csv(mean_path)
    numeric_columns = _numeric_columns(mean_frame)
    min_frame = mean_frame[list(IDENTIFIER_COLUMNS) + numeric_columns].copy()
    max_frame = min_frame.copy()
    for column in numeric_columns:
        values = pd.to_numeric(mean_frame[column], errors="raise").astype(float)
        min_frame[column] = values * (1.0 - bounds_ratio)
        max_frame[column] = values * (1.0 + bounds_ratio)
    min_output = Path(min_path)
    max_output = Path(max_path)
    min_output.parent.mkdir(parents=True, exist_ok=True)
    min_frame.to_csv(min_output, index=False)
    max_frame.to_csv(max_output, index=False)
    return min_output, max_output


def _select_template_row(frame: pd.DataFrame, template_type: str) -> pd.Series:
    rows = frame[
        (frame["climate_zone"].astype(str).str.lower() == f"climatezone{CLIMATE_ZONE}")
        & (frame["standard"].astype(str) == STANDARD)
        & (frame["type"].astype(str).str.upper() == template_type)
    ]
    if rows.empty:
        raise ValueError(f"No calibration row found for type={template_type}, climatezone{CLIMATE_ZONE}, {STANDARD}")
    return rows.iloc[0]


def load_param_space(
    mean_min_csv: str | Path,
    mean_max_csv: str | Path,
    bld_type: str,
) -> "OrderedDict[str, tuple[float, float]]":
    template_type = BLD_TYPE_TO_TEMPLATE_TYPE[bld_type]
    min_frame = pd.read_csv(mean_min_csv)
    max_frame = pd.read_csv(mean_max_csv)
    min_row = _select_template_row(min_frame, template_type)
    max_row = _select_template_row(max_frame, template_type)
    space: "OrderedDict[str, tuple[float, float]]" = OrderedDict()
    for column in min_frame.columns:
        if column in IDENTIFIER_COLUMNS or column in EXCLUDED_PARAM_COLUMNS:
            continue
        lower = float(min_row[column])
        upper = float(max_row[column])
        if not math.isfinite(lower) or not math.isfinite(upper):
            continue
        if lower > upper:
            raise ValueError(f"mean bound for {column!r} has min greater than max")
        space[f"mean__{column}"] = (lower, upper)
        space[f"std__{column}"] = (STD_MIN, STD_MAX)
    if not space:
        raise ValueError("No numeric calibration parameters were found.")
    return space


def _read_weather_hoys(weather_path: str | Path) -> np.ndarray:
    hoys: list[int] = []
    with Path(weather_path).open("r", encoding="utf-8", errors="ignore", newline="") as handle:
        reader = csv.reader(handle)
        for row in reader:
            if len(row) < 3:
                continue
            try:
                hoys.append(int(float(row[2])))
            except ValueError:
                continue
    return np.asarray(hoys, dtype=np.int32)


def _complete_month_numbers(weather_path: str | Path) -> list[int]:
    hoys = _read_weather_hoys(weather_path)
    months: list[int] = []
    for month_index in range(12):
        start = int(MONTH_START_HOURS[month_index])
        end = int(MONTH_START_HOURS[month_index + 1])
        expected = np.arange(start, end, dtype=np.int32)
        actual = hoys[(hoys >= start) & (hoys < end)]
        if actual.shape[0] == expected.shape[0] and np.array_equal(actual, expected):
            months.append(month_index + 1)
    return months


def _parse_year(path: Path) -> int | None:
    matches = re.findall(r"(20\d{2})", path.name)
    if not matches:
        return None
    return int(matches[-1])


def build_sample_manifest(
    *,
    bld_type: str,
    instance_number: int,
    seed: int,
    group_data_csv: str | Path = GROUP_DATA_CSV,
    pack_root: str | Path = PACK_ROOT,
) -> list[BuildingSample]:
    group_frame = _read_csv_drop_index(group_data_csv)
    groups = (
        group_frame.loc[group_frame[GROUP_TYPE_COLUMN].astype(str).str.upper() == bld_type, GROUP_ID_COLUMN]
        .dropna()
        .astype(str)
        .drop_duplicates()
        .tolist()
    )
    rng = random.Random(seed)
    rng.shuffle(groups)

    root = Path(pack_root)
    candidates: list[BuildingSample] = []
    for group_id in groups:
        pack_dirs = sorted(path for path in root.glob(f"{group_id}*") if path.is_dir())
        for pack_dir in pack_dirs:
            instance_root = pack_dir / "instance"
            weather_root = pack_dir / "weather"
            if not instance_root.exists() or not weather_root.exists():
                continue
            for instance_dir in sorted(path for path in instance_root.iterdir() if path.is_dir()):
                year = _parse_year(instance_dir)
                if year is None:
                    continue
                weather_path = weather_root / WEATHER_PATTERN.format(year=year)
                if not weather_path.exists():
                    continue
                building_paths = sorted(instance_dir.glob("building_*.json"))
                for building_json_path in building_paths:
                    candidates.append(
                        BuildingSample(
                            building_json_path=building_json_path,
                            year=year,
                            weather_path=weather_path,
                            pack_id=pack_dir.name,
                        )
                    )
                if len(candidates) >= max(instance_number * 5, instance_number):
                    break
            if len(candidates) >= max(instance_number * 5, instance_number):
                break
        if len(candidates) >= max(instance_number * 5, instance_number):
            break

    if not candidates:
        raise RuntimeError(f"No BuildingInstance JSON samples found for BLD_TYPE={bld_type}")
    rng.shuffle(candidates)
    return candidates[: min(instance_number, len(candidates))]


def load_truth_distribution(
    *,
    bld_type: str,
    truth_csv: str | Path = TRUTH_CSV,
    group_data_csv: str | Path = GROUP_DATA_CSV,
) -> pd.DataFrame:
    group_frame = _read_csv_drop_index(group_data_csv)
    target_groups = set(
        group_frame.loc[group_frame[GROUP_TYPE_COLUMN].astype(str).str.upper() == bld_type, GROUP_ID_COLUMN]
        .dropna()
        .astype(str)
        .tolist()
    )
    truth = _read_csv_drop_index(truth_csv)
    truth = truth[truth[GROUP_ID_COLUMN].astype(str).isin(target_groups)].copy()
    truth["year"] = pd.to_numeric(truth[TRUTH_YEAR_COLUMN], errors="raise").astype(int)
    truth["month"] = pd.to_numeric(truth[TRUTH_MONTH_COLUMN], errors="raise").astype(int)
    truth["electricity"] = pd.to_numeric(truth[TRUTH_VALUE_COLUMN], errors="coerce")
    truth = truth[np.isfinite(truth["electricity"])]
    truth[GROUP_ID_COLUMN] = truth[GROUP_ID_COLUMN].astype(str)

    baseline = (
        truth[truth["month"].isin(BASELINE_MONTHS)]
        .groupby([GROUP_ID_COLUMN, "year"], as_index=False)["electricity"]
        .mean()
        .rename(columns={"electricity": "baseline_electricity"})
    )
    target = truth[truth["month"].isin(CALIBRATION_MONTHS)].copy()
    target = target.merge(baseline, on=[GROUP_ID_COLUMN, "year"], how="inner")
    target["electricity"] = (target["electricity"] - target["baseline_electricity"]).clip(lower=0.0)
    target = target[["year", "month", "electricity"]].reset_index(drop=True)
    if target.empty:
        raise RuntimeError(
            f"No baseline-adjusted truth rows found for BLD_TYPE={bld_type}; "
            f"target_months={CALIBRATION_MONTHS}, baseline_months={BASELINE_MONTHS}"
        )
    return target


def _sampled_archetype_frames(
    params: dict[str, float],
    bld_type: str,
    mean_base: pd.DataFrame,
    std_base: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    template_type = BLD_TYPE_TO_TEMPLATE_TYPE[bld_type]
    mean_frame = mean_base.copy()
    std_frame = std_base.copy()
    mask = (
        (mean_frame["climate_zone"].astype(str).str.lower() == f"climatezone{CLIMATE_ZONE}")
        & (mean_frame["standard"].astype(str) == STANDARD)
        & (mean_frame["type"].astype(str).str.upper() == template_type)
    )
    if int(mask.sum()) != 1:
        raise ValueError(f"Expected exactly one archetype row for {template_type}, got {int(mask.sum())}")
    sampled_columns = {name.split("__", 1)[1] for name in params}
    for column in sampled_columns:
        mean_frame[column] = pd.to_numeric(mean_frame[column], errors="raise").astype(float)
        std_frame[column] = pd.to_numeric(std_frame[column], errors="raise").astype(float)
    for name, value in params.items():
        prefix, column = name.split("__", 1)
        if prefix == "mean":
            mean_frame.loc[mask, column] = float(value)
        elif prefix == "std":
            std_frame.loc[mask, column] = float(value)
    return mean_frame, std_frame


@contextmanager
def _temporary_archetype_tables(mean_frame: pd.DataFrame, std_frame: pd.DataFrame):
    mean_path = Path(ARCHETYPE_MEAN_PATH)
    std_path = Path(ARCHETYPE_STD_PATH)
    original_mean = mean_path.read_bytes()
    original_std = std_path.read_bytes()
    try:
        mean_frame.to_csv(mean_path, index=False)
        std_frame.to_csv(std_path, index=False)
        yield
    finally:
        mean_path.write_bytes(original_mean)
        std_path.write_bytes(original_std)


def _apply_solar_schedule_scale(building: BuildingInstance, scale: float) -> None:
    if scale == 1.0:
        return
    if scale < 0.0:
        raise ValueError(f"SOLAR_SCHEDULE_SCALE must be non-negative, got {scale}")
    if building.schedule is None:
        return
    solar_names = [
        schedule_name
        for schedule_name in (building.SummerSolarGain, building.WinterSolarGain)
        if isinstance(schedule_name, str)
    ]
    if solar_names:
        building.schedule.scale_names(solar_names, float(scale))


def _sample_to_payload(sample_index: int, sample: BuildingSample) -> dict[str, Any]:
    return {
        "sample_index": int(sample_index),
        "building_json_path": str(sample.building_json_path),
        "year": int(sample.year),
        "weather_path": str(sample.weather_path),
        "pack_id": str(sample.pack_id),
    }


def _simulate_one_sample_worker(
    sample_payload: dict[str, Any],
    params: dict[str, float],
    bld_type: str,
    location_data: dict[str, Any],
    seed: int,
) -> list[dict[str, Any]]:
    sample_index = int(sample_payload["sample_index"])
    rng = random.Random(int(seed) + sample_index)
    building_json_path = Path(str(sample_payload["building_json_path"]))
    weather_path = Path(str(sample_payload["weather_path"]))
    location = EpwLocation(**location_data)
    archetype = BLD_TYPE_TO_ARCHETYPE[bld_type]

    building = BuildingInstance.loadJSON(building_json_path)
    previous_summer = building.SummerSolarGain
    previous_winter = building.WinterSolarGain
    building.fromArchetype(CLIMATE_ZONE, archetype, standard=STANDARD, rng=rng)
    if building.SummerSolarGain != previous_summer or building.WinterSolarGain != previous_winter:
        raise ValueError(
            f"Solar schedule names changed for {building_json_path}: "
            f"{previous_summer}/{previous_winter} -> {building.SummerSolarGain}/{building.WinterSolarGain}"
        )
    _apply_solar_schedule_scale(building, SOLAR_SCHEDULE_SCALE)

    month_numbers = _complete_month_numbers(weather_path)
    result = building.simulation(location, month=True, weather_path=weather_path)
    month_values = result.get("month")
    if not isinstance(month_values, list):
        return []

    rows: list[dict[str, Any]] = []
    for month, electricity in zip(month_numbers, month_values):
        if month not in CALIBRATION_MONTHS:
            continue
        value = float(electricity)
        if math.isfinite(value):
            rows.append(
                {
                    "sample_index": sample_index,
                    "pack_id": str(sample_payload["pack_id"]),
                    "building_json_path": str(building_json_path),
                    "year": int(sample_payload["year"]),
                    "month": int(month),
                    "electricity": value,
                }
            )
    return rows


def _run_sample_workers(
    sample_payloads: list[dict[str, Any]],
    *,
    params: dict[str, float],
    bld_type: str,
    location_data: dict[str, Any],
    seed: int,
    workers: int,
) -> list[dict[str, Any]]:
    if workers <= 1 or len(sample_payloads) <= 1:
        rows: list[dict[str, Any]] = []
        for payload in sample_payloads:
            rows.extend(_simulate_one_sample_worker(payload, params, bld_type, location_data, seed))
        return rows

    max_workers = min(int(workers), len(sample_payloads), os.cpu_count() or int(workers))
    rows = []
    with concurrent.futures.ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(_simulate_one_sample_worker, payload, params, bld_type, location_data, seed)
            for payload in sample_payloads
        ]
        for future in concurrent.futures.as_completed(futures):
            rows.extend(future.result())
    return rows


def run_simulation_distribution(
    samples: list[BuildingSample],
    *,
    params: dict[str, float],
    bld_type: str,
    location,
    log_output: Path,
    seed: int,
    mean_base: pd.DataFrame,
    std_base: pd.DataFrame,
    workers: int,
) -> pd.DataFrame:
    mean_frame, std_frame = _sampled_archetype_frames(params, bld_type, mean_base, std_base)
    sample_payloads = [_sample_to_payload(index, sample) for index, sample in enumerate(samples)]
    location_data = {
        "station_id": location.station_id,
        "city": location.city,
        "state": location.state,
        "latitude": location.latitude,
        "longitude": location.longitude,
        "altitude": location.altitude,
        "pressure": location.pressure,
        "climate_zone": location.climate_zone,
    }
    with _temporary_archetype_tables(mean_frame, std_frame):
        rows = _run_sample_workers(
            sample_payloads,
            params=params,
            bld_type=bld_type,
            location_data=location_data,
            seed=seed,
            workers=workers,
        )
    sim_frame = pd.DataFrame(rows)
    if sim_frame.empty:
        raise RuntimeError("Simulation did not produce any usable monthly electricity rows.")
    sim_frame = sim_frame.sort_values(["sample_index", "year", "month"]).reset_index(drop=True)
    log_output.parent.mkdir(parents=True, exist_ok=True)
    sim_frame.to_csv(log_output, index=False)
    return sim_frame


def compute_wasserstein_loss(sim_frame: pd.DataFrame, truth_frame: pd.DataFrame) -> float:
    losses: list[float] = []
    for (year, month), truth_group in truth_frame.groupby(["year", "month"]):
        sim_group = sim_frame[(sim_frame["year"] == year) & (sim_frame["month"] == month)]
        if sim_group.empty or truth_group.empty:
            continue
        losses.append(
            float(
                wasserstein_distance(
                    sim_group["electricity"].to_numpy(dtype=np.float64),
                    truth_group["electricity"].to_numpy(dtype=np.float64),
                )
            )
        )
    if not losses:
        raise RuntimeError("No overlapping year/month distributions between simulation and truth.")
    return float(np.mean(losses))


def _distribution_mean_std(values: pd.Series) -> tuple[float, float]:
    array = values.to_numpy(dtype=np.float64)
    if array.size == 0:
        raise ValueError("Distribution must contain at least one value.")
    mean = float(np.mean(array))
    std = float(np.std(array, ddof=1)) if array.size > 1 else 0.0
    return mean, std


def compute_distribution_consistency_score(sim_frame: pd.DataFrame, truth_frame: pd.DataFrame) -> float:
    components: list[tuple[float, float, float, float]] = []
    for (year, month), truth_group in truth_frame.groupby(["year", "month"]):
        sim_group = sim_frame[(sim_frame["year"] == year) & (sim_frame["month"] == month)]
        if sim_group.empty or truth_group.empty:
            continue

        truth_mean, truth_std = _distribution_mean_std(truth_group["electricity"])
        sim_mean, sim_std = _distribution_mean_std(sim_group["electricity"])
        components.append((sim_mean, sim_std, truth_mean, truth_std))

    if not components:
        raise RuntimeError("No overlapping year/month distributions between simulation and truth.")

    truth_mean_avg = float(np.mean([truth_mean for _, _, truth_mean, _ in components]))
    truth_std_avg = float(np.mean([truth_std for _, _, _, truth_std in components]))
    ratios: list[float] = []
    for sim_mean, sim_std, truth_mean, truth_std in components:
        numerator = math.sqrt((sim_mean - truth_mean) ** 2 + (sim_std - truth_std) ** 2)
        denominator = math.sqrt((truth_mean - truth_mean_avg) ** 2 + (truth_std - truth_std_avg) ** 2)
        ratios.append(float(numerator / max(denominator, 1e-9)))
    return float(1.0 - np.mean(ratios))


def compute_mean_bias_metrics(sim_frame: pd.DataFrame, truth_frame: pd.DataFrame) -> dict[str, float]:
    truth_means: list[float] = []
    sim_means: list[float] = []
    diffs: list[float] = []
    for (year, month), truth_group in truth_frame.groupby(["year", "month"]):
        sim_group = sim_frame[(sim_frame["year"] == year) & (sim_frame["month"] == month)]
        if sim_group.empty or truth_group.empty:
            continue
        truth_mean = float(np.mean(truth_group["electricity"].to_numpy(dtype=np.float64)))
        sim_mean = float(np.mean(sim_group["electricity"].to_numpy(dtype=np.float64)))
        truth_means.append(truth_mean)
        sim_means.append(sim_mean)
        diffs.append(sim_mean - truth_mean)
    if not diffs:
        raise RuntimeError("No overlapping year/month distributions between simulation and truth.")
    return {
        "truth_mean": float(np.mean(truth_means)),
        "sim_mean": float(np.mean(sim_means)),
        "mean_diff": float(np.mean(diffs)),
        "mean_abs_diff": float(np.mean(np.abs(diffs))),
    }


def _loss_metric_name() -> str:
    metric = str(LOSS_METRIC).strip().upper()
    if metric not in LOSS_METRIC_CHOICES:
        raise ValueError(f"Unsupported LOSS_METRIC={LOSS_METRIC!r}; expected one of {sorted(LOSS_METRIC_CHOICES)}")
    return metric


def compute_objective_loss(w1: float, dcs: float, mean_abs_diff: float) -> float:
    metric = _loss_metric_name()
    if metric == LOSS_METRIC_W1:
        return float(w1)
    if metric == LOSS_METRIC_DCS:
        return float(100.0 - dcs)
    if metric == LOSS_METRIC_MEAN:
        return float(mean_abs_diff)
    raise AssertionError(f"Unhandled LOSS_METRIC={metric!r}")


def _export_trial_summary_csv(log_dir: Path) -> None:
    log_file = log_dir / "trial_log.jsonl"
    if not log_file.exists():
        return
    records = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    if records:
        pd.DataFrame(records).to_csv(log_dir / "trial_summary.csv", index=False)


def _successful_trials(study) -> list[Any]:
    return [
        trial
        for trial in study.trials
        if trial.state.name == "COMPLETE"
        and trial.value is not None
        and math.isfinite(float(trial.value))
        and trial.params
    ]


def _write_best_outputs(study, output_csv: str | Path, log_dir: Path, param_space: "OrderedDict[str, tuple[float, float]]") -> Path:
    successful = _successful_trials(study)
    if not successful:
        raise RuntimeError(f"No successful calibration trials were recorded; see logs under {log_dir}")
    best_trial = min(successful, key=lambda trial: float(trial.value))
    completed_params = [{name: float(value) for name, value in trial.params.items()} for trial in successful]
    rows: list[dict[str, Any]] = []
    for name, (lower, upper) in param_space.items():
        values = np.asarray([params[name] for params in completed_params if name in params], dtype=np.float64)
        rows.append(
            {
                "params": name,
                "min": lower,
                "max": upper,
                "calibrated": float(best_trial.params[name]),
                "mean": float(np.mean(values)),
                "std": float(np.std(values, ddof=0)),
                "p05": float(np.quantile(values, 0.05)),
                "p50": float(np.quantile(values, 0.50)),
                "p95": float(np.quantile(values, 0.95)),
            }
        )
    output_path = Path(output_csv)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=RESULT_COLUMNS).to_csv(output_path, index=False)
    summary = {
        "best_trial_number": int(best_trial.number),
        "best_loss": float(best_trial.value),
        "loss_metric": best_trial.user_attrs.get("loss_metric"),
        "best_w1": best_trial.user_attrs.get("w1"),
        "best_dcs": best_trial.user_attrs.get("dcs"),
        "best_mean_diff": best_trial.user_attrs.get("mean_diff"),
        "best_mean_abs_diff": best_trial.user_attrs.get("mean_abs_diff"),
        "best_truth_mean": best_trial.user_attrs.get("truth_mean"),
        "best_sim_mean": best_trial.user_attrs.get("sim_mean"),
        "best_output_csv": best_trial.user_attrs.get("output_csv"),
        "result_csv": str(output_path.resolve()),
        "log_dir": str(log_dir.resolve()),
    }
    (log_dir / "best_summary.json").write_text(json.dumps(summary, ensure_ascii=True, indent=2), encoding="utf-8")
    return output_path


def _write_run_metadata(
    log_dir: Path,
    *,
    param_space: "OrderedDict[str, tuple[float, float]]",
    sample_manifest: list[BuildingSample],
) -> None:
    metadata = {
        "study_name": RUN_STUDY_NAME,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "bld_type": BLD_TYPE,
        "instance_number": INSTANCE_NUMBER,
        "n_trials": RUN_N_TRIALS,
        "n_startup_trials": RUN_N_STARTUP_TRIALS,
        "workers": RUN_WORKERS,
        "seed": RUN_SEED,
        "solar_schedule_scale": SOLAR_SCHEDULE_SCALE,
        "storage": RUN_STORAGE,
        "loss_metric": _loss_metric_name(),
        "loss_metric_options": sorted(LOSS_METRIC_CHOICES),
        "calibration_months": CALIBRATION_MONTHS,
        "truth_target_months": CALIBRATION_MONTHS,
        "truth_baseline_months": BASELINE_MONTHS,
        "truth_negative_fallback": "clip_to_zero",
        "simulation_value": "net_load_month_kwh_m2",
        "truth_csv": str(Path(TRUTH_CSV)),
        "group_data_csv": str(Path(GROUP_DATA_CSV)),
        "pack_root": str(Path(PACK_ROOT)),
        "epw_file": str(Path(EPW_FILE)),
        "mean_min_csv": str(Path(MEAN_MIN_CSV).resolve()),
        "mean_max_csv": str(Path(MEAN_MAX_CSV).resolve()),
        "archetype_mean_path": str(Path(ARCHETYPE_MEAN_PATH).resolve()),
        "archetype_std_path": str(Path(ARCHETYPE_STD_PATH).resolve()),
        "sample_count": len(sample_manifest),
        "sample_manifest": [sample.__dict__ for sample in sample_manifest],
        "param_space": {name: {"min": lower, "max": upper} for name, (lower, upper) in param_space.items()},
    }
    (log_dir / "run_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=True, indent=2, default=_json_default),
        encoding="utf-8",
    )


def run_calibration() -> Path:
    bld_type = BLD_TYPE.strip().upper()
    if bld_type not in BLD_TYPE_TO_ARCHETYPE:
        raise ValueError(f"Unsupported BLD_TYPE={BLD_TYPE!r}; expected one of {sorted(BLD_TYPE_TO_ARCHETYPE)}")

    if not Path(MEAN_MIN_CSV).exists() or not Path(MEAN_MAX_CSV).exists():
        generate_default_mean_bounds()

    metric_name = _loss_metric_name()

    optuna, TPESampler = _import_optuna()
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    if hasattr(optuna, "exceptions") and hasattr(optuna.exceptions, "ExperimentalWarning"):
        warnings.filterwarnings("ignore", category=optuna.exceptions.ExperimentalWarning)

    param_space = load_param_space(MEAN_MIN_CSV, MEAN_MAX_CSV, bld_type)
    sample_manifest = build_sample_manifest(bld_type=bld_type, instance_number=INSTANCE_NUMBER, seed=RUN_SEED)
    truth_frame = load_truth_distribution(bld_type=bld_type)
    location = epw2location(Path(EPW_FILE))
    mean_base = pd.read_csv(ARCHETYPE_MEAN_PATH)
    std_base = pd.read_csv(ARCHETYPE_STD_PATH)
    log_dir = _create_log_dir(RUN_LOGS_ROOT, RUN_STUDY_NAME)
    RUN_TMP_DIR.mkdir(parents=True, exist_ok=True)
    _write_run_metadata(log_dir, param_space=param_space, sample_manifest=sample_manifest)
    shutil.copy2(MEAN_MIN_CSV, log_dir / "mean_min.csv")
    shutil.copy2(MEAN_MAX_CSV, log_dir / "mean_max.csv")

    sampler = TPESampler(
        n_startup_trials=RUN_N_STARTUP_TRIALS,
        multivariate=True,
        group=True,
        seed=RUN_SEED,
    )
    study = optuna.create_study(
        study_name=RUN_STUDY_NAME,
        direction="minimize",
        sampler=sampler,
        storage=RUN_STORAGE,
        load_if_exists=True,
    )

    best_loss = float("inf")
    best_w1: float | None = None
    best_dcs: float | None = None
    best_mean_metrics: dict[str, float] | None = None

    def objective(trial) -> float:
        nonlocal best_loss, best_w1, best_dcs, best_mean_metrics
        params = {name: trial.suggest_float(name, lower, upper) for name, (lower, upper) in param_space.items()}
        log_output = log_dir / "trial_outputs" / f"trial_{trial.number:04d}.csv"
        record: dict[str, Any] = {
            "trial_number": int(trial.number),
            "status": "failed",
            "loss": None,
            "loss_metric": metric_name,
            "w1": None,
            "dcs": None,
            "mean_diff": None,
            "mean_abs_diff": None,
            "truth_mean": None,
            "sim_mean": None,
            "sampled_params": params,
            "output_csv": str(log_output.resolve()),
            "failure": None,
        }
        try:
            sim_frame = run_simulation_distribution(
                sample_manifest,
                params=params,
                bld_type=bld_type,
                location=location,
                log_output=log_output,
                seed=RUN_SEED + int(trial.number) * 100000,
                mean_base=mean_base,
                std_base=std_base,
                workers=RUN_WORKERS,
            )
            w1 = compute_wasserstein_loss(sim_frame, truth_frame)
            dcs = compute_distribution_consistency_score(sim_frame, truth_frame)
            mean_metrics = compute_mean_bias_metrics(sim_frame, truth_frame)
            loss = compute_objective_loss(w1, dcs, mean_metrics["mean_abs_diff"])
            record["status"] = "completed"
            record["loss"] = loss
            record["w1"] = w1
            record["dcs"] = dcs
            record["mean_diff"] = mean_metrics["mean_diff"]
            record["mean_abs_diff"] = mean_metrics["mean_abs_diff"]
            record["truth_mean"] = mean_metrics["truth_mean"]
            record["sim_mean"] = mean_metrics["sim_mean"]
            trial.set_user_attr("loss_metric", metric_name)
            trial.set_user_attr("w1", w1)
            trial.set_user_attr("dcs", dcs)
            trial.set_user_attr("mean_diff", mean_metrics["mean_diff"])
            trial.set_user_attr("mean_abs_diff", mean_metrics["mean_abs_diff"])
            trial.set_user_attr("truth_mean", mean_metrics["truth_mean"])
            trial.set_user_attr("sim_mean", mean_metrics["sim_mean"])
            trial.set_user_attr("output_csv", str(log_output.resolve()))
            if math.isfinite(loss) and loss < best_loss:
                best_loss = float(loss)
                best_w1 = float(w1)
                best_dcs = float(dcs)
                best_mean_metrics = dict(mean_metrics)
            return loss
        except Exception as exc:
            record["failure"] = repr(exc)
            trial.set_user_attr("failure", repr(exc))
            return float("inf")
        finally:
            _append_trial_log(log_dir, record)
            loss_text = f"{record['loss']:.6f}" if record["loss"] is not None else "inf"
            w1_text = f"{record['w1']:.6f}" if record["w1"] is not None else "nan"
            dcs_text = f"{record['dcs']:.6f}" if record["dcs"] is not None else "nan"
            best_loss_text = f"{best_loss:.6f}" if math.isfinite(best_loss) else "inf"
            best_w1_text = f"{best_w1:.6f}" if best_w1 is not None else "nan"
            best_dcs_text = f"{best_dcs:.6f}" if best_dcs is not None else "nan"
            print(
                f"TPE SAMPLING {trial.number}: "
                f"METRIC={metric_name} LOSS={loss_text} W1={w1_text} DCS={dcs_text} "
                f"BEST LOSS={best_loss_text} BEST W1={best_w1_text} BEST DCS={best_dcs_text}",
                flush=True,
            )

    try:
        study.optimize(objective, n_trials=RUN_N_TRIALS)
        _export_trial_summary_csv(log_dir)
        return _write_best_outputs(study, RUN_OUTPUT_CSV, log_dir, param_space)
    finally:
        if RUN_TMP_DIR.exists():
            shutil.rmtree(RUN_TMP_DIR, ignore_errors=True)


def main() -> int:
    output = run_calibration()
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
