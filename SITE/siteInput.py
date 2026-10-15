from __future__ import annotations

import csv
import json
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import numpy as np
import rasterio

from .calc_inx_uhi_inputs import calc_inx_uhi_inputs
from .extract_inx_landuse import extract_layers
from .process_BLD_SHP import processBuildingShp
from .process_BLD_TIF import process as process_building_tif


SITE_INPUT_FIELDS = [
    "TotalArea",
    "AreaBuilding",
    "AreaFacade",
    "AreaRoad",
    "AreaGround",
    "AreaLandscape",
    "AreaWater",
    "AreaTreeShading",
    "kS",
    "greenRoofPercentage",
    "groundInfiltratePercentage",
    "vaporWater",
    "vaporLandscape",
    "vaporGround",
    "vaporRoof",
    "coolingLoad",
    "SVF",
    "plotRatio",
    "GFA",
    "SDL",
    "SDG",
    "SRTL",
    "SRTG",
    "fPSA",
    "CL",
    "CG",
]
SITE_ARRAY_FIELDS = {"coolingLoad", "vaporWater", "vaporLandscape", "vaporGround", "vaporRoof"}
SITE_AREA_FIELDS = {"AreaBuilding", "AreaRoad", "AreaGround", "AreaLandscape", "AreaWater"}
SITE_CSV_COLUMNS = [
    "sourceType",
    "sourcePath",
    "sourceName",
    "AreaLandscapeProvided",
    "AreaWaterProvided",
    *SITE_INPUT_FIELDS,
]


def _serialize_value(value: Any) -> str:
    if isinstance(value, np.ndarray):
        return json.dumps([float(item) for item in value.tolist()], ensure_ascii=False)
    if isinstance(value, (list, tuple)):
        return json.dumps([float(item) for item in value], ensure_ascii=False)
    if value is None:
        return ""
    if isinstance(value, (float, int, np.floating, np.integer)):
        return str(float(value))
    return str(value)


def _parse_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return float(text)


def _parse_array(value: Any) -> np.ndarray | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    parsed = json.loads(text)
    if isinstance(parsed, list):
        return np.asarray(parsed, dtype=np.float32)
    return np.asarray([parsed], dtype=np.float32)


def _mean_raster(path: Path) -> float:
    with rasterio.open(path) as src:
        data = src.read(1, masked=True)
    if np.ma.isMaskedArray(data):
        data = data.filled(np.nan)
    data = np.asarray(data, dtype=np.float32)
    if data.size == 0:
        return 0.0
    finite = data[np.isfinite(data)]
    if finite.size == 0:
        return 0.0
    return float(finite.mean())


def _total_area_from_raster(path: Path) -> float:
    with rasterio.open(path) as src:
        pixel_width = abs(float(src.transform.a))
        pixel_height = abs(float(src.transform.e))
        return float(src.width * src.height * pixel_width * pixel_height)


class siteInput(object):
    __slots__ = ("sourceType", "sourcePath", "sourceName", "_provided_fields", *SITE_INPUT_FIELDS)

    def __init__(
        self,
        *,
        sourceType: str | None = None,
        sourcePath: str | None = None,
        sourceName: str | None = None,
        provided_fields: Iterable[str] | None = None,
        **kwargs: Any,
    ) -> None:
        self.sourceType = sourceType
        self.sourcePath = sourcePath
        self.sourceName = sourceName
        self._provided_fields = set(provided_fields or [])
        for field_name in SITE_INPUT_FIELDS:
            setattr(self, field_name, kwargs.get(field_name))

    @classmethod
    def _from_site_kwargs(
        cls,
        *,
        source_type: str,
        source_path: Path,
        site_kwargs: dict[str, Any],
        provided_fields: Iterable[str],
    ) -> "siteInput":
        return cls(
            sourceType=source_type,
            sourcePath=str(source_path.resolve()),
            sourceName=source_path.stem,
            provided_fields=provided_fields,
            **site_kwargs,
        )

    @classmethod
    def fromTIF(
        cls,
        tif_path: str | Path,
        *,
        AreaLandscape: float | None = None,
        AreaWater: float | None = None,
        floorHeight: float = 3.5,
        SVFNeighbor: float = 250.0,
        windDirectionDeg: float = 0.0,
        xmin: float | None = None,
        xmax: float | None = None,
        ymin: float | None = None,
        ymax: float | None = None,
        **overrides: Any,
    ) -> "siteInput":
        tif_path = Path(tif_path)
        metrics = process_building_tif(
            tif_path=tif_path,
            floorHeight=floorHeight,
            SVFNeighbor=SVFNeighbor,
            windDirectionDeg=windDirectionDeg,
            xmin=xmin,
            xmax=xmax,
            ymin=ymin,
            ymax=ymax,
        )
        total_area = float(metrics["TotalArea"])
        site_kwargs: dict[str, Any] = {
            "TotalArea": total_area,
            "AreaBuilding": float(metrics["AreaBuilding"]) * total_area,
            "AreaFacade": float(metrics["AreaFacade"]) * total_area,
            "kS": float(metrics["kS"]),
            "SVF": float(metrics["SVF"]),
            "plotRatio": float(metrics["plotRatio"]),
        }
        provided_fields = {"AreaBuilding", "AreaFacade", "kS", "SVF", "plotRatio", "TotalArea"}
        if AreaLandscape is not None:
            site_kwargs["AreaLandscape"] = float(AreaLandscape)
            provided_fields.add("AreaLandscape")
        if AreaWater is not None:
            site_kwargs["AreaWater"] = float(AreaWater)
            provided_fields.add("AreaWater")
        site_kwargs.update(overrides)

        return cls._from_site_kwargs(
            source_type="tif",
            source_path=tif_path,
            site_kwargs=site_kwargs,
            provided_fields=provided_fields,
        )

    @classmethod
    def fromSHP(
        cls,
        shp_path: str | Path,
        *,
        resolution: float = 5.0,
        height_column: str = "height",
        AreaLandscape: float | None = None,
        AreaWater: float | None = None,
        buff: float = 500.0,
        svf_search_radius: float = 200.0,
        workers: int = 1,
        wind_direction: float = 0.0,
        bounds: tuple[float, float, float, float] | None = None,
        **overrides: Any,
    ) -> "siteInput":
        shp_path = Path(shp_path)
        with tempfile.TemporaryDirectory(prefix="siteinput_shp_") as temp_dir:
            output_dir = Path(temp_dir)
            processBuildingShp(
                shp_path=shp_path,
                output_dir=output_dir,
                resolution=resolution,
                height_column=height_column,
                bounds=bounds,
                buff=buff,
                svf_search_radius=svf_search_radius,
                workers=workers,
                wind_direction=wind_direction,
            )
            total_area = _total_area_from_raster(output_dir / "BuildingAreaRatio.tif")
            site_kwargs: dict[str, Any] = {
                "TotalArea": total_area,
                "AreaBuilding": _mean_raster(output_dir / "BuildingAreaRatio.tif") * total_area,
                "AreaFacade": _mean_raster(output_dir / "facaAreaRatio.tif") * total_area,
                "kS": _mean_raster(output_dir / "kS.tif"),
                "SVF": _mean_raster(output_dir / "SVF.tif"),
                "plotRatio": _mean_raster(output_dir / "FAR.tif"),
            }

        provided_fields = {"AreaBuilding", "AreaFacade", "kS", "SVF", "plotRatio", "TotalArea"}
        if AreaLandscape is not None:
            site_kwargs["AreaLandscape"] = float(AreaLandscape)
            provided_fields.add("AreaLandscape")
        if AreaWater is not None:
            site_kwargs["AreaWater"] = float(AreaWater)
            provided_fields.add("AreaWater")
        site_kwargs.update(overrides)
        return cls._from_site_kwargs(
            source_type="shp",
            source_path=shp_path,
            site_kwargs=site_kwargs,
            provided_fields=provided_fields,
        )

    @classmethod
    def fromINX(
        cls,
        inx_path: str | Path,
        *,
        building_scale: float = 0.1,
        radius_m: float = 1000.0,
        svf_local_radius_m: float = 250.0,
        building_floor_height_m: float = 3.0,
        windDirectionDeg: float = 0.0,
        **overrides: Any,
    ) -> "siteInput":
        inx_path = Path(inx_path)
        with tempfile.TemporaryDirectory(prefix="siteinput_inx_") as temp_dir:
            temp_root = Path(temp_dir)
            extract_dir = temp_root / "extract"
            uhi_dir = temp_root / "uhi"
            extract_layers(inx_path, extract_dir, building_scale=building_scale)
            calc_inx_uhi_inputs(
                extract_dir,
                uhi_dir,
                radius_m=radius_m,
                svf_local_radius_m=svf_local_radius_m,
                building_floor_height_m=building_floor_height_m,
            )
            building_height_tif = extract_dir / "building_height.tif"
            metrics = process_building_tif(
                tif_path=building_height_tif,
                floorHeight=building_floor_height_m,
                SVFNeighbor=svf_local_radius_m,
                windDirectionDeg=windDirectionDeg,
            )
            total_area = float(metrics["TotalArea"])
            site_kwargs: dict[str, Any] = {
                "TotalArea": total_area,
                "AreaBuilding": float(metrics["AreaBuilding"]) * total_area,
                "AreaFacade": float(metrics["AreaFacade"]) * total_area,
                "AreaRoad": _mean_raster(uhi_dir / "road.tif") * total_area,
                "AreaGround": _mean_raster(uhi_dir / "ground.tif") * total_area,
                "AreaLandscape": _mean_raster(uhi_dir / "lanscape.tif") * total_area,
                "AreaWater": _mean_raster(uhi_dir / "water.tif") * total_area,
                "kS": float(metrics["kS"]),
                "SVF": float(metrics["SVF"]),
                "plotRatio": float(metrics["plotRatio"]),
            }

        site_kwargs.update(overrides)
        return cls._from_site_kwargs(
            source_type="inx",
            source_path=inx_path,
            site_kwargs=site_kwargs,
            provided_fields=site_kwargs.keys(),
        )

    def to_row(self) -> dict[str, str]:
        row: dict[str, str] = {
            "sourceType": self.sourceType or "",
            "sourcePath": self.sourcePath or "",
            "sourceName": self.sourceName or "",
            "AreaLandscapeProvided": "1" if "AreaLandscape" in self._provided_fields else "0",
            "AreaWaterProvided": "1" if "AreaWater" in self._provided_fields else "0",
        }
        for field_name in SITE_INPUT_FIELDS:
            value = getattr(self, field_name, None)
            if field_name == "AreaLandscape" and "AreaLandscape" not in self._provided_fields and self.sourceType in {"tif", "shp"}:
                row[field_name] = ""
                continue
            if field_name == "AreaWater" and "AreaWater" not in self._provided_fields and self.sourceType in {"tif", "shp"}:
                row[field_name] = ""
                continue
            row[field_name] = _serialize_value(value)
        return row

    def writeToCSV(self, csv_path: str | Path) -> Path:
        csv_path = Path(csv_path)
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        write_header = not csv_path.exists() or csv_path.stat().st_size == 0
        with csv_path.open("a", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=SITE_CSV_COLUMNS)
            if write_header:
                writer.writeheader()
            writer.writerow(self.to_row())
        return csv_path

    @classmethod
    def fromCSV(cls, csv_path: str | Path, row: int = 0) -> "siteInput":
        csv_path = Path(csv_path)
        with csv_path.open("r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            rows = list(reader)
        if row < 0 or row >= len(rows):
            raise IndexError(f"row index {row} is out of range for {csv_path}")
        record = rows[row]
        kwargs: dict[str, Any] = {}
        for field_name in SITE_INPUT_FIELDS:
            raw_value = record.get(field_name)
            if raw_value is None or not str(raw_value).strip():
                continue
            if field_name in SITE_ARRAY_FIELDS:
                parsed = _parse_array(raw_value)
                if parsed is not None:
                    kwargs[field_name] = parsed
                continue
            kwargs[field_name] = float(raw_value)
        provided_fields = set()
        if record.get("AreaLandscapeProvided", "0") == "1":
            provided_fields.add("AreaLandscape")
        if record.get("AreaWaterProvided", "0") == "1":
            provided_fields.add("AreaWater")
        return cls(
            sourceType=record.get("sourceType") or None,
            sourcePath=record.get("sourcePath") or None,
            sourceName=record.get("sourceName") or None,
            provided_fields=provided_fields,
            **kwargs,
        )
