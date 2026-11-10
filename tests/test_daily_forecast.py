"""Fast offline tests for scripts/31_daily_forecast.py (daily sealed forecasts) and scripts/32_score_daily.py (their scoring),
on a tiny synthetic snapshot, a tiny model file saved with forecast_lib, and synthetic award files.
No real solicitation numbers appear here, and nothing is written outside temporary folders."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

import numpy as np
import pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))
import forecast_lib as fl  # noqa: E402


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(REPO, "scripts", filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


D = _load("daily31_test", "31_daily_forecast.py")
S = _load("score32_test", "32_score_daily.py")

NOW = pd.Timestamp("2026-11-10T12:00:00Z")
DAY = pd.Timedelta(days=1)
VA, DOD_SAM = "VETERANS AFFAIRS, DEPARTMENT OF", "DEPT OF DEFENSE"


def notice(sol, typ="Solicitation", posted="2026-11-01", deadline=NOW + 5 * DAY, dept=VA, nid=None, base_type=None, kw_sole=0):
    return {"notice_id": nid or f"N{abs(hash((sol, typ, posted))) % 10**9}", "sol_number": sol, "type": typ,
            "base_type": base_type or typ, "posted_date": pd.Timestamp(posted),
            "response_deadline": None if deadline is None else pd.Timestamp(deadline), "department": dept,
            "sub_tier": dept, "aac_code": sol[:6], "set_aside_code": "SBA", "naics": "541511", "psc": "R499",
            "kw_brand": 0, "kw_sole": kw_sole}


def write_snapshot(folder, rows, date="2026-11-10"):
    df = pd.DataFrame(rows)
    df["posted_date"] = pd.to_datetime(df["posted_date"])
    df["response_deadline"] = pd.to_datetime(df["response_deadline"], utc=True)
    for c in ("kw_brand", "kw_sole"):
        df[c] = df[c].astype("int8")
    for c in ("department", "sub_tier", "aac_code", "type", "base_type", "set_aside_code", "naics", "psc"):
        df[c] = df[c].astype("category")
    os.makedirs(folder, exist_ok=True)
    df.to_parquet(os.path.join(folder, f"sam_active_{date}.parquet"), index=False)


def write_model(path, award_day="2026-02-15"):
    """a minimal version-2 model: a small logistic model and a little award history, saved with the real saver."""
    rng = np.random.default_rng(1)
    n = 60
    aw = pd.DataFrame({"solicitation_identifier": [f"VAOFFC26Q{i:04d}" for i in range(30)] + [f"DODOFF26R{i:04d}" for i in range(30)],
                       "action_date": pd.Timestamp(award_day), "awarding_agency_name": ["Department of Veterans Affairs"] * 30 +
                       ["Department of Defense"] * 30, "awarding_sub_agency_name": "x", "awarding_office_code": ["VAOFFC"] * 30 + ["DODOFF"] * 30,
                       "award_kind": "PO", "extent_competed_code": "A", "offers": rng.integers(1, 4, n).astype(str),
                       "product_or_service_code": "R499", "naics_code": "541511", "base_and_all_options_value": "1000"})
    hist = fl.history_from_awards(fl.prepare_awards(aw))
    hist["crosswalk"] = {"department": {VA: "Department of Veterans Affairs", DOD_SAM: "Department of Defense"}, "subtier": {}}
    model = {"version": "v2", "kind": "logit", "calibration": None, "settings": {}, "trained_on": {},
             "logit": {"intercept": -1.2, "cat": {"sector": {"Department of Defense": 0.4}}, "cont": {}, "bin": {"kw_sole": 1.5}},
             "baselines": {"global": 0.22, "sector": {"Department of Defense": 0.25, "civilian except GSA": 0.2},
                           "agency": {"Department of Veterans Affairs": 0.18}},
             "history": hist}
    fl.save_model(model, path)


def tx_zip(path, rows):
    """rows: (solicitation id, piid, action date, extent, offers, value[, modification number])"""
    df = pd.DataFrame([{"contract_award_unique_key": f"K{i}", "award_id_piid": r[1], "modification_number": r[6] if len(r) > 6 else "0",
                        "action_date": r[2], "awarding_agency_name": "Department of Veterans Affairs", "solicitation_identifier": r[0],
                        "extent_competed_code": r[3], "number_of_offers_received": str(r[4]), "base_and_all_options_value": str(r[5])}
                       for i, r in enumerate(rows)], columns=S.TX_COLS)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("Contracts_PrimeTransactions_1.csv", df.to_csv(index=False))


def read(path, mode="rb"):
    with open(path, mode, **({} if "b" in mode else {"encoding": "utf-8"})) as fh:
        return fh.read()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fcc_daily_test_")
        self.P = {"snap_dir": os.path.join(self.tmp, "snap"), "model_path": os.path.join(self.tmp, "models", "model_v2.json"),
                  "batch_dir": os.path.join(self.tmp, "private", "batches"), "daily_dir": os.path.join(self.tmp, "public", "daily"),
                  "dryrun_dir": os.path.join(self.tmp, "private", "dryrun")}
        self.S = {"batch_dir": self.P["batch_dir"], "daily_dir": self.P["daily_dir"], "live_glob": os.path.join(self.tmp, "live", "*.zip"),
                  "defense_dir": os.path.join(self.tmp, "defense"), "private_dir": os.path.join(self.tmp, "private"),
                  "results_dir": os.path.join(self.tmp, "public", "results")}
        write_model(self.P["model_path"])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def forecast(self, argv=(), now=NOW, out=None):
        with contextlib.redirect_stdout(out or io.StringIO()), mock.patch.dict(os.environ, {"GITHUB_ACTIONS": ""}):
            return D.run(list(argv), paths=self.P, now=now)

    def score(self, argv=()):
        with contextlib.redirect_stdout(io.StringIO()):
            return S.run(list(argv), paths=self.S)

    def batch(self, day="20261110"):
        with open(os.path.join(self.P["batch_dir"], f"PIO-D-{day}.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def records(self, day="20261110"):
        return D.batch_records(self.batch(day)).to_dict(orient="records")

    def public_files(self):
        out = []
        for root in (self.P["daily_dir"], self.S["results_dir"]):
            for dirpath, _, names in os.walk(root):
                out += [os.path.join(dirpath, n) for n in names]
        return out


class SelectionTests(Base):
    def test_rules(self):
        rows = [notice("VAOFFC26Q0001"),                                                   # kept
                notice("VAOFFC26Q0002", typ="Combined Synopsis/Solicitation"),            # kept
                notice("VAOFFC26Q0003", deadline=NOW + pd.Timedelta(hours=12)),             # deadline too close
                notice("VAOFFC26Q0004", deadline=None),                                     # no deadline
                notice("VAOFFC26Q0005", typ="Presolicitation"),                             # not a solicitation
                notice("VAOFFC26Q0006"), notice("VAOFFC26Q0006", typ="Award Notice", posted="2026-11-05"),  # awarded
                notice("SHORT1"),                                                           # number too short to match
                # amended: first version with an old deadline, newest version with a later one -> kept once, first-version inputs
                notice("VA-OFFC-26-Q-0007", posted="2026-10-01", deadline="2026-10-11T00:00:00Z", nid="A1"),
                notice("VAOFFC26Q0007", posted="2026-11-02", deadline=NOW + 9 * DAY, nid="A2"),
                # newest notice is a cancel/amendment type -> not kept
                notice("VAOFFC26Q0008"), notice("VAOFFC26Q0008", typ="Modification/Amendment/Cancel", posted="2026-11-06"),
                # an earlier presolicitation
                notice("VAOFFC26Q0009", typ="Presolicitation", posted="2026-09-01"), notice("VAOFFC26Q0009", posted="2026-10-15")]
        snap = pd.DataFrame(rows)
        sols, funnel = D.select_solicitations(snap, NOW, set())
        self.assertEqual(sorted(sols.sol_norm), ["VAOFFC26Q0001", "VAOFFC26Q0002", "VAOFFC26Q0007", "VAOFFC26Q0009"])
        r7 = sols.set_index("sol_norm").loc["VAOFFC26Q0007"]
        self.assertEqual(r7.first_posted, pd.Timestamp("2026-10-01"))
        self.assertAlmostEqual(r7.window_first_days, 10.0)
        r9 = sols.set_index("sol_norm").loc["VAOFFC26Q0009"]
        self.assertEqual(r9.presol_first_posted, pd.Timestamp("2026-09-01"))
        self.assertEqual(funnel["not_forecast_before"], 4)
        seen = {D.short_hash("VAOFFC26Q0001")}
        sols2, _ = D.select_solicitations(snap, NOW, seen)
        self.assertNotIn("VAOFFC26Q0001", set(sols2.sol_norm))

    def test_features_flow_into_forecast(self):
        snap = pd.DataFrame([notice("VAOFFC26Q0001"), notice("VAOFFC26Q0002", kw_sole=1), notice("DODOFF26R0001", dept=DOD_SAM)])
        sols, _ = D.select_solicitations(snap, NOW, set())
        rec = D.forecast_rows(fl.load_model(self.P["model_path"]), sols, "2026-11-10").set_index("sol_norm")
        self.assertGreater(rec.loc["VAOFFC26Q0002", "p_single_offer"], rec.loc["VAOFFC26Q0001", "p_single_offer"])
        self.assertEqual(rec.loc["DODOFF26R0001", "sector"], "Department of Defense")
        self.assertTrue(((rec.p_single_offer > 0) & (rec.p_single_offer < 1)).all())


class SealingTests(Base):
    def setUp(self):
        super().setUp()
        write_snapshot(self.P["snap_dir"], [notice(f"VAOFFC26Q{i:04d}") for i in range(1, 8)] + [notice("DODOFF26R0001", dept=DOD_SAM)])

    def test_seal_fingerprint_and_ledger(self):
        self.assertEqual(self.forecast(), 0)
        path = os.path.join(self.P["batch_dir"], "PIO-D-20261110.json")
        sha = hashlib.sha256(read(path)).hexdigest()
        self.assertEqual(D.read_sums(self.P["daily_dir"]), {"PIO-D-20261110.json": sha})
        led = pd.read_csv(os.path.join(self.P["daily_dir"], "ledger.csv"), dtype=str)
        self.assertEqual(list(led.columns), D.LEDGER_COLS)
        self.assertEqual(led.batch_sha256.iloc[0], sha)
        self.assertEqual(int(led.n_forecasts.iloc[0]), 8)
        self.assertEqual(int(led.n_defense.iloc[0]), 1)
        self.assertEqual(int(led.n_civilian_except_gsa.iloc[0]), 7)
        b = self.batch()
        self.assertEqual(b["model"]["sha256"], D.sha256_file(self.P["model_path"]))
        self.assertEqual(b["resolution_rule"], D.RESOLUTION_RULE)
        self.assertEqual(len(b["forecasts"]), 8)
        hashes, ids = D.read_seen(self.P["daily_dir"])
        self.assertEqual(hashes, {D.short_hash(x) for x in D.batch_records(b)["sol_norm"]})
        self.assertEqual(ids, {"PIO-D-20261110"})

    def test_same_day_rerun_is_idempotent(self):
        self.assertEqual(self.forecast(), 0)
        before = {p: read(p) for p in self.public_files()}
        batch_before = read(os.path.join(self.P["batch_dir"], "PIO-D-20261110.json"))
        write_snapshot(self.P["snap_dir"], [notice(f"VAOFFC26Q{i:04d}") for i in range(1, 12)])     # more notices later the same day
        self.assertEqual(self.forecast(now=NOW + pd.Timedelta(hours=5)), 0)
        self.assertEqual(os.listdir(self.P["batch_dir"]), ["PIO-D-20261110.json"])
        self.assertEqual(read(os.path.join(self.P["batch_dir"], "PIO-D-20261110.json")), batch_before)
        self.assertEqual({p: read(p) for p in self.public_files()}, before)

    def test_rerun_completes_missing_public_lines_without_changing_the_batch(self):
        self.assertEqual(self.forecast(), 0)
        os.remove(os.path.join(self.P["daily_dir"], "ledger.csv"))          # as if the first run crashed before the ledger
        self.assertEqual(self.forecast(), 0)
        self.assertEqual(D.ledger_ids(self.P["daily_dir"]), {"PIO-D-20261110"})
        self.assertEqual(len(read(os.path.join(self.P["daily_dir"], "SHA256SUMS.txt"), "r").splitlines()), 1)

    def test_changed_batch_is_refused(self):
        self.assertEqual(self.forecast(), 0)
        path = os.path.join(self.P["batch_dir"], "PIO-D-20261110.json")
        with open(path, "ab") as fh:
            fh.write(b" ")
        self.assertEqual(self.forecast(), D.EXIT_REFUSED)

    def test_never_forecasts_a_seen_solicitation(self):
        self.assertEqual(self.forecast(), 0)
        first = {r["sol_norm"] for r in self.records()}
        write_snapshot(self.P["snap_dir"], [notice(f"VAOFFC26Q{i:04d}") for i in range(1, 8)] + [notice("NEWOFF26Q0001")],
                       date="2026-11-11")
        self.assertEqual(self.forecast(now=NOW + DAY), 0)
        second = {r["sol_norm"] for r in self.records("20261111")}
        self.assertEqual(second, {"NEWOFF26Q0001"})
        self.assertFalse(first & second)
        led = pd.read_csv(os.path.join(self.P["daily_dir"], "ledger.csv"), dtype=str)
        self.assertEqual(list(led.batch_id), ["PIO-D-20261110", "PIO-D-20261111"])
        self.assertEqual(led.mean_forecast.fillna("").iloc[1], "")              # fewer than 5 forecasts: no mean published

    def test_nothing_private_in_public_files(self):
        self.assertEqual(self.forecast(), 0)
        sols = [r["sol_norm"] for r in self.records()]
        tx_dir = os.path.join(self.tmp, "live")
        os.makedirs(tx_dir)
        tx_zip(os.path.join(tx_dir, "CIVILIAN__X.zip"), [(s, f"P{i}", "2026-12-01", "A", 1 + i % 3, 100) for i, s in enumerate(sols)])
        self.assertEqual(self.score(), 0)
        text = "".join(read(p, "r") for p in self.public_files())
        for s in sols + ["VAOFFC", "VA-OFFC", "P1", VA, DOD_SAM]:
            self.assertNotIn(s, text.replace("PIO-D-", ""))
        for s in sols:
            self.assertNotIn(f'"{s[:8]}', text)

    def test_dry_run_writes_nothing_public(self):
        self.assertEqual(self.forecast(["--dry-run"]), 0)
        self.assertEqual(self.public_files(), [])
        self.assertFalse(os.path.exists(os.path.join(self.P["batch_dir"], "PIO-D-20261110.json")))
        self.assertTrue(os.path.exists(os.path.join(self.P["dryrun_dir"], "PIO-D-20261110-DRYRUN.json")))
        self.assertEqual(self.forecast(), 0)                                    # a later real run is not blocked by the dry run
        self.assertEqual(len(self.batch()["forecasts"]), 8)

    def test_stale_model_is_refused(self):
        write_model(self.P["model_path"], award_day="2021-08-01")
        self.assertEqual(self.forecast(), D.EXIT_REFUSED)
        self.assertEqual(self.public_files(), [])
        self.assertFalse(os.path.isdir(self.P["batch_dir"]))
        self.assertEqual(self.forecast(["--allow-stale", "--dry-run"]), 0)
        self.assertEqual(self.public_files(), [])

    def test_robot_log_shows_no_single_forecast(self):
        out = io.StringIO()
        self.assertEqual(self.forecast(out=out), 0)                            # 8 forecasts, a real run: counts and mean only
        self.assertNotIn("percentiles", out.getvalue())
        self.assertNotIn("by band", out.getvalue())
        self.assertIn("Mean forecast", out.getvalue())
        out = io.StringIO()
        write_snapshot(self.P["snap_dir"], [notice("NEWOFF26Q0001")], date="2026-11-11")
        self.assertEqual(self.forecast(now=NOW + DAY, out=out), 0)            # a single forecast: nothing about it
        p = self.records("20261111")[0]["p_single_offer"]
        self.assertNotIn(f"{p:.3f}", out.getvalue())
        self.assertNotIn("mean", out.getvalue().lower())
        out = io.StringIO()
        write_snapshot(self.P["snap_dir"], [notice(f"DRYOFF26Q{i:04d}") for i in range(6)], date="2026-11-12")
        self.assertEqual(self.forecast(["--dry-run"], now=NOW + 2 * DAY, out=out), 0)
        self.assertIn("percentiles", out.getvalue())                          # the spread only in a dry run

    def test_partly_written_public_records_never_make_a_second_batch(self):
        self.assertEqual(self.forecast(), 0)
        batch_path = os.path.join(self.P["batch_dir"], "PIO-D-20261110.json")
        saved = read(batch_path)
        # a fresh runner (no local batch) after a run whose seen.txt never reached the repository
        os.remove(os.path.join(self.P["daily_dir"], "seen.txt"))
        os.remove(batch_path)
        self.assertEqual(self.forecast(), D.EXIT_REFUSED)
        self.assertFalse(os.path.exists(os.path.join(self.P["daily_dir"], "seen.txt")))
        with open(batch_path, "wb") as fh:                                     # the file is back: the rerun completes seen.txt
            fh.write(saved)
        self.assertEqual(self.forecast(), 0)
        self.assertEqual(D.read_seen(self.P["daily_dir"])[1], {"PIO-D-20261110"})
        # seen.txt and the ledger have the batch, the seal does not, and the file is gone: no new batch for the same day
        os.remove(os.path.join(self.P["daily_dir"], "SHA256SUMS.txt"))
        os.remove(batch_path)
        write_snapshot(self.P["snap_dir"], [notice(f"VAOFFC26Q{i:04d}") for i in range(1, 12)])
        self.assertEqual(self.forecast(), D.EXIT_REFUSED)
        self.assertFalse(os.path.exists(batch_path))
        self.assertFalse(os.path.exists(os.path.join(self.P["daily_dir"], "SHA256SUMS.txt")))

    def test_seal_is_written_only_after_the_upload(self):
        def failed_upload(path, day):
            raise RuntimeError("upload failed")
        with mock.patch.object(D, "upload_batch", failed_upload), mock.patch.object(D, "fetch_existing_batch", lambda *a: False):
            self.assertEqual(self.forecast(["--upload"]), D.EXIT_GH)
            self.assertEqual(self.public_files(), [])
            self.assertEqual(self.forecast(["--upload"]), D.EXIT_GH)              # the rerun finds the kept batch; still no seal
            self.assertEqual(self.public_files(), [])
        with mock.patch.object(D, "upload_batch", lambda path, day: None):
            self.assertEqual(self.forecast(["--upload"]), 0)
        self.assertEqual(len(D.read_sums(self.P["daily_dir"])), 1)

    def test_ledger_fingerprint_must_match_too(self):
        self.assertEqual(self.forecast(), 0)
        led = os.path.join(self.P["daily_dir"], "ledger.csv")
        text = read(led, "r")
        sha = text.strip().split(",")[-1]
        with open(led, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text.replace(sha, "0" * 64))
        os.remove(os.path.join(self.P["daily_dir"], "SHA256SUMS.txt"))
        self.assertEqual(self.forecast(), D.EXIT_REFUSED)
        self.assertFalse(os.path.exists(os.path.join(self.P["daily_dir"], "SHA256SUMS.txt")))

    def test_github_actions_run_needs_upload(self):
        with contextlib.redirect_stdout(io.StringIO()), mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}):
            self.assertEqual(D.run([], paths=self.P, now=NOW), D.EXIT_REFUSED)
            self.assertEqual(D.run(["--dry-run"], paths=self.P, now=NOW), 0)
        self.assertEqual(self.public_files(), [])
        self.assertFalse(os.path.isdir(self.P["batch_dir"]))

    def test_two_runs_at_once(self):
        real = D.write_once

        def other_run_first(path, data):
            real(path, b"made by the other run")
            real(path, data)
        with mock.patch.object(D, "write_once", other_run_first):
            self.assertEqual(self.forecast(), D.EXIT_REFUSED)
        self.assertEqual(read(os.path.join(self.P["batch_dir"], "PIO-D-20261110.json")), b"made by the other run")
        self.assertEqual(self.public_files(), [])

    def test_records_hold_enough_to_recompute_every_forecast(self):
        rows = [notice("VAOFFC26Q0001"), notice("VAOFFC26Q0002", kw_sole=1, typ="Combined Synopsis/Solicitation"),
                notice("DODOFF26R0001", dept=DOD_SAM, deadline="2026-11-20T21:30:00Z"),
                notice("VA-OFFC-26-Q-0007", posted="2026-10-01", deadline=None, nid="A1"),       # first notice without a deadline
                notice("VAOFFC26Q0007", posted="2026-11-09T08:15:00", deadline=NOW + 9 * DAY, nid="A2"),
                notice("VAOFFC26Q0009", typ="Presolicitation", posted="2026-09-01"), notice("VAOFFC26Q0009", posted="2026-11-08")]
        write_snapshot(self.P["snap_dir"], rows)
        dry = os.path.join(self.P["dryrun_dir"], "PIO-D-20261110-DRYRUN.json")
        self.assertEqual(self.forecast(["--dry-run"]), 0)
        dry1 = read(dry)
        self.assertEqual(self.forecast(["--dry-run"]), 0)
        self.assertEqual(read(dry), dry1)                                       # deterministic: same inputs, same bytes
        self.assertEqual(self.forecast(), 0)
        self.assertEqual(read(os.path.join(self.P["batch_dir"], "PIO-D-20261110.json")), dry1)
        b = self.batch()
        self.assertEqual(b["schema_version"], 1)
        self.assertEqual(b["fields"], D.FIELDS)
        self.assertEqual(b["n_fresh"], 1)                                       # only 0009 was first posted within 3 days
        rec = D.batch_records(b).set_index("sol_norm")
        self.assertEqual(rec.loc["DODOFF26R0001", "deadline_utc"], "2026-11-20T21:30:00+00:00")
        self.assertEqual(rec.loc["DODOFF26R0001", "deadline_date"], "2026-11-20")
        self.assertEqual(rec.loc["VAOFFC26Q0007", "first_posted"], "2026-10-01T00:00:00")   # inputs from the first notice ...
        self.assertTrue(pd.isna(rec.loc["VAOFFC26Q0007", "window_first_days"]))
        self.assertEqual(rec.loc["VAOFFC26Q0007", "deadline_date"], "2026-11-19")            # ... the deadline from the newest
        self.assertAlmostEqual(rec.loc["VAOFFC26Q0007", "days_first_posted_to_forecast"], 40.5)
        self.assertFalse(rec.loc["VAOFFC26Q0007", "fresh"])
        self.assertTrue(rec.loc["VAOFFC26Q0009", "fresh"])
        self.assertEqual(rec.loc["VAOFFC26Q0009", "presol_first_posted"], "2026-09-01T00:00:00")
        for c in ("agency", "sector", "office_key", "naics", "psc", "set_aside_code", "type_newest"):
            self.assertTrue(rec[c].notna().all(), c)
        p = D.recompute(b, fl.load_model(self.P["model_path"]))
        np.testing.assert_allclose(p, D.batch_records(b)["p_single_offer"].to_numpy(float), rtol=0, atol=1e-6)
        led = pd.read_csv(os.path.join(self.P["daily_dir"], "ledger.csv"), dtype=str)
        self.assertEqual(led.n_fresh.iloc[0], "1")

    def test_old_snapshot_and_missing_model(self):
        self.assertEqual(self.forecast(now=NOW + 5 * DAY), D.EXIT_USAGE)
        os.remove(self.P["model_path"])
        self.assertEqual(self.forecast(), D.EXIT_USAGE)
        self.assertEqual(self.public_files(), [])


class ScoringTests(Base):
    def make_batch(self, n, day="2026-11-10"):
        write_snapshot(self.P["snap_dir"], [notice(f"VAOFFC26Q{i:04d}") for i in range(n)], date=day)
        self.assertEqual(self.forecast(now=pd.Timestamp(day + "T12:00:00Z")), 0)
        return [r["sol_norm"] for r in self.records(day.replace("-", ""))]

    def awards(self, rows):
        os.makedirs(os.path.join(self.tmp, "live"), exist_ok=True)
        tx_zip(os.path.join(self.tmp, "live", "CIVILIAN__FY2027Q1.zip"), rows)

    def outcomes(self):
        return pd.read_csv(os.path.join(self.S["private_dir"], S.OUTCOMES_NAME), dtype={"sol_norm": str})

    def public(self):
        with open(os.path.join(self.S["results_dir"], S.PUBLIC_JSON), encoding="utf-8") as fh:
            return json.load(fh)

    def test_outcome_rule(self):
        sols = self.make_batch(6)                                               # offers due 2026-11-15 (NOW + 5 days)
        self.assertEqual({r["deadline_date"] for r in self.records()}, {"2026-11-15"})
        self.awards([(sols[0].lower(), "OLD", "2026-11-09", "A", 5, 1),        # before the forecast date: ignored
                     (sols[0], "BETWEEN", "2026-11-12", "A", 4, 9),             # after the forecast, before offers were due: ignored
                     (sols[0], "FIRST", "2026-11-15", "A", 1, 1),               # on the deadline day: counts
                     (sols[0], "LATER", "2026-12-01", "A", 3, 1),
                     (sols[5], "EARLY", "2026-11-10", "A", 1, 1),               # only an award before the deadline day ...
                     (sols[5], "USED", "2026-11-16", "A", 2, 1),                # ... so the later one is used
                     (sols[1], "NC", "2026-11-20", "C", 1, 1),                  # not competed
                     (sols[2], "BLANK", "2026-11-20", "A", 999, 1),             # offers 999
                     (sols[3], "MOD", "2026-11-20", "A", 1, 1, "P00001")])      # a modification, not a base action
        self.assertEqual(self.score(), 0)
        o = self.outcomes().set_index("sol_norm")
        self.assertEqual(o.loc[sols[0], "status"], "resolved")
        self.assertEqual(o.loc[sols[0], "award_id_piid"], "FIRST")
        self.assertEqual(o.loc[sols[0], "outcome"], 1.0)
        self.assertEqual(o.loc[sols[1], "status"], "award_not_competed")
        self.assertEqual(o.loc[sols[2], "status"], "offers_blank_or_999")
        self.assertEqual(o.loc[sols[3], "status"], "no_matching_award_yet")
        self.assertEqual(o.loc[sols[4], "status"], "no_matching_award_yet")
        self.assertEqual((o.loc[sols[5], "award_id_piid"], o.loc[sols[5], "outcome"]), ("USED", 0.0))
        pub = self.public()
        self.assertEqual(pub["n_resolved"], 2)
        self.assertIsNone(pub["scores"])
        self.assertIn("deadline_date", D.RESOLUTION_RULE)
        self.assertEqual(pub["outcome_rule"], self.batch()["resolution_rule"])
        self.assertFalse(pub["scores_published"])

    def test_earlier_matches_are_kept_when_live_data_move_on(self):
        sols = self.make_batch(3)
        self.awards([(sols[0], "FIRST", "2026-11-20", "A", 1, 1)])
        self.assertEqual(self.score(), 0)
        self.awards([(sols[0], "LATER", "2027-10-05", "A", 4, 1)])           # a new fiscal year: the first award has left the live file
        self.assertEqual(self.score(), 0)
        o = self.outcomes().set_index("sol_norm")
        self.assertEqual(o.loc[sols[0], "award_id_piid"], "FIRST")
        self.assertEqual(o.loc[sols[0], "outcome"], 1.0)

    def test_twenty_resolved_rule_and_small_cells(self):
        sols = self.make_batch(25)
        rows = [(s, f"P{i}", "2026-11-20", "A", 1 if i % 3 == 0 else 2, 1) for i, s in enumerate(sols[:19])]
        self.awards(rows)
        self.assertEqual(self.score(), 0)
        self.assertIsNone(self.public()["scores"])                            # 19 resolved: counts only
        full = json.loads(read(os.path.join(self.S["private_dir"], S.FULL_NAME), "r"))
        self.assertIsNotNone(full["scores"])                                  # the private aggregate has them
        self.awards(rows + [(sols[19], "P19", "2026-11-21", "A", 1, 1)])
        self.assertEqual(self.score(), 0)
        pub = self.public()
        self.assertTrue(pub["scores_published"])
        o = pub["scores"]["overall"]
        self.assertEqual(o["n_resolved"], 20)
        y = np.array([1.0 if i % 3 == 0 else 0.0 for i in range(19)] + [1.0])
        self.assertEqual(o["n_single_offer"], int(y.sum()))
        for k in ("model", "sector_rate", "agency_rate", "office_history"):
            self.assertIn("brier", o[k])
        for band in pub["scores"]["calibration"]:
            self.assertTrue(band["n"] in (None, 0) or band["n"] >= S.MIN_CELL)
        hidden = [b for b in pub["scores"]["calibration"] if b["suppressed"]]
        self.assertNotEqual(len(hidden), 1)                                   # never exactly one hidden band
        csv = pd.read_csv(os.path.join(self.S["results_dir"], S.PUBLIC_CSV))
        self.assertIn("brier_model", csv.columns)

    def test_suppress_hides_a_second_row(self):
        rows = [{"group": "a", "n_resolved": 3, "x": 1}, {"group": "b", "n_resolved": 9, "x": 2}, {"group": "c", "n_resolved": 30, "x": 3}]
        S.suppress(rows, "n_resolved", ["x"])
        self.assertEqual([r["suppressed"] for r in rows], [True, True, False])
        self.assertIsNone(rows[1]["x"])

    def test_suppress_hides_until_the_hidden_rows_are_not_a_small_cell(self):
        rows = [{"group": "a", "n_resolved": 1, "x": 1}, {"group": "b", "n_resolved": 1, "x": 2},
                {"group": "c", "n_resolved": 7, "x": 3}, {"group": "d", "n_resolved": 30, "x": 4}]
        S.suppress(rows, "n_resolved", ["x"])                                 # 1 + 1 hidden would give both away
        self.assertEqual([r["suppressed"] for r in rows], [True, True, True, False])
        rows = [{"group": "a", "n_resolved": 2, "x": 1}, {"group": "b", "n_resolved": 3, "x": 2}, {"group": "c", "n_resolved": 30, "x": 3}]
        S.suppress(rows, "n_resolved", ["x"])                                 # 2 + 3 = 5: enough
        self.assertEqual([r["suppressed"] for r in rows], [True, True, False])
        rows = [{"group": "a", "n_resolved": 0, "x": 1}, {"group": "b", "n_resolved": 9, "x": 2}]
        S.suppress(rows, "n_resolved", ["x"])                                 # nothing small: nothing hidden
        self.assertEqual([r["suppressed"] for r in rows], [False, False])

    def test_scores_are_kept_until_five_more_resolve(self):
        sols = self.make_batch(30)
        rows = [(s, f"P{i}", "2026-11-20", "A", 1 if i % 3 == 0 else 2, 1) for i, s in enumerate(sols[:20])]
        self.awards(rows)
        self.assertEqual(self.score(["--date", "2026-12-01"]), 0)
        first = self.public()["scores"]
        self.assertEqual(first["as_of"], "2026-12-01")
        for k in range(21, 25):                                               # one more resolved each day: same public scores
            self.awards(rows + [(s, f"Q{i}", "2026-11-21", "A", 1, 1) for i, s in enumerate(sols[20:k])])
            self.assertEqual(self.score(["--date", f"2026-12-{k - 19:02d}"]), 0)
            pub = self.public()
            self.assertEqual(pub["n_resolved"], k)
            self.assertEqual(pub["scores"], first)
        full = json.loads(read(os.path.join(self.S["private_dir"], S.FULL_NAME), "r"))
        self.assertEqual(full["scores"]["overall"]["n_resolved"], 24)          # the private aggregate is always current
        self.awards(rows + [(s, f"Q{i}", "2026-11-21", "A", 1, 1) for i, s in enumerate(sols[20:25])])
        self.assertEqual(self.score(["--date", "2026-12-07"]), 0)
        pub = self.public()["scores"]
        self.assertEqual((pub["as_of"], pub["overall"]["n_resolved"]), ("2026-12-07", 25))

    def test_audit_finds_a_forecast_singled_out_across_tables(self):
        # month x freshness: November has backlog only, December has 10 fresh and ONE backlog forecast.  December minus the
        # fresh row (all in December) is that one forecast, although every row has 5 or more.
        lab = pd.DataFrame({"sector": ["c"] * 31, "month": ["2026-11"] * 20 + ["2026-12"] * 11,
                            "freshness": ["backlog"] * 20 + ["fresh"] * 10 + ["backlog"]})
        shown = {"month": ["2026-11", "2026-12"], "freshness": ["fresh", "backlog"], "sector": ["c"]}
        self.assertTrue(S.isolates_small_set(lab, shown))
        self.assertFalse(S.isolates_small_set(lab, {"month": ["2026-11", "2026-12"], "sector": ["c"]}))
        lab2 = lab.copy()
        lab2.loc[:5, "freshness"] = "fresh"                                     # some fresh in November too: a cycle, nothing isolated
        self.assertFalse(S.isolates_small_set(lab2, shown))
        # the same in build_summary: the freshness table is hidden whole, the month table stays
        rng = np.random.default_rng(3)
        m = pd.DataFrame({"batch_id": "B", "forecast_date": np.where(lab["month"] == "2026-11", "2026-11-10", "2026-12-02"),
                          "sol_norm": [f"S{i}" for i in range(31)], "department": "", "agency": "a", "sector": "civilian except GSA",
                          "deadline_date": "", "fresh": (lab["freshness"] == "fresh").to_numpy(), "status": "resolved",
                          "outcome": rng.integers(0, 2, 31).astype(float), "p_single_offer": rng.uniform(0.05, 0.6, 31),
                          "p_sector_rate": 0.2, "p_agency_rate": 0.2, "p_office_history": 0.2})
        pub = S.build_summary(m, {}, {}, "2026-12-20", public=True)
        sc = pub["scores"]
        self.assertEqual(sc["tables_hidden_by_audit"], ["by_freshness"])
        self.assertTrue(all(r["suppressed"] and r["mean_forecast"] is None for r in sc["by_freshness"]))
        self.assertTrue(all(not r["suppressed"] for r in sc["by_month"]))
        m2 = m.assign(fresh=(lab2["freshness"] == "fresh").to_numpy())
        sc2 = S.build_summary(m2, {}, {}, "2026-12-20", public=True)["scores"]
        self.assertEqual(sc2["tables_hidden_by_audit"], [])
        self.assertEqual([(r["group"], r["n_resolved"]) for r in sc2["by_freshness"]], [("backlog", 15), ("fresh", 16)])
        self.assertEqual([c["group"] for c in pub["counts_by_freshness"]], ["fresh", "backlog"])

    def test_tampered_batch_is_refused(self):
        self.make_batch(3)
        path = os.path.join(self.P["batch_dir"], "PIO-D-20261110.json")
        b = json.loads(read(path, "r"))
        b["forecasts"][0][b["fields"].index("p_single_offer")] = 0.99
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(b, fh)
        self.awards([])
        self.assertEqual(self.score(), S.EXIT_REFUSED)
        pub = self.public()
        self.assertEqual(pub["batches"]["refused"], 1)
        self.assertEqual(pub["n_forecasts"], 0)

    def test_unsealed_batch_is_ignored(self):
        os.makedirs(self.P["batch_dir"])
        with open(os.path.join(self.P["batch_dir"], "PIO-D-20261101.json"), "w", encoding="utf-8") as fh:
            json.dump({"id": "PIO-D-20261101", "forecast_date": "2026-11-01", "forecasts": []}, fh)
        self.awards([])
        self.assertEqual(self.score(), 0)
        self.assertEqual(self.public()["batches"]["scored"], 0)

    def test_defense_window(self):
        items = pd.DataFrame({"batch_id": ["B1", "B2"], "sol_norm": ["X1", "X2"], "sector": [fl.DOD, fl.DOD],
                              "forecast_date": ["2026-11-10", "2027-01-05"]})
        today = pd.Timestamp("2027-06-01")
        self.assertEqual(S.defense_window(items, pd.DataFrame(columns=["batch_id", "sol_norm", "status"]), None, today, False),
                         ("2026-11-10", "2027-06-01"))
        prev = pd.DataFrame({"batch_id": ["B1"], "sol_norm": ["X1"], "status": ["resolved"]})
        self.assertEqual(S.defense_window(items, prev, None, today, True)[0], "2027-01-05")
        pub = {"award_data": {"defense": {"last_download": {"from": "2026-11-10", "to": "2027-05-25"}}}}
        self.assertEqual(S.defense_window(items, prev, pub, today, True)[0], "2027-01-25")
        done = pd.DataFrame({"batch_id": ["B1", "B2"], "sol_norm": ["X1", "X2"], "status": ["resolved", "award_not_competed"]})
        self.assertIsNone(S.defense_window(items, done, None, today, True))


if __name__ == "__main__":
    unittest.main()
