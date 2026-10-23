import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sensors
import store
from model import MIN_MS

NOW = 1_800_000_000_000
STREET = {"key": "артема", "name": "Артёма"}
SALT = "соль"


def sensor(mode="pulse", **fields):
    base = {
        "id": 1,
        "token_hash": "h",
        "mode": mode,
        "key": "артема",
        "name": "Артёма",
        "lon": 37.8,
        "lat": 48.0,
        "radius": 150,
        "last_seen": NOW,
        "misses": 0,
        "state": None,
        "state_at": None,
        "reported_at": None,
        "quiet": 0,
    }
    base.update(fields)
    return base


class AddressTest(unittest.TestCase):
    def test_house_number(self):
        self.assertEqual(sensors.house_number("Артёма 10"), (10, ""))
        self.assertEqual(sensors.house_number("Артёма, 10А"), (10, "а"))
        self.assertEqual(sensors.house_number("9 января 15б"), (15, "б"))
        self.assertIsNone(sensors.house_number("Артёма"))

    def test_standalone_letter_is_not_part_of_number(self):
        self.assertEqual(sensors.house_number("Артёма 10 в Донецке"), (10, ""))

    def test_find_exact_house(self):
        houses = [["1", 37.80, 48.00], ["10", 37.81, 48.01], ["10а", 37.82, 48.02]]
        found = sensors.find_house(houses, (10, "а"))
        self.assertEqual((found["label"], found["exact"]), ("10а", True))

    def test_find_by_number_when_letter_differs(self):
        houses = [["10", 37.81, 48.01]]
        found = sensors.find_house(houses, (10, "б"))
        self.assertEqual((found["label"], found["exact"]), ("10", True))

    def test_find_nearest_number(self):
        houses = [["2", 37.80, 48.00], ["20", 37.81, 48.01]]
        found = sensors.find_house(houses, (18, ""))
        self.assertEqual((found["label"], found["exact"]), ("20", False))

    def test_same_number_far_apart_is_ambiguous(self):
        houses = [["5", 37.80, 48.00], ["5", 37.90, 48.00]]
        self.assertEqual(sensors.find_house(houses, (5, "")), {"ambiguous": True})

    def test_no_houses(self):
        self.assertIsNone(sensors.find_house([], (5, "")))

    def test_snap_stays_near_and_hides_the_house(self):
        lon, lat = sensors.snap(37.80012, 48.00031)
        self.assertLess(abs(lon - 37.80012), 0.0006)
        self.assertLess(abs(lat - 48.00031), 0.0004)
        self.assertNotEqual((lon, lat), (37.80012, 48.00031))
        self.assertEqual(sensors.snap(lon, lat), (lon, lat))

    def test_only_public_ipv4_can_be_pinged(self):
        self.assertEqual(sensors.global_ipv4("8.8.8.8"), "8.8.8.8")
        for bad in ("192.168.1.1", "10.0.0.5", "127.0.0.1", "100.64.1.1", "2001:4860:4860::8888", "мусор", ""):
            self.assertIsNone(sensors.global_ipv4(bad), bad)


class DeriveTest(unittest.TestCase):
    def test_never_heard_is_unknown(self):
        self.assertIsNone(sensors.derive(sensor(last_seen=None), NOW))

    def test_pulse_fresh_and_lost(self):
        self.assertEqual(sensors.derive(sensor(last_seen=NOW - 2 * MIN_MS), NOW), "on")
        self.assertEqual(sensors.derive(sensor(last_seen=NOW - 5 * MIN_MS), NOW), "off")

    def test_ping_needs_several_misses(self):
        self.assertEqual(sensors.derive(sensor("ping", misses=0), NOW), "on")
        self.assertIsNone(sensors.derive(sensor("ping", misses=1), NOW))
        self.assertEqual(sensors.derive(sensor("ping", misses=2), NOW), "off")


class PlanTest(unittest.TestCase):
    def test_first_beat_reports_on(self):
        self.assertEqual(sensors.plan(sensor(), NOW), ("on", NOW, True))

    def test_going_off_is_dated_by_last_seen(self):
        row = sensor(state="on", last_seen=NOW - 6 * MIN_MS, reported_at=NOW - 30 * MIN_MS)
        self.assertEqual(sensors.plan(row, NOW), ("off", NOW - 6 * MIN_MS, True))

    def test_off_is_not_older_than_previous_report(self):
        row = sensor(state="on", last_seen=NOW - 6 * MIN_MS, reported_at=NOW - 2 * MIN_MS)
        status, at, changed = sensors.plan(row, NOW)
        self.assertEqual((status, changed), ("off", True))
        self.assertGreater(at, row["reported_at"])
        self.assertLessEqual(at, NOW)

    def test_same_state_is_refreshed_after_a_while(self):
        row = sensor(state="on", reported_at=NOW - 25 * MIN_MS)
        self.assertEqual(sensors.plan(row, NOW), ("on", NOW, False))
        row = sensor(state="on", reported_at=NOW - 5 * MIN_MS)
        self.assertIsNone(sensors.plan(row, NOW))

    def test_hold_blocks_off_but_not_on(self):
        lost = sensor(state="on", last_seen=NOW - 6 * MIN_MS, reported_at=NOW - 30 * MIN_MS)
        self.assertIsNone(sensors.plan(lost, NOW, hold=True))
        self.assertEqual(sensors.plan(sensor(state="off"), NOW, hold=True)[0], "on")

    def test_quiet_sensor_is_silent_but_wakes_up(self):
        quiet = sensor(state="off", last_seen=NOW - 6 * MIN_MS, reported_at=NOW - 90 * MIN_MS, quiet=1)
        self.assertIsNone(sensors.plan(quiet, NOW))
        self.assertEqual(sensors.plan(dict(quiet, last_seen=NOW), NOW)[0], "on")


class DatabaseTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.old_db = store.DB
        store.DB = Path(self.dir.name) / "test.db"
        store.init()

    def tearDown(self):
        store.DB = self.old_db
        self.dir.cleanup()

    def marks(self, conn):
        return [dict(row) for row in conn.execute("SELECT * FROM reports ORDER BY at, id")]

    def test_pulse_life_cycle(self):
        with store.connect() as conn:
            token = sensors.create(conn, "pulse", STREET, 37.8001, 48.0001, "o", NOW)
            self.assertEqual(sensors.tick(conn, NOW, SALT), {})
            self.assertEqual(sensors.beat(conn, token, NOW, SALT), {"state": "on"})
            # лишний пульс сразу после первого ничего не пишет
            sensors.beat(conn, token, NOW + 1000, SALT)
            self.assertEqual(len(self.marks(conn)), 1)
            later = NOW + 10 * MIN_MS
            done = sensors.tick(conn, later, SALT)
            self.assertEqual(list(done.values()), ["off"])
            marks = self.marks(conn)
            self.assertEqual([m["status"] for m in marks], ["on", "off"])
            self.assertTrue(all(m["kind"] == "sensor" and m["radius"] == sensors.RADIUS for m in marks))
            self.assertEqual(sensors.beat(conn, token, later + MIN_MS, SALT), {"state": "on"})
            self.assertEqual([m["status"] for m in self.marks(conn)], ["on", "off", "on"])

    def test_ping_goes_off_only_after_misses(self):
        with store.connect() as conn:
            token = sensors.create(conn, "ping", STREET, 37.8, 48.0, "o", NOW, ip="8.8.8.8", alive=True)
            sensor_id = sensors.by_token(conn, token)["id"]
            self.assertEqual(list(sensors.tick(conn, NOW, SALT).values()), ["on"])
            sensors.record_ping(conn, sensor_id, False, NOW + 5 * MIN_MS)
            self.assertEqual(sensors.tick(conn, NOW + 5 * MIN_MS, SALT), {})
            sensors.record_ping(conn, sensor_id, False, NOW + 10 * MIN_MS)
            self.assertEqual(list(sensors.tick(conn, NOW + 10 * MIN_MS, SALT).values()), ["off"])
            sensors.record_ping(conn, sensor_id, True, NOW + 15 * MIN_MS)
            self.assertEqual(list(sensors.tick(conn, NOW + 15 * MIN_MS, SALT).values()), ["on"])

    def test_hold_writes_no_off(self):
        with store.connect() as conn:
            token = sensors.create(conn, "pulse", STREET, 37.8, 48.0, "o", NOW)
            sensors.beat(conn, token, NOW, SALT)
            self.assertEqual(sensors.tick(conn, NOW + 10 * MIN_MS, SALT, hold=True), {})
            self.assertEqual(len(self.marks(conn)), 1)

    def test_long_off_goes_quiet(self):
        with store.connect() as conn:
            token = sensors.create(conn, "pulse", STREET, 37.8, 48.0, "o", NOW)
            sensors.beat(conn, token, NOW, SALT)
            sensors.tick(conn, NOW + 10 * MIN_MS, SALT)
            sensors.tick(conn, NOW + sensors.GIVE_UP_MS + 20 * MIN_MS, SALT)
            self.assertEqual(sensors.by_token(conn, token)["quiet"], 1)
            self.assertEqual(store.sensor_counts(conn), {"on": 0, "off": 0})

    def test_remove_drops_the_sensor_but_keeps_history(self):
        with store.connect() as conn:
            token = sensors.create(conn, "ping", STREET, 37.8, 48.0, "o", NOW, ip="8.8.8.8", alive=True)
            sensors.tick(conn, NOW, SALT)
            self.assertTrue(sensors.ip_taken(conn, "8.8.8.8"))
            self.assertTrue(sensors.remove(conn, token))
            self.assertFalse(sensors.remove(conn, token))
            self.assertFalse(sensors.ip_taken(conn, "8.8.8.8"))
            self.assertEqual(len(self.marks(conn)), 1)

    def test_watch_sensor_is_silent_until_ip_is_confirmed(self):
        with store.connect() as conn:
            token = sensors.create(conn, "ping", STREET, 37.8, 48.0, "o", NOW, ip="8.8.8.8", confirmed=False)
            sensor_id = sensors.by_token(conn, token)["id"]
            self.assertEqual(sensors.due_pings(conn, NOW + 10 * MIN_MS), [])
            self.assertEqual(sensors.tick(conn, NOW + 10 * MIN_MS, SALT), {})
            self.assertEqual(store.sensor_counts(conn), {"on": 0, "off": 0})
            soon = sensors.check(conn, token, "8.8.8.8", NOW + 60 * MIN_MS)
            self.assertEqual((soon["state"], soon["same_ip"]), ("probation", True))
            elsewhere = sensors.check(conn, token, "5.5.5.5", NOW + sensors.CONFIRM_MS + MIN_MS)
            self.assertEqual((elsewhere["state"], elsewhere["same_ip"]), ("probation", False))
            late = sensors.check(conn, token, "8.8.8.8", NOW + sensors.CONFIRM_MS + MIN_MS)
            self.assertEqual(late["state"], "active")
            self.assertEqual([row["id"] for row in sensors.due_pings(conn, NOW + sensors.CONFIRM_MS + MIN_MS)], [sensor_id])
            sensors.record_ping(conn, sensor_id, True, NOW + sensors.CONFIRM_MS + MIN_MS)
            self.assertEqual(
                list(sensors.tick(conn, NOW + sensors.CONFIRM_MS + MIN_MS, SALT).values()), ["on"]
            )

    def test_pings_are_spaced_by_five_minutes(self):
        with store.connect() as conn:
            token = sensors.create(conn, "ping", STREET, 37.8, 48.0, "o", NOW, ip="8.8.8.8", alive=True)
            sensor_id = sensors.by_token(conn, token)["id"]
            self.assertEqual(len(sensors.due_pings(conn, NOW)), 1)
            sensors.record_ping(conn, sensor_id, True, NOW)
            self.assertEqual(sensors.due_pings(conn, NOW + 2 * MIN_MS), [])
            self.assertEqual(len(sensors.due_pings(conn, NOW + 5 * MIN_MS)), 1)

    def test_reenroll_restarts_probation_and_keeps_token(self):
        with store.connect() as conn:
            token = sensors.create(conn, "ping", STREET, 37.8, 48.0, "o", NOW, ip="8.8.8.8", alive=True)
            other = {"key": "кирова", "name": "Кирова"}
            self.assertTrue(sensors.reenroll(conn, token, other, 37.7, 48.1, "1.1.1.1", NOW + MIN_MS))
            row = sensors.by_token(conn, token)
            self.assertEqual((row["key"], row["ip"], row["confirmed"], row["state"]), ("кирова", "1.1.1.1", 0, None))
            self.assertFalse(sensors.reenroll(conn, "нет такого", other, 37.7, 48.1, "1.1.1.1", NOW))

    def test_check_unknown_or_pulse_token(self):
        with store.connect() as conn:
            pulse = sensors.create(conn, "pulse", STREET, 37.8, 48.0, "o", NOW)
            self.assertEqual(sensors.check(conn, pulse, "8.8.8.8", NOW)["state"], "gone")
            self.assertEqual(sensors.check(conn, "нет", "8.8.8.8", NOW)["state"], "gone")

    def test_sensor_marks_appear_in_live_places(self):
        import time

        now = int(time.time() * 1000)
        with store.connect() as conn:
            token = sensors.create(conn, "pulse", STREET, 37.8, 48.0, "o", now)
            sensors.beat(conn, token, now, SALT)
            state = store.live(conn, now)
            self.assertEqual(len(state["places"]), 1)
            self.assertEqual(state["places"][0]["status"], "on")
            self.assertEqual(state["sensors"], {"on": 1, "off": 0})


if __name__ == "__main__":
    unittest.main()
