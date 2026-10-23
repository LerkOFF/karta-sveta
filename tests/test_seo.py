import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import seo
import store

ROOT = Path(__file__).resolve().parent.parent


class SeoPages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.old_db = store.DB
        store.DB = Path(cls.tmp.name) / "t.db"
        store.init()
        cls.site = seo.Site(json.loads((ROOT / "data" / "houses.json").read_text(encoding="utf-8")))

    @classmethod
    def tearDownClass(cls):
        store.DB = cls.old_db
        cls.tmp.cleanup()

    def test_translit_and_slugs_are_unique(self):
        self.assertEqual(seo.translit("Артёма"), "artema")
        self.assertEqual(seo.translit("1я александровка"), "1ya-aleksandrovka")
        self.assertEqual(len(self.site.by_slug), len(self.site.index))
        for slug in self.site.by_slug:
            self.assertRegex(slug, r"^[a-z0-9-]+$")

    def test_display_names(self):
        self.assertEqual(seo.display_name("Артёма улица"), "улица Артёма")
        self.assertEqual(seo.display_name("Правды площадьдь"), "площадь Правды")
        self.assertEqual(seo.display_name("Ленинский проспект"), "Ленинский проспект")
        self.assertEqual(seo.in_street("улица Артёма"), "на улице Артёма")
        self.assertEqual(seo.in_street("Ленинский проспект"), "Ленинский проспект")

    def test_every_page_renders_with_head(self):
        paths = self.site.urls()
        paths.remove("/")
        paths.remove("/about")
        for path in paths:
            status, body, ctype = self.site.route(path)
            self.assertEqual(status, 200, path)
            self.assertIn("text/html", ctype)
            self.assertEqual(len(re.findall(r"<h1>", body)), 1, path)
            self.assertIn(f'<link rel="canonical" href="{seo.SITE}{path}">', body, path)
            title = re.search(r"<title>(.*?)</title>", body).group(1)
            self.assertLess(len(title), 90, path)
            self.assertRegex(body, r'<meta name="description" content="[^"]{40,}')
            for block in re.findall(r'<script type="application/ld\+json">(.*?)</script>', body):
                json.loads(block)

    def test_unknown_pages_404_and_other_paths_pass_through(self):
        self.assertEqual(self.site.route("/ulica/net-takoy")[0], 404)
        self.assertEqual(self.site.route("/rayon/net")[0], 404)
        self.assertIsNone(self.site.route("/app.js"))
        self.assertIsNone(self.site.route("/api/live"))

    def test_sitemap_lists_every_url_once(self):
        status, body, ctype = self.site.route("/sitemap.xml")
        self.assertEqual(status, 200)
        root = ElementTree.fromstring(body)
        locs = [el.text for el in root.iter("{http://www.sitemaps.org/schemas/sitemap/0.9}loc")]
        self.assertEqual(len(locs), len(set(locs)))
        self.assertEqual(len(locs), len(self.site.urls()))
        self.assertLess(len(locs), 50000)
        self.assertTrue(all(loc.startswith(seo.SITE + "/") for loc in locs))

    def test_street_page_shows_live_status(self):
        import time

        with store.connect() as conn:
            street = {"key": "артема", "name": "Артёма улица"}
            store.add(conn, street, "off", "button", "кнопка на карте", "r1", at=int(time.time() * 1000) - 5 * 60 * 1000)
        self.site._live = (0, None)
        body = self.site.route("/ulica/artema")[1]
        self.assertIn("Света нет", body)
        self.assertIn("5 мин назад", body)

    def test_phones_page_has_all_districts(self):
        body = self.site.route("/telefony")[1]
        for district in self.site.contacts["districts"]:
            self.assertIn(district["name"], body)
        self.assertIn("FAQPage", body)


if __name__ == "__main__":
    unittest.main()
