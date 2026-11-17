"""Offline tests for scripts/14_sam_daily_snapshot.py (SAM.gov's Get Opportunities API).  No request leaves the computer: a fake
API answers in-process, and a made-up key is used.  Checked: the field whitelist, the department / sub-tier / office split, the
paging (page index or record offset), the request budget, the day log, the outputs, the second pull of a day pulled right after
it ended, and that the key is never printed or saved."""
import contextlib
import glob
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from unittest import mock

import pandas as pd

SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
sys.path.insert(0, SCRIPTS)
_spec = importlib.util.spec_from_file_location("sam_api_14", os.path.join(SCRIPTS, "14_sam_daily_snapshot.py"))
sam = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sam)

KEY = "TESTKEY0123456789abcdefSECRET"
NOW = pd.Timestamp("2026-11-12 12:00", tz="UTC")           # SAM.gov's yesterday: 2026-11-11
YESTERDAY = date(2026, 11, 11)
MIDNIGHT = pd.Timestamp("2026-11-15 05:00:12", tz="UTC")   # 00:00:12 Eastern (standard time) on Nov 15, as robot run 43679152661
NOV14, NOV15, NOV16 = date(2026, 11, 14), date(2026, 11, 15), date(2026, 11, 16)


def record(i, day, typ="Solicitation", dept="VETERANS AFFAIRS, DEPARTMENT OF", path=None, codes=None, sol=None):
    return {
        "noticeId": f"N{day:%Y%m%d}{i:05d}", "title": f"Brand name or equal pump {i} call 555-123-4567 or a.b@agency.gov",
        "solicitationNumber": f" {sol or f'36C{day:%m%d}26Q{i:04d}'} ",
        "fullParentPathName": path or f"{dept}.VETERANS AFFAIRS, DEPARTMENT OF.{i % 3}36-NETWORK CONTRACT OFFICE 6",
        "fullParentPathCode": codes or "036.3600.36C246",
        "postedDate": f"{day:%Y-%m-%d}", "type": typ, "baseType": typ, "archiveType": "autocustom",
        "archiveDate": "2027-01-15", "typeOfSetAside": "SDVOSBC", "typeOfSetAsideDescription": "SDVOSB Set-Aside",
        "responseDeadLine": f"{day + timedelta(days=20):%Y-%m-%d}T15:00:00-05:00", "naicsCode": "333914", "classificationCode": "4320",
        "active": "Yes", "award": {"date": None, "number": None, "amount": None,
                                   "awardee": {"name": "SECRET AWARDEE LLC", "location": {"streetAddress": "1 SECRET ST"}}},
        "pointOfContact": [{"fullName": "Secret Person", "email": "secret.person@va.gov", "phone": "2025550100"}],
        "description": "https://api.sam.gov/prod/opportunities/v1/noticedesc?noticeid=x",
        "organizationType": "OFFICE", "officeAddress": {"zipcode": "23667", "city": "HAMPTON", "countryCode": "USA", "state": "VA"},
        "placeOfPerformance": {"streetAddress": "100 SECRET ROAD", "city": {"code": "1", "name": "Hampton"}, "state": {"code": "VA"},
                               "zip": "23667", "country": {"code": "USA"}},
        "additionalInfoLink": None, "uiLink": "https://sam.gov/opp/x/view", "links": [], "resourceLinks": None}


class FakeApi:
    """notices per day; offset is a page index (mode "page") or a record offset (mode "record")"""

    def __init__(self, per_day, mode="page", refuse=False, rate_limit_after=None, error=False):
        self.per_day, self.mode, self.refuse, self.error = per_day, mode, refuse, error
        self.rate_limit_after, self.calls = rate_limit_after, []

    def __call__(self, key, day, offset):
        self.calls.append((day, offset))
        if key != KEY:
            raise AssertionError("wrong key passed")
        if self.refuse:
            raise sam.KeyRefused("HTTP 403: An invalid api_key was supplied")
        if self.rate_limit_after is not None and len(self.calls) > self.rate_limit_after:
            raise sam.RateLimited("HTTP 429: OVER_RATE_LIMIT")
        if self.error:
            raise sam.ApiError("HTTP 500: Internal Server Error")
        n = self.per_day.get(day, 0)
        recs = [record(i, day, typ="Award Notice" if i % 10 == 9 else "Solicitation",
                       dept="DEPT OF DEFENSE" if i % 4 == 0 else "VETERANS AFFAIRS, DEPARTMENT OF") for i in range(n)]
        start = offset * sam.PAGE if self.mode == "page" else offset
        return {"totalRecords": n, "limit": sam.PAGE, "offset": offset, "opportunitiesData": recs[start:start + sam.PAGE]}


class Paths(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fcc_test_sam_api_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        p = lambda *a: os.path.join(self.tmp, *a)                      # noqa: E731
        patches = {"STORE": p("data", "sam_api", "notices.parquet"), "SNAP_DIR": p("data", "sam_snapshots"),
                   "PULLS_CSV": p("results", "sam_api_pulls.csv"), "NEW_CSV": p("results", "sam_new_notices.csv"),
                   "LATEST_JSON": p("results", "sam_daily_latest.json")}
        for k, v in patches.items():
            m = mock.patch.object(sam, k, v)
            m.start()
            self.addCleanup(m.stop)
        env = mock.patch.dict(os.environ, {sam.KEY_ENV: KEY})
        env.start()
        self.addCleanup(env.stop)

    def run_script(self, fake, *args, now=NOW):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = sam.run(list(args), now=now, get=fake)
        return code, out.getvalue()

    def pulls(self):
        return pd.read_csv(sam.PULLS_CSV, dtype=str, keep_default_na=False)


class FieldTests(unittest.TestCase):
    def test_only_whitelisted_fields_are_kept_and_titles_are_masked(self):
        df = sam.to_frame([record(1, date(2026, 11, 5))], date(2026, 11, 5), "2026-11-06T12:00:00Z")
        self.assertEqual(list(df.columns), sam.COLUMNS)
        blob = df.astype(str).to_csv()
        for secret in ("SECRET", "Secret Person", "secret.person", "2025550100", "noticedesc", "sam.gov/opp", "555-123-4567",
                       "a.b@agency.gov"):
            self.assertNotIn(secret, blob)
        r = df.iloc[0]
        self.assertEqual(r["title"], "Brand name or equal pump 1 call [phone] or [email]")
        self.assertEqual(r["sol_number"], "36C110526Q0001")                    # spaces around the number removed
        self.assertEqual((r["cgac"], r["fpds_code"], r["aac_code"]), ("036", "3600", "36C246"))
        self.assertEqual(r["department"], "VETERANS AFFAIRS, DEPARTMENT OF")
        self.assertEqual(r["sub_tier"], "VETERANS AFFAIRS, DEPARTMENT OF")
        self.assertEqual(r["office"], "136-NETWORK CONTRACT OFFICE 6")
        self.assertEqual(r["response_deadline"], pd.Timestamp("2026-11-25 20:00", tz="UTC"))
        self.assertEqual(r["posted_date"], pd.Timestamp("2026-11-05"))
        self.assertEqual((r["pop_state"], r["office_zip"], r["naics"], r["psc"]), ("VA", "23667", "333914", "4320"))
        self.assertEqual(r["kw_brand"], 1)                                      # "brand name or equal" in the title
        self.assertTrue(pd.isna(r["award_amount"]))

    def test_names_with_periods_go_to_the_sub_tier(self):
        rec = {"fullParentPathName": "DEPT OF DEFENSE.U.S. SPECIAL OPERATIONS COMMAND (USSOCOM).SOF AT&L-KS",
               "fullParentPathCode": "097.97ZS.H92240"}
        h = sam.hierarchy(rec)
        self.assertEqual((h["department"], h["sub_tier"], h["office"]),
                         ("DEPT OF DEFENSE", "U.S. SPECIAL OPERATIONS COMMAND (USSOCOM)", "SOF AT&L-KS"))
        self.assertEqual((h["cgac"], h["fpds_code"], h["aac_code"]), ("097", "97ZS", "H92240"))
        h = sam.hierarchy({"fullParentPathName": "DEPT OF DEFENSE.DEPT OF THE ARMY.AMC.ACC.W6QK ACC-RI",
                           "fullParentPathCode": "097.2100.AMC.ACC.W6QK"})
        self.assertEqual((h["sub_tier"], h["office"], h["aac_code"]), ("DEPT OF THE ARMY", "W6QK ACC-RI", "W6QK"))
        h = sam.hierarchy({"fullParentPathName": "GENERAL SERVICES ADMINISTRATION.FEDERAL ACQUISITION SERVICE",
                           "fullParentPathCode": "047.4732"})
        self.assertEqual((h["sub_tier"], h["office"], h["aac_code"]), ("FEDERAL ACQUISITION SERVICE", None, None))

    def test_null_strings_and_typos_count_as_missing(self):
        rec = record(2, date(2026, 11, 5))
        rec.update({"naicsCode": "null", "responseDeadLine": None, "reponseDeadLine": "0202-11-20T15:00:00-05:00",
                    "archiveDate": "null", "naicsCodes": ["541511"]})
        r = sam.to_frame([rec], date(2026, 11, 5), "x").iloc[0]
        self.assertEqual(r["naics"], "541511")
        self.assertTrue(pd.isna(r["response_deadline"]))                       # year 202: a typo, dropped
        self.assertTrue(pd.isna(r["archive_date"]))

    def test_store_round_trip_keeps_types(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        df = sam.to_frame([record(i, date(2026, 11, 5)) for i in range(3)], date(2026, 11, 5), "2026-11-06T12:00:00Z")
        p = os.path.join(tmp, "s.parquet")
        sam.atomic_parquet(df, p)
        back = sam.read_store(p)
        pd.testing.assert_frame_equal(back, df)


class PagingTests(unittest.TestCase):
    def test_page_index_offsets(self):
        fake, b = FakeApi({YESTERDAY: 1500}, mode="page"), sam.Budget(6)
        recs, total, mode, reason = sam.pull_day(KEY, YESTERDAY, b, None, fake)
        self.assertEqual((len(recs), total, mode, reason, b.used), (1500, 1500, "page", None, 2))
        self.assertEqual([c[1] for c in fake.calls], [0, 1])

    def test_record_offsets_are_detected(self):
        fake, b = FakeApi({YESTERDAY: 1500}, mode="record"), sam.Budget(6)
        recs, total, mode, reason = sam.pull_day(KEY, YESTERDAY, b, None, fake)
        self.assertEqual((len(recs), mode, reason, b.used), (1500, "record", None, 3))
        self.assertEqual([c[1] for c in fake.calls], [0, 1, 1000])
        fake2, b2 = FakeApi({YESTERDAY: 2500}, mode="record"), sam.Budget(6)
        recs, _, mode, _ = sam.pull_day(KEY, YESTERDAY, b2, "record", fake2)  # the mode is known: no test request
        self.assertEqual((len(recs), b2.used), (2500, 3))
        self.assertEqual([c[1] for c in fake2.calls], [0, 1000, 2000])

    def test_budget_and_too_big(self):
        b = sam.Budget(2)
        b.take()                                                             # one request already spent on another day
        recs, total, _, reason = sam.pull_day(KEY, YESTERDAY, b, "page", FakeApi({YESTERDAY: 1500}))
        self.assertEqual((len(recs), total, reason), (1000, 1500, "budget"))
        b = sam.Budget(1)                                                    # 2 pages can never fit a cap of 1
        recs, total, _, reason = sam.pull_day(KEY, YESTERDAY, b, "page", FakeApi({YESTERDAY: 1500}))
        self.assertEqual((len(recs), reason), (1000, "too-big"))
        b = sam.Budget(2)
        recs, total, _, reason = sam.pull_day(KEY, YESTERDAY, b, "page", FakeApi({YESTERDAY: 2500}))
        self.assertEqual((len(recs), reason, b.used), (1000, "too-big", 1))

    def test_empty_day(self):
        recs, total, _, reason = sam.pull_day(KEY, YESTERDAY, sam.Budget(6), None, FakeApi({}))
        self.assertEqual((recs, total, reason), ({}, 0, None))


class RunTests(Paths):
    def test_no_key(self):
        with mock.patch.dict(os.environ, {sam.KEY_ENV: ""}):
            code, out = self.run_script(FakeApi({YESTERDAY: 5}))
            self.assertEqual(code, 0)
            self.assertIn("Step skipped", out)
            code, out = self.run_script(FakeApi({YESTERDAY: 5}), "--require-key")
            self.assertEqual(code, sam.EXIT_KEY)
        self.assertFalse(os.path.exists(sam.PULLS_CSV))

    def test_first_run_pulls_yesterday_and_writes_everything(self):
        fake = FakeApi({YESTERDAY: 40, YESTERDAY - timedelta(days=1): 7})
        code, out = self.run_script(fake)
        self.assertEqual(code, 0)
        self.assertEqual([c[0] for c in fake.calls], [YESTERDAY])               # empty log: yesterday only
        p = self.pulls()
        self.assertEqual(p[["posted_day", "status", "requests", "total_records", "notices_received"]].values.tolist(),
                         [["2026-11-11", "complete", "1", "40", "40"]])
        new = pd.read_csv(sam.NEW_CSV, dtype=str)
        self.assertEqual(set(new["posted_day"]), {"2026-11-11"})
        self.assertEqual(new["n_notices"].astype(int).sum(), 40)
        with open(sam.LATEST_JSON, encoding="utf-8") as fh:
            latest = json.load(fh)
        self.assertEqual((latest["schema"], latest["through"], latest["window"]["days_pulled"]), (2, "2026-11-11", 1))
        sp = latest["solicitation_notices_posted"]
        self.assertEqual(sp["all"]["notices"], 36)                             # 4 of 40 are award notices
        self.assertEqual(sp["dod"]["notices"] + sp["civilian"]["notices"], 36)
        self.assertEqual(latest["requests"], {"this_run": 1, "per_run_cap": 6, "daily_limit": 10})
        views = glob.glob(os.path.join(sam.SNAP_DIR, "sam_view_*.parquet"))
        self.assertEqual([os.path.basename(v) for v in views], ["sam_view_2026-11-12.parquet"])
        v = pd.read_parquet(views[0])
        f31 = ["notice_id", "sol_number", "type", "base_type", "posted_date", "response_deadline", "department", "sub_tier",
               "aac_code", "set_aside_code", "naics", "psc", "kw_brand", "kw_sole"]
        self.assertTrue(set(f31) <= set(v.columns))                           # what 31_daily_forecast.py reads
        self.assertEqual(v["store_first_day"].iloc[0], pd.Timestamp("2026-11-11"))
        self.assertEqual(len(v), 40)

    def test_the_key_is_never_printed_or_saved(self):
        code, out = self.run_script(FakeApi({YESTERDAY: 30}))
        self.assertNotIn(KEY, out)
        for f in glob.glob(os.path.join(self.tmp, "**", "*"), recursive=True):
            if os.path.isfile(f):
                with open(f, "rb") as fh:
                    self.assertNotIn(KEY.encode(), fh.read(), f)
        self.assertNotIn(KEY, sam.redact(f"https://api.sam.gov/x?api_key={KEY}&limit=1", KEY))

    def test_catch_up_oldest_first_within_the_budget(self):
        days = {YESTERDAY - timedelta(days=i): 1200 for i in range(5)}        # 2 requests a day
        self.run_script(FakeApi({YESTERDAY - timedelta(days=4): 3}), now=NOW - pd.Timedelta(days=4))   # log starts Nov 7
        fake = FakeApi(days)
        code, out = self.run_script(fake, "--max-requests", "5")              # Nov 8 and 9 take 2 each; Nov 10 gets 1 of 2
        self.assertEqual(code, 0)
        p = self.pulls()
        self.assertEqual(p["posted_day"].tolist(), ["2026-11-07", "2026-11-08", "2026-11-09", "2026-11-10"])
        self.assertEqual(p["status"].tolist(), ["complete", "complete", "complete", "incomplete"])
        self.assertEqual(p["notices_received"].tolist()[-1], "1000")
        self.assertIn("budget ran out", out)
        fake = FakeApi(days)
        self.run_script(fake, now=NOW + pd.Timedelta(hours=1))
        p = self.pulls()
        self.assertEqual(p["posted_day"].tolist()[-2:], ["2026-11-10", "2026-11-11"])   # the incomplete day first, in full
        self.assertEqual(p["status"].tolist()[-2:], ["complete", "complete"])
        self.assertEqual(p["attempt"].tolist()[-2:], ["2", "1"])
        store = pd.read_parquet(sam.STORE)
        self.assertEqual(len(store), 3 + 4 * 1200)                             # Nov 10's full pull replaced its partial one
        self.assertEqual(store["notice_id"].nunique(), len(store))
        code, out = self.run_script(FakeApi(days), now=NOW + pd.Timedelta(hours=2))
        self.assertIn("Nothing to pull", out)

    def test_refused_key_rate_limit_and_errors(self):
        code, out = self.run_script(FakeApi({YESTERDAY: 5}, refuse=True))
        self.assertEqual(code, sam.EXIT_KEY)
        self.assertIn("renew it every 90 days", out)
        self.assertNotIn(KEY, out)
        self.assertEqual(self.pulls()["status"].tolist(), ["incomplete"])
        code, out = self.run_script(FakeApi({YESTERDAY: 5}, error=True))
        self.assertEqual(code, sam.EXIT_API)
        code, out = self.run_script(FakeApi({YESTERDAY: 5}, rate_limit_after=0))
        self.assertEqual(code, 0)
        self.assertIn("request limit is used up", out)
        code, out = self.run_script(FakeApi({YESTERDAY: 5}))
        self.assertEqual((code, self.pulls()["status"].tolist()[-1]), (0, "complete"))

    def test_short_day_after_two_tries(self):
        class Lying(FakeApi):                    # says 12 but sends 10, then an empty page
            def __call__(self, key, day, offset):
                d = super().__call__(key, day, offset)
                return dict(d, totalRecords=12, opportunitiesData=d["opportunitiesData"][:10] if offset == 0 else [])
        self.run_script(Lying({YESTERDAY: 12}))
        self.assertEqual(self.pulls()["status"].tolist(), ["incomplete"])
        self.run_script(Lying({YESTERDAY: 12}), now=NOW + pd.Timedelta(hours=1))
        p = self.pulls()
        self.assertEqual((p["status"].tolist(), p["notices_received"].tolist()[-1]), (["incomplete", "short"], "10"))

    def test_check_writes_nothing_and_prints_no_names(self):
        code, out = self.run_script(FakeApi({YESTERDAY: 3}), "--check")
        self.assertEqual(code, 0)
        self.assertIn("totalRecords 3", out)
        self.assertIn("pointOfContact", out)                                   # named as a field that is never kept
        for secret in ("VETERANS", "Secret Person", "pump", KEY):
            self.assertNotIn(secret, out)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "results")))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "data")))


class PullAgainRule(unittest.TestCase):
    """which days are pulled a second time: complete days whose latest pull started before 12:00 UTC the next day"""

    def test_a_day_ends_at_05_utc_and_settles_at_12_utc(self):
        self.assertEqual(sam.day_end_utc(NOV14), datetime(2026, 11, 15, 5, 0))
        self.assertEqual(sam.day_end_utc(NOV14) + sam.SETTLE, datetime(2026, 11, 15, 12, 0))

    def test_which_days_are_pulled_again(self):
        def log(*rows):
            return pd.DataFrame([[d, t, "1", "1", "5", "5", s, "page"] for d, t, s in rows], columns=sam.PULL_COLS)
        pulls = log(("2026-09-01", "2026-09-02T05:00:00Z", "complete"),    # early, but more than 60 days back
                    ("2026-11-10", "not a time", "complete"),               # an unreadable time is left alone
                    ("2026-11-12", "2026-11-13T11:59:59Z", "complete"),     # one second before 12:00 UTC: early
                    ("2026-11-13", "2026-11-14T12:00:00Z", "complete"),     # at 12:00 UTC: settled
                    ("2026-11-14", "2026-11-15T05:00:53Z", "short"),        # short days are not pulled again
                    ("2026-11-15", "2026-11-16T05:00:10Z", "complete"))     # early; settles at 12:00 UTC on Nov 16
        self.assertEqual(sam.days_to_pull_again(pulls, datetime(2026, 11, 16, 11, 59), NOV15), [date(2026, 11, 12)])
        self.assertEqual(sam.days_to_pull_again(pulls, datetime(2026, 11, 16, 12, 0), NOV15), [date(2026, 11, 12), NOV15])
        later = pd.concat([pulls, log(("2026-11-12", "2026-11-16T12:00:05Z", "complete"))], ignore_index=True)
        self.assertEqual(sam.days_to_pull_again(later, datetime(2026, 11, 16, 12, 0), NOV15), [NOV15])   # once only
        cut = pd.concat([pulls, log(("2026-11-15", "2026-11-16T12:00:05Z", "incomplete"))], ignore_index=True)
        self.assertEqual(sam.days_to_pull_again(cut, datetime(2026, 11, 16, 13, 0), NOV15), [date(2026, 11, 12)])
        self.assertEqual(sam.days_to_pull_again(pd.DataFrame(columns=sam.PULL_COLS), datetime(2026, 11, 16), NOV15), [])


class PullAgainRuns(Paths):
    """A run right after midnight pulls the day that just ended; the first run from 12:00 UTC on pulls it once more, so a
    notice that SAM.gov's API listed late still reaches the store (and the forecast step) with its own posting date."""

    def test_a_day_pulled_right_after_midnight_is_pulled_once_more_and_merged(self):
        fake = FakeApi({NOV14: 16})                                            # the 17th notice is not listed yet
        code, out = self.run_script(fake, now=MIDNIGHT)
        self.assertEqual((code, [c[0] for c in fake.calls]), (0, [NOV14]))
        self.assertRegex(self.pulls()["pulled_utc"].iloc[0], r"^2026-11-15T05:00:1\dZ$")   # the run's clock
        fake = FakeApi({NOV14: 17})
        code, out = self.run_script(fake, now=MIDNIGHT + pd.Timedelta(hours=4))          # 04:00 Eastern: too soon
        self.assertEqual((code, fake.calls), (0, []))
        self.assertIn("Nothing to pull", out)
        fake = FakeApi({NOV14: 17, NOV15: 5})
        code, out = self.run_script(fake, now=MIDNIGHT + pd.Timedelta(days=1))           # the next midnight
        self.assertEqual(code, 0)
        self.assertEqual([c[0] for c in fake.calls], [NOV14, NOV15])                     # in date order
        self.assertIn("2026-11-14 was pulled less than 7 hours after it ended: it is due to be pulled once more", out)
        self.assertIn("2026-11-14 (pulled again): 17 notices of 17 in 1 request(s): complete; 1 not in the store before", out)
        self.assertIn("2026-11-15: 5 notices of 5 in 1 request(s): complete\n", out)
        self.assertNotIn(KEY, out)
        p = self.pulls()
        self.assertEqual(p[["posted_day", "attempt", "requests", "status", "notices_received"]].values.tolist(),
                         [["2026-11-14", "1", "1", "complete", "16"], ["2026-11-14", "2", "1", "complete", "17"],
                          ["2026-11-15", "1", "1", "complete", "5"]])
        store = pd.read_parquet(sam.STORE)
        self.assertEqual((int((store["posted_day"] == pd.Timestamp(NOV14)).sum()), len(store)), (17, 22))
        self.assertEqual(store["notice_id"].nunique(), len(store))
        new = pd.read_csv(sam.NEW_CSV, dtype=str)
        self.assertEqual(new.groupby("posted_day")["n_notices"].apply(lambda s: s.astype(int).sum()).to_dict(),
                         {"2026-11-14": 17, "2026-11-15": 5})
        with open(sam.LATEST_JSON, encoding="utf-8") as fh:
            latest = json.load(fh)
        self.assertEqual((latest["through"], latest["requests"]["this_run"], latest["store"]["notices"]), ("2026-11-15", 2, 22))
        view = pd.read_parquet(os.path.join(sam.SNAP_DIR, "sam_view_2026-11-16.parquet"))
        late = view[view["notice_id"] == "N2026111400016"]                               # what the forecast step reads
        self.assertEqual(late["posted_date"].tolist(), [pd.Timestamp("2026-11-14")])
        # Nov 14 is never pulled a third time; Nov 15, pulled at midnight too, is pulled again from 12:00 UTC
        fake = FakeApi({NOV14: 18, NOV15: 6, NOV16: 3})
        code, out = self.run_script(fake, now=MIDNIGHT + pd.Timedelta(days=1, hours=8))
        self.assertEqual((code, [c[0] for c in fake.calls]), (0, [NOV15]))
        fake = FakeApi({NOV14: 18, NOV15: 6, NOV16: 3})
        code, out = self.run_script(fake, now=MIDNIGHT + pd.Timedelta(days=2, hours=10))
        self.assertEqual((code, [c[0] for c in fake.calls]), (0, [NOV16]))
        self.assertEqual(self.pulls()["posted_day"].value_counts().to_dict(), {"2026-11-14": 2, "2026-11-15": 2, "2026-11-16": 1})

    def test_a_day_pulled_from_12_utc_on_is_not_pulled_again(self):
        self.run_script(FakeApi({NOV14: 16}), now=pd.Timestamp("2026-11-15 12:40", tz="UTC"))   # 07:40 Eastern
        fake = FakeApi({NOV14: 17, NOV15: 5})
        code, out = self.run_script(fake, now=MIDNIGHT + pd.Timedelta(days=1))
        self.assertEqual((code, [c[0] for c in fake.calls]), (0, [NOV15]))
        self.assertNotIn("pulled again", out)

    def test_the_second_pull_takes_its_turn_in_date_order_within_the_budget(self):
        self.run_script(FakeApi({NOV14: 16}), now=MIDNIGHT)
        fake = FakeApi({NOV14: 17, NOV15: 5})
        code, out = self.run_script(fake, "--max-requests", "1", now=MIDNIGHT + pd.Timedelta(days=1))
        self.assertEqual((code, [c[0] for c in fake.calls]), (0, [NOV14]))               # Nov 15 waits for the next run
        self.assertIn("budget ran out", out)
        fake = FakeApi({NOV14: 18, NOV15: 5})
        code, out = self.run_script(fake, now=MIDNIGHT + pd.Timedelta(days=1, hours=1))
        self.assertEqual((code, [c[0] for c in fake.calls]), (0, [NOV15]))               # and Nov 14 is not pulled a third time

    def test_a_second_pull_that_falls_short_once_is_tried_again_before_the_day_is_kept_short(self):
        class Lying(FakeApi):                    # says one more than it sends, then an empty page
            def __call__(self, key, day, offset):
                d = super().__call__(key, day, offset)
                return dict(d, totalRecords=d["totalRecords"] + 1, opportunitiesData=d["opportunitiesData"] if offset == 0 else [])
        self.run_script(FakeApi({NOV14: 16}), now=MIDNIGHT)
        code, out = self.run_script(Lying({NOV14: 16}), now=MIDNIGHT + pd.Timedelta(hours=8))
        self.assertEqual((code, self.pulls()["status"].tolist()), (0, ["complete", "incomplete"]))   # once: not "short" yet
        self.run_script(Lying({NOV14: 16}), now=MIDNIGHT + pd.Timedelta(hours=9))
        p = self.pulls()
        self.assertEqual((p["attempt"].tolist(), p["status"].tolist()), (["1", "2", "3"], ["complete", "incomplete", "short"]))
        self.assertEqual(int((pd.read_parquet(sam.STORE)["posted_day"] == pd.Timestamp(NOV14)).sum()), 16)

    def test_a_failed_second_pull_loses_nothing_and_the_day_is_pulled_again_in_full(self):
        self.run_script(FakeApi({NOV14: 16}), now=MIDNIGHT)
        code, out = self.run_script(FakeApi({NOV14: 17}, error=True), now=MIDNIGHT + pd.Timedelta(hours=8))
        self.assertEqual(code, sam.EXIT_API)                                               # as for any day the API fails on
        self.assertEqual(self.pulls()["status"].tolist(), ["complete", "incomplete"])
        self.assertEqual(len(pd.read_parquet(sam.STORE)), 16)                             # the first pull's notices stay
        fake = FakeApi({NOV14: 17, NOV15: 5})
        code, out = self.run_script(fake, now=MIDNIGHT + pd.Timedelta(days=1, hours=8))
        self.assertEqual((code, [c[0] for c in fake.calls]), (0, [NOV14, NOV15]))
        p = self.pulls()
        self.assertEqual((p["attempt"].tolist(), p["status"].tolist()),
                         (["1", "2", "3", "1"], ["complete", "incomplete", "complete", "complete"]))
        self.assertEqual(int((pd.read_parquet(sam.STORE)["posted_day"] == pd.Timestamp(NOV14)).sum()), 17)
        fake = FakeApi({NOV14: 18, NOV15: 6, NOV16: 3})
        self.run_script(fake, now=MIDNIGHT + pd.Timedelta(days=2, hours=8))
        self.assertEqual([c[0] for c in fake.calls], [NOV16])


class ViewForTheForecastStep(Paths):
    def test_31_reads_the_view_and_refuses_a_short_store(self):
        spec = importlib.util.spec_from_file_location("daily_forecast_31", os.path.join(SCRIPTS, "31_daily_forecast.py"))
        f31 = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(f31)
        self.run_script(FakeApi({YESTERDAY: 20}))
        os.makedirs(sam.SNAP_DIR, exist_ok=True)
        pd.DataFrame({"notice_id": ["x"]}).to_parquet(os.path.join(sam.SNAP_DIR, "sam_active_2026-11-12.parquet"))
        path, day = f31.newest_snapshot(sam.SNAP_DIR)
        self.assertEqual((os.path.basename(path), day), ("sam_view_2026-11-12.parquet", "2026-11-12"))   # the view wins
        self.assertEqual(f31.store_first_day(path), pd.Timestamp("2026-11-11"))
        self.assertIsNone(f31.store_first_day(os.path.join(sam.SNAP_DIR, "sam_active_2026-11-12.parquet")))
        out = io.StringIO()
        paths = {"snap_dir": sam.SNAP_DIR, "model_path": os.path.join(self.tmp, "none.json"),
                 "batch_dir": os.path.join(self.tmp, "batches"), "daily_dir": os.path.join(self.tmp, "daily"),
                 "dryrun_dir": os.path.join(self.tmp, "dry")}
        with contextlib.redirect_stdout(out), mock.patch.dict(os.environ, {"GITHUB_ACTIONS": ""}):
            code = f31.run([], paths=paths, now=NOW)
        self.assertEqual(code, 0)                                            # waits without failing the robot
        self.assertIn("WAITING", out.getvalue())
        self.assertIn("the first batch can be sealed from 2026-12-11", out.getvalue())
        self.assertFalse(os.path.exists(paths["batch_dir"]))                 # nothing was sealed


if __name__ == "__main__":
    unittest.main()
