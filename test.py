"""Run the bundled 24-hour CSV simulation: python test.py (Python 3.12+)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from UHI.csv_calculator import csvCalculation


PROJECT_ROOT = Path(__file__).resolve().parent


def runCsvCalculationTest() -> Path:
    """Simulate the bundled case, validate it, and print all hourly results."""
    input_csv = PROJECT_ROOT / "input_2026_05_27.csv"
    output_csv = PROJECT_ROOT / "outputs" / "csv" / "output.csv"
    output_csv.parent.mkdir(parents=True, exist_ok=True)

    print(f"Input CSV: {input_csv}", flush=True)
    print("Running 24-hour UHI simulation (preheat=1)...", flush=True)
    csvCalculation(str(input_csv), str(output_csv), show_progress=False, preheat=1)

    result = pd.read_csv(output_csv)
    expected_columns = ["caseid", "t", "QS", "QL", "QV", "QA", "Tair", "Tsurface", "Tambient"]
    if result.columns.tolist() != expected_columns:
        raise AssertionError(f"Unexpected output columns: {result.columns.tolist()}")
    if len(result) != 24:
        raise AssertionError(f"Expected 24 hourly rows, got {len(result)}")
    if result["caseid"].isna().any() or result["caseid"].nunique() != 1:
        raise AssertionError("Expected exactly one non-empty simulation case")
    if result["t"].tolist() != list(range(24)):
        raise AssertionError("Expected each hour 0..23 exactly once, in order")
    if not np.isfinite(result[expected_columns[2:]].to_numpy(dtype=float)).all():
        raise AssertionError("CSV calculation output contains NaN or infinite values")

    print(result.to_string(index=False))
    print(f"Mean air temperature: {result['Tair'].mean():.3f} C")
    print(f"Mean air temperature difference: {(result['Tair'] - result['Tambient']).mean():.3f} C")
    print(f"CSV test output: {output_csv}")
    print("PASS: 24 hourly results validated.")
    return output_csv


if __name__ == "__main__":
    runCsvCalculationTest()

