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

API = "https://chaekgalpi.co.kr/api/tools/{slug}/latest"
SITE = "https://chaekgalpi.co.kr/"  # the notice only ever opens a page on this site
TIMEOUT = 4.0
DELAY_MS = 1500
MAX_BYTES = 64 * 1024


@dataclasses.dataclass
class UpdateInfo:
    latest: str
    current: str
    url: str


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
    return UpdateInfo(latest, version, url)


def check_async(root, slug: str, version: str, prefs, on_result: Callable[[UpdateInfo | None, bool], None], *,
                fetcher: Callable = fetch, day: str | None = None) -> bool:
    """Schedule one check DELAY_MS after now. on_result(info, answered) runs on the Tk
    thread: `answered` is True when the server replied (the caller records the day then),
    `info` is set only when a notice should be shown. Returns False when no check is due."""
    if not should_check(prefs, day or today()):
        return False
    skipped = getattr(prefs, "update_check_skipped", "")

    def work() -> None:
        data = fetcher(slug, version)
        info = interpret(data, version, skipped)
        try:
            root.after(0, lambda: on_result(info, data is not None))
        except Exception:  # the window was closed meanwhile
            pass

    def start() -> None:
        threading.Thread(target=work, name="update-check", daemon=True).start()

    try:
        root.after(DELAY_MS, start)
    except Exception:
        return False
    return True
