#!/usr/bin/env python3
"""Сервер карты: раздаёт страницу, принимает отметки и считает статус улиц.

Запуск:  python3 server.py
Адрес:   KARTA_HOST (по умолчанию 127.0.0.1) и KARTA_PORT (по умолчанию 8765).
"""

import hmac
import json
import mimetypes
import os
import threading
import time
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import bulletins
import sensors
import store
from model import HORIZON_MIN, MIN_MS, clean_radius, valid_point
from parsing import KEYS, status_of, streets_in

ROOT = Path(__file__).resolve().parent
MAX_BODY = 16 * 1024

# Наружу отдаётся только это. База, секреты и код остаются закрыты.
PUBLIC_FILES = {
    "/": "index.html",
    "/index.html": "index.html",
    "/app.js": "app.js",
    "/styles.css": "styles.css",
    "/data/streets.geojson": "data/streets.geojson",
    "/data/streets-index.json": "data/streets-index.json",
    "/data/kyiv-queues.json": "data/kyiv-queues.json",
    "/data/sources.json": "data/sources.json",
    "/data/contacts.json": "data/contacts.json",
    "/about": "about.html",
    "/about.html": "about.html",
    "/about.js": "about.js",
    "/favicon.ico": "favicon.ico",
    "/favicon.svg": "favicon.svg",
    "/favicon-120.png": "favicon-120.png",
    "/apple-touch-icon.png": "apple-touch-icon.png",
    "/robots.txt": "robots.txt",
    "/sitemap.xml": "sitemap.xml",
}
LONG_CACHE = ("/vendor/", "/data/streets")

mimetypes.add_type("application/geo+json", ".geojson")
mimetypes.add_type("text/javascript", ".js")

STREET_BY_KEY = {key: name for key, name in KEYS}

# Не больше 30 запросов с одного адреса за 10 минут на публичные POST.
RATE_LIMIT = 30
RATE_WINDOW = 10 * 60
_hits = defaultdict(deque)
_hits_lock = threading.Lock()

def load_houses():
    path = ROOT / "data" / "houses.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


# {ключ улицы: [[номер, lon, lat], ...]}: см. scripts/build_houses.py
HOUSES = load_houses()

SECRET = store.load_secret()
store.init()


def rate_ok(ip: str, limit: int = RATE_LIMIT, window: int = RATE_WINDOW) -> bool:
    """Не больше limit запросов за window секунд. ip - любой ключ счётчика."""
    now = time.time()
    with _hits_lock:
        queue = _hits[ip]
        while queue and now - queue[0] > window:
            queue.popleft()
        if len(queue) >= limit:
            return False
        queue.append(now)
        return True


def message_time(raw):
    """Время сообщения из чата: секунды или миллисекунды, не из будущего, не старше суток."""
    now = int(time.time() * 1000)
    if raw in (None, ""):
        return now
    try:
        value = int(float(raw))
    except (TypeError, ValueError):
        return now
    if value < 10**11:
        value *= 1000
    return min(value, now)


def add_reports(
    text,
    source,
    kind,
    reporter,
    status=None,
    key=None,
    at=None,
    ext_id=None,
    segment=None,
    place=None,
):
    """Находит улицы и статус в тексте (или берёт готовые key и status) и пишет в базу."""
    found_status = status if status in ("on", "off") else status_of(text)
    if key:
        name = STREET_BY_KEY.get(key)
        matched = [{"key": key, "name": name}] if name and found_status else []
    else:
        matched = streets_in(text) if found_status else []
    if not found_status or not matched:
        return {
            "ok": False,
            "status": found_status,
            "streets": [],
            "reason": "нужны улица и слова «свет есть» или «света нет»",
        }
    now = int(time.time() * 1000)
    at = at or now
    if now - at > HORIZON_MIN * MIN_MS:
        return {"ok": False, "status": found_status, "streets": [], "reason": "сообщение старше суток"}
    saved = []
    with store.connect() as conn:
        for street in matched:
            new = store.add(
                conn,
                street,
                found_status,
                kind,
                source or "чат",
                reporter,
                text=text,
                at=at,
                ext_id=ext_id,
                segment=segment,
                place=place,
            )
            saved.append({"key": street["key"], "name": street["name"], "new": new})
    return {"ok": True, "status": found_status, "streets": saved}


class Handler(BaseHTTPRequestHandler):
    server_version = "karta-sveta"

    def log_message(self, fmt, *args):
        if os.environ.get("KARTA_QUIET"):
            return
        super().log_message(fmt, *args)

    def client_ip(self):
        peer = self.client_address[0]
        if peer in ("127.0.0.1", "::1"):
            return self.headers.get("X-Real-IP") or peer
        return peer

    def send_bytes(self, status, body, ctype, cache="no-store"):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_bytes(status, body, "application/json; charset=utf-8")

    def static_path(self, path):
        if path in PUBLIC_FILES:
            return ROOT / PUBLIC_FILES[path]
        if path.startswith("/vendor/"):
            target = (ROOT / path.lstrip("/")).resolve()
            if (ROOT / "vendor") in target.parents and target.is_file():
                return target
        return None

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/live":
            with store.connect() as conn:
                state = store.live(conn)
            self.send_json({"now": int(time.time() * 1000), **state})
            return
        if path == "/api/houses":
            key = parse_qs(urlsplit(self.path).query).get("key", [""])[0]
            self.send_json({"key": key, "houses": HOUSES.get(key, [])})
            return
        if path == "/api/health":
            self.send_json({"ok": True})
            return
        if path.startswith("/api/pulse/"):
            self.pulse(path[len("/api/pulse/"):])
            return
        target = self.static_path(path)
        if not target or not target.is_file():
            self.send_error(404)
            return
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("json") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        cache = "public, max-age=3600" if path.startswith(LONG_CACHE) else "no-store"
        self.send_bytes(200, target.read_bytes(), ctype, cache)

    def read_payload(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            return None
        raw = self.rfile.read(length).decode("utf-8", "replace") if length else "{}"
        try:
            data = json.loads(raw or "{}")
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        payload = self.read_payload()
        if payload is None:
            self.send_json({"ok": False, "reason": "слишком длинное сообщение"}, 413)
            return
        if path == "/api/ingest":
            self.ingest(payload)
            return
        if path == "/api/bulletin":
            self.bulletin(payload)
            return
        if path.startswith("/api/pulse/"):
            self.pulse(path[len("/api/pulse/"):])
            return
        if path == "/api/sensor":
            self.sensor_register(payload)
            return
        if path == "/api/sensor/delete":
            self.sensor_delete(payload)
            return
        if path not in ("/api/comments", "/api/mark"):
            self.send_error(404)
            return
        ip = self.client_ip()
        if not rate_ok(ip):
            self.send_json({"ok": False, "reason": "слишком часто, подожди несколько минут"}, 429)
            return
        who = store.reporter_id(SECRET["salt"], "web", ip, self.headers.get("User-Agent", ""))
        now = int(time.time() * 1000)
        if path == "/api/comments":
            result = add_reports(
                str(payload.get("text") or ""),
                str(payload.get("source") or "чат"),
                "comment",
                who,
                at=now,
            )
        else:
            place = None
            lon, lat = payload.get("lon"), payload.get("lat")
            if lon is not None or lat is not None:
                if not valid_point(lon, lat):
                    self.send_json({"ok": False, "reason": "точка вне Донецка"})
                    return
                place = (float(lon), float(lat), clean_radius(payload.get("radius")))
            result = add_reports(
                str(payload.get("label") or "")[:40] if place else "",
                "кнопка на карте",
                "button",
                who,
                status=payload.get("status"),
                key=str(payload.get("key") or ""),
                at=now,
                segment=str(payload.get("segment") or "") or None,
                place=place,
            )
        self.send_json(result)

    def pulse(self, token):
        """Пульс датчика: устройство раз в минуту открывает /api/pulse/<токен>."""
        now = int(time.time() * 1000)
        with store.connect() as conn:
            state = sensors.beat(conn, token, now, SECRET["salt"])
        if state is None:
            if not rate_ok("badtoken:" + self.client_ip(), 20, 10 * 60):
                self.send_json({"ok": False, "reason": "слишком часто"}, 429)
                return
            self.send_json({"ok": False, "reason": "такого датчика нет"}, 404)
            return
        self.send_json({"ok": True, **state})

    def sensor_register(self, payload):
        """Новый датчик: {"address": "Артёма 10", "mode": "pulse" | "ping"}.

        В режиме ping пингуется IP, с которого пришёл запрос: чужой адрес так не подключить.
        """
        ip = self.client_ip()
        if not rate_ok("sensor:" + ip, 10, 60 * 60):
            self.send_json({"ok": False, "reason": "слишком часто, попробуй позже"}, 429)
            return
        mode = payload.get("mode")
        if mode not in ("pulse", "ping"):
            self.send_json({"ok": False, "reason": "выбери способ проверки"})
            return
        address = str(payload.get("address") or "")[:120]
        found = streets_in(address)
        number = sensors.house_number(address)
        if len(found) != 1 or not number:
            self.send_json({"ok": False, "reason": "Напиши одну улицу и номер дома, например «Артёма 10»."})
            return
        street = found[0]
        house = sensors.find_house(HOUSES.get(street["key"], []), number)
        if not house:
            self.send_json({"ok": False, "reason": f"Для улицы {street['name']} нет домов в базе адресов."})
            return
        if house.get("ambiguous"):
            self.send_json(
                {"ok": False, "reason": "Дом с таким номером есть в разных местах этой улицы, напиши соседнюю улицу или другой дом рядом."}
            )
            return
        owner = store.reporter_id(SECRET["salt"], "owner", ip)
        now = int(time.time() * 1000)
        public_ip = None
        if mode == "ping":
            public_ip = sensors.global_ipv4(ip)
            if not public_ip:
                self.send_json(
                    {
                        "ok": False,
                        "reason": "Для проверки пингом открой страницу из домашней сети по IPv4 с белым адресом. С мобильного интернета и за общим адресом провайдера это не работает, выбери пульс.",
                    }
                )
                return
        with store.connect() as conn:
            if sensors.owner_count(conn, owner) >= sensors.MAX_PER_OWNER:
                self.send_json({"ok": False, "reason": f"С одного адреса можно подключить не больше {sensors.MAX_PER_OWNER} датчиков."})
                return
            if public_ip and sensors.ip_taken(conn, public_ip):
                self.send_json({"ok": False, "reason": "Этот адрес уже подключён."})
                return
        if public_ip and not sensors.ping(public_ip, count=3):
            self.send_json(
                {
                    "ok": False,
                    "reason": "Роутер не отвечает на ping снаружи. Включи ответ на ping из интернета (WAN) в его настройках или выбери пульс.",
                }
            )
            return
        with store.connect() as conn:
            token = sensors.create(
                conn, mode, street, house["lon"], house["lat"], owner, now, ip=public_ip, alive=bool(public_ip)
            )
            sensor = sensors.by_token(conn, token)
            sensors.settle(conn, sensor, now, SECRET["salt"])
        self.send_json(
            {
                "ok": True,
                "mode": mode,
                "token": token,
                "name": street["name"],
                "house": house["label"],
                "exact": house["exact"],
                "radius": sensors.RADIUS,
            }
        )

    def sensor_delete(self, payload):
        """Удаляет датчик по токену вместе с IP."""
        if not rate_ok("sensor-del:" + self.client_ip(), 20, 60 * 60):
            self.send_json({"ok": False, "reason": "слишком часто"}, 429)
            return
        with store.connect() as conn:
            gone = sensors.remove(conn, str(payload.get("token") or ""))
        self.send_json({"ok": gone, "reason": "" if gone else "такого датчика нет"})

    def bulletin(self, payload):
        """Общегородская сводка («все ТП запитаны», «частично обесточены районы»).

        Тело: {"text", "source", "ts", "ext_id"}. Вид и районы сервер определяет сам.
        """
        if not check_token(self):
            self.send_json({"ok": False, "reason": "нет доступа"}, 403)
            return
        found = bulletins.classify(str(payload.get("text") or ""))
        if not found:
            self.send_json({"ok": False, "reason": "это не сводка"})
            return
        at = message_time(payload.get("ts"))
        if int(time.time() * 1000) - at > HORIZON_MIN * MIN_MS:
            self.send_json({"ok": False, "reason": "сводка старше суток"})
            return
        with store.connect() as conn:
            new = store.add_bulletin(
                conn,
                found["kind"],
                at,
                str(payload.get("source") or "канал"),
                found["districts"],
                text=str(payload.get("text") or ""),
                ext_id=str(payload.get("ext_id") or "") or None,
            )
        self.send_json({"ok": True, "kind": found["kind"], "districts": found["districts"], "new": new})

    def ingest(self, payload):
        """Для читалок чатов и датчиков. Нужен заголовок X-Ingest-Token.

        Тело: {"text", "source", "sender", "ts", "ext_id"}; для датчика вместо text
        можно прислать {"kind": "sensor", "key", "status", "sender"}.
        """
        if not check_token(self):
            self.send_json({"ok": False, "reason": "нет доступа"}, 403)
            return
        kind = payload.get("kind") if payload.get("kind") in ("chat", "bot", "sensor", "channel") else "chat"
        source = str(payload.get("source") or "чат")
        sender = str(payload.get("sender") or source)
        who = store.reporter_id(SECRET["salt"], kind, source, sender)
        result = add_reports(
            str(payload.get("text") or ""),
            source,
            kind,
            who,
            status=payload.get("status"),
            key=str(payload.get("key") or "") or None,
            at=message_time(payload.get("ts")),
            ext_id=str(payload.get("ext_id") or "") or None,
        )
        self.send_json(result)


def check_token(handler):
    given = handler.headers.get("X-Ingest-Token", "")
    return hmac.compare_digest(given.encode(), SECRET["ingest_token"].encode())


def main():
    host = os.environ.get("KARTA_HOST", "127.0.0.1")
    port = int(os.environ.get("KARTA_PORT", "8765"))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"http://{host}:{port}/")
    server.serve_forever()


if __name__ == "__main__":
    main()
