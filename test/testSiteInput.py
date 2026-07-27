from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import sys

import numpy as np

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from UHI.modelInfo import SiteInfo
from SITE.siteInput import siteInput

TIF_PATH = WORKSPACE_ROOT / "workplace" / "_inxExtract" / "building_height.tif"
SHP_PATH = WORKSPACE_ROOT / "workplace" / "_cityJsonExport" / "pazhou.shp"
INX_PATH = WORKSPACE_ROOT / "workplace" / "pazhou_actually.INX"


class SiteInputIntegrationTest(unittest.TestCase):
    def _assert_valid_site_input(self, site: siteInput) -> None:
        self.assertGreater(float(site.TotalArea), 0.0)
        self.assertGreaterEqual(float(site.AreaBuilding), 0.0)
        self.assertGreaterEqual(float(site.AreaFacade), 0.0)
        self.assertGreaterEqual(float(site.plotRatio), 0.0)
        self.assertGreaterEqual(float(site.SVF), 0.0)
        self.assertLessEqual(float(site.SVF), 1.0)

    def _assert_valid_site_info(self, site: SiteInfo) -> None:
        self.assertGreater(float(site.TotalArea), 0.0)
        self.assertGreaterEqual(float(site.AreaBuilding), 0.0)
        self.assertGreaterEqual(float(site.AreaFacade), 0.0)
        self.assertGreaterEqual(float(site.plotRatio), 0.0)
        self.assertGreaterEqual(float(site.SVF), 0.0)
        self.assertLessEqual(float(site.SVF), 1.0)

    def test_from_tif_and_csv_roundtrip(self) -> None:
        site = siteInput.fromTIF(TIF_PATH)
        self._assert_valid_site_input(site)

        with tempfile.TemporaryDirectory(prefix="siteinput_test_") as temp_dir:
            csv_path = Path(temp_dir) / "siteinfo.csv"
            site.writeToCSV(csv_path)
            restored = siteInput.fromCSV(csv_path)

        self.assertEqual(restored.sourceType, "tif")
        self.assertAlmostEqual(float(restored.TotalArea), float(site.TotalArea))
        self.assertAlmostEqual(float(restored.AreaBuilding), float(site.AreaBuilding))
        self.assertAlmostEqual(float(restored.AreaFacade), float(site.AreaFacade))

    def test_from_shp(self) -> None:
        site = siteInput.fromSHP(SHP_PATH, resolution=50.0, workers=1)
        self._assert_valid_site_input(site)
        self.assertEqual(site.sourceType, "shp")

    def test_from_inx(self) -> None:
        site = siteInput.fromINX(INX_PATH)
        self._assert_valid_site_input(site)
        self.assertEqual(site.sourceType, "inx")
        self.assertGreaterEqual(float(site.AreaRoad), 0.0)
        self.assertGreaterEqual(float(site.AreaGround), 0.0)
        self.assertGreaterEqual(float(site.AreaLandscape), 0.0)
        self.assertGreaterEqual(float(site.AreaWater), 0.0)
        self.assertIsNone(site.vaporWater)

    def test_siteinfo_factories(self) -> None:
        tif_site = SiteInfo.fromTIF(TIF_PATH)
        shp_site = SiteInfo.fromSHP(SHP_PATH, resolution=50.0, workers=1)
        inx_site = SiteInfo.fromINX(INX_PATH)
        self._assert_valid_site_info(tif_site)
        self._assert_valid_site_info(shp_site)
        self._assert_valid_site_info(inx_site)
        self.assertTrue(np.isfinite(inx_site.vaporWater).all())

    def test_siteinfo_from_csv(self) -> None:
        with tempfile.TemporaryDirectory(prefix="siteinput_test_") as temp_dir:
            csv_path = Path(temp_dir) / "siteinfo.csv"
            siteInput.fromTIF(TIF_PATH).writeToCSV(csv_path)
            site = SiteInfo.fromCSV(csv_path)
        self._assert_valid_site_info(site)


if __name__ == "__main__":
    unittest.main()
