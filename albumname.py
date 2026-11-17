"""Artist, album and year read from the file name of a disc image or archive (.iso, .zip ...).

These files carry no tags, but their names often say what they are:
    "Minako Yoshida - Spangles.iso"            -> Minako Yoshida / Spangles
    "Rie Yoshizawa - JUICY (2000).zip"          -> Rie Yoshizawa / JUICY / 2000
    "A - Bells (Special Edition) (1986) (2002, Label) [Flac].zip" -> A / Bells (Special Edition) / 1986

Only the file name is read, never the folder it sits in. The folder is what a run changes, and a guess that
depended on it could read the folder a run just made as a new artist and album, and move the file again
(LESSONS A27-A33). The file name is not touched by the move, so the same name gives the same answer on the
next run. A guess is only ever a proposal: plan.py shows it unchecked.
"""
from __future__ import annotations

import os
import re
from typing import NamedTuple

# "0928_[J-Pop] " in front: an upload date and a genre tag
_PREFIX = re.compile(r"^\s*(?:\d{3,8}[_\s]+)?(?:\[[^\]]*\]\s*)*")
# the last "(...)" or "[...]" group of a name
_TAIL = re.compile(r"^(?P<head>.*\S)\s*(?:\((?P<paren>[^()]*)\)|\[(?P<bracket>[^\[\]]*)\])\s*$")
_YEAR = re.compile(r"(?<!\d)((?:19|20)\d\d)(?!\d)")
_COPY = re.compile(r"^\d{1,2}$")  # "(1)": the number a copy of the same name got
MAX_NAME = 120


class Guess(NamedTuple):
    artist: str
    album: str
    year: str  # "" when the name has none


def guess(file_name: str) -> Guess | None:
    """The artist, album and year a disc image's file name says, or None when it does not say them."""
    stem = os.path.splitext(file_name)[0]
    stem = stem[_PREFIX.match(stem).end():]
    if " - " not in stem:
        return None
    artist, rest = (part.strip() for part in stem.split(" - ", 1))
    year = ""
    while True:
        m = _TAIL.match(rest)
        if m is None:
            break
        paren = m.group("paren")
        if paren is not None:  # a "(...)" group goes only when it is a year, a year with a label, or a copy number
            found = _YEAR.search(paren)
            if found:
                year = found.group(1)  # the leftmost one wins: the first release, not the reissue
            elif not _COPY.match(paren.strip()):
                break
        rest = m.group("head")
    album = rest.strip()
    if not artist or not album or len(artist) > MAX_NAME or len(album) > MAX_NAME:
        return None
    if not (re.search(r"[^\W\d_]", artist) and re.search(r"[^\W\d_]", album)):
        return None  # "01 - 02": no letters, so not names
    return Guess(artist, album, year)
