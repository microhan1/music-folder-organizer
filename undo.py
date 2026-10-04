"""Reverse a run recorded in organize_log.json.

By default the newest run that is not undone yet; ``run_id`` picks another. An
older run cannot be undone while a newer, not undone run moved the files it
placed (undo that one first). A file whose original place is taken by
something else is left where it is and listed; files sent to the recycle bin
are listed too (restore them from the recycle bin).
"""
from __future__ import annotations

import dataclasses
import os
import threading
import time
from typing import Callable

import i18n
import mover
from scan import LOG_NAME, key_of


@dataclasses.dataclass
class UndoResult:
    restored: int = 0
    skipped: list[tuple[str, str]] = dataclasses.field(default_factory=list)  # (path, reason)
    trashed: list[str] = dataclasses.field(default_factory=list)
    nothing: bool = False  # no log, or every run already undone
    cancelled: bool = False
    blocked_by: list["RunInfo"] = dataclasses.field(default_factory=list)  # newer runs to undo first
    log_error: str = ""  # the log cannot be written: nothing was undone
    log_unsaved: str = ""  # files were put back but the log could not record it


@dataclasses.dataclass
class RunInfo:
    """One run of a log, as the history list shows it."""
    log: str
    index: int  # position in that log's runs
    id: str
    time: str
    mode: str
    source: str
    dest: str
    ops: list
    undone: bool
    undone_time: str = ""
    skipped: list[tuple[str, str]] = dataclasses.field(default_factory=list)

    def count(self, *kinds: str) -> int:
        return sum(1 for op in self.ops if op.get("op") in kinds)

    @property
    def files(self) -> int:
        return self.count("move", "copy", "trash")

    @property
    def can_undo(self) -> bool:
        return not self.undone and bool(self.ops)

    def order(self) -> tuple[str, str, int]:
        return (self.time, key_of(self.log), self.index)


def runs_in(logs: list[str | None]) -> list[RunInfo]:
    """Every run in the given logs (duplicates and missing logs ignored), newest first."""
    seen: set[str] = set()
    out: list[RunInfo] = []
    for log in logs:
        if not log or not os.path.isfile(log) or key_of(log) in seen:
            continue
        seen.add(key_of(log))
        for i, run in enumerate(mover.load_log(log).get("runs", [])):
            if not run.get("ops") and not run.get("undone"):
                continue
            skipped = [(s.get("path", ""), s.get("reason", "")) for s in run.get("undo_skipped", []) if isinstance(s, dict)]
            out.append(RunInfo(log, i, str(run.get("id", "")), str(run.get("time", "")), str(run.get("mode", "move")),
                               str(run.get("source", "")), str(run.get("dest", "")), list(run.get("ops", [])),
                               bool(run.get("undone")), str(run.get("undone_time", "")), skipped))
    out.sort(key=lambda r: r.order(), reverse=True)
    return out


def blockers(target: RunInfo, runs: list[RunInfo]) -> list[RunInfo]:
    """Newer runs, not undone, that moved or trashed a file this run placed. (A newer
    copy leaves the file where it was, so it does not get in the way.)"""
    placed = {key_of(op["dst"]) for op in target.ops if op.get("op") in ("move", "copy") and op.get("dst")}
    out = []
    for r in runs:
        if r is target or not r.can_undo or r.order() <= target.order():
            continue
        if any(key_of(op.get("src", "")) in placed for op in r.ops if op.get("op") in ("move", "trash")):
            out.append(r)
    return out


def find_log(folder: str) -> str | None:
    """organize_log.json in ``folder``; else the last log from settings when its
    run started from or went to that folder."""
    here = os.path.join(folder, LOG_NAME)
    if os.path.isfile(here):
        return here
    last = i18n.load_settings().get("last_log")
    if isinstance(last, str) and os.path.isfile(last):
        for run in mover.load_log(last).get("runs", []):
            if key_of(folder) in (key_of(run.get("source", "")), key_of(run.get("dest", ""))):
                return last
    return None


def candidate_logs(*folders: str | None) -> list[str]:
    """Logs that may hold runs for these folders: each folder's own log, and the
    last log from settings when one of its runs started from or went to them."""
    out = [os.path.join(f, LOG_NAME) for f in folders if f]
    out = [p for p in out if os.path.isfile(p)]
    last = i18n.load_settings().get("last_log")
    if isinstance(last, str) and os.path.isfile(last):
        keys = {key_of(f) for f in folders if f}
        if any(key_of(r.get("source", "")) in keys or key_of(r.get("dest", "")) in keys
               for r in mover.load_log(last).get("runs", [])):
            out.append(last)
    return list(dict.fromkeys(out))


def pending_run(log_path: str | None) -> dict | None:
    if not log_path:
        return None
    runs = mover.load_log(log_path).get("runs", [])
    for run in reversed(runs):
        if not run.get("undone") and run.get("ops"):
            return run
    return None


def undo(log_path: str | None, progress: Callable[[int, int], None] | None = None,
         cancel: threading.Event | None = None, run_id: str | None = None,
         other_logs: list[str | None] | None = None) -> UndoResult:
    """Undo ``run_id`` (default: the newest run not undone). ``other_logs`` are
    searched too for newer runs that would block it."""
    res = UndoResult()
    if not log_path or not os.path.isfile(log_path):
        res.nothing = True
        return res
    data = mover.load_log(log_path)
    pending = [r for r in data["runs"] if not r.get("undone") and r.get("ops")]
    if run_id:
        run = next((r for r in pending if str(r.get("id")) == run_id), None)
    else:
        run = pending[-1] if pending else None
    if run is None:
        res.nothing = True
        return res
    known = runs_in([log_path, *(other_logs or [])])
    me = next((r for r in known if key_of(r.log) == key_of(log_path) and r.id == str(run.get("id"))), None)
    if me is not None:
        res.blocked_by = blockers(me, known)
        if res.blocked_by:
            return res
    try:
        mover.save_log(log_path, data)  # same principle as a run: no file moves before the log is known to be writable
    except OSError as exc:
        res.log_error = i18n.t("err_undo_log_write", path=log_path, error=exc)
        return res
    ops = list(reversed(run["ops"]))
    for n, op in enumerate(ops, 1):
        if cancel is not None and cancel.is_set():
            res.cancelled = True
            run["ops"] = list(reversed(ops[n - 1:]))  # the next undo continues from here
            break
        kind = op.get("op")
        try:
            if kind == "move":
                _undo_move(op["src"], op["dst"], res)
            elif kind == "copy":
                _undo_copy(op, res)
            elif kind == "trash":
                res.trashed.append(op["src"])
            elif kind == "rmdir":
                os.makedirs(op["path"], exist_ok=True)
            elif kind == "mkdir":
                mover.remove_junk_folder(op["path"])
        except (OSError, KeyError) as exc:
            res.skipped.append((op.get("src") or op.get("path", ""), mover.reason_of(exc) if isinstance(exc, OSError) else str(exc)))
        if progress is not None:
            progress(n, len(ops))
    if not res.cancelled:
        run["undone"] = True
        run["undone_time"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        if res.skipped:
            run["undo_skipped"] = [{"path": p, "reason": r} for p, r in res.skipped]
    try:
        mover.save_log(log_path, data)
    except OSError as exc:
        res.log_unsaved = i18n.t("err_undo_log_unsaved", path=log_path, error=exc)
    return res


def _undo_move(src: str, dst: str, res: UndoResult) -> None:
    if not os.path.lexists(dst):
        res.skipped.append((src, i18n.t("err_missing")))
        return
    if os.path.lexists(src) and os.path.normcase(src) != os.path.normcase(dst):
        res.skipped.append((src, i18n.t("err_original_taken")))
        return
    os.makedirs(os.path.dirname(src), exist_ok=True)
    mover.move_file(dst, src)
    res.restored += 1


def _undo_copy(op: dict, res: UndoResult) -> None:
    dst = op["dst"]
    try:
        st = os.stat(dst)
    except FileNotFoundError:
        return  # already gone
    if st.st_size != op.get("size") or st.st_mtime_ns != op.get("mtime_ns"):
        res.skipped.append((dst, i18n.t("err_copy_changed")))
        return
    mover.remove_file(dst)
    res.restored += 1
