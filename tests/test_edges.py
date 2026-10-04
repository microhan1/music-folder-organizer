"""Edge cases of planning, running and undoing: races, cancels, read-only files,
broken logs, covers split across albums, nested empty folders."""
import json
import os
import shutil
import stat
import threading

import pytest

import i18n
import mover
import pattern
import plan as plan_mod
import undo
from conftest import build, put, snapshot
from scan import TagSet, Track


# ------------------------------------------------------------------ pattern
def _t(name="x.mp3", **tags):
    return Track(os.path.join("C:\\m", name), "MP3", 1, tags=TagSet(**tags))


FB = {"artist": "UA", "album": "UB", "year": "UY", "genre": "UG"}


def test_empty_folder_placeholder_is_dropped():
    assert pattern.render("{artist}/{disc}/{title}", _t(title="T", artist="A"), FB) == ["A", "T"]
    assert pattern.render("{artist}/Disc {disc}/{title}", _t(title="T", artist="A"), FB) == ["A", "Disc", "T"]
    assert pattern.render("{artist}/{disc}/{title}", _t(title="T", artist="A", disc="2/2"), FB) == ["A", "2", "T"]


def test_slash_in_value_never_makes_folders():
    segs = pattern.render(pattern.DEFAULT_PATTERN, _t(title="A/B\\C", artist="AC/DC", album="x:y"), FB)
    assert segs == ["AC_DC", "x_y", "A_B_C"]


def test_no_path_traversal():
    segs = pattern.render("../{artist}/./{title}", _t(title="..", artist=".."), FB)
    assert ".." not in segs and "." not in segs and len(segs) == 4
    segs = pattern.render("C:/x/{title}", _t(title="T"), FB)
    assert segs[0] == "C_"


def test_format_specs():
    t = _t(name="9 x.mp3", title="T", track="A1")
    assert pattern.render("{track:03} {title}", t, FB) == ["009 T"]  # "A1" is no number: the file name's 9
    assert pattern.render("{track:zz} {title}", _t(title="T", track="5"), FB) == ["5 T"]  # bad spec: plain
    assert pattern.render("{title:>6}", _t(title="T"), FB) == ["T"]  # padding spaces are trimmed
    assert pattern.render("{track:02} - {title}", _t(name="x.mp3", title="T", track="0"), FB) == ["T"]


def test_genre_and_year_fallbacks():
    assert pattern.render("{genre}/{year}/{title}", _t(title="T"), FB) == ["UG", "UY", "T"]


def test_leading_trailing_slashes_and_spaces():
    assert pattern.render("  /{artist}/{title}/  ", _t(title="T", artist="A"), FB) == ["A", "T"]


# ------------------------------------------------------------------ plan
def test_target_taken_by_a_file_that_moves_away_still_gets_a_number(tmp_path):
    # B sits where A wants to go; B itself moves elsewhere. Overwrite order never matters:
    # A takes " (2)" and a second run tidies it.
    put(tmp_path, "song-128.mp3", "X/Y/01 - A.mp3", title="B", artist="Q", album="R", tracknumber="1")
    put(tmp_path, "other.mp3", "in/a.mp3", title="A", artist="X", album="Y", tracknumber="1")
    _, p = build(tmp_path)
    a = next(i for i in p.items if i.src.endswith("a.mp3"))
    assert a.dst.endswith("01 - A (2).mp3")
    mover.execute(p)
    _, p2 = build(tmp_path)
    assert next(i for i in p2.items if i.src.endswith("01 - A (2).mp3")).dst.endswith("01 - A.mp3")


def test_unchecked_file_keeps_its_folder_and_cover(tmp_path):
    put(tmp_path, "song-128.mp3", "d/a.mp3", title="A", artist="X", album="Y", tracknumber="1")
    b = put(tmp_path, "other.mp3", "d/b.mp3", title="B", artist="X", album="Y", tracknumber="2")
    (tmp_path / "d" / "cover.jpg").write_bytes(b"c")
    from scan import key_of

    _, p = build(tmp_path, overrides={key_of(b): False})
    cover = [c for c in p.companions if c.kind == "cover"]
    assert len(cover) == 1 and cover[0].op == "copy"  # b stays, so the cover stays too
    assert p.empty_dirs == []


def test_cover_split_between_two_albums(tmp_path):
    put(tmp_path, "song-128.mp3", "d/a1.mp3", title="A1", artist="X", album="A", tracknumber="1")
    put(tmp_path, "other.mp3", "d/a2.mp3", title="A2", artist="X", album="A", tracknumber="2")
    put(tmp_path, "tone.ogg", "d/b1.ogg", title="B1", artist="X", album="B", tracknumber="1")
    (tmp_path / "d" / "Folder.JPG").write_bytes(b"c")
    before = snapshot(tmp_path)
    _, p = build(tmp_path)
    ops = sorted((c.op, os.path.basename(os.path.dirname(c.dst))) for c in p.companions)
    assert ops == [("copy", "B"), ("move", "A")]  # most files went to A
    res = mover.execute(p)
    assert (tmp_path / "X" / "A" / "Folder.JPG").exists() and (tmp_path / "X" / "B" / "Folder.JPG").exists()
    assert not (tmp_path / "d").exists()
    undo.undo(res.log_path)
    os.remove(res.log_path)
    assert snapshot(tmp_path) == before


def test_existing_cover_at_target_is_not_overwritten(tmp_path):
    put(tmp_path, "song-128.mp3", "d/a.mp3", title="A", artist="X", album="Y", tracknumber="1")
    (tmp_path / "d" / "cover.jpg").write_bytes(b"new")
    (tmp_path / "X" / "Y").mkdir(parents=True)
    (tmp_path / "X" / "Y" / "cover.jpg").write_bytes(b"old")
    _, p = build(tmp_path)
    assert [c for c in p.companions if c.kind == "cover"] == []
    mover.execute(p)
    assert (tmp_path / "X" / "Y" / "cover.jpg").read_bytes() == b"old"
    assert (tmp_path / "d" / "cover.jpg").read_bytes() == b"new"  # stays; its folder is not removed
    assert (tmp_path / "d").exists()


def test_lrc_follows_duplicate(tmp_path):
    put(tmp_path, "song-128.mp3", "a/s.mp3", title="S", artist="A")
    shutil.copyfile(tmp_path / "a" / "s.mp3", tmp_path / "b.mp3")  # shorter path: this one is kept
    (tmp_path / "a" / "s.lrc").write_text("x")
    _, p = build(tmp_path, groups="find")
    dup = next(i for i in p.items if i.status == plan_mod.DUP)
    assert dup.src.endswith("s.mp3")
    lrc = next(c for c in p.companions if c.kind == "lrc")
    assert os.path.dirname(lrc.dst) == os.path.dirname(dup.dst)


def test_nested_empty_folders_and_what_keeps_a_folder(tmp_path):
    # artist/album are "Art"/"Alb": a target "A/B" would be the same folder as "a/b" on Windows
    put(tmp_path, "song-128.mp3", "a/b/c/d/s.mp3", title="S", artist="Art", album="Alb", tracknumber="1")
    put(tmp_path, "other.mp3", "keep/t.mp3", title="T", artist="Art", album="Alb", tracknumber="2")
    (tmp_path / "keep" / "my notes.docx").write_text("not an album extra")  # keeps its folder
    (tmp_path / "a" / "b" / ".DS_Store").write_bytes(b"x")
    (tmp_path / "a" / "b" / "c" / "desktop.ini").write_bytes(b"x")
    os.chmod(tmp_path / "a" / "b" / "c" / "desktop.ini", stat.S_IREAD)  # read-only junk
    (tmp_path / "already-empty").mkdir()
    _, p = build(tmp_path)
    names = sorted(os.path.relpath(d, tmp_path) for d in p.empty_dirs)
    assert names == sorted(["a", os.path.join("a", "b"), os.path.join("a", "b", "c"), os.path.join("a", "b", "c", "d"),
                            "already-empty"])
    res = mover.execute(p)
    assert res.folders_removed == 5 and not (tmp_path / "a").exists()
    assert (tmp_path / "keep" / "my notes.docx").exists()
    assert os.path.isdir(tmp_path)  # the source root itself is never removed
    undo.undo(res.log_path)
    assert (tmp_path / "a" / "b" / "c" / "d" / "s.mp3").exists() and (tmp_path / "already-empty").is_dir()


def test_keep_empty_option(tmp_path):
    put(tmp_path, "song-128.mp3", "a/s.mp3", title="S", artist="A", album="B", tracknumber="1")
    _, p = build(tmp_path, remove_empty=False)
    assert p.empty_dirs == []


def test_copy_mode_duplicates_are_left_out_unless_ticked(tmp_path):
    src = tmp_path / "src"
    put(src, "song-128.mp3", "a/s.mp3", title="S", artist="A", album="B", tracknumber="1")
    shutil.copyfile(src / "a" / "s.mp3", src / "s2.mp3")
    _, p = build(src, dest=tmp_path / "out", mode="copy", groups="find")
    dup = next(i for i in p.items if i.status == plan_mod.DUP)
    assert dup.action == plan_mod.SKIP and not dup.checked
    _, p2 = build(src, dest=tmp_path / "out", mode="copy", groups="find", overrides={dup.key: True})
    dup2 = next(i for i in p2.items if i.key == dup.key)
    assert dup2.action == plan_mod.COPY
    assert sorted(os.path.basename(i.dst) for i in p2.items) == ["01 - S (2).mp3", "01 - S.mp3"]


def test_applied_group_without_keep_sends_all_away(tmp_path):
    # the GUI refuses to run this; the plan itself just follows the group
    import dedupe
    import scan as scan_mod

    put(tmp_path, "song-128.mp3", "a.mp3", title="S", artist="A")
    shutil.copyfile(tmp_path / "a.mp3", tmp_path / "b.mp3")
    groups = dedupe.find(scan_mod.scan(str(tmp_path)).tracks)
    groups[0].keep.clear()
    _, p = build(tmp_path, groups=groups)
    assert [i.status for i in p.items] == [plan_mod.DUP, plan_mod.DUP]


# ------------------------------------------------------------------ running
def test_target_appears_between_preview_and_run(tmp_path):
    put(tmp_path, "song-128.mp3", "in/a.mp3", title="A", artist="X", album="Y", tracknumber="1")
    _, p = build(tmp_path)
    os.makedirs(os.path.dirname(p.items[0].dst))
    with open(p.items[0].dst, "wb") as f:
        f.write(b"someone else")
    res = mover.execute(p)
    assert res.done == 0 and len(res.failed) == 1
    assert open(p.items[0].dst, "rb").read() == b"someone else"
    assert (tmp_path / "in" / "a.mp3").exists()


def test_source_vanishes_between_preview_and_run(tmp_path):
    put(tmp_path, "song-128.mp3", "in/a.mp3", title="A", artist="X", album="Y", tracknumber="1")
    _, p = build(tmp_path)
    os.remove(tmp_path / "in" / "a.mp3")
    res = mover.execute(p)
    assert len(res.failed) == 1 and not (tmp_path / "X").exists()  # no empty folders left behind
    assert res.log_path == "" or not json.load(open(res.log_path, encoding="utf-8"))["runs"][-1]["ops"] == []


def test_folder_name_taken_by_a_file(tmp_path):
    put(tmp_path, "song-128.mp3", "in/a.mp3", title="A", artist="X", album="Y", tracknumber="1")
    put(tmp_path, "other.mp3", "in/b.mp3", title="B", artist="Z", album="W", tracknumber="1")
    (tmp_path / "X").write_bytes(b"a file called X")
    _, p = build(tmp_path)
    res = mover.execute(p)
    assert res.done == 1 and len(res.failed) == 1
    assert (tmp_path / "X").read_bytes() == b"a file called X"


def test_log_cannot_be_written_moves_nothing(tmp_path):
    put(tmp_path, "src/in/a.mp3".split("/")[0] and "song-128.mp3", "src/in/a.mp3", title="A", artist="X", album="Y", tracknumber="1")
    (tmp_path / "blocker").write_bytes(b"file, not a folder")
    before = snapshot(tmp_path)
    _, p = build(tmp_path / "src", dest=tmp_path / "blocker" / "out")
    with pytest.raises(OSError):
        mover.execute(p)
    assert snapshot(tmp_path) == before


def test_broken_log_is_kept_aside(tmp_path):
    put(tmp_path, "song-128.mp3", "in/a.mp3", title="A", artist="X", album="Y", tracknumber="1")
    (tmp_path / "organize_log.json").write_text("{ not json", encoding="utf-8")
    res = mover.execute(build(tmp_path)[1])
    broken = [n for n in os.listdir(tmp_path) if n.startswith("organize_log.json.broken-")]
    assert len(broken) == 1 and (tmp_path / broken[0]).read_text(encoding="utf-8") == "{ not json"
    assert len(json.load(open(res.log_path, encoding="utf-8"))["runs"]) == 1


def test_nothing_to_do_writes_no_log(tmp_path):
    a = put(tmp_path, "song-128.mp3", "in/a.mp3", title="A", artist="X", album="Y", tracknumber="1")
    from scan import key_of

    res = mover.execute(build(tmp_path, overrides={key_of(a): False}, remove_empty=False)[1])
    assert res.log_path == "" and not (tmp_path / "organize_log.json").exists()
    # an existing log keeps exactly its runs
    mover.execute(build(tmp_path)[1])
    runs = len(json.load(open(tmp_path / "organize_log.json", encoding="utf-8"))["runs"])
    mover.execute(build(tmp_path)[1])  # second run: everything already in place
    assert len(json.load(open(tmp_path / "organize_log.json", encoding="utf-8"))["runs"]) == runs


def test_cancel_mid_run_then_undo(tmp_path):
    for i in range(6):
        put(tmp_path, "tone.ogg", f"d{i}/t.ogg", title=f"T{i}", artist="A", album="B", tracknumber=str(i + 1))
    before = snapshot(tmp_path)
    ev = threading.Event()
    calls = []

    def progress(done, total):
        calls.append(done)
        if done == 2:
            ev.set()

    res = mover.execute(build(tmp_path)[1], progress=progress, cancel=ev)
    assert res.cancelled and res.done == 2 and res.folders_removed == 0
    u = undo.undo(res.log_path)
    assert u.restored == 2 and not u.skipped
    os.remove(res.log_path)
    assert snapshot(tmp_path) == before


def _sixty(tmp_path):
    for i in range(60):
        put(tmp_path, "tone.ogg", f"in/{i:02}.ogg", title=f"T{i}", artist="A", album="B", tracknumber=str(i + 1))


def _journal_fails_at(monkeypatch, line):
    """The journal refuses its ``line``-th line (1 = the header) as a full disk would."""
    real, calls = mover.RunLog._write, []

    def write(self, obj):
        calls.append(obj)
        if len(calls) == line:
            raise mover.LogWriteError(28, "No space left on device", mover.journal_path(self.path))
        real(self, obj)

    monkeypatch.setattr(mover.RunLog, "_write", write)


def _save_fails_after(monkeypatch, ok):
    """save_log works ``ok`` times, then fails."""
    real, calls = mover.save_log, []

    def save(path, data):
        calls.append(path)
        if len(calls) > ok:
            raise PermissionError(13, "locked by another program", path)
        real(path, data)

    monkeypatch.setattr(mover, "save_log", save)
    return real


def test_log_is_saved_twice_per_run_not_every_50_steps(tmp_path, monkeypatch):
    _sixty(tmp_path)
    real, calls = mover.save_log, []
    monkeypatch.setattr(mover, "save_log", lambda p, d: (calls.append(p), real(p, d)))
    res = mover.execute(build(tmp_path)[1])
    assert res.done == 60 and not res.failed and len(calls) == 2  # before the first move, after the last
    assert not os.path.exists(mover.journal_path(res.log_path))
    run = undo.runs_in([res.log_path])[0]
    assert (run.count("mkdir"), run.count("move"), run.count("rmdir")) == (2, 60, 1)  # A, A/B; in


def test_journal_fails_mid_run_stops(tmp_path, monkeypatch):
    _sixty(tmp_path)
    before = snapshot(tmp_path)
    _journal_fails_at(monkeypatch, 31)  # header, mkdir A, mkdir A/B, then the 28th move
    res = mover.execute(build(tmp_path)[1])
    assert res.cancelled and res.done == 28  # the 28th file had moved when its line was refused
    assert len(os.listdir(tmp_path / "in")) == 60 - 28
    assert res.failed == [(res.log_path, i18n.t("err_log_stopped"))]  # the final save has all 28
    u = undo.undo(res.log_path)
    assert u.restored == 28 and not u.skipped
    os.remove(res.log_path)
    assert snapshot(tmp_path) == before


def test_journal_and_final_save_both_fail(tmp_path, monkeypatch):
    _sixty(tmp_path)
    _journal_fails_at(monkeypatch, 31)
    real = _save_fails_after(monkeypatch, 1)
    res = mover.execute(build(tmp_path)[1])
    assert res.cancelled and res.done == 28
    assert res.failed == [(res.log_path, i18n.t("err_log_lost"))]
    assert not (tmp_path / "organize_log.json.part").exists()
    monkeypatch.setattr(mover, "save_log", real)
    u = undo.undo(res.log_path)  # the journal had 27 of the 28 moves
    assert u.restored == 27 and not u.skipped


def test_final_save_fails_and_the_journal_is_merged_later(tmp_path, monkeypatch):
    _sixty(tmp_path)
    before = snapshot(tmp_path)
    real = _save_fails_after(monkeypatch, 1)
    res = mover.execute(build(tmp_path)[1])
    assert res.done == 60 and not res.cancelled
    assert res.failed == [(res.log_path, i18n.t("err_log_pending"))]
    assert os.path.exists(mover.journal_path(res.log_path))
    monkeypatch.setattr(mover, "save_log", real)
    assert undo.runs_in([res.log_path])[0].files == 60  # history reads through the journal
    u = undo.undo(res.log_path)
    assert u.restored == 60 and not u.skipped
    assert not os.path.exists(mover.journal_path(res.log_path))  # merged, then dropped
    os.remove(res.log_path)
    assert snapshot(tmp_path) == before


def test_program_stops_mid_run_then_undo_restores(tmp_path):
    """A real stop (os._exit, no finally): only the journal knows the moves."""
    import subprocess
    import sys

    lib = tmp_path / "lib"
    _sixty(lib)
    before = snapshot(lib)
    tests = os.path.dirname(os.path.abspath(__file__))
    script = (
        "import os, sys\n"
        f"sys.path[:0] = [{os.path.dirname(tests)!r}, {tests!r}]\n"
        "import i18n, mover\n"
        f"i18n._settings_path = {str(tmp_path / 'settings.json')!r}\n"
        "from conftest import build\n"
        f"p = build({str(lib)!r}, artists={str(tmp_path / 'artists.json')!r})[1]\n"
        "mover.execute(p, progress=lambda d, n: os._exit(3) if d == 30 else None)\n"
    )
    assert subprocess.run([sys.executable, "-c", script], timeout=120).returncode == 3
    log = str(lib / "organize_log.json")
    assert os.path.exists(mover.journal_path(log))
    moved = 60 - len(os.listdir(lib / "in"))
    assert moved == 30
    run = undo.runs_in([log])[0]
    assert run.files == 30 and run.can_undo
    u = undo.undo(log)
    assert u.restored == 30 and not u.skipped
    os.remove(log)
    assert snapshot(lib) == before


def test_a_finished_run_ignores_a_leftover_journal(tmp_path):
    _sixty(tmp_path)
    res = mover.execute(build(tmp_path)[1])
    run = undo.runs_in([res.log_path])[0]
    with open(mover.journal_path(res.log_path), "w", encoding="utf-8") as f:  # e.g. its delete failed
        f.write(json.dumps({"journal": {"id": run.id}}) + "\n")
        for _ in range(100):
            f.write(json.dumps({"op": "move", "src": "x", "dst": "y"}) + "\n")
    assert len(undo.runs_in([res.log_path])[0].ops) == len(run.ops)
    u = undo.undo(res.log_path)
    assert u.restored == 60 and not u.skipped


def test_undo_with_unwritable_log_puts_nothing_back(tmp_path):
    put(tmp_path, "song-128.mp3", "in/a.mp3", title="A", artist="X", album="Y", tracknumber="1")
    res = mover.execute(build(tmp_path)[1])
    organized = snapshot(tmp_path)
    os.chmod(res.log_path, stat.S_IREAD)
    try:
        u = undo.undo(res.log_path)
    finally:
        os.chmod(res.log_path, stat.S_IREAD | stat.S_IWRITE)
    assert u.log_error and u.restored == 0 and not u.log_unsaved
    assert snapshot(tmp_path) == organized
    assert undo.undo(res.log_path).restored == 1  # writable again: undo works


def test_undo_log_save_fails_at_the_end_is_reported(tmp_path, monkeypatch):
    put(tmp_path, "song-128.mp3", "in/a.mp3", title="A", artist="X", album="Y", tracknumber="1")
    res = mover.execute(build(tmp_path)[1])
    real, calls = mover.save_log, []

    def flaky(path, data):
        calls.append(path)
        if len(calls) == 2:  # the first save (before undoing) works, the last one fails
            raise PermissionError(13, "locked by another program", path)
        real(path, data)

    monkeypatch.setattr(mover, "save_log", flaky)
    u = undo.undo(res.log_path)
    assert u.restored == 1 and u.log_unsaved and not u.log_error
    assert (tmp_path / "in" / "a.mp3").exists()


def test_cli_undo_reports_log_errors(tmp_path, capsys, monkeypatch):
    import main

    put(tmp_path, "song-128.mp3", "in/a.mp3", title="A", artist="X", album="Y", tracknumber="1")
    res = mover.execute(build(tmp_path)[1])
    os.chmod(res.log_path, stat.S_IREAD)
    try:
        assert main.run_undo(str(tmp_path)) == 1
    finally:
        os.chmod(res.log_path, stat.S_IREAD | stat.S_IWRITE)
    assert i18n.t("err_undo_log_write", path=res.log_path, error="").split("(")[0] in capsys.readouterr().err


def test_cancelled_undo_continues_next_time(tmp_path):
    for i in range(6):
        put(tmp_path, "tone.ogg", f"d{i}/t.ogg", title=f"T{i}", artist="A", album="B", tracknumber=str(i + 1))
    before = snapshot(tmp_path)
    res = mover.execute(build(tmp_path)[1])
    ev = threading.Event()
    u1 = undo.undo(res.log_path, progress=lambda d, n: ev.set() if d == 3 else None, cancel=ev)
    assert u1.cancelled
    u2 = undo.undo(res.log_path)
    assert not u2.skipped, u2.skipped  # no "missing" noise for what the first press already did
    assert u1.restored + u2.restored == 6
    os.remove(res.log_path)
    assert snapshot(tmp_path) == before


def test_undo_with_files_moved_or_deleted_by_the_user(tmp_path):
    for i in range(3):
        put(tmp_path, "tone.ogg", f"d{i}/t.ogg", title=f"T{i}", artist="A", album="B", tracknumber=str(i + 1))
    res = mover.execute(build(tmp_path)[1])
    os.remove(tmp_path / "A" / "B" / "01 - T0.ogg")
    u = undo.undo(res.log_path)
    assert u.restored == 2 and len(u.skipped) == 1
    data = json.load(open(res.log_path, encoding="utf-8"))
    assert data["runs"][-1]["undone"] and len(data["runs"][-1]["undo_skipped"]) == 1


def test_read_only_files_copy_mode_undo(tmp_path):
    a = put(tmp_path, "song-128.mp3", "src/a.mp3", title="A", artist="X", album="Y", tracknumber="1")
    os.chmod(a, stat.S_IREAD)
    res = mover.execute(build(tmp_path / "src", dest=tmp_path / "out", mode="copy")[1])
    copy = tmp_path / "out" / "X" / "Y" / "01 - A.mp3"
    assert copy.exists() and not os.access(copy, os.W_OK)
    u = undo.undo(res.log_path)
    assert u.restored == 1 and not copy.exists()
    os.chmod(a, stat.S_IWRITE)


def test_read_only_file_moves_and_comes_back(tmp_path):
    a = put(tmp_path, "song-128.mp3", "in/a.mp3", title="A", artist="X", album="Y", tracknumber="1")
    os.chmod(a, stat.S_IREAD)
    res = mover.execute(build(tmp_path)[1])
    assert res.done == 1
    undo.undo(res.log_path)
    assert os.path.exists(a) and not os.access(a, os.W_OK)
    os.chmod(a, stat.S_IWRITE)


def _other_drive_dir():
    here = os.path.splitdrive(os.path.abspath(__file__))[0]
    temp = os.path.splitdrive(os.environ.get("TEMP", "C:\\"))[0]
    if here.upper() == temp.upper():
        return None
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "build", "pytest-xdrive")


@pytest.mark.skipif(_other_drive_dir() is None, reason="needs the repo and TEMP on different drives")
def test_cross_drive_read_only_move_and_undo(tmp_path):
    dest = _other_drive_dir()
    shutil.rmtree(dest, ignore_errors=True)
    try:
        a = put(tmp_path, "song.flac", "in/a.flac", title="A", artist="X", album="Y", tracknumber="1")
        os.chmod(a, stat.S_IREAD)
        before = snapshot(tmp_path)
        res = mover.execute(build(tmp_path, dest=dest)[1])
        assert res.done == 1 and not res.failed, res.failed
        assert not os.path.exists(a)
        moved = os.path.join(dest, "X", "Y", "01 - A.flac")
        assert not os.access(moved, os.W_OK)  # attributes travel with the copy
        u = undo.undo(res.log_path)
        assert u.restored == 1 and not u.skipped, u.skipped
        assert snapshot(tmp_path) == before
        os.chmod(a, stat.S_IWRITE)
    finally:
        shutil.rmtree(dest, ignore_errors=True)


def test_trash_is_logged_and_reported_by_undo(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    monkeypatch.setattr(mover, "trash", lambda p: shutil.move(p, bin_dir / os.path.basename(p)))
    root = tmp_path / "m"
    put(root, "song-128.mp3", "a/s.mp3", title="S", artist="A", album="B", tracknumber="1")
    shutil.copyfile(root / "a" / "s.mp3", root / "copy.mp3")
    _, p = build(root, groups="find", dupes_action="trash")
    dup = next(i for i in p.items if i.status == plan_mod.DUP)
    assert dup.action == plan_mod.DUPE_TRASH and dup.dst == "" and p.summary()["trash"] == 1
    res = mover.execute(p)
    assert res.trashed == 1 and (bin_dir / os.path.basename(dup.src)).exists()
    u = undo.undo(res.log_path)
    assert u.trashed == [dup.src]


def test_trash_failure_is_a_failure_not_a_delete(tmp_path, monkeypatch):
    def refuse(p):
        raise OSError(5, "recycle bin refused")

    monkeypatch.setattr(mover, "trash", refuse)
    put(tmp_path, "song-128.mp3", "a/s.mp3", title="S", artist="A", album="B", tracknumber="1")
    shutil.copyfile(tmp_path / "a" / "s.mp3", tmp_path / "copy.mp3")
    res = mover.execute(build(tmp_path, groups="find", dupes_action="trash")[1])
    assert len(res.failed) == 1 and (tmp_path / "copy.mp3").exists()


def test_music_tag_filler_backup_still_restores_after_moving(tmp_path):
    import importlib.util

    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "music-tag-filler", "tags.py")
    if not os.path.exists(path):
        pytest.skip("music-tag-filler is not next to this repo")
    import sys

    spec = importlib.util.spec_from_file_location("mtf_tags", path)
    mtf = importlib.util.module_from_spec(spec)
    sys.modules["mtf_tags"] = mtf  # dataclasses look their module up there
    spec.loader.exec_module(mtf)
    a = put(tmp_path, "song-128.mp3", "dl/track.mp3")
    original = snapshot(tmp_path)["files"][os.path.join("dl", "track.mp3")]
    mtf.make_backup(a)
    mtf.write_file(a, mtf.Tags(title="Filled", artist="Filler", album="Album", track="4"))
    res = mover.execute(build(tmp_path)[1])
    new = tmp_path / "Filler" / "Album" / "04 - Filled.mp3"
    assert new.exists() and (tmp_path / "Filler" / "Album" / "04 - Filled.mp3.tagbak.json").exists()
    assert mtf.restore_backup(str(new)) is True  # byte-exact undo in the other tool, at the new place
    assert snapshot(tmp_path)["files"][os.path.join("Filler", "Album", "04 - Filled.mp3")] == original
    assert res.done == 1


def _music_tag_filler_tags():
    import importlib.util
    import sys

    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "music-tag-filler", "tags.py")
    if not os.path.exists(path):
        pytest.skip("music-tag-filler is not next to this repo")
    spec = importlib.util.spec_from_file_location("mtf_tags", path)
    mtf = importlib.util.module_from_spec(spec)
    sys.modules["mtf_tags"] = mtf
    spec.loader.exec_module(mtf)
    if not hasattr(mtf, "Ids"):
        pytest.skip("this music-tag-filler does not write identifiers yet")
    return mtf


def test_ids_written_by_music_tag_filler_merge_artists(tmp_path):
    """Write with the other tool's real writer, read here: the formats must match."""
    import scan as scan_mod

    mtf = _music_tag_filler_tags()
    a = put(tmp_path, "song-128.mp3", "x/a.mp3")
    b = put(tmp_path, "song.flac", "y/b.flac")
    c = put(tmp_path, "silent.m4a", "z/c.m4a")
    mtf.write_file(a, mtf.Tags(title="A", artist="岡田有希子", album="Fairy", track="1"),
                   ids=mtf.Ids(mb_artist_ids=["mb-okada"], itunes_artist_id="275749278"))
    mtf.write_file(b, mtf.Tags(title="B", artist="Yukiko Okada", album="Fairy", track="2"),
                   ids=mtf.Ids(itunes_artist_id="275749278"))
    mtf.write_file(c, mtf.Tags(title="C", artist="오카다 유키코", album="Fairy", track="3"),
                   ids=mtf.Ids(mb_artist_ids=["mb-okada"]))
    tracks = {t.name: t for t in scan_mod.scan(str(tmp_path)).tracks}
    assert tracks["a.mp3"].tags.itunes_artist_id == "275749278" and tracks["a.mp3"].tags.mb_artist_id == "mb-okada"
    assert tracks["b.flac"].tags.itunes_artist_id == "275749278"
    assert tracks["c.m4a"].tags.mb_artist_id == "mb-okada"
    _, p = build(tmp_path)
    folders = {os.path.relpath(i.dst, tmp_path).split(os.sep)[0] for i in p.items}
    assert len(folders) == 1, folders  # three spellings, one folder, joined only by the ids


def test_five_thousand_files_preview_is_fast(tmp_path):
    import time

    seed = put(tmp_path, "tone.ogg", "seed.ogg", title="T", artist="A", album="B")
    root = tmp_path / "big"
    for i in range(5000):
        d = root / f"dl{i % 97}"
        d.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(seed, d / f"{i}.ogg")
    start = time.perf_counter()
    res, p = build(root)
    first = time.perf_counter() - start
    start = time.perf_counter()
    import plan as pm
    import session
    import prefs as prefs_mod

    pr = prefs_mod.Prefs(artists_path=os.devnull)
    pm.build(res, session.options(pr, str(root), str(root), "move"), None, None, {p.items[0].key: False})
    rebuild = time.perf_counter() - start
    print(f"\n5000 files: scan+plan {first:.2f}s, re-plan after a click {rebuild:.2f}s")
    assert first < 30 and rebuild < 3
    assert len({i.dst for i in p.items}) == 5000  # all identical tags: 4999 numbered, none lost
