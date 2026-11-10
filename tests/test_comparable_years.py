"""Comparisons over the long history use only comparable years.

A year with a note in results/history_by_fy.csv is not comparable (a known reporting change, or a Defense year whose last
months are not published yet).  19_history_atlas.py writes the notes; build_report.py and build_brief.py must not use such a
year as the start or the end of a "from ... to ..." sentence, and must say which years were left out.
"""
import importlib.util
import os
import sys
import tempfile
import unittest

import pandas as pd

SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
sys.path.insert(0, SCRIPTS)


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(SCRIPTS, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


atlas = load("history_atlas_19", "19_history_atlas.py")
import build_report as br  # noqa: E402


def year_table(path, fy, last_full_month):
    """one fiscal year of Defense awards: 100 a month up to `last_full_month` (1 = October ... 12 = September), 1 a month after"""
    months = pd.period_range(f"{fy - 1}-10", f"{fy}-09", freq="M")
    rows = []
    for i, m in enumerate(months, start=1):
        for _ in range(100 if i <= last_full_month else 1):
            rows.append({"action_date": m.to_timestamp() + pd.Timedelta(days=14), "is_dod": True, "fy": fy})
    rows.append({"action_date": pd.Timestamp(fy, 9, 30), "is_dod": True, "fy": fy})      # a stray late record
    rows.append({"action_date": pd.Timestamp(fy, 9, 29), "is_dod": False, "fy": fy})     # civilian rows do not count
    pd.DataFrame(rows).to_parquet(path, index=False)


class DefenseIncomplete(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "base_actions_FY2026.parquet")

    def test_thin_last_months_are_flagged_and_the_last_full_month_is_named(self):
        year_table(self.path, 2026, last_full_month=10)                 # full through July 2026
        fy, note = atlas.defense_incomplete(self.path)
        self.assertEqual(fy, 2026)
        self.assertIn("after July 2026", note)
        self.assertIn("Not comparable", note)

    def test_a_complete_year_is_not_flagged_even_with_stray_dates(self):
        year_table(self.path, 2026, last_full_month=12)
        self.assertIsNone(atlas.defense_incomplete(self.path))

    def test_no_defense_rows_gives_no_note(self):
        pd.DataFrame([{"action_date": pd.Timestamp(2026, 3, 1), "is_dod": False, "fy": 2026}]).to_parquet(self.path, index=False)
        self.assertIsNone(atlas.defense_incomplete(self.path))


def row(fy, share, note=""):
    return {"sector": "", "fy": str(fy), "share": str(share), "ci_lo": str(share - 0.01), "ci_hi": str(share + 0.01),
            "n_eval": "1000", "award_kind": "all", "note": note}


class ComparableYearsInTheReadme(unittest.TestCase):
    def setUp(self):
        self.saved = br.history_all
        self.addCleanup(lambda: setattr(br, "history_all", self.saved))

    def render(self, table):
        br.history_all = lambda: table
        return br.r_history()

    def test_noted_first_and_last_years_are_left_out_and_named(self):
        out = self.render({
            "civilian except GSA": [row(2008, 0.48, "FY2008 reporting change. Not comparable with later years."), row(2009, 0.34),
                                    row(2010, 0.33), row(2011, 0.30)],
            "Department of Defense": [row(2008, 0.35), row(2009, 0.33), row(2010, 0.31),
                                      row(2011, 0.20, "FY2011 is incomplete for the Department of Defense. Not comparable.")]})
        self.assertIn("in civilian agencies except GSA from 34.0%", out)
        self.assertIn("in FY2009 to 30.0%", out)
        self.assertIn("at Defense from 35.0%", out)
        self.assertIn("in FY2010", out)
        self.assertNotIn("from 48.0%", out)
        self.assertIn("Left out of that comparison, as not comparable: FY2008 in civilian agencies except GSA (48.0% as recorded).", out)
        self.assertIn("FY2011 at Defense (20.0% as recorded).", out)

    def test_without_notes_the_first_and_last_years_are_used(self):
        out = self.render({"civilian except GSA": [row(2008, 0.40), row(2009, 0.34), row(2010, 0.30)]})
        self.assertIn("from 40.0%", out)
        self.assertIn("in FY2010", out)
        self.assertNotIn("Left out", out)

    def test_a_note_in_the_middle_is_not_listed(self):
        out = self.render({"civilian except GSA": [row(2008, 0.40), row(2009, 0.34, "a mid-series note"), row(2010, 0.30)]})
        self.assertIn("from 40.0%", out)
        self.assertNotIn("Left out", out)


if __name__ == "__main__":
    unittest.main()
