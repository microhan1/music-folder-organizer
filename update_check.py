"""Once a day after start: ask the 책갈피 tools site whether a newer version exists.

Only the tool's slug, its version and "win" are sent - no file, path, device or user
information. The request runs on a background thread 1.5 s after the window is shown and
never blocks the UI; any failure (offline, timeout, HTTP error, bad JSON) is silent. The
server decides whether the version is older (`updateAvailable`); null means "cannot
compare" and shows nothing. Standard library only.
"""
from __future__ import annotations

import dataclasses
import datetime
import json
import threading
import urllib.parse
import urllib.request
from typing import Callable

from i18n import DEFAULT_LANG

API = "https://chaekgalpi.co.kr/api/tools/{slug}/latest"
SITE = "https://chaekgalpi.co.kr/"  # the notice only ever opens a page on this site
TIMEOUT = 4.0
DELAY_MS = 1500
POLL_MS = 100  # how often the Tk thread looks for the answer
MAX_BYTES = 64 * 1024


@dataclasses.dataclass
class UpdateInfo:
    latest: str
    current: str
    url: str
    url_en: str = ""  # the English tools page, when the server has one (and it is on the tools site)


def is_korean(lang: str | None) -> bool:
    """Which tools page a screen language gets (the same rule across the series): the Korean
    page only for a code whose first part is ko (ko, ko-KR, ko_KR), the English page (/en)
    for every other code, known or not - the site has no Japanese or Chinese page. None or
    a blank value follows this tool's default screen language (i18n.DEFAULT_LANG)."""
    code = (lang or "").strip() or DEFAULT_LANG
    return code.lower().replace("_", "-").split("-")[0] == "ko"


def page_for(info: UpdateInfo, lang: str | None) -> str:
    """The page [Update] opens: the Korean one on a Korean screen, otherwise the English
    one when the server gave a valid one, else the Korean one."""
    if not is_korean(lang) and info.url_en.startswith(SITE):
        return info.url_en
    return info.url


def feedback_url(slug: str, version: str | None, lang: str | None) -> str:
    """The tools page's feedback box, which [Send feedback] opens in the browser. Built from
    constants only; nothing is sent from the app. The Korean page for a Korean screen, the
    English one for any other language (is_korean). utm_source=app marks it as the Windows
    program on the site's admin screen; #feedback scrolls down to the box."""
    prefix = "" if is_korean(lang) else "en/"
    query = "utm_source=app&utm_medium=feedback"
    version = (version or "").strip()
    if version:
        query += "&v=" + urllib.parse.quote(version, safe="")
    return f"{SITE}{prefix}tools/{urllib.parse.quote(slug, safe='')}?{query}#feedback"


def today() -> str:
    return datetime.date.today().isoformat()


def should_check(prefs, day: str) -> bool:
    """Checking is on and has not been done today."""
    return bool(getattr(prefs, "update_check", True)) and getattr(prefs, "update_check_last", "") != day


def fetch(slug: str, version: str, *, opener: Callable = urllib.request.urlopen) -> dict | None:
    """The server's answer, or None on any failure."""
    query = urllib.parse.urlencode({"v": version, "os": "win"})
    req = urllib.request.Request(f"{API.format(slug=slug)}?{query}",
                                 headers={"User-Agent": f"chaekgalpi-tool/{slug}/{version}",
                                          "Accept": "application/json"})
    try:
        with opener(req, timeout=TIMEOUT) as resp:
            if getattr(resp, "status", 200) != 200:
                return None
            data = json.loads(resp.read(MAX_BYTES).decode("utf-8"))
    except Exception:  # offline, timeout, HTTP error, bad JSON: say nothing
        return None
    return data if isinstance(data, dict) else None


def interpret(data: dict | None, version: str, skipped: str = "") -> UpdateInfo | None:
    """An UpdateInfo only when the server says a newer version exists, it is not the
    version the user chose to skip, and the link points at the tools site."""
    if not isinstance(data, dict) or data.get("updateAvailable") is not True:
        return None
    latest, url = data.get("latest"), data.get("url")
    if not isinstance(latest, str) or not latest.strip() or len(latest) > 32:
        return None
    if not isinstance(url, str) or not url.startswith(SITE):
        return None
    latest = latest.strip()
    if latest == (skipped or "").strip():
        return None
    url_en = data.get("urlEn")
    url_en = url_en if isinstance(url_en, str) and url_en.startswith(SITE) else ""
    return UpdateInfo(latest, version, url, url_en)


def check_async(root, slug: str, version: str, prefs, on_result: Callable[[UpdateInfo | None, bool], None], *,
                fetcher: Callable = fetch, day: str | None = None) -> bool:
    """Schedule one check DELAY_MS after now. on_result(info, answered) runs on the Tk
    thread: `answered` is True when the server replied (the caller records the day then),
    `info` is set only when a notice should be shown. Returns False when no check is due."""
    if not should_check(prefs, day or today()):
        return False
    skipped = getattr(prefs, "update_check_skipped", "")
    box: dict = {}

    # The thread only fetches into `box` and holds no Tk object. It used to hand the
    # result over with root.after(..., on_result): then a window closed while the
    # request was out left the thread with the last reference to the App, its Tk
    # variables could be freed off the main thread and Tcl aborted ("Tcl_AsyncDelete:
    # async handler deleted by the wrong thread", LESSONS A35). The Tk thread polls instead.
    def work(box=box) -> None:
        box["data"] = fetcher(slug, version)

    def poll() -> None:
        if "data" not in box:
            try:
                root.after(POLL_MS, poll)
            except Exception:  # the window was closed meanwhile
                pass
            return
        data = box["data"]
        on_result(interpret(data, version, skipped), data is not None)

    def start() -> None:
        threading.Thread(target=work, name="update-check", daemon=True).start()
        poll()

    try:
        root.after(DELAY_MS, start)
    except Exception:
        return False
    return True
