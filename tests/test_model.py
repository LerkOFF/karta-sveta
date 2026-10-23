import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from model import MIN_MS, assess, assess_places, clean_radius, distance_m, valid_point

NOW = 1_800_000_000_000


def row(minutes_ago, status="off", reporter="a", kind="chat", key="артема"):
    return {
        "key": key,
        "name": "Артёма",
        "status": status,
        "at": NOW - int(minutes_ago * MIN_MS),
        "kind": kind,
        "reporter": reporter,
        "source": "чат",
    }


class ExactTest(unittest.TestCase):
    def test_only_official_and_sensor_are_exact(self):
        for kind, exact in (("official", True), ("sensor", True), ("chat", False), ("channel", False), ("button", False), ("comment", False)):
            self.assertEqual(assess([row(1, kind=kind)], NOW)["exact"], exact, kind)

    def test_newest_report_decides_exactness(self):
        rows = [row(30, kind="sensor", reporter="s"), row(2, kind="chat", reporter="c")]
        self.assertFalse(assess(rows, NOW)["exact"])

    def test_places_carry_exactness(self):
        place = dict(row(1, kind="sensor"), lon=37.8, lat=48.0, radius=100, text="")
        self.assertTrue(assess_places([place], NOW)[0]["exact"])


class AssessTest(unittest.TestCase):
    def test_empty(self):
        self.assertIsNone(assess([], NOW))

    def test_older_than_day_is_dropped(self):
        self.assertIsNone(assess([row(25 * 60)], NOW))

    def test_fresh_report_is_sure(self):
        result = assess([row(1)], NOW)
        self.assertEqual(result["status"], "off")
        self.assertEqual(result["level"], "sure")

    def test_confidence_fades_with_age(self):
        levels = [assess([row(m)], NOW)["level"] for m in (1, 300, 1000)]
        self.assertEqual(levels, ["sure", "likely", "stale"])

    def test_on_fades_faster_than_off(self):
        on = assess([row(120, "on")], NOW)["confidence"]
        off = assess([row(120, "off")], NOW)["confidence"]
        self.assertLess(on, off)

    def test_newest_report_wins(self):
        result = assess([row(300, "off", "a"), row(5, "on", "b")], NOW)
        self.assertEqual(result["status"], "on")

    def test_second_person_raises_confidence(self):
        alone = assess([row(120, "off", "a")], NOW)
        two = assess([row(120, "off", "a"), row(100, "off", "b")], NOW)
        self.assertGreater(two["confidence"], alone["confidence"])
        self.assertEqual(two["sources"], 2)

    def test_same_person_counts_once(self):
        alone = assess([row(120, "off", "a")], NOW)
        spam = assess([row(120, "off", "a"), row(110, "off", "a"), row(100, "off", "a")], NOW)
        self.assertEqual(alone["sources"], spam["sources"])

    def test_dispute_lowers_confidence(self):
        calm = assess([row(10, "off", "a")], NOW)
        dispute = assess([row(20, "on", "b"), row(10, "off", "a")], NOW)
        self.assertTrue(dispute["conflict"])
        self.assertLess(dispute["confidence"], calm["confidence"])

    def test_button_trusted_less_than_chat(self):
        chat = assess([row(10, kind="chat")], NOW)["confidence"]
        button = assess([row(10, kind="button")], NOW)["confidence"]
        self.assertLess(button, chat)

    def test_old_off_is_weakened_by_restore_bulletin(self):
        plain = assess([row(120, "off")], NOW)
        restored = assess([row(120, "off")], NOW, restored_at=NOW - 30 * MIN_MS)
        self.assertLess(restored["confidence"], plain["confidence"])
        self.assertEqual(restored["superseded_at"], NOW - 30 * MIN_MS)

    def test_off_after_restore_bulletin_is_untouched(self):
        plain = assess([row(10, "off")], NOW)
        restored = assess([row(10, "off")], NOW, restored_at=NOW - 30 * MIN_MS)
        self.assertEqual(restored["confidence"], plain["confidence"])
        self.assertIsNone(restored["superseded_at"])

    def test_on_is_not_touched_by_restore_bulletin(self):
        restored = assess([row(120, "on")], NOW, restored_at=NOW - 30 * MIN_MS)
        self.assertIsNone(restored["superseded_at"])

    def test_report_from_the_future_is_age_zero(self):
        result = assess([row(-5)], NOW)
        self.assertEqual(result["age_min"], 0)

def place_row(minutes_ago, lon, lat, status="off", reporter="a", radius=200):
    item = row(minutes_ago, status=status, reporter=reporter)
    item.update({"lon": lon, "lat": lat, "radius": radius})
    return item


class PlacesTest(unittest.TestCase):
    def test_distance(self):
        self.assertAlmostEqual(distance_m(37.8, 48.0, 37.8, 48.001), 110.5, delta=1)

    def test_valid_point(self):
        self.assertTrue(valid_point(37.8, 48.0))
        self.assertFalse(valid_point(30.5, 50.4))
        self.assertFalse(valid_point("37.8", 48.0))

    def test_far_apart_marks_are_two_places(self):
        rows = [place_row(5, 37.80, 48.00, "off"), place_row(10, 37.80, 48.02, "on", reporter="b")]
        places = assess_places(rows, NOW)
        self.assertEqual([p["status"] for p in places], ["on", "off"])

    def test_near_marks_merge_and_agree(self):
        rows = [place_row(5, 37.8000, 48.0000, "off"), place_row(10, 37.8005, 48.0005, "off", reporter="b")]
        places = assess_places(rows, NOW)
        self.assertEqual(len(places), 1)
        self.assertEqual(places[0]["sources"], 2)

    def test_newest_mark_sets_the_place(self):
        rows = [place_row(60, 37.8, 48.0, "off"), place_row(2, 37.8, 48.0, "on", reporter="b")]
        places = assess_places(rows, NOW)
        self.assertEqual(len(places), 1)
        self.assertEqual(places[0]["status"], "on")
        self.assertEqual(places[0]["radius"], 200)

    def test_clean_radius(self):
        self.assertEqual(clean_radius(200), 200)
        self.assertEqual(clean_radius(380.4), 380)
        self.assertEqual(clean_radius(5), 30)
        self.assertEqual(clean_radius(99999), 2000)
        self.assertEqual(clean_radius("x"), 100)
        self.assertEqual(clean_radius(None), 100)

    def test_old_marks_dropped(self):
        self.assertEqual(assess_places([place_row(25 * 60, 37.8, 48.0)], NOW), [])


if __name__ == "__main__":
    unittest.main()