"""Offline tests for scripts/build_explorer.py with small synthetic result files in a temporary folder (nothing from data/ or results/)."""
import csv
import importlib.util
import json
import os
import shutil
import tempfile
import unittest
from datetime import date

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("build_explorer", os.path.join(REPO, "scripts", "build_explorer.py"))
bx = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bx)


def write_csv(folder, name, rows):
    with open(os.path.join(folder, name), "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def share_row(**kw):
    r = {"k_single": 30, "n_eval": 100, "share": 0.3, "ci_lo": 0.22, "ci_hi": 0.39}
    r.update(kw)
    return r


def load_js(path):
    """Parse the JSON inside 'window.FCC_DATA = {...};'."""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    marker = "window.FCC_DATA = "
    i = text.index(marker) + len(marker)
    body = text[i:].strip()
    assert body.endswith(";"), "the data script must end with a semicolon"
    return json.loads(body[:-1])


def base_files(d):
    """The required result files, a few rows each."""
    write_csv(d, "atlas_civilian_by_agency.csv", [
        dict(agency="General Services Administration", abbreviation="GSA", n_base=900, n_competed=700, in_original_five="False", **share_row(n_eval=600, k_single=240, share=0.4)),
        dict(agency="Department of Veterans Affairs", abbreviation="VA", n_base=300, n_competed=200, in_original_five="True", **share_row())])
    write_csv(d, "atlas_civilian_by_agency_fy.csv", [
        dict(agency="Department of Veterans Affairs", fy=2024, **share_row()),
        dict(agency="Department of Veterans Affairs", fy=2025, **share_row(share=0.25)),
        dict(agency="General Services Administration", fy=2024, **share_row(share=0.55)),
        dict(agency="General Services Administration", fy=2026, **share_row(share=0.17))])
    write_csv(d, "atlas_civilian_by_fy.csv", [
        dict(fy=2024, scope="all civilian agencies except GSA", **share_row()),
        dict(fy=2024, scope="GSA only", **share_row(share=0.55))])
    write_csv(d, "atlas_civilian_totals.csv", [
        dict(scope="all civilian agencies", n_agencies=2, median_award_value=500, dollar_weighted_share_po_def="", snapshot="2026-11-09", **share_row(n_eval=700)),
        dict(scope="GSA only", n_agencies=1, median_award_value=143, dollar_weighted_share_po_def="", snapshot="2026-11-09", **share_row(n_eval=600)),
        dict(scope="all civilian agencies except GSA", n_agencies=1, median_award_value=40000, dollar_weighted_share_po_def="", snapshot="2026-11-09", **share_row())])
    write_csv(d, "threshold_quarterly_bunching.csv", [
        dict(fyq=q, threshold=t, n_base=1000, n_window=100, obs_below=30, cf_below=20.5, ratio=1.46, se_boot=0.2, lo95=1.1, hi95=1.9)
        for q in ("FY2025Q4", "FY2026Q1") for t in (250000, 350000)])
    write_csv(d, "threshold_sat_moved.csv", [dict(threshold=350000, ratio_pre=0.97, ratio_post=1.96, diff=0.99, diff_lo=0.65, diff_hi=1.36)])
    write_csv(d, "quality_flags.csv", [
        dict(agency="VA", award_kind="PO", quarter="FY2025Q1", metric="offers_1", before=40.0, after=20.0, change_points=-20.0, z=-9.1, n_before=900, n_after=300)])
    with open(os.path.join(d, "quality_summary.json"), "w") as f:
        json.dump({"n_flags": 1, "thresholds": {"min_n": 200, "min_points": 10.0, "volume_pct": 40.0}}, f)
    write_csv(d, "sam_daily_counts.csv", [
        dict(snapshot_date="2026-11-09", department="VETERANS AFFAIRS, DEPARTMENT OF", type="Solicitation", n_active=9, n_sol_numbers=5, n_posted_last_7d=1, n_open=4, n_deadline_next_7d=1, n_deadline_next_30d=2),
        dict(snapshot_date="2026-11-09", department="VETERANS AFFAIRS, DEPARTMENT OF", type="Award Notice", n_active=9, n_sol_numbers=5, n_posted_last_7d=1, n_open="", n_deadline_next_7d="", n_deadline_next_30d=""),
        dict(snapshot_date="2026-11-09", department="DEPT OF DEFENSE", type="Solicitation", n_active=90, n_sol_numbers=50, n_posted_last_7d=1, n_open=40, n_deadline_next_7d=1, n_deadline_next_30d=2),
        dict(snapshot_date="2026-11-10", department="VETERANS AFFAIRS, DEPARTMENT OF", type="Solicitation", n_active=9, n_sol_numbers=5, n_posted_last_7d=1, n_open=6, n_deadline_next_7d=1, n_deadline_next_30d=2)])


class ExplorerDataTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.res = os.path.join(self.tmp, "results")
        os.makedirs(self.res)
        base_files(self.res)
        self.out = os.path.join(self.tmp, "docs", "data", "explorer-data.js")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def build(self):
        bx.build(self.res, self.out, repo="someone/project", today=date(2026, 11, 10))
        return load_js(self.out)

    def test_valid_js_wrapper_and_metadata(self):
        d = self.build()
        with open(self.out, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("window.FCC_DATA = {", text)
        self.assertNotIn("NaN", text)
        self.assertEqual(d["built"], "2026-11-10")
        self.assertEqual(d["repo"], "https://github.com/someone/project")
        self.assertIn("atlas_civilian_by_agency.csv", d["sources"])

    def test_missing_optional_files_are_skipped(self):
        d = self.build()
        self.assertNotIn("history_by_agency_fy.csv", d["sources"])
        self.assertNotIn("history", d["thresholds"])
        self.assertNotIn("staggered", d["thresholds"])
        self.assertFalse(d["sectors"]["has_history"])
        self.assertEqual(d["agency_meta"]["history_years"], [])
        va = next(a for a in d["agencies"] if a["abbr"] == "VA")
        self.assertEqual([p[0] for p in va["years"]], [2024, 2025])
        self.assertEqual(d["thresholds"]["quarterly"]["350000"][0][0], "FY2025Q4")

    def test_history_merges_and_wins_on_overlap(self):
        write_csv(self.res, "history_by_agency_fy.csv", [
            dict(agency="Department of Veterans Affairs", fy=fy, **share_row(share=s)) for fy, s in ((2008, 0.5), (2009, 0.45), (2024, 0.31))]
            + [dict(agency="Department of Defense", fy=2008, **share_row())])
        write_csv(self.res, "history_by_fy.csv", [
            dict(sector=s, fy=fy, award_kind=k, **share_row()) for s in ("GSA", "Department of Defense") for fy in (2008, 2009) for k in ("all", "PO")])
        write_csv(self.res, "threshold_history.csv", [
            dict(fy=fy, sector=sec, line=line, ratio=1.2, ci_lo="", ci_hi="", obs_below=10, cf_below=8.3, n_window=50, n_base=500, active=str(fy > 2010))
            for fy in (2010, 2011) for sec in ("civilian", "defense") for line in (100000, 150000)])
        write_csv(self.res, "threshold_staggered.csv", [
            dict(sector="civilian", event_quarter=q, line=150000, ratio=1.1, ci_lo=0.9, ci_hi=1.3, obs_below=5, cf_below=4.5, n_awards=400, dates=dt)
            for q in (-1, 0) for dt in ("actual", "placebo, one year earlier")])
        d = self.build()
        va = next(a for a in d["agencies"] if a["abbr"] == "VA")
        self.assertEqual([p[0] for p in va["years"]], [2008, 2009, 2024, 2025])
        self.assertEqual(va["years"][2][3], 0.31)      # the long history wins for 2024
        self.assertEqual(va["years"][2][6], "h")
        self.assertEqual(va["years"][3][6], "r")
        self.assertTrue(any(a["full"] == "Department of Defense" for a in d["agencies"]))
        self.assertEqual(d["agency_meta"]["history_years"], [2008, 2009, 2024])
        self.assertTrue(d["sectors"]["has_history"])
        self.assertEqual([p[0] for p in d["sectors"]["series"]["GSA"]["all"]], [2008, 2009, 2024])
        th = d["thresholds"]["history"]["defense"]["150000"]
        self.assertEqual([p[0] for p in th], [2010, 2011])
        self.assertIsNone(th[0][2])                     # blank interval -> null
        self.assertEqual([p[4] for p in th], [False, True])
        self.assertEqual(len(d["thresholds"]["staggered"]["placebo"]["civilian"]["150000"]), 2)

    def test_quality_and_sam(self):
        d = self.build()
        flag = d["quality"]["flags"][0]
        self.assertEqual(flag[0], "Veterans Affairs")   # abbreviation mapped to the agency name
        self.assertEqual(flag[3], "offers_1")
        sam = d["sam"]
        self.assertEqual(sam["date"], "2026-11-10")     # no latest.json: newest day in the counts file
        self.assertEqual(sam["departments"], [["Veterans Affairs", 6, None]])
        self.assertEqual(sam["daily"], [["2026-11-09", 4, 40], ["2026-11-10", 6, 0]])

    def test_gsa_note_numbers(self):
        g = self.build()["gsa"]
        self.assertAlmostEqual(g["award_share"], 600 / 700, places=3)
        self.assertEqual(g["first"], [2024, 0.55])
        self.assertEqual(g["last"], [2026, 0.17])

    def test_only_results_folder_is_read(self):
        os.remove(os.path.join(self.res, "sam_daily_counts.csv"))
        os.remove(os.path.join(self.res, "quality_flags.csv"))
        d = self.build()
        self.assertIsNone(d["sam"])
        self.assertIsNone(d["quality"])

    def test_names(self):
        self.assertEqual(bx.sam_name("EXPORT-IMPORT BANK OF THE US"), "Export-Import Bank of the US")
        self.assertEqual(bx.sam_name("INTERIOR, DEPARTMENT OF THE"), "Interior")
        self.assertEqual(bx.short_name("Department of the Treasury"), "Treasury")


if __name__ == "__main__":
    unittest.main()
