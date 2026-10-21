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

import store
from model import HORIZON_MIN, MIN_MS
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

SECRET = store.load_secret()
store.init()


def rate_ok(ip: str) -> bool:
    now = time.time()
    with _hits_lock:
        queue = _hits[ip]
        while queue and now - queue[0] > RATE_WINDOW:
            queue.popleft()
        if len(queue) >= RATE_LIMIT:
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
                reports = store.live(conn)
            self.send_json({"now": int(time.time() * 1000), "reports": reports})
            return
        if path == "/api/health":
            self.send_json({"ok": True})
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
            result = add_reports(
                str(payload.get("text") or ""),
                "кнопка на карте",
                "button",
                who,
                status=payload.get("status"),
                key=str(payload.get("key") or ""),
                at=now,
                segment=str(payload.get("segment") or "") or None,
            )
        self.send_json(result)

    def ingest(self, payload):
        """Для читалок чатов и датчиков. Нужен заголовок X-Ingest-Token.

        Тело: {"text", "source", "sender", "ts", "ext_id"}; для датчика вместо text
        можно прислать {"kind": "sensor", "key", "status", "sender"}.
        """
        given = self.headers.get("X-Ingest-Token", "")
        if not hmac.compare_digest(given.encode(), SECRET["ingest_token"].encode()):
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


def main():
    host = os.environ.get("KARTA_HOST", "127.0.0.1")
    port = int(os.environ.get("KARTA_PORT", "8765"))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"http://{host}:{port}/")
    server.serve_forever()


if __name__ == "__main__":
    main()
