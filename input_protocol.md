# UHI Input Protocol

## Overview

The UHI package provides two CLI calculators:

- `UHI/csv_calculator.py`
- `UHI/raster_calculator.py`

Both calculators:

- always run a full 24-hour simulation
- support `--preheat` to control warm-up cycles
- support overriding any `settingInfo` field from CLI

## CSV Calculator

### CLI

```bash
python UHI/csv_calculator.py input.csv
python UHI/csv_calculator.py input.csv sim_out.csv --preheat 3 --dt none --HAir 15 --UDeepWater 1.2
```

If `output_csv` is omitted, the default output path is `output.csv` beside the input CSV.

### Required input columns

- `caseId`
- `t`

Each `caseId` must contain exactly one row for each hour `t = 0..23`.

### Supported climate columns

- `Tb`
- `Ta`
- `Tg`
- `Velocity`
- `RadTotal`
- `RadDiffuse`
- `Rh`
- `Pressure`

### Supported site columns

- `TotalArea`
- `AreaBuilding`
- `AreaRoad`
- `AreaGround`
- `AreaLandscape`
- `AreaWater`
- `AreaFacade`
- `SVF`
- `plotRatio`
- `kS`
- `greenRoofPercentage`
- `groundInfiltratePercentage`
- `SDL`
- `SDG`
- `SRTL`
- `SRTG`
- `fPSA`
- `CL`
- `CG`
- `vaporWater`
- `vaporLandscape`
- `vaporGround`
- `vaporRoof`
- `coolingLoad`

Site scalar columns must remain constant within one `caseId`.

Time-varying site columns are:

- `vaporWater`
- `vaporLandscape`
- `vaporGround`
- `vaporRoof`
- `coolingLoad`

### Output columns

- `caseid`
- `t`
- `QS`
- `QL`
- `QV`
- `QA`
- `Tair`
- `Tground`
- `Tambient`

Meanings:

- `QS`: short-wave heat flux density
- `QL`: long-wave heat flux density
- `QV`: vapor heat flux density
- `QA`: anthropogenic heat flux density
- `Tair`: simulated air temperature `Ta`
- `Tground`: simulated weighted ground temperature `Tg`
- `Tambient`: background temperature `Tb`

## Raster Calculator

### CLI

```bash
python UHI/raster_calculator.py climate.csv reference.tif
python UHI/raster_calculator.py climate.csv reference.tif sim_out.tif --SVF svf.tif --plotRatio plotRatio.tif --AreaBuilding building.tif
python UHI/raster_calculator.py climate.csv reference.tif out_dir --caseId case_001 --AreaRoad road.tif --AreaGround ground.tif --AreaLandscape lanscape.tif --AreaWater water.tif --AreaFacade facadeArea.tif
```

If `output_tif` is omitted, the default output base path is `output.tif` beside the reference raster.

### Positional arguments

1. `climate_csv`
   Uses the same input format as `csv_calculator.py`.
   If it contains multiple `caseId`, the first one is used by default, or you can specify `--caseId`.
2. `reference.tif`
   Defines output extent, transform, grid shape, and pixel size.
3. `output_tif` or output directory
   Optional.

### Climate input rule

Climate input is read from the CSV, not from rasters.

Supported climate CSV columns:

- `Tb`
- `Ta`
- `Tg`
- `Velocity`
- `RadTotal`
- `RadDiffuse`
- `Rh`
- `Pressure`

Optional CLI climate overrides are still allowed, but only as:

- scalar values, such as `--Tb 30`
- comma-separated 24-hour series, such as `--Velocity 1.0,1.0,1.1,...`

Raster climate overrides are not allowed.

### Site raster inputs

Site fields are passed through CLI flags, typically as raster paths:

- `--TotalArea`
- `--AreaBuilding`
- `--AreaRoad`
- `--AreaGround`
- `--AreaLandscape`
- `--AreaWater`
- `--AreaFacade`
- `--kS`
- `--greenRoofPercentage`
- `--groundInfiltratePercentage`
- `--vaporWater`
- `--vaporLandscape`
- `--vaporGround`
- `--vaporRoof`
- `--coolingLoad`
- `--SVF`
- `--plotRatio`
- `--SDL`
- `--SDG`
- `--SRTL`
- `--SRTG`
- `--fPSA`
- `--CL`
- `--CG`

Each site argument may be:

- a raster path
- a scalar value
- a comma-separated 24-hour series

Typical usage is raster paths for spatial fields like:

- `--plotRatio plotRatio.tif`
- `--AreaBuilding building.tif`
- `--AreaRoad road.tif`
- `--AreaGround ground.tif`
- `--AreaLandscape lanscape.tif`
- `--AreaWater water.tif`
- `--AreaFacade facadeArea.tif`
- `--SVF SVF.tif`

### Output files

The raster calculator writes 24 hourly multi-band GeoTIFF files.

If the output base is `output.tif`, the actual files are:

- `output_00.tif`
- `output_01.tif`
- ...
- `output_23.tif`

If the output argument has no `.tif` suffix, it is treated as a directory:

- `hour_00.tif`
- `hour_01.tif`
- ...
- `hour_23.tif`

### Band order

Each hourly TIFF contains 7 bands:

1. `QS`
2. `QL`
3. `QV`
4. `QA`
5. `Tair`
6. `Tground`
7. `Tambient`

## `settingInfo` CLI overrides

Both calculators support every `settingInfo` field as a CLI override.

Supported fields:

- `qlx`
- `intensity`
- `annualDistance`
- `FE`
- `fuelDensity`
- `NHC`
- `a`
- `k`
- `ReferenceHeight`
- `coolingSetPoint`
- `outSetPoint`
- `inSetPoint`
- `AbsoRoad`
- `AbsoGround`
- `AbsoLandscape`
- `AbsoWater`
- `dt`
- `GroundTemperature`
- `zRef`
- `HAir`
- `UDeepLand`
- `UDeepGround`
- `UDeepWater`
- `CLand`
- `CGround`
- `CWater`
- `conductionMode`
- `c1`
- `outerMaxIter`
- `outerTolerance`
- `innerTolerance`
- `relaxationFactor`
- `BruntEmissivity`

Special parsing rules:

- `--dt none` means steady-state mode
- `--dt default` restores the built-in default timestep
- `--qlx` expects a comma-separated 24-value series
- `--conductionMode` is parsed as text
