"""Randomised end-to-end scenarios (same idea as photo-organizer's tests/test_scenarios.py): a messy music
folder, a random combination of options, then the properties that must hold for ANY input.

  P1  nothing is lost: every file's bytes are still somewhere afterwards (duplicates moved, not deleted)
  P2  the plan's targets are unique, inside the destination, short enough
  P3  after a run, planning again finds nothing to do (idempotent)              [destination: same / above]
  P4  switching the language after a run still finds nothing to do              [same / above]
  P5  undo gives back exactly the folder we started with
  P6  files that were never touched keep their modified time

MUSIC_FUZZ_SEEDS=200 MUSIC_FUZZ_START=0 python -m pytest tests/test_scenarios.py   runs more seeds (default 10).
A failing seed is in the test id: rerun it with -k "seed17"."""
from __future__ import annotations

import hashlib
import os
import random
import shutil
import stat
import zlib

import mutagen
import pytest
from conftest import SAMPLES

import dedupe
import i18n
import mover
import plan as plan_mod
import prefs as prefs_mod
import scan
import session
import undo

SEEDS = int(os.environ.get("MUSIC_FUZZ_SEEDS", "10"))
START = int(os.environ.get("MUSIC_FUZZ_START", "0"))
SAMPLE_FILES = ["song-128.mp3", "song-320.mp3", "song.flac", "tone.ogg", "other.mp3", "silent.m4a"]
ARTISTS = ["Artist A", "artist a", "ARTIST A ", "岡田 有希子", "岡田有希子", "Minako Yoshida (吉田美奈子)", "吉田美奈子",
           "Various Artists", "A/B", "AC:DC", "", "x" * 60, "The Band", "Band, The", "아이유", "IU", "Con", "Trail."]
ALBUMS = ["Album One", "Album One (Deluxe)", "Best of A", "", "CD1/2", "What?", "NUL", "ünï 앨범", "album one", "Live '99",
          "y" * 70, "Unknown Album", "[Single]"]
TITLES = ["Song", "Another Song", "Track", "노래", "歌", "Sing: A (Remix)", "What?", "", "Intro", "Song (2)", "t" * 80]
FOLDERS = ["a", "B", "new folder", "dump", "음악", "CD1", "Disc 2", "_Duplicates", "_중복", "Unknown Artist", "알 수 없는 가수",
           "with.dot", "2020", "x y z"]
NAME_STYLES = ["{n:02d} {t}", "track{n:02d}", "{a} - {t}", "{t}", "{n}.{t}", "{n:02d}. {t} ", "01 - {t}", "{t} copy"]
PATTERNS = [prefs_mod.PRESETS[0], prefs_mod.PRESETS[1], "{artist}/{title}", "{year}/{artist} - {title}",
            "{genre}/{album}/{title}", "{artist}/{album}/{disc}-{track:02} {title}", "{album_artist|artist}/{title}",
            "{artist_sort}/{album}/{track:02} {title}"]
FALLBACKS = [{}, {}, {"artist": "Nobody", "album": "Nothing"}, {"artist": "", "year": "????"}]


def scenario_rng(seed: int, where: str) -> random.Random:
    """Same seed, same folder, every run (str hash() is salted per process: use a fixed checksum)."""
    return random.Random(seed * 7919 + zlib.crc32(where.encode()) % 1000)


class Gen:
    def __init__(self, root: str, rnd: random.Random) -> None:
        self.root, self.rnd, self.n = root, rnd, 0
        self.used: set[str] = set()
        self.made: list[str] = []
        self.songs: list[str] = []  # files that can be copied as exact duplicates

    def folder(self) -> str:
        parts = [self.rnd.choice(FOLDERS) for _ in range(self.rnd.choice([0, 0, 1, 1, 2, 3]))]
        return os.path.join(self.root, *parts)

    def claim(self, path: str) -> bool:
        # relative length: the sandbox path itself must not change what gets generated (reproducibility)
        key = os.path.normcase(path).casefold()
        if key in self.used or len(os.path.relpath(path, self.root)) > 150:
            return False
        self.used.add(key)
        return True

    def tag(self, path: str, **tags) -> None:
        try:
            audio = mutagen.File(path, easy=True)
            if audio is None:
                return
            if audio.tags is None:
                audio.add_tags()
            for k, v in tags.items():
                if v != "":
                    audio[k] = v
            audio.save()
        except Exception:  # a format whose tags we cannot write (wav): it stays untagged
            pass

    def song(self, folder: str, name_style: str, artist: str, album: str, title: str, number: int, disc: str,
             year: str, sample: str | None = None, tagged: bool = True) -> str | None:
        sample = sample or self.rnd.choice(SAMPLE_FILES)
        ext = os.path.splitext(sample)[1]
        name = name_style.format(n=number, t=title or "x", a=artist or "x")
        name = "".join("_" if ch in '<>:"/\\|?*' or ord(ch) < 32 else ch for ch in name)  # file names, not tags
        path = os.path.join(folder, name + ext)
        if not self.claim(path):
            return None
        os.makedirs(folder, exist_ok=True)
        shutil.copyfile(os.path.join(SAMPLES, sample), path)
        if tagged:
            self.tag(path, artist=artist, album=album, title=title, tracknumber=str(number) if number else "",
                     discnumber=disc, date=year, albumartist=self.rnd.choice(["", "", artist]),
                     genre=self.rnd.choice(["", "Pop", "J-Pop"]))
        self.made.append(path)
        self.songs.append(path)
        return path

    def album(self) -> None:
        rnd = self.rnd
        artist, album, year = rnd.choice(ARTISTS), rnd.choice(ALBUMS), rnd.choice(["2020", "2020-05-01", "", "abcd", "1999"])
        folder = self.folder()
        style = rnd.choice(NAME_STYLES)
        discs = rnd.choice([["1"], ["1"], ["1", "2"], [""], ["1/2", "2/2"]])
        files = []
        for disc in discs:
            for number in range(1, rnd.randint(1, 4) + 1):
                title = rnd.choice(TITLES)
                p = self.song(folder if len(discs) == 1 else os.path.join(folder, f"CD{disc[:1] or 0}"), style, artist,
                              album, title, number, disc, year)
                if p:
                    files.append(p)
        if not files:
            return
        if rnd.random() < 0.25:  # a cue sheet that names the files: only their folder may change
            cue = os.path.join(folder, "album.cue")
            if self.claim(cue):
                with open(cue, "w", encoding="utf-8") as f:
                    f.write("\n".join(f'FILE "{os.path.basename(p)}" WAVE' for p in files if os.path.dirname(p) == folder))
                self.made.append(cue)
        for extra in rnd.sample(["cover.jpg", "folder.jpg", "album.log", "info.txt", "Artwork/front.jpg"], rnd.randint(0, 3)):
            p = os.path.join(folder, *extra.split("/"))
            if self.claim(p):
                os.makedirs(os.path.dirname(p), exist_ok=True)
                open(p, "wb").write(b"extra" + extra.encode())
                self.made.append(p)
        for p in files:
            stem = os.path.splitext(p)[0]
            for suffix, body in ((".lrc", b"[00:01.00]la"), (".mp3.tagbak.json", b"{}"), (".flac.tagbak.json", b"{}")):
                if rnd.random() < 0.15 and self.claim(stem + suffix):
                    open(stem + suffix, "wb").write(body)
                    self.made.append(stem + suffix)

    def one(self) -> None:
        rnd, r = self.rnd, self.rnd.random()
        self.n += 1
        if r < 0.45:
            self.album()
        elif r < 0.55:  # a loose untagged file
            self.song(self.folder(), rnd.choice(NAME_STYLES), "", "", "", rnd.randint(0, 9), "", "", tagged=False)
        elif r < 0.62:  # tags with characters Windows does not allow, and empty fields
            self.song(self.folder(), "{n:02d} {t}", rnd.choice(["A/B", "AC:DC", 'Q"uote', "star*", "<lt>", "pipe|", ""]),
                      rnd.choice(["Al?bum", "", "a\\b"]), rnd.choice(["T\tab", "line\nbreak", "Song", ""]), rnd.randint(1, 3), "", "")
        elif r < 0.75 and self.songs:  # exact duplicate of an earlier song, or a re-encode of the same song
            src = rnd.choice(self.songs)
            ext = os.path.splitext(src)[1]
            p = os.path.join(self.folder(), f"copy {self.n}{ext}")
            if self.claim(p):
                os.makedirs(os.path.dirname(p), exist_ok=True)
                shutil.copy2(src, p)
                self.made.append(p)
                self.songs.append(p)
        elif r < 0.82 and self.songs:  # the same song as another file but another bitrate / format (the "same song" stage)
            src = rnd.choice(self.songs)
            try:
                a = mutagen.File(src, easy=True)
                artist, title = a.get("artist", [""])[0], a.get("title", [""])[0]
            except Exception:
                return
            if artist and title:
                self.song(self.folder(), "{t}", artist, rnd.choice(ALBUMS), title, 1, "", "",
                          sample=rnd.choice(["song-128.mp3", "song-320.mp3", "song.flac"]))
        elif r < 0.88:  # junk and mac resource forks
            folder = self.folder()
            os.makedirs(folder, exist_ok=True)
            for name in rnd.choice([["Thumbs.db"], ["desktop.ini"], [".DS_Store"], ["._01 Song.mp3"], ["notes.txt"]]):
                p = os.path.join(folder, name)
                if self.claim(p):
                    open(p, "wb").write(b"x" * rnd.randint(1, 20))
                    self.made.append(p)
        elif r < 0.94:  # already organised the way the default pattern would put it
            artist = rnd.choice(["Artist A", "The Band"])
            album = rnd.choice(["Album One", "Live '99"])
            self.song(os.path.join(self.root, artist, album), "{n:02d} - {t}", artist, album, rnd.choice(["Song", "Intro"]),
                      rnd.randint(1, 3), "", "2020", tagged=True)
        else:  # the same file name under several albums, different songs
            title = rnd.choice(["Song", "Intro"])
            for _ in range(rnd.randint(2, 3)):
                self.song(self.folder(), "01 {t}", rnd.choice(ARTISTS[:6]), rnd.choice(ALBUMS[:5]), title, 1, "", "2020")


def make_folder(root: str, rnd: random.Random) -> Gen:
    g = Gen(root, rnd)
    os.makedirs(root, exist_ok=True)
    for _ in range(rnd.randint(6, 30)):
        g.one()
    for p in g.made:
        if rnd.random() < 0.08 and os.path.exists(p):
            os.chmod(p, stat.S_IREAD)
        if rnd.random() < 0.3 and os.path.exists(p):
            t = 1500000000 + rnd.randint(0, 10 ** 8)
            os.utime(p, (t, t))
    return g


def options(rnd: random.Random) -> dict:
    return dict(pattern=rnd.choice(PATTERNS), remove_empty=rnd.random() < 0.8, include_untagged=rnd.random() < 0.4,
                move_sidecars=rnd.random() < 0.8, dupes_action=rnd.choice(["move", "move", "trash"]),
                fallbacks=dict(rnd.choice(FALLBACKS)), artist_name_preference=rnd.choice(["original", "latin"]))


def pipeline(root: str, dest: str, p: prefs_mod.Prefs, dedupe_kind: str):
    """scan -> artist index -> duplicate groups -> plan, the way the CLI does it (main.run_cli)."""
    res = scan.scan(root, session.excludes(root, dest, p))
    index, _ = session.make_index(res.tracks, p, session.existing_folders(res, dest))
    opts = session.options(p, root, dest, "move")
    groups = []
    if dedupe_kind:
        groups = dedupe.find(res.tracks, index.rep, preferred=session.preferred_paths(res, opts, index),
                             across_albums=dedupe_kind == "across")
    return res, plan_mod.build(res, opts, index, groups)


def hashes(root: str) -> list[str]:
    out = []
    for here, _ds, fs_ in os.walk(root):
        for f in fs_:
            if f.startswith("organize_log.json") or scan.is_junk(f):
                continue
            with open(os.path.join(here, f), "rb") as fh:
                out.append(hashlib.sha1(fh.read()).hexdigest())
    return sorted(out)


def file_map(root: str) -> dict:
    out = {}
    for here, _ds, fs_ in os.walk(root):
        for f in fs_:
            if f.startswith("organize_log.json") or scan.is_junk(f):
                continue
            p = os.path.join(here, f)
            with open(p, "rb") as fh:
                out[os.path.relpath(p, root)] = hashlib.sha1(fh.read()).hexdigest()
    return out


def writable_again(root: str) -> None:
    for here, _ds, fs_ in os.walk(root):
        for f in fs_:
            try:
                os.chmod(os.path.join(here, f), stat.S_IWRITE | stat.S_IREAD)
            except OSError:
                pass


def idle(the_plan: plan_mod.Plan) -> list:
    """What a plan would still do: checked items that move, companions that move, folders to remove."""
    acts = [(i.track.name, i.status, os.path.relpath(i.dst, the_plan.options.dest) if i.dst else "(trash)")
            for i in the_plan.items if i.moves and i.checked]
    acts += [(os.path.basename(c.src), c.kind, os.path.relpath(c.dst, the_plan.options.dest))
             for c in the_plan.companions if scan.key_of(c.src) != scan.key_of(c.dst)]
    acts += [("rmdir", d) for d in the_plan.empty_dirs]
    return acts


@pytest.fixture
def fake_trash(monkeypatch):
    gone = []
    monkeypatch.setattr(mover, "trash", lambda p: (gone.append(open(p, "rb").read()), mover.remove_file(p))[1])
    return gone


@pytest.mark.parametrize("seed", range(START, START + SEEDS), ids=lambda s: f"seed{s}")
@pytest.mark.parametrize("where", ["same", "above", "inside", "other"])
def test_random_music_folder_survives_run_rerun_and_undo(tmp_path, fake_trash, seed, where):
    rnd = scenario_rng(seed, where)
    top = tmp_path / "top"
    root = top / "in1"
    gen = make_folder(str(root), rnd)
    opts = options(rnd)
    dedupe_kind = rnd.choice(["", "same", "same", "across"])
    langs = list(i18n.LANGS)
    lang, other_lang = rnd.sample(langs, 2)
    i18n.set_lang(lang, persist=False)
    dest = {"same": str(root), "above": str(top), "inside": str(root / "sorted"), "other": str(tmp_path / "out")}[where]
    p = prefs_mod.Prefs(artists_path=str(tmp_path / "no-artists.json"), **opts)
    tag = f"seed={seed} where={where} lang={lang}->{other_lang} dedupe={dedupe_kind!r} opts={opts} files={len(gen.made)}"
    before = file_map(str(tmp_path))
    before_hashes = hashes(str(tmp_path))
    mtimes = {m: os.stat(m).st_mtime for m in gen.made if os.path.exists(m)}

    res, the_plan = pipeline(str(root), dest, p, dedupe_kind)
    # P2: targets unique, inside the destination, short
    seen: dict[str, str] = {}
    targets = [(i.dst, i.src) for i in the_plan.items if i.dst and i.moves] + [(c.dst, c.src) for c in the_plan.companions]
    for d, src in targets:
        k = scan.target_key(d)
        assert k not in seen or seen[k] == src, f"{tag}: two files aim at {d}: {seen.get(k)} and {src}"
        seen[k] = src
        assert len(d) < 260, f"{tag}: path too long ({len(d)})"
        assert scan.key_of(d).startswith(scan.key_of(dest).rstrip(os.sep) + os.sep), f"{tag}: {d} outside {dest}"

    out = mover.execute(the_plan)
    writable_again(str(tmp_path))
    assert not out.failed, f"{tag}: failed {out.failed}"

    # P1: nothing lost
    after = hashes(str(tmp_path))
    trashed = sorted(hashlib.sha1(b).hexdigest() for b in fake_trash)
    missing = list(before_hashes)
    for h in after + trashed:
        if h in missing:
            missing.remove(h)
    assert missing == [], f"{tag}: {len(missing)} files' bytes vanished"

    # P3 / P4: quiet when run again, also after the language was switched
    if where in ("same", "above"):
        _, again = pipeline(str(root), dest, prefs_mod.Prefs(artists_path=str(tmp_path / "no-artists.json"), **opts),
                            dedupe_kind)
        acts = idle(again)
        assert not acts, f"{tag}: second run still wants to act: {acts[:4]}"
        i18n.set_lang(other_lang, persist=False)
        _, switched = pipeline(str(root), dest, prefs_mod.Prefs(artists_path=str(tmp_path / "no-artists.json"), **opts),
                               dedupe_kind)
        acts = idle(switched)
        assert not acts, f"{tag}: after switching to {other_lang} the run wants to act: {acts[:4]}"
        i18n.set_lang(lang, persist=False)

    # P5: undo gives back the exact starting folder
    log = undo.find_log(dest) or undo.find_log(str(root))
    if out.log_path and log:
        u = undo.undo(log)
        writable_again(str(tmp_path))
        assert not u.log_error and not u.log_unsaved, tag
        assert not u.skipped, f"{tag}: undo skipped {u.skipped[:3]}"
        assert len(u.trashed) == len(fake_trash), tag
    back = file_map(str(tmp_path))
    if not fake_trash:
        assert back == before, f"{tag}: undo differs: {set(back) ^ set(before)}"
        for m, t in mtimes.items():
            if os.path.exists(m):
                assert abs(os.stat(m).st_mtime - t) < 2, f"{tag}: mtime changed for {m}"
    else:
        trashed_set = {hashlib.sha1(b).hexdigest() for b in fake_trash}
        assert {k for k in before if k not in back} <= {k for k, v in before.items() if v in trashed_set}, tag
        assert all(before[k] == v for k, v in back.items() if k in before), tag
