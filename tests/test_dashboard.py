"""Offline tests for scripts/build_dashboard.py: the long-history sections are left out when their files are missing or too thin,
and build valid HTML, with numbers taken from the files, from small synthetic results in a temporary folder."""
import csv
import importlib.util
import json
import os
import re
import tempfile
import unittest
from html.parser import HTMLParser

SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
_spec = importlib.util.spec_from_file_location("build_dashboard", os.path.join(SCRIPTS, "build_dashboard.py"))
bd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bd)

SECTIONS = [fn for fn, _, _, _ in bd.NEW_SECTIONS]
VOID = {"img", "br", "meta", "link", "input", "hr"}


class Checker(HTMLParser):
    """fails on unbalanced or misnested tags"""
    def __init__(self):
        super().__init__()
        self.stack, self.errors = [], []

    def handle_starttag(self, tag, attrs):
        if tag not in VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(f"unexpected </{tag}>, open: {self.stack[-3:]}")
        else:
            self.stack.pop()


def check_html(test, h):
    c = Checker()
    c.feed(h)
    c.close()
    test.assertEqual(c.errors, [])
    test.assertEqual(c.stack, [])
    test.assertNotIn("—", h)          # no em dashes in the new sections
    test.assertIsNone(re.search(r"\bnan\b|\bNone\b", h))


def write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f)


def synthetic(root):
    res, fig = os.path.join(root, "results"), os.path.join(root, "figures")
    os.makedirs(res)
    os.makedirs(fig)
    for png in ("single_offer_history.png", "threshold_history.png", "threshold_staggered.png", "threshold_scan.png", "drivers.png", "outcomes.png"):
        open(os.path.join(fig, png), "wb").close()
    hist = []
    for s, base in (("civilian except GSA", 0.40), ("GSA", 0.20), ("Department of Defense", 0.30)):
        for y in (2008, 2009, 2010):
            hist.append(dict(sector=s, fy=y, k_single=1, n_eval=1000, share=base - 0.01 * (y - 2008), ci_lo=0, ci_hi=1, award_kind="all"))
            hist.append(dict(sector=s, fy=y, k_single=1, n_eval=500, share=0.9, ci_lo=0, ci_hi=1, award_kind="PO"))
    write_csv(os.path.join(res, "history_by_fy.csv"), hist)
    th = []
    for s in ("civilian", "defense"):
        for y, r100, r150 in ((2010, 1.6, 1.1), (2011, 1.2, 1.7)):
            for L, r in ((100000, r100), (150000, r150), (250000, 1.0), (350000, 1.0)):
                th.append(dict(fy=y, sector=s, line=L, ratio=r, ci_lo=r - 0.1, ci_hi=r + 0.1, obs_below=1, cf_below=1, n_window=1,
                               n_base=1, active=False))
    write_csv(os.path.join(res, "threshold_history.csv"), th)
    st = []
    for dates, jump in (("actual", 0.5), ("placebo, one year earlier", 0.0)):
        for q in range(-8, 9):
            for L in (150000, 250000):
                r = 1.1 + (jump if q >= 0 and L == 250000 else 0)
                st.append(dict(sector="civilian", event_quarter=q, line=L, ratio=r, ci_lo=r - .1, ci_hi=r + .1, obs_below=1,
                               cf_below=1, n_awards=10, dates=dates))
    write_csv(os.path.join(res, "threshold_staggered.csv"), st)
    ch = [dict(family="simplified_acquisition", sector="civilian", old_line=100000.0, new_line=150000.0, change_date="2010-10-01",
               verdict="moved", did=0.8, did_lo=0.3, did_hi=1.3, reason="the new line's pile-up grew", years_missing="", data_complete=True),
          dict(family="simplified_acquisition", sector="all", old_line=100000.0, new_line=150000.0, change_date="2010-10-01",
               verdict="moved", did=0.7, did_lo=0.3, did_hi=1.3, reason="", years_missing="", data_complete=True),
          dict(family="micro_purchase", sector="defense", old_line=3000.0, new_line=3500.0, change_date="2015-10-01",
               verdict="not testable", did="", did_lo="", did_hi="", reason="no awards (missing FY2015)", years_missing="FY2015",
               data_complete=False)]
    write_csv(os.path.join(res, "threshold_scan_changes.csv"), ch)
    write_json(os.path.join(res, "threshold_scan_summary.json"), {"fiscal_years_present": [2008, 2009, 2010, 2011]})
    write_json(os.path.join(res, "drivers_summary.json"), {
        "years_in_model": [2009, 2010],
        "top_five_factors": [{"factor": "set-aside", "level": "small business", "versus": "no set-aside",
                              "marginal_effect_points": -15.22, "ci_95_points": [-17.03, -13.4]}],
        "blocks": {"split": "train FY2009, test FY2010", "auc_full_model": 0.6349}})
    est = []
    for o, role, unit, coef, lo, hi in (("overrun25", "outcome", "share", -0.004, -0.010, 0.001),
                                        ("any_termination", "outcome", "share", -0.004, -0.008, -0.0004),
                                        ("oblig_ratio_w", "outcome", "ratio", -0.001, -0.011, 0.009),
                                        ("placebo_coin", "placebo", "share", -0.002, -0.009, 0.006),
                                        ("bal_month", "balance", "months", 0.2, 0.1, 0.3)):
        for scope_type in ("pooled", "sector"):
            est.append(dict(outcome=o, role=role, unit=unit, label=f"Label {o}", follow_up="t to t+3", scope_type=scope_type,
                            scope="all sectors", cohorts="FY2008-FY2010", coef=coef, lo=lo, hi=hi, mean_multi_offer=0.05))
    write_csv(os.path.join(res, "outcomes_estimates.csv"), est)
    write_json(os.path.join(res, "outcomes_summary.json"), {"headline_follow_up": "t to t+3", "design": {"size_floor": 25000},
                                                            "follow_ups": {"t to t+3": {"share_of_awards_carrying_the_estimate": 0.27}}})
    write_json(os.path.join(res, "split_screen_summary.json"), {"years_used": [2009, 2010], "excess_ratio_pooled": {
        "defense|SAT|all": {"ratio": 1.15, "ci_lo": 1.128, "ci_hi": 1.175}, "defense|SAT|donut": {"ratio": 1.036, "ci_lo": 1.0, "ci_hi": 1.06},
        "gsa|MPT|all": {"ratio": 1.05, "ci_lo": 0.9, "ci_hi": 1.2}}})
    write_json(os.path.join(res, "supplier_base_summary.json"), {   # the supplier base (33_supplier_base.py)
        "companies_above_25k": {"first_year": 2008, "first": 57371, "last_year": 2010, "last": 45757, "change": -0.2024},
        "new_companies_above_25k": {"years": [2010], "share_of_companies_mean": 0.346, "share_of_awards_mean": 0.13},
        "staying_above_25k": {"next_year_mean": 0.4819},
        "competition_above_25k_pooled": {"period": "FY2011-FY2021 pooled", "single_offer_share_new": {"share": 0.215},
                                         "single_offer_share_established": {"share": 0.263}}})
    write_json(os.path.join(res, "quality_summary.json"), {
        "n_flags": 4, "by_metric": {"volume": 1, "offers_1": 3},
        "top_15": [{"agency": "Department of the Interior", "award_kind": "PO", "quarter": "FY2025Q3", "metric": "offers_1",
                    "before": 30.0, "after": 12.5}],
        "thresholds": {"min_points": 10.0, "z_bar": 6.0, "volume_pct": 40.0}})
    return res


class MissingFiles(unittest.TestCase):
    def test_every_section_is_empty_without_files(self):
        with tempfile.TemporaryDirectory() as d:
            res = os.path.join(d, "results")
            os.makedirs(res)
            for fn in SECTIONS:
                self.assertEqual(fn(res), "", fn.__name__)
            self.assertEqual(bd.new_sections(res), [])
            self.assertEqual(bd.whats_new([]), "")

    def test_thin_or_broken_files_are_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            res = os.path.join(d, "results")
            os.makedirs(res)
            # one year only
            write_csv(os.path.join(res, "history_by_fy.csv"),
                      [dict(sector="GSA", fy=2008, k_single=1, n_eval=10, share=0.2, ci_lo=0, ci_hi=1, award_kind="all")])
            # only one of the two years a change needs
            write_csv(os.path.join(res, "threshold_history.csv"),
                      [dict(fy=2010, sector="civilian", line=100000, ratio=1.5, ci_lo=1.4, ci_hi=1.6, active=True)])
            # every change not testable
            write_csv(os.path.join(res, "threshold_scan_changes.csv"),
                      [dict(family="micro_purchase", sector="civilian", old_line=1, new_line=2, change_date="2015-10-01",
                            verdict="not testable", did="", did_lo="", did_hi="", reason="x", years_missing="FY2015", data_complete=False)])
            write_json(os.path.join(res, "drivers_summary.json"), {"top_five_factors": []})
            with open(os.path.join(res, "outcomes_estimates.csv"), "w") as f:
                f.write("outcome,role\n")
            with open(os.path.join(res, "split_screen_summary.json"), "w") as f:
                f.write("{not json")
            write_json(os.path.join(res, "quality_summary.json"), {})
            for fn in SECTIONS:
                self.assertEqual(fn(res), "", fn.__name__)


class Synthetic(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.res = synthetic(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_each_section_builds_valid_html(self):
        for fn in SECTIONS:
            h = fn(self.res)
            self.assertTrue(h, fn.__name__)
            self.assertIn('<h2 class="num"', h)
            self.assertIn("How to read this", h)
            check_html(self, h)

    def test_numbers_come_from_the_files(self):
        self.assertIn("40.0%", bd.sec_history(self.res))                       # first year
        self.assertIn("38.0%", bd.sec_history(self.res))                       # latest year
        th = bd.sec_threshold_history(self.res)
        self.assertIn("2 of 2", th)
        self.assertIn("1.10 → <b>1.70</b>", th)
        self.assertIn("The pile-up followed the limit", th)                  # one change testable: not "every time"
        self.assertIn("Not testable yet", th)
        self.assertIn("1.60", th)                                              # staggered: 1.1 before, 1.6 after
        self.assertIn("threshold_staggered.png", th)
        scan = bd.sec_threshold_scan(self.res)
        self.assertIn("<b>2</b> changes", scan)                               # the "all" sector row is not double counted
        self.assertIn("+0.80", scan)
        self.assertIn("Oct 1, 2010", scan)
        self.assertIn("−15.2", bd.sec_drivers(self.res))
        out = bd.sec_outcomes(self.res)
        self.assertIn("<b>1 of 3</b>", out)
        self.assertIn("as they should", out)
        self.assertIn("three years after", out)
        self.assertNotIn("Label bal_month", out)
        self.assertNotIn("−0.0)", out)
        sp = bd.sec_split(self.res)
        self.assertIn("1.05 to 1.15", sp)
        self.assertIn("not enough data", sp)                                   # GSA donut missing
        su = bd.sec_supplier(self.res)
        self.assertIn("<b>45,757</b>", su)
        self.assertIn("20% fewer", su)
        self.assertIn("21.5%", su)
        q = bd.sec_quality(self.res)
        self.assertIn("<b>4</b>", q)
        self.assertIn("Interior", q)
        self.assertIn("30.0%", q)

    def test_missing_figure_leaves_out_only_the_image(self):
        os.remove(os.path.join(self.tmp.name, "figures", "drivers.png"))
        h = bd.sec_drivers(self.res)
        self.assertTrue(h)
        self.assertNotIn("<img", h)

    def test_whats_new_and_numbering(self):
        present = bd.new_sections(self.res)
        self.assertEqual([s for s, _, _ in present], ["history", "limits", "scan", "drivers", "outcomes", "split", "supplier", "quality"])
        line = bd.whats_new(present)
        self.assertIn('href="#history"', line)
        page = bd.number_sections('<h2 class="num" id="a">A</h2><h2 id="x">X</h2>' + "".join(h for _, _, h in present))
        self.assertIn('id="a">1. A', page)
        self.assertIn('id="quality">9. Data quality', page)
        self.assertIn('id="x">X', page)

    def test_placebo_failure_is_reported(self):
        with open(os.path.join(self.res, "outcomes_estimates.csv"), encoding="utf-8") as f:
            est = list(csv.DictReader(f))
        for r in est:
            if r["outcome"] == "placebo_coin":
                r["lo"], r["hi"] = "0.01", "0.03"
        write_csv(os.path.join(self.res, "outcomes_estimates.csv"), est)
        self.assertIn("Read the rows with care", bd.sec_outcomes(self.res))


class Polish(unittest.TestCase):
    """the generated GSA sentence, the page's scope line, history cautions and the two quality lists"""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.res = synthetic(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_gsa_note_comes_from_the_file(self):
        self.assertEqual(bd.gsa_trend_note(self.res), "")                   # no file: no sentence
        rows = [dict(fy=y, k_single=1, n_eval=10, share=v, ci_lo=0, ci_hi=1, scope=sc)
                for y, v, sc in ((2024, 0.61, "GSA only"), (2025, 0.4, "GSA only"), (2026, 0.123, "GSA only"),
                                 (2024, 0.30, "all civilian agencies"))]
        write_csv(os.path.join(self.res, "atlas_civilian_by_fy.csv"), rows)
        t = bd.gsa_trend_note(self.res)
        self.assertIn("fell from 61% in FY2024 to 12% in FY2026", t)
        self.assertNotIn("55%", t)
        self.assertNotIn("—", t)

    def test_scope_line_is_true(self):
        self.assertIn("Department of Defense is not in them", bd.scope_text([]))
        self.assertNotIn("long-history", bd.scope_text([]))
        t = bd.scope_text(bd.new_sections(self.res))
        self.assertIn("FY2024 to FY2026 sections cover civilian agencies only", t)
        self.assertIn("also cover the Department of Defense", t)
        self.assertIn("90 days late", t)
        self.assertNotIn("—", t)

    def test_unverified_live_table_is_marked(self):
        note = bd.live_verification_note({"fiscal_year": 2027, "verified": False})
        self.assertIn("Not verified", note)
        self.assertNotIn("—", note)
        check_html(self, note)
        self.assertEqual(bd.live_verification_note({"fiscal_year": 2027, "verified": True}), "")
        self.assertEqual(bd.live_verification_note({"fiscal_year": 2027}), "")          # files written before the check existed
        with open(os.path.join(SCRIPTS, "build_dashboard.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("live_parts.append(live_verification_note(lt) +", src)

    def test_history_caution_is_shown(self):
        p = os.path.join(self.res, "history_by_fy.csv")
        with open(p, encoding="utf-8") as f:
            hist = list(csv.DictReader(f))
        for r in hist:
            r["note"] = "FY2008 reporting change: test note." if (r["sector"], r["fy"], r["award_kind"]) == ("civilian except GSA", "2008", "all") else ""
        write_csv(p, hist)
        h = bd.sec_history(self.res)
        check_html(self, h)
        self.assertIn("Caution", h)
        self.assertIn("FY2008 reporting change: test note.", h)
        self.assertIn("FY2008 is not comparable", h)
        self.assertIn("From FY2009, <b>39.0%</b>", h)

    def test_quality_lists_are_separate(self):
        write_json(os.path.join(self.res, "quality_summary.json"), {
            "n_flags": 3, "by_metric": {"volume": 1, "offers_1": 1, "competed": 1},
            "top_share_flags": [{"agency": "Department of the Interior", "award_kind": "PO", "quarter": "FY2025Q3", "metric": "offers_1",
                                 "before": 30.0, "after": 12.5, "change_points": -17.5}],
            "top_volume_flags": [{"agency": "General Services Administration", "award_kind": "IDV", "quarter": "FY2025Q1",
                                  "metric": "volume", "before": 401.0, "after": 2805.0, "change_points": 599.5}],
            "top_15": [], "thresholds": {"min_points": 10.0, "z_bar": 6.0, "volume_pct": 40.0}})
        q = bd.sec_quality(self.res)
        check_html(self, q)
        self.assertIn("Largest shifts in shares", q)
        self.assertIn("Largest changes in the number of awards", q)
        self.assertIn("−17.5 points", q)
        self.assertIn("+600%", q)
        self.assertLess(q.index("Interior"), q.index("Largest changes in the number"))
        self.assertGreater(q.index("2,805"), q.index("Largest changes in the number"))
        self.assertNotIn("long history", q)
        write_json(os.path.join(self.res, "quality_history_summary.json"),
                   {"n_flags": 529, "inputs": ["base_actions_FY2008.parquet", "base_actions_FY2009.parquet"]})
        q = bd.sec_quality(self.res)
        check_html(self, q)
        self.assertIn("long history (FY2008 to FY2009, Defense included) flags <b>529</b>", q)

    def test_older_quality_summary_is_split_by_metric(self):
        old = {"top_15": [{"metric": "volume", "agency": "A"}, {"metric": "offers_1", "agency": "B"}]}
        share, vol = bd.quality_tops(old)
        self.assertEqual([f["agency"] for f in share], ["B"])
        self.assertEqual([f["agency"] for f in vol], ["A"])


LEDGER = ("batch_id,forecast_date,made_at_utc,snapshot_date,model_version,model_sha256,n_forecasts,n_civilian_except_gsa,n_gsa,"
          "n_defense,n_unknown,n_fresh,mean_forecast,batch_sha256\n"
          "PIO-D-20301101,2030-11-01,2030-11-01T11:00:00Z,2030-11-01,2,abc,300,100,0,200,0,30,0.225,ghi\n"
          "PIO-D-20301102,2030-11-02,2030-11-02T11:00:00Z,2030-11-02,2,abc,100,50,0,50,0,70,0.2,def\n")


class DailyForecasts(unittest.TestCase):
    """the small daily-forecast block under the scoreboard: true before the first batch and after it"""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.res, self.daily = os.path.join(self.tmp.name, "results"), os.path.join(self.tmp.name, "daily")
        os.makedirs(self.res)
        os.makedirs(self.daily)

    def tearDown(self):
        self.tmp.cleanup()

    def ledger(self, text):
        with open(os.path.join(self.daily, "ledger.csv"), "w", encoding="utf-8", newline="") as f:
            f.write(text)

    def test_before_the_first_batch(self):
        for text in (None, "batch_id,forecast_date,n_forecasts\n", "batch_id,forecast_date,n_forecasts\nX,2030-11-0"):
            if text is not None:
                self.ledger(text)
            h = bd.daily_block(self.res, self.daily)
            check_html(self, h)
            self.assertIn("the first batch has not been sealed yet", h)
            self.assertNotIn("kpi", h)

    def test_after_the_first_batch(self):
        self.ledger(LEDGER)
        h = bd.daily_block(self.res, self.daily)
        check_html(self, h)
        self.assertNotIn("not been sealed", h)
        self.assertIn("<b>2</b><span>daily batches sealed since Nov 1, 2030</span>", h)
        self.assertIn("<b>400</b><span>forecasts in them</span>", h)
        self.assertIn("<b>25%</b><span>fresh", h)                      # (30 + 70) / 400
        self.assertNotIn("resolved", h)
        write_json(os.path.join(self.res, "daily_forecast_scores.json"),
                   {"n_forecasts": 400, "n_resolved": 12, "scores_published": False, "scores": None})
        h = bd.daily_block(self.res, self.daily)
        self.assertIn("<b>12</b><span>of 400 forecasts resolved so far</span>", h)
        self.assertIn("Scores are published once enough forecasts have resolved.", h)
        write_json(os.path.join(self.res, "daily_forecast_scores.json"),
                   {"n_forecasts": 400, "n_resolved": 40, "scores_published": True,
                    "scores": {"overall": {"n_resolved": 38, "model": {"brier": 0.1512, "auc": 0.6789}, "agency_rate": {"brier": 0.1701}}}})
        h = bd.daily_block(self.res, self.daily)
        check_html(self, h)
        self.assertIn("Scores on 38 resolved forecasts: Brier score 0.151 against 0.170 for the agency's past rate", h)
        self.assertIn("AUC 0.679", h)

    def test_broken_scores_file(self):
        self.ledger(LEDGER)
        with open(os.path.join(self.res, "daily_forecast_scores.json"), "w", encoding="utf-8") as f:
            f.write("{not json")
        h = bd.daily_block(self.res, self.daily)
        check_html(self, h)
        self.assertIn("daily batches sealed", h)
        self.assertNotIn("resolved so far", h)


if __name__ == "__main__":
    unittest.main()
