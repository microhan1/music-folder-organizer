"""Folders deeper than 260 characters and macOS "._" files (ported from photo-organizer, LESSONS A2/D)."""
import builtins
import errno
import os

import pytest

import mover
import undo
from conftest import build, put
from longpath import fs, plain
from scan import is_junk

PREFIX = "\\\\?\\"


@pytest.fixture
def no_long_paths(monkeypatch):
    """Act like a PC with Windows long paths off (this one has them on, so a missing fs() would
    go unnoticed): a plain path of 260+ characters fails."""
    def too_long(p) -> bool:
        if not isinstance(p, (str, os.PathLike)):
            return False
        s = os.fspath(p)
        return isinstance(s, str) and not s.startswith(PREFIX) and len(os.path.abspath(s)) >= 260

    def guard(fn, both=False):
        def inner(*args, **kwargs):
            for p in args[: 2 if both else 1]:
                if too_long(p):
                    raise FileNotFoundError(errno.ENOENT, "long paths are off", os.fspath(p))
            return fn(*args, **kwargs)
        return inner

    monkeypatch.setattr(builtins, "open", guard(builtins.open))
    for name in ("scandir", "stat", "lstat", "remove", "mkdir", "listdir", "chmod", "utime", "rmdir"):
        monkeypatch.setattr(os, name, guard(getattr(os, name)))
    for name in ("rename", "replace"):
        monkeypatch.setattr(os, name, guard(getattr(os, name), both=True))
    for name in ("isdir", "isfile", "exists", "lexists", "getsize"):
        monkeypatch.setattr(os.path, name, guard(getattr(os.path, name)))


def put_long(folder, sample_name: str, name: str, **tags) -> None:
    """conftest.put for a folder deeper than 260 characters: the fixture itself must use fs()."""
    import shutil

    import mutagen
    from conftest import sample

    dst = fs(os.path.join(folder, name))
    shutil.copyfile(fs(sample(sample_name)), dst)
    audio = mutagen.File(dst, easy=True)
    if audio.tags is None:
        audio.add_tags()
    for k, v in tags.items():
        audio[k] = v
    audio.save()


def test_fs_and_plain_round_trip():
    short = "C:\\music\\a.mp3"
    assert fs(short) == short
    long = "C:\\" + "\\".join(["x" * 40] * 8) + "\\a.mp3"
    assert fs(long).startswith(PREFIX) and plain(fs(long)) == long
    unc = "\\\\nas\\" + "\\".join(["x" * 40] * 8)
    assert plain(fs(unc)) == unc


def test_mac_resource_fork_files_are_junk_not_music(tmp_path):
    assert is_junk("._01 Song.mp3") and is_junk("Thumbs.db") and not is_junk("01 Song.mp3")
    folder = tmp_path / "in1"
    put(folder, "song-128.mp3", "x/01 Song.mp3", artist="A", album="B", title="Song", tracknumber="1")
    (folder / "x" / "._01 Song.mp3").write_bytes(b"\x00\x05\x16\x07")  # AppleDouble header, not audio
    res, p = build(folder)
    assert [t.name for t in res.tracks] == ["01 Song.mp3"]  # the "._" file is not a track
    assert not p.untagged()
    assert not mover.execute(p).failed
    assert not os.path.exists(folder / "x")  # only the "._" file was left in it: the folder goes too
    assert not any(n.startswith("._") for _, _, fs_ in os.walk(folder / "A") for n in fs_)


def test_long_source_path_is_scanned_organized_and_undone(tmp_path, no_long_paths):
    folder = tmp_path / "in1"
    deep = str(folder)
    while len(deep) < 290:
        deep = os.path.join(deep, "a_rather_long_folder_name_for_testing")
    os.makedirs(fs(deep))
    put_long(deep, "song-128.mp3", "01 Song.mp3", artist="Artist", album="Album", title="Song", tracknumber="1")
    put_long(deep, "song.flac", "02 Other.flac", artist="Artist", album="Album", title="Other", tracknumber="2")
    before = sorted(os.listdir(fs(deep)))
    res, p = build(folder)
    assert len(res.tracks) == 2 and all(PREFIX not in t.path for t in res.tracks)
    out = mover.execute(p)
    assert not out.failed, out.failed
    moved = [os.path.join(str(folder), "Artist", "Album", n) for n in os.listdir(os.path.join(str(folder), "Artist", "Album"))]
    assert len(moved) == 2
    u = undo.undo(undo.find_log(str(folder)))
    assert not u.skipped
    assert sorted(n for n in os.listdir(fs(deep)) if n != "organize_log.json") == before


def _deep(base, length=290) -> str:
    deep = str(base)
    while len(deep) < length:
        deep = os.path.join(deep, "a_rather_long_folder_name_for_testing")
    os.makedirs(fs(deep))
    return deep


def test_cue_album_in_a_long_folder_keeps_its_file_names(tmp_path, no_long_paths):
    """Unread, the cue sheet would be ignored and its files renamed out from under it."""
    deep = _deep(tmp_path / "in")
    put_long(deep, "song-128.mp3", "track01.mp3", artist="A", album="B", title="Song", tracknumber="1")
    with open(fs(os.path.join(deep, "album.cue")), "w", encoding="utf-8") as f:
        f.write('FILE "track01.mp3" MP3\n  TRACK 01 AUDIO\n')
    _, p = build(tmp_path / "in")
    assert [i.keep_name for i in p.items] == [True]
    assert os.path.basename(p.items[0].dst) == "track01.mp3"


def test_identical_files_in_a_long_folder_are_found(tmp_path, no_long_paths):
    import shutil

    import dedupe

    deep = _deep(tmp_path / "in")
    put_long(deep, "song-128.mp3", "1.mp3", artist="A", album="B", title="Song", tracknumber="1")
    shutil.copyfile(fs(os.path.join(deep, "1.mp3")), fs(os.path.join(deep, "2.mp3")))
    res, _ = build(tmp_path / "in")
    assert [g.stage for g in dedupe.find(res.tracks)] == [1]


def test_fpcalc_gets_the_long_form_of_a_long_path(tmp_path, no_long_paths, monkeypatch):
    import dedupe

    deep = _deep(tmp_path / "in")
    put_long(deep, "song-128.mp3", "1.mp3", artist="A", album="B", title="Song", tracknumber="1")
    res, _ = build(tmp_path / "in")
    given = []
    monkeypatch.setattr(dedupe, "run_fpcalc", lambda exe, path: (given.append(path), None)[1])
    dedupe.find(res.tracks, fingerprint=True, fpcalc="fpcalc")
    assert given and all(p.startswith(PREFIX) for p in given)


def test_undo_finds_a_log_in_a_long_destination(tmp_path, no_long_paths):
    from conftest import put, snapshot

    src = tmp_path / "src"
    put(src, "song-128.mp3", "a.mp3", artist="A", album="B", title="Song", tracknumber="1")
    before = snapshot(src)
    dest = _deep(tmp_path / "dst", 250)  # the log there is longer than 260 characters
    _, p = build(src, dest=dest)
    out = mover.execute(p)
    assert not out.failed and len(out.log_path) >= 260
    assert undo.find_log(dest) == out.log_path
    assert [r.id for r in undo.runs_in(undo.candidate_logs(dest))]
    u = undo.undo(out.log_path)
    assert not u.nothing and u.restored == 1 and not u.skipped
    assert snapshot(src) == before
