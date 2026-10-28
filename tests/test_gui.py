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


def test_window_size_grows_with_the_screen_scaling(root, monkeypatch):
    """At 150 % scaling (144 dpi) the window is 1.5x bigger, never bigger than the screen."""
    from gui import window_size

    monkeypatch.setattr(root, "winfo_screenwidth", lambda: 3840)
    monkeypatch.setattr(root, "winfo_screenheight", lambda: 2160)
    monkeypatch.setattr(root, "winfo_fpixels", lambda _u: 96.0)
    assert window_size(root) == (1280, 1000, 1000, 840)
    monkeypatch.setattr(root, "winfo_fpixels", lambda _u: 144.0)
    assert window_size(root) == (1920, 1500, 1500, 1260)
    monkeypatch.setattr(root, "winfo_screenwidth", lambda: 1600)  # a small screen: nothing exceeds it
    monkeypatch.setattr(root, "winfo_screenheight", lambda: 900)
    w, h, min_w, min_h = window_size(root)
    assert w <= 1600 and h <= 900 - 135 and min_w <= 1600 and min_h <= 900 - 135


# ------------------------------------------------------------------ v0.4.0 item 4: duplicates tab shows what needs clearing
def test_dupes_tab_hides_groups_where_every_album_keeps_its_copy(app, lib):
    from scan import key_of

    put(lib, "tone.ogg", "orig/s.ogg", title="Sweet", artist="Someone", album="Original", tracknumber="1")
    put(lib, "tone.ogg", "best/s.ogg", title="Sweet", artist="Someone", album="Best Of", tracknumber="9")
    app.load_source(str(lib))
    app.wait()
    cross = [g for g in app.groups if not g.has_extras]
    shown = [r for r in app.dtree.get_children()]
    assert cross and all(f"g{g.id}" not in shown for g in cross)
    assert len(shown) == len(app.groups) - len(cross)
    assert str(len(cross)) in app.lbl_dupes_shown.cget("text") and "hidden" in app.lbl_dupes_shown.cget("text")
    app.var_dupes_all.set(True)
    app._fill_dupes()
    assert len(app.dtree.get_children()) == len(app.groups)
    assert app.lbl_dupes_shown.cget("text") == f"All {len(app.groups)} groups shown"
    app.var_dupes_all.set(False)
    app._fill_dupes()
    first = next(g for g in app.groups if g.has_extras)
    first.keep = {key_of(m.path) for m in first.members}  # keep everything: the group stays listed
    app._replan()
    app.wait()
    assert f"g{first.id}" in app.dtree.get_children()


# ------------------------------------------------------------------ update check notice (tool-update-check)
def _info(latest="0.4.3"):
    import update_check

    return update_check.UpdateInfo(latest, "0.4.2", "https://chaekgalpi.co.kr/tools/musicfolder?utm_source=app")


def _boxes(app):
    """Screen y and height of the header card, the tabs, the action bar and the notice."""
    app.pump(0.3)
    nb, bar, note = app.notebook, app._action_bar, app.frm_update
    return {"tabs_top": nb.winfo_rooty(), "tabs_h": nb.winfo_height(), "bar_top": bar.winfo_rooty(),
            "bar_h": bar.winfo_height(), "note_top": note.winfo_rooty() if note.winfo_manager() else None,
            "note_h": note.winfo_height() if note.winfo_manager() else 0}


def test_update_notice_shows_under_the_action_bar_and_keeps_the_layout(app, lib):
    app.load_source(str(lib))
    app.wait()
    before = _boxes(app)
    app._on_update_result(_info(), True)
    after = _boxes(app)
    assert "0.4.3" in app.lbl_update.cget("text") and "0.4.2" in app.lbl_update.cget("text")
    assert i18n.load_settings()["update_check_last"]  # the server answered: today is used up
    assert after["tabs_top"] == before["tabs_top"]  # nothing above the tabs moved
    assert after["bar_h"] == before["bar_h"] and after["note_top"] > after["bar_top"]  # bar intact, notice under it
    assert after["tabs_h"] > 200 and before["tabs_h"] - after["tabs_h"] <= after["note_h"] + 20  # only the table gave room
    app.notebook.select(1)
    app.pump(0.2)
    assert app.dtree.winfo_height() > 50  # the duplicates table still has rows


def test_update_notice_not_answered_leaves_the_day(app):
    app._on_update_result(None, False)
    assert not app.frm_update.winfo_manager()
    assert not i18n.load_settings().get("update_check_last")


def test_update_later_skips_that_version(app):
    app._on_update_result(_info(), True)
    app._update_later()
    assert not app.frm_update.winfo_manager()
    assert i18n.load_settings()["update_check_skipped"] == "0.4.3"


def test_update_button_opens_only_the_tools_page(app, monkeypatch):
    import dataclasses
    import webbrowser

    opened = []
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url))
    app._on_update_result(_info(), True)
    app._update_open()
    assert opened == [_info().url] and not app.frm_update.winfo_manager()
    app._update_info = dataclasses.replace(_info(), url="https://evil.example/")
    app._update_open()
    assert opened == [_info().url]  # nothing opened for another site


def test_update_notice_survives_a_language_switch_and_settings_can_turn_it_off(app):
    app._on_update_result(_info(), True)
    app.var_lang.set(i18n.LANG_NAMES["ko"])
    app._on_lang()
    assert app.frm_update.winfo_manager() and "0.4.3" in app.lbl_update.cget("text")
    app.var_lang.set(i18n.LANG_NAMES["en"])
    app._on_lang()
    app._show_settings()
    app.set_update.set(False)
    app._save_settings()
    assert not app.frm_update.winfo_manager()
    assert i18n.load_settings()["update_check"] is False
    app._on_update_result(_info(), True)  # switched off: a late answer shows nothing
    assert not app.frm_update.winfo_manager()


# ------------------------------------------------------------------ worker threads hold no Tk object (LESSONS A36)
def _reaches_tk(obj, seen=None) -> object | None:
    """The App, a Tk widget or a Tk variable reachable from a thread target through closures,
    default values, nested functions and bound methods; None if there is none."""
    import types

    import gui

    seen = seen if seen is not None else set()
    if id(obj) in seen:
        return None
    seen.add(id(obj))
    if isinstance(obj, (gui.App, tk.Misc, tk.Variable)):
        return obj
    if isinstance(obj, types.MethodType):
        return _reaches_tk(obj.__self__, seen) or _reaches_tk(obj.__func__, seen)
    if isinstance(obj, types.FunctionType):
        for cell in obj.__closure__ or ():
            try:
                found = _reaches_tk(cell.cell_contents, seen)
            except ValueError:  # an empty cell
                continue
            if found is not None:
                return found
        for value in (obj.__defaults__ or ()):
            found = _reaches_tk(value, seen)
            if found is not None:
                return found
    return None


def test_no_worker_thread_holds_the_window(app, lib, dialogs, monkeypatch):
    """A thread holding the App could drop the last reference after the window closed and
    free Tk objects off the main thread (Tcl_AsyncDelete). Every job is checked."""
    import threading

    import gui

    targets = []
    real = threading.Thread

    class Recording(real):
        def __init__(self, *a, target=None, **k):
            targets.append(target)
            super().__init__(*a, target=target, **k)

    monkeypatch.setattr(gui.threading, "Thread", Recording)
    app.load_source(str(lib))        # scan
    app.wait()                       # (then a plan on its own thread)
    app._find_dupes()
    app.wait()
    app._run()
    app.wait()
    app.wait()
    app._undo()
    app.wait()
    app.wait()
    assert len(targets) >= 5
    held = [(t.__qualname__, _reaches_tk(t)) for t in targets]
    assert all(found is None for _, found in held), held


def test_update_button_opens_the_english_page_unless_the_screen_is_korean(app, monkeypatch):
    import dataclasses
    import webbrowser

    opened = []
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url))
    en = "https://chaekgalpi.co.kr/en/tools/musicfolder?utm_source=app"
    app._on_update_result(dataclasses.replace(_info(), url_en=en), True)
    app._update_open()                               # the tests run on an English screen
    app.var_lang.set(i18n.LANG_NAMES["ko"])
    app._on_lang()
    app._on_update_result(dataclasses.replace(_info(), url_en=en), True)
    app._update_open()
    assert opened == [en, _info().url]
    app.var_lang.set(i18n.LANG_NAMES["en"])
    app._on_lang()


# ------------------------------------------------------------------ v0.5.0 item 1: disc marks and the hint button
def _box(root):
    for d in (1, 2, 3):
        put(root, "song-128.mp3" if d % 2 else "other.mp3", f"box/Disc {d}/1.mp3", title=f"T{d}", artist="X",
            album=f"Box [Disc {d}]", tracknumber="1")


def test_disc_hint_button_offers_the_disc_rule_and_merges_the_discs(app, tmp_path):
    root = tmp_path / "boxes"
    _box(root)
    app.load_source(str(root))
    app.wait()
    assert app.btn_disc_rule.winfo_manager() == "pack"          # the default pattern has no {disc}
    assert "1" in app.btn_disc_rule.cget("text") and "Multi-disc" in app.btn_disc_rule.cget("text")
    assert len({os.path.dirname(i.dst) for i in app.plan.items}) == 3  # three "[Disc n]" folders as before
    app._use_disc_rule()
    app.wait()
    assert app.btn_disc_rule.winfo_manager() == ""               # the rule has {disc} now: no hint
    assert sorted(os.path.relpath(i.dst, str(root)) for i in app.plan.items) == [
        os.path.join("X", "Box", f"{d}-01 - T{d}.mp3") for d in (1, 2, 3)]


def test_disc_hint_button_is_absent_without_multi_disc_albums(app, lib):
    app.load_source(str(lib))
    app.wait()
    assert app.btn_disc_rule.winfo_manager() == ""


# ------------------------------------------------------------------ send feedback (v0.5.1)
# The browser and the network are faked: webbrowser.open records the address, and every
# socket connect or name lookup is refused and counted (the update check is already
# stubbed by conftest.no_network). The address itself is tested in test_feedback.py.
@pytest.fixture
def browser(monkeypatch):
    import webbrowser

    state = {"opened": [], "result": True}

    def fake_open(url, *args, **kwargs):
        state["opened"].append(url)
        res = state["result"]
        if isinstance(res, BaseException):
            raise res
        return res

    monkeypatch.setattr(webbrowser, "open", fake_open)
    return state


@pytest.fixture
def no_sockets(monkeypatch, no_network):
    """Any attempt to open a connection or look up a name, by anything, fails the test."""
    import socket

    tries = []

    def refuse(name):
        def guard(*args, **kwargs):
            tries.append((name, args[:2]))
            raise OSError("blocked in tests (test_gui.no_sockets)")
        return guard

    monkeypatch.setattr(socket, "create_connection", refuse("create_connection"))
    monkeypatch.setattr(socket, "getaddrinfo", refuse("getaddrinfo"))
    monkeypatch.setattr(socket.socket, "connect", refuse("connect"))
    monkeypatch.setattr(socket.socket, "connect_ex", refuse("connect_ex"))
    yield tries
    assert tries == [] and list(no_network) == []


@pytest.fixture
def fb(app, browser, no_sockets):
    return app


def _feedback_url(lang):
    import update_check
    from version import APP_VERSION

    return update_check.feedback_url("musicfolder", APP_VERSION, lang)


def _set_lang(app, lang):
    app.var_lang.set(i18n.LANG_NAMES[lang])
    app._on_lang()
    app.root.update()


def _state(app):
    return {"source": app.source, "plan": app.plan, "busy": app.busy, "lang": i18n.current_lang(),
            "settings": {k: v for k, v in i18n.load_settings().items() if k != "artists_path"},
            "update": app._update_info, "notice": bool(app.frm_update.winfo_manager())}


def test_feedback_button_is_in_the_header_and_fits(fb):
    import gui

    btn = fb.btn_feedback
    assert btn.cget("text") == "Send feedback" and btn.cget("style") == "Link.TButton"
    width, height, min_w, min_h = gui.window_size(fb.root)
    for w, h in ((width, height), (min_w, min_h)):
        fb.root.geometry(f"{w}x{h}")
        fb.pump(0.3)
        x = btn.winfo_rootx() - fb.root.winfo_rootx()
        assert btn.winfo_ismapped() and btn.winfo_width() > 20
        assert 0 <= x and x + btn.winfo_width() <= fb.root.winfo_width()


def test_feedback_click_opens_the_page_once(fb, browser, dialogs):
    fb.btn_feedback.invoke()
    assert browser["opened"] == [_feedback_url("en")]
    assert dialogs["shown"] == []


@pytest.mark.parametrize("lang", ["ko", "ja", "zh-CN"])
def test_feedback_follows_the_language_chosen_while_running(fb, browser, lang):
    """The button is rebuilt with the new text and the next click opens that language's page."""
    import urllib.parse

    fb.btn_feedback.invoke()
    _set_lang(fb, lang)
    assert fb.btn_feedback.cget("text") == i18n.t("btn_feedback")
    fb.btn_feedback.invoke()
    assert browser["opened"] == [_feedback_url("en"), _feedback_url(lang)]
    path = urllib.parse.urlsplit(browser["opened"][1]).path
    assert path == ("/tools/musicfolder" if lang == "ko" else "/en/tools/musicfolder")
    _set_lang(fb, "en")
    assert fb.btn_feedback.cget("text") == "Send feedback"


@pytest.mark.parametrize("failure", ["false", "none", "webbrowser.Error", "OSError", "other"])
def test_feedback_without_a_browser_shows_the_address(fb, browser, dialogs, failure):
    """No exception; one warning with the address to copy and the address on the status
    line; nothing else changes, and the next click tries again."""
    import webbrowser

    before = _state(fb)
    browser["result"] = {"false": False, "none": None, "webbrowser.Error": webbrowser.Error("no runnable browser"),
                         "OSError": OSError(2, "not found"), "other": RuntimeError("odd")}[failure]
    fb.btn_feedback.invoke()
    url = _feedback_url("en")
    assert browser["opened"] == [url]
    assert dialogs["shown"] == [("showwarning", i18n.t("msg_feedback_failed", url=url))]
    assert url in fb.lbl_status.cget("text")
    assert _state(fb) == before and fb.frame.winfo_exists()
    browser["result"] = True
    fb.btn_feedback.invoke()
    assert len(browser["opened"]) == 2 and len(dialogs["shown"]) == 1


def test_feedback_failure_message_in_korean(fb, browser, dialogs):
    browser["result"] = False
    _set_lang(fb, "ko")
    fb.btn_feedback.invoke()
    url = _feedback_url("ko")
    assert dialogs["shown"] == [("showwarning", i18n.t("msg_feedback_failed", url=url))]
    assert "브라우저를 열지 못했습니다" in dialogs["shown"][0][1] and url in dialogs["shown"][0][1]


def test_feedback_does_not_touch_the_update_check(fb, browser, monkeypatch):
    """The button neither asks the server nor changes the update settings, and the update
    notice still works as before."""
    import update_check

    calls = []
    monkeypatch.setattr(update_check, "check_async", lambda *a, **k: calls.append(a) or True)
    monkeypatch.setattr(update_check, "fetch", lambda *a, **k: calls.append(a))
    settings = dict(i18n.load_settings())
    fb.btn_feedback.invoke()
    assert calls == [] and i18n.load_settings() == settings
    fb._on_update_result(_info("9.9.9"), True)
    fb.pump()
    assert fb.frm_update.winfo_manager() and "9.9.9" in fb.lbl_update.cget("text")
    fb.btn_feedback.invoke()  # the notice stays
    assert fb.frm_update.winfo_manager()
    fb._update_open()
    assert browser["opened"][-1] == _info().url and not fb.frm_update.winfo_manager()


def test_feedback_many_clicks_send_nothing(fb, browser, no_sockets):
    for _ in range(20):
        fb.btn_feedback.invoke()
    browser["result"] = False
    for _ in range(3):
        fb.btn_feedback.invoke()
    fb.pump(0.3)
    assert len(browser["opened"]) == 23 and len(set(browser["opened"])) == 1 and no_sockets == []


# ------------------------------------------------------------------ send feedback: keyboard and tooltip (v0.5.2)
# Keys run the window's own Tk bindings (_key), never the OS keyboard, so a test run cannot
# type into whatever the user is doing, and it does not matter which window holds the OS
# focus (other test runs may; a generated key is dropped then, LESSONS C10). The pointer is
# generated Tk events. The page language rule and the READMEs are in test_feedback_followup.py.
TIPS = {"ko": "안 되는 것·불편한 것·바라는 기능을 알려 주세요(브라우저가 열립니다)",
        "en": "Tell us what's broken or missing (opens your browser)",
        "ja": "不具合・ご要望をお知らせください（ブラウザが開きます）",
        "zh-CN": "告诉我们问题或建议（将打开浏览器）"}


@pytest.fixture
def quick_tip(fb, monkeypatch):
    """The tooltip without its wait, so a test does not sleep."""
    import gui

    monkeypatch.setattr(gui.Tooltip, "DELAY_MS", 0)
    return fb


def _settings_button(app):
    kids = app.btn_feedback.master.winfo_children()
    return kids[kids.index(app.btn_feedback) + 1]


def _language_box(app):
    kids = app.btn_feedback.master.winfo_children()
    return kids[kids.index(app.btn_feedback) + 3]


def _key(widget, seq):
    """Run what Tk runs when the key `seq` (canonical, "<Key-Return>") reaches `widget`:
    for each bind tag in order (widget, class, toplevel, all) the most specific binding -
    the key itself, a virtual event made of it (<<NextWindow>> is Tab), or any key - with
    its %-fields filled in, stopping at "break"."""
    import re

    tcl = widget.tk
    virtual = [v for v in tcl.splitlist(tcl.call("event", "info")) if seq in tcl.splitlist(tcl.call("event", "info", v))]
    keysym = seq[1:-1].split("-")[-1]
    fields = {"%": "%", "W": str(widget), "K": keysym, "A": "{}", "T": "2"}
    for tag in widget.bindtags():
        script = next((x for x in (tcl.call("bind", tag, q) for q in (seq, *virtual, "<KeyPress>")) if x), "")
        if not script:
            continue
        script = re.sub(r"%(.)", lambda m: fields.get(m.group(1), "0"), script)
        code = int(tcl.call("catch", script))
        if code == 3:  # break
            break
        assert code in (0, 4), tcl.call("set", "errorInfo")
    widget.update()


def _press(app, key):
    app.pump(0.1)  # the rebuilt header is mapped, so it can take the focus
    app.btn_feedback.focus_set()
    app.root.update()
    assert str(app.root.focus_lastfor()) == str(app.btn_feedback)
    _key(app.btn_feedback, key)


def _hover(app):
    app.btn_feedback.event_generate("<Enter>", x=3, y=3)
    for _ in range(3):
        app.root.update()
    return app.feedback_tip.tip


def _leave(app):
    app.btn_feedback.event_generate("<Leave>")
    app.root.update()


def _tip_label(tip):
    (label,) = tip.winfo_children()
    return label


def _tips_open(app):
    return [w for w in app.btn_feedback.winfo_children() if isinstance(w, tk.Toplevel)]


# a. keyboard
def test_the_key_helper_runs_tk_bindings(root):
    from tkinter import ttk

    hits = []
    b = ttk.Button(root, text="x", command=lambda: hits.append("invoke"))
    _key(b, "<Key-Return>")
    assert hits == []                                 # ttk alone has no Enter
    _key(b, "<Key-space>")
    assert hits == ["invoke"]                         # the class binding ran
    b.bind("<Key-space>", lambda e: (hits.append("own"), "break")[1])
    _key(b, "<Key-space>")
    assert hits == ["invoke", "own"]                  # "break" stopped the class binding
    b.destroy()


def test_header_tab_order_follows_the_screen(fb):
    """Left to right as seen: [Send feedback] -> [Settings] -> language box; Shift+Tab the other way."""
    from tkinter import ttk

    fb.pump(0.1)
    btn, settings, lang = fb.btn_feedback, _settings_button(fb), _language_box(fb)
    assert settings.cget("text") == i18n.t("btn_settings") and isinstance(lang, ttk.Combobox)
    assert str(btn.tk_focusNext()) == str(settings)       # Tab from the link reaches [Settings]
    assert str(settings.tk_focusNext()) == str(lang)      # then the language box
    assert str(lang.tk_focusPrev()) == str(settings)      # Shift+Tab goes back
    assert str(settings.tk_focusPrev()) == str(btn)
    assert str(btn.tk_focusPrev()) not in (str(btn), str(settings), str(lang))  # and leaves the header


def test_tab_and_shift_tab_keys_move_the_focus(fb):
    fb.pump(0.1)
    btn, settings = fb.btn_feedback, _settings_button(fb)
    btn.focus_set()
    fb.root.update()
    _key(btn, "<Key-Tab>")
    assert str(fb.root.focus_lastfor()) == str(settings)
    _key(settings, "<Shift-Key-Tab>")
    assert str(fb.root.focus_lastfor()) == str(btn)


@pytest.mark.parametrize("key", ["<Key-Return>", "<Key-space>"])
@pytest.mark.parametrize("lang", list(TIPS))
def test_enter_and_space_open_the_page_exactly_once(fb, browser, dialogs, lang, key):
    if lang != "en":
        _set_lang(fb, lang)
    _press(fb, key)
    assert browser["opened"] == [_feedback_url(lang)] and dialogs["shown"] == []


def test_keypad_enter_is_bound_too(fb):
    """Windows delivers the keypad Enter as Return (a generated KP_Enter has no key code
    there); the binding is for other systems."""
    assert fb.btn_feedback.bind("<KP_Enter>") and fb.btn_feedback.bind("<Return>")


def test_other_keys_do_nothing(fb, browser, dialogs):
    before = _state(fb)
    for key in ("<Key-Escape>", "<Key-a>", "<Key-Up>", "<Key-Down>", "<Key-BackSpace>", "<Key-F1>", "<Key-Tab>"):
        _press(fb, key)
    assert browser["opened"] == [] and dialogs["shown"] == [] and _state(fb) == before


def test_focus_is_visible_and_keeps_the_size(fb):
    from tkinter import ttk

    btn = fb.btn_feedback
    s = ttk.Style(fb.root)
    for option in ("background", "bordercolor"):
        assert s.lookup("Link.TButton", option, ["focus"]) != s.lookup("Link.TButton", option)
    fb.pump(0.2)
    size = (btn.winfo_width(), btn.winfo_height())
    btn.event_generate("<FocusIn>")
    fb.pump(0.2)
    assert btn.instate(["focus"]) and (btn.winfo_width(), btn.winfo_height()) == size
    btn.event_generate("<FocusOut>")
    fb.pump(0.2)
    assert not btn.instate(["focus"]) and (btn.winfo_width(), btn.winfo_height()) == size


def test_enter_without_a_browser_shows_the_address_once(fb, browser, dialogs):
    browser["result"] = False
    _set_lang(fb, "ja")
    _press(fb, "<Key-Return>")
    url = _feedback_url("ja")
    assert browser["opened"] == [url]
    assert dialogs["shown"] == [("showwarning", i18n.t("msg_feedback_failed", url=url))]


# b. tooltip
def test_tooltip_strings():
    for lang, text in TIPS.items():
        assert i18n.load_lang_file(lang)["tip_feedback"] == text
    assert len(set(TIPS.values())) == 4 and all(v.strip() for v in TIPS.values())


@pytest.mark.parametrize("lang", list(TIPS))
def test_hover_shows_the_tooltip_and_leaving_hides_it(quick_tip, browser, lang):
    if lang != "en":
        _set_lang(quick_tip, lang)
    assert quick_tip.feedback_tip.tip is None
    tip = _hover(quick_tip)
    assert tip is not None and tip.winfo_ismapped()
    assert _tip_label(tip).cget("text") == TIPS[lang]
    family = quick_tip.root.tk.splitlist(_tip_label(tip).cget("font"))[0]
    assert family == quick_tip.f_small[0]             # the UI font of that language (Yu Gothic UI for ja)
    _leave(quick_tip)
    assert quick_tip.feedback_tip.tip is None and not tip.winfo_exists()
    assert browser["opened"] == []                    # resting on it opens nothing


def test_tooltip_waits_before_it_appears(fb):
    import gui

    _hover(fb)
    assert fb.feedback_tip.tip is None                # passing over it shows nothing yet
    _leave(fb)
    fb.pump(gui.Tooltip.DELAY_MS / 1000 + 0.3)
    assert fb.feedback_tip.tip is None                # left before the wait: never shown
    _hover(fb)
    fb.pump(gui.Tooltip.DELAY_MS / 1000 + 0.3)
    assert fb.feedback_tip.tip is not None            # stayed: shown
    assert 400 <= gui.Tooltip.DELAY_MS <= 500


def test_click_hides_the_tooltip_and_opens_once(quick_tip, browser):
    btn = quick_tip.btn_feedback
    _hover(quick_tip)
    btn.event_generate("<ButtonPress-1>", x=3, y=3)
    quick_tip.root.update()
    assert quick_tip.feedback_tip.tip is None and _tips_open(quick_tip) == []
    btn.event_generate("<ButtonRelease-1>", x=3, y=3)
    quick_tip.root.update()
    assert browser["opened"] == [_feedback_url("en")]  # ttk presses on the release, once


def test_hovering_twice_keeps_one_tooltip(quick_tip):
    first = _hover(quick_tip)
    assert _hover(quick_tip) is first and len(_tips_open(quick_tip)) == 1


@pytest.mark.parametrize("lang", ["ko", "ja", "zh-CN"])
def test_language_change_while_shown_changes_the_text(quick_tip, monkeypatch, lang):
    """Changing the language from the keyboard while the pointer rests on the link: the
    window is rebuilt and the tooltip comes back in the new language."""
    import gui

    monkeypatch.setattr(gui.Tooltip, "pointer_inside", lambda self: True)
    old = _hover(quick_tip)
    assert _tip_label(old).cget("text") == TIPS["en"]
    _set_lang(quick_tip, lang)
    quick_tip.root.update()
    assert not old.winfo_exists()
    tip = quick_tip.feedback_tip.tip
    assert tip is not None and _tip_label(tip).cget("text") == TIPS[lang] and len(_tips_open(quick_tip)) == 1


def test_language_change_with_the_pointer_elsewhere_leaves_no_tooltip(quick_tip, monkeypatch):
    import gui

    monkeypatch.setattr(gui.Tooltip, "pointer_inside", lambda self: False)
    _hover(quick_tip)
    _set_lang(quick_tip, "ko")
    quick_tip.root.update()
    assert quick_tip.feedback_tip.tip is None and _tips_open(quick_tip) == []
    assert _tip_label(_hover(quick_tip)).cget("text") == TIPS["ko"]


def test_language_change_while_the_tooltip_is_waiting(fb):
    import gui

    _hover(fb)                                        # scheduled, not shown
    pending = fb.feedback_tip
    _set_lang(fb, "ja")
    fb.pump(gui.Tooltip.DELAY_MS / 1000 + 0.3)
    assert pending.tip is None and pending._job is None and fb.feedback_tip.tip is None


@pytest.mark.parametrize("edge", ["left", "right"])
@pytest.mark.parametrize("lang", list(TIPS))
def test_tooltip_stays_in_the_smallest_window_and_on_screen(quick_tip, lang, edge):
    import gui

    app = quick_tip
    if lang != "en":
        _set_lang(app, lang)
    _, _, min_w, min_h = gui.window_size(app.root)
    sw, sh = app.root.winfo_screenwidth(), app.root.winfo_screenheight()
    app.root.geometry(f"{min_w}x{min_h}+{0 if edge == 'left' else sw - min_w}+0")
    app.pump(0.4)
    tip = _hover(app)
    app.root.update()
    x, y, w, h = tip.winfo_rootx(), tip.winfo_rooty(), tip.winfo_width(), tip.winfo_height()
    r = app.root
    assert 0 <= x and x + w <= sw and 0 <= y and y + h <= sh, (x, y, w, h, sw, sh)
    assert r.winfo_rootx() <= x and x + w <= r.winfo_rootx() + r.winfo_width(), (x, w, r.winfo_rootx(), r.winfo_width())
    assert r.winfo_rooty() <= y and y + h <= r.winfo_rooty() + r.winfo_height()
    label = _tip_label(tip)
    assert label.winfo_width() >= label.winfo_reqwidth() and label.winfo_height() >= label.winfo_reqheight()
    _leave(app)


def test_tooltip_does_not_move_the_header(quick_tip):
    """It is a separate little window, so the header keeps its layout while it is shown."""
    btn = quick_tip.btn_feedback
    quick_tip.pump(0.2)
    where = [(w.winfo_x(), w.winfo_width()) for w in btn.master.winfo_children()]
    _hover(quick_tip)
    quick_tip.pump(0.2)
    assert [(w.winfo_x(), w.winfo_width()) for w in btn.master.winfo_children()] == where


# c. [Update] in the window follows the same language rule as the feedback page
@pytest.mark.parametrize("lang", list(TIPS))
def test_update_button_opens_the_page_for_the_language(fb, browser, lang):
    import update_check

    info = update_check.UpdateInfo("9.9.9", "0.5.1", "https://chaekgalpi.co.kr/tools/musicfolder?utm_source=app",
                                   "https://chaekgalpi.co.kr/en/tools/musicfolder?utm_source=app")
    if lang != "en":
        _set_lang(fb, lang)
    fb._on_update_result(info, True)
    fb.pump()
    fb._update_open()
    assert browser["opened"] == [info.url if lang == "ko" else info.url_en]
    assert ("/en/" in browser["opened"][0]) == ("/en/" in _feedback_url(lang))
