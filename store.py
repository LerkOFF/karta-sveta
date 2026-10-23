"""SQLite: все отметки о свете лежат в data/karta.db и никогда не стираются."""

import hashlib
import json
import os
import secrets
import sqlite3
import time
from pathlib import Path

import bulletins
from model import HORIZON_MIN, MIN_MS, assess, assess_places, distance_m

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
    lon REAL,
    lat REAL,
    radius INTEGER,
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

CREATE TABLE IF NOT EXISTS bulletins (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('all_on', 'partial_off')),
    at INTEGER NOT NULL,
    saved_at INTEGER NOT NULL,
    source TEXT NOT NULL,
    districts TEXT NOT NULL DEFAULT '',
    text TEXT,
    ext_id TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS bulletins_at ON bulletins (at);

CREATE TABLE IF NOT EXISTS sensors (
    id INTEGER PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE,
    mode TEXT NOT NULL CHECK (mode IN ('pulse', 'ping')),
    key TEXT NOT NULL,
    name TEXT NOT NULL,
    lon REAL NOT NULL,
    lat REAL NOT NULL,
    radius INTEGER NOT NULL,
    ip TEXT,
    owner TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    last_seen INTEGER,
    misses INTEGER NOT NULL DEFAULT 0,
    state TEXT CHECK (state IN ('on', 'off')),
    state_at INTEGER,
    reported_at INTEGER,
    quiet INTEGER NOT NULL DEFAULT 0,
    confirmed INTEGER NOT NULL DEFAULT 1,
    ip_seen_at INTEGER,
    last_ping INTEGER
);
CREATE INDEX IF NOT EXISTS sensors_owner ON sensors (owner);
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
        have = {row["name"] for row in conn.execute("PRAGMA table_info(reports)")}
        for column, kind in (("lon", "REAL"), ("lat", "REAL"), ("radius", "INTEGER")):
            if column not in have:
                conn.execute(f"ALTER TABLE reports ADD COLUMN {column} {kind}")
        have = {row["name"] for row in conn.execute("PRAGMA table_info(sensors)")}
        for column, kind in (
            ("confirmed", "INTEGER NOT NULL DEFAULT 1"),
            ("ip_seen_at", "INTEGER"),
            ("last_ping", "INTEGER"),
        ):
            if column not in have:
                conn.execute(f"ALTER TABLE sensors ADD COLUMN {column} {kind}")
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


def add(conn, street, status, kind, source, reporter, text="", at=None, ext_id=None, segment=None, place=None):
    """Пишет отметку. Возвращает True, если записана, False, если это повтор.

    place: (lon, lat, radius), если отметка про кусок улицы, а не про всю улицу.
    """
    now = int(time.time() * 1000)
    at = int(at or now)
    key = street["key"]
    if ext_id:
        if conn.execute("SELECT 1 FROM reports WHERE ext_id = ? AND key = ?", (ext_id, key)).fetchone():
            return False
    lon, lat, radius = place or (None, None, None)
    near = conn.execute(
        "SELECT lon, lat FROM reports WHERE key = ? AND reporter = ? AND status = ? AND ABS(at - ?) < ?",
        (key, reporter, status, at, REPEAT_MS),
    ).fetchall()
    for row in near:
        if lon is None and row["lon"] is None:
            return False
        if lon is not None and row["lon"] is not None and distance_m(lon, lat, row["lon"], row["lat"]) < 50:
            return False
    conn.execute(
        "INSERT INTO reports (key, name, segment, lon, lat, radius, status, at, saved_at, kind, source, reporter, ext_id, text)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (key, street["name"], segment, lon, lat, radius, status, at, now, kind, source[:80], reporter, ext_id, (text or "")[:240]),
    )
    return True


def add_bulletin(conn, kind, at, source, districts, text="", ext_id=None):
    """Пишет общегородскую сводку. False, если такая запись уже есть."""
    now = int(time.time() * 1000)
    cursor = conn.execute(
        "INSERT OR IGNORE INTO bulletins (kind, at, saved_at, source, districts, text, ext_id)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (kind, int(at), now, source[:80], ",".join(districts), (text or "")[:300], ext_id),
    )
    return cursor.rowcount > 0


def recent_bulletins(conn, now_ms):
    """Сводки за сутки, слитые по видам: см. bulletins.merge."""
    since = now_ms - HORIZON_MIN * MIN_MS
    rows = conn.execute("SELECT * FROM bulletins WHERE at >= ? ORDER BY at", (since,)).fetchall()
    return bulletins.merge(
        {
            "kind": row["kind"],
            "at": row["at"],
            "source": row["source"],
            "districts": [name for name in row["districts"].split(",") if name],
            "text": row["text"],
        }
        for row in rows
    )


def live(conn, now_ms=None):
    """Статус каждой улицы за последние сутки и последняя общегородская сводка."""
    now_ms = int(now_ms or time.time() * 1000)
    since = now_ms - HORIZON_MIN * MIN_MS
    clusters = recent_bulletins(conn, now_ms)
    restored = bulletins.restored_at(clusters)
    rows = conn.execute("SELECT * FROM reports WHERE at >= ? ORDER BY key, at", (since,)).fetchall()
    by_key = {}
    by_key_places = {}
    for row in rows:
        item = dict(row)
        if item["lon"] is None:
            by_key.setdefault(row["key"], []).append(item)
        else:
            by_key_places.setdefault(row["key"], []).append(item)
    result = [assess(items, now_ms, restored) for items in by_key.values()]
    result = [item for item in result if item]
    result.sort(key=lambda item: item["at"], reverse=True)
    places = []
    for items in by_key_places.values():
        places.extend(assess_places(items, now_ms, restored))
    places.sort(key=lambda item: item["at"])
    latest = clusters[-1] if clusters else None
    return {"reports": result, "places": places, "bulletin": latest, "sensors": sensor_counts(conn)}


def sensor_counts(conn):
    """Сколько датчиков сейчас видят свет и сколько нет. Адресов и IP здесь нет."""
    counts = {"on": 0, "off": 0}
    for row in conn.execute("SELECT state, COUNT(*) AS n FROM sensors WHERE state IS NOT NULL AND quiet = 0 AND confirmed = 1 GROUP BY state"):
        counts[row["state"]] = row["n"]
    return counts
