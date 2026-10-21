import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from model import MIN_MS, assess

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


if __name__ == "__main__":
    unittest.main()
