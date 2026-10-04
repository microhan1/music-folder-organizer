"""Entry point for music-folder-organizer.

    python main.py                                  -> GUI
    python main.py ./music [--pattern P] [--dest D] [--copy] [--dedupe] [--fingerprint] [--dry-run]
    python main.py ./music --undo                   -> reverse the latest run
"""
from __future__ import annotations

import argparse
import os
import sys

import dedupe
import i18n
import mover
import pattern as pattern_mod
import plan as plan_mod
import prefs as prefs_mod
import scan as scan_mod
import session
import undo as undo_mod
from i18n import t


def _preselect_lang(argv: list[str]) -> str | None:
    """--lang has to be known before the parser is built, so help is translated."""
    for i, a in enumerate(argv):
        if a == "--lang" and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith("--lang="):
            return a.split("=", 1)[1]
    return None


def _localize_argparse() -> None:
    """argparse's own labels go through gettext; route them to lang files."""
    table = {
        "usage: ": t("cli_usage"),
        "positional arguments": t("cli_positional"),
        "options": t("cli_options"),
        "show this help message and exit": t("cli_help"),
    }
    argparse._ = lambda s: table.get(s, s)  # type: ignore[attr-defined]


def build_parser() -> argparse.ArgumentParser:
    _localize_argparse()
    p = argparse.ArgumentParser(prog="music-folder-organizer", description=t("cli_desc"))
    p.add_argument("folder", nargs="?", help=t("cli_folder"))
    p.add_argument("--pattern", help=t("cli_pattern"))
    p.add_argument("--dest", help=t("cli_dest"))
    p.add_argument("--copy", action="store_true", help=t("cli_copy"))
    p.add_argument("--dedupe", action="store_true", help=t("cli_dedupe"))
    p.add_argument("--dedupe-across-albums", action="store_true", help=t("cli_dedupe_across"))
    p.add_argument("--fingerprint", action="store_true", help=t("cli_fingerprint"))
    p.add_argument("--trash", action="store_true", help=t("cli_trash"))
    p.add_argument("--include-untagged", action="store_true", help=t("cli_include_untagged"))
    p.add_argument("--keep-empty", action="store_true", help=t("cli_keep_empty"))
    p.add_argument("--no-sidecars", action="store_true", help=t("cli_no_sidecars"))
    p.add_argument("--artists", help=t("cli_artists"))
    p.add_argument("--dry-run", action="store_true", help=t("cli_dry_run"))
    p.add_argument("--undo", action="store_true", help=t("cli_undo"))
    p.add_argument("--history", action="store_true", help=t("cli_history"))
    p.add_argument("--undo-run", metavar="ID", help=t("cli_undo_run"))
    p.add_argument("--lang", choices=i18n.LANGS, help=t("cli_lang"))
    p.add_argument("--gui", action="store_true", help=t("cli_gui"))
    return p


def _rel(path: str, base: str) -> str:
    try:
        return os.path.relpath(path, base)
    except ValueError:  # another drive
        return path


def status_label(item: plan_mod.Item) -> str:
    label = t(f"status_{item.status}")
    if item.keep_name:
        label += " · " + t("status_keep_name")
    if item.truncated:
        label += " · " + t("status_truncated")
    return label


def run_state(r: undo_mod.RunInfo) -> str:
    if not r.undone:
        return t("state_undoable")
    text = t("state_undone", time=r.undone_time.replace("T", " "))
    if r.skipped:
        text += " " + t("state_skipped", count=len(r.skipped))
    return text


def run_history(folder: str) -> int:
    runs = undo_mod.runs_in(undo_mod.candidate_logs(os.path.abspath(folder)))
    if not runs:
        print(t("msg_no_log"))
        return 1
    for r in runs:
        mode = t("opt_copy") if r.mode == "copy" else t("opt_move")
        print(t("cli_history_line", id=r.id, time=r.time.replace("T", " "), mode=mode, count=r.files,
                state=run_state(r), dest=r.dest))
    return 0


def run_undo(folder: str, run_id: str | None = None) -> int:
    folder = os.path.abspath(folder)
    logs = undo_mod.candidate_logs(folder)
    if run_id:
        match = next((r for r in undo_mod.runs_in(logs) if r.id == run_id), None)
        if match is None:
            print(t("err_no_such_run", id=run_id), file=sys.stderr)
            return 2
        res = undo_mod.undo(match.log, run_id=run_id, other_logs=logs)
    else:
        res = undo_mod.undo(undo_mod.find_log(folder), other_logs=logs)
    if res.blocked_by:
        first = res.blocked_by[0]  # newest first: undo that one, then come back
        print(t("msg_undo_blocked", time=first.time.replace("T", " "), id=first.id), file=sys.stderr)
        return 1
    if res.nothing:
        print(t("msg_no_log"))
        return 1
    if res.log_error:
        print(res.log_error, file=sys.stderr)
        return 1
    print(t("msg_undo_done", count=res.restored))
    for path, reason in res.skipped:
        print(t("msg_undo_skipped_item", path=path, reason=reason), file=sys.stderr)
    for path in res.trashed:
        print(t("msg_undo_trashed_item", path=path), file=sys.stderr)
    if res.log_unsaved:
        print(res.log_unsaved, file=sys.stderr)
    return 1 if res.skipped or res.log_unsaved else 0


def run_cli(args: argparse.Namespace) -> int:
    root = os.path.abspath(args.folder)
    if not os.path.isdir(root):
        print(t("err_not_folder", path=args.folder), file=sys.stderr)
        return 2
    if args.history:
        return run_history(root)
    if args.undo or args.undo_run:
        return run_undo(root, args.undo_run)
    p = prefs_mod.load()
    if args.pattern:
        p.pattern = args.pattern
    if args.include_untagged:
        p.include_untagged = True
    if args.keep_empty:
        p.remove_empty = False
    if args.no_sidecars:
        p.move_sidecars = False
    if args.trash:
        p.dupes_action = "trash"
    if args.artists:
        p.artists_path = os.path.abspath(args.artists)
    problem = pattern_mod.validate(p.pattern)
    if problem:
        print(t(problem) + f": {p.pattern}", file=sys.stderr)
        return 2
    dest = os.path.abspath(args.dest) if args.dest else root
    mode = "copy" if args.copy else "move"
    if mode == "copy" and scan_mod.key_of(dest) == scan_mod.key_of(root):
        print(t("err_copy_in_place"), file=sys.stderr)
        return 2

    print(t("msg_scanning", path=root))
    result = scan_mod.scan(root, session.excludes(root, dest, p))
    if not result.tracks:
        print(t("msg_no_music"))
        return 1
    index, alias_error = session.make_index(result.tracks, p, session.existing_folders(result, dest))
    if alias_error:
        print(t("err_artists_file", path=p.artists_file(), error=alias_error), file=sys.stderr)
    opts = session.options(p, root, dest, mode)
    groups = []
    if args.dedupe or args.fingerprint or args.dedupe_across_albums:
        exe = dedupe.find_fpcalc(p.fpcalc_path) if args.fingerprint else None
        if args.fingerprint:
            if exe:
                print(t("msg_fp_estimate", count=len(result.tracks), sec=dedupe.estimate_seconds(len(result.tracks))))
            else:
                print(t("err_no_fpcalc"), file=sys.stderr)
        groups = dedupe.find(result.tracks, index.rep, fingerprint=bool(exe), fpcalc=exe,
                             preferred=session.preferred_paths(result, opts, index),
                             across_albums=args.dedupe_across_albums)
    the_plan = plan_mod.build(result, opts, index, groups)

    for item in the_plan.items:
        new = _rel(item.dst, dest) if item.dst else t("lbl_trash")
        mark = " " if item.moves else "-"
        print(f"{mark} [{status_label(item)}] {_rel(item.src, root)} → {new}")
        if item.artist_note:
            print(f"      {t('col_artist')}: {item.artist_note}")
    for c in the_plan.sidecars():
        print(f"  + {_rel(c.src, root)} → {_rel(c.dst, dest)}")
    for g in index.guesses:
        print(t("cli_guess", names=" / ".join(g.names), album=g.album, proposed=g.proposed))
    s = the_plan.summary()
    line = t("summary", **{k: s[k] for k in ("move", "same", "untagged", "dupes", "folders")})
    if s["sidecars"]:
        line += ", " + t("summary_sidecars", count=s["sidecars"])
    print(line)
    if s["untagged"]:
        print(t("msg_untagged_hint"))
    if args.dry_run:
        print(t("msg_dry_run"))
        return 0

    try:
        res = mover.execute(the_plan, progress=_progress)
    except OSError as exc:  # the log could not be written: nothing was moved
        print(t("err_log_write", path=mover.log_path_for(dest), error=exc), file=sys.stderr)
        return 1
    print()
    if s["untagged"]:
        target = os.path.join(dest, "untagged.txt")
        try:
            mover.write_untagged([i.src for i in the_plan.untagged()], target)
            print(t("msg_untagged_written", path=target))
        except OSError:
            pass
    for path, reason in res.failed:
        print(t("msg_failed_item", path=path, reason=reason), file=sys.stderr)
    print(t("msg_done", count=res.done))
    if res.trashed:
        print(t("msg_trashed_note", count=res.trashed))
    if res.log_path:
        print(t("msg_log_saved", path=res.log_path))
    return 1 if res.failed else 0


def _progress(done: int, total: int) -> None:
    if sys.stdout is not None and sys.stdout.isatty():
        print(f"\r{done}/{total}", end="", flush=True)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # Windows consoles default to cp949; Chinese/Japanese names would crash print().
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    i18n.init(_preselect_lang(argv))
    args = build_parser().parse_args(argv)
    if args.lang:
        i18n.set_lang(args.lang, persist=False)
    # A windowed exe has no console: a folder dropped on it opens the GUI with that folder.
    headless = getattr(sys, "frozen", False) and sys.stdout is None
    if args.gui or headless or not args.folder:
        import gui

        gui.launch(args.folder)
        return 0
    try:
        return run_cli(args)
    except KeyboardInterrupt:
        print("\n" + t("status_cancelled"))
        return 130


if __name__ == "__main__":
    sys.exit(main())
