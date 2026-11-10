"""Offline tests for scripts/23_drivers.py on small synthetic tables (no files from data/)."""
import importlib.util
import os
import sys
import unittest

import numpy as np
import pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))
_spec = importlib.util.spec_from_file_location("drivers23", os.path.join(REPO, "scripts", "23_drivers.py"))
d23 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(d23)


def base_table(rows, fy):
    """a tiny base_actions table with the columns 23_drivers.py reads."""
    t = pd.DataFrame(rows, columns=["awarding_office_code", "product_or_service_code", "vendor_id", "action_date"])
    n = len(t)
    t["action_date"] = pd.to_datetime(t.action_date)
    t["awarding_agency_name"] = "Test Agency"
    t["awarding_sub_agency_name"] = "Test Component"
    t["award_kind"] = "PO"
    t["fy"] = fy
    t["solicitation_identifier"] = ["SOL1", ""] * (n // 2) + ["SOL1"] * (n % 2)
    t["naics_code"] = "541511"
    t["extent_competed_code"] = "F"
    t["solicitation_procedures_code"] = "SP1"
    t["type_of_set_aside_code"] = "NONE"
    t["base_and_all_options_value"] = 5000.0
    t["type_of_contract_pricing_code"] = "J"
    t["commercial_item_acquisition_procedures_code"] = "A"
    t["is_dod"] = False
    t["eval_set"] = True
    t["single"] = False
    return t


def planted_sample(n=30_000, seed=7):
    """synthetic model sample: 'small business' set-asides raise the log-odds of one offer by 1.0; award kind has no effect."""
    rng = np.random.default_rng(seed)
    office = rng.integers(0, 300, n)
    office_effect = rng.normal(0, 0.3, 300)[office]
    sa = np.where(rng.random(n) < 0.4, "small business", "no set-aside")
    kind = np.where(rng.random(n) < 0.3, "DEF", "PO")
    fy = rng.choice([2020, 2021, 2022], n)
    eta = -1.2 + 1.0 * (sa == "small business") + office_effect + 0.1 * (fy == 2022)
    y = rng.random(n) < 1 / (1 + np.exp(-eta))
    s = pd.DataFrame({"setaside_grp": sa, "award_kind": kind, "office": [f"O{i}" for i in office], "org": "Test Agency",
                      "time_ctrl": [f"civ FY{x}" for x in fy], "fy": fy.astype(str), "day": rng.integers(1, 29, n), "single": y})
    return s, eta


class TimingTests(unittest.TestCase):
    def test_september_flags(self):
        dates = pd.Series(pd.to_datetime(["2023-10-01", "2024-03-31", "2024-08-31", "2024-09-01", "2024-09-23", "2024-09-24",
                                          "2024-09-30"]))
        t = d23.timing_flags(dates)
        self.assertEqual(list(t.fquarter), ["Q1", "Q2", "Q4", "Q4", "Q4", "Q4", "Q4"])
        self.assertEqual(list(t.september == "September"), [False, False, False, True, True, True, True])
        self.assertEqual(list(t.last_week == "September 24-30"), [False, False, False, False, False, True, True])
        self.assertEqual(list(t.timing), ["Q1 Oct-Dec", "Q2 Jan-Mar", "Q4 Jul-Aug", "September 1-23", "September 1-23",
                                          "September 24-30", "September 24-30"])


class IncumbencyTests(unittest.TestCase):
    def test_uses_only_earlier_years(self):
        # FY2021: company V1 wins from office O1 for R499.  FY2022: V1 again (incumbent), V2 twice in the same year (not an incumbent:
        # a win earlier in the SAME year does not count), V3 at a different office.  FY2023: V2 again (incumbent, because of FY2022).
        y21 = base_table([("O1", "R499", "V1", "2020-11-01")], 2021)
        y22 = base_table([("O1", "R499", "V1", "2021-12-01"), ("O1", "R499", "V2", "2021-10-05"), ("O1", "R499", "V2", "2022-05-05"),
                          ("O2", "R499", "V1", "2022-06-01"), ("O1", "R499", "", "2022-06-02")], 2022)
        y23 = base_table([("O1", "R499", "V2", "2022-11-01"), ("O1", "R4991", "V1", "2023-01-01")], 2023)
        _, k21 = d23.add_factors(y21)
        f22, k22 = d23.add_factors(y22, [k21, np.array([], np.uint64), np.array([], np.uint64)])
        self.assertEqual(list(f22.incumbent.cat.add_categories("missing").fillna("missing")), ["yes", "no", "no", "no", "missing"])
        f23, _ = d23.add_factors(y23, [k22, k21, np.array([], np.uint64)])
        self.assertEqual(list(f23.incumbent), ["yes", "yes"])     # V1 matches on the first 4 characters of the product code
        # a later year's wins are never used: FY2021 scored with only its own (absent) history has no incumbents
        f21, _ = d23.add_factors(y21, [np.array([], np.uint64)] * 3)
        self.assertEqual(list(f21.incumbent), ["no"])
        # without the three previous years, incumbency is missing, not "no"
        f22_none, _ = d23.add_factors(y22, None)
        self.assertTrue(f22_none.incumbent.isna().all())

    def test_workload_counts_awards_per_office(self):
        rows = [("BIG", "R499", f"V{i}", "2022-01-01") for i in range(120)] + [("SMALL", "R499", "V1", "2022-01-01")]
        f, _ = d23.add_factors(base_table(rows, 2022))
        self.assertEqual(f.workload_band.iloc[0], "100-499")
        self.assertEqual(f.workload_band.iloc[-1], "1-99")


class ModelTests(unittest.TestCase):
    def test_planted_factor_recovered(self):
        s, eta = planted_sample()
        me, fit = d23.fit_scope(s, ["setaside_grp", "award_kind"], ["org", "time_ctrl"], [])
        sb = me[(me.factor == "setaside_grp") & (me.level == "small business")].iloc[0]
        self.assertEqual(sb.reference, "no set-aside")
        # true average marginal effect of the planted factor
        is_sb = s.setaside_grp.to_numpy() == "small business"
        e0 = eta - 1.0 * is_sb
        true = np.mean(1 / (1 + np.exp(-(e0 + 1.0))) - 1 / (1 + np.exp(-e0)))
        self.assertGreater(sb.ci_lo, 0)
        self.assertAlmostEqual(sb.ame, true, delta=0.03)
        kind = me[me.factor == "award_kind"].iloc[0]
        self.assertLess(abs(kind.ame), 0.02)
        self.assertGreater(fit["auc_in_sample"], 0.6)

    def test_matches_statsmodels_cluster(self):
        import statsmodels.api as sm
        s, _ = planted_sample(n=4_000, seed=3)
        spec = d23.level_spec(s, ["setaside_grp", "award_kind"])
        X, names = d23.design(s, spec)
        y = s.single.to_numpy(float)
        beta, H = d23.fit_logit(X, y, ridge=0.0)
        V, _ = d23.cluster_cov(X, y, beta, H, s.office.to_numpy())
        ref = sm.Logit(y, X.toarray()).fit(disp=0, cov_type="cluster", cov_kwds={"groups": pd.factorize(s.office)[0]})
        np.testing.assert_allclose(beta, ref.params, rtol=1e-6, atol=1e-8)
        np.testing.assert_allclose(np.sqrt(np.diag(V)), ref.bse, rtol=1e-4)

    def test_blocks_rank_planted_block_first(self):
        s, _ = planted_sample(n=20_000, seed=11)
        bt = d23.block_importance(s, {"set-aside": ["setaside_grp"], "award type": ["award_kind"]}, [], test_year=2022, n_boot=20)
        self.assertEqual(bt.block.iloc[0], "set-aside")
        self.assertGreater(bt.log_loss_increase_without.iloc[0], 0)
        one = d23.block_importance(s, {"set-aside": ["setaside_grp"]}, [], test_year=None, n_boot=10)
        self.assertIn("even days", one.split.iloc[0])


class ShareTests(unittest.TestCase):
    def test_counts_match_share_table(self):
        from atlas_lib import share_table
        rng = np.random.default_rng(5)
        df = pd.DataFrame({"g": rng.choice(list("abc"), 1000), "single": rng.random(1000) < 0.3})
        a = share_table(df, "g")
        b = d23.share_from_counts(df.groupby("g")["single"].agg(k_single="sum", n_eval="count").reset_index())
        np.testing.assert_allclose(a[["share", "ci_lo", "ci_hi"]].to_numpy(), b[["share", "ci_lo", "ci_hi"]].to_numpy())

    def test_incomplete_defense_year(self):
        months = pd.Series(pd.to_datetime([f"2026-{m:02d}-15" for m in [10, 11, 12, 1, 2, 3, 4, 5, 6, 7, 8] for _ in range(10)]))
        self.assertTrue(d23.dod_incomplete(months, np.ones(len(months), bool)))
        full = pd.concat([months, pd.Series(pd.to_datetime(["2026-09-15"] * 20))], ignore_index=True)
        self.assertFalse(d23.dod_incomplete(full, np.ones(len(full), bool)))


if __name__ == "__main__":
    unittest.main()
