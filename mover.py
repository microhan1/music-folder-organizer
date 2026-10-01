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
from scan import JUNK_NAMES, LOG_NAME, key_of

FLUSH_EVERY = 50
CHUNK = 1024 * 1024
ProgressFn = Callable[[int, int], None]


class CopyMismatch(OSError):
    pass


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


def load_log(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {"version": 1, "runs": []}
    if not isinstance(data, dict) or not isinstance(data.get("runs"), list):
        return {"version": 1, "runs": []}
    return data


def save_log(path: str, data: dict) -> None:
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


class RunLog:
    """Written once before the first file moves: when the log cannot be saved
    the run does not start, so nothing ever moves without a way back."""

    def __init__(self, folder: str, source: str, mode: str) -> None:
        os.makedirs(folder, exist_ok=True)
        self.path = log_path_for(folder)
        self.existed = os.path.exists(self.path)
        self.data = load_log(self.path)
        if self.existed and not self.data["runs"] and os.path.getsize(self.path) > 0:
            # unreadable: keep it for the user instead of writing over it
            os.replace(self.path, f"{self.path}.broken-{time.strftime('%Y%m%d-%H%M%S')}")
            self.existed = False
        self.run = {"id": uuid.uuid4().hex[:12], "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "source": source, "dest": folder, "mode": mode, "ops": [], "undone": False}
        self.data["runs"].append(self.run)
        self._pending = 0
        save_log(self.path, self.data)  # raises: the caller stops before touching any file

    def add(self, **op) -> None:
        self.run["ops"].append(op)
        self._pending += 1
        if self._pending >= FLUSH_EVERY:
            self.flush()

    def flush(self) -> None:
        self._pending = 0
        try:
            save_log(self.path, self.data)
        except OSError:
            pass

    def discard(self) -> None:
        """Nothing happened: leave the log as it was before this run."""
        self.data["runs"].remove(self.run)
        try:
            if self.existed:
                save_log(self.path, self.data)
            else:
                os.remove(self.path)
        except OSError:
            pass


# ------------------------------------------------------------------ file ops
def _same_device_error(exc: OSError) -> bool:
    return exc.errno == errno.EXDEV or getattr(exc, "winerror", None) == 17


def copy_verified(src: str, dst: str) -> None:
    """Copy with SHA-1 on the way, re-read the copy and compare. A mismatch removes the copy."""
    h_src = hashlib.sha1()
    tmp = dst + ".part"
    try:
        with open(src, "rb") as fi, open(tmp, "wb") as fo:
            while chunk := fi.read(CHUNK):
                h_src.update(chunk)
                fo.write(chunk)
        shutil.copystat(src, tmp)
        h_dst = hashlib.sha1()
        with open(tmp, "rb") as f:
            while chunk := f.read(CHUNK):
                h_dst.update(chunk)
        if h_src.digest() != h_dst.digest():
            raise CopyMismatch(errno.EIO, "copy differs from the original", dst)
        os.replace(tmp, dst)
    except BaseException:
        _remove_quietly(tmp)
        raise


def move_file(src: str, dst: str) -> None:
    if os.path.lexists(dst) and os.path.normcase(src) != os.path.normcase(dst):
        raise FileExistsError(errno.EEXIST, "target exists", dst)
    if os.path.normcase(src) == os.path.normcase(dst) and src != dst:
        # only the case of the name changes: Windows needs a detour through another name
        tmp = f"{dst}.{uuid.uuid4().hex[:8]}.tmp"
        os.rename(src, tmp)
        os.rename(tmp, dst)
        return
    try:
        os.rename(src, dst)
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
        os.remove(path)
        return
    except PermissionError:
        mode = os.stat(path).st_mode
        if mode & stat.S_IWRITE:
            raise  # not read-only: in use
    os.chmod(path, stat.S_IWRITE)
    try:
        os.remove(path)
    except OSError:
        os.chmod(path, mode)
        raise


def copy_file(src: str, dst: str) -> None:
    if os.path.lexists(dst):
        raise FileExistsError(errno.EEXIST, "target exists", dst)
    copy_verified(src, dst)


def _remove_quietly(path: str) -> None:
    try:
        os.chmod(path, stat.S_IWRITE)
        os.remove(path)
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
    if rc != 0 or op.fAnyOperationsAborted or os.path.lexists(path):
        raise OSError(errno.EIO, f"recycle bin refused ({rc})", path)


def make_dirs(folder: str, log: RunLog | None) -> None:
    """Create missing folders one level at a time so each one is logged."""
    missing = []
    d = os.path.abspath(folder)
    while not os.path.isdir(d):
        missing.append(d)
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    for d in reversed(missing):
        os.mkdir(d)
        if log is not None:
            log.add(op="mkdir", path=d)


def only_junk(folder: str) -> bool:
    try:
        with os.scandir(folder) as it:
            return all(e.is_file() and e.name.lower() in JUNK_NAMES for e in it)
    except OSError:
        return False


def remove_junk_folder(folder: str) -> bool:
    if not only_junk(folder):
        return False
    try:
        for name in os.listdir(folder):
            p = os.path.join(folder, name)
            os.chmod(p, stat.S_IWRITE)
            os.remove(p)
        os.rmdir(folder)
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
                    log.add(op="trash", src=item.src)
                    res.trashed += 1
                else:
                    if not os.path.lexists(item.src):  # gone since the preview: create no folders for it
                        raise FileNotFoundError(errno.ENOENT, "missing", item.src)
                    make_dirs(os.path.dirname(item.dst), log)
                    if item.action == COPY:
                        copy_file(item.src, item.dst)
                        st = os.stat(item.dst)
                        log.add(op="copy", src=item.src, dst=item.dst, size=st.st_size, mtime_ns=st.st_mtime_ns)
                    elif item.action in (MOVE, DUPE_MOVE):
                        move_file(item.src, item.dst)
                        log.add(op="move", src=item.src, dst=item.dst)
                res.done += 1
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
                        st = os.stat(c.dst)
                        log.add(op="copy", src=c.src, dst=c.dst, size=st.st_size, mtime_ns=st.st_mtime_ns)
                    else:
                        move_file(c.src, c.dst)
                        log.add(op="move", src=c.src, dst=c.dst)
                except OSError as exc:
                    res.failed.append((c.src, reason_of(exc)))
                tick()
        if not res.cancelled:
            for folder in plan.empty_dirs:
                if remove_junk_folder(folder):
                    log.add(op="rmdir", path=folder)
                    res.folders_removed += 1
                tick()
    finally:
        if log.run["ops"]:
            log.flush()
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
