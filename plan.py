"""The preview: where every file would go and what else moves with it.

Nothing here touches the disk except existence checks. mover.execute() runs a
Plan; building one again after every option change or checkbox click is
cheap (a few ms per thousand files).
"""
from __future__ import annotations

import dataclasses
import os
from collections import Counter

import pattern as pattern_mod
from artists import ArtistIndex
from dedupe import DupeGroup
from longpath import fs
from scan import (COVER_NAMES, LRC_EXT, SIDECAR_EXTS, TAGBAK_SUFFIX, ScanResult, Track, format_of,
                  is_junk, key_of, target_key)

# item status
OK, SAME, UNTAGGED, DUP, CONFLICT = "ok", "same", "untagged", "dup", "conflict"
# item action
MOVE, COPY, DUPE_MOVE, DUPE_TRASH, SKIP = "move", "copy", "dupe_move", "dupe_trash", "skip"


@dataclasses.dataclass
class Options:
    root: str
    dest: str  # == root: organize in place
    mode: str = "move"  # or "copy"
    pattern: str = pattern_mod.DEFAULT_PATTERN
    fallbacks: dict = dataclasses.field(default_factory=dict)
    include_untagged: bool = False
    remove_empty: bool = True
    dupes_action: str = "move"  # or "trash"
    dupes_name: str = "_Duplicates"
    move_sidecars: bool = True  # an album's .cue/.log/images/Artwork follow it when the whole album moves

    @property
    def in_place(self) -> bool:
        return key_of(self.dest) == key_of(self.root)


@dataclasses.dataclass
class Item:
    track: Track
    src: str
    dst: str  # absolute; for trash: ""
    status: str
    checked: bool
    action: str
    truncated: bool = False
    artist_note: str = ""
    guess: bool = False  # touched by an unconfirmed artist guess
    group: int = 0  # duplicate group id
    keep_name: bool = False  # a .cue sheet names this file: only its folder changes
    multi_disc: bool = False  # its album has two or more discs: "{disc}" shows

    @property
    def key(self) -> str:
        return key_of(self.src)

    @property
    def moves(self) -> bool:
        return self.checked and self.action != SKIP and self.status != SAME


@dataclasses.dataclass
class Companion:
    src: str
    dst: str
    op: str  # "move" or "copy"
    kind: str  # "lrc", "tagbak", "cover", "sidecar"
    owner: str = ""  # lrc / tagbak: key_of(item.src); sidecar: "dir:" + key_of(album folder). Skipped when that fails


@dataclasses.dataclass
class Plan:
    options: Options
    items: list[Item]
    companions: list[Companion]
    empty_dirs: list[str]

    def summary(self) -> dict[str, int]:
        return {
            "move": sum(1 for i in self.items if i.moves and i.action in (MOVE, COPY)),
            "same": sum(1 for i in self.items if i.status == SAME),
            "untagged": sum(1 for i in self.items if i.track.is_untagged() or i.track.error),
            "dupes": sum(1 for i in self.items if i.status == DUP),
            "folders": len(self.empty_dirs),
            "trash": sum(1 for i in self.items if i.moves and i.action == DUPE_TRASH),
            "sidecars": sum(1 for c in self.companions if c.kind == "sidecar"),
        }

    def sidecars(self) -> list[Companion]:
        return [c for c in self.companions if c.kind == "sidecar"]

    def untagged(self) -> list[Item]:
        return [i for i in self.items if i.track.is_untagged() or i.track.error]


def build(scan: ScanResult, opts: Options, index: ArtistIndex | None = None,
          groups: list[DupeGroup] | None = None, overrides: dict[str, bool] | None = None) -> Plan:
    """``overrides`` maps key_of(path) -> checked, the user's checkbox clicks."""
    overrides = overrides or {}
    artist_map = index.rep if index is not None else (lambda s: s)
    removable: dict[str, int] = {}
    for g in groups or []:
        if not g.applied:
            continue
        for t in g.removable():
            removable[key_of(t.path)] = g.id
    group_of = {key_of(t.path): g.id for g in groups or [] for t in g.members}
    copy = opts.mode == "copy"
    items: list[Item] = []
    for track in sorted(scan.tracks, key=lambda t: key_of(t.path)):
        k = key_of(track.path)
        untagged = track.is_untagged() or bool(track.error)
        if k in removable:
            action = SKIP if copy else (DUPE_TRASH if opts.dupes_action == "trash" else DUPE_MOVE)
            status, checked = DUP, not copy
        else:
            action = COPY if copy else MOVE
            status, checked = (UNTAGGED if untagged else OK), (opts.include_untagged or not untagged)
        if k in overrides:
            checked = overrides[k]
        if action == SKIP and checked:  # copy mode: a checked duplicate is copied like any file
            action = COPY
        item = Item(track, track.path, "", status, checked, action, group=group_of.get(k, 0))
        _artist_note(item, index)
        items.append(item)

    # folders whose .cue sheet names their own music files: renaming those files would break the sheet
    cue_dirs = {k for k, info in scan.dirs.items()
                if info.cue_refs & {f.lower() for f in info.files if format_of(f)}}
    multi = multi_disc_albums(scan.tracks, artist_map)
    claimed: set[str] = set()
    counters: dict[str, int] = {}
    disk = _Listing()
    for item in items:
        item.keep_name = key_of(item.track.folder) in cue_dirs and item.action not in (DUPE_MOVE, DUPE_TRASH)
        item.multi_disc = album_id(item.track, artist_map) in multi
        _place(item, scan.root, opts, artist_map, claimed, counters, disk)
    companions = _companions(items, scan, opts, disk)
    empty = _empty_dirs(items, companions, scan, opts) if (not copy and opts.remove_empty) else []
    return Plan(opts, items, companions, empty)


def album_id(track: Track, artist_map) -> tuple[str, str]:
    from artists import normalize, normalize_text

    who = track.tags.album_artist or track.tags.artist
    return normalize(artist_map(who)) if who else "", normalize_text(track.tags.album)


def multi_disc_albums(tracks: list[Track], artist_map=lambda s: s) -> set[tuple[str, str]]:
    """Albums with two or more discs: different disc numbers among their files, or a
    total such as "1/2" that says so."""
    discs: dict[tuple[str, str], set[int]] = {}
    totals: set[tuple[str, str]] = set()
    for t in tracks:
        if not t.tags.album:
            continue
        key = album_id(t, artist_map)
        parts = [p.strip() for p in (t.tags.disc or "").split("/")]
        if parts[0].isdigit() and int(parts[0]) > 0:
            discs.setdefault(key, set()).add(int(parts[0]))
        if len(parts) > 1 and parts[1].isdigit() and int(parts[1]) > 1:
            totals.add(key)
    return {k for k, v in discs.items() if len(v) > 1} | totals


def _artist_note(item: Item, index: ArtistIndex | None) -> None:
    if index is None:
        return
    tags = item.track.tags
    notes = []
    for name in dict.fromkeys(n for n in (tags.album_artist, tags.artist) if n):
        rep = index.rep(name)
        if rep != name:
            notes.append(f"{name} → {rep}")
        else:
            g = index.guess_for(name)
            if g is not None and g.proposed != name:
                notes.append(f"{name} → {g.proposed} ?")
                item.guess = True
    item.artist_note = ", ".join(notes)


def _target(item: Item, root: str, opts: Options, artist_map) -> tuple[list[str], str, str]:
    track = item.track
    ext = os.path.splitext(track.path)[1]
    if item.action in (DUPE_MOVE, DUPE_TRASH):
        rel = os.path.relpath(track.path, root)
        parts = rel.split(os.sep)
        return [opts.dupes_name, *parts[:-1]], os.path.splitext(parts[-1])[0], ext
    segs = pattern_mod.render(opts.pattern, track, opts.fallbacks, artist_map, item.multi_disc)
    if item.keep_name:
        return segs[:-1], os.path.splitext(track.name)[0], ext  # the pattern picks the folder only
    return segs[:-1], segs[-1], ext


class _Listing:
    """os.path.lexists for thousands of targets in a few hundred folders: one
    directory listing per folder instead of one disk query per file. Names compare
    with normcase, as Windows does; NFC and NFD stay different names, as on NTFS."""

    def __init__(self) -> None:
        self._dirs: dict[str, set[str]] = {}

    def lexists(self, path: str) -> bool:
        folder, name = os.path.split(path)
        key = os.path.normcase(folder)
        names = self._dirs.get(key)
        if names is None:
            try:
                names = {os.path.normcase(n) for n in os.listdir(fs(folder))}
            except OSError:  # no such folder yet (or a file in the way): nothing there
                names = set()
            self._dirs[key] = names
        return os.path.normcase(name) in names


def _place(item: Item, root: str, opts: Options, artist_map, claimed: set[str], counters: dict[str, int],
           disk: "_Listing") -> None:
    dirs, stem, ext = _target(item, root, opts, artist_map)
    base = opts.dest
    if item.action == DUPE_TRASH:
        item.dst = ""
        return
    dirs, fitted, item.truncated = pattern_mod.fit(base, dirs, stem, ext)
    dst = os.path.join(base, *dirs, fitted + ext)
    if os.path.normcase(dst) == os.path.normcase(item.src):
        item.dst = dst
        same_name = os.path.basename(dst) == os.path.basename(item.src)
        if same_name and item.status != DUP:
            item.status = SAME  # at most the folder case differs: leave it
            item.dst = item.src
        if item.checked:
            claimed.add(target_key(dst))
        return
    if not item.checked:
        item.dst = dst  # shown greyed; claims nothing
        return
    # numbering resumes where the last file with this name stopped: a thousand
    # identical names cost a thousand checks, not half a million
    name_key = target_key(dst)
    n = counters.get(name_key, 1)
    if n > 1:
        d2, s2, cut = pattern_mod.fit(base, dirs, f"{stem} ({n})", ext)
        dst = os.path.join(base, *d2, s2 + ext)
    own = os.path.normcase(item.src)
    while target_key(dst) in claimed or (disk.lexists(dst) and os.path.normcase(dst) != own):
        n += 1
        d2, s2, cut = pattern_mod.fit(base, dirs, f"{stem} ({n})", ext)
        item.truncated = item.truncated or cut
        dst = os.path.join(base, *d2, s2 + ext)
    counters[name_key] = n
    claimed.add(target_key(dst))
    if os.path.normcase(dst) == own and os.path.basename(dst) == os.path.basename(item.src):
        # "Song (2).mp3" from an earlier run is already where it belongs
        item.dst = item.src
        if item.status != DUP:
            item.status = SAME
        return
    if n > 1 and item.status not in (DUP,):
        item.status = CONFLICT
    item.dst = dst


# ------------------------------------------------------------------ companions
def _companions(items: list[Item], scan: ScanResult, opts: Options, disk: "_Listing") -> list[Companion]:
    out: list[Companion] = []
    taken: set[str] = set()
    by_dir: dict[str, list[Item]] = {}
    for item in items:
        by_dir.setdefault(key_of(item.track.folder), []).append(item)

    def add(src: str, dst: str, op: str, kind: str, owner: str = "") -> None:
        if key_of(src) in taken or target_key(dst) == target_key(src):
            return
        if target_key(dst) in taken or disk.lexists(dst):
            return
        taken.add(key_of(src))
        taken.add(target_key(dst))
        out.append(Companion(src, dst, op, kind, owner))

    for dkey, dir_items in by_dir.items():
        info = scan.dirs.get(dkey)
        if info is None:
            continue
        lower = {name.lower(): name for name in info.files}
        for item in dir_items:
            if not item.moves or item.action == DUPE_TRASH:
                continue
            op = "copy" if item.action == COPY else "move"
            src_stem = os.path.splitext(item.track.name)[0]
            dst_dir, dst_name = os.path.split(item.dst)
            lrc = lower.get((src_stem + LRC_EXT).lower())
            if lrc:
                add(os.path.join(info.path, lrc), os.path.join(dst_dir, os.path.splitext(dst_name)[0] + LRC_EXT), op, "lrc", item.key)
            bak = lower.get((item.track.name + TAGBAK_SUFFIX).lower())
            if bak:
                add(os.path.join(info.path, bak), os.path.join(dst_dir, dst_name + TAGBAK_SUFFIX), op, "tagbak", item.key)
        covers = [lower[n] for n in sorted(lower) if n in COVER_NAMES]
        if not covers:
            continue
        leaving = [i for i in dir_items if i.moves]
        everyone_leaves = len(leaving) == len(dir_items) and opts.mode == "move"
        targets = Counter(os.path.dirname(i.dst) for i in leaving if i.dst)
        if not targets:
            continue
        # the folder most files went to; a tie goes to a non-duplicate folder, then the first
        ranked = sorted(targets, key=lambda d: (-targets[d], _is_dupe_dir(d, opts), d.casefold()))
        for name in covers:
            src = os.path.join(info.path, name)
            first, *rest = ranked
            add(src, os.path.join(first, name), "move" if everyone_leaves else "copy", "cover")
            for target in rest:
                _copy_cover(out, taken, src, os.path.join(target, name), disk)
    if opts.move_sidecars:
        for dkey, dir_items in by_dir.items():
            _sidecars(dkey, dir_items, scan, opts, add)
    return out


def _sidecars(dkey: str, dir_items: list[Item], scan: ScanResult, opts: Options, add) -> None:
    """When every music file of a folder goes to one new folder, the album's extras
    (.cue, .log, booklet, scans, an Artwork/ subfolder ...) go with it."""
    info = scan.dirs.get(dkey)
    if info is None or dkey in (key_of(scan.root), key_of(opts.dest)):
        return  # the roots hold this tool's own log and lists, and unrelated things
    if not all(i.moves and i.dst for i in dir_items):
        return
    targets = {target_key(os.path.dirname(i.dst)) for i in dir_items}
    if len(targets) != 1:
        return  # the album is split over several folders: its extras stay put
    target = os.path.dirname(dir_items[0].dst)
    if target_key(target) == target_key(info.path):
        return
    op = "copy" if opts.mode == "copy" else "move"
    owner = "dir:" + dkey
    for name in info.files:
        low = name.lower()
        if format_of(name) or is_junk(name) or low in COVER_NAMES:
            continue  # music, junk and covers have their own rules
        if os.path.splitext(low)[1] in SIDECAR_EXTS:
            add(os.path.join(info.path, name), os.path.join(target, name), op, "sidecar", owner)
    for sub in info.subdirs:
        files = _extras_only(key_of(os.path.join(info.path, sub)), scan)
        for path in files or []:
            add(path, os.path.join(target, os.path.relpath(path, info.path)), op, "sidecar", owner)


def _extras_only(dkey: str, scan: ScanResult) -> list[str] | None:
    """All files under a subfolder when it holds only album extras (and junk), else None.
    A subfolder with music in it is an album of its own, not an extra."""
    info = scan.dirs.get(dkey)
    if info is None:
        return None
    out = []
    for name in info.files:
        low = name.lower()
        if is_junk(name):
            continue
        if format_of(name) or os.path.splitext(low)[1] not in SIDECAR_EXTS:
            return None
        out.append(os.path.join(info.path, name))
    for sub in info.subdirs:
        inner = _extras_only(key_of(os.path.join(info.path, sub)), scan)
        if inner is None:
            return None
        out.extend(inner)
    return out


def _copy_cover(out: list[Companion], taken: set[str], src: str, dst: str, disk: "_Listing") -> None:
    # several copies of one cover share a source, so they bypass the "source used" check
    if target_key(dst) in taken or disk.lexists(dst):
        return
    taken.add(target_key(dst))
    out.append(Companion(src, dst, "copy", "cover"))


def _is_dupe_dir(path: str, opts: Options) -> bool:
    return key_of(path).startswith(key_of(os.path.join(opts.dest, opts.dupes_name)))


# ------------------------------------------------------------------ empty folders
def _empty_dirs(items: list[Item], companions: list[Companion], scan: ScanResult, opts: Options) -> list[str]:
    leaving: set[str] = {key_of(i.src) for i in items if i.moves}
    leaving |= {key_of(c.src) for c in companions if c.op == "move"}
    # folders that will receive files, and their parents, must stay
    keep: set[str] = {key_of(scan.root)}
    for d in [opts.dest] + [os.path.dirname(p) for p in [i.dst for i in items if i.moves and i.dst] + [c.dst for c in companions]]:
        while True:
            k = key_of(d)
            if k in keep:
                break
            keep.add(k)
            parent = os.path.dirname(d)
            if parent == d:
                break
            d = parent
    empty: dict[str, bool] = {}
    for dkey in sorted(scan.dirs, key=lambda k: -k.count(os.sep)):
        info = scan.dirs[dkey]
        if dkey in keep:
            empty[dkey] = False
            continue
        files_ok = all(is_junk(name) or key_of(os.path.join(info.path, name)) in leaving for name in info.files)
        subs_ok = all(empty.get(key_of(os.path.join(info.path, s)), False) for s in info.subdirs)
        empty[dkey] = files_ok and subs_ok
    return [scan.dirs[k].path for k in sorted(empty, key=lambda k: (-k.count(os.sep), k)) if empty[k]]
