"""Offline tests for scripts/24_outcomes.py with synthetic data in which the true effect of a single offer is known (no files from data/)."""
import importlib.util
import json
import os
import tempfile
import unittest

import numpy as np
import pandas as pd
import statsmodels.api as sm

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("outcomes24", os.path.join(REPO, "scripts", "24_outcomes.py"))
o24 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(o24)

TRUE_EFFECT = 0.10


def confounded_table(seed=7, n_cells=60, per_cell=80):
    """Awards in cells.  Cells with a high outcome also have many single-offer awards, so the raw comparison is badly biased;
    inside a cell the true effect of a single offer is TRUE_EFFECT."""
    rng = np.random.default_rng(seed)
    rows = []
    for c in range(n_cells):
        level = rng.normal(0, 1)                                    # the cell's own level of the outcome
        p_single = 1 / (1 + np.exp(-(1.5 * level - 0.5)))             # ... which also drives how often there is one offer
        single = (rng.random(per_cell) < p_single).astype(float)
        y = level + TRUE_EFFECT * single + rng.normal(0, 0.3, per_cell)
        rows.append(pd.DataFrame({"agency": f"A{c % 5}", "psc4": f"P{c}", "office": [f"OFF{c % 12}-{k % 3}" for k in range(per_cell)],
                                  "single": single, "y": y}))
    return pd.concat(rows, ignore_index=True)


class EstimatorTests(unittest.TestCase):
    def test_recovers_known_effect_despite_confounding(self):
        df = confounded_table()
        raw = df.y[df.single == 1].mean() - df.y[df.single == 0].mean()
        self.assertGreater(raw, 0.5)                                 # the raw comparison is far from the truth
        r = o24.fe_estimate(df, "y", ["agency", "psc4"], "office")
        self.assertAlmostEqual(r["coef"], TRUE_EFFECT, delta=0.03)
        self.assertLess(r["lo"], TRUE_EFFECT)
        self.assertGreater(r["hi"], TRUE_EFFECT)
        self.assertLess(r["hi"] - r["lo"], 0.1)

    def test_no_effect_gives_an_interval_around_zero(self):
        df = confounded_table(seed=11)
        df["placebo"] = np.random.default_rng(3).normal(0, 1, len(df))
        r = o24.fe_estimate(df, "placebo", ["agency", "psc4"], "office")
        self.assertLess(r["lo"], 0)
        self.assertGreater(r["hi"], 0)

    def test_cells_without_variation_are_dropped(self):
        df = confounded_table()
        base = o24.fe_estimate(df, "y", ["agency", "psc4"], "office")
        all_single = pd.DataFrame({"agency": "A0", "psc4": "ONLY_SINGLE", "office": "OFF0-0", "single": 1.0, "y": [1e6] * 40})
        all_multi = pd.DataFrame({"agency": "A0", "psc4": "ONLY_MULTI", "office": "OFF0-1", "single": 0.0, "y": [-1e6] * 40})
        r = o24.fe_estimate(pd.concat([df, all_single, all_multi], ignore_index=True), "y", ["agency", "psc4"], "office")
        self.assertEqual(r["n"], base["n"])                          # the 80 awards in cells without variation take no part
        self.assertEqual(r["n_cells"], base["n_cells"])
        self.assertAlmostEqual(r["coef"], base["coef"], places=12)   # ... however extreme their outcomes are
        self.assertAlmostEqual(r["se"], base["se"], places=12)

    def test_unknown_outcomes_take_no_part_and_minimum_cell_size(self):
        x = np.array([1, 0, 1, 0, 1, 0, 0], dtype=float)
        y = np.array([2.0, 1.0, 5.0, np.nan, 9.0, 7.0, 8.0])
        cell = np.array([0, 0, 1, 1, 2, 2, 2])
        xd, yd, keep, good = o24.within_cells(x, y, cell)
        self.assertEqual(list(good), [True, False, True])            # cell 1 has one known award left: no variation
        self.assertEqual(list(keep), [True, True, False, False, True, True, True])
        np.testing.assert_allclose(xd[keep], [0.5, -0.5, 2 / 3, -1 / 3, -1 / 3])
        np.testing.assert_allclose(yd[keep], [0.5, -0.5, 1.0, -1.0, 0.0])
        _, _, keep3, good3 = o24.within_cells(x, y, cell, min_cell=3)
        self.assertEqual(list(good3), [False, False, True])

    def test_same_numbers_as_statsmodels_two_way_clustered_ols(self):
        for seed in (5, 6, 7):                                       # in seed 7 the office-only variance is the larger one
            with self.subTest(seed=seed):
                df = confounded_table(seed=seed, n_cells=30, per_cell=40)
                r = o24.fe_estimate(df, "y", ["agency", "psc4"], "office")
                cell = pd.factorize(df.agency + "|" + df.psc4)[0]
                xd, yd, keep, _ = o24.within_cells(df.single.to_numpy(), df.y.to_numpy(), cell)
                office = pd.factorize(df.office[keep])[0]
                model = sm.OLS(yd[keep], xd[keep].reshape(-1, 1))
                fits = [model.fit(cov_type="cluster", cov_kwds={"groups": groups}, use_t=True)
                        for groups in (office, np.column_stack([office, cell[keep]]))]
                fit = max(fits, key=lambda f: float(f.bse[0]))       # two-way, and never less than by office alone
                self.assertAlmostEqual(r["coef"], float(fit.params[0]), places=10)
                self.assertAlmostEqual(r["se"], float(fit.bse[0]), places=10)
                if fit is fits[1]:                                   # same degrees of freedom: the interval must match too
                    self.assertAlmostEqual(r["lo"], float(fit.conf_int()[0][0]), places=8)
                    self.assertAlmostEqual(r["hi"], float(fit.conf_int()[0][1]), places=8)
                    self.assertAlmostEqual(r["p"], float(fit.pvalues[0]), places=8)

    def test_small_cells_spread_over_offices_get_honest_standard_errors(self):
        """2,000 cells of two awards (one single offer, one not), each award in a random office, pure noise outcomes.
        The estimate is the mean of 2,000 differences, so its true standard error is sqrt(2 / 2000).
        Clustering by office alone would report about 0.71 of that; clustering by office and cell must report all of it."""
        rng = np.random.default_rng(0)
        n = 2000
        t = pd.DataFrame({"cell": np.repeat(np.arange(n), 2), "single": np.tile([1.0, 0.0], n), "office": rng.integers(0, 400, 2 * n),
                          "y": rng.normal(0, 1, 2 * n)})
        r = o24.fe_estimate(t, "y", ["cell"], "office")
        self.assertAlmostEqual(r["se"], np.sqrt(2 / n), delta=0.1 * np.sqrt(2 / n))
        self.assertTrue(r["lo"] < 0 < r["hi"])
        xd, yd, keep, _ = o24.within_cells(t.single.to_numpy(), t.y.to_numpy(), t.cell.to_numpy())
        by_office = pd.DataFrame({"o": t.office, "sxx": xd ** 2, "sxy": xd * yd}).groupby("o").sum()
        office_only = o24.cluster_estimate(by_office.sxx.to_numpy(), by_office.sxy.to_numpy())
        self.assertLess(office_only["se"], 0.8 * np.sqrt(2 / n))

    def test_too_little_data_gives_no_estimate(self):
        r = o24.cluster_estimate([1.0], [0.5])
        self.assertTrue(np.isnan(r["coef"]))


class PlanTests(unittest.TestCase):
    def test_a_cohort_is_complete_only_when_every_follow_up_year_is_present(self):
        plan = o24.cohort_plan([2008, 2009, 2010, 2011, 2014], [2008, 2009, 2010, 2011, 2012, 2014, 2015])
        self.assertEqual(plan["t to t+3"], [2008, 2009])             # 2010 would need 2013
        self.assertEqual(plan["t to t+1"], [2008, 2009, 2010, 2011, 2014])
        self.assertEqual(o24.cohort_plan([2018], [2018])["t to t+3"], [])

    def test_span(self):
        self.assertEqual(o24.span([2008, 2009, 2010, 2014]), "FY2008-FY2010, FY2014")
        self.assertEqual(o24.span([]), "none")


# ---- a small synthetic copy of the yearly tables -----------------------------------------------------------------------------------------
BUREAUS = [("Agency A", "Bureau A1", False), ("Agency A", "Bureau A2", False), ("General Services Administration", "Bureau G1", False),
           ("Department of Defense", "Army", True), ("Department of Defense", "Navy", True)]
PSC = ["7030", "R425", "Y1AA"]
PSC_LEVEL = {"7030": 0.00, "R425": 0.10, "Y1AA": 0.25}
RATIO_EFFECT, SCOPE_EFFECT, GROWTH_EFFECT = 0.20, 0.10, 0.15


def make_history(folder, cohorts=(2001, 2002, 2003, 2004), last_year=2006, n=5000, seed=1, value_sum=True):
    """Write base_actions_FY*.parquet and award_year_FY*.parquet to `folder`; return the award-level truth as one table."""
    rng = np.random.default_rng(seed)
    truth, years = [], {y: [] for y in range(min(cohorts), last_year + 1)}
    for fy in cohorts:
        b = rng.integers(0, len(BUREAUS), n)
        psc = rng.choice(PSC, n)
        level = np.array([PSC_LEVEL[p] for p in psc])
        single = rng.random(n) < 0.15 + 1.5 * level                 # product codes with high outcomes draw one offer more often
        value = np.round(np.exp(rng.normal(11.3, 0.9, n)), 2)        # around $80,000; some below the floors
        kind = np.where(rng.random(n) < 0.1, "DEF", np.where(rng.random(n) < 0.1, "IDV", "PO"))
        eval_set = rng.random(n) < 0.9
        day = pd.Timestamp(f"{fy - 1}-10-01") + pd.to_timedelta(rng.integers(0, 180, n), unit="D")      # two quarters
        has_options = rng.random(n) < 0.3
        t = pd.DataFrame({
            "contract_award_unique_key": [f"KEY_{fy}_{i}" for i in range(n)],
            "awarding_agency_name": pd.Categorical([BUREAUS[k][0] for k in b]),
            "awarding_sub_agency_name": pd.Categorical([BUREAUS[k][1] for k in b]),
            "awarding_office_code": [f"OFF{k}{j}" for k, j in zip(b, rng.integers(0, 6, n))],
            "award_kind": pd.Categorical(kind), "eval_set": eval_set, "single": single,
            "is_dod": np.array([BUREAUS[k][2] for k in b]), "action_date": day, "product_or_service_code": psc,
            "base_and_all_options_value": value, "base_and_exercised_options_value": np.where(has_options, value / 2, value),
            "period_of_performance_start_date": day.strftime("%Y-%m-%d 00:00:00"),
            "period_of_performance_potential_end_date": (day + pd.Timedelta(days=400)).strftime("%Y-%m-%d 00:00:00"),
        })
        t.to_parquet(os.path.join(folder, f"base_actions_FY{fy}.parquet"), index=False)
        # what happens after award.  Years t and t+1: 70% of the value is obligated, whoever won.  Year t+2: the rest, and here a
        # single offer adds RATIO_EFFECT.  Scope changes come in year t+2; one single large increase in value comes in year t+3.
        ratio = 0.9 + RATIO_EFFECT * single + level + rng.normal(0, 0.05, n)
        scope = (rng.random(n) < 0.2 + SCOPE_EFFECT * single + level).astype(int)
        growth = 0.1 + GROWTH_EFFECT * single + level + rng.normal(0, 0.05, n)
        option = (has_options & (rng.random(n) < 0.5)).astype(int)
        zero = np.zeros(n)
        plan = {0: (value * 0.5, value, zero, zero, zero), 1: (value * 0.2, zero, zero, option, zero),
                2: (value * (ratio - 0.7), zero, scope, zero, zero), 3: (zero, value * growth, zero, zero, value * growth)}
        for h, (obligation, vmax, n_scope, n_option, change) in plan.items():
            if fy + h <= last_year:
                years[fy + h].append(pd.DataFrame({
                    "contract_award_unique_key": t.contract_award_unique_key, "n_tx": 1, "obligation_sum": obligation, "value_max": vmax,
                    "value_last": vmax, "n_options": n_option, "n_scope_changes": n_scope.astype(int), "n_terminations": 0,
                    "value_sum": (value if h == 0 else change)}))
        truth.append(t.assign(fy=fy, ratio=ratio, scope=scope, growth=growth))
    for y, parts in years.items():
        a = pd.concat(parts, ignore_index=True).sample(frac=1, random_state=y)          # shuffled, as the real tables are not sorted
        a.drop(columns=[] if value_sum else ["value_sum"]).to_parquet(os.path.join(folder, f"award_year_FY{y}.parquet"), index=False)
    return pd.concat(truth, ignore_index=True)


def analysis_table(truth, cohorts, floor):
    """The awards the script should use, built here directly from the truth, with the cell columns of the main design."""
    t = truth[truth.eval_set & truth.award_kind.isin(["PO", "DEF"]) & (truth.base_and_all_options_value >= floor)
              & truth.fy.isin(cohorts)].copy()
    t["bureau"] = t.awarding_agency_name.astype(str) + "|" + t.awarding_sub_agency_name.astype(str)
    t["quarter"] = ((t.action_date.dt.month - 10) % 12) // 3
    t["bin"] = np.searchsorted(o24.BIN_EDGES, t.base_and_all_options_value, side="right")
    t["single"] = t.single.astype(float)
    t["ratio_w"] = t.ratio.clip(0, 3)
    t["scope_any"] = (t.scope > 0).astype(float)
    return t


CELLS = ["awarding_office_code", "fy", "quarter", "award_kind", "product_or_service_code", "bin"]


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.hist = os.path.join(cls.tmp.name, "history")
        cls.out = os.path.join(cls.tmp.name, "results")
        os.makedirs(cls.hist)
        cls.truth = make_history(cls.hist)
        cls.est, cls.by_cohort, cls.summary = o24.run(cls.hist, cls.out, os.path.join(cls.tmp.name, "outcomes.png"), quiet=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def row(self, outcome, follow_up="t to t+3", scope="all sectors"):
        e = self.est[(self.est.outcome == outcome) & (self.est.follow_up == follow_up) & (self.est.scope == scope)]
        self.assertEqual(len(e), 1)
        return e.iloc[0]

    def test_cohorts_used(self):
        self.assertEqual(self.summary["follow_ups"]["t to t+3"]["cohorts"], [2001, 2002, 2003])      # 2004 would need 2007
        self.assertEqual(self.summary["follow_ups"]["t to t+1"]["cohorts"], [2001, 2002, 2003, 2004])
        self.assertEqual(self.summary["follow_ups"]["t to t+3"]["cohorts_ending_in_latest_year"], [2003])
        self.assertEqual(self.summary["headline_follow_up"], "t to t+3")

    def test_recovers_the_true_effects(self):
        r = self.row("oblig_ratio_w")
        self.assertAlmostEqual(r.coef, RATIO_EFFECT, delta=0.01)
        self.assertTrue(r.lo < RATIO_EFFECT < r.hi)
        self.assertGreater(r.raw_mean_single - r.raw_mean_multi, RATIO_EFFECT + 0.03)       # the raw gap is pushed up by product mix
        r = self.row("any_scope")
        self.assertAlmostEqual(r.coef, SCOPE_EFFECT, delta=0.04)
        self.assertTrue(r.lo < SCOPE_EFFECT < r.hi)
        r = self.row("value_growth_w")                              # only there because the synthetic tables carry value_sum
        self.assertAlmostEqual(r.coef, GROWTH_EFFECT, delta=0.01)
        self.assertAlmostEqual(self.row("value_jump_w").coef, GROWTH_EFFECT, delta=0.01)   # one single increase: the lower bound is exact

    def test_nothing_happens_before_it_happens(self):
        # over t to t+1 every award has obligated exactly 70% of its value, had no scope change and no growth
        for outcome in ("oblig_ratio_w", "any_scope", "value_jump_w", "overrun25", "any_termination"):
            self.assertAlmostEqual(self.row(outcome, "t to t+1").coef, 0.0, places=9)
        self.assertAlmostEqual(self.row("oblig_ratio_w", "t to t+1").raw_mean_single, 0.7, places=9)

    def test_placebo_and_options(self):
        for placebo in ("placebo_even_day", "placebo_coin"):
            r = self.row(placebo)
            self.assertTrue(r.lo < 0 < r.hi)
            self.assertAlmostEqual(r.mean_multi_offer, 0.5, delta=0.05)
        r = self.row("option_exercised")                            # options were exercised with the same chance whoever won
        self.assertLess(abs(r.coef), 3 * r.se)
        self.assertLess(r.n_all, self.row("any_scope").n_all * 0.4)  # only the awards that had options

    def test_pooled_estimate_equals_the_direct_estimate(self):
        """Adding up per-office sums cohort by cohort must give the same numbers as one regression on all awards."""
        t = analysis_table(self.truth, [2001, 2002, 2003], o24.FLOOR)
        for outcome, column in (("oblig_ratio_w", "ratio_w"), ("any_scope", "scope_any")):
            direct = o24.fe_estimate(t, column, CELLS, "awarding_office_code")
            r = self.row(outcome)
            self.assertAlmostEqual(r.coef, direct["coef"], places=6)
            self.assertAlmostEqual(r.se, direct["se"], places=6)
            self.assertEqual(r.n_awards, direct["n"])
            self.assertEqual(r.n_cells, direct["n_cells"])
            self.assertEqual(r.n_offices, direct["n_offices"])
            self.assertEqual(r.n_all, len(t))

    def test_sector_band_and_floor_rows_are_exact_subsets(self):
        t = analysis_table(self.truth, [2001, 2002, 2003], o24.FLOOR)
        cases = {"Department of Defense": t[t.is_dod], "GSA": t[t.awarding_agency_name == "General Services Administration"],
                 "$100K-$250K": t[t.base_and_all_options_value.between(1e5, 2.5e5, inclusive="left")],
                 "size floor $100,000": t[t.base_and_all_options_value >= 1e5],
                 "size floor $10,000": analysis_table(self.truth, [2001, 2002, 2003], 10_000)}
        for scope, part in cases.items():
            with self.subTest(scope=scope):
                direct = o24.fe_estimate(part, "ratio_w", CELLS, "awarding_office_code")
                r = self.row("oblig_ratio_w", scope=scope)
                self.assertAlmostEqual(r.coef, direct["coef"], places=6)
                self.assertAlmostEqual(r.se, direct["se"], places=6)
                self.assertEqual(r.n_awards, direct["n"])

    def test_checks_use_their_own_cells(self):
        t = analysis_table(self.truth, [2001, 2002, 2003], o24.FLOOR)
        direct = o24.fe_estimate(t, "ratio_w", ["awarding_office_code", "fy", "quarter", "award_kind", "bin"], "awarding_office_code")
        r = self.row("oblig_ratio_w", scope="cells: " + o24.SPECS["no product code"][1])
        self.assertAlmostEqual(r.coef, direct["coef"], places=6)
        self.assertEqual(r.n_awards, direct["n"])
        self.assertGreater(r.coef, RATIO_EFFECT + 0.03)              # without the product code the estimate is biased, as built

    def test_by_cohort_table(self):
        c = self.by_cohort[(self.by_cohort.outcome == "oblig_ratio_w") & (self.by_cohort.scope == "all sectors")]
        self.assertEqual(sorted(c[c.follow_up == "t to t+3"].cohort_fy), [2001, 2002, 2003])
        self.assertEqual(sorted(c[c.follow_up == "t to t+1"].cohort_fy), [2001, 2002, 2003, 2004])
        for r in c[c.follow_up == "t to t+3"].itertuples():
            self.assertAlmostEqual(r.coef, RATIO_EFFECT, delta=0.02)
        self.assertEqual(int(c[c.follow_up == "t to t+3"].n_awards.sum()), int(self.row("oblig_ratio_w").n_awards))

    def test_outputs_name_nothing_below_agency_level(self):
        names = ["outcomes_estimates.csv", "outcomes_by_cohort.csv", "outcomes_summary.json"]
        text = ""
        for name in names:
            with open(os.path.join(self.out, name), encoding="utf-8") as f:
                text += f.read()
        for private in ("OFF", "KEY_", "Bureau A1", "Army", "Navy"):
            self.assertNotIn(private, text)
        self.assertIn("Department of Defense", text)
        with open(os.path.join(self.out, "outcomes_summary.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["design"]["size_floor"], o24.FLOOR)
        self.assertGreater(os.path.getsize(os.path.join(self.tmp.name, "outcomes.png")), 10_000)


class FollowUpTests(unittest.TestCase):
    def test_follow_up_totals_and_the_largest_single_increase(self):
        """Four awards of $100 each, checked by hand.  The tables here have no value_sum column, as the published ones."""
        keys = ["A", "B", "C", "D"]
        d = pd.DataFrame({"key": keys, "value0": 100.0})
        cols = ["contract_award_unique_key", "n_tx", "obligation_sum", "value_max", "value_last", "n_options", "n_scope_changes",
                "n_terminations"]
        year0 = pd.DataFrame([["A", 1, 100.0, 100.0, 100.0, 0, 0, 0],      # the award alone
                              ["B", 2, 90.0, 100.0, 40.0, 0, 1, 0],        # a later change of +40 is the last action of the year
                              ["C", 3, 300.0, 250.0, 0.0, 0, 2, 0],        # a change of +250, larger than the award itself
                              ["D", 2, 100.0, 100.0, 100.0, 0, 0, 0],      # last action equal to the award: taken as the award
                              ["someone else", 1, 5.0, 5.0, 5.0, 0, 0, 0]], columns=cols)
        year1 = pd.DataFrame([["D", 1, -50.0, -50.0, -50.0, 0, 0, 1], ["A", 2, 20.0, 30.0, 0.0, 1, 0, 0]], columns=cols)
        with tempfile.TemporaryDirectory() as tmp:
            year0.to_parquet(os.path.join(tmp, "award_year_FY2001.parquet"), index=False)
            year1.to_parquet(os.path.join(tmp, "award_year_FY2002.parquet"), index=False)
            fu = o24.follow_up(tmp, d, 2001, [0, 1])
        self.assertNotIn("value_sum", fu[1])
        self.assertEqual(list(fu[0].jump), [0.0, 40.0, 250.0, 0.0])
        self.assertEqual(list(fu[1].jump), [30.0, 40.0, 250.0, 0.0])
        self.assertEqual(list(fu[0].obligations), [100.0, 90.0, 300.0, 100.0])
        self.assertEqual(list(fu[1].obligations), [120.0, 90.0, 300.0, 50.0])
        self.assertEqual(list(fu[1].scope), [0, 1, 2, 0])
        self.assertEqual(list(fu[1].terminations), [0, 0, 0, 1])
        self.assertEqual(list(fu[1].options), [1, 0, 0, 0])
        y = o24.outcome_table(d.assign(has_options=[1.0, 0.0, 0.0, 0.0], even_day=0.0, coin=1.0, planned_years=1.0, month=1), fu[1])
        self.assertNotIn("value_growth_w", y)
        self.assertEqual(list(y["value_jump25"]), [1.0, 1.0, 1.0, 0.0])
        self.assertEqual(list(y["value_jump_w"]), [0.3, 0.4, 2.5, 0.0])
        self.assertEqual(list(y["overrun25"]), [0.0, 0.0, 1.0, 0.0])
        self.assertEqual(list(y["oblig_ratio_w"]), [1.2, 0.9, 3.0, 0.5])
        self.assertEqual(y["option_exercised"][0], 1.0)
        self.assertTrue(np.isnan(y["option_exercised"][1:]).all())       # awards without options are not part of that outcome


if __name__ == "__main__":
    unittest.main()
