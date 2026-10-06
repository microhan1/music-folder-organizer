"""Drive the real tkinter window: dialogs are answered by the test."""
import json
import os
import shutil
import stat
import time
import unicodedata

import pytest

import i18n
from conftest import put, snapshot

tk = pytest.importorskip("tkinter")


@pytest.fixture
def dialogs(monkeypatch):
    from tkinter import filedialog, messagebox, simpledialog

    log = {"shown": [], "answer": True, "ask": None, "save": None, "dir": None}
    for name in ("showinfo", "showwarning", "showerror"):
        monkeypatch.setattr(messagebox, name, lambda title, msg, _n=name, **kw: log["shown"].append((_n, msg)))
    monkeypatch.setattr(messagebox, "askokcancel", lambda title, msg, **kw: (log["shown"].append(("ask", msg)), log["answer"])[1])
    monkeypatch.setattr(simpledialog, "askstring", lambda *a, **k: log["ask"])
    monkeypatch.setattr(filedialog, "asksaveasfilename", lambda **k: log["save"])
    monkeypatch.setattr(filedialog, "askdirectory", lambda **k: log["dir"])
    return log


@pytest.fixture(scope="module")
def root():
    """One Tk for the whole module: creating and destroying dozens of Tk
    interpreters in one process makes Tcl's init flaky on Windows."""
    import gui

    try:
        r = gui.TkinterDnD.Tk() if gui._HAS_DND else tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"no display: {exc}")
    yield r
    r.destroy()


@pytest.fixture
def app(root, tmp_path, dialogs):
    i18n.update_settings(artists_path=str(tmp_path / "artists.json"))
    import gui

    a = gui.App(root, None)
    a.pump = lambda sec=0.2: _pump(root, sec)
    a.wait = lambda: _wait(a)
    yield a
    a.cancel.set()
    _wait(a)
    a.frame.destroy()


def _pump(root, sec):
    end = time.time() + sec
    while time.time() < end:
        root.update()
        time.sleep(0.01)


def _idle(a) -> bool:
    """No job, and the plan on screen matches the settings (built on a worker thread)."""
    return not a.busy and not a.planning and a._plan_job is None and not a.filling


def _wait(a):
    for _ in range(600):
        _pump(a.root, 0.05)
        if _idle(a):
            _pump(a.root, 0.1)
            if _idle(a):
                return
    raise TimeoutError


@pytest.fixture
def lib(tmp_path):
    root = tmp_path / "music"
    put(root, "song-320.mp3", "dl/a.mp3", title="Alpha", artist="岡田 有希子", album="Fairy", tracknumber="1")
    put(root, "song-128.mp3", "dl/a-low.mp3", title="Alpha", artist="岡田有希子", album="Fairy", tracknumber="1")
    put(root, "other.mp3", "dl/sub/b.mp3", title="Beta", artist="岡田有希子", album="Fairy", tracknumber="2")
    put(root, "tone.wav", "misc/noise.wav")
    shutil.copyfile(root / "dl" / "sub" / "b.mp3", root / "misc" / "b copy.mp3")
    return root


def test_load_preview_and_summary(app, lib):
    app.load_source(str(lib))
    app.wait()
    assert app.plan is not None and len(app.tree.get_children()) == 5
    s = app.plan.summary()
    assert s["dupes"] == 2  # the identical copy, and the 128k copy of the same album's song
    assert app.lbl_summary.cget("text").startswith(f"Move {s['move']}")
    assert str(app.btn_run.cget("state")) == "normal"
    assert len(app.dtree.get_children()) == 2  # identical pair + same song (128k vs 320k)
    assert app.atree.get_children()  # 岡田 有希子 / 岡田有希子 merged


def test_real_click_on_checkbox_column(app, lib):
    app.load_source(str(lib))
    app.wait()
    app.notebook.select(0)
    app.pump(0.3)
    first = app.tree.get_children()[0]
    x, y, w, h = app.tree.bbox(first, "check")
    app.tree.event_generate("<Button-1>", x=x + w // 2, y=y + h // 2)
    app.wait()  # the plan is rebuilt on a worker thread
    item = next(i for i in app.plan.items if i.key == first)
    assert not item.checked and app.tree.set(first, "check") == "☐"
    app._toggle_all()
    app.wait()
    assert all(i.checked for i in app.plan.items)
    app._toggle_all()
    app.wait()
    assert not any(i.checked for i in app.plan.items)
    assert str(app.btn_run.cget("state")) == "disabled" or app.plan.summary()["folders"] == 0


def test_stage_two_group_apply_and_keep_rules(app, lib, dialogs):
    app.load_source(str(lib))
    app.wait()
    g2 = next(g for g in app.groups if g.stage == 2)
    assert g2.applied and not g2.cross_album  # same album: applied by default
    before = app.plan.summary()["dupes"]
    g2.applied = False
    app._replan()
    app.wait()
    assert app.plan.summary()["dupes"] == before - 1
    g2.applied = True
    g2.keep.clear()
    app._replan()
    app.wait()
    app._run()  # refused: an applied group keeps nothing
    assert dialogs["shown"][-1][0] == "showwarning" and not app.busy
    assert (lib / "dl" / "a.mp3").exists()


def test_invalid_pattern_and_copy_in_place(app, lib):
    app.load_source(str(lib))
    app.wait()
    app.var_pattern.set("{nope}")
    app.pump(0.5)
    assert app.plan is None and str(app.btn_run.cget("state")) == "disabled"
    app.var_pattern.set("{artist}/{title}")
    app.pump(0.5)
    assert app.plan is not None and all(i.dst.count(os.sep) - str(lib).count(os.sep) == 2 for i in app.plan.items if i.moves and i.status != "dup")
    app.var_mode.set("copy")
    app._options_changed()
    app.pump(0.5)
    assert app.plan is None and "destination" in app.lbl_summary.cget("text")


def test_pattern_choice_is_remembered(app, lib):
    app.var_pattern.set("{year}/{artist} - {title}")
    app._options_changed()
    assert i18n.load_settings()["pattern"] == "{year}/{artist} - {title}"


def test_other_destination_inside_source(app, lib, dialogs):
    app.load_source(str(lib))
    app.wait()
    dialogs["dir"] = str(lib / "sorted")
    os.makedirs(lib / "sorted")
    app._pick_dest()
    app.wait()
    assert app.dest() == str(lib / "sorted")
    assert all(i.dst.startswith(str(lib / "sorted")) for i in app.plan.items if i.moves)


def test_cancelled_destination_dialog_falls_back(app, lib, dialogs):
    app.load_source(str(lib))
    app.wait()
    dialogs["dir"] = ""
    app.var_where.set("other")
    app._dest_changed()
    assert app.var_where.get() == "inplace"


def test_run_then_undo(app, lib, dialogs):
    before = snapshot(lib)
    app.load_source(str(lib))
    app.wait()
    app._run()
    app.wait()
    app.wait()
    assert dialogs["shown"][-1][0] == "showinfo" and "Done" in dialogs["shown"][-1][1]
    assert (lib / "岡田有希子" / "Fairy" / "02 - Beta.mp3").exists()  # the spelling on more files
    assert app.plan.summary()["move"] == 0  # rescanned: all in place now
    app._undo()
    app.wait()
    app.wait()
    os.remove(lib / "organize_log.json")
    assert snapshot(lib) == before
    app._undo()
    assert "Nothing to undo" in dialogs["shown"][-1][1]


def test_undo_with_unwritable_log_shows_error(app, lib, dialogs):
    app.load_source(str(lib))
    app.wait()
    app._run()
    app.wait()
    app.wait()
    organized = snapshot(lib)
    log = lib / "organize_log.json"
    os.chmod(log, stat.S_IREAD)
    try:
        app._undo()
        app.wait()
        app.wait()
    finally:
        os.chmod(log, stat.S_IREAD | stat.S_IWRITE)
    assert dialogs["shown"][-1][0] == "showerror" and "Nothing was undone" in dialogs["shown"][-1][1]
    assert snapshot(lib) == organized


def test_run_declined_changes_nothing(app, lib, dialogs):
    before = snapshot(lib)
    app.load_source(str(lib))
    app.wait()
    dialogs["answer"] = False
    app._run()
    app.pump(0.3)
    assert snapshot(lib) == before


def test_run_with_unwritable_log_shows_error(app, lib, dialogs, tmp_path):
    before = snapshot(lib)
    (tmp_path / "blocker").write_bytes(b"x")
    dialogs["dir"] = str(tmp_path / "blocker" / "out")
    app.load_source(str(lib))
    app.wait()
    app.dest_other = str(tmp_path / "blocker" / "out")
    app.var_where.set("other")
    app._replan()
    app.wait()
    app._run()
    app.wait()
    assert dialogs["shown"][-1][0] == "showerror" and "Nothing was moved" in dialogs["shown"][-1][1]
    assert snapshot(lib) == before


def test_artist_rename_ungroup_and_guess(app, lib, dialogs, tmp_path):
    put(lib, "tone.ogg", "album/1.ogg", title="One", artist="Yukiko Okada", album="Kiss", tracknumber="1")
    put(lib, "tone.ogg", "album/2.ogg", title="Two", artist="오카다 유키코", album="Kiss", tracknumber="2")
    app.load_source(str(lib))
    app.wait()
    rows = app.atree.get_children()
    guess_row = next(r for r in rows if r.startswith("q"))
    cluster_row = next(r for r in rows if r.startswith("c"))
    # confirm the guess -> artists.json joins the two spellings
    app.atree.selection_set(guess_row)
    app._answer_guess(True)
    aliases = json.load(open(tmp_path / "artists.json", encoding="utf-8"))
    assert any({"Yukiko Okada", "오카다 유키코"} <= {k, *v} for k, v in aliases.items())
    assert app.index.rep("Yukiko Okada") == app.index.rep("오카다 유키코")
    # rename the auto-merged 岡田 group
    cluster_row = next(r for r in app.atree.get_children() if r.startswith("c") and "岡田" in app.atree.item(r, "text"))
    app.atree.selection_set(cluster_row)
    dialogs["ask"] = "岡田有希子"
    app._rename_rep()
    assert app.index.rep("岡田 有希子") == "岡田有希子"
    app.wait()
    assert any(i.dst.split(os.sep)[-3] == "岡田有希子" for i in app.plan.items if i.moves and i.status != "dup")
    # split it again
    cluster_row = next(r for r in app.atree.get_children() if r.startswith("c") and "岡田" in app.atree.item(r, "text"))
    app.atree.selection_set(cluster_row)
    app._ungroup()
    assert app.index.rep("岡田 有希子") != app.index.rep("岡田有希子")


def test_guess_rejected_is_not_asked_again(app, lib):
    put(lib, "tone.ogg", "album/1.ogg", title="One", artist="Yukiko Okada", album="Kiss", tracknumber="1")
    put(lib, "tone.ogg", "album/2.ogg", title="Two", artist="오카다 유키코", album="Kiss", tracknumber="2")
    app.load_source(str(lib))
    app.wait()
    row = next(r for r in app.atree.get_children() if r.startswith("q"))
    app.atree.selection_set(row)
    app._answer_guess(False)
    assert not [r for r in app.atree.get_children() if r.startswith("q")]
    assert i18n.load_settings()["artist_rejected"]


def test_language_switch_keeps_state(app, lib):
    app.load_source(str(lib))
    app.wait()
    key = app.plan.items[0].key
    app._set_checked([key], False)
    for code in ("ko", "zh-CN", "ja", "en"):
        app.var_lang.set(i18n.LANG_NAMES[code])
        app._on_lang()
        app.pump(0.1)
        assert app.root.title() == i18n.load_lang_file(code)["app_title"]
        assert not next(i for i in app.plan.items if i.key == key).checked
        assert len(app.tree.get_children()) == 5
    assert i18n.load_settings()["lang"] == "en"


def test_export_untagged(app, lib, dialogs, tmp_path):
    app.load_source(str(lib))
    app.wait()
    dialogs["save"] = str(tmp_path / "list.txt")
    app._export_untagged()
    assert (tmp_path / "list.txt").read_text(encoding="utf-8-sig").splitlines() == [str(lib / "misc" / "noise.wav")]


def test_drop_a_file_loads_its_folder(app, lib):
    class Ev:
        data = "{" + str(lib / "misc" / "noise.wav") + "}"

    app._on_drop(Ev())
    app.wait()
    assert app.source == str(lib / "misc")


def test_busy_ignores_new_source(app, lib, tmp_path):
    app.busy = True
    app.load_source(str(tmp_path))
    assert app.source == ""
    app.busy = False


@pytest.mark.skipif(os.name != "nt", reason="NTFS keeps NFC and NFD names apart")
def test_nfc_nfd_twins_do_not_break_the_table(app, tmp_path):
    root = tmp_path / "twins"
    put(root, "song-128.mp3", unicodedata.normalize("NFC", "노래.mp3"), title="노래", artist="가수")
    put(root, "other.mp3", unicodedata.normalize("NFD", "노래.mp3"), title="노래", artist="가수")
    if len(os.listdir(root)) != 2:
        pytest.skip("names merged by the file system")
    app.load_source(str(root))
    app.wait()
    assert len(app.tree.get_children()) == 2


def test_fingerprint_button(app, lib, dialogs):
    import dedupe

    if not dedupe.find_fpcalc():
        pytest.skip("fpcalc not available")
    app.load_source(str(lib))
    app.wait()
    app.var_fp.set(True)
    app._fp_estimate()
    assert app.lbl_fp.cget("text")
    app._find_dupes()
    app.wait()
    assert "duplicate groups" in app.lbl_status.cget("text")


# ------------------------------------------------------------------ v0.2.0 item 3: music-tag-filler hand-off
class FakeProc:
    def __init__(self, cmd, cwd=None):
        self.cmd, self.cwd, self.done = cmd, cwd, False

    def poll(self):
        return 0 if self.done else None


@pytest.fixture
def launched(monkeypatch):
    import gui

    procs = []
    monkeypatch.setattr(gui.subprocess, "Popen", lambda cmd, cwd=None: procs.append(FakeProc(cmd, cwd)) or procs[-1])
    return procs


def test_tag_filler_gets_only_files_it_can_handle(app, lib, launched, tmp_path):
    put(lib, "song-128.mp3", "misc/untagged.mp3")  # no tags, mp3: goes over
    exe = tmp_path / "tools" / "music-tag-filler.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"")
    app.prefs.tag_filler_path = str(exe)
    app.load_source(str(lib))
    app.wait()
    assert app.btn_tag_filler.winfo_manager() == "pack"
    app._open_tag_filler()
    assert len(launched) == 1
    cmd = launched[0].cmd
    assert cmd[0] == str(exe) and cmd[1:] == [str(lib / "misc" / "untagged.mp3")]  # noise.wav is left out
    assert "1" in app.lbl_status.cget("text") and "wav" in app.lbl_status.cget("text")
    # the window closes: tags are read again
    launched[0].done = True
    app.pump(0.4)
    app.wait()
    assert app._filler is None and "Music Tag Filler closed" in app.lbl_status.cget("text")


def test_tag_filler_button_hidden_without_untagged_files(app, tmp_path):
    root = tmp_path / "tagged"
    put(root, "song-128.mp3", "a.mp3", title="T", artist="A", album="B", tracknumber="1")
    app.load_source(str(root))
    app.wait()
    assert app.btn_tag_filler.winfo_manager() == ""


def test_tag_filler_is_asked_for_once(app, lib, launched, dialogs, tmp_path, monkeypatch):
    import gui

    put(lib, "song-128.mp3", "misc/untagged.mp3")
    monkeypatch.setattr(gui.i18n, "app_dir", lambda: str(tmp_path / "nowhere" / "app"))
    monkeypatch.setattr(gui.filedialog, "askopenfilename", lambda **k: str(tmp_path / "picked.exe"))
    (tmp_path / "picked.exe").write_bytes(b"")
    app.prefs.tag_filler_path = ""
    app.load_source(str(lib))
    app.wait()
    app._open_tag_filler()
    assert launched[0].cmd[0] == str(tmp_path / "picked.exe")
    assert i18n.load_settings()["tag_filler_path"] == str(tmp_path / "picked.exe")


def test_tag_filler_dialog_cancelled(app, lib, launched, tmp_path, monkeypatch):
    import gui

    put(lib, "song-128.mp3", "misc/untagged.mp3")
    monkeypatch.setattr(gui.i18n, "app_dir", lambda: str(tmp_path / "nowhere" / "app"))
    monkeypatch.setattr(gui.filedialog, "askopenfilename", lambda **k: "")
    app.prefs.tag_filler_path = ""
    app.load_source(str(lib))
    app.wait()
    app._open_tag_filler()
    assert launched == []


def test_tag_filler_long_list_passes_folders(app, lib, launched, tmp_path, monkeypatch):
    import gui

    for i in range(3):
        put(lib, "song-128.mp3", f"raw{i}/u.mp3")
    exe = tmp_path / "music-tag-filler.exe"
    exe.write_bytes(b"")
    app.prefs.tag_filler_path = str(exe)
    monkeypatch.setattr(gui, "CMDLINE_LIMIT", 10)
    app.load_source(str(lib))
    app.wait()
    app._open_tag_filler()
    assert sorted(launched[0].cmd[1:]) == [str(lib / f"raw{i}") for i in range(3)]


def test_tag_filler_nothing_to_send(app, lib, launched, dialogs):
    app.load_source(str(lib))  # only noise.wav lacks tags
    app.wait()
    app._open_tag_filler()
    assert launched == [] and "mp3" in dialogs["shown"][-1][1]


def test_tag_filler_launch_failure(app, lib, dialogs, tmp_path, monkeypatch):
    import gui

    def boom(cmd, cwd=None):
        raise OSError(2, "not found")

    put(lib, "song-128.mp3", "misc/untagged.mp3")
    exe = tmp_path / "music-tag-filler.exe"
    exe.write_bytes(b"")
    app.prefs.tag_filler_path = str(exe)
    monkeypatch.setattr(gui.subprocess, "Popen", boom)
    app.load_source(str(lib))
    app.wait()
    app._open_tag_filler()
    assert dialogs["shown"][-1][0] == "showerror" and app._filler is None


# ------------------------------------------------------------------ v0.3.0 item 4: undo history window
def _two_runs(app, root):
    """Run 1 puts in/x.mp3 at A/X.mp3; run 2 moves it on to A/B/01 - X.mp3 (so run 1 is blocked)."""
    import mover
    from conftest import build

    put(root, "song-128.mp3", "in/x.mp3", title="X", artist="A", album="B", tracknumber="1")
    before = snapshot(root)
    mover.execute(build(root, pattern="{artist}/{title}")[1])
    time.sleep(1.1)
    mover.execute(build(root)[1])
    return before


def test_history_lists_runs_and_blocks_the_older_one(app, tmp_path, dialogs):
    root = tmp_path / "h"
    before = _two_runs(app, root)
    app.load_source(str(root))
    app.wait()
    app._show_history()
    app.pump(0.2)
    assert len(app.htree.get_children()) == 2
    assert app.htree.selection() == ("0",)  # the newest undoable run is preselected
    assert "Can be undone" in app.htree.set("0", "state")
    assert "moved 1" in app.lbl_history_detail.cget("text")
    app.htree.selection_set("1")
    app.pump(0.1)
    assert "Undo that run first" in app.lbl_history_detail.cget("text")
    assert str(app.btn_undo_selected.cget("state")) == "disabled"
    # undo the newest from the window, then the older one becomes possible
    app.htree.selection_set("0")
    app.pump(0.1)
    app._undo_selected()
    app.wait()
    app.wait()
    assert "Undone (" in app.htree.set("0", "state")
    app.htree.selection_set("1")
    app.pump(0.1)
    assert str(app.btn_undo_selected.cget("state")) == "normal"
    app._undo_selected()
    app.wait()
    app.wait()
    import os
    os.remove(root / "organize_log.json")
    assert snapshot(root) == before
    app.history.destroy()


def test_history_empty(app, tmp_path):
    app._show_history()
    app.pump(0.1)
    assert app.htree.get_children() == () or all("Undone" in app.htree.set(i, "state") for i in app.htree.get_children())
    app.history.destroy()


def test_history_closes_on_language_switch(app, tmp_path):
    app._show_history()
    app.pump(0.1)
    app.var_lang.set(i18n.LANG_NAMES["ko"])
    app._on_lang()
    assert not app.history.winfo_exists()
    app.var_lang.set(i18n.LANG_NAMES["en"])
    app._on_lang()


def test_quick_undo_respects_blockers(app, tmp_path, dialogs):
    root = tmp_path / "q"
    _two_runs(app, root)
    app.load_source(str(root))
    app.wait()
    app._undo()  # the newest: allowed
    app.wait()
    app.wait()
    assert "Undo finished" in dialogs["shown"][-1][1]


# ------------------------------------------------------------------ v0.3.0 item 5: pattern helper
def test_placeholder_button_inserts_at_cursor(app):
    app.var_pattern.set("{artist}/ - {title}")
    app.pattern_box.icursor(9)  # right after "{artist}/"
    app._insert_placeholder("{album}")
    assert app.var_pattern.get() == "{artist}/{album} - {title}"
    app.pattern_box.selection_range(9, 16)  # "{album}" selected: replaced
    app._insert_placeholder("{year}")
    assert app.var_pattern.get() == "{artist}/{year} - {title}"


def test_example_without_a_folder_uses_a_sample(app):
    app.var_pattern.set("{artist}/{album}/{track:02} - {title}")
    app.pump(0.5)
    assert "Artist/Album/03 - Title.mp3" in app.lbl_example.cget("text")
    app.var_pattern.set("{oops}")
    app.pump(0.5)
    assert "—" in app.lbl_example.cget("text")


def test_example_follows_the_selected_row(app, lib):
    app.var_pattern.set(prefs_default())
    app.load_source(str(lib))
    app.wait()
    first = app.lbl_example.cget("text")
    rows = app.tree.get_children()
    app.tree.selection_set(rows[-1])
    app.pump(0.2)
    item = next(i for i in app.plan.items if i.key == rows[-1])
    assert os.path.basename(item.src) in app.lbl_example.cget("text") and app.lbl_example.cget("text") != first
    app.var_pattern.set("{title}")
    app.pump(0.6)
    assert app.lbl_example.cget("text").split("→")[1].strip().startswith(os.path.splitext(os.path.basename(item.dst))[0])


def prefs_default():
    import prefs

    return prefs.DEFAULT_PATTERN


# ------------------------------------------------------------------ v0.3.0 item 6: settings window
def test_settings_save_changes_the_preview(app, lib, dialogs):
    app.load_source(str(lib))
    app.wait()
    app._show_settings()
    app.pump(0.1)
    app.set_vars["fallback_artist"].set("Nobody Knows")
    app._save_settings()
    app.wait()
    assert i18n.load_settings()["fallbacks"] == {"artist": "Nobody Knows"}
    app.var_untagged.set(True)
    app._options_changed()
    app.pump(0.5)
    item = next(i for i in app.plan.items if i.src.endswith("noise.wav"))
    assert os.path.relpath(item.dst, lib).split(os.sep)[0] == "Nobody Knows"


def test_settings_reject_bad_values(app, dialogs, tmp_path):
    app._show_settings()
    app.pump(0.1)
    app.set_vars["dupes_folder"].set("bad/name")
    app.set_vars["fpcalc_path"].set(str(tmp_path / "missing.exe"))
    app._save_settings()
    assert dialogs["shown"][-1][0] == "showerror"
    assert "Duplicates folder name" in dialogs["shown"][-1][1] and "fpcalc" in dialogs["shown"][-1][1]
    assert app.settings_win.winfo_exists()  # stays open to fix
    assert "dupes_folder" not in i18n.load_settings() or i18n.load_settings()["dupes_folder"] == ""
    app._reset_settings()
    assert all(v.get() == "" for v in app.set_vars.values())
    app.settings_win.destroy()


def test_settings_rename_dupes_folder_updates_the_button(app, lib):
    app._show_settings()
    app.pump(0.1)
    app.set_vars["dupes_folder"].set("Doubles")
    app._save_settings()
    app.pump(0.2)
    assert app.prefs.dupes_name() == "Doubles"
    texts = []

    def walk(w):
        for c in w.winfo_children():
            try:
                texts.append(str(c.cget("text")))
            except Exception:
                pass
            walk(c)

    walk(app.frame)
    assert any("Doubles" in s for s in texts)


def test_settings_closes_on_language_switch(app):
    app._show_settings()
    app.pump(0.1)
    app.var_lang.set(i18n.LANG_NAMES["ja"])
    app._on_lang()
    assert not app.settings_win.winfo_exists()
    app.var_lang.set(i18n.LANG_NAMES["en"])
    app._on_lang()


# ------------------------------------------------------------------ v0.4.0 item 2: the plan is built off the UI thread
@pytest.fixture
def slow_build(monkeypatch):
    """plan.build that takes a while, as with 10,000 files, and counts its calls."""
    import plan as plan_mod

    real, calls = plan_mod.build, []

    def build(scan, opts, *a, **kw):
        calls.append(opts.pattern)
        time.sleep(0.4)
        return real(scan, opts, *a, **kw)

    monkeypatch.setattr(plan_mod, "build", build)
    return calls


def test_window_stays_responsive_while_planning(app, lib, slow_build):
    app.load_source(str(lib))
    app.wait()
    t0 = time.perf_counter()
    app._replan()
    assert time.perf_counter() - t0 < 0.2 and app.planning  # returned at once; the worker builds
    t0 = time.perf_counter()
    app.root.update()
    assert time.perf_counter() - t0 < 0.2
    assert str(app.btn_run.cget("state")) == "disabled"  # the plan on screen is about to change
    app.wait()
    assert not app.planning and app.plan is not None and str(app.btn_run.cget("state")) == "normal"


def test_run_never_uses_a_plan_older_than_the_settings(app, lib, dialogs, slow_build):
    before = snapshot(lib)
    app.load_source(str(lib))
    app.wait()
    app.var_pattern.set("{artist}/{title}")  # re-plans after 250 ms, then 0.4 s on the worker
    app._run()
    app.pump(0.1)
    app._run()
    assert not dialogs["shown"] and snapshot(lib) == before  # refused both times, silently
    app.wait()
    app._run()
    app.wait()
    app.wait()
    assert (lib / "岡田有希子" / "Beta.mp3").exists()  # the new pattern, not the old one


def test_rapid_changes_build_only_the_newest(app, lib, slow_build):
    app.load_source(str(lib))
    app.wait()
    slow_build.clear()
    for pattern in ("{artist}/{title}", "{album}/{title}", "{year}/{title}", "{genre}/{title}", "{artist} - {title}"):
        app.var_pattern.set(pattern)
        app._replan()
    app.wait()
    assert slow_build[0] == "{artist}/{title}" and slow_build[-1] == "{artist} - {title}"
    assert len(slow_build) == 2  # the first one in flight, then the newest; the ones between never ran
    assert app.plan.options.pattern == "{artist} - {title}"


def test_a_plan_for_the_old_folder_is_dropped(app, lib, tmp_path, slow_build):
    other = tmp_path / "other"
    put(other, "tone.ogg", "x/1.ogg", title="Solo", artist="Someone", album="Alone", tracknumber="1")
    app.load_source(str(lib))
    app.wait()
    app._replan()  # still building for lib ...
    assert app.planning and not app.busy  # a folder can be opened while a plan is being built
    app.load_source(str(other))
    app.wait()
    assert app.plan is not None and all(i.src.startswith(str(other)) for i in app.plan.items)


def test_example_follows_the_typed_pattern_while_the_plan_is_built(app, lib, slow_build):
    from scan import key_of

    app.load_source(str(lib))
    app.wait()
    app.tree.selection_set(key_of(str(lib / "dl" / "sub" / "b.mp3")))  # not a duplicate
    app.var_pattern.set("{album}/{title}")
    app.pump(0.35)  # past the 250 ms wait; the worker is still building
    assert app.planning
    assert "Fairy/Beta.mp3" in app.lbl_example.cget("text")  # not the old "…/02 - Beta.mp3"
    app.wait()
    assert "Fairy/Beta.mp3" in app.lbl_example.cget("text")


# ------------------------------------------------------------------ v0.4.0 item 3: rescans reuse unchanged tags
def test_rescan_after_run_and_undo_opens_no_file(app, lib, dialogs, monkeypatch):
    import scan as scan_mod

    real, opened = scan_mod._open, []
    monkeypatch.setattr(scan_mod, "_open", lambda path, fmt: (opened.append(path), real(path, fmt))[1])
    app.load_source(str(lib))
    app.wait()
    n = len(app.scan.tracks)
    assert len(opened) == n
    opened.clear()
    app._run()
    app.wait()
    app.wait()
    assert app.plan.summary()["move"] == 0  # rescanned after the run: all in place
    assert len(app.scan.tracks) < n  # the duplicates went to the dupes folder, which scans skip
    assert opened == [] and app.tag_cache.hits == len(app.scan.tracks)
    app._undo()
    app.wait()
    app.wait()
    assert len(app.scan.tracks) == n  # back, duplicates included
    assert opened == [] and app.tag_cache.hits == n
