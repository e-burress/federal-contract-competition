"""Tests for the what-if calculator of the explorer (docs/explorer.html, section "Forecast model, version 2").

The page rebuilds forecast_lib.predict in JavaScript from the data that scripts/build_explorer.py writes (model2.calc).
calc_features and calc_predict below are a line-by-line Python copy of the page's features() and predict(); the tests check
them against the real forecast_lib.predict to 1e-6:
  - on a small synthetic model (always runs),
  - on the published model card in results/ (runs when the card has its calculator block),
  - on the private model file in the data folder (runs only on a machine that has it; it never leaves that folder).
The JavaScript itself was checked once against these numbers in a headless browser through the page's test hook
(explorer.html#calc-selftest writes fixed cases and their forecasts into the page); that check needs a browser and is not
part of this suite.
"""
import csv
import importlib.util
import json
import math
import os
import re
import shutil
import sys
import tempfile
import unittest
from datetime import date

import numpy as np
import pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))
import forecast_lib as fl  # noqa: E402


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bx = _load("build_explorer", os.path.join(REPO, "scripts", "build_explorer.py"))
tx = _load("test_explorer_data_helpers", os.path.join(REPO, "tests", "test_explorer_data.py"))
m30 = _load("model_v2_script", os.path.join(REPO, "scripts", "30_model_v2.py"))

DOD, GSA_FULL = "Department of Defense", "General Services Administration"
OFFICE = fl.BLOCKS["office history"][0]
WINDOWS = [3, 5, 7, 10, 14, 21, 30, 45, 60, 90]
FLAGS = ["kw_sole", "kw_brand", "has_presol", "has_ss"]


# ---- the page's arithmetic, in Python ---------------------------------------------------------------------------------
def sector_of(a):
    return DOD if a == DOD else "GSA" if a == GSA_FULL else "civilian except GSA"


def calc_features(calc, st):
    """features() of the page: the feature row for the chosen circumstances, history inputs from the typical values."""
    T = calc["typ"]
    s = sector_of(st["agency"])
    x = {"sector": s, "agency": st["agency"], "subagency": T["comp"][st["agency"]]["subagency"], "setaside": st["setaside"], "notice_type": st["notice_type"],
         "sol_letter": st["sol_letter"], "pscg": st["pscg"], "naics3": st["naics3"], "month": st["month"]}
    x.update(T["overall"])
    x.update(T["office"][s])
    x.update(T["agency"][st["agency"]])
    x["lo_subagency"], x["mo_subagency"] = T["comp"][st["agency"]]["lo_subagency"], T["comp"][st["agency"]]["mo_subagency"]
    x.update(T["pscg"][st["pscg"]])
    x.update(T["naics3"][st["naics3"]])
    w = float("nan") if st["window"] == "none" else float(st["window"])
    good = math.isfinite(w) and 0 < w <= 365
    x["log_window"] = math.log1p(min(max(w, 0), 120) if good else 14)
    x["short7"], x["short14"], x["window_missing"] = int(good and w <= 7), int(good and w <= 14), int(not good)
    x["late_sept"] = int(st["month"] == "9" and bool(st.get("late_sept")))
    for f in FLAGS:
        x[f] = int(bool(st.get(f)))
    return x


def calc_predict(calc, x):
    """predict() of the page: the calibrated probability."""
    def expit(z):
        z = max(-35.0, min(35.0, z))
        return 1.0 / (1.0 + math.exp(-z))

    def logit(p):
        p = min(1 - calc["eps"], max(calc["eps"], p))
        return math.log(p / (1 - p))
    lp = calc["b0"]
    for f, table in calc["cat"].items():
        lp += table.get(x[f], 0.0)
    for f, (b, mean, sd) in calc["cont"].items():
        lp += b * (x[f] - mean) / sd
    for f, b in calc["bin"].items():
        lp += b * x[f]
    g = calc["cal"]["groups"].get(x["sector"])
    a, b = calc["cal"]["by"][g] if g in calc["cal"]["by"] else (calc["cal"]["a"], calc["cal"]["b"])
    return expit(a + b * logit(expit(lp)))


def code_key(v):
    return (0 if v[:1].isdigit() else 1, v)


def cases(calc, n):
    """deterministic circumstances, built like the page's self-test cases"""
    T = calc["typ"]
    agencies = sorted(a for a in T["agency"] if sector_of(a) in T["office"] and a in T["comp"])
    sas, nts = list(calc["cat"]["setaside"]), list(calc["cat"]["notice_type"])
    letters = [v for v in ["Q", "R", "B", "T", "other", "unknown"] if v in calc["cat"]["sol_letter"]]
    pscgs = sorted(T["pscg"], key=code_key)
    naics = sorted(T["naics3"], key=lambda v: (v == "unknown",) + code_key(v))
    out = []
    for k in range(n):
        a = agencies[(k * 7) % len(agencies)]
        m = str(k % 12 + 1)
        out.append({"agency": a, "setaside": sas[k % len(sas)], "notice_type": nts[k % len(nts)],
                    "sol_letter": letters[k % len(letters)], "pscg": pscgs[(k * 13) % len(pscgs)], "naics3": naics[(k * 17) % len(naics)],
                    "window": "none" if k % 11 == 10 else str(WINDOWS[k % 11]), "month": m, "late_sept": int(m == "9"),
                    "kw_brand": k & 1, "kw_sole": (k >> 1) & 1, "has_presol": (k >> 2) & 1, "has_ss": (k >> 3) & 1})
    return out


# ---- the real functions ----------------------------------------------------------------------------------------------
def model_from_card(card):
    """A forecast_lib model built from the public card only: the card's full-precision coefficients and its correction."""
    c = card["shipped"]["calibration"]
    cal = {"a": c["a_pooled"], "b": c["b_pooled"]}
    if c.get("by_sector_group"):
        cal["groups"] = {s: g["group"] for g in c["by_sector_group"] for s in g["sectors"]}
        cal["by_group"] = {g["group"]: {"a": g["a"], "b": g["b"]} for g in c["by_sector_group"]}
    return {"kind": "logit", "logit": card["calculator"]["logit"], "calibration": cal}


def feature_frame(rows):
    f = pd.DataFrame(rows)
    for c in fl.CAT_FEATURES:
        f[c] = f[c].astype(object)
    for c in fl.CONT_FEATURES + fl.BIN_FEATURES:
        f[c] = f[c].astype(float)
    return f


# ---- privacy -----------------------------------------------------------------------------------------------------------
def strings_in(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield str(k)
            yield from strings_in(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from strings_in(v)
    elif isinstance(obj, str):
        yield obj


def names_no_component(level):
    """True when a sub-agency level names no component (the public labels of scripts/30_model_v2.py)"""
    ag, _, sub = str(level).partition(" | ")
    return level == "unknown" or sub == ag or sub.endswith("(name withheld)") or sub == m30.POOLED_COMPONENT


def read_levels(path):
    with open(path, encoding="utf-8", newline="") as f:
        return bx.calc_levels(list(csv.DictReader(f)))


def check_no_office_content(tc, calc, published_levels):
    """the calculator's data: office history only as medians of the three sectors; every category level published already."""
    tc.assertEqual(set(calc["typ"]["office"]), set(calc["typ"]["office"]) & set(bx.CALC_SECTORS))
    for f in OFFICE:
        for part, table in calc["typ"].items():
            if part == "office":
                continue
            rows = table.values() if part != "overall" else [table]
            tc.assertFalse(any(f in r for r in rows), f"{f} found under typ.{part}")
    for f, table in calc["cat"].items():
        tc.assertTrue(set(table) <= set(published_levels.get(f, {})), f"levels of {f} that the coefficient table does not publish")
    agencies = set(calc["cat"]["agency"])
    tc.assertTrue(set(calc["typ"]["comp"]) <= agencies)
    for lv in list(calc["cat"].get("subagency", {})) + [c["subagency"] for c in calc["typ"]["comp"].values()]:
        tc.assertTrue(names_no_component(lv), f"a component is named: {lv!r}")
    text = json.dumps(calc)
    for bad in ("office_key", "office_source", "sol_norm", "aac_code", "p_office_history", "n_recent", "uei", "duns"):
        tc.assertNotIn(bad, text.lower() if bad in ("uei", "duns") else text)
    # nothing that looks like an office code or a solicitation number (6+ characters, at least 2 letters and 2 digits)
    names = set(fl.CAT_FEATURES + fl.CONT_FEATURES + fl.BIN_FEATURES)
    for s in strings_in(calc):
        if s in names:
            continue
        for tok in re.findall(r"[A-Za-z0-9]+", s):
            if len(tok) >= 6 and len(re.findall(r"\d", tok)) >= 2 and len(re.findall(r"[A-Za-z]", tok)) >= 2:
                tc.fail(f"identifier-like text in the calculator data: {tok!r} in {s!r}")


# ---- a small synthetic model -------------------------------------------------------------------------------------------
def synthetic_card(seed=7):
    """a card whose calculator block is written by the real writer (scripts/30_model_v2.py calculator_card) from a small
    synthetic training table; the coefficients name components, which the writer must withhold"""
    rng = np.random.default_rng(seed)
    r = lambda: float(rng.normal(0, 0.4))  # noqa: E731
    subs = {DOD: ["Department of Defense | Defense Logistics Agency", "Department of Defense | Department of the Army"],
            "Department of Tests": ["Department of Tests | Bureau of Checks", "Department of Tests | Office of Small Things"],
            GSA_FULL: ["General Services Administration | Public Buildings Service"]}
    with_coef = [v for vs in subs.values() for v in vs if v != "Department of Tests | Office of Small Things"]
    cat = {"sector": {s: r() for s in [DOD, "GSA", "civilian except GSA", "unknown"]},
           "agency": {a: r() for a in [DOD, GSA_FULL, "Department of Tests", "unknown"]},
           "subagency": {**{v: r() for v in with_coef}, "unknown": r()},
           "setaside": {s: r() for s in ["none", "small business", "WOSB", "8(a)"]},
           "notice_type": {"Solicitation": r(), "Combined Synopsis/Solicitation": r()},
           "sol_letter": {s: r() for s in ["Q", "R", "B", "unknown"]},
           "pscg": {s: r() for s in ["59", "65", "R", "J"]},
           "naics3": {s: r() for s in ["332", "541", "unknown"]},
           "month": {str(m): r() for m in range(1, 13)}}
    centre = {"log_office_n": (7.2, 3.1), "log_prefix_n": (7.0, 3.0), "log_window": (2.5, 0.57), "log_depth": (7.7, 1.8),
              "mo_subagency": (0.0, 0.15), "mo_office": (0.0, 0.2), "mo_prefix": (0.0, 0.2)}
    cont = {c: [r(), *centre.get(c, (float(rng.normal(-1, 0.1)), 0.5))] for c in fl.CONT_FEATURES}
    logit = {"intercept": -0.6, "cat": cat, "cont": cont, "bin": {c: r() for c in fl.BIN_FEATURES}}
    n = 600
    ag = rng.choice([DOD, GSA_FULL, "Department of Tests"], n, p=[0.5, 0.2, 0.3])
    F = pd.DataFrame({"posted_fy": rng.choice([2020, 2024, 2025, 2026], n), "agency": ag,
                      "subagency": [subs[a][int(rng.integers(len(subs[a])))] for a in ag],
                      "sector": [sector_of(a) for a in ag],
                      "setaside": rng.choice(list(cat["setaside"]), n), "notice_type": rng.choice(list(cat["notice_type"]), n),
                      "sol_letter": rng.choice(list(cat["sol_letter"]), n), "pscg": rng.choice(list(cat["pscg"]), n),
                      "naics3": rng.choice(list(cat["naics3"]), n), "month": rng.choice([str(m) for m in range(1, 13)], n)})
    for c in fl.CONT_FEATURES:
        F[c] = rng.normal(cont[c][1], cont[c][2], n)
    for c in fl.BIN_FEATURES:
        F[c] = rng.integers(0, 2, n).astype(float)
    calc = m30.calculator_card(F, logit, 2026)
    cal = {"probability": "expit(a + b * logit(raw probability))", "a_pooled": 0.18, "b_pooled": 1.32,
           "by_sector_group": [{"group": "Defense", "sectors": [DOD], "a": 0.33, "b": 1.45, "n": 100},
                               {"group": "civilian", "sectors": ["GSA", "civilian except GSA"], "a": -0.59, "b": 0.87, "n": 100}]}
    return {"model": "single-offer forecast, version 2", "shipped": {"kind": "L2 logistic regression", "name_in_evaluation": "logistic v2", "calibration": cal},
            "evaluation": {"last_five_years_by_sector": [{"test_fy": 2025, "sector": "all", "model": "logistic v2", "auc": 0.79},
                                                         {"test_fy": 2026, "sector": "all", "model": "logistic v2", "auc": 0.81}]},
            "baselines": {"global": 0.23, "sector": {DOD: 0.24, "GSA": 0.15, "civilian except GSA": 0.21}, "agency": {"Department of Tests": 0.3}},
            "calculator": calc, "_private_logit": logit}


def write_v2(folder, card, coefficients=True):
    with open(os.path.join(folder, "model_v2_card.json"), "w", encoding="utf-8") as f:
        json.dump({k: v for k, v in card.items() if k != "_private_logit"}, f)
    tx.write_csv(folder, "model_v2_evaluation.csv", [dict(test_fy="pooled", sector="all", model="logistic v2", n=100, single_share=0.2,
                                                          mean_forecast=0.2, auc=0.7, brier=0.15, logloss=0.48, cal_slope=1.0)])
    if coefficients:
        m30.coefficient_table(card["calculator"]["logit"]).to_csv(os.path.join(folder, "model_v2_coefficients.csv"), index=False)


class SyntheticCalculator(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.res = os.path.join(self.tmp, "results")
        os.makedirs(self.res)
        tx.base_files(self.res)
        self.out = os.path.join(self.tmp, "docs", "data", "explorer-data.js")
        self.card = synthetic_card()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def build(self, **kw):
        bx.build(self.res, self.out, repo="someone/project", today=date(2026, 11, 10), **kw)
        return tx.load_js(self.out)

    def test_data_present_and_no_office_content(self):
        write_v2(self.res, self.card)
        calc = self.build()["model2"]["calc"]
        self.assertIsNotNone(calc)
        for k in ("b0", "cat", "cont", "bin", "cal", "eps", "typ", "labels", "defaults", "base", "years", "oot"):
            self.assertIn(k, calc)
        self.assertEqual(set(calc["typ"]["office"]), set(fl.SECTORS))
        self.assertNotIn("unknown", calc["cat"]["agency"])
        self.assertEqual(set(calc["typ"]["comp"]), {DOD, GSA_FULL, "Department of Tests"})
        self.assertEqual(calc["oot"], {"years": [2025, 2026], "auc": [0.79, 0.81]})
        self.assertEqual(calc["labels"]["setaside"]["none"], "No set-aside")
        self.assertEqual(calc["labels"]["naics3"]["541"], "industry 541")           # no title file: the code only
        ref = os.path.join(self.tmp, "reference")
        os.makedirs(ref, exist_ok=True)
        tx.write_csv(ref, "naics3_titles.csv", [dict(naics3="541", title="Professional, Scientific, and Technical Services", vintage="2022 NAICS"),
                                               dict(naics3="111", title="Crop Production", vintage="2022 NAICS")])
        labels = self.build()["model2"]["calc"]["labels"]["naics3"]
        self.assertEqual(labels["541"], "541: Professional, Scientific, and Technical Services")
        self.assertEqual(labels["332"], "industry 332 (older code)")
        self.assertEqual(labels["unknown"], "industry not given")
        published = read_levels(os.path.join(self.res, "model_v2_coefficients.csv"))
        check_no_office_content(self, calc, published)

    def test_exact_math(self):
        write_v2(self.res, self.card)
        calc = self.build()["model2"]["calc"]
        sts = cases(calc, 40)
        rows = [calc_features(calc, s) for s in sts]
        p_real = fl.predict(model_from_card(self.card), feature_frame(rows))
        p_page = np.array([calc_predict(calc, x) for x in rows])
        self.assertLess(np.abs(p_real - p_page).max(), 1e-6)
        self.assertGreater(np.std(p_page), 0.01)                   # the cases really differ
        self.assertEqual({sector_of(s["agency"]) for s in sts}, set(fl.SECTORS))   # every correction group is used

    def test_missing_files_hide_only_the_calculator(self):
        write_v2(self.res, self.card, coefficients=False)          # no coefficient table
        m = self.build()["model2"]
        self.assertIsNone(m["calc"])
        self.assertIsNotNone(m["card"])
        self.assertTrue(m["eval"])
        card = dict(self.card)
        card.pop("calculator")                                     # a card without the calculator block
        with open(os.path.join(self.res, "model_v2_card.json"), "w", encoding="utf-8") as f:
            json.dump(card, f)
        self.assertIsNone(self.build()["model2"]["calc"])
        os.remove(os.path.join(self.res, "model_v2_card.json"))     # no card at all: the evaluation is still shown
        m = self.build()["model2"]
        self.assertIsNone(m["calc"])
        self.assertTrue(m["eval"])

    def test_card_and_table_of_different_models(self):
        write_v2(self.res, self.card)
        other = synthetic_card(seed=8)                             # the coefficient table of another fit: no calculator
        m30.coefficient_table(other["calculator"]["logit"]).to_csv(os.path.join(self.res, "model_v2_coefficients.csv"), index=False)
        self.assertIsNone(self.build()["model2"]["calc"])

    def test_card_that_names_a_component_gives_no_calculator(self):
        card = json.loads(json.dumps({k: v for k, v in self.card.items() if k != "_private_logit"}))
        card["calculator"]["logit"] = self.card["_private_logit"]      # the names the writer must not publish
        write_v2(self.res, card)
        self.assertIsNone(self.build()["model2"]["calc"])

    def test_writer_withholds_component_names(self):
        table = m30.coefficient_table(m30.public_logit(self.card["_private_logit"]))
        text = table.to_csv(index=False) + json.dumps(self.card["calculator"])
        for name in ("Defense Logistics Agency", "Department of the Army", "Bureau of Checks", "Office of Small Things", "Public Buildings Service"):
            self.assertNotIn(name, text)
        sub = table[table.feature == "subagency"]
        self.assertTrue(all(names_no_component(v) for v in sub.level))
        self.assertTrue((sub[sub.level != "unknown"].note == m30.WITHHELD_NOTE).all())
        # the coefficients are unchanged, only relabelled
        self.assertEqual(sorted(m30.public_logit(self.card["_private_logit"])["cat"]["subagency"].values()),
                         sorted(self.card["_private_logit"]["cat"]["subagency"].values()))

    def test_calculator_is_not_counted_against_the_cap(self):
        write_v2(self.res, self.card, coefficients=False)
        self.build()
        cap = os.path.getsize(self.out) + 400
        write_v2(self.res, self.card)
        d = self.build(cap=cap)
        self.assertIsNotNone(d["model2"]["calc"])
        self.assertEqual(d["trimmed"], [])


class PageHasCalculator(unittest.TestCase):
    def test_page(self):
        with open(os.path.join(REPO, "docs", "explorer.html"), encoding="utf-8") as f:
            html = f.read()
        for s in ('id="m2-calc-card" hidden', "D.model2 && D.model2.calc", "#calc-selftest", "typical buying office"):
            self.assertIn(s, html)
        self.assertNotIn("—", html)                          # no em dashes


CARD_PATH = os.path.join(REPO, "results", "model_v2_card.json")


def published_card():
    if not (os.path.exists(CARD_PATH) and os.path.exists(os.path.join(REPO, "results", "model_v2_coefficients.csv"))):
        return None
    with open(CARD_PATH, encoding="utf-8") as f:
        card = json.load(f)
    return card if isinstance(card.get("calculator"), dict) else None


@unittest.skipUnless(published_card(), "results/model_v2_card.json has no calculator block yet")
class PublishedCalculator(unittest.TestCase):
    """The calculator built from the real results/ (written to a temporary file; docs/ is not touched)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        out = os.path.join(cls.tmp, "explorer-data.js")
        bx.build(os.path.join(REPO, "results"), out, repo="someone/project", today=date(2026, 11, 10))
        cls.data = tx.load_js(out)
        cls.calc = cls.data["model2"]["calc"]
        cls.card = published_card()
        cls.size = len(json.dumps(cls.calc, separators=(",", ":")))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_published_files_name_no_component(self):
        with open(os.path.join(REPO, "results", "model_v2_coefficients.csv"), encoding="utf-8", newline="") as f:
            levels = [r["level"] for r in csv.DictReader(f) if r["feature"] == "subagency"]
        self.assertTrue(levels and all(names_no_component(v) for v in levels))
        self.assertTrue(all(names_no_component(v) for v in self.card["calculator"]["logit"]["cat"]["subagency"]))
        self.assertNotIn("subtier", self.card.get("crosswalk_sam_to_usaspending") or {})

    def test_present_small_and_private(self):
        self.assertIsNotNone(self.calc)
        self.assertLess(self.size, 150 * 1024)
        published = read_levels(os.path.join(REPO, "results", "model_v2_coefficients.csv"))
        check_no_office_content(self, self.calc, published)

    def test_exact_math_against_forecast_lib(self):
        sts = cases(self.calc, 60)
        rows = [calc_features(self.calc, s) for s in sts]
        p_page = np.array([calc_predict(self.calc, x) for x in rows])
        p_real = fl.predict(model_from_card(self.card), feature_frame(rows))
        self.assertLess(np.abs(p_real - p_page).max(), 1e-6)

    def test_exact_math_against_the_private_model_file(self):
        """Only where the private model file exists (the maintainer's machine)."""
        try:
            from common import DATA
        except Exception:                                          # pragma: no cover
            self.skipTest("scripts/common.py could not be imported")
        path = os.path.join(DATA, "models", "model_v2.json")
        if not os.path.exists(path):
            self.skipTest("the private model file is not on this machine")
        model = fl.load_model(path)
        model["logit"] = m30.public_logit(model["logit"])          # the same coefficients, components relabelled as published
        sts = cases(self.calc, 60)
        rows = [calc_features(self.calc, s) for s in sts]
        p_page = np.array([calc_predict(self.calc, x) for x in rows])
        self.assertLess(np.abs(fl.predict(model, feature_frame(rows)) - p_page).max(), 1e-6)


if __name__ == "__main__":
    unittest.main()
