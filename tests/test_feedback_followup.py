"""[Send feedback] follow-up across the series (2026-10-28): which tools page a screen
language gets, for the feedback box and for [Update] alike, and the links in the four
READMEs and the release notes. The keyboard and the tooltip are in test_gui.py (one Tk per
process there). Nothing here opens a browser or reaches the site (conftest.no_network)."""
from __future__ import annotations

import pathlib
import re
import urllib.parse

import pytest

import i18n
import update_check as uc
from version import APP_VERSION

ROOT = pathlib.Path(__file__).resolve().parents[1]
SLUG = "musicfolder"
KO_PAGE = "https://chaekgalpi.co.kr/tools/musicfolder"
EN_PAGE = "https://chaekgalpi.co.kr/en/tools/musicfolder"

# ------------------------------------------------------------------ c. the page language rule
KOREAN = ["ko", "ko-KR", "ko_KR", "KO", "Ko-kr", " ko ", "ko-Kore-KR"]
OTHERS = ["en", "EN", "en-US", "en_GB", "ja", "ja-JP", "zh", "zh-CN", "zh_CN", "zh-cn", "zh-TW", "zh-Hant-TW",
          "fr", "fr-FR", "de", "es", "pt-BR", "ru", "vi", "xx", "kor", "k", "jp", "-ko", "en-KO"]
BLANK = [None, "", " ", "   ", "\t"]


def path_of(url: str) -> str:
    return urllib.parse.urlsplit(url).path


@pytest.mark.parametrize("lang", KOREAN)
def test_korean_codes_get_the_korean_page(lang):
    assert uc.is_korean(lang)
    assert path_of(uc.feedback_url(SLUG, "1.2.3", lang)) == "/tools/musicfolder"


@pytest.mark.parametrize("lang", OTHERS)
def test_every_other_code_gets_the_english_page(lang):
    assert not uc.is_korean(lang)
    assert path_of(uc.feedback_url(SLUG, "1.2.3", lang)) == "/en/tools/musicfolder"


@pytest.mark.parametrize("lang", BLANK)
def test_a_blank_language_follows_the_default_screen_language(lang):
    assert uc.feedback_url(SLUG, "1.2.3", lang) == uc.feedback_url(SLUG, "1.2.3", i18n.DEFAULT_LANG)
    assert uc.is_korean(lang) == uc.is_korean(i18n.DEFAULT_LANG) is False


def test_the_default_screen_language_is_english():
    """What the tool shows when it cannot tell (i18n.set_lang of an unknown code)."""
    assert uc.DEFAULT_LANG is i18n.DEFAULT_LANG == "en"
    i18n.set_lang("xx", persist=False)
    assert i18n.current_lang() == "en"


@pytest.mark.parametrize("lang", i18n.LANGS)
def test_the_four_screen_languages(lang):
    expect = KO_PAGE if lang == "ko" else EN_PAGE
    assert uc.feedback_url(SLUG, APP_VERSION, lang).startswith(expect + "?")


@pytest.mark.parametrize("lang", KOREAN + OTHERS + BLANK)
@pytest.mark.parametrize("version", ["1.2.3", "", None, APP_VERSION])
def test_every_address_has_the_same_shape(lang, version):
    url = uc.feedback_url(SLUG, version, lang)
    parts = urllib.parse.urlsplit(url)
    assert url.startswith(uc.SITE) and url.endswith("#feedback") and url.count("#") == 1
    assert parts.scheme == "https" and parts.netloc == "chaekgalpi.co.kr"
    assert parts.path in ("/tools/musicfolder", "/en/tools/musicfolder")
    q = urllib.parse.parse_qs(parts.query)
    assert q["utm_source"] == ["app"] and q["utm_medium"] == ["feedback"]
    assert q.get("v", [""]) == [(version or "").strip()] or not (version or "").strip()
    assert url.isascii() and " " not in url


INFO = uc.UpdateInfo("9.0.0", "0.5.1", KO_PAGE + "?utm_source=app", EN_PAGE + "?utm_source=app")


@pytest.mark.parametrize("lang", KOREAN)
def test_update_opens_the_korean_page_for_korean(lang):
    assert uc.page_for(INFO, lang) == INFO.url


@pytest.mark.parametrize("lang", OTHERS + BLANK)
def test_update_follows_the_same_rule(lang):
    assert uc.page_for(INFO, lang) == INFO.url_en
    assert ("/en/" in uc.page_for(INFO, lang)) == ("/en/" in uc.feedback_url(SLUG, "1", lang))


@pytest.mark.parametrize("lang", KOREAN + OTHERS + BLANK)
def test_update_without_an_english_page_opens_the_one_it_has(lang):
    assert uc.page_for(uc.UpdateInfo("9.0.0", "0.5.1", KO_PAGE), lang) == KO_PAGE


def test_one_function_decides_both(monkeypatch):
    """Changing is_korean changes the feedback page and the update page together."""
    monkeypatch.setattr(uc, "is_korean", lambda lang: True)
    assert path_of(uc.feedback_url(SLUG, "1", "en")) == "/tools/musicfolder" and uc.page_for(INFO, "en") == INFO.url
    monkeypatch.setattr(uc, "is_korean", lambda lang: False)
    assert path_of(uc.feedback_url(SLUG, "1", "ko")) == "/en/tools/musicfolder" and uc.page_for(INFO, "ko") == INFO.url_en


# ------------------------------------------------------------------ d. README and release-note links
READMES = {"ko": "README.md", "en": "README.en.md", "ja": "README.ja.md", "zh-CN": "README.zh-CN.md"}
MD_LINK = re.compile(r"\]\((https?://[^)\s]*)\)")
BARE = re.compile(r"https?://[^\s<>]+")
GFM_TRAILING = "?!.,:*_~'\""  # GitHub drops these from the end of an autolink
URL_CHARS = re.compile(r"[A-Za-z0-9\-._~:/?#\[\]@!$&'*+,;=%]+")


def read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def links(text: str) -> list[tuple[str, str]]:
    """(kind, href as GitHub renders it) for every markdown link and bare autolink."""
    out = [("md", m.group(1)) for m in MD_LINK.finditer(text)]
    for m in BARE.finditer(text):
        if text[max(0, m.start() - 2):m.start()] == "](":
            continue
        out.append(("bare", m.group(0)))
    return out


def problems(text: str) -> list[str]:
    """Links whose href would swallow a bracket, Hangul or full-width punctuation."""
    bad = []
    for kind, raw in links(text):
        href = raw.rstrip(GFM_TRAILING) if kind == "bare" else raw
        if not URL_CHARS.fullmatch(href) or href.endswith(")") or "(" in href:
            bad.append(f"{kind}: {raw}")
    return bad


def site_links(text: str) -> tuple[list[str], list[str]]:
    """(feedback links, tools-page links) to this tool on the 책갈피 site."""
    hrefs = [h.rstrip(GFM_TRAILING) for _, h in links(text) if "chaekgalpi.co.kr/" in h]
    tool = [h for h in hrefs if re.match(r"https://chaekgalpi\.co\.kr/(en/)?tools/musicfolder", h)]
    return [h for h in tool if h.endswith("#feedback")], [h for h in tool if not h.endswith("#feedback")]


@pytest.mark.parametrize("lang", READMES)
def test_readme_feedback_link_is_the_page_for_its_language(lang):
    feedback, _ = site_links(read(READMES[lang]))
    assert feedback == [(KO_PAGE if lang == "ko" else EN_PAGE) + "#feedback"], feedback


@pytest.mark.parametrize("lang", READMES)
def test_readme_tools_page_links_are_the_page_for_its_language(lang):
    _, pages = site_links(read(READMES[lang]))
    expect = KO_PAGE if lang == "ko" else EN_PAGE
    assert all(urllib.parse.urlsplit(p)._replace(query="", fragment="").geturl() == expect for p in pages), pages
    if lang == "en":
        assert pages, "the English README links the English tools page"
        assert all("utm_source=github" in p for p in pages)  # the tail is kept


@pytest.mark.parametrize("lang", READMES)
def test_readme_links_end_cleanly(lang):
    text = read(READMES[lang])
    assert links(text) and problems(text) == []


def test_japanese_and_chinese_readmes_have_no_tools_page_link():
    """Only the feedback link (already the English page). Nothing was added: a note that the
    책갈피 service itself is Korean-only would be true and is left alone where it exists."""
    for name in ("README.ja.md", "README.zh-CN.md"):
        _, pages = site_links(read(name))
        assert pages == []


@pytest.mark.parametrize("name", sorted(p.name for p in (ROOT / "docs" / "releases").glob("*.md")))
def test_release_note_links_end_cleanly(name):
    assert problems(read(f"docs/releases/{name}")) == []


def test_the_checker_catches_broken_links():
    """The rule itself: these are the shapes that broke on 2026-10-27."""
    broken = ["의견 칸(https://chaekgalpi.co.kr/tools/musicfolder#feedback)이 열리고",
              "https://chaekgalpi.co.kr/tools/musicfolder#feedback에서",
              "或 https://chaekgalpi.co.kr/en/tools/musicfolder#feedback，免登录",
              "（https://chaekgalpi.co.kr/en/tools/musicfolder#feedback）",
              "https://chaekgalpi.co.kr/en/tools/musicfolder#feedback。"]
    for text in broken:
        assert problems(text), text
    fine = ["[의견 칸](https://chaekgalpi.co.kr/tools/musicfolder#feedback)이 열리고",
            "또는 https://chaekgalpi.co.kr/tools/musicfolder#feedback 에서",
            "at https://chaekgalpi.co.kr/en/tools/musicfolder#feedback, no sign-in needed."]
    for text in fine:
        assert problems(text) == [], text
