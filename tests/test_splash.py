"""The start-up picture (PyInstaller --splash) is closed by gui.launch once the window is drawn,
and also when the window cannot be made. No Tk is created here: launch is stopped before it."""
from __future__ import annotations

import os
import sys
import types

import pytest

import gui

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class FakeSplash(types.ModuleType):
    def __init__(self):
        super().__init__("pyi_splash")
        self.closed = 0

    def close(self):
        self.closed += 1


@pytest.fixture
def splash(monkeypatch):
    mod = FakeSplash()
    monkeypatch.setitem(sys.modules, "pyi_splash", mod)
    return mod


def test_close_splash_without_pyi_splash(monkeypatch):
    monkeypatch.setitem(sys.modules, "pyi_splash", None)  # import raises ImportError
    gui.close_splash()  # no error when run from Python


def test_close_splash_calls_pyi_splash(splash):
    gui.close_splash()
    assert splash.closed == 1


def test_launch_closes_splash_when_window_fails(monkeypatch, splash):
    class Boom(RuntimeError):
        pass

    def no_tk(*a, **k):
        raise Boom("no display")

    monkeypatch.setattr(gui, "_HAS_DND", False)
    monkeypatch.setattr(gui.tk, "Tk", no_tk)
    with pytest.raises(Boom):
        gui.launch()
    assert splash.closed == 1


def test_launch_closes_splash_after_window_is_drawn(monkeypatch, splash):
    order = []

    class FakeRoot:
        def iconphoto(self, *a):
            order.append("icon")

        def update(self):
            order.append("update")

        def mainloop(self):
            order.append("mainloop")

    def fake_app(root, initial):
        order.append(("app", initial))

    monkeypatch.setattr(gui, "_HAS_DND", False)
    monkeypatch.setattr(gui.tk, "Tk", lambda: FakeRoot())
    monkeypatch.setattr(gui.tk, "PhotoImage", lambda file: file)
    monkeypatch.setattr(gui, "App", fake_app)
    splash.close = lambda: order.append("close")
    gui.launch("x")
    assert order == ["icon", ("app", "x"), "update", "close", "mainloop"]


def test_splash_assets_present():
    png = os.path.join(ROOT, "assets", "splash.png")
    assert os.path.isfile(png)
    assert os.path.getsize(png) < 20_000  # a few KB: it is shown before Python starts
    with open(os.path.join(ROOT, "build.bat"), encoding="utf-8") as f:
        bat = f.read()
    assert '--splash "assets\\splash.png"' in bat
