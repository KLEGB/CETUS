from __future__ import annotations

from pathlib import Path
import sys

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from RAD.workflow import (
    build_weather_matrix,
    compute_building_radiation,
    write_radiation_schedules,
)

EPW_PATH = REPO_ROOT / "workplace" / "epw" / "CHN_Guangdong.Guangzhou.592870_CSWD.epw"
SHP_PATH = REPO_ROOT / "workplace" / "_cityJsonExport" / "shp" / "Pazhou.shp"
EXPECTED_BUILDING_COUNT = 208


def main() -> None:
    weather_path = build_weather_matrix(EPW_PATH)
    weather_matrix = np.load(weather_path)
    assert weather_matrix.shape == (145, 8760), weather_matrix.shape

    rad_path = compute_building_radiation(SHP_PATH, weather_path, sample=1.0, seed=42)
    rad_data = np.load(rad_path, allow_pickle=False)
    for key in ("N", "S", "W", "E", "T"):
        assert key in rad_data.files, rad_data.files
        assert rad_data[key].shape == (EXPECTED_BUILDING_COUNT, 8760), (key, rad_data[key].shape)
        assert np.all(rad_data[key] >= 0.0), key

    schedule_dir = write_radiation_schedules(SHP_PATH, rad_path)
    schedule_files = sorted(schedule_dir.glob("*.sch"))
    assert len(schedule_files) == EXPECTED_BUILDING_COUNT, len(schedule_files)

    first_text = schedule_files[0].read_text(encoding="utf-8").splitlines()
    assert len(first_text) == 5, len(first_text)
    hourly_lines = [line for line in first_text if ",Hourly," in line]
    assert len(hourly_lines) == 2, hourly_lines
    for line in hourly_lines:
        assert len(line.split(",")) == 8762, line[:120]
    assert hourly_lines[0].split(",")[2:] == hourly_lines[1].split(",")[2:]

    sampled_path_a = compute_building_radiation(SHP_PATH, weather_path, sample=0.25, seed=42)
    sampled_data_a = np.load(sampled_path_a, allow_pickle=False)
    sampled_path_b = compute_building_radiation(SHP_PATH, weather_path, sample=0.25, seed=42)
    sampled_data_b = np.load(sampled_path_b, allow_pickle=False)
    for key in ("N", "S", "W", "E", "T"):
        assert sampled_data_a[key].shape == (EXPECTED_BUILDING_COUNT, 8760), (key, sampled_data_a[key].shape)
        assert np.all(sampled_data_a[key] >= 0.0), key
        assert np.array_equal(sampled_data_a[key], sampled_data_b[key]), key

    try:
        compute_building_radiation(SHP_PATH, weather_path, sample=0.0)
    except ValueError:
        pass
    else:
        raise AssertionError("sample=0.0 must raise ValueError")

    try:
        compute_building_radiation(SHP_PATH, weather_path, sample=1.1)
    except ValueError:
        pass
    else:
        raise AssertionError("sample=1.1 must raise ValueError")

    print(weather_path)
    print(rad_path)
    print(schedule_dir)
    print("RAD workflow test passed")


if __name__ == "__main__":
    main()
