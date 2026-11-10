"""Offline tests for the corrected growth measures.

On a USAspending transaction row, base_and_all_options_value is the CHANGE made by that action, not a running total.  So:
  * 02_build_awards.py: value_all_options_sum (the sum of the changes) is the award's potential value; max and last are single changes;
  * 03_atlas.py: the published outcomes use obligations over the initial value (oblig_ratio_w, overrun25); value growth only when
    value_all_options_sum is present;
  * 18_history_build.py: award_year has value_sum and exercised_sum; base_actions has vendor_uei_code and vendor_duns_code.
"""
import contextlib
import csv
import hashlib
import importlib.util
import io
import os
import shutil
import sys
import tempfile
import unittest
import zipfile

import numpy as np
import pandas as pd

SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
sys.path.insert(0, SCRIPTS)


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, os.path.join(SCRIPTS, file))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ba = load("build_awards_02_growth", "02_build_awards.py")
a3 = load("atlas_03_growth", "03_atlas.py")          # importing must not run the atlas (it reads data/processed)
hb = load("history_build_18_growth", "18_history_build.py")

VA = "Department of Veterans Affairs"


def write_zip(path, columns, rows, csv_name="FY2020_All_Contracts_Transactions_1.csv"):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columns)
    w.writeheader()
    for r in rows:
        w.writerow({c: r.get(c, "") for c in columns})
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(csv_name, buf.getvalue())


def code(text):
    return hashlib.sha256(("fcc-v1:" + text).encode()).hexdigest()[:16]


class CumulativeValueIn02(unittest.TestCase):
    """An award whose modifications add value: the sum of the changes is the true potential value."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="fcc_test_growth02_")
        def tx(key, mod, date, oblig, value):
            return dict(contract_transaction_unique_key=key, contract_award_unique_key="G1", modification_number=mod,
                        action_date=date, award_type_code="B", award_or_idv_flag="AWARD", awarding_agency_name=VA,
                        extent_competed_code="A", number_of_offers_received="1" if mod == "0" else "",
                        federal_action_obligation=oblig, base_and_all_options_value=value)
        rows = [tx("K0", "0", "2024-01-10", "100000", "100000"),       # base: initial value 100,000
                tx("K1", "P00001", "2024-03-01", "20000", "20000"),      # +20,000
                tx("K2", "P00002", "2024-06-01", "30000", "30000"),      # +30,000
                tx("K3", "P00003", "2024-08-01", "0", "0"),              # administrative change: 0
                tx("K4", "P00004", "2024-09-01", "-10000", "-10000")]    # -10,000
        write_zip(os.path.join(cls.tmp, "part.zip"), ba.CIVILIAN_COLS, rows)
        with contextlib.redirect_stdout(io.StringIO()):
            cls.t = ba.build(src=os.path.join(cls.tmp, "*.zip"), usecols=ba.CIVILIAN_COLS, window_end="2026-09-30") \
                .set_index("contract_award_unique_key").loc["G1"]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_sum_of_changes_is_the_cumulative_potential_value(self):
        self.assertEqual(self.t["value0"], 100_000)
        self.assertEqual(self.t["value_all_options_sum"], 140_000)      # 100,000 + 20,000 + 30,000 + 0 - 10,000
        self.assertEqual(self.t["obligation_sum_window"], 140_000)
        self.assertEqual(self.t["n_tx"], 5)

    def test_max_and_last_are_single_changes_not_totals(self):
        self.assertEqual(self.t["value_all_options_max"], 100_000)     # the base action: growth by "max" would be 0
        self.assertEqual(self.t["value_all_options_last"], -10_000)    # the last change


def premium_frame(n_offices=10, per_cell=10):
    """Synthetic competed PO and DEF awards.  In every cell half are single-offer.  Multi-offer awards are obligated exactly
    their initial value; single-offer awards 150% of it (so overrun25 differs by 1 and oblig_ratio_w by 0.5)."""
    rows, i = [], 0
    for kind in ("PO", "DEF"):
        for q in range(5):                                   # five value levels -> five quintile cells
            for k in range(per_cell):
                single = k % 2 == 0
                v0 = 10_000 * (2 ** q) * (1 + k / 1000)
                rows.append(dict(contract_award_unique_key=f"P{i}", agency="VA", fy=2025, award_kind=kind, value0=v0,
                                 product_or_service_code="R499", awarding_office_code=f"O{i % n_offices}", single=single,
                                 obligation_sum_window=v0 * (1.5 if single else 1.0),
                                 value_all_options_sum=v0 * (1.3 if single else 1.1)))
                i += 1
    return pd.DataFrame(rows)


class ObligationOutcomesIn03(unittest.TestCase):

    def test_importing_did_not_run_the_atlas(self):
        self.assertTrue(callable(a3.main))
        self.assertEqual(a3.lines, [])

    def test_outcome_columns(self):
        ev = pd.DataFrame(dict(contract_award_unique_key=["a", "b", "c", "d", "e", "f"],
                               award_kind=["PO", "PO", "DEF", "DEF", "PO", "IDV"],
                               value0=[1000.0, 1000.0, 2000.0, 1000.0, 0.0, 1000.0],
                               obligation_sum_window=[1250.0, 1249.0, 10_000.0, -50.0, 10.0, 5000.0],
                               product_or_service_code=["R499", "R499", "7030", "7030", "R499", "R499"],
                               single=[True, False, True, False, True, True]))
        pp = a3.premium_sample(ev).set_index("contract_award_unique_key")
        self.assertEqual(list(pp.index), ["a", "b", "c", "d"])             # IDVs and zero initial value are left out
        self.assertEqual(pp["overrun25"].tolist(), [1.0, 0.0, 1.0, 0.0])    # 125% exactly counts
        np.testing.assert_allclose(pp["oblig_ratio_w"], [1.25, 1.249, 3.0, 0.0])   # limited to 0..3
        self.assertNotIn("value_growth25", pp.columns)                       # no value_all_options_sum: no value growth
        self.assertEqual(pp.loc["c", "psc2"], "70")

    def test_value_growth_only_with_the_sum_column(self):
        ev = pd.DataFrame(dict(award_kind=["PO", "PO", "PO"], value0=[1000.0, 1000.0, 1000.0], obligation_sum_window=[0.0, 0.0, 0.0],
                               product_or_service_code=["R499"] * 3, single=[True, False, True],
                               value_all_options_sum=[1300.0, 1100.0, 9000.0]))
        pp = a3.premium_sample(ev)
        self.assertEqual(pp["value_growth25"].tolist(), [1.0, 0.0, 1.0])
        np.testing.assert_allclose(pp["value_growth_w"], [0.3, 0.1, 3.0])

    def test_within_cell_comparison_recovers_the_difference(self):
        pp = a3.premium_sample(premium_frame())
        res = {r["spec"].split(";")[0]: r for r in a3.outcome_rows(pp, a3.OUTCOMES + a3.VALUE_GROWTH)}
        self.assertEqual(set(res), {f"{k}: {y}" for k in ("PO", "DEF") for y in a3.OUTCOMES + a3.VALUE_GROWTH})
        for kind in ("PO", "DEF"):
            self.assertAlmostEqual(res[f"{kind}: overrun25"]["coef"], 1.0, places=9)
            self.assertAlmostEqual(res[f"{kind}: oblig_ratio_w"]["coef"], 0.5, places=9)
            self.assertAlmostEqual(res[f"{kind}: oblig_ratio_w"]["mean_multi_offer"], 1.0, places=9)
            self.assertAlmostEqual(res[f"{kind}: value_growth25"]["coef"], 1.0, places=9)
            self.assertAlmostEqual(res[f"{kind}: value_growth_w"]["coef"], 0.2, places=9)
            self.assertEqual(res[f"{kind}: overrun25"]["n"], 50)
            self.assertEqual(res[f"{kind}: overrun25"]["n_cells"], 5)

    def test_published_table_has_size_then_obligation_rows(self):
        prem = a3.premium_table(a3.premium_sample(premium_frame()))
        self.assertEqual(prem["outcome"].tolist(), ["log_value0", "overrun25", "oblig_ratio_w", "overrun25", "oblig_ratio_w"])
        self.assertFalse(prem["outcome"].str.contains("growth").any())


class HistoryColumnsIn18(unittest.TestCase):
    """award_year: value_sum and exercised_sum, also for an award split across two files; base_actions: the two vendor codes."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="fcc_test_growth18_")
        src = os.path.join(cls.tmp, "history", "FY2020")
        os.makedirs(src)
        cols = ["contract_transaction_unique_key", "modification_number", "action_type_code"] + hb.BASE_COLS + hb.VENDOR_COLS

        def tx(key, award, mod, date, value, exercised, atype="", uei="", duns=""):
            return dict(contract_transaction_unique_key=key, contract_award_unique_key=award, modification_number=mod,
                        action_type_code=atype, action_date=date, awarding_agency_name=VA, award_type_code="C",
                        award_or_idv_flag="AWARD", extent_competed_code="A", number_of_offers_received="2",
                        federal_action_obligation=exercised, base_and_all_options_value=value,
                        base_and_exercised_options_value=exercised, recipient_uei=uei, recipient_duns=duns)
        write_zip(os.path.join(src, "a.zip"), cols, [
            tx("X0", "X", "0", "2019-11-01", "100", "50", uei="UEI123ABC", duns="123456789"),
            tx("X1", "X", "P00001", "2020-02-01", "40", "25", atype="B"),
            tx("Y0", "Y", "0", "2019-12-01", "500", "500", duns="987654321"),
            tx("Z0", "Z", "0", "2020-01-05", "70", "70"),
        ])
        write_zip(os.path.join(src, "b.zip"), cols, [
            tx("X2", "X", "P00002", "2020-03-01", "0", "25", atype="G"),
            tx("Y1", "Y", "P00001", "2020-04-01", "-100", "-100", atype="B"),
        ])
        old_data, old_argv = hb.DATA, sys.argv
        hb.DATA, sys.argv = cls.tmp, ["18_history_build.py", "2020"]
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                hb.main()
        finally:
            hb.DATA, sys.argv = old_data, old_argv
        out = os.path.join(cls.tmp, "history", "processed")
        cls.ay = pd.read_parquet(os.path.join(out, "award_year_FY2020.parquet")).set_index("contract_award_unique_key")
        cls.base = pd.read_parquet(os.path.join(out, "base_actions_FY2020.parquet")).set_index("contract_award_unique_key")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_value_and_exercised_sums(self):
        x, y = self.ay.loc["X"], self.ay.loc["Y"]
        self.assertEqual(x["value_sum"], 140)          # 100 + 40 + 0, across the two files
        self.assertEqual(x["exercised_sum"], 100)      # 50 + 25 + 25
        self.assertEqual(y["value_sum"], 400)
        self.assertEqual(y["exercised_sum"], 400)
        self.assertEqual(self.ay.loc["Z", "value_sum"], 70)

    def test_existing_columns_are_kept(self):
        for c in ("n_tx", "obligation_sum", "value_max", "value_last", "exercised_last", "last_action_date",
                  "n_options", "n_scope_changes", "n_terminations", "fy"):
            self.assertIn(c, self.ay.columns)
        x = self.ay.loc["X"]
        self.assertEqual(x["n_tx"], 3)
        self.assertEqual(x["value_max"], 100)          # single changes, unchanged meaning
        self.assertEqual(x["value_last"], 0)
        self.assertEqual(x["n_options"], 1)
        self.assertEqual(x["n_scope_changes"], 1)

    def test_vendor_codes(self):
        b = self.base
        self.assertEqual(b.loc["X", "vendor_id"], code("UEI123ABC"))                 # unchanged scheme
        self.assertEqual(b.loc["X", "vendor_uei_code"], code("UEI123ABC"))
        self.assertEqual(b.loc["X", "vendor_duns_code"], code("duns:123456789"))
        self.assertEqual(b.loc["Y", "vendor_id"], code("duns:987654321"))
        self.assertEqual(b.loc["Y", "vendor_uei_code"], "")
        self.assertEqual(b.loc["Y", "vendor_duns_code"], code("duns:987654321"))
        self.assertEqual(tuple(b.loc["Z", ["vendor_id", "vendor_uei_code", "vendor_duns_code"]]), ("", "", ""))
        cols = list(b.columns)
        self.assertEqual(cols[cols.index("vendor_id"):cols.index("vendor_id") + 3], ["vendor_id", "vendor_uei_code", "vendor_duns_code"])
        self.assertNotIn("recipient_uei", cols)                                       # raw identifiers stay out

    def test_a_duns_only_year_links_to_a_uei_year(self):
        # the same company: UEI and DUNS on one row (later year), DUNS only on another (earlier year)
        later = hb.vendor_columns(pd.DataFrame({"recipient_uei": ["UEI123ABC"], "recipient_duns": ["123456789"]})).iloc[0]
        earlier = hb.vendor_columns(pd.DataFrame({"recipient_uei": [""], "recipient_duns": ["123456789"]})).iloc[0]
        self.assertNotEqual(later["vendor_id"], earlier["vendor_id"])               # the known limit of vendor_id
        self.assertEqual(later["vendor_duns_code"], earlier["vendor_duns_code"])     # the link
        self.assertEqual(later["vendor_id"], hb.vendor_id("UEI123ABC", "123456789"))


if __name__ == "__main__":
    unittest.main()
