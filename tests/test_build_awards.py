"""Offline tests for scripts/02_build_awards.py: build() on a tiny fake transaction ZIP."""
import contextlib
import csv
import importlib.util
import io
import os
import shutil
import sys
import tempfile
import unittest
import zipfile

import pandas as pd

SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
sys.path.insert(0, SCRIPTS)
_spec = importlib.util.spec_from_file_location("build_awards_02", os.path.join(SCRIPTS, "02_build_awards.py"))
ba = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ba)

VA = "Department of Veterans Affairs"
HHS = "Department of Health and Human Services"
WINDOW_END = "2026-09-30"


def tx(key, award, mod, date, agency, extent, offers, oblig, value, atype="D", flag="AWARD"):
    row = dict.fromkeys(ba.CIVILIAN_COLS, "")
    row.update(contract_transaction_unique_key=key, contract_award_unique_key=award, modification_number=mod,
               action_date=date, award_type_code=atype, award_or_idv_flag=flag, awarding_agency_name=agency,
               extent_competed_code=extent, number_of_offers_received=offers, federal_action_obligation=oblig,
               base_and_all_options_value=value, solicitation_identifier="")
    return row


ROWS_ZIP1 = [
    # award A1: base + duplicate of the base row (same transaction key) + one later modification
    tx("T1", "A1", "0", "2024-01-15", VA, "A", "1", "1000", "5000"),
    tx("T1", "A1", "0", "2024-01-15", VA, "A", "1", "1000", "5000"),
    tx("T2", "A1", "P00001", "2024-06-01", VA, "A", "", "500", "7000"),
    # A2: competed (D), 3 offers, purchase order, fiscal year 2026 (Oct 2025)
    tx("T3", "A2", "0", "2025-10-05", HHS, "D", "3", "200", "200", atype="B"),
    # A3: competed but 999 offers (sentinel), fiscal year 2025 (Oct 1 2024)
    tx("T4", "A3", "0", "2024-10-01", VA, "A", "999", "300", "300"),
    # A4: not competed (extent code C), 1 offer
    tx("T5", "A4", "00", "2024-03-03", HHS, "C", "1", "400", "400"),
]
ROWS_ZIP2 = [
    # A5: only a modification in the file -> no base action -> dropped
    tx("T6", "A5", "P00002", "2025-02-02", VA, "A", "1", "50", "50"),
    # A6: an IDV, competitive delivery order code, blank offers
    tx("T7", "A6", "0", "2025-05-05", HHS, "CDO", "", "10", "10", flag="IDV"),
]


def write_zip(path, rows, csv_name):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=ba.CIVILIAN_COLS)
    w.writeheader()
    w.writerows(rows)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(csv_name, buf.getvalue())
        z.writestr("readme_not_a_transaction_file.csv", "contract_transaction_unique_key\nJUNK\n")   # must be ignored


class BuildAwardsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="fcc_test_build_")
        write_zip(os.path.join(cls.tmp, "part1.zip"), ROWS_ZIP1, "FY2024_All_Contracts_Transactions_1.csv")
        write_zip(os.path.join(cls.tmp, "part2.zip"), ROWS_ZIP2, "FY2025_All_Contracts_Transactions_1.csv")
        cls.src = os.path.join(cls.tmp, "*.zip")
        with contextlib.redirect_stdout(io.StringIO()):
            cls.all = ba.build(src=cls.src, usecols=ba.CIVILIAN_COLS, window_end=WINDOW_END)
            cls.one = ba.build(src=cls.src, usecols=ba.CIVILIAN_COLS, window_end=WINDOW_END, agencies=[HHS])
        cls.t = cls.all.set_index("contract_award_unique_key")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_one_row_per_award_and_no_base_means_dropped(self):
        self.assertEqual(len(self.all), 5)
        self.assertTrue(self.all["contract_award_unique_key"].is_unique)
        self.assertEqual(set(self.t.index), {"A1", "A2", "A3", "A4", "A6"})
        self.assertNotIn("A5", self.t.index)

    def test_duplicate_transaction_key_is_dropped_and_modification_is_summed(self):
        a1 = self.t.loc["A1"]
        self.assertEqual(a1["n_tx"], 2)                                  # duplicate T1 removed, T2 counted
        self.assertEqual(a1["obligation_sum_window"], 1500)              # 1000 + 500, not 2500
        self.assertEqual(a1["obl0"], 1000)                               # the base action's own obligation
        self.assertEqual(a1["value0"], 5000)
        self.assertEqual(a1["value_all_options_last"], 7000)             # latest modification
        self.assertEqual(a1["value_all_options_max"], 7000)
        self.assertEqual(a1["last_action_date"], pd.Timestamp("2024-06-01"))
        self.assertEqual(a1["action_date"], pd.Timestamp("2024-01-15"))  # base action date, not the modification's

    def test_competition_labels(self):
        t = self.t
        self.assertEqual(
            {k: (bool(t.loc[k, "competed"]), bool(t.loc[k, "offers_ok"]), bool(t.loc[k, "eval_set"]), bool(t.loc[k, "single"]))
             for k in t.index},
            {"A1": (True, True, True, True),       # competed, 1 offer: single
             "A2": (True, True, True, False),      # competed, 3 offers
             "A3": (True, False, False, False),    # competed, 999 offers: not in the evaluation set
             "A4": (False, True, False, True),     # one offer but not competed: not in the evaluation set
             "A6": (True, False, False, False)})   # competitive delivery order, blank offers
        self.assertEqual(int(t["eval_set"].sum()), 2)
        self.assertEqual(int(t["single"].sum()), 2)
        self.assertEqual(float(t.loc["A2", "offers"]), 3.0)

    def test_fiscal_year_and_award_kind(self):
        t = self.t
        self.assertEqual(t["fy"].to_dict(), {"A1": 2024, "A2": 2026, "A3": 2025, "A4": 2024, "A6": 2025})
        self.assertEqual({k: str(v) for k, v in t["award_kind"].items()},
                         {"A1": "DEF", "A2": "PO", "A3": "DEF", "A4": "DEF", "A6": "IDV"})

    def test_agency_short_names_and_months_at_risk(self):
        t = self.t
        self.assertEqual({k: str(v) for k, v in t["agency"].items()}, {"A1": "VA", "A2": "HHS", "A3": "VA", "A4": "HHS", "A6": "HHS"})
        days = (pd.Timestamp(WINDOW_END) - pd.Timestamp("2024-01-15")).days
        self.assertAlmostEqual(t.loc["A1", "months_at_risk"], round(days / 30.44, 1), places=6)

    def test_agency_filter(self):
        one = self.one
        self.assertEqual(set(one["contract_award_unique_key"]), {"A2", "A4", "A6"})
        self.assertEqual({str(a) for a in one["agency"]}, {"HHS"})
        t = one.set_index("contract_award_unique_key")
        self.assertEqual(int(t["eval_set"].sum()), 1)
        self.assertEqual(t.loc["A2", "fy"], 2026)

    def test_unknown_agency_filter_gives_no_awards_for_that_agency(self):
        with contextlib.redirect_stdout(io.StringIO()):
            tx_only = ba.read_all(self.src, ba.CIVILIAN_COLS, agencies=["Department of Nothing"])
        self.assertEqual(len(tx_only), 0)

    def test_summary_columns_exist(self):
        for c in ("competed", "offers_ok", "eval_set", "single", "fy", "award_kind", "n_tx", "obligation_sum_window",
                  "months_at_risk", "has_sol_id"):
            self.assertIn(c, self.all.columns)

    def test_importing_the_module_did_not_run_the_pipeline(self):
        self.assertTrue(callable(ba.main))
        self.assertEqual(ba.fiscal_year(pd.Series(pd.to_datetime(["2024-09-30", "2024-10-01"]))).tolist(), [2024, 2025])


if __name__ == "__main__":
    unittest.main()
