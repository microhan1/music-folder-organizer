"""Check boxes show a tick (✓), not the clam theme's X (2026-11-17, same as music-tag-filler).

The window keeps the clam theme and swaps only the TCheckbutton.indicator element for an image
element drawn by check_indicator.py. The one plain check box is 「새 버전 확인」 in the settings
window; the toggle chips (Chip.Toolbutton) already carry a ✓ in their text and have no indicator.
"""
import time

import pytest

import i18n

tk = pytest.importorskip("tkinter")

import check_indicator as ci  # noqa: E402


@pytest.fixture(scope="module")
def root():
    import gui

    try:
        r = gui.TkinterDnD.Tk() if gui._HAS_DND else tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"no display: {exc}")
    yield r
    r.destroy()


@pytest.fixture
def app(root, tmp_path):
    i18n.update_settings(artists_path=str(tmp_path / "artists.json"))
    import gui

    a = gui.App(root, None)
    _pump(root, 0.2)
    yield a
    a.cancel.set()
    if a.settings_win is not None and a.settings_win.winfo_exists():
        a.settings_win.destroy()
    a.frame.destroy()


def _pump(root, sec):
    end = time.time() + sec
    while time.time() < end:
        root.update()
        time.sleep(0.01)


def _names(layout) -> list:
    out = []
    for name, opts in layout:
        out.append(name)
        out += _names(opts.get("children", []))
    return out


def _walk(w):
    yield w
    for c in w.winfo_children():
        yield from _walk(c)


def test_the_theme_stays_clam_and_only_the_indicator_is_swapped(app):
    from tkinter import ttk

    style = ttk.Style(app.root)
    assert style.theme_use() == "clam"
    names = _names(style.layout("TCheckbutton"))
    assert ci.ELEMENT in names and ci.OLD_ELEMENT not in names
    assert "Checkbutton.focus" in names and "Checkbutton.label" in names  # keyboard focus ring kept
    assert ci.ELEMENT in style.element_names()
    assert ci.ELEMENT not in _names(style.layout("Toolbutton"))  # chips and segmented choices untouched


def test_the_settings_check_box_uses_the_tick(app):
    from tkinter import ttk

    app._show_settings()
    _pump(app.root, 0.2)
    boxes = [w for w in _walk(app.settings_win) if w.winfo_class() == "TCheckbutton"]
    assert len(boxes) == 1 and boxes[0].cget("text") == i18n.t("opt_update_check")
    assert ci.ELEMENT in _names(ttk.Style(app.root).layout(boxes[0].winfo_class()))
    assert (boxes[0].cget("style") or "TCheckbutton") == "TCheckbutton"
    # the main window has no plain check box: its on/off options are chips
    assert [w for w in _walk(app.frame) if w.winfo_class() == "TCheckbutton"
            and (w.cget("style") or "TCheckbutton") == "TCheckbutton"] == []


def test_same_size_and_text_position_as_the_clam_box(app):
    """A check box with clam's old indicator asks for exactly the same space."""
    import gui
    from tkinter import ttk

    style = ttk.Style(app.root)
    style.layout("OldX.TCheckbutton", ci._swap(style.layout("TCheckbutton"), ci.ELEMENT, ci.OLD_ELEMENT))
    style.configure("OldX.TCheckbutton", indicatorsize=10)  # clam's default; margin (0, 0, 6, 0) from _style
    assert gui.CHECK_SPACE_PX == 10 + 6
    for key in ("opt_update_check", "opt_fingerprint"):
        new = ttk.Checkbutton(app.root, text=i18n.t(key))
        old = ttk.Checkbutton(app.root, text=i18n.t(key), style="OldX.TCheckbutton")
        try:
            assert (new.winfo_reqwidth(), new.winfo_reqheight()) == (old.winfo_reqwidth(), old.winfo_reqheight())
        finally:
            new.destroy()
            old.destroy()


def test_images_are_kept_and_survive_a_language_change(app):
    """_style runs again when the language changes; the element keeps the first images."""
    import gui

    first = app._check_images
    assert set(first) == {name for _, name in ci.STATE_MAP} | {"off"}
    for img in first.values():
        assert (img.width(), img.height()) == (gui.CHECK_SPACE_PX, gui.CHECK_BOX_PX)
        assert all(img.transparency_get(x, 0) for x in range(gui.CHECK_BOX_PX, gui.CHECK_SPACE_PX))
    app._style()
    assert app._check_images is first
    assert app.root._tick_check_images is first


def _rgb(c):
    return ci._rgb(c)


def _near(a, b, tol=40):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def test_ticked_box_is_green_with_a_white_tick_unticked_is_white():
    import gui

    spec = ci.colours(gui.ACCENT, gui.ACCENT_HOVER)
    size = gui.CHECK_BOX_PX
    on = ci.render(size, *spec["on"], bg=gui.SURFACE)
    px = [_rgb(c) for row in on for c in row if c]
    assert sum(_near(p, _rgb(gui.ACCENT), 10) for p in px) > len(px) * 0.4
    assert any(_near(p, (255, 255, 255), 10) for p in px)
    q = size // 4  # a ✓ leaves the top-left and bottom-right corners inside the box green (an X would not)
    assert _near(_rgb(on[q][q]), _rgb(gui.ACCENT), 10)
    assert _near(_rgb(on[size - 1 - q][size - 1 - q]), _rgb(gui.ACCENT), 10)
    off = ci.render(size, *spec["off"], bg=gui.SURFACE)
    inner = [_rgb(off[j][i]) for j in range(2, size - 2) for i in range(2, size - 2)]
    assert all(p == (255, 255, 255) for p in inner)
    for state in ("off_disabled", "on_disabled"):
        dim = [_rgb(c) for row in ci.render(size, *spec[state], bg=gui.SURFACE) for c in row if c]
        assert all(min(p) >= 0xc0 for p in dim)


def test_ticking_still_works(app):
    app._show_settings()
    _pump(app.root, 0.2)
    box = [w for w in _walk(app.settings_win) if w.winfo_class() == "TCheckbutton"][0]
    before = bool(app.set_update.get())
    box.invoke()
    assert bool(app.set_update.get()) is (not before)
    assert ("selected" in box.state()) is (not before)
