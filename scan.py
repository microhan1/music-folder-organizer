"""Walk a folder and read the tags of every music file in it. Read only.

The tag readers follow music-tag-filler's tags.py (same mutagen calls, same
field names) but read a few more fields and two more formats, and never write.
"""
from __future__ import annotations

import dataclasses
import os
import re
import threading
from typing import Callable

import mutagen

AUDIO_EXTS = (".mp3", ".flac", ".m4a", ".ogg", ".wav", ".wma")
LOSSLESS_FORMATS = {"FLAC", "WAV"}
LRC_EXT = ".lrc"
TAGBAK_SUFFIX = ".tagbak.json"  # music-tag-filler's per-file backup
COVER_NAMES = {f"{stem}{ext}" for stem in ("cover", "folder", "front") for ext in (".jpg", ".jpeg", ".png")}
JUNK_NAMES = {"thumbs.db", ".ds_store", "desktop.ini", "ehthumbs.db"}

LOG_NAME = "organize_log.json"
# album extras that may follow a whole album to its new folder (anything else stays put)
SIDECAR_EXTS = {".cue", ".log", ".txt", ".nfo", ".m3u", ".m3u8", ".pdf", ".accurip", ".sfv", ".md5", ".ffp",
                ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tif", ".tiff"}
CUE_MAX_BYTES = 1024 * 1024
_CUE_FILE = re.compile(r'^\s*FILE\s+(?:"([^"]+)"|(\S+))', re.IGNORECASE | re.MULTILINE)


@dataclasses.dataclass
class TagSet:
    title: str = ""
    artist: str = ""
    album: str = ""
    album_artist: str = ""
    year: str = ""
    track: str = ""
    disc: str = ""
    genre: str = ""
    artist_sort: str = ""
    mb_artist_id: str = ""
    itunes_artist_id: str = ""  # Apple's artist id: music-tag-filler's "iTunes Artist Id" or m4a atID
    compilation: bool = False


@dataclasses.dataclass
class Track:
    path: str
    fmt: str
    size: int
    length: float = 0.0  # seconds
    bitrate: int = 0  # kbps
    sample_rate: int = 0
    lossless: bool = False
    tags: TagSet = dataclasses.field(default_factory=TagSet)
    error: str = ""  # unreadable file: tags stay empty
    # filled in by dedupe when first needed; TagCache carries them on while the file is unchanged
    sha1: str = dataclasses.field(default="", compare=False, repr=False)
    fingerprint: bytes | None = dataclasses.field(default=None, compare=False, repr=False)  # b"": too short to use

    @property
    def folder(self) -> str:
        return os.path.dirname(self.path)

    @property
    def name(self) -> str:
        return os.path.basename(self.path)

    def is_untagged(self) -> bool:
        return not self.tags.title or not (self.tags.artist or self.tags.album_artist)


@dataclasses.dataclass
class DirInfo:
    path: str
    files: list[str]  # names
    subdirs: list[str]  # names
    cue_refs: set[str] = dataclasses.field(default_factory=set)  # lower-case file names the folder's .cue sheets name


@dataclasses.dataclass
class ScanResult:
    root: str
    tracks: list[Track]
    dirs: dict[str, DirInfo]  # key: key_of(path)
    cancelled: bool = False


def key_of(path: str) -> str:
    """Identity of an existing path: case-insensitive like Windows. NTFS keeps NFC
    and NFD spellings apart, so two such files stay two keys."""
    return os.path.normcase(os.path.abspath(path))


def target_key(path: str) -> str:
    """Key for new names: NFC too, so the tool never creates two names that only
    differ in Unicode normalization (they look identical in Explorer)."""
    import unicodedata

    return os.path.normcase(unicodedata.normalize("NFC", os.path.abspath(path)))


def format_of(path: str) -> str | None:
    ext = os.path.splitext(path)[1].lower()
    return {".mp3": "MP3", ".flac": "FLAC", ".m4a": "M4A", ".ogg": "OGG", ".wav": "WAV", ".wma": "WMA"}.get(ext)


# ------------------------------------------------------------------ walking
ProgressFn = Callable[[int, int], None]


class TagCache:
    """Tracks read by an earlier scan, so a rescan (after a run, an undo, Music Tag
    Filler) opens only files that changed.

    A file is known by its identity on the disk — volume and file number, which a
    move within a drive keeps — and must still have the same size and modified
    time; then its tags are taken as read before, under its new path. Where the
    drive gives no file number (FAT32, some network drives) the path is the key.
    Files that could not be read are never kept: they may be readable next time.
    Memory only: nothing outlives the program."""

    def __init__(self) -> None:
        self._entries: dict[tuple, tuple[int, int, Track]] = {}
        self.hits = 0  # files the last scan did not have to open

    @staticmethod
    def key(path: str, st: os.stat_result) -> tuple:
        return ("id", st.st_dev, st.st_ino) if st.st_ino else ("path", key_of(path))

    def get(self, path: str, st: os.stat_result) -> Track | None:
        entry = self._entries.get(self.key(path, st))
        if entry is None or entry[0] != st.st_size or entry[1] != st.st_mtime_ns:
            return None
        return dataclasses.replace(entry[2], path=os.path.abspath(path))

    def add(self, entries: dict[tuple, tuple[int, int, Track]], hits: int) -> None:
        """Keeps what the scan just saw. Files that left the scan stay known: duplicates
        sent to the excluded dupes folder come back on undo. An old entry is harmless;
        it is used only while size and modified time still match."""
        self._entries.update(entries)
        self.hits = hits


def scan(root: str, exclude: list[str] | None = None, progress: ProgressFn | None = None,
         cancel: threading.Event | None = None, cache: TagCache | None = None) -> ScanResult:
    """Every music file under root (sorted), with tags. ``exclude`` lists folders
    whose subtree is skipped (the dupes folder, a destination inside root).
    With ``cache``, unchanged files are not opened again (see TagCache)."""
    root = os.path.abspath(root)
    skip = {key_of(p) for p in (exclude or [])}
    dirs: dict[str, DirInfo] = {}
    audio: list[str] = []
    for here, subdirs, files in os.walk(root):
        if cancel is not None and cancel.is_set():
            return ScanResult(root, [], dirs, cancelled=True)
        subdirs[:] = sorted(d for d in subdirs if key_of(os.path.join(here, d)) not in skip)
        files.sort()
        info = DirInfo(here, list(files), list(subdirs))
        for f in files:
            if f.lower().endswith(".cue"):
                info.cue_refs |= cue_refs(os.path.join(here, f))
        dirs[key_of(here)] = info
        audio.extend(os.path.join(here, f) for f in files if format_of(f))
    tracks: list[Track] = []
    total = len(audio)
    seen: dict[tuple, tuple[int, int, Track]] = {}
    hits = 0
    for i, path in enumerate(audio):
        if cancel is not None and cancel.is_set():
            return ScanResult(root, tracks, dirs, cancelled=True)  # the cache stays as it was
        try:
            st = os.stat(path)
        except OSError:
            st = None
        track = cache.get(path, st) if (cache is not None and st is not None) else None
        if track is None:
            track = read_track(path, st)
        else:
            hits += 1
        if cache is not None and st is not None and not track.error:
            seen[cache.key(path, st)] = (st.st_size, st.st_mtime_ns, track)
        tracks.append(track)
        if progress is not None and (i % 25 == 0 or i + 1 == total):
            progress(i + 1, total)
    if cache is not None:
        cache.add(seen, hits)
    return ScanResult(root, tracks, dirs)


def cue_refs(path: str) -> set[str]:
    """Lower-case base names of the files a cue sheet points at (its FILE lines).
    Read only; cue sheets come in UTF-8, Shift-JIS, CP949 and Latin-1."""
    try:
        if os.path.getsize(path) > CUE_MAX_BYTES:
            return set()
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return set()
    for enc in ("utf-8-sig", "cp932", "cp949", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    out = set()
    for m in _CUE_FILE.finditer(text):
        name = (m.group(1) or m.group(2) or "").strip()
        if name:
            out.add(name.replace("\\", "/").rsplit("/", 1)[-1].lower())
    return out


# ------------------------------------------------------------------ reading
def read_track(path: str, st: os.stat_result | None = None) -> Track:
    fmt = format_of(path) or ""
    try:
        size = (st or os.stat(path)).st_size
    except OSError:
        size = 0
    track = Track(os.path.abspath(path), fmt, size)
    try:
        audio = _open(path, fmt)
    except Exception as exc:  # unreadable or locked: the file can still be listed
        track.error = str(exc) or exc.__class__.__name__
        return track
    if audio is None:
        track.error = "unsupported"
        return track
    info = audio.info
    track.length = float(getattr(info, "length", 0.0) or 0.0)
    track.bitrate = int(round((getattr(info, "bitrate", 0) or 0) / 1000))
    track.sample_rate = int(getattr(info, "sample_rate", 0) or 0)
    track.lossless = fmt in LOSSLESS_FORMATS or str(getattr(info, "codec", "")).lower() == "alac"
    if track.bitrate == 0 and track.length > 0 and size:
        track.bitrate = int(round(size * 8 / track.length / 1000))
    try:
        track.tags = _read_tags(fmt, audio)
    except Exception as exc:
        track.error = str(exc) or exc.__class__.__name__
    return track


def _open(path: str, fmt: str):
    if fmt == "MP3":
        from mutagen.mp3 import MP3
        return MP3(path)
    if fmt == "FLAC":
        from mutagen.flac import FLAC
        return FLAC(path)
    if fmt == "M4A":
        from mutagen.mp4 import MP4
        return MP4(path)
    if fmt == "WAV":
        from mutagen.wave import WAVE
        return WAVE(path)
    if fmt == "WMA":
        from mutagen.asf import ASF
        return ASF(path)
    return mutagen.File(path)  # .ogg may be Vorbis, Opus or FLAC-in-Ogg


def _read_tags(fmt: str, audio) -> TagSet:
    tags = audio.tags
    if tags is None:
        return TagSet()
    if fmt in ("MP3", "WAV"):
        return _read_id3(tags)
    if fmt == "M4A":
        return _read_mp4(tags)
    if fmt == "WMA":
        return _read_asf(tags)
    return _read_vorbis(tags)


def _text(frame) -> str:
    try:
        return str(frame.text[0]).strip() if frame is not None and frame.text else ""
    except (AttributeError, IndexError):
        return ""


def _read_id3(id3) -> TagSet:
    if not hasattr(id3, "getall"):
        return TagSet()
    mbid = itunes = ""
    for frame in id3.getall("TXXX"):
        desc = str(getattr(frame, "desc", "")).lower()
        if desc == "musicbrainz artist id" and frame.text:
            mbid = str(frame.text[0])
        elif desc == "itunes artist id" and frame.text:
            itunes = str(frame.text[0])
    genre = ""
    tcon = id3.get("TCON")
    if tcon is not None:
        try:
            genre = str(tcon.genres[0]) if tcon.genres else ""
        except Exception:
            genre = _text(tcon)
    return TagSet(
        title=_text(id3.get("TIT2")),
        artist=_text(id3.get("TPE1")),
        album=_text(id3.get("TALB")),
        album_artist=_text(id3.get("TPE2")),
        year=_text(id3.get("TDRC")) or _text(id3.get("TYER")),
        track=_text(id3.get("TRCK")),
        disc=_text(id3.get("TPOS")),
        genre=genre.strip(),
        artist_sort=_text(id3.get("TSOP")),
        mb_artist_id=_first_id(mbid),
        itunes_artist_id=_first_id(itunes),
        compilation=_text(id3.get("TCMP")) in ("1", "true"),
    )


def _first(vc, *keys: str) -> str:
    for key in keys:
        try:
            values = vc.get(key)
        except (KeyError, TypeError, ValueError):
            values = None
        if values:
            return str(values[0]).strip()
    return ""


def _read_vorbis(vc) -> TagSet:
    return TagSet(
        title=_first(vc, "title"),
        artist=_first(vc, "artist"),
        album=_first(vc, "album"),
        album_artist=_first(vc, "albumartist", "album artist"),
        year=_first(vc, "date", "year"),
        track=_first(vc, "tracknumber"),
        disc=_first(vc, "discnumber"),
        genre=_first(vc, "genre"),
        artist_sort=_first(vc, "artistsort"),
        mb_artist_id=_first_id(_first(vc, "musicbrainz_artistid")),
        itunes_artist_id=_first_id(_first(vc, "itunes_artistid")),
        compilation=_first(vc, "compilation") in ("1", "true"),
    )


def _read_mp4(mp4) -> TagSet:
    def s(key: str) -> str:
        v = mp4.get(key)
        if not v:
            return ""
        item = v[0]
        if isinstance(item, (bytes, bytearray)):
            return bytes(item).decode("utf-8", "replace").strip()
        return str(item).strip()

    def pair(key: str) -> str:
        v = mp4.get(key)
        if v and isinstance(v[0], (tuple, list)) and v[0]:
            n, total = (list(v[0]) + [0, 0])[:2]
            return f"{n}/{total}" if total else str(n) if n else ""
        return ""

    mbid = s("----:com.apple.iTunes:MusicBrainz Artist Id")
    itunes = s("----:com.apple.iTunes:iTunes Artist Id")  # how music-tag-filler writes it
    if not itunes and mp4.get("atID"):  # the iTunes Store's own atom
        itunes = str(mp4["atID"][0])
    return TagSet(
        title=s("\xa9nam"), artist=s("\xa9ART"), album=s("\xa9alb"), album_artist=s("aART"),
        year=s("\xa9day"), track=pair("trkn"), disc=pair("disk"), genre=s("\xa9gen"),
        artist_sort=s("soar"), mb_artist_id=_first_id(mbid), itunes_artist_id=_first_id(itunes),
        compilation=bool(mp4.get("cpil") and mp4["cpil"]),
    )


def _read_asf(asf) -> TagSet:
    def s(key: str) -> str:
        v = asf.get(key)
        return str(v[0]).strip() if v else ""

    return TagSet(
        title=s("Title"), artist=s("Author"), album=s("WM/AlbumTitle"), album_artist=s("WM/AlbumArtist"),
        year=s("WM/Year"), track=s("WM/TrackNumber"), disc=s("WM/PartOfSet"), genre=s("WM/Genre"),
        artist_sort=s("WM/ArtistSortOrder"), mb_artist_id=_first_id(s("MusicBrainz/Artist Id")),
        compilation=s("WM/IsCompilation").lower() in ("1", "true"),
    )


def _first_id(text: str) -> str:
    """Multi-artist credits store several ids; the first one names the main artist."""
    return re.split(r"[;/,\s]+", text.strip())[0].lower() if text and text.strip() else ""


def parse_number(text: str) -> int:
    m = re.match(r"\s*(\d+)", text or "")
    return int(m.group(1)) if m else 0


def parse_year(text: str) -> str:
    m = re.search(r"(\d{4})", text or "")
    return m.group(1) if m else ""
