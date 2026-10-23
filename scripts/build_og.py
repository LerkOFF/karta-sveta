#!/usr/bin/env python3
"""Рисует og.png (1200x630) для превью ссылок в поиске и мессенджерах. Нужен Pillow, шрифты macOS."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parent.parent / "og.png"
FONTS = "/System/Library/Fonts/Supplemental/"

W, H = 1200, 630
im = Image.new("RGB", (W, H), "#1e1a16")
d = ImageDraw.Draw(im)

# лампа как в favicon.svg
cx, cy = 230, 300
d.ellipse([cx - 120, cy - 120, cx + 120, cy + 120], fill="#ffd23f")
for dx, dy, ex, ey in [(0, -165, 0, -205), (-150, -80, -185, -110), (150, -80, 185, -110), (-190, 0, -240, 0), (190, 0, 235, 0)]:
    d.line([cx + dx, cy + dy, cx + ex, cy + ey], fill="#ffd23f", width=14)
d.polygon([(cx - 55, cy + 105), (cx + 55, cy + 105), (cx + 42, cy + 165), (cx - 42, cy + 165)], fill="#ffd23f")
d.rounded_rectangle([cx - 62, cy + 175, cx + 62, cy + 205], radius=14, fill="#ddd4c6")
d.rounded_rectangle([cx - 46, cy + 215, cx + 46, cy + 245], radius=14, fill="#ddd4c6")

bold = ImageFont.truetype(FONTS + "Arial Bold.ttf", 108)
text = ImageFont.truetype(FONTS + "Arial.ttf", 44)
small = ImageFont.truetype(FONTS + "Arial Bold.ttf", 40)
d.text((510, 190), "Карта света", font=bold, fill="#f3efe6")
d.text((514, 330), "Донецк: где есть свет", font=text, fill="#ddd4c6")
d.text((514, 386), "и где его нет", font=text, fill="#ddd4c6")
d.text((514, 500), "свет-донецк.рф", font=small, fill="#ffd23f")
im.save(OUT, optimize=True)
