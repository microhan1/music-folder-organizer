"""tkinter GUI: source folder and options on top, three tabs (organize preview,
duplicates, artist merging), progress and actions at the bottom.

Long work (scan, hashing, fingerprints, moving, undo) runs on a worker
thread; results come back through a queue polled with after().
"""
from __future__ import annotations

import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

import dedupe
import i18n
import mover
import pattern as pattern_mod
import plan as plan_mod
import prefs as prefs_mod
import scan as scan_mod
import session
import undo as undo_mod
from artists import ArtistIndex, save_aliases
from i18n import t

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD

    _HAS_DND = True
except Exception:  # pragma: no cover - optional dependency
    _HAS_DND = False

ON, OFF = "☑", "☐"
BG = "#f3f4f6"
CARD = "#ffffff"
TEXT = "#1f2937"
MUTED = "#6b7280"
ACCENT = "#2563eb"
CONFLICT_BG = "#fef3c7"
DUP_FG = "#b45309"
GUESS_FG = "#2563eb"
ERROR_FG = "#b91c1c"
MAX_LIST = 30  # lines of a failure list shown in a dialog


def _enable_dpi_awareness() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def _fmt_length(seconds: float) -> str:
    if not seconds:
        return "-"
    return f"{int(seconds // 60)}:{int(seconds % 60):02d}"


def _rel(path: str, base: str) -> str:
    try:
        return os.path.relpath(path, base)
    except ValueError:
        return path


def open_path(path: str) -> None:
    try:
        if sys.platform == "win32":
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            import subprocess

            subprocess.Popen(["xdg-open", path])
    except OSError:
        pass


class App:
    def __init__(self, root: tk.Tk, initial: str | None = None) -> None:
        self.root = root
        self.prefs = prefs_mod.load()
        self.source = ""
        self.dest_other = ""
        self.scan: scan_mod.ScanResult | None = None
        self.index: ArtistIndex | None = None
        self.groups: list[dedupe.DupeGroup] = []
        self.overrides: dict[str, bool] = {}
        self.plan: plan_mod.Plan | None = None
        self.last_log = ""
        self.busy = False
        self.cancel = threading.Event()
        self.queue: queue.Queue = queue.Queue()
        self.alias_error = ""
        self._plan_job: str | None = None
        self.var_pattern = tk.StringVar(value=self.prefs.pattern)
        self.var_where = tk.StringVar(value="inplace")
        self.var_dest = tk.StringVar()
        self.var_mode = tk.StringVar(value="move")
        self.var_remove_empty = tk.BooleanVar(value=self.prefs.remove_empty)
        self.var_untagged = tk.BooleanVar(value=self.prefs.include_untagged)
        self.var_fp = tk.BooleanVar(value=False)
        self.var_dupes_action = tk.StringVar(value=self.prefs.dupes_action)
        self.var_pref = tk.StringVar(value=self.prefs.artist_name_preference)
        self.var_lang = tk.StringVar(value=i18n.LANG_NAMES[i18n.current_lang()])
        root.configure(bg=BG)
        root.geometry("1280x820")
        root.minsize(960, 640)
        self._style()
        self.frame: ttk.Frame | None = None
        self._build()
        self.var_pattern.trace_add("write", lambda *_: self._options_changed())
        root.after(100, self._poll)
        if _HAS_DND:
            root.drop_target_register(DND_FILES)  # type: ignore[attr-defined]
            root.dnd_bind("<<Drop>>", self._on_drop)  # type: ignore[attr-defined]
        if initial and os.path.isdir(initial):
            root.after(200, lambda: self.load_source(initial))

    # ------------------------------------------------------------------ layout
    def _style(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", background=BG, foreground=TEXT)
        style.configure("Card.TFrame", background=CARD)
        style.configure("Card.TLabel", background=CARD)
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("Title.TLabel", font=("Segoe UI", 14, "bold"))
        style.configure("Error.TLabel", foreground=ERROR_FG)
        style.configure("Accent.TButton", foreground="white", background=ACCENT)
        style.map("Accent.TButton", background=[("disabled", "#93c5fd"), ("active", "#1d4ed8")])
        style.configure("Treeview", rowheight=24)

    def _build(self) -> None:
        if self.frame is not None:
            self.frame.destroy()
        self.root.title(t("app_title"))
        f = ttk.Frame(self.root, padding=10)
        f.pack(fill="both", expand=True)
        self.frame = f

        top = ttk.Frame(f)
        top.pack(fill="x")
        ttk.Label(top, text=t("app_title"), style="Title.TLabel").pack(side="left")
        lang = ttk.Combobox(top, textvariable=self.var_lang, values=list(i18n.LANG_NAMES.values()), state="readonly", width=10)
        lang.pack(side="right")
        lang.bind("<<ComboboxSelected>>", self._on_lang)
        ttk.Label(top, text=t("lbl_language")).pack(side="right", padx=6)

        src = ttk.Frame(f)
        src.pack(fill="x", pady=(10, 4))
        ttk.Label(src, text=t("source_label"), width=12).pack(side="left")
        self.lbl_source = ttk.Label(src, text=self.source or t("drop_hint"), style="Muted.TLabel" if not self.source else "TLabel")
        self.lbl_source.pack(side="left", fill="x", expand=True)
        ttk.Button(src, text=t("btn_pick_folder"), command=self._pick_source).pack(side="right")

        opt = ttk.Frame(f)
        opt.pack(fill="x", pady=4)
        ttk.Label(opt, text=t("pattern_label"), width=12).grid(row=0, column=0, sticky="w")
        combo = ttk.Combobox(opt, textvariable=self.var_pattern, values=list(prefs_mod.PRESETS), width=60)
        combo.grid(row=0, column=1, sticky="w")
        ttk.Label(opt, text=t("pattern_hint"), style="Muted.TLabel").grid(row=1, column=1, columnspan=4, sticky="w")
        ttk.Label(opt, text=t("dest_label"), width=12).grid(row=2, column=0, sticky="w", pady=4)
        where = ttk.Frame(opt)
        where.grid(row=2, column=1, columnspan=4, sticky="we")
        ttk.Radiobutton(where, text=t("opt_in_place"), value="inplace", variable=self.var_where,
                        command=self._dest_changed).pack(side="left")
        ttk.Radiobutton(where, text=t("opt_other_folder"), value="other", variable=self.var_where,
                        command=self._dest_changed).pack(side="left", padx=(12, 4))
        self.lbl_dest = ttk.Label(where, text=self.dest_other or "-", style="Muted.TLabel")
        self.lbl_dest.pack(side="left", padx=4)
        ttk.Button(where, text=t("btn_browse"), command=self._pick_dest).pack(side="left", padx=4)
        flags = ttk.Frame(opt)
        flags.grid(row=3, column=1, columnspan=4, sticky="w")
        ttk.Radiobutton(flags, text=t("opt_move"), value="move", variable=self.var_mode, command=self._options_changed).pack(side="left")
        ttk.Radiobutton(flags, text=t("opt_copy"), value="copy", variable=self.var_mode, command=self._options_changed).pack(side="left", padx=(8, 20))
        ttk.Checkbutton(flags, text=t("opt_remove_empty"), variable=self.var_remove_empty, command=self._options_changed).pack(side="left")
        ttk.Checkbutton(flags, text=t("opt_include_untagged"), variable=self.var_untagged, command=self._options_changed).pack(side="left", padx=12)
        opt.columnconfigure(1, weight=1)

        nb = ttk.Notebook(f)
        nb.pack(fill="both", expand=True, pady=(8, 4))
        self.notebook = nb
        nb.add(self._build_organize(nb), text=t("tab_organize"))
        nb.add(self._build_dupes(nb), text=t("tab_dupes"))
        nb.add(self._build_artists(nb), text=t("tab_artists"))

        bottom = ttk.Frame(f)
        bottom.pack(fill="x")
        self.progress = ttk.Progressbar(bottom, mode="determinate", length=220)
        self.progress.pack(side="left")
        self.lbl_status = ttk.Label(bottom, text="", style="Muted.TLabel")
        self.lbl_status.pack(side="left", padx=8, fill="x", expand=True)
        self.btn_cancel = ttk.Button(bottom, text=t("btn_cancel"), command=self.cancel.set, state="disabled")
        self.btn_cancel.pack(side="right")
        self.btn_run = ttk.Button(bottom, text=t("btn_run"), style="Accent.TButton", command=self._run)
        self.btn_run.pack(side="right", padx=4)
        self.btn_undo = ttk.Button(bottom, text=t("btn_undo"), command=self._undo)
        self.btn_undo.pack(side="right", padx=4)
        self.btn_export = ttk.Button(bottom, text=t("btn_export_untagged"), command=self._export_untagged)
        self.btn_export.pack(side="right", padx=4)
        self._refresh_all()

    def _build_organize(self, parent) -> ttk.Frame:
        tab = ttk.Frame(parent, padding=6)
        self.lbl_summary = ttk.Label(tab, text="")
        self.lbl_summary.pack(fill="x", pady=(0, 4))
        cols = ("check", "current", "new", "status", "artist")
        tree = ttk.Treeview(tab, columns=cols, show="headings", selectmode="extended")
        for c, key, width, stretch in (("check", "", 32, False), ("current", "col_current", 380, True),
                                      ("new", "col_new", 380, True), ("status", "col_status", 120, False),
                                      ("artist", "col_artist", 220, False)):
            tree.heading(c, text=t(key) if key else ON)
            tree.column(c, width=width, stretch=stretch, anchor="center" if c == "check" else "w")
        tree.heading("check", command=self._toggle_all)
        tree.tag_configure("conflict", background=CONFLICT_BG)
        tree.tag_configure("dup", foreground=DUP_FG)
        tree.tag_configure("guess", foreground=GUESS_FG)
        tree.tag_configure("muted", foreground=MUTED)
        self._attach_scroll(tab, tree)
        tree.bind("<Button-1>", self._on_org_click)
        tree.bind("<space>", lambda e: self._toggle_selected())
        self.tree = tree
        return tab

    def _build_dupes(self, parent) -> ttk.Frame:
        tab = ttk.Frame(parent, padding=6)
        bar = ttk.Frame(tab)
        bar.pack(fill="x", pady=(0, 4))
        ttk.Checkbutton(bar, text=t("opt_fingerprint"), variable=self.var_fp, command=self._fp_estimate).pack(side="left")
        self.lbl_fp = ttk.Label(bar, text="", style="Muted.TLabel")
        self.lbl_fp.pack(side="left", padx=8)
        ttk.Button(bar, text=t("btn_find_dupes"), command=self._find_dupes).pack(side="left", padx=8)
        ttk.Radiobutton(bar, text=t("opt_dupes_move", folder=self.prefs.dupes_name()), value="move",
                        variable=self.var_dupes_action, command=self._options_changed).pack(side="left", padx=(20, 4))
        ttk.Radiobutton(bar, text=t("opt_dupes_trash"), value="trash", variable=self.var_dupes_action,
                        command=self._options_changed).pack(side="left")
        ttk.Label(tab, text=t("dupes_hint"), style="Muted.TLabel").pack(fill="x", pady=(0, 4))
        cols = ("apply", "keep", "path", "format", "bitrate", "length")
        tree = ttk.Treeview(tab, columns=cols, show="tree headings", selectmode="browse")
        tree.column("#0", width=260, stretch=False)
        tree.heading("#0", text=t("col_group"))
        for c, key, width, stretch in (("apply", "col_apply", 60, False), ("keep", "col_keep", 60, False),
                                      ("path", "col_current", 520, True), ("format", "col_format", 70, False),
                                      ("bitrate", "col_bitrate", 100, False), ("length", "col_length", 70, False)):
            tree.heading(c, text=t(key))
            tree.column(c, width=width, stretch=stretch, anchor="center" if c in ("apply", "keep") else "w")
        tree.tag_configure("nokeep", foreground=ERROR_FG)
        tree.tag_configure("off", foreground=MUTED)
        self._attach_scroll(tab, tree)
        tree.bind("<Button-1>", self._on_dupe_click)
        self.dtree = tree
        return tab

    def _build_artists(self, parent) -> ttk.Frame:
        tab = ttk.Frame(parent, padding=6)
        bar = ttk.Frame(tab)
        bar.pack(fill="x", pady=(0, 4))
        ttk.Button(bar, text=t("btn_rename_rep"), command=self._rename_rep).pack(side="left")
        ttk.Button(bar, text=t("btn_ungroup"), command=self._ungroup).pack(side="left", padx=4)
        ttk.Button(bar, text=t("btn_guess_yes"), command=lambda: self._answer_guess(True)).pack(side="left", padx=(16, 4))
        ttk.Button(bar, text=t("btn_guess_no"), command=lambda: self._answer_guess(False)).pack(side="left")
        ttk.Button(bar, text=t("btn_open_artists"), command=self._open_artists).pack(side="right")
        ttk.Radiobutton(bar, text=t("opt_pref_latin"), value="latin", variable=self.var_pref,
                        command=self._pref_changed).pack(side="right", padx=4)
        ttk.Radiobutton(bar, text=t("opt_pref_original"), value="original", variable=self.var_pref,
                        command=self._pref_changed).pack(side="right", padx=4)
        ttk.Label(bar, text=t("lbl_name_preference")).pack(side="right", padx=4)
        self.lbl_alias = ttk.Label(tab, text=t("artists_hint"), style="Muted.TLabel")
        self.lbl_alias.pack(fill="x", pady=(0, 4))
        cols = ("variants", "files", "via")
        tree = ttk.Treeview(tab, columns=cols, show="tree headings", selectmode="browse")
        tree.column("#0", width=260, stretch=False)
        tree.heading("#0", text=t("col_rep_name"))
        for c, key, width in (("variants", "col_variants", 520), ("files", "col_files", 70), ("via", "col_via", 200)):
            tree.heading(c, text=t(key))
            tree.column(c, width=width, stretch=c == "variants")
        tree.tag_configure("guess", foreground=GUESS_FG)
        self._attach_scroll(tab, tree)
        self.atree = tree
        return tab

    @staticmethod
    def _attach_scroll(parent, tree: ttk.Treeview) -> None:
        box = ttk.Frame(parent)
        box.pack(fill="both", expand=True)
        sb = ttk.Scrollbar(box, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(in_=box, side="left", fill="both", expand=True)
        tree.lift(box)  # the box was created after the tree and would cover it
        sb.pack(side="right", fill="y")

    # ------------------------------------------------------------------ events
    def _on_lang(self, _event=None) -> None:
        name = self.var_lang.get()
        code = next((c for c, n in i18n.LANG_NAMES.items() if n == name), i18n.DEFAULT_LANG)
        i18n.set_lang(code)
        self._build()
        if self.scan is not None:
            self._replan()

    def _on_drop(self, event) -> None:
        paths = self.root.tk.splitlist(event.data)
        for p in paths:
            if os.path.isdir(p):
                self.load_source(p)
                return
            if os.path.isfile(p):
                self.load_source(os.path.dirname(p))
                return

    def _pick_source(self) -> None:
        folder = filedialog.askdirectory(title=t("source_label"))
        if folder:
            self.load_source(folder)

    def _pick_dest(self) -> None:
        folder = filedialog.askdirectory(title=t("dest_label"))
        if folder:
            self.dest_other = os.path.abspath(folder)
            self.var_where.set("other")
            self.lbl_dest.configure(text=self.dest_other)
            self._dest_changed()

    def _dest_changed(self) -> None:
        if self.var_where.get() == "other" and not self.dest_other:
            folder = filedialog.askdirectory(title=t("dest_label"))
            if not folder:
                self.var_where.set("inplace")
                return
            self.dest_other = os.path.abspath(folder)
            self.lbl_dest.configure(text=self.dest_other)
        if self.source:
            self.load_source(self.source)  # the dupes folder and a destination inside the source change what a scan sees

    def _options_changed(self) -> None:
        self.prefs.remove_empty = self.var_remove_empty.get()
        self.prefs.include_untagged = self.var_untagged.get()
        self.prefs.dupes_action = self.var_dupes_action.get()
        if not pattern_mod.validate(self.var_pattern.get()):
            self.prefs.pattern = self.var_pattern.get().strip()
        prefs_mod.save(self.prefs)
        if self._plan_job is not None:
            self.root.after_cancel(self._plan_job)
        self._plan_job = self.root.after(250, self._replan)

    def _pref_changed(self) -> None:
        self.prefs.artist_name_preference = self.var_pref.get()
        prefs_mod.save(self.prefs)
        self._reindex()

    # ------------------------------------------------------------------ state
    def dest(self) -> str:
        if self.var_where.get() == "other" and self.dest_other:
            return self.dest_other
        return self.source

    def options(self) -> plan_mod.Options:
        p = dataclass_copy(self.prefs)
        p.pattern = self.var_pattern.get().strip()
        return session.options(p, self.source, self.dest(), self.var_mode.get())

    def load_source(self, folder: str) -> None:
        if self.busy:
            return
        self.source = os.path.abspath(folder)
        self.lbl_source.configure(text=self.source, style="TLabel")
        self.scan, self.index, self.groups, self.plan = None, None, [], None
        self.overrides.clear()
        excl = session.excludes(self.source, self.dest(), self.prefs)
        root = self.source
        opts = self.options()  # tk variables are read here, never on the worker thread

        def work():
            res = scan_mod.scan(root, excl, progress=lambda d, n: self.queue.put(("progress", d, n, "msg_reading")),
                                cancel=self.cancel)
            if res.cancelled:
                return ("scanned", None)
            index, err = session.make_index(res.tracks, self.prefs)
            pref = session.preferred_paths(res, opts, index)
            groups = dedupe.find(res.tracks, index.rep, preferred=pref, cancel=self.cancel,
                                 progress=lambda ph, d, n: self.queue.put(("progress", d, n, "msg_hashing")))
            for g in groups:
                g.applied = g.stage == 1
            return ("scanned", (res, index, err, groups))

        self._start(work, "msg_scanning_short")

    def _reindex(self) -> None:
        if self.scan is None:
            return
        self.index, self.alias_error = session.make_index(self.scan.tracks, self.prefs)
        self._replan()

    def _replan(self) -> None:
        self._plan_job = None
        if self.scan is None or self.busy:
            self._refresh_all()
            return
        problem = pattern_mod.validate(self.var_pattern.get())
        if problem:
            self.plan = None
            self.lbl_summary.configure(text=t(problem), style="Error.TLabel")
            self.btn_run.configure(state="disabled")
            return
        if self.var_mode.get() == "copy" and scan_mod.key_of(self.dest()) == scan_mod.key_of(self.source):
            self.plan = None
            self.lbl_summary.configure(text=t("err_copy_in_place"), style="Error.TLabel")
            self.btn_run.configure(state="disabled")
            return
        self.plan = plan_mod.build(self.scan, self.options(), self.index, self.groups, self.overrides)
        self._refresh_all()

    def _refresh_all(self) -> None:
        self._fill_organize()
        self._fill_dupes()
        self._fill_artists()
        self._fp_estimate()
        has_plan = self.plan is not None and not self.busy
        self.btn_run.configure(state="normal" if has_plan and self.plan.summary()["move"] + self.plan.summary()["dupes"] + self.plan.summary()["folders"] else "disabled")
        self.btn_export.configure(state="normal" if has_plan and self.plan.untagged() else "disabled")
        self.btn_undo.configure(state="disabled" if self.busy else "normal")

    def _fill_organize(self) -> None:
        tree = self.tree
        tree.delete(*tree.get_children())
        if self.plan is None:
            if self.scan is None:
                self.lbl_summary.configure(text=t("drop_hint"), style="Muted.TLabel")
            return
        s = self.plan.summary()
        text = t("summary", **{k: s[k] for k in ("move", "same", "untagged", "dupes", "folders")})
        if s["untagged"]:
            text += "   ·   " + t("msg_untagged_hint")
        if self.alias_error:
            text += "   ·   " + t("err_artists_file", path=self.prefs.artists_file(), error=self.alias_error)
        self.lbl_summary.configure(text=text, style="TLabel")
        dest = self.plan.options.dest
        for item in self.plan.items:
            tags = []
            if item.status == plan_mod.CONFLICT:
                tags.append("conflict")
            if item.status == plan_mod.DUP:
                tags.append("dup")
            if item.guess:
                tags.append("guess")
            if not item.moves:
                tags.append("muted")
            new = t("lbl_trash") if item.action == plan_mod.DUPE_TRASH else _rel(item.dst, dest)
            status = t(f"status_{item.status}")
            if item.truncated:
                status += " · " + t("status_truncated")
            tree.insert("", "end", iid=item.key, tags=tags,
                        values=(ON if item.checked else OFF, _rel(item.src, self.plan.options.root), new, status, item.artist_note))

    def _fill_dupes(self) -> None:
        tree = self.dtree
        tree.delete(*tree.get_children())
        for g in self.groups:
            label = t("dupe_group", n=g.id, stage=t(f"stage_{g.stage}"), count=len(g.members))
            tags = ["nokeep"] if not g.keep else ([] if g.applied else ["off"])
            gid = f"g{g.id}"
            tree.insert("", "end", iid=gid, text=label, open=True, tags=tags,
                        values=(ON if g.applied else OFF, "", "", "", "", ""))
            for m in g.members:
                k = scan_mod.key_of(m.path)
                tree.insert(gid, "end", iid=f"{gid}|{k}", tags=([] if g.applied else ["off"]),
                            values=("", ON if k in g.keep else OFF, _rel(m.path, self.source), m.fmt,
                                    f"{m.bitrate} kbps", _fmt_length(m.length)))

    def _fill_artists(self) -> None:
        tree = self.atree
        tree.delete(*tree.get_children())
        if self.index is None:
            return
        for c in self.index.merged():
            via = ", ".join(t(f"via_{v}") for v in sorted(c.via)) or t("via_alias")
            tree.insert("", "end", iid=f"c{c.id}", text=c.rep,
                        values=(" / ".join(c.names), c.files, via))
        for i, g in enumerate(self.index.guesses):
            tree.insert("", "end", iid=f"q{i}", text=f"{g.proposed} ?", tags=["guess"],
                        values=(" / ".join(g.names), g.files, t("via_guess", album=g.album)))

    def _fp_estimate(self) -> None:
        if self.scan is None or not self.var_fp.get():
            self.lbl_fp.configure(text="")
            return
        if not dedupe.find_fpcalc(self.prefs.fpcalc_path):
            self.lbl_fp.configure(text=t("err_no_fpcalc"), style="Error.TLabel")
            return
        sec = dedupe.estimate_seconds(len(self.scan.tracks))
        self.lbl_fp.configure(text=t("msg_fp_estimate_short", min=sec // 60, sec=sec % 60), style="Muted.TLabel")

    # ------------------------------------------------------------------ organize tab
    def _on_org_click(self, event) -> str | None:
        if self.tree.identify_region(event.x, event.y) != "cell" or self.tree.identify_column(event.x) != "#1":
            return None
        iid = self.tree.identify_row(event.y)
        if iid:
            self._set_checked([iid], self.tree.set(iid, "check") != ON)
        return "break"

    def _toggle_selected(self) -> None:
        sel = list(self.tree.selection())
        if sel:
            self._set_checked(sel, self.tree.set(sel[0], "check") != ON)

    def _toggle_all(self) -> None:
        if self.plan is None:
            return
        target = not all(i.checked for i in self.plan.items)
        self._set_checked([i.key for i in self.plan.items], target)

    def _set_checked(self, keys: list[str], value: bool) -> None:
        for k in keys:
            self.overrides[k] = value
        top = self.tree.yview()[0]
        sel = self.tree.selection()
        self._replan()
        self.tree.yview_moveto(top)
        keep = [k for k in sel if self.tree.exists(k)]
        if keep:
            self.tree.selection_set(keep)

    # ------------------------------------------------------------------ dupes tab
    def _on_dupe_click(self, event) -> str | None:
        if self.dtree.identify_region(event.x, event.y) != "cell":
            return None
        col = self.dtree.identify_column(event.x)
        iid = self.dtree.identify_row(event.y)
        if not iid:
            return None
        if col == "#1" and iid.startswith("g") and "|" not in iid:
            g = self._group(int(iid[1:]))
            g.applied = not g.applied
        elif col == "#2" and "|" in iid:
            gid, key = iid.split("|", 1)
            g = self._group(int(gid[1:]))
            if key in g.keep:
                g.keep.discard(key)
            else:
                g.keep.add(key)
        else:
            return None
        top = self.dtree.yview()[0]
        self._replan()
        self.dtree.yview_moveto(top)
        return "break"

    def _group(self, gid: int) -> dedupe.DupeGroup:
        return next(g for g in self.groups if g.id == gid)

    def _find_dupes(self) -> None:
        if self.scan is None or self.busy:
            return
        fp = self.var_fp.get()
        exe = dedupe.find_fpcalc(self.prefs.fpcalc_path) if fp else None
        if fp and not exe:
            messagebox.showwarning(t("app_title"), t("err_no_fpcalc"))
            return
        tracks, index = self.scan.tracks, self.index
        pref = session.preferred_paths(self.scan, self.options(), index)

        def work():
            groups = dedupe.find(tracks, index.rep, fingerprint=fp, fpcalc=exe, preferred=pref, cancel=self.cancel,
                                 progress=lambda ph, d, n: self.queue.put(("progress", d, n, f"msg_{'fingerprinting' if ph == 'fingerprint' else 'hashing'}")))
            for g in groups:
                g.applied = g.stage == 1
            return ("dupes", groups)

        self._start(work, "msg_hashing")

    # ------------------------------------------------------------------ artists tab
    def _selected_cluster(self):
        sel = self.atree.selection()
        if not sel or self.index is None:
            return None
        iid = sel[0]
        if iid.startswith("c"):
            cid = int(iid[1:])
            return next((c for c in self.index.clusters if c.id == cid), None)
        return None

    def _selected_guess(self):
        sel = self.atree.selection()
        if not sel or self.index is None or not sel[0].startswith("q"):
            return None
        return self.index.guesses[int(sel[0][1:])]

    def _save_aliases(self, aliases: dict) -> bool:
        try:
            save_aliases(self.prefs.artists_file(), aliases)
            return True
        except OSError as exc:
            messagebox.showerror(t("app_title"), t("err_artists_save", error=str(exc)))
            return False

    def _rename_rep(self) -> None:
        c = self._selected_cluster()
        if c is None:
            messagebox.showinfo(t("app_title"), t("msg_pick_group"))
            return
        name = simpledialog.askstring(t("btn_rename_rep"), t("msg_new_rep"), initialvalue=c.rep, parent=self.root)
        if name and name.strip() and self._save_aliases(self.index.aliases_with(c.names, name.strip())):
            self._reindex()

    def _ungroup(self) -> None:
        c = self._selected_cluster()
        if c is None:
            messagebox.showinfo(t("app_title"), t("msg_pick_group"))
            return
        if c.alias_key:
            if not self._save_aliases(self.index.aliases_without(c)):
                return
        if "id" in c.via or "norm" in c.via:
            self.prefs.artist_no_merge = sorted(set(self.prefs.artist_no_merge) | set(c.names))
            prefs_mod.save(self.prefs)
        self._reindex()

    def _answer_guess(self, yes: bool) -> None:
        g = self._selected_guess()
        if g is None:
            messagebox.showinfo(t("app_title"), t("msg_pick_guess"))
            return
        if yes:
            if not self._save_aliases(self.index.aliases_with(self.index.cluster_names_for(g), g.proposed)):
                return
        else:
            self.prefs.artist_rejected.append(list(g.names))
            prefs_mod.save(self.prefs)
        self._reindex()

    def _open_artists(self) -> None:
        path = self.prefs.artists_file()
        if not os.path.exists(path) and not self._save_aliases({}):
            return
        open_path(path)

    # ------------------------------------------------------------------ run / undo
    def _run(self) -> None:
        if self.plan is None or self.busy:
            return
        if any(g.applied and not g.keep for g in self.groups):
            messagebox.showwarning(t("app_title"), t("msg_need_one_keep"))
            self.notebook.select(1)
            return
        s = self.plan.summary()
        msg = t("confirm_run", **{k: s[k] for k in ("move", "dupes", "folders")})
        if s["trash"]:
            msg += "\n\n" + t("confirm_trash", count=s["trash"])
        if self.var_mode.get() == "move":
            msg += "\n\n" + t("confirm_undo_note")
        if not messagebox.askokcancel(t("btn_run"), msg):
            return
        the_plan = self.plan

        def work():
            try:
                res = mover.execute(the_plan, progress=lambda d, n: self.queue.put(("progress", d, n, "msg_moving")),
                                    cancel=self.cancel)
            except OSError as exc:  # the log could not be written: nothing was moved
                raise RuntimeError(t("err_log_write", path=mover.log_path_for(the_plan.options.dest), error=exc)) from exc
            return ("ran", res)

        self._start(work, "msg_moving")

    def _undo(self) -> None:
        if self.busy:
            return
        log = self.last_log if self.last_log and undo_mod.pending_run(self.last_log) else None
        if log is None and self.source:
            log = undo_mod.find_log(self.dest()) or undo_mod.find_log(self.source)
        if log is None:
            last = self.prefs.last_log or i18n.load_settings().get("last_log", "")
            log = last if isinstance(last, str) and undo_mod.pending_run(last) else None
        run = undo_mod.pending_run(log)
        if run is None:
            messagebox.showinfo(t("btn_undo"), t("msg_no_log"))
            return
        moves = sum(1 for op in run["ops"] if op.get("op") in ("move", "copy"))
        if not messagebox.askokcancel(t("btn_undo"), t("confirm_undo", time=run.get("time", ""), count=moves, path=run.get("dest", ""))):
            return

        def work():
            return ("undone", undo_mod.undo(log, progress=lambda d, n: self.queue.put(("progress", d, n, "msg_undoing")),
                                            cancel=self.cancel), run.get("source", ""))

        self._start(work, "msg_undoing")

    def _export_untagged(self) -> None:
        if self.plan is None:
            return
        target = filedialog.asksaveasfilename(title=t("btn_export_untagged"), initialdir=self.source,
                                              initialfile="untagged.txt", defaultextension=".txt",
                                              filetypes=[("Text", "*.txt")])
        if not target:
            return
        try:
            mover.write_untagged([i.src for i in self.plan.untagged()], target)
        except OSError as exc:
            messagebox.showerror(t("app_title"), str(exc))
            return
        self.lbl_status.configure(text=t("msg_untagged_written", path=target))

    # ------------------------------------------------------------------ worker plumbing
    def _start(self, work, status_key: str) -> None:
        self.busy = True
        self.cancel.clear()
        self.btn_cancel.configure(state="normal")
        self.btn_run.configure(state="disabled")
        self.btn_undo.configure(state="disabled")
        self.lbl_status.configure(text=t(status_key))
        self.progress.configure(value=0, maximum=1)

        def runner():
            try:
                self.queue.put(work())
            except Exception as exc:  # keep the GUI alive and say what happened
                self.queue.put(("error", exc))

        threading.Thread(target=runner, daemon=True).start()

    def _poll(self) -> None:
        if self.frame is None or not self.frame.winfo_exists():
            return  # this window's content is gone
        try:
            while True:
                msg = self.queue.get_nowait()
                self._handle(msg)
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def _handle(self, msg) -> None:
        kind = msg[0]
        if kind == "progress":
            _, done, total, key = msg
            self.progress.configure(maximum=max(total, 1), value=done)
            self.lbl_status.configure(text=f"{t(key)} {done}/{total}")
            return
        self.busy = False
        self.btn_cancel.configure(state="disabled")
        cancelled = self.cancel.is_set()
        if kind == "error":
            self.lbl_status.configure(text=str(msg[1]))
            messagebox.showerror(t("app_title"), str(msg[1]))
        elif kind == "scanned":
            if msg[1] is None:
                self.lbl_status.configure(text=t("status_cancelled"))
            else:
                self.scan, self.index, self.alias_error, self.groups = msg[1]
                n = len(self.scan.tracks)
                self.lbl_status.configure(text=t("msg_scanned", count=n) if n else t("msg_no_music"))
        elif kind == "dupes":
            self.groups = msg[1] if not cancelled else self.groups
            self.lbl_status.configure(text=t("status_cancelled") if cancelled else t("msg_dupes_found", count=len(self.groups)))
        elif kind == "ran":
            self._after_run(msg[1])
            return
        elif kind == "undone":
            self._after_undo(msg[1], msg[2])
            return
        self.cancel.clear()
        self._replan()

    def _after_run(self, res: mover.Result) -> None:
        self.last_log = res.log_path or self.last_log
        text = t("msg_done", count=res.done)
        if res.folders_removed:
            text += " · " + t("msg_folders_removed", count=res.folders_removed)
        if res.cancelled:
            text += " · " + t("status_cancelled")
        self.lbl_status.configure(text=text)
        details = [text]
        if res.trashed:
            details.append(t("msg_trashed_note", count=res.trashed))
        if self.plan is not None and self.plan.untagged():
            details.append(t("msg_untagged_hint"))
        if res.failed:
            details.append(t("msg_failed_count", count=len(res.failed)))
            details += [f"• {_rel(p, self.source)} — {r}" for p, r in res.failed[:MAX_LIST]]
        if res.log_path:
            details.append(t("msg_log_saved", path=res.log_path))
        (messagebox.showwarning if res.failed else messagebox.showinfo)(t("btn_run"), "\n".join(details))
        self.cancel.clear()
        self.load_source(self.source)

    def _after_undo(self, res: undo_mod.UndoResult, source: str) -> None:
        if res.nothing:
            messagebox.showinfo(t("btn_undo"), t("msg_no_log"))
        else:
            lines = [t("msg_undo_done", count=res.restored)]
            if res.skipped:
                lines.append(t("msg_undo_skipped", count=len(res.skipped)))
                lines += [f"• {p} — {r}" for p, r in res.skipped[:MAX_LIST]]
            if res.trashed:
                lines.append(t("msg_undo_trashed", count=len(res.trashed)))
            (messagebox.showwarning if res.skipped or res.trashed else messagebox.showinfo)(t("btn_undo"), "\n".join(lines))
            self.lbl_status.configure(text=lines[0])
        self.cancel.clear()
        folder = self.source or source
        if folder and os.path.isdir(folder):
            self.load_source(folder)
        else:
            self._replan()


def dataclass_copy(p: prefs_mod.Prefs) -> prefs_mod.Prefs:
    import copy

    return copy.deepcopy(p)


def launch(initial: str | None = None) -> None:
    _enable_dpi_awareness()
    root = TkinterDnD.Tk() if _HAS_DND else tk.Tk()
    icon = os.path.join(i18n.resource_dir(), "assets", "icon.png")
    if os.path.exists(icon):
        try:
            root.iconphoto(True, tk.PhotoImage(file=icon))
        except tk.TclError:
            pass
    App(root, initial)
    root.mainloop()
