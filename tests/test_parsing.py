import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from parsing import status_of, streets_in

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from tg_ingest import to_payload


class ParsingTest(unittest.TestCase):
    def test_off(self):
        self.assertEqual(status_of('Нам пишут: "На ул. Киселева, света нету 4 неделю"'), "off")

    def test_on(self):
        self.assertEqual(status_of("на Артёма дали свет"), "on")

    def test_latest_mark_wins(self):
        self.assertEqual(status_of("света нет, потом дали свет"), "on")

    def test_exception_is_not_a_street_status(self):
        text = "Дали свет во всей округе кроме дома на ул. Университетская, 100б"
        self.assertIsNone(status_of(text))

    def test_water_is_not_light(self):
        text = 'Нам пишут: "На Трудовых Резервов 20, вторую подачу воды фактически нет. Сегодня дали с 20:00 и отключили в 21:00. Люди неделю без воды."'
        self.assertIsNone(status_of(text))

    def test_street_named_like_light_is_not_a_light_word(self):
        self.assertIsNone(status_of("на Светлова отключили воду"))
        self.assertEqual(status_of("на Светлова нет света"), "off")

    def test_district_only_has_no_street(self):
        self.assertEqual(streets_in("в Киевском районе нет света"), [])

    def test_long_news_does_not_become_a_street(self):
        news = (
            '"Донбасстеплоэнерго" сделает всё возможное, чтобы котельные работали даже при отключении света. '
            "Как сообщается, предприятие планирует использовать альтернативные источники электропитания: "
            "уже закуплено 173 дизель-генератора, до конца года их количество увеличится до 510. "
            "В критических ситуациях будут задействованы блочно-модульные котельные. Сейчас их 45, "
            "ожидается поставка ещё 100. Они работают на дизельном топливе."
        )
        self.assertEqual(streets_in(news), [])

    def test_question_is_not_a_report(self):
        self.assertIsNone(to_payload("на Артёма нет света?", "чат", 1, 1, 2, 0))
        self.assertIsNotNone(to_payload("на Артёма нет света", "чат", 1, 2, 2, 0))

    def test_street_found(self):
        keys = [item["key"] for item in streets_in("на Артёма нет света")]
        self.assertIn("артема", keys)


if __name__ == "__main__":
    unittest.main()
