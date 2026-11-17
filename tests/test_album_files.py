"""v0.6.0 item 2: two identical album images (.iso) or archives (.zip) are found and the extra copy goes to the
duplicates folder with the extras that sit beside it. The 8 MiB limit is lowered to 1 KiB here so the files stay small."""
import os

import pytest

import dedupe
import mover
import plan as plan_mod
import scan as scan_mod
import undo
from conftest import build, put, snapshot


@pytest.fixture(autouse=True)
def small_is_big(monkeypatch):
    monkeypatch.setattr(scan_mod, "BIG_FILE_BYTES", 1024)


def album(root, name, iso=b"A" * 5000, extras=True, image="Album.iso"):
    d = root / name
    (d / "Artwork").mkdir(parents=True, exist_ok=True)
    (d / image).write_bytes(iso)
    if extras:
        (d / "folder.jpg").write_bytes(b"cover")
        (d / "notes.txt").write_text("log", encoding="utf-8")
        (d / "Artwork" / "front.png").write_bytes(b"png")


def twins(root):
    album(root, "Album [SACD]")
    album(root, "Album [SACD] (1)")


def rel(p, path):
    return os.path.relpath(path, p.options.dest).replace(os.sep, "/")


def test_identical_images_are_one_group_and_the_copy_goes_to_the_duplicates_folder(tmp_path):
    twins(tmp_path)
    res, p = build(tmp_path, groups="find")
    groups = dedupe.find(res.dupe_candidates)
    assert len(groups) == 1 and groups[0].stage == 1 and all(m.is_album_file for m in groups[0].members)
    assert [os.path.basename(os.path.dirname(m.path)) for m in groups[0].members][0] == "Album [SACD]"  # the shorter path stays
    dup = [i for i in p.items if i.status == plan_mod.DUP]
    assert len(dup) == 1 and rel(p, dup[0].dst) == "_Duplicates/Album [SACD] (1)/Album.iso"
    assert p.summary()["dupes"] == 1 and p.summary()["untagged"] == 0
    # what sits beside the image follows it, so no empty shell stays behind
    moves = sorted(rel(p, c.dst) for c in p.companions)
    assert moves == ["_Duplicates/Album [SACD] (1)/Artwork/front.png", "_Duplicates/Album [SACD] (1)/folder.jpg",
                     "_Duplicates/Album [SACD] (1)/notes.txt"]


def test_run_then_run_again_then_undo(tmp_path):
    twins(tmp_path)
    before = snapshot(tmp_path)
    res = mover.execute(build(tmp_path, groups="find")[1])
    assert not res.failed
    assert not (tmp_path / "Album [SACD] (1)").exists()                      # emptied and removed
    assert (tmp_path / "_Duplicates" / "Album [SACD] (1)" / "Album.iso").exists()
    assert (tmp_path / "_Duplicates" / "Album [SACD] (1)" / "Artwork" / "front.png").exists()
    assert (tmp_path / "Album [SACD]" / "Album.iso").exists()               # the one that stays is untouched
    again = build(tmp_path, groups="find")[1]
    assert again.summary()["move"] == 0 and again.summary()["dupes"] == 0    # the duplicates folder is not scanned
    u = undo.undo(res.log_path)
    assert u.restored == 1 + 3 and not u.skipped  # the image and the three extras that went with it
    os.remove(res.log_path)
    assert snapshot(tmp_path) == before


def test_same_size_but_different_bytes_is_not_a_duplicate(tmp_path):
    album(tmp_path, "One", iso=b"A" * 5000)
    album(tmp_path, "Two", iso=b"B" * 5000)
    res, p = build(tmp_path, groups="find")
    assert dedupe.find(res.dupe_candidates) == [] and p.summary()["dupes"] == 0
    assert p.skipped_files == 2  # both stay and are reported


def test_one_image_is_left_alone_and_counted(tmp_path):
    album(tmp_path, "Only")
    _, p = build(tmp_path, groups="find")
    assert [i for i in p.items if i.track.is_album_file] == [] and p.skipped_files == 1


def test_a_moved_copy_is_no_longer_left_alone(tmp_path):
    twins(tmp_path)
    _, p = build(tmp_path, groups="find")
    assert p.skipped_files == 1 and p.skipped_bytes == 5000  # only the copy that stays


def test_images_are_never_missing_tags(tmp_path):
    twins(tmp_path)
    _, p = build(tmp_path, groups="find")
    assert p.untagged() == [] and p.summary()["untagged"] == 0


def test_the_trash_option_plans_no_move_of_the_extras(tmp_path):
    twins(tmp_path)
    _, p = build(tmp_path, groups="find", dupes_action="trash")
    dup = [i for i in p.items if i.status == plan_mod.DUP]
    assert len(dup) == 1 and dup[0].action == plan_mod.DUPE_TRASH and dup[0].dst == ""
    assert p.companions == []  # nothing to follow into the recycle bin (the same rule as for songs)


def test_copy_mode_does_not_copy_the_duplicate(tmp_path):
    twins(tmp_path)
    _, p = build(tmp_path, dest=tmp_path / "out", mode="copy", groups="find")
    dup = [i for i in p.items if i.status == plan_mod.DUP]
    assert len(dup) == 1 and not dup[0].moves  # an unticked duplicate: nothing is copied


def test_small_and_other_big_files_are_not_compared(tmp_path):
    album(tmp_path, "A", iso=b"A" * 500)                 # under the limit
    album(tmp_path, "B", iso=b"A" * 500)
    album(tmp_path, "C", iso=b"Z" * 5000, image="movie.dat")   # big, identical, but not an album file
    album(tmp_path, "D", iso=b"Z" * 5000, image="movie.dat")
    res, p = build(tmp_path, groups="find")
    assert dedupe.find(res.dupe_candidates) == []
    assert p.skipped_files == 2  # only the two .dat files are "big"; they stay


def test_songs_in_the_folder_are_not_disturbed(tmp_path):
    twins(tmp_path)
    put(tmp_path, "song-128.mp3", "Album [SACD]/01.mp3", title="S", artist="X", album="Y", tracknumber="1")
    _, p = build(tmp_path, groups="find")
    song = next(i for i in p.items if not i.track.is_album_file)
    assert rel(p, song.dst) == "X/Y/01 - S.mp3" and song.status == plan_mod.OK
    assert [rel(p, i.dst) for i in p.items if i.status == plan_mod.DUP] == ["_Duplicates/Album [SACD] (1)/Album.iso"]


def test_a_zip_pair_works_the_same(tmp_path):
    album(tmp_path, "Disc A", iso=b"Q" * 4000, image="Album (2000).zip", extras=False)
    album(tmp_path, "Disc A (copy)", iso=b"Q" * 4000, image="Album (2000).zip", extras=False)
    _, p = build(tmp_path, groups="find")
    assert [rel(p, i.dst) for i in p.items if i.status == plan_mod.DUP] == ["_Duplicates/Disc A (copy)/Album (2000).zip"]


def test_sizes_under_a_megabyte_read_in_kb():
    assert scan_mod.format_size(5000) == "5KB" and scan_mod.format_size(3 * 2 ** 20) == "3MB"


def test_cli_dedupe_works_on_a_folder_with_only_images(tmp_path, capsys):
    """The real library's pair had no music beside it: the CLI stopped at "no music" before comparing (found on a real-folder copy)."""
    import main

    twins(tmp_path)
    assert main.main([str(tmp_path), "--dedupe", "--dry-run", "--lang", "en"]) == 0
    out = capsys.readouterr().out
    assert "[Duplicate]" in out and "Big non-music files left alone: 1" in out
    listed = [l for l in out.splitlines() if l.startswith("  - ")]
    assert len(listed) == 1 and "Album [SACD] (1)" not in listed[0]  # the moved copy is not listed as left alone
    assert main.main([str(tmp_path), "--lang", "en"]) == 1  # without --dedupe there is still nothing to organize
