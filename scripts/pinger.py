#!/usr/bin/env python3
"""Фоновая служба датчиков: раз в минуту пингует роутеры, решает, у кого пропал или вернулся свет.

Запуск на сервере рядом со страницей: python3 scripts/pinger.py  (unit: deploy/karta-pinger.service).
База та же, что у server.py (data/karta.db, переменная KARTA_DB). Проверка одного круга: --once.

Прежде чем записать «нет», служба убеждается, что виновата не наша сторона: отвечает ли
внешний мир (CANARY) и жив ли сам сервер карты (пульс принимает он).
"""

import argparse
import os
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import sensors  # noqa: E402
import store  # noqa: E402

# Если не отвечает ни один из них, у нас самих нет сети: ни «нет», ни повторов не пишем.
CANARY = [host for host in os.environ.get("KARTA_CANARY", "77.88.8.8,1.1.1.1,8.8.8.8").split(",") if host]
HEALTH = f"http://127.0.0.1:{os.environ.get('KARTA_PORT', '8765')}/api/health"


def server_alive() -> bool:
    try:
        with urllib.request.urlopen(HEALTH, timeout=3) as answer:
            return answer.status == 200
    except OSError:
        return False


def canary_alive() -> bool:
    return any(sensors.ping(host, count=1, wait=2) for host in CANARY)


def cycle(salt: str, since: dict) -> dict:
    """Один круг. since: {"ok": время, когда сервер карты стал отвечать, или None}."""
    now = int(time.time() * 1000)
    server_ok = server_alive()
    since["ok"] = (since["ok"] or now) if server_ok else None
    net_ok = canary_alive()
    # После запуска и после перезапуска сервера пульсы ещё не успели дойти: не верим тишине.
    settled = since["ok"] is not None and now - since["ok"] >= sensors.PULSE_LOST_MS
    hold = not (server_ok and net_ok and settled)

    with store.connect() as conn:
        pinged = [dict(row) for row in conn.execute("SELECT id, ip FROM sensors WHERE mode = 'ping' AND ip IS NOT NULL")]
    results = {}
    if net_ok and pinged:
        with ThreadPoolExecutor(max_workers=32) as pool:
            for row, ok in zip(pinged, pool.map(lambda row: sensors.ping(row["ip"]), pinged)):
                results[row["id"]] = ok

    with store.connect() as conn:
        for sensor_id, ok in results.items():
            sensors.record_ping(conn, sensor_id, ok, now)
        done = sensors.tick(conn, now, salt, hold)
        counts = store.sensor_counts(conn)
    return {"hold": hold, "net": net_ok, "server": server_ok, "pinged": len(pinged), "marks": len(done), **counts}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="один круг и выход")
    parser.add_argument("--interval", type=int, default=60, help="секунд между кругами")
    args = parser.parse_args()
    store.init()
    salt = store.load_secret()["salt"]
    since = {"ok": None}
    while True:
        started = time.time()
        try:
            info = cycle(salt, since)
            print(time.strftime("%H:%M:%S"), info, flush=True)
        except Exception as error:  # служба не должна падать от одной ошибки
            print(time.strftime("%H:%M:%S"), "ошибка круга:", repr(error), flush=True)
        if args.once:
            return
        time.sleep(max(1.0, args.interval - (time.time() - started)))


if __name__ == "__main__":
    main()
