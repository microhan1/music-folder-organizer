"""v0.2.0 item 1: album extras follow a whole album; cue albums keep their file names."""
import os
import shutil

import mover
import plan as plan_mod
import scan as scan_mod
import undo
from conftest import build, put, snapshot


def album(root, folder="dl/Some Album [FLAC]", artist="Artist", name="Album", n=2, sample="song-128.mp3"):
    for i in range(1, n + 1):
        put(root, sample, f"{folder}/{i:02d}.mp3", title=f"T{i}", artist=artist, album=name, tracknumber=str(i))
    return os.path.join(str(root), *folder.split("/"))


def extras(folder):
    for name, data in (("album.cue", "x"), ("rip.log", "x"), ("info.txt", "x"), ("list.m3u", "x"),
                       ("back.jpg", "x"), ("booklet.pdf", "x")):
        with open(os.path.join(folder, name), "w", encoding="utf-8") as f:
            f.write(data)
    os.makedirs(os.path.join(folder, "Artwork", "Inlay"))
    for rel in ("Artwork/front.png", "Artwork/Thumbs.db", "Artwork/Inlay/1.jpg"):
        with open(os.path.join(folder, *rel.split("/")), "w") as f:
            f.write("img")


def test_whole_album_takes_its_extras_and_undo_restores(tmp_path):
    src = album(tmp_path)
    extras(src)
    before = snapshot(tmp_path)
    _, p = build(tmp_path)
    s = p.summary()
    assert s["sidecars"] == 8  # 6 files + front.png + Inlay/1.jpg; Thumbs.db is junk
    target = os.path.join(str(tmp_path), "Artist", "Album")
    assert {os.path.relpath(c.dst, target) for c in p.sidecars()} == {
        "album.cue", "rip.log", "info.txt", "list.m3u", "back.jpg", "booklet.pdf",
        os.path.join("Artwork", "front.png"), os.path.join("Artwork", "Inlay", "1.jpg")}
    res = mover.execute(p)
    assert not res.failed
    assert not os.path.exists(src)  # nothing left behind, folder removed
    assert os.path.exists(os.path.join(target, "Artwork", "Inlay", "1.jpg"))
    undo.undo(res.log_path)
    os.remove(res.log_path)
    del before["files"][os.path.join("dl", "Some Album [FLAC]", "Artwork", "Thumbs.db")]  # junk goes with its folder
    assert snapshot(tmp_path) == before


def test_split_album_leaves_extras(tmp_path):
    src = album(tmp_path, n=1)
    put(tmp_path, "other.mp3", "dl/Some Album [FLAC]/99.mp3", title="X", artist="Else", album="Other", tracknumber="1")
    extras(src)
    _, p = build(tmp_path)
    assert p.sidecars() == []
    mover.execute(p)
    assert os.path.exists(os.path.join(src, "rip.log")) and os.path.isdir(src)


def test_unticked_song_keeps_the_extras_home(tmp_path):
    src = album(tmp_path)
    extras(src)
    first = scan_mod.key_of(os.path.join(src, "01.mp3"))
    _, p = build(tmp_path, overrides={first: False})
    assert p.sidecars() == []


def test_unknown_files_stay_but_known_extras_go(tmp_path):
    src = album(tmp_path)
    for name in ("rip.log", "resume.docx", "photo.heic"):
        with open(os.path.join(src, name), "w") as f:
            f.write("x")
    _, p = build(tmp_path)
    assert [os.path.basename(c.src) for c in p.sidecars()] == ["rip.log"]
    mover.execute(p)
    assert sorted(os.listdir(src)) == ["photo.heic", "resume.docx"]  # folder kept for them


def test_subfolder_with_music_is_its_own_album(tmp_path):
    root = tmp_path
    for disc in (1, 2):
        put(root, "song-128.mp3", f"box/Disc {disc}/01.mp3", title=f"D{disc}", artist="A", album=f"Box {disc}", tracknumber="1")
    with open(os.path.join(str(root), "box", "box.log"), "w") as f:
        f.write("x")
    os.makedirs(os.path.join(str(root), "box", "Scans"))
    with open(os.path.join(str(root), "box", "Scans", "s.jpg"), "w") as f:
        f.write("x")
    _, p = build(root)
    # "box" holds no music itself, so it is not an album folder; its discs are albums without extras
    assert p.sidecars() == []


def test_subfolder_with_unknown_file_stays_whole(tmp_path):
    src = album(tmp_path)
    os.makedirs(os.path.join(src, "Artwork"))
    for name in ("a.jpg", "notes.docx"):
        with open(os.path.join(src, "Artwork", name), "w") as f:
            f.write("x")
    _, p = build(tmp_path)
    assert p.sidecars() == []  # not split: Artwork stays together with its unknown file


def test_existing_file_at_target_is_kept_and_extra_stays(tmp_path):
    src = album(tmp_path)
    extras(src)
    target = os.path.join(str(tmp_path), "Artist", "Album")
    os.makedirs(target)
    with open(os.path.join(target, "rip.log"), "w") as f:
        f.write("already here")
    _, p = build(tmp_path)
    assert "rip.log" not in [os.path.basename(c.src) for c in p.sidecars()]
    mover.execute(p)
    assert open(os.path.join(target, "rip.log")).read() == "already here"
    assert os.path.exists(os.path.join(src, "rip.log"))


def test_copy_mode_copies_extras(tmp_path):
    src = album(tmp_path / "in")
    extras(src)
    before = snapshot(tmp_path / "in")
    _, p = build(tmp_path / "in", dest=tmp_path / "out", mode="copy")
    assert all(c.op == "copy" for c in p.sidecars()) and len(p.sidecars()) == 8
    res = mover.execute(p)
    assert snapshot(tmp_path / "in") == before
    assert os.path.exists(tmp_path / "out" / "Artist" / "Album" / "Artwork" / "front.png")
    undo.undo(res.log_path)
    assert os.listdir(tmp_path / "out") == ["organize_log.json"]


def test_duplicate_album_takes_extras_to_the_duplicates_folder(tmp_path):
    src = album(tmp_path, folder="a/Album")
    extras(src)
    copy = os.path.join(str(tmp_path), "b", "Album (1)")
    shutil.copytree(src, copy)
    _, p = build(tmp_path, groups="find")
    dupe_dir = {os.path.dirname(i.dst) for i in p.items if i.status == plan_mod.DUP}
    assert len(dupe_dir) == 1 and "_Duplicates" in next(iter(dupe_dir))
    extras_to_dupes = [c for c in p.sidecars() if "_Duplicates" in c.dst]
    assert len(extras_to_dupes) == 8


def test_failed_song_keeps_its_album_extras(tmp_path):
    src = album(tmp_path)
    extras(src)
    _, p = build(tmp_path)
    with open(os.path.join(src, "01.mp3"), "rb"):  # in use: cannot be moved on Windows
        res = mover.execute(p)
    if os.name != "nt":
        return
    assert len(res.failed) == 1
    assert os.path.exists(os.path.join(src, "rip.log"))  # the album is not split


def test_root_folders_never_give_away_files(tmp_path):
    put(tmp_path, "song-128.mp3", "loose.mp3", title="T", artist="A", album="B", tracknumber="1")
    with open(tmp_path / "untagged.txt", "w") as f:
        f.write("x")
    with open(tmp_path / "notes.txt", "w") as f:
        f.write("x")
    _, p = build(tmp_path)
    assert p.sidecars() == []


def test_option_off(tmp_path):
    src = album(tmp_path)
    extras(src)
    _, p = build(tmp_path, move_sidecars=False)
    assert p.sidecars() == [] and p.summary()["sidecars"] == 0


# ------------------------------------------------------------------ cue albums
def cue_album(root, encoding="utf-8", names=("01 Intro.flac", "02 Song.flac")):
    folder = os.path.join(str(root), "rip", "Album [CUE]")
    for i, name in enumerate(names, 1):
        put(root, "song.flac", f"rip/Album [CUE]/{name}", title=f"Title {i}", artist="Singer", album="Cue Album",
            tracknumber=str(i))
    lines = ['PERFORMER "Singer"', 'TITLE "Cue Album"']
    for i, name in enumerate(names, 1):
        lines += [f'FILE "{name}" WAVE', f"  TRACK {i:02d} AUDIO", "    INDEX 01 00:00:00"]
    with open(os.path.join(folder, "album.cue"), "wb") as f:
        f.write("\r\n".join(lines).encode(encoding))
    return folder


def test_cue_album_keeps_file_names_and_its_cue(tmp_path):
    src = cue_album(tmp_path)
    cue_bytes = open(os.path.join(src, "album.cue"), "rb").read()
    _, p = build(tmp_path)
    target = os.path.join(str(tmp_path), "Singer", "Cue Album")
    assert sorted(os.path.basename(i.dst) for i in p.items) == ["01 Intro.flac", "02 Song.flac"]
    assert all(i.keep_name for i in p.items)
    assert [os.path.basename(c.dst) for c in p.sidecars()] == ["album.cue"]
    mover.execute(p)
    assert sorted(os.listdir(target)) == ["01 Intro.flac", "02 Song.flac", "album.cue"]
    assert open(os.path.join(target, "album.cue"), "rb").read() == cue_bytes  # sheet untouched and still valid


def test_shift_jis_cue_is_understood(tmp_path):
    cue_album(tmp_path, encoding="cp932", names=("01 はじまり.flac", "02 うた.flac"))
    _, p = build(tmp_path)
    assert all(i.keep_name for i in p.items)


def test_cue_naming_other_files_does_not_freeze_names(tmp_path):
    src = album(tmp_path)
    with open(os.path.join(src, "image.cue"), "w") as f:
        f.write('FILE "CDImage.wav" WAVE\n  TRACK 01 AUDIO\n')
    _, p = build(tmp_path)
    assert not any(i.keep_name for i in p.items)
    assert sorted(os.path.basename(i.dst) for i in p.items) == ["01 - T1.mp3", "02 - T2.mp3"]


def test_cue_refs_parser(tmp_path):
    cue = tmp_path / "x.cue"
    cue.write_bytes(b'\xef\xbb\xbfFILE "sub\\A.flac" WAVE\nfile b.wav WAVE\nREM FILE "no"\n')
    assert scan_mod.cue_refs(str(cue)) == {"a.flac", "b.wav"}
    assert scan_mod.cue_refs(str(tmp_path / "missing.cue")) == set()
