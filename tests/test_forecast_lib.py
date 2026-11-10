"""Offline tests for scripts/forecast_lib.py (and two helpers of scripts/30_model_v2.py) on small synthetic tables."""
import importlib.util
import os
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))
import forecast_lib as fl  # noqa: E402

AGENCY, SUB = "Department of Tests", "Bureau of Checks"


def awards(rows):
    """rows: (solicitation number, date, office, extent competed, offers[, value[, agency]]) -> a tiny base_actions table."""
    t = pd.DataFrame([list(r) + [None] * (7 - len(r)) for r in rows],
                     columns=["solicitation_identifier", "action_date", "awarding_office_code", "extent_competed_code", "offers",
                              "base_and_all_options_value", "awarding_agency_name"])
    t["action_date"] = pd.to_datetime(t.action_date)
    t["base_and_all_options_value"] = t.base_and_all_options_value.fillna(1000.0).astype(float)
    t["awarding_agency_name"] = t.awarding_agency_name.fillna(AGENCY)
    t["awarding_sub_agency_name"] = SUB
    t["award_kind"] = "PO"
    t["product_or_service_code"] = "6515"
    t["naics_code"] = "339112"
    return t


def history_rows(extra=()):
    """office AAA111: 40 competed awards in 2019 Q1..Q4 (10 per quarter), 3 of each 10 with one offer; plus `extra` rows."""
    rows = []
    for month in (2, 5, 8, 11):
        for i in range(10):
            rows.append((f"AAA11119Q{month:02d}{i:02d}", f"2019-{month:02d}-15", "AAA111", "A", 1 if i < 3 else 4))
    return awards(rows + list(extra))


def sols(**cols):
    base = {"sol_norm": ["AAA11120Q0001"], "first_posted": ["2020-05-10 09:00:00"], "window_first_days": [10.0],
            "type_first": ["Solicitation"], "set_aside_code": ["SBA"], "naics": ["339112"], "psc": ["6515"],
            "department": ["TESTS, DEPARTMENT OF"], "sub_tier": ["BUREAU OF CHECKS"], "aac_code": ["AAA111"], "kw_brand": [0], "kw_sole": [1]}
    base.update(cols)
    n = max(len(v) for v in base.values())
    return pd.DataFrame({k: (list(v) * n if len(v) == 1 else list(v)) for k, v in base.items()})


FEATURE_COLS = fl.CAT_FEATURES + fl.CONT_FEATURES + fl.BIN_FEATURES + ["p_office_history", "office_key"]


class TestNormAndKeys(unittest.TestCase):
    def test_norm(self):
        self.assertEqual(fl.norm("w912nw-26-q-a027 "), "W912NW26QA027")
        self.assertEqual(fl.norm(None), "")
        self.assertEqual(list(fl.norm_series(["w912nw-26-q-a027", None, "36C24618Q0001"])), ["W912NW26QA027", "", "36C24618Q0001"])

    def test_groups(self):
        self.assertEqual(list(fl.psc_group(["6515", "R499", "", None, "65"])), ["65", "R", "", "", "65"])
        self.assertEqual(list(fl.sol_letter(["W912NW26QA027", "SPE7L318T199Y", "36C24618R0001", "N0010420X0001", "RFQ12345"])),
                         ["Q", "T", "R", "other", "unknown"])
        self.assertEqual(list(fl.setaside_group(["SBA", None, "NONE", "SDVOSBC", "XYZ"])), ["small business", "none", "none", "SDVOSB", "other"])
        self.assertEqual(list(fl.sector_of([fl.DOD, fl.GSA_NAME, "Department of State", "unknown"])),
                         [fl.DOD, "GSA", "civilian except GSA", "unknown"])

    def test_quarter_and_fiscal_year(self):
        d = ["2019-12-31", "2020-01-01", "2020-09-30", "2020-10-01", None]
        self.assertEqual(list(fl.quarter_index(d)), [2019 * 4 + 3, 2020 * 4, 2020 * 4 + 2, 2020 * 4 + 3, -1])
        self.assertEqual(list(fl.fiscal_year(d)), [2020, 2020, 2020, 2021, -1])


class TestOutcomeRule(unittest.TestCase):
    def setUp(self):
        self.s = pd.DataFrame({"sol_norm": ["AAA11120Q0001", "AAA11120Q0002", "AAA11120Q0003", "AAA11120Q0004", "AAA11120Q0005", "SHORT1"],
                               "first_posted": pd.to_datetime(["2020-05-10 14:00"] * 6)})
        self.a = awards([
            ("AAA111-20-Q-0001", "2020-05-09", "AAA111", "A", 5),            # before posting: ignored
            ("aaa11120q0001", "2020-07-01", "AAA111", "A", 1),               # earliest on or after posting: one offer
            ("AAA11120Q0001", "2020-08-01", "AAA111", "A", 7),
            ("AAA11120Q0002", "2020-06-01", "AAA111", "C", 1),               # not competed
            ("AAA11120Q0003", "2020-06-01", "AAA111", "F", 999),             # offers unusable
            ("AAA11120Q0004", "2020-06-01", "AAA111", "D", 3, 10.0),         # same day: the larger value wins
            ("AAA11120Q0004", "2020-06-01", "AAA111", "D", 1, 99.0),
            ("AAA11120Q0005", "2022-06-01", "AAA111", "A", 1),               # later than MAX_WAIT_DAYS
            ("SHORT1", "2020-06-01", "AAA111", "A", 1),                      # number too short to match safely
        ])

    def test_rule(self):
        best = fl.earliest_awards(self.s, fl.prepare_awards(self.a))
        out = fl.label_outcomes(self.s, best).set_index("sol_norm")
        self.assertEqual(out.reason.to_dict(), {"AAA11120Q0001": "resolved", "AAA11120Q0002": "award_not_competed",
                                                "AAA11120Q0003": "offers_blank_or_999", "AAA11120Q0004": "resolved",
                                                "AAA11120Q0005": "no_matching_award", "SHORT1": "no_matching_award"})
        self.assertEqual(out.y["AAA11120Q0001"], 1.0)
        self.assertEqual(out.y["AAA11120Q0004"], 1.0)                       # the 99.0 award (one offer) won the tie
        self.assertTrue(np.isnan(out.y["AAA11120Q0002"]))
        self.assertEqual(out.award_date["AAA11120Q0001"], pd.Timestamp("2020-07-01"))
        self.assertEqual(int(out.matched.sum()), 4)
        self.assertEqual(int(out.resolved.sum()), 2)

    def test_award_on_the_posting_day_counts(self):
        a = awards([("AAA11120Q0001", "2020-05-10", "AAA111", "A", 2)])
        out = fl.label_outcomes(self.s, fl.earliest_awards(self.s, fl.prepare_awards(a)))
        self.assertEqual(out.y[0], 0.0)

    def test_one_file_at_a_time_gives_the_same_answer(self):
        aw = fl.prepare_awards(self.a)
        whole = fl.earliest_awards(self.s, aw)
        idx = fl.solicitation_index(self.s)
        step = fl.earliest_awards(idx, aw.iloc[5:].reset_index(drop=True), fl.earliest_awards(idx, aw.iloc[:5].reset_index(drop=True)))
        both = fl.combine_earliest([fl.earliest_awards(idx, aw.iloc[5:].reset_index(drop=True)), fl.earliest_awards(idx, aw.iloc[:5].reset_index(drop=True))])
        for other in (step, both):
            pd.testing.assert_frame_equal(whole.sort_values("sol_norm").reset_index(drop=True),
                                          other.sort_values("sol_norm").reset_index(drop=True), check_dtype=False, check_categorical=False)

    def test_no_awards(self):
        out = fl.label_outcomes(self.s, fl.earliest_awards(self.s, fl.prepare_awards(self.a.iloc[:0])))
        self.assertEqual(int(out.matched.sum()), 0)


class TestHistory(unittest.TestCase):
    def setUp(self):
        self.h = fl.history_from_awards(fl.prepare_awards(history_rows()))

    def test_counts(self):
        off = self.h["levels"]["office"]
        self.assertEqual(off.n.tolist(), [10, 10, 10, 10])
        self.assertEqual(off.k.tolist(), [3, 3, 3, 3])
        self.assertEqual(self.h["levels"]["prefix"].key.unique().tolist(), ["AAA111"])
        self.assertEqual(self.h["levels"]["pscg"].key.unique().tolist(), ["65"])
        q = np.array([2019 * 4 + 3, 2019 * 4 + 1, 2018 * 4 + 3, 2025 * 4])
        n, k, _ = fl.window_counts(self.h, "office", ["AAA111"] * 4, q)
        self.assertEqual(n.tolist(), [40, 20, 0, 0])
        self.assertEqual(k.tolist(), [12, 6, 0, 0])
        n, _, _ = fl.window_counts(self.h, "office", ["NOPE"], np.array([2019 * 4 + 3]))
        self.assertEqual(n.tolist(), [0])

    def test_merge_adds(self):
        both = fl.merge_history(self.h, self.h)
        self.assertEqual(both["levels"]["office"].n.tolist(), [20, 20, 20, 20])
        self.assertEqual(both["levels"]["office"].agency.unique().tolist(), [AGENCY])

    def test_office_agency_is_as_of(self):
        moved = history_rows([("AAA11121Q9999", "2021-03-01", "AAA111", "A", 2, 5.0, "Department of Later")])
        h = fl.history_from_awards(fl.prepare_awards(moved))
        ag, _, found = fl.latest_info(h, "office", ["AAA111", "AAA111", "AAA111"], np.array([2018 * 4, 2020 * 4, 2021 * 4 + 1]))
        self.assertEqual(found.tolist(), [False, True, True])
        self.assertEqual(ag.tolist(), ["", AGENCY, "Department of Later"])

    def test_json_round_trip(self):
        back = fl.history_from_json(fl.history_to_json(self.h))
        for name, t in self.h["levels"].items():
            pd.testing.assert_frame_equal(t.reset_index(drop=True), back["levels"][name], check_dtype=False)


class TestFeatures(unittest.TestCase):
    def setUp(self):
        self.h = fl.history_from_awards(fl.prepare_awards(history_rows()))

    def test_values(self):
        f = fl.build_features(sols(), self.h)                                # posted 2020 Q2 -> history through 2019 Q4
        r = f.iloc[0]
        self.assertEqual((r.agency, r.sector, r.office_key, r.setaside, r.sol_letter, r.month), (AGENCY, "civilian except GSA", "AAA111", "small business", "Q", "5"))
        g = fl.shrunk_rate(12, 40, fl.PRIOR_RATE)                           # every level sees the same 40 awards here
        agency = fl.shrunk_rate(12, 40, fl.shrunk_rate(12, 40, g))          # agency shrunk toward its sector, the sector toward all
        self.assertAlmostEqual(r.p_office_history, fl.shrunk_rate(12, 40, agency), places=12)
        self.assertAlmostEqual(r.log_office_n, np.log1p(40))
        self.assertEqual((r.office_known, r.short7, r.short14, r.kw_sole, r.kw_brand, r.has_presol), (1, 0, 1, 1, 0, 0))
        self.assertAlmostEqual(r.log_window, np.log1p(10.0))
        self.assertLess(abs(r.mo_office), 0.05)                              # the last year looks like the three years before

    def test_momentum_follows_the_last_year(self):
        older = [(f"AAA11118Q8{i:03d}", "2018-03-20", "AAA111", "A", 4) for i in range(60)]       # 2018: never a single offer; 2019: 3 in 10
        h = fl.history_from_awards(fl.prepare_awards(history_rows(older)))
        self.assertGreater(fl.build_features(sols(), h).mo_office[0], 0.2)
        self.assertEqual(fl.build_features(sols(sol_norm=["ZZZ99920Q0001"], aac_code=["ZZZ999"]), h).mo_office[0], 0.0)

    def test_no_history_gives_the_prior(self):
        f = fl.build_features(sols(sol_norm=["ZZZ99920Q0001"], aac_code=["ZZZ999"], department=[None]), self.h)
        r = f.iloc[0]
        self.assertEqual((r.agency, r.sector, r.office_key, r.office_known), ("unknown", "unknown", "", 0))
        self.assertAlmostEqual(r.p_office_history, fl.shrunk_rate(12, 40, fl.PRIOR_RATE))     # the government-wide rate

    def test_lag_respects_the_reporting_delay(self):
        # posted in 2020 Q1: the newest quarter used is 2019 Q3, so the 2019 Q4 awards are not counted yet
        f = fl.build_features(sols(first_posted=["2020-02-01"]), self.h)
        self.assertAlmostEqual(f.log_office_n[0], np.log1p(30))

    def test_awards_after_posting_do_not_change_features(self):
        s = sols(sol_norm=["AAA11120Q0001", "AAA11120R0002"], first_posted=["2020-05-10", "2020-01-20"])
        before = fl.build_features(s, self.h)
        later = [(f"AAA11120Q9{i:03d}", d, "AAA111", "A", 1) for i, d in enumerate(
            ["2020-01-20", "2020-01-21", "2020-03-31", "2020-05-10", "2020-05-11", "2020-12-01", "2021-06-01"] * 5)]
        after = fl.build_features(s, fl.history_from_awards(fl.prepare_awards(history_rows(later))))
        pd.testing.assert_frame_equal(before[FEATURE_COLS], after[FEATURE_COLS])
        # the same holds for the solicitation's own award (the outcome)
        own = fl.build_features(s, fl.history_from_awards(fl.prepare_awards(history_rows([("AAA11120Q0001", "2020-06-30", "AAA111", "A", 1)]))))
        pd.testing.assert_frame_equal(before[FEATURE_COLS], own[FEATURE_COLS])
        # ... while an award from the history window does change them (the test can fail)
        early = fl.build_features(s, fl.history_from_awards(fl.prepare_awards(history_rows([("AAA11119Q7777", "2019-06-01", "AAA111", "A", 1)]))))
        self.assertFalse(np.allclose(before.lo_office, early.lo_office))

    def test_as_of_caps_the_history(self):
        s = sols(first_posted=["2020-05-10"])
        capped = fl.build_features(s, self.h, as_of="2019-08-15")           # 2019 Q3 -> history through 2019 Q1 only
        self.assertAlmostEqual(capped.log_office_n[0], np.log1p(10))
        missing = fl.build_features(sols(first_posted=[None]), self.h, as_of="2020-05-10")
        self.assertAlmostEqual(missing.log_office_n[0], np.log1p(40))

    def test_earlier_notice_must_come_first(self):
        s = sols(sol_norm=["AAA11120Q0001"] * 4, presol_first_posted=["2020-04-01", "2020-05-20", None, None],
                 ss_first_posted=[None, "2020-03-01", None, None], base_type_first=["Solicitation", "Solicitation", "Presolicitation", "Solicitation"])
        f = fl.build_features(s, self.h)
        self.assertEqual(f.has_presol.tolist(), [1, 0, 1, 0])
        self.assertEqual(f.has_ss.tolist(), [0, 1, 0, 0])

    def test_window_flags(self):
        f = fl.build_features(sols(window_first_days=[3.0, 14.0, 45.0, -2.0, None]), self.h)
        self.assertEqual(f.short7.tolist(), [1, 0, 0, 0, 0])
        self.assertEqual(f.short14.tolist(), [1, 1, 0, 0, 0])
        self.assertEqual(f.window_missing.tolist(), [0, 0, 0, 1, 1])
        d = fl.build_features(sols(first_deadline=["2020-05-20 09:00:00+00:00"]).drop(columns="window_first_days"), self.h)
        self.assertAlmostEqual(d.log_window[0], np.log1p(10.0))

    def test_crosswalk_and_market_depth(self):
        h = dict(self.h)
        h["crosswalk"] = fl.build_crosswalk(["Tests, Department of"] * 6, ["Bureau of Checks"] * 6, [AGENCY] * 6, [SUB] * 6)
        h["market_depth"] = {"339112": 99}
        f = fl.build_features(sols(sol_norm=["OLDSTYLE-20-0001"], aac_code=[None]), h)
        self.assertEqual((f.agency[0], f.subagency[0], f.office_key[0]), (AGENCY, AGENCY + " | " + SUB, ""))
        self.assertAlmostEqual(f.log_depth[0], np.log1p(99))
        self.assertEqual(f.depth_missing[0], 0)
        self.assertEqual(fl.build_features(sols(naics=["999999"]), h).depth_missing[0], 1)

    def test_trimmed_history_scores_recent_solicitations_the_same(self):
        old = [(f"AAA11115Q{i:04d}", "2015-03-01", "AAA111", "A", 1) for i in range(30)]
        full = fl.history_from_awards(fl.prepare_awards(history_rows(old)))
        s = sols()
        pd.testing.assert_frame_equal(fl.build_features(s, full)[FEATURE_COLS],
                                      fl.build_features(s, fl.trim_history(full, 2019 * 4))[FEATURE_COLS])


def toy_model(history):
    logit = {"intercept": -1.2, "cat": {"setaside": {"small business": -0.4, "none": 0.3}, "sector": {fl.DOD: 0.1}},
             "cont": {"lo_office": [0.5, -1.0, 0.8], "log_window": [-0.2, 2.5, 0.6]}, "bin": {"kw_sole": 1.1, "short7": 0.2}}
    return {"version": fl.MODEL_VERSION, "kind": "logit", "logit": logit, "calibration": {"a": 0.05, "b": 0.9},
            "baselines": {"global": 0.21, "sector": {"civilian except GSA": 0.2, fl.DOD: 0.22}, "agency": {AGENCY: 0.3}},
            "history": history}


class TestModel(unittest.TestCase):
    def setUp(self):
        self.h = fl.history_from_awards(fl.prepare_awards(history_rows()))
        self.s = sols(sol_norm=["AAA11120Q0001", "ZZZ99920Q0001", "AAA11120R0003"], set_aside_code=["SBA", None, "8A"],
                      aac_code=["AAA111", "ZZZ999", "AAA111"], department=["TESTS, DEPARTMENT OF", None, None],
                      window_first_days=[3.0, 30.0, None], kw_sole=[1, 0, 0])
        self.f = fl.build_features(self.s, self.h)

    def test_predict_by_hand(self):
        m = toy_model(self.h)
        r = self.f.iloc[0]
        lp = -1.2 - 0.4 + 0.5 * (r.lo_office + 1.0) / 0.8 - 0.2 * (r.log_window - 2.5) / 0.6 + 1.1 + 0.2
        self.assertAlmostEqual(fl.predict_raw(m, self.f)[0], 1 / (1 + np.exp(-lp)), places=12)
        raw = fl.predict_raw(m, self.f)
        self.assertTrue(np.allclose(fl.predict(m, self.f), 1 / (1 + np.exp(-(0.05 + 0.9 * np.log(raw / (1 - raw)))))))

    def test_save_load_predict_round_trip(self):
        m = toy_model(self.h)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "sub", "model_v2.json")
            fl.save_model(m, path)
            back = fl.load_model(path)
            p0 = fl.predict(m, self.f)
            p1 = fl.predict(back, fl.build_features(self.s, back["history"]))      # features rebuilt from the saved history
            self.assertTrue(np.array_equal(p0, p1))
            self.assertTrue(((p1 >= 0) & (p1 <= 1)).all())
            pd.testing.assert_frame_equal(fl.baseline_predictions(m, self.f), fl.baseline_predictions(back, self.f))
            with open(path, "w", encoding="utf-8") as fh:
                fh.write('{"version": "v1", "history": {}}')
            self.assertRaises(ValueError, fl.load_model, path)

    def test_probabilities_stay_in_range(self):
        m = toy_model(self.h)
        f = pd.concat([self.f] * 2, ignore_index=True)
        f.loc[0, "lo_office"], f.loc[1, "lo_office"], f.loc[2, "log_window"] = 500.0, -500.0, 1e6
        for model in (m, {**m, "calibration": None}):
            p = fl.predict(model, f)
            self.assertTrue(np.isfinite(p).all() and (p >= 0).all() and (p <= 1).all())
        self.assertTrue(((fl.apply_calibration([0.0, 1.0, 0.5], {"a": 0.0, "b": 1.0}) >= 0)).all())

    def test_design_matrix_matches_the_scoring_path(self):
        rng = np.random.default_rng(1)
        f = pd.concat([self.f] * 40, ignore_index=True)
        f["lo_office"] = rng.normal(size=len(f))
        spec = fl.fit_spec(f, cat=["setaside", "sector", "month"], cont=["lo_office", "log_window"], binary=["kw_sole", "short7"], min_level=30)
        self.assertEqual(spec["cat"]["setaside"], ["8(a)", "none", "small business"])
        X = fl.design_matrix(f, spec)
        self.assertEqual(X.shape[1], len(fl.design_names(spec)))
        coef = rng.normal(size=X.shape[1])
        logit = fl.logit_to_dict(spec, 0.3, coef)
        self.assertTrue(np.allclose(0.3 + X @ coef, fl.linear_predictor(logit, f)))
        rare = fl.fit_spec(f, cat=["setaside"], cont=[], binary=[], min_level=10_000)
        self.assertEqual(rare["cat"]["setaside"], [])                       # too few rows: no coefficient, the level joins the baseline

    def test_baselines(self):
        m = toy_model(self.h)
        b = fl.baseline_predictions(m, self.f)
        self.assertEqual(b.p_sector_rate.tolist(), [0.2, 0.21, 0.2])        # unknown sector -> the overall rate
        self.assertEqual(b.p_agency_rate.tolist(), [0.3, 0.21, 0.3])        # unknown agency -> its sector's rate -> the overall rate
        self.assertTrue(np.allclose(b.p_office_history, self.f.p_office_history))
        rates = fl.baseline_rates(pd.DataFrame({"sector": ["a"] * 60 + ["b"] * 40, "agency": ["x"] * 60 + ["y"] * 40}), [1] * 30 + [0] * 70)
        self.assertAlmostEqual(rates["global"], 0.3)
        self.assertEqual(rates["sector"], {"a": 0.5, "b": 0.0})
        self.assertEqual(rates["agency"], {"x": 0.5})                       # agency y has fewer than 50 rows

    def test_platt(self):
        rng = np.random.default_rng(2)
        x = rng.normal(-1.2, 1.0, 40_000)
        y = (rng.random(len(x)) < 1 / (1 + np.exp(-(0.3 + 0.7 * x)))).astype(float)
        a, b = fl.fit_platt(1 / (1 + np.exp(-x)), y)
        self.assertAlmostEqual(a, 0.3, delta=0.05)
        self.assertAlmostEqual(b, 0.7, delta=0.05)
        self.assertEqual(fl.fit_platt([0.2, 0.3], [1, 1]), (0.0, 1.0))      # one class only: no correction

    def test_exported_trees(self):
        tree = {"feature": [0, 0, 0], "threshold": [0.5, 0.0, 0.0], "left": [1, 0, 0], "right": [2, 0, 0], "value": [0.0, -1.0, 2.0],
                "leaf": [0, 1, 1], "missing_left": [1, 0, 0]}
        gbm = {"features": ["lo_office"], "baseline": 0.25, "trees": [tree, tree]}
        X = np.array([[0.1], [0.9], [np.nan]])
        self.assertTrue(np.allclose(fl.gbm_raw(gbm, X), [0.25 - 2, 0.25 + 4, 0.25 - 2]))
        m = {**toy_model(self.h), "kind": "gbm", "gbm": gbm, "calibration": None}
        p = fl.predict(m, self.f)
        self.assertTrue(((p > 0) & (p < 1)).all())


class TestTrainingHelpers(unittest.TestCase):
    """the exported trees must reproduce scikit-learn, and the capped sample must be repeatable."""

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("model_v2_30", os.path.join(REPO, "scripts", "30_model_v2.py"))
        cls.m30 = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.m30)

    def test_export_matches_sklearn(self):
        from sklearn.ensemble import HistGradientBoostingClassifier
        rng = np.random.default_rng(3)
        X = rng.normal(size=(3000, 4))
        X[rng.random(X.shape) < 0.05] = np.nan
        y = (rng.random(3000) < 1 / (1 + np.exp(-(np.nan_to_num(X[:, 0]) - 0.5 * np.nan_to_num(X[:, 1]) ** 2)))).astype(int)
        g = HistGradientBoostingClassifier(max_iter=25, max_leaf_nodes=8, random_state=0).fit(X, y)
        gbm = self.m30.export_gbm(g)
        self.assertTrue(np.allclose(fl._expit(fl.gbm_raw(gbm, X)), g.predict_proba(X)[:, 1], atol=1e-9))

    def test_logit_fit_recovers_a_planted_effect(self):
        rng = np.random.default_rng(4)
        n = 6000
        f = pd.DataFrame({"setaside": rng.choice(["none", "small business"], n), "lo_office": rng.normal(size=n), "kw_sole": (rng.random(n) < 0.1).astype(float)})
        lp = -1.0 + 0.8 * (f.setaside == "none") + 0.6 * f.lo_office + 1.5 * f.kw_sole
        y = (rng.random(n) < 1 / (1 + np.exp(-lp))).astype(float)
        logit = self.m30.fit_logit(f, y, cat=["setaside"], cont=["lo_office"], binary=["kw_sole"], C=10.0)
        self.assertAlmostEqual(logit["cat"]["setaside"]["none"] - logit["cat"]["setaside"]["small business"], 0.8, delta=0.2)
        self.assertAlmostEqual(logit["cont"]["lo_office"][0] / logit["cont"]["lo_office"][2], 0.6, delta=0.1)
        self.assertAlmostEqual(logit["bin"]["kw_sole"], 1.5, delta=0.3)
        p = fl._expit(fl.linear_predictor(logit, f))
        self.assertAlmostEqual(p.mean(), y.mean(), delta=0.01)
        # the chunked Newton fit (used for the final model on all rows) solves the same problem
        newton = self.m30.fit_logit_all(f, y, cat=["setaside"], cont=["lo_office"], binary=["kw_sole"], C=10.0, block=1000)
        self.assertTrue(np.allclose(fl.linear_predictor(newton, f), fl.linear_predictor(logit, f), atol=2e-3))
        f["setaside"] = f["setaside"].astype("category")                    # category columns give the same fit
        again = self.m30.fit_logit_all(f, y, cat=["setaside"], cont=["lo_office"], binary=["kw_sole"], C=10.0)
        self.assertTrue(np.allclose(fl.linear_predictor(again, f), fl.linear_predictor(newton, f), atol=1e-8))

    def test_capped_sample(self):
        rows = np.arange(self.m30.MAX_FIT_ROWS + 500)
        a, b = self.m30.capped(rows, 7), self.m30.capped(rows, 7)
        self.assertTrue(np.array_equal(a, b))
        self.assertEqual(len(a), self.m30.MAX_FIT_ROWS)
        self.assertTrue(np.array_equal(self.m30.capped(rows[:10], 7), rows[:10]))


if __name__ == "__main__":
    unittest.main()
