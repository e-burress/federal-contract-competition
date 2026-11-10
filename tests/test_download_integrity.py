"""Offline tests for the verified download (November 2026): USAspending's download service returned CUT-OFF files (status
"finished", a good ZIP, far fewer rows than the window holds). Every download now counts the rows in the file and compares them with
USAspending's own count. A fake service stands in for USAspending (tests/fake_usaspending.py); no network."""
import contextlib
import csv
import glob
import importlib.util
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
import zipfile
from contextlib import ExitStack
from datetime import date
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fake_usaspending import FakeService  # noqa: E402


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, os.path.join(REPO, path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


DL = load("dl01_test", "scripts/01_download_usaspending.py")
AG = ["Department of the Interior"]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fcc_dl_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def fetch(self, svc, label, start, end, dl=DL, **kw):
        with ExitStack() as st:
            for p in svc.patch(dl):
                st.enter_context(p)
            st.enter_context(contextlib.redirect_stdout(io.StringIO()))
            return dl.fetch_verified(label, start, end, AG, self.tmp, **kw)

    def manifest(self, label):
        with open(os.path.join(self.tmp, f"manifest_{label}.json"), encoding="utf-8") as f:
            return json.load(f)

    def rows_used(self, labels):
        summary, leaves = DL.verification(self.tmp, labels)
        keys = []
        for lf in leaves:
            with zipfile.ZipFile(lf["zip"]) as z:
                for n in z.namelist():
                    if "Transactions" in n:
                        keys += [r["contract_transaction_unique_key"] for r in csv.DictReader(io.TextIOWrapper(z.open(n), encoding="utf-8"))]
        return summary, keys


# ---- the building blocks --------------------------------------------------------------------------------------------------
class Rules(Base):
    def test_tolerance(self):
        self.assertTrue(DL.counts_agree(130_000, 130_000))
        self.assertTrue(DL.counts_agree(130_000, 130_065))         # 0.05 percent of 130,000 = 65 rows
        self.assertFalse(DL.counts_agree(130_000, 130_066))
        self.assertFalse(DL.counts_agree(276_880, 130_000))        # the cut-off file of November 10, 2026
        self.assertTrue(DL.counts_agree(10, 15))                   # at least 5 rows are always allowed
        self.assertFalse(DL.counts_agree(10, 16))
        self.assertTrue(DL.counts_agree(0, 0))
        self.assertFalse(DL.counts_agree(None, 10))
        self.assertFalse(DL.counts_agree(10, None))

    def test_rows_in_zip(self):
        p = os.path.join(self.tmp, "x.zip")
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("Contracts_PrimeTransactions_1.csv", 'a,b\n1,"two\nlines"\n\n3,4\n')
            z.writestr("Contracts_PrimeTransactions_2.csv", "a,b\n5,6\n")
            z.writestr("Contracts_Subawards.csv", "")
            z.writestr("readme.txt", "x\ny\n")
        self.assertEqual(DL.rows_in_zip(p), 3)                     # a quoted line break and a blank line are not rows

    def test_pieces_partition_the_window(self):
        for s, e in [("2007-10-01", "2007-12-31"), ("2008-02-01", "2008-02-29"), ("2026-10-01", "2026-11-09"), ("2026-10-01", "2026-10-02")]:
            p = DL.pieces("W", s, e)
            self.assertTrue(DL.partitions(s, e, p), (s, e, p))
        self.assertEqual([x[0] for x in DL.pieces("Q", "2007-10-01", "2007-12-31")], ["Q_m1", "Q_m2", "Q_m3"])
        self.assertEqual([x[0] for x in DL.pieces("M", "2007-10-01", "2007-10-31")], ["Ma", "Mb"])
        self.assertIsNone(DL.pieces("D", "2007-10-01", "2007-10-01"))
        self.assertFalse(DL.partitions("2007-10-01", "2007-10-31", [["a", "2007-10-01", "2007-10-15"], ["b", "2007-10-15", "2007-10-31"]]))
        self.assertFalse(DL.partitions("2007-10-01", "2007-10-31", [["a", "2007-10-01", "2007-10-14"], ["b", "2007-10-16", "2007-10-31"]]))

    def test_count_request_uses_the_download_filters(self):
        svc = FakeService()
        self.fetch(svc, "W", "2026-10-01", "2026-10-03", award_types=["B", "D"])
        sent = [b for b in svc.jobs.values()][0]["filters"]
        self.assertEqual(sent, DL.tx_filters("2026-10-01", "2026-10-03", AG, ["B", "D"]))
        self.assertEqual(svc.counts, [("2026-10-01", "2026-10-03")])


# ---- the verified download ------------------------------------------------------------------------------------------------
class VerifiedDownload(Base):
    def test_good_file_is_verified_and_recorded(self):
        svc = FakeService()
        self.assertEqual(self.fetch(svc, "Q", "2007-10-01", "2007-12-31"), [])
        m = self.manifest("Q")
        self.assertTrue(m["done"] and m["verified"])
        self.assertEqual((m["expected_rows"], m["received_rows"]), (92 * 2, 92 * 2))
        self.assertIn("checked_utc", m)

    def test_cut_off_file_is_detected_split_and_recovered(self):
        svc = FakeService(cutoff=[("2007-10-01", "2007-12-31")])
        self.assertEqual(self.fetch(svc, "Q", "2007-10-01", "2007-12-31"), [])
        m = self.manifest("Q")
        self.assertFalse(m["done"])
        self.assertFalse(m["verified"])
        self.assertEqual((m["expected_rows"], m["received_rows"]), (184, 92))
        self.assertIn("REJECTED", m["note"])
        self.assertEqual([p[0] for p in m["pieces"]], ["Q_m1", "Q_m2", "Q_m3"])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "CIVILIAN__Q.zip")))     # the cut-off file is never used
        for p in ("Q_m1", "Q_m2", "Q_m3"):
            self.assertTrue(self.manifest(p)["verified"])
        summary, keys = self.rows_used(["Q"])
        self.assertTrue(summary["verified"])
        self.assertEqual(len(keys), 184)
        self.assertEqual(len(set(keys)), 184)                                           # no row twice
        self.assertEqual(summary["expected_rows"], 184)
        self.assertEqual(summary["received_rows"], 184)

    def test_cut_off_everywhere_fails_clearly(self):
        cut = [("2007-10-01", "2007-10-31"), ("2007-10-01", "2007-10-16"), ("2007-10-17", "2007-10-31"), ("2007-10-01", "2007-10-08"),
               ("2007-10-09", "2007-10-16"), ("2007-10-17", "2007-10-24"), ("2007-10-25", "2007-10-31")]
        svc = FakeService(cutoff=cut)                       # the month and every smaller piece come back cut off
        failed = self.fetch(svc, "M", "2007-10-01", "2007-10-31")
        self.assertEqual(sorted(failed), ["Maa", "Mab", "Mba", "Mbb"])
        summary, _ = DL.verification(self.tmp, ["M"])
        self.assertFalse(summary["verified"])
        self.assertTrue(any("not downloaded" in p for p in summary["problems"]))
        self.assertEqual(glob.glob(os.path.join(self.tmp, "*.zip")), [])

    def test_mismatch_splitting_is_limited(self):
        svc = FakeService(drift=1000)                       # the count never matches: two rounds of splitting, then stop
        failed = self.fetch(svc, "M", "2007-10-01", "2007-10-31")
        self.assertEqual(sorted(failed), ["Maa", "Mab", "Mba", "Mbb"])
        self.assertEqual(len(svc.submits), 1 + 2 + 4)

    def test_small_drift_is_accepted(self):
        svc = FakeService(drift=5)
        self.assertEqual(self.fetch(svc, "M", "2007-10-01", "2007-10-31"), [])
        self.assertTrue(self.manifest("M")["verified"])

    def test_too_big_is_split_without_using_the_mismatch_budget(self):
        svc = FakeService(big=[("2007-10-01", "2007-12-31"), ("2007-10-01", "2007-10-31")],
                          cutoff=[("2007-10-01", "2007-10-16"), ("2007-10-01", "2007-10-08")])
        self.assertEqual(self.fetch(svc, "Q", "2007-10-01", "2007-12-31"), [])
        summary, keys = self.rows_used(["Q"])
        self.assertTrue(summary["verified"])
        self.assertEqual(len(keys), 184)
        self.assertEqual(len(set(keys)), 184)

    def test_count_service_down_strict(self):
        svc = FakeService(count_down=True)
        self.assertEqual(self.fetch(svc, "M", "2007-10-01", "2007-10-31"), ["M"])
        m = self.manifest("M")
        self.assertFalse(m["done"])
        self.assertFalse(m["verified"])
        self.assertIsNone(m["expected_rows"])
        self.assertEqual(m["received_rows"], 62)
        summary, _ = DL.verification(self.tmp, ["M"])
        self.assertFalse(summary["verified"])
        # when the count service is back, the file on disk is checked: no new download
        svc.count_down = False
        self.assertEqual(self.fetch(svc, "M", "2007-10-01", "2007-10-31"), [])
        self.assertEqual(len(svc.downloads), 1)
        self.assertTrue(self.manifest("M")["verified"])

    def test_count_service_down_allowed_but_marked(self):
        svc = FakeService(count_down=True)
        self.assertEqual(self.fetch(svc, "M", "2007-10-01", "2007-10-31", allow_unverified=True), [])
        m = self.manifest("M")
        self.assertTrue(m["done"])
        self.assertFalse(m["verified"])
        summary, leaves = DL.verification(self.tmp, ["M"])
        self.assertFalse(summary["verified"])
        self.assertEqual(len(leaves), 1)
        self.assertTrue(any("NOT verified" in p for p in summary["problems"]))

    def test_failed_download(self):
        svc = FakeService(fail_download=True)
        self.assertEqual(self.fetch(svc, "M", "2007-10-01", "2007-10-31"), ["M"])
        self.assertFalse(self.manifest("M")["done"])

    def test_verified_window_is_skipped(self):
        svc = FakeService()
        self.fetch(svc, "M", "2007-10-01", "2007-10-31")
        n = (len(svc.submits), len(svc.counts))
        self.assertEqual(self.fetch(svc, "M", "2007-10-01", "2007-10-31"), [])
        self.assertEqual((len(svc.submits), len(svc.counts)), n)
        self.fetch(svc, "M", "2007-10-01", "2007-10-31", force=True)
        self.assertEqual(len(svc.submits), n[0] + 1)

    def test_old_unverified_file_is_checked_or_replaced(self):
        good = FakeService()
        self.fetch(good, "M", "2007-10-01", "2007-10-31")
        self.fetch(FakeService(cutoff=[("2007-11-01", "2007-11-30")], count_down=True), "N", "2007-11-01", "2007-11-30",
                   allow_unverified=True)
        for label in ("M", "N"):                            # make them look like manifests written before November 2026
            m = self.manifest(label)
            for k in ("verified", "expected_rows", "received_rows", "checked_utc", "allowed_difference", "note"):
                m.pop(k, None)
            m["done"] = True
            with open(os.path.join(self.tmp, f"manifest_{label}.json"), "w", encoding="utf-8") as f:
                json.dump(m, f)
        summary, _ = DL.verification(self.tmp, ["M", "N"])
        self.assertTrue(any("no verification record" in p for p in summary["problems"]))
        svc = FakeService(cutoff=[("2007-11-01", "2007-11-30")])
        self.assertEqual(self.fetch(svc, "M", "2007-10-01", "2007-10-31"), [])
        self.assertEqual(svc.submits, [])                   # the good old file was checked, not downloaded again
        self.assertEqual(self.fetch(svc, "N", "2007-11-01", "2007-11-30"), [])
        self.assertEqual(svc.submits, [("2007-11-01", "2007-11-15"), ("2007-11-16", "2007-11-30")])   # the cut-off one was replaced
        summary, keys = self.rows_used(["M", "N"])
        self.assertTrue(summary["verified"])
        self.assertEqual(len(keys), 61 * 2)
        self.assertEqual(len(set(keys)), 61 * 2)

    def test_incomplete_old_pieces_are_completed(self):
        svc = FakeService()                                 # an older version split Q, then stopped after its second month
        self.fetch(svc, "Q_m1", "2007-10-01", "2007-10-31")
        self.fetch(svc, "Q_m2", "2007-11-01", "2007-11-30")
        with open(os.path.join(self.tmp, "manifest_Q.json"), "w", encoding="utf-8") as f:
            json.dump({"window": "Q", "start": "2007-10-01", "end": "2007-12-31", "split": True}, f)
        self.assertEqual(self.fetch(svc, "Q", "2007-10-01", "2007-12-31"), [])
        self.assertEqual(svc.submits[-1], ("2007-12-01", "2007-12-31"))
        summary, keys = self.rows_used(["Q"])
        self.assertTrue(summary["verified"], summary["problems"])
        self.assertEqual(len(set(keys)), 184)

    def test_piece_recorded_for_other_dates_is_not_used(self):
        svc = FakeService()
        self.fetch(svc, "Ma", "2007-10-01", "2007-10-10")  # a record of the label Ma for other dates
        self.fetch(svc, "Mb", "2007-10-17", "2007-10-31")
        with open(os.path.join(self.tmp, "manifest_M.json"), "w", encoding="utf-8") as f:
            json.dump({"window": "M", "start": "2007-10-01", "end": "2007-10-31", "split": True,
                       "pieces": [["Ma", "2007-10-01", "2007-10-16"], ["Mb", "2007-10-17", "2007-10-31"]]}, f)
        summary, _ = DL.verification(self.tmp, ["M"])
        self.assertFalse(summary["verified"])
        self.assertTrue(any("its record is for 2007-10-01..2007-10-10" in p for p in summary["problems"]))
        self.assertEqual(self.fetch(svc, "M", "2007-10-01", "2007-10-31"), [])       # asked again for the right dates
        self.assertEqual(svc.submits[-1], ("2007-10-01", "2007-10-16"))
        summary, keys = self.rows_used(["M"])
        self.assertTrue(summary["verified"])
        self.assertEqual(len(set(keys)), 62)

    def test_stale_pieces_are_not_used(self):
        svc = FakeService(cutoff=[("2007-10-01", "2007-10-31")])
        self.fetch(svc, "M", "2007-10-01", "2007-10-31")
        self.assertEqual(len(DL.verification(self.tmp, ["M"])[1]), 2)
        svc.cutoff = set()                                  # later the whole month comes back complete
        self.fetch(svc, "M", "2007-10-01", "2007-10-31", force=True)
        summary, keys = self.rows_used(["M"])
        self.assertTrue(summary["verified"])
        self.assertEqual([w["window"] for w in summary["windows"]], ["M"])     # the old pieces Ma, Mb are left out
        self.assertEqual(len(keys), 62)


# ---- every caller goes through the verified download -------------------------------------------------------------------------
class EveryCaller(unittest.TestCase):
    def test_only_the_shared_module_talks_to_the_download_service(self):
        hits = {}
        for p in glob.glob(os.path.join(REPO, "scripts", "*.py")) + [os.path.join(REPO, "run.py")]:
            with open(p, encoding="utf-8") as f:
                src = f.read()
            for pat in (r"http_json\([^\n]*download/transactions", r"download/status", r"generated_downloads"):
                if re.search(pat, src):
                    hits.setdefault(os.path.basename(p), []).append(pat)
        self.assertEqual(set(hits), {"01_download_usaspending.py"}, hits)

    def test_callers_use_the_verified_function(self):
        uses = {"01b_download_threshold_universe.py": "fetch_verified", "01c_download_civilian.py": "dl.fetch_verified",
                "09_resolve_forecast.py": "DL.fetch_verified", "12_resolve_threshold.py": "DL.fetch_verified",
                "13_track_threshold.py": "r12.fetch(", "15_civilian_fytd.py": "civ.fetch_window(",
                "17_history_download.py": "civ.dl.fetch_verified", "32_score_daily.py": "civ.dl.fetch_verified"}
        for name, needle in uses.items():
            with open(os.path.join(REPO, "scripts", name), encoding="utf-8") as f:
                self.assertIn(needle, f.read(), name)

    def test_defense_scoring_download_is_strict(self):
        s32 = load("score32_dl", "scripts/32_score_daily.py")
        h17 = load("hist17_for32", "scripts/17_history_download.py")
        tmp = tempfile.mkdtemp(prefix="fcc_dl32_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        svc = FakeService(count_down=True)
        with ExitStack() as st:
            for p in svc.patch(h17.civ.dl):
                st.enter_context(p)
            st.enter_context(mock.patch.object(s32, "load", return_value=h17))
            st.enter_context(contextlib.redirect_stdout(io.StringIO()))
            failed = s32.download_defense("2026-10-01", "2026-10-31", tmp)
        self.assertEqual(failed, ["DOD_2026-10"])
        self.assertEqual(svc.jobs["job0"]["columns"], s32.TX_COLS)


# ---- 12 / 13: the sealed F2 score is strict ----------------------------------------------------------------------------------
class ThresholdScoreStrict(Base):
    @classmethod
    def setUpClass(cls):
        cls.m = load("resolve12_dl", "scripts/12_resolve_threshold.py")

    def run_fetch(self, svc, start="2026-10-01", end="2026-12-31"):
        with ExitStack() as st:
            for p in svc.patch(self.m.DL):
                st.enter_context(p)
            st.enter_context(mock.patch.object(self.m.dl, "civilian_agencies", return_value=AG))
            st.enter_context(mock.patch.object(self.m, "DATA", self.tmp))
            st.enter_context(contextlib.redirect_stdout(io.StringIO()))
            return self.m.fetch(start, end)

    def test_cut_off_quarter_recovers_from_months(self):
        svc = FakeService(cutoff=[("2026-10-01", "2026-12-31")])
        base = self.run_fetch(svc)
        self.assertEqual(len(base), 92 * 2)
        self.assertTrue(self.m.LAST_VERIFICATION["verified"])
        self.assertEqual(self.m.LAST_VERIFICATION["expected_rows"], 184)
        self.assertEqual(svc.jobs["job0"]["filters"]["award_type_codes"], ["B", "D"])
        self.assertEqual(svc.jobs["job0"]["columns"], self.m.dl.COLUMNS)

    def test_count_service_down_refuses_to_score(self):
        with self.assertRaises(SystemExit) as cm:
            self.run_fetch(FakeService(count_down=True))
        self.assertIn("refusing to score", str(cm.exception.code))

    def test_unrecoverable_cut_off_refuses_to_score(self):
        with self.assertRaises(SystemExit) as cm:
            self.run_fetch(FakeService(drift=10_000))
        self.assertIn("refusing to score", str(cm.exception.code))

    def test_fresh_folder_each_run(self):
        self.run_fetch(FakeService(cutoff=[("2026-10-01", "2026-12-31")]))
        base = self.run_fetch(FakeService())                 # earlier pieces are not mixed in
        self.assertEqual(len(base), 184)
        self.assertEqual(len(glob.glob(os.path.join(self.tmp, "resolve_tmp", "f2_threshold", "*.zip"))), 1)


# ---- 15: the live table -------------------------------------------------------------------------------------------------------
class LiveTable(Base):
    @classmethod
    def setUpClass(cls):
        cls.m = load("fytd15_dl", "scripts/15_civilian_fytd.py")

    def run_main(self, svc):
        out, res = os.path.join(self.tmp, "live"), os.path.join(self.tmp, "results")
        os.makedirs(res)

        def agencies(o):
            with open(os.path.join(o, "agencies.json"), "w", encoding="utf-8") as f:
                json.dump([{"name": AG[0], "abbreviation": "DOI"}], f)
            return AG
        with ExitStack() as st:
            for p in svc.patch(self.m.civ.dl):
                st.enter_context(p)
            st.enter_context(mock.patch.object(self.m, "OUT", out))
            st.enter_context(mock.patch.object(self.m, "RESULTS", res))
            st.enter_context(mock.patch.object(self.m.civ, "civilian_agencies", side_effect=agencies))
            st.enter_context(mock.patch.object(self.m, "http_json", return_value={"last_updated": "10/06/2027"}))
            st.enter_context(contextlib.redirect_stdout(io.StringIO()))
            self.m.main(today=date(2027, 10, 6), argv=[])
        return res

    def test_verified_table_says_so(self):
        svc = FakeService(rows_per_day=4, cutoff=[("2027-10-01", "2027-10-05")])     # 20 rows; the cut-off file has 10
        res = self.run_main(svc)
        with open(os.path.join(res, "live_fy2028_totals.json"), encoding="utf-8") as f:
            t = json.load(f)
        self.assertTrue(t["verified"])
        self.assertEqual(t["download_verification"]["expected_rows"], 20)
        self.assertEqual(t["n_base_awards"], 20)
        self.assertEqual(len(svc.submits), 3)                                          # the quarter, then its two halves

    def test_count_service_down_is_written_but_marked(self):
        res = self.run_main(FakeService(count_down=True))
        with open(os.path.join(res, "live_fy2028_totals.json"), encoding="utf-8") as f:
            t = json.load(f)
        self.assertFalse(t["verified"])
        self.assertIsNone(t["download_verification"]["expected_rows"])

    def test_detected_cut_off_writes_nothing(self):
        with self.assertRaises(SystemExit):
            self.run_main(FakeService(drift=500))
        self.assertEqual(os.listdir(os.path.join(self.tmp, "results")), [])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "live", self.m.CHECK_NAME)))     # so 32 will not score from it

    def test_record_for_the_daily_scorer(self):
        s32 = load("score32_live", "scripts/32_score_daily.py")
        self.assertEqual(s32.LIVE_CHECK, self.m.CHECK_NAME)
        live_glob = os.path.join(self.tmp, "live", "*.zip")
        self.run_main(FakeService(rows_per_day=4, cutoff=[("2027-10-01", "2027-10-05")]))
        with open(os.path.join(self.tmp, "live", self.m.CHECK_NAME), encoding="utf-8") as f:
            rec = json.load(f)
        self.assertTrue(rec["verified"])
        self.assertEqual(rec["files"], ["CIVILIAN__FY2028Q1a.zip", "CIVILIAN__FY2028Q1b.zip"])
        self.assertIsNone(s32.live_problem(live_glob))
        shutil.rmtree(os.path.join(self.tmp, "results"))
        self.run_main(FakeService(count_down=True))
        self.assertIn("NOT VERIFIED", s32.live_problem(live_glob))
        self.assertIsNone(s32.live_problem(os.path.join(self.tmp, "nowhere", "*.zip")))


# ---- 17 + 18: the year gate ---------------------------------------------------------------------------------------------------
class YearGate(Base):
    @classmethod
    def setUpClass(cls):
        cls.b18 = load("build18_dl", "scripts/18_history_build.py")

    def download(self, svc, fy=2014, scope="all"):
        out = os.path.join(self.tmp, "history", f"FY{fy}")
        os.makedirs(out, exist_ok=True)
        h17 = self.b18.H17
        with ExitStack() as st:
            for p in svc.patch(h17.civ.dl):
                st.enter_context(p)
            st.enter_context(mock.patch.object(h17.civ, "civilian_agencies", return_value=AG))
            st.enter_context(contextlib.redirect_stdout(io.StringIO()))
            return h17.one_pass(fy, scope, out)

    def build(self, fy=2014, *flags):
        with mock.patch.object(self.b18, "DATA", self.tmp), mock.patch.object(sys, "argv", ["18", str(fy), *flags]), \
                contextlib.redirect_stdout(io.StringIO()):
            self.b18.main()
        with open(os.path.join(self.tmp, "history", "processed", f"history_FY{fy}.json"), encoding="utf-8") as f:
            return json.load(f)

    def test_verified_year(self):
        self.assertEqual(self.download(FakeService(rows_per_day=1, cutoff=[("2014-04-01", "2014-06-30")])), [])
        d = self.build()
        self.assertTrue(d["verified"])
        self.assertEqual(d["verification"]["expected_rows"], 365 * 2)          # civilian and Defense rows
        self.assertEqual(d["verification"]["received_rows"], 365 * 2)
        self.assertEqual(d["transactions"], 365 * 2)                            # nothing counted twice
        self.assertIn("CIVILIAN__FY2014Q03_m1.zip", d["files"])
        self.assertNotIn("CIVILIAN__FY2014Q03.zip", d["files"])
        self.assertEqual(d["verification"]["problems"], [])
        self.assertTrue(all(w["verified"] for w in d["verification"]["windows"]))

    def test_stray_files_are_ignored(self):
        self.download(FakeService(rows_per_day=1))
        src = os.path.join(self.tmp, "history", "FY2014")
        shutil.copy(os.path.join(src, "CIVILIAN__FY2014Q01.zip"), os.path.join(src, "CIVILIAN__FY2014Q01a.zip"))
        d = self.build()
        self.assertTrue(d["verified"])
        self.assertEqual(d["ignored_files"], ["CIVILIAN__FY2014Q01a.zip"])
        self.assertEqual(d["transactions"], 365 * 2)

    def test_one_sector_year_is_not_verified(self):
        self.download(FakeService(rows_per_day=1), scope="civilian")
        d = self.build()
        self.assertFalse(d["verified"])
        self.assertTrue(any("FY2014M01" in p for p in d["verification"]["problems"]))

    def test_unverified_year_fails_with_require_verified(self):
        self.download(FakeService(rows_per_day=1))
        mp = os.path.join(self.tmp, "history", "FY2014", "manifest_FY2014M05.json")
        with open(mp, encoding="utf-8") as f:
            m = json.load(f)
        del m["verified"]                                    # as written before November 2026
        with open(mp, "w", encoding="utf-8") as f:
            json.dump(m, f)
        with self.assertRaises(SystemExit) as cm:
            self.build(2014, "--require-verified")
        self.assertEqual(cm.exception.code, self.b18.EXIT_NOT_VERIFIED)
        with open(os.path.join(self.tmp, "history", "processed", "history_FY2014.json"), encoding="utf-8") as f:
            d = json.load(f)
        self.assertFalse(d["verified"])
        self.assertTrue(any("no verification record" in p for p in d["verification"]["problems"]))

    def test_workflow_gates(self):
        with open(os.path.join(REPO, ".github", "workflows", "history.yml"), encoding="utf-8") as f:
            h = f.read()
        self.assertIn("18_history_build.py ${{ matrix.fy }} --require-verified", h)
        self.assertLess(h.index("d.get('verified') is True"), h.index("gh release upload"))
        with open(os.path.join(REPO, ".github", "workflows", "history-analysis.yml"), encoding="utf-8") as f:
            a = f.read()
        self.assertLess(a.index("28_reconcile.py --strict"), a.index("python run.py history"))
        self.assertLess(a.index("28_reconcile.py --strict"), a.index("git commit"))


# ---- 28 --strict ----------------------------------------------------------------------------------------------------------------
class StrictReconcile(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rc = load("reconcile28_dl", "scripts/28_reconcile.py")

    def row(self, fy, ours, theirs, sector="download_agencies"):
        return self.rc.reconcile_year(fy, {"transactions": ours, "obligations": float(ours)},
                                      None if theirs is None else {"transactions": theirs, "obligations": float(theirs)}, "now", sector)

    def main(self, rows, *argv):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = self.rc.main(list(argv), run_fn=lambda years=None: (rows, {"verdict": "v"}))
        return code, out.getvalue()

    def test_default_never_fails(self):
        self.assertEqual(self.main([self.row(2014, 1_095_526, 1_136_880)])[0], 0)

    def test_strict_fails_above_half_a_percent(self):
        code, out = self.main([self.row(2014, 1_095_526, 1_136_880), self.row(2017, 100, 100)], "--strict")
        self.assertEqual(code, 1)
        self.assertIn("FY2014", out)
        self.assertNotIn("FY2017: differs", out)

    def test_strict_passes_within_half_a_percent(self):
        rows = [self.row(2024, 1_250_274, 1_250_276), self.row(2025, 995, 1000), self.row(2025, 900, 1000, "all_agencies")]
        self.assertEqual(self.main(rows, "--strict")[0], 0)

    def test_strict_fails_when_a_year_is_not_checked(self):
        self.assertEqual(self.main([self.row(2014, 100, None)], "--strict")[0], 1)
        self.assertEqual(self.main([], "--strict")[0], 1)

    def test_settled_rule(self):
        D = date
        self.assertEqual(self.rc.SETTLED_MONTHS, 15)
        self.assertFalse(self.rc.settled(2025, D(2026, 12, 30)))            # FY2025 ended 2025-09-30: exactly 15 months later
        self.assertTrue(self.rc.settled(2025, D(2026, 12, 31)))             # more than 15 months
        short = [self.row(2025, 990_000, 1_000_000)]                          # 1 percent below USAspending
        notes = []
        self.assertEqual(self.rc.strict_problems(short, today=D(2026, 12, 30), notes=notes), [])
        self.assertIn("expected growth since the download", notes[0])
        self.assertTrue(self.rc.strict_problems(short, today=D(2026, 12, 31))[0].startswith("FY2025: differs"))
        above = [self.row(2025, 1_006_000, 1_000_000)]                        # recent, but we hold MORE than the source
        self.assertIn("MORE transactions", self.rc.strict_problems(above, today=D(2026, 11, 10))[0])
        self.assertEqual(self.rc.strict_problems([self.row(2025, 1_004_000, 1_000_000)], today=D(2026, 11, 10)), [])
        self.assertTrue(self.rc.strict_problems([self.row(2025, 100, None)], today=D(2026, 11, 10)))   # not checked: fails

    def test_strict_fails_when_the_script_cannot_finish(self):
        def boom(years=None):
            raise RuntimeError("down")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.rc.main(["--strict"], run_fn=boom), 1)
            self.assertEqual(self.rc.main([], run_fn=boom), 0)


# ---- run.py get-history: what each year's json says ------------------------------------------------------------------------------
class GetHistoryMessage(Base):
    def test_classifies_years_without_crashing(self):
        rp = load("runpy_dl", "run.py")
        for fy, body in ((2014, {"verified": True}), (2015, {"verified": False, "verification": {"problems": ["FY2015Q03: NOT verified"]}}),
                         (2016, {"transactions": 5}), (2017, None)):
            with open(os.path.join(self.tmp, f"history_FY{fy}.json"), "w", encoding="utf-8") as f:
                f.write("{broken" if body is None else json.dumps(body))
        with contextlib.redirect_stdout(io.StringIO()) as out:
            res = rp.history_verification(self.tmp)
        self.assertEqual(res, {"verified": ["2014"], "not_verified": ["2015"], "no_record": ["2016"], "unreadable": ["2017"]})
        self.assertIn("FY2015 is marked NOT VERIFIED", out.getvalue())
        self.assertIn("FY2016 carry no download verification", out.getvalue())
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(rp.history_verification(os.path.join(self.tmp, "missing"))["verified"], [])

    def test_require_verified_switch(self):
        rp = load("runpy_dl2", "run.py")

        def gate(folder):
            with contextlib.redirect_stdout(io.StringIO()):
                return rp.unverified_years(folder, rp.history_verification(folder))
        for fy, body in ((2014, {"verified": True}), (2015, {"verified": False}), (2016, {"transactions": 5})):
            with open(os.path.join(self.tmp, f"history_FY{fy}.json"), "w", encoding="utf-8") as f:
                json.dump(body, f)
        open(os.path.join(self.tmp, "base_actions_FY2017.parquet"), "wb").close()      # a table without its json
        msg = gate(self.tmp)
        self.assertIn("not verified: FY2015", msg)
        self.assertIn("no verification record: FY2016, FY2017", msg)
        self.assertNotIn("2014", msg)
        for name in ("history_FY2015.json", "history_FY2016.json", "base_actions_FY2017.parquet"):
            os.remove(os.path.join(self.tmp, name))
        self.assertIsNone(gate(self.tmp))
        self.assertIn("no yearly tables", gate(os.path.join(self.tmp, "missing")))
        # the switch reaches get_history, and the default (by hand) stays messages only
        with mock.patch.object(rp, "get_history") as gh:
            rp.run_step("get-history", ["--require-verified"])
            rp.run_step("get-history")
        self.assertEqual([c.kwargs for c in gh.call_args_list], [{"require_verified": True}, {"require_verified": False}])
        with open(os.path.join(REPO, ".github", "workflows", "history-analysis.yml"), encoding="utf-8") as f:
            a = f.read()
        self.assertLess(a.index("python run.py get-history --require-verified"), a.index("python run.py history"))


if __name__ == "__main__":
    unittest.main()
