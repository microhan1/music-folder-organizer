"""Tag reading for every supported format, plus broken and odd files."""
import os
import shutil

import mutagen
import pytest
from mutagen.id3 import ID3, TCMP, TCON, TIT2, TPE1, TPE2, TPOS, TRCK, TSOP, TXXX

import scan
from conftest import build, put, sample


def test_mp3_id3v24_fields(tmp_path):
    p = put(tmp_path, "song-128.mp3", "a.mp3")
    tags = ID3()
    tags.add(TIT2(encoding=3, text="Title"))
    tags.add(TPE1(encoding=3, text=["Main", "Second"]))  # multi-value: the first one names the folder
    tags.add(TPE2(encoding=3, text="Album Artist"))
    tags.add(TRCK(encoding=3, text="3/12"))
    tags.add(TPOS(encoding=3, text="2/2"))
    tags.add(TCON(encoding=3, text="(17)"))  # numeric ID3v1 genre reference
    tags.add(TSOP(encoding=3, text="Main, The"))
    tags.add(TCMP(encoding=3, text="1"))
    tags.add(TXXX(encoding=3, desc="MusicBrainz Artist Id", text="ABC-123/def-456"))
    tags.save(p)
    t = scan.read_track(p)
    assert not t.error
    assert (t.tags.title, t.tags.artist, t.tags.album_artist) == ("Title", "Main", "Album Artist")
    assert (t.tags.track, t.tags.disc, t.tags.genre) == ("3/12", "2/2", "Rock")
    assert (t.tags.artist_sort, t.tags.mb_artist_id, t.tags.compilation) == ("Main, The", "abc-123", True)
    assert t.fmt == "MP3" and t.bitrate == 128 and not t.lossless and 15 < t.length < 17


def test_mp3_id3v1_only(tmp_path):
    p = put(tmp_path, "song-128.mp3", "v1.mp3")
    v1 = b"TAG" + b"Old Title".ljust(30, b"\0") + b"Old Artist".ljust(30, b"\0") + b"Old Album".ljust(30, b"\0") \
        + b"1999" + b"".ljust(28, b"\0") + b"\0\x05" + bytes([17])
    with open(p, "ab") as f:
        f.write(v1)
    t = scan.read_track(p)
    assert (t.tags.title, t.tags.artist, t.tags.album, t.tags.year) == ("Old Title", "Old Artist", "Old Album", "1999")


def test_flac_ogg_opus_vorbis_comments(tmp_path):
    for name, rel in (("song.flac", "a.flac"), ("tone.ogg", "b.ogg"), ("tone.opus.ogg", "c.ogg")):
        p = put(tmp_path, name, rel, title="T", artist="A", albumartist="AA", album="B", tracknumber="7",
                discnumber="1", date="2001-05-06", genre="Pop")
        audio = mutagen.File(p)
        audio["artistsort"] = "A, The"
        audio["musicbrainz_artistid"] = "mb-1"
        audio["compilation"] = "1"
        audio.save()
        t = scan.read_track(p)
        assert not t.error, (name, t.error)
        assert (t.tags.title, t.tags.artist, t.tags.album_artist, t.tags.album) == ("T", "A", "AA", "B")
        assert (t.tags.track, t.tags.disc, t.tags.year, t.tags.genre) == ("7", "1", "2001-05-06", "Pop")
        assert (t.tags.artist_sort, t.tags.mb_artist_id, t.tags.compilation) == ("A, The", "mb-1", True)
    assert scan.read_track(str(tmp_path / "a.flac")).lossless
    assert not scan.read_track(str(tmp_path / "b.ogg")).lossless


def test_wav_with_id3(tmp_path):
    p = put(tmp_path, "tone.wav", "w.wav")
    from mutagen.wave import WAVE

    w = WAVE(p)
    w.add_tags()
    w.tags.add(TIT2(encoding=3, text="Wave Title"))
    w.tags.add(TPE1(encoding=3, text="Wave Artist"))
    w.save()
    t = scan.read_track(p)
    assert (t.tags.title, t.tags.artist, t.fmt, t.lossless) == ("Wave Title", "Wave Artist", "WAV", True)
    assert t.bitrate > 0


def test_wav_without_tags_is_untagged(tmp_path):
    t = scan.read_track(put(tmp_path, "tone.wav", "w.wav"))
    assert not t.error and t.is_untagged()


def test_m4a_atoms(tmp_path):
    from mutagen.mp4 import MP4, MP4FreeForm

    p = put(tmp_path, "silent.m4a", "a.m4a")
    m = MP4(p)
    m.add_tags()
    m["\xa9nam"] = ["M4A Title"]
    m["\xa9ART"] = ["M4A Artist"]
    m["aART"] = ["M4A Album Artist"]
    m["\xa9alb"] = ["Album"]
    m["trkn"] = [(4, 10)]
    m["disk"] = [(1, 2)]
    m["\xa9day"] = ["2010"]
    m["soar"] = ["Artist, M4A"]
    m["cpil"] = True
    m["----:com.apple.iTunes:MusicBrainz Artist Id"] = [MP4FreeForm(b"mb-m4a")]
    m.save()
    t = scan.read_track(p)
    assert not t.error
    assert (t.tags.title, t.tags.artist, t.tags.album_artist) == ("M4A Title", "M4A Artist", "M4A Album Artist")
    assert (t.tags.track, t.tags.disc, t.tags.year, t.tags.artist_sort) == ("4/10", "1/2", "2010", "Artist, M4A")
    assert (t.tags.mb_artist_id, t.tags.compilation) == ("mb-m4a", True)


def test_m4a_itunes_artist_id(tmp_path):
    from mutagen.mp4 import MP4

    p = put(tmp_path, "silent.m4a", "a.m4a")
    m = MP4(p)
    m.add_tags()
    m["atID"] = [123456]
    m.save()
    t = scan.read_track(p)
    assert (t.tags.itunes_artist_id, t.tags.mb_artist_id) == ("123456", "")


def test_itunes_artist_id_as_music_tag_filler_writes_it(tmp_path):
    """The three spellings music-tag-filler uses (tests/test_ids.py there)."""
    from mutagen.mp4 import MP4, MP4FreeForm

    p = put(tmp_path, "song-128.mp3", "a.mp3", title="T", artist="A")
    tags = ID3(p)
    tags.add(TXXX(encoding=3, desc="iTunes Artist Id", text="275749278"))
    tags.save(p)
    assert scan.read_track(p).tags.itunes_artist_id == "275749278"
    p = put(tmp_path, "song.flac", "b.flac", title="T", artist="A")
    audio = mutagen.File(p)
    audio["ITUNES_ARTISTID"] = "275749278"
    audio.save()
    assert scan.read_track(p).tags.itunes_artist_id == "275749278"
    p = put(tmp_path, "silent.m4a", "c.m4a")
    m = MP4(p)
    m.add_tags()
    m["----:com.apple.iTunes:iTunes Artist Id"] = [MP4FreeForm(b"275749278")]
    m.save()
    assert scan.read_track(p).tags.itunes_artist_id == "275749278"


def test_uppercase_extension_and_mixed_case(tmp_path):
    put(tmp_path, "song-128.mp3", "A.MP3", title="T", artist="A")
    put(tmp_path, "song.flac", "B.Flac", title="U", artist="A")
    res = scan.scan(str(tmp_path))
    assert sorted(t.fmt for t in res.tracks) == ["FLAC", "MP3"]


def test_broken_and_empty_files_are_listed_not_fatal(tmp_path):
    root = tmp_path / "m"
    root.mkdir()
    (root / "garbage.mp3").write_bytes(b"this is not audio" * 100)
    (root / "empty.flac").write_bytes(b"")
    (root / "fake.m4a").write_bytes(b"\0" * 64)
    put(root, "song-128.mp3", "ok.mp3", title="T", artist="A", album="B", tracknumber="1")
    res, p = build(root)
    broken = [t for t in res.tracks if t.error]
    assert len(broken) == 3
    for item in p.items:
        if item.track.error:
            assert item.status == "untagged" and not item.checked
    assert p.summary()["untagged"] == 3 and p.summary()["move"] == 1


def test_odd_tag_text_survives(tmp_path):
    p = put(tmp_path, "song-128.mp3", "x.mp3", title="  Line\nbreak\ttab  ", artist="🎵 Emoji 가수 ", album="רוק")
    t = scan.read_track(p)
    import pattern

    segs = pattern.render("{artist}/{album}/{title}", t, {"artist": "?", "album": "?"})
    assert segs == ["🎵 Emoji 가수", "רוק", "Line break tab"]


def test_non_audio_is_ignored(tmp_path):
    root = tmp_path / "m"
    root.mkdir()
    for name in ("a.cue", "b.log", "c.txt", "d.iso", "e.m3u", "f.jpg"):
        (root / name).write_bytes(b"x")
    assert scan.scan(str(root)).tracks == []


def test_exclude_skips_subtree(tmp_path):
    put(tmp_path, "song-128.mp3", "keep/a.mp3")
    put(tmp_path, "song-128.mp3", "_Duplicates/deep/b.mp3")
    res = scan.scan(str(tmp_path), [str(tmp_path / "_Duplicates")])
    assert [t.name for t in res.tracks] == ["a.mp3"]
    assert scan.key_of(str(tmp_path / "_Duplicates")) not in res.dirs


def test_scan_cancel(tmp_path):
    import threading

    for i in range(30):
        put(tmp_path, "tone.ogg", f"d/{i}.ogg")
    ev = threading.Event()
    res = scan.scan(str(tmp_path), progress=lambda d, n: ev.set(), cancel=ev)
    assert res.cancelled and len(res.tracks) < 30


@pytest.mark.skipif(os.name != "nt", reason="NTFS keeps NFC and NFD names apart")
def test_nfc_and_nfd_twins_stay_two_files(tmp_path):
    import unicodedata

    nfc = unicodedata.normalize("NFC", "가요.mp3")
    nfd = unicodedata.normalize("NFD", "가요.mp3")
    put(tmp_path, "song-128.mp3", nfc, title="가요", artist="가수", album="앨범", tracknumber="1")
    put(tmp_path, "other.mp3", nfd, title=unicodedata.normalize("NFD", "가요"), artist="가수", album="앨범", tracknumber="1")
    if len(os.listdir(tmp_path)) != 2:
        pytest.skip("this file system merges NFC and NFD names")
    res, p = build(tmp_path)
    assert len({i.key for i in p.items}) == 2
    names = sorted(os.path.basename(i.dst) for i in p.items)
    assert names == ["01 - 가요 (2).mp3", "01 - 가요.mp3"]  # NFC names, never two that look identical
    assert all(n == unicodedata.normalize("NFC", n) for n in names)
