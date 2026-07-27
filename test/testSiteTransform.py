from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import sys

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from SITE.transform import inx_to_shp, inx_to_tif, shp_to_inx, shp_to_tif, tif_to_inx, tif_to_shp


class SiteTransformTest(unittest.TestCase):
    def test_tif_inx_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory(prefix="site_transform_") as temp_dir:
            temp_root = Path(temp_dir)
            tif_path = temp_root / "input.tif"
            inx_path = temp_root / "output.INX"
            roundtrip_tif = temp_root / "roundtrip.tif"

            arrays = [
                np.array([[0.0, 1.0, 1.0], [0.0, 2.0, 0.0]], dtype=np.float32),
                np.array([[0.0, 1.0, 0.0], [0.0, 0.0, 0.0]], dtype=np.float32),
                np.array([[1.0, 0.0, 0.0], [0.0, 0.0, 0.0]], dtype=np.float32),
                np.array([[0.0, 0.0, 1.0], [0.0, 0.0, 0.0]], dtype=np.float32),
            ]
            with rasterio.open(
                tif_path,
                "w",
                driver="GTiff",
                height=2,
                width=3,
                count=4,
                dtype="float32",
                transform=from_origin(0.0, 2.0, 1.0, 1.0),
                crs=None,
                nodata=0.0,
            ) as dst:
                for index, array in enumerate(arrays, start=1):
                    dst.write(array, index)

            tif_to_inx(tif_path, inx_path, building_scale=0.1)
            inx_to_tif(inx_path, roundtrip_tif, building_scale=0.1)

            with rasterio.open(roundtrip_tif) as src:
                self.assertEqual(src.count, 4)
                roundtrip_arrays = [src.read(index) for index in range(1, 5)]

            self.assertTrue(np.allclose(roundtrip_arrays[0], arrays[0]))
            self.assertTrue(np.array_equal(roundtrip_arrays[1], arrays[1]))
            self.assertTrue(np.array_equal(roundtrip_arrays[2], arrays[2]))
            self.assertTrue(np.array_equal(roundtrip_arrays[3], arrays[3]))

    def test_shp_tif_shp_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory(prefix="site_transform_") as temp_dir:
            temp_root = Path(temp_dir)
            shp_path = temp_root / "input.shp"
            tif_path = temp_root / "output.tif"
            roundtrip_shp = temp_root / "roundtrip.shp"

            gdf = gpd.GeoDataFrame(
                {
                    "Height": [5.0, 8.0],
                    "geometry": [box(0, 0, 2, 2), box(3, 0, 5, 3)],
                },
                crs="EPSG:3857",
            )
            gdf.to_file(shp_path)

            shp_to_tif(shp_path, tif_path, resolution=1.0, height_column="Height")
            tif_to_shp(tif_path, roundtrip_shp, height_column="Height")

            out_gdf = gpd.read_file(roundtrip_shp)
            self.assertFalse(out_gdf.empty)
            self.assertEqual(set(np.round(out_gdf["Height"].astype(float), 6)), {5.0, 8.0})

    def test_shp_inx_shp_pipeline(self) -> None:
        with tempfile.TemporaryDirectory(prefix="site_transform_") as temp_dir:
            temp_root = Path(temp_dir)
            shp_path = temp_root / "input.shp"
            inx_path = temp_root / "output.INX"
            roundtrip_shp = temp_root / "roundtrip.shp"

            gdf = gpd.GeoDataFrame(
                {
                    "Height": [4.0],
                    "geometry": [box(0, 0, 2, 2)],
                },
                crs="EPSG:3857",
            )
            gdf.to_file(shp_path)

            shp_to_inx(shp_path, inx_path, resolution=1.0, height_column="Height", building_scale=0.1)
            inx_to_shp(inx_path, roundtrip_shp, building_scale=0.1, height_column="Height")

            out_gdf = gpd.read_file(roundtrip_shp)
            self.assertFalse(out_gdf.empty)
            self.assertIn("Height", out_gdf.columns)


if __name__ == "__main__":
    unittest.main()
