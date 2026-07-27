from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from UHI.calculator import calculate
from UHI.csv_calculator import (
    _buildClimateInfo,
    _buildColumnMap,
    _buildSiteInfo,
    _validateCaseHours,
    csvCalculation,
)

WEATHER_CSV = PROJECT_ROOT / "workplace" / "data" / "weather_guangzhou" / "selected_weather.csv"
TEMPLATE_INPUT_CSV = PROJECT_ROOT / "test" / "data" / "csv" / "input_100_first24.csv"
TARGET_DATE = (2026, 5, 27)


def _buildMayInputCsv(output_csv: Path) -> Path:
    """
    Generate a 24-hour CSV input using Guangzhou weather on or near 2026-05-27.
    """
    weather = pd.read_csv(WEATHER_CSV)
    template = pd.read_csv(TEMPLATE_INPUT_CSV)

    candidate_dates = [
        TARGET_DATE,
        (2026, 5, 26),
        (2026, 5, 28),
        (2026, 5, 25),
    ]
    selected_weather = None
    selected_date = None
    for year, month, day in candidate_dates:
        day_weather = weather[
            (weather["year"] == year) & (weather["mon"] == month) & (weather["day"] == day)
        ].sort_values("hour")
        if len(day_weather) == 24:
            selected_weather = day_weather.reset_index(drop=True)
            selected_date = (year, month, day)
            break
    if selected_weather is None:
        raise AssertionError("Could not find a complete 24-hour record near 2026-05-27")

    result = template.copy()
    result["caseId"] = f"{selected_date[0]:04d}_{selected_date[1]:02d}_{selected_date[2]:02d}"
    result["t"] = selected_weather["hour"].to_numpy(dtype=int)
    result["Tb"] = selected_weather["TEM"].to_numpy(dtype=float)
    result["Ta"] = selected_weather["TEM"].to_numpy(dtype=float)
    result["Tg"] = selected_weather["TEM"].to_numpy(dtype=float)
    result["Velocity"] = selected_weather["VELOCITY"].to_numpy(dtype=float)
    result["RadTotal"] = selected_weather["RADGHI"].to_numpy(dtype=float)
    result["RadDiffuse"] = selected_weather["RADDIF"].to_numpy(dtype=float)
    result["Rh"] = selected_weather["RHU"].to_numpy(dtype=float)
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(output_csv, index=False)
    return output_csv


def _runDetailedCalculation(input_csv: Path):
    """
    Re-run the calculator path directly so the test can print internal Tg partitions.
    """
    data_frame = pd.read_csv(input_csv)
    column_map = _buildColumnMap(data_frame.columns)
    case_id_column = column_map["caseid"]
    t_column = column_map["t"]
    case_id = data_frame[case_id_column].iloc[0]
    case_frame = data_frame[data_frame[case_id_column] == case_id]
    sorted_case_frame = _validateCaseHours(case_id, case_frame, t_column)
    site = _buildSiteInfo(case_id, sorted_case_frame, column_map)
    climate = _buildClimateInfo(sorted_case_frame, column_map)
    return calculate(
        np.array([climate], dtype=object),
        np.array([site], dtype=object),
        show_progress=False,
        preheat=1,
    )


def runCsvCalculationTest() -> Path:
    """
    Run the 24-hour CSV calculator test case and validate the exported columns.
    :return: path to the generated CSV output.
    """
    input_csv = PROJECT_ROOT / "test" / "output" / "csv" / "input_2026_05_27.csv"
    output_dir = PROJECT_ROOT / "test" / "output" / "csv"
    output_csv = output_dir / "output.csv"
    output_dir.mkdir(parents=True, exist_ok=True)

    _buildMayInputCsv(input_csv)
    csvCalculation(str(input_csv), str(output_csv), show_progress=False, preheat=1)

    result = pd.read_csv(output_csv)
    expected_columns = ["caseid", "t", "QS", "QL", "QV", "QA", "Tair", "Tsurface", "Tambient"]
    if result.columns.tolist() != expected_columns:
        raise AssertionError(f"Unexpected output columns: {result.columns.tolist()}")
    if len(result) != 24:
        raise AssertionError(f"Expected 24 hourly rows, got {len(result)}")
    if result[expected_columns[2:]].isna().any().any():
        raise AssertionError("CSV calculation output contains NaN values")

    detailed = _runDetailedCalculation(input_csv)
    print_frame = result.copy()
    print_frame["Delta_T"] = print_frame["Tair"] - print_frame["Tambient"]
    print_frame["TgLand"] = detailed["TgLand"][:, 0]
    print_frame["TgWater"] = detailed["TgWater"][:, 0]
    print_frame["TgGround"] = detailed["TgGround"][:, 0]
    

    print(print_frame.to_string(index=False))
    print(f"CSV test output: {output_csv}")
    return output_csv


if __name__ == "__main__":
    runCsvCalculationTest()
