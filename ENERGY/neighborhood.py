"""Neighborhood-scale ENERGY workflow coordinator."""

from __future__ import annotations

import copy
import concurrent.futures
import json
import os
import random
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path


import geopandas as gpd
import numpy as np
import pandas as pd

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from .model import BuildingInstance
except ImportError:
    from model import BuildingInstance
from RAD import build_weather_matrix, compute_building_radiation, write_radiation_schedules
from RAD.schedule_writer import sanitize_token

from UHI.constant import *
from UHI.calculator import calculate
from UHI.modelInfo import ClimateInfo, SiteInfo, settingInfo
from WEA import (
    EpwLocation,
    classify_climate_zone,
    day_hoy_range,
    dew_point_from_humidity_ratio,
    epw2location,
    humidity_ratio_from_rh,
    load_epw_file,
    relative_humidity_from_humidity_ratio,
)


def _parse_mm_dd(date: str) -> tuple[int, int]:
    """Parse a date string in mm-dd format."""
    try:
        month_text, day_text = date.split("-", 1)
        return int(month_text), int(day_text)
    except Exception as exc:
        raise ValueError(f"Invalid date '{date}'. Expected format mm-dd.") from exc


def _has_csv_columns(csv_path: Path, *column_names: str) -> bool:
    columns = pd.read_csv(csv_path, nrows=0).columns
    normalized_columns = {str(column).casefold() for column in columns}
    return all(column_name.casefold() in normalized_columns for column_name in column_names)


def _is_moosas_case_csv(csv_path: Path) -> bool:
    return _has_csv_columns(csv_path, "caseId", "t")


def _try_read_site_from_csv(csv_path: Path) -> SiteInfo | None:
    columns = pd.read_csv(csv_path, nrows=0).columns
    normalized_columns = {str(column).casefold() for column in columns}
    site_columns = {slot_name.casefold() for slot_name in SiteInfo.__slots__}
    if normalized_columns.isdisjoint(site_columns):
        return None

    site = SiteInfo.fromCSV(csv_path, row=0)
    if isinstance(site, list):
        return site[0] if site else None
    return site


def _assign_sequential_hoy(climate_series: list[ClimateInfo], start_hoy: int = 0) -> None:
    for day_index, climate in enumerate(climate_series):
        day_start = int(start_hoy) + day_index * 24
        climate.HOY = np.arange(day_start, day_start + 24, dtype=np.int32)


def _infer_location_from_climate_csv(csv_path: Path, climate_series: list[ClimateInfo]) -> EpwLocation:
    if not climate_series:
        return EpwLocation()

    climate_zone: int | None = None
    dry_bulb = np.concatenate([np.asarray(climate.Ta, dtype=float) for climate in climate_series])
    if dry_bulb.shape[0] == 8760:
        try:
            climate_zone = classify_climate_zone(dry_bulb)
        except ValueError:
            climate_zone = None

    pressure = np.concatenate([np.asarray(climate.Pressure, dtype=float) for climate in climate_series])
    return EpwLocation(
        station_id=csv_path.stem,
        pressure=float(np.mean(pressure)) if pressure.size else 0.0,
        climate_zone=climate_zone,
    )


@dataclass
class Neighborhood:
    """Coordinate buildings, weather, radiation, UHI and energy simulations."""

    buildings: list[BuildingInstance] = field(default_factory=list)
    site: SiteInfo = field(default_factory=SiteInfo)
    climate: list[ClimateInfo] = field(default_factory=lambda: [ClimateInfo()])
    location: EpwLocation = field(default_factory=EpwLocation)
    setting: settingInfo = field(default_factory=settingInfo)
    _start_hoy: int = 5544
    _end_hoy: int = 5568
    _expanded_start_hoy: int = 5544
    _expanded_end_hoy: int = 5568
    _shp: str | None = None
    _epw: str | None = None
    _weather_csv: str | None = None
    _mtx: str | None = None
    _rad: str | None = None
    _height_field: str = "height"
    _energy_results: list[dict[str, object]] | None = None

    @classmethod
    def fromFile(
        cls,
        shpFile: str | Path,
        epwFile: str | Path,
        start_hoy: int = 0,
        fvc: float | None = None,
        fwc: float | None = None,
        end_hoy: int = 8759,
    ) -> "Neighborhood":
        """Create a fully initialized neighborhood from SHP/EPW inputs."""
        neighborhood = cls()
        neighborhood.readEPW(epwFile, start_hoy=int(start_hoy), end_hoy=int(end_hoy) + 1)
        neighborhood.readSHP(shpFile)
        if fvc is not None or fwc is not None:
            site_kwargs: dict[str, float] = {"TotalArea": float(neighborhood.site.TotalArea)}
            if fvc is not None:
                site_kwargs["AreaLandscape"] = float(fvc)
            if fwc is not None:
                site_kwargs["AreaWater"] = float(fwc)
            neighborhood.site = SiteInfo(**site_kwargs)
        neighborhood.radSimulation(sample=0.3)
        neighborhood.sampleInstance()
        return neighborhood

    def readSHP(self, shp_path: str | Path, *, height_field: str = "height") -> "Neighborhood":
        """Read building footprints through SITE raster workflow and update buildings/site."""
        shp_path = Path(shp_path).resolve()
        self._height_field = height_field

        from SITE.transform import shp_to_tif

        with tempfile.TemporaryDirectory(prefix="neighborhood_shp_") as temp_dir:
            tif_path = Path(temp_dir) / f"{shp_path.stem}.tif"
            shp_to_tif(shp_path, tif_path, height_column=height_field)
            self.site = SiteInfo.fromTIF(tif_path)
        self.setting = settingInfo.loadJSON(settingInfo.params_dir / f"{self.site.LCZ}.json")
        gdf = gpd.read_file(shp_path)
        if height_field not in gdf.columns:
            raise KeyError(f"Required height field '{height_field}' is missing in {shp_path}")

        gdf = gdf[gdf.geometry.notnull()].copy()
        gdf[height_field] = gdf[height_field].astype(float)
        gdf = gdf[gdf[height_field] > 0].copy()

        buildings: list[BuildingInstance] = []
        for row in gdf.itertuples(index=False):
            building = BuildingInstance()
            building.fromAOI(row.geometry, getattr(row, height_field))
            buildings.append(building)

        self.buildings = buildings
        self._shp = str(shp_path)
        return self

    def sampleInstance(
        self,
        residential: float = 0.3,
        office: float = 0.3,
        commercial: float = 0.3,
        hotel: float = 0.1,
        school: float = 0.0,
        medical: float = 0.0,
        *,
        standard: str = "GB55015-2021",
        seed: int | None = None,
        radFile: str | Path | None = None,
    ) -> "Neighborhood":
        """Randomly assign archetypes to all buildings and attach solar schedules."""
        climate_zone = self.location.climate_zone
        if climate_zone is None:
            raise ValueError("Neighborhood.location.climate_zone is not set. Call readEPW() first.")
        if radFile is not None:
            self._rad = str(Path(radFile).resolve())
        elif self._rad is None:
            raise ValueError("Neighborhood.radSimulation() must be called before sampleInstance().")
        if not self.buildings:
            raise ValueError("Neighborhood.buildings is empty. Call readSHP() first.")
        weights = {
            "residential": float(residential),
            "office": float(office),
            "commercial": float(commercial),
            "hotel": float(hotel),
            "school": float(school),
            "medical": float(medical),
        }
        positive_weights = {name: value for name, value in weights.items() if value > 0.0}
        total_weight = sum(positive_weights.values())
        if total_weight <= 0.0:
            raise ValueError("At least one building type weight must be greater than 0.")

        archetypes = list(positive_weights.keys())
        normalized_weights = [positive_weights[name] / total_weight for name in archetypes]
        rng = random.Random(seed)

        for building in self.buildings:
            chosen_type = rng.choices(archetypes, weights=normalized_weights, k=1)[0]
            building.fromArchetype(climate_zone, chosen_type, standard=standard, rng=rng)
        self._processRad()
        return self

    def readEPW(
        self,
        epwFile: str | Path,
        *,
        start_hoy: int | None = None,
        end_hoy: int | None = None,
        date: str = "08-20",
        caseId: str | None = None,
        climate_zone: int | None = None,
        climateZone: int | None = None,
    ) -> "Neighborhood":
        """Read EPW or MOOSAS CSV weather and split it into daily climates."""
        epw_path = Path(epwFile).resolve()
        if climate_zone is not None and climateZone is not None and int(climate_zone) != int(climateZone):
            raise ValueError("climate_zone and climateZone cannot specify different values.")
        climate_zone_override = climate_zone if climate_zone is not None else climateZone
        if (start_hoy is None) != (end_hoy is None):
            raise ValueError("start_hoy and end_hoy must be provided together.")

        if epw_path.suffix.casefold() == ".csv":
            return self._read_weather_csv(epw_path, start_hoy=start_hoy, end_hoy=end_hoy, caseId=caseId, climate_zone=climate_zone_override)

        if start_hoy is None and end_hoy is None:
            month, day = _parse_mm_dd(date)
            start_hoy, inclusive_end_hoy = day_hoy_range(month, day)
            end_hoy = inclusive_end_hoy + 1
        else:
            start_hoy = int(start_hoy)
            end_hoy = int(end_hoy)
            if start_hoy < 0 or end_hoy <= start_hoy:
                raise ValueError(f"Invalid HOY window: start_hoy={start_hoy}, end_hoy={end_hoy}")

        expanded_start_hoy = (start_hoy // 24) * 24
        expanded_end_hoy = ((end_hoy + 23) // 24) * 24
        location = epw2location(epw_path)
        if climate_zone_override is not None:
            location = EpwLocation(
                station_id=location.station_id,
                city=location.city,
                state=location.state,
                latitude=location.latitude,
                longitude=location.longitude,
                altitude=location.altitude,
                pressure=location.pressure,
                climate_zone=int(climate_zone_override),
            )
        self.location = location
        climate_series = ClimateInfo.fromFile(str(epw_path), start_hoy=expanded_start_hoy, end_hoy=expanded_end_hoy - 1)
        if len(climate_series) == 0:
            raise ValueError(f"No climate data returned for {epw_path}")

        self.climate = climate_series
        self._epw = str(epw_path)
        self._weather_csv = None
        self._start_hoy = start_hoy
        self._end_hoy = end_hoy
        self._expanded_start_hoy = expanded_start_hoy
        self._expanded_end_hoy = expanded_end_hoy
        return self

    def _read_weather_csv(
        self,
        csv_path: Path,
        *,
        start_hoy: int | None = None,
        end_hoy: int | None = None,
        caseId: str | None = None,
        climate_zone: int | None = None,
    ) -> "Neighborhood":
        """Read an already-converted MOOSAS CSV weather file."""
        is_case_csv = _is_moosas_case_csv(csv_path)

        if is_case_csv:
            climate_series = ClimateInfo.fromFile(str(csv_path), caseId=caseId)
            total_hours = len(climate_series) * 24
            if total_hours == 0:
                raise ValueError(f"No climate data returned for {csv_path}")

            if start_hoy is None and end_hoy is None:
                start_hoy = 0
                end_hoy = total_hours
            else:
                start_hoy = int(start_hoy)
                end_hoy = int(end_hoy)
                if start_hoy < 0 or end_hoy <= start_hoy:
                    raise ValueError(f"Invalid HOY window: start_hoy={start_hoy}, end_hoy={end_hoy}")
                if end_hoy > total_hours:
                    raise ValueError(f"Requested end_hoy {end_hoy} exceeds available CSV range 0..{total_hours}")

            expanded_start_hoy = (start_hoy // 24) * 24
            expanded_end_hoy = ((end_hoy + 23) // 24) * 24
            climate_series = climate_series[expanded_start_hoy // 24 : expanded_end_hoy // 24]
            _assign_sequential_hoy(climate_series, expanded_start_hoy)
        else:
            if start_hoy is None and end_hoy is None:
                climate_series = ClimateInfo.fromFile(str(csv_path), caseId=caseId)
                if len(climate_series) == 0:
                    raise ValueError(f"No climate data returned for {csv_path}")
                hoy_values = np.concatenate([np.asarray(climate.HOY, dtype=np.int32) for climate in climate_series])
                start_hoy = int(hoy_values[0])
                end_hoy = int(hoy_values[-1]) + 1
                expanded_start_hoy = int(hoy_values[0])
                expanded_end_hoy = int(hoy_values[-1]) + 1
            else:
                start_hoy = int(start_hoy)
                end_hoy = int(end_hoy)
                if start_hoy < 0 or end_hoy <= start_hoy:
                    raise ValueError(f"Invalid HOY window: start_hoy={start_hoy}, end_hoy={end_hoy}")
                expanded_start_hoy = (start_hoy // 24) * 24
                expanded_end_hoy = ((end_hoy + 23) // 24) * 24
                climate_series = ClimateInfo.fromFile(
                    str(csv_path),
                    caseId=caseId,
                    start_hoy=expanded_start_hoy,
                    end_hoy=expanded_end_hoy - 1,
                )
                if len(climate_series) == 0:
                    raise ValueError(f"No climate data returned for {csv_path}")

        csv_site = _try_read_site_from_csv(csv_path)
        if csv_site is not None:
            self.site = csv_site

        self.climate = climate_series
        self.location = _infer_location_from_climate_csv(csv_path, climate_series)
        if climate_zone is not None:
            self.location = EpwLocation(
                station_id=self.location.station_id,
                city=self.location.city,
                state=self.location.state,
                latitude=self.location.latitude,
                longitude=self.location.longitude,
                altitude=self.location.altitude,
                pressure=self.location.pressure,
                climate_zone=int(climate_zone),
            )
        self._epw = None
        self._weather_csv = str(csv_path)
        self._start_hoy = int(start_hoy)
        self._end_hoy = int(end_hoy)
        self._expanded_start_hoy = int(expanded_start_hoy)
        self._expanded_end_hoy = int(expanded_end_hoy)
        return self

    def radSimulation(self, epwFile: str | Path | None = None, *, radFile: str | Path | None = None, sample: float = 0.3) -> "Neighborhood":
        """Create or reuse yearly radiation results for the current shapefile."""
        if self._shp is None:
            raise ValueError("Neighborhood.readSHP() must be called before radSimulation().")

        if radFile is not None:
            self._rad = str(Path(radFile).resolve())
            return self

        epw_source = self._epw if epwFile is None else epwFile
        if epw_source is None:
            raise ValueError("epwFile is required when radFile is not provided. CSV weather input cannot build a RAD sky matrix.")

        epw_path = Path(epw_source).resolve()
        mtx_path = build_weather_matrix(epw_path)
        floor_height = self.buildings[0].StoryHeight if self.buildings else 4.0
        rad_path = compute_building_radiation(
            self._shp,
            mtx_path,
            id_field="index",
            height_field=self._height_field,
            floor_height=floor_height if floor_height > 0 else 4.0,
            sample=sample
        )

        self._mtx = str(Path(mtx_path).resolve())
        self._rad = str(Path(rad_path).resolve())
        return self

    def _processRad(self) -> "Neighborhood":
        """Write yearly radiation schedules and merge them into buildings by SHP order."""
        if self._shp is None or self._rad is None:
            raise ValueError("Neighborhood.readSHP() and Neighborhood.radSimulation() must be called before solar schedules are processed.")

        schedule_dir = write_radiation_schedules(
            self._shp,
            self._rad,
            wwr=1.0,
            floor_height=[float(building.StoryHeight) if float(building.StoryHeight) > 0 else 4.0 for building in self.buildings],
            shgc=1.0,
            id_field="index",
            height_field=self._height_field,
        )

        gdf = gpd.read_file(self._shp)
        gdf = gdf[gdf.geometry.notnull()].copy()
        gdf[self._height_field] = gdf[self._height_field].astype(float)
        gdf = gdf[gdf[self._height_field] > 0].copy()

        if "index" in gdf.columns:
            building_ids = gdf["index"].astype(str).tolist()
        else:
            building_ids = gdf.index.astype(str).tolist()
        if len(building_ids) != len(self.buildings):
            raise ValueError("Shapefile building count does not match Neighborhood.buildings.")

        schedule_dir = Path(schedule_dir).resolve()
        for building, building_id in zip(self.buildings, building_ids):
            schedule_path = schedule_dir / f"{sanitize_token(building_id)}.sch"
            building.includeSolarSchedule(schedule_path.read_text(encoding="utf-8", errors="replace"))
        return self

    def processRad(self, radFile: str | Path | None = None) -> "Neighborhood":
        """Public entry for merging yearly RAD schedules into all buildings."""
        if radFile is not None:
            self._rad = str(Path(radFile).resolve())
        return self._processRad()

    def uhiSimulation(self, **kwargs):
        """Run UHI for all daily ClimateInfo objects and write temperatures back."""
        settings_kwargs = dict(kwargs)
        preheat = int(settings_kwargs.pop("preheat", 4))
        for key, value in settings_kwargs.items():
            if not hasattr(self.setting, key):
                raise AttributeError(f"settingInfo has no attribute '{key}'")
            setattr(self.setting, key, value)

        site_series = np.array([copy.deepcopy(self.site) for _ in self.climate], dtype=object)
        result = calculate(self.climate, site_series, settings=self.setting, show_progress=False, preheat=preheat)

        for climate_index, climate in enumerate(self.climate):
            climate.Tb = np.asarray(result["Tambient"], dtype=np.float32)[:, climate_index]
            climate.Ta = np.asarray(result["Tair"], dtype=np.float32)[:, climate_index]
            climate.Tg = np.asarray(result["Tsurface"], dtype=np.float32)[:, climate_index]
        return result

    def energySimulation(
        self,
        *,
        building_indices: list[int] | None = None,
        output_dir: str | Path | None = None,
        export_daily: bool = True,
        export_hourly: bool = True,
        max_workers: int | None = None,
    ) -> list[dict[str, object]]:
        """Run per-building MoosasEnergy and write net load to ClimateInfo.coolingLoad."""
        if not self.buildings:
            raise ValueError("Neighborhood.buildings is empty. Call readSHP() first.")
        if len(self.climate) == 0:
            raise ValueError("Neighborhood.climate is empty. Call readEPW() first.")
        if self.location.latitude is None:
            raise ValueError("Neighborhood.location is not initialized. Call readEPW() first.")

        selected_indices = list(range(len(self.buildings))) if building_indices is None else [int(index) for index in building_indices]
        if not selected_indices:
            raise ValueError("building_indices must select at least one building.")
        if any(index < 0 or index >= len(self.buildings) for index in selected_indices):
            raise IndexError("building_indices contains an out-of-range index.")
        if max_workers is not None and int(max_workers) < 1:
            raise ValueError("max_workers must be at least 1.")

        if output_dir is None:
            base_name = Path(self._shp).stem if self._shp else "neighborhood"
            simulation_root = Path.cwd() / "outputs" / "energy" / f"{base_name}_{self._start_hoy}_{self._end_hoy}"
        else:
            simulation_root = Path(output_dir).resolve()
        simulation_root.mkdir(parents=True, exist_ok=True)
        weather_path = ClimateInfo.seriesToCSV(
            self.climate,
            simulation_root / "weather.csv",
            station_id=self.location.station_id or "000000",
            include_header=False,
        )

        results: list[dict[str, object]] = []
        total_hours = len(self.climate) * 24
        worker_count = int(max_workers) if max_workers is not None else min(len(selected_indices), os.cpu_count() or 1)

        def _simulate_building(building_index: int) -> dict[str, object]:
            building = copy.deepcopy(self.buildings[building_index])
            building_root = simulation_root / f"building_{building_index:04d}"
            building_root.mkdir(parents=True, exist_ok=True)
            simulation_result = building.simulation(
                self.location,
                year=len(self.climate) == 365,
                month=True,
                daily=export_daily,
                hourly=export_hourly,
                weather_path=weather_path,
                work_dir=building_root,
            )
            if np.asarray(building.load).shape[0] != total_hours:
                raise ValueError(
                    f"Energy output for building {building_index} contains {np.asarray(building.load).shape[0]} hourly rows, expected {total_hours}."
                )
            return {
                "building_index": building_index,
                "archetype": building.archetype,
                "result": simulation_result,
                "load": np.asarray(building.load, dtype=np.float32).copy(),
                "weather_path": str(Path(weather_path).resolve()),
                "output_dir": str(building_root.resolve()),
            }

        if worker_count == 1:
            results = [_simulate_building(index) for index in selected_indices]
        else:
            completed_results: dict[int, dict[str, object]] = {}
            with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
                future_to_index = {executor.submit(_simulate_building, index): index for index in selected_indices}
                for future in concurrent.futures.as_completed(future_to_index):
                    building_index = future_to_index[future]
                    completed_results[building_index] = future.result()
            results = [completed_results[index] for index in selected_indices]

        for result in results:
            self.buildings[int(result["building_index"])].load = np.asarray(result["load"], dtype=np.float32).copy()

        climate_hourly_load = np.sum(
            [
                np.asarray(building.load, dtype=np.float32) * float(building.GFA)
                for building in self.buildings
            ],
            axis=0,
        ) / max(float(self.site.TotalArea), 1e-6)
        climate_hourly_load = np.asarray(climate_hourly_load, dtype=np.float32)
        for climate_index, climate in enumerate(self.climate):
            start = climate_index * 24
            if self.setting.isWeekDay:
                loadCoff = np.array(occDenseWeekDay['office'])/peakDense['office']
            else:
                loadCoff = np.array(occDenseWeekEnd['office'])/peakDense['office']
            climate_hourly_load[start : start + 24] *= loadCoff
            climate.coolingLoad = [float(value) for value in climate_hourly_load[start : start + 24].tolist()]

        self._energy_results = [
            {
                **result,
                "load": np.asarray(result["load"], dtype=np.float32).tolist(),
            }
            for result in results
        ]
        return results

    def writeEPW(self, epwPath: str | Path) -> Path:
        """Write a new EPW whose dry-bulb temperature follows simulated climate.Ta."""
        if self._epw is None:
            raise ValueError("writeEPW() requires an EPW source. CSV weather input cannot be written back as EPW.")
        if len(self.climate) == 0:
            raise ValueError("Neighborhood.climate is empty. Run readEPW() and uhiSimulation() first.")

        source_epw = Path(self._epw).resolve()
        output_epw = Path(epwPath).resolve()
        output_epw.parent.mkdir(parents=True, exist_ok=True)

        header_rows, climate_rows = load_epw_file(source_epw)

        original_ta = np.asarray([float(row[6]) for row in climate_rows], dtype=float)
        original_rh_fraction = np.asarray([float(row[8]) for row in climate_rows], dtype=float) / 100.0
        original_pressure = np.asarray([float(row[9]) for row in climate_rows], dtype=float)
        _original_dew_point = np.asarray([float(row[7]) for row in climate_rows], dtype=float)

        humidity_ratio = humidity_ratio_from_rh(original_ta, original_rh_fraction, original_pressure)

        climate_ta_series = np.concatenate([np.asarray(climate.Ta, dtype=float) for climate in self.climate], axis=0)
        available_hours = self._expanded_end_hoy - self._expanded_start_hoy
        if climate_ta_series.shape[0] != available_hours:
            raise ValueError(
                "Neighborhood climate length does not match expanded HOY window: "
                f"{climate_ta_series.shape[0]} vs {available_hours}"
            )

        replace_start = self._start_hoy
        replace_end = self._end_hoy
        climate_offset = replace_start - self._expanded_start_hoy
        climate_end = climate_offset + (replace_end - replace_start)
        if climate_offset < 0 or climate_end > climate_ta_series.shape[0]:
            raise ValueError("Requested HOY window is inconsistent with expanded climate series.")

        updated_ta = original_ta.copy()
        updated_ta[replace_start:replace_end] = climate_ta_series[climate_offset:climate_end]

        updated_rh_fraction = relative_humidity_from_humidity_ratio(updated_ta, humidity_ratio, original_pressure)
        updated_dew_point = dew_point_from_humidity_ratio(humidity_ratio, original_pressure)

        for hour_index, row in enumerate(climate_rows):
            row[6] = f"{updated_ta[hour_index]:.1f}"
            row[7] = f"{updated_dew_point[hour_index]:.1f}"
            row[8] = str(int(round(float(updated_rh_fraction[hour_index]) * 100.0)))

        import csv

        with output_epw.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerows(header_rows)
            writer.writerows(climate_rows)
        return output_epw

    def toDict(self) -> dict[str, object]:
        """Serialize the neighborhood workflow state to a JSON-compatible dict."""
        return {
            "buildings": [building.to_dict() for building in self.buildings],
            "site": self.site.to_dict(),
            "climate": [climate.to_dict() for climate in self.climate],
            "location": {
                "station_id": self.location.station_id,
                "city": self.location.city,
                "state": self.location.state,
                "latitude": self.location.latitude,
                "longitude": self.location.longitude,
                "altitude": self.location.altitude,
                "pressure": self.location.pressure,
                "climate_zone": self.location.climate_zone,
            },
            "setting": self.setting.to_dict(),
            "_start_hoy": self._start_hoy,
            "_end_hoy": self._end_hoy,
            "_expanded_start_hoy": self._expanded_start_hoy,
            "_expanded_end_hoy": self._expanded_end_hoy,
            "_shp": self._shp,
            "_epw": self._epw,
            "_weather_csv": self._weather_csv,
            "_mtx": self._mtx,
            "_rad": self._rad,
            "_height_field": self._height_field,
            "_energy_results": self._energy_results,
        }

    @classmethod
    def fromDict(cls, data: dict[str, object]) -> "Neighborhood":
        """Restore a neighborhood workflow state from a JSON-compatible dict."""
        location_data = dict(data.get("location", {}))
        neighborhood = cls(
            buildings=[BuildingInstance.from_dict(item) for item in data.get("buildings", [])],
            site=SiteInfo.from_dict(dict(data["site"])) if "site" in data else SiteInfo(),
            climate=[ClimateInfo.from_dict(dict(item)) for item in data.get("climate", [])] if "climate" in data else [ClimateInfo()],
            location=EpwLocation(**location_data) if location_data else EpwLocation(),
            setting=settingInfo.from_dict(dict(data["setting"])) if "setting" in data else settingInfo(),
            _start_hoy=int(data.get("_start_hoy", 5544)),
            _end_hoy=int(data.get("_end_hoy", 5568)),
            _expanded_start_hoy=int(data.get("_expanded_start_hoy", data.get("_start_hoy", 5544))),
            _expanded_end_hoy=int(data.get("_expanded_end_hoy", data.get("_end_hoy", 5568))),
            _shp=data.get("_shp"),
            _epw=data.get("_epw"),
            _weather_csv=data.get("_weather_csv"),
            _mtx=data.get("_mtx"),
            _rad=data.get("_rad"),
            _height_field=str(data.get("_height_field", "height")),
            _energy_results=data.get("_energy_results"),
        )
        return neighborhood

    def to_dict(self) -> dict[str, object]:
        """Compatibility wrapper for legacy snake_case serialization."""
        return self.toDict()

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "Neighborhood":
        """Compatibility wrapper for legacy snake_case deserialization."""
        return cls.fromDict(data)

    def saveJSON(self, output_path: str | Path) -> Path:
        """Save this neighborhood as formatted JSON."""
        output_path = Path(output_path).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return output_path

    @classmethod
    def loadJSON(cls, input_path: str | Path) -> "Neighborhood":
        """Load one neighborhood from a JSON file."""
        return cls.from_dict(json.loads(Path(input_path).read_text(encoding="utf-8")))


if __name__ == "__main__":
    test_neighborhood = Neighborhood()
    print(
        {
            "buildingCount": len(test_neighborhood.buildings),
            "climateDays": len(test_neighborhood.climate),
            "hoyWindow": [test_neighborhood._start_hoy, test_neighborhood._end_hoy],
        }
    )
