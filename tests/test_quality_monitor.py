"""Offline test for scripts/16_quality_monitor.py with a small synthetic table (no files from data/)."""
import importlib.util
import json
import os
import tempfile
import unittest

import numpy as np
import pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("quality16", os.path.join(REPO, "scripts", "16_quality_monitor.py"))
q16 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(q16)


def make_table():
    rng = np.random.default_rng(1)
    rows = []
    # eight quarters from Oct 2023 (FY2024Q1) to Sep 2025 (FY2025Q4); 500 awards per quarter per agency
    starts = pd.date_range("2023-10-01", periods=8, freq="3MS")
    for agency, jump in (("Jumpy Agency", True), ("Stable Agency", False)):
        for i, s in enumerate(starts):
            p_single = 0.15 if (jump and i == 7) else 0.50
            n = 500
            offers = np.where(rng.random(n) < p_single, 1, 2)
            dates = s + pd.to_timedelta(rng.integers(0, 85, n), unit="D")
            rows.append(pd.DataFrame({"agency": agency, "award_kind": "PO", "action_date": dates, "competed": True,
                                      "offers": offers.astype(float), "offers_ok": True, "eval_set": True}))
    return pd.concat(rows, ignore_index=True)


class QualityMonitorTests(unittest.TestCase):
    def test_jump_flagged_and_stable_not(self):
        fl = q16.find_flags(make_table())
        jumpy = fl[(fl.agency == "Jumpy Agency") & (fl.metric == "offers_1")]
        self.assertEqual(list(jumpy.quarter), ["FY2025Q4"])
        self.assertLess(jumpy.iloc[0].change_points, -25)
        self.assertFalse((fl.agency == "Stable Agency").any())

    def test_quarter_labels(self):
        i = q16.quarter_index(pd.Series(pd.to_datetime(["2023-10-01", "2024-01-15", "2024-09-30"])))
        self.assertEqual([q16.quarter_label(int(x)) for x in i], ["FY2024Q1", "FY2024Q2", "FY2024Q4"])

    def test_volume_drop_flagged(self):
        t = make_table()
        drop = (t.agency == "Stable Agency") & (t.action_date >= "2025-07-01") & (np.arange(len(t)) % 2 == 0)
        fl = q16.find_flags(t[~drop], data_end=pd.Timestamp("2025-09-30"))
        v = fl[(fl.metric == "volume") & (fl.agency == "Stable Agency")]
        self.assertEqual(list(v.quarter), ["FY2025Q4"])


class HistoryInputTests(unittest.TestCase):
    """the history tables: awarding_agency_name, several files or a folder, their own output names, separate top lists"""
    def test_awarding_agency_name_gives_the_same_flags(self):
        t = make_table()
        a = q16.find_flags(t)
        b = q16.find_flags(t.rename(columns={"agency": "awarding_agency_name"}))
        self.assertTrue(a.equals(b))
        with self.assertRaises(KeyError):
            q16.find_flags(t.drop(columns="agency"))

    def test_several_files_equal_one_table(self):
        t = make_table()
        h = t.rename(columns={"agency": "awarding_agency_name"})
        fy = h.action_date.dt.year + (h.action_date.dt.month >= 10)
        with tempfile.TemporaryDirectory() as d:
            folder = os.path.join(d, "processed")
            os.makedirs(folder)
            for y, g in h.groupby(fy):
                g.to_parquet(os.path.join(folder, f"base_actions_FY{y}.parquet"), index=False)
            open(os.path.join(folder, "award_year_FY2024.parquet"), "wb").close()     # other tables in the folder are not read
            files = q16.input_files([folder])
            self.assertEqual([os.path.basename(f) for f in files], ["base_actions_FY2024.parquet", "base_actions_FY2025.parquet"])
            fl, n, end, ags = q16.flags_for_files(files)
            self.assertEqual(n, len(t))
            self.assertEqual(ags, {"awarding_agency_name"})
            self.assertTrue(fl.equals(q16.find_flags(t)))
            out = os.path.join(d, "results")
            fl2, summ, name = q16.main(["16_quality_monitor.py", folder], results_dir=out, verbose=False)
            self.assertEqual(name, "quality_history")
            self.assertEqual(sorted(os.listdir(out)), ["quality_history_flags.csv", "quality_history_summary.json"])
            # a snapshot-style table keeps the old names
            p = os.path.join(d, "snap.parquet")
            t.to_parquet(p, index=False)
            self.assertEqual(q16.main(["x", p], results_dir=out, verbose=False)[2], "quality")
            self.assertTrue(os.path.exists(os.path.join(out, "quality_flags.csv")))
            self.assertEqual(q16.main(["x", "--prefix", "custom", p], results_dir=out, verbose=False)[2], "custom")

    def test_share_and_volume_flags_are_listed_separately(self):
        t = make_table()
        drop = (t.agency == "Stable Agency") & (t.action_date >= "2025-07-01") & (np.arange(len(t)) % 2 == 0)
        fl = q16.find_flags(t[~drop], data_end=pd.Timestamp("2025-09-30"))
        self.assertEqual(list(fl.columns)[-1], "unit")
        self.assertEqual(set(fl[fl.metric == "volume"].unit), {"percent"})
        self.assertEqual(set(fl[fl.metric != "volume"].unit), {"points"})
        kinds = list((fl.metric == "volume").astype(int))
        self.assertEqual(kinds, sorted(kinds))                          # all share flags before all volume flags
        s = q16.summarize(fl)
        self.assertTrue(s["top_share_flags"] and s["top_volume_flags"])
        self.assertTrue(all(f["metric"] != "volume" for f in s["top_share_flags"]))
        self.assertTrue(all(f["metric"] == "volume" for f in s["top_volume_flags"]))
        self.assertEqual(s["top_15"], s["top_share_flags"][:15])          # kept for older readers, one unit only
        self.assertIn("points", s["units"]["share"])
        self.assertIn("percent", s["units"]["volume"])
        json.dumps(s, default=float)


if __name__ == "__main__":
    unittest.main()
