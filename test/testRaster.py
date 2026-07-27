from __future__ import annotations

import sys
from pathlib import Path

import rasterio

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from UHI.csv_calculator import loadClimateFromCsv
from UHI.modelInfo import settingInfo
from UHI.raster_calculator import calculateRasterSeries, writeHourlyRasterOutputs


def runRasterCalculationTest() -> list[str]:
    """
    Run the 24-hour raster calculator test case and validate the hourly GeoTIFF outputs.
    :return: generated hourly GeoTIFF paths.
    """
    climate_csv = PROJECT_ROOT / "test" / "data" / "csv" / "input_100_first24.csv"
    raster_dir = PROJECT_ROOT / "test" / "data" / "raster_UTM49" / "_uhiCalculate_100"
    output_dir = PROJECT_ROOT / "test" / "output" / "raster"
    output_tif = output_dir / "output.tif"
    output_dir.mkdir(parents=True, exist_ok=True)

    climate = loadClimateFromCsv(str(climate_csv))
    reference_xy, results = calculateRasterSeries(
        str(raster_dir / "plotRatio.tif"),
        climate,
        settings=settingInfo(),
        show_progress=False,
        preheat=1,
        chunkSize=32,
        plotRatio=str(raster_dir / "plotRatio.tif"),
        AreaBuilding=str(raster_dir / "building.tif"),
        AreaRoad=str(raster_dir / "road.tif"),
        AreaGround=str(raster_dir / "ground.tif"),
        AreaLandscape=str(raster_dir / "lanscape.tif"),
        AreaWater=str(raster_dir / "water.tif"),
        AreaFacade=str(raster_dir / "facadeArea.tif"),
        SVF=str(raster_dir / "SVF.tif"),
    )
    saved_paths = writeHourlyRasterOutputs(reference_xy, climate, results, str(output_tif))

    if len(saved_paths) != 24:
        raise AssertionError(f"Expected 24 hourly rasters, got {len(saved_paths)}")
    with rasterio.open(saved_paths[12]) as dataset:
        if dataset.count != 7:
            raise AssertionError(f"Expected 7 output bands, got {dataset.count}")
        if dataset.descriptions != ("QS", "QL", "QV", "QA", "Tair", "Tsurface", "Tambient"):
            raise AssertionError(f"Unexpected band descriptions: {dataset.descriptions}")
        sample = dataset.read()[:, 0, 0].tolist()

    print(f"Raster test output count: {len(saved_paths)}")
    print(f"Hour 12 band sample: {sample}")
    print(f"Raster test output dir: {output_dir}")
    return saved_paths


if __name__ == "__main__":
    runRasterCalculationTest()
