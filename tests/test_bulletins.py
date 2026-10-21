import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bulletins import classify, merge, restored_at, MIN_MS


def row(kind, minutes, source="к1", districts=()):
    return {"kind": kind, "at": minutes * MIN_MS, "source": source, "districts": list(districts), "text": kind}


class ClassifyTest(unittest.TestCase):
    def kind(self, text):
        found = classify(text)
        return found["kind"] if found else None

    def test_all_powered(self):
        text = 'По информации "Донецкэнерго", во всех районах Донецка все трансформаторные подстанции запитаны'
        self.assertEqual(self.kind(text), "all_on")

    def test_district_progress_is_not_all(self):
        self.assertIsNone(classify("В Киевском районе Донецка запитаны 222 трансформаторные подстанции"))

    def test_all_with_exception_is_not_all(self):
        self.assertIsNone(classify("В Донецке запитаны все подстанции, кроме двух в Киевском районе"))

    def test_partial_off_with_districts(self):
        found = classify("Частично обесточены Ворошиловский, Киевский, Калининский районы Донецка")
        self.assertEqual(found["kind"], "partial_off")
        self.assertEqual(found["districts"], ["Ворошиловский", "Киевский", "Калининский"])

    def test_some_districts_lost_light(self):
        found = classify("В некоторых районах Донецка пропал свет")
        self.assertEqual(found, {"kind": "partial_off", "districts": []})

    def test_district_outage_count(self):
        found = classify("в Киевском районе обесточены 272 трансформаторные подстанции")
        self.assertEqual(found["kind"], "partial_off")
        self.assertEqual(found["districts"], ["Киевский"])

    def test_other_city_district_is_ignored(self):
        self.assertIsNone(classify("Макеевка: частично обесточены Кировский и Советский районы"))

    def test_chatter_is_ignored(self):
        self.assertIsNone(classify("На Артёма нет света"))
        self.assertIsNone(classify(""))


class MergeTest(unittest.TestCase):
    def test_same_bulletin_from_many_channels_is_one(self):
        rows = [row("all_on", 100, "к1"), row("all_on", 105, "к2"), row("all_on", 170, "к3")]
        clusters = merge(rows)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0]["at"], 100 * MIN_MS)
        self.assertEqual(clusters[0]["sources"], 3)

    def test_new_outage_after_restore_cancels_it(self):
        clusters = merge([row("all_on", 100), row("partial_off", 200)])
        self.assertIsNone(restored_at(clusters))

    def test_restore_after_outage(self):
        clusters = merge([row("partial_off", 50), row("all_on", 100)])
        self.assertEqual(restored_at(clusters), 100 * MIN_MS)

    def test_no_bulletins(self):
        self.assertIsNone(restored_at(merge([])))


if __name__ == "__main__":
    unittest.main()
