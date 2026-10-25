# CETUS Energy 9.2 — Usage, Interfaces and Physical Assumptions

**Applies to:** the delivered `CETUS_9.2_clean.zip` package.  
**Document date:** 25 October 2026.  
**Default native solver:** `ENERGY/bin/CetusSurfaceEnergy` on Linux x64, or `CetusSurfaceEnergy.exe` on Windows x64.

Version 9.2 changes data management and result export while retaining the prebuilt 9.1 thermal kernel, including its spectral longwave implementation. Version labels such as `cetus-surface-7` and `CES3` are protocol identifiers; they do not mean that the package has reverted to an earlier release.

## 1. What the applications do

| Component | Responsibility |
|---|---|
| `Neighborhood` | Manages editable building parameters, site/settings, Climate objects and city-level results. |
| `Energy.py` | Reads a Neighborhood JSON, runs the coupled calculation and writes a building-result archive. |
| `ENERGY.simulate_surface` | Runs an isolated building from scalar envelope areas and prescribed weather/radiation, without a SHP or room geometry. |
| `CetusSurfaceEnergy` | Solves building heat balances; accepts JSON and the native CES3 binary transport. |
| `EnergyUnpack.py` | Independently exports **all** buildings, their parameter snapshots and complete results to one JSON file. |

Complete building histories are no longer attached to `Neighborhood.buildings`. Building parameters remain editable there, and city results remain in `Neighborhood.climate`. The `building_result_file` attribute points to the external building archive.

The package also contains older `MoosasEnergy` and `MoosasEnergy-legacy` executables for historical APIs. They are not interchangeable with the default `CetusSurfaceEnergy` solver.

## 2. Installation and execution policy

Run examples below from the extracted `CETUS_9.2` directory, using Python 3.12 or later:

```bash
pip install -r requirements.txt
```

The simulation application uses the package dependencies, including NumPy, SciPy and the geospatial libraries. The unpacking application needs **only the Python standard library**, the `EnergyUnpack.py` script and its adjacent `result_store.py` module.

Energy and RAD use their supplied executables directly. They do not check executable SHA files, compare executable/source timestamps or automatically compile Go code. Missing executables produce an error. Linux execute permissions are enabled by the wrappers where applicable. The bundled executables target Linux x64 and Windows x64; this release does not supply a native macOS executable.

Input/cache/restart configuration signatures are still checked. These prevent incompatible models, radiation caches or states from being mixed and are separate from executable SHA checking.

## 3. Recommended command-line workflow

Start with an existing Neighborhood JSON prepared through the CETUS workflow:

```bash
python Energy.py --input neighborhood.json --output building_results.bin
python EnergyUnpack.py building_results.bin building_results.json
```

The first command writes:

- `building_results.bin`: the external building-result archive;
- `building_results_neighborhood.json`: building parameters and updated city Climate results, with a reference to the archive;
- `building_results_city/`: city hourly CSV, audit and state/checkpoint files.

Paths stored in the input JSON for SHP, EPW, weather CSV, sky matrix and radiation data are resolved relative to the input JSON directory when they are not absolute. Required external files must remain available for simulation. The unpacker does not require these source files or a solver executable.

The unpacker always exports every building and all available records. Its interface intentionally has only two positional arguments: input archive and output JSON. Large JSON export is deferred until this command is invoked.

`Energy.py` is the city-coupled application. It is not the CLI for the isolated scalar-area model; use `simulate_surface` for that case.

## 4. Editing inputs and calling the Python interface

### 4.1 City-coupled simulation

```python
from ENERGY import Neighborhood
from ENERGY.coupled import simulate_coupled

n = Neighborhood.loadJSON("neighborhood.json")
n.buildings[0].WallUValue = 0.5

# If options is omitted, the calculation uses n.energy_options,
# or the applicable cached radiation options when none are specified.
result = simulate_coupled(
    n,
    output_dir="outputs/run92",
    preheat=4,
    max_preheat=120,
    warmup_tolerance_k=0.1,
    show_progress=True,
)

n.saveJSON("neighborhood_after.json")
print(result["building_result_file"])
print(result["coupled"]["lighting_summary"])
```

Supply complete, consecutive natural days: each Climate object contains 24 hours, and each day starts at an HOY divisible by 24. HOY is **zero-based**. High-level hourly adapters require `dt_s=3600`.

When preparing radiation, use the same optical options as the subsequent energy run:

```python
n.radSimulation(
    sample=0.1,
    energy_options=n.energy_options,
    output_path="outputs/network.rad.npz",
)
result = simulate_coupled(n, output_dir="outputs/run92")
```

An existing compatible radiation cache can instead be loaded with `radFile`. Changed geometry, window areas or solar optical properties can invalidate that cache. Spectral longwave coupling requires a RAD network; the isolated model below supports prescribed spectral boundaries without that network.

### 4.2 Isolated building: scalar areas, no SHP

```python
import numpy as np
from ENERGY import BuildingInstance, simulate_surface
from UHI.modelInfo import ClimateInfo

building = BuildingInstance(
    StoryHeight=3.0, FloorArea=100.0, GFA=200.0,
    PerimeterZoneArea=60.0,
    ExteriorWallArea=60.0, ExteriorWindowArea=20.0,
    RoofArea=100.0, GroundFloorArea=100.0,
    WallUValue=0.6, WindowUValue=2.4,
    CoolingEER=3.0, HeatingEER=3.0,
    HeatingSetpointTemp=20.0, CoolingSetpointTemp=26.0,
    LightingHeatGain=8.0, EquipmentHeatGain=3.0,
    OccupantDensity=0.1, OccupantHeatGain=75.0,
    InfiltrationACH=0.5, NightVentilationACH=0.5,
)
weather = ClimateInfo(
    HOY=np.arange(24), Ta=np.full(24, 28.0),
    Tg=np.full(24, 24.0), SkyTemperature=np.full(24, 18.0),
    RadTotal=np.zeros(24), RadDiffuse=np.zeros(24),
)
result = simulate_surface(
    building, [weather],
    irradiance_by_day=[{
        "facade": np.zeros(24), "roof": np.zeros(24)
    }],
    output_dir="outputs/isolated92",
)
print(result["building_result_file"])
```

In this scalar-only path, `ExteriorWallArea` and `ExteriorWindowArea` describe a typical floor and are scaled by `GFA/FloorArea`. `RoofArea`, `GroundFloorArea` and `SkylightArea` are whole-building areas. `FloorArea` is the typical-floor area; `GFA` is total gross floor area. `PerimeterZoneArea` is used for approximate lighting partitioning, not as an additional thermal zone.

If a building has a positive geometric perimeter, the adapter instead derives total facade areas from perimeter, height and WWR. Avoid mixing inconsistent scalar and geometric definitions.

Provide facade/roof **incident** shortwave irradiance in W/m², before glass transmission or surface absorption. If radiation arrays are omitted, the isolated adapter uses roof GHI and a direction-mean facade proxy; that fallback does not resolve geometric shading or facade orientations.

The isolated Python return value retains `hours` for compatibility, together with `final_state`, `warmup`, `lighting_summary`, `day_audits` and the archive path. It does not automatically write a full result JSON.

## 5. High-level options and input conventions

Supported top-level energy options are `surfaces`, `internal_radiant_fraction`, `solar_floor_fraction`, `dt_s`, `daylighting` and `longwave`. Unknown top-level keys are rejected.

| Input | Meaning / units |
|---|---|
| `WallUValue`, `WindowUValue` | Nominal U-values, W/(m²·K). Roof/floor use the wall value unless overridden. |
| `WallReflectance`, `RoofReflectance`, `WindowReflectance` | Broadband **solar** reflectance, dimensionless. |
| `WindowTransmittance` | Broadband direct solar transmittance, not SHGC. |
| `WallEmissivity`, `RoofEmissivity`, `WindowEmissivity` | Scalar longwave emissivities for the grey model and indoor radiation parameters. |
| `LightingHeatGain`, `EquipmentHeatGain` | Power-density values or schedule references, W/m². |
| `HeatingSetpointTemp`, `CoolingSetpointTemp` | Indoor air setpoints, °C. |
| `HeatingEER`, `CoolingEER` | Heating/cooling COP values used by the adapter. |

Scalar parameters or valid existing Schedule references are supported by the applicable schedule-aware fields. Do not interpret an existing lighting schedule in W/m² as a 0–1 multiplier.

Surface overrides address `wall`, `roof`, `floor`, `window` or `skylight`. For example:

```python
n.energy_options = {
    "surfaces": {
        "roof": {
            "u_value_w_m2k": 0.4,
            "u_convention": "air_to_air",
            "thermal_mass_j_m2k": 80000.0
        }
    },
    "internal_radiant_fraction": 0.6,
    "solar_floor_fraction": 0.642
}
```

The adapter removes reference inside/outside film resistances once when converting air-to-air U to construction resistance. `surface_to_surface` bypasses that removal. Low-level `resistance_m2k_w` is construction resistance. Explicit `layers` contain `resistance_m2k_w` and `capacity_j_m2k`; they replace the default effective mass specification. Layer capacities are per area and are multiplied by surface area in the solver.

## 6. Low-level native solver interface

This section is intended for external software integration. Prefer the Python adapters unless the caller already constructs complete thermal models and boundary conditions.

The native model contains:

| Field | Meaning |
|---|---|
| `protocol` | `cetus-surface-7` |
| `dt_s` | Step length, seconds; high-level CETUS currently uses 3600. |
| `surfaces` | Ordered surface definitions. |
| `hours` | Ordered forcing records, with surface arrays in the same order. |
| `initial_c` / `initial_state` | Uniform initialization, or a complete solver state vector. |
| `cooling_cop`, `heating_cop` | Positive electrical conversion factors. |
| `internal_radiant_fraction` | Internal gain fraction sent to the indoor radiant node. |
| `solar_floor_fraction` | Fraction of transmitted solar power preferentially distributed to floors. |

Each surface includes `name`, `kind`, `area_m2`, `resistance_m2k_w`, `thermal_mass_j_m2k`, solar `reflectance`/`transmittance`, scalar `emissivity`, `hi_w_m2k`, `ho_w_m2k`, `sky_view`, `ground_view`, `ground_contact_w_m2k` and `absorbed_inside_fraction`. Optional `layers` replace the lumped construction description. Optional `longwave_bands` contain `lower_um`, `upper_um` and `emissivity`. A JSON `null` upper wavelength means infinity.

Each forcing record contains:

| Field | Units / interpretation |
|---|---|
| `air_c`, `ground_c` | Exterior air and ground boundary temperatures, °C. |
| `sky_ir_w_m2` | Prescribed sky hemispherical longwave irradiance, W/m². |
| `irradiance_w_m2` | Incident shortwave array, one value per surface. |
| `internal_w` | Total internal sensible heat input, W. |
| `heat_setpoint_c`, `cool_setpoint_c` | Air setpoints, °C. |
| `ventilation_w_k` | Combined outdoor air exchange conductance, W/K. |
| `ho_w_m2k` | Optional hourly exterior film coefficients. |
| `network_ir_w_m2` | Optional external longwave boundary array; see the spectral qualification below. |
| `additional_transmitted_w`, `additional_glass_inward_w` | Compatibility/validation gains; do not duplicate solar power already represented through the optical model. |

The native executable retains a JSON request interface (top-level model fields, or batch/model requests) for compatibility. That interface returns native-model JSON responses; it does **not** produce the 9.2 CER archive or add Python-side daylight accounting. The recommended archive-producing workflow is the high-level Energy application.

A minimal **low-level** one-hour grey model can be saved as `model.json`:

```json
{
  "protocol": "cetus-surface-7",
  "dt_s": 3600,
  "initial_c": 26,
  "cooling_cop": 3,
  "heating_cop": 3,
  "internal_radiant_fraction": 0.6,
  "solar_floor_fraction": 0.642,
  "surfaces": [{
    "name": "wall", "kind": "wall", "area_m2": 100,
    "resistance_m2k_w": 2, "thermal_mass_j_m2k": 60000,
    "reflectance": 0.3, "transmittance": 0, "emissivity": 0.9,
    "hi_w_m2k": 3, "ho_w_m2k": 8,
    "sky_view": 1, "ground_view": 0,
    "ground_contact_w_m2k": 0, "absorbed_inside_fraction": 0
  }],
  "hours": [{
    "air_c": 30, "ground_c": 25, "sky_ir_w_m2": 400,
    "irradiance_w_m2": [200], "internal_w": 300,
    "heat_setpoint_c": 20, "cool_setpoint_c": 26,
    "ventilation_w_k": 5
  }]
}
```

On Linux, run `./ENERGY/bin/CetusSurfaceEnergy model.json model_result.json`; on Windows, use `ENERGY\bin\CetusSurfaceEnergy.exe model.json model_result.json`. The input fields are at the JSON root, **not** nested under an `input` key. This deliberately small API example is not a complete physical building or a Neighborhood input. The low-level solver accepts this single step; the high-level daily adapters require full 24-hour days.

For persistent binary sessions, launch the executable with `--binary`. CES3 uses little-endian frames and the header layout `<4sHHQQ`: magic `CES3`, version, operation, request ID and payload length. The current frame version is 1 and the payload limit is 256 MiB. Relevant operations include model registration (1), begin step (2), end step (4), cached-result retrieval (6), masked evaluation (7), solver options (8), profiling (9) and explicit spectral registration (10).

Use `ENERGY.surface_binary.BinarySurfaceEngine` and `ENERGY.surface_codec` as the reference implementations. Register models once; then register the immutable previous physical state and fixed forcing at each `begin_step`. Candidate evaluations change boundary fields without advancing that physical state. Response levels are `totals`, `coupling` and `full`; the archive stores the accepted `full` response with state, not every trial.

CES3 communication frames and the CER result archive are different formats. Do not pass an arbitrary CES3 stream to `EnergyUnpack.py`. Do not manually construct a state vector: preserve the complete returned vector and its model/topology association.

## 7. Building-result archive and JSON export

`building_results.bin` is a CER version-1 container using lossless ZIP compression. It stores accepted native CES3 full/state response payloads without JSON re-encoding. Parameters, actual boundary conditions and Python-side additions use an explicit typed binary representation; pickle is not used. A small JSON manifest describes record order, layout and completion.

The archive includes building IDs, original parameters, resolved surface models, actual hourly forcing, lighting additions, diagnostic context, native group totals and final states. Spectral coupled runs also store resolved emission/reflection/transmission coefficients. Updates to Neighborhood after the run cannot change the archive's parameter snapshot.

Only accepted production hours are recorded. Warm-up history/checkpoints are separate from production energy accounting. Temporary files are finalized by atomic replacement; the unpacker checks completion, version/layout and container integrity.

The exported JSON has this structure:

```json
{
  "implementation": "9.2.0",
  "context": {},
  "buildings": [
    {
      "building_id": "B000000",
      "parameters": {},
      "resolved_model": {},
      "hours": [],
      "applied_inputs": []
    }
  ],
  "group_totals": [],
  "run_metadata": {}
}
```

`context` holds shared options, source climate and day-level diagnostics. Building and surface histories remain attached to their stable IDs/names. A full JSON can be substantially larger than the archive; that expansion happens in the unpacking application, not in the default simulation output stage.

Historical steady/legacy APIs also externalize their building results, but use identified typed-tree records because those solvers do not emit CES3 payloads. These records must not be described as native CES3 output.

## 8. Output units and sign conventions

Unless explicitly labeled otherwise, powers are W per **whole building** or whole surface; temperatures are °C. City fields with `_w_m2_site` are normalized by total site area. Energy totals are kWh. For one-hour steps, a mean power in W has the same numerical value as step energy in Wh; this does not make W and Wh interchangeable for other step lengths.

| Output | Interpretation |
|---|---|
| `hvac_w` | Positive heating supplied to the zone; negative cooling. |
| `electricity_w` | HVAC electricity only: heating/COP or cooling magnitude/COP. |
| `lighting_electricity_w` | Direct lighting electricity, added by the adapter; never divided by HVAC COP. |
| `hvac_plus_lighting_electricity_w` | Sum of the two meters; excludes other electrical end uses. |
| `convection_to_air_w`, `ventilation_to_air_w` | Signed heat transfer to exterior air. |
| `hvac_to_air_w` | Outdoor HVAC exchange, `-hvac_w + electricity_w`; negative values are possible in heating mode. |
| `longwave_to_network_w` | Signed net longwave power leaving the building through the network boundary. |
| `contact_to_ground_w`, `longwave_to_ground_w`, `longwave_to_sky_w` | Signed outward boundary exchange on the applicable path. |
| `storage_w` | Positive increase in construction thermal storage. |
| `balance_residual_w` | Whole-building heat-balance closure diagnostic. |
| `exterior_c`, `interior_c` | Exterior/interior temperatures of each surface. |
| `external_net_lw_w` | Signed outward longwave power for that surface. |
| `incident_w`, `reflected_w`, `absorbed_w`, `transmitted_w` | Solar power partition at the surface. |

In a network run, longwave exchange is reported through the network route; do not add a second sky/ground exchange for the same boundary. The source code and archived surface fields provide the finer construction/convection/radiation decomposition.

## 9. Thermal model and numerical assumptions

### 9.1 One thermal zone per building

Each building has one well-mixed, algebraic indoor air node and a massless indoor radiant enclosure node. Rooms used for lighting control do not create independent indoor temperatures. Construction mass, rather than a separate indoor air capacitance, supplies the principal building thermal storage in this implementation.

Opaque surfaces contain exterior/interior boundary temperatures and one or more thermal storage nodes. Construction storage is discretized implicitly through capacity divided by step length. Layers provide resistance/capacity networks, not a detailed moisture-dependent material model. Transparent surfaces have **zero thermal capacity** and may not contain storage layers.

The solver iterates nonlinear radiative terms and applies ideal heating/cooling to keep the air temperature within the setpoint band. HVAC capacity, equipment cycling and part-load COP curves are not represented. COP is prescribed; a correct load response is not by itself proof of real-system electrical accuracy.

Default opaque capacities, film coefficients and construction proxies are assumptions. For example, the adapter uses effective capacities of 60,000 J/(m²·K) for walls/roofs and 120,000 for floors unless overridden, and a 100 W/(m²·K) floor contact coefficient. These are not calibrated values for every building. Supply project-specific constructions and boundary parameters when available.

### 9.2 Shortwave and internal gains

Solar absorption uses `alpha = 1 - rho - tau`. Opaque solar transmission is zero. Window transmission contributes once to indoor solar gains; absorbed window power is handled by the surface heat balance. Direct solar transmittance and SHGC are different quantities: the adapter does not reuse legacy `WindowSHGC` as the direct transmittance.

Transmitted solar power is distributed to interior surface nodes, with a default preferential floor fraction of 0.642; remaining power is area distributed. Internal sensible gains are split between the zone and radiant enclosure, with default radiant fraction 0.6. Indoor radiative exchange is a grey reduced-order enclosure representation, not a room-by-room ray tracer.

When a RAD network is used, its first-incident and once-reflected solar components supply building irradiance. Longwave radiation is not included in solar/daylight input. Without explicit standalone radiation, the facade proxy described above is used.

### 9.3 Daylight and lighting control

Example:

```python
n.energy_options["daylighting"] = {
    "enabled": True,
    "visible_transmittance": 0.7,
    "near_infrared_transmittance": 0.2,
    "threshold_lux": 300.0,
    "controlled_fraction": 1.0
}
```

The model approximately splits broadband shortwave using declared energy fractions, defaulting to visible/NIR/other = 0.43/0.52/0.05. Visible irradiance is converted to illuminance using a declared efficacy, default 250 lm per visible W. This is not a universal conversion from total solar W/m² to lux or a spectrally resolved measurement.

Default other-band glass transmission is zero, giving broadband thermal transmittance `0.43*t_vis + 0.52*t_NIR + 0.05*t_other`. Optical glass properties still apply when daylight control is disabled.

Equivalent workplane illuminance is estimated from transmitted visible flux, window area, zone area and utilization factors (default facade 0.22, skylight 0.35). Virtual perimeter/core and optional room groups are inferred from scalar floor/room areas and window-access metadata. Skylights serve the applicable top floor. No interior geometry is constructed.

Each group's controlled lights switch according to the threshold, then group powers are summed. The uncontrolled fraction retains its baseline schedule. Revised lighting heat **replaces** the old lighting contribution; it is not added on top of it. Lighting electricity is metered separately. The default treatment sends lighting power into the sensible heat balance.

These coefficients require independent calibration for quantitative daylight accuracy. The model does not resolve glare, individual desks, real occupant behavior or minute-scale switching. A threshold demand is not proof that the installed lamps can deliver the target lux. Room metadata may be supplied through `rooms_by_floor`; see the bundled standalone example for its accepted structure.

### 9.4 Grey and spectral longwave radiation

Without spectral options, the model uses scalar grey emissivities. The existing network grey path solves diffuse radiosity, including repeated reflections. The spectral path is opt-in and defaults to **one diffuse reflection**, not specular ray tracing.

```python
n.energy_options["longwave"] = {
    "enabled": True,
    "sky_mode": "prescribed",
    "reflection_mode": "one_bounce",
    "reference_temperature_k": 300.0,
    "bands": [
        {"name": "below_8", "lower_um": 0, "upper_um": 8,
         "atmospheric_transmittance": 0.02},
        {"name": "window_8_13", "lower_um": 8, "upper_um": 13,
         "atmospheric_transmittance": 0.75},
        {"name": "above_13", "lower_um": 13, "upper_um": None,
         "atmospheric_transmittance": 0.02}
    ],
    "materials": {
        "roof": {"emissivity": {
            "below_8": 0.1, "window_8_13": 0.95, "above_13": 0.1
        }}
    }
}
```

Use `null` instead of `None` in JSON files. Bands must form an ordered, non-overlapping partition of `[0, infinity)`, with at most 128 bands. Materials can address wall, roof, window, skylight, hard ground/road, landscape and water; coupled per-building overrides use `building_materials` and zero-based index strings.

For blackbody band exitance `B_b(T)`, surface emission is:

`E(T) = sum_b epsilon_b * B_b(T)`.

The integrated emissivity is `E(T)/(sigma*T^4)`. It is Planck weighted and checked to be below 1 at the reference temperature; the unweighted sum of band coefficients is not the physical total emissivity. Both Python and Go use Planck-based tables with 0.05 K interpolation over 173.15–453.15 K and integral evaluation outside that interval.

Kirchhoff's relation sets band absorptivity equal to emissivity. Missing band reflectance is filled by the arithmetic mean of the material's explicitly supplied reflectances in other bands. If none are supplied, the opaque complement `1-epsilon` is used. Opaque materials require `epsilon + rho = 1` in every band; an inconsistent mean-filled combination is rejected rather than silently normalized. For advanced `opaque=False` materials, the nonnegative remainder is an outward transmission ledger item; it does not automatically heat the building interior and is not a full semitransparent-envelope model.

`atmospheric_transmittance` is an **effective hemispherical sky transmittance**. In prescribed mode, sky irradiance in each band is `(1-tau_b)*B_b(T_background)`, replacing the EPW broadband sky boundary rather than being added to it. `epw_matched` instead scales optical depth to match the EPW total longwave flux when the target is feasible. Neither mode performs line-by-line atmospheric or directional cloud/water-vapor transfer. Fixed example transmittances are scenarios, not measurements inferred from every EPW.

Diffuse exchange includes surface orientation, cosine projection, distance and geometric obstruction through view factors. It does not preserve an incoming ray's specular reflection direction. For emission `E`, external incidence `L` and view matrix `F`, one-bounce incidence is:

`H0 = F*E + L; R1 = rho*H0; H = H0 + F*R1`.

The next reflected amount is recorded as `lw_second_reflection_unresolved_w`. It is not surface absorption or demonstrated escape to space. The optional `reflection_mode="radiosity"` solves repeated diffuse reflections as a reference. Budget closure does not prove that one-bounce truncation is negligible.

In spectral mode, low-level `network_ir_w_m2` is an **equivalent absorbed boundary**: multiplying it by the surface's scalar carrier emissivity yields band-weighted absorbed irradiance. Go subtracts that from its native spectral emission. Passing an ordinary broadband irradiance into this field for a spectral model is incorrect. Use the spectral adapter/network to construct it. The scalar emissivity still controls indoor grey radiation; applying an exterior selective coating does not automatically change the interior face.

## 10. City coupling and warm-up

Building exterior convection, ventilation, HVAC exchange and the applicable ground exchanges feed the existing UHI calculation. Building solar gains and lighting are not independently added again as city air heat sources. Roof migration avoids a duplicate roof contribution in the city ground/roof bookkeeping. Spectral atmospheric absorption is not automatically assigned to the local canopy air; this release has no vertically resolved atmospheric radiative-dynamical solver.

The current coupled ground receiver uses shared geometric exposure and an area-weighted ground boundary for building contact; the exposed ground thermal groups retain their separate temperatures. Evaporation remains the existing prescribed canopy sensible-energy sink, not prognostic humidity. Coupled green-roof evaporation is disabled without a roof moisture model.

| Control | Current default / behavior |
|---|---|
| `preheat` | Minimum 4 cycles; set 0 to skip warm-up or restart. |
| `max_preheat` | Maximum 120 cycles. |
| `warmup_tolerance_k` | 0.1 K. |
| Warm-up forcing | Periodic first-day forcing; not a reconstruction of preceding weather. |
| Unconverged warm-up | `RuntimeWarning`, then production continues from the last solved state. |
| Warm-up printing | Silent; history/checkpoints remain available. |
| Formal-hour convergence | Still enforced; an unconverged production solve raises an error. |

Coupled warm-up can accelerate guesses, but convergence is evaluated from solved daily states. A final unverified accelerated guess is not used when the cycle limit is reached. `show_progress` controls formal-hour/day progress. Optional chronological `spinup_days` is distinct from repeated-day warm-up and is excluded from reporting.

For a coupled restart, pass the complete returned final state through `initial_state` and set `preheat=0`; the next day must be contiguous and configuration signatures must match. Isolated `simulate_surface` accepts its own returned state vector with `preheat=0`. Do not exchange these two different state representations.

## 11. Validation status and practical limits

The delivered 9.2 report records 53 Python tests, including native/JSON consistency, construction physics, spectral transport checks, archive completeness, isolated unpacking, restart behavior and the revised warm-up policy. A 756-building, 72-hour comparison against 9.1 found zero differences across 4,393,152 shared building-result values and identical city temperatures. Very small city aggregate-energy summation differences were at floating-point rounding scale.

For that case, the post-run Neighborhood JSON decreased from 260.92 MB to 3.26 MB; the external archive was 34.88 MB including parameter/boundary snapshots. These are file sizes, not process RSS. The interface refactor does not claim faster thermal solves or a smaller native executable. Linux was exercised; Windows binaries were retained from the preceding release without a new Windows execution test.

Historical ASHRAE-related reports concern the particular cases and assumptions described in those reports. They are not certification of the whole engine, nor independent validation of daylight utilization factors, spectral material data or atmospheric profiles. The 9.2 interface release did not rerun an exhaustive ASHRAE test suite.

Before using results quantitatively, check construction data, weather/site consistency, solar and longwave material properties, control availability, warm-up warnings, heat-balance residuals and reflection truncation. Selective emission in the 8–13 µm window can change cooling, but is not guaranteed to outperform a broadband high-emissivity surface under every solar, temperature or sky condition.

## 12. Source files to consult

Paths below are relative to the CETUS package root:

- `Energy.py`, `EnergyUnpack.py`, `result_store.py`: application workflow and archive/export format.
- `ENERGY/model.py`, `ENERGY/neighborhood.py`: building/Neighborhood data model.
- `ENERGY/surface.py`, `ENERGY/standalone.py`, `ENERGY/coupled.py`: input mapping and simulation orchestration.
- `ENERGY/surface_binary.py`, `ENERGY/surface_codec.py`: native transport and binary field order.
- `ENERGY/surface_engine/main.go`, `binary.go`, `spectral.go`: thermal kernel, transport and spectral exterior emission.
- `ENERGY/daylight.py`, `UHI/spectral.py`, `RAD/spectral.py`, `RAD/network.py`: daylight and radiative approximations.
- `docs/INTERFACE_9.2.md`, `docs/VALIDATION_9.2.md`: release-specific interface and test records.

This README is a standalone English guide to the delivered 9.2 implementation. It does not modify the module or introduce new simulation results.
