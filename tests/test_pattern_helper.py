"""v0.3.0 item 5: "{disc}" only on albums with two or more discs, and the new preset."""
import os

import plan as plan_mod
import prefs
from conftest import build, put
from scan import TagSet, Track

DISC = "{album_artist|artist}/{album}/{disc}-{track:02} - {title}"


def names(p):
    return sorted(os.path.relpath(i.dst, p.options.dest).replace(os.sep, "/") for i in p.items)


def test_year_preset_puts_the_year_in_the_album_folder(tmp_path):
    put(tmp_path, "song-128.mp3", "in/a.mp3", title="A", artist="X", album="Fairy", tracknumber="1", date="1984-11-28")
    put(tmp_path, "other.mp3", "in/b.mp3", title="B", artist="X", album="Nameless", tracknumber="2")  # no year
    assert prefs.YEAR_PATTERN in prefs.PRESETS and prefs.PRESETS[0] == prefs.DEFAULT_PATTERN
    _, p = build(tmp_path, pattern=prefs.YEAR_PATTERN)
    assert names(p) == ["X/[1984] Fairy/01 - A.mp3", "X/[Unknown Year] Nameless/02 - B.mp3"]  # one disc: no disc part
    from mover import execute
    execute(p)
    assert build(tmp_path, pattern=prefs.YEAR_PATTERN)[1].summary()["move"] == 0  # a second run leaves them be


def test_disc_preset_is_offered():
    assert DISC in prefs.PRESETS and prefs.PRESETS[0] == prefs.DEFAULT_PATTERN  # the default stays


def test_two_disc_album_shows_the_disc(tmp_path):
    put(tmp_path, "song-128.mp3", "in1/a.mp3", title="A", artist="X", album="Box", tracknumber="1", discnumber="1/2")
    put(tmp_path, "other.mp3", "in1/b.mp3", title="B", artist="X", album="Box", tracknumber="1", discnumber="2/2")
    _, p = build(tmp_path, pattern=DISC)
    assert names(p) == ["X/Box/1-01 - A.mp3", "X/Box/2-01 - B.mp3"]


def test_disc_numbers_without_a_total_still_count(tmp_path):
    put(tmp_path, "song-128.mp3", "in1/a.mp3", title="A", artist="X", album="Box", tracknumber="1", discnumber="1")
    put(tmp_path, "other.mp3", "in1/b.mp3", title="B", artist="X", album="Box", tracknumber="1", discnumber="2")
    _, p = build(tmp_path, pattern=DISC)
    assert names(p) == ["X/Box/1-01 - A.mp3", "X/Box/2-01 - B.mp3"]


def test_one_disc_album_hides_the_disc(tmp_path):
    put(tmp_path, "song-128.mp3", "in1/a.mp3", title="A", artist="X", album="Single", tracknumber="1", discnumber="1/1")
    put(tmp_path, "other.mp3", "in1/b.mp3", title="B", artist="X", album="Single", tracknumber="2", discnumber="1")
    put(tmp_path, "tone.ogg", "in2/c.ogg", title="C", artist="Y", album="Nodisc", tracknumber="3")
    _, p = build(tmp_path, pattern=DISC)
    assert names(p) == ["X/Single/01 - A.mp3", "X/Single/02 - B.mp3", "Y/Nodisc/03 - C.ogg"]


def test_disc_total_on_one_file_is_enough(tmp_path):
    # disc 1 of 2 present, disc 2 not in this folder: still a two-disc album
    put(tmp_path, "song-128.mp3", "in1/a.mp3", title="A", artist="X", album="Box", tracknumber="1", discnumber="1/2")
    _, p = build(tmp_path, pattern=DISC)
    assert names(p) == ["X/Box/1-01 - A.mp3"]


def test_same_album_name_by_other_artists_is_another_album():
    ts = [Track("C:\\a.mp3", "MP3", 1, tags=TagSet(artist="X", album="Greatest Hits", disc="1")),
          Track("C:\\b.mp3", "MP3", 1, tags=TagSet(artist="Y", album="Greatest Hits", disc="2"))]
    assert plan_mod.multi_disc_albums(ts) == set()


def test_year_preset_gathers_a_box_set_and_numbers_its_discs(tmp_path):
    for d in (1, 2):
        put(tmp_path, "song-128.mp3" if d == 1 else "other.mp3", f"in/{d}.mp3", title=f"T{d}", artist="X",
            album=f"Box [Disc {d}]", tracknumber="1", date="2002")
    _, p = build(tmp_path, pattern=prefs.YEAR_PATTERN)
    assert names(p) == ["X/[2002] Box/1-01 - T1.mp3", "X/[2002] Box/2-01 - T2.mp3"]


def test_stand_in_year_inside_a_folder_name_survives_a_language_switch(tmp_path):
    """"[Unknown Year] Nameless" holds the stand-in name inside the folder name: after switching the
    language the next run must keep it, not rename the folder to "[연도 미상] Nameless" (LESSONS A38)."""
    import i18n
    from mover import execute

    put(tmp_path, "song-128.mp3", "in/a.mp3", title="A", artist="X", album="Nameless", tracknumber="1")
    execute(build(tmp_path, pattern=prefs.YEAR_PATTERN)[1])
    assert os.path.isdir(tmp_path / "X" / "[Unknown Year] Nameless")
    i18n.set_lang("ko", persist=False)
    try:
        again = build(tmp_path, pattern=prefs.YEAR_PATTERN)[1]
        assert again.summary()["move"] == 0, names(again)
    finally:
        i18n.set_lang("en", persist=False)
