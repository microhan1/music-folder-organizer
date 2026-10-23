"""v0.5.0 item 1: a disc mark at the end of an album name ("Box [Disc 2]") is the disc number, but only
when the folder pattern uses "{disc}"; the default pattern and the files' tags stay as they are."""
import os

import mover
import undo
from conftest import build, put, snapshot
from scan import split_disc_mark

DISC = "{album_artist|artist}/{album}/{disc}-{track:02} - {title}"


def names(p):
    return sorted(os.path.relpath(i.dst, p.options.dest).replace(os.sep, "/") for i in p.items)


# ------------------------------------------------------------------ the parsing
def test_marks_that_are_taken():
    for album in ("Box [Disc 2]", "Box (Disc 2)", "Box [disc2]", "Box [CD 2]", "Box (CD2)", "Box - Disc 2",
                  "Box – CD 2", "Box Disc 2", "Box CD2", "Box disk 2", "Box [Disc 02]", "Box（Disc 2）", "Box [디스크 2]",
                  "Box [ディスク2]", "Box [Disc-2]", "Box   [Disc 2]  "):
        assert split_disc_mark(album) == ("Box", "2"), album


def test_what_is_not_a_mark_is_left_alone():
    for album in ("Disc 2", "[Disc 2]", "Discovery", "Boxed Discs", "Box [Disc 2] Remastered", "Cd Player", "Box 2",
                  "Box (Deluxe)", "Disc One", "Box [Disc]", "Box [Disc 0]", "", "Album (2003 Strings Version)"):
        assert split_disc_mark(album) == (album, ""), album


def test_the_disc_tag_decides_when_there_is_one():
    assert split_disc_mark("Box [Disc 2]", "2") == ("Box", "2")        # the same: the mark just goes
    assert split_disc_mark("Box [Disc 2]", "2/5") == ("Box", "2/5")    # a total is kept
    assert split_disc_mark("Box [Disc 2]", "1") == ("Box [Disc 2]", "1")      # disagreeing: nothing is guessed
    assert split_disc_mark("Box [Disc 2]", "x") == ("Box [Disc 2]", "x")
    assert split_disc_mark("Box [Disc 2]", "0") == ("Box [Disc 2]", "0")


# ------------------------------------------------------------------ the plan
def box(tmp_path, n=3, tagged_disc=False):
    for d in range(1, n + 1):
        for t in (1, 2):
            put(tmp_path, "song-128.mp3" if t == 1 else "other.mp3", f"in/Disc {d}/{t}.mp3", title=f"T{d}{t}", artist="X",
                album=f"Box [Disc {d}]", tracknumber=str(t), **({"discnumber": f"{d}/{n}"} if tagged_disc else {}))


def test_with_the_disc_pattern_the_discs_become_one_album(tmp_path):
    box(tmp_path)
    _, p = build(tmp_path, pattern=DISC)
    assert names(p) == ["X/Box/1-01 - T11.mp3", "X/Box/1-02 - T12.mp3", "X/Box/2-01 - T21.mp3", "X/Box/2-02 - T22.mp3",
                        "X/Box/3-01 - T31.mp3", "X/Box/3-02 - T32.mp3"]
    assert p.disc_albums == 0  # no hint needed: the pattern has {disc}


def test_with_the_disc_tag_present_the_same(tmp_path):
    box(tmp_path, tagged_disc=True)
    _, p = build(tmp_path, pattern=DISC)
    assert names(p)[0] == "X/Box/1-01 - T11.mp3" and len({n.split("/")[1] for n in names(p)}) == 1


def test_default_pattern_is_unchanged_and_hints(tmp_path):
    box(tmp_path)
    _, p = build(tmp_path)  # the default pattern: no {disc}
    assert sorted({n.split("/")[1] for n in names(p)}) == ["Box [Disc 1]", "Box [Disc 2]", "Box [Disc 3]"]
    assert p.disc_albums == 1  # one multi-disc album: offer the disc pattern


def test_a_single_disc_keeps_a_plain_folder_and_no_disc_number(tmp_path):
    put(tmp_path, "song-128.mp3", "in/a.mp3", title="A", artist="X", album="Solo [Disc 1]", tracknumber="1")
    _, p = build(tmp_path, pattern=DISC)
    assert names(p) == ["X/Solo/01 - A.mp3"]  # one disc: "{disc}" and its dash disappear


def test_a_disagreeing_tag_keeps_the_album_name(tmp_path):
    put(tmp_path, "song-128.mp3", "in/a.mp3", title="A", artist="X", album="Box [Disc 2]", tracknumber="1", discnumber="1")
    _, p = build(tmp_path, pattern=DISC)
    assert names(p) == ["X/Box [Disc 2]/01 - A.mp3"]


def test_the_files_tags_are_never_written(tmp_path):
    import mutagen

    box(tmp_path)
    res, p = build(tmp_path, pattern=DISC)
    mover.execute(p)
    after = {}
    for h, _, ns in os.walk(tmp_path):
        for n in ns:
            if n.endswith(".mp3"):
                tags = mutagen.File(os.path.join(h, n), easy=True)
                after[n] = tags["album"][0]
    assert sorted(after.values()) == sorted([f"Box [Disc {d}]" for d in (1, 2, 3) for _ in (1, 2)])


def test_run_again_finds_nothing_and_undo_restores(tmp_path):
    box(tmp_path)
    before = snapshot(tmp_path)
    res = mover.execute(build(tmp_path, pattern=DISC)[1])
    assert not res.failed and res.done == 6
    again = build(tmp_path, pattern=DISC)[1]
    assert again.summary()["move"] == 0  # the new names are where the pattern puts them
    u = undo.undo(res.log_path)
    assert u.restored == res.done and not u.skipped
    os.remove(res.log_path)
    assert snapshot(tmp_path) == before


def test_two_albums_with_one_name_stay_apart_by_artist(tmp_path):
    for artist in ("X", "Y"):
        for d in (1, 2):
            put(tmp_path, "song-128.mp3", f"in/{artist}{d}.mp3", title=f"T{d}", artist=artist, album=f"Hits CD{d}", tracknumber="1")
    _, p = build(tmp_path, pattern=DISC)
    assert names(p) == ["X/Hits/1-01 - T1.mp3", "X/Hits/2-01 - T2.mp3", "Y/Hits/1-01 - T1.mp3", "Y/Hits/2-01 - T2.mp3"]
