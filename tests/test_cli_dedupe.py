"""Command line behaviour and duplicate-finder corner cases."""
import json
import os
import shutil
from array import array

import pytest

import dedupe
import i18n
import main
from conftest import put, snapshot
from scan import TagSet, Track, key_of


# ------------------------------------------------------------------ CLI
@pytest.fixture
def lib(tmp_path):
    root = tmp_path / "music"
    put(root, "song-320.mp3", "x/a.mp3", title="Alpha", artist="Yukiko Okada", album="Fairy", tracknumber="1")
    put(root, "other.mp3", "y/b.mp3", title="Beta", artist="오카다 유키코", album="Fairy", tracknumber="2")
    put(root, "tone.wav", "z/untagged.wav")
    return root


def run(argv, capsys):
    rc = main.main([*map(str, argv), "--lang", "en"])
    out = capsys.readouterr()
    return rc, out.out, out.err


def test_missing_folder(capsys, tmp_path):
    rc, _, err = run([tmp_path / "nope"], capsys)
    assert rc == 2 and "Not a folder" in err


def test_copy_needs_another_destination(lib, capsys):
    rc, _, err = run([lib, "--copy"], capsys)
    assert rc == 2 and "different destination" in err


def test_bad_pattern(lib, capsys):
    rc, _, err = run([lib, "--pattern", "{singer}/{title}", "--dry-run"], capsys)
    assert rc == 2 and "Unknown placeholder" in err


def test_undo_without_log(lib, capsys):
    rc, out, _ = run([lib, "--undo"], capsys)
    assert rc == 1 and "Nothing to undo" in out


@pytest.mark.parametrize("lang,needle", [("ko", "음악 파일"), ("en", "music files"), ("zh-CN", "音乐文件"), ("ja", "音楽ファイル")])
def test_help_in_every_language(lang, needle, capsys):
    with pytest.raises(SystemExit) as exc:
        main.main(["--lang", lang, "--help"])
    assert exc.value.code == 0
    assert needle in capsys.readouterr().out


def test_pattern_option_and_untagged_txt(lib, capsys):
    rc, out, _ = run([lib, "--pattern", "{artist} - {title}"], capsys)
    assert rc == 0
    assert (lib / "Yukiko Okada - Alpha.mp3").exists()
    lines = (lib / "untagged.txt").read_text(encoding="utf-8-sig").splitlines()
    assert lines == [str(lib / "z" / "untagged.wav")]


def test_artists_file_option(lib, tmp_path, capsys):
    aliases = tmp_path / "my-artists.json"
    aliases.write_text(json.dumps({"岡田有希子": ["Yukiko Okada", "오카다 유키코"]}, ensure_ascii=False), encoding="utf-8")
    rc, out, _ = run([lib, "--artists", aliases, "--dry-run"], capsys)
    assert rc == 0 and out.count("岡田有希子") >= 2


def test_broken_artists_file_is_reported_not_fatal(lib, tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("{oops", encoding="utf-8")
    rc, _, err = run([lib, "--artists", bad, "--dry-run"], capsys)
    assert rc == 0 and "artists.json" in err


def test_guess_is_printed_not_applied(lib, capsys):
    rc, out, _ = run([lib, "--dry-run"], capsys)
    assert "same artist?" not in out  # different folders: no guess
    shutil.move(str(lib / "y" / "b.mp3"), str(lib / "x" / "b.mp3"))  # one album folder
    rc, out, _ = run([lib, "--dry-run"], capsys)
    assert "same artist?" in out
    assert "Yukiko Okada\\Fairy" in out and "오카다 유키코\\Fairy" in out


def test_include_untagged_and_keep_empty(lib, capsys):
    rc, _, _ = run([lib, "--include-untagged", "--keep-empty"], capsys)
    assert rc == 0
    assert (lib / "Unknown Artist" / "Unknown Album" / "untagged.wav").exists()
    assert (lib / "x").is_dir() and (lib / "z").is_dir()  # emptied but kept


def test_dest_and_copy(lib, tmp_path, capsys):
    before = snapshot(lib)
    out_dir = tmp_path / "sorted"
    rc, _, _ = run([lib, "--dest", out_dir, "--copy"], capsys)
    assert rc == 0 and snapshot(lib) == before
    assert (out_dir / "Yukiko Okada" / "Fairy" / "01 - Alpha.mp3").exists()
    rc, _, _ = run([out_dir, "--undo"], capsys)
    assert rc == 0 and sorted(os.listdir(out_dir)) == ["organize_log.json", "untagged.txt"]  # outputs, not moved files


def test_undo_by_source_folder_uses_last_log(lib, tmp_path, capsys):
    out_dir = tmp_path / "sorted"
    run([lib, "--dest", out_dir], capsys)
    rc, out, _ = run([lib, "--undo"], capsys)  # log lives in the destination; settings remember it
    assert rc == 0 and (lib / "x" / "a.mp3").exists()


def test_dedupe_and_trash_flags(lib, capsys, monkeypatch):
    import mover

    binned = []
    monkeypatch.setattr(mover, "trash", lambda p: (binned.append(p), os.remove(p)))
    shutil.copyfile(lib / "x" / "a.mp3", lib / "copy.mp3")
    rc, out, _ = run([lib, "--dedupe", "--trash"], capsys)
    assert rc == 0 and len(binned) == 1
    assert "recycle bin" in out


def test_dry_run_with_fingerprint(lib, capsys):
    if not dedupe.find_fpcalc():
        pytest.skip("fpcalc not available")
    before = snapshot(lib)
    rc, out, _ = run([lib, "--dedupe", "--fingerprint", "--dry-run"], capsys)
    assert rc == 0 and "Fingerprinting" in out and snapshot(lib) == before


# ------------------------------------------------------------------ dedupe
def tr(path, size, length, fmt="MP3", bitrate=128, lossless=False, **tags):
    return Track(path, fmt, size, length=length, bitrate=bitrate, lossless=lossless, tags=TagSet(**tags))


def test_same_title_other_artist_is_not_a_duplicate():
    ts = [tr("C:\\a.mp3", 1, 200, title="Intro", artist="A"), tr("C:\\b.mp3", 2, 200, title="Intro", artist="B")]
    assert dedupe.find(ts) == []


def test_length_tolerance_boundary():
    ts = [tr("C:\\a.mp3", 1, 200.0, title="S", artist="A"), tr("C:\\b.mp3", 2, 202.0, title="S", artist="A"),
          tr("C:\\c.mp3", 3, 204.5, title="S", artist="A")]
    groups = dedupe.find(ts)
    assert len(groups) == 1 and sorted(t.path for t in groups[0].members) == ["C:\\a.mp3", "C:\\b.mp3"]


def test_title_normalization_and_album_artist_fallback():
    ts = [tr("C:\\a.mp3", 1, 200, title="Ｈｅｌｌｏ， World!", album_artist="A"),
          tr("C:\\b.mp3", 2, 200, title="hello world", artist="A")]
    assert len(dedupe.find(ts)) == 1


def test_artist_map_joins_spellings():
    ts = [tr("C:\\a.mp3", 1, 200, title="S", artist="岡田 有希子"), tr("C:\\b.mp3", 2, 200, title="S", artist="Yukiko Okada")]
    assert dedupe.find(ts) == []
    assert len(dedupe.find(ts, lambda n: "岡田有希子")) == 1


def test_keep_ranking():
    ts = [tr("C:\\long\\path\\a.mp3", 1, 200, bitrate=320, title="S", artist="A"),
          tr("C:\\b.flac", 2, 200, fmt="FLAC", bitrate=900, lossless=True, title="S", artist="A"),
          tr("C:\\c.mp3", 3, 200, bitrate=128, title="S", artist="A")]
    g = dedupe.find(ts)[0]
    assert g.members[0].fmt == "FLAC" and g.keep == {key_of("C:\\b.flac")}
    same = [tr("C:\\deep\\x\\a.mp3", 1, 200, title="S", artist="A"), tr("C:\\b.mp3", 2, 200, title="S", artist="A")]
    assert dedupe.find(same)[0].members[0].path == "C:\\b.mp3"  # shorter path
    pref = {key_of("C:\\deep\\x\\a.mp3")}
    assert dedupe.find(same, preferred=pref)[0].members[0].path == "C:\\deep\\x\\a.mp3"  # already in place wins


def test_untitled_files_are_never_grouped_by_tags():
    ts = [tr("C:\\a.mp3", 1, 200), tr("C:\\b.mp3", 2, 200)]
    assert dedupe.find(ts) == []


def test_file_vanishing_during_hashing(tmp_path):
    a = put(tmp_path, "song-128.mp3", "a.mp3")
    shutil.copyfile(a, tmp_path / "b.mp3")
    import scan

    tracks = scan.scan(str(tmp_path)).tracks
    os.remove(a)
    assert dedupe.find(tracks) == []


def test_identical_and_same_song_merge_into_one_group(tmp_path):
    a = put(tmp_path, "song-128.mp3", "a.mp3", title="S", artist="A")
    shutil.copyfile(a, tmp_path / "a2.mp3")
    put(tmp_path, "song.flac", "b.flac", title="S", artist="A")
    import scan

    groups = dedupe.find(scan.scan(str(tmp_path)).tracks)
    assert len(groups) == 1 and len(groups[0].members) == 3 and groups[0].stage == 2
    assert groups[0].members[0].fmt == "FLAC"


def test_bit_error_rate_alignment():
    import random

    rnd = random.Random(1)
    fp = [rnd.getrandbits(32) for _ in range(300)]
    pack = lambda xs: array("I", xs).tobytes()
    assert dedupe.bit_error_rate(pack(fp), pack(fp)) == 0
    assert dedupe.bit_error_rate(pack(fp[3:]), pack(fp)) == 0  # shifted by 3 frames
    assert dedupe.bit_error_rate(pack(fp), pack([rnd.getrandbits(32) for _ in range(300)])) > 0.4
    assert dedupe.bit_error_rate(pack(fp[:10]), pack(fp[:10])) == 1.0  # too short to judge


def test_missing_fpcalc_is_skipped_quietly(tmp_path, monkeypatch):
    monkeypatch.setattr(dedupe, "find_fpcalc", lambda configured="": None)
    put(tmp_path, "song-128.mp3", "a.mp3", title="X", artist="Y")
    put(tmp_path, "song.flac", "b.flac", title="Z", artist="W")
    import scan

    assert dedupe.find(scan.scan(str(tmp_path)).tracks, fingerprint=True) == []


def test_fpcalc_failure_on_a_file(tmp_path):
    exe = dedupe.find_fpcalc()
    if not exe:
        pytest.skip("fpcalc not available")
    (tmp_path / "bad.mp3").write_bytes(b"\xff\xfb" + b"\0" * 5000)
    assert dedupe.run_fpcalc(exe, str(tmp_path / "bad.mp3")) is None
    assert dedupe.run_fpcalc(exe, str(tmp_path / "missing.mp3")) is None


def test_estimate_is_positive():
    assert dedupe.estimate_seconds(0) == 0 and dedupe.estimate_seconds(1000) > 0
