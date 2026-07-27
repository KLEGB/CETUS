from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import rasterio
from pyproj import CRS
from rasterio.enums import Resampling
from rasterio.windows import from_bounds
from rasterio.warp import calculate_default_transform, reproject
from scipy import ndimage
from scipy.signal import fftconvolve


def _clean_array(arr: np.ndarray, nodata: float | None) -> np.ndarray:
    data = np.asarray(arr, dtype=np.float32)
    if nodata is not None:
        data = np.where(data == nodata, 0.0, data)
    data = np.nan_to_num(data, nan=0.0, posinf=0.0, neginf=0.0)
    data = np.where(data > 0.0, data, 0.0)
    return data.astype(np.float32, copy=False)


def _utm_epsg_for_lonlat(lon: float, lat: float) -> int:
    zone = int(math.floor((lon + 180.0) / 6.0) + 1)
    zone = max(1, min(zone, 60))
    return 32600 + zone if lat >= 0 else 32700 + zone


def _reproject_if_needed(
    data: np.ndarray,
    transform,
    crs: CRS | None,
    *,
    src_nodata: float | None,
) -> tuple[np.ndarray, object, CRS]:
    if crs is None:
        # Some local preprocessing outputs (for example INX-derived rasters) are
        # already in a meter-based local grid but omit CRS metadata.
        return data, transform, CRS.from_epsg(3857)

    src_crs = CRS.from_user_input(crs)
    if src_crs.is_projected:
        return data, transform, src_crs

    bounds = rasterio.transform.array_bounds(data.shape[0], data.shape[1], transform)
    center_lon = (bounds[0] + bounds[2]) * 0.5
    center_lat = (bounds[1] + bounds[3]) * 0.5
    dst_crs = CRS.from_epsg(_utm_epsg_for_lonlat(center_lon, center_lat))

    pixel_x_deg = abs(float(transform.a))
    pixel_y_deg = abs(float(transform.e))
    meters_per_degree_lat = 111320.0
    meters_per_degree_lon = meters_per_degree_lat * max(math.cos(math.radians(center_lat)), 1e-6)
    target_resolution = max(
        pixel_x_deg * meters_per_degree_lon,
        pixel_y_deg * meters_per_degree_lat,
    )

    dst_transform, width, height = calculate_default_transform(
        src_crs,
        dst_crs,
        data.shape[1],
        data.shape[0],
        *bounds,
        resolution=target_resolution,
    )
    dst = np.zeros((height, width), dtype=np.float32)
    reproject(
        source=data,
        destination=dst,
        src_transform=transform,
        src_crs=src_crs,
        src_nodata=src_nodata,
        dst_transform=dst_transform,
        dst_crs=dst_crs,
        dst_nodata=0.0,
        resampling=Resampling.nearest,
    )
    dst = _clean_array(dst, 0.0)
    return dst, dst_transform, dst_crs


def _clip_bounds_to_dataset(
    dataset_bounds: tuple[float, float, float, float],
    xmin: float | None,
    xmax: float | None,
    ymin: float | None,
    ymax: float | None,
) -> tuple[float, float, float, float] | None:
    left, bottom, right, top = dataset_bounds
    clip_left = left if xmin is None else max(left, float(xmin))
    clip_right = right if xmax is None else min(right, float(xmax))
    clip_bottom = bottom if ymin is None else max(bottom, float(ymin))
    clip_top = top if ymax is None else min(top, float(ymax))
    if clip_left >= clip_right or clip_bottom >= clip_top:
        return None
    return clip_left, clip_bottom, clip_right, clip_top


def _crop_raster(
    data: np.ndarray,
    transform,
    bounds: tuple[float, float, float, float] | None,
) -> tuple[np.ndarray, object]:
    if bounds is None:
        return data, transform
    window = from_bounds(*bounds, transform=transform)
    window = window.round_offsets().round_lengths()
    row_off = max(0, int(window.row_off))
    col_off = max(0, int(window.col_off))
    height = max(0, int(window.height))
    width = max(0, int(window.width))
    cropped = data[row_off : row_off + height, col_off : col_off + width]
    if cropped.size == 0:
        raise ValueError("Cropping bounds produced an empty raster.")
    cropped_transform = rasterio.windows.transform(
        rasterio.windows.Window(col_off, row_off, width, height),
        transform,
    )
    return cropped, cropped_transform


def _window_radius_cells(radius_m: float, pixel_size: float) -> int:
    if radius_m <= 0:
        return 0
    return max(0, int(math.ceil(radius_m / max(pixel_size, 1e-9))))


def _distance_kernel(radius_x: int, radius_y: int, pixel_x: float, pixel_y: float) -> np.ndarray:
    kernel = np.zeros((2 * radius_y + 1, 2 * radius_x + 1), dtype=np.float32)
    cy = radius_y
    cx = radius_x
    for y in range(kernel.shape[0]):
        dy = (y - cy) * pixel_y
        for x in range(kernel.shape[1]):
            dx = (x - cx) * pixel_x
            dist = float(np.hypot(dx, dy))
            if dist == 0.0:
                kernel[y, x] = 1.0 / max(min(pixel_x, pixel_y), 1e-6)
            else:
                kernel[y, x] = 1.0 / dist
    return kernel


def _calc_local_svf(height: np.ndarray, pixel_x: float, pixel_y: float, neighbor_m: float) -> np.ndarray:
    radius_x = _window_radius_cells(neighbor_m, pixel_x)
    radius_y = _window_radius_cells(neighbor_m, pixel_y)
    kernel = _distance_kernel(radius_x, radius_y, pixel_x, pixel_y)
    weighted_height = fftconvolve(height.astype(np.float32), kernel, mode="same").astype(np.float32)
    weight_sum = fftconvolve(np.ones_like(height, dtype=np.float32), kernel, mode="same").astype(np.float32)
    with np.errstate(divide="ignore", invalid="ignore"):
        local_slope = np.where(weight_sum > 0, weighted_height / weight_sum, 0.0)
    return (1.0 / (1.0 + local_slope)).astype(np.float32)


def _calc_facade_area(height: np.ndarray, pixel_x: float, pixel_y: float) -> float:
    right = np.abs(height[:, 1:] - height[:, :-1]) * pixel_y
    down = np.abs(height[1:, :] - height[:-1, :]) * pixel_x
    return float(right.sum() + down.sum())


def _component_oriented_bbox(
    coords_xy: np.ndarray,
    pixel_x: float,
    pixel_y: float,
    wind_direction_deg: float,
) -> tuple[float, float]:
    if coords_xy.shape[0] == 1:
        width = pixel_x
        length = pixel_y
        theta = 0.0
    else:
        centered = coords_xy - coords_xy.mean(axis=0, keepdims=True)
        cov = np.cov(centered.T)
        eigvals, eigvecs = np.linalg.eigh(cov)
        major = eigvecs[:, int(np.argmax(eigvals))]
        if np.linalg.norm(major) == 0.0:
            major = np.array([1.0, 0.0], dtype=np.float64)
        major = major / np.linalg.norm(major)
        minor = np.array([-major[1], major[0]], dtype=np.float64)
        rotated = centered @ np.column_stack([major, minor])
        width = float(rotated[:, 0].max() - rotated[:, 0].min() + pixel_x)
        length = float(rotated[:, 1].max() - rotated[:, 1].min() + pixel_y)
        theta = math.atan2(float(major[1]), float(major[0]))

    diagonal = math.hypot(width, length)
    if diagonal == 0.0:
        return 0.0, 0.0

    wind_theta = math.radians(90.0 - float(wind_direction_deg))
    axis_1 = np.array([math.cos(theta), math.sin(theta)], dtype=np.float64)
    axis_2 = np.array([-axis_1[1], axis_1[0]], dtype=np.float64)
    wind_vec = np.array([math.cos(wind_theta), math.sin(wind_theta)], dtype=np.float64)
    normal = np.array([-wind_vec[1], wind_vec[0]], dtype=np.float64)
    frontal = abs(np.dot(axis_1, normal)) * width + abs(np.dot(axis_2, normal)) * length
    f_yf = frontal / diagonal if diagonal > 0.0 else 0.0
    return diagonal, float(f_yf)


def _calc_ks(
    building_mask: np.ndarray,
    transform,
    pixel_x: float,
    pixel_y: float,
    total_area: float,
    wind_direction_deg: float,
) -> float:
    structure = np.ones((3, 3), dtype=np.uint8)
    labels, count = ndimage.label(building_mask.astype(np.uint8), structure=structure)
    if count == 0 or total_area <= 0.0:
        return 0.0

    total_blockage = 0.0
    for label_id in range(1, count + 1):
        rows, cols = np.where(labels == label_id)
        if len(rows) == 0:
            continue
        xs, ys = rasterio.transform.xy(transform, rows, cols, offset="center")
        coords_xy = np.column_stack([np.asarray(xs, dtype=np.float64), np.asarray(ys, dtype=np.float64)])
        _diag, f_yf = _component_oriented_bbox(coords_xy, pixel_x, pixel_y, wind_direction_deg)
        footprint_area = float(len(rows)) * pixel_x * pixel_y
        total_blockage += footprint_area * f_yf
    return float(total_blockage / total_area)


def process(
    tif_path: str | Path,
    floorHeight: float = 3.5,
    SVFNeighbor: float = 250.0,
    windDirectionDeg: float = 0.0,
    xmin: float | None = None,
    xmax: float | None = None,
    ymin: float | None = None,
    ymax: float | None = None,
) -> dict[str, float]:
    tif_path = Path(tif_path)
    if floorHeight <= 0:
        raise ValueError("floorHeight must be > 0.")
    if SVFNeighbor <= 0:
        raise ValueError("SVFNeighbor must be > 0.")

    with rasterio.open(tif_path) as src:
        raw = src.read(1)
        data = _clean_array(raw, src.nodata)
        transform = src.transform
        crs = src.crs
        src_nodata = src.nodata

    data, transform, _crs = _reproject_if_needed(data, transform, crs, src_nodata=src_nodata)
    data_bounds = rasterio.transform.array_bounds(data.shape[0], data.shape[1], transform)
    clipped_bounds = _clip_bounds_to_dataset(data_bounds, xmin, xmax, ymin, ymax)
    data, transform = _crop_raster(data, transform, clipped_bounds)
    pixel_x = abs(float(transform.a))
    pixel_y = abs(float(transform.e))
    pixel_area = pixel_x * pixel_y
    total_area = float(data.shape[0] * data.shape[1]) * pixel_area
    building_mask = data > 0.0
    outdoor_mask = ~building_mask

    building_area_ratio = float(building_mask.sum() * pixel_area / total_area) if total_area > 0.0 else 0.0
    plot_ratio = float(((data / float(floorHeight)) * pixel_area).sum() / total_area) if total_area > 0.0 else 0.0
    facade_ratio = float(_calc_facade_area(data, pixel_x, pixel_y) / total_area) if total_area > 0.0 else 0.0
    local_svf = _calc_local_svf(data, pixel_x, pixel_y, SVFNeighbor)
    # Keep SVF consistent with process_BLD_SHP.py: only outdoor cells contribute.
    outdoor_svf = local_svf[outdoor_mask]
    svf = float(outdoor_svf.mean()) if outdoor_svf.size else 1.0
    ks = _calc_ks(building_mask, transform, pixel_x, pixel_y, total_area, windDirectionDeg)

    return {
        "AreaBuilding": building_area_ratio,
        "AreaFacade": facade_ratio,
        "kS": ks,
        "SVF": svf,
        "plotRatio": plot_ratio,
        "TotalArea": total_area,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize building-height tif metrics.")
    parser.add_argument("--input", required=True, help="Input building height tif path.")
    parser.add_argument("--floor-height", type=float, default=3.5, help="Meters per floor. Default: 3.5.")
    parser.add_argument("--svf-neighbor", type=float, default=250.0, help="Local SVF neighborhood radius in meters. Default: 250.")
    parser.add_argument("--wind-direction", type=float, default=0.0, help="Dominant wind direction in degrees, north=0 and clockwise positive. Default: 0.")
    parser.add_argument("--xmin", type=float, help="Optional left clipping bound in raster CRS.")
    parser.add_argument("--xmax", type=float, help="Optional right clipping bound in raster CRS.")
    parser.add_argument("--ymin", type=float, help="Optional bottom clipping bound in raster CRS.")
    parser.add_argument("--ymax", type=float, help="Optional top clipping bound in raster CRS.")
    args = parser.parse_args()

    result = process(
        tif_path=args.input,
        floorHeight=args.floor_height,
        SVFNeighbor=args.svf_neighbor,
        windDirectionDeg=args.wind_direction,
        xmin=args.xmin,
        xmax=args.xmax,
        ymin=args.ymin,
        ymax=args.ymax,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
