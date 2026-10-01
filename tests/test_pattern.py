import os

import pattern
from scan import TagSet, Track

FALLBACKS = {"artist": "Unknown Artist", "album": "Unknown Album", "year": "Unknown Year", "genre": "Unknown Genre"}


def track(name="x.mp3", **tags) -> Track:
    return Track(os.path.join("C:\\music", name), "MP3", 1, tags=TagSet(**tags))


def test_default_pattern():
    t = track(title="Song", artist="A", album="Alb", track="3/12")
    assert pattern.render(pattern.DEFAULT_PATTERN, t, FALLBACKS) == ["A", "Alb", "03 - Song"]


def test_album_artist_wins_over_artist():
    t = track(title="Song", artist="A feat. B", album_artist="A", album="Alb", track="1")
    assert pattern.render(pattern.DEFAULT_PATTERN, t, FALLBACKS)[0] == "A"


def test_fallbacks_and_missing_track():
    t = track(name="05.Hello.mp3", title="Hello", artist="A")
    assert pattern.render(pattern.DEFAULT_PATTERN, t, FALLBACKS) == ["A", "Unknown Album", "05 - Hello"]
    t = track(name="Hello.mp3", title="Hello", artist="A", album="B")
    assert pattern.render(pattern.DEFAULT_PATTERN, t, FALLBACKS)[-1] == "Hello"  # no dangling " - "


def test_year_and_title_fallback():
    t = track(name="orig name.mp3", artist="A", year="1985-03-21")
    assert pattern.render("{year}/{artist} - {title}", t, FALLBACKS) == ["1985", "A - orig name"]
    t = track(name="x.mp3", artist="A", title="T")
    assert pattern.render("{year}/{artist} - {title}", t, FALLBACKS)[0] == "Unknown Year"


def test_artist_map_applies():
    t = track(title="T", artist="岡田 有希子")
    segs = pattern.render("{artist}/{title}", t, FALLBACKS, lambda s: "岡田有希子")
    assert segs == ["岡田有希子", "T"]


def test_sanitize():
    assert pattern.sanitize('AC/DC: "Live"?') == "AC_DC_ _Live__"
    assert pattern.sanitize("  a   b  ") == "a b"
    assert pattern.sanitize("Vol.") == "Vol"
    assert pattern.sanitize("CON") == "CON_"
    assert pattern.sanitize("nul.txt") == "nul.txt_"
    assert pattern.sanitize("...") == "_"
    assert pattern.sanitize("가\u1100\u1161") == "가가"  # NFD Hangul from macOS becomes NFC


def test_fit_shortens_name_then_folders():
    root = "C:\\" + "r" * 40
    dirs, stem, cut = pattern.fit(root, ["a" * 100, "b" * 80], "c" * 100, ".mp3")
    assert cut
    assert len(os.path.join(root, *dirs, stem + ".mp3")) <= pattern.PATH_LIMIT
    assert len(stem) >= pattern.MIN_SEGMENT
    dirs, stem, cut = pattern.fit("C:\\m", ["a"], "b", ".mp3")
    assert not cut and stem == "b"


def test_validate():
    assert pattern.validate(pattern.DEFAULT_PATTERN) == ""
    assert pattern.validate("{artist}/{title}") == ""
    assert pattern.validate("") == "err_pattern_empty"
    assert pattern.validate("{artist/{title}") == "err_pattern_braces"
    assert pattern.validate("{singer}/{title}") == "err_pattern_unknown"
    assert pattern.validate("{artist}/music") == "err_pattern_no_name"
