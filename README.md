> **Energy v7 / UHI 逐时耦合重建分支**：使用 `uhiSimulation(energy_model="v7")`。
> [模型、参数与复现方法](docs/energy_v7_coupling.md) · [本次验证报告](docs/energy_v7_validation.md)。
> 从既有 main 重建，并非旧交付 ZIP 的逐字恢复。

# CETUS

CETUS combines an urban canopy thermal model (`UHI`), building radiation (`RAD`), and building energy calculation (`ENERGY`). **EPW weather + building-footprint SHP is the recommended input combination**: it retains the geometry, solar exposure, and building properties needed for thermal–energy feedback studies. CSV and Raster provide alternative interfaces for prescribed urban-model inputs.

This guide describes the distributed code. The accompanying technical report, `Supplement.tex`, explains the physical framework in Sections 1–5, calibration in Section 6, and calibrated parameters in Section 8. See [Report and implementation alignment](#report-and-implementation-alignment) for differences that affect reproduction of the report.

## Contents

- [Installation and quick verification](#installation-and-quick-verification)
- [Choose an input workflow](#choose-an-input-workflow)
- [Recommended: EPW + SHP](#recommended-epw--shp)
- [Optional: CSV](#optional-csv)
- [Optional: Raster](#optional-raster)
- [Parameters and scenario changes](#parameters-and-scenario-changes)
- [Outputs and units](#outputs-and-units)
- [Report and implementation alignment](#report-and-implementation-alignment)
- [Troubleshooting and reproducibility](#troubleshooting-and-reproducibility)
- [Source map](#source-map)

## Installation and quick verification

Use Python 3.12. Run commands from the repository root:

```bash
python -m venv .venv
# Windows CMD:
.venv\Scripts\activate.bat
# Linux / macOS shell:
# source .venv/bin/activate
python -m pip install -r requirements.txt
python test.py
```

`test.py` reads `input_2026_05_27.csv` beside the script, runs one preheat cycle followed by a reported 24-hour simulation, validates the results, and prints all hourly values. It saves `outputs/csv/output.csv`. This is the bundled verification case; EPW/SHP examples below require your own data.

The requirements include NumPy, pandas, rasterio, GeoPandas, Shapely, pyproj, psutil, and Optuna. Retain the package data and parameter directories when distributing the repository.

### Native engines and Code Ocean

The CSV/UHI calculation uses Python. Full EPW + SHP processing also calls native programs:

| Component | Executable / source | Current platform behavior |
| --- | --- | --- |
| Weather sky conversion | `RAD/bin/weather/epw2wea.exe`, `gendaymtx.exe` | Python explicitly selects Windows executable paths |
| Ray tracing | `RAD/bin/rad/MoosasRad.exe`; source `MoosasRad.go` | Python explicitly selects a Windows executable path |
| JSON energy kernel | `ENERGY/bin/MoosasEnergy.exe` or `MoosasEnergy`; source `MoosasEnergy-new.go` | Selects Windows/Linux; automatically rebuilds if missing or older than source |
| Legacy energy kernel | `ENERGY/bin/MoosasEnergy-legacy.exe` or `MoosasEnergy-legacy` | Selects Windows/Linux |

Install a compatible Go compiler if the JSON kernel needs rebuilding, or provide a compatible precompiled engine with `executable_path`. The repository's energy notes specify Go 1.20+.

For a Linux Code Ocean capsule, start with `python test.py`. A fresh full radiation calculation additionally needs Linux weather/ray engines **and executable-selection changes in `WEA/epw.py` and `RAD/rad_engine.py`**. Installing Python requirements alone does not provide these engines. Reusing a compatible annual `rad.npz` skips those radiation executable calls. Include `UHI/params`, `ENERGY/data`, and the chosen engine binaries/source in the capsule.

## Choose an input workflow

| Input | Interface | Calculation scope | Typical use |
| --- | --- | --- | --- |
| **EPW + SHP** | Python `ENERGY.Neighborhood` | Weather → geometry → RAD → UHI → building energy, with explicit feedback updates | Building and urban-form scenarios |
| CSV | Python `csvCalculation()`; bundled `python test.py` | Independent 24-hour cases with prescribed site/weather | Observations, examples, parameter studies |
| Climate CSV + site rasters | CLI `UHI/raster_calculator.py` or Python `calculateRasterSeries()` | Independent 24-hour calculations at raster cell centers | Spatial temperature and flux maps |

The bundled CSV describes an urban-model case. A complete building-energy calculation additionally needs a building inventory, construction/operation parameters, and radiation inputs.

## Recommended: EPW + SHP

### Prepare the inputs

- **EPW:** non-leap-year, 8760-hour weather with background temperature, humidity, pressure, wind, and radiation. The file supplies location and building-climate classification. A representative suburban/background station is appropriate for canopy-to-background comparisons.
- **SHP:** valid building footprint polygons and accompanying `.shx`, `.dbf`, and `.prj` files. Heights must be positive numeric values in metres; default field `height`, or pass an exact `height_field` name.
- **Coordinates:** use a projected CRS with metre units. `Neighborhood.readSHP()` does not reproject geometries before area, distance, and radiation calculations.
- **IDs:** preserve unique building `index` identifiers when reusing radiation. If absent, RAD creates them from SHP row indices.
- **Cover fractions:** provide `fvc` (vegetation) and `fwc` (water), each on a 0–1 scale. Missing values use `SiteInfo` defaults and produce a warning. Footprints alone do not describe observed vegetation/water coverage.

`readSHP()` rasterizes heights internally at the default 5 m resolution, derives `SiteInfo` through `fromTIF()`, creates buildings, synchronizes total GFA, and loads the inferred LCZ parameter JSON. Site extent follows the rasterized SHP bounds; prepare a suitable study-area layer and inspect `ng.site.TotalArea`.

### Explicit Python workflow

Save as `run_epw_shp.py` in the repository root, replacing the input filenames and cover fractions:

```python
from pathlib import Path
import numpy as np
import pandas as pd
from ENERGY import Neighborhood

root = Path(__file__).resolve().parent
out = root / "outputs" / "epw_shp"
out.mkdir(parents=True, exist_ok=True)
ng = Neighborhood()
ng.readEPW(root / "data" / "weather.epw", date="07-22", smooth_weather=False)
ng.readSHP(root / "data" / "buildings.shp", height_field="height",
           fvc=0.20, fwc=0.02)
ng.radSimulation(sample=0.3)
ng.sampleInstance(residential=0.3, office=0.3, commercial=0.3, hotel=0.1,
                  school=0.0, medical=0.0, seed=42)

# Sequential urban → energy calculation: zero initial building return;
# traffic remains enabled. Missing coolingLoad instead uses an empirical proxy.
for climate in ng.climate:
    climate.coolingLoad = [0.0] * 24
urban = ng.uhiSimulation(preheat=4)
energy = ng.energySimulationSteady(output_dir=out / "energy")

# Each urban result has shape (24, number_of_days).
frame = pd.DataFrame({
    "HOY": np.concatenate([c.HOY for c in ng.climate]),
    **{name: values.T.reshape(-1) for name, values in urban.items()},
})
frame.to_csv(out / "urban.csv", index=False)
print(frame.to_string(index=False))
print("HVAC electricity (kWh):",
      sum(sum(b["hvac_electricity_kwh"]) for b in energy))
ng.saveJSON(out / "neighborhood.json")
ng.writeEPW(out / "urban_weather.epw")
```

Run `python run_epw_shp.py`. ENERGY uses the simulated `Ta/Tg`, then writes net heat feedback to `ng.climate`. A subsequent UHI call consumes it. The exported table above is the sequential urban result before that additional update.

### Dates, radiation reuse, and archetypes

| Interface / option | Meaning |
| --- | --- |
| `readEPW(..., date="07-22")` | One natural day in EPW local standard time; default date `08-20` |
| `readEPW(..., start_hoy=4800, end_hoy=4968)` | Zero-based **half-open** interval `[4800, 4968)`, seven days |
| Partial-day windows | Expanded to whole days internally; UHI/ENERGY returns expanded days; `writeEPW()` updates only the requested interval |
| `fromFile(..., start_hoy=0, end_hoy=23)` | Convenience constructor uses an **inclusive** end and passes `end_hoy + 1` to `readEPW()` |
| `smooth_weather=True, max_temp_delta=3.0` | Default EPW preprocessing interpolates rain hours and temperature jumps ≥ 3 °C/h across weather fields; `False` retains source forcing |
| `climate_zone=...` | Override building-climate classification; distinct from the site's LCZ |
| `radSimulation(sample=1.0)` | Trace all buildings |
| `radSimulation(sample=0.3)` | Default: trace a building subset, interpolate remaining buildings by IDW |
| `radSimulation(radFile="data/buildings_rad.npz")` | Reuse matching annual RAD data; skip fresh sky conversion and ray tracing |
| `sampleInstance(..., seed=42)` | Reproducible archetype sampling from `ENERGY/data/archetype/mean.csv`, `std.csv`, and schedules |

`sampleInstance()` requires buildings, weather classification, and RAD. Its six positive type weights are normalized and sampled **by building**, rather than enforced as exact GFA shares. Defaults: residential 0.3, office 0.3, commercial 0.3, hotel 0.1, school/medical 0. `standard="GB55015-2021"` is the default. For controlled functional GFA shares, assign/check individual buildings and calculate the achieved GFA mix.

`Neighborhood.fromFile(shpFile=..., epwFile=..., start_hoy=0, end_hoy=23, fvc=0.2, fwc=0.02, sample=0.3, smooth_weather=False)` performs weather → SHP → RAD → archetype initialization. Use the explicit sequence for a custom height field or fixed archetype seed. `Neighborhood.fromSHP(..., radFile=...)` initializes using existing radiation; it does not compute missing RAD automatically.

### Energy and feedback calls

Prefer `ng.energySimulationSteady(...)` for the JSON sensible-load kernel. It returns whole-building hourly thermal demand and HVAC electricity, saves audit files, and updates building/climate feedback. `ng.energySimulation(...)` selects the separate legacy engine and decomposition. Recompute all buildings when switching kernels; cached feedback from different kernels cannot be mixed.

```python
# One ENERGY → UHI update after initialization:
updated_urban = ng.uhiSimulation(
    preheat=4,
    energy_model="steady",
    energy_kwargs={"output_dir": "outputs/feedback_energy"},
)
```

This call runs ENERGY using current `Ta/Tg`, then UHI. It performs **one explicit update**. `preheat` controls urban daily warm-up, not the number of energy–urban exchanges. Repeated calls update the state again; they do not automatically restart from a common initial state or test convergence.

For an externally managed iterative experiment, retain an initialized snapshot, record complete `Ta`, `Tg`, feedback, and electricity sequences after each pass, and enforce chosen thermal/energy tolerances and a maximum iteration count. Address the [report/code differences](#report-and-implementation-alignment) before treating such an experiment as reproduction of the report's converged C state.

## Optional: CSV

### Included example and custom Python interface

```bash
python test.py
```

`UHI/csv_calculator.py` exposes Python functions and currently has **no command-line `main()`**. `python UHI/csv_calculator.py input.csv` does not execute a calculation. For a custom case, save/run a script in the root:

```python
from UHI.csv_calculator import csvCalculation
from UHI.modelInfo import settingInfo

settings = settingInfo.loadJSON("UHI/params/LCZ2.json")
settings.AbsoRoof = 0.45
csvCalculation("input_2026_05_27.csv", "outputs/csv/custom.csv",
               settings=settings, preheat=4, show_progress=True)
```

Pass physical parameters through `settings`. Extra keywords are forwarded to `calculate()`, whose options are `settings`, `show_progress`, and `preheat`. Passing `HAir=15` or `dt=None` directly to `csvCalculation()` is invalid.

With `settings=None`, the calculator derives each site's LCZ and loads its JSON, falling back to LCZ5 when an LCZ file is unavailable. An explicit settings object applies to all cases.

### CSV schema

Column matching is case-insensitive. Every `caseId` must contain one row for each integer `t=0..23`. Multiple IDs are independent cases. Calculation sorts the hours; output retains input row order. Static site values must remain constant within a case.

| Columns | Units / behavior |
| --- | --- |
| `caseId`, `t` | Required case identifier and hour 0–23 |
| `TotalArea` | Positive site area, m²; can be inferred from supplied area components |
| `AreaBuilding`, `AreaRoad`, `AreaGround`, `AreaLandscape`, `AreaWater` | Absolute areas, m²; supply all five plus `TotalArea` for reproducible composition |
| `AreaFacade` | Façade area, m²; small-value conversion described below |
| `SVF`, `plotRatio`, `kS` | Sky view factor, FAR, and ventilation blockage descriptor; dimensionless |
| `GFA` | Total floor area, m², synchronized with `plotRatio * TotalArea` |
| `Tb`, `Ta`, `Tg` | Background, initial canopy-air, initial surface temperatures, °C |
| `Velocity` | Wind speed, m/s |
| `RadTotal`, `RadDiffuse` | Global/diffuse horizontal radiation, hourly mean W/m² |
| `Rh`, `Pressure` | Relative humidity **0–1**, pressure **Pa** |
| `coolingLoad` | Hourly **signed net building heat feedback per site area**, W/m²; empty → empirical proxy, zero → zero building return |
| `solarBuilding` | Building incident radiation per footprint area, W/m²; provide 24 finite values; missing → `RadTotal` |
| `greenRoofPercentage`, `SDL`, `SDG`, `SRTL`, `SRTG`, `fPSA`, `CL`, `CG` | Static green-roof, shading, transmission, correction fractions |

Missing climate fields use defaults. Supplying only `Tb` or `Ta` initializes both; missing `Tg` uses the initialized air temperature. Supply forcing explicitly for scientific runs. `coolingLoad` belongs to `ClimateInfo`. Old `vaporWater/vaporLandscape/vaporGround/vaporRoof` site columns are not implemented by current `SiteInfo`.

`SiteInfo` normalizes the component areas, and rescales building/vegetation/water when their combined fraction exceeds 0.8. Inspect constructed areas; raw areas may not be preserved. Its constructor interprets `AreaFacade < 20` as a ratio and multiplies by `TotalArea`, which matters for small sites.

For a headerless classic Moosas weather CSV plus one-case 24-hour site template:

```python
from UHI.csv_calculator import csvCalculationFromWeatherCsv

csvCalculationFromWeatherCsv(
    "weather.csv", "input_2026_05_27.csv", "outputs/csv/weather_result.csv",
    start_hoy=4800, end_hoy=4967, preheat=4,
)
```

This adapter uses **inclusive** `end_hoy`, expands to natural days, saves `weather_result__input.csv`, and processes daily cases independently. Its time convention differs from `Neighborhood.readEPW()`.

## Optional: Raster

### Climate CSV + site-raster CLI

```bash
python UHI/raster_calculator.py --help
python UHI/raster_calculator.py input_2026_05_27.csv data/reference.tif outputs/raster --preheat 4 --SVF data/SVF.tif --plotRatio data/FAR.tif --TotalArea 10000 --AreaBuilding data/building_area.tif --AreaRoad data/road_area.tif --AreaGround data/ground_area.tif --AreaLandscape data/vegetation_area.tif --AreaWater data/water_area.tif --AreaFacade data/facade_area.tif
```

For a 100 m × 100 m cell, `TotalArea=10000` m². Area rasters must contain m² per cell, not fractions. Align all rasters to the same projected metre CRS, resolution, extent, and grid. Sampling uses reference cell centers; there is no automatic CRS reprojection. Use north-up square cells with at least two rows and columns. Preprocess missing values: the workflow does not propagate a common NoData mask.

- The climate CSV provides one 24-hour forcing across the grid. `--caseId CASE` selects a case; default is the first case.
- Site flags use exact `SiteInfo` names and accept raster filenames or scalars. Use static numeric values for static site fields.
- Climate overrides accept scalars or comma-separated 24-hour series, e.g. `--Velocity 2.0`; climate rasters are rejected.
- Model flags use exact `settingInfo` names, e.g. `--HAir 200 --dt 3600 --AbsoRoof 0.45`. Any setting override constructs a fresh `settingInfo` from generic defaults plus overrides, replacing automatic LCZ selection. Use Python to retain an LCZ calibration while changing selected parameters.
- CLI preheat defaults to **2** cycles; set `--preheat 4` for the normal Python UHI default.
- TIFF base `outputs/map.tif` produces `map_00.tif` … `map_23.tif`; directory `outputs/raster` produces `hour_00.tif` … `hour_23.tif`. Omitted output produces `output_00.tif` … beside the reference raster.

### Python interface with LCZ parameters

```python
import rasterio
from UHI.csv_calculator import loadClimateFromCsv
from UHI.modelInfo import settingInfo
from UHI.raster_calculator import calculateRasterSeries, writeHourlyRasterOutputs

climate = loadClimateFromCsv("input_2026_05_27.csv")
settings = settingInfo.loadJSON("UHI/params/LCZ2.json")
settings.AbsoRoof = 0.45
xy, result = calculateRasterSeries(
    "data/reference.tif", climate, settings=settings,
    preheat=4, chunkSize=256, show_progress=True,
    TotalArea=10000, SVF="data/SVF.tif", plotRatio="data/FAR.tif",
    AreaBuilding="data/building_area.tif", AreaRoad="data/road_area.tif",
    AreaGround="data/ground_area.tif", AreaLandscape="data/vegetation_area.tif",
    AreaWater="data/water_area.tif", AreaFacade="data/facade_area.tif",
)
paths = writeHourlyRasterOutputs(xy, climate, result, "outputs/raster")
# Current writer retains the transform but does not assign the reference CRS.
with rasterio.open("data/reference.tif") as reference:
    crs = reference.crs
for path in paths:
    with rasterio.open(path, "r+") as dst:
        dst.crs = crs
```

Results have shape `(24, rows, columns)`. `chunkSize` controls sites grouped per calculation. The writer uses `nodata=0`, overlapping valid zero heat fluxes; preserve/assign an appropriate mask and NoData convention for GIS analysis.

### Derive morphology rasters from SHP

```bash
python SITE/process_BLD_SHP.py --input data/buildings.shp --output-dir outputs/morphology --resolution 100 --height-column height --workers 1
```

This separate CLI writes `SVF.tif`, `facaAreaRatio.tif`, `BuildingAreaRatio.tif`, `FAR.tif`, `kS.tif`, and `manifest.json`. Point mode uses `--lat`, `--lon`, and `--resolution`. It is separate from `Neighborhood.readSHP()`'s internal rasterization.

Map `FAR.tif` → `plotRatio`, `SVF.tif` → `SVF`, `kS.tif` → `kS`. Convert `BuildingAreaRatio * cell_area` to `AreaBuilding`, and `facaAreaRatio * cell_area` to `AreaFacade`. Obtain remaining surface areas from land-cover data on the same grid. An area flag interprets a ratio raster as absolute area unless you convert it first.

## Parameters and scenario changes

### Parameter layers and precedence

1. **`ClimateInfo`:** hourly forcing and feedback; change CSV values or 24-element arrays.
2. **`SiteInfo`:** geometry, composition, shading, green-roof fraction; change `ng.site` or CSV/raster fields.
3. **`settingInfo`:** physical coefficients, empirical load/traffic, numerical controls; change `ng.setting` or pass `settings` to CSV/raster Python functions.

`readSHP()` loads inferred LCZ calibration. Select a different calibration **after** reading the site:

```python
from UHI.modelInfo import settingInfo

ng.setting = settingInfo.loadJSON("UHI/params/LCZ2.json")
ng.setting.AbsoRoof = 0.45
ng.setting.rsveg = 150.0
ng.site.greenRoofPercentage = 0.50
urban = ng.uhiSimulation(preheat=4)
ng.setting.saveJSON("outputs/used_settings.json")
```

`ng.uhiSimulation(preheat=4, AbsoRoof=0.45)` also updates the existing settings. It accepts setting names, not `show_progress`; progress is disabled internally. Direct `settingInfo(...)` starts from generic defaults rather than LCZ calibration. CSV/raster `settings=None` enables automatic LCZ selection, while an explicit settings object replaces it.

### Main urban parameters

These are **generic constructor defaults**, not the LCZ JSON calibration values.

| Group | Parameter | Default / unit | Role |
| --- | --- | --- | --- |
| Absorption | `AbsoRoad`, `AbsoGround`, `AbsoRoof`, `AbsoLandscape`, `AbsoWater` | 0.74, 0.74, inherits ground, 0.80, 0.96 | Absorptance; albedo = 1 − absorptance |
| CTTC | `CTTCD`, `CTTCB`, `CTTCQ` | 8, 6, 12 h | Ground/building/vegetation response constants; constructor converts to integers |
| Longwave | `BruntEmissivity` | 0.605 | Brunt intercept; humidity coefficient fixed in code |
| Emission | `EmiRoad`, `EmiGround`, `EmiRoof`, `EmiLandscape`, `EmiWater` | 0.94, 0.94, 0.90, 0.96, 0.98 | Longwave emissivity |
| Evaporation | `rsveg`, `rsground` | 100, 200 s/m | Effective surface resistance |
| Evaporation multipliers | `gRVEff`, `gInff`, `gWUEffnight`, `gWUEffday` | 0.8, 0.6, 0.6, 1.0 | Roof/ground and night/day water efficiency |
| Storage | `CLand`, `CGround`, `CWater` | 5e5, 2e4, 1.1e6 J/(m² K) | Active-layer areal heat capacity |
| Deep exchange | `UDeepLand`, `UDeepGround`, `UDeepWater` | 0.40, 0.70, 1.50 W/(m² K) | Conductance to deep reference |
| Deep boundary | `GroundTemperature` | 25 °C | Reference temperature |
| Wind attenuation | `a`, `k` | 0.22, 0.2 | Wind/blockage correction; site blockage is `site.kS` |
| Aerodynamics | `zRef`, `z0`, `zRefd`, `z0d` | 2, 0.001, 400, 1.5 m | Reference heights / roughness lengths |
| Air storage | `HAir` | Initially equals `zRefd`, m | Effective air-layer depth |
| Natural convection | `c1` | 2.1 W/(m² K^(4/3)) | Surface–air exchange coefficient |
| Time step | `dt` | 3600 s | `None` disables transient storage; data remain hourly |
| Hourly solver | `outerMaxIter`, `outerTolerance`, `innerTolerance`, `relaxationFactor` | 50, 0.01 K, 1e-4 K, 0.6 | Nonlinear heat-balance controls, distinct from between-model convergence |
| Empirical load proxy | `settingLoad`, `coolingSetPoint`, `outSetPoint`, `inSetPoint`, `isWeekDay` | 90 W/m² floor, 28.8, 35, 26 °C, True | Used when `climate.coolingLoad` is absent |
| Traffic | `intensity`, `annualDistance`, `FE`, `fuelDensity`, `NHC` | 20, 25000, 10.9, 0.0072, 45000 | Coefficients as encoded; retain the source conversion convention when tuning |

Inspect and archive active values:

```python
print(ng.site.to_dict())
print(ng.setting.to_dict())
print(ng.site.LCZ, ng.site.GFA, ng.site.plotRatio)
```

`HAir` copies `zRefd` only at construction; later changes do not synchronize them. Not every stored setting is active in every closure: `conductionMode` is stored, while the current balance directly uses deep conduction plus storage when `dt` is set. The empirical building proxy uses its embedded occupancy profile rather than `qlx`. Interpret sensitivity from executed code.

### Building and energy parameters

Set archetypes first, then apply overrides; another `sampleInstance()` call resamples parameters.

```python
for building in ng.buildings:
    building.CoolingEER = 3.5
    building.WallUValue = 0.8
    building.WindowUValue = 1.8
    building.WindowSHGC = 0.35
    building.wwr = 0.30
    building.CoolingSetpointTemp = 26.0
ng.processRad()  # refresh solar schedules/UHI forcing after WWR changes
energy = ng.energySimulationSteady(
    output_dir="outputs/retrofit_energy",
    options={
        "surfaces": {"roof": {"resistance": 3.0, "absorptance": 0.45},
                     "floor": {"resistance": 2.0}},
        "internal_radiant_fraction": 0.6,
        "glass_inward_fraction": 0.0,
    },
)
```

- `WallUValue` / `WindowUValue`: W/(m² K). `WindowSHGC` / `wwr`: 0–1. `CoolingEER` / `HeatingEER`: positive COP-like efficiencies. Setpoints: °C, with heating below cooling.
- Internal gains, occupant density, fresh-air rates, and setpoints can use numeric constants or loaded schedule names where supported by the adapter. A numeric value is constant; schedule references preserve hourly operation.
- `InfiltrationACH` / `NightVentilationACH`: air changes per hour. The JSON adapter combines fresh air and infiltration into a daily equivalent ACH.
- JSON `options["surfaces"]` supports wall/roof/floor/window overrides. `resistance` excludes surface films, in m² K/W; `absorptance` is a fraction. Explicit roof/floor resistances are appropriate for construction-specific comparisons; otherwise the adapter uses its documented U-value mapping assumptions.
- The default adapter uses `WallUValue` for wall, roof, and floor construction mapping, and `AbsoRoof` for wall/roof absorptance. A roof-only intervention therefore needs an explicit roof surface override; an urban `AbsoRoof` change alone also changes the default building wall absorptance.
- Other JSON options: `stage`, `parameters`, `redistribution`, `infiltration_ach`, `density_kg_m3`, `cp_j_kg_k`. See [energy protocol](ENERGY/bin/Energy-Readme.md) and [adapter notes](ENERGY/readme.md). Daily solar redistribution defaults to disabled; benchmark coefficients are not installed automatically.
- JSON process options: `executable_path`, `timeout_seconds_per_day`. Retain `mapping.json` to audit assumptions and mappings.

Morphology changes require updated site descriptors and RAD visibility. WWR/archetype changes require `processRad()` after overrides. Thermal/COP changes can reuse radiation. Initialize paired scenarios from the same baseline and retain identical weather and sampling.

## Outputs and units

| Output | Meaning / unit |
| --- | --- |
| `QS` | CTTC-filtered shortwave forcing, W/m² site |
| `QL` | Signed longwave flux, W/m² site; negative denotes heat loss |
| `QV` | Effective evaporative contribution, W/m² site; cooling is negative |
| `QA` | Building heat contribution plus traffic, W/m² site |
| `Tair`, `Tsurface`, `Tambient` | Weighted canopy air, weighted surface, background air temperatures, °C |
| `TaGround/TaLand/TaWater`, `TgGround/TgLand/TgWater` | Category-specific air/surface temperatures in Python UHI results |
| `cooling_kwh`, `heating_kwh` | JSON whole-building hourly thermal demand, kWh |
| `hvac_electricity_kwh` | JSON whole-building hourly HVAC electricity, kWh |
| `ACLoad`, `ACWasteHeat` | Signed whole-building / aggregated neighborhood hourly quantities, Wh |
| `climate.coolingLoad` | Net building feedback / site area, hourly Wh/m², numerically W/m² at a one-hour step |

CSV columns are exactly `caseid,t,QS,QL,QV,QA,Tair,Tsurface,Tambient`. Each hourly raster has seven bands: `QS,QL,QV,QA,Tair,Tsurface,Tambient`. The correct output name is **`Tsurface`**, replacing stale `Tground` references in older documents.

JSON ENERGY saves `input.json`, `mapping.json`, `output.json` under `building_XXXX/day_XXX`, plus root `summary.json`. `hvac_electricity_kwh` excludes direct lighting/equipment electricity. `condenser_rejection_wh` is gross cooling-rejection diagnostics, not signed net feedback.

`ng.saveJSON()` / `Neighborhood.loadJSON()` save/restore state; retain referenced SHP/RAD files separately for reruns. `ng.writeEPW()` updates dry bulb in the requested interval and recalculates RH/dew point at the original humidity ratio; it requires a source EPW.

## Report and implementation alignment

The urban model assembles CTTC shortwave response, Brunt longwave, an empirical FAO-56-based evaporative closure, convection, and anthropogenic heat. It solves impervious ground (buildings + roads + pavement), vegetation, and water separately and area-weights their results. The JSON energy kernel supplies a grey-box single-zone sensible-load calculation.

| Topic | Technical report | Current distributed code |
| --- | --- | --- |
| Multi-day states | Four first-day warm-ups, continuous states and CTTC history across 168 hours | Each 24-hour object is processed independently with its own `preheat`; CTTC repeats that object's daily forcing |
| Between-model solution | Converged sequence exchange with common initial state per pass | Explicit calls; no automatic convergence driver or common-state reset |
| Heat routing | Separate canopy return `q_a*` and surface debit `q_g,b*` | One net building feedback in `climate.coolingLoad`, consumed by canopy anthropogenic heat; no separate surface-return input |
| Source accounting | Separately attributed air/surface transfers, including paired opaque-radiation exchange | JSON adapter subtracts inward infiltration and summed contact transfers; this is not the full report two-layer attribution |
| Building solar redistribution | Calibrated daily redistribution in the benchmark | Adapter defaults to disabled; configure protocol options explicitly |
| Electricity | HVAC plus direct lighting/equipment for total end use | JSON wrapper reports HVAC electricity; direct end-use accounting must be added separately |
| CTTC parameters | Calibrated continuous values | Constructor truncates CTTC values to integers, including JSON loads |

The current JSON feedback is:

```text
ACLoad = 1000 * (cooling_kwh - heating_kwh)
ACWasteHeat = ACLoad + electricity_wh - air_exchange_wh - contact_transfer_wh
climate.coolingLoad = sum_buildings(ACWasteHeat) / site.TotalArea
QA = climate.coolingLoad + traffic_heat
```

Keep signs and the site-area denominator. Do not divide net feedback by COP again or clip it to nonnegative values. These differences require code changes and numerical validation before claiming exact reproduction of the report's continuous, two-channel, converged CETUS experiment. This README documents current interfaces without changing those physical/numerical implementations.

## Troubleshooting and reproducibility

| Symptom | Action |
| --- | --- |
| CSV module invocation gives no output | Run `python test.py` or call `csvCalculation()` from a script |
| CSV rejects keyword `HAir` | Set `settings.HAir`, then pass the settings object |
| SHP height-field error | Pass the exact field name; use positive metre heights |
| Unexpected area / radiation geometry | Reproject SHP to metre units first; inspect site area and building GFA |
| Missing climate zone / RAD when sampling | Read weather and SHP, compute/reuse RAD, then sample archetypes |
| RAD building mismatch | Preserve filtered geometry and unique IDs used to generate the radiation |
| Native engine cannot run | Check platform-specific paths and the installation table |
| PROJ database layout/version error | Check `PROJ_LIB` / `PROJ_DATA` for paths inherited from another GIS installation; use the PROJ data matching the active rasterio/pyproj environment |
| JSON energy rejects zero-area surface | Check geometry/WWR; all four surface groups require positive areas |
| Repeated results differ | Fix seed and inputs; restart from an identical initialized state |
| Raster output lacks CRS | Assign reference CRS as in the Python example |
| `preheat=4` does not converge feedback | Warm-up is a daily UHI operation; coupling needs separate controlled iteration |

Archive source/environment versions, native engines, inputs, preprocessing options, building seed and achieved functional GFA shares, active LCZ settings/overrides, smoothing choice, requested/expanded HOY windows, RAD sample/cache, and energy mapping/output audits. Label results as urban-only, sequential urban → energy, or explicitly managed feedback experiments.

## Source map

| Module | Responsibility |
| --- | --- |
| [`ENERGY/neighborhood.py`](ENERGY/neighborhood.py) | EPW + SHP coordinator and state/result exchange |
| [`WEA/epw.py`](WEA/epw.py) | Weather parsing, sky matrix, climate classification |
| [`SITE/transform.py`](SITE/transform.py), [`SITE/siteInput.py`](SITE/siteInput.py) | Geometry/raster conversion and site inputs |
| [`SITE/process_BLD_SHP.py`](SITE/process_BLD_SHP.py) | Morphology-raster and point-query CLI |
| [`RAD/workflow.py`](RAD/workflow.py) | Radiation sampling, IDW, schedules |
| [`UHI/modelInfo.py`](UHI/modelInfo.py), [`UHI/params`](UHI/params) | Data/settings classes and LCZ calibration |
| [`UHI/calculator.py`](UHI/calculator.py), [`UHI/heatBalance.py`](UHI/heatBalance.py) | Flux assembly and surface/air solution |
| [`UHI/csv_calculator.py`](UHI/csv_calculator.py) | CSV Python calculation / weather adapter |
| [`UHI/raster_calculator.py`](UHI/raster_calculator.py) | Raster Python API, CLI, hourly export |
| [`ENERGY/steady.py`](ENERGY/steady.py), [`ENERGY/bin/Energy-Readme.md`](ENERGY/bin/Energy-Readme.md) | JSON energy mapping and protocol |
| [`ENERGY/model.py`](ENERGY/model.py) | Building geometry, archetypes, schedules, feedback |

Older [`input_protocol.md`](input_protocol.md), [`methodology.md`](methodology.md), and [`ENERGY/readme.md`](ENERGY/readme.md) include background and legacy interfaces. Use the code-confirmed names and behaviors above when those documents differ.
