"""SQLite: все отметки о свете лежат в data/karta.db и никогда не стираются."""

import hashlib
import json
import os
import secrets
import sqlite3
import time
from pathlib import Path

from model import HORIZON_MIN, MIN_MS, assess

ROOT = Path(__file__).resolve().parent
DB = Path(os.environ.get("KARTA_DB") or ROOT / "data" / "karta.db")
SECRET = Path(os.environ.get("KARTA_SECRET") or ROOT / "data" / "secret.json")
OLD_LIVE = ROOT / "data" / "live-reports.json"

# Тот же человек с тем же ответом по той же улице за это время считается один раз.
REPEAT_MS = 2 * MIN_MS

SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY,
    key TEXT NOT NULL,
    name TEXT NOT NULL,
    segment TEXT,
    status TEXT NOT NULL CHECK (status IN ('on', 'off')),
    at INTEGER NOT NULL,
    saved_at INTEGER NOT NULL,
    kind TEXT NOT NULL,
    source TEXT NOT NULL,
    reporter TEXT NOT NULL,
    ext_id TEXT,
    text TEXT
);
CREATE INDEX IF NOT EXISTS reports_key_at ON reports (key, at);
CREATE INDEX IF NOT EXISTS reports_at ON reports (at);
CREATE UNIQUE INDEX IF NOT EXISTS reports_ext ON reports (ext_id, key) WHERE ext_id IS NOT NULL;
"""


def connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def load_secret():
    """Токен приёма и соль для хеша людей. Создаётся при первом запуске, наружу не отдаётся."""
    if SECRET.exists():
        data = json.loads(SECRET.read_text(encoding="utf-8"))
    else:
        data = {"ingest_token": secrets.token_urlsafe(32), "salt": secrets.token_hex(16)}
        SECRET.parent.mkdir(parents=True, exist_ok=True)
        SECRET.write_text(json.dumps(data), encoding="utf-8")
        os.chmod(SECRET, 0o600)
    token = os.environ.get("KARTA_INGEST_TOKEN")
    if token:
        data["ingest_token"] = token
    return data


def reporter_id(salt: str, *parts: str) -> str:
    """Один человек = один короткий хеш. Имя, телефон и IP не хранятся."""
    raw = salt + "\x00" + "\x00".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def init():
    with connect() as conn:
        conn.executescript(SCHEMA)
    migrate_old()


def migrate_old():
    """Перенос data/live-reports.json из прежней версии, один раз."""
    if not OLD_LIVE.exists():
        return
    try:
        items = json.loads(OLD_LIVE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        items = []
    now = int(time.time() * 1000)
    with connect() as conn:
        for item in items if isinstance(items, list) else []:
            conn.execute(
                "INSERT INTO reports (key, name, status, at, saved_at, kind, source, reporter, text)"
                " VALUES (?, ?, ?, ?, ?, 'comment', ?, ?, ?)",
                (
                    item["key"],
                    item["name"],
                    item["status"],
                    int(item["at"]),
                    now,
                    item.get("source") or "чат",
                    "old:" + str(item.get("source") or "чат"),
                    item.get("text"),
                ),
            )
    OLD_LIVE.rename(OLD_LIVE.with_name(OLD_LIVE.name + ".migrated"))


def add(conn, street, status, kind, source, reporter, text="", at=None, ext_id=None, segment=None):
    """Пишет отметку. Возвращает True, если записана, False, если это повтор."""
    now = int(time.time() * 1000)
    at = int(at or now)
    key = street["key"]
    if ext_id:
        if conn.execute("SELECT 1 FROM reports WHERE ext_id = ? AND key = ?", (ext_id, key)).fetchone():
            return False
    repeat = conn.execute(
        "SELECT 1 FROM reports WHERE key = ? AND reporter = ? AND status = ? AND ABS(at - ?) < ?",
        (key, reporter, status, at, REPEAT_MS),
    ).fetchone()
    if repeat:
        return False
    conn.execute(
        "INSERT INTO reports (key, name, segment, status, at, saved_at, kind, source, reporter, ext_id, text)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (key, street["name"], segment, status, at, now, kind, source[:80], reporter, ext_id, (text or "")[:240]),
    )
    return True


def live(conn, now_ms=None):
    """Статус каждой улицы, по которой есть отметки за последние сутки."""
    now_ms = int(now_ms or time.time() * 1000)
    since = now_ms - HORIZON_MIN * MIN_MS
    rows = conn.execute("SELECT * FROM reports WHERE at >= ? ORDER BY key, at", (since,)).fetchall()
    by_key = {}
    for row in rows:
        by_key.setdefault(row["key"], []).append(dict(row))
    result = [assess(items, now_ms) for items in by_key.values()]
    result = [item for item in result if item]
    result.sort(key=lambda item: item["at"], reverse=True)
    return result
