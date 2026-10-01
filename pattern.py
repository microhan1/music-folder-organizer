"""Folder pattern -> relative path, file name cleanup, path length limit.

    {artist}/{album}/{track:02} - {title}
    {album_artist|artist}/...    "|" means: when the first is empty use the next

An empty placeholder takes the fallback text ("Unknown Artist"); one with no
fallback (track, disc) disappears together with the separator next to it.
"""
from __future__ import annotations

import os
import re
import unicodedata
from typing import Callable

from scan import Track, parse_number, parse_year

DEFAULT_PATTERN = "{album_artist|artist}/{album}/{track:02} - {title}"
PLACEHOLDERS = ("artist", "album_artist", "album", "title", "track", "disc", "year", "genre", "artist_sort")
NUMERIC = {"track", "disc"}
FALLBACK_FOR = {"artist": "artist", "album_artist": "artist", "artist_sort": "artist",
                "album": "album", "year": "year", "genre": "genre"}
_TOKEN = re.compile(r"\{([^{}:]*)(?::([^{}]*))?\}")
_BAD_CHARS = re.compile(r'[<>:"/\\|?*]')
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
MAX_SEGMENT = 120
MIN_SEGMENT = 20
PATH_LIMIT = 240


def validate(pattern: str) -> str:
    """'' when usable, else a lang key describing the problem."""
    if not pattern or not pattern.strip():
        return "err_pattern_empty"
    stripped = _TOKEN.sub("", pattern)
    if "{" in stripped or "}" in stripped:
        return "err_pattern_braces"
    for m in _TOKEN.finditer(pattern):
        for name in m.group(1).split("|"):
            if name.strip() not in PLACEHOLDERS:
                return "err_pattern_unknown"
    last = re.split(r"[/\\]", pattern.strip())[-1]
    if not _TOKEN.search(last):
        return "err_pattern_no_name"
    return ""


def placeholders_in(pattern: str) -> set[str]:
    return {n.strip() for m in _TOKEN.finditer(pattern) for n in m.group(1).split("|")}


def _value(name: str, track: Track, artist_map: Callable[[str], str], multi_disc: bool = True) -> str | int:
    tags = track.tags
    if name == "disc" and not multi_disc:
        return 0  # a one-disc album: "{disc}" and the separator next to it disappear
    if name == "artist":
        return artist_map(tags.artist) if tags.artist else ""
    if name == "album_artist":
        return artist_map(tags.album_artist) if tags.album_artist else ""
    if name == "year":
        return parse_year(tags.year)
    if name in NUMERIC:
        number = parse_number(getattr(tags, name))
        if not number and name == "track":
            # "01.Sweet Planet.mp3" with no track tag still keeps its order
            m = re.match(r"\s*(\d{1,3})(?!\d)", track.name)
            number = int(m.group(1)) if m else 0
        return number
    return getattr(tags, name, "") or ""


def _format(value: str | int, spec: str) -> str:
    if not spec:
        return str(value)
    try:
        return format(value, spec)
    except (ValueError, TypeError):
        return str(value)


def render(pattern: str, track: Track, fallbacks: dict[str, str],
           artist_map: Callable[[str], str] = lambda s: s, multi_disc: bool = True) -> list[str]:
    """Sanitized path segments; the last one is the file name without extension.
    ``multi_disc`` False (the album has one disc) leaves "{disc}" empty."""
    stem = os.path.splitext(track.name)[0]
    out: list[str] = []
    raws = re.split(r"[/\\]", pattern.strip().strip("/\\"))
    for pos, raw in enumerate(raws):
        emptied = False

        def sub(m: re.Match) -> str:
            nonlocal emptied
            names = [n.strip() for n in m.group(1).split("|")]
            for name in names:
                value = _value(name, track, artist_map, multi_disc)
                if value not in ("", 0):
                    return _format(value, m.group(2) or "")
            last = names[-1]
            if last == "title":
                return stem
            if last in FALLBACK_FOR:
                return fallbacks.get(FALLBACK_FOR[last], "")
            emptied = True
            return ""

        seg = _TOKEN.sub(sub, raw)
        if emptied:
            seg = _tidy_separators(seg)
            if not seg and pos < len(raws) - 1:
                continue  # "{artist}/{disc}/{title}" without a disc: no "_" folder
        out.append(sanitize(seg))
    return out


def _tidy_separators(seg: str) -> str:
    seg = re.sub(r"^[\s\-–_.]+", "", seg)
    seg = re.sub(r"[\s\-–_]+$", "", seg)
    seg = re.sub(r"\s+[-–]\s+[-–]\s+", " - ", seg)
    seg = re.sub(r"\(\s*\)|\[\s*\]", "", seg)
    return seg.strip()


def sanitize(seg: str) -> str:
    seg = unicodedata.normalize("NFC", seg)
    seg = _CONTROL.sub(" ", seg)  # a line break in a tag separates words
    seg = _BAD_CHARS.sub("_", seg)
    seg = re.sub(r"\s+", " ", seg).strip()
    seg = seg.rstrip(" .")
    if len(seg) > MAX_SEGMENT:
        seg = seg[:MAX_SEGMENT].rstrip(" .")
    if seg.split(".")[0].upper() in RESERVED:
        seg = seg + "_"
    return seg or "_"


def fit(root: str, dirs: list[str], stem: str, ext: str, limit: int = PATH_LIMIT) -> tuple[list[str], str, bool]:
    """Shorten the file name, then the longest folder names, until the absolute
    path fits in ``limit`` characters. Returns (dirs, stem, shortened)."""
    dirs = list(dirs)

    def length() -> int:
        return len(os.path.join(root, *dirs, stem + ext))

    excess = length() - limit
    if excess <= 0:
        return dirs, stem, False
    if len(stem) > MIN_SEGMENT:
        stem = stem[: max(MIN_SEGMENT, len(stem) - excess)].rstrip(" .") or "_"
    while (excess := length() - limit) > 0:
        i = max(range(len(dirs)), key=lambda k: len(dirs[k]), default=-1)
        if i < 0 or len(dirs[i]) <= MIN_SEGMENT:
            break
        dirs[i] = dirs[i][: max(MIN_SEGMENT, len(dirs[i]) - excess)].rstrip(" .") or "_"
    return dirs, stem, True
