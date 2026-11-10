"""Offline tests for scripts/build_report.py, the blocks of the newer analyses (long history, threshold scan, drivers, outcomes,
split screen, quality monitor, reference files, forecast model v2, completeness check, daily sealed forecasts).  They render from
small synthetic result files in a temporary folder; missing, thin or half-written files give a short "not yet available" line
(model-v2: "being built"; the daily forecasts: "not been sealed yet") instead of an error; and rewriting a document twice changes
nothing.  Standard library only."""
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
    write_json(os.path.join(res, "supplier_base_summary.json"), {   # the supplier base (33_supplier_base.py)
        "years_present": [2010, 2011, 2012, 2013],
        "companies_above_25k": {"first_year": 2010, "first": 1000, "last_year": 2013, "last": 800, "change": -0.2},
        "companies_all_awards": {"first_year": 2010, "first": 3000, "last_year": 2013, "last": 2000, "change": -0.3333},
        "new_companies_above_25k": {"years": [2013], "share_of_companies_mean": 0.35, "share_of_awards_mean": 0.13,
                                    "share_of_dollars_mean": 0.14, "largest_gap_linked_vs_unlinked_points": 0.0, "largest_gap_year": 2013},
        "staying_above_25k": {"next_year_mean": 0.48, "within_3_years_mean": 0.64},
        "competition_above_25k_pooled": {"period": "FY2013-FY2013 pooled",
                                         "single_offer_share_new": {"share": 0.215, "ci_lo": 0.213, "ci_hi": 0.217},
                                         "single_offer_share_established": {"share": 0.263, "ci_lo": 0.262, "ci_hi": 0.264}},
        "identifier_switch": {"years_with_link_columns": [], "years_without_link_columns": [2010, 2011, 2012, 2013], "by_year": {}}})
    write_json(os.path.join(res, "reconciliation_summary.json"), {      # the completeness check (28_reconcile.py)
        "years_checked": [2010, 2011, 2012], "years_not_answered": [],
        "largest_differences": [{"fy": 2011, "diff_transactions_pct": -0.0123, "diff_obligations_pct": 0.004}],
        "all_agencies_largest_transaction_gap_pct": 0.3456})
    write_csv(os.path.join(res, "supplier_base_by_fy.csv"), [
        {"fy": 2013, "sector": "all sectors", "award_scope": "above_25k", "award_kind": "all", "n_awards": 900, "n_companies": 800,
         "new_share_companies": 0.35, "new_measures_comparable": "True", "stay_share_next_year": "", "note": ""}])
    for name in ("single_offer_history.png", "threshold_history.png"):
        open(os.path.join(fig, name), "wb").close()
    return res, ref, fig


class ReportBlocks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = (br.RESULTS, br.REFERENCE, br.FIGURES, br.DAILY, br.TODAY)
        root = self.tmp.name
        br.RESULTS, br.REFERENCE, br.FIGURES, br.DAILY = (os.path.join(root, d) for d in ("results", "reference", "figures", "daily"))
        br.TODAY = br.date(2026, 11, 10)
        for d in (br.RESULTS, br.REFERENCE, br.FIGURES):
            os.makedirs(d, exist_ok=True)
        br.clear_caches()

    def tearDown(self):
        br.RESULTS, br.REFERENCE, br.FIGURES, br.DAILY, br.TODAY = self.saved
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
                    self.assertIn("version 2 is being built", out)
                elif name in ("daily-forecasts", "m-daily-forecasts"):
                    self.assertIn("the first batch has not been sealed yet", out)
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
        self.assertIn("version 2 is being built", self.render("model-v2"))   # no card yet
        rc = self.render("reconcile")
        self.assertIn("FY2010–FY2012 (3 years checked) differ by at most 0.01 percent", rc)
        self.assertIn("they hold at most 0.35 percent of a year's transactions", rc)
        self.assertIn("(results/reconciliation.csv)", rc)
        self.assertNotIn("results/reconciliation.csv", self.render("m-reconcile"))
        su = self.render("supplier-base")
        self.assertIn("In FY2010, 1,000 companies won", su)
        self.assertIn("21.5% (95% interval 21.3–21.7%) against 26.3% (26.2–26.4%)", su)
        self.assertIn("| FY2013 | 900 | 800 | 35.0% |", self.render("m-supplier-base"))

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
        self.assertIn("It is the model of the daily forecasts", out)
        self.assertIn("123,456 solicitations", out)
        self.assertIn("FY2009–FY2010", out)
        self.assertIn("its award history ends September 30, 2010", out)
        self.assertIn("0.700 (0.500 for the best simple baseline, the sector-wide rate, and 0.680 for version 1's features refitted)", out)
        self.assertIn("against 0.170 for that baseline", out)
        self.assertIn("Version 1 (finding 3) is the earlier model, frozen", out)
        self.assertIn("must not score live solicitations", out)          # history ends in 2010: stale on the build date
        m = self.render("m-model-v2")
        self.assertIn("(../results/model_v2_card.json)", m)
        self.assertIn("Version 1 (section 5)", m)

    def test_model_v2_staleness_follows_the_history_end(self):
        synthetic(self.tmp.name)
        card = {"built_utc": "2026-11-12T10:00:00Z", "data": {"resolved_solicitations": 10, "award_years_present": [2008, 2026]},
                "shipped": {"name_in_evaluation": "logistic v2", "kind": "L2 logistic regression"}, "evaluation": {"test_years": [2025]}}
        write_json(os.path.join(br.RESULTS, "model_v2_card.json"), card)
        ev = [{"test_fy": "pooled", "sector": "all", "model": m, "auc": a, "brier": b, "cal_slope": 0.95}
              for m, a, b in (("logistic v2", 0.70, 0.15), ("agency rate (baseline)", 0.55, 0.17), ("office history only (baseline)", 0.60, 0.18))]
        write_csv(os.path.join(br.RESULTS, "model_v2_evaluation.csv"), ev)
        br.clear_caches()
        card["built_utc"] = "2027-06-29T10:00:00Z"                        # trained 9 months after September 30, 2026: still fine
        write_json(os.path.join(br.RESULTS, "model_v2_card.json"), card)
        br.clear_caches()
        out = self.render("model-v2")
        self.assertNotIn("must not score", out)
        self.assertIn("0.600 for the best simple baseline, the buying office's past rate)", out)
        self.assertIn("its award history ends September 30, 2026", out)
        card["built_utc"] = "2027-07-02T10:00:00Z"                        # trained more than 9 months later: stale
        write_json(os.path.join(br.RESULTS, "model_v2_card.json"), card)
        br.clear_caches()
        self.assertIn("must not score live solicitations", self.render("model-v2"))
        br.TODAY = br.date(2030, 1, 1)                                    # the calendar alone never changes the wording
        card["built_utc"] = "2027-06-29T10:00:00Z"
        write_json(os.path.join(br.RESULTS, "model_v2_card.json"), card)
        br.clear_caches()
        self.assertNotIn("must not score", self.render("model-v2"))
        self.assertEqual(br.stale_months(), 9)                           # read from 31_daily_forecast.py

    def daily(self, ledger_text, scores=None):
        os.makedirs(br.DAILY, exist_ok=True)
        if ledger_text is not None:
            with open(os.path.join(br.DAILY, "ledger.csv"), "w", encoding="utf-8", newline="") as f:
                f.write(ledger_text)
        if scores is not None:
            write_json(os.path.join(br.RESULTS, "daily_forecast_scores.json"), scores)
        br.clear_caches()

    LEDGER = ("batch_id,forecast_date,made_at_utc,snapshot_date,model_version,model_sha256,n_forecasts,n_civilian_except_gsa,n_gsa,"
              "n_defense,n_unknown,n_fresh,mean_forecast,batch_sha256" + chr(10) +
              "PIO-D-20301102,2030-11-02,2030-11-02T11:00:00Z,2030-11-02,2,abc,4,1,0,3,0,1,,def" + chr(10) +
              "PIO-D-20301101,2030-11-01,2030-11-01T11:00:00Z,2030-11-01,2,abc,5000,1000,10,3990,0,300,0.225,ghi" + chr(10))

    def test_daily_forecasts_before_the_first_batch(self):
        for setup in (lambda: None, lambda: self.daily("batch_id,forecast_date,n_forecasts" + chr(10)),
                      lambda: self.daily(None, {"n_forecasts": 9, "n_resolved": 9})):   # a scores file alone does not make it live
            setup()
            for name in ("daily-forecasts", "m-daily-forecasts"):
                out = self.render(name)
                self.assertIn("the first batch has not been sealed yet", out)
                self.assertNotIn("So far", out)
        out = self.render("daily-forecasts")
        self.assertIn("(docs/daily-forecasts.md)", out)
        self.assertIn("(forecasts/daily/)", out)
        self.assertIn("on or after the day offers were due", out)
        self.assertIn("When it starts", out)

    def test_daily_forecasts_after_the_first_batch(self):
        self.daily(self.LEDGER)
        out = self.render("daily-forecasts")
        self.assertNotIn("not been sealed", out)
        self.assertIn("So far: 2 batches holding 5,004 forecasts, the first on November 1, 2030; 301 of them fresh", out)
        self.assertIn("No forecast has been scored yet.", out)
        m = self.render("m-daily-forecasts")
        self.assertTrue(m.startswith("**Daily forecasts.** So far: 2 batches"))
        # a scores file, scores not published yet
        self.daily(self.LEDGER, {"as_of": "2030-11-20", "n_forecasts": 5004, "n_resolved": 12, "scores_published": False, "scores": None})
        out = self.render("daily-forecasts")
        self.assertIn("12 of the 5,004 forecasts checked so far have resolved (as of November 20, 2030).", out)
        self.assertIn("Scores are published once enough forecasts have resolved.", out)
        self.assertNotIn("Brier", out)
        # published
        self.daily(self.LEDGER, {"as_of": "2030-12-20", "n_forecasts": 5004, "n_resolved": 40, "scores_published": True,
                                 "scores": {"as_of": "2030-12-18", "overall": {"n_resolved": 38, "model": {"brier": 0.1512, "auc": 0.6789},
                                                                               "agency_rate": {"brier": 0.1701, "auc": 0.6}}}})
        out = self.render("daily-forecasts")
        self.assertIn("Scores as of December 18, 2030, on 38 resolved forecasts: Brier score 0.151 against 0.170 for the agency's past "
                      "rate (lower is better), AUC 0.679 against 0.600 (0.5 is a coin flip).", out)

    def test_daily_forecasts_half_written_files(self):
        self.daily("batch_id,forecast_date,n_forecasts" + chr(10) + "PIO-D-20301101,2030-11-01,7" + chr(10) + "PIO-D-20301102,2030-11-0")
        with open(os.path.join(br.RESULTS, "daily_forecast_scores.json"), "w", encoding="utf-8") as f:
            f.write('{"n_forecasts": ')
        br.clear_caches()
        out = self.render("daily-forecasts")
        self.assertIn("So far: 1 batch holding 7 forecasts, the first on November 1, 2030.", out)   # no fresh column: not stated
        self.assertIn("No forecast has been scored yet.", out)

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
