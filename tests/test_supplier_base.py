"""Offline tests for scripts/33_supplier_base.py on small synthetic yearly tables (no files from data/)."""
import glob
import hashlib
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))
_spec = importlib.util.spec_from_file_location("supplier33", os.path.join(REPO, "scripts", "33_supplier_base.py"))
sb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sb)


def code(key):
    """a 16-hex-digit code made like the real ones"""
    return hashlib.sha256(("fcc-v1:" + key).encode()).hexdigest()[:16]


def fillers(prefix, n):
    return [code(f"{prefix}{i}") for i in range(n)]


def table(fy, vendors, value=50_000.0, kind="PO", agency="Test Agency", is_dod=False, single=False, link=None):
    """one award per vendor code.  link: {vendor_id: (uei_code, duns_code)} adds the two link columns."""
    n = len(vendors)
    t = pd.DataFrame({
        "awarding_agency_name": [agency] * n, "is_dod": [is_dod] * n, "award_kind": [kind] * n,
        "base_and_all_options_value": [value] * n if np.isscalar(value) else list(value),
        "federal_action_obligation": [1000.0] * n, "eval_set": [True] * n,
        "single": [single] * n if np.isscalar(single) else list(single),
        "type_of_set_aside_code": ["NONE"] * n, "naics_code": ["541511"] * n, "vendor_id": list(vendors),
        "action_date": pd.to_datetime([f"{fy - 1}-10-15"] * (n // 2) + [f"{fy}-09-10"] * (n - n // 2)), "fy": [fy] * n})
    if link is not None:
        t["vendor_uei_code"] = [link.get(v, (v, ""))[0] for v in vendors]
        t["vendor_duns_code"] = [link.get(v, (v, ""))[1] for v in vendors]
    return t


class Helpers(unittest.TestCase):
    def test_link_groups_union_find(self):
        a = np.array([1, 2, 5, 0], dtype=np.uint64)
        b = np.array([2, 3, 6, 9], dtype=np.uint64)
        nodes, root = sb.link_groups(a, b)
        self.assertEqual(nodes.tolist(), [1, 2, 3, 5, 6])          # the pair with a 0 is ignored
        self.assertEqual(root.tolist(), [1, 1, 1, 5, 5])            # 1-2-3 chained into one group
        self.assertEqual(sb.canon(np.array([3, 6, 7], dtype=np.uint64), nodes, root).tolist(), [1, 5, 7])

    def test_hex_codes(self):
        x = pd.Series(["00000000000000ff", "", None, "zz"])
        self.assertEqual(sb.hex_codes(x).tolist(), [255, 0, 0, 0])

    def test_top_share_and_hhi(self):
        self.assertEqual(sb.top_share([50, 30, 10, 10], 0.25), (0.5, 1))
        s, k = sb.top_share([1] * 200, 0.01)
        self.assertEqual((round(s, 6), k), (0.01, 2))
        # market 0: two companies 50/50 -> 5,000; market 1: one company -> 10,000; market 2: too few awards
        m = np.array([0] * 20 + [1] * 25 + [2] * 5)
        c = np.array([0] * 10 + [1] * 10 + [2] * 25 + [3] * 5)
        out = sb.hhi_markets(m, c, np.ones(50)).set_index("market")
        self.assertEqual(sorted(out.index), [0, 1])
        self.assertAlmostEqual(out.hhi[0], 5000.0)
        self.assertAlmostEqual(out.hhi[1], 10000.0)
        self.assertEqual(sb.weighted_median([5000, 10000], [20, 25]), 10000.0)
        self.assertEqual(sb.weighted_median([5000, 10000], [30, 25]), 5000.0)

    def test_window_rule(self):
        years = [2008, 2009, 2010, 2011, 2013]
        self.assertEqual(sb.window_years(2011, years), [2010, 2009, 2008])
        self.assertIsNone(sb.window_years(2010, years))              # the fourth year present is the first measurable
        self.assertIsNone(sb.window_years(2013, years))              # FY2012 missing: no look-back
        self.assertTrue(sb.spans_switch([2021, 2022]))
        self.assertFalse(sb.spans_switch([2018, 2019, 2020, 2021]))
        self.assertFalse(sb.spans_switch([2023, 2024]))


class Pipeline(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.out = os.path.join(self.tmp, "results")
        os.makedirs(self.out)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, tables):
        paths = {}
        for fy, t in tables.items():
            p = os.path.join(self.tmp, f"base_actions_FY{fy}.parquet")
            t.to_parquet(p, index=False)
            paths[fy] = p
        return paths

    def run_(self, tables, **kw):
        t, c, cp, s = sb.run(self.write(tables), out_dir=self.out, verbose=False, **kw)
        return t, c, cp, s

    def row(self, t, fy, scope="all", sector="all sectors", kind="all"):
        r = t[(t.fy == fy) & (t.award_scope == scope) & (t.sector == sector) & (t.award_kind == kind)]
        self.assertEqual(len(r), 1)
        return r.iloc[0]

    def test_lookback_and_staying(self):
        base = fillers("f", 20)                      # 20 companies every year
        a, b = code("A"), code("B")
        tabs = {2009: table(2009, base + [b]), 2010: table(2010, base + [a]), 2011: table(2011, base + [a]),
                2012: table(2012, base + [a]), 2013: table(2013, base + [a, b])}
        t, _, _, s = self.run_(tabs)
        r13 = self.row(t, 2013)
        # A won in 2010-2012: not new.  B last won in 2009, outside the 3-year look-back: new.
        self.assertEqual(r13.n_companies, 22)
        self.assertEqual(r13.n_new_companies, 1)
        self.assertAlmostEqual(r13.new_share_companies, 1 / 22)
        self.assertAlmostEqual(r13.new_share_companies_never_seen, 0.0)    # B was seen in 2009
        r12 = self.row(t, 2012)
        self.assertEqual(r12.n_new_companies, 0)
        self.assertTrue(np.isnan(self.row(t, 2011).new_share_companies))   # 2011 has only two earlier years
        # staying: of 2009's 21 companies, the 20 fillers won in 2010; within 3 years all 21 (B never; fillers yes) -> 20/21
        r09 = self.row(t, 2009)
        self.assertAlmostEqual(r09.stay_share_next_year, 20 / 21)
        self.assertAlmostEqual(r09.stay_share_within_3_years, 20 / 21)
        r10 = self.row(t, 2010)
        self.assertAlmostEqual(r10.stay_share_within_3_years, 1.0)
        self.assertTrue(np.isnan(self.row(t, 2013).stay_share_next_year))    # no later year
        self.assertTrue(np.isnan(self.row(t, 2011).stay_share_within_3_years))
        self.assertEqual(s["years_present"], [2009, 2010, 2011, 2012, 2013])

    def test_award_totals_match_tables(self):
        base = fillers("f", 15)
        tabs = {2010: table(2010, base), 2011: table(2011, base[:12], value=[10_000.0] * 6 + [40_000.0] * 6)}
        t, _, _, _ = self.run_(tabs)
        self.assertEqual(self.row(t, 2010).n_awards, 15)
        self.assertEqual(self.row(t, 2011).n_awards, 12)
        self.assertEqual(self.row(t, 2011, scope="above_25k").n_awards, 6)

    def test_linking_two_identifiers(self):
        base = fillers("f", 20)
        duns, uei = code("duns:123"), code("UEI123")
        # until FY2021 the company is known by its DUNS code; in FY2022 one record carries both, the rest of its records the UEI
        old = {y: table(y, base + [duns]) for y in (2019, 2020, 2021)}
        link = {uei: (uei, duns)}
        new = table(2022, base + [uei], link=link)
        t, _, _, s = self.run_({**old, 2022: new})
        r = self.row(t, 2022)
        self.assertEqual(r.n_new_companies, 0)                              # linked: not new
        self.assertAlmostEqual(r.new_share_companies_unlinked_ids, 1 / 21)  # raw ids: looks new
        self.assertEqual(r.n_companies, 21)
        # the earlier years lack the link columns: the measure is marked not comparable, in the column and the note
        self.assertFalse(r.new_measures_comparable)
        self.assertIn("not comparable", r.note)
        self.assertIn("FY2019", r.note)
        sw = s["identifier_switch"]
        self.assertEqual(sw["duns_uei_links"], 1)
        self.assertEqual(sw["years_with_link_columns"], [2022])

    def test_linked_years_are_comparable(self):
        base = fillers("f", 20)
        duns, uei = code("duns:9"), code("UEI9")
        tabs = {y: table(y, base + [uei], link={uei: (uei, duns)}) for y in (2019, 2020, 2021, 2022)}
        t, _, _, s = self.run_(tabs)
        r = self.row(t, 2022)
        self.assertTrue(r.new_measures_comparable)
        self.assertEqual(r.note, "")
        self.assertEqual(s["identifier_switch"]["by_year"]["2021"]["share_duns_only_unlinked"], 0.0)

    def test_unlinked_duns_marks_years(self):
        base = fillers("f", 20)
        lonely = code("duns:777")                        # a DUNS-only record that no row links to a UEI: 1 of 21 awards
        tabs = {y: table(y, base + [lonely], link={lonely: ("", lonely)}) for y in (2019, 2020, 2021)}
        tabs[2022] = table(2022, base, link={})
        t, _, _, s = self.run_(tabs)
        self.assertAlmostEqual(s["identifier_switch"]["by_year"]["2021"]["share_duns_only_unlinked"], round(1 / 21, 4))
        self.assertIn("carry a DUNS that no record links", self.row(t, 2022).note)

    def test_small_cells_are_suppressed(self):
        base = fillers("f", 20)
        tabs = {2010: pd.concat([table(2010, base), table(2010, fillers("g", 3), agency="General Services Administration")])}
        t, c, _, _ = self.run_(tabs)
        g = self.row(t, 2010, sector="GSA")
        self.assertEqual(g.n_companies, 3)
        self.assertTrue(np.isnan(g.initial_dollars))
        self.assertIn("fewer than 10 companies", g.note)
        gc = c[(c.sector == "GSA") & (c.award_scope == "all")].iloc[0]
        self.assertTrue(np.isnan(gc.top10pct_share_dollars) and np.isnan(gc.hhi_median_award_weighted))
        civ = self.row(t, 2010, sector="civilian except GSA")
        self.assertFalse(np.isnan(civ.initial_dollars))

    def test_competition_rows_and_wilson(self):
        base = fillers("f", 30)
        newc = fillers("n", 20)
        tabs = {y: table(y, base) for y in (2010, 2011, 2012)}
        tabs[2013] = table(2013, base + newc, single=[False] * 30 + [True] * 10 + [False] * 10)
        _, _, cp, s = self.run_(tabs)
        r = cp[(cp.period == "FY2013") & (cp.sector == "all sectors") & (cp.award_scope == "all") & (cp.measure == "single_offer_share")]
        r = r.set_index("group")
        self.assertEqual((r.k["new companies"], r.n["new companies"]), (10, 20))
        self.assertAlmostEqual(r.share["new companies"], 0.5)
        self.assertLess(r.ci_lo["new companies"], 0.5)
        self.assertGreater(r.ci_hi["new companies"], 0.5)
        self.assertEqual(r.k["established companies"], 0)
        pooled = cp[cp.fy.isna()]
        self.assertTrue(len(pooled))
        self.assertEqual(s["competition_above_25k_pooled"]["single_offer_share_new"]["n"], 20)

    def test_missing_years(self):
        base = fillers("f", 20)
        tabs = {2008: table(2008, base), 2010: table(2010, base), 2011: table(2011, base), 2013: table(2013, base)}
        t, c, cp, s = self.run_(tabs)
        self.assertEqual(sorted(t.fy.unique()), [2008, 2010, 2011, 2013])
        self.assertTrue(t.new_share_companies.isna().all())          # no year has its three previous years
        self.assertTrue(np.isnan(self.row(t, 2008).stay_share_next_year))   # FY2009 is missing
        self.assertAlmostEqual(self.row(t, 2010).stay_share_next_year, 1.0)
        self.assertEqual(len(cp), 0)
        self.assertNotIn("new_companies_above_25k", s)

    def test_no_identifiers_in_outputs(self):
        base = fillers("f", 25)
        duns, uei = code("duns:55"), code("UEI55")
        tabs = {y: table(y, base + [uei], link={uei: (uei, duns)}) for y in (2019, 2020, 2021, 2022, 2023)}
        self.run_(tabs, fig_path=os.path.join(self.tmp, "fig.png"))
        ids = set(base) | {duns, uei} | {str(int(x, 16)) for x in base + [duns, uei]}
        text = ""
        for p in glob.glob(os.path.join(self.out, "*")):
            with open(p, encoding="utf-8") as f:
                text += f.read()
        self.assertFalse([i for i in ids if i in text])
        self.assertFalse(re.search(r"\b[0-9a-f]{16}\b", text))
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "fig.png")))
        with open(os.path.join(self.out, "supplier_base_summary.json"), encoding="utf-8") as f:
            s = json.load(f)
        self.assertIn("caveats", s)


if __name__ == "__main__":
    unittest.main()
