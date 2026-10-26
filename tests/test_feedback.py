"""[Send feedback]: the address it builds, its strings and the untouched command line. The
button in the window is tested in test_gui.py (one Tk per process there). Nothing here
opens a browser or reaches the site (conftest.no_network)."""
from __future__ import annotations

import urllib.parse

import pytest

import i18n
import update_check as uc
from version import APP_VERSION

SLUG = "musicfolder"
KO = "https://chaekgalpi.co.kr/tools/musicfolder?utm_source=app&utm_medium=feedback&v=0.2.1#feedback"
EN = "https://chaekgalpi.co.kr/en/tools/musicfolder?utm_source=app&utm_medium=feedback&v=0.2.1#feedback"


def query_of(url: str) -> dict:
    return urllib.parse.parse_qs(urllib.parse.urlsplit(url).query, keep_blank_values=True)


# ------------------------------------------------------------------ a. the address per language
@pytest.mark.parametrize("lang,expected", [("ko", KO), ("en", EN), ("ja", EN), ("zh-CN", EN)])
def test_address_for_each_language(lang, expected):
    assert uc.feedback_url(SLUG, "0.2.1", lang) == expected


@pytest.mark.parametrize("lang", [None, "", "xx", "de", "zh-TW", "fr-FR", "  "])
def test_unknown_language_gets_the_korean_page(lang):
    assert uc.feedback_url(SLUG, "0.2.1", lang) == KO


@pytest.mark.parametrize("lang", ["EN", "En", "JA", "zh-cn", "ZH-CN", " en "])
def test_language_code_case_and_spaces_do_not_matter(lang):
    assert uc.feedback_url(SLUG, "0.2.1", lang) == EN


@pytest.mark.parametrize("lang", ["ko", "en", "ja", "zh-CN", None, "xx"])
@pytest.mark.parametrize("version", ["0.2.1", "", None, "a b&c#d"])
def test_address_shape(lang, version):
    url = uc.feedback_url(SLUG, version, lang)
    parts = urllib.parse.urlsplit(url)
    assert url.startswith("https://chaekgalpi.co.kr/") and url.startswith(uc.SITE)
    assert url.endswith("#feedback") and parts.fragment == "feedback" and url.count("#") == 1
    assert parts.netloc == "chaekgalpi.co.kr" and parts.path.endswith("/tools/musicfolder")
    q = query_of(url)
    assert q["utm_source"] == ["app"] and q["utm_medium"] == ["feedback"]


# ------------------------------------------------------------------ b. the version
@pytest.mark.parametrize("version", ["0.2.1", "v0.2.1", "1.0.0-beta+3", "0.5.1", APP_VERSION])
def test_version_is_carried_as_is(version):
    url = uc.feedback_url(SLUG, version, "ko")
    assert query_of(url)["v"] == [version]


def test_plus_is_encoded_not_read_as_a_space():
    url = uc.feedback_url(SLUG, "1.0.0-beta+3", "en")
    assert "v=1.0.0-beta%2B3#" in url


@pytest.mark.parametrize("version", ["", None, "   "])
def test_no_version_leaves_out_v(version):
    url = uc.feedback_url(SLUG, version, "ko")
    assert "v" not in query_of(url) and "&v=" not in url
    assert url == "https://chaekgalpi.co.kr/tools/musicfolder?utm_source=app&utm_medium=feedback#feedback"


@pytest.mark.parametrize("version", ["0.2 1", "1&utm_source=evil", "1#top", "버전 0.2", "a=b?c/d", "%41", "v1\t2",
                                     "1.0;drop", "日本語+中文"])
def test_odd_versions_are_encoded_and_do_not_break_the_address(version):
    url = uc.feedback_url(SLUG, version, "ko")
    parts = urllib.parse.urlsplit(url)
    q = query_of(url)
    assert q["v"] == [version]  # read back exactly
    assert q["utm_source"] == ["app"] and q["utm_medium"] == ["feedback"] and set(q) == {"utm_source", "utm_medium", "v"}
    assert parts.fragment == "feedback" and parts.path == "/tools/musicfolder"
    assert " " not in url and url.isascii()


def test_app_version_is_the_one_sent():
    assert query_of(uc.feedback_url(SLUG, APP_VERSION, "ko"))["v"] == [APP_VERSION]


# ------------------------------------------------------------------ e. strings
NEW_KEYS = ("btn_feedback", "msg_feedback_failed", "msg_feedback_failed_status")


@pytest.mark.parametrize("lang", i18n.LANGS)
def test_new_strings_exist_in_every_language(lang):
    i18n.set_lang(lang, persist=False)
    for key in NEW_KEYS:
        text = i18n.t(key, url="URL")
        assert text.strip() and text != key, (lang, key)
    assert "URL" in i18n.t("msg_feedback_failed", url="URL") and "URL" in i18n.t("msg_feedback_failed_status", url="URL")


def test_button_text_per_language():
    expected = {"ko": "의견 보내기", "en": "Send feedback", "ja": "ご意見を送る", "zh-CN": "发送反馈"}
    for lang, text in expected.items():
        i18n.set_lang(lang, persist=False)
        assert i18n.t("btn_feedback") == text


# ------------------------------------------------------------------ f. the command line is untouched
def test_cli_help_still_works_and_has_no_new_option(capsys):
    import main

    with pytest.raises(SystemExit) as exc:
        main.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "--undo" in out and "feedback" not in out.lower()


def test_cli_version_option_still_does_not_exist(capsys):
    """The CLI has never had --version; adding the button must not change that (argparse error)."""
    import main

    with pytest.raises(SystemExit) as exc:
        main.main(["--version"])
    assert exc.value.code == 2
