#!/usr/bin/env python3
"""Читалка публичных каналов Telegram без аккаунта: страница https://t.me/s/<канал>.

Каждые interval_sec секунд забирает свежие посты каналов из data/channels.json и шлёт те,
где речь о свете, на /api/ingest. Ключей Telegram не нужно. Закрытые чаты (группы) этим
способом не читаются, для них scripts/tg_ingest.py.

Запускать там, где открыт t.me: с российского VPS он не отвечает.

  python3 scripts/tgweb_ingest.py --token-file /etc/karta-sveta.token
  python3 scripts/tgweb_ingest.py --once --dry-run      # показать, что ушло бы на сервер
"""

import argparse
import html
import json
import os
import random
import re
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bulletins import classify  # noqa: E402
from parsing import status_of, streets_in  # noqa: E402
from tg_ingest import to_payload, post  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "data" / "channels.json"
MAX_REPORT = 500
USER_AGENT = "karta-sveta/1.0 (+https://github.com/LerkOFF/karta-sveta)"

POST_RE = re.compile(r'<div class="tgme_widget_message_wrap.*?(?=<div class="tgme_widget_message_wrap|\Z)', re.S)
TEXT_RE = re.compile(r'class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', re.S)
ID_RE = re.compile(r'data-post="[^"/]+/(\d+)"')
TIME_RE = re.compile(r'<time[^>]*datetime="([^"]+)"')


def clean(fragment):
    text = re.sub(r"<br\s*/?>", "\n", fragment)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def parse_page(page):
    """Из страницы канала: список (id поста, время в секундах, текст)."""
    posts = []
    for block in POST_RE.findall(page):
        ident = ID_RE.search(block)
        stamp = TIME_RE.search(block)
        body = TEXT_RE.search(block)
        if not (ident and stamp and body):
            continue
        ts = datetime.fromisoformat(stamp.group(1)).timestamp()
        posts.append((int(ident.group(1)), ts, clean(body.group(1))))
    return posts


def fetch(name):
    request = urllib.request.Request(f"https://t.me/s/{name}", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=25) as response:
        return response.read().decode("utf-8", "replace")


def tidy(text):
    """Хвост «Киевский | Предложить новость» в конце поста не часть сообщения."""
    return re.sub(r"\s*\S+\s*\|\s*Предложить новость\s*$", "", text).strip()


def load_config(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def token_of(args):
    if args.token_file:
        return Path(args.token_file).read_text(encoding="utf-8").strip()
    return os.environ.get("KARTA_INGEST_TOKEN") or json.loads(
        (ROOT / "data" / "secret.json").read_text(encoding="utf-8")
    )["ingest_token"]


def send_bulletin(config, token, name, channel, ident, ts, text, dry_run):
    """Общегородская сводка («все ТП запитаны», «частично обесточены районы») идёт отдельным путём."""
    found = classify(text)
    if not found:
        return False
    payload = {
        "text": text,
        "source": channel.get("title") or name,
        "ts": ts,
        "ext_id": f"tgweb:{name}/{ident}",
    }
    if dry_run:
        print(json.dumps({"bulletin": found, **payload}, ensure_ascii=False), flush=True)
        return True
    endpoint = config.get("bulletin_endpoint") or config["endpoint"].rsplit("/", 1)[0] + "/bulletin"
    try:
        result = post(endpoint, token, payload)
    except Exception as error:
        print(f"{name}/{ident}: сводка не отправилась: {error}", file=sys.stderr, flush=True)
        return True
    if result.get("new"):
        print(f"{name}/{ident}: сводка {result.get('kind')} {', '.join(result.get('districts', []))}", flush=True)
    return True


def round_once(config, token, seen, dry_run):
    sent = 0
    for channel in config["channels"]:
        name = channel["name"]
        try:
            posts = parse_page(fetch(name))
        except Exception as error:
            print(f"{name}: страница не открылась: {error}", file=sys.stderr, flush=True)
            continue
        for ident, ts, text in posts:
            key = (name, ident)
            if key in seen:
                continue
            seen.add(key)
            text = tidy(text)
            if send_bulletin(config, token, name, channel, ident, ts, text, dry_run):
                continue
            # из канала берём только пересказ жителей, новости и рекламу не трогаем
            if not text.lower().startswith("нам пишут") or len(text) > MAX_REPORT:
                continue
            # то же сито, что на сервере: нужны и улица, и слова про свет
            if status_of(text) is None or not streets_in(text):
                continue
            payload = to_payload(text, channel.get("title") or name, name, ident, name, ts)
            if not payload:
                continue
            payload["kind"] = "channel"
            payload["ext_id"] = f"tgweb:{name}/{ident}"
            if dry_run:
                print(json.dumps(payload, ensure_ascii=False), flush=True)
                continue
            try:
                result = post(config["endpoint"], token, payload)
            except Exception as error:
                seen.discard(key)  # не дошло: попробуем в следующем круге
                print(f"{name}/{ident}: не отправилось: {error}", file=sys.stderr, flush=True)
                continue
            if result.get("ok"):
                sent += 1
                names = ", ".join(item["name"] for item in result.get("streets", []))
                print(f"{name}/{ident}: {result.get('status')} {names}", flush=True)
        time.sleep(random.uniform(1.0, 3.0))
    return sent


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default=str(CONFIG))
    parser.add_argument("--token-file")
    parser.add_argument("--once", action="store_true", help="один круг и выход")
    parser.add_argument("--dry-run", action="store_true", help="ничего не слать, только показать")
    args = parser.parse_args()

    config = load_config(args.config)
    token = "dry-run" if args.dry_run else token_of(args)
    interval = float(config.get("interval_sec", 90))
    seen = set()
    while True:
        sent = round_once(config, token, seen, args.dry_run)
        if args.once:
            print(f"принято отметок: {sent}")
            return
        time.sleep(interval + random.uniform(0, 10))


if __name__ == "__main__":
    main()
