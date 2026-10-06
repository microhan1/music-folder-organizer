"""Run a Plan: move / copy files, send duplicates away, remove emptied folders,
and record every step in organize_log.json so undo.py can reverse it.

File contents are never rewritten. A move within one drive is a rename; across
drives the copy is verified by SHA-1 before the original is removed.
"""
from __future__ import annotations

import dataclasses
import errno
import hashlib
import json
import os
import shutil
import stat
import sys
import threading
import time
import uuid
from typing import Callable

import i18n
from plan import COPY, DUPE_MOVE, DUPE_TRASH, MOVE, Plan
from longpath import fs
from scan import LOG_NAME, is_junk, key_of

FLUSH_EVERY = 50  # journal lines between fsyncs
JOURNAL_SUFFIX = ".journal"
CHUNK = 1024 * 1024
ProgressFn = Callable[[int, int], None]


class CopyMismatch(OSError):
    pass


class LogWriteError(OSError):
    """The undo log could not be saved mid-run: the run has to stop, not skip one file."""


@dataclasses.dataclass
class Result:
    done: int = 0
    failed: list[tuple[str, str]] = dataclasses.field(default_factory=list)  # (path, reason)
    trashed: int = 0
    folders_removed: int = 0
    log_path: str = ""
    cancelled: bool = False


# ------------------------------------------------------------------ log
def log_path_for(folder: str) -> str:
    return os.path.join(folder, LOG_NAME)


def journal_path(log_path: str) -> str:
    return log_path + JOURNAL_SUFFIX


def _read_log(path: str) -> tuple[dict, bool]:
    """(data, broken): broken = the file exists, is not empty, and cannot be read as a log."""
    empty = {"version": 1, "runs": []}
    try:
        with open(fs(path), "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return empty, False
    except (OSError, ValueError):
        return empty, _size(path) > 0
    if not isinstance(data, dict) or not isinstance(data.get("runs"), list):
        return empty, True
    return data, False


def _size(path: str) -> int:
    try:
        return os.path.getsize(fs(path))
    except OSError:
        return 0


def _merge_journal(path: str, data: dict) -> None:
    """Steps of a run that never finished (the program stopped mid-run) are only in
    the journal: put them into that run. Only an open run takes them, so a journal
    left behind by a finished run can never bring back steps an undo removed."""
    try:
        with open(fs(journal_path(path)), "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return
    header, ops = None, []
    for line in lines:
        try:
            obj = json.loads(line)
        except ValueError:
            break  # a line cut short when the program stopped: the ones before it count
        if header is None:
            header = obj.get("journal") if isinstance(obj, dict) else None
            if not isinstance(header, dict) or not header.get("id"):
                return
        elif isinstance(obj, dict):
            ops.append(obj)
    if header is None:
        return
    run = next((r for r in data["runs"] if r.get("id") == header["id"]), None)
    if run is None:  # the log itself lost it (replaced, set aside as broken)
        run = {**header, "ops": []}
        data["runs"].append(run)
    if not run.get("open"):
        return
    if len(ops) > len(run.get("ops") or []):
        run["ops"] = ops
    run.pop("open", None)


def load_log(path: str) -> dict:
    data, _ = _read_log(path)
    _merge_journal(path, data)
    return data


def save_log(path: str, data: dict) -> None:
    """Every caller saves what load_log returned, journal steps included, so a
    journal is no longer needed once this succeeds."""
    tmp = path + ".part"
    try:
        with open(fs(tmp), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(fs(tmp), fs(path))
    except BaseException:
        _remove_quietly(tmp)  # a failed save leaves no .part behind
        raise
    _remove_quietly(journal_path(path))


class RunLog:
    """organize_log.json is saved before the first file moves: when it cannot be
    saved the run does not start, so nothing ever moves without a way back.

    During the run each step is appended to organize_log.json.journal (one JSON
    line, flushed at once) instead of rewriting the whole log; the steps go into
    organize_log.json when the run ends. If the program stops mid-run, the next
    load_log takes them from the journal."""

    def __init__(self, folder: str, source: str, mode: str) -> None:
        os.makedirs(fs(folder), exist_ok=True)
        self.path = log_path_for(folder)
        self.existed = os.path.exists(fs(self.path))
        self.data, broken = _read_log(self.path)
        if broken:
            # unreadable: keep it for the user instead of writing over it
            os.replace(fs(self.path), fs(f"{self.path}.broken-{time.strftime('%Y%m%d-%H%M%S')}"))
            self.existed = False
        _merge_journal(self.path, self.data)  # an earlier run that was cut short
        self.run = {"id": uuid.uuid4().hex[:12], "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "source": source, "dest": folder, "mode": mode, "ops": [], "undone": False, "open": True}
        self.data["runs"].append(self.run)
        self._pending = 0
        self._journal = None
        save_log(self.path, self.data)  # raises: the caller stops before touching any file
        try:
            self._journal = open(fs(journal_path(self.path)), "w", encoding="utf-8")
            self._write({"journal": {k: v for k, v in self.run.items() if k != "ops"}})
        except OSError:
            self.discard()
            raise

    def _write(self, obj: dict) -> None:
        try:
            self._journal.write(json.dumps(obj, ensure_ascii=False) + "\n")
            self._journal.flush()  # in the OS now: survives the program stopping
            self._pending += 1
            if self._pending >= FLUSH_EVERY:  # on the disk now: survives a power cut
                self._pending = 0
                os.fsync(self._journal.fileno())
        except (OSError, ValueError) as exc:  # ValueError: the journal was closed
            raise LogWriteError(getattr(exc, "errno", None) or errno.EIO, str(exc), journal_path(self.path)) from exc

    def add(self, **op) -> None:
        """Raises LogWriteError; the step stays in memory so flush() can still save it."""
        self.run["ops"].append(op)
        self._write(op)

    def _close_journal(self) -> None:
        if self._journal is not None:
            try:
                self._journal.close()
            except OSError:
                pass  # every line was flushed when written; the final save has them all anyway
            self._journal = None

    def flush(self) -> None:
        """End of the run: write every step into organize_log.json and drop the journal.
        Raises LogWriteError; the journal then stays and the next load_log merges it."""
        self._close_journal()
        self.run.pop("open", None)
        try:
            save_log(self.path, self.data)
        except OSError as exc:
            self.run["open"] = True
            raise LogWriteError(exc.errno, str(exc), self.path) from exc

    def discard(self) -> None:
        """Nothing happened: leave the log as it was before this run."""
        self._close_journal()
        self.data["runs"].remove(self.run)
        try:
            if self.existed:
                save_log(self.path, self.data)
            else:
                os.remove(fs(self.path))
                _remove_quietly(journal_path(self.path))
        except OSError:
            pass  # at worst an empty open run remains, which history and undo skip


# ------------------------------------------------------------------ file ops
def _same_device_error(exc: OSError) -> bool:
    return exc.errno == errno.EXDEV or getattr(exc, "winerror", None) == 17


def copy_verified(src: str, dst: str) -> None:
    """Copy with SHA-1 on the way, re-read the copy and compare. A mismatch removes the copy."""
    h_src = hashlib.sha1()
    tmp = dst + ".part"
    try:
        with open(fs(src), "rb") as fi, open(fs(tmp), "wb") as fo:
            while chunk := fi.read(CHUNK):
                h_src.update(chunk)
                fo.write(chunk)
        shutil.copystat(fs(src), fs(tmp))
        h_dst = hashlib.sha1()
        with open(fs(tmp), "rb") as f:
            while chunk := f.read(CHUNK):
                h_dst.update(chunk)
        if h_src.digest() != h_dst.digest():
            raise CopyMismatch(errno.EIO, "copy differs from the original", dst)
        os.replace(fs(tmp), fs(dst))
    except BaseException:
        _remove_quietly(tmp)
        raise


def move_file(src: str, dst: str) -> None:
    if os.path.lexists(fs(dst)) and os.path.normcase(src) != os.path.normcase(dst):
        raise FileExistsError(errno.EEXIST, "target exists", dst)
    if os.path.normcase(src) == os.path.normcase(dst) and src != dst:
        # only the case of the name changes: Windows needs a detour through another name
        tmp = f"{dst}.{uuid.uuid4().hex[:8]}.tmp"
        os.rename(fs(src), fs(tmp))
        os.rename(fs(tmp), fs(dst))
        return
    try:
        os.rename(fs(src), fs(dst))
        return
    except OSError as exc:
        if not _same_device_error(exc):
            raise
    copy_verified(src, dst)
    try:
        remove_file(src)
    except OSError:
        _remove_quietly(dst)  # the original is in use: keep it, drop the copy
        raise


def remove_file(path: str) -> None:
    """os.remove that also takes read-only files (the attribute is restored on failure)."""
    try:
        os.remove(fs(path))
        return
    except PermissionError:
        mode = os.stat(fs(path)).st_mode
        if mode & stat.S_IWRITE:
            raise  # not read-only: in use
    os.chmod(fs(path), stat.S_IWRITE)
    try:
        os.remove(fs(path))
    except OSError:
        os.chmod(fs(path), mode)
        raise


def copy_file(src: str, dst: str) -> None:
    if os.path.lexists(fs(dst)):
        raise FileExistsError(errno.EEXIST, "target exists", dst)
    copy_verified(src, dst)


def _remove_quietly(path: str) -> None:
    try:
        os.chmod(fs(path), stat.S_IWRITE)
        os.remove(fs(path))
    except OSError:
        pass


def trash(path: str) -> None:
    """Send to the recycle bin. Raises OSError when that is not possible."""
    if sys.platform != "win32":
        try:
            from send2trash import send2trash  # type: ignore
        except ImportError as exc:
            raise OSError(errno.ENOTSUP, "recycle bin not available") from exc
        send2trash(path)
        return
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                    ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]

    FO_DELETE, FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_NOERRORUI = 3, 0x4, 0x10, 0x40, 0x400
    op = SHFILEOPSTRUCTW()
    op.wFunc = FO_DELETE
    op.pFrom = os.path.abspath(path) + "\0"  # the field needs a double NUL; ctypes adds the second
    op.fFlags = FOF_SILENT | FOF_NOCONFIRMATION | FOF_ALLOWUNDO | FOF_NOERRORUI
    rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if rc != 0 or op.fAnyOperationsAborted or os.path.lexists(fs(path)):
        raise OSError(errno.EIO, f"recycle bin refused ({rc})", path)


def make_dirs(folder: str, log: RunLog | None) -> None:
    """Create missing folders one level at a time so each one is logged."""
    missing = []
    d = os.path.abspath(folder)
    while not os.path.isdir(fs(d)):
        missing.append(d)
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    for d in reversed(missing):
        os.mkdir(fs(d))
        if log is not None:
            log.add(op="mkdir", path=d)


def only_junk(folder: str) -> bool:
    try:
        with os.scandir(fs(folder)) as it:
            return all(e.is_file() and is_junk(e.name) for e in it)
    except OSError:
        return False


def remove_junk_folder(folder: str) -> bool:
    if not only_junk(folder):
        return False
    try:
        for name in os.listdir(fs(folder)):
            p = os.path.join(folder, name)
            os.chmod(fs(p), stat.S_IWRITE)
            os.remove(fs(p))
        os.rmdir(fs(folder))
        return True
    except OSError:
        return False


def reason_of(exc: BaseException) -> str:
    if isinstance(exc, FileExistsError):
        return i18n.t("err_target_exists")
    if isinstance(exc, PermissionError) or getattr(exc, "winerror", None) in (5, 32, 33):
        return i18n.t("err_in_use")
    if isinstance(exc, CopyMismatch):
        return i18n.t("err_copy_mismatch")
    if isinstance(exc, FileNotFoundError):
        return i18n.t("err_missing")
    return str(exc) or exc.__class__.__name__


# ------------------------------------------------------------------ run
def execute(plan: Plan, progress: ProgressFn | None = None, cancel: threading.Event | None = None) -> Result:
    opts = plan.options
    items = [i for i in plan.items if i.moves]
    companions = sorted(plan.companions, key=lambda c: 0 if c.op == "copy" else 1)  # copy a cover before moving it
    total = len(items) + len(companions) + len(plan.empty_dirs)
    res = Result()
    log = RunLog(opts.dest, opts.root, opts.mode)
    res.log_path = log.path
    failed_keys: set[str] = set()
    log_stopped = False
    step = 0

    def tick() -> None:
        nonlocal step
        step += 1
        if progress is not None:
            progress(step, total)

    try:
        for item in items:
            if cancel is not None and cancel.is_set():
                res.cancelled = True
                break
            try:
                if item.action == DUPE_TRASH:
                    trash(item.src)
                    op = dict(op="trash", src=item.src)
                    res.trashed += 1
                else:
                    if not os.path.lexists(fs(item.src)):  # gone since the preview: create no folders for it
                        raise FileNotFoundError(errno.ENOENT, "missing", item.src)
                    make_dirs(os.path.dirname(item.dst), log)
                    if item.action == COPY:
                        copy_file(item.src, item.dst)
                        st = os.stat(fs(item.dst))
                        op = dict(op="copy", src=item.src, dst=item.dst, size=st.st_size, mtime_ns=st.st_mtime_ns)
                    else:  # MOVE, DUPE_MOVE
                        move_file(item.src, item.dst)
                        op = dict(op="move", src=item.src, dst=item.dst)
                res.done += 1  # the file has moved: count it even if saving its log entry fails
                log.add(**op)
            except LogWriteError:
                log_stopped = True
                res.cancelled = True
                tick()
                break
            except OSError as exc:
                failed_keys.add(item.key)
                failed_keys.add("dir:" + key_of(os.path.dirname(item.src)))  # its album's extras stay too
                res.failed.append((item.src, reason_of(exc)))
            tick()
        if not res.cancelled:
            for c in companions:
                if cancel is not None and cancel.is_set():
                    res.cancelled = True
                    break
                if c.owner and c.owner in failed_keys:
                    tick()
                    continue
                try:
                    make_dirs(os.path.dirname(c.dst), log)
                    if c.op == "copy":
                        copy_file(c.src, c.dst)
                        st = os.stat(fs(c.dst))
                        log.add(op="copy", src=c.src, dst=c.dst, size=st.st_size, mtime_ns=st.st_mtime_ns)
                    else:
                        move_file(c.src, c.dst)
                        log.add(op="move", src=c.src, dst=c.dst)
                except LogWriteError:
                    log_stopped = True
                    res.cancelled = True
                    break
                except OSError as exc:
                    res.failed.append((c.src, reason_of(exc)))
                tick()
        if not res.cancelled:
            for folder in plan.empty_dirs:
                if remove_junk_folder(folder):
                    res.folders_removed += 1
                    try:
                        log.add(op="rmdir", path=folder)
                    except LogWriteError:
                        log_stopped = True
                        res.cancelled = True
                        break
                tick()
    finally:
        if log.run["ops"]:
            try:
                log.flush()  # every step so far is in memory, even one the journal refused
                if log_stopped:
                    res.failed.append((log.path, i18n.t("err_log_stopped")))
            except LogWriteError:
                # the journal still holds every step it took; the next load merges them
                res.failed.append((log.path, i18n.t("err_log_lost" if log_stopped else "err_log_pending")))
            i18n.update_settings(last_log=log.path)
        else:
            log.discard()
            res.log_path = ""
    return res


def write_untagged(paths: list[str], target: str) -> None:
    with open(target, "w", encoding="utf-8-sig", newline="\r\n") as f:
        for p in paths:
            f.write(p + "\n")


def key_in(path: str, folder: str) -> bool:
    k, f = key_of(path), key_of(folder)
    return k == f or k.startswith(f.rstrip(os.sep) + os.sep)
