"""Drive the real tkinter window: dialogs are answered by the test."""
import json
import os
import shutil
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
        monkeypatch.setattr(messagebox, name, lambda title, msg, _n=name: log["shown"].append((_n, msg)))
    monkeypatch.setattr(messagebox, "askokcancel", lambda title, msg: (log["shown"].append(("ask", msg)), log["answer"])[1])
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


def _wait(a):
    for _ in range(600):
        _pump(a.root, 0.05)
        if not a.busy:
            _pump(a.root, 0.1)
            if not a.busy:
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
    assert s["dupes"] == 1  # the identical copy is applied automatically
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
    app.pump()
    item = next(i for i in app.plan.items if i.key == first)
    assert not item.checked and app.tree.set(first, "check") == "☐"
    app._toggle_all()
    assert all(i.checked for i in app.plan.items)
    app._toggle_all()
    assert not any(i.checked for i in app.plan.items)
    assert str(app.btn_run.cget("state")) == "disabled" or app.plan.summary()["folders"] == 0


def test_stage_two_group_apply_and_keep_rules(app, lib, dialogs):
    app.load_source(str(lib))
    app.wait()
    g2 = next(g for g in app.groups if g.stage == 2)
    assert not g2.applied
    before = app.plan.summary()["dupes"]
    g2.applied = True
    app._replan()
    assert app.plan.summary()["dupes"] == before + 1
    g2.keep.clear()
    app._replan()
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
