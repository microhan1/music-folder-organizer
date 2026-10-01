"""Reverse the latest run recorded in organize_log.json.

Each press undoes one run, newest first. A file whose original place is taken
by something else is left where it is and listed; files sent to the recycle
bin are listed too (restore them from the recycle bin).
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


def pending_run(log_path: str | None) -> dict | None:
    if not log_path:
        return None
    runs = mover.load_log(log_path).get("runs", [])
    for run in reversed(runs):
        if not run.get("undone") and run.get("ops"):
            return run
    return None


def undo(log_path: str | None, progress: Callable[[int, int], None] | None = None,
         cancel: threading.Event | None = None) -> UndoResult:
    res = UndoResult()
    if not log_path or not os.path.isfile(log_path):
        res.nothing = True
        return res
    data = mover.load_log(log_path)
    run = next((r for r in reversed(data["runs"]) if not r.get("undone") and r.get("ops")), None)
    if run is None:
        res.nothing = True
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
    except OSError:
        pass
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
