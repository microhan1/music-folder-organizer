"""v0.3.0 item 5: "{disc}" only on albums with two or more discs, and the new preset."""
import os

import plan as plan_mod
import prefs
from conftest import build, put
from scan import TagSet, Track

DISC = "{album_artist|artist}/{album}/{disc}-{track:02} - {title}"


def names(p):
    return sorted(os.path.relpath(i.dst, p.options.dest).replace(os.sep, "/") for i in p.items)


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
