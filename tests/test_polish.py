"""Offline tests for chart text and history notes: dollar signs in matplotlib text are escaped (scripts 19, 20, 21, 26), fiscal-year
axes use whole years, and 19_history_atlas.py notes the FY2008 VA reporting change from the data."""
import ast
import importlib.util
import os
import sys
import unittest

import pandas as pd

SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
sys.path.insert(0, SCRIPTS)


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, os.path.join(SCRIPTS, file))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


TEXT_CALLS = {"set_title", "suptitle", "set_xlabel", "set_ylabel", "text", "annotate", "figtext"}
CHART_SCRIPTS = ["19_history_atlas.py", "20_threshold_history.py", "21_threshold_staggered.py", "26_split_screen.py"]


def unescaped_dollars(path):
    """string pieces passed to matplotlib text calls (or as label=) that hold a '$' without a backslash before it"""
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    bad = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "attr", getattr(node.func, "id", ""))
        args = list(node.args) if name in TEXT_CALLS else []
        args += [k.value for k in node.keywords if k.arg == "label"]
        for a in args:
            for piece in ast.walk(a):
                if isinstance(piece, ast.Constant) and isinstance(piece.value, str):
                    s = piece.value
                    if any(c == "$" and (i == 0 or s[i - 1] != "\\") for i, c in enumerate(s)):
                        bad.append((node.lineno, s))
    return bad


class ChartText(unittest.TestCase):
    def test_dollar_signs_are_escaped(self):
        for f in CHART_SCRIPTS:
            self.assertEqual(unescaped_dollars(os.path.join(SCRIPTS, f)), [], f)

    def test_escaped_text_renders_dollar_signs(self):
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig = plt.figure()
        t = fig.suptitle("under \\$150K and \\$250K")
        shown, is_math = t._preprocess_math(t.get_text())           # what matplotlib draws, and whether it reads it as math
        self.assertEqual((shown, bool(is_math)), ("under $150K and $250K", False))
        plain = fig.suptitle("under $150K and $250K")                # unescaped: read as math, the bug this guards against
        self.assertTrue(plain._preprocess_math(plain.get_text())[1])
        plt.close(fig)

    def test_whole_fiscal_years_on_the_axis(self):
        th = load("th20", "20_threshold_history.py")
        self.assertEqual(th.year_ticks([2008, 2009, 2010]), [2008, 2009, 2010])
        ticks = th.year_ticks(range(2008, 2022))
        self.assertEqual(ticks, list(range(2008, 2022, 2)))
        self.assertTrue(all(isinstance(t, int) for t in ticks))


class HistoryNotes(unittest.TestCase):
    def test_va_fy2008_note_is_computed_from_the_data(self):
        h = load("h19", "19_history_atlas.py")
        rows = []
        # FY2008: VA reports many micro-purchases coded one offer; FY2009: almost none.  Another agency is steady at 30%.
        for fy, n_micro in ((2008, 900), (2009, 10)):
            rows += [dict(agency=h.VA, fy=fy, award_kind="PO", single=True, base_and_all_options_value=500.0)] * n_micro
            rows += [dict(agency=h.VA, fy=fy, award_kind="PO", single=i < 50, base_and_all_options_value=50_000.0) for i in range(100)]
            rows += [dict(agency="Department of the Interior", fy=fy, award_kind="DEF", single=i < 300, base_and_all_options_value=80_000.0)
                     for i in range(1000)]
        b = pd.DataFrame(rows)
        b["is_dod"] = False
        b["sector"] = h.sector_of(b.agency, b.is_dod)
        sec, ag, cautions = h.issue_notes(b)
        self.assertEqual(set(sec), {("civilian except GSA", 2008, "all"), ("civilian except GSA", 2008, "PO")})
        self.assertEqual(set(ag), {(h.VA, 2008)})
        t = sec[("civilian except GSA", 2008, "all")]
        self.assertIn("900 competed micro-purchases", t)
        self.assertIn("against 10 in FY2009", t)
        self.assertIn("100% of them record one offer", t)
        self.assertIn("share is 30.0%", t)                               # without VA
        self.assertIn("Not comparable", t)
        self.assertNotIn("—", t)
        self.assertEqual(len(cautions), 1)
        self.assertAlmostEqual(cautions[0]["share_without"], 0.30)

    def test_no_note_without_the_year(self):
        h = load("h19b", "19_history_atlas.py")
        b = pd.DataFrame([dict(agency=h.VA, fy=2012, award_kind="PO", single=True, base_and_all_options_value=500.0, is_dod=False)])
        b["sector"] = h.sector_of(b.agency, b.is_dod)
        self.assertEqual(h.issue_notes(b), ({}, {}, []))


if __name__ == "__main__":
    unittest.main()
