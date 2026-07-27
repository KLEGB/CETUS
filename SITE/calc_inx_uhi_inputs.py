from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import rasterio

try:
    from scipy.signal import fftconvolve
except Exception:  # pragma: no cover - optional dependency fallback
    fftconvolve = None


def bar(process, msg, dig=0, printed=True):
    q = round(process * 100, dig)
    hint = f"\r[{msg}: {'*' * round(q / 2)} {'_' * (50 - round(q / 2))} {q} %]"
    if printed:
        print(hint, end="")
    return hint


def _read_raster(path: Path):
    with rasterio.open(path) as src:
        data = src.read(1, masked=True)
        meta = src.meta.copy()
        transform = src.transform
        shape = src.shape
    if np.ma.isMaskedArray(data):
        data = data.filled(0.0)
    data = np.nan_to_num(np.asarray(data), nan=0.0, posinf=0.0, neginf=0.0)
    return data, meta, transform, shape


def _clean_array(arr: np.ndarray) -> np.ndarray:
    return np.nan_to_num(np.asarray(arr), nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32, copy=False)


def _same_grid(meta_list: list[dict]) -> dict:
    base = meta_list[0]
    for meta in meta_list[1:]:
        if meta["width"] != base["width"] or meta["height"] != base["height"]:
            raise ValueError("Input rasters must have the same shape")
        if meta.get("transform") != base.get("transform"):
            raise ValueError("Input rasters must have the same transform")
    return base


def _save_raster(path: Path, data: np.ndarray, meta: dict, *, dtype=None, nodata=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    out_meta = meta.copy()
    out_meta.update(
        {
            "driver": "GTiff",
            "count": 1,
            "dtype": dtype or data.dtype,
            "compress": "lzw",
        }
    )
    if nodata is not None:
        out_meta["nodata"] = nodata
    else:
        out_meta.pop("nodata", None)
    data = _clean_array(data)
    with rasterio.open(path, "w", **out_meta) as dst:
        dst.write(data.astype(out_meta["dtype"]), 1)


def _window_radius_cells(radius_m: float, pixel_size: float) -> int:
    if radius_m <= 0:
        return 0
    return max(0, int(np.ceil(radius_m / max(pixel_size, 1e-9))))


def _integral_image(arr: np.ndarray) -> np.ndarray:
    padded = np.pad(arr, ((1, 0), (1, 0)), mode="constant", constant_values=0)
    return padded.cumsum(axis=0).cumsum(axis=1)


def _window_sum(arr: np.ndarray, radius_x: int, radius_y: int, label: str = "window"):
    arr = _clean_array(arr)
    h, w = arr.shape
    padded = np.pad(arr, ((radius_y, radius_y), (radius_x, radius_x)), mode="constant", constant_values=0)
    integ = _integral_image(padded)
    out = np.zeros((h, w), dtype=np.float32)
    cols = np.arange(w, dtype=np.int32)
    left = cols
    right = cols + 2 * radius_x
    total = max(h, 1)
    for i in range(h):
        top = i
        bottom = i + 2 * radius_y
        row = (
            integ[bottom + 1, right + 1]
            - integ[top, right + 1]
            - integ[bottom + 1, left]
            + integ[top, left]
        )
        out[i] = row
        bar((i + 1) / total, label, dig=2)
    print()
    return out


def _window_mean(arr: np.ndarray, radius_x: int, radius_y: int, label: str = "mean"):
    summed = _window_sum(arr.astype(np.float32), radius_x, radius_y, label=label)
    counts = _window_sum(np.ones_like(arr, dtype=np.float32), radius_x, radius_y, label=f"{label}-count")
    with np.errstate(divide="ignore", invalid="ignore"):
        mean = np.where(counts > 0, summed / counts, 0.0).astype(np.float32)
    return mean


def _box_count(mask: np.ndarray, radius_x: int, radius_y: int, label: str = "count"):
    mask_f = mask.astype(np.float32)
    return _window_sum(mask_f, radius_x, radius_y, label=label)


def _distance_kernel(radius_x: int, radius_y: int, pixel_x: float, pixel_y: float, *, power: float = 1.0):
    kernel = np.zeros((2 * radius_y + 1, 2 * radius_x + 1), dtype=np.float32)
    total = max(kernel.shape[0], 1)
    cy = radius_y
    cx = radius_x
    for y in range(kernel.shape[0]):
        dy = (y - cy) * pixel_y
        for x in range(kernel.shape[1]):
            dx = (x - cx) * pixel_x
            dist = float(np.hypot(dx, dy))
            if dist == 0:
                kernel[y, x] = 1.0 / max(min(pixel_x, pixel_y), 1e-6) ** power
            elif dist <= max(radius_x * pixel_x, radius_y * pixel_y):
                kernel[y, x] = 1.0 / (dist ** power)
        bar((y + 1) / total, "SVF-kernel", dig=2)
    print()
    return kernel


def _fft_convolve_same(arr: np.ndarray, kernel: np.ndarray):
    arr = _clean_array(arr)
    kernel = _clean_array(kernel)
    if fftconvolve is not None:
        return fftconvolve(arr, kernel, mode="same").astype(np.float32)

    shape = tuple(int(a + b - 1) for a, b in zip(arr.shape, kernel.shape))
    fa = np.fft.rfftn(arr, shape)
    fk = np.fft.rfftn(kernel, shape)
    out = np.fft.irfftn(fa * fk, shape)
    start = tuple((k - 1) // 2 for k in kernel.shape)
    end = tuple(start[i] + arr.shape[i] for i in range(2))
    return out[start[0] : end[0], start[1] : end[1]].astype(np.float32)


def _calc_facade_map(height: np.ndarray, pixel_x: float, pixel_y: float):
    height = _clean_array(height)
    h, w = height.shape
    facade = np.zeros((h, w), dtype=np.float32)
    total = max(h, 1)
    for i in range(h):
        row = height[i]
        right = np.abs(row[:-1] - row[1:]) * pixel_y * 0.5
        facade[i, :-1] += right
        facade[i, 1:] += right
        if i < h - 1:
            down = np.abs(row - height[i + 1]) * pixel_x * 0.5
            facade[i, :] += down
            facade[i + 1, :] += down
        bar((i + 1) / total, "facade-source", dig=2)
    print()
    return facade


def _calc_local_svf(building_height: np.ndarray, pixel_x: float, pixel_y: float, local_radius_m: float):
    building_height = _clean_array(building_height)
    radius_x = _window_radius_cells(local_radius_m, pixel_x)
    radius_y = _window_radius_cells(local_radius_m, pixel_y)
    kernel = _distance_kernel(radius_x, radius_y, pixel_x, pixel_y, power=1.0)
    weighted_height = _fft_convolve_same(building_height.astype(np.float32), kernel)
    weight_sum = _fft_convolve_same(np.ones_like(building_height, dtype=np.float32), kernel)
    with np.errstate(divide="ignore", invalid="ignore"):
        local_slope = np.where(weight_sum > 0, weighted_height / weight_sum, 0.0)
    # Simplified sky-view proxy: larger surrounding height lowers sky exposure.
    local_svf = 1.0 / (1.0 + local_slope)
    return local_svf.astype(np.float32)


def _load_inputs(input_dir: Path):
    names = {
        "building_height": "building_height.tif",
        "vegetation": "vegetation.tif",
        "water": "water.tif",
        "road": "road.tif",
        "paved": "paved.tif",
        "soil": "soil.tif",
    }
    arrays: dict[str, np.ndarray] = {}
    metas: list[dict] = []
    transform = None
    shape = None
    for key, filename in names.items():
        path = input_dir / filename
        if not path.exists():
            raise FileNotFoundError(f"Missing required input raster: {path}")
        data, meta, tfm, shp = _read_raster(path)
        arrays[key] = data.astype(np.float32)
        metas.append(meta)
        if transform is None:
            transform = tfm
            shape = shp
    base_meta = _same_grid(metas)
    return arrays, base_meta, transform, shape


def calc_inx_uhi_inputs(
    input_dir: Path,
    output_dir: Path,
    *,
    radius_m: float = 1000.0,
    svf_local_radius_m: float = 250.0,
    building_floor_height_m: float = 3.0,
):
    arrays, meta, transform, shape = _load_inputs(input_dir)

    pixel_x = abs(float(transform.a))
    pixel_y = abs(float(transform.e))
    pixel_area = pixel_x * pixel_y
    radius_x = _window_radius_cells(radius_m, pixel_x)
    radius_y = _window_radius_cells(radius_m, pixel_y)
    window_count = _window_sum(np.ones(shape, dtype=np.float32), radius_x, radius_y, label="window-count")

    building_height = _clean_array(arrays["building_height"])
    vegetation = (_clean_array(arrays["vegetation"]) > 0).astype(np.float32)
    water = (_clean_array(arrays["water"]) > 0).astype(np.float32)
    road = (_clean_array(arrays["road"]) > 0).astype(np.float32)
    paved = (_clean_array(arrays["paved"]) > 0).astype(np.float32)
    soil = (_clean_array(arrays["soil"]) > 0).astype(np.float32)
    building_mask = (building_height > 0).astype(np.float32)
    ground_mask = np.clip(soil + paved, 0, 1).astype(np.float32)

    print("Calculating building ratio")
    building_ratio = _window_sum(building_mask, radius_x, radius_y, label="building-ratio")
    with np.errstate(divide="ignore", invalid="ignore"):
        building_ratio = np.where(window_count > 0, building_ratio / window_count, 0.0).astype(np.float32)

    print("Calculating ground ratio")
    ground_ratio = _window_sum(ground_mask, radius_x, radius_y, label="ground-ratio")
    with np.errstate(divide="ignore", invalid="ignore"):
        ground_ratio = np.where(window_count > 0, ground_ratio / window_count, 0.0).astype(np.float32)

    print("Calculating road ratio")
    road_ratio = _window_sum(road, radius_x, radius_y, label="road-ratio")
    with np.errstate(divide="ignore", invalid="ignore"):
        road_ratio = np.where(window_count > 0, road_ratio / window_count, 0.0).astype(np.float32)

    print("Calculating landscape ratio")
    landscape_ratio = _window_sum(vegetation, radius_x, radius_y, label="landscape-ratio")
    with np.errstate(divide="ignore", invalid="ignore"):
        landscape_ratio = np.where(window_count > 0, landscape_ratio / window_count, 0.0).astype(np.float32)

    print("Calculating water ratio")
    water_ratio = _window_sum(water, radius_x, radius_y, label="water-ratio")
    with np.errstate(divide="ignore", invalid="ignore"):
        water_ratio = np.where(window_count > 0, water_ratio / window_count, 0.0).astype(np.float32)

    print("Calculating plot ratio")
    floor_area_density = np.where(building_height > 0, (building_height / building_floor_height_m) * pixel_area, 0.0)
    plot_ratio = _window_sum(floor_area_density.astype(np.float32), radius_x, radius_y, label="plot-ratio")
    window_area = window_count * pixel_area
    with np.errstate(divide="ignore", invalid="ignore"):
        plot_ratio = np.where(window_area > 0, plot_ratio / window_area, 0.0).astype(np.float32)
    plot_ratio = np.clip(plot_ratio, 0.0, None)

    print("Calculating facade source map")
    facade_source = _calc_facade_map(building_height, pixel_x, pixel_y)

    print("Calculating facade area")
    facade_area = _window_sum(facade_source, radius_x, radius_y, label="facade-area")

    print("Calculating local SVF")
    local_svf = _calc_local_svf(building_height, pixel_x, pixel_y, svf_local_radius_m)

    print("Calculating averaged SVF")
    svf = _window_sum(local_svf, radius_x, radius_y, label="svf-mean")
    with np.errstate(divide="ignore", invalid="ignore"):
        svf = np.where(window_count > 0, svf / window_count, 0.0).astype(np.float32)
    svf = np.clip(svf, 0.0, 1.0).astype(np.float32)

    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = {
        "building.tif": building_ratio.astype(np.float32),
        "ground.tif": ground_ratio.astype(np.float32),
        "road.tif": road_ratio.astype(np.float32),
        "lanscape.tif": landscape_ratio.astype(np.float32),
        "water.tif": water_ratio.astype(np.float32),
        "SVF.tif": svf,
        "plotRatio.tif": plot_ratio.astype(np.float32),
        "facadeArea.tif": facade_area.astype(np.float32),
    }

    out_meta = meta.copy()
    out_meta.update({"dtype": "float32"})
    for name, data in outputs.items():
        _save_raster(output_dir / name, data, out_meta, dtype="float32", nodata=None)

    manifest = {
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "radius_m": radius_m,
        "svf_local_radius_m": svf_local_radius_m,
        "building_floor_height_m": building_floor_height_m,
        "pixel_x": pixel_x,
        "pixel_y": pixel_y,
        "shape": {"rows": shape[0], "cols": shape[1]},
        "outputs": list(outputs.keys()),
        "notes": {
            "building": "ratio of building footprint within the sliding window",
            "ground": "ratio of soil + paved within the sliding window",
            "road": "ratio of road within the sliding window",
            "lanscape": "ratio of vegetation within the sliding window",
            "water": "ratio of water within the sliding window",
            "SVF": "simplified local sky-view proxy averaged over the sliding window",
            "plotRatio": "floor area ratio from building height / 3.0m per floor",
            "facadeArea": "sum of edge height differences times pixel edge length",
        },
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main():
    parser = argparse.ArgumentParser(description="Build UHI tif inputs from extract_inx_landuse.py outputs.")
    parser.add_argument(
        "--input-dir",
        default=r"workplace/rasters",
        help="Directory containing building_height.tif, vegetation.tif, water.tif, road.tif, paved.tif, soil.tif.",
    )
    parser.add_argument(
        "--output-dir",
        default=r"workplace/uhi_inputs",
        help="Directory for generated UHI input tif files.",
    )
    parser.add_argument(
        "--radius-m",
        type=float,
        default=1000.0,
        help="Sliding window radius in meters for land-use aggregation.",
    )
    parser.add_argument(
        "--svf-local-radius-m",
        type=float,
        default=250.0,
        help="Local radius in meters used for the simplified SVF proxy.",
    )
    parser.add_argument(
        "--building-floor-height-m",
        type=float,
        default=3.0,
        help="Meters per floor for plotRatio estimation.",
    )
    args = parser.parse_args()

    manifest = calc_inx_uhi_inputs(
        Path(args.input_dir),
        Path(args.output_dir),
        radius_m=args.radius_m,
        svf_local_radius_m=args.svf_local_radius_m,
        building_floor_height_m=args.building_floor_height_m,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
