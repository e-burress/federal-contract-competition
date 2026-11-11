"""Offline tests for scripts/build_brief.py: the one-page brief builds from small synthetic results in a temporary folder, shows the
numbers in the files, links only to the project, survives missing files, and carries no e-mail address and no office or company
identifiers.  No browser is needed (the print check is optional and not run here)."""
import csv
import importlib.util
import json
import os
import re
import shutil
import tempfile
import unittest
from datetime import date
from html.parser import HTMLParser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("build_brief", os.path.join(ROOT, "scripts", "build_brief.py"))
bb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bb)

REPO = "example-owner/example-project"
VOID = {"img", "br", "meta", "link", "input", "hr", "line", "circle", "polyline"}


class Checker(HTMLParser):
    """fails on unbalanced or misnested tags"""
    def __init__(self):
        super().__init__()
        self.stack, self.errors = [], []

    def handle_starttag(self, tag, attrs):
        if tag not in VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        pass

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(f"unexpected </{tag}>, open: {self.stack[-3:]}")
        else:
            self.stack.pop()


def write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f)


def synthetic(root, readme=True):
    """a small, made-up results folder with the files the brief reads"""
    res = os.path.join(root, "results")
    fc = os.path.join(root, "forecasts")
    os.makedirs(res)
    os.makedirs(fc)
    with open(os.path.join(root, "config.toml"), "w", encoding="utf-8") as f:
        f.write(f'[project]\nname = "Test Project: Findings"\nrepository = "{REPO}"\nauthor = "Someone"\n'
                '[analysis]\nfiscal_years = [2030, 2031]\n'
                '[forecasts.F1]\nid = "PIO-F1-test"\nsealed = "2030-10-24"\nn_solicitations = 77\nchecks = ["2031-03-31"]\n'
                '[forecasts.F2]\nid = "PIO-F2-test"\nsealed = "2030-10-24"\nfile = "forecasts/PIO-F2-test.json"\n')
    if readme:
        with open(os.path.join(root, "README.md"), "w", encoding="utf-8") as f:
            f.write("# Test\n\n## About\n\nBuilt by **Test Author** with AI assistance. Corrections welcome.\n\n**License:** MIT.\n")
    write_json(os.path.join(res, "atlas_headline.json"), {"k_single": 3880, "n_eval": 12345, "share": 0.31429, "offers_999_among_competed": 41})
    write_csv(os.path.join(res, "atlas_by_agency.csv"), [
        {"agency": "AAA", "k_single": 10, "n_eval": 100, "share": 0.0512, "ci_lo": 0.04, "ci_hi": 0.06},
        {"agency": "BBB", "k_single": 50, "n_eval": 100, "share": 0.4987, "ci_lo": 0.45, "ci_hi": 0.55}])
    write_csv(os.path.join(res, "atlas_by_agency_fy.csv"), [
        {"agency": "AAA", "fy": y, "k_single": 1, "n_eval": 10, "share": 0.1, "ci_lo": 0, "ci_hi": 0.2} for y in (2028, 2029, 2030)])
    write_csv(os.path.join(res, "atlas_civilian_totals.csv"), [
        {"scope": "all civilian agencies except GSA", "k_single": 100, "n_eval": 54321, "share": 0.2468, "ci_lo": 0.24, "ci_hi": 0.25,
         "n_agencies": 9, "median_award_value": 1, "dollar_weighted_share_po_def": "", "snapshot": "2030-11-09"}])
    write_csv(os.path.join(res, "threshold_sat_moved.csv"), [
        {"threshold": 250000, "ratio_pre": 1.81, "ratio_post": 1.22, "diff": -0.59, "diff_lo": -0.8, "diff_hi": -0.3},
        {"threshold": 350000, "ratio_pre": 0.93, "ratio_post": 2.47, "diff": 1.54, "diff_lo": 1.0, "diff_hi": 2.0},
        {"threshold": 200000, "ratio_pre": 1.4, "ratio_post": 1.3, "diff": -0.1, "diff_lo": -0.4, "diff_hi": 0.2}])
    write_json(os.path.join(res, "model_v2_card.json"), {"shipped": {"name_in_evaluation": "logistic v2"}, "evaluation": {"pooled": [
        {"sector": "all", "model": "agency rate (baseline)", "auc": 0.611}, {"sector": "all", "model": "logistic v2", "auc": 0.788}]}})
    hist = []
    for i, y in enumerate(range(2020, 2026)):
        for sector, base, n in (("civilian except GSA", 0.45, 1000), ("Department of Defense", 0.35, 2000), ("GSA", 0.25, 300)):
            hist.append({"sector": sector, "fy": y, "k_single": 1, "n_eval": n + i, "share": round(base - 0.01 * i, 4),
                         "ci_lo": 0, "ci_hi": 1, "award_kind": "all", "note": ""})
            hist.append({"sector": sector, "fy": y, "k_single": 1, "n_eval": 5, "share": 0.9, "ci_lo": 0, "ci_hi": 1,
                         "award_kind": "PO", "note": ""})
    write_csv(os.path.join(res, "history_by_fy.csv"), hist)
    th = []
    for y in range(2020, 2026):
        for sector in ("civilian", "defense"):
            for line in (150000, 250000):
                active = (line == 150000 and y <= 2022) or (line == 250000 and y >= 2023)
                th.append({"fy": y, "sector": sector, "line": line, "ratio": 1.9 if active else 1.05, "ci_lo": 0.9, "ci_hi": 2.1,
                           "obs_below": 1, "cf_below": 1, "n_window": 1, "n_base": 1, "active": str(active)})
    write_csv(os.path.join(res, "threshold_history.csv"), th)
    st = []
    for q in range(-4, 5):
        st.append({"sector": "civilian", "event_quarter": q, "line": 250000, "ratio": 1.0 if q < 0 else 1.7, "ci_lo": 0.8, "ci_hi": 1.9,
                   "obs_below": 1, "cf_below": 1, "n_awards": 1, "dates": "actual"})
        st.append({"sector": "civilian", "event_quarter": q, "line": 250000, "ratio": 1.0 if q < 4 else 1.7, "ci_lo": 0.8, "ci_hi": 1.9,
                   "obs_below": 1, "cf_below": 1, "n_awards": 1, "dates": "placebo, one year earlier"})
    write_csv(os.path.join(res, "threshold_staggered.csv"), st)
    write_json(os.path.join(res, "quality_summary.json"), {"n_flags": 17})
    write_json(os.path.join(fc, "PIO-F2-test.json"), {"id": "PIO-F2-test", "resolution_date": "2031-02-28", "quantities": [
        {"name": "bunching_ratio_350K", "forecast": 2.111, "interval80": [1.333, 2.888]}]})
    write_json(os.path.join(res, "tracking_PIO-F2-test.json"), {"as_of": "2030-11-09", "quantities": [
        {"name": "bunching_ratio_350K", "so_far": 1.444}]})
    # office- and company-level file: the brief must never read from it
    write_csv(os.path.join(res, "atlas_offices.csv"), [{"office_code": "36C24X", "office": "ZZTOP BUYING OFFICE", "vendor": "ACME WIDGETS LLC",
                                                        "uei": "ABCD1234EFGH", "share": 0.31429}])
    return root


class BriefTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = synthetic(os.path.join(self.tmp, "proj"))
        self._env = os.environ.pop("GITHUB_REPOSITORY", None)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        if self._env is not None:
            os.environ["GITHUB_REPOSITORY"] = self._env

    def build(self, **kw):
        return bb.build(self.root, today=date(2030, 11, 10), **kw)

    def test_builds_valid_html(self):
        out = os.path.join(self.root, "docs", "brief.html")
        page, notes = self.build(out=out)
        self.assertTrue(os.path.exists(out))
        self.assertEqual(notes, [])
        c = Checker()
        c.feed(page)
        c.close()
        self.assertEqual(c.errors, [])
        self.assertEqual(c.stack, [])
        self.assertEqual(page.count("<svg"), 3)
        self.assertIn("@media print", page)
        self.assertIn("prefers-color-scheme: dark", page)
        self.assertNotIn("—", page)                                    # no em dashes
        self.assertIsNone(re.search(r"\bnan\b|\bNone\b", page))
        self.assertIn("Brief built Nov 10, 2030", page)

    def test_only_project_links(self):
        page, _ = self.build()
        urls = set(re.findall(r"https?://[^\s\"'<>)]+", page))
        self.assertEqual(urls, {f"https://github.com/{REPO}"})
        hrefs = set(re.findall(r'href="([^"]+)"', page))
        self.assertEqual(hrefs, {"index.html", "explorer.html", f"https://github.com/{REPO}"})
        self.assertNotRegex(page, r"<script[^>]+src=|<link[^>]+href=|<img|@import|url\(")

    def test_numbers_match_files(self):
        page, _ = self.build()
        for s in ("31.4%", "12,345", "0.93 → 2.47", "1.81 to 1.22", "0.93 to 2.47", "0.79", "0.61", "5.1% (AAA)", "49.9% (BBB)",
                  "24.7% of 54,321", "FY2028 to FY2030", "41 competed awards", "17 agency quarters",
                  "2.11 (80% range 1.33 to 2.89)", "1.44", "Nov 9, 2030", "45.0% in FY2020 to 40.0% in FY2025",
                  "35.0% in FY2020 to 30.0% in FY2025"):
            self.assertIn(s, page)
        total = sum(1000 + 2000 + 300 + 3 * i for i in range(6))      # 'all' rows only, three sectors, six years
        self.assertIn(f"{total:,}", page)
        self.assertIn("FY2020 to FY2025", page)
        self.assertIn("one in three", page)                             # 31.4% is about one in three
        self.assertIn("at each of the 2 changes".capitalize(), page)
        self.assertIn("in the 4 quarters before the switch and 1.70 in the 5 quarters", page)
        self.assertIn("quarter +4, four quarters later", page)
        self.assertIn("did not clearly move", page)
        self.assertIn("later years are still being added", page)        # history ends before the last configured year

    def test_missing_files(self):
        os.remove(os.path.join(self.root, "results", "threshold_staggered.csv"))
        os.remove(os.path.join(self.root, "results", "model_v2_card.json"))
        page, notes = self.build()
        self.assertNotIn("agency by agency", page)
        self.assertEqual(page.count("<svg"), 2)
        self.assertTrue(any("threshold_staggered.csv" in x for x in notes))
        self.assertTrue(any("model_v2_card.json" in x for x in notes))
        self.assertNotIn("0.79", page)

    def test_everything_missing(self):
        shutil.rmtree(os.path.join(self.root, "results"))
        page, notes = self.build()
        self.assertIn("<h1>", page)
        self.assertIn("How it is made", page)
        self.assertEqual(page.count("<svg"), 0)
        self.assertGreaterEqual(len(notes), len(bb.FINDINGS) + len(bb.TILES))

    def test_unreadable_file(self):
        with open(os.path.join(self.root, "results", "threshold_sat_moved.csv"), "w", encoding="utf-8") as f:
            f.write("threshold,ratio_pre\n250000,abc\n")
        page, notes = self.build()
        self.assertNotIn("When the limit moved", page)
        self.assertTrue(any("threshold" in x for x in notes))

    def test_no_personal_or_office_data(self):
        page, _ = self.build()
        self.assertIsNone(re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", page))  # no e-mail address
        for s in ("36C24X", "ZZTOP", "ACME", "LLC", "ABCD1234EFGH", "Someone"):
            self.assertNotIn(s, page)
        text = re.sub(r"<style>.*?</style>|<script>.*?</script>|<[^>]+>", " ", page, flags=re.S)
        self.assertIsNone(re.search(r"\b(?=[A-Z0-9]*\d)(?=[A-Z0-9]*[A-Z])[A-Z0-9]{12}\b", text))   # UEI-like codes
        self.assertIsNone(re.search(r"\b\d{9}\b", text))                                           # DUNS-like numbers
        self.assertIsNone(re.search(r"\b(?!FY\d{4}\b)(?=\w*\d)(?=\w*[A-Z])[A-Z0-9]{6}\b", text))   # office-code-like tokens (fiscal years aside)

    def test_author_line_from_readme_only(self):
        page, _ = self.build()
        self.assertIn("Built by Test Author with AI assistance. Corrections welcome.", page)
        os.remove(os.path.join(self.root, "README.md"))
        page, _ = self.build()
        self.assertNotIn("Built by", page)

    def test_years_not_hard_coded(self):
        """more fiscal years in the files show up on the page without code changes"""
        p = os.path.join(self.root, "results", "history_by_fy.csv")
        with open(p, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        extra = [dict(r, fy="2031") for r in rows if r["fy"] == "2025"]
        write_csv(p, rows + extra)
        page, _ = self.build()
        self.assertIn("in FY2031", page)
        self.assertNotIn("still being added", page)


class PdfPagesTest(unittest.TestCase):
    def test_counts_page_objects(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "x.pdf")
            with open(p, "wb") as f:
                f.write(b"%PDF-1.4\n1 0 obj <</Type /Pages /Kids [2 0 R 3 0 R] /Count 2>>\n2 0 obj <</Type /Page>>\n3 0 obj <</Type/Page>>\n")
            self.assertEqual(bb.pdf_pages(p), 2)


class RealResultsTest(unittest.TestCase):
    """the brief builds from a temporary copy of the repository's own results (skipped when they are not there)"""
    def test_real_copy(self):
        if not os.path.exists(os.path.join(ROOT, "results", "atlas_headline.json")):
            self.skipTest("no results in this checkout")
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("config.toml", "README.md"):
                shutil.copy2(os.path.join(ROOT, name), tmp)
            shutil.copytree(os.path.join(ROOT, "results"), os.path.join(tmp, "results"),
                            ignore=shutil.ignore_patterns("atlas_offices*", "*.parquet"))
            shutil.copytree(os.path.join(ROOT, "forecasts"), os.path.join(tmp, "forecasts"), ignore=shutil.ignore_patterns("PIO-F1-*"))
            page, _ = bb.build(tmp)
            with open(os.path.join(tmp, "results", "atlas_headline.json"), encoding="utf-8") as f:
                h = json.load(f)
            self.assertIn(bb.pct(h["share"]), page)
            self.assertIn(f"{int(h['n_eval']):,}", page)
            self.assertIsNone(re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", page))
            self.assertNotIn("—", page)



class ModelTileTest(unittest.TestCase):
    """the forecast tile shows model version 2 (the model of the daily forecasts), and is left out without a usable card"""
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = synthetic(os.path.join(self.tmp, "proj"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def card(self, pooled):
        write_json(os.path.join(self.root, "results", "model_v2_card.json"),
                   {"shipped": {"name_in_evaluation": "logistic v2"}, "evaluation": {"pooled": pooled}})

    def test_version_2_with_its_best_baseline(self):
        self.card([{"sector": "all", "model": "logistic v2", "auc": 0.6924},
                   {"sector": "all", "model": "sector rate (baseline)", "auc": 0.496},
                   {"sector": "all", "model": "office history only (baseline)", "auc": 0.5835},
                   {"sector": "all", "model": "version 1 feature set (refit)", "auc": 0.68},
                   {"sector": "GSA", "model": "agency rate (baseline)", "auc": 0.9}])
        page, notes = bb.build(self.root, today=date(2030, 11, 10))
        self.assertIn("<b>0.69</b><span>forecast score (AUC) of model version 2, the model of the daily forecasts", page)
        self.assertIn("vs. 0.58 for its best simple baseline", page)
        self.assertNotIn("0.79", page)
        self.assertFalse(any("model" in x for x in notes))

    def test_shipped_name_comes_from_the_card(self):
        ship = "logistic v2, half-life 3 years, correction by sector"
        pooled = [{"sector": "all", "model": "logistic v2", "auc": 0.6924}, {"sector": "all", "model": ship, "auc": 0.7612},
                  {"sector": "all", "model": "office history only (baseline)", "auc": 0.5835}]
        write_json(os.path.join(self.root, "results", "model_v2_card.json"),
                   {"shipped": {"name_in_evaluation": ship}, "evaluation": {"pooled": pooled}})
        page, _ = bb.build(self.root, today=date(2030, 11, 10))
        self.assertIn("<b>0.76</b><span>forecast score (AUC) of model version 2", page)
        write_json(os.path.join(self.root, "results", "model_v2_card.json"), {"shipped": {}, "evaluation": {"pooled": pooled}})
        page, _ = bb.build(self.root, today=date(2030, 11, 10))
        self.assertIn("<b>0.69</b><span>forecast score (AUC) of model version 2", page)     # no name: the fallback row

    def test_no_usable_card_leaves_the_tile_out(self):
        os.remove(os.path.join(self.root, "results", "model_v2_card.json"))
        page, notes = bb.build(self.root, today=date(2030, 11, 10))
        self.assertNotIn("forecast score (AUC)", page)
        self.assertTrue(any("model_v2_card.json" in x for x in notes))
        self.card([{"sector": "all", "model": "logistic v2", "auc": 0.69}])   # no baseline row: not usable
        page, _ = bb.build(self.root, today=date(2030, 11, 10))
        self.assertNotIn("forecast score (AUC)", page)
        with open(os.path.join(self.root, "results", "model_v2_card.json"), "w", encoding="utf-8") as f:
            f.write('{"shipped": ')                                     # half written
        page, _ = bb.build(self.root, today=date(2030, 11, 10))
        self.assertNotIn("forecast score (AUC)", page)


if __name__ == "__main__":
    unittest.main()
