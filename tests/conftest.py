from __future__ import annotations

import hashlib
import os
import shutil
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLES = os.path.join(ROOT, "samples")
sys.path.insert(0, ROOT)

import i18n  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path_factory, monkeypatch):
    """Every test gets its own settings.json (outside the folders it snapshots) and English strings."""
    monkeypatch.setattr(i18n, "_settings_path", str(tmp_path_factory.mktemp("settings") / "settings.json"))
    i18n.set_lang("en", persist=False)
    yield


def sample(name: str) -> str:
    path = os.path.join(SAMPLES, name)
    if not os.path.exists(path):
        pytest.skip(f"run samples/make_samples.py first ({name} missing)")
    return path


def put(folder, sample_name: str, rel: str, **tags) -> str:
    """Copy a sample to folder/rel and write tags (mutagen easy keys)."""
    import mutagen

    dst = os.path.join(str(folder), rel)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copyfile(sample(sample_name), dst)
    if tags:
        audio = mutagen.File(dst, easy=True)
        if audio.tags is None:
            audio.add_tags()
        for k, v in tags.items():
            audio[k] = v
        audio.save()
    return dst


def snapshot(root) -> dict:
    files, dirs = {}, []
    for here, ds, fs in os.walk(str(root)):
        dirs.append(os.path.relpath(here, str(root)))
        for f in fs:
            p = os.path.join(here, f)
            with open(p, "rb") as fh:
                files[os.path.relpath(p, str(root))] = hashlib.sha1(fh.read()).hexdigest()
    return {"files": files, "dirs": sorted(dirs)}


def build(root, dest=None, mode="move", groups=None, artists=None, overrides=None, **pref):
    """scan -> artist index -> (dupes) -> plan, the way the GUI and CLI do it."""
    import dedupe
    import plan as plan_mod
    import prefs as prefs_mod
    import scan as scan_mod
    import session

    p = prefs_mod.Prefs(artists_path=artists or os.path.join(str(root), "..", "artists.json"), **pref)
    dest = str(dest or root)
    res = scan_mod.scan(str(root), session.excludes(str(root), dest, p))
    index, _ = session.make_index(res.tracks, p, session.existing_folders(res, dest))
    opts = session.options(p, str(root), dest, mode)
    if groups == "find":
        groups = dedupe.find(res.tracks, index.rep)
    return res, plan_mod.build(res, opts, index, groups, overrides)
