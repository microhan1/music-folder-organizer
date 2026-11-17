"""v0.6.0 item 1: big files that are not music (an .iso, a .zip) are counted and shown, not organized."""
import os

import mover
import scan as scan_mod
from conftest import build, put

MIB = 1024 * 1024


def big(path, mib=9):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.truncate(mib * MIB)  # zeros up to that size. NOT sparse on this PC: keep the sizes small (LESSONS D25)


def test_big_images_and_archives_are_counted(tmp_path):
    put(tmp_path, "song-128.mp3", "in/a.mp3", title="A", artist="X", album="Y", tracknumber="1")
    big(str(tmp_path / "sacd" / "Album.iso"), 9)
    big(str(tmp_path / "Album (2000).zip"), 12)
    res, p = build(tmp_path)
    assert sorted(os.path.basename(path) for path, _ in res.skipped) == ["Album (2000).zip", "Album.iso"]
    assert (p.skipped_files, p.skipped_bytes) == (2, 21 * MIB)


def test_what_is_not_counted(tmp_path):
    big(str(tmp_path / "small.iso"), 1)                      # below 8 MiB
    big(str(tmp_path / "cover.jpg"), 9)                      # an album extra: it travels with its album
    big(str(tmp_path / "rip.log"), 9)
    big(str(tmp_path / "song.mp3"), 9)                       # music (a broken one still is a track)
    big(str(tmp_path / ".DS_Store"), 9)                      # clutter
    big(str(tmp_path / "._big.iso"), 9)                      # macOS resource fork
    big(str(tmp_path / "organize_log.json"), 9)              # this tool's own undo log
    big(str(tmp_path / "organize_log.json.journal"), 9)
    big(str(tmp_path / "_Duplicates" / "old.iso"), 9)        # the duplicates folder is never scanned
    res, p = build(tmp_path)
    assert res.skipped == [] and p.skipped_files == 0


def test_the_big_files_stay_where_they_are_after_a_run(tmp_path):
    put(tmp_path, "song-128.mp3", "in/a.mp3", title="A", artist="X", album="Y", tracknumber="1")
    big(str(tmp_path / "sacd" / "Album.iso"), 9)
    res = mover.execute(build(tmp_path)[1])
    assert not res.failed and (tmp_path / "sacd" / "Album.iso").exists()
    assert build(tmp_path)[1].skipped_files == 1  # still reported: it is still there


def test_sizes_read_like_a_person_would():
    assert scan_mod.format_size(193 * MIB) == "193MB"
    assert scan_mod.format_size(int(15.88 * 1024 ** 3)) == "15.9GB"
    assert scan_mod.format_size(8 * MIB) == "8MB"


def test_cli_dry_run_lists_them(tmp_path, capsys):
    import main

    put(tmp_path, "song-128.mp3", "in/a.mp3", title="A", artist="X", album="Y", tracknumber="1")
    big(str(tmp_path / "sacd" / "Album.iso"), 9)
    assert main.main([str(tmp_path), "--dry-run", "--lang", "en"]) == 0
    out = capsys.readouterr().out
    assert "Big non-music files left alone: 1 (9MB)" in out and "sacd/Album.iso (9MB)" in out.replace("\\", "/")


def test_cli_with_only_images_still_says_what_it_saw(tmp_path, capsys):
    import main

    big(str(tmp_path / "sacd" / "Album.iso"), 9)
    assert main.main([str(tmp_path), "--dry-run", "--lang", "en"]) == 1  # no music: nothing to organize
    out = capsys.readouterr().out
    assert "Big non-music files left alone: 1 (9MB)" in out
