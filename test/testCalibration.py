from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from calibration.calibrate import (
    PARAM_REGISTRY,
    _load_truth,
    _validate_alignment,
    build_time_window_hours,
    generate_params_field,
    load_default_params,
    load_param_space,
    run_calibration,
)
from UHI.csv_calculator import csvCalculation
from UHI.modelInfo import settingInfo


def testParamsFieldGeneration(tmp_dir: Path) -> Path:
    params_csv = tmp_dir / "params_field.csv"
    generate_params_field(params_csv)

    frame = pd.read_csv(params_csv)
    if frame.columns.tolist() != ["params", "min", "max"]:
        raise AssertionError(f"Unexpected params field columns: {frame.columns.tolist()}")
    if len(frame) != len(PARAM_REGISTRY):
        raise AssertionError(f"Expected {len(PARAM_REGISTRY)} calibration parameters, got {len(frame)}")
    forbidden = {"qlx", "conductionMode"}
    present_forbidden = forbidden.intersection(set(frame["params"].tolist()))
    if present_forbidden:
        raise AssertionError(f"Forbidden parameters were generated: {sorted(present_forbidden)}")
    numeric_slots = set(PARAM_REGISTRY)
    generated_slots = set(frame["params"].tolist())
    if generated_slots != numeric_slots:
        missing = sorted(numeric_slots - generated_slots)
        extra = sorted(generated_slots - numeric_slots)
        raise AssertionError(f"Generated params do not match numeric settingInfo slots. missing={missing}, extra={extra}")
    if "rsground" in settingInfo.__slots__ and "rsground" not in generated_slots:
        raise AssertionError("Expected dynamic settingInfo parameter 'rsground' in params field")

    space = load_param_space(params_csv)
    if list(space.keys()) != list(PARAM_REGISTRY.keys()):
        raise AssertionError("Generated search space order does not match PARAM_REGISTRY")
    return params_csv


def testAlignmentError(tmp_dir: Path) -> None:
    truth_csv = tmp_dir / "truth_bad.csv"
    pd.DataFrame({"caseId": ["a", "a"], "t": [0, 1], "TEM": [1.0, 2.0]}).to_csv(truth_csv, index=False)
    truth = _load_truth(truth_csv, "TEM")
    sim = pd.DataFrame({"caseid_key": ["a", "a"], "t_key": [1, 0], "value": [1.0, 2.0]})
    try:
        _validate_alignment(sim, truth)
    except ValueError as exc:
        if "caseId/t order differ" not in str(exc):
            raise AssertionError(f"Unexpected alignment error: {exc}") from exc
        return
    raise AssertionError("Expected alignment mismatch to raise ValueError")


def testCalibratedWarmStart(tmp_dir: Path, params_csv: Path) -> None:
    warm_start_csv = tmp_dir / "params_field_with_calibrated.csv"
    frame = pd.read_csv(params_csv)
    frame["calibrated"] = frame["min"]
    frame.to_csv(warm_start_csv, index=False)

    defaults = load_default_params(warm_start_csv)
    for _, row in frame.iterrows():
        name = str(row["params"])
        expected = float(row["min"])
        actual = float(defaults[name])
        if actual != expected:
            raise AssertionError(f"Expected calibrated warm start {expected} for {name}, got {actual}")


def testParamAliasNormalization(tmp_dir: Path, params_csv: Path) -> None:
    if "rsground" not in PARAM_REGISTRY:
        return
    alias_csv = tmp_dir / "params_field_alias.csv"
    frame = pd.read_csv(params_csv)
    frame.loc[frame["params"] == "rsground", "params"] = "rsgrond"
    frame.to_csv(alias_csv, index=False)

    space = load_param_space(alias_csv)
    if "rsground" not in space:
        raise AssertionError("Expected rsgrond alias to normalize to rsground")
    if "rsgrond" in space:
        raise AssertionError("Expected rsgrond alias not to remain in loaded param space")


def testTimeWindowHours() -> None:
    if build_time_window_hours(None, None) is not None:
        raise AssertionError("Expected no time filter when start_t/end_t are omitted")
    if build_time_window_hours(3, 5) != [3, 4, 5]:
        raise AssertionError("Expected non-cross-day time window 3..5")
    if build_time_window_hours(18, 6) != [18, 19, 20, 21, 22, 23, 0, 1, 2, 3, 4, 5, 6]:
        raise AssertionError("Expected cross-day time window 18..23 plus 0..6")


def testOneTrialSmoke(tmp_dir: Path, params_csv: Path) -> None:
    if importlib.util.find_spec("optuna") is None:
        print("Skipping one-trial calibration smoke test because optuna is not installed.")
        return

    source_input_csv = PROJECT_ROOT / "test" / "data" / "csv" / "input_100_first24.csv"
    input_csv = tmp_dir / "input_two_cases.csv"
    baseline_csv = tmp_dir / "baseline.csv"
    truth_csv = tmp_dir / "truth.csv"
    output_csv = tmp_dir / "calibrated_params.csv"
    logs_root = tmp_dir / "logs"
    source_frame = pd.read_csv(source_input_csv)
    second_case = source_frame.copy()
    second_case["caseId"] = "02_02_24"
    combined_input = pd.concat([source_frame, second_case], ignore_index=True)
    combined_input.to_csv(input_csv, index=False)

    csvCalculation(str(input_csv), str(baseline_csv), preheat=1, show_progress=False)
    baseline = pd.read_csv(baseline_csv)
    pd.DataFrame({"caseId": baseline["caseid"], "t": baseline["t"], "TEM": baseline["Tair"]}).to_csv(truth_csv, index=False)

    run_calibration(
        params_csv,
        input_csv,
        truth_csv,
        output_csv,
        n_trials=1,
        n_startup_trials=1,
        preheat=1,
        workers=2,
        start_t=18,
        end_t=6,
        storage=f"sqlite:///{tmp_dir / 'calibration.db'}",
        tmp_dir=tmp_dir / "tmp",
        logs_root=logs_root,
    )

    result = pd.read_csv(output_csv)
    if result.columns.tolist() != ["params", "min", "max", "calibrated", "mean", "std", "p05", "p50", "p95"]:
        raise AssertionError(f"Unexpected calibrated output columns: {result.columns.tolist()}")
    if result["calibrated"].isna().any():
        raise AssertionError("Calibrated output contains empty values")
    if result[["mean", "std", "p05", "p50", "p95"]].isna().any().any():
        raise AssertionError("Calibration statistics contain empty values")

    log_dirs = list(logs_root.iterdir())
    if len(log_dirs) != 1:
        raise AssertionError(f"Expected exactly one log directory, got {len(log_dirs)}")
    log_dir = log_dirs[0]
    required_paths = [
        log_dir / "params_field.csv",
        log_dir / "warm_start_params.json",
        log_dir / "run_metadata.json",
        log_dir / "trial_log.jsonl",
        log_dir / "trial_summary.csv",
        log_dir / "current_best.json",
        log_dir / "best_summary.json",
    ]
    for path in required_paths:
        if not path.exists():
            raise AssertionError(f"Missing expected calibration log artifact: {path}")

    trial_outputs = list((log_dir / "trial_outputs").glob("*.csv"))
    if len(trial_outputs) != 1:
        raise AssertionError(f"Expected one logged trial output CSV, got {len(trial_outputs)}")

    best_summary = json.loads((log_dir / "best_summary.json").read_text(encoding="utf-8"))
    if best_summary["best_rmse"] != 0.0:
        raise AssertionError(f"Expected zero RMSE for truth-equals-simulation smoke test, got {best_summary['best_rmse']}")

    warm_start = json.loads((log_dir / "warm_start_params.json").read_text(encoding="utf-8"))
    first_trial = json.loads((log_dir / "trial_log.jsonl").read_text(encoding="utf-8").splitlines()[0])
    for name, value in warm_start.items():
        sampled = float(first_trial["sampled_params"][name])
        if sampled != float(value):
            raise AssertionError(f"Expected first trial to use warm-start value for {name}, got {sampled} != {value}")


def runCalibrationTests() -> None:
    tmp_dir = Path(tempfile.mkdtemp(prefix="moosas_calibration_test_"))
    try:
        params_csv = testParamsFieldGeneration(tmp_dir)
        testAlignmentError(tmp_dir)
        testCalibratedWarmStart(tmp_dir, params_csv)
        testParamAliasNormalization(tmp_dir, params_csv)
        testTimeWindowHours()
        testOneTrialSmoke(tmp_dir, params_csv)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    print("Calibration tests passed.")


if __name__ == "__main__":
    runCalibrationTests()
