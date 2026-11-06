import importlib
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import seo

ENV = ("KARTA_ORIGIN", "KARTA_BASE", "KARTA_COUNTER")


def load(**env):
    for key in ENV:
        os.environ.pop(key, None)
    os.environ.update(env)
    return importlib.reload(seo)


class LocalizeTest(unittest.TestCase):
    def tearDown(self):
        load()

    def test_without_env_page_is_unchanged(self):
        mod = load()
        page = '<a href="/ulica/x">x</a><link rel="canonical" href="' + mod.LEGACY_ORIGIN + '/">'
        self.assertEqual(mod.localize(page), page)

    def test_section_prefix_and_origin(self):
        mod = load(KARTA_ORIGIN="https://example.test", KARTA_BASE="/svet", KARTA_COUNTER="42")
        page = (
            '<link rel="canonical" href="' + mod.LEGACY_ORIGIN + '/">'
            '<a href="/ulica/x">x</a><a href="//cdn.test/a">cdn</a><a href="https://other.test/">o</a>'
            '<script src="/app.js"></script>ym(113728535,"init");<a href="{{HOME}}">home</a>'
        )
        out = mod.localize(page)
        self.assertIn('href="https://example.test/svet/"', out)
        self.assertIn('href="/svet/ulica/x"', out)
        self.assertIn('href="//cdn.test/a"', out)
        self.assertIn('href="https://other.test/"', out)
        self.assertIn('src="/svet/app.js"', out)
        self.assertIn("ym(42,", out)
        self.assertIn('href="https://example.test/"', out)
        self.assertEqual(mod.SITE, "https://example.test/svet")

    def test_pages_use_site_with_prefix(self):
        mod = load(KARTA_ORIGIN="https://example.test", KARTA_BASE="/svet")
        self.assertTrue(mod.SITE.endswith("/svet"))


if __name__ == "__main__":
    unittest.main()
