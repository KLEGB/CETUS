from __future__ import annotations

from collections.abc import Iterable
import json
import math
from pathlib import Path
from typing import Optional
import numpy as np
import pandas as pd
from numpy.ma import copy
from .constant import defPressure, defRadDiffuse, defRadTotal, defRh, defTa, defVelocity

Interval = tuple[Optional[float], Optional[float]]

LCZ_RULES: dict[str, dict[str, Interval]] = {
    "LCZ1": {
        "BSF": (40.0, 60.0),
        "ISF": (40.0, 60.0),
        "PSF": (None, 10.0),
        "SVF": (0.2, 0.4),
        "HRE": (25.0, None),
    },
    "LCZ2": {
        "BSF": (40.0, 70.0),
        "ISF": (30.0, 50.0),
        "PSF": (None, 20.0),
        "SVF": (0.3, 0.6),
        "HRE": (10.0, 25.0),
    },
    "LCZ3": {
        "BSF": (40.0, 70.0),
        "ISF": (20.0, 50.0),
        "PSF": (None, 30.0),
        "SVF": (0.2, 0.6),
        "HRE": (3.0, 10.0),
    },
    "LCZ4": {
        "BSF": (20.0, 40.0),
        "ISF": (30.0, 50.0),
        "PSF": (20.0, 40.0),
        "SVF": (0.5, 0.7),
        "HRE": (25.0, None),
    },
    "LCZ5": {
        "BSF": (20.0, 40.0),
        "ISF": (20.0, 50.0),
        "PSF": (20.0, 50.0),
        "SVF": (0.6, 0.8),
        "HRE": (10.0, 25.0),
    },
    "LCZ6": {
        "BSF": (20.0, 40.0),
        "ISF": (10.0, 30.0),
        "PSF": (30.0, 60.0),
        "SVF": (0.7, 0.9),
        "HRE": (3.0, 10.0),
    },
    "LCZ7": {
        "BSF": (40.0, 70.0),
        "ISF": (10.0, 30.0),
        "PSF": (None, 30.0),
        "SVF": (0.5, 0.8),
        "HRE": (2.0, 4.0),
    },
    "LCZ8": {
        "BSF": (20.0, 40.0),
        "ISF": (50.0, 80.0),
        "PSF": (None, 20.0),
        "SVF": (0.7, 0.9),
        "HRE": (3.0, 10.0),
    },
    "LCZ9": {
        "BSF": (10.0, 20.0),
        "ISF": (10.0, 30.0),
        "PSF": (60.0, 90.0),
        "SVF": (0.8, 0.95),
        "HRE": (3.0, 15.0),
    },
    "LCZ10": {
        "BSF": (20.0, 40.0),
        "ISF": (60.0, 90.0),
        "PSF": (None, 15.0),
        "SVF": (0.6, 0.9),
        "HRE": (5.0, 30.0),
    },
}

LCZ_WEIGHTS = {
    "BSF": 0.25,
    "ISF": 0.05,
    "PSF": 0.10,
    "SVF": 0.25,
    "HRE": 0.35,
}

LCZ_TOLERANCES = {
    "BSF": 10.0,
    "ISF": 10.0,
    "PSF": 10.0,
    "SVF": 0.10,
    "HRE": 5.0,
}

CLASSIC_WEATHER_COLUMN_ORDER = [
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
CLIMATE_RESULT_FIELDS = {"QS", "QL", "QV", "QA", "Qsa", "Qsg", "QGFlux", "QHFlux", "QVFlux"}
CLIMATE_SPLIT_FIELDS = {"Ta_split", "Tg_split"}
CLIMATE_WEATHER_OUTPUT_COLUMNS = [
    "station_id",
    "unused",
    "hoy",
    "dry_bulb_temperature",
    "dew_point_temperature",
    "global_horizontal_radiation",
    "diffuse_horizontal_radiation",
    "ground_temperature",
    "sky_temperature",
    "wind_speed",
    "wind_direction",
    "pressure",
    "unknown",
]


def _jsonify_value(value):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, dict):
        return {str(key): _jsonify_value(item) for key, item in value.items()}
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    if isinstance(value, list):
        return [_jsonify_value(item) for item in value]
    return value


def interval_distance(value: float, interval: Interval, tolerance: float) -> float:
    """Return normalized distance from observed value to target interval."""
    lower, upper = interval
    if lower is not None and value < lower:
        return (lower - value) / tolerance
    if upper is not None and value > upper:
        return (value - upper) / tolerance
    return 0.0


def classify_lcz(features: dict[str, float]) -> list[dict[str, float | str]]:
    """Return sorted LCZ candidates by weighted interval-distance similarity."""
    results = []
    for lcz_name, rules in LCZ_RULES.items():
        distance = 0.0
        for feature_name, interval in rules.items():
            deviation = interval_distance(
                value=features[feature_name],
                interval=interval,
                tolerance=LCZ_TOLERANCES[feature_name],
            )
            distance += LCZ_WEIGHTS[feature_name] * deviation ** 2

        score = math.exp(-0.5 * distance)
        results.append({
            "lcz": lcz_name,
            "distance": distance,
            "score": score,
        })

    return sorted(results, key=lambda item: item["distance"])


def _normalize_name(name) -> str:
    return str(name).casefold()


def _build_column_map(columns) -> dict[str, str]:
    mapping: dict[str, str] = {}
    duplicates: list[str] = []
    for column in columns:
        normalized = _normalize_name(column)
        if normalized in mapping:
            duplicates.append(str(column))
            continue
        mapping[normalized] = str(column)
    if duplicates:
        raise ValueError(f"duplicate columns when ignoring case: {duplicates}")
    return mapping


def _is_classic_weather_csv_columns(columns) -> bool:
    normalized = [_normalize_name(column) for column in columns]
    if len(normalized) < len(CLASSIC_WEATHER_COLUMN_ORDER):
        return False
    return normalized[: len(CLASSIC_WEATHER_COLUMN_ORDER)] == [_normalize_name(name) for name in CLASSIC_WEATHER_COLUMN_ORDER]


def _saturation_vapor_pressure_pa(temperature_c: float) -> float:
    return 610.94 * math.exp((17.625 * temperature_c) / (temperature_c + 243.04))


def _relative_humidity_from_ratio(humidity_ratio: float, dry_bulb_temperature: float, pressure_pa: float) -> float:
    partial_pressure = pressure_pa * humidity_ratio / (0.621945 + humidity_ratio)
    saturation_pressure = _saturation_vapor_pressure_pa(dry_bulb_temperature)
    if saturation_pressure <= 0:
        return 0.0
    return float(np.clip(partial_pressure / saturation_pressure, 0.0, 1.0))


def _humidity_ratio_from_dew_point(dew_point_temperature: float, pressure_pa: float) -> float:
    saturation_pressure = _saturation_vapor_pressure_pa(dew_point_temperature)
    denominator = max(pressure_pa - saturation_pressure, 1e-6)
    return float(max(0.0, 0.621945 * saturation_pressure / denominator))


def _dew_point_from_humidity_ratio(humidity_ratio: float, pressure_pa: float) -> float:
    partial_pressure = pressure_pa * humidity_ratio / max(0.621945 + humidity_ratio, 1e-6)
    partial_pressure = max(partial_pressure, 1e-6)
    ratio = max(partial_pressure / 610.94, 1e-9)
    gamma = math.log(ratio)
    denominator = max(17.625 - gamma, 1e-6)
    return float(243.04 * gamma / denominator)


def _humidity_ratio_from_relative_humidity(relative_humidity: float, dry_bulb_temperature: float, pressure_pa: float) -> float:
    saturation_pressure = _saturation_vapor_pressure_pa(dry_bulb_temperature)
    partial_pressure = float(np.clip(relative_humidity, 0.0, 1.0)) * saturation_pressure
    denominator = max(pressure_pa - partial_pressure, 1e-6)
    return float(max(0.0, 0.621945 * partial_pressure / denominator))


def _normalize_classic_weather_window(data_frame: pd.DataFrame, start_hoy: int | None, end_hoy: int | None) -> tuple[int, int, int, int]:
    min_hoy = int(pd.to_numeric(data_frame.iloc[:, 2], errors="raise").min())
    max_hoy = int(pd.to_numeric(data_frame.iloc[:, 2], errors="raise").max())
    actual_start = min_hoy if start_hoy is None else int(start_hoy)
    actual_end = max_hoy if end_hoy is None else int(end_hoy)
    if actual_start < 0 or actual_end < 0 or actual_start > actual_end:
        raise ValueError(f"Invalid HOY window: start_hoy={start_hoy}, end_hoy={end_hoy}")
    if actual_start < min_hoy:
        raise ValueError(f"Requested start_hoy {actual_start} precedes available range {min_hoy}..{max_hoy}")
    if actual_end > max_hoy:
        raise ValueError(f"Requested end_hoy {actual_end} exceeds available range {min_hoy}..{max_hoy}")
    extended_start = (actual_start // 24) * 24
    extended_end = (actual_end // 24) * 24 + 23
    if extended_start < min_hoy or extended_end > max_hoy:
        raise ValueError(
            f"Extended HOY window {extended_start}..{extended_end} exceeds available range {min_hoy}..{max_hoy}. "
            "Choose a full-day window within the available classic weather rows."
        )
    return actual_start, actual_end, extended_start, extended_end


def _load_classic_weather_frame(input_csv: str) -> pd.DataFrame:
    data_frame = pd.read_csv(input_csv, header=None)
    if data_frame.shape[1] < len(CLASSIC_WEATHER_COLUMN_ORDER):
        raise ValueError(f"classic weather csv must contain at least {len(CLASSIC_WEATHER_COLUMN_ORDER)} columns")
    data_frame = data_frame.iloc[:, : len(CLASSIC_WEATHER_COLUMN_ORDER)].copy()
    data_frame.columns = CLASSIC_WEATHER_COLUMN_ORDER
    data_frame["hoy"] = pd.to_numeric(data_frame["hoy"], errors="raise").astype(int)
    return data_frame


def _validate_case_hours(case_id, case_frame: pd.DataFrame, t_column: str) -> pd.DataFrame:
    hours = pd.to_numeric(case_frame[t_column], errors="coerce")
    if hours.isna().any():
        raise ValueError(f"caseId {case_id!r} contains non-numeric t values")
    if (hours % 1 != 0).any():
        raise ValueError(f"caseId {case_id!r} contains non-integer t values")
    sorted_frame = case_frame.assign(_t_int=hours.astype(int)).sort_values("_t_int")
    actual = sorted_frame["_t_int"].tolist()
    expected = list(range(24))
    if actual != expected:
        raise ValueError(f"caseId {case_id!r} must contain each hour 0..23 exactly once, got {actual}")
    return sorted_frame


class settingInfo(object):
    __slots__ = ['a', 'k', 'zRef', 'z0', 'c1','zRefd', 'z0d',
                 'GroundTemperature', 'HAir',
                 'UDeepLand', 'UDeepGround', 'UDeepWater',
                 'CLand', 'CGround', 'CWater',
                 'AbsoRoad','AbsoRoof', 'AbsoGround', 'AbsoLandscape', 'AbsoWater','CTTCD','CTTCB','CTTCQ',
                 'rsveg', 'rsground', 'gWUEffnight', 'gWUEffday',
                 'BruntEmissivity','EmiRoad','EmiRoof','EmiGround','EmiLandscape','EmiWater',
                 'qlx','settingLoad', 'intensity', 'annualDistance', 'FE', 'fuelDensity', 'NHC',
                 'coolingSetPoint', 'outSetPoint', 'inSetPoint','isWeekDay',
                 'outerMaxIter', 'outerTolerance', 'innerTolerance', 'relaxationFactor',
                 'conductionMode', 'dt', 'gRVEff','gInff']
    
    params_dir = Path(__file__).resolve().parent / 'params'
    def __init__(self, **kwargs):
        # Hourly cooling-load shape factor for anthropogenic building heat release, dimensionless.
        """
        Helper function used by the UHI calculation workflow.
        """
        
        """IMPORTANT: general settings for heat estimation, change with caution"""
        # Aerodynamic roughness length used in the urban wind-profile parameterization, m.
        self.a = 0.22 if kwargs.get('a') is None else kwargs.get('a')
        # Ventilation overhead ratio used in the site blockage correction, dimensionless.
        self.k = 0.2 if kwargs.get('k') is None else kwargs.get('k')
        # Reference height of urban canopy, for the canopy-amospheric interface, m.
        self.zRefd = 400 if kwargs.get('zRefd') is None else kwargs.get('zRefd')
        # Aerodynamic roughness length for the canopy-amospheric interface, m.
        self.z0d = 1.5 if kwargs.get('z0d') is None else kwargs.get('z0d')
        # Meteorological wind reference height used in the bulk-transfer coefficient, m.
        self.zRef = 2.0 if kwargs.get('zRef') is None else kwargs.get('zRef')


        """Heat balance model settings"""

        # Aerodynamic roughness length used in the bulk-transfer coefficient, m.
        self.z0 = 0.001 if kwargs.get('z0') is None else kwargs.get('z0')
        # Empirical coefficient for natural convection above the urban surface, W/(m2.K^(4/3)).
        self.c1 = 2.1 if kwargs.get('c1') is None else kwargs.get('c1')
        # Reference height of urban canopy, the functional volume of the urban heat island effect, m.
        self.HAir = self.zRefd if kwargs.get('HAir') is None else kwargs.get('HAir')
        # Deep-layer reference temperature that anchors conductive exchange below the active surface, degC.
        self.GroundTemperature = 25.0 if kwargs.get('GroundTemperature') is None else kwargs.get('GroundTemperature')
        # Effective deep-soil conductance beneath vegetated land, W/(m2.K).
        self.UDeepLand = 0.40 if kwargs.get('UDeepLand') is None else kwargs.get('UDeepLand')
        # Effective deep-soil conductance beneath paved and built ground, W/(m2.K).
        self.UDeepGround = 0.70 if kwargs.get('UDeepGround') is None else kwargs.get('UDeepGround')
        # Effective deep-water conductance beneath the urban water body, W/(m2.K).
        self.UDeepWater = 1.50 if kwargs.get('UDeepWater') is None else kwargs.get('UDeepWater')
        # Areal heat capacity of the active vegetated land layer, J/(m2.K).
        self.CLand = 0.5e6 if kwargs.get('CLand') is None else kwargs.get('CLand')
        # Areal heat capacity of the active paved-ground layer, J/(m2.K).
        self.CGround = 0.2e5 if kwargs.get('CGround') is None else kwargs.get('CGround')
        # Areal heat capacity of the active water surface layer, J/(m2.K).
        self.CWater = 1.1e6 if kwargs.get('CWater') is None else kwargs.get('CWater')

        """Short-wave absorptivity settings"""
        # Short-wave absorptivity of paved road surfaces, dimensionless.
        self.AbsoRoad = 0.74 if kwargs.get('AbsoRoad') is None else kwargs.get('AbsoRoad')
        # Short-wave absorptivity of hard bare ground surfaces, dimensionless.
        self.AbsoGround = 0.74 if kwargs.get('AbsoGround') is None else kwargs.get('AbsoGround')
        # Short-wave absorptivity of building roofs, dimensionless.
        self.AbsoRoof = self.AbsoGround if kwargs.get('AbsoRoof') is None else kwargs.get('AbsoRoof')
        # Short-wave absorptivity of vegetated land surfaces, dimensionless.
        self.AbsoLandscape = 0.80 if kwargs.get('AbsoLandscape') is None else kwargs.get('AbsoLandscape')
        # Short-wave absorptivity of urban water surfaces, dimensionless.
        self.AbsoWater = 0.96 if kwargs.get('AbsoWater') is None else kwargs.get('AbsoWater')
        # thermal time coefficient for ground
        self.CTTCD = 8  if kwargs.get('CTTCD') is None else int(kwargs.get('CTTCD'))
        # thermal time coefficient for Building
        self.CTTCB = 6 if kwargs.get('CTTCB') is None else int(kwargs.get('CTTCB'))
        # thermal time coefficient for tree
        self.CTTCQ = 12 if kwargs.get('CTTCQ') is None else int(kwargs.get('CTTCQ'))


        """Evapotranspiration vapor availability settings"""
        # vegetation surface resistance, s/m, suggested range: 70-5000
        self.rsveg = 100 if kwargs.get('rsveg') is None else kwargs.get('rsveg')
        # ground surface resistance, s/m suggested range: 500-100000
        self.rsground = 200 if kwargs.get('rsground') is None else kwargs.get('rsground')
        # green roof vapor efficiency, dimensionless, the fraction of the grass that is effective for evapotranspiration (default: 0.8)
        self.gRVEff = 0.8 if kwargs.get('gRVEff') is None else kwargs.get('gRVEff')
        # ground infiltration percentage, dimensionless, the fraction of the ground that is effective for evapotranspiration (default: 0.6)
        self.gInff = 0.6 if kwargs.get('gInff') is None else kwargs.get('gInff')
        # water useibility percentage, dimensionless, the fraction of the water that is effective for evapotranspiration (default: 0.8)
        self.gWUEffnight = 0.6 if kwargs.get('gWUEffnight') is None else kwargs.get('gWUEffnight')
        self.gWUEffday = 1.0 if kwargs.get('gWUEffday') is None else kwargs.get('gWUEffday')



        """Long-wave emissivity settings"""
        # Clear-sky Brunt emissivity intercept used in the atmospheric long-wave emissivity model, dimensionless.
        self.BruntEmissivity = 0.605 if kwargs.get('BruntEmissivity') is None else kwargs.get('BruntEmissivity')
        # Short-wave absorptivity of paved road surfaces, dimensionless.
        self.EmiRoad = 0.94 if kwargs.get('EmiRoad') is None else kwargs.get('EmiRoad')
        # Emissivity of hard bare ground surfaces, dimensionless.
        self.EmiGround = 0.94 if kwargs.get('EmiGround') is None else kwargs.get('EmiGround')
        # Emissivity of building roofs, dimensionless.
        self.EmiRoof = 0.90 if kwargs.get('EmiRoof') is None else kwargs.get('EmiRoof')
        # Emissivity of vegetated land surfaces, dimensionless.
        self.EmiLandscape = 0.96 if kwargs.get('EmiLandscape') is None else kwargs.get('EmiLandscape')
        # Emissivity of urban water surfaces, dimensionless.
        self.EmiWater = 0.98 if kwargs.get('EmiWater') is None else kwargs.get('EmiWater')


        """anthropic heat settings, usually do not need to be changed"""
        # Road traffic density factor used to convert vehicle energy use to urban-area heat release, veh/ha or equivalent design factor.
        self.intensity = 20 if kwargs.get('intensity') is None else kwargs.get('intensity')
        # Mean annual vehicle travel distance represented by the traffic source term, km/a.
        self.annualDistance = 2.5 * 10 ** 4 if kwargs.get('annualDistance') is None else kwargs.get('annualDistance')
        # Fuel economy of the representative vehicle fleet, L/100 km.
        self.FE = 10.9 if kwargs.get('FE') is None else kwargs.get('FE')
        # Fuel density of the representative vehicle fuel, kg/L.
        self.fuelDensity = 0.72 * 10 ** -2 if kwargs.get('fuelDensity') is None else kwargs.get('fuelDensity')
        # Net heating value of the representative vehicle fuel, kJ/kg.
        self.NHC = 45 * 10 ** 3 if kwargs.get('NHC') is None else kwargs.get('NHC')
        # Indoor cooling set-point temperature used by the anthropogenic building-load model, degC.
        self.coolingSetPoint = 28.8 if kwargs.get('coolingSetPoint') is None else kwargs.get('coolingSetPoint')
        # Flag indicating whether the current day is a weekday, 1 or 0, dimensionless.
        self.isWeekDay = True if kwargs.get('isWeekDay') is None else kwargs.get('isWeekDay')
        # Outdoor reference temperature paired with the building-load normalization model, degC.
        self.outSetPoint = 35 if kwargs.get('outSetPoint') is None else kwargs.get('outSetPoint')
        # Indoor reference temperature paired with the building-load normalization model, degC.
        self.inSetPoint = 26 if kwargs.get('inSetPoint') is None else kwargs.get('inSetPoint')
        self.qlx = listObj2List(
            np.array([0.16, 0.16, 0.25, 0.25, 0.25, 0.50, 0.59, 0.67, 0.67, 0.75, 0.84, 0.90,
                      1.00, 1.00, 0.92, 0.84, 0.84, 0.74, 0.74, 0.50, 0.50, 0.33, 0.16, 0.16], dtype=np.float32)
            if kwargs.get('qlxSeries') is None else kwargs.get('qlxSeries')
        )
        # Design cooling load setting for the anthropogenic building-load model, W/m2.
        self.settingLoad = 90 if kwargs.get('settingLoad') is None else kwargs.get('settingLoad')

        """settings for the iterative solution of the coupled surface-air heat-balance problem"""
        # Maximum number of outer iterations in the coupled surface-air heat-balance solve, count.
        self.outerMaxIter = 50 if kwargs.get('outerMaxIter') is None else kwargs.get('outerMaxIter')
        # Convergence tolerance for the outer air-temperature iteration, K.
        self.outerTolerance = 0.01 if kwargs.get('outerTolerance') is None else kwargs.get('outerTolerance')
        # Convergence tolerance for the inner surface-temperature bisection solve, K.
        self.innerTolerance = 1e-4 if kwargs.get('innerTolerance') is None else kwargs.get('innerTolerance')
        # Relaxation factor applied to the outer coupled iteration, dimensionless.
        self.relaxationFactor = 0.6 if kwargs.get('relaxationFactor') is None else kwargs.get('relaxationFactor')
        # Ground heat-exchange closure mode used by the heat-balance solver, dimensionless mode selector.
        self.conductionMode = 'storage_plus_deep' if kwargs.get('conductionMode') is None else kwargs.get('conductionMode')
        # Time step used by the transient heat-balance integration; `None` switches to steady-state mode, s.
        self.dt = 3600 if kwargs.get('dt', 'default') == 'default' else kwargs.get('dt')
    
    def to_dict(self) -> dict[str, object]:
        return {slot_name: _jsonify_value(getattr(self, slot_name, None)) for slot_name in self.__slots__}

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "settingInfo":
        return cls(**dict(data))

    def saveJSON(self, output_path: str | Path) -> Path:
        output_path = Path(output_path).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return output_path

    @classmethod
    def loadJSON(cls, input_path: str | Path) -> "settingInfo":
        return cls.from_dict(json.loads(Path(input_path).read_text(encoding="utf-8")))


class SiteInfo(object):
    __slots__ = ['TotalArea',
                 'AreaBuilding','AreaFacade','AreaRoad','AreaGround','AreaLandscape','AreaWater','AreaTreeShading',
                 'kS',
                 'greenRoofPercentage', 'groundInfiltratePercentage',
                 'SVF','plotRatio','GFA','SDL','SDG','SRTL','SRTG','fPSA','CL','CG']

    """
    :param AreaBuilding: absolute building occupied area. Under default initialization it is derived from 40% of total area.
    :param AreaRoad: absolute road area. Under default initialization it is derived from the default site composition.
    :param AreaGround: absolute open ground area. Under default initialization it is derived from the default site composition.
    :param AreaLandscape: absolute landscape area. Under default initialization it is derived from the default site composition.
    :param AreaWater: absolute water area. Under default initialization it is derived from the default site composition.
    :param kS: ventilation blockage ratio of the site, used with settingInfo.k to derive the effective wind reduction factor.
        The default is 0.7 times building density.
    :param greenRoofPercentage: area percentage of greening on the roof (default: 10%)
    :param SVF: Average Sky View Factor in the area (default: 0.7)
    :param plotRatio: ratio of building gross floor area to total area (default: 1.2)
    :param SDL: Shading percentage of Lanscape (default=area ratio of landscape)
    :param SDG: Shading percentage of urban construction (default=0.1)
    :param SRTL: Transparency of lanscape (default: 0.4)
    :param SRTG: Transparency of urban construction (default: 0.2)
    :param fPSA: Shading percentage of building on the open space (default: 0.2)
    :param CL: Ventilated heat transfer ratio to total heat transfer, of lanscape (default: 0.7)
    :param CG: Ventilated heat transfer ratio to total heat transfer, of urban construction (default: 0.8)
    """
    def __init__(self,**kwargs):
        """
        Helper function used by the UHI calculation workflow.
        """
        bldPercentage = kwargs.get('AreaBuilding') if kwargs.get('AreaBuilding') is not None else 0.4
        roadPercentage = kwargs.get('AreaRoad') if kwargs.get('AreaRoad') is not None else 0.25
        groundPercentage = kwargs.get('AreaGround') if kwargs.get('AreaGround') is not None else 0.15
        landscapePercentage = kwargs.get('AreaLandscape') if kwargs.get('AreaLandscape') is not None else 0.3
        waterPercentage = kwargs.get('AreaWater') if kwargs.get('AreaWater') is not None else 0.05
        self.TotalArea=1000*1000 if kwargs.get('TotalArea') is None else kwargs.get('TotalArea')
        if bldPercentage>1.0:
            bldPercentage/=self.TotalArea
        if roadPercentage>1.0:
            roadPercentage/=self.TotalArea
        if groundPercentage>1.0:
            groundPercentage/=self.TotalArea
        if landscapePercentage>1.0:
            landscapePercentage/=self.TotalArea
        if waterPercentage>1.0:
            waterPercentage/=self.TotalArea
        
        scalePercentage = bldPercentage + landscapePercentage + waterPercentage
        if bldPercentage + landscapePercentage + waterPercentage > 0.8:
            bldPercentage *= 0.8 / scalePercentage
            landscapePercentage *= 0.8 / scalePercentage
            waterPercentage *= 0.8 / scalePercentage
            roadPercentage_default = 0.05
            groundPercentage_default = 0.15
        else:
            roadPercentage_default=0.25*(1-scalePercentage)
            groundPercentage_default=0.75*(1-scalePercentage)
        if groundPercentage is None:
            groundPercentage=groundPercentage_default
        if roadPercentage is None:
            roadPercentage=roadPercentage_default
        sumPercentage = bldPercentage + landscapePercentage + waterPercentage + roadPercentage + groundPercentage



        self.AreaBuilding = self.TotalArea*bldPercentage/sumPercentage
        self.AreaRoad = self.TotalArea*roadPercentage/sumPercentage
        self.AreaGround = self.TotalArea*groundPercentage/sumPercentage
        self.AreaLandscape = self.TotalArea*landscapePercentage/sumPercentage
        self.AreaWater = self.TotalArea*waterPercentage/sumPercentage

        self.AreaTreeShading = self.AreaLandscape
        self.kS = 0.7 * self.AreaBuilding / self.TotalArea if kwargs.get('kS') is None else kwargs.get('kS')
        self.SVF = 0.7 if kwargs.get('SVF') is None else kwargs.get('SVF')
        self.plotRatio=bldPercentage*3 if kwargs.get('plotRatio') is None else kwargs.get('plotRatio')
        if kwargs.get('GFA') is not None:
            self.GFA = float(kwargs['GFA'])
        self.AreaFacade=0.5*self.TotalArea if kwargs.get('AreaFacade') is None else kwargs.get('AreaFacade')
        if self.AreaFacade<20.0:
            self.AreaFacade*=self.TotalArea

        self.greenRoofPercentage = 0.20 if kwargs.get('greenRoofPercentage') is None else kwargs.get('greenRoofPercentage')
        # self.groundInfiltratePercentage = 0.50 if kwargs.get('groundInfiltratePercentage') is None else kwargs.get('groundInfiltratePercentage')

        self.SDL = self.AreaLandscape/self.TotalArea if kwargs.get('SDL') is None else kwargs.get('SDL')
        self.SDG = 0.1 if kwargs.get('SDG') is None else kwargs.get('SDG')
        self.SRTL = 0.4 if kwargs.get('SRTL') is None else kwargs.get('SRTL')
        self.SRTG = 0.2 if kwargs.get('SRTG') is None else kwargs.get('SRTG')
        self.fPSA = 0.2 if kwargs.get('fPSA') is None else kwargs.get('fPSA')
        self.CL = 0.7 if kwargs.get('CL') is None else kwargs.get('CL')
        self.CG = 0.8 if kwargs.get('CG') is None else kwargs.get('CG')

    def __setattr__(self, name, value):
        """Keep GFA / TotalArea / plotRatio consistent for all input paths."""
        if name in {'TotalArea', 'GFA', 'plotRatio'}:
            value = float(value)
            if not math.isfinite(value) or value < 0 or (name == 'TotalArea' and value == 0):
                raise ValueError(f"Invalid site {name}: {value}")
        object.__setattr__(self, name, value)
        if name == 'plotRatio' and hasattr(self, 'TotalArea'):
            object.__setattr__(self, 'GFA', value * self.TotalArea)
        elif name in {'GFA', 'TotalArea'} and hasattr(self, 'GFA') and hasattr(self, 'TotalArea'):
            object.__setattr__(self, 'plotRatio', self.GFA / self.TotalArea)

    def to_dict(self) -> dict[str, object]:
        return {slot_name: _jsonify_value(getattr(self, slot_name, None)) for slot_name in self.__slots__}

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "SiteInfo":
        return cls(**dict(data))

    def saveJSON(self, output_path: str | Path) -> Path:
        output_path = Path(output_path).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return output_path

    @classmethod
    def loadJSON(cls, input_path: str | Path) -> "SiteInfo":
        return cls.from_dict(json.loads(Path(input_path).read_text(encoding="utf-8")))

    @classmethod
    def _from_site_input_object(cls, source) -> "SiteInfo":
        total_area = getattr(source, "TotalArea", None)
        if total_area is None or float(total_area) <= 0.0:
            raise ValueError("siteInput.TotalArea must be positive.")

        init_kwargs: dict[str, float] = {"TotalArea": float(total_area)}
        for field_name in ("AreaBuilding", "AreaRoad", "AreaGround", "AreaLandscape", "AreaWater"):
            value = getattr(source, field_name, None)
            if value is None:
                continue
            init_kwargs[field_name] = float(value) / float(total_area)

        site = cls(**init_kwargs)
        for field_name in cls.__slots__:
            if field_name in {"TotalArea", "AreaBuilding", "AreaRoad", "AreaGround", "AreaLandscape", "AreaWater"}:
                continue
            value = getattr(source, field_name, None)
            if value is None:
                continue
            setattr(site, field_name, float(value))

        for field_name in ("AreaBuilding", "AreaRoad", "AreaGround", "AreaLandscape", "AreaWater"):
            value = getattr(source, field_name, None)
            if value is not None:
                setattr(site, field_name, float(value))
        return site

    @classmethod
    def _convert_site_input_result(cls, result):
        if isinstance(result, list):
            return [cls._from_site_input_object(item) for item in result]
        return cls._from_site_input_object(result)

    @staticmethod
    def _normalize_source_argument(source):
        if isinstance(source, (str, Path)):
            return False, source
        if isinstance(source, Iterable) and not isinstance(source, (bytes, str)):
            return True, list(source)
        return False, source

    @classmethod
    def fromTIF(cls, tif_path, **kwargs):
        from SITE.siteInput import siteInput

        is_many, source = cls._normalize_source_argument(tif_path)
        if is_many:
            return [cls._from_site_input_object(siteInput.fromTIF(item, **kwargs)) for item in source]
        return cls._from_site_input_object(siteInput.fromTIF(source, **kwargs))

    @classmethod
    def fromSHP(cls, shp_path, **kwargs):
        from SITE.siteInput import siteInput

        is_many, source = cls._normalize_source_argument(shp_path)
        if is_many:
            return [cls._from_site_input_object(siteInput.fromSHP(item, **kwargs)) for item in source]
        return cls._from_site_input_object(siteInput.fromSHP(source, **kwargs))

    @classmethod
    def fromINX(cls, inx_path, **kwargs):
        from SITE.siteInput import siteInput

        is_many, source = cls._normalize_source_argument(inx_path)
        if is_many:
            return [cls._from_site_input_object(siteInput.fromINX(item, **kwargs)) for item in source]
        return cls._from_site_input_object(siteInput.fromINX(source, **kwargs))

    @classmethod
    def fromCSV(cls, csv_path, **kwargs):
        from SITE.siteInput import siteInput

        row = kwargs.pop("row", None)
        is_many, source = cls._normalize_source_argument(csv_path)
        if is_many:
            return [cls._from_site_input_object(siteInput.fromCSV(item, row=0 if row is None else row)) for item in source]

        if row is not None:
            return cls._from_site_input_object(siteInput.fromCSV(source, row=row))

        data_frame = pd.read_csv(source)
        if len(data_frame) == 1:
            return cls._from_site_input_object(siteInput.fromCSV(source, row=0))
        return [cls._from_site_input_object(siteInput.fromCSV(source, row=index)) for index in range(len(data_frame))]

    @property
    def areaFractions(self):
        """
        Returns the area fractions of the site as a dictionary with keys "ground", "land", and "water".
        """
        return {
            "ground": (self.AreaGround + self.AreaRoad + self.AreaBuilding) / self.TotalArea,
            "land": self.AreaLandscape / self.TotalArea,
            "water": self.AreaWater / self.TotalArea
        }

    @property
    def LCZ(self):
        """
        Estimate LCZ label from site morphology and cover fractions.

        Returns:
            str: best-match LCZ class label.
        """
        ranking = classify_lcz(self.LCZFeatures)
        if not ranking:
            return "LCZ5"

        # Confidence is internally represented by the score; only return top label.
        best = max(ranking, key=lambda item: float(item["score"]))
        return str(best["lcz"])

    @property
    def LCZFeatures(self):
        """
        Return derived LCZ input features used by the classifier.
        """
        total_area = max(float(self.TotalArea), 1e-6)

        bsf = 100.0 * float(self.AreaBuilding) / total_area
        isf = 100.0 * float(self.AreaRoad + self.AreaGround) / total_area
        psf = 100.0 * float(self.AreaLandscape + self.AreaWater) / total_area
        svf = float(np.clip(self.SVF, 0.05, 1.0))

        # Use FAR and footprint ratio to derive a mean roughness-element height.
        bsf_frac = max(bsf / 100.0, 1e-4)
        far = max(float(self.plotRatio), 0.0)
        avg_floors = far / bsf_frac
        hre = avg_floors * 4.0

        return {
            "BSF": float(bsf),
            "ISF": float(isf),
            "PSF": float(psf),
            "SVF": float(svf),
            "HRE": float(hre),
        }

    @property
    def LCZRanking(self):
        """
        Return all LCZ candidates sorted by increasing weighted distance.
        """
        return classify_lcz(self.LCZFeatures)



class ClimateInfo(object):
    __slots__ =['solarBuilding','RadTotal','RadDiffuse','Velocity','Ta','Tg','Tb','Rh', 'Pressure',
                'HOY','DewPoint','HumidityRatio','SkyTemperature','WindDirection',
                'coolingLoad', 'ACLoad', 'ACWasteHeat',
                'Ta_split', 'Tg_split', 
                'QS','QL','QV','QA','Qsa','Qsg','QGFlux','QHFlux','QVFlux'
                ]
    """
    All climate information should be a 24 length series of floats for each params.
    besides, the temporal result will be stored in the same class, for easy communication.

    :param solarBuilding: 24-hour building incident solar intensity per building footprint area (W/m2); None uses RadTotal
    :param RadTotal: Total radiation in time t
    :param RadDiffuse: diffuse radiation in time t
    :param Velocity: wind speed in time t
    :param Ta: Dry Bulb Temperature(C) in specific daytime t, which will be updated by the heat Balance method
    :param Tg: Ground Temperature(C) in specific daytime t, which will be updated by the heat Balance method
    :param Tb: Background Temperature(C) in specific daytime t
    :param Rh: Relative humidity(%) in specific daytime t
    :param Pressure: Atmospheric pressure (Pa) in specific daytime t
    :param HOY: absolute hours-of-year used when exporting a MoosasEnergy weather csv
    :param DewPoint: dew-point temperature(C) used by the public-building energy path
    :param HumidityRatio: humidity ratio(kg/kg) used by the classic weather csv bridge
    :param SkyTemperature: sky temperature(C) retained when round-tripping the classic weather csv
    :param WindDirection: wind direction bucket used by the classic weather csv bridge
    :param coolingLoad: hourly AC waste heat in Wh/m2_site (mean W/m2 over one hour); None uses the empirical anthropic model
    :param ACLoad: aggregate signed hourly thermal load in Wh (steady: cooling minus heating)
    :param ACWasteHeat: signed net heat in Wh; steady load + electricity - inward air/contact exchange

    :param Ta_split: Dry Bulb Temperature(C) in specific daytime t, which will be updated by the heat Balance method, split by surface type
    :param Tg_split: Ground Temperature(C) in specific daytime t, which will be updated by the heat Balance method, split by surface type
    :param QS: Shortwave heat flux(W/m2) in specific daytime t, split by surface type
    :param QL: Longwave heat flux(W/m2) in specific daytime t, split by surface type
    :param QV: Evaporation heat flux(W/m2) in specific daytime t, split by surface type
    :param QA: Anthropogenic heat flux(W/m2) in specific daytime t, split by surface type
    :param Qsa: Air Storage heat flux(W/m2) in specific daytime t, split by surface type
    :param Qsg: Ground Storage heat flux(W/m2) in specific daytime t, split by surface type
    :param QGFlux: Total heat flux from surface to deep ground(W/m2) in specific daytime t, split by surface type
    :param QHFlux: Total heat flux from surface to air(W/m2) in specific daytime t, split by surface type
    :param QVFlux: Total heat flux escaped from canopy(W/m2) in specific daytime t, split by surface type    
    """

    def __init__(self,**kwargs):
        self.solarBuilding = None
        if kwargs.get('solarBuilding') is not None:
            values = np.asarray(kwargs['solarBuilding'], dtype=np.float32)
            if values.shape != (24,) or not np.all(np.isfinite(values)) or np.any(values < 0):
                raise ValueError('solarBuilding must be None or 24 finite non-negative hourly values.')
            self.solarBuilding = values.copy()
        self.RadTotal:np.ndarray[np.float32]=listObj2List(kwargs.get('RadTotal')) if kwargs.get('RadTotal') is not None else listObj2List(defRadTotal)
        self.RadDiffuse:np.ndarray[np.float32]=listObj2List(kwargs.get('RadDiffuse')) if kwargs.get('RadDiffuse') is not None else listObj2List(defRadDiffuse)
        self.Velocity:np.ndarray[np.float32]=listObj2List(kwargs.get('Velocity')) if kwargs.get('Velocity') is not None else listObj2List(defVelocity)
        self.Velocity = np.maximum(self.Velocity, 0.5).astype(np.float32)
        self.Ta:np.ndarray[np.float32]=listObj2List(kwargs.get('Ta')) if kwargs.get('Ta') is not None else listObj2List(defTa)
        self.Tg:np.ndarray[np.float32]=listObj2List(kwargs.get('Tg')) if kwargs.get('Tg') is not None else np.array(self.Ta, copy=True)
        self.Tb:np.ndarray[np.float32]=listObj2List(kwargs.get('Tb')) if kwargs.get('Tb') is not None else np.array(self.Ta, copy=True)
        self.Rh:np.ndarray[np.float32]=listObj2List(kwargs.get('Rh')) if kwargs.get('Rh') is not None else listObj2List(defRh)
        self.Pressure:np.ndarray[np.float32]=listObj2List(kwargs.get('Pressure')) if kwargs.get('Pressure') is not None else listObj2List(defPressure)
        self.HOY:np.ndarray[np.int32]=listObj2List(kwargs.get('HOY')) if kwargs.get('HOY') is not None else np.arange(24, dtype=np.float32)
        self.HOY = np.rint(self.HOY).astype(np.int32)
        self.DewPoint:np.ndarray[np.float32]=listObj2List(kwargs.get('DewPoint')) if kwargs.get('DewPoint') is not None else None
        self.HumidityRatio:np.ndarray[np.float32]=listObj2List(kwargs.get('HumidityRatio')) if kwargs.get('HumidityRatio') is not None else None
        self.SkyTemperature:np.ndarray[np.float32]=listObj2List(kwargs.get('SkyTemperature')) if kwargs.get('SkyTemperature') is not None else np.array(self.Ta, copy=True)
        self.WindDirection:np.ndarray[np.float32]=listObj2List(kwargs.get('WindDirection')) if kwargs.get('WindDirection') is not None else np.zeros(24, dtype=np.float32)

        if self.HumidityRatio is None and self.DewPoint is not None:
            self.HumidityRatio = np.array(
                [_humidity_ratio_from_dew_point(dew_point, pressure_pa) for dew_point, pressure_pa in zip(self.DewPoint, self.Pressure)],
                dtype=np.float32,
            )
        if self.HumidityRatio is None:
            self.HumidityRatio = np.array(
                [
                    _humidity_ratio_from_relative_humidity(relative_humidity, dry_bulb_temperature, pressure_pa)
                    for relative_humidity, dry_bulb_temperature, pressure_pa in zip(self.Rh, self.Ta, self.Pressure)
                ],
                dtype=np.float32,
            )
        if self.DewPoint is None:
            self.DewPoint = np.array(
                [_dew_point_from_humidity_ratio(humidity_ratio, pressure_pa) for humidity_ratio, pressure_pa in zip(self.HumidityRatio, self.Pressure)],
                dtype=np.float32,
            )
        self.HumidityRatio = np.asarray(self.HumidityRatio, dtype=np.float32)
        self.DewPoint = np.asarray(self.DewPoint, dtype=np.float32)
        self.SkyTemperature = np.asarray(self.SkyTemperature, dtype=np.float32)
        self.WindDirection = np.asarray(self.WindDirection, dtype=np.float32)
        self.coolingLoad = listObjToOptionalList(kwargs.get('coolingLoad'))
        if self.coolingLoad is None:
            self.coolingLoad = [None] * 24
        # Whole-neighborhood hourly Wh; None denotes results not yet computed.
        for name in ('ACLoad', 'ACWasteHeat'):
            values = listObjToOptionalList(kwargs.get(name))
            setattr(self, name, values if values is not None else [None] * 24)
        # temporary storage for the results
        for attr in ['Ta_split', 'Tg_split',
                     'QS','QL','QV','QA','Qsa','Qsg','QGFlux','QHFlux','QVFlux']:
            zeroDict = {"ground": np.zeros_like(self.Ta, dtype=np.float32), 
                    "water": np.zeros_like(self.Ta, dtype=np.float32), 
                    "land": np.zeros_like(self.Ta, dtype=np.float32)}
            setattr(self, attr,zeroDict)

    def to_dict(self) -> dict[str, object]:
        return {slot_name: _jsonify_value(getattr(self, slot_name, None)) for slot_name in self.__slots__}

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "ClimateInfo":
        init_kwargs = {
            key: value
            for key, value in data.items()
            if key not in CLIMATE_SPLIT_FIELDS and key not in CLIMATE_RESULT_FIELDS
        }
        climate = cls(**init_kwargs)
        for key in CLIMATE_SPLIT_FIELDS | CLIMATE_RESULT_FIELDS:
            if key in data:
                value = data[key]
                if isinstance(value, dict):
                    setattr(
                        climate,
                        key,
                        {name: np.asarray(series, dtype=np.float32) for name, series in value.items()},
                    )
        return climate

    def saveJSON(self, output_path: str | Path) -> Path:
        output_path = Path(output_path).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return output_path

    @classmethod
    def loadJSON(cls, input_path: str | Path) -> "ClimateInfo":
        return cls.from_dict(json.loads(Path(input_path).read_text(encoding="utf-8")))

    @classmethod
    def _from_case_frame(cls, sorted_case_frame: pd.DataFrame, column_map: dict[str, str]) -> "ClimateInfo":
        climate_kwargs: dict[str, np.ndarray] = {}
        default_climate = cls()
        t_values = sorted_case_frame["_t_int"].to_numpy(dtype=int)
        for slot_name in cls.__slots__:
            column_name = column_map.get(_normalize_name(slot_name))
            if column_name is None or slot_name.endswith("_split") or slot_name in CLIMATE_RESULT_FIELDS:
                continue
            if slot_name in {"coolingLoad", "ACLoad", "ACWasteHeat"}:
                values = [None] * 24
                for t_value, raw_value in zip(t_values, sorted_case_frame[column_name].to_numpy()):
                    if pd.isna(raw_value):
                        continue
                    values[t_value] = float(raw_value)
                climate_kwargs[slot_name] = values
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
            base_values = np.array(getattr(default_climate, slot_name), copy=True)
            for t_value, raw_value in zip(t_values, sorted_case_frame[column_name].to_numpy()):
                if pd.isna(raw_value):
                    continue
                base_values[t_value] = float(raw_value)
            climate_kwargs[slot_name] = base_values.astype(np.float32)

        if "Tb" not in climate_kwargs and "Ta" in climate_kwargs:
            climate_kwargs["Tb"] = np.array(climate_kwargs["Ta"], copy=True)
        if "Ta" not in climate_kwargs and "Tb" in climate_kwargs:
            climate_kwargs["Ta"] = np.array(climate_kwargs["Tb"], copy=True)
        if "Tg" not in climate_kwargs:
            climate_kwargs["Tg"] = np.array(climate_kwargs.get("Ta", default_climate.Ta), copy=True)
        return cls(**climate_kwargs)

    @classmethod
    def _from_classic_weather_frame(
        cls,
        weather_frame: pd.DataFrame,
        *,
        start_hoy: int | None = None,
        end_hoy: int | None = None,
    ) -> list["ClimateInfo"]:
        _, _, extended_start, extended_end = _normalize_classic_weather_window(weather_frame, start_hoy, end_hoy)
        extended_weather = weather_frame[
            (weather_frame["hoy"] >= extended_start) & (weather_frame["hoy"] <= extended_end)
        ].copy()
        if len(extended_weather) % 24 != 0:
            raise ValueError(f"Extended classic weather rows must be a multiple of 24, got {len(extended_weather)}")

        climate_list: list[ClimateInfo] = []
        for day_start in range(extended_start, extended_end + 1, 24):
            day_weather = extended_weather[extended_weather["hoy"].between(day_start, day_start + 23)].reset_index(drop=True)
            if len(day_weather) != 24:
                raise ValueError(f"Classic weather day window {day_start}..{day_start + 23} does not contain 24 rows")
            fifth_column = day_weather["humidity_ratio"].to_numpy(dtype=float)
            if np.nanmax(np.abs(fifth_column)) <= 1.0:
                humidity_ratio = fifth_column.astype(np.float32)
                dew_point = np.array(
                    [_dew_point_from_humidity_ratio(value, pressure_pa) for value, pressure_pa in zip(fifth_column, day_weather["pressure"].to_numpy(dtype=float))],
                    dtype=np.float32,
                )
            else:
                dew_point = fifth_column.astype(np.float32)
                humidity_ratio = np.array(
                    [_humidity_ratio_from_dew_point(value, pressure_pa) for value, pressure_pa in zip(fifth_column, day_weather["pressure"].to_numpy(dtype=float))],
                    dtype=np.float32,
                )
            climate_kwargs = {
                "HOY": day_weather["hoy"].to_numpy(dtype=np.int32),
                "Tb": day_weather["dry_bulb_temperature"].to_numpy(dtype=np.float32),
                "Ta": day_weather["dry_bulb_temperature"].to_numpy(dtype=np.float32),
                "Tg": day_weather["ground_temperature"].to_numpy(dtype=np.float32),
                "Velocity": day_weather["wind_speed"].to_numpy(dtype=np.float32),
                "RadTotal": day_weather["global_horizontal_radiation"].to_numpy(dtype=np.float32),
                "RadDiffuse": day_weather["diffuse_horizontal_radiation"].to_numpy(dtype=np.float32),
                "Pressure": day_weather["pressure"].to_numpy(dtype=np.float32),
                "DewPoint": dew_point,
                "HumidityRatio": humidity_ratio,
                "SkyTemperature": day_weather["sky_temperature"].to_numpy(dtype=np.float32),
                "WindDirection": day_weather["wind_direction"].to_numpy(dtype=np.float32),
                "Rh": np.array(
                    [
                        _relative_humidity_from_ratio(humidity_ratio, dry_bulb_temperature, pressure_pa)
                        for humidity_ratio, dry_bulb_temperature, pressure_pa in zip(
                            humidity_ratio,
                            day_weather["dry_bulb_temperature"].to_numpy(dtype=float),
                            day_weather["pressure"].to_numpy(dtype=float),
                        )
                    ],
                    dtype=np.float32,
                ),
            }
            climate_list.append(cls(**climate_kwargs))
        return climate_list

    @classmethod
    def fromCSV(
        cls,
        input_csv: str,
        *,
        caseId: str | None = None,
        start_hoy: int | None = None,
        end_hoy: int | None = None,
    ) -> list["ClimateInfo"]:
        return cls.fromFile(input_csv, caseId=caseId, start_hoy=start_hoy, end_hoy=end_hoy)

    def toWeatherFrame(
        self,
        *,
        station_id: str = "000000",
        start_hoy: int | None = None,
    ) -> pd.DataFrame:
        hoy_values = np.asarray(self.HOY, dtype=np.int32)
        if start_hoy is not None:
            hoy_values = np.arange(int(start_hoy), int(start_hoy) + len(self.Ta), dtype=np.int32)

        frame = pd.DataFrame(
            {
                "station_id": [str(station_id)] * len(hoy_values),
                "unused": np.zeros(len(hoy_values), dtype=np.int32),
                "hoy": hoy_values,
                "dry_bulb_temperature": np.asarray(self.Ta, dtype=np.float32),
                "dew_point_temperature": np.asarray(self.DewPoint, dtype=np.float32),
                "global_horizontal_radiation": np.asarray(self.RadTotal, dtype=np.float32),
                "diffuse_horizontal_radiation": np.asarray(self.RadDiffuse, dtype=np.float32),
                "ground_temperature": np.asarray(self.Tg, dtype=np.float32),
                "sky_temperature": np.asarray(self.SkyTemperature, dtype=np.float32),
                "wind_speed": np.asarray(self.Velocity, dtype=np.float32),
                "wind_direction": np.asarray(self.WindDirection, dtype=np.float32),
                "pressure": np.asarray(self.Pressure, dtype=np.float32),
                "unknown": np.full(len(hoy_values), 9999999, dtype=np.int32),
            },
            columns=CLIMATE_WEATHER_OUTPUT_COLUMNS,
        )
        return frame

    def toCSV(
        self,
        output_csv: str | Path,
        *,
        station_id: str = "000000",
        start_hoy: int | None = None,
        include_header: bool = False,
    ) -> Path:
        output_path = Path(output_csv).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        self.toWeatherFrame(station_id=station_id, start_hoy=start_hoy).to_csv(output_path, index=False, header=include_header)
        return output_path

    @classmethod
    def toWeatherSeriesFrame(
        cls,
        climate_series: Iterable["ClimateInfo"],
        *,
        station_id: str = "000000",
    ) -> pd.DataFrame:
        frames = [climate.toWeatherFrame(station_id=station_id) for climate in climate_series]
        if len(frames) == 0:
            return pd.DataFrame(columns=CLIMATE_WEATHER_OUTPUT_COLUMNS)
        return pd.concat(frames, ignore_index=True)

    @classmethod
    def seriesToCSV(
        cls,
        climate_series: Iterable["ClimateInfo"],
        output_csv: str | Path,
        *,
        station_id: str = "000000",
        include_header: bool = False,
    ) -> Path:
        output_path = Path(output_csv).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        cls.toWeatherSeriesFrame(climate_series, station_id=station_id).to_csv(output_path, index=False, header=include_header)
        return output_path

    @classmethod
    def fromFile(
        cls,
        input_path: str,
        *,
        caseId: str | None = None,
        start_hoy: int | None = None,
        end_hoy: int | None = None,
    ) -> list["ClimateInfo"]:
        source_path = str(input_path)
        suffix = source_path.lower()

        if suffix.endswith(".epw"):
            from WEA import build_weather_csv

            weather_csv = build_weather_csv(source_path, start_hoy=start_hoy, end_hoy=end_hoy)
            weather_frame = _load_classic_weather_frame(str(weather_csv))
            return cls._from_classic_weather_frame(weather_frame, start_hoy=start_hoy, end_hoy=end_hoy)

        data_frame = pd.read_csv(source_path)
        if _is_classic_weather_csv_columns(data_frame.columns):
            return cls._from_classic_weather_frame(_load_classic_weather_frame(source_path), start_hoy=start_hoy, end_hoy=end_hoy)

        preview_columns = [str(column) for column in data_frame.columns]
        if len(preview_columns) == len(CLASSIC_WEATHER_COLUMN_ORDER):
            try:
                [float(value) for value in preview_columns[1:]]
                return cls._from_classic_weather_frame(_load_classic_weather_frame(source_path), start_hoy=start_hoy, end_hoy=end_hoy)
            except ValueError:
                pass

        column_map = _build_column_map(data_frame.columns)
        case_id_column = column_map.get("caseid")
        t_column = column_map.get("t")
        if case_id_column is None or t_column is None:
            return cls._from_classic_weather_frame(_load_classic_weather_frame(source_path), start_hoy=start_hoy, end_hoy=end_hoy)

        unique_case_ids = data_frame[case_id_column].drop_duplicates().tolist()
        if not unique_case_ids:
            raise ValueError("input csv does not contain any caseId rows")

        selected_case_ids = [caseId] if caseId is not None else unique_case_ids
        climate_list: list[ClimateInfo] = []
        for selected_case_id in selected_case_ids:
            case_frame = data_frame[data_frame[case_id_column] == selected_case_id]
            if case_frame.empty:
                raise ValueError(f"caseId {selected_case_id!r} not found in climate csv")
            sorted_case_frame = _validate_case_hours(selected_case_id, case_frame, t_column)
            climate_list.append(cls._from_case_frame(sorted_case_frame, column_map))
        return climate_list


def listObj2List(listObj):
    """
    Helper function used by the UHI calculation workflow.
    """
    if isinstance(listObj, np.ndarray):
        values = listObj.tolist()
    elif isinstance(listObj, Iterable) and not isinstance(listObj, (str, bytes)):
        values = list(listObj)
    else:
        values = [listObj]

    if len(values) == 0:
        values = [0.0]

    while len(values) < 24:
        values.append(values[-1])

    return np.array(values[:24], dtype=np.float32)


def listObjToOptionalList(listObj):
    if listObj is None:
        return [None] * 24
    if isinstance(listObj, np.ndarray):
        values = list(listObj.tolist())
    elif isinstance(listObj, Iterable) and not isinstance(listObj, (str, bytes)):
        values = list(listObj)
    else:
        values = [listObj]

    if len(values) == 0:
        values = [None]

    while len(values) < 24:
        values.append(values[-1])

    normalized: list[float | None] = []
    for value in values[:24]:
        if value is None:
            normalized.append(None)
        elif isinstance(value, float) and np.isnan(value):
            normalized.append(None)
        else:
            normalized.append(float(value))
    return normalized


# Backward-compatible alias for older imports.
settingsInfo = settingInfo

def initializeSeries(defaultValue=0.0):
    """
    Helper function used by the UHI calculation workflow.
    """
    return np.array([defaultValue] * 24, dtype=np.float32)
