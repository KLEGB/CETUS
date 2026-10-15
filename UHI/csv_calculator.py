from __future__ import annotations

import os
import argparse
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

if __package__ in {None, ""}:
    import sys
    sys.path.append(str(Path(__file__).resolve().parents[1]))
    from UHI.modelInfo import ClimateInfo, SiteInfo, settingInfo, listObjToOptionalList
    from UHI.calculator import calculate
else:
    from .modelInfo import ClimateInfo, SiteInfo, settingInfo, listObjToOptionalList
    from .calculator import calculate

CSV_OUTPUT_COLUMNS = ['caseid', 't', 'QS', 'QL', 'QV', 'QA', 'Tair', 'Tsurface', 'Tambient']

SITE_ARRAY_SLOTS = set()
SITE_INIT_AREA_SLOTS = ('AreaBuilding', 'AreaRoad', 'AreaGround', 'AreaLandscape', 'AreaWater')
CASE_ID_KEY = 'caseid'
T_KEY = 't'

DEFAULT_SITE = SiteInfo()
DEFAULT_CLIMATE = ClimateInfo()


def _relative_humidity_from_ratio(humidity_ratio: float, dry_bulb_temperature: float, pressure_pa: float) -> float:
    saturation_pressure = max(
        610.94 * np.exp((17.625 * dry_bulb_temperature) / (dry_bulb_temperature + 243.04)),
        1e-9,
    )
    partial_pressure = pressure_pa * humidity_ratio / (0.621945 + humidity_ratio)
    return float(np.clip(partial_pressure / saturation_pressure, 0.0, 1.0))


def _buildDailyInputFramesFromClassicWeather(
    weather_csv: str,
    site_template_csv: str,
    start_hoy: int | None,
    end_hoy: int | None,
) -> pd.DataFrame:
    weather_frame = pd.read_csv(weather_csv, header=None)
    weather_frame = weather_frame.iloc[:, :13].copy()
    weather_frame.columns = [
        "station_id", "unused", "hoy", "dry_bulb_temperature", "humidity_ratio",
        "global_horizontal_radiation", "diffuse_horizontal_radiation", "ground_temperature",
        "sky_temperature", "wind_speed", "wind_direction", "pressure", "unknown",
    ]
    weather_frame["hoy"] = pd.to_numeric(weather_frame["hoy"], errors="raise").astype(int)
    max_hoy = int(weather_frame["hoy"].max())
    actual_start = 0 if start_hoy is None else int(start_hoy)
    actual_end = max_hoy if end_hoy is None else int(end_hoy)
    extended_start = (actual_start // 24) * 24
    extended_end = (actual_end // 24) * 24 + 23
    extended_weather = weather_frame[
        (weather_frame["hoy"] >= extended_start) & (weather_frame["hoy"] <= extended_end)
    ].copy()
    if len(extended_weather) % 24 != 0:
        raise ValueError(f"Extended classic weather rows must be a multiple of 24, got {len(extended_weather)}")

    template_frame = pd.read_csv(site_template_csv)
    column_map = _buildColumnMap(template_frame.columns)
    case_id_column = column_map.get(CASE_ID_KEY)
    t_column = column_map.get(T_KEY)
    if case_id_column is None or t_column is None:
        raise ValueError("site template csv must contain caseId and t columns")

    site_case_ids = template_frame[case_id_column].drop_duplicates().tolist()
    if len(site_case_ids) != 1:
        raise ValueError("site template csv must contain exactly one caseId when used with classic weather csv")
    base_case_id = str(site_case_ids[0])
    site_case_frame = _validateCaseHours(
        base_case_id,
        template_frame[template_frame[case_id_column] == site_case_ids[0]],
        t_column,
    ).reset_index(drop=True)

    daily_frames: list[pd.DataFrame] = []
    for day_index, day_start in enumerate(range(extended_start, extended_end + 1, 24)):
        day_weather = extended_weather[extended_weather["hoy"].between(day_start, day_start + 23)].reset_index(drop=True)
        if len(day_weather) != 24:
            raise ValueError(f"Classic weather day window {day_start}..{day_start + 23} does not contain 24 rows")
        result = site_case_frame.copy()
        result[case_id_column] = f"{base_case_id}_hoy_{day_start:04d}"
        result[t_column] = np.arange(24, dtype=int)
        result["Tb"] = day_weather["dry_bulb_temperature"].to_numpy(dtype=float)
        result["Ta"] = day_weather["dry_bulb_temperature"].to_numpy(dtype=float)
        result["Tg"] = day_weather["ground_temperature"].to_numpy(dtype=float)
        result["Velocity"] = day_weather["wind_speed"].to_numpy(dtype=float)
        result["RadTotal"] = day_weather["global_horizontal_radiation"].to_numpy(dtype=float)
        result["RadDiffuse"] = day_weather["diffuse_horizontal_radiation"].to_numpy(dtype=float)
        result["Rh"] = np.array(
            [
                _relative_humidity_from_ratio(humidity_ratio, dry_bulb_temperature, pressure_pa)
                for humidity_ratio, dry_bulb_temperature, pressure_pa in zip(
                    day_weather["humidity_ratio"].to_numpy(dtype=float),
                    day_weather["dry_bulb_temperature"].to_numpy(dtype=float),
                    day_weather["pressure"].to_numpy(dtype=float),
                )
            ],
            dtype=np.float32,
        )
        result["Pressure"] = day_weather["pressure"].to_numpy(dtype=float)
        daily_frames.append(result)
    return pd.concat(daily_frames, ignore_index=True)


def _normalizeName(name: Any) -> str:
    """
    Helper function used by the UHI calculation workflow.
    """
    return str(name).casefold()


def _buildColumnMap(columns) -> dict[str, str]:
    """
    Build normalized inputs for the model workflow.
    """
    mapping: dict[str, str] = {}
    duplicates: list[str] = []
    for column in columns:
        normalized = _normalizeName(column)
        if normalized in mapping:
            duplicates.append(str(column))
            continue
        mapping[normalized] = str(column)
    if duplicates:
        raise ValueError(f"duplicate columns when ignoring case: {duplicates}")
    return mapping


def _getOptionalFloat(row: pd.Series, column_map: dict[str, str], slot_name: str) -> float | None:
    """
    Helper function used by the UHI calculation workflow.
    """
    column_name = column_map.get(_normalizeName(slot_name))
    if column_name is None:
        return None
    value = row[column_name]
    if pd.isna(value):
        return None
    return float(value)


def _validateCaseHours(case_id: Any, case_frame: pd.DataFrame, t_column: str) -> pd.DataFrame:
    """
    Validate input values before running the model workflow.
    """
    hours = pd.to_numeric(case_frame[t_column], errors='coerce')
    if hours.isna().any():
        raise ValueError(f"caseId {case_id!r} contains non-numeric t values")
    if (hours % 1 != 0).any():
        raise ValueError(f"caseId {case_id!r} contains non-integer t values")
    sorted_frame = case_frame.assign(_t_int=hours.astype(int)).sort_values('_t_int')
    actual = sorted_frame['_t_int'].tolist()
    expected = list(range(24))
    if actual != expected:
        raise ValueError(f"caseId {case_id!r} must contain each hour 0..23 exactly once, got {actual}")
    return sorted_frame


def _validateSiteConsistency(case_id: Any, case_frame: pd.DataFrame, column_map: dict[str, str]) -> None:
    """
    Validate input values before running the model workflow.
    """
    for slot_name in SiteInfo.__slots__:
        normalized = _normalizeName(slot_name)
        column_name = column_map.get(normalized)
        if column_name is None or slot_name in SITE_ARRAY_SLOTS:
            continue
        non_null = case_frame[column_name].dropna()
        if non_null.empty:
            continue
        if non_null.nunique(dropna=True) > 1:
            raise ValueError(f"site column {column_name!r} is not constant within caseId {case_id!r}")


def _buildSiteInfo(case_id: Any, sorted_case_frame: pd.DataFrame, column_map: dict[str, str]) -> SiteInfo:
    """
    Build normalized inputs for the model workflow.
    """
    _validateSiteConsistency(case_id, sorted_case_frame, column_map)
    first_row = sorted_case_frame.iloc[0]

    total_area = _getOptionalFloat(first_row, column_map, 'TotalArea')
    area_values: dict[str, float] = {}
    for area_slot in SITE_INIT_AREA_SLOTS:
        area_value = _getOptionalFloat(first_row, column_map, area_slot)
        if area_value is not None:
            area_values[area_slot] = area_value
    if total_area is None and area_values:
        total_area = float(sum(area_values.values()))
    if total_area is None or total_area <= 0:
        raise ValueError(f"caseId {case_id!r} has non-positive total area")

    init_kwargs: dict[str, float] = {'TotalArea': total_area}
    for area_slot, area_value in area_values.items():
        init_kwargs[area_slot] = area_value / total_area
    site = SiteInfo(**init_kwargs)

    t_values = sorted_case_frame['_t_int'].to_numpy(dtype=int)
    for slot_name in SiteInfo.__slots__:
        column_name = column_map.get(_normalizeName(slot_name))
        if column_name is None or slot_name in {'TotalArea', *SITE_INIT_AREA_SLOTS}:
            continue
        if slot_name in SITE_ARRAY_SLOTS:
            base_values = np.array(getattr(DEFAULT_SITE, slot_name), copy=True)
            for t_value, raw_value in zip(t_values, sorted_case_frame[column_name].to_numpy()):
                if pd.isna(raw_value):
                    continue
                base_values[t_value] = float(raw_value)
            setattr(site, slot_name, base_values.astype(np.float32))
            continue

        series = sorted_case_frame[column_name].dropna()
        if series.empty:
            continue
        setattr(site, slot_name, float(series.iloc[0]))

    return site


def _buildClimateInfo(sorted_case_frame: pd.DataFrame, column_map: dict[str, str]) -> ClimateInfo:
    """
    Build normalized inputs for the model workflow.
    """
    climate_kwargs: dict[str, np.ndarray] = {}
    t_values = sorted_case_frame['_t_int'].to_numpy(dtype=int)
    for slot_name in ClimateInfo.__slots__:
        column_name = column_map.get(_normalizeName(slot_name))
        if column_name is None:
            continue
        if slot_name in {'coolingLoad', 'ACLoad', 'ACWasteHeat'}:
            base_values = [None] * 24
            for t_value, raw_value in zip(t_values, sorted_case_frame[column_name].to_numpy()):
                if pd.isna(raw_value):
                    continue
                base_values[t_value] = float(raw_value)
            climate_kwargs[slot_name] = base_values
            continue
        if slot_name == 'solarBuilding':
            values = np.full(24, np.nan, dtype=np.float32)
            for t_value, raw_value in zip(t_values, sorted_case_frame[column_name].to_numpy()):
                if not pd.isna(raw_value):
                    values[t_value] = float(raw_value)
            if not np.all(np.isnan(values)):
                if not np.all(np.isfinite(values)):
                    raise ValueError('solarBuilding requires all 24 hourly values when provided.')
                climate_kwargs[slot_name] = values
            continue
        base_values = np.array(getattr(DEFAULT_CLIMATE, slot_name), copy=True)
        for t_value, raw_value in zip(t_values, sorted_case_frame[column_name].to_numpy()):
            if pd.isna(raw_value):
                continue
            base_values[t_value] = float(raw_value)
        climate_kwargs[slot_name] = base_values.astype(np.float32)

    if 'Tb' not in climate_kwargs and 'Ta' in climate_kwargs:
        climate_kwargs['Tb'] = np.array(climate_kwargs['Ta'], copy=True)
    if 'Ta' not in climate_kwargs and 'Tb' in climate_kwargs:
        climate_kwargs['Ta'] = np.array(climate_kwargs['Tb'], copy=True)
    if 'Tg' not in climate_kwargs:
        climate_kwargs['Tg'] = np.array(climate_kwargs.get('Ta', DEFAULT_CLIMATE.Ta), copy=True)

    return ClimateInfo(**climate_kwargs)


def loadClimateFromCsv(
    input_csv: str,
    caseId: str | None = None,
    start_hoy: int | None = None,
    end_hoy: int | None = None,
) -> ClimateInfo:
    """
    Load input data required by the model workflow.
    """
    return ClimateInfo.fromCSV(
        input_csv,
        caseId=caseId,
        start_hoy=start_hoy,
        end_hoy=end_hoy,
    )[0]


def csvCalculation(input_csv: str, output_csv: str, **calculate_kwargs) -> str:
    """
    Helper function used by the UHI calculation workflow.
    """
    data_frame = pd.read_csv(input_csv)
    column_map = _buildColumnMap(data_frame.columns)

    case_id_column = column_map.get(CASE_ID_KEY)
    t_column = column_map.get(T_KEY)
    if case_id_column is None or t_column is None:
        raise ValueError("input csv must contain caseId and t columns")

    result_frame = pd.DataFrame({
        'caseid': data_frame[case_id_column].copy(),
        't': pd.to_numeric(data_frame[t_column], errors='coerce').astype(int),
        'QS': np.nan,
        'QL': np.nan,
        'QV': np.nan,
        'QA': np.nan,
        'Tair': np.nan,
        'Tsurface': np.nan,
        'Tambient': np.nan,
    })

    calculate_options = dict(calculate_kwargs)
    calculate_options.setdefault('show_progress', False)

    for case_id, case_frame in data_frame.groupby(case_id_column, sort=False):
        sorted_case_frame = _validateCaseHours(case_id, case_frame, t_column)
        site = _buildSiteInfo(case_id, sorted_case_frame, column_map)
        climate = _buildClimateInfo(sorted_case_frame, column_map)
        hourly_results = calculate(
            np.array([climate], dtype=object),
            np.array([site], dtype=object),
            **calculate_options,
        )

        for t_value, row_index in zip(sorted_case_frame['_t_int'].to_numpy(dtype=int), sorted_case_frame.index.to_numpy()):
            result_frame.at[row_index, 'QS'] = float(hourly_results['QS'][t_value, 0])
            result_frame.at[row_index, 'QL'] = float(hourly_results['QL'][t_value, 0])
            result_frame.at[row_index, 'QV'] = float(hourly_results['QV'][t_value, 0])
            result_frame.at[row_index, 'QA'] = float(hourly_results['QA'][t_value, 0])
            result_frame.at[row_index, 'Tair'] = float(hourly_results['Tair'][t_value, 0])
            result_frame.at[row_index, 'Tsurface'] = float(hourly_results['Tsurface'][t_value, 0])
            result_frame.at[row_index, 'Tambient'] = float(hourly_results['Tambient'][t_value, 0])

    result_frame.drop(columns=['_t_int'], inplace=True, errors='ignore')
    output_dir = os.path.dirname(os.path.abspath(output_csv))
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    result_frame.to_csv(output_csv, index=False)
    return output_csv


def csvCalculationFromWeatherCsv(
    weather_csv: str,
    site_template_csv: str,
    output_csv: str,
    *,
    start_hoy: int | None = None,
    end_hoy: int | None = None,
    **calculate_kwargs,
) -> str:
    data_frame = _buildDailyInputFramesFromClassicWeather(
        weather_csv=weather_csv,
        site_template_csv=site_template_csv,
        start_hoy=start_hoy,
        end_hoy=end_hoy,
    )
    temp_input = Path(output_csv).resolve().with_name(f"{Path(output_csv).stem}__input.csv")
    temp_input.parent.mkdir(parents=True, exist_ok=True)
    data_frame.to_csv(temp_input, index=False)
    return csvCalculation(str(temp_input), output_csv, **calculate_kwargs)
