from __future__ import annotations

import concurrent.futures
import json
import math
import os
import shutil
import sys
import uuid
import warnings
from collections import OrderedDict
from numbers import Real
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.chdir(PROJECT_ROOT)

from UHI.csv_calculator import csvCalculation
from UHI.modelInfo import settingInfo


def _build_param_registry() -> "OrderedDict[str, float]":
    settings = settingInfo()
    registry: "OrderedDict[str, float]" = OrderedDict()
    for name in settingInfo.__slots__:
        value = getattr(settings, name)
        if isinstance(value, bool):
            continue
        if isinstance(value, Real):
            registry[name] = float(value)
    return registry


PARAM_REGISTRY: "OrderedDict[str, float]" = _build_param_registry()

PARAM_FIELD_COLUMNS = ["params", "min", "max"]
RESULT_COLUMNS = ["params", "min", "max", "calibrated", "mean", "std", "p05", "p50", "p95"]

# -----------------------------------------------------------------------------
# Script configuration (edit here, then run: python calibration/calibrate.py)
# -----------------------------------------------------------------------------
# run settings
RUN_PARAMS_FIELD_CSV = "calibration/params_field.csv"
RUN_INPUT_CSV = r"\\166.111.40.8\home\2025_UHIAirToAir\dataset\packed\1000m\input_LCZ1.csv"
RUN_TRUTH_CSV = r"\\166.111.40.8\home\2025_UHIAirToAir\dataset\packed\1000m\truth_LCZ1.csv"
RUN_OUTPUT_CSV = "calibration/calibrated_params_LCZ1.csv"
RUN_N_TRIALS = 200
RUN_N_STARTUP_TRIALS = 40
RUN_PREHEAT = 4
RUN_WORKERS = 4
RUN_START_T: int | None = None
RUN_END_T: int | None = None
RUN_STUDY_NAME = "calibration_LCZ1"
RUN_STORAGE: str | None = "sqlite:///calibration/calibration.db"
RUN_TRUTH_COLUMN = "TEM"
RUN_SIM_COLUMN = "Tair"
RUN_TMP_DIR = "calibration/tmp"
RUN_LOGS_ROOT = "calibration/logs"
RUN_SEED = 42


def generate_params_field(output_path: str | Path, bounds_ratio: float = 0.20) -> Path:
    """
    Generate the editable parameter bounds CSV.
    """
    if bounds_ratio < 0:
        raise ValueError("bounds_ratio must be non-negative")

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "params": name,
            "min": default * (1.0 - bounds_ratio),
            "max": default * (1.0 + bounds_ratio),
        }
        for name, default in PARAM_REGISTRY.items()
    ]
    pd.DataFrame(rows, columns=PARAM_FIELD_COLUMNS).to_csv(output, index=False)
    return output


def _load_param_frame(params_field_csv: str | Path) -> pd.DataFrame:
    path = Path(params_field_csv)
    if not path.exists():
        raise FileNotFoundError(f"params field csv not found: {path}")

    frame = pd.read_csv(path)
    missing = [column for column in PARAM_FIELD_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(f"params field csv is missing required columns: {missing}")
    if frame["params"].isna().any():
        raise ValueError("params field csv contains empty parameter names")
    if frame["params"].duplicated().any():
        duplicates = frame.loc[frame["params"].duplicated(), "params"].tolist()
        raise ValueError(f"params field csv contains duplicated parameters: {duplicates}")

    canonical_names = {name.casefold(): name for name in PARAM_REGISTRY}
    normalized_params: list[str] = []
    unknown: list[str] = []
    for raw_name in frame["params"].tolist():
        original = str(raw_name).strip()
        canonical = canonical_names.get(original.casefold())
        if canonical is None:
            unknown.append(original)
            normalized_params.append(original)
        else:
            normalized_params.append(canonical)
    if unknown:
        raise ValueError(f"params field csv contains unsupported calibration parameters: {unknown}")

    frame = frame.copy()
    frame["params"] = normalized_params
    if frame["params"].duplicated().any():
        duplicates = frame.loc[frame["params"].duplicated(), "params"].tolist()
        raise ValueError(f"params field csv contains duplicated parameters after case normalization: {duplicates}")
    return frame


def load_param_space(params_field_csv: str | Path) -> "OrderedDict[str, tuple[float, float]]":
    """
    Load and validate params_field.csv as an ordered search space.
    """
    frame = _load_param_frame(params_field_csv)
    space: "OrderedDict[str, tuple[float, float]]" = OrderedDict()
    for _, row in frame.iterrows():
        name = str(row["params"])
        try:
            lower = float(row["min"])
            upper = float(row["max"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"parameter {name!r} has non-numeric min/max") from exc
        if not math.isfinite(lower) or not math.isfinite(upper):
            raise ValueError(f"parameter {name!r} has non-finite min/max")
        if lower > upper:
            raise ValueError(f"parameter {name!r} has min greater than max")
        space[name] = (lower, upper)

    if not space:
        raise ValueError("params field csv does not contain any parameters")
    return space


def load_default_params(params_field_csv: str | Path) -> "OrderedDict[str, float]":
    """
    Load warm-start defaults. Prefer calibrated column when present; otherwise use PARAM_REGISTRY defaults.
    """
    frame = _load_param_frame(params_field_csv)
    defaults: "OrderedDict[str, float]" = OrderedDict()
    use_calibrated = "calibrated" in frame.columns
    for _, row in frame.iterrows():
        name = str(row["params"])
        if use_calibrated and not pd.isna(row["calibrated"]):
            try:
                value = float(row["calibrated"])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"parameter {name!r} has non-numeric calibrated value") from exc
            if not math.isfinite(value):
                raise ValueError(f"parameter {name!r} has non-finite calibrated value")
            defaults[name] = value
        else:
            defaults[name] = float(PARAM_REGISTRY[name])
    return defaults


def compute_rmse(sim_tair: np.ndarray, truth_tem: np.ndarray) -> float:
    """
    Compute RMSE while rejecting NaN or mismatched inputs.
    """
    sim = np.asarray(sim_tair, dtype=np.float64)
    truth = np.asarray(truth_tem, dtype=np.float64)
    if sim.shape != truth.shape:
        raise ValueError(f"simulation and truth arrays must have the same shape, got {sim.shape} and {truth.shape}")
    if sim.size == 0:
        raise ValueError("simulation and truth arrays must not be empty")
    diff = sim - truth
    if not np.isfinite(diff).all():
        raise ValueError("simulation or truth arrays contain NaN or infinite values")
    return float(np.sqrt(np.mean(diff * diff)))


def compute_mae(sim_tair: np.ndarray, truth_tem: np.ndarray) -> float:
    """
    Compute MAE while rejecting NaN or mismatched inputs.
    """
    sim = np.asarray(sim_tair, dtype=np.float64)
    truth = np.asarray(truth_tem, dtype=np.float64)
    if sim.shape != truth.shape:
        raise ValueError(f"simulation and truth arrays must have the same shape, got {sim.shape} and {truth.shape}")
    if sim.size == 0:
        raise ValueError("simulation and truth arrays must not be empty")
    diff = sim - truth
    if not np.isfinite(diff).all():
        raise ValueError("simulation or truth arrays contain NaN or infinite values")
    return float(np.mean(np.abs(diff)))


def compute_r2(sim_tair: np.ndarray, truth_tem: np.ndarray) -> float:
    """
    Compute coefficient of determination for the calibrated target.
    """
    sim = np.asarray(sim_tair, dtype=np.float64)
    truth = np.asarray(truth_tem, dtype=np.float64)
    if sim.shape != truth.shape:
        raise ValueError(f"simulation and truth arrays must have the same shape, got {sim.shape} and {truth.shape}")
    if sim.size == 0:
        raise ValueError("simulation and truth arrays must not be empty")
    if not np.isfinite(sim).all() or not np.isfinite(truth).all():
        raise ValueError("simulation or truth arrays contain NaN or infinite values")
    truth_mean = float(np.mean(truth))
    ss_res = float(np.sum((truth - sim) ** 2))
    ss_tot = float(np.sum((truth - truth_mean) ** 2))
    if ss_tot == 0.0:
        return 1.0 if ss_res == 0.0 else 0.0
    return float(1.0 - ss_res / ss_tot)


def build_time_window_hours(start_t: int | None, end_t: int | None) -> list[int] | None:
    """
    Build inclusive hour filters. A window such as 18 -> 6 means 18..23 plus 0..6.
    """
    if start_t is None and end_t is None:
        return None
    if start_t is None or end_t is None:
        raise ValueError("start_t and end_t must be provided together")
    if not 0 <= start_t <= 23:
        raise ValueError("start_t must be in [0, 23]")
    if not 0 <= end_t <= 23:
        raise ValueError("end_t must be in [0, 23]")
    if start_t <= end_t:
        return list(range(start_t, end_t + 1))
    return list(range(start_t, 24)) + list(range(0, end_t + 1))


def _filter_by_hours(frame: pd.DataFrame, hours: list[int] | None) -> pd.DataFrame:
    if hours is None:
        return frame.reset_index(drop=True)
    filtered = frame[frame["t_key"].isin(hours)].reset_index(drop=True)
    if filtered.empty:
        raise ValueError(f"time window {hours} did not match any rows")
    return filtered


def _normalize_column_name(name: Any) -> str:
    return str(name).casefold()


def _column_by_casefold(frame: pd.DataFrame, requested: str) -> str:
    mapping = {_normalize_column_name(column): str(column) for column in frame.columns}
    key = _normalize_column_name(requested)
    if key not in mapping:
        raise ValueError(f"CSV is missing required column {requested!r}")
    return mapping[key]


def _read_observation_csv(path: str | Path, value_column: str) -> pd.DataFrame:
    frame = pd.read_csv(path)
    case_column = _column_by_casefold(frame, "caseId")
    t_column = _column_by_casefold(frame, "t")
    value_name = _column_by_casefold(frame, value_column)
    return pd.DataFrame(
        {
            "caseid_key": frame[case_column].astype(str),
            "t_key": pd.to_numeric(frame[t_column], errors="raise").astype(int),
            "value": pd.to_numeric(frame[value_name], errors="raise").astype(float),
        }
    )


def _load_truth(truth_csv: str | Path, truth_column: str) -> pd.DataFrame:
    return _read_observation_csv(truth_csv, truth_column)


def _load_simulation(output_csv: str | Path, sim_column: str) -> pd.DataFrame:
    return _read_observation_csv(output_csv, sim_column)


def _validate_alignment(sim_frame: pd.DataFrame, truth_frame: pd.DataFrame) -> None:
    if len(sim_frame) != len(truth_frame):
        raise ValueError(f"simulation and truth row counts differ: {len(sim_frame)} != {len(truth_frame)}")
    sim_keys = sim_frame[["caseid_key", "t_key"]].reset_index(drop=True)
    truth_keys = truth_frame[["caseid_key", "t_key"]].reset_index(drop=True)
    if not sim_keys.equals(truth_keys):
        mismatch = (sim_keys != truth_keys).any(axis=1)
        first = int(mismatch.idxmax()) if mismatch.any() else 0
        raise ValueError(
            "simulation and truth caseId/t order differ at row "
            f"{first}: simulation={sim_keys.iloc[first].to_dict()}, truth={truth_keys.iloc[first].to_dict()}"
        )


def _import_optuna():
    try:
        import optuna
        from optuna.samplers import TPESampler
    except ImportError as exc:
        raise RuntimeError(
            "optuna is required to run calibration. Install dependencies with: pip install -r calibration/requirements.txt"
        ) from exc
    return optuna, TPESampler


def _split_case_ids(case_ids: list[str], workers: int) -> list[list[str]]:
    if workers <= 1 or len(case_ids) <= 1:
        return [case_ids]
    worker_count = max(1, min(workers, len(case_ids)))
    base = len(case_ids) // worker_count
    remainder = len(case_ids) % worker_count
    chunks: list[list[str]] = []
    start = 0
    for index in range(worker_count):
        size = base + (1 if index < remainder else 0)
        end = start + size
        chunks.append(case_ids[start:end])
        start = end
    return [chunk for chunk in chunks if chunk]


def _run_csv_chunk(
    input_csv: str,
    output_csv: str,
    params: dict[str, float],
    preheat: int,
) -> str:
    settings = settingInfo(**params)
    return csvCalculation(
        input_csv,
        output_csv,
        settings=settings,
        preheat=preheat,
        show_progress=False,
    )


def _run_trial_csv_calculation(
    input_csv: str | Path,
    output_csv: str | Path,
    params: dict[str, float],
    preheat: int,
    workers: int,
    trial_workspace: str | Path,
) -> Path:
    input_path = Path(input_csv)
    output_path = Path(output_csv)
    workspace = Path(trial_workspace)
    workspace.mkdir(parents=True, exist_ok=True)

    if workers <= 1:
        _run_csv_chunk(str(input_path), str(output_path), params, preheat)
        return output_path

    data_frame = pd.read_csv(input_path)
    case_column = _column_by_casefold(data_frame, "caseId")
    case_ids = [str(value) for value in data_frame[case_column].drop_duplicates().tolist()]
    case_chunks = _split_case_ids(case_ids, workers)
    if len(case_chunks) <= 1:
        _run_csv_chunk(str(input_path), str(output_path), params, preheat)
        return output_path

    chunk_inputs: list[Path] = []
    chunk_outputs: list[Path] = []
    for chunk_index, chunk_case_ids in enumerate(case_chunks):
        chunk_input = workspace / f"chunk_{chunk_index:03d}_input.csv"
        chunk_output = workspace / f"chunk_{chunk_index:03d}_output.csv"
        chunk_frame = data_frame[data_frame[case_column].astype(str).isin(chunk_case_ids)].copy()
        chunk_frame.to_csv(chunk_input, index=False)
        chunk_inputs.append(chunk_input)
        chunk_outputs.append(chunk_output)

    max_workers = min(len(case_chunks), workers, os.cpu_count() or workers)
    with concurrent.futures.ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(_run_csv_chunk, str(chunk_input), str(chunk_output), params, preheat)
            for chunk_input, chunk_output in zip(chunk_inputs, chunk_outputs)
        ]
        for future in futures:
            future.result()

    merged_frames = [pd.read_csv(chunk_output) for chunk_output in chunk_outputs]
    merged = pd.concat(merged_frames, ignore_index=True)
    merged.to_csv(output_path, index=False)
    return output_path


def _json_default(value: Any) -> Any:
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    return value


def _sanitize_tag(value: str) -> str:
    allowed = []
    for char in value:
        if char.isalnum() or char in {"_", "-"}:
            allowed.append(char)
        else:
            allowed.append("_")
    return "".join(allowed).strip("_") or "study"


def _create_log_dir(logs_root: str | Path, study_name: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_name = f"{_sanitize_tag(study_name)}_{timestamp}"
    log_dir = Path(logs_root) / run_name
    log_dir.mkdir(parents=True, exist_ok=False)
    (log_dir / "trial_outputs").mkdir(parents=True, exist_ok=True)
    return log_dir


def _write_run_metadata(
    log_dir: Path,
    *,
    study_name: str,
    input_csv: Path,
    truth_csv: Path,
    params_field_csv: Path,
    output_csv: Path,
    preheat: int,
    n_trials: int,
    n_startup_trials: int,
    workers: int,
    start_t: int | None,
    end_t: int | None,
    loss_hours: list[int] | None,
    seed: int,
    storage: str | None,
    truth_column: str,
    sim_column: str,
    param_space: "OrderedDict[str, tuple[float, float]]",
) -> None:
    metadata = {
        "study_name": study_name,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "input_csv": str(input_csv.resolve()),
        "truth_csv": str(truth_csv.resolve()),
        "params_field_csv": str(params_field_csv.resolve()),
        "output_csv": str(output_csv.resolve()),
        "preheat": preheat,
        "n_trials": n_trials,
        "n_startup_trials": n_startup_trials,
        "workers": workers,
        "start_t": start_t,
        "end_t": end_t,
        "loss_hours": loss_hours,
        "seed": seed,
        "storage": storage,
        "truth_column": truth_column,
        "sim_column": sim_column,
        "param_space": {name: {"min": lower, "max": upper} for name, (lower, upper) in param_space.items()},
    }
    (log_dir / "run_metadata.json").write_text(json.dumps(metadata, ensure_ascii=True, indent=2), encoding="utf-8")


def _append_trial_log(log_dir: Path, record: dict[str, Any]) -> None:
    log_file = log_dir / "trial_log.jsonl"
    with log_file.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=True, default=_json_default) + "\n")


def _format_metric(value: float | None) -> str:
    if value is None:
        return "NA"
    if not math.isfinite(float(value)):
        return "inf"
    return f"{float(value):.6f}"


def _print_trial_progress(
    trial_number: int,
    rmse: float | None,
    best_rmse: float | None,
    best_mae: float | None,
    best_r2: float | None,
) -> None:
    print(
        f"TPE SAMPLING {trial_number}: "
        f"RMSE={_format_metric(rmse)} "
        f"BEST_RMSE={_format_metric(best_rmse)} "
        f"BEST_MAE={_format_metric(best_mae)} "
        f"BEST_R2={_format_metric(best_r2)}",
        flush=True,
    )


def _print_startup_progress(completed: int, total: int) -> None:
    print(f"STARTUP SAMPLING {completed}/{total} completed", flush=True)


def _export_trial_summary_csv(log_dir: Path) -> None:
    log_file = log_dir / "trial_log.jsonl"
    if not log_file.exists():
        return
    records = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not records:
        return

    summary_rows: list[dict[str, Any]] = []
    for record in records:
        row = {
            "trial_number": record["trial_number"],
            "status": record["status"],
            "rmse": record.get("rmse"),
            "mae": record.get("mae"),
            "r2": record.get("r2"),
            "best_rmse": record.get("best_rmse"),
            "best_mae": record.get("best_mae"),
            "best_r2": record.get("best_r2"),
            "output_csv": record.get("output_csv"),
            "failure": record.get("failure"),
        }
        params = record.get("sampled_params", {})
        for name, value in params.items():
            row[f"param_{name}"] = value
        summary_rows.append(row)
    pd.DataFrame(summary_rows).to_csv(log_dir / "trial_summary.csv", index=False)


def _build_stats_frame(
    params_field_csv: str | Path,
    best_params: dict[str, float],
    completed_trials: list[dict[str, float]],
) -> pd.DataFrame:
    params_frame = _load_param_frame(params_field_csv).copy()
    stats_by_param: dict[str, dict[str, float]] = {}
    for name in params_frame["params"].tolist():
        values = np.asarray([float(trial[name]) for trial in completed_trials if name in trial], dtype=np.float64)
        if values.size == 0:
            raise RuntimeError(f"no completed trial statistics available for parameter {name!r}")
        stats_by_param[name] = {
            "calibrated": float(best_params[name]),
            "mean": float(np.mean(values)),
            "std": float(np.std(values, ddof=0)),
            "p05": float(np.quantile(values, 0.05)),
            "p50": float(np.quantile(values, 0.50)),
            "p95": float(np.quantile(values, 0.95)),
        }

    for column in RESULT_COLUMNS[3:]:
        params_frame[column] = params_frame["params"].map(lambda name: stats_by_param[str(name)][column])
    return params_frame[RESULT_COLUMNS]


def _successful_trials(study) -> list[Any]:
    return [
        trial
        for trial in study.trials
        if trial.state.name == "COMPLETE"
        and trial.value is not None
        and math.isfinite(float(trial.value))
        and trial.params
    ]


def _write_best_outputs(
    *,
    study,
    params_path: Path,
    output_path: Path,
    log_dir: Path,
    interrupted: bool,
) -> Path:
    successful = _successful_trials(study)
    if successful:
        best_trial = min(successful, key=lambda trial: float(trial.value))
        best_params = {name: float(value) for name, value in best_trial.params.items()}
        completed_trials = [{name: float(value) for name, value in trial.params.items()} for trial in successful]
        summary = {
            "best_trial_number": int(best_trial.number),
            "best_rmse": float(best_trial.value),
            "best_mae": best_trial.user_attrs.get("mae"),
            "best_r2": best_trial.user_attrs.get("r2"),
            "best_output_csv": best_trial.user_attrs.get("output_csv"),
            "result_csv": str(output_path.resolve()),
            "log_dir": str(log_dir.resolve()),
            "interrupted": interrupted,
        }
    else:
        if not interrupted:
            raise RuntimeError(f"no successful calibration trials were recorded; see logs under {log_dir}")
        warm_start_file = log_dir / "warm_start_params.json"
        best_params = {
            name: float(value)
            for name, value in json.loads(warm_start_file.read_text(encoding="utf-8")).items()
        }
        completed_trials = [best_params]
        summary = {
            "best_trial_number": None,
            "best_rmse": None,
            "best_mae": None,
            "best_r2": None,
            "best_output_csv": None,
            "result_csv": str(output_path.resolve()),
            "log_dir": str(log_dir.resolve()),
            "interrupted": interrupted,
            "source": "warm_start_params.json",
            "note": "Interrupted before any successful trial completed; output uses warm-start parameters.",
        }

    result_frame = _build_stats_frame(params_path, best_params, completed_trials)
    result_frame.to_csv(output_path, index=False)
    (log_dir / "best_summary.json").write_text(json.dumps(summary, ensure_ascii=True, indent=2), encoding="utf-8")
    return output_path


def _build_default_trial(
    param_space: "OrderedDict[str, tuple[float, float]]",
    default_params: "OrderedDict[str, float]",
) -> dict[str, float]:
    default_trial: dict[str, float] = {}
    out_of_bounds: list[str] = []
    for name, (lower, upper) in param_space.items():
        value = float(default_params[name])
        if lower <= value <= upper:
            default_trial[name] = value
        else:
            out_of_bounds.append(f"{name}={value} not in [{lower}, {upper}]")
    if out_of_bounds:
        raise ValueError(
            "default/calibrated warm-start values must be inside params_field min/max bounds: "
            + "; ".join(out_of_bounds)
        )
    return default_trial


def _cleanup_tmp_dir(tmp_dir: Path) -> None:
    if not tmp_dir.exists():
        return
    for child in tmp_dir.iterdir():
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)


def run_calibration(
    params_field_csv: str | Path,
    input_csv: str | Path,
    truth_csv: str | Path,
    output_csv: str | Path,
    *,
    n_trials: int = 200,
    n_startup_trials: int = 40,
    preheat: int = 3,
    workers: int = 1,
    start_t: int | None = None,
    end_t: int | None = None,
    study_name: str = "uhi_csv_calibration",
    storage: str | None = "sqlite:///calibration/calibration.db",
    truth_column: str = "TEM",
    sim_column: str = "Tair",
    tmp_dir: str | Path = "calibration/tmp",
    logs_root: str | Path = "calibration/logs",
    seed: int = 42,
) -> Path:
    """
    Run Optuna calibration and export params,min,max plus point and distribution statistics.
    """
    optuna, TPESampler = _import_optuna()
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    if hasattr(optuna, "exceptions") and hasattr(optuna.exceptions, "ExperimentalWarning"):
        warnings.filterwarnings("ignore", category=optuna.exceptions.ExperimentalWarning)
    param_space = load_param_space(params_field_csv)
    default_params = load_default_params(params_field_csv)
    loss_hours = build_time_window_hours(start_t, end_t)
    truth_frame_full = _load_truth(truth_csv, truth_column)
    truth_frame = _filter_by_hours(truth_frame_full, loss_hours)
    truth_values = truth_frame["value"].to_numpy()
    input_path = Path(input_csv)
    params_path = Path(params_field_csv)
    truth_path = Path(truth_csv)
    output_path = Path(output_csv)
    tmp_path = Path(tmp_dir)
    log_dir = _create_log_dir(logs_root, study_name)
    tmp_path.mkdir(parents=True, exist_ok=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    pd.read_csv(params_path).to_csv(log_dir / "params_field.csv", index=False)
    _write_run_metadata(
        log_dir,
        study_name=study_name,
        input_csv=input_path,
        truth_csv=truth_path,
        params_field_csv=params_path,
        output_csv=output_path,
        preheat=preheat,
        n_trials=n_trials,
        n_startup_trials=n_startup_trials,
        workers=workers,
        start_t=start_t,
        end_t=end_t,
        loss_hours=loss_hours,
        seed=seed,
        storage=storage,
        truth_column=truth_column,
        sim_column=sim_column,
        param_space=param_space,
    )
    (log_dir / "warm_start_params.json").write_text(
        json.dumps({name: float(default_params[name]) for name in param_space}, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )

    sampler = TPESampler(
        n_startup_trials=n_startup_trials,
        multivariate=True,
        group=True,
        seed=seed,
    )
    study = optuna.create_study(
        study_name=study_name,
        direction="minimize",
        sampler=sampler,
        storage=storage,
        load_if_exists=True,
    )

    default_trial = _build_default_trial(param_space, default_params)
    study.enqueue_trial(default_trial, skip_if_exists=False)

    startup_total = max(0, int(n_startup_trials))
    startup_completed_before_run = sum(1 for trial in study.trials if trial.state.name == "COMPLETE")
    startup_remaining_at_start = max(0, startup_total - startup_completed_before_run)
    if startup_remaining_at_start > 0:
        print(
            "STARTUP PHASE BEGIN: "
            f"{startup_remaining_at_start} startup trial(s) remaining "
            f"(target={startup_total}, completed_before_run={startup_completed_before_run})",
            flush=True,
        )
    else:
        print(
            "STARTUP PHASE SKIPPED: "
            f"already satisfied before this run "
            f"(target={startup_total}, completed_before_run={startup_completed_before_run})",
            flush=True,
        )

    state: dict[str, Any] = {
        "best_rmse": float("inf"),
        "best_mae": None,
        "best_r2": None,
        "completed_trials_in_run": 0,
        "startup_end_printed": startup_remaining_at_start == 0,
    }

    def objective(trial) -> float:
        params = {
            name: trial.suggest_float(name, lower, upper)
            for name, (lower, upper) in param_space.items()
        }
        tmp_output = tmp_path / f"trial_{trial.number:04d}_{uuid.uuid4().hex[:8]}.csv"
        trial_workspace = tmp_path / f"trial_{trial.number:04d}_{uuid.uuid4().hex[:8]}"
        log_output = log_dir / "trial_outputs" / f"trial_{trial.number:04d}.csv"

        record: dict[str, Any] = {
            "trial_number": int(trial.number),
            "status": "failed",
            "search_space": {name: {"min": lower, "max": upper} for name, (lower, upper) in param_space.items()},
            "sampled_params": params,
            "output_csv": str(log_output.resolve()),
            "rmse": None,
            "mae": None,
            "r2": None,
            "best_rmse": state["best_rmse"] if math.isfinite(state["best_rmse"]) else None,
            "best_mae": state["best_mae"],
            "best_r2": state["best_r2"],
            "failure": None,
        }

        try:
            _run_trial_csv_calculation(
                input_path,
                str(tmp_output),
                params,
                preheat,
                workers,
                trial_workspace,
            )
            tmp_output.replace(log_output)
            sim_frame_full = _load_simulation(log_output, sim_column)
            _validate_alignment(sim_frame_full, truth_frame_full)
            sim_frame = _filter_by_hours(sim_frame_full, loss_hours)
            _validate_alignment(sim_frame, truth_frame)
            sim_values = sim_frame["value"].to_numpy()
            rmse = compute_rmse(sim_values, truth_values)
            mae = compute_mae(sim_values, truth_values)
            r2 = compute_r2(sim_values, truth_values)

            if rmse < state["best_rmse"]:
                state["best_rmse"] = rmse
                state["best_mae"] = mae
                state["best_r2"] = r2

            record["status"] = "completed"
            record["rmse"] = rmse
            record["mae"] = mae
            record["r2"] = r2
            record["best_rmse"] = state["best_rmse"]
            record["best_mae"] = state["best_mae"]
            record["best_r2"] = state["best_r2"]
            trial.set_user_attr("mae", mae)
            trial.set_user_attr("r2", r2)
            trial.set_user_attr("output_csv", str(log_output.resolve()))
            return rmse
        except Exception as exc:
            record["failure"] = repr(exc)
            trial.set_user_attr("failure", repr(exc))
            return float("inf")
        finally:
            _append_trial_log(log_dir, record)
            _print_trial_progress(
                int(trial.number),
                record["rmse"],
                record["best_rmse"],
                record["best_mae"],
                record["best_r2"],
            )

            state["completed_trials_in_run"] += 1
            startup_completed_after_this_trial = startup_completed_before_run + int(state["completed_trials_in_run"])
            if startup_total > 0 and startup_completed_after_this_trial <= startup_total:
                _print_startup_progress(startup_completed_after_this_trial, startup_total)
            if (
                startup_total > 0
                and not bool(state["startup_end_printed"])
                and startup_completed_after_this_trial >= startup_total
            ):
                print(
                    f"STARTUP PHASE END: switching to TPE-guided sampling after {startup_total} startup trial(s)",
                    flush=True,
                )
                state["startup_end_printed"] = True

            tmp_output.unlink(missing_ok=True)
            if trial_workspace.exists():
                for child in trial_workspace.iterdir():
                    child.unlink(missing_ok=True)
                trial_workspace.rmdir()

    interrupted = False
    try:
        try:
            study.optimize(objective, n_trials=n_trials)
        except KeyboardInterrupt:
            interrupted = True
            print("Calibration interrupted by Ctrl+C; writing current best result before exit.", flush=True)
        finally:
            _export_trial_summary_csv(log_dir)

        return _write_best_outputs(
            study=study,
            params_path=params_path,
            output_path=output_path,
            log_dir=log_dir,
            interrupted=interrupted,
        )
    finally:
        _cleanup_tmp_dir(tmp_path)


def _validate_nonempty_path(name: str, value: str) -> None:
    if not str(value).strip():
        raise ValueError(f"{name} must be a non-empty path in the script configuration block")


def main() -> int:
    _validate_nonempty_path("RUN_PARAMS_FIELD_CSV", RUN_PARAMS_FIELD_CSV)
    _validate_nonempty_path("RUN_INPUT_CSV", RUN_INPUT_CSV)
    _validate_nonempty_path("RUN_TRUTH_CSV", RUN_TRUTH_CSV)
    _validate_nonempty_path("RUN_OUTPUT_CSV", RUN_OUTPUT_CSV)

    output = run_calibration(
        RUN_PARAMS_FIELD_CSV,
        RUN_INPUT_CSV,
        RUN_TRUTH_CSV,
        RUN_OUTPUT_CSV,
        n_trials=RUN_N_TRIALS,
        n_startup_trials=RUN_N_STARTUP_TRIALS,
        preheat=RUN_PREHEAT,
        workers=RUN_WORKERS,
        start_t=RUN_START_T,
        end_t=RUN_END_T,
        study_name=RUN_STUDY_NAME,
        storage=RUN_STORAGE,
        truth_column=RUN_TRUTH_COLUMN,
        sim_column=RUN_SIM_COLUMN,
        tmp_dir=RUN_TMP_DIR,
        logs_root=RUN_LOGS_ROOT,
        seed=RUN_SEED,
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
