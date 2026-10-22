#!/usr/bin/env python3
"""Рисует favicon.ico, favicon-120.png и apple-touch-icon.png по геометрии favicon.svg."""
from pathlib import Path
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
INK, LAMP, METAL = "#1e1a16", "#ffd23f", "#ddd4c6"


def draw(size):
    k = 8
    s = size * k / 64
    img = Image.new("RGBA", (size * k, size * k), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, size * k - 1, size * k - 1], radius=14 * s, fill=INK)
    w = max(1, round(3.5 * s))
    for a, b in [((32, 5), (32, 10)), ((12, 13), (15.5, 16.5)), ((52, 13), (48.5, 16.5)), ((6, 30), (11, 30)), ((53, 30), (58, 30))]:
        d.line([(a[0] * s, a[1] * s), (b[0] * s, b[1] * s)], fill=LAMP, width=w)
        for p in (a, b):
            d.ellipse([p[0] * s - w / 2, p[1] * s - w / 2, p[0] * s + w / 2, p[1] * s + w / 2], fill=LAMP)
    d.ellipse([(32 - 13) * s, (29 - 13) * s, (32 + 13) * s, (29 + 13) * s], fill=LAMP)
    d.polygon([(26 * s, 38 * s), (38 * s, 38 * s), (36.5 * s, 45 * s), (27.5 * s, 45 * s)], fill=LAMP)
    d.rounded_rectangle([25 * s, 46 * s, 39 * s, 50 * s], radius=2 * s, fill=METAL)
    d.rounded_rectangle([27 * s, 51.5 * s, 37 * s, 55.5 * s], radius=2 * s, fill=METAL)
    return img.resize((size, size), Image.LANCZOS)


draw(120).save(ROOT / "favicon-120.png")
draw(180).save(ROOT / "apple-touch-icon.png")
draw(48).save(ROOT / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
