"""v0.6.0 plan 3: a disc image or archive is filed under the artist and album its file name says, as a proposal."""
from __future__ import annotations

import os

import pytest

import albumname
import mover
import plan as plan_mod
import scan as scan_mod
import undo
from conftest import build, put, snapshot


@pytest.fixture(autouse=True)
def small_is_big(monkeypatch):
    monkeypatch.setattr(scan_mod, "BIG_FILE_BYTES", 1024)


def image(root, folder, name, body=b"A" * 5000, extras=True):
    d = root / folder if folder else root
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_bytes(body)
    if extras and folder:
        (d / "Artwork").mkdir(exist_ok=True)
        (d / "folder.jpg").write_bytes(b"cover")
        (d / "notes.txt").write_text("log", encoding="utf-8")
        (d / "Artwork" / "front.png").write_bytes(b"png")


def rel(p, path):
    return os.path.relpath(path, p.options.dest).replace(os.sep, "/")


# ------------------------------------------------------------------ reading the name
@pytest.mark.parametrize("name, want", [
    ("Minako Yoshida - Spangles.iso", ("Minako Yoshida", "Spangles", "")),
    ("Rie Yoshizawa - JUICY (2000).zip", ("Rie Yoshizawa", "JUICY", "2000")),
    ("0928_[J-Pop] Miho Nakayama - Catch the Nite (1988) (2022.King Records NKGD-29) [SACD.J-Pop].iso",
     ("Miho Nakayama", "Catch the Nite", "1988")),
    ("Minako Yoshida - Bells (Special Edition) (1986) (2002, Avex-Japan) [Flac].zip",
     ("Minako Yoshida", "Bells (Special Edition)", "1986")),  # an edition name is part of the album
    ("Minako Yoshida & Kazumi Watanabe - Nowadays.iso", ("Minako Yoshida & Kazumi Watanabe", "Nowadays", "")),
    ("Foo - Bar (2).iso", ("Foo", "Bar", "")),  # the number a copy got is not part of the album
    ("A - B - C.iso", ("A", "B - C", "")),
])
def test_names_that_say_whose_album_it_is(name, want):
    assert tuple(albumname.guess(name)) == want


@pytest.mark.parametrize("name", ["타카무라 아루 高村 亜留.zip", "disc.iso", "01 - 02.iso", "Foo - (2000).zip", "- Bar.iso", "Foo - .iso"])
def test_names_that_do_not(name):
    assert albumname.guess(name) is None


# ------------------------------------------------------------------ the plan
def test_a_named_image_is_proposed_but_not_ticked(tmp_path):
    image(tmp_path, "Minako Yoshida - Spangles (2006, Avex) [SACD]", "Minako Yoshida - Spangles.iso")
    _, p = build(tmp_path)
    [item] = p.items
    assert item.status == plan_mod.NAME and not item.checked and not item.moves and item.guess
    assert rel(p, item.dst) == "Minako Yoshida/Spangles/Minako Yoshida - Spangles.iso"  # the file keeps its name
    assert p.summary()["guessed"] == 1 and p.summary()["move"] == 0 and p.summary()["untagged"] == 0
    assert p.companions == []  # nothing moves, so nothing follows
    assert p.skipped_files == 0  # it is listed as a proposal, no longer "left alone"
    assert "name" in item.artist_note.lower()


def test_ticking_it_moves_the_whole_folder_along_and_a_rerun_is_quiet(tmp_path):
    folder = "Minako Yoshida - Spangles (2006, Avex) [SACD]"
    image(tmp_path, folder, "Minako Yoshida - Spangles.iso")
    before = snapshot(tmp_path)
    _, p0 = build(tmp_path)
    _, p = build(tmp_path, overrides={p0.items[0].key: True})
    assert p.items[0].moves
    assert sorted(rel(p, c.dst) for c in p.companions) == [
        "Minako Yoshida/Spangles/Artwork/front.png", "Minako Yoshida/Spangles/folder.jpg", "Minako Yoshida/Spangles/notes.txt"]
    run = mover.execute(p)
    assert not run.failed
    assert (tmp_path / "Minako Yoshida" / "Spangles" / "Minako Yoshida - Spangles.iso").exists()
    assert not (tmp_path / folder).exists()  # emptied and removed
    again = build(tmp_path, guesses=True)[1]
    assert again.summary()["move"] == 0 and [i.status for i in again.items] == [plan_mod.SAME]
    u = undo.undo(run.log_path)
    assert u.restored == 1 + 3 and not u.skipped
    os.remove(run.log_path)
    assert snapshot(tmp_path) == before


def test_an_image_in_the_source_folder_itself(tmp_path):
    image(tmp_path, "", "Rie Yoshizawa - JUICY (2000).zip")
    _, p = build(tmp_path, guesses=True)
    assert rel(p, p.items[0].dst) == "Rie Yoshizawa/JUICY/Rie Yoshizawa - JUICY (2000).zip"
    run = mover.execute(p)
    assert not run.failed
    assert build(tmp_path, guesses=True)[1].summary()["move"] == 0


def test_a_joint_credit_is_its_own_artist_folder(tmp_path):
    image(tmp_path, "x", "Minako Yoshida & Kazumi Watanabe - Nowadays.iso")
    _, p = build(tmp_path)
    assert rel(p, p.items[0].dst).startswith("Minako Yoshida & Kazumi Watanabe/Nowadays/")


def test_a_name_that_says_nothing_stays_where_it_is_and_is_counted(tmp_path):
    image(tmp_path, "타카무라", "타카무라 아루.zip")
    _, p = build(tmp_path, guesses=True)
    assert p.items == [] and p.skipped_files == 1


def test_the_year_pattern_does_not_move_it_again(tmp_path):
    image(tmp_path, "Minako Yoshida - Spangles (2006, Avex) [SACD]", "Minako Yoshida - Spangles.iso")
    pattern = "{album_artist|artist}/[{year}] {album}/{track:02} - {title}"
    _, p = build(tmp_path, guesses=True, pattern=pattern)
    run = mover.execute(p)
    assert not run.failed
    assert build(tmp_path, guesses=True, pattern=pattern)[1].summary()["move"] == 0
    # a name that carries a year gives it
    image(tmp_path, "", "Rie Yoshizawa - JUICY (2000).zip")
    p2 = build(tmp_path, guesses=True, pattern=pattern)[1]
    juicy = next(i for i in p2.items if "JUICY" in i.src)
    assert rel(p2, juicy.dst) == "Rie Yoshizawa/[2000] JUICY/Rie Yoshizawa - JUICY (2000).zip"


def test_two_files_with_one_name_are_numbered_not_overwritten(tmp_path):
    image(tmp_path, "a", "Foo - Bar.iso", body=b"A" * 5000)
    image(tmp_path, "b", "Foo - Bar.iso", body=b"B" * 5000)
    _, p = build(tmp_path, guesses=True)
    assert sorted(rel(p, i.dst) for i in p.items) == ["Foo/Bar/Foo - Bar (2).iso", "Foo/Bar/Foo - Bar.iso"]
    run = mover.execute(p)
    assert not run.failed
    assert build(tmp_path, guesses=True)[1].summary()["move"] == 0  # "Foo - Bar (2)" reads as the same album


def test_the_copy_that_stays_is_proposed_and_the_extra_one_is_a_duplicate(tmp_path):
    body = b"S" * 5000
    image(tmp_path, "Foo - Bar [SACD]", "Foo - Bar.iso", body=body)
    image(tmp_path, "Foo - Bar [SACD] (1)", "Foo - Bar.iso", body=body)
    _, p = build(tmp_path, groups="find")
    by_status = {i.status: i for i in p.items}
    assert set(by_status) == {plan_mod.NAME, plan_mod.DUP}
    assert by_status[plan_mod.DUP].checked and not by_status[plan_mod.NAME].checked
    assert by_status[plan_mod.NAME].src.endswith(os.path.join("Foo - Bar [SACD]", "Foo - Bar.iso"))
    assert rel(p, by_status[plan_mod.DUP].dst).startswith("_Duplicates/")


def test_copy_mode_copies_a_ticked_one_once(tmp_path):
    src, out = tmp_path / "in", tmp_path / "out"
    image(src, "Foo - Bar [SACD]", "Foo - Bar.iso")
    _, p = build(src, dest=out, mode="copy", guesses=True)
    assert [i.action for i in p.items] == [plan_mod.COPY]
    run = mover.execute(p)
    assert not run.failed and (out / "Foo" / "Bar" / "Foo - Bar.iso").exists()
    assert (src / "Foo - Bar [SACD]" / "Foo - Bar.iso").exists()
    assert build(src, dest=out, mode="copy", guesses=True)[1].summary()["move"] == 0  # not "Foo - Bar (2).iso"


def test_an_image_that_stays_keeps_the_folder_extras_with_it(tmp_path):
    """Songs and an image share a folder; only the songs are ticked. The extras stay beside the image."""
    folder = "Foo - Bar [SACD]"
    image(tmp_path, folder, "Foo - Bar.iso")
    put(tmp_path / folder, "tone.ogg", "01.ogg", title="One", artist="Foo", album="Bar", tracknumber="1")
    _, p = build(tmp_path)
    assert {i.status for i in p.items} == {plan_mod.OK, plan_mod.NAME}
    assert not [c for c in p.companions if c.kind == "sidecar"]  # a sidecar moves only when everyone in the folder does
    assert not [d for d in p.empty_dirs if d.endswith(folder)]


def test_cli_lists_the_proposal_and_moves_it_only_with_the_flag(tmp_path, capsys):
    import main

    image(tmp_path, "Foo - Bar [SACD]", "Foo - Bar.iso")
    assert main.main([str(tmp_path), "--from-names", "--dry-run", "--lang", "en"]) == 0
    out = capsys.readouterr().out
    assert "Guessed" in out and os.path.join("Foo", "Bar", "Foo - Bar.iso") in out
    assert "Files whose album was guessed from the name: 1" in out
    assert main.main([str(tmp_path), "--lang", "en"]) == 1  # no music, and nothing asked for: it only says what it saw
    assert (tmp_path / "Foo - Bar [SACD]" / "Foo - Bar.iso").exists()
    assert main.main([str(tmp_path), "--from-names", "--lang", "en"]) == 0
    assert (tmp_path / "Foo" / "Bar" / "Foo - Bar.iso").exists()
