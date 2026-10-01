"""v0.3.0 item 4: undo history — list runs, undo a chosen one, refuse when a newer run moved its files."""
import os
import time

import pytest

import main
import mover
import undo
from conftest import build, put, snapshot


def run(root, **kw):
    res = mover.execute(build(root, **kw)[1])
    time.sleep(1.1)  # runs are ordered by their time stamp (seconds)
    return res


def test_list_newest_first_with_counts(tmp_path):
    put(tmp_path, "song-128.mp3", "in1/x.mp3", title="X", artist="A", album="B", tracknumber="1")
    r1 = run(tmp_path)
    put(tmp_path, "other.mp3", "in2/y.mp3", title="Y", artist="C", album="D", tracknumber="1")
    r2 = run(tmp_path)
    runs = undo.runs_in([r1.log_path])
    assert [r.files for r in runs] == [1, 1]
    assert runs[0].time > runs[1].time and all(r.can_undo for r in runs)
    assert runs[0].count("mkdir") == 2 and runs[0].dest == str(tmp_path)


def test_undo_an_older_unrelated_run_first(tmp_path):
    put(tmp_path, "song-128.mp3", "in1/x.mp3", title="X", artist="A", album="B", tracknumber="1")
    before_first = snapshot(tmp_path)
    r1 = run(tmp_path)
    put(tmp_path, "other.mp3", "in2/y.mp3", title="Y", artist="C", album="D", tracknumber="1")
    run(tmp_path)
    old = undo.runs_in([r1.log_path])[1]
    res = undo.undo(r1.log_path, run_id=old.id)
    assert not res.blocked_by and res.restored == 1
    assert (tmp_path / "in1" / "x.mp3").exists() and (tmp_path / "C" / "D" / "01 - Y.mp3").exists()
    newest = undo.runs_in([r1.log_path])[0]
    assert newest.can_undo and not undo.runs_in([r1.log_path])[1].can_undo
    undo.undo(r1.log_path)
    os.remove(r1.log_path)
    del before_first  # the second file did not exist then; compare the end state instead
    assert (tmp_path / "in2" / "y.mp3").exists() and not (tmp_path / "A").exists() and not (tmp_path / "C").exists()


def test_newer_run_that_moved_the_files_blocks(tmp_path):
    put(tmp_path, "song-128.mp3", "in/x.mp3", title="X", artist="A", album="B", tracknumber="1")
    before = snapshot(tmp_path)
    r1 = run(tmp_path, pattern="{artist}/{title}")
    run(tmp_path)  # moves A/X.mp3 on to A/B/01 - X.mp3
    runs = undo.runs_in([r1.log_path])
    older = runs[1]
    assert [b.id for b in undo.blockers(older, runs)] == [runs[0].id]
    res = undo.undo(r1.log_path, run_id=older.id)
    assert res.blocked_by and res.restored == 0
    assert (tmp_path / "A" / "B" / "01 - X.mp3").exists()  # nothing touched
    undo.undo(r1.log_path)  # the newer one
    res = undo.undo(r1.log_path, run_id=older.id)
    assert not res.blocked_by and res.restored == 1
    os.remove(r1.log_path)
    assert snapshot(tmp_path) == before


def test_newer_copy_does_not_block(tmp_path):
    src = tmp_path / "src"
    put(src, "song-128.mp3", "in/x.mp3", title="X", artist="A", album="B", tracknumber="1")
    r1 = run(src)
    r2 = run(src, dest=tmp_path / "out", mode="copy")
    runs = undo.runs_in([r1.log_path, r2.log_path])
    older = next(r for r in runs if r.mode == "move")
    assert undo.blockers(older, runs) == []
    assert undo.undo(r1.log_path, run_id=older.id, other_logs=[r2.log_path]).restored == 1


def test_blocker_in_another_log(tmp_path):
    src = tmp_path / "src"
    put(src, "song-128.mp3", "in/x.mp3", title="X", artist="A", album="B", tracknumber="1")
    r1 = run(src)  # log in src
    r2 = run(src, dest=tmp_path / "out")  # moves the file out again; log in out
    older = next(r for r in undo.runs_in([r1.log_path]))
    res = undo.undo(r1.log_path, run_id=older.id, other_logs=[r2.log_path])
    assert len(res.blocked_by) == 1 and res.blocked_by[0].log == r2.log_path


def test_skipped_files_are_listed_in_history(tmp_path):
    put(tmp_path, "song-128.mp3", "in/x.mp3", title="X", artist="A", album="B", tracknumber="1")
    r1 = run(tmp_path)
    os.remove(tmp_path / "A" / "B" / "01 - X.mp3")
    undo.undo(r1.log_path)
    r = undo.runs_in([r1.log_path])[0]
    assert not r.can_undo and r.undone_time and len(r.skipped) == 1


def test_candidate_logs(tmp_path):
    src = tmp_path / "src"
    put(src, "song-128.mp3", "in/x.mp3", title="X", artist="A", album="B", tracknumber="1")
    r = run(src, dest=tmp_path / "out")
    assert undo.candidate_logs(str(src)) == [r.log_path]  # found through settings' last_log
    assert undo.candidate_logs(str(tmp_path / "out")) == [r.log_path]
    assert undo.candidate_logs(str(tmp_path / "unrelated")) == []


# ------------------------------------------------------------------ CLI
def cli(argv, capsys):
    rc = main.main([*map(str, argv), "--lang", "en"])
    return rc, capsys.readouterr()


def test_cli_history_and_undo_run(tmp_path, capsys):
    put(tmp_path, "song-128.mp3", "in/x.mp3", title="X", artist="A", album="B", tracknumber="1")
    before = snapshot(tmp_path)
    cli([tmp_path, "--pattern", "{artist}/{title}"], capsys)
    time.sleep(1.1)
    cli([tmp_path], capsys)
    rc, out = cli([tmp_path, "--history"], capsys)
    lines = [l for l in out.out.splitlines() if "Can be undone" in l]
    assert rc == 0 and len(lines) == 2
    newer_id, older_id = lines[0].split()[0], lines[1].split()[0]
    rc, out = cli([tmp_path, "--undo-run", older_id], capsys)
    assert rc == 1 and "Undo that run first" in out.err and newer_id in out.err
    assert cli([tmp_path, "--undo-run", newer_id], capsys)[0] == 0
    assert cli([tmp_path, "--undo-run", older_id], capsys)[0] == 0
    assert cli([tmp_path, "--undo-run", "nope"], capsys)[0] == 2
    rc, out = cli([tmp_path, "--history"], capsys)
    assert out.out.count("Undone (") == 2
    os.remove(tmp_path / "organize_log.json")
    assert snapshot(tmp_path) == before


def test_cli_history_without_log(tmp_path, capsys):
    rc, out = cli([tmp_path, "--history"], capsys)
    assert rc == 1 and "Nothing to undo" in out.out
