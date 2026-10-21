"""Общегородские сводки: «все ТП запитаны» и «частично обесточены районы».

Сводка не говорит про улицу, поэтому цвет улиц она не ставит. Она нужна для двух вещей:
полоски на странице и снижения уверенности в старых «света нет» (см. model.assess).
"""

import re

MIN_MS = 60 * 1000

# Одна и та же сводка приходит из девяти каналов с разницей в минуты: считаем её одной.
CLUSTER_MIN = 90

DISTRICTS = (
    ("ворошиловск", "Ворошиловский"),
    ("киевск", "Киевский"),
    ("калининск", "Калининский"),
    ("куйбышевск", "Куйбышевский"),
    ("петровск", "Петровский"),
    ("кировск", "Кировский"),
    ("ленинск", "Ленинский"),
    ("буденновск", "Будённовский"),
    ("пролетарск", "Пролетарский"),
)

# Не все подстанции, исключения и пересказ чужих городов в сводку «всё запитано» не годятся.
NOT_ALL = ("кроме", "за исключением", "не все", "частично", "ещё не", "еще не")


# Районы Макеевки и других городов называются так же, как донецкие.
OTHER_CITIES = ("макеевк", "горловк", "мариупол", "енакиев", "ясиноват", "торез", "амвросиев", "луганск", "дебальцев")


def _fold(text: str) -> str:
    folded = text.lower().replace("ё", "е")
    return re.sub(r"\s+", " ", folded)


def _districts(folded: str):
    return [name for stem, name in DISTRICTS if stem in folded]


def classify(text: str):
    """Возвращает {"kind": "all_on" | "partial_off", "districts": [...]} или None."""
    folded = _fold(text or "")
    districts = _districts(folded)
    if "донецк" not in folded and (not districts or any(city in folded for city in OTHER_CITIES)):
        return None

    all_on = re.search(r"\bвсе\b[^.]{0,40}(подстанц|\bтп\b)[^.]{0,30}запитан", folded) or re.search(
        r"запитан\w*[^.]{0,20}\bвсе\b[^.]{0,30}(подстанц|\bтп\b)", folded
    )
    if all_on and not any(mark in folded for mark in NOT_ALL):
        return {"kind": "all_on", "districts": []}

    partial = (
        re.search(r"частично\W+(?:\w+\W+){0,3}обесточ", folded)
        or re.search(r"обесточ\w*\W+(?:\w+\W+){0,3}частично", folded)
        or ("в некоторых районах" in folded and "пропал свет" in folded)
        or (districts and re.search(r"обесточен\w*\W+\d+\W+(?:трансформаторн|тп)", folded))
    )
    if partial:
        return {"kind": "partial_off", "districts": districts}
    return None


def merge(rows):
    """rows: сводки (kind, at, source, districts, text) по возрастанию at.

    Подряд идущие сводки одного вида в пределах CLUSTER_MIN сливаются в одну: время берётся
    самое раннее, источники считаются, районы объединяются.
    """
    clusters = []
    for row in sorted(rows, key=lambda item: item["at"]):
        last = clusters[-1] if clusters else None
        if last and last["kind"] == row["kind"] and row["at"] - last["at"] <= CLUSTER_MIN * MIN_MS:
            last["sources"].add(row["source"])
            for name in row["districts"]:
                if name not in last["districts"]:
                    last["districts"].append(name)
            continue
        clusters.append(
            {
                "kind": row["kind"],
                "at": row["at"],
                "sources": {row["source"]},
                "districts": list(row["districts"]),
                "text": row["text"],
            }
        )
    for cluster in clusters:
        cluster["sources"] = len(cluster["sources"])
    return clusters


def restored_at(clusters):
    """Когда Донецкэнерго сообщил «всё запитано», если после этого новых отключений не было."""
    if clusters and clusters[-1]["kind"] == "all_on":
        return clusters[-1]["at"]
    return None
