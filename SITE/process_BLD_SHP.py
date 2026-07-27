from __future__ import annotations

import argparse
import json
import math
import os
import warnings
from dataclasses import dataclass
from multiprocessing import get_context
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
import shapely
from shapely.strtree import STRtree
from pyproj import CRS, Transformer
from rasterio.transform import from_origin


@dataclass(frozen=True)
class Vector:
    origin: np.ndarray
    direction: np.ndarray


@dataclass(frozen=True)
class Mesh:
    edge: shapely.Geometry
    height: float


def _unit(v: np.ndarray) -> np.ndarray:
    """
    Helper function used by the UHI calculation workflow.
    """
    norm = float(np.linalg.norm(v))
    if norm == 0.0:
        return v
    return v / norm


def _loadSkyVectors() -> list[np.ndarray]:
    """
    Load input data required by the model workflow.
    """
    cetus_root = Path(__file__).resolve().parents[1]
    candidates = [
        cetus_root / "RAD" / "data" / "sun_position.csv",
        cetus_root / "UHI" / "sky_vector.csv",
        Path(__file__).resolve().parents[2] / "UHI" / "sky_vector.csv",
    ]
    sky_path = next((path for path in candidates if path.exists()), None)
    if sky_path is None:
        raise FileNotFoundError("No sky-vector CSV found in CETUS RAD/data or UHI resources.")
    vectors = pd.read_csv(sky_path).to_numpy(dtype=float)
    return [_unit(v) for v in vectors]


SKY_VECTORS = _loadSkyVectors()
DEFAULT_BUFF_M = 500.0
DEFAULT_SVF_SEARCH_RADIUS_M = 200.0
DEFAULT_WORKERS = max(1, (os.cpu_count() or 1) - 1)
DEFAULT_WIND_DIRECTION_DEG = 0.0
_SVF_WORKER_STATE: dict[str, object] = {}


def _isUtmCrs(crs: CRS | None) -> bool:
    """
    Helper function used by the UHI calculation workflow.
    """
    if crs is None:
        return False
    try:
        return crs.is_projected and "UTM" in crs.name.upper()
    except Exception:
        return False


def _looksLikeLonlatBounds(bounds: tuple[float, float, float, float]) -> bool:
    """
    Helper function used by the UHI calculation workflow.
    """
    xmin, ymin, xmax, ymax = bounds
    return (
        -180.0 <= xmin <= 180.0
        and -180.0 <= xmax <= 180.0
        and -90.0 <= ymin <= 90.0
        and -90.0 <= ymax <= 90.0
    )


def _looksLikeLonlatPoint(lat: float, lon: float) -> bool:
    """
    Helper function used by the UHI calculation workflow.
    """
    return -180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0


def _utmEpsgForLonlat(lon: float, lat: float) -> int:
    """
    Helper function used by the UHI calculation workflow.
    """
    zone = int(math.floor((lon + 180.0) / 6.0) + 1)
    zone = max(1, min(zone, 60))
    return 32600 + zone if lat >= 0 else 32700 + zone


def _chooseTargetUtm(shp: gpd.GeoDataFrame, bounds: tuple[float, float, float, float] | None = None) -> CRS:
    """
    Helper function used by the UHI calculation workflow.
    """
    if bounds and _looksLikeLonlatBounds(bounds):
        lon = (bounds[0] + bounds[2]) * 0.5
        lat = (bounds[1] + bounds[3]) * 0.5
        return CRS.from_epsg(_utmEpsgForLonlat(lon, lat))

    if shp.crs is None:
        if bounds and not _looksLikeLonlatBounds(bounds):
            raise ValueError("Shapefile has no CRS. Cannot interpret projected bounds safely.")
        raise ValueError("Shapefile has no CRS. Please provide a CRS-aware shapefile.")

    geographic = shp if shp.crs.is_geographic else shp.to_crs(4326)
    xmin, ymin, xmax, ymax = geographic.total_bounds
    lon = (xmin + xmax) * 0.5
    lat = (ymin + ymax) * 0.5
    return CRS.from_epsg(_utmEpsgForLonlat(lon, lat))


def _normalizeBounds(bounds: tuple[float, float, float, float] | None) -> tuple[float, float, float, float] | None:
    """
    Helper function used by the UHI calculation workflow.
    """
    if bounds is None:
        return None
    xmin, xmax, ymin, ymax = bounds
    if xmin >= xmax or ymin >= ymax:
        raise ValueError("Bounds must satisfy xmin < xmax and ymin < ymax.")
    return float(xmin), float(ymin), float(xmax), float(ymax)


def _resolveTargetCrsForPoint(shp: gpd.GeoDataFrame, lat: float, lon: float) -> CRS:
    """
    Helper function used by the UHI calculation workflow.
    """
    if shp.crs is None:
        raise ValueError("Shapefile CRS is missing.")
    src_crs = CRS.from_user_input(shp.crs)
    if _isUtmCrs(src_crs):
        return src_crs
    if _looksLikeLonlatPoint(lat, lon):
        return CRS.from_epsg(_utmEpsgForLonlat(lon, lat))
    return _chooseTargetUtm(shp, None)


def _pointToTargetUtm(lat: float, lon: float, target_crs: CRS) -> tuple[float, float]:
    """
    Helper function used by the UHI calculation workflow.
    """
    if _looksLikeLonlatPoint(lat, lon):
        transformer = Transformer.from_crs(4326, target_crs, always_xy=True)
        x, y = transformer.transform(lon, lat)
        return float(x), float(y)
    return float(lon), float(lat)


def _pointToWgs84(lat: float, lon: float, source_crs: CRS) -> tuple[float, float]:
    """
    Helper function used by the UHI calculation workflow.
    """
    if _looksLikeLonlatPoint(lat, lon):
        return float(lat), float(lon)
    transformer = Transformer.from_crs(source_crs, 4326, always_xy=True)
    out_lon, out_lat = transformer.transform(lon, lat)
    return float(out_lat), float(out_lon)


def _toTargetUtm(
    shp: gpd.GeoDataFrame,
    bounds: tuple[float, float, float, float] | None,
) -> tuple[gpd.GeoDataFrame, tuple[float, float, float, float], CRS, dict[str, str | int]]:
    """
    Helper function used by the UHI calculation workflow.
    """
    requested = _normalizeBounds(bounds)
    if shp.crs is None:
        if requested is not None and _looksLikeLonlatBounds(requested):
            raise ValueError("Shapefile CRS is missing. Cannot safely transform lon/lat bounds.")
        xmin, ymin, xmax, ymax = shp.total_bounds
        target_crs = CRS.from_epsg(3857)
        meta = {
            "source_crs": "missing_assumed_local_metric",
            "target_crs": target_crs.to_string(),
            "bounds_source": "shp_extent" if requested is None else "local_metric_input",
            "target_epsg": target_crs.to_epsg() or target_crs.to_string(),
        }
        if requested is not None:
            xmin, ymin, xmax, ymax = requested
        return shp.copy(), (float(xmin), float(ymin), float(xmax), float(ymax)), target_crs, meta

    src_crs = CRS.from_user_input(shp.crs)
    target_crs = src_crs if _isUtmCrs(src_crs) else _chooseTargetUtm(shp, requested)
    shp_utm = shp.to_crs(target_crs) if src_crs != target_crs else shp.copy()

    if requested is None:
        xmin, ymin, xmax, ymax = shp_utm.total_bounds
        bounds_source = "shp_extent"
    elif _looksLikeLonlatBounds(requested):
        transformer = Transformer.from_crs(4326, target_crs, always_xy=True)
        xs = [requested[0], requested[2], requested[2], requested[0]]
        ys = [requested[1], requested[1], requested[3], requested[3]]
        tx, ty = transformer.transform(xs, ys)
        xmin, xmax = min(tx), max(tx)
        ymin, ymax = min(ty), max(ty)
        bounds_source = "lonlat_to_utm"
    else:
        xmin, ymin, xmax, ymax = requested
        bounds_source = "utm_input"

    meta = {
        "source_crs": src_crs.to_string(),
        "target_crs": target_crs.to_string(),
        "bounds_source": bounds_source,
        "target_epsg": target_crs.to_epsg() or target_crs.to_string(),
    }
    return shp_utm, (xmin, ymin, xmax, ymax), target_crs, meta


def _explodePolygons(geometry: shapely.Geometry) -> list[shapely.Polygon]:
    """
    Helper function used by the UHI calculation workflow.
    """
    if geometry is None or geometry.is_empty:
        return []
    if geometry.geom_type == "Polygon":
        return [geometry]
    if geometry.geom_type == "MultiPolygon":
        return [poly for poly in geometry.geoms if not poly.is_empty]
    polygons = []
    if hasattr(geometry, "geoms"):
        for geom in geometry.geoms:
            polygons.extend(_explodePolygons(geom))
    return polygons


def _polygonMeshes(geometry: shapely.Geometry, height: float) -> list[Mesh]:
    """
    Helper function used by the UHI calculation workflow.
    """
    meshes: list[Mesh] = []
    for polygon in _explodePolygons(geometry):
        coords = np.asarray(polygon.exterior.coords, dtype=float)
        for i in range(1, len(coords)):
            meshes.append(Mesh(shapely.LineString([coords[i - 1], coords[i]]), height))
        for interior in polygon.interiors:
            ring_coords = np.asarray(interior.coords, dtype=float)
            for i in range(1, len(ring_coords)):
                meshes.append(Mesh(shapely.LineString([ring_coords[i - 1], ring_coords[i]]), height))
    return meshes


def _intersectsSky(ray: Vector, mesh: Mesh, ray_length: float) -> bool:
    """
    Helper function used by the UHI calculation workflow.
    """
    horizontal = float(ray.direction[0] ** 2 + ray.direction[1] ** 2)
    if horizontal == 0.0:
        return False

    ray_end = ray.origin[:2] + ray.direction[:2] * ray_length
    sun_ray = shapely.LineString([ray.origin[:2], ray_end])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        intersection = shapely.intersection(mesh.edge, sun_ray)
    if intersection.is_empty:
        return False

    coords = shapely.get_coordinates(intersection)
    if len(coords) == 0:
        return False

    point = coords[0]
    dist2 = float(np.sum((ray.origin[:2] - point) ** 2))
    if dist2 == 0.0:
        return True

    return (mesh.height * mesh.height / dist2) > (ray.direction[2] ** 2 / horizontal)


def _cellSvf(x: float, y: float, meshes: list[Mesh], *, ray_length: float) -> float:
    """
    Helper function used by the UHI calculation workflow.
    """
    if not meshes:
        return 1.0

    origin = np.array([x, y, 0.0], dtype=float)
    valid = 0
    for direction in SKY_VECTORS:
        ray = Vector(origin=origin, direction=direction)
        blocked = False
        for mesh in meshes:
            if _intersectsSky(ray, mesh, ray_length):
                blocked = True
                break
        if not blocked:
            valid += 1
    return valid / max(len(SKY_VECTORS), 1)


def _cellSvfWithTree(x: float, y: float, edge_tree: STRtree, edge_heights: np.ndarray, *, ray_length: float) -> float:
    """
    Helper function used by the UHI calculation workflow.
    """
    origin = np.array([x, y, 0.0], dtype=float)
    valid = 0
    for direction in SKY_VECTORS:
        horizontal = float(direction[0] ** 2 + direction[1] ** 2)
        if horizontal == 0.0:
            valid += 1
            continue

        ray_end = origin[:2] + direction[:2] * ray_length
        sun_ray = shapely.LineString([origin[:2], ray_end])
        candidate_ids = edge_tree.query(sun_ray)
        blocked = False
        for idx in candidate_ids:
            mesh = Mesh(edge=edge_tree.geometries[idx], height=float(edge_heights[idx]))
            if _intersectsSky(Vector(origin=origin, direction=direction), mesh, ray_length):
                blocked = True
                break
        if not blocked:
            valid += 1
    return valid / max(len(SKY_VECTORS), 1)


def _averageWithBuffer(array: np.ndarray, resolution: float, buff: float) -> np.ndarray:
    """
    Helper function used by the UHI calculation workflow.
    """
    effective_buff = max(float(buff), float(resolution))
    radius_cells = int(math.ceil(effective_buff / resolution))
    offsets: list[tuple[int, int]] = []
    limit2 = effective_buff * effective_buff
    for drow in range(-radius_cells, radius_cells + 1):
        for dcol in range(-radius_cells, radius_cells + 1):
            if ((drow * resolution) ** 2 + (dcol * resolution) ** 2) <= limit2:
                offsets.append((drow, dcol))

    height, width = array.shape
    averaged = np.zeros_like(array, dtype=np.float32)
    total = max(height * width, 1)
    for row in range(height):
        for col in range(width):
            values = []
            for drow, dcol in offsets:
                rr = row + drow
                cc = col + dcol
                if 0 <= rr < height and 0 <= cc < width:
                    values.append(array[rr, cc])
            averaged[row, col] = float(np.mean(values)) if values else float(array[row, col])

            done = row * width + col + 1
            if done == 1 or done % 500 == 0 or done == total:
                print(f"Averaging cells: {done}/{total}")
    return averaged


def _bufferOffsets(resolution: float, buff: float) -> list[tuple[int, int]]:
    """
    Helper function used by the UHI calculation workflow.
    """
    effective_buff = max(float(buff), float(resolution))
    radius_cells = int(math.ceil(effective_buff / resolution))
    offsets: list[tuple[int, int]] = []
    limit2 = effective_buff * effective_buff
    for drow in range(-radius_cells, radius_cells + 1):
        for dcol in range(-radius_cells, radius_cells + 1):
            if ((drow * resolution) ** 2 + (dcol * resolution) ** 2) <= limit2:
                offsets.append((drow, dcol))
    return offsets


def _meanAtPoint(array: np.ndarray, point_x: float, point_y: float, xmin: float, ymin: float, resolution: float, buff: float) -> float:
    """
    Helper function used by the UHI calculation workflow.
    """
    height, width = array.shape
    col = int(math.floor((point_x - xmin) / resolution))
    row = int(math.floor((point_y - ymin) / resolution))
    row_src = height - 1 - row
    if not (0 <= col < width and 0 <= row_src < height):
        raise ValueError("Point falls outside the computed raster extent.")

    values = []
    for drow, dcol in _bufferOffsets(resolution, buff):
        rr = row_src - drow
        cc = col + dcol
        if 0 <= rr < height and 0 <= cc < width:
            values.append(array[rr, cc])
    if not values:
        return float(array[row_src, col])
    return float(np.mean(values))


def _initSvfWorker(
    x_centers: np.ndarray,
    height: int,
    svf_search_radius: float,
    edge_geometries: list[shapely.Geometry],
    edge_heights: np.ndarray,
) -> None:
    """
    Helper function used by the UHI calculation workflow.
    """
    global _SVF_WORKER_STATE
    _SVF_WORKER_STATE = {
        "x_centers": x_centers,
        "height": height,
        "svf_search_radius": svf_search_radius,
        "edge_tree": STRtree(edge_geometries),
        "edge_heights": edge_heights,
    }


def _computeSvfRow(task: tuple[int, float]) -> tuple[int, np.ndarray]:
    """
    Helper function used by the UHI calculation workflow.
    """
    row_idx, y = task
    x_centers: np.ndarray = _SVF_WORKER_STATE["x_centers"]  # type: ignore[assignment]
    height: int = int(_SVF_WORKER_STATE["height"])
    svf_search_radius: float = float(_SVF_WORKER_STATE["svf_search_radius"])
    edge_tree: STRtree = _SVF_WORKER_STATE["edge_tree"]  # type: ignore[assignment]
    edge_heights: np.ndarray = _SVF_WORKER_STATE["edge_heights"]  # type: ignore[assignment]

    row_values = np.ones(len(x_centers), dtype=np.float32)
    for col_idx, x in enumerate(x_centers):
        row_values[col_idx] = float(
            _cellSvfWithTree(x, y, edge_tree, edge_heights, ray_length=svf_search_radius)
        )
    return height - 1 - row_idx, row_values


def _prepareFeatures(shp: gpd.GeoDataFrame, height_column: str) -> gpd.GeoDataFrame:
    """
    Build normalized inputs for the model workflow.
    """
    if height_column not in shp.columns:
        raise KeyError(f"Height column '{height_column}' was not found in shapefile.")

    data = shp.copy()
    data = data.loc[~data.geometry.is_empty & data.geometry.notna()].copy()
    data[height_column] = pd.to_numeric(data[height_column], errors="coerce")
    data = data.loc[data[height_column].notna()].copy()
    data["centroid_x"] = data.geometry.centroid.x
    data["centroid_y"] = data.geometry.centroid.y
    data["footprint_area"] = data.geometry.area.astype(float)
    data["facade_area"] = data.geometry.length.astype(float) * np.minimum(data[height_column].astype(float), 18.0)
    data["far_area"] = data["footprint_area"] * data[height_column].astype(float)
    data["meshes"] = [
        _polygonMeshes(geometry, float(height))
        for geometry, height in zip(data.geometry, data[height_column])
    ]
    return data


def _bboxProjectedLength(x_span: float, y_span: float, wind_direction_deg: float) -> float:
    """
    Helper function used by the UHI calculation workflow.
    """
    theta = math.radians(float(wind_direction_deg))
    wind_vec_x = math.sin(theta)
    wind_vec_y = math.cos(theta)
    normal_x = -wind_vec_y
    normal_y = wind_vec_x
    return abs(x_span * normal_x) + abs(y_span * normal_y)


def _addVentilationBlockageTerms(data: gpd.GeoDataFrame, wind_direction_deg: float) -> gpd.GeoDataFrame:
    """
    Helper function used by the UHI calculation workflow.
    """
    bounds = data.geometry.bounds
    x_span = (bounds["maxx"] - bounds["minx"]).astype(float)
    y_span = (bounds["maxy"] - bounds["miny"]).astype(float)
    diagonal = np.hypot(x_span, y_span)
    projected = np.array(
        [_bboxProjectedLength(dx, dy, wind_direction_deg) for dx, dy in zip(x_span, y_span)],
        dtype=np.float32,
    )
    with np.errstate(divide="ignore", invalid="ignore"):
        f_yf = np.where(diagonal > 0.0, projected / diagonal, 0.0).astype(np.float32)
    enriched = data.copy()
    enriched["F_yf"] = f_yf
    enriched["kS_area"] = enriched["footprint_area"].astype(float) * enriched["F_yf"].astype(float)
    return enriched


def _buildGroupIndex(
    data: gpd.GeoDataFrame,
    xmin: float,
    ymin: float,
    resolution: float,
    width: int,
    height: int,
) -> dict[tuple[int, int], list[int]]:
    """
    Build normalized inputs for the model workflow.
    """
    groups: dict[tuple[int, int], list[int]] = {}
    for idx, row in enumerate(data.itertuples(index=False)):
        col = int(math.floor((float(row.centroid_x) - xmin) / resolution))
        row_idx = int(math.floor((float(row.centroid_y) - ymin) / resolution))
        if 0 <= col < width and 0 <= row_idx < height:
            groups.setdefault((col, row_idx), []).append(idx)
    return groups


def _cellFeatureIndices(
    groups: dict[tuple[int, int], list[int]],
    col: int,
    row: int,
    resolution: float,
) -> list[int]:
    """
    Helper function used by the UHI calculation workflow.
    """
    indices = list(groups.get((col, row), []))
    if resolution < 100.0:
        for dcol, drow in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            indices.extend(groups.get((col + dcol, row + drow), []))
    return indices


def _writeTif(path: Path, array: np.ndarray, transform, crs: CRS) -> None:
    """
    Write calculated model outputs to disk.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=array.shape[0],
        width=array.shape[1],
        count=1,
        dtype="float32",
        crs=crs,
        transform=transform,
        compress="lzw",
    ) as dst:
        dst.write(array.astype(np.float32), 1)


def _computeBaseGrids(
    prepared: gpd.GeoDataFrame,
    xmin: float,
    ymin: float,
    xmax: float,
    ymax: float,
    resolution: float,
    svf_search_radius: float,
    workers: int,
) -> tuple[dict[str, np.ndarray], dict[str, object]]:
    """
    Helper function used by the UHI calculation workflow.
    """
    width = max(1, int(math.ceil((xmax - xmin) / resolution)))
    height = max(1, int(math.ceil((ymax - ymin) / resolution)))
    x_centers = xmin + (np.arange(width, dtype=float) + 0.5) * resolution
    y_centers = ymin + (np.arange(height, dtype=float) + 0.5) * resolution

    groups = _buildGroupIndex(prepared, xmin, ymin, resolution, width, height)
    building_ratio = np.zeros((height, width), dtype=np.float32)
    facade_ratio = np.zeros((height, width), dtype=np.float32)
    far_ratio = np.zeros((height, width), dtype=np.float32)
    ks_ratio = np.zeros((height, width), dtype=np.float32)
    svf = np.ones((height, width), dtype=np.float32)
    cell_area = resolution * resolution
    edge_geometries = [mesh.edge for mesh_list in prepared["meshes"] for mesh in mesh_list]
    edge_heights = np.asarray([mesh.height for mesh_list in prepared["meshes"] for mesh in mesh_list], dtype=np.float32)

    total = max(width * height, 1)
    for row_idx, _y in enumerate(y_centers):
        for col_idx, _x in enumerate(x_centers):
            feature_indices = _cellFeatureIndices(groups, col_idx, row_idx, resolution)
            if feature_indices:
                region = prepared.iloc[feature_indices]
                building_ratio[height - 1 - row_idx, col_idx] = float(region["footprint_area"].sum() / cell_area)
                facade_ratio[height - 1 - row_idx, col_idx] = float(region["facade_area"].sum() / cell_area)
                far_ratio[height - 1 - row_idx, col_idx] = float(region["far_area"].sum() / cell_area)
                ks_ratio[height - 1 - row_idx, col_idx] = float(region["kS_area"].sum() / cell_area)

            done = row_idx * width + col_idx + 1
            if done == 1 or done % 200 == 0 or done == total:
                print(f"Processing non-SVF base cells: {done}/{total}")

    print(f"Computing SVF with {workers} worker(s)")
    tasks = [(row_idx, y) for row_idx, y in enumerate(y_centers)]
    if workers == 1:
        _initSvfWorker(x_centers, height, svf_search_radius, edge_geometries, edge_heights)
        for done, task in enumerate(tasks, start=1):
            target_row, row_values = _computeSvfRow(task)
            svf[target_row, :] = row_values
            if done == 1 or done % 10 == 0 or done == len(tasks):
                print(f"Computing SVF rows: {done}/{len(tasks)}")
    else:
        with get_context("spawn").Pool(
            processes=workers,
            initializer=_initSvfWorker,
            initargs=(x_centers, height, svf_search_radius, edge_geometries, edge_heights),
        ) as pool:
            for done, (target_row, row_values) in enumerate(pool.imap_unordered(_computeSvfRow, tasks), start=1):
                svf[target_row, :] = row_values
                if done == 1 or done % 10 == 0 or done == len(tasks):
                    print(f"Computing SVF rows: {done}/{len(tasks)}")

    return (
        {
            "BuildingAreaRatio": building_ratio,
            "facaAreaRatio": facade_ratio,
            "FAR": far_ratio,
            "kS": ks_ratio,
            "SVF": svf,
        },
        {
            "width": width,
            "height": height,
            "x_centers": x_centers,
            "y_centers": y_centers,
        },
    )


def processBuildingShp(
    shp_path: str | Path,
    output_dir: str | Path,
    resolution: float,
    *,
    height_column: str = "height",
    bounds: tuple[float, float, float, float] | None = None,
    buff: float = DEFAULT_BUFF_M,
    svf_search_radius: float = DEFAULT_SVF_SEARCH_RADIUS_M,
    workers: int = DEFAULT_WORKERS,
    wind_direction: float = DEFAULT_WIND_DIRECTION_DEG,
) -> dict[str, object]:
    """
    Process source data into model-ready outputs.
    """
    shp_path = Path(shp_path)
    output_dir = Path(output_dir)
    if resolution <= 0:
        raise ValueError("Resolution must be > 0.")
    if buff <= 0:
        raise ValueError("Buff must be > 0.")
    if svf_search_radius <= 0:
        raise ValueError("svf_search_radius must be > 0.")
    if workers <= 0:
        raise ValueError("workers must be > 0.")

    shp = gpd.read_file(shp_path)
    shp_utm, (xmin, ymin, xmax, ymax), target_crs, projection_meta = _toTargetUtm(shp, bounds)
    prepared = _addVentilationBlockageTerms(_prepareFeatures(shp_utm, height_column), wind_direction)
    effective_buff = max(float(buff), float(resolution))
    base_grids, grid_meta = _computeBaseGrids(prepared, xmin, ymin, xmax, ymax, resolution, svf_search_radius, workers)
    width = int(grid_meta["width"])
    height = int(grid_meta["height"])

    print("Averaging BuildingAreaRatio")
    building_ratio_avg = _averageWithBuffer(base_grids["BuildingAreaRatio"], resolution, effective_buff)
    print("Averaging facaAreaRatio")
    facade_ratio_avg = _averageWithBuffer(base_grids["facaAreaRatio"], resolution, effective_buff)
    print("Averaging FAR")
    far_ratio_avg = _averageWithBuffer(base_grids["FAR"], resolution, effective_buff)
    print("Averaging kS")
    ks_ratio_avg = _averageWithBuffer(base_grids["kS"], resolution, effective_buff)
    print("Averaging SVF")
    svf_avg = _averageWithBuffer(base_grids["SVF"], resolution, effective_buff)

    transform = from_origin(xmin, ymin + height * resolution, resolution, resolution)
    outputs = {
        "SVF.tif": svf_avg,
        "facaAreaRatio.tif": facade_ratio_avg,
        "BuildingAreaRatio.tif": building_ratio_avg,
        "FAR.tif": far_ratio_avg,
        "kS.tif": ks_ratio_avg,
    }
    for name, array in outputs.items():
        _writeTif(output_dir / name, array, transform, target_crs)

    manifest = {
        "input": str(shp_path),
        "output_dir": str(output_dir),
        "height_column": height_column,
        "resolution_m": resolution,
        "buff_m_input": float(buff),
        "buff_m_effective": effective_buff,
        "svf_search_radius_m": float(svf_search_radius),
        "workers": int(workers),
        "wind_direction_deg_from_north_clockwise": float(wind_direction),
        "grid": {"width": width, "height": height},
        "bounds_utm": {"xmin": xmin, "ymin": ymin, "xmax": xmax, "ymax": ymax},
        **projection_meta,
        "outputs": list(outputs.keys()),
        "metrics": {
            "SVF": "Step 1: compute point SVF with buildings within svf_search_radius_m using the original ray-blocking logic. Step 2: average by buff_m_effective.",
            "facaAreaRatio": "Step 1: sum(geometry.length * min(height, 18m)) / cell_area. Step 2: average by buff_m_effective.",
            "BuildingAreaRatio": "Step 1: sum(geometry.area) / cell_area. Step 2: average by buff_m_effective.",
            "FAR": "Step 1: sum(geometry.area * height) / cell_area. Step 2: average by buff_m_effective.",
            "kS": "Step 1: sum(geometry.area * F_yf) / cell_area, where F_yf = bbox projected frontal length / bbox diagonal length under wind_direction_deg_from_north_clockwise. Step 2: average by buff_m_effective.",
        },
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def queryPointMetrics(
    shp_path: str | Path,
    lat: float,
    lon: float,
    resolution: float,
    *,
    height_column: str = "height",
    buff: float = DEFAULT_BUFF_M,
    svf_search_radius: float = DEFAULT_SVF_SEARCH_RADIUS_M,
    workers: int = DEFAULT_WORKERS,
    wind_direction: float = DEFAULT_WIND_DIRECTION_DEG,
) -> dict[str, float | str]:
    """
    Query calculated preprocessing metrics at a target location.
    """
    if resolution <= 0:
        raise ValueError("Resolution must be > 0.")
    if buff <= 0:
        raise ValueError("Buff must be > 0.")

    shp = gpd.read_file(shp_path)
    target_crs = _resolveTargetCrsForPoint(shp, lat, lon)
    shp_utm = shp.to_crs(target_crs) if CRS.from_user_input(shp.crs) != target_crs else shp.copy()
    prepared = _addVentilationBlockageTerms(_prepareFeatures(shp_utm, height_column), wind_direction)

    effective_buff = max(float(buff), float(resolution))
    point_x, point_y = _pointToTargetUtm(lat, lon, target_crs)
    xmin = point_x - effective_buff
    xmax = point_x + effective_buff
    ymin = point_y - effective_buff
    ymax = point_y + effective_buff

    base_grids, _grid_meta = _computeBaseGrids(prepared, xmin, ymin, xmax, ymax, resolution, svf_search_radius, workers)
    point_lat, point_lon = _pointToWgs84(lat, lon, target_crs)

    result: dict[str, float | str] = {
        "lat": point_lat,
        "lon": point_lon,
        "resolution_m": float(resolution),
        "buff_m_input": float(buff),
        "buff_m_effective": float(effective_buff),
        "svf_search_radius_m": float(svf_search_radius),
        "wind_direction_deg_from_north_clockwise": float(wind_direction),
        "target_crs": target_crs.to_string(),
        "SVF": _meanAtPoint(base_grids["SVF"], point_x, point_y, xmin, ymin, resolution, effective_buff),
        "facaAreaRatio": _meanAtPoint(base_grids["facaAreaRatio"], point_x, point_y, xmin, ymin, resolution, effective_buff),
        "BuildingAreaRatio": _meanAtPoint(base_grids["BuildingAreaRatio"], point_x, point_y, xmin, ymin, resolution, effective_buff),
        "FAR": _meanAtPoint(base_grids["FAR"], point_x, point_y, xmin, ymin, resolution, effective_buff),
        "kS": _meanAtPoint(base_grids["kS"], point_x, point_y, xmin, ymin, resolution, effective_buff),
    }
    return result


def main() -> None:
    """
    Run the command-line entrypoint.
    """
    parser = argparse.ArgumentParser(description="Generate SVF / facaAreaRatio / BuildingAreaRatio / FAR tif files from a building footprint shapefile.")
    parser.add_argument("--input", required=True, help="Input building shapefile path.")
    parser.add_argument("--output-dir", help="Directory for output tif files. Required for area mode.")
    parser.add_argument("--resolution", required=True, type=float, help="Output tif resolution in meters.")
    parser.add_argument("--height-column", default="height", help="Height column name. Default: height.")
    parser.add_argument("--lat", type=float, help="Point latitude or projected Y coordinate for point mode.")
    parser.add_argument("--lon", type=float, help="Point longitude or projected X coordinate for point mode.")
    parser.add_argument("--xmin", type=float, help="Optional xmin. Must be provided together with xmax/ymin/ymax.")
    parser.add_argument("--xmax", type=float, help="Optional xmax. Must be provided together with xmin/ymin/ymax.")
    parser.add_argument("--ymin", type=float, help="Optional ymin. Must be provided together with xmin/xmax/ymax.")
    parser.add_argument("--ymax", type=float, help="Optional ymax. Must be provided together with xmin/xmax/ymin.")
    parser.add_argument("--buff", type=float, default=DEFAULT_BUFF_M, help="Buffer radius in meters for step-2 averaging. Default: 500.")
    parser.add_argument(
        "--svf-search-radius",
        type=float,
        default=DEFAULT_SVF_SEARCH_RADIUS_M,
        help="Building search radius in meters for step-1 SVF obstruction calculation. Default: 200.",
    )
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help=f"Worker count for SVF calculation. Default: {DEFAULT_WORKERS}.")
    parser.add_argument(
        "--wind-direction",
        type=float,
        default=DEFAULT_WIND_DIRECTION_DEG,
        help="Dominant wind direction in degrees, using north=0 and clockwise positive. Default: 0.",
    )
    args = parser.parse_args()

    bounds_values = [args.xmin, args.xmax, args.ymin, args.ymax]
    if any(value is not None for value in bounds_values) and not all(value is not None for value in bounds_values):
        raise ValueError("xmin, xmax, ymin, ymax must all be provided together.")
    bounds = tuple(bounds_values) if all(value is not None for value in bounds_values) else None
    point_mode = args.lat is not None or args.lon is not None
    if point_mode and not (args.lat is not None and args.lon is not None):
        raise ValueError("lat and lon must be provided together in point mode.")

    if point_mode:
        result = queryPointMetrics(
            shp_path=args.input,
            lat=args.lat,
            lon=args.lon,
            resolution=args.resolution,
            height_column=args.height_column,
            buff=args.buff,
            svf_search_radius=args.svf_search_radius,
            workers=args.workers,
            wind_direction=args.wind_direction,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if not args.output_dir:
        raise ValueError("output-dir is required in area mode.")

    manifest = processBuildingShp(
        shp_path=args.input,
        output_dir=args.output_dir,
        resolution=args.resolution,
        height_column=args.height_column,
        bounds=bounds,
        buff=args.buff,
        svf_search_radius=args.svf_search_radius,
        workers=args.workers,
        wind_direction=args.wind_direction,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))



# Backward-compatible aliases for older imports.
process_building_shp = processBuildingShp
query_point_metrics = queryPointMetrics

if __name__ == "__main__":
    main()
