"""Offline tests for scripts/27_threshold_scan.py on synthetic data: a planted pile-up under a line is found and a smooth distribution is
not; bins near other thresholds are left out of the fit; the active/inactive logic and the before/after windows use the right dates
(including each agency's own 2018 date)."""
import importlib.util
import os
import sys
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
_spec = importlib.util.spec_from_file_location("scan27", os.path.join(ROOT, "scripts", "27_threshold_scan.py"))
sc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sc)

D = np.datetime64


def smooth(L, n, seed):
    """n award values, smooth (lognormal) across the window of line L"""
    r = np.random.default_rng(seed)
    v = np.exp(r.normal(np.log(L), 0.6, n * 4))
    return v[(v > 0.6 * L) & (v <= 1.4 * L)][:n]


def planted(L, vals, n_extra, seed):
    """add n_extra awards spread over the 6% just under L, plus a few exactly at L"""
    r = np.random.default_rng(seed)
    return np.concatenate([vals, L * (1 - 0.06 * r.random(n_extra)), np.full(n_extra // 5, float(L))])


def estimate(vals, L, excl=(), boot=200, seed=1):
    g = sc.Grid([L])
    return sc.measure(g, g.hist(np.asarray(vals, float)), [L], list(excl), np.random.default_rng(seed), boot)[L]


class EstimatorTests(unittest.TestCase):
    def test_planted_pile_up_is_found(self):
        for L in (250_000, 4_000_000, 10_000):
            r = estimate(planted(L, smooth(L, 20_000, 1), 600, 2), L)
            lo, hi = sc.ci(r["reps"])
            self.assertGreater(r["ratio"], 1.25, L)                 # 720 extra awards over about 2,000 expected
            self.assertGreater(lo, 1.1, L)

    def test_smooth_distribution_is_not_flagged(self):
        for seed, L in enumerate((250_000, 700_000, 4_000_000)):
            r = estimate(smooth(L, 20_000, 10 + seed), L)
            lo, hi = sc.ci(r["reps"])
            self.assertLess(abs(r["ratio"] - 1), 0.12, L)
            self.assertTrue(lo <= 1 <= hi, (L, lo, hi))

    def test_value_on_the_line_is_in_the_zone(self):
        g = sc.Grid([250_000])
        y = g.line_counts(g.hist(np.array([250_000.0, 250_001.0])), 250_000)
        zone, _ = sc.masks(250_000, [])
        self.assertEqual(int(y[zone].sum()), 1)

    def test_bins_near_other_thresholds_and_round_values_left_out_of_fit(self):
        zone, fit = sc.masks(250_000, [200_000, 250_000])
        e = sc.edges_of(250_000)
        hi = e[1:]
        self.assertFalse(fit[(hi > 184_000) & (hi <= 208_000)].any())       # (0.92 x 200K, 1.04 x 200K] left out
        self.assertFalse(fit[hi == 300_000].any())                          # bin holding $300,000 (a multiple of $100,000)
        self.assertFalse(fit[zone].any())
        self.assertEqual(int(zone.sum()), 4)
        self.assertTrue(fit[hi == 160_000].all())

    def test_other_thresholds_pile_up_does_not_bias_the_line(self):
        L = 250_000
        v = planted(200_000, smooth(L, 20_000, 3), 800, 4)           # a pile-up under $200K only
        self.assertLess(abs(estimate(v, L, excl=[200_000])["ratio"] - 1), 0.15)

    def test_too_few_awards_gives_no_ratio(self):
        r = estimate(smooth(250_000, 30, 5), 250_000, boot=0)
        self.assertTrue(np.isnan(r["ratio"]))

    def test_placebos_are_round_and_never_thresholds(self):
        T = sc.load_thresholds()
        allv = sorted(set(T.value))
        self.assertEqual(sc.placebos_for(250_000, allv), [200_000, 300_000])
        for L in (350_000, 10_000, 750_000):
            for p in sc.placebos_for(L, allv):
                self.assertFalse(any(sc.zones_overlap(p, V) for V in allv), (L, p))


class DateLogicTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.T = sc.load_thresholds()
        cls.A = sc.load_adoption()

    def inforce(self, family, rows):
        dates = np.array([D(r[0]) for r in rows], dtype="datetime64[D]")
        dod = np.array([r[1] for r in rows])
        agency = np.array([r[2] for r in rows], dtype=object)
        return sc.in_force(self.T, family, dates, dod, agency, self.A)

    def test_sat_by_date_sector_and_agency(self):
        va, doj, dod = "Department of Veterans Affairs", "Department of Justice", "Department of Defense"
        got = self.inforce("simplified_acquisition", [
            ("2010-09-30", False, va), ("2010-10-01", False, va),          # Oct 1 inflation change
            ("2018-03-21", False, va), ("2018-03-22", False, va),          # VA's own date
            ("2018-06-01", False, doj),                                    # no documented date: transition, unknown
            ("2020-10-02", False, doj),                                    # after FY2020: new value
            ("2018-04-12", True, dod), ("2018-04-13", True, dod),          # Defense date
            ("2025-09-30", True, dod), ("2025-10-01", False, va)])
        exp = [100_000, 150_000, 150_000, 250_000, np.nan, 250_000, 150_000, 250_000, 250_000, 350_000]
        np.testing.assert_array_equal(got, exp)

    def test_mpt_uses_its_own_agency_date(self):
        com = "Department of Commerce"                                     # SAT 2019-06-13, MPT 2020-05-15
        self.assertEqual(self.inforce("micro_purchase", [("2019-12-01", False, com)])[0], 3_500)
        self.assertEqual(self.inforce("simplified_acquisition", [("2019-12-01", False, com)])[0], 250_000)
        self.assertEqual(self.inforce("micro_purchase", [("2017-08-01", True, "Department of Defense")])[0], 5_000)

    def test_tina_2026_change_is_defense_only(self):
        got = self.inforce("cost_or_pricing_data", [("2026-07-01", True, "x"), ("2026-07-01", False, "x"), ("2026-06-30", True, "x")])
        np.testing.assert_array_equal(got, [10_000_000, 2_500_000, 2_500_000])

    def test_no_line_before_it_existed(self):
        self.assertEqual(self.inforce("8a_sole_source_ja", [("2010-01-01", False, "x")])[0], 0)

    def test_status(self):
        self.assertEqual(sc.status_of(100, 95, 0)[0], "active")
        self.assertEqual(sc.status_of(100, 0, 0)[0], "inactive")
        self.assertEqual(sc.status_of(100, 0, 30)[0], "partial")
        self.assertEqual(sc.status_of(100, 50, 0)[0], "partial")

    def test_change_windows(self):
        ch = [c for c in sc.changes(self.T, "simplified_acquisition", "defense") if c["new"] == 250_000][0]
        self.assertFalse(ch["agency_dated"])
        (b0, b1), (a0, a1) = sc.change_windows(ch)
        self.assertEqual((b0, b1, a0, a1), (D("2016-04-13"), D("2018-04-13"), D("2018-04-13"), D("2020-04-13")))
        # windows stop at the neighbouring change: Defense MPT $3,500 -> $5,000 (2017-07-13), then $10,000 on 2018-08-31
        m = [c for c in sc.changes(self.T, "micro_purchase", "defense") if c["new"] == 5_000][0]
        (b0, b1), (a0, a1) = sc.change_windows(m)
        self.assertEqual((b0, a1), (D("2015-10-01"), D("2018-08-31")))

    def test_agency_dated_windows(self):
        ch = [dict(c, family="simplified_acquisition") for c in sc.changes(self.T, "simplified_acquisition", "civilian")
              if c["new"] == 250_000][0]
        self.assertTrue(ch["agency_dated"])
        dates = np.array([D("2018-01-01")] * 2, dtype="datetime64[D]")
        (b0, b1), (a0, a1) = sc.change_windows(ch, dates, np.array(["Department of Veterans Affairs", "Department of Justice"],
                                                                    dtype=object), self.A)
        self.assertEqual((b0[0], b1[0], a0[0], a1[0]), (D("2016-03-22"), D("2018-03-22"), D("2018-03-22"), D("2020-03-22")))
        self.assertEqual((b0[1], b1[1], a0[1], a1[1]), (D("2015-10-01"), D("2017-10-01"), D("2020-10-01"), D("2022-10-01")))

    def test_all_sector_changes_only_when_both_sectors_agree(self):
        self.assertEqual(sc.changes(self.T, "simplified_acquisition", "all"), [])
        olds = [c["old"] for c in sc.changes(self.T, "cost_or_pricing_data", "all")]
        self.assertNotIn(2_500_000, olds)                                  # the 2026 $10M move is Defense only


if __name__ == "__main__":
    unittest.main()
