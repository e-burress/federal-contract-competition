"""Offline tests for scripts/common.py (keyword flags, config.toml, default contact address) and the forecast seals."""
import hashlib
import json
import os
import re
import subprocess
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(REPO, "scripts")
sys.path.insert(0, SCRIPTS)
import common  # noqa: E402


class KwFlagsTests(unittest.TestCase):
    def test_brand_name_variants(self):
        for text in ("Brand Name or equal", "BRAND-NAME justification", "brand   name only", "Brandname laptops", "brand\tname"):
            with self.subTest(text=text):
                self.assertEqual(common.kw_flags(text, ""), (1, 0))

    def test_sole_source_variants(self):
        for text in ("Sole Source award", "sole-source", "SINGLE SOURCE", "single-source", "only one responsible source",
                     "one responsible source", "limited-source", "Limited source notice", "proprietary software"):
            with self.subTest(text=text):
                self.assertEqual(common.kw_flags(text, ""), (0, 1))

    def test_plain_text_is_zero_zero(self):
        self.assertEqual(common.kw_flags("Janitorial services for building 4",
                                         "Full and open competition, small business set aside"), (0, 0))
        self.assertEqual(common.kw_flags(None, None), (0, 0))
        self.assertEqual(common.kw_flags("", ""), (0, 0))

    def test_title_and_snippet_are_both_searched_and_flags_are_ints(self):
        b, s = common.kw_flags("Router purchase", "brand name or equal; sole source")
        self.assertEqual((b, s), (1, 1))
        self.assertIs(type(b), int)
        self.assertEqual(common.kw_flags("sole source", "brand name"), (1, 1))



class ConfigTests(unittest.TestCase):
    def test_config_values(self):
        c = common.CONFIG
        self.assertRegex(c["project"]["repository"], r"^[\w.-]+/[\w.-]+$")
        fy = c["analysis"]["fiscal_years"]
        self.assertTrue(fy and all(isinstance(y, int) for y in fy))
        self.assertEqual(fy, sorted(fy))
        five = c["analysis"]["five_departments"]
        self.assertEqual(len(five), 5)
        self.assertEqual(len(set(five)), 5)
        self.assertEqual(common.AGENCIES, five)
        self.assertTrue(c["forecasts"]["F1"]["id"].startswith("PIO-F1-"))
        self.assertTrue(c["forecasts"]["F2"]["id"].startswith("PIO-F2-"))
        self.assertEqual(common.FISCAL_YEARS, fy)
        self.assertRegex(common.WINDOW_END, r"^\d{4}-\d{2}-\d{2}$")

    def test_project_paths(self):
        self.assertEqual(os.path.normcase(common.PROJECT), os.path.normcase(REPO))
        self.assertTrue(os.path.isfile(os.path.join(common.PROJECT, "config.toml")))


class ContactTests(unittest.TestCase):
    """CONTACT is computed at import time from the environment, so check it in a clean child process."""

    def contact(self, **env_over):
        env = {k: v for k, v in os.environ.items() if k not in ("PIO_CONTACT", "GITHUB_REPOSITORY")}
        env.update(env_over)
        code = "import sys; sys.path.insert(0, sys.argv[1]); import common; print(common.CONTACT); print(common.UA_SAM)"
        out = subprocess.run([sys.executable, "-I", "-c", code, SCRIPTS], env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout.splitlines()[:2]

    def test_default_is_a_web_address_without_an_email(self):
        contact, ua = self.contact()
        self.assertTrue(contact.startswith("https://"))
        self.assertNotIn("@", contact)
        self.assertIn(contact, ua)
        self.assertEqual(contact, common.CONFIG["contact"]["default"])

    def test_environment_overrides(self):
        self.assertEqual(self.contact(PIO_CONTACT="https://example.org/me")[0], "https://example.org/me")
        self.assertEqual(self.contact(GITHUB_REPOSITORY="someone/fork")[0], "https://github.com/someone/fork")


class ForecastSealTests(unittest.TestCase):
    def sums(self):
        with open(os.path.join(REPO, "forecasts", "SHA256SUMS.txt"), encoding="utf-8") as fh:
            rows = [ln.split() for ln in fh.read().splitlines() if ln.strip()]
        return {r[1]: r[0] for r in rows}, rows

    def test_f2_file_matches_recorded_sha256(self):
        sums, _ = self.sums()
        name = "PIO-F2-threshold-fy2027q1-20261024.json"
        self.assertIn(name, sums)
        with open(os.path.join(REPO, "forecasts", name), "rb") as fh:
            blob = fh.read()
        self.assertEqual(hashlib.sha256(blob).hexdigest(), sums[name])

    def test_f2_file_is_the_configured_forecast(self):
        f2 = common.CONFIG["forecasts"]["F2"]
        with open(os.path.join(REPO, f2["file"]), "rb") as fh:
            self.assertEqual(json.loads(fh.read())["id"], f2["id"])
        self.assertEqual(os.path.basename(f2["file"]), f2["id"] + ".json")

    def test_sums_file_lists_an_f1_file_and_well_formed_entries(self):
        sums, rows = self.sums()
        self.assertTrue(any(n.startswith("PIO-F1-") for n in sums))
        for r in rows:
            with self.subTest(file=r[1]):
                self.assertRegex(r[0], r"^[0-9a-f]{64}$")
                self.assertTrue(any(re.fullmatch(r"made_at=\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", x) for x in r[2:]))
        self.assertIn(common.CONFIG["forecasts"]["F1"]["id"] + ".json", sums)   # F1 file itself is private: not required


if __name__ == "__main__":
    unittest.main()
