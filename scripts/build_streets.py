#!/usr/bin/env python3
"""Склеивает соседние куски одной улицы в участки и пишет GeoJSON."""

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/donetsk-streets.json")
OUT = ROOT / "data" / "streets.geojson"
INDEX = ROOT / "data" / "streets-index.json"

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


def uk_to_ru(text: str) -> str:
    text = text.replace("І", "И").replace("Ї", "И").replace("Є", "Е").replace("Ґ", "Г")
    text = text.replace("’", "'").replace("ʼ", "'")
    text = (
        text.replace("'є", "ье")
        .replace("'ї", "ьи")
        .replace("'я", "ья")
        .replace("'ю", "ью")
        .replace("'е", "ье")
    )
    text = text.replace("ої", "ой").replace("ська", "ская").replace("ський", "ский").replace("ське", "ское")
    text = text.replace("ників", "ников").replace("є", "е").replace("ї", "и").replace("і", "и").replace("ґ", "г")
    text = text.replace("'", "")
    for src, dst in (
        ("вулиця", "улица"),
        ("провулок", "переулок"),
        ("набережна", "набережная"),
        ("площа", "площадь"),
        ("шосе", "шоссе"),
    ):
        text = text.replace(src, dst)
    return text


def russian_name(tags: dict) -> str:
    russian = (tags.get("name:ru") or "").strip()
    return uk_to_ru(russian or (tags.get("name") or "").strip())


def meters(a, b) -> float:
    lat = math.radians((a[1] + b[1]) / 2)
    x = math.radians(b[0] - a[0]) * math.cos(lat) * 6371000
    y = math.radians(b[1] - a[1]) * 6371000
    return math.hypot(x, y)


def length(coords) -> float:
    return sum(meters(a, b) for a, b in zip(coords, coords[1:]))


def touches(lines: list[list[list[float]]], coords: list[list[float]]) -> bool:
    ends = [coords[0], coords[-1]]
    for line in lines:
        for left in (line[0], line[-1]):
            for right in ends:
                if meters(left, right) <= 40:
                    return True
    return False


def cluster_pieces(pieces: list[dict], limit: float = 500) -> list[list[dict]]:
    """Склеивает соседние короткие куски, пока участок не длиннее limit метров."""
    pending = list(pieces)
    clusters = []
    while pending:
        group = [pending.pop(0)]
        total = group[0]["meters"]
        grew = True
        while grew:
            grew = False
            for index, piece in enumerate(pending):
                if total + piece["meters"] > limit:
                    continue
                if touches([item["coords"] for item in group], piece["coords"]):
                    group.append(pending.pop(index))
                    total += piece["meters"]
                    grew = True
                    break
        clusters.append(group)
    return clusters


def split_way(coords: list[list[float]], target: float = 500) -> list[list[list[float]]]:
    """Режет линию кусками около target метров, чтобы клик красил квартал, не весь проспект."""
    if len(coords) < 2:
        return []
    chunks: list[list[list[float]]] = []
    current = [coords[0]]
    walked = 0.0
    for left, right in zip(coords, coords[1:]):
        current.append(right)
        walked += meters(left, right)
        if walked >= target and right is not coords[-1]:
            chunks.append(current)
            current = [right]
            walked = 0.0
    if len(current) >= 2:
        chunks.append(current)
    return chunks or [coords]


def main() -> None:
    raw = json.loads(SRC.read_text(encoding="utf-8"))
    elements = raw.get("elements") or []
    if not elements:
        raise SystemExit(f"в {SRC} нет улиц")

    groups: dict[str, list[dict]] = {}
    for element in elements:
        tags = element.get("tags") or {}
        name = russian_name(tags)
        geometry = element.get("geometry") or []
        if not name or len(geometry) < 2:
            continue
        coords = [
            [round(point["lon"], 5), round(point["lat"], 5)] for point in geometry
        ]
        key = norm(name)
        if not key:
            continue
        groups.setdefault(key, []).append(
            {"name": name, "coords": coords, "meters": length(coords)}
        )

    features = []
    index = []
    for key, ways in groups.items():
        pieces = []
        for way in ways:
            for coords in split_way(way["coords"]):
                pieces.append({"name": way["name"], "coords": coords, "meters": length(coords)})
        clusters = cluster_pieces(pieces)
        clusters.sort(key=lambda group: -group[0]["coords"][0][1])
        display = max((item["name"] for item in pieces), key=len)
        total = round(sum(item["meters"] for item in pieces))
        longest = max(clusters, key=lambda group: sum(item["meters"] for item in group))
        mid = longest[0]["coords"][len(longest[0]["coords"]) // 2]
        for part, group in enumerate(clusters, start=1):
            segment = f"{key}-{part}"
            features.append(
                {
                    "type": "Feature",
                    "properties": {
                        "name": display,
                        "key": key,
                        "segment": segment,
                        "part": part,
                        "parts": len(clusters),
                        "meters": round(sum(item["meters"] for item in group)),
                    },
                    "geometry": {
                        "type": "MultiLineString",
                        "coordinates": [item["coords"] for item in group],
                    },
                }
            )
        index.append(
            {
                "name": display,
                "key": key,
                "parts": len(clusters),
                "meters": total,
                "lon": mid[0],
                "lat": mid[1],
            }
        )

    features.sort(key=lambda item: item["properties"]["name"])
    index.sort(key=lambda item: (-item["meters"], item["name"]))
    OUT.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    INDEX.write_text(json.dumps(index, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"ways {sum(len(v) for v in groups.values())} segments {len(features)} names {len(groups)}")
    print(f"geojson {OUT.stat().st_size} index {INDEX.stat().st_size}")


if __name__ == "__main__":
    main()
