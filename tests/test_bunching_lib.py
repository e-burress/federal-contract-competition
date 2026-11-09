"""Offline tests for scripts/bunching_lib.py: right-closed bins, the bunching ratio, the bootstrap."""
import os
import sys
import unittest
import warnings

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import bunching_lib as bl  # noqa: E402


def smooth_values(spec, n, seed):
    """n award values spread smoothly (a skewed beta shape) across the spec's window"""
    lo, hi = spec[0], spec[1]
    r = np.random.default_rng(seed)
    return lo + (hi - lo) * r.beta(1.2, 2.5, n)


def planted(T, vals, n_extra, seed, width):
    """add n_extra awards just under threshold T (within `width` below it)"""
    r = np.random.default_rng(seed)
    extra = T - width * r.random(n_extra) - 1
    return np.concatenate([vals, extra])


class CountBinsTests(unittest.TestCase):
    def test_right_closed_bins(self):
        edges, counts = bl.count_bins(np.array([0, 5, 10, 10.0001, 20, 30, 31, -1, 30.5]), 0, 30, 10)
        np.testing.assert_array_equal(edges, [0, 10, 20, 30])
        # (0,10]: 5 and 10 | (10,20]: 10.0001 and 20 | (20,30]: 30.  0 is left of the open edge, 31 and -1 are outside
        np.testing.assert_array_equal(counts, [2, 2, 1])

    def test_value_on_threshold_goes_to_bin_below(self):
        edges, counts = bl.count_bins(np.array([250_000.0, 250_001.0]), 150_000, 330_000, 5_000)
        i_below = int(np.where(edges == 250_000)[0][0]) - 1        # the bin (245000, 250000]
        self.assertEqual(counts[i_below], 1)
        self.assertEqual(counts[i_below + 1], 1)                    # 250001 is in (250000, 255000]
        self.assertEqual(counts.sum(), 2)

    def test_spec_shapes(self):
        for T, (lo, hi, w, bx, ax) in bl.SPEC.items():
            with self.subTest(T=T):
                self.assertLess(lo, T - bx)
                self.assertGreater(hi, T + ax)
                self.assertEqual((hi - lo) % w, 0)
                self.assertEqual(T % w, 0)                          # the threshold sits exactly on a bin edge
                edges, counts = bl.count_bins(np.array([T - 1.0]), lo, hi, w)
                self.assertEqual(len(counts), len(edges) - 1)
                self.assertEqual(len(counts), (hi - lo) // w)


class BunchTests(unittest.TestCase):
    def ratio(self, vals, T):
        lo, hi, w, bx, ax = bl.SPEC[T]
        e, c = bl.count_bins(vals, lo, hi, w)
        return bl.bunch(c, e, T, bx, ax)

    def test_smooth_histogram_ratio_near_one(self):
        for T, n in ((250_000, 150_000), (10_000, 80_000)):
            with self.subTest(T=T):
                res = self.ratio(smooth_values(bl.SPEC[T], n, seed=11), T)
                self.assertAlmostEqual(res["ratio"], 1.0, delta=0.12)
                self.assertAlmostEqual(res["obs_below"] / res["cf_below"], res["ratio"])
                self.assertGreater(res["cf_above"], 0)

    def test_planted_awards_just_below_threshold_raise_the_ratio(self):
        for T, n, extra, width in ((250_000, 150_000, 6000, 15_000), (10_000, 80_000, 3000, 800)):
            with self.subTest(T=T):
                base_vals = smooth_values(bl.SPEC[T], n, seed=12)
                clean = self.ratio(base_vals, T)
                bunched = self.ratio(planted(T, base_vals, extra, 13, width), T)
                self.assertGreater(bunched["ratio"], 1.2)
                self.assertGreater(bunched["ratio"], clean["ratio"] + 0.2)
                self.assertEqual(bunched["obs_below"], clean["obs_below"] + extra)
                self.assertEqual(bunched["obs_above"], clean["obs_above"])   # nothing was planted above

    def test_counterfactual_ignores_the_excluded_zone(self):
        # a bigger spike inside the exclusion zone must not move the prediction for that zone much
        T = 250_000
        v = smooth_values(bl.SPEC[T], 150_000, seed=14)
        small = self.ratio(planted(T, v, 300, 15, 15_000), T)
        large = self.ratio(planted(T, v, 3000, 15, 15_000), T)
        self.assertAlmostEqual(small["cf_below"], large["cf_below"], delta=0.1 * small["cf_below"])
        self.assertGreater(large["ratio"], small["ratio"])

    def test_degenerate_input_does_not_raise(self):
        edges = np.arange(0, 101, 10.0)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            res = bl.bunch(np.zeros(len(edges) - 1, dtype=int), edges, 50, 10, 10, deg=5)
        self.assertIn("ratio", res)


class BootTests(unittest.TestCase):
    def test_boot_returns_B_finite_values_around_the_point_estimate(self):
        T = 250_000
        spec = bl.SPEC[T]
        vals = planted(T, smooth_values(spec, 60_000, seed=16), 800, 17, 15_000)
        lo, hi, w, bx, ax = spec
        e, c = bl.count_bins(vals, lo, hi, w)
        point = bl.bunch(c, e, T, bx, ax)["ratio"]
        out = bl.boot(vals, T, spec, B=12)
        self.assertEqual(out.shape, (12,))
        self.assertTrue(np.isfinite(out).all())
        self.assertAlmostEqual(float(np.median(out)), point, delta=0.3 * point)
        self.assertGreater(float(out.min()), 1.0)
        self.assertGreater(out.std(), 0)                            # it really resamples


if __name__ == "__main__":
    unittest.main()
