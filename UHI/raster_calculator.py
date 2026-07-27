from __future__ import annotations

import argparse
import os
from pathlib import Path

import numpy as np

if __package__ in {None, ""}:
    import sys
    sys.path.append(str(Path(__file__).resolve().parents[1]))
    from UHI.calculator import RESULT_COLUMNS, calculate
    from UHI.csv_calculator import loadClimateFromCsv
    from UHI.modelInfo import ClimateInfo, SiteInfo, settingInfo, listObj2List, listObjToOptionalList
else:
    from .calculator import RESULT_COLUMNS, calculate
    from .csv_calculator import loadClimateFromCsv
    from .modelInfo import ClimateInfo, SiteInfo, settingInfo, listObj2List, listObjToOptionalList

RASTER_BAND_NAMES = ['QS', 'QL', 'QV', 'QA', 'Tair', 'Tsurface', 'Tambient']


def _loadRasterio():
    """
    Load input data required by the model workflow.
    """
    import rasterio as rio
    return rio


def _isRasterDataset(value):
    """
    Helper function used by the UHI calculation workflow.
    """
    return hasattr(value, "sample") and callable(value.sample)


def getValue(rasterSeries, xy=None, noData=None):
    """
    Helper function used by the UHI calculation workflow.
    """
    single_value = not isinstance(rasterSeries, (list, tuple))
    if single_value:
        rasterSeries = [rasterSeries]
    series = []
    for raster in rasterSeries:
        if _isRasterDataset(raster):
            if xy is None:
                raise ValueError("sampling position xy is None")
            value = next(raster.sample([xy]))[0]
        else:
            value = raster
        if np.isscalar(value) and value <= -999:
            value = noData
        series.append(value)
    if single_value and len(series) == 1:
        return series[0]
    return series


def _splitSamplingInputs(input_kwargs):
    """
    Helper function used by the UHI calculation workflow.
    """
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


def _openSamplingSource(value, rio):
    """
    Helper function used by the UHI calculation workflow.
    """
    if isinstance(value, (str, os.PathLike)):
        path_value = Path(value)
        if path_value.exists():
            return rio.open(path_value), True
        text_value = str(value).strip()
        if ',' in text_value:
            return [float(item) for item in text_value.split(',') if item.strip()], False
        return float(text_value), False
    return value, False


def _prepareSamplingSources(reference, climate_inputs, site_inputs, rio):
    """
    Build normalized inputs for the model workflow.
    """
    opened_sources = []
    prepared_climate = {}
    prepared_site = {}

    prepared_reference = None
    if reference is not None:
        prepared_reference, should_close = _openSamplingSource(reference, rio)
        if should_close:
            opened_sources.append(prepared_reference)

    for source_map, prepared_map in ((climate_inputs, prepared_climate), (site_inputs, prepared_site)):
        for key, value in source_map.items():
            prepared_value, should_close = _openSamplingSource(value, rio)
            prepared_map[key] = prepared_value
            if should_close:
                opened_sources.append(prepared_value)

    return prepared_reference, prepared_climate, prepared_site, opened_sources


def _closeSamplingSources(opened_sources):
    """
    Helper function used by the UHI calculation workflow.
    """
    for source in opened_sources:
        source.close()


def _resolveSamplingGrid(reference_source, all_inputs):
    """
    Helper function used by the UHI calculation workflow.
    """
    if reference_source is not None:
        return raster2Grid(reference_source)
    for value in all_inputs.values():
        if _isRasterDataset(value):
            return raster2Grid(value)
    return None


def _buildClimateFromSample(sampled_values):
    """
    Build normalized inputs for the model workflow.
    """
    climate_kwargs = {}
    for slot_name in ClimateInfo.__slots__:
        value = sampled_values.get(slot_name)
        if value is not None:
            climate_kwargs[slot_name] = value
    if 'Tb' not in climate_kwargs and 'Ta' in climate_kwargs:
        climate_kwargs['Tb'] = climate_kwargs['Ta']
    if 'Ta' not in climate_kwargs and 'Tb' in climate_kwargs:
        climate_kwargs['Ta'] = climate_kwargs['Tb']
    if 'Tg' not in climate_kwargs and 'Ta' in climate_kwargs:
        climate_kwargs['Tg'] = climate_kwargs['Ta']
    return ClimateInfo(**climate_kwargs)


def _copyClimateInfo(climate: ClimateInfo) -> ClimateInfo:
    """
    Helper function used by the UHI calculation workflow.
    """
    return ClimateInfo(**{
        slot_name: np.array(getattr(climate, slot_name), copy=True)
        for slot_name in ClimateInfo.__slots__
    })


def _buildSiteFromSample(sampled_values, default_total_area):
    """
    Build normalized inputs for the model workflow.
    """
    init_area_slots = ('AreaBuilding', 'AreaRoad', 'AreaGround', 'AreaLandscape', 'AreaWater')
    total_area = sampled_values.get('TotalArea')
    area_values = {slot: sampled_values[slot] for slot in init_area_slots if sampled_values.get(slot) is not None}

    if total_area is None:
        total_area = float(sum(area_values.values())) if area_values else default_total_area
    if total_area is None or total_area <= 0:
        raise ValueError("TotalArea must be positive")

    init_kwargs = {'TotalArea': total_area}
    for slot_name, area_value in area_values.items():
        init_kwargs[slot_name] = float(area_value) / float(total_area)

    site = SiteInfo(**init_kwargs)
    for slot_name in SiteInfo.__slots__:
        if slot_name in {'TotalArea', *init_area_slots}:
            continue
        value = sampled_values.get(slot_name)
        if value is None:
            continue
        setattr(site, slot_name, value)
    return site


def _sampleClimateAndSite(xy, climate_inputs, site_inputs):
    """
    Helper function used by the UHI calculation workflow.
    """
    climate_defaults = {
        'Ta': 27.0,
        'Tb': 27.0,
        'Tg': 27.0,
        'Velocity': 4.0,
        'RadTotal': 0.0,
        'RadDiffuse': 0.0,
        'Rh': 0.7,
        'Pressure': 101325.0,
    }
    site_defaults = {'SVF': 0.7}
    sampled_climate = {}
    sampled_site = {}

    for slot_name in ClimateInfo.__slots__:
        value = climate_inputs.get(slot_name)
        sampled_climate[slot_name] = None if value is None else getValue(value, xy, climate_defaults.get(slot_name))

    for slot_name in SiteInfo.__slots__:
        value = site_inputs.get(slot_name)
        sampled_site[slot_name] = None if value is None else getValue(value, xy, site_defaults.get(slot_name))

    return sampled_climate, sampled_site


def _expandClimateSeries(climate: ClimateInfo, shape):
    """
    Helper function used by the UHI calculation workflow.
    """
    climateSeries = np.empty(shape, dtype=object)
    for idx in np.ndindex(shape):
        climateSeries[idx] = _copyClimateInfo(climate)
    return climateSeries


def _sampleSiteOnly(xy, site_inputs):
    """
    Helper function used by the UHI calculation workflow.
    """
    site_defaults = {'SVF': 0.7}
    sampled_site = {}
    for slot_name in SiteInfo.__slots__:
        value = site_inputs.get(slot_name)
        sampled_site[slot_name] = None if value is None else getValue(value, xy, site_defaults.get(slot_name))
    return sampled_site


def calculateRasterSeries(reference, climate, settings=None, show_progress=True, preheat=2, chunkSize=256, **site_kwargs):
    """
    Calculate model values for the requested UHI workflow.
    """
    rio = _loadRasterio()
    _, site_inputs = _splitSamplingInputs(site_kwargs)
    reference_source, _, site_inputs, opened_sources = _prepareSamplingSources(reference, {}, site_inputs, rio)
    try:
        grid = _resolveSamplingGrid(reference_source, site_inputs)
        if grid is None:
            raise ValueError("Unable to resolve raster sampling grid from the provided inputs.")

        row_count, col_count = grid.shape[:2]
        grid_size = grid[0, 1, 0] - grid[0, 0, 0] if col_count > 1 else 1.0
        flat_xy = grid.reshape(-1, 2)
        result = {
            key: np.zeros((24, flat_xy.shape[0]), dtype=np.float32)
            for key in RESULT_COLUMNS
        }

        chunk_sites = []
        chunk_climates = []
        chunk_indices = []
        total = flat_xy.shape[0]

        def flushChunk():
            """
            Helper function used by the UHI calculation workflow.
            """
            if not chunk_sites:
                return
            chunk_result = calculate(
                np.array(chunk_climates, dtype=object),
                np.array(chunk_sites, dtype=object),
                settings=settings,
                show_progress=False,
                preheat=preheat,
            )
            for key in result.keys():
                result[key][:, chunk_indices] = chunk_result[key]
            chunk_sites.clear()
            chunk_climates.clear()
            chunk_indices.clear()

        for flat_index, xy in enumerate(flat_xy):
            sampled_site = _sampleSiteOnly(tuple(xy), site_inputs)
            chunk_sites.append(_buildSiteFromSample(sampled_site, grid_size * grid_size))
            chunk_climates.append(_copyClimateInfo(climate))
            chunk_indices.append(flat_index)
            if len(chunk_sites) >= int(chunkSize):
                flushChunk()
                if show_progress:
                    print(f"\r[rasterCalculation: {flat_index + 1}/{total}]", end="")

        flushChunk()
        if show_progress:
            print(f"\r[rasterCalculation: {total}/{total}]")

        for key, values in list(result.items()):
            result[key] = values.reshape((24, row_count, col_count))
        return grid, result
    finally:
        _closeSamplingSources(opened_sources)


def rasterSampling(reference=None, sampleClimate=True, **input_kwargs):
    """
    Helper function used by the UHI calculation workflow.
    """
    rio = _loadRasterio()
    climate_inputs, site_inputs = _splitSamplingInputs(input_kwargs)
    reference_source, climate_inputs, site_inputs, opened_sources = _prepareSamplingSources(reference, climate_inputs, site_inputs, rio)
    grid = _resolveSamplingGrid(reference_source, {**climate_inputs, **site_inputs})
    if grid is None:
        return None

    gridSize = grid[0, 1, 0] - grid[0, 0, 0]
    siteSeries = []
    climateSeries = [] if sampleClimate else None
    for lines in grid:
        site_row = []
        climate_row = [] if sampleClimate else None
        for xy in lines:
            sampled_climate, sampled_site = _sampleClimateAndSite(xy, climate_inputs, site_inputs)
            if sampleClimate:
                climate_row.append(_buildClimateFromSample(sampled_climate))
            site_row.append(_buildSiteFromSample(sampled_site, gridSize * gridSize))
        if sampleClimate:
            climateSeries.append(climate_row)
        siteSeries.append(site_row)

    _closeSamplingSources(opened_sources)
    if sampleClimate:
        return np.array(siteSeries, dtype=object), np.array(climateSeries, dtype=object)
    return np.array(siteSeries, dtype=object), None


def sampleAndCalculate(reference=None, settings=None, show_progress=True, preheat=2, **input_kwargs):
    """
    Helper function used by the UHI calculation workflow.
    """
    sampled = rasterSampling(reference=reference, **input_kwargs)
    if sampled is None:
        return None
    siteSeries, climateSeries = sampled
    if climateSeries is None:
        raise ValueError("sampleAndCalculate requires climate inputs or a pre-built climate series")
    return calculate(
        climateSeries,
        siteSeries,
        settings=settings,
        show_progress=show_progress,
        preheat=preheat,
    )


def raster2Grid(imgPath):
    """
    Helper function used by the UHI calculation workflow.
    """
    rio = _loadRasterio()
    should_close = False
    if _isRasterDataset(imgPath):
        img = imgPath
    else:
        img = rio.open(imgPath)
        should_close = True
    row, col = img.shape
    xy = [[img.xy(r, c) for c in range(col)] for r in range(row)]
    if should_close:
        img.close()
    return np.array(xy)


def _gridTransform(xy: np.ndarray):
    """
    Helper function used by the UHI calculation workflow.
    """
    from rasterio.transform import Affine
    return Affine.translation(float(xy[0, 0, 0]), float(xy[0, 0, 1])) * Affine.scale(
        float(xy[0, 1, 0] - xy[0, 0, 0]), float(xy[0, 0, 0] - xy[0, 1, 0]))


def _extractClimateBand(climateSource, attr_name: str, hour: int, shape):
    """
    Helper function used by the UHI calculation workflow.
    """
    if isinstance(climateSource, ClimateInfo):
        return np.full(shape, float(getattr(climateSource, attr_name)[hour]), dtype=np.float32)
    band = np.zeros(climateSource.shape, dtype=np.float32)
    for idx in np.ndindex(climateSource.shape):
        band[idx] = float(getattr(climateSource[idx], attr_name)[hour])
    return band


def writeHourlyRasterOutputs(reference_xy: np.ndarray, climateSource, results: dict, output_path: str):
    """
    Write calculated model outputs to disk.
    """
    rio = _loadRasterio()
    output_base = Path(output_path).resolve()
    output_base.parent.mkdir(parents=True, exist_ok=True)
    transform = _gridTransform(reference_xy)
    saved_paths = []

    for hour in range(24):
        if output_base.suffix.lower() == '.tif':
            tif_path = output_base.with_name(f"{output_base.stem}_{hour:02d}.tif")
        else:
            output_base.mkdir(parents=True, exist_ok=True)
            tif_path = output_base / f"hour_{hour:02d}.tif"

        band_stack = np.stack([
            results['QS'][hour].astype(np.float32),
            results['QL'][hour].astype(np.float32),
            results['QV'][hour].astype(np.float32),
            results['QA'][hour].astype(np.float32),
            results['Tair'][hour].astype(np.float32),
            results['Tsurface'][hour].astype(np.float32),
            results['Tambient'][hour].astype(np.float32),
        ], axis=0)

        with rio.open(
            tif_path,
            'w',
            driver='GTiff',
            height=band_stack.shape[1],
            width=band_stack.shape[2],
            dtype=np.float32,
            count=band_stack.shape[0],
            transform=transform,
            nodata=0,
        ) as dataset:
            dataset.write(band_stack)
            dataset.descriptions = tuple(RASTER_BAND_NAMES)
        saved_paths.append(str(tif_path))

    return saved_paths


def _parseCliValue(name: str, raw_value: str):
    """
    Parse user-provided values into model-ready values.
    """
    if name == 'dt':
        lowered = raw_value.strip().casefold()
        if lowered == 'none':
            return None
        if lowered == 'default':
            return 'default'
        return float(raw_value)
    if name == 'qlx':
        return [float(item) for item in raw_value.split(',') if item.strip()]
    if name == 'conductionMode':
        return raw_value
    if name in {'outerMaxIter'}:
        return int(raw_value)
    return float(raw_value)


def _collectSettingKwargs(args_namespace):
    """
    Helper function used by the UHI calculation workflow.
    """
    kwargs = {}
    for field_name in settingInfo.__slots__:
        raw_value = getattr(args_namespace, field_name, None)
        if raw_value is None:
            continue
        if field_name == 'qlx':
            kwargs['qlxSeries'] = _parseCliValue(field_name, raw_value)
        else:
            kwargs[field_name] = _parseCliValue(field_name, raw_value)
    return kwargs


def _buildArgParser():
    """
    Build normalized inputs for the model workflow.
    """
    parser = argparse.ArgumentParser(description="Run UHI raster calculation and export 24 hourly multi-band GeoTIFFs.")
    parser.add_argument('climate_csv', help='Climate CSV path using the same hourly input format as csv_calculator.py.')
    parser.add_argument('reference', help='Reference raster path used to define the output grid.')
    parser.add_argument('output_tif', nargs='?', help='Output TIFF base path. Defaults to output.tif next to the reference raster.')
    parser.add_argument('--caseId', dest='caseId', help='Optional caseId to select from the climate CSV when it contains multiple cases.')
    parser.add_argument('--start-hoy', dest='start_hoy', type=int, help='Optional start HOY when climate_csv is a MOOSAS classic weather csv.')
    parser.add_argument('--end-hoy', dest='end_hoy', type=int, help='Optional end HOY when climate_csv is a MOOSAS classic weather csv.')
    parser.add_argument('--preheat', type=int, default=2, help='Number of warm-up daily cycles before the reported day.')
    parser.add_argument('--show-progress', action='store_true', help='Print calculation progress.')
    for field_name in settingInfo.__slots__:
        parser.add_argument(f'--{field_name}', dest=field_name, help=f'Override settingInfo.{field_name}.')
    for field_name in ClimateInfo.__slots__:
        parser.add_argument(f'--{field_name}', dest=field_name, help=f'Optional climate override for {field_name} using a scalar or comma-separated 24-value series.')
    for field_name in SiteInfo.__slots__:
        parser.add_argument(f'--{field_name}', dest=field_name, help=f'Site input raster/scalar/series for {field_name}.')
    return parser


def main():
    """
    Run the command-line entrypoint.
    """
    parser = _buildArgParser()
    args = parser.parse_args()
    reference_path = Path(args.reference).resolve()
    output_base = Path(args.output_tif).resolve() if args.output_tif else reference_path.with_name('output.tif')
    setting_kwargs = _collectSettingKwargs(args)
    settings = settingInfo(**setting_kwargs) if setting_kwargs else None

    climate = loadClimateFromCsv(
        str(Path(args.climate_csv).resolve()),
        caseId=args.caseId,
        start_hoy=args.start_hoy,
        end_hoy=args.end_hoy,
    )
    for field_name in ClimateInfo.__slots__:
        value = getattr(args, field_name, None)
        if value is None:
            continue
        parsed_value, _ = _openSamplingSource(value, _loadRasterio())
        if _isRasterDataset(parsed_value):
            parsed_value.close()
            raise ValueError(f"climate override {field_name} does not accept raster input; use the climate CSV instead")
        if field_name == 'coolingLoad':
            setattr(climate, field_name, listObjToOptionalList(parsed_value))
        else:
            setattr(climate, field_name, listObj2List(parsed_value))
    climate = ClimateInfo(**{slot_name: getattr(climate, slot_name) for slot_name in ClimateInfo.__slots__})

    site_kwargs = {}
    for field_name in SiteInfo.__slots__:
        value = getattr(args, field_name, None)
        if value is not None:
            site_kwargs[field_name] = value

    reference_xy, results = calculateRasterSeries(
        str(reference_path),
        climate,
        settings=settings,
        show_progress=args.show_progress,
        preheat=args.preheat,
        **site_kwargs,
    )
    saved_paths = writeHourlyRasterOutputs(reference_xy, climate, results, str(output_base))
    for path in saved_paths:
        print(path)

if __name__ == '__main__':
    main()
