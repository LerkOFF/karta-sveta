"""Датчики-«пульс»: устройство жителя, по которому видно, есть ли у него дома свет.

Два режима:
- ping: человек нажимает на карте «Свет есть» и «Следить за этим местом»; сервер запоминает
  его белый IP и раз в 5 минут пингует. Сутки датчик на испытании: пока тот же человек не
  зашёл с тем же IP через сутки, адрес не считается статическим и датчик молчит;
- pulse: устройство раз в минуту дёргает /api/pulse/<токен>, адрес не нужен.

Датчик пишет обычные отметки kind="sensor" по куску улицы, дальше их считает model.py.
Чистая логика (snap, find_house, derive, plan) не трогает базу, функции с conn пишут в неё.
"""

import hashlib
import ipaddress
import math
import re
import secrets
import subprocess
import sys

import store
from model import MIN_MS, distance_m

# Радиус куска улицы вокруг датчика, метры.
RADIUS = 150

# Точка датчика округляется до клетки около 75 м: дом жителя на карте и в API не виден.
CELL = (0.001, 0.0007)

# Пульс раз в минуту; тишина дольше этого времени считается «света нет».
PULSE_LOST_MS = 4 * MIN_MS

# Как часто пингуем и сколько проверок подряд без ответа считаются «света нет» (10 минут).
PING_EVERY_MS = 5 * MIN_MS
PING_MISS_LIMIT = 2

# Испытание ping-датчика: IP считается статическим, если тот же человек (по токену в браузере)
# пришёл с тем же IP не раньше чем через столько после подключения.
CONFIRM_MS = 24 * 60 * MIN_MS

# Пока состояние не меняется, отметку повторяем: уверенность в ней падает вдвое за 90 минут и 4 часа.
REFRESH_MS = 20 * MIN_MS

# Дольше трёх суток «нет» подряд: устройство, скорее всего, выключили. Отметки прекращаются,
# вернётся ответ - датчик оживёт сам.
GIVE_UP_MS = 72 * 60 * MIN_MS

# С одного адреса можно подключить не больше датчиков, чтобы карту нельзя было залить выдуманными.
MAX_PER_OWNER = 3

# Пульс чаще этого интервала только обновляет время, лишней работы не вызывает.
MIN_BEAT_GAP_MS = 5000

SOURCE = "датчик"


def new_token() -> str:
    return secrets.token_urlsafe(16)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def snap(lon: float, lat: float):
    """Центр клетки сетки, в которую попала точка."""
    cx, cy = CELL
    return round((math.floor(lon / cx) + 0.5) * cx, 5), round((math.floor(lat / cy) + 0.5) * cy, 5)


def house_number(text: str):
    """Последний номер дома в тексте адреса: «Артёма, 10а» -> (10, 'а'). Нет номера - None."""
    found = re.findall(r"(\d+)([а-яёa-z]?)(?![а-яёa-z\d])", text.lower())
    if not found:
        return None
    number, letter = found[-1]
    return int(number), letter


def _label_ref(label: str):
    match = re.match(r"\s*(\d+)\s*([а-яёa-z]?)", label.lower())
    return (int(match.group(1)), match.group(2)) if match else None


# Дальше этого друг от друга дома с одним номером на одной улице считаются разными местами.
AMBIGUOUS_M = 800


def find_house(houses, number):
    """houses: [[номер, lon, lat], ...] одной улицы; number: (число, литера) из house_number.

    Возвращает {"label", "lon", "lat", "exact"} или {"ambiguous": True}, если такой номер стоит
    в разных концах улицы, или None, если на улице нет ни одного дома с номером.
    exact: нашёлся именно этот дом (литера может не совпасть), иначе взят ближайший по номеру.
    """
    refs = [(ref, row) for row in houses for ref in [_label_ref(str(row[0]))] if ref]
    if not refs:
        return None
    want_num, want_letter = number
    same = [row for ref, row in refs if ref == (want_num, want_letter)]
    exact = True
    if not same:
        same = [row for ref, row in refs if ref[0] == want_num]
    if not same:
        exact = False
        best = min(abs(ref[0] - want_num) for ref, _ in refs)
        same = [row for ref, row in refs if abs(ref[0] - want_num) == best]
    first = same[0]
    if any(distance_m(first[1], first[2], row[1], row[2]) > AMBIGUOUS_M for row in same[1:]):
        return {"ambiguous": True}
    return {"label": str(first[0]), "lon": first[1], "lat": first[2], "exact": exact}


def global_ipv4(raw: str):
    """Белый IPv4 как строка или None: частные, служебные и CGNAT-адреса пинговать нельзя."""
    try:
        addr = ipaddress.ip_address(raw.strip())
    except ValueError:
        return None
    if addr.version != 4 or not addr.is_global or addr.is_multicast:
        return None
    return str(addr)


def ping(ip: str, count: int = 2, wait: int = 2) -> bool:
    """True, если хоть один из пакетов вернулся. ip должен быть проверен через global_ipv4."""
    ip = str(ipaddress.ip_address(ip))
    # На macOS -W в миллисекундах, на Linux в секундах.
    timeout = str(wait * 1000) if sys.platform == "darwin" else str(wait)
    try:
        done = subprocess.run(
            ["ping", "-c", str(count), "-W", timeout, "-n", "-q", ip],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=count * wait + 5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return done.returncode == 0


def derive(sensor, now_ms: int):
    """Что говорят данные датчика: "on", "off" или None («пока не ясно, оставить как было»)."""
    if sensor["last_seen"] is None:
        return None
    if sensor["mode"] == "ping":
        if sensor["misses"] == 0:
            return "on"
        if sensor["misses"] >= PING_MISS_LIMIT:
            return "off"
        return None
    return "on" if now_ms - sensor["last_seen"] <= PULSE_LOST_MS else "off"


def plan(sensor, now_ms: int, hold: bool = False):
    """Нужна ли новая отметка. None или (status, at, changed).

    hold: нашей сети или службы верить нельзя, «нет» и повторы не пишем. «Есть» пишем всегда:
    ответ устройства - прямое свидетельство.
    """
    state = sensor["state"]
    want = derive(sensor, now_ms) or state
    if want is None:
        return None
    if hold and want != "on":
        return None
    reported = sensor["reported_at"] or 0
    if want != state:
        if want == "off":
            # Отключилось примерно тогда, когда мы слышали в последний раз, но не раньше прошлой отметки.
            at = min(now_ms, max(sensor["last_seen"] or now_ms, reported + 1))
        else:
            at = now_ms
        return want, at, True
    if not sensor["quiet"] and now_ms - reported >= REFRESH_MS:
        return want, now_ms, False
    return None


def settle(conn, sensor, now_ms: int, salt: str, hold: bool = False):
    """Применяет plan к одному датчику: пишет отметку и обновляет его состояние."""
    if not sensor["confirmed"]:
        return None
    if sensor["state"] == "off" and not sensor["quiet"] and now_ms - (sensor["state_at"] or now_ms) > GIVE_UP_MS:
        conn.execute("UPDATE sensors SET quiet = 1 WHERE id = ?", (sensor["id"],))
        sensor["quiet"] = 1
    step = plan(sensor, now_ms, hold)
    if step:
        status, at, changed = step
        store.add(
            conn,
            {"key": sensor["key"], "name": sensor["name"]},
            status,
            "sensor",
            SOURCE,
            store.reporter_id(salt, "sensor", sensor["token_hash"]),
            at=at,
            place=(sensor["lon"], sensor["lat"], sensor["radius"]),
        )
        if changed:
            conn.execute(
                "UPDATE sensors SET state = ?, state_at = ?, reported_at = ?, quiet = 0 WHERE id = ?",
                (status, at, at, sensor["id"]),
            )
            sensor.update(state=status, state_at=at, reported_at=at, quiet=0)
        else:
            conn.execute("UPDATE sensors SET reported_at = ? WHERE id = ?", (at, sensor["id"]))
            sensor["reported_at"] = at
        return status
    return None


# Авария провайдера, а не света: в одной сети (ASN) не меньше PROVIDER_MIN датчиков, не отвечает
# PROVIDER_SHARE из них, и у большинства упавших рядом (NEIGHBOR_M) живёт датчик другой сети.
PROVIDER_MIN = 3
PROVIDER_SHARE = 0.8
PROVIDER_NEIGHBORS = 0.6
NEIGHBOR_M = 1500


def provider_suspects(rows):
    """rows: подтверждённые ping-датчики (dict с id, asn, misses, last_seen, lon, lat).

    Возвращает множество id упавших датчиков, которые, похоже, потеряла сеть провайдера, а не свет.
    """
    groups = {}
    for row in rows:
        if row["asn"]:
            groups.setdefault(row["asn"], []).append(row)
    alive = [row for row in rows if row["last_seen"] is not None and row["misses"] == 0]
    suspects = set()
    for asn, members in groups.items():
        failing = [row for row in members if row["misses"] >= 1]
        if len(members) < PROVIDER_MIN or len(failing) / len(members) < PROVIDER_SHARE:
            continue
        backed = [
            row
            for row in failing
            if any(
                other["asn"] != asn and distance_m(row["lon"], row["lat"], other["lon"], other["lat"]) <= NEIGHBOR_M
                for other in alive
            )
        ]
        if len(backed) / len(failing) >= PROVIDER_NEIGHBORS:
            suspects.update(row["id"] for row in failing)
    return suspects


def by_token(conn, token: str):
    row = conn.execute("SELECT * FROM sensors WHERE token_hash = ?", (token_hash(token),)).fetchone()
    return dict(row) if row else None


def owner_count(conn, owner: str) -> int:
    return conn.execute("SELECT COUNT(*) FROM sensors WHERE owner = ?", (owner,)).fetchone()[0]


def ip_taken(conn, ip: str) -> bool:
    return conn.execute("SELECT 1 FROM sensors WHERE ip = ?", (ip,)).fetchone() is not None


def create(
    conn,
    mode: str,
    street,
    lon: float,
    lat: float,
    owner: str,
    now_ms: int,
    ip=None,
    alive: bool = False,
    confirmed: bool = True,
    asn=None,
):
    """Заводит датчик и возвращает токен (в базе только его хеш). alive: ответ уже получен.

    confirmed=False: датчик на испытании, ничего не пишет, пока check не подтвердит IP.
    """
    token = new_token()
    lon, lat = snap(lon, lat)
    conn.execute(
        "INSERT INTO sensors (token_hash, mode, key, name, lon, lat, radius, ip, owner, created_at, last_seen,"
        " confirmed, ip_seen_at, asn) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            token_hash(token), mode, street["key"], street["name"], lon, lat, RADIUS, ip, owner, now_ms,
            now_ms if alive else None, 1 if confirmed else 0, now_ms if ip else None, asn,
        ),
    )
    return token


def reenroll(conn, token: str, street, lon: float, lat: float, ip: str, now_ms: int, asn=None) -> bool:
    """Тот же человек (токен) подключает ping-датчик заново: другое место или другой IP, снова испытание."""
    lon, lat = snap(lon, lat)
    done = conn.execute(
        "UPDATE sensors SET key = ?, name = ?, lon = ?, lat = ?, ip = ?, created_at = ?, ip_seen_at = ?,"
        " last_seen = NULL, misses = 0, state = NULL, state_at = NULL, reported_at = NULL,"
        " quiet = 0, confirmed = 0, last_ping = NULL, asn = ? WHERE token_hash = ? AND mode = 'ping'",
        (street["key"], street["name"], lon, lat, ip, now_ms, now_ms, asn, token_hash(token)),
    )
    return done.rowcount > 0


def check(conn, token: str, ip: str, now_ms: int, cellular: bool = False):
    """Человек с токеном открыл карту. Подтверждает IP, если он тот же через сутки после подключения.

    Возвращает {"state": "gone"} или {"state": "probation" | "active", "sensor": "on" | "off" | None,
    "ready_at": когда можно ждать включения}. Другой IP не вредит: человек мог зайти с мобильного.
    cellular: браузер сообщил, что человек сейчас на мобильной сети; такой заход IP не подтверждает.
    """
    sensor = by_token(conn, token)
    if not sensor or sensor["mode"] != "ping":
        return {"state": "gone"}
    if sensor["ip"] and ip == sensor["ip"] and not cellular:
        conn.execute("UPDATE sensors SET ip_seen_at = ? WHERE id = ?", (now_ms, sensor["id"]))
        if not sensor["confirmed"] and now_ms - sensor["created_at"] >= CONFIRM_MS:
            conn.execute("UPDATE sensors SET confirmed = 1, last_ping = NULL WHERE id = ?", (sensor["id"],))
            sensor["confirmed"] = 1
    return {
        "state": "active" if sensor["confirmed"] else "probation",
        "sensor": sensor["state"],
        "ready_at": sensor["created_at"] + CONFIRM_MS,
        "same_ip": bool(sensor["ip"] and ip == sensor["ip"] and not cellular),
    }


def beat(conn, token: str, now_ms: int, salt: str):
    """Пульс от устройства. None, если такого датчика нет или он не в режиме пульса."""
    sensor = by_token(conn, token)
    if not sensor or sensor["mode"] != "pulse":
        return None
    if sensor["last_seen"] is None or now_ms - sensor["last_seen"] >= MIN_BEAT_GAP_MS:
        conn.execute("UPDATE sensors SET last_seen = ?, misses = 0 WHERE id = ?", (now_ms, sensor["id"]))
        sensor.update(last_seen=now_ms, misses=0)
        settle(conn, sensor, now_ms, salt)
    return {"state": sensor["state"]}


def due_pings(conn, now_ms: int):
    """Подтверждённые ping-датчики, которых пора проверить: [{id, ip}, ...]."""
    rows = conn.execute(
        "SELECT id, ip FROM sensors WHERE mode = 'ping' AND confirmed = 1 AND ip IS NOT NULL"
        " AND (last_ping IS NULL OR last_ping <= ?)",
        (now_ms - PING_EVERY_MS + 5000,),
    )
    return [dict(row) for row in rows]


def record_ping(conn, sensor_id: int, ok: bool, now_ms: int):
    if ok:
        conn.execute("UPDATE sensors SET last_seen = ?, misses = 0, last_ping = ? WHERE id = ?", (now_ms, now_ms, sensor_id))
    else:
        conn.execute("UPDATE sensors SET misses = misses + 1, last_ping = ? WHERE id = ?", (now_ms, sensor_id))


def tick(conn, now_ms: int, salt: str, hold: bool = False, hold_ids=()):
    """Один проход по всем датчикам. Возвращает {id: новый статус} для тех, где была отметка.

    hold_ids: датчики, которым «нет» не пишем (подозрение на аварию провайдера).
    """
    done = {}
    for row in conn.execute("SELECT * FROM sensors").fetchall():
        status = settle(conn, dict(row), now_ms, salt, hold or row["id"] in hold_ids)
        if status:
            done[row["id"]] = status
    return done


def remove(conn, token: str) -> bool:
    """Удаляет датчик вместе с IP. Старые отметки остаются в истории без привязки к человеку."""
    return conn.execute("DELETE FROM sensors WHERE token_hash = ?", (token_hash(token),)).rowcount > 0
