from __future__ import annotations

import os

from .vapor import vaporDifference
from .shortwave import shortWaveRadDifference
from .longwave import longWaveRadDifference
from .buildings import activityDifference
from .site import SiteInfo, ClimateInfo, settingsInfo, listObj2List
import numpy as np
from .constant import defaultValues, carHeatParameters


def bar(process, msg, dig=0, printed=True):
    q = round(process * 100, dig)
    hint = f"\r[{msg}: {'*' * round(q / 2)} {'_' * (50 - round(q / 2))} {q} %]"
    if printed:
        print(hint, end="")
    return hint


def _load_rasterio():
    import rasterio as rio
    return rio


def _is_raster_dataset(value):
    return hasattr(value, "sample") and callable(value.sample)


def _prepare_calculate_kwargs(kwargs):
    call_kwargs = dict(kwargs)
    show_progress = call_kwargs.pop('show_progress', True)
    settings = call_kwargs.pop('settings', None)
    if settings is not None and not isinstance(settings, settingsInfo):
        raise TypeError("settings must be an instance of settingsInfo")

    scalar_defaults = dict(defaultValues)
    scalar_defaults['k'] = 0.1
    if settings is not None:
        scalar_defaults.update(settings.default_values_dict())
    for key in scalar_defaults.keys():
        if key in call_kwargs:
            scalar_defaults[key] = call_kwargs.pop(key)

    base_car_params = call_kwargs.pop('_carParams', None)
    if base_car_params is not None:
        local_car_params = dict(base_car_params)
    elif settings is not None:
        local_car_params = settings.car_params_dict()
    else:
        local_car_params = dict(carHeatParameters)
    for key in carHeatParameters.keys():
        if key in call_kwargs:
            local_car_params[key] = call_kwargs.pop(key)

    load_coefficient = call_kwargs.pop('loadCoefficient', None)
    if load_coefficient is None and settings is not None:
        load_coefficient = np.array(settings.qlx, copy=True)
    if call_kwargs:
        invalid = ', '.join(sorted(call_kwargs.keys()))
        raise TypeError(f"unexpected calculate kwargs: {invalid}")

    shortwave_kwargs = {key: scalar_defaults[key] for key in ('a', 'k')}
    longwave_kwargs = dict(shortwave_kwargs)
    vapor_kwargs = {key: scalar_defaults[key] for key in ('ReferenceHeight', 'a', 'k')}
    activity_kwargs = {key: scalar_defaults[key] for key in
                       ('ReferenceHeight', 'a', 'k', 'coolingSetPoint', 'outSetPoint', 'inSetPoint')}
    activity_kwargs['_carParams'] = local_car_params
    if load_coefficient is not None:
        activity_kwargs['loadCoefficient'] = load_coefficient

    return show_progress, shortwave_kwargs, longwave_kwargs, vapor_kwargs, activity_kwargs


def getValue(rasterSeries, xy=None, noData=None):
    """mixture input of data:
    raster,list[raster],float,list[float]
    :param rasterSeries:
    :param xy:  sample position tuple, default None for float input
    :param noData: default NonData for the raster,default None
    :returns dataSeries used for input
    """
    single_value = not isinstance(rasterSeries, (list, tuple))
    if single_value:
        rasterSeries = [rasterSeries]
    series = []
    for raster in rasterSeries:
        if _is_raster_dataset(raster):
            if xy is None:
                raise ValueError("sampling position xy is None")
            a = next(raster.sample([xy]))[0]
        else:
            a = raster
        if np.isscalar(a) and a <= -999:
            a = noData
        series.append(a)
    if single_value and len(series) == 1:
        return series[0]
    return series


def _apply_site_overrides(site, xy, site_inputs):
    scalar_defaults = {
        'AreaFacade': 0.4,
        'SVF': 0.7,
        'plotRatio': 3.0,
    }
    array_slots = {'vaporWater', 'vaporLandscape', 'vaporGround', 'vaporRoof'}
    for slot_name, input_value in site_inputs.items():
        if input_value is None:
            continue
        sampled = getValue(input_value, xy, scalar_defaults.get(slot_name))
        if sampled is None:
            continue
        if slot_name in array_slots:
            setattr(site, slot_name, listObj2List(sampled))
        else:
            setattr(site, slot_name, sampled)


def _split_sampling_inputs(input_kwargs):
    climate_names = set(ClimateInfo.__slots__)
    site_names = set(SiteInfo.__slots__)
    climate_inputs = {}
    site_inputs = {}
    unknown = []

    for key, value in input_kwargs.items():
        if key in climate_names:
            climate_inputs[key] = value
        elif key in site_names:
            site_inputs[key] = value
        else:
            unknown.append(key)

    if unknown:
        raise TypeError(f"unexpected sampling inputs: {', '.join(sorted(unknown))}")
    return climate_inputs, site_inputs


def _open_sampling_source(value, rio):
    if isinstance(value, (str, os.PathLike)):
        return rio.open(value), True
    return value, False


def _prepare_sampling_sources(reference, climate_inputs, site_inputs, rio):
    opened_sources = []
    prepared_climate = {}
    prepared_site = {}

    prepared_reference = None
    if reference is not None:
        prepared_reference, should_close = _open_sampling_source(reference, rio)
        if should_close:
            opened_sources.append(prepared_reference)

    for mapping, prepared in ((climate_inputs, prepared_climate), (site_inputs, prepared_site)):
        for key, value in mapping.items():
            prepared_value, should_close = _open_sampling_source(value, rio)
            prepared[key] = prepared_value
            if should_close:
                opened_sources.append(prepared_value)

    return prepared_reference, prepared_climate, prepared_site, opened_sources


def _close_sampling_sources(opened_sources):
    for source in opened_sources:
        source.close()


def _resolve_sampling_grid(reference_source, all_inputs):
    if reference_source is not None:
        return raster2Grid(reference_source)
    for value in all_inputs.values():
        if _is_raster_dataset(value):
            return raster2Grid(value)
    return None


def _build_site_from_sample(sampled_site_values, default_total_area):
    init_area_slots = ('AreaBuilding', 'AreaRoad', 'AreaGround', 'AreaLandscape', 'AreaWater')
    init_only_slots = {'TotalArea', 'coolingLoad', *init_area_slots}
    array_slots = {'vaporWater', 'vaporLandscape', 'vaporGround', 'vaporRoof'}

    total_area = sampled_site_values.get('TotalArea')
    area_values = {slot: sampled_site_values[slot] for slot in init_area_slots if sampled_site_values.get(slot) is not None}
    if total_area is None:
        total_area = float(sum(area_values.values())) if area_values else default_total_area

    init_kwargs = {'totalArea': total_area}
    if total_area is not None and total_area <= 0:
        raise ValueError("TotalArea must be positive")

    ratio_arg_map = {
        'AreaBuilding': 'bldPercentage',
        'AreaRoad': 'roadPercentage',
        'AreaGround': 'groundPercentage',
        'AreaLandscape': 'landscapePercentage',
        'AreaWater': 'waterPercentage',
    }
    if total_area is not None:
        for area_slot, arg_name in ratio_arg_map.items():
            if area_slot in area_values:
                init_kwargs[arg_name] = area_values[area_slot] / total_area

    if sampled_site_values.get('coolingLoad') is not None:
        init_kwargs['coolingLoad'] = sampled_site_values['coolingLoad']

    site = SiteInfo(**init_kwargs)
    for slot_name in SiteInfo.__slots__:
        if slot_name in init_only_slots:
            continue
        value = sampled_site_values.get(slot_name)
        if value is None:
            continue
        if slot_name in array_slots:
            setattr(site, slot_name, listObj2List(value))
        else:
            setattr(site, slot_name, value)
    return site


def _build_climate_from_sample(sampled_climate_values):
    climate_kwargs = {}
    for slot_name in ClimateInfo.__slots__:
        value = sampled_climate_values.get(slot_name)
        if value is not None:
            climate_kwargs[slot_name] = value
    return ClimateInfo(**climate_kwargs)


def _sample_slot_values(xy, climate_inputs, site_inputs):
    sampled_climate = {}
    sampled_site = {}
    climate_defaults = {'Ta': 27.0, 'Velocity': 4.0, 'RadTotal': 0.0, 'RadDiffuse': 0.0, 'Rh': 70}
    site_defaults = {'SVF': 0.7}

    for slot_name in ClimateInfo.__slots__:
        value = climate_inputs.get(slot_name)
        sampled_climate[slot_name] = None if value is None else getValue(value, xy, climate_defaults.get(slot_name))

    for slot_name in SiteInfo.__slots__:
        value = site_inputs.get(slot_name)
        sampled_site[slot_name] = None if value is None else getValue(value, xy, site_defaults.get(slot_name))

    return sampled_climate, sampled_site


def rasterSampling(reference=None, **input_kwargs):
    rio = _load_rasterio()
    climate_inputs, site_inputs = _split_sampling_inputs(input_kwargs)
    reference_source, climate_inputs, site_inputs, opened_sources = _prepare_sampling_sources(reference, climate_inputs, site_inputs, rio)
    grid = _resolve_sampling_grid(reference_source, {**climate_inputs, **site_inputs})
    if grid is None: return None
    gridSize = grid[0, 1, 0] - grid[0, 0, 0]
    print(gridSize, gridSize * gridSize)
    siteSeries, climateSeries = [], []
    for i, lines in enumerate(grid):
        _cSeries, _sSeries = [], []
        for j, xy in enumerate(lines):
            sampled_climate, sampled_site = _sample_slot_values(xy, climate_inputs, site_inputs)
            _cSeries.append(_build_climate_from_sample(sampled_climate))
            _sSeries.append(_build_site_from_sample(sampled_site, gridSize * gridSize))
            bar((i * len(lines) + j) / len(lines) / len(grid), "Sampling", dig=2)
        siteSeries.append(_sSeries)
        climateSeries.append(_cSeries)

    _close_sampling_sources(opened_sources)
    return np.array(siteSeries), np.array(climateSeries)


def calculate(t: int, climateSeries: np.ndarray[ClimateInfo], siteSeries: np.ndarray[SiteInfo], **kwargs):
    """calculate the temperature change from series of climate and site info"""

    # record data shape
    if not isinstance(climateSeries,np.ndarray):
        climateSeries = np.array(climateSeries)
    if not isinstance(siteSeries,np.ndarray):
        siteSeries = np.array(siteSeries)
    if climateSeries.shape != siteSeries.shape:
        raise ValueError("climateSeries and siteSeries must have the same shape")
    dataShape = climateSeries.shape
    climateSeries = climateSeries.flatten()
    siteSeries = siteSeries.flatten()

    show_progress, shortwave_kwargs, longwave_kwargs, vapor_kwargs, activity_kwargs = _prepare_calculate_kwargs(kwargs)

    # initialize
    xLen = len(siteSeries)
    tempChanges = {'shortWave': np.zeros_like(siteSeries).astype(np.float16),
                   'longWave': np.zeros_like(siteSeries).astype(np.float16),
                   'vapor': np.zeros_like(siteSeries).astype(np.float16),
                   'activities': np.zeros_like(siteSeries).astype(np.float16),
                   'total': np.zeros_like(siteSeries).astype(np.float16),
                   'fixedTa': np.zeros_like(siteSeries).astype(np.float16)}

    # calculate
    for i in range(len(siteSeries)):
        tempChanges['shortWave'][i] = shortWaveRadDifference(t, climateSeries[i], siteSeries[i], **shortwave_kwargs)
        tempChanges['longWave'][i] = longWaveRadDifference(t, climateSeries[i], siteSeries[i], **longwave_kwargs)
        tempChanges['vapor'][i] = vaporDifference(t, climateSeries[i], siteSeries[i], **vapor_kwargs)
        tempChanges['activities'][i] = activityDifference(t, climateSeries[i], siteSeries[i], **activity_kwargs)
        tempChanges['total'][i] = tempChanges['shortWave'][i] + tempChanges['longWave'][i] + \
                                     tempChanges['vapor'][i] + tempChanges['activities'][i]
        tempChanges['fixedTa'][i] = tempChanges['total'][i] + climateSeries[i].Ta[t]
        if show_progress:
            bar(i / xLen, 'deltaTCalculation', 2)

    # reshape data
    for key in tempChanges.keys():
        tempChanges[key]=tempChanges[key].reshape(dataShape)
    return tempChanges


def sampleAndCalculate(t: int, reference=None, **kwargs):
    rio = _load_rasterio()
    climate_slot_names = set(ClimateInfo.__slots__)
    site_slot_names = set(SiteInfo.__slots__)
    input_kwargs = {key: value for key, value in kwargs.items() if key in climate_slot_names or key in site_slot_names}
    calculate_kwargs = {key: value for key, value in kwargs.items() if key not in input_kwargs}

    climate_inputs, site_inputs = _split_sampling_inputs(input_kwargs)
    reference_source, climate_inputs, site_inputs, opened_sources = _prepare_sampling_sources(reference, climate_inputs, site_inputs, rio)
    grid = _resolve_sampling_grid(reference_source, {**climate_inputs, **site_inputs})
    if grid is None: return None
    gridSize = grid[0, 1, 0] - grid[0, 0, 0]
    print(gridSize, gridSize * gridSize)
    tempChanges = {'shortWave': np.zeros((grid.shape[0], grid.shape[1])).astype(np.float32),
                   'longWave': np.zeros((grid.shape[0], grid.shape[1])).astype(np.float32),
                   'vapor': np.zeros((grid.shape[0], grid.shape[1])).astype(np.float32),
                   'activities': np.zeros((grid.shape[0], grid.shape[1])).astype(np.float32),
                   'total': np.zeros((grid.shape[0], grid.shape[1])).astype(np.float32),
                   'fixedTa': np.zeros((grid.shape[0], grid.shape[1])).astype(np.float32)}
    for i, lines in enumerate(grid):
        _cSeries, _sSeries = [], []
        for j, xy in enumerate(lines):
            sampled_climate, sampled_site = _sample_slot_values(xy, climate_inputs, site_inputs)
            _cSeries.append(_build_climate_from_sample(sampled_climate))
            _sSeries.append(_build_site_from_sample(sampled_site, gridSize * gridSize))
            bar((i * len(lines) + j) / len(lines) / len(grid), "Sampling", dig=2)
        changeSeries = calculate(t, np.array([_cSeries]), np.array([_sSeries]), **calculate_kwargs)
        for key in tempChanges.keys():
            tempChanges[key][i] = changeSeries[key]

    for key in tempChanges.keys():
        tempChanges[key] = np.reshape(tempChanges[key], (grid.shape[0], grid.shape[1]))
    _close_sampling_sources(opened_sources)

    return tempChanges


def raster2Grid(imgPath):
    rio = _load_rasterio()
    print('Extracting grid from raster')
    img = rio.open(imgPath)
    row, col = img.shape
    xy = [[img.xy(r, c) for c in range(col)] for r in range(row)]
    img.close()
    return np.array(xy)


def grid2Raster(xy: np.ndarray, data: np.ndarray, savingPath: str):
    rio = _load_rasterio()
    from rasterio.transform import Affine
    transform = Affine.translation(float(xy[0, 0, 0]), float(xy[0, 0, 1])) * Affine.scale(
        float(xy[0, 1, 0] - xy[0, 0, 0]), float(xy[0, 0, 0] - xy[0, 1, 0]))
    print(transform)
    for i, _ in enumerate(data):
        for j, _ in enumerate(data[i]):
            if data[i][j] < -100 or data[i][j] > 100:
                data[i][j] = 0
    img_new = rio.open(savingPath,
                       'w+',
                       driver='GTiff',
                       height=data.shape[0],
                       width=data.shape[1],
                       dtype=np.float32,
                       count=1,
                       transform=transform,
                       nodata=0,
                       )
    img_new.write(data, 1)
    img_new.close()
