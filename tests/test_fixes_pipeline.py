"""Offline tests for the November 2026 fixes: run.py verify, the live table on the first days of a fiscal year (15), the explorer's
choice of live files, the forecast-tracking history (13) and the F2 scoring download (12).  Synthetic data only, no network."""
import contextlib
import csv
import importlib.util
import io
import json
import os
import shutil
import tempfile
import time
import unittest
import zipfile
from datetime import date
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, os.path.join(REPO, path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write_zip(path, columns, rows):
    """A ZIP like USAspending's, holding one Transactions CSV."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(columns)
    w.writerows(rows)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("All_Contracts_PrimeTransactions_1.csv", buf.getvalue())


class TempDirTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fcc_fix_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


# ---- 1. run.py verify -------------------------------------------------------------------------------------------------
class VerifyBaseline(TempDirTest):
    @classmethod
    def setUpClass(cls):
        cls.rp = load("run_fix", "run.py")

    def setUp(self):
        super().setUp()
        self.results = os.path.join(self.tmp, "results")
        self.published = os.path.join(self.tmp, "published_results")
        os.makedirs(self.results)
        self.patches = [mock.patch.object(self.rp, "RESULTS", self.results), mock.patch.object(self.rp, "PUBLISHED", self.published)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        super().tearDown()

    def write(self, folder, name, text):
        with open(os.path.join(folder, name), "w", encoding="utf-8") as f:
            f.write(text)

    def quiet(self, fn, *a):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            fn(*a)
        return out.getvalue()

    def test_nan_equals_nan_only(self):
        cj = self.rp.close_json
        self.assertTrue(cj(float("nan"), float("nan")))
        self.assertTrue(cj({"a": [1, float("nan")]}, {"a": [1, float("nan")]}))
        self.assertFalse(cj(float("nan"), 0.5))
        self.assertFalse(cj(0.5, float("nan")))
        self.assertTrue(cj(1.0, 1.0 + 1e-12))

    def test_without_git_the_first_copy_is_kept(self):
        self.write(self.results, "a.csv", "x\n1\n")
        with mock.patch.object(self.rp, "git_published", return_value=None):
            self.quiet(self.rp.keep_published_copy, "quick")
            self.write(self.results, "a.csv", "x\n2\n")           # a rebuild changes the result
            self.quiet(self.rp.keep_published_copy, "quick")     # a second run must not take the rebuilt file as the baseline
        with open(os.path.join(self.published, "a.csv"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "x\n1\n")

    def test_with_git_the_baseline_is_the_commit(self):
        os.makedirs(self.published)
        self.write(self.published, "a.csv", "x\n9\n")                # stale baseline from an earlier run
        self.write(self.published, "old.csv", "x\n9\n")
        with mock.patch.object(self.rp, "git_published", return_value={"a.csv": b"x\n1\n"}):
            self.quiet(self.rp.keep_published_copy, "quick")
        self.assertEqual(sorted(f for f in os.listdir(self.published) if f.endswith(".csv")), ["a.csv"])
        with open(os.path.join(self.published, "a.csv"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "x\n1\n")

    def test_files_not_rebuilt_are_not_counted_as_matches(self):
        self.write(self.results, "rebuilt.csv", "x\n1\n")
        self.write(self.results, "untouched.csv", "x\n1\n")
        old = time.time() - 3600
        os.utime(os.path.join(self.results, "untouched.csv"), (old, old))
        with mock.patch.object(self.rp, "git_published", return_value={"rebuilt.csv": b"x\n1\n", "untouched.csv": b"x\n1\n"}):
            self.quiet(self.rp.keep_published_copy, "quick")
        os.utime(os.path.join(self.results, "rebuilt.csv"), None)      # written by the pipeline after it started
        out = self.quiet(self.rp.verify)
        self.assertIn("verify: 1 result files match", out)
        self.assertIn("not rebuilt by this command  untouched.csv", out)

    def test_a_rebuilt_file_that_differs_fails(self):
        self.write(self.results, "a.json", json.dumps({"v": 1.0}))
        with mock.patch.object(self.rp, "git_published", return_value={"a.json": json.dumps({"v": 2.0}).encode()}):
            self.quiet(self.rp.keep_published_copy, "quick")
        os.utime(os.path.join(self.results, "a.json"), None)
        with self.assertRaises(SystemExit) as cm, contextlib.redirect_stdout(io.StringIO()):
            self.rp.verify()
        self.assertIn("differences found", str(cm.exception.code))


# ---- 3. 15_civilian_fytd.py on the first days of a fiscal year ----------------------------------------------------------------
class LiveTableEarlyInTheYear(TempDirTest):
    @classmethod
    def setUpClass(cls):
        cls.m = load("fytd_fix", "scripts/15_civilian_fytd.py")

    def setUp(self):
        super().setUp()
        self.out = os.path.join(self.tmp, "live")
        self.res = os.path.join(self.tmp, "results")
        os.makedirs(self.res)
        self.previous = os.path.join(self.res, "live_fy2027_totals.json")
        with open(self.previous, "w", encoding="utf-8") as f:
            f.write('{"fiscal_year": 2027}')

    def agencies(self, out):
        with open(os.path.join(out, "agencies.json"), "w", encoding="utf-8") as f:
            json.dump([{"name": "Department of the Interior", "abbreviation": "DOI"}], f)
        return ["Department of the Interior"]

    def run_main(self, today, rows):
        def fetch_window(label, start, end, agencies, out, force):
            write_zip(os.path.join(out, f"CIVILIAN__{label}.zip"), self.m.b02.CIVILIAN_COLS, rows)
            return []
        with mock.patch.object(self.m, "OUT", self.out), mock.patch.object(self.m, "RESULTS", self.res), \
                mock.patch.object(self.m.civ, "civilian_agencies", side_effect=self.agencies) as ca, \
                mock.patch.object(self.m.civ, "fetch_window", side_effect=fetch_window), \
                mock.patch.object(self.m, "http_json", side_effect=AssertionError("no network")), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.m.main(today=today, argv=[])
        return ca, out.getvalue()

    def assert_previous_untouched(self):
        self.assertEqual(os.listdir(self.res), ["live_fy2027_totals.json"])
        with open(self.previous, encoding="utf-8") as f:
            self.assertEqual(f.read(), '{"fiscal_year": 2027}')

    def test_october_first_downloads_nothing(self):
        ca, out = self.run_main(date(2027, 10, 1), [])
        ca.assert_not_called()
        self.assertIn("FY2028", out)
        self.assertIn("nothing to download", out)
        self.assert_previous_untouched()

    def test_no_awards_loaded_yet(self):
        ca, out = self.run_main(date(2027, 10, 3), [])
        self.assertIn("no award records", out)
        self.assert_previous_untouched()

    def test_awards_but_none_usable_yet(self):
        cols = self.m.b02.CIVILIAN_COLS
        row = dict.fromkeys(cols, "")
        row.update(contract_transaction_unique_key="t1", contract_award_unique_key="a1", modification_number="0",
                   action_date="2027-10-01", award_type_code="B", awarding_agency_name="Department of the Interior",
                   extent_competed_code="C", number_of_offers_received="1", base_and_all_options_value="100",
                   federal_action_obligation="100", base_and_exercised_options_value="100")
        ca, out = self.run_main(date(2027, 10, 3), [[row[c] for c in cols]])
        self.assertIn("too few to compute shares", out)
        self.assert_previous_untouched()

    def test_quarters_on_october_first(self):
        self.assertEqual(self.m.quarters(2028, date(2027, 9, 30)), [])
        self.assertEqual(self.m.quarters(2028, date(2027, 10, 1)), [("FY2028Q1", "2027-10-01", "2027-10-01")])


# ---- 4. build_explorer.py picks the newest live year -------------------------------------------------------------------------
class ExplorerLiveYear(TempDirTest):
    @classmethod
    def setUpClass(cls):
        cls.bx = load("explorer_fix", "scripts/build_explorer.py")

    def live(self, fy, share):
        with open(os.path.join(self.tmp, f"live_fy{fy}_totals.json"), "w", encoding="utf-8") as f:
            json.dump({"fiscal_year": fy, "data_from": f"{fy - 1}-10-01", "data_to": f"{fy - 1}-11-08"}, f)
        with open(os.path.join(self.tmp, f"live_fy{fy}_by_agency.csv"), "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["agency", "abbreviation", "k_single", "n_eval", "share", "ci_lo", "ci_hi"])
            w.writerow(["Department of the Interior", "DOI", 10, 100, share, 0.05, 0.2])

    def test_newest_year_is_used(self):
        with open(os.path.join(self.tmp, "atlas_civilian_by_agency.csv"), "w", encoding="utf-8", newline="") as f:
            f.write("agency,abbreviation,k_single,n_eval,share,ci_lo,ci_hi\nDepartment of the Interior,DOI,30,100,0.3,0.2,0.4\n")
        self.live(2027, 0.1)
        self.live(2028, 0.2)
        rd = self.bx.Reader(self.tmp)
        agencies, meta = self.bx.build_agencies(rd)
        self.assertEqual(meta["sofar"]["fy"], 2028)
        self.assertEqual(agencies[0]["sofar"][0], 2028)
        self.assertEqual(agencies[0]["sofar"][3], 0.2)
        self.assertIn("live_fy2028_by_agency.csv", rd.used)
        self.assertNotIn("live_fy2027_totals.json", rd.used)

    def test_no_live_files(self):
        agencies, meta = self.bx.build_agencies(self.bx.Reader(self.tmp))
        self.assertNotIn("sofar", meta)


# ---- 5. 13_track_threshold.py history: one block per day -----------------------------------------------------------------
class TrackingHistory(TempDirTest):
    @classmethod
    def setUpClass(cls):
        cls.m = load("track_fix", "scripts/13_track_threshold.py")

    def read(self, path):
        with open(path, newline="", encoding="utf-8") as f:
            return list(csv.reader(f))

    def test_same_day_replaces_and_other_days_stay(self):
        hist = os.path.join(self.tmp, "h.csv")
        day1 = [["2026-11-08", "2026-11-07", 10, "q", 1.5, 1.0, 2.0]]
        self.m.update_history(hist, "2026-11-08", day1)
        self.m.update_history(hist, "2026-11-09", [["2026-11-09", "2026-11-08", 12, "q", 1.6, None, None]])
        self.m.update_history(hist, "2026-11-09", [["2026-11-09", "2026-11-08", 13, "q", 1.7, 1.1, 2.1]])   # second run, same day
        rows = self.read(hist)
        self.assertEqual(rows[0], self.m.HEADER)
        self.assertEqual([r[0] for r in rows[1:]], ["2026-11-08", "2026-11-09"])
        self.assertEqual(rows[2][2], "13")
        self.assertEqual(os.listdir(self.tmp), ["h.csv"])                # no temporary file left behind

    def test_committed_history_has_no_duplicates(self):
        rows = self.read(os.path.join(REPO, "results", "tracking_PIO-F2-threshold-fy2027q1-20261024_history.csv"))
        keys = [(r[0], r[3]) for r in rows[1:]]
        self.assertEqual(len(keys), len(set(keys)))


# ---- 9. 12_resolve_threshold.py download checks and safe writing ------------------------------------------------------------
class ResolveThresholdDownload(TempDirTest):
    @classmethod
    def setUpClass(cls):
        cls.m = load("resolve_fix", "scripts/12_resolve_threshold.py")

    def run_fetch(self, start, end, rows_by_window, download_ok=True):
        """rows_by_window: {(start, end): total_rows reported by USAspending}."""
        m = self.m

        def http_json(url, method="GET", body=None, ua=None, **kw):
            if method == "POST":
                tp = body["filters"]["time_period"][0]
                key = f"{tp['start_date']}_{tp['end_date']}"
                return {"file_name": key + ".zip", "file_url": "https://example.invalid/" + key + ".zip"}
            key = url.split("file_name=")[1][:-4]
            s, e = key.split("_")
            return {"status": "finished", "total_rows": rows_by_window.get((s, e), 1), "total_size": 10}

        def download_file(url, dest, ua=None):
            if not download_ok:
                return None
            key = os.path.basename(dest)[:-4]
            s = key.split("_")[0]
            row = {c: "" for c in m.dl.COLUMNS}
            row.update(contract_transaction_unique_key="t" + key, contract_award_unique_key="a" + key, modification_number="0",
                       action_date=s, award_type_code="B", base_and_all_options_value="300000", federal_action_obligation="300000")
            write_zip(dest, m.dl.COLUMNS, [[row[c] for c in m.dl.COLUMNS]])
            return os.path.getsize(dest)

        with mock.patch.object(m, "http_json", side_effect=http_json), mock.patch.object(m, "download_file", side_effect=download_file), \
                mock.patch.object(m.dl, "civilian_agencies", return_value=["Department of the Interior"]), \
                mock.patch.object(m, "DATA", self.tmp), contextlib.redirect_stdout(io.StringIO()):
            return m.fetch(start, end)

    def test_failed_download_stops(self):
        with self.assertRaises(SystemExit) as cm:
            self.run_fetch("2026-10-01", "2026-12-31", {}, download_ok=False)
        self.assertIn("failed", str(cm.exception.code))

    def test_capped_quarter_is_split_into_months(self):
        base = self.run_fetch("2026-10-01", "2026-12-31", {("2026-10-01", "2026-12-31"): 500_000})
        self.assertEqual(sorted(base.action_date.dt.month), [10, 11, 12])

    def test_capped_month_is_refused(self):
        with self.assertRaises(SystemExit) as cm:
            self.run_fetch("2026-10-01", "2026-12-31", {("2026-10-01", "2026-12-31"): 500_000, ("2026-11-01", "2026-11-30"): 499_999})
        self.assertIn("refusing to score", str(cm.exception.code))

    def test_uncapped_quarter_is_one_download(self):
        base = self.run_fetch("2026-10-01", "2026-12-31", {})
        self.assertEqual(len(base), 1)

    def test_write_atomic(self):
        path = os.path.join(self.tmp, "score.json")
        with self.assertRaises(TypeError):
            self.m.write_atomic(path, {"ok": 1, "bad": object()})     # fails halfway through writing
        self.assertEqual(os.listdir(self.tmp), [])                    # no half-written score that would block re-scoring
        self.m.write_atomic(path, {"ok": 1})
        with open(path, encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"ok": 1})
        self.assertEqual(os.listdir(self.tmp), ["score.json"])


if __name__ == "__main__":
    unittest.main()
