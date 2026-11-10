"""Offline tests for scripts/25_market_depth.py: counting, NAICS string parsing, inactive entities skipped, no names written.
The synthetic lines follow the real 142-field layout (GSA "SAM Master Extract Mapping v6.0 Public File V2 Layout")."""
import importlib.util
import io
import os
import shutil
import sys
import tempfile
import unittest
import zipfile

SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
sys.path.insert(0, SCRIPTS)
_spec = importlib.util.spec_from_file_location("market_depth_25", os.path.join(SCRIPTS, "25_market_depth.py"))
md = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(md)

EXTRACT = "20261101"
SECRET_NAME = "ZEBRA QUILT WORKS LLC"
SECRET_STREET = "99 HIDDEN ALLEY"
SECRET_PERSON = "PRISCILLA"


def make_line(uei="AAAAAAAAAAA1", code="A", purpose="Z2", expiry="20270101", state="VA", country="USA", primary="541511",
              naics="541511Y~541512N", exc="", excl=" ", pipes=141):
    """One line with the real positions (1-based layout position in the comments)."""
    f = [""] * 142
    f[0] = uei                       # 1  UEI
    f[3] = "1ABC2"                   # 4  CAGE
    f[5] = code                      # 6  SAM extract code
    f[6] = purpose                   # 7  purpose of registration
    f[8] = expiry                    # 9  registration expiration date
    f[11] = SECRET_NAME              # 12 legal business name
    f[15] = SECRET_STREET            # 16 physical address line 1
    f[17] = "SPRINGFIELD"            # 18 city
    f[18] = state                    # 19 state
    f[19] = "22150"                  # 20 zip
    f[21] = country                  # 22 country
    f[32] = primary                  # 33 primary NAICS
    f[34] = naics                    # 35 NAICS code string
    f[46] = SECRET_PERSON            # 47 government business POC first name
    f[113] = exc                     # 114 NAICS exception string
    f[115] = excl                    # 116 exclusion status flag
    f[141] = "!end"                  # 142 end of record
    line = "|".join(f)
    if pipes != 141:                 # simulate a stray "|" inside a text field
        line = line.replace(SECRET_NAME, SECRET_NAME + "|X")
    return (line + "\n").encode("latin-1")


def run(lines, extract=EXTRACT):
    t = md.Tally(extract)
    for ln in lines:
        t.add(ln)
    return t


class LayoutTests(unittest.TestCase):
    def test_synthetic_line_has_142_fields(self):
        self.assertEqual(make_line().count(b"|"), md.N_PIPES)
        self.assertEqual(len(make_line().rstrip(b"\n").split(b"|")), 142)

    def test_positions_match_layout(self):
        # layout positions 1, 6, 7, 9, 19, 22, 33, 35, 114, 116
        self.assertEqual((md.F_UEI, md.F_CODE, md.F_PURPOSE, md.F_EXPIRY, md.F_STATE, md.F_COUNTRY),
                         (0, 5, 6, 8, 18, 21))
        self.assertEqual((md.F_PRIMARY, md.F_NAICS, md.F_EXC, md.F_EXCL), (32, 34, 113, 115))


class CountingTests(unittest.TestCase):
    def test_counts_primary_any_small(self):
        t = run([
            make_line("U1", primary="541511", naics="541511Y~541512N"),
            make_line("U2", primary="541512", naics="541511N~541512Y~236220Y", state="TX"),
            make_line("U3", primary="236220", naics="236220 ", state="TX"),      # blank size letter = unknown
        ])
        self.assertEqual(t.counted, 3)
        self.assertEqual(t.any["541511"], 2)
        self.assertEqual(t.any["541512"], 2)
        self.assertEqual(t.any["236220"], 2)
        self.assertEqual(t.primary["541511"], 1)
        self.assertEqual(t.primary["541512"], 1)
        self.assertEqual(t.primary["236220"], 1)
        self.assertEqual(t.small["541511"], 1)
        self.assertEqual(t.small["541512"], 1)
        self.assertEqual(t.small["236220"], 1)
        self.assertEqual(t.unknown["236220"], 1)
        self.assertEqual(t.code_slots, 6)
        self.assertEqual(t.state[("236220", "TX")], 2)
        self.assertEqual(t.state[("541511", "VA")], 1)
        self.assertEqual(t.state3[("541", "TX")], 1)          # one entity listing two 541xxx codes counts once
        self.assertEqual(t.state3[("541", "VA")], 1)

    def test_code_listed_twice_by_one_entity_counts_once(self):
        t = run([make_line(naics="541511Y~541511Y~541512N")])
        self.assertEqual(t.any["541511"], 1)

    def test_primary_not_in_list_still_counts_as_primary(self):
        t = run([make_line(primary="999999", naics="541511Y")])
        self.assertEqual(t.primary["999999"], 1)
        self.assertEqual(t.any["999999"], 0)

    def test_bad_primary_ignored(self):
        t = run([make_line(primary="333998~333914", naics="541511Y")])
        self.assertEqual(sum(t.primary.values()), 0)
        self.assertEqual(t.bad_primary, 1)


class NaicsStringTests(unittest.TestCase):
    def test_parsing_letters(self):
        t = md.Tally(EXTRACT)
        got = t._codes("541511Y~541512N~541519 ~541611", "")
        self.assertEqual(got, {"541511": "Y", "541512": "N", "541519": "?", "541611": "?"})

    def test_bad_tokens_skipped_and_counted(self):
        t = md.Tally(EXTRACT)
        got = t._codes("5415Y~541512N~~ABCDEFY", "")
        self.assertEqual(got, {"541512": "N"})
        self.assertEqual(t.bad_tokens, 2)

    def test_exception_letter_uses_first_exception_answer(self):
        t = md.Tally(EXTRACT)
        got = t._codes("541330E~541511Y~541715E~611430E", "541330YNNN~541715NYYY")
        self.assertEqual(got["541330"], "Y")
        self.assertEqual(got["541715"], "N")
        self.assertEqual(got["541511"], "Y")
        self.assertEqual(got["611430"], "?")                 # E without an exception entry
        self.assertEqual(t.sizes_from_exception, 2)

    def test_exception_with_blank_answer_is_unknown(self):
        t = md.Tally(EXTRACT)
        self.assertEqual(t._codes("541330E", "541330 YY ")["541330"], "?")

    def test_exception_small_counted_in_n_small(self):
        t = run([make_line(naics="541330E", exc="541330YYNN")])
        self.assertEqual(t.small["541330"], 1)
        self.assertEqual(t.unknown["541330"], 0)


class SkipTests(unittest.TestCase):
    def test_inactive_entities_skipped(self):
        t = run([
            make_line("U1"),
            make_line("U2", code="E"),                            # expired in the monthly file
            make_line("U3", expiry="20261031"),                   # active code but past the extract date
            make_line("U4", excl="D"),                            # active exclusion
            make_line("U5", pipes=142),                           # stray pipe: malformed
        ])
        self.assertEqual(t.active, 1)
        self.assertEqual(t.counted, 1)
        self.assertEqual(t.any["541511"], 1)
        self.assertEqual(sum(t.skipped.values()), 4)

    def test_expiry_on_extract_date_is_active(self):
        self.assertEqual(run([make_line(expiry=EXTRACT)]).active, 1)

    def test_repeated_uei_counted_once(self):
        t = run([make_line("U1"), make_line("U1"), make_line("U2")])
        self.assertEqual(t.active, 2)
        self.assertEqual(t.any["541511"], 2)
        self.assertEqual(t.skipped["repeated UEI (second CAGE code)"], 1)

    def test_assistance_only_entities_not_in_industry_counts(self):
        t = run([make_line("U1", purpose="Z1"), make_line("U2")])
        self.assertEqual(t.active, 2)
        self.assertEqual(t.active_purpose["Z1"], 1)
        self.assertEqual(t.counted, 1)
        self.assertEqual(t.any["541511"], 1)

    def test_entity_without_naics(self):
        t = run([make_line(primary="", naics="")])
        self.assertEqual(t.active, 1)
        self.assertEqual(t.no_naics, 1)
        self.assertEqual(t.counted, 0)

    def test_header_and_trailer_ignored(self):
        t = run([b"BOF PUBLIC V2 00000000 20261101 0000001 0000001\n", make_line(), b"EOF PUBLIC V2 00000000 20261101 0000001 0000001\n"])
        self.assertEqual(t.lines, 1)

    def test_state_only_for_us_addresses(self):
        t = run([make_line("U1", state="VA"), make_line("U2", state="ON", country="CAN"), make_line("U3", state="AE"),
                 make_line("U4", state="CALIFORNIA")])
        self.assertEqual(t.no_us_state, 3)
        self.assertEqual(sum(v for (c, s), v in t.state.items() if c == "541511"), 1)
        self.assertEqual(t.any["541511"], 4)                    # still in the NAICS counts


class PickFileTests(unittest.TestCase):
    def test_newest_utf8_file(self):
        def item(name):
            return {"key": "Entity Registration/Public V2/" + name}
        listing = {"_embedded": {"customS3ObjectSummaryList": [
            item("SAM_PUBLIC_MONTHLY_V2_20261101.ZIP"), item("SAM_PUBLIC_UTF-8_MONTHLY_V2_20261101.ZIP"),
            item("SAM_PUBLIC_UTF-8_MONTHLY_V2_20261005.ZIP"), item("readme.txt")]}}
        self.assertEqual(md.newest_file(listing), ("Entity Registration/Public V2/SAM_PUBLIC_UTF-8_MONTHLY_V2_20261101.ZIP", "20261101"))
        self.assertIsNone(md.newest_file({}))
        self.assertIsNone(md.newest_file(None))

    def test_header_date(self):
        self.assertEqual(md.header_date(b"BOF PUBLIC V2 00000000 20261101 0911820 0008387\n"), "20261101")
        self.assertIsNone(md.header_date(b"AAAA|BBB\n"))


class FileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fcc_test_md_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def build_zip_in_zip(self, lines):
        data = b"BOF PUBLIC V2 00000000 20261101 0000003 0000001\n" + b"".join(lines) + b"EOF PUBLIC V2 00000000 20261101 0000003 0000001\n"
        inner = io.BytesIO()
        with zipfile.ZipFile(inner, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("SAM_PUBLIC_TEST.dat", data)
        path = os.path.join(self.tmp, "SAM_PUBLIC_UTF-8_MONTHLY_V2_20261101.ZIP")
        with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as z:
            z.writestr("inner.ZIP", inner.getvalue())
        return path

    def test_read_nested_zip_and_write_outputs_without_names(self):
        lines = [make_line("U1"), make_line("U2", state="TX", naics="541511N~236220Y"), make_line("U3", code="E")]
        path = self.build_zip_in_zip(lines)
        work = os.path.join(self.tmp, "work")
        os.makedirs(work)
        tally, ext = md.read_file(path, work, None)
        self.assertEqual(ext, "20261101")
        self.assertEqual(os.listdir(work), [])                    # the unpacked inner file is removed
        self.assertEqual(tally.counted, 2)
        out = os.path.join(self.tmp, "out")
        info = md.write_outputs(tally, ext, out)
        md.write_doc(tally, ext, info, os.path.basename(path), out)
        self.assertEqual(sorted(os.listdir(out)), ["market_depth.md", "market_depth_naics.csv", "market_depth_naics_state.csv"])
        with open(os.path.join(out, "market_depth_naics.csv"), encoding="utf-8") as fh:
            rows = fh.read().splitlines()
        self.assertEqual(rows[0], "naics,n_primary,n_any,n_small_any,n_size_unknown,extract_date")
        self.assertIn("541511,2,2,1,0,2026-11-01", rows)
        self.assertIn("236220,0,1,1,0,2026-11-01", rows)
        with open(os.path.join(out, "market_depth_naics_state.csv"), encoding="utf-8") as fh:
            st = fh.read().splitlines()
        self.assertEqual(st[0], "naics,state,n_any")
        self.assertIn("541511,TX,1", st)
        # privacy: nothing identifying in any output
        for name in os.listdir(out):
            with open(os.path.join(out, name), encoding="utf-8") as fh:
                text = fh.read()
            for secret in (SECRET_NAME, SECRET_STREET, SECRET_PERSON, "SPRINGFIELD", "22150", "AAAAAAAAAAA1", "1ABC2", "U1", "U2"):
                self.assertNotIn(secret, text, f"{secret!r} found in {name}")

    def test_plain_dat_file(self):
        p = os.path.join(self.tmp, "x.dat")
        with open(p, "wb") as fh:
            fh.write(b"BOF PUBLIC V2 00000000 20261101 0000001 0000001\n" + make_line() + b"EOF PUBLIC V2 00000000 20261101 0000001 0000001\n")
        tally, ext = md.read_file(p, self.tmp, None)
        self.assertEqual((tally.counted, ext), (1, "20261101"))

    def test_state_file_falls_back_to_3_digit_when_big(self):
        t = run([make_line("U1"), make_line("U2", state="TX")])
        old = md.STATE_FILE_MAX
        md.STATE_FILE_MAX = 10
        try:
            info = md.write_outputs(t, EXTRACT, os.path.join(self.tmp, "o"))
        finally:
            md.STATE_FILE_MAX = old
        self.assertEqual(info["state_level"], 3)
        with open(os.path.join(self.tmp, "o", "market_depth_naics_state.csv"), encoding="utf-8") as fh:
            rows = fh.read().splitlines()
        self.assertEqual(rows[0], "naics3,state,n_any")
        self.assertIn("541,TX,1", rows)


if __name__ == "__main__":
    unittest.main()
