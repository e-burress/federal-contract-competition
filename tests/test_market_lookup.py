"""Offline tests for scripts/34_market_lookup.py (the market lookup for buyers) on small synthetic yearly tables, and for its blocks in
build_report.py and its section in build_explorer.py.  Nothing from data/ or results/ is read."""
import csv
import importlib.util
import json
import os
import re
import sys
import tempfile
import unittest
from datetime import date

import numpy as np
import pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, os.path.join(REPO, "scripts", file))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ml = _load("market34", "34_market_lookup.py")
br = _load("build_report_ml", "build_report.py")
bx = _load("build_explorer_ml", "build_explorer.py")


def year_table(fy, n=300, offers=None, naics="541330", psc="R425", setaside=None, dod_share=0.3, gsa_share=0.1, thin_dod_september=False,
               value=50_000.0):
    """one fiscal year of base actions, every row in the eval set unless offers has NaN; dates spread over the year"""
    rng = np.random.default_rng(fy)
    offers = np.asarray(offers if offers is not None else rng.integers(1, 6, n), dtype=float)
    n = len(offers)
    months = np.tile(np.arange(12), n // 12 + 1)[:n]
    dates = [pd.Timestamp(fy - 1 if m < 3 else fy, (m + 9) % 12 + 1, 15) for m in months]
    is_dod = np.arange(n) % 10 < round(10 * dod_share)
    if thin_dod_september:
        is_dod = is_dod & np.array([d.month != 9 for d in dates])
    agency = np.where(is_dod, "Department of Defense", np.where(np.arange(n) % 10 >= 10 - round(10 * gsa_share),
                                                                 "General Services Administration", "Department of the Interior"))
    ok = ~np.isnan(offers)
    return pd.DataFrame({
        "naics_code": naics if isinstance(naics, str) else list(naics), "product_or_service_code": psc if isinstance(psc, str) else list(psc),
        "type_of_set_aside_code": setaside if setaside is not None else np.where(np.arange(n) % 2 == 0, "SBA", "NONE"),
        "offers": offers, "eval_set": ok, "single": ok & (offers == 1), "is_dod": is_dod, "awarding_agency_name": agency,
        "base_and_all_options_value": value, "action_date": dates, "fy": fy,
        "awarding_office_code": "OFFICE1", "vendor_id": "abcdef0123456789", "solicitation_identifier": "SOL-123"})


def write_years(folder, years, **kw):
    files = {}
    for fy in years:
        p = os.path.join(folder, f"base_actions_FY{fy}.parquet")
        (kw.pop("table_for")(fy) if "table_for" in kw else year_table(fy, **kw)).to_parquet(p, index=False)
        files[fy] = p
    return files


class Window(unittest.TestCase):
    def test_choose_window(self):
        w = ml.choose_window(list(range(2008, 2027)), [2026])
        self.assertEqual(w["recent"], [2021, 2022, 2023, 2024, 2025])
        self.assertEqual(w["earlier"], [2016, 2017, 2018, 2019, 2020])
        self.assertEqual(w["left_out_incomplete"], [2026])
        self.assertTrue(w["change_ok"])

    def test_missing_years(self):
        w = ml.choose_window([2016, 2017, 2019, 2020, 2021, 2022, 2023, 2024, 2025], [])
        self.assertEqual(w["missing"], [2018])
        self.assertFalse(w["change_ok"])
        self.assertEqual(w["earlier_present"], [2016, 2017, 2019, 2020])
        self.assertIsNone(ml.choose_window([2026], [2026]))

    def test_incomplete_defense_year_is_left_out(self):
        with tempfile.TemporaryDirectory() as d:
            files = write_years(d, [2024], n=240)
            files.update(write_years(d, [2025], n=240, thin_dod_september=True))
            self.assertEqual(ml.find_incomplete(files, date(2026, 11, 10)), [2025])
            self.assertEqual(ml.find_incomplete(files, date(2025, 6, 1)), [2025])    # not ended yet either


class Statistics(unittest.TestCase):
    def test_hist_quantile_matches_pandas(self):
        rng = np.random.default_rng(1)
        x = pd.Series(rng.integers(1, 30, 501))
        vc = x.value_counts()
        for q in (0.25, 0.5, 0.75, 0.1, 0.9):
            self.assertAlmostEqual(ml.hist_quantile(vc.index.to_numpy(), vc.to_numpy(), q), x.quantile(q))
        self.assertAlmostEqual(ml.hist_quantile([1, 2], [1, 1], 0.5), 1.5)

    def test_wilson_matches_statsmodels(self):
        from statsmodels.stats.proportion import proportion_confint
        lo, hi = proportion_confint(30, 120, method="wilson")
        a, b = ml.wilson(30, 120)
        self.assertAlmostEqual(a, lo)
        self.assertAlmostEqual(b, hi)

    def test_older_code_labels(self):
        titles = {"5132": ("Software Publishers", "2022"), "5112": ("Software Publishers", "2017")}
        self.assertEqual(ml.naics_title("5132", titles), "Software Publishers")
        self.assertEqual(ml.naics_title("5112", titles), "Software Publishers (2017 code)")
        self.assertEqual(ml.naics_title("5181", titles), "industry 5181 (older code)")
        self.assertEqual([ml.older_code(c, titles) for c in ("5132", "5112", "5181")], [False, True, True])
        self.assertFalse(ml.older_code("5181", {}))          # without the titles file nothing is called older

    def test_ranking_leaves_out_older_codes_and_gsa_majorities(self):
        rows = []
        for code, share, older, gsa in (("5132", 0.51, False, 0), ("5112", 0.53, True, 0), ("3256", 0.74, False, 3000),
                                        ("3114", 0.04, False, 0), ("5413", 0.25, False, 0)):
            rows.append({"naics4": code, "title": code, "older_code": older, "scope": "all sectors", "n_eval": 5000, "share": share,
                         "ci_lo": share, "ci_hi": share, "offers_median": 2.0, "registered_primary": 1})
            rows.append({"naics4": code, "title": code, "older_code": older, "scope": "GSA", "n_eval": gsa, "share": share,
                         "ci_lo": share, "ci_hi": share, "offers_median": 2.0, "registered_primary": 1})
        e = ml.extremes(pd.DataFrame(rows), "naics4", k=2)
        self.assertEqual([r["naics4"] for r in e["thinnest"]], ["5132", "5413"])
        self.assertEqual([r["naics4"] for r in e["deepest"]], ["3114", "5413"])
        self.assertEqual((e["n_large"], e["n_ranked"], e["left_out_gsa_majority"], e["left_out_older_code"]), (5, 3, 1, 1))
        saved = br.RESULTS
        with tempfile.TemporaryDirectory() as d:
            try:
                br.RESULTS = d
                with open(os.path.join(d, "market_lookup_summary.json"), "w", encoding="utf-8") as f:
                    json.dump({"window": {"recent_present": [2021, 2025], "earlier": [2016, 2020]}, "min_cell": 100, "groups": {},
                               "all_markets": {"all sectors": {"n_eval": 1, "share": 0.3, "ci_lo": 0.2, "ci_hi": 0.4,
                                                               "offers_median": 2}}, "markets_naics4": e}, f)
                br.clear_caches()
                txt = br.r_market_lookup()
            finally:
                br.RESULTS = saved
                br.clear_caches()
        self.assertIn("Left out of that ranking: 1 group where GSA's very small supply orders are the majority of awards and 1 group recorded "
                      "under an older industry code", txt)

    def test_setaside_groups(self):
        g = ml.setaside_group(pd.Series(["SBA", "8A", "SDVOSBC", "NONE", "", None, "IEE", "HZC"]))
        self.assertEqual(list(g), ["sb", "sb", "sb", "none", "other", "other", "other", "sb"])


class Tables(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.res, self.ref = os.path.join(self.tmp.name, "results"), os.path.join(self.tmp.name, "reference")
        os.makedirs(self.ref)
        with open(os.path.join(self.ref, "naics4_titles.csv"), "w", encoding="utf-8", newline="") as f:
            f.write("naics4,title,vintage,source_url,retrieved\n5413,\"Architectural, Engineering, and Related Services\",2022 NAICS,u,2026-11-10\n")
        with open(os.path.join(self.ref, "market_depth_naics.csv"), "w", encoding="utf-8", newline="") as f:
            f.write("naics,n_primary,n_any,n_small_any,n_size_unknown,extract_date\n541330,100,500,400,0,2026-11-01\n"
                    "541310,20,300,250,0,2026-11-01\n511210,5,9,9,0,2026-11-01\n")

    def tearDown(self):
        self.tmp.cleanup()

    def run_lookup(self, files):
        return ml.run(files, out_dir=self.res, ref_dir=self.ref, today=date(2026, 11, 10), built="2026-11-10T00:00:00Z")

    def test_shares_split_change_and_cell_minimum(self):
        def table_for(fy):
            if fy >= 2021:          # recent: 300 awards in 5413 (one offer on 1 in 3), 60 in 5112 (under the minimum)
                offers = np.array([1, 2, 4] * 100 + [1] * 60, dtype=float)
                naics = ["541330"] * 300 + ["511210"] * 60
                sa = ["SBA" if i % 3 == 0 else "NONE" for i in range(300)] + ["NONE"] * 60    # every one-offer award is set aside
            else:                   # earlier: one offer on 1 in 4
                offers = np.array([1, 2, 3, 5] * 75 + [1] * 60, dtype=float)
                naics = ["541330"] * 300 + ["511210"] * 60
                sa = ["NONE"] * 360
            return year_table(fy, offers=offers, naics=naics, psc="R425", setaside=sa)
        with tempfile.TemporaryDirectory() as d:
            files = {}
            for fy in range(2016, 2026):
                p = os.path.join(d, f"base_actions_FY{fy}.parquet")
                table_for(fy).to_parquet(p, index=False)
                files[fy] = p
            s = self.run_lookup(files)
        self.assertEqual(s["window"]["recent"], [2021, 2022, 2023, 2024, 2025])
        t = pd.read_csv(os.path.join(self.res, "market_lookup_naics4.csv"), dtype={"naics4": str})
        a = t[(t.naics4 == "5413") & (t.scope == "all sectors")].iloc[0]
        self.assertEqual(a.n_eval, 1500)
        self.assertAlmostEqual(a.share, 1 / 3, places=8)
        self.assertEqual((a.offers_p25, a.offers_median, a.offers_p75), (1.0, 2.0, 4.0))
        self.assertAlmostEqual(a.share_one_or_two, 2 / 3)
        self.assertAlmostEqual(a.share_setaside, 1.0)
        self.assertAlmostEqual(a.share_no_setaside, 0.0)
        self.assertAlmostEqual(a.share_earlier, 0.25)
        self.assertAlmostEqual(a.change_points, 100 * (1 / 3 - 0.25))
        self.assertLess(a.change_lo, a.change_points)
        self.assertEqual(a.registered_primary, 120)          # 100 + 20: distinct companies (one main code each)
        self.assertEqual(a.registered_any_largest, 500)       # at least this many list a code of the group
        self.assertEqual(a.registered_any_listings, 800)
        self.assertEqual(a.title, "Architectural, Engineering, and Related Services")
        small = t[(t.naics4 == "5112") & (t.scope == "all sectors")].iloc[0]
        self.assertEqual(small.n_eval, 300)                   # 60 a year: published, it has 300 in the window
        dod = t[(t.naics4 == "5112") & (t.scope == "Department of Defense")]
        self.assertTrue(dod.empty or pd.isna(dod.iloc[0].share))   # under 100: blank, not zero
        self.assertEqual(small.title, "industry 5112 (older code)")
        gsa = t[(t.naics4 == "5413") & (t.scope == "GSA")].iloc[0]
        self.assertEqual(gsa.n_eval, 150)                     # 10% of 300 a year, five years
        self.assertIn("GSA", gsa.change_note)
        # totals by year equal the rows of the eval set
        self.assertEqual(sum(s["eval_by_year"]["2021"].values()), 360)
        # the size band of every award is 25-100K
        self.assertEqual(int(a["n_25-100K"]), 1500)
        self.assertTrue(pd.isna(a["share_<25K"]))

    def test_cell_minimum(self):
        with tempfile.TemporaryDirectory() as d:
            files = write_years(d, range(2016, 2026), n=19, dod_share=0.0, gsa_share=0.0)     # 95 awards in the window
            self.run_lookup(files)
        t = pd.read_csv(os.path.join(self.res, "market_lookup_psc2.csv"), dtype={"psc2": str})
        r = t[t.scope == "all sectors"].iloc[0]
        self.assertEqual(r.n_eval, 95)
        for c in ("share", "ci_lo", "offers_median", "share_one_or_two", "change_points"):
            self.assertTrue(pd.isna(r[c]), c)

    def test_missing_year_blanks_the_change(self):
        with tempfile.TemporaryDirectory() as d:
            files = write_years(d, [y for y in range(2016, 2026) if y != 2017], n=240)
            s = self.run_lookup(files)
        self.assertEqual(s["window"]["missing_years"], [2017])
        self.assertFalse(s["window"]["change_available"])
        t = pd.read_csv(os.path.join(self.res, "market_lookup_naics4.csv"), dtype={"naics4": str})
        self.assertTrue(t.change_points.isna().all())
        self.assertTrue(t.share.notna().any())

    def test_no_identifier_like_content(self):
        with tempfile.TemporaryDirectory() as d:
            files = write_years(d, range(2016, 2026), n=240)
            self.run_lookup(files)
        for name in ("market_lookup_naics4.csv", "market_lookup_psc2.csv", "market_lookup_summary.json"):
            with open(os.path.join(self.res, name), encoding="utf-8") as f:
                text = f.read()
            for bad in ("OFFICE1", "abcdef0123456789", "SOL-123", "office", "vendor", "uei", "piid", "solicitation_identifier"):
                self.assertNotIn(bad, text.lower() if bad.islower() else text, (name, bad))
            self.assertIsNone(re.search(r"\b(?=[0-9a-f]*[a-f])[0-9a-f]{16}\b", text), name)

    def test_report_and_explorer(self):
        with tempfile.TemporaryDirectory() as d:
            files = write_years(d, range(2016, 2026), n=240)
            self.run_lookup(files)
        saved = br.RESULTS
        try:
            br.RESULTS = self.res
            br.clear_caches()
            r, m = br.r_market_lookup(), br.m_market_lookup()
            self.assertIn("FY2021–FY2025", r)
            self.assertIn("*What it cannot show:*", r)
            self.assertIn("| All sectors |", m)
            self.assertNotIn("—", r + m)
            br.RESULTS = os.path.join(self.tmp.name, "nothing")
            br.clear_caches()
            self.assertIn("Not yet available", br.r_market_lookup())
            self.assertIn("Not yet available", br.m_market_lookup())
        finally:
            br.RESULTS = saved
            br.clear_caches()
        out = os.path.join(self.tmp.name, "explorer-data.js")
        data = bx.build(self.res, out, repo="o/r", today=date(2026, 11, 10), reference_dir=self.ref)
        lk = data["lookup"]
        self.assertEqual(lk["years"], [2021, 2025])
        self.assertEqual(lk["scopes"], ["all sectors", "civilian except GSA", "Department of Defense"])
        g = [x for x in lk["groups"] if x[1] == "5413"][0]
        self.assertEqual(g[0], 0)
        self.assertEqual(len(g[5]), 3)
        self.assertIn("market_lookup_summary.json", data["sources"])
        empty = os.path.join(self.tmp.name, "empty")
        os.makedirs(empty)
        self.assertIsNone(bx.build(empty, out, repo="o/r", today=date(2026, 11, 10), reference_dir=self.ref)["lookup"])


if __name__ == "__main__":
    unittest.main()
