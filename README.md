# MoosasUrban

MoosasUrban is a CTTC-based urban heat island modeling project with two file-oriented calculation entrypoints:

- `UHI/csv_calculator.py`: point/case simulation from a 24-hour CSV table
- `UHI/raster_calculator.py`: raster simulation from one 24-hour climate CSV plus per-field raster inputs
- `preprocess/process_BLD_SHP.py`: building-shapefile preprocessing for SVF, facade ratio, building ratio, FAR, and ventilation blockage rasters

## Installation

```bash
pip install -r requirements.txt
```

Python 3.12 is the intended runtime.

## Core package layout

- `UHI/modelInfo.py`: `ClimateInfo`, `SiteInfo`, `settingInfo`
- `UHI/calculator.py`: 24-hour heat-flux and heat-balance workflow
- `UHI/heatBalance.py`: coupled air/surface temperature solver
- `UHI/csv_calculator.py`: CSV CLI and CSV IO helpers
- `UHI/raster_calculator.py`: raster CLI and 24-hour multi-band TIFF export
- `input_protocol.md`: calculator IO protocol and CLI field definitions

Function names in `UHI` and `preprocess/process_BLD_SHP.py` use camelCase. Backward-compatible aliases are kept for older public names such as `csv_calculation`, `generate_fake_csv`, `process_building_shp`, and `query_point_metrics`.

## CSV calculator

```bash
python UHI/csv_calculator.py input.csv
python UHI/csv_calculator.py input.csv sim_out.csv --preheat 3 --dt none --HAir 15
```

Output columns:

- `caseid`
- `t`
- `QS`
- `QL`
- `QV`
- `QA`
- `Tair`
- `Tground`
- `Tambient`

## Raster calculator

The raster calculator now uses one climate CSV for the 24-hour meteorology and separate CLI arguments for the spatial site fields.

```bash
python UHI/raster_calculator.py climate.csv reference.tif
python UHI/raster_calculator.py climate.csv reference.tif sim_out.tif --SVF svf.tif --plotRatio plotRatio.tif --AreaBuilding building.tif
python UHI/raster_calculator.py climate.csv reference.tif output_dir --caseId case_001 --AreaRoad road.tif --AreaGround ground.tif --AreaLandscape lanscape.tif --AreaWater water.tif --AreaFacade facadeArea.tif
```

Behavior:

- `climate.csv` must follow the same hourly format as `csv_calculator.py`
- if `climate.csv` contains multiple `caseId`, the first one is used unless `--caseId` is provided
- `reference.tif` defines output extent, transform, resolution, and grid shape
- each site field is passed with `--FieldName path.tif`
- if `output_tif` is omitted, outputs are written as `output_00.tif ... output_23.tif` beside the reference raster

Each hourly TIFF contains 7 bands:

1. `QS`
2. `QL`
3. `QV`
4. `QA`
5. `Tair`
6. `Tground`
7. `Tambient`

## Building shapefile preprocessing

```bash
python preprocess/process_BLD_SHP.py --input buildings.shp --output-dir out_bld --resolution 100 --height-column height
python preprocess/process_BLD_SHP.py --input buildings.shp --resolution 100 --lat 23.1 --lon 113.3 --height-column height
```

The preprocessing script writes single-band rasters and a `manifest.json` in area mode. Point mode prints the queried metrics as JSON.

## Example test data

The repository includes small UHI test cases under `test/`:

- `test/data/csv/input_100_first24.csv`: the first 24 rows from `calibration/input_100.csv`
- `test/data/raster_UTM49/_uhiCalculate_100`: a 10x10 raster window cut from `workplace/raster_UTM49/_uhiCalculate_100`

Run the tests:

```bash
python test/testCsv.py
python test/testRaster.py
```

## Preprocessing

`preprocess/` contains research-oriented helpers for deriving raster inputs from shapefiles or ENVI-met models. Those scripts are not the main simulation entrypoints; use the two calculators above for normal runs.
