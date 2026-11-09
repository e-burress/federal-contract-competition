"""Offline tests for scripts/14_sam_daily_snapshot.py: mixed-encoding decoding, scrub(), the header check, the counts CSV merge."""
import contextlib
import datetime
import importlib.util
import io
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

import pandas as pd

SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
sys.path.insert(0, SCRIPTS)
_spec = importlib.util.spec_from_file_location("sam_snapshot_14", os.path.join(SCRIPTS, "14_sam_daily_snapshot.py"))
sam = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sam)


class TempDirCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fcc_test_sam_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def write(self, name, data):
        p = os.path.join(self.tmp, name)
        with open(p, "wb") as fh:
            fh.write(data)
        return p


class MixedTextStreamTests(TempDirCase):
    def read(self, data, **kw):
        s = sam.MixedTextStream(self.write("x.csv", data), **kw)
        try:
            return s.read(), s
        finally:
            s.close()

    def test_mixed_utf8_and_cp1252(self):
        # "café" in UTF-8, then cp1252 curly double quotes (0x93, 0x94) and an apostrophe (0x92), plus an en dash (0x96)
        data = b"caf\xc3\xa9 \x93quoted\x94 it\x92s a\x96b\n"
        text, s = self.read(data)
        self.assertEqual(text, "café “quoted” it’s a–b\n")
        self.assertEqual(s.n_utf8, 1)
        self.assertEqual(s.n_stray, 4)

    def test_pure_ascii_and_pure_utf8_pass_through(self):
        text, s = self.read(b"plain,ascii\n1,2\n")
        self.assertEqual(text, "plain,ascii\n1,2\n")
        self.assertEqual((s.n_utf8, s.n_stray), (0, 0))
        text, s = self.read("Niño — €\n".encode("utf-8"))
        self.assertEqual(text, "Niño — €\n")
        self.assertEqual((s.n_utf8, s.n_stray), (3, 0))

    def test_utf8_bom_is_removed(self):
        text, _ = self.read(b"\xef\xbb\xbfa,b\n1,2\n")
        self.assertEqual(text, "a,b\n1,2\n")

    def test_undefined_cp1252_byte_becomes_replacement_char(self):
        text, _ = self.read(b"x\x81y\n")                                # 0x81 is undefined in cp1252
        self.assertEqual(text, "x�y\n")

    def test_small_blocks_never_split_a_multibyte_character(self):
        line = "café “x”".encode("utf-8") + b"\n"
        data = line * 50 + b"it\x92s\n" * 50
        text, s = self.read(data, block=7)                            # tiny blocks: cut points fall inside sequences
        self.assertEqual(text, ("café “x”\n" * 50) + ("it’s\n" * 50))
        self.assertEqual(s.n_utf8, 150)
        self.assertEqual(s.n_stray, 50)

    def test_utf16_is_refused(self):
        with self.assertRaises(RuntimeError):
            self.read("a,b\n".encode("utf-16"))

    def test_pandas_can_read_the_stream(self):
        p = self.write("m.csv", b"Title,Other\ncaf\xc3\xa9,\x93hi\x94\n")
        s = sam.MixedTextStream(p)
        df = pd.read_csv(s, dtype=str)
        s.close()
        self.assertEqual(df["Title"][0], "café")
        self.assertEqual(df["Other"][0], "“hi”")


class ScrubTests(unittest.TestCase):
    def test_emails_are_masked(self):
        self.assertEqual(sam.scrub("Contact john.o'brien+x@agency.gov today"), "Contact [email] today")
        self.assertEqual(sam.scrub("a@b.com and c@d.co.uk"), "[email] and [email]")

    def test_us_phone_numbers_are_masked(self):
        for phone in ("(202) 555-0147", "202-555-0147", "202.555.0147", "202 555 0147", "+1 202 555 0147", "1-202-555-0147"):
            with self.subTest(phone=phone):
                self.assertEqual(sam.scrub(f"Call {phone} now"), "Call [phone] now")

    def test_normal_titles_are_left_alone(self):
        for title in ("Janitorial Services - Building 4", "36C10X26Q0123 Brand Name Laptops", "W911-2026-0001 Repair of HVAC",
                      "FY2026 Order 12345-6789", "Purchase of 250,000 sq ft carpet $1,234,567.89", "Posted 2026-11-09 amendment 0003",
                      "Item 1234 5678 9012", ""):
            with self.subTest(title=title):
                self.assertEqual(sam.scrub(title), title)

    def test_non_strings_pass_through(self):
        self.assertIsNone(sam.scrub(None))
        self.assertEqual(sam.scrub(5), 5)
        self.assertIs(sam.scrub(pd.NA), pd.NA)

    def test_clean_chunk_scrubs_titles_sets_flags_and_drops_description(self):
        cols = list(sam.USECOLS)
        row = dict.fromkeys(cols, "")
        row.update({"NoticeId": "n1", "Title": "Brand name chairs, call 202-555-0147", "Description": "a sole source buy"})
        out = sam.clean_chunk(pd.DataFrame([row]))
        self.assertEqual(out["title"][0], "Brand name chairs, call [phone]")
        self.assertEqual((int(out["kw_brand"][0]), int(out["kw_sole"][0])), (1, 1))
        self.assertNotIn("Description", out.columns)
        self.assertNotIn("description", out.columns)


class CheckHeaderTests(TempDirCase):
    def header_file(self, cols, name="h.csv"):
        return self.write(name, (",".join(cols) + "\n").encode("utf-8"))

    def test_complete_header_passes_and_reports_unknown_columns(self):
        cols = list(sam.USECOLS) + ["PrimaryContactEmail", "SecondaryContactFullname", "SomeBrandNewColumn"]
        got, extra = sam.check_header(self.header_file(cols))
        self.assertEqual(got, cols)
        self.assertEqual(extra, ["SomeBrandNewColumn"])                # contact columns and known drops are not "new"

    def test_missing_required_column_stops_with_a_clear_message(self):
        cols = [c for c in sam.USECOLS if c != "ResponseDeadLine"]
        with self.assertRaises(SystemExit) as cm:
            sam.check_header(self.header_file(cols))
        msg = str(cm.exception.code)
        self.assertIn("STOP", msg)
        self.assertIn("missing columns", msg)
        self.assertIn("ResponseDeadLine", msg)

    def test_several_missing_columns_are_all_named(self):
        cols = [c for c in sam.USECOLS if c not in ("Title", "Sol#")]
        with self.assertRaises(SystemExit) as cm:
            sam.check_header(self.header_file(cols))
        self.assertTrue("Title" in str(cm.exception.code) and "Sol#" in str(cm.exception.code))


class UpdateCountsCsvTests(TempDirCase):
    def frame(self, snap, rows):
        return pd.DataFrame([[snap.isoformat(), *r] for r in rows], columns=sam.COUNT_COLS)

    def run_update(self, snap, rows):
        with contextlib.redirect_stdout(io.StringIO()):
            return sam.update_counts_csv(self.frame(snap, rows), snap)

    def test_rerunning_a_date_replaces_its_rows(self):
        d1, d2 = datetime.date(2026, 11, 1), datetime.date(2026, 11, 2)
        path = os.path.join(self.tmp, "counts.csv")
        with mock.patch.object(sam, "COUNTS_CSV", path):
            self.assertEqual(self.run_update(d1, [("DEPT A", "Solicitation", 10, 9, 3, 7, 2, 5),
                                                  ("DEPT A", "Award Notice", 4, 4, 1, None, None, None)]), 2)
            self.assertEqual(self.run_update(d2, [("DEPT A", "Solicitation", 11, 10, 2, 8, 1, 6)]), 3)
            # re-run 2026-11-01 with different numbers and one row fewer: replaced, not appended
            self.assertEqual(self.run_update(d1, [("DEPT A", "Solicitation", 99, 90, 9, 70, 8, 60)]), 2)
            df = pd.read_csv(path, dtype={"snapshot_date": str})
        self.assertEqual(list(df.columns), sam.COUNT_COLS)
        self.assertEqual(df["snapshot_date"].tolist(), ["2026-11-01", "2026-11-02"])
        self.assertFalse(df.duplicated(["snapshot_date", "department", "type"]).any())
        self.assertEqual(df.loc[df.snapshot_date == "2026-11-01", "n_active"].tolist(), [99])
        self.assertEqual(df.loc[df.snapshot_date == "2026-11-02", "n_active"].tolist(), [11])
        self.assertFalse(os.path.exists(path + ".tmp"))                  # the atomic write left no temp file

    def test_new_date_is_appended_in_sorted_order(self):
        path = os.path.join(self.tmp, "counts.csv")
        with mock.patch.object(sam, "COUNTS_CSV", path):
            self.run_update(datetime.date(2026, 11, 5), [("B", "Solicitation", 1, 1, 0, 0, 0, 0)])
            self.run_update(datetime.date(2026, 11, 3), [("B", "Solicitation", 2, 2, 0, 0, 0, 0)])
            df = pd.read_csv(path, dtype={"snapshot_date": str})
        self.assertEqual(df["snapshot_date"].tolist(), ["2026-11-03", "2026-11-05"])


class BuildCountsTests(unittest.TestCase):
    def test_counts_per_department_and_type(self):
        snap = datetime.date(2026, 11, 9)
        df = pd.DataFrame({
            "department": ["DEPT A", "DEPT A", "DEPT A", "DEPT B"],
            "type": ["Solicitation", "Solicitation", "Award Notice", "Solicitation"],
            "sol_number": ["S1", "S1", "S9", "S2"],
            "posted_date": pd.to_datetime(["2026-11-08", "2026-10-01", "2026-11-09", "2026-11-09"]),
            "response_deadline": pd.to_datetime(["2026-11-12", "2026-12-20", None, "2026-11-01"], utc=True),
        })
        out = sam.build_counts(df, snap).set_index(["department", "type"])
        a = out.loc[("DEPT A", "Solicitation")]
        self.assertEqual((a.n_active, a.n_sol_numbers, a.n_posted_last_7d), (2, 1, 1))
        self.assertEqual((a.n_open, a.n_deadline_next_7d, a.n_deadline_next_30d), (2, 1, 1))
        b = out.loc[("DEPT B", "Solicitation")]
        self.assertEqual((b.n_active, b.n_open), (1, 0))                  # its deadline has already passed
        self.assertTrue(pd.isna(out.loc[("DEPT A", "Award Notice"), "n_open"]))     # blank for non-solicitation types
        self.assertEqual(set(out.index.get_level_values(0)), {"DEPT A", "DEPT B"})


if __name__ == "__main__":
    unittest.main()
