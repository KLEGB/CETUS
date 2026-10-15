"""Adapt Urban buildings and daily climates to the moosas-steady-4 protocol."""

from __future__ import annotations

import json
import platform
import subprocess
from copy import deepcopy
from pathlib import Path

import numpy as np

from .engine import BIN_DIR


def ensure_steady_binary(executable_path=None):
    """Resolve or rebuild the JSON engine installed under the canonical name."""
    if executable_path is not None:
        path = Path(executable_path).resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        return path
    source = BIN_DIR / "MoosasEnergy-new.go"
    target = BIN_DIR / ("MoosasEnergy.exe" if platform.system() == "Windows" else "MoosasEnergy")
    if not target.exists() or source.stat().st_mtime > target.stat().st_mtime:
        subprocess.run(["go", "build", "-o", str(target), str(source)],
                       check=True, capture_output=True, text=True, timeout=120)
    if platform.system() != "Windows":
        target.chmod(target.stat().st_mode | 0o755)
    return target


def _series(values, name):
    values = np.asarray(values, dtype=float)
    if values.shape != (24,) or not np.all(np.isfinite(values)):
        raise ValueError(f"{name} must contain 24 finite values")
    return values


def _schedule(building, name, hoy):
    value = getattr(building, name)
    try:
        return np.full(24, float(value))
    except (TypeError, ValueError):
        if building.schedule is None or value not in building.schedule.entries:
            raise ValueError(f"Missing schedule for {name}: {value}") from None
        return _series(building.schedule.entries[value].hourly_values[hoy], name)


def build_steady_input(building, climate, *, site=None, setting=None,
                       facade_irradiance=None, roof_irradiance=None, options=None):
    """Return one whole-building, full-day input and its approximation audit.

    Surface overrides use protocol keys, e.g. options['surfaces']['wall'].
    No benchmark calibration is assumed: redistribution is disabled by default.
    """
    options = deepcopy(options or {})
    allowed = {"stage", "parameters", "redistribution", "surfaces", "infiltration_ach",
               "density_kg_m3", "cp_j_kg_k", "internal_radiant_fraction", "glass_inward_fraction"}
    if set(options) - allowed:
        raise ValueError(f"Unknown steady options: {sorted(set(options) - allowed)}")
    hoy = _series(climate.HOY, "HOY")
    if np.any(hoy != np.floor(hoy)) or hoy[0] % 24 or not np.array_equal(hoy, np.arange(hoy[0], hoy[0] + 24)) or hoy[0] < 0 or hoy[-1] >= 8760:
        raise ValueError("Steady input requires one complete natural day with HOY in 0..8759")
    hoy = hoy.astype(int)
    ta, tg = (_series(getattr(climate, key), key) for key in ("Ta", "Tg"))
    if np.any(ta <= -273.15) or np.any(tg <= -273.15):
        raise ValueError("Temperature must be above absolute zero")
    gfa, height = float(building.GFA), float(building.StoryHeight)
    if not np.isfinite([gfa, height, building.FloorArea]).all() or min(gfa, height, building.FloorArea) <= 0:
        raise ValueError("GFA, FloorArea and StoryHeight must be positive")
    stories = gfa / building.FloorArea
    volume = gfa * height
    areas = [building.ExteriorWallArea * stories, building.RoofArea,
             building.GroundFloorArea, building.ExteriorWindowArea * stories + building.SkylightArea]
    if not np.isfinite(areas).all() or min(areas) <= 0:
        raise ValueError("The new Go engine requires positive wall, roof, floor and window areas")
    audit = ["One mixed zone per building; facade areas scaled by GFA/FloorArea.",
             "Ta is a physical air boundary; Tg approximates the ground-contact boundary.",
             "Latent loads and HVAC on/off control are outside this engine's scope.",
             "Daily mean density, wind film and equivalent ventilation ACH are used."]
    ghi = np.maximum(_series(climate.RadTotal, "RadTotal"), 0)
    dhi = np.clip(_series(climate.RadDiffuse, "RadDiffuse"), 0, ghi)
    if facade_irradiance is None:
        # A direction-averaged proxy, not a solar-position transposition model.
        facade = 0.5 * dhi + 0.25 * (ghi - dhi) + 0.1 * ghi
        audit.append("Facade shortwave proxy: 0.5*DHI + 0.25*(GHI-DHI) + 0.1*GHI; no directional shading.")
    else:
        facade = _series(facade_irradiance, "facade_irradiance")
        audit.append("Facade shortwave is the equal-direction mean of RAD N/W/S/E.")
    roof = ghi if roof_irradiance is None else _series(roof_irradiance, "roof_irradiance")
    if roof_irradiance is None:
        audit.append("Roof shortwave uses ClimateInfo.RadTotal.")
    sky = np.asarray(climate.SkyTemperature, dtype=float)
    if sky.shape != (24,):
        raise ValueError("SkyTemperature must have 24 values")
    invalid_sky = ~np.isfinite(sky) | (sky <= -273.15) | (sky > 100)
    sky = np.where(invalid_sky, ta, sky)
    audit.append("Sky IR = sigma*(SkyTemperature+273.15)^4; missing/invalid sky uses Ta.")
    pressure = _series(climate.Pressure, "Pressure")
    humidity = _series(climate.HumidityRatio, "HumidityRatio")
    if np.any(pressure <= 0) or np.any(humidity < 0):
        raise ValueError("Pressure must be positive and humidity ratio nonnegative")
    density = pressure / (287.05 * (ta + 273.15)) * (1 + humidity) / (1 + 1.6078 * humidity)
    wind = np.maximum(_series(climate.Velocity, "Velocity"), 0)
    # Combined exterior film includes a fixed radiative contribution.
    ho = float(np.mean(5.8 + 4.1 * wind) + 5.13)
    svf = float(getattr(site, "SVF", 1.0))
    alpha = float(getattr(setting, "AbsoRoof", 0.74))
    surfaces = []
    for i, name in enumerate(("wall", "roof", "floor", "window")):
        u = float(building.WindowUValue if name == "window" else building.WallUValue)
        reference_inside = {"wall": .13, "roof": .10, "floor": .17, "window": .13}[name]
        surface = dict(name=name, area=float(areas[i]), u=u,
                       resistance=max(0.0, 1/u-reference_inside-.04) if u > 0 else 0.0,
                       hi=3.0, ho=ho, sky_view=svf*.5 if name in {"wall", "window"} else float(name == "roof"),
                       absorptance=alpha if name in {"wall", "roof"} else 0.0)
        overrides = options.get("surfaces", {}).get(name, {})
        if set(overrides) - (set(surface) - {"name"}):
            raise ValueError(f"Unknown surface options for {name}")
        if "u" in overrides and "resistance" not in overrides:
            u = float(overrides["u"])
            surface["resistance"] = max(0.0, 1/u-reference_inside-.04) if u > 0 else 0.0
        surface.update(overrides)
        if name == "floor":
            surface["sky_view"] = 0.0
        surfaces.append(surface)
    if set(options.get("surfaces", {})) - {"wall", "roof", "floor", "window"}:
        raise ValueError("Unknown surface name")
    audit.append("Construction R approximated from U with assumed combined reference films: inside wall/window .13, roof .10, floor .17; outside .04 m2K/W; negative R clipped to zero. Override resistance for known constructions.")
    audit.append("Roof/floor U use WallUValue; wall absorptance uses AbsoRoof; hi=3; ho=5.8+4.1*mean(wind)+5.13. Window external absorptance=0 to avoid duplicate solar gains.")
    values = {key: _schedule(building, key, hoy) for key in (
        "OccupantDensity", "FreshAirPerPerson", "OccupantHeatGain", "EquipmentHeatGain",
        "LightingHeatGain", "HeatingSetpointTemp", "CoolingSetpointTemp")}
    for name, value in values.items():
        if not np.isfinite(value).all() or ("Setpoint" not in name and np.any(value < 0)):
            raise ValueError(f"Invalid {name}")
    internal = gfa * (values["OccupantDensity"] * values["OccupantHeatGain"] + values["EquipmentHeatGain"] + values["LightingHeatGain"])
    clock = hoy % 24
    start, end = building.OccupancyStartHour, building.OccupancyEndHour
    occupied = ((clock >= start) & (clock < end)) if start < end else ((clock >= start) | (clock < end))
    ach = np.where(occupied, building.InfiltrationACH, building.NightVentilationACH)
    ach = ach + values["OccupantDensity"] * values["FreshAirPerPerson"] / height
    if not np.isfinite(ach).all() or np.any(ach < 0):
        raise ValueError("Ventilation ACH must be finite and nonnegative")
    solar_gain = (facade * building.ExteriorWindowArea * stories + roof * building.SkylightArea) * building.WindowSHGC
    glass_fraction = float(options.get("glass_inward_fraction", 0.0))
    if not 0 <= glass_fraction <= 1 or not 0 <= building.WindowSHGC <= 1:
        raise ValueError("SHGC and glass_inward_fraction must be in [0, 1]")
    audit.append("SHGC applied once to incident window/skylight solar. Default all effective gain treated as transmitted; glass inward split is configurable (default 0), not measured optical transmittance.")
    payload = dict(protocol="moosas-steady-4", stage=options.get("stage", "R5"), volume_m3=volume,
                   infiltration_ach=float(options.get("infiltration_ach", np.mean(ach))),
                   density_kg_m3=float(options.get("density_kg_m3", np.mean(density))),
                   cp_j_kg_k=float(options.get("cp_j_kg_k", 1006.0)),
                   cooling_cop=float(building.CoolingEER), heating_cop=float(building.HeatingEER),
                   internal_radiant_fraction=float(options.get("internal_radiant_fraction", .6)),
                   parameters={"interior_convection": 1.0, "exterior_film": 1.0, "solar_floor_fraction": .642},
                   redistribution={"window_fraction": 0.0, "opaque_fraction": 0.0, "delay_hours": 0.0,
                                   "width_hours": 1.0, "background_fraction": 0.0}, surfaces=surfaces, hours=[])
    for key in ("parameters", "redistribution"):
        if set(options.get(key, {})) - set(payload[key]):
            raise ValueError(f"Unknown {key} option")
        payload[key].update(options.get(key, {}))
    audit.append("Uncalibrated redistribution disabled by default; parameters/radiant fraction are explicit adapter assumptions.")
    for j in range(24):
        payload["hours"].append(dict(hour_ending=int(hoy[j]+1), air_c=float(ta[j]),
            boundary_c=[float(ta[j]), float(ta[j]), float(tg[j]), float(ta[j])],
            irradiance_w_m2=[float(facade[j]), float(roof[j]), 0.0, 0.0],
            sky_ir_w_m2=float(5.670374419e-8*(sky[j]+273.15)**4),
            solar_transmitted_w=float(solar_gain[j]*(1-glass_fraction)),
            solar_absorbed_inward_w=float(solar_gain[j]*glass_fraction), internal_w=float(internal[j]),
            heat_setpoint_c=float(values["HeatingSetpointTemp"][j]), cool_setpoint_c=float(values["CoolingSetpointTemp"][j])))
    # Reject nonfinite overrides before writing JSON; Go performs protocol validation.
    json.dumps(payload, allow_nan=False)
    return payload, {"assumptions": audit, "hourly_equivalent_ach": ach.tolist(),
                     "hourly_density_kg_m3": density.tolist(), "options": options}


def run_steady_day(payload, output_dir, *, executable_path=None, timeout_seconds=60):
    """Keep the exact JSON input and engine output for review and reproduction."""
    executable = ensure_steady_binary(executable_path)
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    input_path, output_path = root / "input.json", root / "output.json"
    input_path.write_text(json.dumps(payload, indent=2, allow_nan=False), encoding="utf-8")
    result = subprocess.run([str(executable), str(input_path), str(output_path)],
                            capture_output=True, text=True, timeout=timeout_seconds)
    if result.returncode:
        raise RuntimeError(f"Steady energy failed: {result.stderr.strip()}")
    return json.loads(output_path.read_text(encoding="utf-8"))


def simulate_neighborhood(neighborhood, *, output_dir=None, options=None,
                          executable_path=None, timeout_seconds_per_day=60):
    """Run all buildings and replace their signed net heat feedback to UHI."""
    if not neighborhood.buildings or not neighborhood.climate:
        raise ValueError("Buildings and climate must be initialized")
    hoy = np.concatenate([np.asarray(c.HOY) for c in neighborhood.climate])
    if np.any(np.diff(hoy) <= 0):
        raise ValueError("Climate days must be in strictly increasing HOY order")
    neighborhood.syncSiteGFA()
    area = float(neighborhood.site.TotalArea)
    if not np.isfinite(area) or area <= 0:
        raise ValueError("site.TotalArea must be finite and positive for UHI feedback")
    root = Path(output_dir or Path.cwd() / "outputs" / "energy_steady").resolve()
    executable = ensure_steady_binary(executable_path)
    radiation = None
    if neighborhood._rad and neighborhood._shp:
        from RAD.workflow import prepare_buildings, read_radiation_intensities
        gdf = prepare_buildings(Path(neighborhood._shp), id_field="index", height_field=neighborhood._height_field)
        if len(gdf) != len(neighborhood.buildings):
            raise ValueError("SHP and building counts differ")
        radiation = read_radiation_intensities(neighborhood._rad, gdf, height_field=neighborhood._height_field)
    results = []
    for index, building in enumerate(neighborhood.buildings):
        hours = []
        for climate in neighborhood.climate:
            indices = np.asarray(climate.HOY, dtype=int)
            facade = None if radiation is None else sum(radiation[k][index, indices] for k in ("N", "W", "S", "E"))/4
            roof = None if radiation is None else radiation["T"][index, indices]
            payload, audit = build_steady_input(building, climate, site=neighborhood.site, setting=neighborhood.setting,
                facade_irradiance=facade, roof_irradiance=roof, options=options)
            day_dir = root / f"building_{index:04d}" / f"day_{indices[0]//24+1:03d}"
            output = run_steady_day(payload, day_dir, executable_path=executable, timeout_seconds=timeout_seconds_per_day)
            (day_dir / "mapping.json").write_text(json.dumps(audit, indent=2, allow_nan=False), encoding="utf-8")
            hours.extend(output["hours"])
        cooling = np.array([h["cooling_kwh"] for h in hours])
        heating = np.array([h["heating_kwh"] for h in hours])
        electricity = np.array([h["electricity_kwh"] for h in hours])
        actual_hoy = np.array([h["hour_ending"] for h in hours])-1
        if not np.array_equal(actual_hoy, hoy):
            raise ValueError("Steady output HOYs do not match the climate window")
        air_exchange = np.array([h["balance"]["infiltration_w"] for h in hours])
        try:
            contact = np.array([h["balance"]["contact_transfer_w"] for h in hours], dtype=float)
        except KeyError as exc:
            raise ValueError("Steady executable lacks contact_transfer_w; rebuild MoosasEnergy-new.go") from exc
        if contact.shape != (len(hoy), 4):
            raise ValueError("Steady contact transfer must contain four surfaces per hour")
        # One-hour intervals: W numerically equals Wh. All exchanges are positive inward.
        feedback = dict(load_wh=(1000*(cooling-heating)).tolist(),
                        electricity_wh=(1000*electricity).tolist(),
                        air_exchange_wh=air_exchange.tolist(),
                        contact_transfer_wh=contact.sum(axis=1).tolist())
        updated = deepcopy(building)
        updated.energyModel = "steady"
        updated.energyFeedback = feedback
        updated.energyHOY = actual_hoy.tolist()
        updated.updateACResults()
        results.append(dict(building_index=index, energyHOY=(np.array([h["hour_ending"] for h in hours])-1).tolist(),
            cooling_kwh=cooling.tolist(), heating_kwh=heating.tolist(), hvac_electricity_kwh=electricity.tolist(),
            condenser_rejection_wh=(1000*cooling*(1+1/building.CoolingEER)).tolist(),
            cooling_w_m2=(1000*cooling/building.GFA).tolist(),
            energyModel="steady", energyFeedback=feedback,
            ACLoad=updated.ACLoad.tolist(), ACWasteHeat=updated.ACWasteHeat.tolist(),
            output_dir=str(root / f"building_{index:04d}")))
    (root / "summary.json").write_text(json.dumps(results, indent=2, allow_nan=False), encoding="utf-8")
    # Commit only after every building succeeds, so failed runs preserve prior feedback.
    for building, result in zip(neighborhood.buildings, results):
        building.energyModel = "steady"
        building.energyFeedback = deepcopy(result["energyFeedback"])
        building.energyHOY = result["energyHOY"][:]
        building.loadDecompose = {}
        building.coolingLoad = np.asarray(result["cooling_kwh"])*1000/building.CoolingEER/building.GFA
        building.heatingLoad = np.asarray(result["heating_kwh"])*1000/building.HeatingEER/building.GFA
        building.load = building.coolingLoad - building.heatingLoad
        building.updateACResults()
    neighborhood._writeEnergyFeedback()
    neighborhood._energy_results = deepcopy(results)
    return results
