#!/usr/bin/env python3
"""Читалка районных чатов Telegram: новое сообщение уходит на /api/ingest карты.

Нужно один раз:
  1. pip install -r requirements-reader.txt
  2. api_id и api_hash с https://my.telegram.org (раздел API development tools)
  3. data/chats.json по образцу data/chats.example.json
  4. первый запуск вручную: Telethon спросит телефон и код, сессия ляжет в data/

Дальше работает само, при старте добирает сообщения за последние часы (повторы сервер отсекает).
Читает только перечисленные чаты. Автор сообщения на сервер уходит как номер и там сразу
превращается в хеш, имя и текст автора нигде не хранятся отдельно.
"""

import argparse
import asyncio
import json
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "data" / "chats.json"

# Дешёвый фильтр, чтобы не слать на сервер болтовню. Решает всё равно сервер.
WORDS = ("свет", "электр", "выруб", "отключ", "включ", "погас", "темно", "дали")
MAX_TEXT = 1500


def to_payload(text, title, chat_id, msg_id, sender_id, ts):
    """Одно сообщение чата в тело запроса. None, если сообщение не про свет."""
    text = (text or "").strip()
    if not text or len(text) > MAX_TEXT:
        return None
    folded = text.lower()
    if not any(word in folded for word in WORDS):
        return None
    return {
        "kind": "chat",
        "text": text,
        "source": title or f"чат {chat_id}",
        "sender": str(sender_id if sender_id is not None else chat_id),
        "ts": ts,
        "ext_id": f"tg:{chat_id}:{msg_id}",
    }


def post(endpoint, token, payload):
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Ingest-Token": token},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def load_config(path):
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    if not config.get("token"):
        secret = ROOT / "data" / "secret.json"
        config["token"] = json.loads(secret.read_text(encoding="utf-8"))["ingest_token"]
    return config


def proxy_of(config):
    proxy = config.get("proxy")
    if not proxy:
        return None
    return (
        proxy.get("type", "socks5"),
        proxy["host"],
        int(proxy["port"]),
        True,
        proxy.get("user"),
        proxy.get("password"),
    )


async def send(config, payload):
    try:
        result = await asyncio.to_thread(post, config["endpoint"], config["token"], payload)
    except Exception as error:  # сеть упала: сообщение придёт при следующем добирании
        print(f"не отправилось: {error}", file=sys.stderr, flush=True)
        return
    names = ", ".join(item["name"] for item in result.get("streets", [])) or "-"
    print(f"{payload['source']}: {result.get('status')} {names} ok={result.get('ok')}", flush=True)


async def run(config, check_only):
    try:
        from telethon import TelegramClient, events
    except ImportError:
        sys.exit("нет telethon: pip install -r requirements-reader.txt")

    session = str(ROOT / config.get("session", "data/tg-reader"))
    client = TelegramClient(session, int(config["api_id"]), config["api_hash"], proxy=proxy_of(config))
    await client.start()

    chats = []
    for ref in config["chats"]:
        entity = await client.get_entity(ref)
        title = getattr(entity, "title", None) or getattr(entity, "username", None) or str(ref)
        chats.append((entity, title))
        print(f"читаю: {title} ({entity.id})", flush=True)
    if check_only:
        await client.disconnect()
        return

    titles = {entity.id: title for entity, title in chats}

    since = datetime.now(timezone.utc) - timedelta(hours=float(config.get("backfill_hours", 6)))
    for entity, title in chats:
        async for message in client.iter_messages(entity):
            if message.date < since:
                break
            payload = to_payload(
                message.text, title, entity.id, message.id, message.sender_id, message.date.timestamp()
            )
            if payload:
                await send(config, payload)

    @client.on(events.NewMessage(chats=[entity for entity, _ in chats]))
    async def on_message(event):
        message = event.message
        chat_id = event.chat_id
        title = titles.get(abs(chat_id)) or titles.get(chat_id) or getattr(event.chat, "title", None)
        payload = to_payload(
            message.text, title, chat_id, message.id, message.sender_id, message.date.timestamp()
        )
        if payload:
            await send(config, payload)

    print(f"слушаю {len(chats)} чатов, {time.strftime('%H:%M:%S')}", flush=True)
    await client.run_until_disconnected()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=str(CONFIG))
    parser.add_argument("--check", action="store_true", help="только войти и показать, какие чаты найдены")
    args = parser.parse_args()
    asyncio.run(run(load_config(args.config), args.check))


if __name__ == "__main__":
    main()
