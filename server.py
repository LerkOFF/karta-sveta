#!/usr/bin/env python3
"""Локальный сервер карты: раздаёт страницу и принимает комментарии из чатов."""

import json
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LIVE = ROOT / "data" / "live-reports.json"
INDEX = ROOT / "data" / "streets-index.json"
TTL_MS = 6 * 60 * 60 * 1000

STOP = (
    "вулиця",
    "улица",
    "вул.",
    "ул.",
    "проспект",
    "просп.",
    "пр-т",
    "переулок",
    "провулок",
    "пер.",
    "бульвар",
    "бульв.",
    "набережная",
    "набережна",
    "площадь",
    "площа",
    "шоссе",
    "шосе",
)

OFF_MARKS = (
    "света нет",
    "нет света",
    "без света",
    "выруб",
    "отключ",
    "погас",
    "темно",
    "не дали",
    "нету света",
)
ON_MARKS = (
    "свет дали",
    "дали свет",
    "свет есть",
    "есть свет",
    "включили",
    "включился",
    "появился свет",
    "дали электричество",
)


def norm(name: str) -> str:
    text = (
        name.lower()
        .replace("ё", "е")
        .replace("ї", "и")
        .replace("і", "и")
        .replace("є", "е")
        .replace("ґ", "г")
    )
    for word in STOP:
        text = text.replace(word, " ")
    text = text.replace("’", "").replace("'", "").replace("-", "")
    return " ".join(text.split())


def street_keys():
    rows = json.loads(INDEX.read_text(encoding="utf-8"))
    named = {}
    for row in rows:
        named.setdefault(row["key"], row["name"])
    return sorted(named.items(), key=lambda item: len(item[0]), reverse=True)


KEYS = street_keys()


def last_mark(folded: str, marks):
    best = -1
    for mark in marks:
        token = norm(mark)
        start = folded.rfind(token)
        while start >= 0:
            prev = folded[:start].rstrip()
            if prev != "не" and not prev.endswith(" не"):
                best = max(best, start)
                break
            start = folded.rfind(token, 0, start)
    return best


def status_of(text: str):
    folded = norm(text)
    off_at = last_mark(folded, OFF_MARKS)
    on_at = last_mark(folded, ON_MARKS)
    if off_at < 0 and on_at < 0:
        return None
    if on_at > off_at:
        return "on"
    return "off"


def is_district(after: str) -> bool:
    return after.startswith("район") or after.startswith("районе") or after.startswith("района")


def streets_in(text: str):
    padded = f" {norm(text)} "
    found = []
    used = set()
    for key, name in KEYS:
        if len(key) < 4:
            continue
        token = f" {key} "
        if token not in padded:
            continue
        after = padded.split(token, 1)[1]
        if is_district(after):
            continue
        found.append({"key": key, "name": name})
        used.add(key)
        padded = padded.replace(token, " ")
    if found:
        return found
    words = padded.split()
    long_words = [word for word in words if len(word) >= 6]
    for index, word in enumerate(long_words):
        nxt = long_words[index + 1] if index + 1 < len(long_words) else ""
        if nxt.startswith("район"):
            continue
        stem = word[:-2]
        hits = []
        for key, name in KEYS:
            if key in used or len(key) < 6:
                continue
            if abs(len(key) - len(word)) > 3:
                continue
            if key.startswith(stem) or word.startswith(key[:-2]):
                hits.append({"key": key, "name": name})
        if len(hits) == 1:
            found.append(hits[0])
            used.add(hits[0]["key"])
    return found


def read_live():
    if not LIVE.exists():
        return []
    try:
        data = json.loads(LIVE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    now = int(time.time() * 1000)
    return [item for item in data if now - int(item.get("at") or 0) < TTL_MS]


def write_live(items):
    LIVE.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")


def latest(items):
    chosen = {}
    for item in items:
        prev = chosen.get(item["key"])
        if not prev or prev["at"] < item["at"]:
            chosen[item["key"]] = item
    return sorted(chosen.values(), key=lambda item: item["at"], reverse=True)


def add_reports(text, source, status=None, key=None, name=None):
    found_status = status or status_of(text)
    if key and name and found_status:
        matched = [{"key": key, "name": name}]
    else:
        matched = streets_in(text) if found_status else []
    if not found_status or not matched:
        return {"ok": False, "status": found_status, "streets": [], "reason": "нужны улица и слова «свет есть» или «света нет»"}
    items = read_live()
    now = int(time.time() * 1000)
    saved = []
    for street in matched:
        row = {
            "key": street["key"],
            "name": street["name"],
            "status": found_status,
            "at": now,
            "source": (source or "чат")[:80],
            "text": text[:240],
        }
        items.append(row)
        saved.append(row)
    write_live(items)
    return {"ok": True, "status": found_status, "streets": saved}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        if self.path.split("?", 1)[0] == "/api/live":
            body = json.dumps({"reports": latest(read_live())}, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        try:
            payload = json.loads(raw or "{}")
        except json.JSONDecodeError:
            payload = {}
        if path == "/api/comments":
            result = add_reports(str(payload.get("text") or ""), str(payload.get("source") or "чат"))
        elif path == "/api/mark":
            result = add_reports(
                str(payload.get("text") or payload.get("name") or ""),
                str(payload.get("source") or "кнопка на карте"),
                status=payload.get("status"),
                key=payload.get("key"),
                name=payload.get("name"),
            )
        else:
            self.send_error(404)
            return
        body = json.dumps(result, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 8765), Handler)
    print("http://127.0.0.1:8765/")
    server.serve_forever()
