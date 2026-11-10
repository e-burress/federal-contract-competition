"""Offline tests for scripts/26_split_screen.py on synthetic data: linking, the split-like rule, thresholds by date, privacy."""
import glob
import importlib.util
import os
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd

SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
sys.path.insert(0, SCRIPTS)
_spec = importlib.util.spec_from_file_location("split_screen", os.path.join(SCRIPTS, "26_split_screen.py"))
ss = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ss)


def awards(rows):
    """rows: (vendor, office, psc, date, value) -> the slim frame load_year returns"""
    df = pd.DataFrame(rows, columns=["vendor", "office", "psc", "date", "value"])
    df["agency"] = "Department of the Interior"
    df["is_dod"] = False
    df["date"] = pd.to_datetime(df["date"]).to_numpy().astype("datetime64[D]")
    return df.astype({"vendor": "str", "office": "str", "psc": "str", "agency": "str"})


def split_flags(df, cl, line):
    n = cl["n"].to_numpy()
    starts = cl["start"].to_numpy()
    return ss.screen_line(df["value"].to_numpy(), starts, n, cl["vmax"].to_numpy(), cl["vsum"].to_numpy(), np.full(len(n), line))


class LinkTests(unittest.TestCase):
    def test_planted_split_is_found(self):
        df = awards([("v1", "o1", "7030", "2015-03-02", 60_000), ("v1", "o1", "7030", "2015-03-12", 60_000),
                     ("v1", "o1", "7030", "2015-03-25", 60_000),
                     ("v2", "o1", "7030", "2015-03-02", 40_000)])          # another company, alone
        df, cl = ss.link_clusters(df)
        self.assertEqual(len(cl), 2)
        a_under, _, split = split_flags(df, cl, 150_000)
        big = int(np.argmax(cl["n"].to_numpy()))
        self.assertEqual(cl["n"].iloc[big], 3)
        self.assertTrue(split[big])
        self.assertFalse(split[1 - big])                                   # a single award is never split-like
        self.assertEqual(int(a_under.sum()), 4)

    def test_30_days_linked_31_days_not(self):
        df = awards([("v1", "o1", "7030", "2015-01-01", 90_000), ("v1", "o1", "7030", "2015-01-31", 90_000)])
        self.assertEqual(len(ss.link_clusters(df)[1]), 1)                  # 30 days apart: one cluster
        df = awards([("v1", "o1", "7030", "2015-01-01", 90_000), ("v1", "o1", "7030", "2015-02-01", 90_000)])
        df, cl = ss.link_clusters(df)
        self.assertEqual(len(cl), 2)                                       # 31 days apart: two clusters
        self.assertFalse(split_flags(df, cl, 150_000)[2].any())

    def test_chain_links_through_middle_award(self):
        df = awards([("v1", "o1", "7030", "2015-01-01", 1), ("v1", "o1", "7030", "2015-01-25", 1), ("v1", "o1", "7030", "2015-02-20", 1)])
        self.assertEqual(len(ss.link_clusters(df)[1]), 1)

    def test_other_office_or_product_not_linked(self):
        df = awards([("v1", "o1", "7030", "2015-01-01", 90_000), ("v1", "o2", "7030", "2015-01-02", 90_000),
                     ("v1", "o1", "7035", "2015-01-03", 90_000)])
        self.assertEqual(len(ss.link_clusters(df)[1]), 3)

    def test_award_above_line_breaks_the_rule(self):
        df = awards([("v1", "o1", "7030", "2015-03-02", 160_000), ("v1", "o1", "7030", "2015-03-05", 50_000)])
        df, cl = ss.link_clusters(df)
        self.assertEqual(len(cl), 1)
        a_under, _, split = split_flags(df, cl, 150_000)
        self.assertFalse(split[0])
        self.assertEqual(int(a_under[0]), 1)

    def test_total_must_be_above_line_and_donut(self):
        df = awards([("v1", "o1", "7030", "2015-03-02", 75_000), ("v1", "o1", "7030", "2015-03-05", 75_000)])
        df, cl = ss.link_clusters(df)
        self.assertFalse(split_flags(df, cl, 150_000)[2][0])               # total exactly at the line is not above it
        df = awards([("v1", "o1", "7030", "2015-03-02", 140_000), ("v1", "o1", "7030", "2015-03-05", 20_000)])
        df, cl = ss.link_clusters(df)
        n, st = cl["n"].to_numpy(), cl["start"].to_numpy()
        args = (df["value"].to_numpy(), st, n, cl["vmax"].to_numpy(), cl["vsum"].to_numpy(), np.array([150_000.0]))
        self.assertTrue(ss.screen_line(*args)[2][0])
        self.assertFalse(ss.screen_line(*args, cap=ss.DONUT)[2][0])         # 140,000 is within 10% of the line

    def test_band_upper_limit(self):
        df = awards([("v1", "o1", "7030", "2015-03-02", 140_000), ("v1", "o1", "7030", "2015-03-05", 140_000)])
        df, cl = ss.link_clusters(df)
        n, st = cl["n"].to_numpy(), cl["start"].to_numpy()
        args = (df["value"].to_numpy(), st, n, cl["vmax"].to_numpy(), cl["vsum"].to_numpy(), np.array([150_000.0]))
        self.assertFalse(ss.screen_line(*args, upper=250_000)[2][0])       # total 280,000 is above the band
        self.assertTrue(ss.screen_line(*args, upper=300_000)[2][0])


class ThresholdTests(unittest.TestCase):
    def test_thresholds_by_date(self):
        adopt = ss.load_adoption()
        ag = ["Department of Veterans Affairs", "Department of Justice", "Department of Justice", "Department of Defense",
              "Department of Defense", "Department of Defense", "Department of Justice", "Department of Justice",
              "Department of Defense", "Department of Defense", "General Services Administration", "General Services Administration"]
        dates = np.array(["2018-04-01", "2018-04-01", "2020-09-01", "2018-05-01", "2018-09-01", "2025-11-01", "2010-09-30",
                          "2025-10-01", "2017-08-01", "2018-04-12", "2020-08-30", "2020-08-31"], dtype="datetime64[D]")
        dod = np.array([a == "Department of Defense" for a in ag])
        sat = ss.thresholds_at("SAT", ag, dates, dod, adopt)
        mpt = ss.thresholds_at("MPT", ag, dates, dod, adopt)
        np.testing.assert_array_equal(sat, [250_000, 150_000, 250_000, 250_000, 250_000, 350_000, 100_000, 350_000,
                                            150_000, 150_000, 150_000, 250_000])
        # reference/thresholds.csv: DoD at $5,000 from 2017-07-13, $10,000 from 2018-08-31, $15,000 from 2025-10-01 (as civilian)
        np.testing.assert_array_equal(mpt, [10_000, 3_500, 10_000, 5_000, 10_000, 15_000, 3_000, 15_000,
                                            5_000, 5_000, 3_500, 10_000])

    def test_thresholds_come_from_the_reference_files(self):
        self.assertIn("reference/thresholds.csv", ss.THRESHOLDS_SOURCE)
        rows, far = ss.read_thresholds()
        self.assertEqual(rows, ss.THRESHOLDS)
        self.assertEqual(far, "2020-08-31")
        self.assertEqual(rows, ss.FALLBACK_THRESHOLDS)              # the fallback is kept in step with the files
        self.assertIn(("SAT", 250_000, "adoption", "civilian"), rows)
        self.assertIn(("MPT", 15_000, "2025-10-01", "dod"), rows)

    def test_reference_values_change_the_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            ref = os.path.join(os.path.dirname(SCRIPTS), "reference")
            t = pd.read_csv(os.path.join(ref, "thresholds.csv"), dtype=str)
            t.loc[(t.threshold == "simplified_acquisition") & (pd.to_numeric(t.value_usd) == 350_000), "effective_date"] = "2025-11-15"
            p = os.path.join(tmp, "thresholds.csv")
            t.to_csv(p, index=False)
            rows, far = ss.read_thresholds(p)
            T = ss.thresholds_at("SAT", ["Department of Justice"] * 2, np.array(["2025-11-01", "2025-11-15"], dtype="datetime64[D]"),
                                 [False, False], ss.load_adoption(), table=rows, far_date=far)
            np.testing.assert_array_equal(T, [250_000, 350_000])

    def test_fallback_when_files_are_missing(self):
        rows, far, source = ss.load_thresholds(path=os.path.join(SCRIPTS, "no_such_file.csv"))
        self.assertEqual(rows, ss.FALLBACK_THRESHOLDS)
        self.assertEqual(far, ss.FALLBACK_FAR_DATE)
        self.assertIn("fallback", source)

    def test_placebo_fit_recovers_smooth_curve(self):
        m = np.array(ss.MULTIPLES)
        shares = 0.1 * m ** 0.3                                           # smooth power law: excess at m = 1 should be 1
        used = ss.placebo_used(150_000)
        self.assertAlmostEqual(ss.baseline_share(shares, used), 0.1, places=10)
        self.assertFalse(used[ss.MULTIPLES.index(1.0)])
        self.assertFalse(used[ss.MULTIPLES.index(1.6)])                    # 240,000 is near the 250,000 line


class EndToEndTests(unittest.TestCase):
    def test_run_finds_split_crosses_years_and_writes_nothing_identifying(self):
        r = np.random.default_rng(1)
        vendors = [f"SECRETVENDOR{i:04d}" for i in range(60)]
        offices = [f"SECRETOFFICE{i:02d}" for i in range(6)]
        rows = []
        for fy in (2015, 2016):
            start = np.datetime64(f"{fy - 1}-10-01")
            for i in range(3000):
                rows.append(dict(fy=fy, awarding_agency_name="Department of the Interior", awarding_office_code=offices[r.integers(6)],
                                 award_type_code=str(r.choice(["B", "D"])), action_date=start + np.timedelta64(int(r.integers(0, 365)), "D"),
                                 product_or_service_code=str(r.choice(["7030", "7035", "R499", "J065"])),
                                 base_and_all_options_value=float(np.exp(r.normal(9, 1.5))), vendor_id=vendors[r.integers(60)],
                                 is_dod=False))
        # planted split: three $60,000 purchases, the first two in FY2015 and the last in FY2016 (linked across the year boundary)
        for d in ("2015-09-10", "2015-09-25", "2015-10-05"):
            rows.append(dict(fy=0, awarding_agency_name="Department of the Interior", awarding_office_code="SECRETOFFICEX",
                             award_type_code="B", action_date=np.datetime64(d), product_or_service_code="6515",
                             base_and_all_options_value=60_000.0, vendor_id="SECRETVENDORX", is_dod=False))
        df = pd.DataFrame(rows)
        df["action_date"] = pd.to_datetime(df["action_date"])
        df["fy"] = df["action_date"].dt.year + (df["action_date"].dt.month >= 10)
        with tempfile.TemporaryDirectory() as tmp:
            proc = os.path.join(tmp, "data", "history", "processed")
            os.makedirs(proc)
            for fy, g in df.groupby("fy"):
                g.drop(columns="fy").to_parquet(os.path.join(proc, f"base_actions_FY{fy}.parquet"), index=False)
            out_dir = os.path.join(tmp, "out")
            out, summary = ss.run(data_dir=os.path.join(tmp, "data"), results_dir=out_dir, fig_dir=out_dir, reps=30, verbose=False)
            self.assertEqual(summary["years_used"], [2015, 2016])
            # the planted cluster is closed in FY2016 with all three awards
            row = out[(out.fy == 2016) & (out.basis == "in_force") & (out.threshold_type == "SAT") & (out.multiple == 1.0)
                      & (out.variant == "all")].iloc[0]
            self.assertGreaterEqual(int(row.awards_split_like), 3)
            self.assertEqual(int(out[(out.fy == 2015)].awards.iloc[0]) + int(out[(out.fy == 2016)].awards.iloc[0]), len(df))
            files = glob.glob(os.path.join(out_dir, "*"))
            self.assertEqual(sorted(os.path.basename(f) for f in files),
                             ["split_screen.csv", "split_screen.png", "split_screen_summary.json"])
            for f in files:
                with open(f, "rb") as fh:
                    blob = fh.read()
                for secret in (b"SECRETVENDOR", b"SECRETOFFICE"):
                    self.assertNotIn(secret, blob, f"{secret!r} found in {os.path.basename(f)}")
            # no award-level column and no id column in the table
            self.assertFalse({"vendor", "vendor_id", "office", "awarding_office_code", "psc"} & set(out.columns))


if __name__ == "__main__":
    unittest.main()
