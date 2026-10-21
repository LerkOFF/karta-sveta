#!/usr/bin/env python3
"""Номера домов по улицам: data/houses.json из выгрузки адресов OSM.

Выгрузка (адреса зданий и точек в рамке Донецка):

    curl -A "karta-sveta/1.0" --data-urlencode \\
      'data=[out:json][timeout:240];(nwr["addr:housenumber"](47.89,37.50,48.12,38.02););out center tags qt;' \\
      https://overpass.kumi.systems/api/interpreter -o /tmp/donetsk-houses.json

Запуск:  python3 scripts/build_houses.py /tmp/donetsk-houses.json

Дом привязывается к улице из data/streets.geojson. Названия в OSM то украинские, то русские и с ними
не всегда совпадают, поэтому смотрим на геометрию: берём ближайшую улицу, чьё имя похоже на addr:street
(до 80 м), а если имени нет или оно непохоже, ближайшую улицу в 40 м. Результат: {ключ улицы: [[номер, lon, lat], ...]},
координаты округлены до 5 знаков.
"""

import difflib
import importlib.util
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/donetsk-houses.json")
OUT = ROOT / "data" / "houses.json"

NAMED_M = 200
ANY_M = 70
CELL = 0.001

spec = importlib.util.spec_from_file_location("build_streets", ROOT / "scripts" / "build_streets.py")
streets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(streets)


def segments():
    data = json.loads((ROOT / "data" / "streets.geojson").read_text(encoding="utf-8"))
    for feature in data["features"]:
        key = feature["properties"]["key"]
        geometry = feature["geometry"]
        lines = geometry["coordinates"] if geometry["type"] == "MultiLineString" else [geometry["coordinates"]]
        for line in lines:
            for a, b in zip(line, line[1:]):
                yield key, a, b


def cell(lon, lat):
    return int(lon // CELL), int(lat // CELL)


def build_grid():
    grid = {}
    for key, a, b in segments():
        lo = cell(min(a[0], b[0]), min(a[1], b[1]))
        hi = cell(max(a[0], b[0]), max(a[1], b[1]))
        for x in range(lo[0], hi[0] + 1):
            for y in range(lo[1], hi[1] + 1):
                grid.setdefault((x, y), []).append((key, a, b))
    return grid


def dist_to_segment(lon, lat, a, b):
    kx = 111320 * math.cos(math.radians(lat))
    ky = 110540
    ax, ay = (a[0] - lon) * kx, (a[1] - lat) * ky
    dx, dy = (b[0] - a[0]) * kx, (b[1] - a[1]) * ky
    length = dx * dx + dy * dy
    t = 0 if not length else max(0, min(1, -(ax * dx + ay * dy) / length))
    return math.hypot(ax + dx * t, ay + dy * t)


def near_streets(grid, lon, lat, limit):
    """Улицы в пределах limit метров: {ключ: расстояние до ближайшего отрезка}."""
    reach = int(limit / 70) + 1
    cx, cy = cell(lon, lat)
    found = {}
    seen = set()
    for x in range(cx - reach, cx + reach + 1):
        for y in range(cy - reach, cy + reach + 1):
            for item in grid.get((x, y), ()):
                if id(item) in seen:
                    continue
                seen.add(id(item))
                d = dist_to_segment(lon, lat, item[1], item[2])
                if d <= limit and d < found.get(item[0], limit + 1):
                    found[item[0]] = d
    return found


def pick_street(grid, lon, lat, name):
    want = streets.norm(streets.uk_to_ru(name)) if name else ""
    if want:
        named = near_streets(grid, lon, lat, NAMED_M)
        similar = [
            (dist, key)
            for key, dist in named.items()
            if key == want or difflib.SequenceMatcher(None, key, want).ratio() >= 0.75
        ]
        if similar:
            return min(similar)[1]
    near = near_streets(grid, lon, lat, ANY_M)
    if near:
        return min(near, key=near.get)
    return None


def main():
    elements = json.loads(SRC.read_text(encoding="utf-8"))["elements"]
    grid = build_grid()
    result = {}
    seen = set()
    skipped = 0
    for element in elements:
        tags = element.get("tags", {})
        number = (tags.get("addr:housenumber") or "").strip()
        point = element if "lat" in element else element.get("center")
        if not number or not point:
            continue
        lon, lat = point["lon"], point["lat"]
        key = pick_street(grid, lon, lat, tags.get("addr:street"))
        if not key:
            skipped += 1
            continue
        item = (key, number.lower(), round(lon, 5), round(lat, 5))
        if item in seen:
            continue
        seen.add(item)
        result.setdefault(key, []).append([number, item[2], item[3]])
    for rows in result.values():
        rows.sort(key=lambda row: (int("".join(ch for ch in row[0] if ch.isdigit()) or 0), row[0]))
    OUT.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    total = sum(len(rows) for rows in result.values())
    print(f"улиц {len(result)}, домов {total}, без улицы {skipped}, файл {OUT.stat().st_size // 1024} КБ")


if __name__ == "__main__":
    main()
