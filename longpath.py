"""Windows paths longer than MAX_PATH.

Every open, listing, rename and delete goes through fs(); paths shown to the
user or written to the log stay in the plain form.

Shared with photo-organizer (same file); keep the two in step.
"""
from __future__ import annotations

import os
import sys
from typing import Iterator

LONG_PATH = 248  # from this length on, Windows file calls need the \\?\ form


def fs(path: str) -> str:
    """The form Windows file calls accept for long paths (\\\\?\\C:\\... or \\\\?\\UNC\\...)."""
    if sys.platform != "win32" or not path:
        return path
    p = os.path.abspath(path)
    if len(p) < LONG_PATH or p.startswith("\\\\?\\"):
        return p
    if p.startswith("\\\\"):
        return "\\\\?\\UNC\\" + p[2:]
    return "\\\\?\\" + p


def plain(path: str) -> str:
    """A path as people read it (without the \\\\?\\ prefix fs() may have added)."""
    if path.startswith("\\\\?\\UNC\\"):
        return "\\\\" + path[8:]
    return path[4:] if path.startswith("\\\\?\\") else path


def walk(top: str) -> Iterator[tuple[str, list[str], list[str]]]:
    """os.walk (top-down, ``subdirs`` may be pruned in place) that reaches folders deeper than
    260 characters. os.walk joins child paths itself and hides listing errors, so below a
    certain depth it silently stops finding anything when long paths are off; this builds
    each path from the plain parent and lists it through fs(). Yields plain paths. Folders it
    cannot list are skipped. A symlink to a folder is listed as a file, never entered."""
    stack = [top]
    while stack:
        here = stack.pop()
        try:
            with os.scandir(fs(here)) as it:
                entries = list(it)
        except OSError:
            continue
        subdirs: list[str] = []
        files: list[str] = []
        for e in entries:
            try:
                is_dir = e.is_dir(follow_symlinks=False)
            except OSError:
                is_dir = False
            (subdirs if is_dir else files).append(e.name)
        yield here, subdirs, files
        stack.extend(os.path.join(here, d) for d in reversed(subdirs))  # after the caller pruned
