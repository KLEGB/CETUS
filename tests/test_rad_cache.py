"""Radiation cache persistence, including Windows fsync compatibility."""
import tempfile
import unittest
from pathlib import Path

import numpy as np
from scipy.sparse import csr_matrix

from RAD.network import RadiationNetwork


class RadiationCacheTests(unittest.TestCase):
    def test_save_load_roundtrip(self):
        exchange = csr_matrix((1, 1))
        net = RadiationNetwork(
            np.zeros((1, 3)), np.array([[0., 0., 1.]]), np.ones(1),
            np.array([-1]), np.zeros(1, dtype=int), np.ones(1),
            exchange, exchange, np.array([[100., 200.]]),
            np.array([[1., 2.]]), np.array([0, 1]), {'test': True})
        net.longwave[:] = [[300., 400.]]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'cache.npz'
            net.save(path)
            restored = RadiationNetwork.load(path)
            np.testing.assert_array_equal(restored.direct, net.direct)
            np.testing.assert_array_equal(restored.reflected, net.reflected)
            np.testing.assert_array_equal(restored.longwave, net.longwave)
            self.assertEqual(restored.meta, net.meta)
            self.assertFalse(path.with_suffix('.writing.npz').exists())


if __name__ == '__main__':
    unittest.main()
