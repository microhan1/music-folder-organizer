"""Hand-designed scenarios behind the randomised test_scenarios.py: one test per defect it found
(LESSONS A27-A31), so a regression names itself."""
import os
import re
import shutil
import threading

import pytest
from conftest import build, put, snapshot

import i18n
import mover
import plan as plan_mod
import scan
import undo

LONG = "t" * 100


def idle(p) -> list:
    acts = [(i.track.name, os.path.relpath(i.dst, p.options.dest) if i.dst else "(trash)") for i in p.items
            if i.moves and i.checked]
    acts += [(os.path.basename(c.src), c.kind) for c in p.companions if scan.key_of(c.src) != scan.key_of(c.dst)]
    acts += [("rmdir", d) for d in p.empty_dirs]
    return acts


def quiet_second_run(folder, **pref):
    _, p1 = build(folder, **pref)
    res = mover.execute(p1)
    assert not res.failed, res.failed
    _, p2 = build(folder, **pref)
    assert idle(p2) == [], idle(p2)
    return p1


# ------------------------------------------------------------------ A27: numbering that never ended
def test_long_names_get_numbers_instead_of_hanging(tmp_path):
    """A name cut to the path limit lost its " (2)" too: every number gave the same path and the loop
    in plan._place never ended."""
    folder = tmp_path / "in1"
    deep = folder / ("d" * 60) / ("e" * 60)
    for n in range(4):
        put(deep, "song-128.mp3", f"{n}.mp3", artist="A" * 50, album="B" * 50, title=LONG, tracknumber="1")
        # a different tag value keeps the files different; the target names are what must be told apart
    box = {}
    t = threading.Thread(target=lambda: box.update(res=build(folder)), daemon=True)
    t.start()
    t.join(30)
    assert not t.is_alive(), "planning never ended"
    _, p = box["res"]
    dsts = [i.dst for i in p.items]
    assert len({scan.target_key(d) for d in dsts}) == 4
    assert all(len(d) <= 240 for d in dsts), max(map(len, dsts))
    numbers = sorted(m.group(0) if (m := re.search(r" \(\d+\)$", os.path.splitext(d)[0])) else "" for d in dsts)
    assert numbers == ["", " (2)", " (3)", " (4)"], numbers  # the number survives the cut, one per file


# ------------------------------------------------------------------ A28: names that grew on every run
@pytest.mark.parametrize("pattern", ["{album_artist|artist}/{album}/{track:02} - {title}", "{artist} - {title}",
                                     "{year}/{artist} - {title}", "{artist}/{disc}-{track:02} {title}",
                                     "{track:02} {title}"])
def test_files_without_a_title_keep_their_name_and_runs_stay_quiet(tmp_path, pattern):
    folder = tmp_path / "in1"
    put(folder, "song-128.mp3", "x/01 track01.mp3")  # no tags at all
    put(folder, "song.flac", "x/1-02 1.x.flac", artist="Minako", album="Alb", tracknumber="2", discnumber="1")  # no title
    put(folder, "song-320.mp3", "y/07. x.mp3", artist="Minako", album="Alb")
    p1 = quiet_second_run(folder, include_untagged=True, pattern=pattern)
    names = sorted(os.path.basename(i.dst) for i in p1.items)
    assert names == ["01 track01.mp3", "07. x.mp3", "1-02 1.x.flac"] or all(
        n.endswith(("track01.mp3", "x.mp3", "1.x.flac")) for n in names)
    assert not any("01 01" in n or "Minako - Minako" in n for n in names)


# ------------------------------------------------------------------ A29: cue albums
def cue_album(folder, albums):
    """Tracks named by a cue sheet; each tagged with the next album of ``albums`` (one album: the whole
    folder moves together, several: it scatters)."""
    for n, album in enumerate(albums, 1):
        put(folder, "song-128.mp3", f"{n:02d}.song.mp3", artist="Art", album=album, title=f"Song {n}", tracknumber=str(n))
    cue = os.path.join(str(folder), "album.cue")
    with open(cue, "w", encoding="utf-8") as f:
        f.write("\n".join(f'FILE "{n:02d}.song.mp3" WAVE' for n in range(1, len(albums) + 1)))


def test_cue_album_that_moves_whole_keeps_names_and_cue_goes_along(tmp_path):
    folder = tmp_path / "in1"
    cue_album(folder / "src", ["One", "One", "One"])
    p1 = quiet_second_run(folder)
    assert sorted(os.path.basename(i.dst) for i in p1.items) == ["01.song.mp3", "02.song.mp3", "03.song.mp3"]
    assert os.path.exists(folder / "Art" / "One" / "album.cue")


@pytest.mark.parametrize("sidecars", [True, False])
def test_cue_album_that_scatters_is_renamed_once_and_then_quiet(tmp_path, sidecars):
    """The sheet stays in the old folder when the album splits, so the names it holds are lost anyway:
    renaming now, once, beats renaming on the second run."""
    folder = tmp_path / "in1"
    cue_album(folder / "src", ["One", "Two", "Three"])
    p1 = quiet_second_run(folder, move_sidecars=sidecars)
    assert sorted(os.path.basename(i.dst) for i in p1.items) == ["01 - Song 1.mp3", "01 - Song 2.mp3", "01 - Song 3.mp3"] \
        or all(" - Song " in os.path.basename(i.dst) for i in p1.items)


def test_cue_album_moved_with_sidecars_off_is_renamed_once(tmp_path):
    folder = tmp_path / "in1"
    cue_album(folder / "src", ["One", "One"])
    p1 = quiet_second_run(folder, move_sidecars=False)
    assert all(" - Song " in os.path.basename(i.dst) for i in p1.items)
    assert os.path.exists(folder / "src" / "album.cue")  # the sheet is left behind, untouched


def test_cue_name_shortened_for_the_path_limit_is_renamed_once(tmp_path):
    folder = tmp_path / "in1"
    long_name = "01 - " + LONG
    put(folder / "src", "song-128.mp3", long_name + ".mp3", artist="A" * 70, album="B" * 70, title="Song", tracknumber="1")
    with open(folder / "src" / "album.cue", "w", encoding="utf-8") as f:
        f.write(f'FILE "{long_name}.mp3" WAVE')
    quiet_second_run(folder)


# ------------------------------------------------------------------ A30: stand-in names and the language
@pytest.mark.parametrize("first, second", [("ko", "en"), ("en", "ja"), ("zh-CN", "ko")])
def test_stand_in_names_survive_a_language_switch(tmp_path, first, second):
    folder = tmp_path / "in1"
    put(folder, "song-128.mp3", "a.mp3", artist="A", title="No Album", tracknumber="1")  # no album, no year, no genre
    put(folder, "song-320.mp3", "b.mp3", title="No Artist", album="Alb", tracknumber="1", date="2020")
    for pattern in ("{artist}/{album}/{track:02} - {title}", "{year}/{artist} - {title}", "{genre}/{album}/{title}"):
        shutil.rmtree(folder, ignore_errors=True)
        put(folder, "song-128.mp3", "a.mp3", artist="A", title="No Album", tracknumber="1")
        put(folder, "song-320.mp3", "b.mp3", title="No Artist", album="Alb", tracknumber="1", date="2020")
        i18n.set_lang(first, persist=False)
        _, p1 = build(folder, pattern=pattern, include_untagged=True)
        assert not mover.execute(p1).failed
        i18n.set_lang(second, persist=False)
        _, p2 = build(folder, pattern=pattern, include_untagged=True)
        assert idle(p2) == [], (pattern, first, second, idle(p2))


def test_a_stand_in_name_typed_in_settings_is_not_touched_by_the_language(tmp_path):
    folder = tmp_path / "in1"
    put(folder, "song-128.mp3", "a.mp3", artist="A", title="T", tracknumber="1")
    i18n.set_lang("ko", persist=False)
    _, p = build(folder, fallbacks={"album": "Nothing"})
    assert "Nothing" in p.items[0].dst and "알 수 없는" not in p.items[0].dst


# ------------------------------------------------------------------ A31: the artist spelling written on the disk
def test_artist_folder_spelling_stays_when_the_files_that_carried_it_leave(tmp_path):
    """"岡田 有希子" (1 file) and "岡田有希子" (2 files, duplicates): the folder takes the bigger spelling; once
    the duplicates are gone, the one file left carries the other spelling and must not rename the folder."""
    folder = tmp_path / "in1"
    put(folder, "song-128.mp3", "x/a.mp3", artist="岡田有希子", album="Alb", title="Song", tracknumber="1")
    shutil.copy2(folder / "x" / "a.mp3", folder / "x" / "copy.mp3")
    put(folder, "song-320.mp3", "y/b.mp3", artist="岡田 有希子", album="Alb", title="Other", tracknumber="2")
    _, p1 = build(folder, groups="find")
    assert not mover.execute(p1).failed
    assert os.path.isdir(folder / "岡田有希子")
    for f in list(scan.scan(str(folder)).tracks):
        if f.tags.artist == "岡田有希子":
            os.remove(f.path)  # the user cleans up: only the "岡田 有希子" file is left
    _, p2 = build(folder, groups="find")
    assert idle(p2) == [], idle(p2)


def test_alias_form_on_the_disk_stays_when_only_the_native_tag_is_left(tmp_path):
    """"Minako Yoshida (吉田美奈子)" (2 duplicate files) and "吉田美奈子" (1 file), Latin preferred: the folder gets
    the Latin form. Once the duplicates are gone, only the bare "吉田美奈子" tag is left, and both spellings are on
    the disk: the preferred script wins, the folder is not renamed."""
    folder = tmp_path / "in1"
    put(folder, "song-128.mp3", "x/a.mp3", artist="Minako Yoshida (吉田美奈子)", album="Alb", title="Song", tracknumber="1")
    shutil.copy2(folder / "x" / "a.mp3", folder / "x" / "copy.mp3")
    put(folder, "song-320.mp3", "y/b.mp3", artist="吉田美奈子", album="Alb", title="Other", tracknumber="2")
    _, p1 = build(folder, groups="find", artist_name_preference="latin")
    assert not mover.execute(p1).failed
    assert os.path.isdir(folder / "Minako Yoshida (吉田美奈子)")
    for t in list(scan.scan(str(folder)).tracks):
        if t.tags.artist.startswith("Minako"):
            os.remove(t.path)
    _, p2 = build(folder, groups="find", artist_name_preference="latin")
    assert idle(p2) == [], idle(p2)


@pytest.mark.parametrize("preference, expected", [("latin", "Minako Yoshida (吉田美奈子)"), ("original", "吉田美奈子")])
def test_with_both_spellings_on_the_disk_the_preferred_script_decides(preference, expected):
    """Seed 482: after a run both folders exist, only the bare tag is left, and the file count would pick it
    whatever the setting says. Both are 'on the disk', so the preference has to break the tie."""
    from artists import ArtistIndex
    from scan import TagSet, Track
    from session import ExistingNames

    existing = ExistingNames()
    for name in ("吉田美奈子", "Minako Yoshida (吉田美奈子)"):
        existing.add_name(name)
    track = Track("C:\\m\\a.mp3", "MP3", 1, tags=TagSet(artist="吉田美奈子", title="x"))
    index = ArtistIndex([track], None, preference, existing=existing)
    assert index.rep("吉田美奈子") == expected


@pytest.mark.parametrize("pattern", ["{year}/{artist} - {title}", "{artist} - {title}"])
def test_artist_case_variant_in_file_names_stays(tmp_path, pattern):
    folder = tmp_path / "in1"
    put(folder, "song-128.mp3", "a/1.mp3", artist="Artist A", title="One", tracknumber="1", date="2020")
    put(folder, "song-320.mp3", "a/2.mp3", artist="ARTIST A", title="Two", tracknumber="2", date="2020")
    put(folder, "song-128.mp3", "a/3.mp3", artist="ARTIST A", title="Three", tracknumber="3", date="2020")
    _, p1 = build(folder, pattern=pattern)
    assert not mover.execute(p1).failed
    written = {os.path.basename(i.dst).split(" - ")[0] for i in p1.items}
    assert len(written) == 1
    os.remove(next(i.dst for i in p1.items if os.path.basename(i.dst).endswith("Three.mp3")))  # counts change
    _, p2 = build(folder, pattern=pattern)
    assert idle(p2) == [], idle(p2)
    assert not undo.undo(undo.find_log(str(folder))).log_error


# ------------------------------------------------------------------ the same situations photo-organizer's audit found
def tracks(folder, n=4, **kw):
    for k in range(1, n + 1):
        put(folder, ["song-128.mp3", "song-320.mp3", "song.flac", "other.mp3"][k % 4], f"x/{k:02d}.mp3" if k % 4 in (0, 1, 3) else f"x/{k:02d}.flac",
            artist="Art", album="Alb", title=f"Song {k}", tracknumber=str(k), **kw)


def test_copy_run_again_does_not_pile_copies_into_the_duplicates_folder(tmp_path):
    folder = tmp_path / "in1"
    out = tmp_path / "out"
    tracks(folder)
    _, p1 = build(folder, dest=out, mode="copy")
    assert not mover.execute(p1).failed
    first = snapshot(out)
    _, p2 = build(folder, dest=out, mode="copy")
    acts = [(i.track.name, i.status, os.path.relpath(i.dst, out)) for i in p2.items if i.moves and i.checked]
    assert acts == [], acts
    if p2.items and any(i.moves and i.checked for i in p2.items):
        mover.execute(p2)
    assert snapshot(out) == first


@pytest.mark.skipif(os.name != "nt", reason="NTFS junctions")
def test_folder_junction_loop_does_not_hang_the_scan(tmp_path):
    import subprocess

    folder = tmp_path / "in1"
    put(folder, "song-128.mp3", "a/01.mp3", artist="A", album="B", title="T", tracknumber="1")
    made = subprocess.run(["cmd", "/c", "mklink", "/J", str(folder / "a" / "loop"), str(folder)], capture_output=True)
    if made.returncode != 0:
        pytest.skip("cannot create a junction here")
    box = {}
    t = threading.Thread(target=lambda: box.update(res=scan.scan(str(folder))), daemon=True)
    t.start()
    t.join(20)
    assert not t.is_alive(), "the scan followed the junction forever"
    assert [x.name for x in box["res"].tracks] == ["01.mp3"]


def test_cancel_mid_run_then_run_again_finishes_and_undo_restores_everything(tmp_path):
    folder = tmp_path / "in1"
    for k in range(30):
        put(folder, "song-128.mp3", f"x/{k:02d}.mp3", artist=f"Art {k % 5}", album="Alb", title=f"Song {k}", tracknumber=str(k + 1))
    before = snapshot(folder)
    _, p = build(folder)
    cancel = threading.Event()
    res = mover.execute(p, progress=lambda d, t: cancel.set() if d == 10 else None, cancel=cancel)
    assert res.cancelled and 0 < res.done < 30
    _, rest = build(folder)
    assert not mover.execute(rest).failed
    assert idle(build(folder)[1]) == []
    for _ in range(2):
        assert not undo.undo(undo.find_log(str(folder))).skipped
    back, want = snapshot(folder), before
    assert {k: v for k, v in back["files"].items() if "organize_log" not in k} == want["files"]


def test_crash_in_the_middle_leaves_a_log_that_undoes_what_moved(tmp_path, monkeypatch):
    folder = tmp_path / "in1"
    tracks(folder, 6)
    before = snapshot(folder)
    _, p = build(folder)
    real, calls = mover.move_file, {"n": 0}

    def move(a, b):
        calls["n"] += 1
        if calls["n"] == 4:
            raise RuntimeError("the program crashed")
        return real(a, b)

    monkeypatch.setattr(mover, "move_file", move)
    with pytest.raises(RuntimeError):
        mover.execute(p)
    monkeypatch.setattr(mover, "move_file", real)
    u = undo.undo(undo.find_log(str(folder)))
    assert u.restored == 3 and not u.skipped
    assert {k: v for k, v in snapshot(folder)["files"].items() if "organize_log" not in k} == before["files"]


def test_killed_program_leaves_a_journal_that_undo_merges(tmp_path):
    folder = tmp_path / "in1"
    tracks(folder, 5)
    before = snapshot(folder)
    _, p = build(folder)
    log = mover.RunLog(str(folder), str(folder), "move")
    for item in p.items[:3]:
        mover.make_dirs(os.path.dirname(item.dst), log)
        mover.move_file(item.src, item.dst)
        log.add(op="move", src=item.src, dst=item.dst)
    log._journal.close()  # killed: no flush(), no final save
    assert os.path.exists(mover.journal_path(log.path))
    u = undo.undo(undo.find_log(str(folder)))
    assert u.restored == 3 and not u.skipped
    assert {k: v for k, v in snapshot(folder)["files"].items() if "organize_log" not in k} == before["files"]


def organized(tmp_path, n=4):
    folder = tmp_path / "in1"
    tracks(folder, n)
    before = snapshot(folder)
    _, p = build(folder)
    assert not mover.execute(p).failed
    return folder, p, before


def test_undo_twice_is_harmless(tmp_path):
    folder, _, before = organized(tmp_path)
    assert not undo.undo(undo.find_log(str(folder))).skipped
    again = undo.undo(undo.find_log(str(folder)))
    assert again.nothing and again.restored == 0


def test_user_deleted_a_moved_file_before_undo(tmp_path):
    folder, p, _ = organized(tmp_path)
    os.remove(p.items[1].dst)
    u = undo.undo(undo.find_log(str(folder)))
    assert len(u.skipped) == 1 and u.restored == 3


def test_original_place_taken_by_a_new_file_keeps_both(tmp_path):
    folder, p, _ = organized(tmp_path)
    taken = p.items[2].src
    os.makedirs(os.path.dirname(taken), exist_ok=True)  # the emptied folder was removed by the run
    open(taken, "wb").write(b"the user saved something new here")
    u = undo.undo(undo.find_log(str(folder)))
    assert len(u.skipped) == 1 and u.restored == 3
    assert open(taken, "rb").read() == b"the user saved something new here"
    assert os.path.exists(p.items[2].dst)


def test_file_deleted_between_preview_and_run_fails_alone(tmp_path):
    folder = tmp_path / "in1"
    tracks(folder, 3)
    _, p = build(folder)
    os.remove(p.items[1].src)
    res = mover.execute(p)
    assert res.done == 2 and len(res.failed) == 1


def test_a_file_named_like_a_target_folder_blocks_only_that_item(tmp_path):
    folder = tmp_path / "in1"
    put(folder, "song-128.mp3", "x/a.mp3", artist="Blocked", album="Alb", title="One", tracknumber="1")
    put(folder, "song-320.mp3", "x/b.mp3", artist="Free", album="Alb", title="Two", tracknumber="1")
    (folder / "Blocked").write_bytes(b"I am a file, not a folder")
    _, p = build(folder)
    res = mover.execute(p)
    assert res.done == 1 and len(res.failed) == 1
    assert (folder / "Blocked").read_bytes() == b"I am a file, not a folder"
    assert os.path.exists(folder / "Free" / "Alb" / "01 - Two.mp3") and os.path.exists(folder / "x" / "a.mp3")


# ------------------------------------------------------------------ A34: the disc number that was written stays
@pytest.mark.parametrize("pattern", ["{album}/{disc}-{track:02} {title}", "{artist}/{album}/{disc}-{track:02} - {title}"])
def test_disc_number_in_names_stays_when_the_other_disc_leaves(tmp_path, pattern):
    """"{disc}" shows for albums with two or more discs. Once the files of disc 2 are gone (their duplicates
    went to the duplicates folder), one disc is left: the names written with "1-" must not lose it."""
    folder = tmp_path / "in1"
    for disc in ("1", "2"):
        for n in (1, 2):
            put(folder, "song-128.mp3" if n == 1 else "song-320.mp3", f"x/{disc}-{n}.mp3", artist="Art", album="Alb",
                title=f"D{disc} Song {n}", tracknumber=str(n), discnumber=disc)
    _, p1 = build(folder, pattern=pattern)
    assert not mover.execute(p1).failed
    names = sorted(os.path.basename(i.dst) for i in p1.items)
    assert names[0].startswith("1-01") and names[-1].startswith("2-02"), names
    for i in p1.items:
        if os.path.basename(i.dst).startswith("2-"):
            os.remove(i.dst)  # disc 2 goes away
    _, p2 = build(folder, pattern=pattern)
    assert idle(p2) == [], idle(p2)


def test_a_one_disc_album_still_drops_the_disc_number(tmp_path):
    folder = tmp_path / "in1"
    put(folder, "song-128.mp3", "x/a.mp3", artist="Art", album="Alb", title="One", tracknumber="1", discnumber="1")
    _, p = build(folder, pattern="{album}/{disc}-{track:02} {title}")
    assert os.path.basename(p.items[0].dst) == "01 One.mp3"
