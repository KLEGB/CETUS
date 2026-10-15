from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin


TARGET_SOIL_CODES = {
    "000000",
    "0200PD",
}

TARGET_WATER_CODES = {
    "0200WW",
}

TARGET_ROAD_CODES = {
    "0200ST",
    "0200AR",
}

TARGET_PAVED_CODES = {
    "0200PG",
}

TARGET_VEGETATION_SURFACE_CODES = {
    "0200GG",
}

TARGET_PLANT_CODES = {
    "0200LG",
    "0200FE",
    "0200H1",
    "0200H2",
    "0200XX",
    "0200XY",
}


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def _parse_scalar(text: str, tag: str, default: str | None = None) -> str | None:
    match = re.search(rf"<{re.escape(tag)}>\s*(.*?)\s*</{re.escape(tag)}>", text, re.S)
    if not match:
        return default
    return match.group(1).strip()


def _parse_matrix(text: str, tag: str, *, dtype: str = "float") -> np.ndarray:
    match = re.search(
        rf"<{re.escape(tag)}[^>]*type=\"matrix-data\"[^>]*>(.*?)</{re.escape(tag)}>",
        text,
        re.S,
    )
    if not match:
        raise ValueError(f"matrix tag not found: {tag}")

    lines = []
    for line in match.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("<"):
            continue
        lines.append(line)

    rows: list[list[float | str]] = []
    for line in lines:
        tokens = [token.strip() for token in line.split(",")]
        if dtype == "str":
            rows.append(tokens)
            continue

        parsed_row: list[float] = []
        for token in tokens:
            if token == "":
                parsed_row.append(0.0)
            else:
                try:
                    parsed_row.append(float(token))
                except ValueError:
                    parsed_row.append(0.0)
        rows.append(parsed_row)

    return np.array(rows, dtype=object if dtype == "str" else np.float32)


def _parse_3dplants(text: str) -> list[dict[str, str]]:
    plants = []
    for match in re.finditer(r"<3Dplants>(.*?)</3Dplants>", text, re.S):
        block = match.group(1)
        record: dict[str, str] = {}
        for key in ("rootcell_i", "rootcell_j", "rootcell_k", "plantID", "name", "observe"):
            key_match = re.search(rf"<{re.escape(key)}>\s*(.*?)\s*</{re.escape(key)}>", block, re.S)
            if key_match:
                record[key] = key_match.group(1).strip()
        if record:
            plants.append(record)
    return plants


def _grid_value(text: str, tag: str, cast=float, default=None):
    value = _parse_scalar(text, tag, None)
    if value is None:
        return default
    try:
        return cast(value)
    except Exception:
        return default


def _build_local_transform(nrows: int, cellsize_x: float, cellsize_y: float):
    return from_origin(0.0, nrows * cellsize_y, cellsize_x, cellsize_y)


def _write_tif(path: Path, array: np.ndarray, *, transform, dtype, nodata):
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=array.shape[0],
        width=array.shape[1],
        count=1,
        dtype=dtype,
        crs=None,
        transform=transform,
        nodata=nodata,
        compress="lzw",
    ) as dst:
        dst.write(array.astype(dtype), 1)


def extract_layers(inx_path: Path, out_dir: Path, *, building_scale: float = 0.1):
    text = _read_text(inx_path)

    ncols = int(_grid_value(text, "grids-I", int))
    nrows = int(_grid_value(text, "grids-J", int))
    dz_base = float(_grid_value(text, "dz-base", float, 1.0))
    dx = float(_grid_value(text, "dx", float, 1.0))
    dy = float(_grid_value(text, "dy", float, 1.0))

    z_top = _parse_matrix(text, "zTop")
    z_bottom = _parse_matrix(text, "zBottom")
    building_nr = _parse_matrix(text, "buildingNr")
    fixed_height = _parse_matrix(text, "fixedheight")
    plants_2d = _parse_matrix(text, "ID_plants1D", dtype="str")
    soil_profile = _parse_matrix(text, "ID_soilprofile", dtype="str")

    if z_top.shape != (nrows, ncols):
        raise ValueError(f"zTop shape mismatch: {z_top.shape} != {(nrows, ncols)}")
    if soil_profile.shape != (nrows, ncols):
        raise ValueError(f"ID_soilprofile shape mismatch: {soil_profile.shape} != {(nrows, ncols)}")

    plants_3d = _parse_3dplants(text)

    building_mask = building_nr > 0
    effective_top = np.maximum(z_top, fixed_height)
    building_height = np.where(building_mask, np.maximum(effective_top - z_bottom, 0.0) * building_scale, 0.0)
    building_height = building_height.astype(np.float32)

    vegetation_mask = np.zeros((nrows, ncols), dtype=np.uint8)
    vegetation_mask |= np.isin(plants_2d, list(TARGET_PLANT_CODES)).astype(np.uint8)
    vegetation_mask |= np.isin(soil_profile, list(TARGET_VEGETATION_SURFACE_CODES)).astype(np.uint8)

    for record in plants_3d:
        try:
            i = int(record.get("rootcell_i", "-1"))
            j = int(record.get("rootcell_j", "-1"))
        except ValueError:
            continue
        if 0 <= j < nrows and 0 <= i < ncols:
            vegetation_mask[j, i] = 1

    water_mask = np.isin(soil_profile, list(TARGET_WATER_CODES)).astype(np.uint8)
    road_mask = np.isin(soil_profile, list(TARGET_ROAD_CODES)).astype(np.uint8)
    paved_mask = np.isin(soil_profile, list(TARGET_PAVED_CODES)).astype(np.uint8)
    soil_mask = np.isin(soil_profile, list(TARGET_SOIL_CODES)).astype(np.uint8)

    surface_class = np.zeros((nrows, ncols), dtype=np.uint8)
    surface_class[soil_mask.astype(bool)] = 6
    surface_class[paved_mask.astype(bool)] = 5
    surface_class[road_mask.astype(bool)] = 4
    surface_class[water_mask.astype(bool)] = 3
    surface_class[vegetation_mask.astype(bool)] = 2
    surface_class[building_mask.astype(bool)] = 1

    transform = _build_local_transform(nrows, dx, dy)

    _write_tif(out_dir / "building_height.tif", building_height, transform=transform, dtype="float32", nodata=0.0)
    _write_tif(out_dir / "vegetation.tif", vegetation_mask, transform=transform, dtype="uint8", nodata=0)
    _write_tif(out_dir / "water.tif", water_mask, transform=transform, dtype="uint8", nodata=0)
    _write_tif(out_dir / "road.tif", road_mask, transform=transform, dtype="uint8", nodata=0)
    _write_tif(out_dir / "paved.tif", paved_mask, transform=transform, dtype="uint8", nodata=0)
    _write_tif(out_dir / "soil.tif", soil_mask, transform=transform, dtype="uint8", nodata=0)
    _write_tif(out_dir / "surface_class.tif", surface_class, transform=transform, dtype="uint8", nodata=0)

    legend = {
        "surface_class": {
            "0": "background/other",
            "1": "building",
            "2": "vegetation",
            "3": "water",
            "4": "road",
            "5": "paved",
            "6": "soil",
        },
        "soil_profile_codes": {
            "water": sorted(TARGET_WATER_CODES),
            "road": sorted(TARGET_ROAD_CODES),
            "paved": sorted(TARGET_PAVED_CODES),
            "soil": sorted(TARGET_SOIL_CODES),
            "vegetation_surface": sorted(TARGET_VEGETATION_SURFACE_CODES),
        },
        "plant_codes": sorted(TARGET_PLANT_CODES),
        "grid": {"ncols": ncols, "nrows": nrows, "dx": dx, "dy": dy, "dz_base": dz_base, "building_scale": building_scale},
    }
    (out_dir / "legend.json").write_text(json.dumps(legend, ensure_ascii=False, indent=2), encoding="utf-8")

    stats = {
        "building_cells": int(building_mask.sum()),
        "vegetation_cells": int(vegetation_mask.sum()),
        "water_cells": int(water_mask.sum()),
        "road_cells": int(road_mask.sum()),
        "paved_cells": int(paved_mask.sum()),
        "soil_cells": int(soil_mask.sum()),
        "3d_plants": len(plants_3d),
    }
    return stats


def main():
    parser = argparse.ArgumentParser(description="Extract ENVI-met INX land-use layers into GeoTIFF rasters.")
    parser.add_argument("--input", default=r"workplace/pazhou_actually.INX", help="Path to the INX file.")
    parser.add_argument("--output-dir", default=r"workplace/rasters", help="Output directory. Default: workplace/rasters.")
    parser.add_argument("--building-scale", type=float, default=0.1, help="Meters per zTop/zBottom unit for building height. Default: 0.1.")
    args = parser.parse_args()

    inx_path = Path(args.input)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    stats = extract_layers(inx_path, out_dir, building_scale=args.building_scale)
    print(json.dumps({"input": str(inx_path), "output_dir": str(out_dir), **stats}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
