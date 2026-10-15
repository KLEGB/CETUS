from __future__ import annotations

import argparse
import math
import tempfile
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.features import rasterize, shapes
from rasterio.transform import from_origin
from shapely.geometry import shape

from .extract_inx_landuse import extract_layers


DEFAULT_BUILDING_SCALE = 0.1
DEFAULT_HEIGHT_COLUMN = "Height"
DEFAULT_RESOLUTION = 5.0
VEGETATION_CODE = "0200GG"
WATER_CODE = "0200WW"
ROAD_CODE = "0200ST"
SOIL_CODE = "000000"


def _normalize_bounds(bounds: tuple[float, float, float, float] | None) -> tuple[float, float, float, float] | None:
    if bounds is None:
        return None
    xmin, ymin, xmax, ymax = bounds
    xmin = float(xmin)
    ymin = float(ymin)
    xmax = float(xmax)
    ymax = float(ymax)
    if xmin >= xmax or ymin >= ymax:
        raise ValueError("bounds must satisfy xmin < xmax and ymin < ymax")
    return xmin, ymin, xmax, ymax


def _resolve_height_column(gdf: gpd.GeoDataFrame, requested: str) -> str:
    columns = list(gdf.columns)
    lookup = {str(column).casefold(): str(column) for column in columns}
    candidates = [requested, "Height", "height", "Mean_H", "pred_h_r", "pred_h"]
    for candidate in candidates:
        key = str(candidate).casefold()
        if key in lookup:
            return lookup[key]
    raise KeyError(f"Height column '{requested}' not found in {columns}")


def _write_multiband_tif(
    output_path: Path,
    arrays: list[np.ndarray],
    *,
    transform,
    crs,
    nodata: float = 0.0,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    profile = {
        "driver": "GTiff",
        "height": int(arrays[0].shape[0]),
        "width": int(arrays[0].shape[1]),
        "count": len(arrays),
        "dtype": "float32",
        "transform": transform,
        "crs": crs,
        "compress": "lzw",
        "nodata": nodata,
    }
    with rasterio.open(output_path, "w", **profile) as dst:
        for index, array in enumerate(arrays, start=1):
            dst.write(np.asarray(array, dtype=np.float32), index)
    return output_path


def _remove_existing_shapefile(path: Path) -> None:
    stem = path.with_suffix("")
    for ext in (".shp", ".shx", ".dbf", ".prj", ".cpg", ".qix", ".fix", ".sbn", ".sbx"):
        candidate = stem.with_suffix(ext)
        if candidate.exists():
            candidate.unlink()


def _format_matrix(matrix: np.ndarray, *, string_values: bool = False) -> str:
    rows: list[str] = []
    for row in matrix:
        values = []
        for value in row.tolist():
            if string_values:
                values.append(str(value))
            else:
                numeric = float(value)
                if numeric.is_integer():
                    values.append(str(int(numeric)))
                else:
                    values.append(f"{numeric:.5f}")
        rows.append("     " + ",".join(values))
    return "\n".join(rows)


def _build_inx_text(
    *,
    building_height: np.ndarray,
    vegetation: np.ndarray | None,
    water: np.ndarray | None,
    road: np.ndarray | None,
    dx: float,
    dy: float,
    building_scale: float,
    dz_base: float = 1.0,
) -> str:
    nrows, ncols = building_height.shape
    building_mask = np.asarray(building_height > 0.0, dtype=np.int32)
    z_bottom = np.zeros((nrows, ncols), dtype=np.int32)
    z_top = np.where(
        building_mask > 0,
        np.maximum(np.rint(np.asarray(building_height, dtype=np.float32) / max(building_scale, 1e-6)), 1),
        0,
    ).astype(np.int32)
    fixed_height = z_top.copy()
    building_nr = building_mask.astype(np.int32)

    soil_profile = np.full((nrows, ncols), SOIL_CODE, dtype=object)
    if vegetation is not None:
        soil_profile[np.asarray(vegetation) > 0] = VEGETATION_CODE
    if road is not None:
        soil_profile[np.asarray(road) > 0] = ROAD_CODE
    if water is not None:
        soil_profile[np.asarray(water) > 0] = WATER_CODE

    plants_1d = np.full((nrows, ncols), "", dtype=object)

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<ENVI-MET_Datafile>
  <modelGeometry>
     <grids-I> {ncols} </grids-I>
     <grids-J> {nrows} </grids-J>
     <grids-Z> 30 </grids-Z>
     <dx> {dx:.5f} </dx>
     <dy> {dy:.5f} </dy>
     <dz-base> {dz_base:.5f} </dz-base>
  </modelGeometry>
  <buildingData>
     <buildingNr type="matrix-data" dataI="{ncols}" dataJ="{nrows}">
{_format_matrix(building_nr)}
     </buildingNr>
     <zTop type="matrix-data" dataI="{ncols}" dataJ="{nrows}">
{_format_matrix(z_top)}
     </zTop>
     <zBottom type="matrix-data" dataI="{ncols}" dataJ="{nrows}">
{_format_matrix(z_bottom)}
     </zBottom>
     <fixedheight type="matrix-data" dataI="{ncols}" dataJ="{nrows}">
{_format_matrix(fixed_height)}
     </fixedheight>
  </buildingData>
  <surfaceData>
     <ID_soilprofile type="matrix-data" dataI="{ncols}" dataJ="{nrows}">
{_format_matrix(soil_profile, string_values=True)}
     </ID_soilprofile>
     <ID_plants1D type="matrix-data" dataI="{ncols}" dataJ="{nrows}">
{_format_matrix(plants_1d, string_values=True)}
     </ID_plants1D>
  </surfaceData>
</ENVI-MET_Datafile>
"""


def inx_to_tif(
    inx_path: str | Path,
    output_tif: str | Path,
    *,
    building_scale: float = DEFAULT_BUILDING_SCALE,
    **_unused: Any,
) -> Path:
    inx_path = Path(inx_path)
    output_tif = Path(output_tif)
    with tempfile.TemporaryDirectory(prefix="inx_to_tif_") as temp_dir:
        temp_root = Path(temp_dir)
        extract_layers(inx_path, temp_root, building_scale=building_scale)
        arrays: list[np.ndarray] = []
        with rasterio.open(temp_root / "building_height.tif") as src:
            transform = src.transform
            crs = src.crs
            arrays.append(src.read(1).astype(np.float32))
        for name in ("vegetation.tif", "water.tif", "road.tif"):
            with rasterio.open(temp_root / name) as src:
                arrays.append(src.read(1).astype(np.float32))
    return _write_multiband_tif(output_tif, arrays, transform=transform, crs=crs, nodata=0.0)


def tif_to_inx(
    tif_path: str | Path,
    output_inx: str | Path,
    *,
    building_scale: float = DEFAULT_BUILDING_SCALE,
    dz_base: float = 1.0,
    **_unused: Any,
) -> Path:
    tif_path = Path(tif_path)
    output_inx = Path(output_inx)
    with rasterio.open(tif_path) as src:
        band_count = src.count
        building = src.read(1).astype(np.float32)
        vegetation = src.read(2).astype(np.float32) if band_count >= 2 else None
        water = src.read(3).astype(np.float32) if band_count >= 3 else None
        road = src.read(4).astype(np.float32) if band_count >= 4 else None
        dx = abs(float(src.transform.a))
        dy = abs(float(src.transform.e))

    text = _build_inx_text(
        building_height=building,
        vegetation=vegetation,
        water=water,
        road=road,
        dx=dx,
        dy=dy,
        building_scale=building_scale,
        dz_base=dz_base,
    )
    output_inx.parent.mkdir(parents=True, exist_ok=True)
    output_inx.write_text(text, encoding="utf-8")
    return output_inx


def shp_to_tif(
    shp_path: str | Path,
    output_tif: str | Path,
    *,
    resolution: float = DEFAULT_RESOLUTION,
    height_column: str = DEFAULT_HEIGHT_COLUMN,
    all_touched: bool = False,
    bounds: tuple[float, float, float, float] | None = None,
    **_unused: Any,
) -> Path:
    shp_path = Path(shp_path)
    output_tif = Path(output_tif)
    gdf = gpd.read_file(shp_path)
    if gdf.empty:
        raise ValueError(f"Shapefile has no features: {shp_path}")

    resolved_height = _resolve_height_column(gdf, height_column)
    gdf = gdf.loc[gdf.geometry.notna() & ~gdf.geometry.is_empty].copy()
    gdf[resolved_height] = pd.to_numeric(gdf[resolved_height], errors="coerce")
    gdf = gdf.loc[gdf[resolved_height].notna()].copy()
    if gdf.empty:
        raise ValueError(f"No valid height features found in {shp_path}")

    normalized_bounds = _normalize_bounds(bounds)
    if normalized_bounds is None:
        minx, miny, maxx, maxy = gdf.total_bounds
    else:
        minx, miny, maxx, maxy = normalized_bounds
    width = max(1, int(math.ceil((maxx - minx) / resolution)))
    height = max(1, int(math.ceil((maxy - miny) / resolution)))
    transform = from_origin(minx, maxy, resolution, resolution)
    shapes_to_burn = [(geom, float(value)) for geom, value in zip(gdf.geometry, gdf[resolved_height])]
    shapes_to_burn.sort(key=lambda item: item[1])
    array = rasterize(
        shapes_to_burn,
        out_shape=(height, width),
        fill=0.0,
        transform=transform,
        dtype="float32",
        all_touched=all_touched,
    ).astype(np.float32)
    return _write_multiband_tif(output_tif, [array], transform=transform, crs=gdf.crs, nodata=0.0)


def tif_to_shp(
    tif_path: str | Path,
    output_shp: str | Path,
    *,
    height_column: str = DEFAULT_HEIGHT_COLUMN,
    **_unused: Any,
) -> Path:
    tif_path = Path(tif_path)
    output_shp = Path(output_shp)
    with rasterio.open(tif_path) as src:
        data = src.read(1).astype(np.float32)
        mask = data > 0.0
        features = []
        for geometry, value in shapes(data, mask=mask, transform=src.transform):
            height_value = float(value)
            if height_value <= 0.0:
                continue
            features.append({height_column: height_value, "geometry": shape(geometry)})
        gdf = gpd.GeoDataFrame(features, geometry="geometry", crs=src.crs)

    output_shp.parent.mkdir(parents=True, exist_ok=True)
    _remove_existing_shapefile(output_shp)
    gdf.to_file(output_shp, driver="ESRI Shapefile", encoding="UTF-8")
    return output_shp


def inx_to_shp(
    inx_path: str | Path,
    output_shp: str | Path,
    *,
    building_scale: float = DEFAULT_BUILDING_SCALE,
    height_column: str = DEFAULT_HEIGHT_COLUMN,
    **_unused: Any,
) -> Path:
    with tempfile.TemporaryDirectory(prefix="inx_to_shp_") as temp_dir:
        temp_tif = Path(temp_dir) / "from_inx.tif"
        inx_to_tif(inx_path, temp_tif, building_scale=building_scale)
        return tif_to_shp(temp_tif, output_shp, height_column=height_column)


def shp_to_inx(
    shp_path: str | Path,
    output_inx: str | Path,
    *,
    resolution: float = DEFAULT_RESOLUTION,
    height_column: str = DEFAULT_HEIGHT_COLUMN,
    building_scale: float = DEFAULT_BUILDING_SCALE,
    bounds: tuple[float, float, float, float] | None = None,
    **_unused: Any,
) -> Path:
    with tempfile.TemporaryDirectory(prefix="shp_to_inx_") as temp_dir:
        temp_tif = Path(temp_dir) / "from_shp.tif"
        shp_to_tif(shp_path, temp_tif, resolution=resolution, height_column=height_column, bounds=bounds)
        return tif_to_inx(temp_tif, output_inx, building_scale=building_scale)


def convert(source_path: str | Path, output_path: str | Path, *, source_format: str, target_format: str, **kwargs: Any) -> Path:
    key = (source_format.casefold(), target_format.casefold())
    handlers = {
        ("inx", "tif"): inx_to_tif,
        ("tif", "inx"): tif_to_inx,
        ("shp", "tif"): shp_to_tif,
        ("tif", "shp"): tif_to_shp,
        ("inx", "shp"): inx_to_shp,
        ("shp", "inx"): shp_to_inx,
    }
    if key not in handlers:
        raise ValueError(f"Unsupported conversion: {source_format} -> {target_format}")
    return handlers[key](source_path, output_path, **kwargs)


def main() -> None:
    parser = argparse.ArgumentParser(description="Transform INX, TIF and SHP files between each other.")
    parser.add_argument("--input", required=True, help="Input path.")
    parser.add_argument("--output", required=True, help="Output path.")
    parser.add_argument("--source-format", required=True, choices=["inx", "tif", "shp"])
    parser.add_argument("--target-format", required=True, choices=["inx", "tif", "shp"])
    parser.add_argument("--resolution", type=float, default=DEFAULT_RESOLUTION)
    parser.add_argument("--height-column", default=DEFAULT_HEIGHT_COLUMN)
    parser.add_argument("--building-scale", type=float, default=DEFAULT_BUILDING_SCALE)
    parser.add_argument("--dz-base", type=float, default=1.0)
    parser.add_argument("--xmin", type=float)
    parser.add_argument("--ymin", type=float)
    parser.add_argument("--xmax", type=float)
    parser.add_argument("--ymax", type=float)
    args = parser.parse_args()

    bounds_values = [args.xmin, args.ymin, args.xmax, args.ymax]
    if any(value is not None for value in bounds_values) and not all(value is not None for value in bounds_values):
        raise ValueError("xmin, ymin, xmax, ymax must all be provided together.")
    bounds = tuple(float(value) for value in bounds_values) if all(value is not None for value in bounds_values) else None

    convert(
        args.input,
        args.output,
        source_format=args.source_format,
        target_format=args.target_format,
        resolution=float(args.resolution),
        height_column=str(args.height_column),
        building_scale=float(args.building_scale),
        dz_base=float(args.dz_base),
        bounds=bounds,
    )


if __name__ == "__main__":
    main()
