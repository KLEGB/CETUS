from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .anthropic import anthropicHeatFlowIntensity
from .heatBalance import solveHeatBalance
from .longwave import longWaveRadHeatIntensity
from .shortwave import shortWaveRadHeatIntensity
from .modelInfo import ClimateInfo, SiteInfo, settingInfo
from .vapor import vaporHeatFlowIntensity


RESULT_COLUMNS = [
    'QS',
    'QL',
    'QV',
    'QA',
    'Tair',
    'Tsurface',
    'Tambient',
    'TaGround',
    'TaWater',
    'TaLand',
    'TgGround',
    'TgWater',
    'TgLand',
]

shortWaveRadDifference = shortWaveRadHeatIntensity
longWaveRadDifference = longWaveRadHeatIntensity
vaporDifference = vaporHeatFlowIntensity
anthropicDifference = anthropicHeatFlowIntensity

PARAMS_DIR = Path(__file__).resolve().parent / 'params'
DEFAULT_LCZ = 'LCZ5'
_SETTING_PARAMS_CACHE: dict[str, dict[str, object]] = {}


def bar(process, msg, dig=0, printed=True):
    """
    Helper function used by the UHI calculation workflow.
    """
    q = round(process * 100, dig)
    hint = f"\r[{msg}: {'*' * round(q / 2)} {'_' * (50 - round(q / 2))} {q} %]"
    if printed:
        print(hint, end="")
    return hint


def _initializeResultArrays():
    """
    Helper function used by the UHI calculation workflow.
    """
    result = {key: np.zeros(24, dtype=np.float32) for key in RESULT_COLUMNS}

    return result

def weighted(valuesDict, areaFractions):
    """
    Area-weight one state variable across the three isolated surface processes.
    :param surfaceStates: dictionary keyed by surface type.
    :param areaFractions: dictionary keyed by surface type.
    :param stateKey: field to read from each surface state.
    :return: area-weighted state value.
    """
    return sum(float(areaFractions[name]) * float(valuesDict[name]) for name in areaFractions.keys())


def _normalizeLczName(value) -> str:
    if value is None:
        return DEFAULT_LCZ
    text = str(value).strip().upper()
    if not text:
        return DEFAULT_LCZ
    if text.startswith('LCZ'):
        return text
    return f'LCZ{text}'


def _loadSettingParams(lcz_name: str) -> dict[str, object]:
    normalized = _normalizeLczName(lcz_name)
    path = PARAMS_DIR / f'{normalized}.json'
    if not path.exists() and normalized != DEFAULT_LCZ:
        normalized = DEFAULT_LCZ
        path = PARAMS_DIR / f'{normalized}.json'
    if normalized not in _SETTING_PARAMS_CACHE:
        if not path.exists():
            _SETTING_PARAMS_CACHE[normalized] = {}
        else:
            _SETTING_PARAMS_CACHE[normalized] = json.loads(path.read_text(encoding='utf-8'))
    return _SETTING_PARAMS_CACHE[normalized]


def _settingInfoForSite(site: SiteInfo) -> settingInfo:
    lcz_value = getattr(site, 'LCZ', None)
    lcz_name = lcz_value() if callable(lcz_value) else lcz_value
    params = _loadSettingParams(_normalizeLczName(lcz_name))
    supported_params = {
        name: value
        for name, value in params.items()
        if name in settingInfo.__slots__
    }
    return settingInfo(**supported_params)


def _resolveSettingsForSite(settings, site: SiteInfo) -> settingInfo:
    if settings is None:
        return _settingInfoForSite(site)
    return settings


def _runDailyCycle(climate:ClimateInfo, site:SiteInfo, settings:settingInfo):
    """
    run a daily cycle of the UHI calculation workflow for a single climate and site case, 
    and also update the climate object with the calculated Qstorage values.
    :param climate: ClimateInfo object for the current case.
    :param site: SiteInfo object for the current case.
    :param settings: settingInfo object for the current case.
    :return: dictionary of calculated results for the current case, keyed by result type.
    """
    result = _initializeResultArrays()
    for hour in range(24):
        shortwave_heat = shortWaveRadHeatIntensity(hour, climate, site, settings)
        longwave_heat = longWaveRadHeatIntensity(hour, climate, site, settings)
        vapor_heat = vaporHeatFlowIntensity(hour, climate, site, settings)
        anthropic_heat = anthropicHeatFlowIntensity(hour, climate, site, settings)
        surface_heat_flux = {
            name: float(shortwave_heat[name]) + float(longwave_heat[name])
            for name in ('ground', 'land', 'water')
        }
        air_heat_flux = float(vapor_heat) + float(anthropic_heat)
        heat_balance_result = solveHeatBalance(
            hour,
            surface_heat_flux,
            air_heat_flux,
            climate,
            site,
            settings=settings,
        )


        result['QS'][hour] = weighted(shortwave_heat, site.areaFractions)
        result['QL'][hour] = weighted(longwave_heat, site.areaFractions)
        result['QV'][hour] = vapor_heat
        result['QA'][hour] = anthropic_heat
        result['Tair'][hour] = weighted(heat_balance_result['Ta'], site.areaFractions)
        result['Tsurface'][hour] = weighted(heat_balance_result['Tg'], site.areaFractions)
        result['Tambient'][hour] = climate.Tb[hour]
        result['TaGround'][hour] = heat_balance_result['Ta']['ground']
        result['TaWater'][hour] = heat_balance_result['Ta']['water']
        result['TaLand'][hour] = heat_balance_result['Ta']['land']
        result['TgGround'][hour] = heat_balance_result['Tg']['ground']
        result['TgWater'][hour] = heat_balance_result['Tg']['water']
        result['TgLand'][hour] = heat_balance_result['Tg']['land']
    
    # def weighted24(valuesDict, areaFractions):
    #     """
    #     Area-weight one state variable across the three isolated surface processes.
    #     :param surfaceStates: dictionary keyed by surface type.
    #     :param areaFractions: dictionary keyed by surface type.
    #     :param stateKey: field to read from each surface state.
    #     :return: area-weighted state value.
    #     """
    #     res = [0.0]*24
    #     for t in range(24):
    #         res[t] = sum(float(areaFractions[name]) * float(valuesDict[name][t]) for name in areaFractions.keys())
    #     return np.array(res)

    # import pandas as pd
    # resultprint = pd.DataFrame({
    #         "Tambient": climate.Tb,
    #         "Tair": climate.Ta,
    #         "QS": weighted24(climate.QS, site.areaFractions),
    #         "QL": weighted24(climate.QL, site.areaFractions),
    #         "QA": weighted24(climate.QA, site.areaFractions),
    #         "Qsa": weighted24(climate.Qsa, site.areaFractions),
    #         "Qsg": weighted24(climate.Qsg, site.areaFractions),
    #         "QGFlux": weighted24(climate.QGFlux, site.areaFractions),
    #         "QHFlux": weighted24(climate.QHFlux, site.areaFractions),
    #         "QVFlux": weighted24(climate.QVFlux, site.areaFractions),
    #     })
    # resultprint['QV'] = resultprint['Qsa'] + resultprint['QVFlux'] - resultprint['QHFlux'] - resultprint['QA']
    # print(resultprint)
    # print('residual heat (W/m2): Air,', 
    #         sum(weighted24(climate.Qsa, site.areaFractions)),
    #         "Ground,", 
    #         sum(weighted24(climate.Qsg, site.areaFractions)))


    return result


def calculateSingleCase(climate:ClimateInfo, site:SiteInfo, settings:settingInfo, preheat:int=4):
    """
    Calculate model values for the requested UHI workflow.
    """
    for _ in range(int(preheat)):
        _runDailyCycle(climate, site, settings)
    result = _runDailyCycle(climate, site, settings)
    return result


def calculate(climateSeries, siteSeries, settings=None, show_progress=True, preheat=4):
    """
    Calculate model values for the requested UHI workflow.
    """
    climateSeries = np.array(climateSeries, dtype=object) if not isinstance(climateSeries, np.ndarray) else climateSeries
    siteSeries = np.array(siteSeries, dtype=object) if not isinstance(siteSeries, np.ndarray) else siteSeries
    if climateSeries.shape != siteSeries.shape:
        raise ValueError("climateSeries and siteSeries must have the same shape")

    dataShape = climateSeries.shape
    flatClimate = climateSeries.flatten()
    flatSite = siteSeries.flatten()
    flatResults = {key: [] for key in RESULT_COLUMNS}

    total_steps = len(flatSite) * 24 * (int(preheat) + 1)
    completed_steps = 0
    for i in range(len(flatSite)):
        case_settings = _resolveSettingsForSite(settings, flatSite[i])
        series_result = calculateSingleCase(flatClimate[i], flatSite[i], case_settings, preheat)
        for key, values in series_result.items():
            flatResults[key].append(values)
        completed_steps += 24 * (int(preheat) + 1)
        if show_progress:
            bar(completed_steps / total_steps, 'heatBalanceCalculation', 2)

    result = {}
    for key, value_list in flatResults.items():
        stacked = np.stack(value_list, axis=1)
        result[key] = stacked.reshape((24, *dataShape))
    return result
