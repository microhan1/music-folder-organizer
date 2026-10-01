"""Duplicate finder in three stages.

  1. identical files     same size -> same SHA-1
  2. same song           same (unified) artist + normalized title, length within 2 s
  3. same recording      local Chromaprint comparison (fpcalc -raw), optional

Groups from all stages are merged; each group recommends one file to keep:
lossless first, then bitrate, sample rate, a file already in place, the
shorter path, the name.
"""
from __future__ import annotations

import concurrent.futures
import dataclasses
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
from array import array
from typing import Callable

import i18n
from artists import normalize, normalize_text
from scan import Track, key_of

LENGTH_TOLERANCE = 2.0
FP_SECONDS_PER_FILE = 0.4
FP_LENGTH = 120  # seconds of audio fpcalc reads
FP_COMPARE_ITEMS = 240  # ~30 s of fingerprint
FP_MIN_ITEMS = 40
FP_MAX_OFFSET = 4
FP_THRESHOLD = 0.15  # share of differing bits below which two files are one recording
CHUNK = 1024 * 1024

ProgressFn = Callable[[str, int, int], None]  # (phase, done, total)


@dataclasses.dataclass
class DupeGroup:
    id: int
    stage: int  # strongest evidence needed: 1 identical, 2 same song, 3 sound
    members: list[Track]
    keep: set[str]  # key_of(path) of files to keep
    applied: bool = True  # the preview sends the others away only when applied

    def removable(self) -> list[Track]:
        return [t for t in self.members if key_of(t.path) not in self.keep]


def fp_workers() -> int:
    return max(1, min(4, (os.cpu_count() or 2) // 2))


def estimate_seconds(count: int) -> int:
    return int(round(count * FP_SECONDS_PER_FILE / fp_workers()))


def find_fpcalc(configured: str = "") -> str | None:
    candidates = [configured] if configured else []
    exe = "fpcalc.exe" if sys.platform == "win32" else "fpcalc"
    candidates += [os.path.join(i18n.resource_dir(), "third_party", exe),
                   os.path.join(i18n.app_dir(), "third_party", exe)]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return shutil.which("fpcalc")


def sha1_file(path: str, cancel: threading.Event | None = None) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            if cancel is not None and cancel.is_set():
                raise InterruptedError
            h.update(chunk)
    return h.hexdigest()


def rank_key(track: Track, preferred: set[str]):
    return (0 if track.lossless else 1, -track.bitrate, -track.sample_rate,
            0 if key_of(track.path) in preferred else 1, len(track.path), track.path.casefold())


class _UF:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}
        self.stage: dict[str, int] = {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str, stage: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:  # already joined by stronger evidence
            return
        self.parent[rb] = ra
        self.stage[ra] = max(self.stage.get(ra, 0), self.stage.get(rb, 0), stage)


def find(tracks: list[Track], artist_map: Callable[[str], str] = lambda s: s, *,
         fingerprint: bool = False, fpcalc: str | None = None, preferred: set[str] | None = None,
         progress: ProgressFn | None = None, cancel: threading.Event | None = None) -> list[DupeGroup]:
    preferred = preferred or set()
    by_key = {key_of(t.path): t for t in tracks}
    uf = _UF()

    # 1. identical: hash only files that share a size
    by_size: dict[int, list[Track]] = {}
    for t in tracks:
        if t.size > 0:
            by_size.setdefault(t.size, []).append(t)
    to_hash = [t for group in by_size.values() if len(group) > 1 for t in group]
    hashes: dict[str, list[str]] = {}
    for i, t in enumerate(to_hash):
        if cancel is not None and cancel.is_set():
            return []
        try:
            digest = sha1_file(t.path, cancel)
        except InterruptedError:
            return []
        except OSError:
            continue
        hashes.setdefault(f"{t.size}:{digest}", []).append(key_of(t.path))
        if progress is not None:
            progress("hash", i + 1, len(to_hash))
    for keys in hashes.values():
        for k in keys[1:]:
            uf.union(keys[0], k, 1)

    # 2. same song: unified artist + title, close lengths
    by_song: dict[tuple[str, str], list[Track]] = {}
    for t in tracks:
        artist = t.tags.artist or t.tags.album_artist
        title = normalize_text(t.tags.title)
        if artist and title:
            by_song.setdefault((normalize(artist_map(artist)), title), []).append(t)
    for group in by_song.values():
        _link_close_lengths(group, uf, 2)

    # 3. same recording by sound
    if fingerprint:
        exe = fpcalc or find_fpcalc()
        if exe:
            prints = _fingerprints(exe, tracks, progress, cancel)
            if cancel is not None and cancel.is_set():
                return []
            _link_by_sound(tracks, prints, uf)

    members: dict[str, list[Track]] = {}
    for k in list(uf.parent):
        members.setdefault(uf.find(k), []).append(by_key[k])
    groups: list[DupeGroup] = []
    for root, ts in members.items():
        if len(ts) < 2:
            continue
        ts.sort(key=lambda t: rank_key(t, preferred))
        groups.append(DupeGroup(0, uf.stage.get(root, 2), ts, {key_of(ts[0].path)}))
    groups.sort(key=lambda g: g.members[0].path.casefold())
    for i, g in enumerate(groups, 1):
        g.id = i
    return groups


def _link_close_lengths(group: list[Track], uf: _UF, stage: int) -> None:
    if len(group) < 2:
        return
    group = sorted(group, key=lambda t: t.length)
    anchor = group[0]
    for t in group[1:]:
        if t.length - anchor.length <= LENGTH_TOLERANCE:
            uf.union(key_of(anchor.path), key_of(t.path), stage)
        else:
            anchor = t


# ------------------------------------------------------------------ fingerprints
def run_fpcalc(exe: str, path: str) -> list[int] | None:
    flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
    try:
        out = subprocess.run([exe, "-raw", "-json", "-length", str(FP_LENGTH), path],
                             capture_output=True, timeout=120, creationflags=flags)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    try:
        data = json.loads(out.stdout.decode("utf-8", "replace"))
        fp = [int(x) & 0xFFFFFFFF for x in data.get("fingerprint") or []]
    except (ValueError, TypeError):
        return None
    return fp or None


def _fingerprints(exe: str, tracks: list[Track], progress: ProgressFn | None,
                  cancel: threading.Event | None) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    todo = [t for t in tracks if t.length > 0]
    done = 0
    with concurrent.futures.ThreadPoolExecutor(fp_workers()) as pool:
        futures = {pool.submit(run_fpcalc, exe, t.path): t for t in todo}
        for fut in concurrent.futures.as_completed(futures):
            if cancel is not None and cancel.is_set():
                for f in futures:
                    f.cancel()
                break
            fp = fut.result()
            if fp and len(fp) >= FP_MIN_ITEMS:
                out[key_of(futures[fut].path)] = array("I", fp[: FP_COMPARE_ITEMS + FP_MAX_OFFSET]).tobytes()
            done += 1
            if progress is not None:
                progress("fingerprint", done, len(todo))
    return out


def bit_error_rate(a: bytes, b: bytes) -> float:
    """Smallest share of differing bits over small alignments of two raw prints."""
    best = 1.0
    na, nb = len(a) // 4, len(b) // 4
    for off in range(-FP_MAX_OFFSET, FP_MAX_OFFSET + 1):
        sa, sb = max(0, off), max(0, -off)
        n = min(na - sa, nb - sb, FP_COMPARE_ITEMS)
        if n < FP_MIN_ITEMS:
            continue
        x = int.from_bytes(a[sa * 4:(sa + n) * 4], "little") ^ int.from_bytes(b[sb * 4:(sb + n) * 4], "little")
        best = min(best, x.bit_count() / (32 * n))
    return best


def _link_by_sound(tracks: list[Track], prints: dict[str, bytes], uf: _UF) -> None:
    items = sorted((t for t in tracks if key_of(t.path) in prints), key=lambda t: t.length)
    for i, a in enumerate(items):
        ka = key_of(a.path)
        for b in items[i + 1:]:
            if b.length - a.length > LENGTH_TOLERANCE:
                break
            kb = key_of(b.path)
            if uf.find(ka) == uf.find(kb):
                continue
            if bit_error_rate(prints[ka], prints[kb]) < FP_THRESHOLD:
                uf.union(ka, kb, 3)
