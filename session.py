"""Glue shared by the GUI and the CLI: from prefs + folders to scan, artist
index, duplicate groups and a Plan."""
from __future__ import annotations

import os

import i18n
import plan as plan_mod
from artists import ArtistIndex, folder_key, load_aliases
from mover import key_in
from prefs import Prefs
from scan import ScanResult, Track, key_of


def dupes_folder_names(p: Prefs) -> set[str]:
    """Every name a duplicates folder may have: each language's default, the current
    one and any used before (a renamed folder must not be scanned as music)."""
    return i18n.all_values("dupes_folder") | {p.dupes_name()} | set(p.dupes_folder_history)


def excludes(root: str, dest: str, p: Prefs) -> list[str]:
    """Folders a scan must skip: every dupes folder name at the source and the
    destination root, and the destination itself when it sits inside the source."""
    out = []
    for base in {root, dest}:
        out += [os.path.join(base, name) for name in dupes_folder_names(p)]
    if key_of(dest) != key_of(root) and key_in(dest, root):
        out.append(dest)
    return out


def existing_folders(scan: ScanResult | None, dest: str | None = None, depth: int = 3) -> set[str]:
    """Names (folder_key) of folders already on disk in the source and, when it lies
    elsewhere, the first levels of the destination."""
    names = {folder_key(os.path.basename(info.path)) for info in scan.dirs.values()} if scan else set()
    if dest and os.path.isdir(dest) and not (scan and key_in(dest, scan.root)):
        base = os.path.abspath(dest).rstrip(os.sep).count(os.sep)
        for here, subdirs, _ in os.walk(dest):
            names.update(folder_key(d) for d in subdirs)
            if here.rstrip(os.sep).count(os.sep) - base >= depth - 1:
                subdirs[:] = []
    return names


def make_index(tracks: list[Track], p: Prefs, existing: set[str] | None = None) -> tuple[ArtistIndex, str]:
    aliases, error = load_aliases(p.artists_file())
    index = ArtistIndex(tracks, aliases, p.artist_name_preference, p.artist_no_merge, p.artist_rejected,
                        existing=existing)
    return index, error


def options(p: Prefs, root: str, dest: str | None, mode: str) -> plan_mod.Options:
    return plan_mod.Options(
        root=os.path.abspath(root),
        dest=os.path.abspath(dest or root),
        mode=mode,
        pattern=p.pattern,
        fallbacks={k: p.fallback(k) for k in ("artist", "album", "year", "genre")},
        include_untagged=p.include_untagged,
        remove_empty=p.remove_empty,
        dupes_action=p.dupes_action,
        dupes_name=p.dupes_name(),
        move_sidecars=p.move_sidecars,
    )


def preferred_paths(scan: ScanResult, opts: plan_mod.Options, index: ArtistIndex) -> set[str]:
    """Files already sitting where the pattern would put them; a duplicate group keeps those first."""
    probe = plan_mod.build(scan, opts, index)
    return {i.key for i in probe.items if i.status == plan_mod.SAME}
