"""Offline tests for run.py's list of steps and pipelines: the completeness check, the one-page brief and the daily sealed forecasts
(steps reconcile, brief, daily-forecast, score-daily).  Nothing is run: subprocess.run is replaced by a stub."""
import contextlib
import importlib.util
import io
import os
import sys
import unittest
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("run_steps", os.path.join(REPO, "run.py"))
rp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rp)


class Steps(unittest.TestCase):
    def test_new_steps(self):
        want = {"reconcile": ("scripts/28_reconcile.py", True), "brief": ("scripts/build_brief.py", False),
                "daily-forecast": ("scripts/31_daily_forecast.py", True), "score-daily": ("scripts/32_score_daily.py", True)}
        for name, (script, online) in want.items():
            with self.subTest(step=name):
                s, args, on, what = rp.STEPS[name]
                self.assertEqual((s, args, on), (script, [], online))
                self.assertTrue(what)
                self.assertTrue(os.path.exists(os.path.join(REPO, script)))

    def test_pipelines(self):
        P = rp.PIPELINES
        for p in ("quick", "all", "daily", "history"):
            with self.subTest(pipeline=p):
                self.assertEqual(P[p][P[p].index("dashboard") + 1], "brief")
        self.assertLess(P["history"].index("reconcile"), P["history"].index("report"))
        for p in P.values():
            self.assertNotIn("daily-forecast", p)                 # they need the private model and batch files
            self.assertNotIn("score-daily", p)
            for name in p:
                self.assertTrue(name in rp.STEPS or name in ("get-snapshot", "get-history"), name)

    def test_verify_skips_live_files(self):
        for name in ("daily_forecast_scores.json", "daily_forecast_scores.csv", "reconciliation.csv", "reconciliation_summary.json"):
            self.assertTrue(name.startswith(rp.LIVE_OR_FROZEN), name)
        for name in ("atlas_civilian_by_agency.csv", "supplier_base_summary.json", "history_by_fy.csv"):
            self.assertFalse(name.startswith(rp.LIVE_OR_FROZEN), name)

    def test_extra_arguments_reach_the_script(self):
        seen = []

        def fake_run(cmd, **kw):
            seen.append(cmd)
            return mock.Mock(returncode=0)
        with mock.patch.object(rp.subprocess, "run", fake_run), mock.patch.object(sys, "argv",
                                                                                 ["run.py", "daily-forecast", "--allow-stale", "--dry-run"]):
            with contextlib.redirect_stdout(io.StringIO()):
                rp.main()
        self.assertEqual(len(seen), 1)
        self.assertTrue(seen[0][2].replace("\\", "/").endswith("scripts/31_daily_forecast.py"))
        self.assertEqual(seen[0][-2:], ["--allow-stale", "--dry-run"])


if __name__ == "__main__":
    unittest.main()
