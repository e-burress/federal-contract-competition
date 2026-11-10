"""Offline tests for scripts/22_sam_archive.py on small synthetic CSV files: version chains, Sol# normalising, has_presol,
privacy of the outputs, the partial-download trim and the merge across fiscal years."""
import csv
import importlib.util
import io
import os
import re
import shutil
import sys
import tempfile
import unittest

import pandas as pd

SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
sys.path.insert(0, SCRIPTS)
_spec = importlib.util.spec_from_file_location("sam_archive_22", os.path.join(SCRIPTS, "22_sam_archive.py"))
arc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(arc)

HEADER = ["NoticeId", "Title", "Sol#", "Department/Ind.Agency", "CGAC", "Sub-Tier", "FPDS Code", "Office", "AAC Code",
          "PostedDate", "Type", "BaseType", "ArchiveType", "ArchiveDate", "SetASideCode", "SetASide", "ResponseDeadLine",
          "NaicsCode", "ClassificationCode", "PopStreetAddress", "PopCity", "PopState", "PopZip", "PopCountry", "Active",
          "AwardNumber", "AwardDate", "Award$", "Awardee", "PrimaryContactTitle", "PrimaryContactFullname",
          "PrimaryContactEmail", "PrimaryContactPhone", "PrimaryContactFax", "SecondaryContactTitle",
          "SecondaryContactFullname", "SecondaryContactEmail", "SecondaryContactPhone", "SecondaryContactFax",
          "OrganizationType", "State", "City", "ZipCode", "CountryCode", "AdditionalInfoLink", "Link", "Description"]
PERSONAL = ["Jane Q Officer", "jane.officer@agency.gov", "202-555-0143", "123 Main Street", "Acme Widgets LLC",
            "secret description text"]


def notice(i, sol, typ, posted, deadline="", base=None, **kw):
    """One CSV row (dict) with personal data in every contact column, so the tests can check that none of it survives."""
    r = {c: "" for c in HEADER}
    r.update({"NoticeId": f"{i:032x}", "Title": f"Notice {i}", "Sol#": sol, "Department/Ind.Agency": "INTERIOR, DEPARTMENT OF THE",
              "CGAC": "014", "Sub-Tier": "NATIONAL PARK SERVICE", "FPDS Code": "1443", "Office": "PWR OFFICE NAME",
              "AAC Code": "140P20", "PostedDate": posted, "Type": typ, "BaseType": base or typ, "ArchiveType": "auto15",
              "ArchiveDate": "2024-12-31", "ResponseDeadLine": deadline, "NaicsCode": "561720", "ClassificationCode": "S201",
              "PopStreetAddress": "123 Main Street", "Active": "No", "Awardee": "Acme Widgets LLC",
              "PrimaryContactFullname": "Jane Q Officer", "PrimaryContactEmail": "jane.officer@agency.gov",
              "PrimaryContactPhone": "202-555-0143", "SecondaryContactEmail": "bob@agency.gov",
              "Description": "secret description text\nwith a second line", "Link": "https://sam.gov/opp/x/view"})
    r.update(kw)
    return r


def csv_bytes(rows):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=HEADER, quoting=csv.QUOTE_ALL, lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return buf.getvalue().encode("utf-8")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fcc_test_sam_archive_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def write(self, name, data):
        p = os.path.join(self.tmp, name)
        with open(p, "wb") as fh:
            fh.write(data)
        return p

    def year_rows(self):
        return [
            notice(1, "140P20-24-R-0001", "Presolicitation", "2023-12-15 09:00:00"),
            notice(2, "140P2024R0001", "Solicitation", "2024-01-01 00:00:00", "2024-01-15T00:00:00+00:00",
                   Description="Brand name or equal pumps"),
            notice(3, "140p20-24-r-0001 ", "Solicitation", "2024-01-10 12:00:00", "2024-01-25T17:00:00-05:00"),
            notice(4, "140P20-24-R-0001", "Award Notice", "2024-03-01 08:00:00", base="Solicitation",
                   AwardNumber="140P2024C0007", AwardDate="2024-02-28", **{"Award$": "$22,612.62"}),
            notice(5, "SOLE-2", "Combined Synopsis/Solicitation", "2024-02-01 10:00:00", "2024-02-11",
                   Title="Sole source repair, call 202-555-0199 or jane.officer@agency.gov"),
            notice(6, "", "Solicitation", "2024-02-02 10:00:00", "2024-02-20"),
            notice(7, "SS-9", "Sources Sought", "2024-04-01 10:00:00"),
            notice(8, "QQ-1 jane.officer@agency.gov", "Solicitation", "2024-05-01 10:00:00", "2024-05-02"),
        ]


class NormaliseTests(unittest.TestCase):
    def test_norm_sol(self):
        got = arc.norm_sol(["w912nw-26-q-a027", " W912NW26QA027 ", "a.b/c 1", "--", None]).tolist()
        self.assertEqual(got[:3], ["W912NW26QA027", "W912NW26QA027", "ABC1"])
        self.assertTrue(pd.isna(got[3]) and pd.isna(got[4]))

    def test_fiscal_year(self):
        s = pd.Series(pd.to_datetime(["2023-09-30", "2023-10-01", "2024-09-30", None]))
        self.assertEqual(arc.fiscal_year(s).tolist()[:3], [2023, 2024, 2024])


class YearTests(Base):
    def run_year(self):
        p = self.write("FY2024.csv", csv_bytes(self.year_rows()))
        return arc.process_file(p, 2024)

    def test_chain_across_versions(self):
        nt, sol, q = self.run_year()
        self.assertEqual(len(nt), 8)
        r = sol.set_index("sol_norm").loc["140P2024R0001"]
        self.assertEqual(r["n_versions"], 2)
        self.assertEqual(r["first_posted"], pd.Timestamp("2024-01-01"))
        self.assertEqual(r["last_posted"], pd.Timestamp("2024-01-10 12:00"))
        self.assertEqual(r["first_deadline"], pd.Timestamp("2024-01-15", tz="UTC"))
        self.assertEqual(r["final_deadline"], pd.Timestamp("2024-01-25 22:00", tz="UTC"))
        self.assertAlmostEqual(float(r["window_first_days"]), 14.0, places=3)
        self.assertAlmostEqual(float(r["window_final_days"]), 24 + 22 / 24, places=3)
        self.assertEqual(r["n_deadlines"], 2)
        self.assertEqual(r["sol_number"], "140P2024R0001")                       # as written on the first version
        self.assertEqual((r["has_presol"], r["has_award_notice"]), (1, 1))
        self.assertEqual(r["award_number"], "140P2024C0007")
        self.assertEqual(r["kw_brand"], 1)                                        # from the first version's description
        self.assertEqual(r["type_first"], "Solicitation")

    def test_has_presol_and_types(self):
        _, sol, q = self.run_year()
        s = sol.set_index("sol_norm")
        self.assertEqual(s.loc["SOLE2", "has_presol"], 0)
        self.assertEqual(s.loc["SOLE2", "kw_sole"], 1)
        self.assertEqual(s.loc["SOLE2", "type_first"], "Combined Synopsis/Solicitation")
        self.assertNotIn("SS9", s.index)                                          # sources sought is not a solicitation
        self.assertEqual(len(sol), 3)                                             # the row without a Sol# is left out
        self.assertEqual(q["notices"]["fiscal_year_definition"]["share_posted_in_file_fy"], 1.0)
        self.assertEqual(q["notices"]["share_with_sol_number"], 0.875)

    def test_award_amount_with_dollar_sign(self):
        nt, _, q = self.run_year()
        self.assertEqual(nt.loc[nt["notice_id"] == f"{4:032x}", "award_amount"].item(), 22612.62)
        self.assertEqual(q["reading"]["award_amount_unparsed"], 0)

    def test_privacy(self):
        nt, sol, q = self.run_year()
        self.assertEqual(list(nt.columns), arc.NOTICE_COLS)
        for df in (nt, sol):
            self.assertFalse({"title", "office", "description", "awardee"} & set(df.columns))
            self.assertFalse([c for c in df.columns if "contact" in c.lower() or "pop_" in c])
            text = df.astype("string").fillna("").to_csv(index=False)
            for p in PERSONAL:
                self.assertNotIn(p, text)
            self.assertNotIn("@", text)
            self.assertIsNone(re.search(r"\d{3}[-. ]\d{3}[-. ]\d{4}", text))
        self.assertIn("QQ-1 [email]", nt["sol_number"].tolist())                  # e-mail inside a Sol# is masked
        self.assertNotIn("PWR OFFICE NAME", nt.astype("string").to_csv())         # office name dropped, code kept
        self.assertEqual(nt["aac_code"].astype("string").unique().tolist(), ["140P20"])


class TrimTests(Base):
    def test_partial_last_row_is_dropped(self):
        data = csv_bytes(self.year_rows()[:3])
        cut = data.index(b"secret description text\nwith", data.index(f"{3:032x}".encode())) + 10   # inside a quoted line break
        p = self.write("part.csv", data[:cut])
        dropped = arc.trim_partial(p)
        self.assertGreater(dropped, 0)
        nt, _, q = arc.process_file(p, 2024)
        self.assertEqual(nt["notice_id"].tolist(), [f"{1:032x}", f"{2:032x}"])
        self.assertEqual(q["reading"]["malformed_rows_skipped"], 0)


class MergeTests(Base):
    def test_merge_across_years(self):
        fy23 = [notice(11, "ABC-23-Q-9", "Solicitation", "2023-09-28 10:00:00", "2023-10-10"),
                notice(12, "PRE-1", "Presolicitation", "2023-09-01 10:00:00")]
        fy24 = [notice(13, "abc23q9", "Solicitation", "2023-10-05 10:00:00", "2023-10-20"),
                notice(14, "ABC23Q9", "Award Notice", "2023-11-01 10:00:00", AwardNumber="P-1"),
                notice(15, "PRE-1", "Combined Synopsis/Solicitation", "2023-10-02 10:00:00", "2023-10-12"),
                notice(16, "ONLY-24", "Solicitation", "2024-02-02 10:00:00", "2024-02-12")]
        out = os.path.join(self.tmp, "out")
        os.makedirs(out)
        for fy, rows in ((2023, fy23), (2024, fy24)):
            nt, sol, _ = arc.process_file(self.write(f"FY{fy}.csv", csv_bytes(rows)), fy)
            arc.write_parquet(nt, os.path.join(out, f"notices_FY{fy}.parquet"))
        # the FY2024 file alone sees ABC23Q9 starting on 2023-10-05; the merge must find the FY2023 version
        meta = arc.merge(out, n_parts=3)
        m = pd.read_parquet(os.path.join(out, "solicitations_all.parquet")).set_index("sol_norm")
        self.assertEqual(sorted(m.index), ["ABC23Q9", "ONLY24", "PRE1"])
        r = m.loc["ABC23Q9"]
        self.assertEqual(r["n_versions"], 2)
        self.assertEqual(r["first_posted"], pd.Timestamp("2023-09-28 10:00"))
        self.assertEqual(r["first_deadline"], pd.Timestamp("2023-10-10", tz="UTC"))
        self.assertEqual(r["final_deadline"], pd.Timestamp("2023-10-20", tz="UTC"))
        self.assertEqual((r["first_file_fy"], r["last_file_fy"]), (2023, 2024))
        self.assertEqual(r["has_award_notice"], 1)
        self.assertEqual(m.loc["PRE1", "has_presol"], 1)                          # presol sits in the other year's file
        self.assertEqual(m.loc["ONLY24", "has_presol"], 0)
        self.assertEqual(meta["rows"], 3)
        self.assertTrue(os.path.exists(os.path.join(out, "sam_archive_all.json")))


if __name__ == "__main__":
    unittest.main()
