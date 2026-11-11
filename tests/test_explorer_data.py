"""Offline tests for scripts/build_explorer.py with small synthetic result files in a temporary folder (nothing from data/ or results/)."""
import csv
import importlib.util
import json
import os
import shutil
import tempfile
import unittest
from datetime import date

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("build_explorer", os.path.join(REPO, "scripts", "build_explorer.py"))
bx = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bx)


def write_csv(folder, name, rows):
    with open(os.path.join(folder, name), "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def share_row(**kw):
    r = {"k_single": 30, "n_eval": 100, "share": 0.3, "ci_lo": 0.22, "ci_hi": 0.39}
    r.update(kw)
    return r


def load_js(path):
    """Parse the JSON inside 'window.FCC_DATA = {...};'."""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    marker = "window.FCC_DATA = "
    i = text.index(marker) + len(marker)
    body = text[i:].strip()
    assert body.endswith(";"), "the data script must end with a semicolon"
    return json.loads(body[:-1])


def base_files(d):
    """The required result files, a few rows each."""
    write_csv(d, "atlas_civilian_by_agency.csv", [
        dict(agency="General Services Administration", abbreviation="GSA", n_base=900, n_competed=700, in_original_five="False", **share_row(n_eval=600, k_single=240, share=0.4)),
        dict(agency="Department of Veterans Affairs", abbreviation="VA", n_base=300, n_competed=200, in_original_five="True", **share_row())])
    write_csv(d, "atlas_civilian_by_agency_fy.csv", [
        dict(agency="Department of Veterans Affairs", fy=2024, **share_row()),
        dict(agency="Department of Veterans Affairs", fy=2025, **share_row(share=0.25)),
        dict(agency="General Services Administration", fy=2024, **share_row(share=0.55)),
        dict(agency="General Services Administration", fy=2026, **share_row(share=0.17))])
    write_csv(d, "atlas_civilian_by_fy.csv", [
        dict(fy=2024, scope="all civilian agencies except GSA", **share_row()),
        dict(fy=2024, scope="GSA only", **share_row(share=0.55))])
    write_csv(d, "atlas_civilian_totals.csv", [
        dict(scope="all civilian agencies", n_agencies=2, median_award_value=500, dollar_weighted_share_po_def="", snapshot="2026-11-09", **share_row(n_eval=700)),
        dict(scope="GSA only", n_agencies=1, median_award_value=143, dollar_weighted_share_po_def="", snapshot="2026-11-09", **share_row(n_eval=600)),
        dict(scope="all civilian agencies except GSA", n_agencies=1, median_award_value=40000, dollar_weighted_share_po_def="", snapshot="2026-11-09", **share_row())])
    write_csv(d, "threshold_quarterly_bunching.csv", [
        dict(fyq=q, threshold=t, n_base=1000, n_window=100, obs_below=30, cf_below=20.5, ratio=1.46, se_boot=0.2, lo95=1.1, hi95=1.9)
        for q in ("FY2025Q4", "FY2026Q1") for t in (250000, 350000)])
    write_csv(d, "threshold_sat_moved.csv", [dict(threshold=350000, ratio_pre=0.97, ratio_post=1.96, diff=0.99, diff_lo=0.65, diff_hi=1.36)])
    write_csv(d, "quality_flags.csv", [
        dict(agency="VA", award_kind="PO", quarter="FY2025Q1", metric="offers_1", before=40.0, after=20.0, change_points=-20.0, z=-9.1, n_before=900, n_after=300)])
    with open(os.path.join(d, "quality_summary.json"), "w") as f:
        json.dump({"n_flags": 1, "thresholds": {"min_n": 200, "min_points": 10.0, "volume_pct": 40.0}}, f)
    write_csv(d, "sam_daily_counts.csv", [
        dict(snapshot_date="2026-11-09", department="VETERANS AFFAIRS, DEPARTMENT OF", type="Solicitation", n_active=9, n_sol_numbers=5, n_posted_last_7d=1, n_open=4, n_deadline_next_7d=1, n_deadline_next_30d=2),
        dict(snapshot_date="2026-11-09", department="VETERANS AFFAIRS, DEPARTMENT OF", type="Award Notice", n_active=9, n_sol_numbers=5, n_posted_last_7d=1, n_open="", n_deadline_next_7d="", n_deadline_next_30d=""),
        dict(snapshot_date="2026-11-09", department="DEPT OF DEFENSE", type="Solicitation", n_active=90, n_sol_numbers=50, n_posted_last_7d=1, n_open=40, n_deadline_next_7d=1, n_deadline_next_30d=2),
        dict(snapshot_date="2026-11-10", department="VETERANS AFFAIRS, DEPARTMENT OF", type="Solicitation", n_active=9, n_sol_numbers=5, n_posted_last_7d=1, n_open=6, n_deadline_next_7d=1, n_deadline_next_30d=2)])


class ExplorerDataTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.res = os.path.join(self.tmp, "results")
        os.makedirs(self.res)
        base_files(self.res)
        self.out = os.path.join(self.tmp, "docs", "data", "explorer-data.js")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def build(self):
        bx.build(self.res, self.out, repo="someone/project", today=date(2026, 11, 10))
        return load_js(self.out)

    def test_valid_js_wrapper_and_metadata(self):
        d = self.build()
        with open(self.out, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("window.FCC_DATA = {", text)
        self.assertNotIn("NaN", text)
        self.assertEqual(d["built"], "2026-11-10")
        self.assertEqual(d["repo"], "https://github.com/someone/project")
        self.assertIn("atlas_civilian_by_agency.csv", d["sources"])

    def test_missing_optional_files_are_skipped(self):
        d = self.build()
        self.assertNotIn("history_by_agency_fy.csv", d["sources"])
        self.assertNotIn("history", d["thresholds"])
        self.assertNotIn("staggered", d["thresholds"])
        self.assertFalse(d["sectors"]["has_history"])
        self.assertEqual(d["agency_meta"]["history_years"], [])
        va = next(a for a in d["agencies"] if a["abbr"] == "VA")
        self.assertEqual([p[0] for p in va["years"]], [2024, 2025])
        self.assertEqual(d["thresholds"]["quarterly"]["350000"][0][0], "FY2025Q4")

    def test_history_merges_and_wins_on_overlap(self):
        write_csv(self.res, "history_by_agency_fy.csv", [
            dict(agency="Department of Veterans Affairs", fy=fy, **share_row(share=s)) for fy, s in ((2008, 0.5), (2009, 0.45), (2024, 0.31))]
            + [dict(agency="Department of Defense", fy=2008, **share_row())])
        write_csv(self.res, "history_by_fy.csv", [
            dict(sector=s, fy=fy, award_kind=k, **share_row()) for s in ("GSA", "Department of Defense") for fy in (2008, 2009) for k in ("all", "PO")])
        write_csv(self.res, "threshold_history.csv", [
            dict(fy=fy, sector=sec, line=line, ratio=1.2, ci_lo="", ci_hi="", obs_below=10, cf_below=8.3, n_window=50, n_base=500, active=str(fy > 2010))
            for fy in (2010, 2011) for sec in ("civilian", "defense") for line in (100000, 150000)])
        write_csv(self.res, "threshold_staggered.csv", [
            dict(sector="civilian", event_quarter=q, line=150000, ratio=1.1, ci_lo=0.9, ci_hi=1.3, obs_below=5, cf_below=4.5, n_awards=400, dates=dt)
            for q in (-1, 0) for dt in ("actual", "placebo, one year earlier")])
        d = self.build()
        va = next(a for a in d["agencies"] if a["abbr"] == "VA")
        self.assertEqual([p[0] for p in va["years"]], [2008, 2009, 2024, 2025])
        self.assertEqual(va["years"][2][3], 0.31)      # the long history wins for 2024
        self.assertEqual(va["years"][2][6], "h")
        self.assertEqual(va["years"][3][6], "r")
        self.assertTrue(any(a["full"] == "Department of Defense" for a in d["agencies"]))
        self.assertEqual(d["agency_meta"]["history_years"], [2008, 2009, 2024])
        self.assertTrue(d["sectors"]["has_history"])
        self.assertEqual([p[0] for p in d["sectors"]["series"]["GSA"]["all"]], [2008, 2009, 2024])
        th = d["thresholds"]["history"]["defense"]["150000"]
        self.assertEqual([p[0] for p in th], [2010, 2011])
        self.assertIsNone(th[0][2])                     # blank interval -> null
        self.assertEqual([p[4] for p in th], [False, True])
        self.assertEqual(len(d["thresholds"]["staggered"]["placebo"]["civilian"]["150000"]), 2)

    def test_quality_and_sam(self):
        d = self.build()
        flag = d["quality"]["flags"][0]
        self.assertEqual(flag[0], "Veterans Affairs")   # abbreviation mapped to the agency name
        self.assertEqual(flag[3], "offers_1")
        sam = d["sam"]
        self.assertEqual(sam["date"], "2026-11-10")     # no latest.json: newest day in the counts file
        self.assertEqual(sam["departments"], [["Veterans Affairs", 6, None]])
        self.assertEqual(sam["daily"], [["2026-11-09", 4, 40], ["2026-11-10", 6, 0]])

    def test_sam_from_the_api(self):
        latest = {"schema": 2, "through": "2026-11-11", "window": {"from": "2026-11-05", "to": "2026-11-11", "days": 7, "days_pulled": 2},
                  "solicitation_notices_posted": {"civilian": {"notices": 9, "distinct_sol_numbers": 8},
                                                  "dod": {"notices": 5, "distinct_sol_numbers": 4},
                                                  "dod_department_label": "DEPT OF DEFENSE"},
                  "top10_civilian_departments": [{"department": "VETERANS AFFAIRS, DEPARTMENT OF", "distinct_sol_numbers": 8,
                                                  "notices": 9}]}
        with open(os.path.join(self.res, "sam_daily_latest.json"), "w", encoding="utf-8") as fh:
            json.dump(latest, fh)
        va = "VETERANS AFFAIRS, DEPARTMENT OF"
        write_csv(self.res, "sam_new_notices.csv", [
            {"posted_day": "2026-11-10", "department": "DEPT OF DEFENSE", "type": "Solicitation", "n_notices": 5, "n_sol_numbers": 4},
            {"posted_day": "2026-11-10", "department": va, "type": "Solicitation", "n_notices": 4, "n_sol_numbers": 4},
            {"posted_day": "2026-11-11", "department": va, "type": "Combined Synopsis/Solicitation", "n_notices": 5, "n_sol_numbers": 4},
            {"posted_day": "2026-11-11", "department": va, "type": "Award Notice", "n_notices": 3, "n_sol_numbers": 3}])
        sam = self.build()["sam"]
        self.assertEqual((sam["schema"], sam["date"], sam["from"], sam["days_pulled"]), (2, "2026-11-11", "2026-11-05", 2))
        self.assertEqual((sam["civilian"], sam["dod"]), (8, 4))
        self.assertEqual(sam["departments"], [["Veterans Affairs", 8, 9]])
        self.assertEqual(sam["daily"], [["2026-11-10", 4, 5], ["2026-11-11", 5, 0]])    # award notices left out

    def test_gsa_note_numbers(self):
        g = self.build()["gsa"]
        self.assertAlmostEqual(g["award_share"], 600 / 700, places=3)
        self.assertEqual(g["first"], [2024, 0.55])
        self.assertEqual(g["last"], [2026, 0.17])

    def test_only_results_folder_is_read(self):
        os.remove(os.path.join(self.res, "sam_daily_counts.csv"))
        os.remove(os.path.join(self.res, "quality_flags.csv"))
        d = self.build()
        self.assertIsNone(d["sam"])
        self.assertIsNone(d["quality"])

    def test_names(self):
        self.assertEqual(bx.sam_name("EXPORT-IMPORT BANK OF THE US"), "Export-Import Bank of the US")
        self.assertEqual(bx.sam_name("INTERIOR, DEPARTMENT OF THE"), "Interior")
        self.assertEqual(bx.short_name("Department of the Treasury"), "Treasury")


# ---- version 2 sections ----------------------------------------------------------------------------------------------
YEARS = list(range(2008, 2027))
FAMS = ["micro_purchase", "simplified_acquisition", "synopsis", "cost_or_pricing_data", "commercial_simplified", "subcontracting_plan",
        "subcontracting_plan_construction", "justification_level1", "justification_level2", "justification_level3_civilian",
        "justification_level3_dod", "8a_competitive_threshold_nonmfg", "8a_competitive_threshold_mfg", "8a_sole_source_ja"]


def scan_rows(years=(2009, 2010), fams=("simplified_acquisition",), sectors=("civilian", "defense"), lines=(100000, 150000), placebos=(125000,)):
    rows = []
    for f in fams:
        for s in sectors:
            for fy in years:
                for v in lines:
                    rows.append(dict(family=f, sector=s, kind="line", value=float(v), placebo_for="", fy=fy,
                                     status="active" if (v == 100000) == (fy < 2011) else ("partial" if fy == 2018 else "inactive"),
                                     active_share=1.0 if v == 100000 else 0.0, other_family_active="", ratio=1.234567, ci_lo=1.1, ci_hi=1.4,
                                     obs_below=500, exp_below=405.1, n_window=5000, n_fit=30, n_universe=10 ** 6, small="False", note=""))
                for v in placebos:
                    rows.append(dict(family=f, sector=s, kind="placebo", value=float(v), placebo_for="150000", fy=fy, status="placebo",
                                     active_share="", other_family_active="", ratio="" if fy == years[0] else 1.05, ci_lo="", ci_hi="",
                                     obs_below=3, exp_below="", n_window=20, n_fit=5, n_universe=10 ** 6, small="False",
                                     note="too few awards in the window" if fy == years[0] else ""))
    return rows


def change_row(**kw):
    r = dict(family="simplified_acquisition", sector="civilian", old_line=100000.0, new_line=150000.0, change_date="2010-10-01",
             verdict="moved", did=1.04, did_lo=0.9, did_hi=1.2, d_old="", d_old_lo="", d_old_hi="", d_new="", d_new_lo="", d_new_hi="",
             d_placebo=-0.02, d_placebo_lo=-0.06, d_placebo_hi=0.01, old_before=2.24, old_after=1.58, new_before=1.16, new_after=1.53,
             reason="the new line's pile-up grew", before_window="2008-10-01 to 2010-09-30", after_window="2010-10-01 to 2012-09-30",
             years_missing="", data_complete="True", years_outside_data="", n_before=1000, n_after=1100, placebos="125000",
             same_day_changes="micro_purchase;synopsis")
    r.update(kw)
    return r


def v2_files(d, ref):
    """Small versions of every new input, with the real column names."""
    write_csv(d, "threshold_scan.csv", scan_rows())
    write_csv(d, "threshold_scan_changes.csv", [change_row(), change_row(sector="defense", verdict="not testable", did="", did_lo="", did_hi="",
                                                                         reason="no awards in the after window in the years present (missing FY2011;FY2012)")])
    with open(os.path.join(d, "threshold_scan_summary.json"), "w") as f:
        json.dump({"fiscal_years_present": [2009, 2010], "fiscal_years_missing": [2011], "not_testable_counts": {"fiscal years not present yet": 1},
                   "levels": [{"family": "simplified_acquisition", "sector": "civilian", "active_cells": 2, "inactive_cells": 2,
                               "active_minus_inactive": 0.9, "active_minus_inactive_ci": [0.5, 1.2]}],
                   "universes": {"simplified_acquisition": "all purchase orders and definitive contracts"}, "caveats": ["a", "b"]}, f)
    write_csv(d, "drivers_shares.csv", [
        dict(factor="size_band", factor_name="initial value", scope=sc, level=lv, label=lv, k_single=10, n_eval=n, share=0.3, ci_lo=0.2, ci_hi=0.4)
        for sc in ("all sectors", "GSA") for lv, n in ((">10M", 50), ("<25K", 500), ("25-100K", 100))])
    write_csv(d, "drivers_model.csv", [
        dict(scope="all sectors", block="award type and size", factor="size_band", factor_name="initial value", level="25-100K", label="25-100K",
             reference="<25K", reference_label="<25K", n_level=1000, single_share_level=0.31, ame_points=1.96, ci_lo_points=0.8, ci_hi_points=3.1, se_points=0.6)])
    write_csv(d, "drivers_blocks.csv", [
        dict(block="set-aside", split="train FY2009-FY2010, test FY2018", n_train=100, n_test=50, factors="setaside_grp", log_loss_full=0.56,
             log_loss_without=0.58, log_loss_increase_without=0.0144, log_loss_increase_ci_lo=0.013, log_loss_increase_ci_hi=0.0156,
             share_of_gain_lost_without=0.668, auc_full=0.63, auc_without=0.62, auc_drop_without=0.005, auc_drop_ci_lo=0.003, auc_drop_ci_hi=0.007,
             log_loss_alone=0.586, share_of_gain_alone=-0.004, auc_alone=0.53, log_loss_base_rate=0.586)])
    with open(os.path.join(d, "drivers_summary.json"), "w") as f:
        json.dump({"years_in_shares": [2009, 2010], "single_offer_share": {"all sectors": {"share": 0.29, "n_eval": 650}}, "caveats": ["x"]}, f)
    split = []
    for sec in ("civilian_ex_gsa", "defense"):
        for fy in (2009, 2010):
            for basis in ("in_force", "band"):
                for m in (0.5, 0.8, 1.0, 1.25, 2.0):
                    split.append(dict(sector=sec, fy=fy, threshold_type="SAT", basis=basis, line_low=100000.0, line_high="" if basis == "in_force" else 150000.0,
                                      variant="all", multiple=m, line_dollars=m * 100000, placebo_line=str(m != 1.0 and m != 0.8), awards=1000, clusters=900,
                                      multi_award_clusters=80, share_awards_at_line_low=1.0, awards_under=700, awards_split_like=50, share_awards=0.071234,
                                      dollars_under=1e6, dollars_split_like=1e5, share_dollars=0.1, clusters_split_like=20,
                                      n_placebo_lines=3 if m == 1.0 else "", baseline_share=0.065 if m == 1.0 else "",
                                      excess_ratio=1.0959 if m == 1.0 else "", ci_lo=1.02 if m == 1.0 else "", ci_hi=1.17 if m == 1.0 else ""))
    write_csv(d, "split_screen.csv", split)
    with open(os.path.join(d, "split_screen_summary.json"), "w") as f:
        json.dump({"years_used": [2009, 2010], "years_not_present": [2008], "design": {"gap_days": 30, "donut_cap": 0.9},
                   "excess_ratio_pooled": {"defense|SAT|all": {"ratio": 1.15, "ci_lo": 1.12, "ci_hi": 1.17, "years": 2, "actual_awards": 10, "expected_awards": 8.7}},
                   "natural_experiment": [{"threshold_type": "SAT", "old_line": 100000, "new_line": 150000, "change_label": "FY2011", "sector": "defense",
                                           "variant": "all", "before_years": [2009, 2010], "after_years": [2011, 2012],
                                           "before": {"ratio": 1.03, "ci_lo": 0.95, "ci_hi": 1.1, "years": 2}, "after": {"ratio": None, "years": 0},
                                           "after_minus_before": {"change": None}}]}, f)
    est = []
    for o, role, unit in (("any_scope", "outcome", "share"), ("placebo_coin", "placebo", "share"), ("bal_month", "balance", "months")):
        for fu in ("t to t+3", "t to t+1"):
            for st, sc in (("pooled", "all sectors"), ("sector", "GSA"), ("check", "size floor $10,000")):
                est.append(dict(outcome=o, role=role, unit=unit, label=o.replace("_", " "), follow_up=fu, scope_type=st, scope=sc, cohorts="FY2008-FY2010",
                                n_cohorts=3, coef=-0.0026, se=0.001, lo=-0.0043, hi=-0.0009, p=0.01, mean_multi_offer=0.046, n_awards=88335, n_single=30000,
                                n_cells=12833, n_offices=1041, n_all=300000, n_all_single=100000, raw_mean_single=0.07, raw_mean_multi=0.12))
    write_csv(d, "outcomes_estimates.csv", est)
    write_csv(d, "outcomes_by_cohort.csv", [
        dict(cohort_fy=fy, follow_up="t to t+1", scope=sc, outcome=o, role=role, coef=0.001, se=0.002, lo=-0.003, hi=0.005, p=0.6, mean_multi_offer=0.04,
             n_awards=20000, n_single=8000, n_cells=3000, n_offices=300, n_all=70000, n_all_single=25000, raw_mean_single=0.07, raw_mean_multi=0.12,
             window_ends_in_latest_year=str(fy == 2010))
        for fy in (2008, 2009, 2010) for sc in ("all sectors", "GSA") for o, role in (("any_scope", "outcome"), ("bal_month", "balance"))])
    with open(os.path.join(d, "outcomes_summary.json"), "w") as f:
        json.dump({"headline_follow_up": "t to t+3", "years_present": {"base_actions": [2008, 2009, 2010]}, "design": {"size_floor": 25000},
                   "follow_ups": {"t to t+3": {"cohorts_text": "FY2008-FY2010", "share_of_awards_carrying_the_estimate": 0.27}}, "caveats": ["c"]}, f)
    write_csv(d, "model_v2_evaluation.csv", [
        dict(test_fy=t, sector=s, model=m, n=1000, single_share=0.21, mean_forecast=0.2, auc=0.65, brier=0.16, logloss=0.5, cal_intercept=0.1, cal_slope=0.9,
             n_train=5000 if t != "pooled" else "", train_years="FY2008-FY2008" if t != "pooled" else "",
             outcome_window_complete="True" if t == 2009 else ("False" if t == 2010 else ""), delta_logloss_when_dropped="", delta_auc_when_dropped="")
        for t in (2009, 2010, "pooled") for s in ("all", "GSA") for m in ("sector rate (baseline)", "logistic v2", "logistic v2 (raw)")]
        + [dict(test_fy=2010, sector="all", model="logistic v2 (raw) without: agency", n=1000, single_share="", mean_forecast="", auc=0.66, brier="",
                logloss=0.49, cal_intercept="", cal_slope="", n_train="", train_years="", outcome_window_complete="", delta_logloss_when_dropped=-0.01,
                delta_auc_when_dropped=0.02)])
    write_csv(d, "model_v2_calibration.csv", [dict(model="logistic v2", sector=s, band=b, n=100, mean_forecast=0.03, observed_share=0.05, test_years="FY2009-FY2010")
                                              for s in ("all", "GSA") for b in ("0.00-0.05", "0.05-0.10")])
    os.makedirs(ref, exist_ok=True)
    write_csv(ref, "market_depth_naics.csv", [dict(naics=c, n_primary=p, n_any=a, n_small_any=s, n_size_unknown=1, extract_date="2026-11-01")
                                              for c, p, a, s in (("541330", 900, 20000, 15000), ("111110", 77, 655, 412), ("1234", 1, 1, 1))])
    write_csv(ref, "thresholds.csv", [
        dict(threshold="micro_purchase", description="Micro-purchase threshold (MPT): purchases up to this value need no quotes", far_citation="FAR 2.101",
             value_usd=v, effective_date=a, end_date=b, applies_to="all", measure="value of the purchase", document="FAC", source_url="https://example.org/x",
             evidence="text", confidence="high", notes="n" * 400)
        for v, a, b in ((2500, "", "2006-09-27"), (3000, "2006-09-28", "2015-09-30"))]
        + [dict(threshold="small_business_reservation", description="Band reserved for small business", far_citation="FAR 19.502-2", value_usd="",
                effective_date="", end_date="", applies_to="all", measure="", document="", source_url="", evidence="", confidence="high", notes="")])


class ExplorerV2Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.res = os.path.join(self.tmp, "results")
        self.ref = os.path.join(self.tmp, "reference")
        os.makedirs(self.res)
        base_files(self.res)
        self.out = os.path.join(self.tmp, "docs", "data", "explorer-data.js")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def build(self, **kw):
        bx.build(self.res, self.out, repo="someone/project", today=date(2026, 11, 10), **kw)
        return load_js(self.out)

    def test_new_inputs_absent(self):
        d = self.build()
        for k in ("scan", "drivers", "split", "outcomes", "model2", "scoreboard", "market_depth", "threshold_table"):
            self.assertIsNone(d[k], k)
        self.assertEqual(d["trimmed"], [])
        self.assertFalse(any(s.startswith("reference/") for s in d["sources"]))

    def test_new_inputs_present(self):
        v2_files(self.res, self.ref)
        d = self.build()
        sc = d["scan"]
        self.assertEqual(sc["years"], [2009, 2010])
        civ = sc["families"]["simplified_acquisition"]["sectors"]["civilian"]
        line, pl = civ[0], civ[-1]
        self.assertEqual((line["v"], line["r"][0], line["st"]), (100000, 123, [1, 1]))   # ratios in hundredths
        self.assertEqual(sc["scale"], 100)
        self.assertTrue(pl["p"] and "st" not in pl)
        self.assertIsNone(pl["r"][0])                                  # thin row kept, with its reason
        self.assertEqual(sc["notes"][pl["nt"][0]], "too few awards in the window")
        self.assertEqual([c[5] for c in sc["changes"]], ["moved", "not testable"])
        self.assertEqual(sc["changes"][0][23], 2)                       # other limits changed the same day
        dr = d["drivers"]
        f = dr["factors"][0]
        self.assertEqual([dr["labels"][i] for i in f["levels"]], ["<25K", "25-100K", ">10M"])   # natural order, not by size
        self.assertEqual(dr["model"]["all sectors"][0][6], 1.96)
        self.assertEqual(dr["blocks"][0]["lost"], 0.668)
        sp = d["split"]
        self.assertEqual(sp["multiples"], [0.5, 0.8, 1.0, 1.25, 2.0])
        p = sp["series"]["defense|SAT|all"][0]
        self.assertEqual((p[0], p[1], p[10][2], p[11]), (2009, 1.096, 712, "10011"))
        self.assertNotIn("defense|SAT|all|band", sp["series"])
        self.assertEqual(sp["natural"][0][10], None)                    # after the change: not testable yet, kept as null
        o = d["outcomes"]
        roles = {x["key"]: x["role"] for x in o["outcomes"]}
        self.assertEqual(roles["placebo_coin"], "placebo")
        self.assertEqual(len(o["rows"]), 18)
        self.assertTrue(all(o["outcomes"][r[0]]["role"] != "balance" for r in o["cohort"]))   # balance rows only in the estimates
        m = d["model2"]
        self.assertIsNone(m["card"])
        self.assertNotIn("logistic v2 (raw)", [m["models"][r[2]] for r in m["eval"] if r[0] != "pooled"])
        self.assertIn("logistic v2 (raw)", [m["models"][r[2]] for r in m["eval"] if r[0] == "pooled"])
        self.assertEqual(m["drops"][0][2], "agency")
        self.assertEqual(len(m["cal"]), 4)
        self.assertEqual([c[0] for c in d["market_depth"]["codes"]], [111110, 541330])   # malformed code dropped
        tt = d["threshold_table"]
        self.assertEqual(len(tt["rows"]), 3)
        self.assertLessEqual(len(tt["rows"][0][8]), bx.NOTE_CAP)
        self.assertIsNone(tt["rows"][-1][1])                            # no dollar value
        self.assertIn("reference/thresholds.csv", d["sources"])
        self.assertIsNone(d["scoreboard"])

    def test_planned_files(self):
        v2_files(self.res, self.ref)
        with open(os.path.join(self.res, "model_v2_card.json"), "w") as f:
            json.dump({"model": "single-offer forecast, version 2", "what_it_predicts": "P(one offer)", "data": {"resolved_solicitations": 5, "x": [1]},
                       "shipped": {"kind": "L2 logistic regression", "why": "simpler", "calibration": {"a": 1}}, "baselines": {"agency": {"A": 0.2}},
                       "metrics": {"auc": 0.6912345, "nested": {"no": 1}}, "caveats": ["one", 2]}, f)
        with open(os.path.join(self.res, "daily_forecast_scores.json"), "w") as f:
            json.dump({"as_of": "2026-11-10", "overall": {"n_batches": 3, "n_forecasts": 90, "resolved": 10, "brier_score": 0.15,
                                                          "baseline": {"name": "sector rate", "brier": 0.17}},
                       "months": {"2026-11": {"batches": 3, "forecasts": 90, "n_resolved": 10, "brier": 0.15, "brier_baseline": 0.17}}}, f)
        d = self.build()
        c = d["model2"]["card"]
        self.assertEqual(c["data"], {"resolved_solicitations": 5})
        self.assertEqual(c["shipped"]["why"], ["simpler"])
        self.assertEqual(c["metrics"], {"auc": 0.6912})
        self.assertEqual(c["caveats"], ["one"])
        self.assertNotIn("baselines", c)
        sb = d["scoreboard"]
        self.assertEqual(sb["updated"], "2026-11-10")
        self.assertEqual(sb["totals"], {"batches": 3, "forecasts": 90, "resolved": 10, "brier": 0.15, "baseline": 0.17})
        self.assertEqual(sb["months"], [["2026-11", 3, 90, 10, 0.15, 0.17]])
        self.assertIn("daily_forecast_scores.json", d["sources"])

    def test_shipped_model_comes_from_the_card(self):
        v2_files(self.res, self.ref)
        ship = "logistic v2, half-life 3 years, correction by sector"
        write_csv(self.res, "model_v2_evaluation.csv", [
            dict(test_fy=t, sector="all", model=m, n=1000, single_share=0.21, mean_forecast=0.2, auc=0.65, brier=0.16, logloss=0.5,
                 cal_intercept=0.1, cal_slope=0.9, n_train="", train_years="", outcome_window_complete="", delta_logloss_when_dropped="",
                 delta_auc_when_dropped="")
            for t in (2009, 2010, "pooled") for m in ("logistic v2", ship, "logistic v2, half-life 5 years")])
        with open(os.path.join(self.res, "model_v2_card.json"), "w") as f:
            json.dump({"shipped": {"kind": "L2 logistic regression", "name_in_evaluation": ship}}, f)
        m = self.build()["model2"]
        self.assertEqual(m["shipped"], ship)
        by_year = {m["models"][r[2]] for r in m["eval"] if r[0] != "pooled"}
        self.assertIn(ship, by_year)                                        # the shipped model is shown year by year
        self.assertIn("logistic v2", by_year)
        self.assertNotIn("logistic v2, half-life 5 years", by_year)         # other candidates keep only their pooled rows
        self.assertIn("logistic v2, half-life 5 years", [m["models"][r[2]] for r in m["eval"] if r[0] == "pooled"])
        with open(os.path.join(self.res, "model_v2_card.json"), "w") as f:  # a card without the name: the fallback
            json.dump({"shipped": {"kind": "L2 logistic regression"}}, f)
        m = self.build()["model2"]
        self.assertEqual(m["shipped"], "logistic v2")
        self.assertNotIn(ship, {m["models"][r[2]] for r in m["eval"] if r[0] != "pooled"})
        self.assertEqual(bx.shipped_model_name(None), "logistic v2")

    def test_trims_when_over_cap(self):
        v2_files(self.res, self.ref)
        d = self.build(cap=1000)                     # far too small: every trim that can change something runs, and the file is still valid
        labels = [t[0] for t in bx.TRIMS]
        self.assertEqual(d["trimmed"], [l for l in labels if l in d["trimmed"]])   # in the planned order
        self.assertIn("Every dollar limit: the count of awards just under each line is left out", d["trimmed"])
        civ = d["scan"]["families"]["simplified_acquisition"]["sectors"]["civilian"]
        self.assertTrue(all("lo" not in s for s in civ if s.get("p")))
        self.assertTrue(all("o" not in s for s in civ))
        self.assertEqual(load_js(self.out)["trimmed"], d["trimmed"])

    def test_size_under_cap_at_full_scale(self):
        """Synthetic inputs as large as the full FY2008-FY2026 run: the data file stays under the cap and is valid JSON."""
        v2_files(self.res, self.ref)
        write_csv(self.res, "threshold_scan.csv", scan_rows(years=YEARS, fams=FAMS, sectors=("civilian", "defense", "all"),
                                                            lines=(100000, 150000, 250000, 350000, 500000, 750000), placebos=(125000, 200000, 300000, 400000, 600000, 900000)))
        write_csv(self.res, "threshold_scan_changes.csv", [change_row(family=f, sector=s, old_line=float(i), new_line=float(i + 1))
                                                            for f in FAMS for s in ("civilian", "defense", "all") for i in range(4)])
        write_csv(self.res, "drivers_shares.csv", [
            dict(factor=f, factor_name=f, scope=sc, level=f"level {i}", label=f"product group {i}: a fairly long product description", k_single=100,
                 n_eval=1000 + i, share=0.3123, ci_lo=0.2987, ci_hi=0.3256)
            for f, n in (("psc_cat", 110), ("naics2", 25), ("route", 30), ("fy", 19), ("setaside_grp", 9), ("size_band", 6), ("timing", 6))
            for sc in ("all sectors", "civilian except GSA", "GSA", "Department of Defense") for i in range(n)])
        write_csv(self.res, "drivers_model.csv", [
            dict(scope=sc, block="what is bought", factor="psc_cat", factor_name="product/service category", level=f"l{i}", label=f"product group {i}: a description",
                 reference="r", reference_label="product group 53: hardware", n_level=1000, single_share_level=0.31, ame_points=1.96, ci_lo_points=0.8,
                 ci_hi_points=3.1, se_points=0.6) for sc in ("all sectors", "civilian except GSA", "GSA", "Department of Defense") for i in range(120)])
        split = []
        for sec in ("civilian_ex_gsa", "gsa", "defense"):
            for fy in YEARS:
                for typ in ("MPT", "SAT"):
                    for basis, lo in (("in_force", 3000.0), ("band", 3000.0), ("band", 3500.0), ("band", 10000.0)):
                        for var in ("all", "donut"):
                            for m in (0.5, 0.6, 0.7, 0.8, 1.0, 1.25, 1.4, 1.6, 2.0):
                                split.append(dict(sector=sec, fy=fy, threshold_type=typ, basis=basis, line_low=lo, line_high="", variant=var, multiple=m,
                                                  line_dollars=m * lo, placebo_line="True", awards=1, clusters=1, multi_award_clusters=1,
                                                  share_awards_at_line_low=0.713, awards_under=284147, awards_split_like=466, share_awards=0.019724,
                                                  dollars_under=1.4e7, dollars_split_like=2.5e5, share_dollars=0.017053, clusters_split_like=153,
                                                  n_placebo_lines=6, baseline_share=0.019378, excess_ratio=1.0995, ci_lo=1.0734, ci_hi=1.1326))
        write_csv(self.res, "split_screen.csv", split)
        outcomes = ["oblig_ratio_w", "overrun25", "value_jump_w", "value_jump25", "any_scope", "n_scope_w", "any_termination", "option_exercised",
                    "placebo_even_day", "placebo_coin", "bal_log_value", "bal_planned_years", "bal_has_options", "bal_month"]
        scopes = [("pooled", "all sectors")] + [("sector", s) for s in ("civilian except GSA", "GSA", "Department of Defense")] + \
                 [("size band", f"band {i}") for i in range(5)] + [("check", f"cells: a check of the method number {i}") for i in range(10)]
        write_csv(self.res, "outcomes_estimates.csv", [
            dict(outcome=o, role="placebo" if o.startswith("placebo") else "balance" if o.startswith("bal") else "outcome", unit="share",
                 label="A fairly long outcome label for " + o, follow_up=fu, scope_type=st, scope=sc, cohorts="FY2008-FY2023", n_cohorts=16,
                 coef=-0.00131459, se=0.00526391, lo=-0.0116437, hi=0.0090145, p=0.80284, mean_multi_offer=1.02227, n_awards=88335, n_single=32523,
                 n_cells=12833, n_offices=1041, n_all=322630, n_all_single=111366, raw_mean_single=1.00706, raw_mean_multi=1.00745)
            for o in outcomes for fu in ("t to t+3", "t to t+1") for st, sc in scopes])
        write_csv(self.res, "outcomes_by_cohort.csv", [
            dict(cohort_fy=fy, follow_up=fu, scope=sc, outcome=o, role="placebo" if o.startswith("placebo") else "balance" if o.startswith("bal") else "outcome",
                 coef=0.000700137, se=0.00289064, lo=-0.00498334, hi=0.00638361, p=0.808748, mean_multi_offer=0.0384739, n_awards=24238, n_single=8669,
                 n_cells=3366, n_offices=385, n_all=74736, n_all_single=30056, raw_mean_single=0.074827, raw_mean_multi=0.122404, window_ends_in_latest_year="False")
            for fy in YEARS for fu in ("t to t+3", "t to t+1") for sc in ("all sectors", "civilian except GSA", "GSA", "Department of Defense") for o in outcomes])
        models = ["sector rate (baseline)", "agency rate (baseline)", "office history only (baseline)", "version 1 feature set (refit)", "logistic v2 (raw)",
                  "logistic v2", "logistic v2, last 6 years (raw)", "logistic v2, last 6 years", "gradient boosting (raw)", "gradient boosting"]
        write_csv(self.res, "model_v2_evaluation.csv", [
            dict(test_fy=t, sector=s, model=m, n=63984, single_share=0.21998, mean_forecast=0.21065, auc=0.50133, brier=0.17165, logloss=0.52706,
                 cal_intercept=-0.1297, cal_slope=0.86, n_train=184508, train_years="FY2008-FY2013", outcome_window_complete="False",
                 delta_logloss_when_dropped="", delta_auc_when_dropped="")
            for t in YEARS[1:] + ["pooled"] for s in ("all", "civilian except GSA", "GSA", "Department of Defense") for m in models])
        write_csv(self.ref, "market_depth_naics.csv", [dict(naics=111110 + i, n_primary=77, n_any=655, n_small_any=412, n_size_unknown=70,
                                                            extract_date="2026-11-01") for i in range(1100)])
        write_csv(self.ref, "thresholds.csv", [
            dict(threshold=FAMS[i % len(FAMS)], description="d" * 250, far_citation="FAR 2.101 (definition); 13.201; 13.003", value_usd=1000 * i,
                 effective_date="2006-09-28", end_date="2015-09-30", applies_to="civilian", measure="m" * 120, document="doc",
                 source_url="https://www.federalregister.gov/d/2015-16206", evidence="e" * 150, confidence="high", notes="n" * 600) for i in range(110)])
        d = self.build()
        size = os.path.getsize(self.out)
        self.assertLessEqual(size, bx.SIZE_CAP, f"{size} bytes, trims: {d['trimmed']}")
        if os.environ.get("FCC_SIZE_REPORT"):
            print(f"\nfull-scale synthetic data: {size / 1024:.1f} KB; trims: {d['trimmed']}")
        self.assertEqual(len(load_js(self.out)["scan"]["years"]), len(YEARS))



class DailyScoreboard(unittest.TestCase):
    """The scoreboard section: the public ledger of the daily sealed forecasts (forecasts/daily/ next to the results folder) and the
    public scores of 32_score_daily.py.  Left out (null) before the first batch."""
    LEDGER = ("batch_id,forecast_date,made_at_utc,snapshot_date,model_version,model_sha256,n_forecasts,n_civilian_except_gsa,n_gsa,"
              "n_defense,n_unknown,n_fresh,mean_forecast,batch_sha256\n"
              "PIO-D-20301102,2030-11-02,x,2030-11-02,2,abc,4,1,0,3,0,1,,def\n"
              "PIO-D-20301101,2030-11-01,x,2030-11-01,2,abc,5000,1000,10,3990,0,300,0.2254,ghi\n"
              "PIO-D-20301103,2030-11-0\n")

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.res = os.path.join(self.tmp, "results")
        self.daily = os.path.join(self.tmp, "forecasts", "daily")
        os.makedirs(self.res)
        base_files(self.res)
        self.out = os.path.join(self.tmp, "docs", "data", "explorer-data.js")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def build(self):
        bx.build(self.res, self.out, repo="someone/project", today=date(2030, 11, 10))
        return load_js(self.out)

    def ledger(self):
        os.makedirs(self.daily, exist_ok=True)
        with open(os.path.join(self.daily, "ledger.csv"), "w", encoding="utf-8", newline="") as f:
            f.write(self.LEDGER)

    def scores(self, obj):
        with open(os.path.join(self.res, "daily_forecast_scores.json"), "w", encoding="utf-8") as f:
            json.dump(obj, f)

    def test_before_the_first_batch(self):
        os.makedirs(self.daily)
        with open(os.path.join(self.daily, "README.md"), "w") as f:
            f.write("# Daily sealed forecasts\n")
        d = self.build()
        self.assertIsNone(d["scoreboard"])
        self.assertNotIn("forecasts/daily/ledger.csv", d["sources"])

    def test_ledger_only(self):
        self.ledger()
        sb = self.build()["scoreboard"]
        self.assertEqual(sb["days"], [["2030-11-01", 5000, 300, 0.225], ["2030-11-02", 4, 1, None]])   # half-written row dropped
        self.assertEqual(sb["totals"]["batches"], 2)
        self.assertEqual(sb["totals"]["forecasts"], 5004)
        self.assertIsNone(sb["totals"]["brier"])
        self.assertFalse(sb["published"])
        self.assertIn("forecasts/daily/ledger.csv", self.build()["sources"])

    def test_scores_of_32_not_published(self):
        self.ledger()
        self.scores({"as_of": "2030-11-20", "batches": {"sealed": 2}, "n_forecasts": 5004, "n_resolved": 12, "scores_published": False,
                     "scores_note": "12 of 5004 forecasts have resolved.", "scores": None})
        sb = self.build()["scoreboard"]
        self.assertEqual(sb["totals"], {"batches": 2, "forecasts": 5004, "resolved": 12, "brier": None, "baseline": None})
        self.assertEqual(sb["months"], [])
        self.assertEqual(sb["notes"], ["12 of 5004 forecasts have resolved."])
        self.assertEqual(len(sb["days"]), 2)

    def test_scores_of_32_published(self):
        self.ledger()
        self.scores({"as_of": "2030-12-20", "batches": {"sealed": 2}, "n_forecasts": 5004, "n_resolved": 40, "scores_published": True,
                     "scores": {"as_of": "2030-12-18",
                                "overall": {"n_resolved": 38, "model": {"brier": 0.15123, "auc": 0.6}, "agency_rate": {"brier": 0.17}},
                                "by_month": [{"group": "2030-11", "n_forecasts": 5004, "n_resolved": 38, "model": {"brier": 0.151},
                                              "agency_rate": {"brier": 0.17}, "suppressed": False},
                                             {"group": "2030-12", "n_forecasts": 3, "n_resolved": 2, "model": None, "agency_rate": None,
                                              "suppressed": True}]}})
        sb = self.build()["scoreboard"]
        self.assertTrue(sb["published"])
        self.assertEqual(sb["updated"], "2030-12-18")
        self.assertEqual(sb["totals"]["brier"], 0.15123)
        self.assertEqual(sb["totals"]["baseline"], 0.17)
        self.assertEqual(sb["months"], [["2030-11", 2, 5004, 38, 0.151, 0.17], ["2030-12", None, 3, 2, None, None]])


if __name__ == "__main__":
    unittest.main()


class LiveVerifiedFlag(unittest.TestCase):
    """the verified flag of the live table goes into agency_meta.sofar; nothing else changes."""
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.res = os.path.join(self.tmp, "results")
        os.makedirs(self.res)
        base_files(self.res)
        self.out = os.path.join(self.tmp, "docs", "data", "explorer-data.js")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def build(self, **flag):
        tot = {"fiscal_year": 2027, "data_from": "2026-10-01", "data_to": "2026-11-08"}
        tot.update(flag)
        with open(os.path.join(self.res, "live_fy2027_totals.json"), "w") as f:
            json.dump(tot, f)
        write_csv(self.res, "live_fy2027_by_agency.csv", [
            dict(agency="Department of Veterans Affairs", abbreviation="VA", **share_row())])
        bx.build(self.res, self.out, repo="someone/project", today=date(2026, 11, 10))
        return load_js(self.out)

    def test_flag_false_true_absent(self):
        absent = self.build()
        self.assertNotIn("verified", absent["agency_meta"]["sofar"])
        f = self.build(verified=False)
        self.assertIs(f["agency_meta"]["sofar"]["verified"], False)
        t = self.build(verified=True)
        self.assertIs(t["agency_meta"]["sofar"]["verified"], True)

    def test_no_other_change(self):
        absent = self.build()
        for flag in (True, False):
            d = self.build(verified=flag)
            del d["agency_meta"]["sofar"]["verified"]
            self.assertEqual(d, absent)

    def test_page_shows_note_only_for_false(self):
        with open(os.path.join(REPO, "docs", "explorer.html"), encoding="utf-8") as f:
            page = f.read()
        self.assertIn("verified === false", page)
        self.assertIn('id="ag-verify"', page)
        self.assertIn("Not verified", page)
        self.assertNotIn("—", page.split('id="ag-verify"')[1][:300])
