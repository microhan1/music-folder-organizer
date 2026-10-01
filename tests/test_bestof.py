"""v0.2.0 item 2: a song on an original album and on a best-of is not a duplicate to
throw away; extra copies within one album are. Plus the rerun stability fix."""
import os
import shutil

import dedupe
import main
import mover
import plan as plan_mod
import scan as scan_mod
from artists import ArtistIndex
from conftest import build, put, snapshot
from scan import TagSet, Track, key_of


def tr(path, size, album, fmt="MP3", bitrate=128, lossless=False, length=200.0):
    return Track(path, fmt, size, length=length, bitrate=bitrate, lossless=lossless,
                 tags=TagSet(title="Sweet Planet", artist="A", album=album))


def test_one_file_per_album_is_kept():
    ts = [tr("C:\\orig\\a.mp3", 1, "Fairy"), tr("C:\\orig\\a.flac", 2, "Fairy", "FLAC", 900, True),
          tr("C:\\best\\09.mp3", 3, "All Songs Request", bitrate=320)]
    g = dedupe.find(ts)[0]
    assert g.cross_album and g.albums == 2
    assert g.keep == {key_of("C:\\orig\\a.flac"), key_of("C:\\best\\09.mp3")}
    assert [t.path for t in g.removable()] == ["C:\\orig\\a.mp3"]
    across = dedupe.find(ts, across_albums=True)[0]
    assert across.keep == {key_of("C:\\orig\\a.flac")} and len(across.removable()) == 2


def test_only_cross_album_copies_remove_nothing():
    ts = [tr("C:\\orig\\a.mp3", 1, "Fairy"), tr("C:\\best\\09.mp3", 2, "Best")]
    g = dedupe.find(ts)[0]
    assert g.cross_album and g.removable() == []


def test_album_names_compare_normalized_and_missing_albums_match():
    ts = [tr("C:\\x\\a.mp3", 1, "FAIRY"), tr("C:\\y\\b.mp3", 2, "Fairy ")]
    assert not dedupe.find(ts)[0].cross_album
    ts = [tr("C:\\x\\a.mp3", 1, ""), tr("C:\\y\\b.mp3", 2, "")]
    assert not dedupe.find(ts)[0].cross_album


def test_identical_files_are_one_album_even_with_odd_tags(tmp_path):
    a = put(tmp_path, "song-128.mp3", "a/x.mp3", title="S", artist="A", album="One")
    os.makedirs(tmp_path / "b")
    shutil.copyfile(a, tmp_path / "b" / "x.mp3")
    put(tmp_path, "song-320.mp3", "c/x.mp3", title="S", artist="A", album="Two")
    g = dedupe.find(scan_mod.scan(str(tmp_path)).tracks)[0]
    assert g.albums == 2 and len(g.removable()) == 1  # one of the identical pair goes; album Two stays


def test_sound_match_across_albums_keeps_both(tmp_path):
    exe = dedupe.find_fpcalc()
    if not exe:
        import pytest
        pytest.skip("fpcalc not available")
    put(tmp_path, "song-128.mp3", "a.mp3", title="Track 01", artist="X", album="Original")
    put(tmp_path, "song.flac", "b.flac", title="Other Title", artist="Y", album="Best Of")
    g = dedupe.find(scan_mod.scan(str(tmp_path)).tracks, fingerprint=True, fpcalc=exe)[0]
    assert g.stage == 3 and g.cross_album and g.removable() == []


def test_cli_dedupe_keeps_best_of_tracks(tmp_path, capsys):
    root = tmp_path / "m"
    put(root, "song-128.mp3", "orig/01.mp3", title="S", artist="A", album="Fairy", tracknumber="1")
    put(root, "song.flac", "orig-flac/01.flac", title="S", artist="A", album="Fairy", tracknumber="1")
    put(root, "song-320.mp3", "best/09.mp3", title="S", artist="A", album="Best", tracknumber="9")
    assert main.main([str(root), "--dedupe", "--lang", "en"]) == 0
    dupes = [f for _, _, fs in os.walk(root / "_Duplicates") for f in fs]
    assert dupes == ["01.mp3"]  # only the lossy copy of the same album
    assert (root / "A" / "Best" / "09 - S.mp3").exists()


def test_cli_dedupe_across_albums(tmp_path):
    root = tmp_path / "m"
    put(root, "song.flac", "orig/01.flac", title="S", artist="A", album="Fairy", tracknumber="1")
    put(root, "song-320.mp3", "best/09.mp3", title="S", artist="A", album="Best", tracknumber="9")
    assert main.main([str(root), "--dedupe-across-albums", "--lang", "en"]) == 0
    dupes = [f for _, _, fs in os.walk(root / "_Duplicates") for f in fs]
    assert dupes == ["09.mp3"]


# ------------------------------------------------------------------ rerun stability
def test_existing_folder_keeps_the_artist_name():
    ts = [Track("C:\\a.mp3", "MP3", 1, tags=TagSet(artist="岡田 有希子")),
          Track("C:\\b.mp3", "MP3", 1, tags=TagSet(artist="岡田有希子")),
          Track("C:\\c.mp3", "MP3", 1, tags=TagSet(artist="岡田有希子"))]
    assert ArtistIndex(ts).rep("岡田 有希子") == "岡田有希子"  # most files
    from artists import folder_key
    assert ArtistIndex(ts, existing={folder_key("岡田 有希子")}).rep("岡田有希子") == "岡田 有希子"


def test_rerun_after_duplicates_left_moves_nothing(tmp_path):
    # before the run 岡田有希子 is on more files; once its duplicates leave, the counts
    # tie, and the folder made by the first run must keep its name
    root = tmp_path / "m"
    put(root, "song-320.mp3", "dl/a.mp3", title="Alpha", artist="岡田 有希子", album="Fairy", tracknumber="1")
    put(root, "song-128.mp3", "dl/a-low.mp3", title="Alpha", artist="岡田有希子", album="Fairy", tracknumber="1")
    put(root, "other.mp3", "dl/sub/b.mp3", title="Beta", artist="岡田有希子", album="Fairy", tracknumber="2")
    shutil.copyfile(root / "dl" / "sub" / "b.mp3", root / "copy.mp3")
    res, p = build(root, groups="find")
    mover.execute(p)
    res, p2 = build(root, groups="find")
    assert p2.summary()["move"] == 0, [(i.src, i.dst) for i in p2.items if i.moves]
