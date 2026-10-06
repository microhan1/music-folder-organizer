"""v0.4.0 item 3: a rescan opens only files that changed (scan.TagCache)."""
from __future__ import annotations

import os
import shutil

import mutagen
import pytest

import mover
import scan as scan_mod
from conftest import build, put


@pytest.fixture
def opened(monkeypatch):
    """Paths the scan really opened with mutagen."""
    real, calls = scan_mod._open, []

    def spy(path, fmt):
        calls.append(path)
        return real(path, fmt)

    monkeypatch.setattr(scan_mod, "_open", spy)
    return calls


def _lib(root, n=4):
    for i in range(n):
        put(root, "tone.ogg", f"in/{i}.ogg", title=f"T{i}", artist="A", album="B", tracknumber=str(i + 1))


def _tags(res):
    return {os.path.relpath(t.path, res.root): (t.tags.title, t.tags.artist) for t in res.tracks}


def test_rescan_opens_nothing_that_did_not_change(tmp_path, opened):
    _lib(tmp_path)
    cache = scan_mod.TagCache()
    first = scan_mod.scan(str(tmp_path), cache=cache)
    assert len(opened) == 4 and cache.hits == 0
    opened.clear()
    second = scan_mod.scan(str(tmp_path), cache=cache)
    assert opened == [] and cache.hits == 4
    assert _tags(second) == _tags(first)
    assert all(a is not b for a, b in zip(first.tracks, second.tracks))  # copies: the old plan keeps its own


def test_a_moved_file_is_known_under_its_new_path(tmp_path, opened):
    _lib(tmp_path)
    cache = scan_mod.TagCache()
    scan_mod.scan(str(tmp_path), cache=cache)
    os.makedirs(tmp_path / "elsewhere")
    os.rename(tmp_path / "in" / "2.ogg", tmp_path / "elsewhere" / "renamed.ogg")
    opened.clear()
    res = scan_mod.scan(str(tmp_path), cache=cache)
    assert opened == []
    moved = next(t for t in res.tracks if t.name == "renamed.ogg")
    assert moved.tags.title == "T2" and moved.path == str(tmp_path / "elsewhere" / "renamed.ogg")


def test_a_file_whose_tags_changed_is_read_again(tmp_path, opened):
    _lib(tmp_path)
    cache = scan_mod.TagCache()
    scan_mod.scan(str(tmp_path), cache=cache)
    target = tmp_path / "in" / "1.ogg"
    audio = mutagen.File(target, easy=True)
    audio["title"] = "Filled in later"  # what Music Tag Filler does; the modified time moves on
    audio.save()
    st = os.stat(target)
    os.utime(target, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))  # even within the clock's step
    opened.clear()
    res = scan_mod.scan(str(tmp_path), cache=cache)
    assert opened == [str(target)]
    assert next(t for t in res.tracks if t.path == str(target)).tags.title == "Filled in later"


def test_a_copy_on_another_place_with_a_new_identity_is_read(tmp_path, opened):
    _lib(tmp_path, 1)
    cache = scan_mod.TagCache()
    scan_mod.scan(str(tmp_path), cache=cache)
    shutil.copy2(tmp_path / "in" / "0.ogg", tmp_path / "in" / "copy.ogg")  # same size and time, another file
    opened.clear()
    scan_mod.scan(str(tmp_path), cache=cache)
    assert opened == [str(tmp_path / "in" / "copy.ogg")]


def test_unreadable_files_are_not_kept(tmp_path, opened):
    (tmp_path / "broken.mp3").write_bytes(b"not really an mp3" * 10)
    cache = scan_mod.TagCache()
    assert scan_mod.scan(str(tmp_path), cache=cache).tracks[0].error
    opened.clear()
    scan_mod.scan(str(tmp_path), cache=cache)
    assert opened == [str(tmp_path / "broken.mp3")]  # may be readable (unlocked) next time


def test_a_cancelled_scan_keeps_the_cache(tmp_path, opened):
    import threading

    _lib(tmp_path)
    cache = scan_mod.TagCache()
    scan_mod.scan(str(tmp_path), cache=cache)
    stop = threading.Event()
    stop.set()
    assert scan_mod.scan(str(tmp_path), cancel=stop, cache=cache).cancelled
    opened.clear()
    scan_mod.scan(str(tmp_path), cache=cache)
    assert opened == []


def test_files_that_left_the_scan_and_came_back_are_not_opened(tmp_path, opened):
    """Duplicates go to the dupes folder, which scans skip; an undo brings them back."""
    _lib(tmp_path)
    cache = scan_mod.TagCache()
    scan_mod.scan(str(tmp_path), cache=cache)
    os.makedirs(tmp_path / "_Duplicates")
    os.rename(tmp_path / "in" / "3.ogg", tmp_path / "_Duplicates" / "3.ogg")
    assert len(scan_mod.scan(str(tmp_path), [str(tmp_path / "_Duplicates")], cache=cache).tracks) == 3
    os.rename(tmp_path / "_Duplicates" / "3.ogg", tmp_path / "in" / "3.ogg")
    opened.clear()
    assert len(scan_mod.scan(str(tmp_path), cache=cache).tracks) == 4
    assert opened == [] and cache.hits == 4


def test_drives_without_file_numbers_fall_back_to_the_path(tmp_path):
    _lib(tmp_path, 1)
    path = str(tmp_path / "in" / "0.ogg")
    real = os.stat(path)
    fake = os.stat_result((real.st_mode, 0, real.st_dev, 1, 0, 0, real.st_size, 0, 0, 0))  # st_ino 0
    assert scan_mod.TagCache.key(path, fake) == ("path", scan_mod.key_of(path))
    assert scan_mod.TagCache.key(path, real)[0] == "id"


def test_after_a_run_the_rescan_opens_nothing_and_everything_is_in_place(tmp_path, opened):
    _lib(tmp_path)
    cache = scan_mod.TagCache()
    res = scan_mod.scan(str(tmp_path), cache=cache)
    p = build(tmp_path)[1]
    assert mover.execute(p).done == 4
    opened.clear()
    again = scan_mod.scan(str(tmp_path), cache=cache)
    assert opened == [] and cache.hits == 4
    assert sorted(_tags(again).values()) == sorted(_tags(res).values())


def _same_size_lib(root):
    """Two identical files and two others of one size: stage 1 has to hash all four."""
    put(root, "tone.ogg", "a/1.ogg", title="T", artist="A", album="B", tracknumber="1")
    shutil.copy2(root / "a" / "1.ogg", root / "a" / "2.ogg")
    put(root, "tone.ogg", "b/3.ogg", title="U", artist="A", album="B", tracknumber="2")
    put(root, "tone.ogg", "b/4.ogg", title="V", artist="A", album="B", tracknumber="3")


def test_hashes_are_kept_with_unchanged_files(tmp_path, monkeypatch):
    import dedupe

    _same_size_lib(tmp_path)
    real, hashed = dedupe.sha1_file, []
    monkeypatch.setattr(dedupe, "sha1_file", lambda p, c=None: (hashed.append(p), real(p, c))[1])
    cache = scan_mod.TagCache()
    first = dedupe.find(scan_mod.scan(str(tmp_path), cache=cache).tracks)
    assert len(hashed) == 4 and len(first) == 1
    hashed.clear()
    again = dedupe.find(scan_mod.scan(str(tmp_path), cache=cache).tracks)
    assert hashed == [] and [len(g.members) for g in again] == [len(g.members) for g in first]
    audio = mutagen.File(tmp_path / "a" / "2.ogg", easy=True)  # now it differs from 1.ogg
    audio["comment"] = "changed"
    audio.save()
    st = os.stat(tmp_path / "a" / "2.ogg")
    os.utime(tmp_path / "a" / "2.ogg", ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    hashed.clear()
    res = scan_mod.scan(str(tmp_path), cache=cache)
    groups = dedupe.find(res.tracks)
    assert str(tmp_path / "a" / "2.ogg") in hashed  # re-hashed: changed
    assert not any(g.stage == 1 for g in groups)  # no longer identical


def test_fingerprints_are_kept_and_failures_retried(tmp_path, monkeypatch):
    import dedupe

    _lib(tmp_path, 3)
    runs, fail = [], {str(tmp_path / "in" / "2.ogg")}

    def fake(exe, path):
        runs.append(path)
        return None if path in fail else list(range(200))

    monkeypatch.setattr(dedupe, "run_fpcalc", fake)
    cache = scan_mod.TagCache()
    dedupe.find(scan_mod.scan(str(tmp_path), cache=cache).tracks, fingerprint=True, fpcalc="fpcalc")
    assert len(runs) == 3
    runs.clear()
    fail.clear()
    dedupe.find(scan_mod.scan(str(tmp_path), cache=cache).tracks, fingerprint=True, fpcalc="fpcalc")
    assert runs == [str(tmp_path / "in" / "2.ogg")]  # only the one that failed last time
