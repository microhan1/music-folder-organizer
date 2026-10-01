"""settings.json values with type checking.

The file sits next to the exe where anyone can edit it, so nothing in it is
trusted: a wrong type or an unknown value falls back to the default.
"""
from __future__ import annotations

import dataclasses
import os

import i18n
from pattern import DEFAULT_PATTERN
PRESETS = (
    DEFAULT_PATTERN,
    "{album_artist|artist}/{album}/{disc}-{track:02} - {title}",  # "2-05 - Title" on multi-disc albums only
    "{artist}/{title}",
    "{year}/{artist} - {title}",
)
FALLBACK_KEYS = ("artist", "album", "year", "genre")


@dataclasses.dataclass
class Prefs:
    pattern: str = DEFAULT_PATTERN
    remove_empty: bool = True
    include_untagged: bool = False
    move_sidecars: bool = True  # album extras (.cue, .log, booklet, Artwork/) follow a whole album
    dupes_action: str = "move"  # "move" to the dupes folder, or "trash"
    dupes_folder: str = ""  # "" = the language file's name
    dupes_folder_history: list = dataclasses.field(default_factory=list)  # earlier names: still skipped by scans
    fallbacks: dict = dataclasses.field(default_factory=dict)  # {"artist": "...", ...}
    artists_path: str = ""  # "" = artists.json next to the exe
    artist_name_preference: str = "original"  # or "latin"
    artist_no_merge: list = dataclasses.field(default_factory=list)
    artist_rejected: list = dataclasses.field(default_factory=list)  # [[name, name, ...], ...]
    fpcalc_path: str = ""
    tag_filler_path: str = ""  # music-tag-filler.exe, asked for once
    last_log: str = ""

    def fallback(self, key: str) -> str:
        value = self.fallbacks.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        return i18n.t(f"fallback_{key}")

    def dupes_name(self) -> str:
        return self.dupes_folder.strip() or i18n.t("dupes_folder")

    def artists_file(self) -> str:
        return self.artists_path or os.path.join(i18n.app_dir(), "artists.json")

    def set_dupes_folder(self, name: str) -> None:
        """A renamed duplicates folder keeps the old name on the skip list, or the next
        scan would treat the old folder's files as music to organize."""
        name = name.strip()
        old = self.dupes_folder.strip()
        if old and old != name and old not in self.dupes_folder_history:
            self.dupes_folder_history.append(old)
        self.dupes_folder = name


_BAD_NAME = set('<>:"/\\|?*')


def check(values: dict) -> list[tuple[str, str]]:
    """Problems in settings typed by hand, as (field, lang key). Empty text means
    "use the default" and is always fine."""
    out = []
    name = values.get("dupes_folder", "").strip()
    if name and (set(name) & _BAD_NAME or name.strip(". ") != name or name in (".", "..")):
        out.append(("dupes_folder", "err_bad_folder_name"))
    for key in FALLBACK_KEYS:
        text = values.get("fallbacks", {}).get(key, "").strip()
        if text and set(text) & _BAD_NAME:
            out.append((f"fallback_{key}", "err_bad_folder_name"))
    for key in ("fpcalc_path", "tag_filler_path"):
        path = values.get(key, "").strip()
        if path and not os.path.isfile(path):
            out.append((key, "err_file_not_found"))
    artists = values.get("artists_path", "").strip()
    if artists and not os.path.isdir(os.path.dirname(os.path.abspath(artists))):
        out.append(("artists_path", "err_folder_not_found"))
    return out


def load() -> Prefs:
    raw = i18n.load_settings()
    default = Prefs()

    def typed(key: str, kind: type):
        value = raw.get(key)
        ok = isinstance(value, kind) and (kind is bool or not isinstance(value, bool))
        return value if ok else getattr(default, key)

    p = Prefs(
        pattern=typed("pattern", str).strip() or DEFAULT_PATTERN,
        remove_empty=typed("remove_empty", bool),
        include_untagged=typed("include_untagged", bool),
        move_sidecars=typed("move_sidecars", bool),
        dupes_action=typed("dupes_action", str),
        dupes_folder=typed("dupes_folder", str),
        dupes_folder_history=[s for s in typed("dupes_folder_history", list) if isinstance(s, str) and s.strip()],
        fallbacks={k: v for k, v in typed("fallbacks", dict).items() if k in FALLBACK_KEYS and isinstance(v, str)},
        artists_path=typed("artists_path", str),
        artist_name_preference=typed("artist_name_preference", str),
        artist_no_merge=[s for s in typed("artist_no_merge", list) if isinstance(s, str)],
        artist_rejected=[[s for s in g if isinstance(s, str)] for g in typed("artist_rejected", list) if isinstance(g, list)],
        fpcalc_path=typed("fpcalc_path", str),
        tag_filler_path=typed("tag_filler_path", str),
        last_log=typed("last_log", str),
    )
    if p.dupes_action not in ("move", "trash"):
        p.dupes_action = default.dupes_action
    if p.artist_name_preference not in ("original", "latin"):
        p.artist_name_preference = default.artist_name_preference
    return p


def save(p: Prefs) -> None:
    settings = i18n.load_settings()
    settings.update(dataclasses.asdict(p))
    i18n.save_settings(settings)
