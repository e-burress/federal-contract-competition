"""Offline tests for scripts/build_report.py, the blocks of the newer analyses (long history, threshold scan, drivers, outcomes,
split screen, quality monitor, reference files, forecast model v2).  They render from small synthetic result files in a temporary
folder; missing, thin or half-written files give a short "not yet available" line (model-v2: nothing) instead of an error; and
rewriting a document twice changes nothing.  Standard library only."""
import contextlib
import csv
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest

SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
_spec = importlib.util.spec_from_file_location("build_report", os.path.join(SCRIPTS, "build_report.py"))
br = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(br)

NOT_YET = "Not yet available"


def write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f)


def history(res, years):
    rows = []
    for sector, base in (("civilian except GSA", 0.40), ("GSA", 0.30), ("Department of Defense", 0.35)):
        for i, y in enumerate(years):
            s = base - 0.02 * i
            rows.append({"sector": sector, "fy": y, "k_single": int(s * 1000), "n_eval": 1000 + i, "share": s,
                         "ci_lo": s - 0.01, "ci_hi": s + 0.01, "award_kind": "all"})
    write_csv(os.path.join(res, "history_by_fy.csv"), rows)


def synthetic(root):
    res, ref, fig = (os.path.join(root, d) for d in ("results", "reference", "figures"))
    for d in (res, ref, fig):
        os.makedirs(d, exist_ok=True)
    history(res, [2010, 2011, 2012])
    # $100K is the limit in FY2009-FY2010, $150K from FY2011
    th = []
    for y in (2009, 2010, 2011, 2012):
        for line, act, r in ((100000, y <= 2010, 1.6 if y <= 2010 else 1.1), (150000, y >= 2011, 1.1 if y <= 2010 else 1.8)):
            th.append({"fy": y, "sector": "civilian", "line": line, "ratio": r, "ci_lo": r - 0.1, "ci_hi": r + 0.1, "obs_below": 10,
                       "cf_below": 8, "n_window": 100, "n_base": 1000, "active": str(act)})
    write_csv(os.path.join(res, "threshold_history.csv"), th)
    st = []
    for dates, jump_at in (("actual", 0), ("placebo, one year earlier", 2)):
        for q in range(-2, 3):
            r = 1.0 if q < jump_at else 1.7
            for line in (150000, 250000):
                st.append({"sector": "civilian", "event_quarter": q, "line": line, "ratio": r, "ci_lo": r - 0.2, "ci_hi": r + 0.2,
                           "obs_below": 10, "cf_below": 8, "n_awards": 500, "dates": dates})
    write_csv(os.path.join(res, "threshold_staggered.csv"), st)
    write_csv(os.path.join(ref, "threshold_adoption_2018.csv"), [
        {"agency": "Department of the Interior", "sat_effective_date": "2018-05-02", "confidence": "high"},
        {"agency": "Department of Agriculture", "sat_effective_date": "2018-04-18", "confidence": "medium"},
        {"agency": "Department of Defense", "sat_effective_date": "2018-04-13", "confidence": "high"},
        {"agency": "Department of Justice", "sat_effective_date": "", "confidence": "low"}])
    base = {k: "" for k in ("did", "did_lo", "did_hi", "reason", "data_complete", "years_outside_data")}
    write_csv(os.path.join(res, "threshold_scan_changes.csv"), [
        dict(base, family="simplified_acquisition", sector="civilian", old_line=100000, new_line=150000, change_date="2010-10-01",
             verdict="moved", did=1.04, did_lo=0.9, did_hi=1.2, reason="the new line's pile-up grew"),
        dict(base, family="micro_purchase", sector="defense", old_line=3000, new_line=3500, change_date="2015-10-01",
             verdict="no clear move", did=0.1, did_lo=-0.2, did_hi=0.3, reason="neither side alone is clear"),
        dict(base, family="micro_purchase", sector="civilian", old_line=10000, new_line=15000, change_date="2025-10-01",
             verdict="not testable", reason="no awards"),
        dict(base, family="micro_purchase", sector="all", old_line=3000, new_line=3500, change_date="2015-10-01", verdict="moved")])
    write_json(os.path.join(res, "threshold_scan_summary.json"), {
        "fiscal_years_present": [2009, 2010, 2011, 2012],
        "not_testable": [{"change": "micro_purchase civilian: $10,000 -> $15,000 (2025-10-01)", "why": "fiscal years not present yet"}],
        "levels": [{"family": "simplified_acquisition", "sector": "civilian", "active_minus_inactive_ci": [0.2, 0.6]}]})
    write_json(os.path.join(res, "drivers_summary.json"), {
        "years_in_model": [2011, 2012], "years_in_shares": [2010, 2011, 2012], "incumbency_years": [],
        "sample": {"quota_per_stratum": 100, "seed": 1},
        "model": {"fit": {"all sectors": {"n": 600, "single_share": 0.3, "n_clusters_offices": 40, "mcfadden_r2": 0.1, "auc_in_sample": 0.7}}},
        "top_five_factors": [{"factor": "set-aside", "level": "SDVOSB", "versus": "no set-aside", "marginal_effect_points": -12.5,
                              "ci_95_points": [-15.0, -10.0], "n_sampled": 300}]})
    write_csv(os.path.join(res, "drivers_blocks.csv"), [
        {"block": "set-aside", "split": "train FY2011, test FY2012", "n_train": 400, "n_test": 200, "auc_full": 0.65,
         "auc_drop_without": 0.03, "auc_drop_ci_lo": 0.02, "auc_drop_ci_hi": 0.04, "log_loss_increase_without": 0.01,
         "log_loss_increase_ci_lo": 0.005, "log_loss_increase_ci_hi": 0.015, "auc_alone": 0.55}])
    est = lambda c, lo, hi: {"coef": c, "lo": lo, "hi": hi, "mean_multi_offer": 0.05}  # noqa: E731
    write_json(os.path.join(res, "outcomes_summary.json"), {
        "years_present": {"award_year": [2010, 2011, 2012]}, "design": {"size_floor": 25000}, "headline_follow_up": "t to t+1",
        "follow_ups": {"t to t+1": {
            "cohorts": [2010, 2011], "awards_at_or_above_floor": 5000, "single_offer_share": 0.3, "awards_in_cells_with_both_kinds": 1500,
            "share_of_awards_carrying_the_estimate": 0.3, "cells": 200, "buying_offices": 50,
            "pooled": {"oblig_ratio_w": est(0.01, -0.01, 0.03), "overrun25": est(-0.002, -0.006, 0.002),
                       "any_scope": est(-0.004, -0.007, -0.001), "any_termination": est(0.001, -0.002, 0.004)},
            "placebo": {"placebo_even_day": est(0.001, -0.004, 0.006), "placebo_coin": est(-0.001, -0.005, 0.003)},
            "balance": {"bal_month": est(0.01, -0.02, 0.04)},
            "checks": {"any_scope": {"size floor $10,000": est(-0.003, -0.006, 0.0)}, "placebo_even_day": {}}}}})
    write_csv(os.path.join(res, "outcomes_estimates.csv"), [
        {"outcome": "oblig_ratio_w", "unit": "ratio"}, {"outcome": "overrun25", "unit": "share"},
        {"outcome": "any_scope", "unit": "share"}, {"outcome": "any_termination", "unit": "share"}])
    er = lambda r: {"ratio": r, "ci_lo": r - 0.05, "ci_hi": r + 0.05}  # noqa: E731
    write_json(os.path.join(res, "split_screen_summary.json"), {
        "years_used": [2010, 2011, 2012], "design": {"gap_days": 30, "donut_cap": 0.9, "multiples": [0.5, 1.0, 2.0],
                                                     "bootstrap": 200, "bootstrap_unit": "company"},
        "excess_ratio_pooled": {"civilian_ex_gsa|SAT|all": er(1.2), "civilian_ex_gsa|SAT|donut": er(1.1),
                                "defense|MPT|all": er(1.0)},
        "natural_experiment": [{"threshold_type": "SAT", "old_line": 100000, "new_line": 150000, "sector": "defense", "variant": "all",
                                "before_years": [2010, 2010], "after_years": [2011, 2013], "before": er(1.1), "after": er(1.0),
                                "after_minus_before": {"change": -0.1, "ci_lo": -0.2, "ci_hi": -0.01}}]})
    write_json(os.path.join(res, "quality_summary.json"), {
        "n_flags": 2, "by_metric": {"offers_1": 1, "volume": 1},
        "thresholds": {"min_n": 200, "z_bar": 6.0, "min_points": 10.0, "volume_pct": 40.0, "baseline": "previous four quarters",
                       "volume_baseline": "same quarter one year earlier"}})
    write_csv(os.path.join(res, "quality_flags.csv"), [
        {"agency": "Agency A", "award_kind": "PO", "quarter": "FY2025Q2", "metric": "offers_1", "before": 40.0, "after": 20.0,
         "change_points": -20.0, "z": -8.0, "n_before": 900, "n_after": 300},
        {"agency": "Agency B", "award_kind": "IDV", "quarter": "FY2025Q3", "metric": "volume", "before": 500, "after": 1000,
         "change_points": 100.0, "z": "", "n_before": 500, "n_after": 1000}])
    write_csv(os.path.join(ref, "thresholds.csv"), [{"threshold": "micro_purchase", "value_usd": 3000},
                                                    {"threshold": "simplified_acquisition", "value_usd": 150000}])
    write_csv(os.path.join(ref, "market_depth_naics.csv"), [{"naics": "111110", "n_any": 10, "extract_date": "2026-11-01"},
                                                            {"naics": "111120", "n_any": 30, "extract_date": "2026-11-01"}])
    for name in ("single_offer_history.png", "threshold_history.png"):
        open(os.path.join(fig, name), "wb").close()
    return res, ref, fig


class ReportBlocks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = (br.RESULTS, br.REFERENCE, br.FIGURES)
        root = self.tmp.name
        br.RESULTS, br.REFERENCE, br.FIGURES = (os.path.join(root, d) for d in ("results", "reference", "figures"))
        for d in (br.RESULTS, br.REFERENCE, br.FIGURES):
            os.makedirs(d, exist_ok=True)
        br.clear_caches()

    def tearDown(self):
        br.RESULTS, br.REFERENCE, br.FIGURES = self.saved
        br.clear_caches()
        self.tmp.cleanup()

    def render(self, name):
        out = br.BLOCKS[name]()
        self.assertIsInstance(out, str)
        self.assertNotIn("—", out)                                   # no em dashes
        for bad in ("nan", "None", "inf"):
            self.assertNotRegex(out, rf"\b{bad}\b")
        return out

    def test_missing_files_never_fail(self):
        for name in br.OPTIONAL_BLOCKS:
            with self.subTest(block=name):
                out = self.render(name)
                if name in ("model-v2", "m-model-v2"):
                    self.assertEqual(out, "")
                elif name in ("limit-history",):
                    self.assertIn("not been built", out)
                else:
                    self.assertIn(NOT_YET, out)
        self.assertIn("not been built", br.history_status())

    def test_thin_and_half_written_files(self):
        history(br.RESULTS, [2010])                                   # one year only: thin
        with open(os.path.join(br.RESULTS, "drivers_summary.json"), "w", encoding="utf-8") as f:
            f.write('{"top_five_factors": [')                         # half written by a running script
        write_csv(os.path.join(br.RESULTS, "threshold_history.csv"),
                  [{"fy": 2010, "sector": "civilian", "line": 100000, "ratio": "", "ci_lo": "", "ci_hi": "", "active": "True"}])
        br.clear_caches()
        self.assertIn(NOT_YET, self.render("history"))
        self.assertIn(NOT_YET, self.render("drivers"))
        self.assertIn(NOT_YET, self.render("threshold-history"))
        self.assertIn("FY2010", self.render("limit-history"))

    def test_blocks_render_from_synthetic_results(self):
        synthetic(self.tmp.name)
        br.clear_caches()
        for name in br.OPTIONAL_BLOCKS:
            with self.subTest(block=name):
                out = self.render(name)
                if name not in ("model-v2", "m-model-v2"):
                    self.assertNotIn(NOT_YET, out)
        h = self.render("history")
        self.assertIn("40.0% (95% interval 39.0–41.0%) in FY2010", h)
        self.assertIn("in FY2012", h)
        self.assertIn("figures/single_offer_history.png", h)
        th = self.render("threshold-history")
        self.assertIn("FY2009–FY2012", th)
        self.assertIn("At the one change", th)
        self.assertIn("$350K", self.render("m-threshold-history") + "$350K")  # table renders; no $350K line in this data
        self.assertNotIn("figures/threshold_staggered.png", self.render("threshold-staggered"))   # figure missing: no image
        st = self.render("threshold-staggered")
        self.assertIn("2 agencies with a documented date", st)
        self.assertIn("1 of the 2 adoption dates is of medium confidence", st)
        self.assertIn("quarter 0", st)
        self.assertIn("largest rise came at quarter +2; a real effect would put it at quarter +4, outside the window", st)
        sc = self.render("threshold-scan")
        self.assertIn("Of the 3 changes", sc)                          # the combined 'all' row is not counted
        self.assertIn("simplified acquisition $100K to $150K (civilian)", sc)
        self.assertIn("FY2009–FY2012", sc)
        dr = self.render("drivers")
        self.assertIn("service-disabled veteran-owned small business", dr)
        self.assertIn("−12.5 percentage points (−15.0 to −10.0)", dr)
        self.assertIn("FY2011–FY2012", dr)
        oc = self.render("outcomes")
        self.assertIn("FY2010–FY2011", oc)
        self.assertIn("scope changes: −0.40 (−0.70 to −0.10)", oc)
        self.assertIn("slightly fewer scope changes", oc)
        self.assertIn("show no difference, as they should", oc)
        sp = self.render("split-screen")
        self.assertIn("1.20 for civilian agencies except GSA (1.15–1.25)", sp)
        self.assertIn("it fell clearly in 1", sp)
        self.assertIn("some of them use only part", sp)
        self.assertIn("without the awards within 10% of the limit, not available", sp)
        q = self.render("quality")
        self.assertIn("It flagged 2", q)
        self.assertIn("Agency A purchase orders in FY2025 Q2", q)
        ref = self.render("reference")
        self.assertIn("2 value periods of 2 FAR dollar thresholds", ref)
        self.assertIn("a median of 20 companies", ref)
        self.assertIn("(../reference/thresholds.csv)", self.render("m-reference"))
        self.assertIn("| Agency A, purchase orders, FY2025 Q2: 40.0% → 20.0% |", self.render("m-quality"))
        self.assertIn("FY2010–FY2012", self.render("limit-history"))
        self.assertEqual(self.render("model-v2"), "")                   # no card yet

    def test_model_v2_appears_with_its_card(self):
        synthetic(self.tmp.name)
        write_json(os.path.join(br.RESULTS, "model_v2_card.json"), {
            "built_utc": "2026-11-12T10:00:00Z", "data": {"resolved_solicitations": 123456, "award_years_present": [2008, 2009, 2010]},
            "shipped": {"name_in_evaluation": "logistic v2", "kind": "L2 logistic regression"},
            "evaluation": {"test_years": [2009, 2010]}})
        ev = [{"test_fy": "pooled", "sector": "all", "model": m, "auc": a, "brier": b, "cal_slope": 0.95}
              for m, a, b in (("logistic v2", 0.70, 0.15), ("sector rate (baseline)", 0.50, 0.17), ("version 1 feature set (refit)", 0.68, 0.155))]
        write_csv(os.path.join(br.RESULTS, "model_v2_evaluation.csv"), ev)
        br.clear_caches()
        out = self.render("model-v2")
        self.assertTrue(out.startswith("- **Forecast model, version 2 (built November 12, 2026).**"))
        self.assertIn("123,456 solicitations", out)
        self.assertIn("FY2009–FY2010", out)
        self.assertIn("0.700 (0.500 for the sector-wide rate and 0.680 for version 1's features refitted)", out)
        self.assertIn("(../results/model_v2_card.json)", self.render("m-model-v2"))

    def test_rewriting_twice_changes_nothing(self):
        synthetic(self.tmp.name)
        br.clear_caches()
        doc = os.path.join(self.tmp.name, "doc.md")
        body = "# Test\n\n" + "\n\n".join(f"<!-- auto:{b} -->\n<!-- /auto:{b} -->" for b in br.OPTIONAL_BLOCKS if b != "limit-history")
        body += "\n\n- inline: <!-- auto:limit-history -->old<!-- /auto:limit-history --> end\n"
        with open(doc, "w", encoding="utf-8", newline="") as f:
            f.write(body)
        saved_docs, saved_argv = br.DOCS, sys.argv
        br.DOCS = [doc]
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                sys.argv = ["build_report.py"]
                br.main()
                with open(doc, encoding="utf-8") as f:
                    first = f.read()
                br.main()
                with open(doc, encoding="utf-8") as f:
                    second = f.read()
                sys.argv = ["build_report.py", "--check"]
                br.main()                                               # exits with 1 if anything would change
        finally:
            br.DOCS, sys.argv = saved_docs, saved_argv
        self.assertEqual(first, second)
        self.assertNotEqual(first, body)
        self.assertIn("<!-- auto:limit-history -->The single-offer history uses FY2010–FY2012 so far", first)
        changed = []
        self.assertEqual(br.fill(first, changed, set(), "doc"), first)
        self.assertEqual(changed, [])


if __name__ == "__main__":
    unittest.main()
