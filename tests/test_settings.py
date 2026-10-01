"""v0.3.0 item 6: settings — validation, and a renamed duplicates folder stays out of scans."""
import os
import shutil

import mover
import prefs
import scan
import session
from conftest import build, put


def test_check_accepts_blanks_and_good_values(tmp_path):
    exe = tmp_path / "fpcalc.exe"
    exe.write_bytes(b"")
    good = {"dupes_folder": "Doubles", "fallbacks": {"artist": "Nobody", "album": ""},
            "fpcalc_path": str(exe), "tag_filler_path": "", "artists_path": str(tmp_path / "a.json")}
    assert prefs.check(good) == []
    assert prefs.check({}) == []


def test_check_reports_each_problem(tmp_path):
    bad = {"dupes_folder": "a/b", "fallbacks": {"genre": "Pop?"},
           "fpcalc_path": str(tmp_path / "missing.exe"), "tag_filler_path": str(tmp_path / "nope.exe"),
           "artists_path": str(tmp_path / "no-such-folder" / "artists.json")}
    assert sorted(prefs.check(bad)) == sorted([
        ("dupes_folder", "err_bad_folder_name"), ("fallback_genre", "err_bad_folder_name"),
        ("fpcalc_path", "err_file_not_found"), ("tag_filler_path", "err_file_not_found"),
        ("artists_path", "err_folder_not_found")])
    assert prefs.check({"dupes_folder": " trailing. "})  # stripped to "trailing." -> trailing dot
    assert prefs.check({"dupes_folder": ".."})


def test_renamed_duplicates_folder_is_still_skipped(tmp_path):
    root = tmp_path / "m"
    put(root, "song-128.mp3", "in1/x.mp3", title="X", artist="A", album="B", tracknumber="1")
    shutil.copyfile(root / "in1" / "x.mp3", root / "in1" / "copy.mp3")
    mover.execute(build(root, groups="find", dupes_folder="Old Doubles")[1])
    assert (root / "Old Doubles").is_dir()
    p = prefs.Prefs(dupes_folder="Old Doubles")
    p.set_dupes_folder("New Doubles")
    assert p.dupes_name() == "New Doubles" and p.dupes_folder_history == ["Old Doubles"]
    names = {t.path for t in scan.scan(str(root), session.excludes(str(root), str(root), p)).tracks}
    assert not any("Old Doubles" in n for n in names)
    p.set_dupes_folder("")  # back to the language default: both custom names stay skipped
    assert p.dupes_folder_history == ["Old Doubles", "New Doubles"]


def test_history_survives_save_and_load(tmp_path):
    p = prefs.Prefs()
    p.set_dupes_folder("First")
    p.set_dupes_folder("Second")
    prefs.save(p)
    q = prefs.load()
    assert q.dupes_folder == "Second" and q.dupes_folder_history == ["First"]
