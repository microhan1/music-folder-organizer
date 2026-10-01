"""Scan -> plan -> execute -> undo on real (tiny) audio files in tmp folders."""
import json
import os
import shutil
import time

import pytest

import dedupe
import main
import mover
import plan as plan_mod
import prefs as prefs_mod
import scan as scan_mod
import session
import undo
from conftest import build, put, sample, snapshot


@pytest.fixture
def library(tmp_path):
    root = tmp_path / "music"
    put(root, "song-320.mp3", "새 폴더/01 a.mp3", title="Alpha", artist="岡田 有希子", album="Fairy", tracknumber="1")
    put(root, "other.mp3", "새 폴더/새 폴더 (2)/b.mp3", title="Beta", artist="岡田有希子", album="Fairy", tracknumber="2")
    put(root, "tone.ogg", "misc/c.ogg", title="Gamma", artist="池田綾子", album="HIKARI", tracknumber="3/14")
    put(root, "tone.wav", "misc/untagged.wav")
    (root / "새 폴더" / "01 a.lrc").write_text("[00:00]la", encoding="utf-8")
    (root / "새 폴더" / "01 a.mp3.tagbak.json").write_text("{}", encoding="utf-8")
    (root / "새 폴더" / "cover.jpg").write_bytes(b"jpg")
    (root / "새 폴더" / "새 폴더 (2)" / "Thumbs.db").write_bytes(b"x")
    return root


def test_preview_and_statuses(library):
    _, p = build(library)
    by_name = {os.path.basename(i.src): i for i in p.items}
    assert os.path.relpath(by_name["01 a.mp3"].dst, library) == os.path.join("岡田 有希子", "Fairy", "01 - Alpha.mp3")
    assert os.path.relpath(by_name["b.mp3"].dst, library) == os.path.join("岡田 有希子", "Fairy", "02 - Beta.mp3")
    assert by_name["b.mp3"].artist_note == "岡田有希子 → 岡田 有希子"
    assert by_name["untagged.wav"].status == plan_mod.UNTAGGED and not by_name["untagged.wav"].checked
    s = p.summary()
    assert (s["move"], s["untagged"]) == (3, 1)
    kinds = sorted(c.kind for c in p.companions)
    assert kinds == ["cover", "lrc", "tagbak"]
    lrc = next(c for c in p.companions if c.kind == "lrc")
    assert lrc.dst.endswith("01 - Alpha.lrc")
    # "새 폴더 (2)" holds only b.mp3 and Thumbs.db; "새 폴더" empties too (cover moves with everything)
    assert sorted(os.path.basename(d) for d in p.empty_dirs) == ["새 폴더", "새 폴더 (2)"]


def test_run_then_undo_restores_everything(library):
    before = snapshot(library)
    _, p = build(library)
    res = mover.execute(p)
    assert res.done == 3 and not res.failed and res.folders_removed == 2
    assert (library / "岡田 有希子" / "Fairy" / "01 - Alpha.lrc").exists()
    assert (library / "岡田 有希子" / "Fairy" / "01 - Alpha.mp3.tagbak.json").exists()
    assert (library / "岡田 有希子" / "Fairy" / "cover.jpg").exists()
    assert not (library / "새 폴더").exists()
    # contents untouched; Thumbs.db went with its emptied folder and does not come back
    del before["files"][os.path.join("새 폴더", "새 폴더 (2)", "Thumbs.db")]
    after_run = snapshot(library)
    assert sorted(after_run["files"].values()) == sorted(list(before["files"].values()) + [after_run["files"]["organize_log.json"]])
    u = undo.undo(res.log_path)
    assert u.restored == 6 and not u.skipped
    os.remove(res.log_path)
    assert snapshot(library) == before
    assert undo.undo(res.log_path).nothing


def test_second_run_changes_nothing(library):
    mover.execute(build(library)[1])
    _, p = build(library)
    assert p.summary()["move"] == 0
    assert all(i.status in (plan_mod.SAME, plan_mod.UNTAGGED) for i in p.items)


def test_conflicts_get_numbers(tmp_path):
    root = tmp_path / "m"
    put(root, "song-128.mp3", "x/1.mp3", title="Same", artist="A", album="B", tracknumber="1")
    put(root, "other.mp3", "y/1.mp3", title="Same", artist="A", album="B", tracknumber="1")
    put(root, "tone.ogg", "z/1.ogg", title="same", artist="a", album="b", tracknumber="1")  # differs only by case
    _, p = build(root)
    names = sorted(os.path.basename(i.dst) for i in p.items)
    assert names == ["01 - Same (2).mp3", "01 - Same.mp3", "01 - same.ogg"]
    assert [i.status for i in p.items].count(plan_mod.CONFLICT) == 1


def test_existing_file_is_never_overwritten(tmp_path):
    root = tmp_path / "m"
    put(root, "song-128.mp3", "A/B/01 - T.mp3")  # untagged file sitting at the target
    put(root, "other.mp3", "in/x.mp3", title="T", artist="A", album="B", tracknumber="1")
    _, p = build(root)
    item = next(i for i in p.items if i.src.endswith("x.mp3"))
    assert item.dst.endswith("01 - T (2).mp3") and item.status == plan_mod.CONFLICT


def test_case_only_rename(tmp_path):
    root = tmp_path / "m"
    put(root, "song-128.mp3", "A/B/01 - t.mp3", title="T", artist="A", album="B", tracknumber="1")
    _, p = build(root)
    assert p.items[0].status == plan_mod.OK
    res = mover.execute(p)
    assert res.done == 1
    assert os.listdir(root / "A" / "B") == ["01 - T.mp3"]
    undo.undo(res.log_path)
    assert os.listdir(root / "A" / "B") == ["01 - t.mp3"]


def test_three_encodings_group_and_flac_is_kept(tmp_path):
    root = tmp_path / "m"
    for name, rel in (("song-128.mp3", "dl/song.mp3"), ("song-320.mp3", "dl2/song.mp3"), ("song.flac", "a/song.flac")):
        put(root, name, rel, title="Song", artist="Artist", album="Album", tracknumber="1")
    res, p = build(root, groups="find")
    groups = dedupe.find(res.tracks)
    assert len(groups) == 1 and len(groups[0].members) == 3
    assert groups[0].members[0].fmt == "FLAC"
    assert groups[0].keep == {scan_mod.key_of(groups[0].members[0].path)}
    dups = [i for i in p.items if i.status == plan_mod.DUP]
    assert len(dups) == 2 and all(f"{os.sep}_Duplicates{os.sep}" in i.dst for i in dups)


def test_identical_copies_are_stage_one(tmp_path):
    root = tmp_path / "m"
    a = put(root, "song-128.mp3", "a/x.mp3", title="T", artist="A")
    os.makedirs(root / "b")
    shutil.copyfile(a, root / "b" / "x.mp3")
    os.makedirs(root / "aa")
    shutil.copyfile(a, root / "aa" / "renamed.mp3")
    groups = dedupe.find(scan_mod.scan(str(root)).tracks)
    assert len(groups) == 1 and groups[0].stage == 1 and len(groups[0].members) == 3


def test_fingerprint_finds_same_recording_with_other_tags(tmp_path):
    exe = dedupe.find_fpcalc()
    if not exe:
        pytest.skip("fpcalc not available")
    root = tmp_path / "m"
    put(root, "song-128.mp3", "a.mp3", title="Track 01", artist="Unknown")
    put(root, "song.flac", "b.flac", title="Real Title", artist="Real Artist")
    put(root, "other.mp3", "c.mp3", title="Else", artist="Someone")
    tracks = scan_mod.scan(str(root)).tracks
    assert dedupe.find(tracks) == []
    groups = dedupe.find(tracks, fingerprint=True, fpcalc=exe)
    assert len(groups) == 1 and groups[0].stage == 3
    assert sorted(os.path.basename(m.path) for m in groups[0].members) == ["a.mp3", "b.flac"]


def test_copy_mode_and_its_undo(tmp_path, library):
    dest = tmp_path / "sorted"
    before = snapshot(library)
    _, p = build(library, dest=dest, mode="copy")
    assert p.empty_dirs == []
    res = mover.execute(p)
    assert res.done == 3
    assert snapshot(library) == before  # the source is untouched
    assert (dest / "岡田 有希子" / "Fairy" / "02 - Beta.mp3").exists()
    undo.undo(res.log_path)
    assert sorted(os.listdir(dest)) == ["organize_log.json"]


def test_dest_inside_source_is_not_rescanned(library):
    dest = library / "sorted"
    mover.execute(build(library, dest=dest)[1])
    res, p = build(library, dest=dest)
    assert all("sorted" not in os.path.relpath(t.path, library).split(os.sep)[0] for t in res.tracks)


def test_undo_skips_when_original_place_is_taken(library):
    _, p = build(library)
    res = mover.execute(p)
    (library / "misc").mkdir(exist_ok=True)
    (library / "misc" / "c.ogg").write_bytes(b"new file")
    u = undo.undo(res.log_path)
    assert any(path.endswith("c.ogg") for path, _ in u.skipped)
    assert (library / "misc" / "c.ogg").read_bytes() == b"new file"


def test_untagged_included_uses_fallbacks(library):
    _, p = build(library, include_untagged=True)
    item = next(i for i in p.items if i.src.endswith("untagged.wav"))
    assert item.checked
    assert os.path.relpath(item.dst, library) == os.path.join("Unknown Artist", "Unknown Album", "untagged.wav")


def test_log_accumulates_runs(library, tmp_path):
    res1 = mover.execute(build(library)[1])
    put(library, "song-128.mp3", "late/new.mp3", title="New", artist="X", album="Y", tracknumber="1")
    res2 = mover.execute(build(library)[1])
    assert res1.log_path == res2.log_path
    data = json.load(open(res2.log_path, encoding="utf-8"))
    assert len(data["runs"]) == 2
    undo.undo(res2.log_path)
    assert (library / "late" / "new.mp3").exists()
    assert not (library / "새 폴더").exists()  # the first run is still in place
    undo.undo(res2.log_path)
    assert (library / "새 폴더" / "01 a.mp3").exists()


def test_cli_dry_run_moves_nothing(library, capsys):
    before = snapshot(library)
    assert main.main([str(library), "--dry-run", "--dedupe", "--lang", "en"]) == 0
    out = capsys.readouterr().out
    assert "01 - Alpha.mp3" in out and "Move 3" in out
    assert snapshot(library) == before


def test_cli_run_and_undo(library):
    before = snapshot(library)
    assert main.main([str(library), "--lang", "en"]) == 0
    assert (library / "untagged.txt").exists()
    os.remove(library / "untagged.txt")
    assert main.main([str(library), "--undo", "--lang", "en"]) == 0
    os.remove(library / "organize_log.json")
    del before["files"][os.path.join("새 폴더", "새 폴더 (2)", "Thumbs.db")]
    assert snapshot(library) == before
    assert main.main([str(library), "--undo", "--lang", "en"]) == 1


def test_thousand_files_scan_and_preview_under_ten_seconds(tmp_path):
    root = tmp_path / "big"
    src = put(tmp_path, "tone.ogg", "seed.ogg", title="T", artist="A", album="B")
    for i in range(1000):
        d = root / f"dl{i % 37}"
        d.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, d / f"{i}.ogg")
    start = time.perf_counter()
    res, p = build(root)
    elapsed = time.perf_counter() - start
    assert len(p.items) == 1000
    assert elapsed < 10, elapsed


def test_file_in_use_fails_alone(tmp_path):
    root = tmp_path / "m"
    a = put(root, "song-128.mp3", "x/a.mp3", title="A", artist="X", album="Y", tracknumber="1")
    put(root, "other.mp3", "x/b.mp3", title="B", artist="X", album="Y", tracknumber="2")
    _, p = build(root)
    with open(a, "rb"):  # Windows will not move a file another handle has open
        res = mover.execute(p)
    if os.name != "nt":
        pytest.skip("open files can be moved on this OS")
    assert res.done == 1 and len(res.failed) == 1 and os.path.exists(a)
    assert (root / "x").exists()  # not empty: a.mp3 is still there


def test_numbered_files_stay_numbered_on_the_next_run(tmp_path):
    for i, name in enumerate(("song-128.mp3", "other.mp3", "song-320.mp3")):
        put(tmp_path, name, f"d{i}/x.mp3", title="Same", artist="A", album="B", tracknumber="1")
    mover.execute(build(tmp_path)[1])
    names = sorted(os.listdir(tmp_path / "A" / "B"))
    assert names == ["01 - Same (2).mp3", "01 - Same (3).mp3", "01 - Same.mp3"]
    for _ in range(2):
        _, p = build(tmp_path)
        assert all(i.status == plan_mod.SAME for i in p.items), [(i.src, i.dst) for i in p.items]
        mover.execute(p)
        assert sorted(os.listdir(tmp_path / "A" / "B")) == names
