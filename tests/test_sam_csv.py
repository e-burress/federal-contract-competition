"""Offline tests for scripts/sam_csv.py (moved from 14_sam_daily_snapshot.py in November 2026): mixed-encoding decoding, scrub(), clean_chunk() and the header check."""
import io
import os
import shutil
import sys
import tempfile
import unittest

import pandas as pd

SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
sys.path.insert(0, SCRIPTS)
import sam_csv as sam  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
