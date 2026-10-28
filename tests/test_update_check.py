"""Update check: once a day, off switch, silent failures, what is sent, when a notice shows,
and the one-line notice in the window. The network is always faked - these tests never
reach the real server (see conftest.no_network)."""
from __future__ import annotations

import io
import json
import socket
import urllib.error
import urllib.parse

import pytest

import i18n
import prefs as prefs_mod
import update_check as uc
from version import APP_VERSION

DAY = "2026-10-13"
SLUG = "musicfolder"
OK = {"slug": SLUG, "latest": "0.4.3", "tag": "v0.4.3", "client": "0.4.2", "updateAvailable": True,
      "url": "https://chaekgalpi.co.kr/tools/musicfolder?utm_source=app&utm_medium=update"}


class FakeRoot:
    """after() runs the callback at once; the background thread is run inline (see fixture)."""

    def __init__(self):
        self.delays = []

    def after(self, ms, fn):
        self.delays.append(ms)
        fn()


@pytest.fixture
def inline_threads(monkeypatch):
    class InlineThread:
        def __init__(self, target, name=None, daemon=None):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(uc.threading, "Thread", InlineThread)


def run(prefs, fetcher, day=DAY):
    """One app start: schedule the check; the caller records the day only when answered (as the GUI does)."""
    results = []

    def on_result(info, answered):
        results.append(info)
        if answered:
            prefs.update_check_last = day

    root = FakeRoot()
    scheduled = uc.check_async(root, SLUG, "0.4.2", prefs, on_result, fetcher=fetcher, day=day)
    return scheduled, results, root


def counting(answer):
    calls = []

    def fetcher(slug, version):
        calls.append((slug, version))
        return answer

    return fetcher, calls


# ------------------------------------------------------------------ how often
def test_same_day_twice_asks_once(inline_threads):
    p = prefs_mod.Prefs()
    fetcher, calls = counting(OK)
    run(p, fetcher)
    run(p, fetcher)
    assert len(calls) == 1
    run(p, fetcher, day="2026-10-14")
    assert len(calls) == 2


def test_switched_off_never_asks(inline_threads):
    p = prefs_mod.Prefs(update_check=False)
    fetcher, calls = counting(OK)
    scheduled, results, _ = run(p, fetcher)
    assert not scheduled and calls == [] and results == []


def test_offline_does_not_use_up_the_day(inline_threads):
    p = prefs_mod.Prefs()
    fetcher, calls = counting(None)  # no answer
    run(p, fetcher)
    run(p, fetcher)
    assert len(calls) == 2 and p.update_check_last == ""


def test_waits_one_and_a_half_seconds_first(inline_threads):
    _, _, root = run(prefs_mod.Prefs(), counting(OK)[0])
    assert root.delays[0] == 1500


def test_the_app_sends_its_own_slug_and_version():
    assert SLUG == "musicfolder" and APP_VERSION == "0.5.2"
    import gui

    assert gui.UPDATE_SLUG == SLUG


# ------------------------------------------------------------------ what is sent
class FakeResp(io.BytesIO):
    def __init__(self, body: bytes, status: int = 200):
        super().__init__(body)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_request_carries_only_slug_version_and_os():
    seen = {}

    def opener(req, timeout):
        seen["url"], seen["timeout"] = req.full_url, timeout
        seen["headers"] = {k.lower(): v for k, v in req.header_items()}
        return FakeResp(json.dumps(OK).encode())

    assert uc.fetch(SLUG, "0.4.2", opener=opener) == OK
    url = urllib.parse.urlsplit(seen["url"])
    assert (url.scheme, url.netloc, url.path) == ("https", "chaekgalpi.co.kr", "/api/tools/musicfolder/latest")
    assert urllib.parse.parse_qs(url.query) == {"v": ["0.4.2"], "os": ["win"]}
    assert seen["timeout"] == 4.0
    assert seen["headers"]["user-agent"] == "chaekgalpi-tool/musicfolder/0.4.2"
    assert set(seen["headers"]) <= {"user-agent", "accept"}  # nothing else about the user or machine


@pytest.mark.parametrize("behaviour", ["urlerror", "timeout", "http500", "badjson", "list", "status500", "oserror"])
def test_failures_are_silent(behaviour):
    def opener(req, timeout):
        if behaviour == "urlerror":
            raise urllib.error.URLError("offline")
        if behaviour == "timeout":
            raise socket.timeout("slow")
        if behaviour == "http500":
            raise urllib.error.HTTPError(req.full_url, 500, "boom", {}, None)
        if behaviour == "oserror":
            raise OSError("reset")
        if behaviour == "badjson":
            return FakeResp(b"<html>not json")
        if behaviour == "list":
            return FakeResp(b"[1, 2]")
        return FakeResp(json.dumps(OK).encode(), status=500)

    assert uc.fetch(SLUG, "0.4.2", opener=opener) is None


# ------------------------------------------------------------------ when a notice shows
@pytest.mark.parametrize("flag", [False, None, "true", 1])
def test_no_notice_unless_server_says_true(flag):
    assert uc.interpret(dict(OK, updateAvailable=flag), "0.4.2") is None


def test_notice_once_when_newer(inline_threads):
    _, results, _ = run(prefs_mod.Prefs(), counting(OK)[0])
    assert results == [uc.UpdateInfo("0.4.3", "0.4.2", OK["url"])]


def test_later_hides_that_version_but_not_a_newer_one():
    p = prefs_mod.Prefs(update_check_skipped="0.4.3")
    assert uc.interpret(OK, "0.4.2", p.update_check_skipped) is None
    assert uc.interpret(dict(OK, latest="0.4.4"), "0.4.2", p.update_check_skipped).latest == "0.4.4"


@pytest.mark.parametrize("url", ["https://github.com/microhan1/music-folder-organizer/releases",
                                 "http://chaekgalpi.co.kr/tools/x", "https://chaekgalpi.co.kr.evil.example/",
                                 "javascript:alert(1)", None, 5])
def test_only_pages_of_the_tools_site_are_opened(url):
    assert uc.interpret(dict(OK, url=url), "0.4.2") is None


@pytest.mark.parametrize("latest", ["", "   ", None, 3, "x" * 40])
def test_odd_versions_show_nothing(latest):
    assert uc.interpret(dict(OK, latest=latest), "0.4.2") is None


# ------------------------------------------------------------------ settings
def test_settings_are_type_checked(monkeypatch):
    monkeypatch.setattr(i18n, "load_settings", lambda: {"update_check": "no", "update_check_last": 20261013,
                                                        "update_check_skipped": ["0.4.3"]})
    p = prefs_mod.load()
    assert p.update_check is True and p.update_check_last == "" and p.update_check_skipped == ""


def test_the_thread_holds_no_tk_object(monkeypatch):
    """The fetch thread must not keep the window or the App alive: if it held the last
    reference, Tk objects could be freed off the main thread and Tcl aborts (LESSONS A35)."""
    captured = {}

    class CaptureThread:
        def __init__(self, target, name=None, daemon=None):
            captured["target"] = target

        def start(self):
            pass

    monkeypatch.setattr(uc.threading, "Thread", CaptureThread)

    class Root:
        def after(self, ms, fn):
            if ms == uc.DELAY_MS:
                fn()

    root, on_result = Root(), lambda info, answered: None
    uc.check_async(root, SLUG, "0.4.3", prefs_mod.Prefs(), on_result, fetcher=lambda s, v: OK, day=DAY)
    target = captured["target"]
    held = [c.cell_contents for c in (target.__closure__ or ())] + list(target.__defaults__ or ())
    assert not any(x is root or x is on_result for x in held)


# ------------------------------------------------------------------ which tools page [Update] opens
EN = "https://chaekgalpi.co.kr/en/tools/musicfolder?utm_source=app"


@pytest.mark.parametrize("lang, url_en, expect", [
    ("ko", EN, "url"),                                    # a Korean screen: the Korean page
    ("en", EN, "en"),                                     # other languages: the English page
    ("ja", EN, "en"),
    ("zh-CN", EN, "en"),
    ("en", None, "url"),                                  # the server has none (before v1.96)
    ("en", "missing", "url"),
    ("en", "https://evil.example/en/tools", "url"),       # never another site
    ("en", "http://chaekgalpi.co.kr/en/tools/x", "url"),
    ("en", 5, "url"),
])
def test_update_opens_the_english_page_on_a_non_korean_screen(lang, url_en, expect):
    data = dict(OK)
    if url_en != "missing":
        data["urlEn"] = url_en
    info = uc.interpret(data, "0.4.4")
    assert uc.page_for(info, lang) == (EN if expect == "en" else OK["url"])


# ------------------------------------------------------------------ the tests' own network block (LESSONS C9)
def test_the_tests_never_reach_the_server(no_network):
    """conftest.no_network: the default opener and urlopen are a recording guard, and the
    default fetcher of check_async answers nothing, so an App opened by a test cannot ask."""
    import urllib.request

    assert uc.fetch.__kwdefaults__["opener"] is urllib.request.urlopen  # both are the guard
    assert uc.fetch(SLUG, "0.4.4") is None  # no opener given: blocked, silent
    assert no_network == ["https://chaekgalpi.co.kr/api/tools/musicfolder/latest?v=0.4.4&os=win"]
    assert uc.check_async.__kwdefaults__["fetcher"](SLUG, "0.4.4") is None
    no_network.clear()  # recorded on purpose here; any other record fails its test
