"""Draw the start-up picture assets/splash.png shown by the exe while it unpacks.

    python assets/make_splash.py

The icon on the left, the tool's name in the four UI languages on the right.
Kept small (a few KB): PyInstaller shows it before Python starts, and
gui.launch() closes it once the window is drawn.
"""
from __future__ import annotations

import os

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
SCALE = 4  # draw large, shrink for smooth edges
W, H = 300, 104
INK, MUTED, RULE = "#1f2328", "#6b7280", "#e3e6ea"
FONTS = r"C:\Windows\Fonts"
# (text, font file, bold weight) in the order of the README language links
NAMES = (
    ("음악 폴더 정리", "malgunbd.ttf"),
    ("Music Folder Organizer", "segoeuib.ttf"),
    ("音乐文件夹整理", "msyhbd.ttc"),
    ("音楽フォルダー整理", "YuGothB.ttc"),
)


def draw() -> Image.Image:
    s = SCALE
    img = Image.new("RGB", (W * s, H * s), "white")
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, W * s - 1, H * s - 1), outline=RULE, width=s)
    icon = Image.open(os.path.join(HERE, "icon.png")).convert("RGBA")
    size = 64 * s
    icon = icon.resize((size, size), Image.LANCZOS)
    img.paste(icon, (18 * s, (H * s - size) // 2), icon)
    x = 98 * s
    top, step = 25 * s, 18 * s
    for i, (text, font_file) in enumerate(NAMES):
        font = ImageFont.truetype(os.path.join(FONTS, font_file), 13 * s)
        d.text((x, top + i * step), text, font=font, fill=INK if i < 2 else MUTED, anchor="lm")
    small = img.resize((W, H), Image.LANCZOS)
    return small.quantize(colors=128, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)


def main() -> None:
    out = os.path.join(HERE, "splash.png")
    draw().save(out, optimize=True)
    print(out, os.path.getsize(out), "bytes")


if __name__ == "__main__":
    main()
