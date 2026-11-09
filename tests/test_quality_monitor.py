"""Offline test for scripts/16_quality_monitor.py with a small synthetic table (no files from data/)."""
import importlib.util
import os
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


if __name__ == "__main__":
    unittest.main()
