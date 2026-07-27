"""End-to-end RAD weather matrix, ray radiation and schedule workflow."""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
from shapely.geometry import box

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from RAD.rad_engine import batch_ray_visibility, ensure_local_rad_binary
from RAD.schedule_writer import write_schedule_file
from WEA import build_weather_matrix
from RAD.solar_geometry import (
    bbox_midpoint_sample_points,
    candidate_neighbors,
    facade_sample_heights,
    load_building_meshes,
    projected_weight,
)
from RAD.weather_epw import day_hour_indices, load_sun_vectors


MODULE_DIR = Path(__file__).resolve().parent
REPO_ROOT = MODULE_DIR.parent
OUTPUT_ROOT = REPO_ROOT / "outputs"
WEATHER_OUTPUT_DIR = OUTPUT_ROOT / "weather"
RADIATION_OUTPUT_DIR = OUTPUT_ROOT / "radiation"
CARDINAL_KEYS = ("N", "S", "W", "E", "T")
CARDINAL_NORMALS = {
    "N": np.array([0.0, 1.0, 0.0], dtype=float),
    "S": np.array([0.0, -1.0, 0.0], dtype=float),
    "W": np.array([-1.0, 0.0, 0.0], dtype=float),
    "E": np.array([1.0, 0.0, 0.0], dtype=float),
}
ROOF_NORMAL = np.array([0.0, 0.0, 1.0], dtype=float)
IDW_DISTANCE_EPSILON = 1e-6


def _shp_stem(shp_path: Path) -> str:
    """Return a stable stem for shapefile-derived outputs."""
    return shp_path.stem


def _epw_stem(epw_path: Path) -> str:
    """Return a stable stem for EPW-derived outputs."""
    return epw_path.stem


def _normalize_hoy_window(start_hoy: int | None, end_hoy: int | None) -> tuple[int, int]:
    """Validate and normalize a half-open HOY window."""
    actual_start = 0 if start_hoy is None else int(start_hoy)
    actual_end = 8760 if end_hoy is None else int(end_hoy)
    if actual_start < 0 or actual_end < 0 or actual_start >= actual_end:
        raise ValueError(f"Invalid HOY window: start_hoy={start_hoy}, end_hoy={end_hoy}")
    if actual_end > 8760:
        raise ValueError(f"end_hoy {actual_end} exceeds available range 0..8760")
    return actual_start, actual_end


def _normalize_wwr_values(wwr: float | list[float] | tuple[float, ...] | np.ndarray, building_count: int) -> np.ndarray:
    """Broadcast or validate per-building WWR values."""
    if np.isscalar(wwr):
        wwr_values = np.full(building_count, float(wwr), dtype=float)
    else:
        wwr_values = np.asarray(wwr, dtype=float)
        if wwr_values.shape != (building_count,):
            raise ValueError(f"Expected {building_count} WWR values, got shape {wwr_values.shape}")
    if np.any(wwr_values < 0.0) or np.any(wwr_values > 1.0):
        raise ValueError("All WWR values must be between 0 and 1.")
    return wwr_values


def _normalize_floor_height_values(
    floor_height: float | list[float] | tuple[float, ...] | np.ndarray,
    building_count: int,
) -> np.ndarray:
    """Broadcast or validate per-building floor-height values."""
    if np.isscalar(floor_height):
        floor_height_values = np.full(building_count, float(floor_height), dtype=float)
    else:
        floor_height_values = np.asarray(floor_height, dtype=float)
        if floor_height_values.shape != (building_count,):
            raise ValueError(f"Expected {building_count} floor-height values, got shape {floor_height_values.shape}")
    if np.any(floor_height_values <= 0.0):
        raise ValueError("All floor-height values must be positive.")
    return floor_height_values


def _normalize_sample(sample: float) -> float:
    """Validate the exact-radiation sampling ratio."""
    sample_value = float(sample)
    if sample_value <= 0.0 or sample_value > 1.0:
        raise ValueError("sample must be in the interval (0, 1].")
    return sample_value


def _sample_indices(building_count: int, sample: float, seed: int) -> np.ndarray:
    """Select sampled buildings for precise MoosasRad calculation."""
    if building_count <= 0:
        return np.zeros(0, dtype=int)
    sample_count = max(1, int(math.ceil(building_count * sample)))
    if sample_count >= building_count:
        return np.arange(building_count, dtype=int)
    rng = np.random.default_rng(int(seed))
    return np.sort(rng.choice(building_count, size=sample_count, replace=False).astype(int))


def prepare_buildings(
    shp_path: Path,
    id_field: str,
    height_field: str,
    bounds: tuple[float, float, float, float] | None = None,
) -> gpd.GeoDataFrame:
    """Load and clean building footprints before RAD processing."""
    gdf = gpd.read_file(shp_path)
    if height_field not in gdf.columns:
        raise KeyError(f"Required height field '{height_field}' is missing in {shp_path}")

    gdf = gdf[gdf.geometry.notnull()].copy()
    gdf[height_field] = gdf[height_field].astype(float)
    gdf = gdf[gdf[height_field] > 0].copy()

    if id_field not in gdf.columns:
        gdf[id_field] = gdf.index.astype(str)
    else:
        gdf[id_field] = gdf[id_field].astype(str)

    if bounds is not None:
        xmin, ymin, xmax, ymax = bounds
        gdf = gdf[gdf.geometry.intersects(box(xmin, ymin, xmax, ymax))].copy()
    return gdf


def _load_hourly_patch_matrix(mtx_path: str | Path) -> np.ndarray:
    """Load the yearly sky patch matrix generated from EPW."""
    mtx_path = Path(mtx_path).resolve()
    matrix = np.load(mtx_path)
    if matrix.shape != (145, 8760):
        raise ValueError(f"Unexpected sky matrix shape: {matrix.shape}")
    return matrix


def _orientation_key(normal: np.ndarray) -> str:
    """Map a facade normal to the closest cardinal orientation key."""
    best_key = "N"
    best_dot = -np.inf
    for key, target in CARDINAL_NORMALS.items():
        dot = float(np.dot(normal, target))
        if dot > best_dot:
            best_dot = dot
            best_key = key
    return best_key


def _facade_area_by_orientation(mesh) -> dict[str, float]:
    """Aggregate facade area by cardinal orientation."""
    area_map = {key: 0.0 for key in CARDINAL_NORMALS}
    for edge_index, normal_2d in enumerate(mesh.facade_normals):
        normal = np.array([normal_2d[0], normal_2d[1], 0.0], dtype=float)
        key = _orientation_key(normal)
        area_map[key] += float(mesh.edge_lengths[edge_index] * mesh.heights[edge_index])
    return area_map


def _build_facade_samples(mesh, floor_height: float, offset: float = 0.1) -> dict[str, list[dict[str, np.ndarray | float]]]:
    """Build facade sample points grouped by orientation."""
    heights = facade_sample_heights(float(np.max(mesh.heights)), floor_height)
    samples_by_key: dict[str, list[dict[str, np.ndarray | float]]] = {key: [] for key in CARDINAL_NORMALS}
    for horizontal_sample in bbox_midpoint_sample_points(mesh):
        normal = np.array(horizontal_sample["normal"], dtype=float)
        key = _orientation_key(normal)
        point_xy = np.array(horizontal_sample["point_xy"], dtype=float)
        shifted_xy = point_xy + normal[:2] * offset
        for z in heights:
            samples_by_key[key].append(
                {
                    "point": np.array([shifted_xy[0], shifted_xy[1], z], dtype=float),
                    "normal": normal,
                    "weight": 1.0,
                }
            )
    return samples_by_key


def _candidate_scene_meshes(mesh, meshes, samples: list[dict[str, np.ndarray | float]]) -> list:
    """Collect the target mesh and nearby blockers for one ray batch."""
    neighbor_map: dict[str, object] = {mesh.building_id: mesh}
    for sample in samples:
        point = np.array(sample["point"], dtype=float)
        for neighbor in candidate_neighbors(mesh, meshes, point[:2], float(point[2])):
            neighbor_map[neighbor.building_id] = neighbor
    return list(neighbor_map.values())


def _compute_building_radiation(mesh, meshes, hourly_patch_kwh_m2: np.ndarray, sky_vectors: np.ndarray, floor_height: float) -> dict[str, np.ndarray]:
    """Compute yearly directional radiation for one building with one MoosasRad call."""
    area_by_orientation = _facade_area_by_orientation(mesh)
    samples_by_orientation = _build_facade_samples(mesh, floor_height=floor_height)
    roof_height = float(np.max(mesh.heights))
    roof_samples = [
        {
            "point": np.array([mesh.centroid_xy[0], mesh.centroid_xy[1], roof_height + 0.1], dtype=float),
            "normal": ROOF_NORMAL,
            "weight": 1.0,
        }
    ]
    surface_specs = [
        {
            "key": key,
            "samples": samples_by_orientation[key],
            "surface_normal": CARDINAL_NORMALS[key],
            "area_m2": area_by_orientation[key],
        }
        for key in ("N", "S", "W", "E")
    ]
    surface_specs.append(
        {
            "key": "T",
            "samples": roof_samples,
            "surface_normal": ROOF_NORMAL,
            "area_m2": float(mesh.area_m2),
        }
    )

    all_samples: list[dict[str, np.ndarray | float]] = []
    sample_counts: list[int] = []
    for spec in surface_specs:
        sample_counts.append(len(spec["samples"]))
        all_samples.extend(spec["samples"])

    result = {key: np.zeros(hourly_patch_kwh_m2.shape[1], dtype=float) for key in CARDINAL_KEYS}
    if not all_samples:
        return result

    scene_meshes = _candidate_scene_meshes(mesh, meshes, all_samples)
    origins = np.repeat(np.vstack([np.array(sample["point"], dtype=float) for sample in all_samples]), len(sky_vectors), axis=0)
    directions = np.tile(sky_vectors, (len(all_samples), 1))
    visible = batch_ray_visibility(scene_meshes, origins, directions).reshape((len(all_samples), len(sky_vectors))).astype(float)

    sample_offset = 0
    for spec, sample_count in zip(surface_specs, sample_counts):
        if sample_count == 0 or float(spec["area_m2"]) <= 0.0:
            sample_offset += sample_count
            continue

        samples = spec["samples"]
        surface_visibility = visible[sample_offset : sample_offset + sample_count]
        surface_weights = np.array(
            [projected_weight(vector, unit=np.array(spec["surface_normal"], dtype=float)) for vector in sky_vectors],
            dtype=float,
        )
        sample_hourly_kwh_m2: list[np.ndarray] = []
        sample_weights: list[float] = []
        for sample_index, sample in enumerate(samples):
            weighted_visibility = surface_visibility[sample_index] * surface_weights
            sample_hourly_kwh_m2.append(np.sum(hourly_patch_kwh_m2 * weighted_visibility.reshape((-1, 1)), axis=0))
            sample_weights.append(float(sample["weight"]))

        mean_hourly_kwh_m2 = np.average(
            np.vstack(sample_hourly_kwh_m2),
            axis=0,
            weights=np.array(sample_weights, dtype=float),
        )
        result[str(spec["key"])] = np.maximum(0.0, mean_hourly_kwh_m2 * float(spec["area_m2"]) * 1000.0)
        sample_offset += sample_count
    return result


def _initialize_result_arrays(building_count: int, hour_count: int) -> dict[str, np.ndarray]:
    """Allocate output arrays for all radiation directions."""
    return {key: np.zeros((building_count, hour_count), dtype=float) for key in CARDINAL_KEYS}


def _fill_precise_samples(
    result_arrays: dict[str, np.ndarray],
    sample_indices: np.ndarray,
    meshes,
    hourly_patch_kwh_m2: np.ndarray,
    sky_vectors: np.ndarray,
    floor_height: float,
) -> None:
    """Fill exact radiation results for sampled buildings."""
    for building_index in sample_indices.tolist():
        building_result = _compute_building_radiation(
            meshes[building_index],
            meshes,
            hourly_patch_kwh_m2=hourly_patch_kwh_m2,
            sky_vectors=sky_vectors,
            floor_height=floor_height,
        )
        for key in CARDINAL_KEYS:
            result_arrays[key][building_index] = building_result[key]


def _idw_interpolate_buildings(
    result_arrays: dict[str, np.ndarray],
    meshes,
    sample_indices: np.ndarray,
    *,
    distance_epsilon: float = IDW_DISTANCE_EPSILON,
) -> None:
    """Interpolate unsampled buildings from sampled neighbors by inverse distance."""
    if len(meshes) == 0 or len(sample_indices) == 0 or len(sample_indices) == len(meshes):
        return

    sample_mask = np.zeros(len(meshes), dtype=bool)
    sample_mask[sample_indices] = True
    target_indices = np.flatnonzero(~sample_mask)
    sample_centroids = np.vstack([np.asarray(meshes[index].centroid_xy, dtype=float) for index in sample_indices])

    for target_index in target_indices.tolist():
        target_centroid = np.asarray(meshes[target_index].centroid_xy, dtype=float)
        deltas = sample_centroids - target_centroid
        distance_squared = np.sum(deltas * deltas, axis=1)
        nearest_index = int(np.argmin(distance_squared))
        if float(distance_squared[nearest_index]) <= float(distance_epsilon):
            source_index = int(sample_indices[nearest_index])
            for key in CARDINAL_KEYS:
                result_arrays[key][target_index] = result_arrays[key][source_index]
            continue

        weights = 1.0 / np.maximum(distance_squared, float(distance_epsilon))
        weights = weights / np.sum(weights)
        for key in CARDINAL_KEYS:
            sample_values = result_arrays[key][sample_indices]
            result_arrays[key][target_index] = np.tensordot(weights, sample_values, axes=(0, 0))


def compute_building_radiation(
    shp_path: str | Path,
    mtx_path: str | Path,
    *,
    id_field: str = "index",
    height_field: str = "height",
    floor_height: float = 4.0,
    sample: float = 0.5,
    seed: int = 42,
) -> Path:
    """Compute and save yearly per-building directional radiation arrays."""
    ensure_local_rad_binary()
    if floor_height <= 0:
        raise ValueError("floor_height must be positive.")
    sample = _normalize_sample(sample)

    shp_path = Path(shp_path).resolve()
    sky_vectors = load_sun_vectors()
    hourly_patch_kwh_m2 = _load_hourly_patch_matrix(mtx_path)
    gdf = prepare_buildings(shp_path, id_field=id_field, height_field=height_field)
    meshes = load_building_meshes(gdf, id_field, height_field)
    sample_indices = _sample_indices(len(meshes), sample, seed)

    result_arrays = _initialize_result_arrays(len(meshes), hourly_patch_kwh_m2.shape[1])
    _fill_precise_samples(
        result_arrays,
        sample_indices,
        meshes,
        hourly_patch_kwh_m2=hourly_patch_kwh_m2,
        sky_vectors=sky_vectors,
        floor_height=floor_height,
    )
    _idw_interpolate_buildings(result_arrays, meshes, sample_indices)

    result_arrays["index"] = gdf[id_field].to_numpy(dtype=str)
    result_arrays["shp_index"] = gdf.index.to_numpy(dtype=int)
    RADIATION_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = RADIATION_OUTPUT_DIR / f"{_shp_stem(shp_path)}_rad.npz"
    np.savez_compressed(output_path, **result_arrays)
    return output_path


def write_radiation_schedules(
    shp_path: str | Path,
    rad_path: str | Path,
    *,
    date: str = "08-20",
    start_hoy: int | None = None,
    end_hoy: int | None = None,
    wwr: float | list[float] | tuple[float, ...] | np.ndarray = 0.5,
    floor_height: float | list[float] | tuple[float, ...] | np.ndarray = 4.0,
    shgc: float = 1.0,
    id_field: str = "index",
    height_field: str = "height",
) -> Path:
    """Write Moosas solar schedules from yearly directional radiation results."""
    if not (0.0 <= shgc <= 1.0):
        raise ValueError("shgc must be between 0 and 1.")

    shp_path = Path(shp_path).resolve()
    rad_path = Path(rad_path).resolve()
    gdf = prepare_buildings(shp_path, id_field=id_field, height_field=height_field)
    if start_hoy is None and end_hoy is None:
        hourly_indices = list(range(8760))
    else:
        if start_hoy is None or end_hoy is None:
            raise ValueError("start_hoy and end_hoy must be provided together.")
        actual_start, actual_end = _normalize_hoy_window(start_hoy, end_hoy)
        hourly_indices = list(range(actual_start, actual_end))
    rad_data = np.load(rad_path, allow_pickle=False)
    wwr_values = _normalize_wwr_values(wwr, len(gdf))
    floor_height_values = _normalize_floor_height_values(floor_height, len(gdf))

    missing = [key for key in CARDINAL_KEYS if key not in rad_data.files]
    if missing:
        raise KeyError(f"Missing radiation keys in {rad_path}: {missing}")

    for key in CARDINAL_KEYS:
        if rad_data[key].shape != (len(gdf), 8760):
            raise ValueError(f"Unexpected array shape for '{key}': {rad_data[key].shape}")

    output_dir = RADIATION_OUTPUT_DIR / _shp_stem(shp_path)
    output_dir.mkdir(parents=True, exist_ok=True)

    summed_facade_wh = (
        rad_data["N"][:, hourly_indices]
        + rad_data["S"][:, hourly_indices]
        + rad_data["W"][:, hourly_indices]
        + rad_data["E"][:, hourly_indices]
    )
    perimeters = gdf.geometry.length.to_numpy(dtype=float).reshape((-1, 1))
    heights = gdf[height_field].to_numpy(dtype=float).reshape((-1, 1))
    facade_area = np.maximum(perimeters * heights, 1e-6)
    facade_intensity_wh_m2 = summed_facade_wh / facade_area
    standard_floor_window_area = perimeters * floor_height_values.reshape((-1, 1)) * wwr_values.reshape((-1, 1))
    schedule_values = facade_intensity_wh_m2 * standard_floor_window_area * float(shgc)

    for building_index, building_id in enumerate(gdf[id_field].astype(str).tolist()):
        write_schedule_file(output_dir, building_id, schedule_values[building_index].tolist())
    return output_dir


def run_full_workflow(
    epw_path: str | Path,
    shp_path: str | Path,
    *,
    date: str = "08-20",
    wwr: float = 0.5,
    shgc: float = 0.6,
    id_field: str = "index",
    height_field: str = "height",
    floor_height: float = 4.0,
    sample: float = 0.5,
    seed: int = 42,
) -> tuple[Path, Path, Path]:
    """Run EPW-to-matrix, radiation, and schedule writing in sequence."""
    weather_path = build_weather_matrix(epw_path)
    rad_path = compute_building_radiation(
        shp_path,
        weather_path,
        id_field=id_field,
        height_field=height_field,
        floor_height=floor_height,
        sample=sample,
        seed=seed,
    )
    schedule_dir = write_radiation_schedules(
        shp_path,
        rad_path,
        date=date,
        wwr=wwr,
        shgc=shgc,
        id_field=id_field,
        height_field=height_field,
    )
    return weather_path, rad_path, schedule_dir


def _build_parser() -> argparse.ArgumentParser:
    """Build the RAD command line parser."""
    parser = argparse.ArgumentParser(description="RAD workflow utilities.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    weather_parser = subparsers.add_parser("build-weather", help="Build corrected hourly sky matrix from EPW.")
    weather_parser.add_argument("--epw", required=True, help="Input EPW weather path.")

    radiation_parser = subparsers.add_parser("compute-radiation", help="Build yearly per-building directional radiation.")
    radiation_parser.add_argument("--shp", required=True, help="Input building shapefile path.")
    radiation_parser.add_argument("--mtx", required=True, help="Input weather matrix .npy path.")
    radiation_parser.add_argument("--id-field", default="index", help="Building id column name.")
    radiation_parser.add_argument("--height-field", default="height", help="Height column name.")
    radiation_parser.add_argument("--floor-height", type=float, default=4.0, help="Typical floor height in meters.")
    radiation_parser.add_argument("--sample", type=float, default=0.5, help="Share of buildings to calculate exactly.")
    radiation_parser.add_argument("--seed", type=int, default=42, help="Random seed for building sampling.")

    schedule_parser = subparsers.add_parser("write-schedules", help="Write schedule files from yearly radiation npz.")
    schedule_parser.add_argument("--shp", required=True, help="Input building shapefile path.")
    schedule_parser.add_argument("--rad", required=True, help="Input directional radiation .npz path.")
    schedule_parser.add_argument("--date", default="08-20", help="Schedule source date in mm-dd.")
    schedule_parser.add_argument("--wwr", type=float, default=0.5, help="Window-to-wall ratio.")
    schedule_parser.add_argument("--shgc", type=float, default=0.6, help="Solar heat gain coefficient.")
    schedule_parser.add_argument("--id-field", default="index", help="Building id column name.")
    schedule_parser.add_argument("--height-field", default="height", help="Height column name.")

    all_parser = subparsers.add_parser("run-all", help="Run the full EPW -> NPZ -> SCH workflow.")
    all_parser.add_argument("--epw", required=True, help="Input EPW weather path.")
    all_parser.add_argument("--shp", required=True, help="Input building shapefile path.")
    all_parser.add_argument("--date", default="08-20", help="Schedule source date in mm-dd.")
    all_parser.add_argument("--wwr", type=float, default=0.5, help="Window-to-wall ratio.")
    all_parser.add_argument("--shgc", type=float, default=0.6, help="Solar heat gain coefficient.")
    all_parser.add_argument("--id-field", default="index", help="Building id column name.")
    all_parser.add_argument("--height-field", default="height", help="Height column name.")
    all_parser.add_argument("--floor-height", type=float, default=4.0, help="Typical floor height in meters.")
    all_parser.add_argument("--sample", type=float, default=0.5, help="Share of buildings to calculate exactly.")
    all_parser.add_argument("--seed", type=int, default=42, help="Random seed for building sampling.")
    return parser


def main() -> None:
    """Command line entry for RAD workflows."""
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "build-weather":
        print(build_weather_matrix(args.epw))
    elif args.command == "compute-radiation":
        print(
            compute_building_radiation(
                args.shp,
                args.mtx,
                id_field=args.id_field,
                height_field=args.height_field,
                floor_height=args.floor_height,
                sample=args.sample,
                seed=args.seed,
            )
        )
    elif args.command == "write-schedules":
        print(
            write_radiation_schedules(
                args.shp,
                args.rad,
                date=args.date,
                wwr=args.wwr,
                shgc=args.shgc,
                id_field=args.id_field,
                height_field=args.height_field,
            )
        )
    elif args.command == "run-all":
        weather_path, rad_path, schedule_dir = run_full_workflow(
            args.epw,
            args.shp,
            date=args.date,
            wwr=args.wwr,
            shgc=args.shgc,
            id_field=args.id_field,
            height_field=args.height_field,
            floor_height=args.floor_height,
            sample=args.sample,
            seed=args.seed,
        )
        print(weather_path)
        print(rad_path)
        print(schedule_dir)


prepareBuildings = prepare_buildings
computeBuildingRadiation = compute_building_radiation
writeRadiationSchedules = write_radiation_schedules
runFullWorkflow = run_full_workflow


if __name__ == "__main__":
    main()
