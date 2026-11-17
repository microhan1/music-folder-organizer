"""Check boxes with a tick (✓) instead of the clam theme's X (2026-11-17).

The clam theme draws a ticked ttk.Checkbutton as a white box with a black X, which reads as
"no" or "off". Only the TCheckbutton.indicator element is swapped for an image element; the
theme, the label and the keyboard focus ring are untouched.

The box images are drawn here at the window's own pixel size (no image files, nothing to bundle
with PyInstaller), so they stay sharp at every display scale:
  ticked   = box filled with the accent colour, white tick
  unticked = white box, grey border (accent border under the mouse)
  disabled = pale grey

The same file is in music-tag-filler and music-folder-organizer: change both together.
"""
from __future__ import annotations

import math
import tkinter as tk
from tkinter import ttk

ELEMENT = "Tick.Checkbutton.indicator"
OLD_ELEMENT = "Checkbutton.indicator"
KEEP_ATTR = "_tick_check_images"

WHITE = "#ffffff"
OFF_BORDER = "#9ca3af"
DISABLED_FILL = "#f3f4f6"
DISABLED_BORDER = "#d1d5db"
DISABLED_TICKED = "#c4c9d2"

# the tick as a polyline in box units (0..1), and its half stroke width
TICK = ((0.25, 0.52), (0.43, 0.70), (0.76, 0.33))
TICK_HALF = 0.075
RADIUS = 0.18  # corner radius in box units
# which image ttk draws: the first row whose states all hold wins (ttk's own rule); else "off"
STATE_MAP = (
    (("disabled", "selected"), "on_disabled"),
    (("disabled",), "off_disabled"),
    (("pressed", "selected"), "on_active"),
    (("active", "selected"), "on_active"),
    (("selected",), "on"),
    (("pressed",), "off_pressed"),
    (("active",), "off_active"),
)


def box_size(scale: float) -> int:
    """The size the clam indicator had (indicatorsize 14 at the window's scale, plus its border)."""
    return int(round(15 * scale))


def _rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


def _mix(a: tuple[float, float, float], b: tuple[float, float, float], t: float) -> tuple[float, float, float]:
    return a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t


def _clamp(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def _box_distance(x: float, y: float, size: float, radius: float) -> float:
    """Signed distance from (x, y) to the rounded box [0, size] x [0, size] (negative inside)."""
    half = size / 2
    qx = abs(x - half) - (half - radius)
    qy = abs(y - half) - (half - radius)
    outside = math.hypot(max(qx, 0.0), max(qy, 0.0))
    return outside + min(max(qx, qy), 0.0) - radius


def _segment_distance(x: float, y: float, a: tuple[float, float], b: tuple[float, float]) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    t = _clamp(((x - a[0]) * dx + (y - a[1]) * dy) / (dx * dx + dy * dy))
    return math.hypot(x - (a[0] + t * dx), y - (a[1] + t * dy))


def render(size: int, fill: str, border: str, tick: str | None, bg: str) -> list[list[str | None]]:
    """Rows of '#rrggbb' (None = transparent) for one box, edges smoothed against bg."""
    bw = max(1.0, round(size / 15))
    radius = max(2.0, size * RADIUS)
    points = [(px * size, py * size) for px, py in TICK]
    half = max(1.0, size * TICK_HALF)
    f, b, back = _rgb(fill), _rgb(border), _rgb(bg)
    k = _rgb(tick) if tick else None
    rows: list[list[str | None]] = []
    for j in range(size):
        row: list[str | None] = []
        for i in range(size):
            x, y = i + 0.5, j + 0.5
            d = _box_distance(x, y, size, radius)
            cover = _clamp(0.5 - d)
            if cover <= 0:
                row.append(None)
                continue
            color = _mix(b, f, _clamp(0.5 - (d + bw)))
            if k is not None:
                dt = min(_segment_distance(x, y, points[0], points[1]), _segment_distance(x, y, points[1], points[2]))
                color = _mix(color, k, _clamp(half + 0.5 - dt))
            color = _mix(back, color, cover)
            row.append("#%02x%02x%02x" % tuple(int(round(c)) for c in color))
        rows.append(row)
    return rows


def make_image(master: tk.Misc, rows: list[list[str | None]], gap: int) -> tk.PhotoImage:
    """A PhotoImage of the box with `gap` transparent columns on its right (the space before the text)."""
    size = len(rows)
    img = tk.PhotoImage(master=master, width=size + gap, height=size)
    for j, row in enumerate(rows):
        for i, color in enumerate(row):
            if color is not None:
                img.put(color, (i, j))
    return img


def image_for(states: set[str] | frozenset[str]) -> str:
    """The image name drawn for a widget in these ttk states (same first-match rule as STATE_MAP)."""
    for need, name in STATE_MAP:
        if all(st in states for st in need):
            return name
    return "off"


def colours(accent: str, accent_dark: str, surface: str = WHITE) -> dict[str, tuple[str, str, str | None]]:
    """(fill, border, tick) for every state."""
    return {
        "off": (surface, OFF_BORDER, None),
        "off_active": (surface, accent, None),
        "off_pressed": (surface, accent_dark, None),
        "on": (accent, accent, WHITE),
        "on_active": (accent_dark, accent_dark, WHITE),
        "off_disabled": (DISABLED_FILL, DISABLED_BORDER, None),
        "on_disabled": (DISABLED_TICKED, DISABLED_TICKED, WHITE),
    }


def _swap(layout: list, old: str, new: str) -> list:
    out = []
    for name, opts in layout:
        opts = dict(opts)
        if "children" in opts:
            opts["children"] = _swap(opts["children"], old, new)
        out.append((new if name == old else name, opts))
    return out


def install(style: ttk.Style, master: tk.Misc, *, scale: float, accent: str, accent_dark: str, bg: str,
            gap: int, size: int | None = None) -> dict[str, tk.PhotoImage]:
    """Draw the boxes and use them as the indicator of every TCheckbutton in this window.

    size: the box in pixels (default box_size(scale)); gap: transparent pixels before the text.

    Calling it again for the same window (a style rebuilt on a language change) keeps the first
    images: the element already holds them and cannot be made twice. The images are also kept on
    the window, since Tk drops an image whose Python object is collected. Toolbutton-style check
    buttons (chips) have no indicator and are not affected.
    """
    root = master.winfo_toplevel()
    images = getattr(root, KEEP_ATTR, None)
    if images is None:
        size = size or box_size(scale)
        images = {state: make_image(master, render(size, *spec, bg=bg), gap)
                  for state, spec in colours(accent, accent_dark).items()}
        style.element_create(ELEMENT, "image", images["off"],
                             *[(*states, images[name]) for states, name in STATE_MAP], sticky="")
        setattr(root, KEEP_ATTR, images)
    style.layout("TCheckbutton", _swap(style.layout("TCheckbutton"), OLD_ELEMENT, ELEMENT))
    return images
