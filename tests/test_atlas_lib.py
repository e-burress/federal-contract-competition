"""Offline tests for scripts/atlas_lib.py: share_table counts, shares and Wilson intervals."""
import math
import os
import statistics
import sys
import unittest

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import atlas_lib  # noqa: E402

Z = statistics.NormalDist().inv_cdf(0.975)


def wilson(k, n):
    """textbook 95% Wilson score interval"""
    p = k / n
    centre = (p + Z * Z / (2 * n)) / (1 + Z * Z / n)
    half = Z * math.sqrt(p * (1 - p) / n + Z * Z / (4 * n * n)) / (1 + Z * Z / n)
    return centre - half, centre + half


class ShareTableTests(unittest.TestCase):
    def setUp(self):
        self.df = pd.DataFrame({
            "agency": ["A"] * 5 + ["B"] * 4 + ["C"] * 3,
            "fy": [2024, 2024, 2025, 2025, 2025] + [2024] * 4 + [2025] * 3,
            "single": [True, True, False, False, False] + [True] * 4 + [False] * 3,
        })

    def test_counts_shares_and_wilson(self):
        t = atlas_lib.share_table(self.df, "agency")
        self.assertEqual(list(t.columns), ["agency", "k_single", "n_eval", "share", "ci_lo", "ci_hi"])
        t = t.set_index("agency")
        for ag, (k, n) in {"A": (2, 5), "B": (4, 4), "C": (0, 3)}.items():
            with self.subTest(agency=ag):
                self.assertEqual(int(t.loc[ag, "k_single"]), k)
                self.assertEqual(int(t.loc[ag, "n_eval"]), n)
                self.assertAlmostEqual(t.loc[ag, "share"], k / n)
                lo, hi = wilson(k, n)
                self.assertAlmostEqual(t.loc[ag, "ci_lo"], lo, places=9)
                self.assertAlmostEqual(t.loc[ag, "ci_hi"], hi, places=9)
                self.assertLessEqual(t.loc[ag, "ci_lo"], t.loc[ag, "share"] + 1e-12)
                self.assertGreaterEqual(t.loc[ag, "ci_hi"], t.loc[ag, "share"] - 1e-12)

    def test_extreme_shares_stay_inside_0_1(self):
        t = atlas_lib.share_table(self.df, "agency").set_index("agency")
        self.assertGreaterEqual(t.loc["C", "ci_lo"], -1e-12)
        self.assertLessEqual(t.loc["B", "ci_hi"], 1 + 1e-12)
        self.assertGreater(t.loc["C", "ci_hi"], 0.2)                # n=3, no successes: a wide upper bound
        self.assertLess(t.loc["B", "ci_lo"], 0.6)

    def test_two_grouping_columns(self):
        t = atlas_lib.share_table(self.df, ["agency", "fy"])
        self.assertEqual(list(t.columns[:2]), ["agency", "fy"])
        row = t[(t.agency == "A") & (t.fy == 2025)].iloc[0]
        self.assertEqual((int(row.k_single), int(row.n_eval)), (0, 3))
        self.assertEqual(int(t.n_eval.sum()), len(self.df))
        self.assertEqual(len(t), 4)

    def test_unobserved_categorical_levels_are_dropped(self):
        df = self.df.copy()
        df["agency"] = pd.Categorical(df["agency"], categories=["A", "B", "C", "UNUSED"])
        t = atlas_lib.share_table(df, "agency")
        self.assertEqual(sorted(t["agency"].astype(str)), ["A", "B", "C"])


if __name__ == "__main__":
    unittest.main()
