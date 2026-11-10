"""Fast offline tests for scripts/09_resolve_forecast.py (scoring of the sealed single-offer forecast), on tiny synthetic data.
No real solicitation numbers appear here."""
import hashlib
import importlib.util
import json
import math
import os
import sys
import tempfile
import unittest

import pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))
_spec = importlib.util.spec_from_file_location("resolve09", os.path.join(REPO, "scripts", "09_resolve_forecast.py"))
R = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(R)

MADE = pd.Timestamp("2026-10-24")


def items(rows):
    """rows: (solicitation number, agency, p_model, p_baseline)"""
    return pd.DataFrame([{"solicitation_number": s, "agency": a, "p_single_offer": p, "p_baseline_agency": b, "p_office_history": p}
                         for s, a, p, b in rows])


def base(rows):
    """rows: (solicitation id, piid, action date, extent competed, offers, value)"""
    df = pd.DataFrame(rows, columns=["solicitation_identifier", "award_id_piid", "action_date", "extent_competed_code", "offers", "value"])
    df["action_date"] = pd.to_datetime(df["action_date"])
    df["sol_norm"] = df.solicitation_identifier.map(R.norm)
    return df


class NormTests(unittest.TestCase):
    def test_norm(self):
        self.assertEqual(R.norm(" ab-12/c.3 "), "AB12C3")
        self.assertEqual(R.norm(None), "")
        self.assertEqual(R.norm(float("nan")), "")


class MatchingRuleTests(unittest.TestCase):
    def status(self, m, sol):
        return m.loc[m.solicitation_number == sol, "status"].iloc[0]

    def test_normalised_match_and_earliest_on_or_after_date(self):
        it = items([("AA-1", "Dept X", 0.2, 0.1)])
        b = base([("aa 1", "OLD", "2026-10-23", "A", 5, 1),        # before the forecast date: ignored
                  ("AA1", "LATE", "2026-12-01", "A", 3, 1),
                  ("AA.1", "FIRST", "2026-10-24", "A", 1, 1)])      # on the forecast date: counts, and is earliest
        m = R.resolve(it, b, MADE)
        self.assertEqual(m.status.iloc[0], "resolved")
        self.assertEqual(m.award_id_piid.iloc[0], "FIRST")
        self.assertEqual(m.outcome.iloc[0], 1.0)

    def test_same_day_tie_uses_larger_value(self):
        it = items([("T1", "Dept X", 0.2, 0.1)])
        b = base([("T1", "SMALL", "2026-11-01", "A", 1, 10), ("T1", "BIG", "2026-11-01", "A", 4, 99)])
        m = R.resolve(it, b, MADE)
        self.assertEqual(m.award_id_piid.iloc[0], "BIG")
        self.assertEqual(m.outcome.iloc[0], 0.0)

    def test_no_award_is_unresolved(self):
        m = R.resolve(items([("N1", "Dept X", 0.2, 0.1)]), base([("OTHER", "P", "2026-11-01", "A", 2, 1)]), MADE)
        self.assertEqual(m.status.iloc[0], "no_matching_award_yet")
        self.assertTrue(math.isnan(m.outcome.iloc[0]))

    def test_not_competed_is_unresolved_even_if_a_later_award_was_competed(self):
        it = items([("C1", "Dept X", 0.2, 0.1)])
        b = base([("C1", "NOCOMP", "2026-11-01", "B", 1, 1), ("C1", "LATER", "2026-12-01", "A", 1, 1)])   # earliest wins, then fails
        m = R.resolve(it, b, MADE)
        self.assertEqual(m.status.iloc[0], "award_not_competed")

    def test_unusable_offers_are_unresolved(self):
        it = items([("U1", "D", .2, .1), ("U2", "D", .2, .1), ("U3", "D", .2, .1), ("U4", "D", .2, .1)])
        b = base([("U1", "P1", "2026-11-01", "A", None, 1), ("U2", "P2", "2026-11-01", "A", 999, 1),
                  ("U3", "P3", "2026-11-01", "A", 0, 1), ("U4", "P4", "2026-11-01", "CDO", 998, 1)])
        m = R.resolve(it, b, MADE)
        self.assertEqual([self.status(m, s) for s in ("U1", "U2", "U3")], ["offers_blank_or_999"] * 3)
        self.assertEqual(self.status(m, "U4"), "resolved")           # 998 is the largest usable count; CDO counts as competed
        self.assertEqual(m.loc[m.solicitation_number == "U4", "outcome"].iloc[0], 0.0)

    def test_empty_solicitation_number(self):
        m = R.resolve(items([("--", "D", .2, .1)]), base([("", "P", "2026-11-01", "A", 1, 1)]), MADE)
        self.assertEqual(m.status.iloc[0], "no_solicitation_number")

    def test_dry_run_ignores_the_date_rule(self):
        it = items([("D1", "D", .2, .1)])
        b = base([("D1", "P", "2020-01-01", "A", 1, 1)])
        self.assertEqual(R.resolve(it, b, MADE).status.iloc[0], "no_matching_award_yet")
        self.assertEqual(R.resolve(it, b, MADE, ignore_date=True).status.iloc[0], "resolved")

    def test_only_base_actions_are_used(self):
        tx = pd.DataFrame({"modification_number": ["0", "1", "000", "P00002"], "action_date": ["2026-11-01"] * 4,
                           "base_and_all_options_value": ["5"] * 4, "number_of_offers_received": ["1"] * 4,
                           "solicitation_identifier": ["S-1", "S-2", "S-3", "S-4"]})
        b = R.base_actions_from_tx(tx)
        self.assertEqual(sorted(b.sol_norm), ["S1", "S3"])


class ScoreTests(unittest.TestCase):
    def test_tiny_example_by_hand(self):
        y, p = [1, 0, 0, 1], [0.8, 0.2, 0.4, 0.6]
        s = R.scores(y, p)
        self.assertAlmostEqual(s["brier"], (0.04 + 0.04 + 0.16 + 0.16) / 4)
        self.assertAlmostEqual(s["log_loss"], -(2 * math.log(0.8) + 2 * math.log(0.6)) / 4)
        self.assertEqual(s["auc"], 1.0)

    def test_auc_with_ties_and_one_class(self):
        self.assertEqual(R.auc([1, 0, 1, 0], [0.5, 0.5, 0.5, 0.5]), 0.5)
        self.assertAlmostEqual(R.auc([1, 0, 1, 0], [0.9, 0.6, 0.3, 0.1]), 0.75)
        self.assertIsNone(R.auc([0, 0, 0], [0.1, 0.2, 0.3]))

    def test_calibration_bands(self):
        rows = R.calibration_table([1, 0, 0, 1], [0.05, 0.1, 0.15, 0.9])
        self.assertEqual(len(rows), 7)
        self.assertEqual([r["n"] for r in rows], [2, 1, 0, 0, 0, 0, 1])     # 0.1 belongs to the first band (low, high]
        self.assertAlmostEqual(rows[0]["observed_share"], 0.5)
        self.assertIsNone(rows[2]["mean_forecast"])

    def test_summary_numbers_and_privacy(self):
        it = items([("ZZ-100", "Dept A", 0.8, 0.5), ("ZZ-200", "Dept A", 0.2, 0.5), ("ZZ-300", "Dept B", 0.4, 0.5),
                    ("ZZ-400", "Dept B", 0.6, 0.5), ("ZZ-500", "Dept B", 0.3, 0.5), ("ZZ-600", "Dept B", 0.3, 0.5)])
        b = base([("ZZ100", "P1", "2026-11-02", "A", 1, 1), ("ZZ200", "P2", "2026-11-03", "A", 3, 1),
                  ("ZZ300", "P3", "2026-11-04", "A", 2, 1), ("ZZ400", "P4", "2026-11-05", "A", 1, 1),
                  ("ZZ500", "P5", "2026-11-05", "B", 1, 1)])               # ZZ-600 has no award
        m = R.resolve(it, b, MADE)
        fc = {"id": "TEST-F", "made_at_utc": "2026-10-24T00:00:00Z"}
        s = R.build_summary(fc, m, "0" * 64, "2026-11-10", "provisional")
        self.assertEqual((s["n_forecasts"], s["n_resolved"], s["n_unresolved"]), (6, 4, 2))
        self.assertEqual(s["unresolved_reasons"]["award_not_competed"], 1)
        self.assertEqual(s["unresolved_reasons"]["no_matching_award_yet"], 1)
        self.assertAlmostEqual(s["model"]["brier"], 0.1)
        self.assertEqual(s["model"]["auc"], 1.0)
        self.assertAlmostEqual(s["baseline_agency_rate"]["brier"], 0.25)
        self.assertAlmostEqual(s["brier_skill_vs_baseline"], 0.6)
        self.assertTrue(s["provisional"])
        self.assertEqual(s["by_department"]["Dept B"]["n_resolved"], 2)
        self.assertEqual(s["by_department"]["Dept B"]["n_single_offer"], 1)
        text = json.dumps(R._clean(s)) + "\n".join(R.summary_text(s))
        for secret in ("ZZ-100", "ZZ100", "ZZ-600", "P1"):
            self.assertNotIn(secret, text)

    def test_no_resolved_forecasts(self):
        m = R.resolve(items([("Q1", "D", .2, .1)]), base([]), MADE)
        s = R.build_summary({"id": "T", "made_at_utc": "x"}, m, "0" * 64, "2026-11-10", "provisional")
        self.assertEqual(s["n_resolved"], 0)
        self.assertNotIn("model", s)
        self.assertTrue(R.summary_text(s))                                # still prints something sensible

    def test_bootstrap_is_repeatable(self):
        y, pm, pb = [1, 0, 0, 1, 0, 0, 0, 1, 0, 0], [.7, .2, .1, .6, .3, .2, .1, .5, .2, .3], [.4] * 10
        a, b = R.bootstrap_intervals(y, pm, pb, 200), R.bootstrap_intervals(y, pm, pb, 200)
        self.assertEqual(a, b)
        self.assertLess(a["brier_skill_95"][0], a["brier_skill_95"][1])


class FingerprintAndOutputTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.fc = {"id": "TEST-F1", "made_at_utc": "2026-10-24T00:00:00Z",
                   "forecasts": [{"solicitation_number": "ZZ-1", "agency": "Dept A", "p_single_offer": 0.2, "p_baseline_agency": 0.1}]}
        self.path = os.path.join(self.dir, "TEST-F1.json")
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(self.fc, fh)
        with open(self.path, "rb") as fh:
            self.sha = hashlib.sha256(fh.read()).hexdigest()
        self.sums = os.path.join(self.dir, "SHA256SUMS.txt")

    def tearDown(self):
        self.tmp.cleanup()

    def write_sums(self, text):
        with open(self.sums, "w", encoding="utf-8") as fh:
            fh.write(text)

    def test_matching_file_is_accepted_even_with_notes_after_the_name(self):
        self.write_sums(f"{'f' * 64}  OTHER.json  made_at=x\n{self.sha}  TEST-F1.json  made_at=2026-10-24T04:47:57Z\n")
        fc, sha = R.load_checked_forecast(self.path, self.sums)
        self.assertEqual((fc["id"], sha), ("TEST-F1", self.sha))

    def test_wrong_hash_is_refused(self):
        self.write_sums(f"{self.sha}  TEST-F1.json\n")
        with open(self.path, "a", encoding="utf-8") as fh:           # change the file after it was fingerprinted
            fh.write(" ")
        with self.assertRaises(R.ScoreError) as cm:
            R.load_checked_forecast(self.path, self.sums)
        self.assertEqual(cm.exception.code, R.EXIT_REFUSED)
        self.assertIn("REFUSED", str(cm.exception))

    def test_unlisted_file_and_missing_sums_are_refused(self):
        self.write_sums(f"{self.sha}  SOMETHING-ELSE.json\n")
        with self.assertRaises(R.ScoreError) as cm:
            R.load_checked_forecast(self.path, self.sums)
        self.assertEqual(cm.exception.code, R.EXIT_REFUSED)
        os.remove(self.sums)
        with self.assertRaises(R.ScoreError):
            R.load_checked_forecast(self.path, self.sums)

    def test_main_refuses_a_bad_file_with_exit_code_3(self):
        # a real run through main(): the repository's own SHA256SUMS.txt does not list this temp file
        self.assertEqual(R.main([self.path, "--from-processed"]), R.EXIT_REFUSED)

    def test_missing_file_exit_code_2(self):
        self.assertEqual(R.main([os.path.join(self.dir, "nope.json")]), R.EXIT_USAGE)

    def test_locate_forecast(self):
        self.assertEqual(R.locate_forecast(None, self.dir, os.path.join(self.dir, "x"), "TEST-F1"), self.path)
        self.assertIsNone(R.locate_forecast(None, self.dir, os.path.join(self.dir, "x"), "NOT-HERE"))
        self.assertIsNone(R.locate_forecast(os.path.join(self.dir, "nope.json")))

    def test_outputs_split_public_and_private(self):
        it = items([("ZZ-1", "Dept A", 0.2, 0.1)])
        m = R.resolve(it, base([("ZZ1", "P1", "2026-11-02", "A", 1, 1)]), MADE)
        s = R.build_summary(self.fc, m, self.sha, "2026-11-10", "provisional")
        res, priv = os.path.join(self.dir, "results"), os.path.join(self.dir, "private")
        jpath, cpath = R.write_outputs(s, m, res, priv)           # 1 resolved: under MIN_PUBLIC, so nothing is public
        self.assertFalse(os.path.exists(res))
        self.assertTrue(jpath.startswith(priv) and cpath.startswith(priv))
        self.assertTrue(os.path.isfile(jpath))
        with open(cpath, encoding="utf-8") as fh:
            self.assertIn("ZZ-1", fh.read())
        # a dry run never touches the results folder
        s2 = R.build_summary(self.fc, m, self.sha, "2026-11-10", "provisional", dry_run=True)
        res2 = os.path.join(self.dir, "results2")
        j2, _ = R.write_outputs(s2, m, res2, priv)
        self.assertFalse(os.path.exists(res2))
        self.assertTrue(j2.startswith(priv))

    def big_case(self, n_a, n_b, n_c):
        """All forecasts resolved: Dept A (n_a), Dept B (n_b, low probabilities), Dept C (n_c)."""
        rows, brows, k = [], [], 0
        for ag, n in (("Dept A", n_a), ("Dept B", n_b), ("Dept C", n_c)):
            for i in range(n):
                k += 1
                rows.append((f"ZQ-{k}", ag, 0.05 if ag == "Dept B" else 0.3 + 0.01 * i, 0.2))
                brows.append((f"ZQ{k}", f"P{k}", "2026-11-02", "A", 1 if i % 2 else 2, 1))
        m = R.resolve(items(rows), base(brows), MADE)
        return R.build_summary(self.fc, m, self.sha, "2026-11-10", "provisional"), m

    def test_under_minimum_nothing_is_public(self):
        s, m = self.big_case(8, 3, 6)                             # 17 resolved
        self.assertEqual(s["n_resolved"], 17)
        self.assertFalse(R.is_public(s))
        res, priv = os.path.join(self.dir, "results"), os.path.join(self.dir, "private")
        jpath, cpath = R.write_outputs(s, m, res, priv)
        self.assertFalse(os.path.exists(res))
        self.assertTrue(jpath.startswith(priv))
        with open(jpath, encoding="utf-8") as fh:
            self.assertIn("calibration", json.load(fh))           # the private copy keeps everything

    def test_at_minimum_small_cells_are_suppressed(self):
        s, m = self.big_case(12, 3, 6)                            # 21 resolved; Dept B has 3 (under 5)
        self.assertTrue(R.is_public(s))
        res, priv = os.path.join(self.dir, "results"), os.path.join(self.dir, "private")
        jpath, _ = R.write_outputs(s, m, res, priv)
        self.assertTrue(jpath.startswith(res))
        with open(jpath, encoding="utf-8") as fh:
            j = json.load(fh)
        self.assertEqual(sorted(j["by_department"]), ["(other departments)", "Dept A", "Dept C"])
        self.assertEqual(j["by_department"]["(other departments)"]["n_resolved"], 3)
        self.assertIsNone(j["by_department"]["(other departments)"]["n_single_offer"])
        self.assertNotIn("Dept B", json.dumps(j))
        for r in j["calibration"]:
            self.assertTrue(r["n"] is None or r["n"] >= R.MIN_CELL)
        self.assertTrue(any(r.get("suppressed") for r in j["calibration"]))       # the 0.0 to 0.1 band held only 3

    def test_stage(self):
        self.assertEqual(R.stage_for("2026-11-10", {}), "provisional")
        self.assertEqual(R.stage_for("2027-02-28", {"resolution_date": "2027-03-31"}), "interim")
        self.assertEqual(R.stage_for("2027-03-31", {"resolution_date": "2027-03-31"}), "final")


if __name__ == "__main__":
    unittest.main()
