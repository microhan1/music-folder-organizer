"""Language file loader and settings.json access.

All user-facing strings live in lang/<code>.json. Use ``t(key, **kwargs)``
to look one up; ``{placeholders}`` are filled from kwargs.
"""
from __future__ import annotations

import json
import locale
import os
import sys

APP_NAME = "music-folder-organizer"
LANGS = ("ko", "en", "zh-CN", "ja")
LANG_NAMES = {"ko": "한국어", "en": "English", "zh-CN": "简体中文", "ja": "日本語"}
DEFAULT_LANG = "en"


def resource_dir() -> str:
    """Folder holding lang/ (inside the PyInstaller bundle when frozen)."""
    return getattr(sys, "_MEIPASS", app_dir())


def app_dir() -> str:
    """Folder next to the executable (or main.py)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _writable(folder: str) -> bool:
    probe = os.path.join(folder, f".{APP_NAME}-probe")
    try:
        with open(probe, "w", encoding="utf-8"):
            pass
        os.remove(probe)
        return True
    except OSError:
        return False


def settings_dir() -> str:
    """Next to the exe; %APPDATA%/music-folder-organizer when that folder is read-only
    (Program Files) and the exe has not already got a settings.json there."""
    here = app_dir()
    if os.path.exists(os.path.join(here, "settings.json")) or _writable(here):
        return here
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    folder = os.path.join(base, APP_NAME)
    os.makedirs(folder, exist_ok=True)
    return folder


_settings_path: str | None = None


def settings_path() -> str:
    global _settings_path
    if _settings_path is None:
        _settings_path = os.path.join(settings_dir(), "settings.json")
    return _settings_path


def load_settings() -> dict:
    try:
        with open(settings_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(data: dict) -> None:
    try:
        with open(settings_path(), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def update_settings(**values) -> None:
    data = load_settings()
    data.update(values)
    save_settings(data)


def detect_os_lang() -> str:
    """Pick one of LANGS from the OS UI language. Falls back to English."""
    code = None
    if sys.platform == "win32":
        try:
            import ctypes

            lcid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            code = {0x12: "ko", 0x11: "ja", 0x04: "zh-CN", 0x09: "en"}.get(lcid & 0x3FF)
        except Exception:
            code = None
    if code is None:
        loc = ""
        for env in ("LC_ALL", "LC_MESSAGES", "LANG"):
            if os.environ.get(env):
                loc = os.environ[env]
                break
        if not loc:
            try:
                loc = locale.getlocale()[0] or ""
            except Exception:
                loc = ""
        loc = loc.replace("-", "_").lower()
        if loc.startswith(("ko", "korean")):
            code = "ko"
        elif loc.startswith(("ja", "japanese")):
            code = "ja"
        elif loc.startswith(("zh", "chinese")):
            code = "zh-CN"
        elif loc.startswith(("en", "english")):
            code = "en"
    return code or DEFAULT_LANG


def load_lang_file(lang: str) -> dict[str, str]:
    path = os.path.join(resource_dir(), "lang", f"{lang}.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


class _I18n:
    def __init__(self) -> None:
        self.lang = DEFAULT_LANG
        self._strings: dict[str, str] = {}
        self._fallback: dict[str, str] = load_lang_file(DEFAULT_LANG)

    def set_lang(self, lang: str, persist: bool = True) -> None:
        if lang not in LANGS:
            lang = DEFAULT_LANG
        self.lang = lang
        self._strings = load_lang_file(lang)
        if persist:
            update_settings(lang=lang)

    def t(self, key: str, **kwargs) -> str:
        text = self._strings.get(key) or self._fallback.get(key) or key
        if not kwargs:
            return text
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            return text


_inst = _I18n()


def init(lang: str | None = None) -> str:
    """Load the language: explicit > settings.json > OS language > English."""
    if lang is None:
        lang = load_settings().get("lang")
    if lang not in LANGS:
        lang = detect_os_lang()
    _inst.set_lang(lang, persist=False)
    return lang


def set_lang(lang: str, persist: bool = True) -> None:
    _inst.set_lang(lang, persist=persist)


def current_lang() -> str:
    return _inst.lang


def t(key: str, **kwargs) -> str:
    return _inst.t(key, **kwargs)


def all_values(key: str) -> set[str]:
    """The value of one key in every language (e.g. every name the dupes folder may have)."""
    out = set()
    for lang in LANGS:
        value = load_lang_file(lang).get(key)
        if value:
            out.add(value)
    return out
