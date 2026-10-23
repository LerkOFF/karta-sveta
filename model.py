"""Как из отметок по улице получается статус, возраст и уверенность.

Чистые функции без базы и сети: их можно проверять отдельно (tests/test_model.py).
"""

import math

MIN_MS = 60 * 1000

# Через сколько минут уверенность в отметке падает вдвое.
# Свет любят то давать, то снимать, поэтому «есть» устаревает быстрее, чем «нет».
HALF_LIFE_MIN = {"on": 90, "off": 240}

# Насколько верим источнику.
# Канал района пересказывает «нам пишут» с задержкой, поэтому доверие ниже.
TRUST = {"chat": 1.0, "bot": 1.0, "sensor": 1.0, "official": 1.0, "button": 0.85, "comment": 0.85, "channel": 0.7}

# Точная информация: ведомство или сайт (official) и датчик. Всё остальное, чат, канал, кнопка,
# комментарий, это пересказ или слово человека: на карте такая отметка рисуется пунктиром.
EXACT_KINDS = ("official", "sensor")

# Дольше суток без подтверждения улица считается серой, но запись остаётся в истории.
HORIZON_MIN = 24 * 60

# Другой человек с тем же ответом за это время поднимает уверенность.
CORROBORATION_MIN = 180
CORROBORATION_BONUS = 0.25

# Если другой человек за это время до последней отметки сказал обратное, это спор.
CONFLICT_MIN = 30
CONFLICT_FACTOR = 0.6

# Донецкэнерго сообщил «все ТП запитаны» позже отметки «света нет»: отметка, скорее всего, устарела.
BULLETIN_FACTOR = 0.35

SURE_FROM = 0.6
LIKELY_FROM = 0.3


def level_of(confidence: float) -> str:
    if confidence >= SURE_FROM:
        return "sure"
    if confidence >= LIKELY_FROM:
        return "likely"
    return "stale"


def assess(rows, now_ms: int, restored_at=None):
    """rows: отметки одной улицы (dict с at, status, kind, reporter, source).

    Возвращает статус по самой новой отметке или None, если все старше суток.
    Каждый человек (reporter) считается один раз, по его последней отметке.
    restored_at: время общегородской сводки «все ТП запитаны», если после неё отключений не было.
    """
    rows = [row for row in rows if now_ms - row["at"] < HORIZON_MIN * MIN_MS]
    if not rows:
        return None

    newest_by_reporter = {}
    for row in rows:
        prev = newest_by_reporter.get(row["reporter"])
        if prev is None or prev["at"] < row["at"]:
            newest_by_reporter[row["reporter"]] = row

    latest = max(newest_by_reporter.values(), key=lambda row: row["at"])
    status = latest["status"]
    age_min = max(0.0, (now_ms - latest["at"]) / MIN_MS)

    base = 0.5 ** (age_min / HALF_LIFE_MIN[status])
    trust = TRUST.get(latest["kind"], 0.85)

    agree = 0
    conflict = False
    for row in newest_by_reporter.values():
        if row["reporter"] == latest["reporter"]:
            continue
        if row["status"] == status:
            if (now_ms - row["at"]) <= CORROBORATION_MIN * MIN_MS:
                agree += 1
        elif latest["at"] - row["at"] <= CONFLICT_MIN * MIN_MS:
            conflict = True

    confidence = min(1.0, base * trust * (1 + CORROBORATION_BONUS * agree))
    if conflict:
        confidence *= CONFLICT_FACTOR

    superseded_at = None
    if status == "off" and restored_at and latest["at"] < restored_at:
        confidence *= BULLETIN_FACTOR
        superseded_at = restored_at

    return {
        "key": latest["key"],
        "name": latest["name"],
        "status": status,
        "at": latest["at"],
        "age_min": round(age_min, 1),
        "confidence": round(confidence, 2),
        "level": level_of(confidence),
        "sources": 1 + agree,
        "conflict": conflict,
        "superseded_at": superseded_at,
        "source": latest["source"],
        "exact": latest["kind"] in EXACT_KINDS,
    }


# Отметка с точкой на карте красит кусок улицы вокруг точки. Радиус куска, метры:
# кнопки 100, 200, 500, а для «дома с … по …» страница считает свой.
MIN_RADIUS = 30
MAX_RADIUS = 2000
DEFAULT_RADIUS = 100

# Границы Донецка с запасом: точки за ними не принимаем.
BOX = (37.45, 47.83, 38.07, 48.16)


def distance_m(lon1, lat1, lon2, lat2) -> float:
    kx = 111320 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot((lon1 - lon2) * kx, (lat1 - lat2) * 110540)


def clean_radius(value) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return DEFAULT_RADIUS
    return int(min(MAX_RADIUS, max(MIN_RADIUS, round(value))))


def valid_point(lon, lat) -> bool:
    return (
        isinstance(lon, (int, float))
        and isinstance(lat, (int, float))
        and BOX[0] <= lon <= BOX[2]
        and BOX[1] <= lat <= BOX[3]
    )


def assess_places(rows, now_ms: int, restored_at=None):
    """rows: отметки с точкой (ещё lon, lat, radius) по одной улице.

    Идём от новых к старым: самая новая отметка задаёт кусок улицы, все более старые
    в пределах её радиуса считаются подтверждением или спором к ней. Старая отметка
    за пределами радиуса даёт свой кусок. Возвращает куски от старых к новым, чтобы
    на карте новый лежал сверху.
    """
    rows = sorted(
        (row for row in rows if now_ms - row["at"] < HORIZON_MIN * MIN_MS),
        key=lambda row: row["at"],
        reverse=True,
    )
    places = []
    left = rows
    while left:
        anchor = left[0]
        near = [
            row
            for row in left
            if distance_m(anchor["lon"], anchor["lat"], row["lon"], row["lat"]) <= anchor["radius"]
        ]
        left = [row for row in left if row not in near]
        result = assess(near, now_ms, restored_at)
        if result:
            result.update(
                {
                    "lon": anchor["lon"],
                    "lat": anchor["lat"],
                    "radius": anchor["radius"],
                    "label": anchor.get("text") or "",
                }
            )
            places.append(result)
    places.reverse()
    return places
