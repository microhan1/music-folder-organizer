"""Draw the app icon and write assets/icon.ico (multi-size) and assets/icon.png.

    python assets/make_icon.py

An amber folder with a white note on a green rounded square: the sibling of
music-tag-filler's blue note-and-tag icon. Drawn large and scaled down so the
small sizes stay clean.
"""
from __future__ import annotations

import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
SIZE = 1024
TOP = (16, 185, 129)
BOTTOM = (4, 120, 87)
WHITE = (255, 255, 255)
AMBER = (251, 191, 36)
AMBER_BACK = (217, 119, 6)


def _gradient(size: int) -> Image.Image:
    im = Image.new("RGB", (size, size))
    px = im.load()
    for y in range(size):
        t = y / (size - 1)
        row = tuple(round(TOP[i] + (BOTTOM[i] - TOP[i]) * t) for i in range(3))
        for x in range(size):
            px[x, y] = row
    return im


def draw(size: int = SIZE) -> Image.Image:
    s = size / 1024
    base = _gradient(size).convert("RGBA")
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((40 * s, 40 * s, 984 * s, 984 * s), radius=220 * s, fill=255)
    icon = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    icon.paste(base, (0, 0), mask)

    # folder: back panel with a tab, then the front panel with a soft shadow
    d = ImageDraw.Draw(icon)
    d.rounded_rectangle((170 * s, 250 * s, 470 * s, 360 * s), radius=40 * s, fill=AMBER_BACK)
    d.rounded_rectangle((170 * s, 300 * s, 854 * s, 780 * s), radius=50 * s, fill=AMBER_BACK)
    shadow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((170 * s, 392 * s, 854 * s, 800 * s), radius=50 * s, fill=(0, 0, 0, 60))
    icon.alpha_composite(shadow)
    d = ImageDraw.Draw(icon)
    d.rounded_rectangle((170 * s, 380 * s, 854 * s, 790 * s), radius=50 * s, fill=AMBER)

    # a single eighth note on the folder front
    cx, cy = 470 * s, 680 * s
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(layer).ellipse((cx - 92 * s, cy - 68 * s, cx + 92 * s, cy + 68 * s), fill=WHITE)
    icon.alpha_composite(layer.rotate(20, center=(cx, cy), resample=Image.BICUBIC))
    d = ImageDraw.Draw(icon)
    stem_x = cx + 62 * s
    d.rectangle((stem_x, 430 * s, stem_x + 34 * s, cy), fill=WHITE)
    d.polygon([(stem_x, 430 * s), (stem_x + 34 * s, 430 * s), (stem_x + 170 * s, 520 * s),
               (stem_x + 150 * s, 575 * s), (stem_x + 34 * s, 500 * s)], fill=WHITE)
    return icon


def main() -> None:
    big = draw(SIZE)
    big.resize((256, 256), Image.LANCZOS).save(os.path.join(HERE, "icon.png"))
    sizes = [16, 24, 32, 48, 64, 128, 256]
    big.resize((256, 256), Image.LANCZOS).save(os.path.join(HERE, "icon.ico"), sizes=[(n, n) for n in sizes])
    print("wrote", os.path.join(HERE, "icon.ico"), "and icon.png")


if __name__ == "__main__":
    main()
