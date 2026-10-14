"""Closing the window while the update check is still waiting for the server must not kill
the program. Before v0.4.3 the fetch thread handed its answer over with root.after(0, ...)
and so held the window and the App; when the window was already closed, the thread dropped
the last reference, Tk variables were freed off the main thread and Tcl aborted with
"Tcl_AsyncDelete: async handler deleted by the wrong thread" (LESSONS A35)."""
from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CLOSE_WHILE_ASKING = r"""
import functools, os, sys, tempfile, time
sys.path.insert(0, {root!r})
import i18n
i18n._settings_path = os.path.join(tempfile.mkdtemp(), "settings.json")
i18n.set_lang("en", persist=False)
import tkinter as tk
import gui, update_check as uc

def slow(slug, version):
    time.sleep(1.5)          # the request is still out when the window closes
    return {{"latest": "9.9.9", "updateAvailable": True, "url": "https://chaekgalpi.co.kr/tools/musicfolder"}}

gui.update_check.check_async = functools.partial(uc.check_async, fetcher=slow)
for _ in range(3):
    root = gui.TkinterDnD.Tk() if gui._HAS_DND else tk.Tk()
    app = gui.App(root, None)
    root.after(1700, root.destroy)           # 0.2 s into the request
    root.mainloop()
    del app, root
    time.sleep(2.0)          # the thread finishes with no window left
print("survived")
"""


def test_closing_while_the_request_is_out_does_not_abort_tcl(tmp_path):
    script = tmp_path / "close_while_asking.py"
    script.write_text(CLOSE_WHILE_ASKING.format(root=ROOT), encoding="utf-8")
    p = subprocess.run([sys.executable, "-W", "ignore", str(script)], capture_output=True, timeout=120)
    out = (p.stdout + p.stderr).decode("utf-8", "replace")
    assert p.returncode == 0 and "survived" in out and "Tcl_AsyncDelete" not in out, out[-800:]
