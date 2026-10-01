import json
import os
import re

import i18n

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRD_KEYS = ["app_title", "drop_hint", "tab_organize", "tab_dupes", "pattern_label", "dest_label", "opt_move", "opt_copy",
            "opt_remove_empty", "opt_fingerprint", "col_current", "col_new", "col_status", "col_keep", "status_same",
            "status_untagged", "status_dup", "status_conflict", "summary", "fallback_artist", "fallback_album",
            "btn_run", "btn_undo", "btn_export_untagged", "msg_done", "msg_untagged_hint", "msg_no_log"]


def load(code):
    with open(os.path.join(ROOT, "lang", f"{code}.json"), encoding="utf-8") as f:
        return json.load(f)


def test_same_keys_and_placeholders_in_every_language():
    en = load("en")
    for code in i18n.LANGS:
        data = load(code)
        assert set(data) == set(en), code
        for key, text in data.items():
            assert set(re.findall(r"\{(\w+)", text)) == set(re.findall(r"\{(\w+)", en[key])), (code, key)


def test_prd_keys_exist():
    en = load("en")
    assert [k for k in PRD_KEYS if k not in en] == []


def test_no_hard_coded_strings_in_dupes_folder():
    assert {load(c)["dupes_folder"] for c in i18n.LANGS} == {"_중복", "_Duplicates", "_重复", "_重複"}


def test_switching_language():
    i18n.set_lang("ko", persist=False)
    assert i18n.t("btn_run") == "실행"
    i18n.set_lang("ja", persist=False)
    assert i18n.t("summary", move=1, same=2, untagged=3, dupes=4, folders=5).startswith("移動 1")
    i18n.set_lang("xx", persist=False)
    assert i18n.current_lang() == "en"
