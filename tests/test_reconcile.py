"""Offline tests for scripts/28_reconcile.py: the arithmetic, missing years, the API being down, the output columns. No network."""
import csv
import importlib.util
import json
import os
import sys
import tempfile
import unittest

import pandas as pd

SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
sys.path.insert(0, SCRIPTS)
_spec = importlib.util.spec_from_file_location("reconcile", os.path.join(SCRIPTS, "28_reconcile.py"))
rc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rc)


def make_year(folder, fy, n_tx, obl, with_json=True):
    pd.DataFrame({"contract_award_unique_key": ["a", "b"], "n_tx": n_tx, "obligation_sum": obl, "fy": fy}).to_parquet(
        os.path.join(folder, f"award_year_FY{fy}.parquet"), index=False)
    if with_json:
        with open(os.path.join(folder, f"history_FY{fy}.json"), "w") as f:
            json.dump({"fiscal_year": fy, "transactions": sum(n_tx)}, f)


class Base(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.proc = os.path.join(self._t.name, "processed")
        self.res = os.path.join(self._t.name, "results")
        os.makedirs(self.proc)
        make_year(self.proc, 2015, [60, 40], [600.0, 400.0])
        make_year(self.proc, 2016, [10, 10], [50.0, 50.0], with_json=False)

    def tearDown(self):
        self._t.cleanup()

    def go(self, fetch, **kw):
        return rc.run(fetch=fetch, processed=self.proc, results_dir=self.res, pause=0, sleep=lambda s: None,
                      names=kw.pop("names", lambda: ["Agency A", "Agency B"]), now="2026-11-10T00:00:00Z", **kw)


class ArithmeticTests(Base):
    def test_pct_diff(self):
        self.assertEqual(rc.pct_diff(99, 100), -1.0)
        self.assertEqual(rc.pct_diff(101, 100), 1.0)
        self.assertIsNone(rc.pct_diff(5, 0))
        self.assertIsNone(rc.pct_diff(5, None))

    def test_totals_and_rows(self):
        rows, summ = self.go(lambda fy, names: {"transactions": 101 if fy == 2015 else 20, "obligations": 1000.0 if fy == 2015 else 200.0})
        r = [x for x in rows if x["fy"] == 2015 and x["sector"] == "download_agencies"][0]
        self.assertEqual(r["our_transactions"], 100)
        self.assertEqual(r["our_obligations"], 1000.0)
        self.assertAlmostEqual(r["diff_transactions_pct"], pct(100, 101), places=3)
        self.assertEqual(r["diff_obligations_pct"], 0.0)
        self.assertEqual(summ["years_checked"], [2015, 2016])
        self.assertEqual(summ["years_above_threshold"], [2016])      # 100 vs 200 dollars
        self.assertTrue(summ["json_transactions_equal_n_tx"]["2015"])
        self.assertNotIn("2016", summ["json_transactions_equal_n_tx"])

    def test_agency_list_is_passed_only_to_the_like_for_like_row(self):
        seen = []
        self.go(lambda fy, names: seen.append(names) or {"transactions": 1, "obligations": 1.0}, years=[2015])
        self.assertEqual(seen, [["Agency A", "Agency B"], None])


class MissingTests(Base):
    def test_year_without_table_is_skipped(self):
        rows, summ = self.go(lambda fy, names: {"transactions": 1, "obligations": 1.0}, years=[2015, 1999])
        self.assertEqual({r["fy"] for r in rows}, {2015})

    def test_no_tables_at_all(self):
        empty = os.path.join(self._t.name, "empty")
        os.makedirs(empty)
        rows, summ = rc.run(fetch=lambda fy, n: None, processed=empty, results_dir=self.res, names=lambda: None)
        self.assertEqual(rows, [])
        self.assertIn("No yearly tables", summ["verdict"])


class ApiDownTests(Base):
    def test_service_down_gives_partial_rows_and_no_exception(self):
        calls = []

        def down(fy, names):
            calls.append(fy)
            return None
        rows, summ = self.go(down)
        self.assertEqual(len(calls), 2)                  # stops asking after two misses in a row
        self.assertEqual(summ["years_checked"], [])
        self.assertTrue(all(r["api_transactions"] is None and r["diff_transactions_pct"] is None for r in rows))
        self.assertIn("did not answer", summ["verdict"])
        self.assertTrue(summ["errors"])

    def test_exception_in_fetch_is_caught(self):
        def boom(fy, names):
            raise RuntimeError("network")
        rows, summ = self.go(boom)
        self.assertEqual(summ["years_checked"], [])

    def test_agency_list_missing_still_checks_all_agencies(self):
        rows, summ = self.go(lambda fy, names: {"transactions": 100, "obligations": 1000.0}, names=lambda: None, years=[2015])
        by = {r["sector"]: r for r in rows}
        self.assertIsNone(by["download_agencies"]["api_transactions"])
        self.assertEqual(by["all_agencies"]["api_transactions"], 100)

    def test_main_returns_zero_on_failure(self):
        old = rc.run
        rc.run = lambda **k: (_ for _ in ()).throw(RuntimeError("x"))
        try:
            self.assertEqual(rc.main([]), 0)
        finally:
            rc.run = old


class OutputTests(Base):
    def test_columns_and_files(self):
        self.go(lambda fy, names: {"transactions": 100, "obligations": 1000.0})
        with open(os.path.join(self.res, "reconciliation.csv"), newline="") as f:
            rd = csv.DictReader(f)
            rows = list(rd)
            self.assertEqual(rd.fieldnames, ["fy", "sector", "our_transactions", "api_transactions", "diff_transactions_pct", "our_obligations",
                                             "api_obligations", "diff_obligations_pct", "checked_utc", "endpoint"])
        self.assertEqual(len(rows), 4)
        s = json.load(open(os.path.join(self.res, "reconciliation_summary.json")))
        for k in ("years_checked", "largest_differences", "verdict", "caveats"):
            self.assertIn(k, s)

    def test_no_email_address_in_the_script(self):
        text = open(os.path.join(SCRIPTS, "28_reconcile.py"), encoding="utf-8").read()
        self.assertNotRegex(text, r"[\w.]+@[\w.]+\.\w+")


def pct(a, b):
    return (a - b) / b * 100


if __name__ == "__main__":
    unittest.main()
