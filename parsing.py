"""Разбор текста из чата: какая улица и есть свет или нет."""

import json
from pathlib import Path

INDEX = Path(__file__).resolve().parent / "data" / "streets-index.json"

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


# Нечёткий поиск улицы только в коротких сообщениях.
FUZZY_MAX_WORDS = 40

# «Дали свет во всей округе, кроме дома на …»: про улицу целиком это не говорит.
EXCEPT_MARKS = ("кроме", "за исключением", "исключая")


def status_of(text: str):
    folded = norm(text)
    if any(mark in folded for mark in EXCEPT_MARKS):
        return None
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
    # Нечёткий поиск по основе слова годится для короткой фразы («на Артёма нет света»),
    # а в длинной новости «котельные» превращаются в улицу Котельникова.
    if len(words) > FUZZY_MAX_WORDS:
        return found
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
