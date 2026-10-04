"""tkinter GUI: source folder and options on top, three tabs (organize preview,
duplicates, artist merging), progress and actions at the bottom.

Long work (scan, hashing, fingerprints, moving, undo) runs on a worker
thread; results come back through a queue polled with after().
"""
from __future__ import annotations

import dataclasses
import itertools
import os
import queue
import subprocess
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
FILL_CHUNK = 1000  # organize-table rows per slice of the event loop (~40 ms)
# palette: light grey page, white cards, the icon's green as the accent
BG = "#f4f5f7"
SURFACE = "#ffffff"
BORDER = "#e3e6ea"
HEAD_BG = "#f8f9fb"
TEXT = "#1f2328"
MUTED = "#6b7280"
ACCENT = "#059669"
ACCENT_HOVER = "#047857"
ACCENT_SOFT = "#e8f6ef"
ACCENT_OFF = "#a7dcc4"
SELECT_BG = "#d7f0e4"
STRIPE = "#fafbfc"
CONFLICT_BG = "#fff6dc"
DUP_FG = "#c2410c"
GUESS_FG = "#2563eb"
ERROR_FG = "#c62828"
ERROR_SOFT = "#fdecec"
MAX_LIST = 30  # lines of a failure list shown in a dialog
TAG_FILLER_EXE = "music-tag-filler.exe"
TAG_FILLER_EXTS = {".mp3", ".flac", ".m4a", ".ogg"}  # what music-tag-filler reads and writes
CMDLINE_LIMIT = 30000  # Windows allows 32 767 characters; keep a margin
SETTING_LABELS = {"fallback_artist": "set_fallback_artist", "fallback_album": "set_fallback_album",
                  "fallback_year": "set_fallback_year", "fallback_genre": "set_fallback_genre",
                  "dupes_folder": "set_dupes_folder", "artists_path": "set_artists_path",
                  "fpcalc_path": "set_fpcalc_path", "tag_filler_path": "set_tag_filler_path"}
PATTERN_BUTTONS = ("{artist}", "{album_artist}", "{album}", "{title}", "{track:02}", "{disc}", "{year}",
                   "{genre}", "{artist_sort}")

# UI font per language; tables always use a font with Hangul, kana and hanzi,
# because file names mix scripts whatever the UI language is
UI_FONTS = {"ko": ("맑은 고딕", "Malgun Gothic"), "ja": ("Yu Gothic UI", "Meiryo UI", "Meiryo"),
            "zh-CN": ("Microsoft YaHei UI", "Microsoft YaHei"), "en": ("Segoe UI",)}
CJK_FONTS = ("맑은 고딕", "Malgun Gothic", "Yu Gothic UI", "Microsoft YaHei UI", "Noto Sans KR")


def _pick_font(root: tk.Misc, candidates: tuple[str, ...]) -> str:
    from tkinter import font as tkfont

    families = set(tkfont.families(root))
    for name in (*candidates, *CJK_FONTS):
        if name in families:
            return name
    return tkfont.nametofont("TkDefaultFont").actual()["family"]


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


def _when(stamp: str, today_short: bool = False) -> str:
    """"2026-10-01T18:24:26" -> "10-01 18:24:26" this year (or "18:24" today when
    ``today_short``), the full date otherwise."""
    import datetime

    try:
        t0 = datetime.datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return stamp.replace("T", " ")
    now = datetime.datetime.now()
    if today_short and t0.date() == now.date():
        return t0.strftime("%H:%M")
    return t0.strftime("%m-%d %H:%M:%S" if t0.year == now.year else "%Y-%m-%d %H:%M")


def _short(path: str, limit: int = 96) -> str:
    """Middle ellipsis: keeps the drive and the last folders, which are what people recognise."""
    if len(path) <= limit:
        return path
    keep = limit - 1
    return path[: keep // 3] + "…" + path[-(keep - keep // 3):]


def _rel(path: str, base: str) -> str:
    """Path relative to base for display, with "/" so it reads the same in every font
    (Korean and Japanese fonts draw a backslash as ₩ or ¥)."""
    try:
        rel = os.path.relpath(path, base)
    except ValueError:
        return path
    return rel.replace(os.sep, "/")


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
        # the plan is built on a worker thread; one at a time, the newest request wins
        self.planning = False
        self._plan_again = False
        self._plan_gen = 0  # bumped by every change that makes a plan in flight stale
        self._status_before_plan = ""
        self._fill_job: str | None = None  # the organize table is refilled in chunks
        self._fill_restore: tuple = ((), 0.0)  # selection and scroll to put back after the last chunk
        self._filler: subprocess.Popen | None = None  # music-tag-filler opened from here
        self.history: tk.Toplevel | None = None  # the undo history window, when open
        self.settings_win: tk.Toplevel | None = None
        self.history_runs: list[undo_mod.RunInfo] = []
        self._rescanned_note = False
        self.var_pattern = tk.StringVar(value=self.prefs.pattern)
        self.var_where = tk.StringVar(value="inplace")
        self.var_dest = tk.StringVar()
        self.var_mode = tk.StringVar(value="move")
        self.var_remove_empty = tk.BooleanVar(value=self.prefs.remove_empty)
        self.var_untagged = tk.BooleanVar(value=self.prefs.include_untagged)
        self.var_sidecars = tk.BooleanVar(value=self.prefs.move_sidecars)
        self.var_fp = tk.BooleanVar(value=False)
        self.var_dupes_action = tk.StringVar(value=self.prefs.dupes_action)
        self.var_pref = tk.StringVar(value=self.prefs.artist_name_preference)
        self.var_lang = tk.StringVar(value=i18n.LANG_NAMES[i18n.current_lang()])
        root.configure(bg=BG)
        # as tall as the screen allows (the table gets the extra room), never taller
        screen_h = root.winfo_screenheight() - 90
        height = max(700, min(1000, screen_h))
        root.geometry(f"1280x{height}")
        root.minsize(1000, min(840, screen_h))  # below ~840 px the duplicates table has no rows left
        self._icon = self._icon_big = None
        icon = os.path.join(i18n.resource_dir(), "assets", "icon.png")
        if os.path.exists(icon):
            try:
                full = tk.PhotoImage(file=icon)  # 256 px
                self._icon = full.subsample(7)
                self._icon_big = full.subsample(4)
            except tk.TclError:
                pass
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
        from tkinter import font as tkfont

        root = self.root
        ui = _pick_font(root, UI_FONTS.get(i18n.current_lang(), ()))
        # Malgun Gothic has Hangul, kana and hanzi; Yu Gothic and YaHei lack Hangul, which then blurs
        table = _pick_font(root, ("맑은 고딕", "Malgun Gothic"))
        self.f_base = (ui, 10)
        self.f_small = (ui, 9)
        self.f_label = (ui, 9, "bold")
        self.f_title = (ui, 15, "bold")
        self.f_drop = (ui, 13, "bold")
        self.f_table = (table, 10)
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            tkfont.nametofont(name).configure(family=ui, size=10)
        root.option_add("*TCombobox*Listbox.font", self.f_base)
        root.option_add("*TCombobox*Listbox.selectBackground", SELECT_BG)
        root.option_add("*TCombobox*Listbox.selectForeground", TEXT)

        s = ttk.Style(root)
        try:
            s.theme_use("clam")
        except tk.TclError:
            pass
        flat = {"bordercolor": BORDER, "lightcolor": SURFACE, "darkcolor": SURFACE}
        s.configure(".", background=SURFACE, foreground=TEXT, font=self.f_base, focuscolor=ACCENT, **flat)
        s.configure("Body.TFrame", background=BG)
        s.configure("Body.TLabel", background=BG)
        s.configure("Muted.TLabel", foreground=MUTED)
        s.configure("Small.TLabel", foreground=MUTED, font=self.f_small)
        s.configure("Error.TLabel", foreground=ERROR_FG)
        s.configure("Field.TLabel", foreground=MUTED, font=self.f_label)
        s.configure("Title.TLabel", font=self.f_title)
        s.configure("Path.TLabel", font=self.f_base)
        s.configure("Example.TLabel", foreground=ACCENT_HOVER, font=self.f_small)
        s.configure("Ph.TButton", padding=(5, 1), font=(self.f_small[0], 9), background=HEAD_BG, relief="raised",
                    bordercolor=BORDER, lightcolor=HEAD_BG, darkcolor=HEAD_BG)
        s.map("Ph.TButton", background=[("active", ACCENT_SOFT)], foreground=[("active", ACCENT_HOVER)],
              bordercolor=[("active", ACCENT)])
        s.configure("Summary.TLabel", background=ACCENT_SOFT, foreground=ACCENT_HOVER, padding=(12, 8))
        s.configure("SummaryMuted.TLabel", background=HEAD_BG, foreground=MUTED, padding=(12, 8))
        s.configure("SummaryError.TLabel", background=ERROR_SOFT, foreground=ERROR_FG, padding=(12, 8))

        # buttons: white with a hairline border; the primary one is filled
        s.configure("TButton", padding=(14, 6), background=SURFACE, relief="raised", anchor="center",
                    bordercolor="#cdd2d8", lightcolor=SURFACE, darkcolor=SURFACE)  # "flat" would hide the border
        s.map("TButton", background=[("disabled", SURFACE), ("pressed", "#eceef1"), ("active", HEAD_BG)],
              foreground=[("disabled", "#b4b9c0")], bordercolor=[("disabled", BORDER), ("active", "#aeb5be")])
        s.configure("Accent.TButton", background=ACCENT, foreground="#ffffff", bordercolor=ACCENT,
                    lightcolor=ACCENT, darkcolor=ACCENT, font=(self.f_base[0], 10, "bold"), padding=(22, 6))
        s.map("Accent.TButton", background=[("disabled", ACCENT_OFF), ("pressed", ACCENT_HOVER), ("active", ACCENT_HOVER)],
              foreground=[("disabled", "#ffffff")], bordercolor=[("disabled", ACCENT_OFF), ("active", ACCENT_HOVER)],
              lightcolor=[("disabled", ACCENT_OFF), ("active", ACCENT_HOVER)], darkcolor=[("disabled", ACCENT_OFF), ("active", ACCENT_HOVER)])

        # two-way choices as a segmented control
        s.configure("Seg.Toolbutton", padding=(14, 5), background=HEAD_BG, foreground=MUTED, anchor="center",
                    relief="raised", bordercolor=BORDER, lightcolor=HEAD_BG, darkcolor=HEAD_BG)
        s.map("Seg.Toolbutton", background=[("selected", ACCENT_SOFT), ("active", "#eef0f3")],
              foreground=[("selected", ACCENT_HOVER)], bordercolor=[("selected", ACCENT)],
              lightcolor=[("selected", ACCENT_SOFT)], darkcolor=[("selected", ACCENT_SOFT)])
        # on/off options as chips: a check mark appears in the text when on (see _chip)
        s.configure("Chip.Toolbutton", padding=(12, 5), background=SURFACE, foreground=MUTED, anchor="center",
                    relief="raised", bordercolor="#d5dae0", lightcolor=SURFACE, darkcolor=SURFACE)
        s.map("Chip.Toolbutton", background=[("selected", ACCENT_SOFT), ("active", HEAD_BG)],
              foreground=[("selected", ACCENT_HOVER)], bordercolor=[("selected", ACCENT)],
              lightcolor=[("selected", ACCENT_SOFT)], darkcolor=[("selected", ACCENT_SOFT)])
        for w in ("TCheckbutton", "TRadiobutton"):
            s.configure(w, background=SURFACE, indicatorbackground=SURFACE, indicatorforeground=ACCENT,
                        upperbordercolor="#c3c8cf", lowerbordercolor="#c3c8cf", indicatormargin=(0, 0, 6, 0))
            s.map(w, background=[("active", SURFACE)], indicatorbackground=[("selected", SURFACE), ("pressed", ACCENT_SOFT)])

        s.configure("TCombobox", padding=(8, 5), arrowcolor=MUTED, fieldbackground=SURFACE, background=SURFACE, **flat)
        s.map("TCombobox", fieldbackground=[("readonly", SURFACE)], bordercolor=[("focus", ACCENT)],
              selectbackground=[("readonly", SURFACE)], selectforeground=[("readonly", TEXT)])
        s.configure("TEntry", padding=(8, 5), **flat)

        s.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(0, 0, 0, 0))
        s.configure("TNotebook.Tab", padding=(18, 8), background=BG, foreground=MUTED, borderwidth=0,
                    bordercolor=BG, lightcolor=BG, darkcolor=BG, font=(self.f_base[0], 10, "bold"))
        s.map("TNotebook.Tab", background=[("selected", SURFACE), ("active", "#eceef1")],
              foreground=[("selected", ACCENT_HOVER)], bordercolor=[("selected", BORDER)],
              lightcolor=[("selected", SURFACE)], expand=[("selected", (0, 0, 0, 0))])

        s.configure("Treeview", background=SURFACE, fieldbackground=SURFACE, foreground=TEXT, rowheight=30,
                    font=self.f_table, borderwidth=0, **flat)
        s.map("Treeview", background=[("selected", SELECT_BG)], foreground=[("selected", TEXT)])
        s.configure("Treeview.Heading", background=HEAD_BG, foreground=MUTED, font=self.f_label, relief="flat",
                    padding=(8, 7), bordercolor=BORDER, lightcolor=HEAD_BG, darkcolor=HEAD_BG)
        s.map("Treeview.Heading", background=[("active", "#eef0f3")])
        s.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])  # no sunken frame

        s.configure("Vertical.TScrollbar", background="#d5d9de", troughcolor=SURFACE, arrowcolor=MUTED,
                    gripcount=0, arrowsize=12, **flat)
        s.map("Vertical.TScrollbar", background=[("active", "#bfc5cc")])
        s.configure("Accent.Horizontal.TProgressbar", background=ACCENT, troughcolor="#e6e9ed", thickness=6,
                    bordercolor="#e6e9ed", lightcolor=ACCENT, darkcolor=ACCENT)

    def _chip(self, parent, key: str, var: tk.BooleanVar, command) -> ttk.Checkbutton:
        """A toggle chip; its text carries a check mark while it is on."""
        def label() -> str:
            return ("✓ " if var.get() else "") + t(key)

        def toggled() -> None:
            chip.configure(text=label())
            command()

        chip = ttk.Checkbutton(parent, text=label(), variable=var, style="Chip.Toolbutton", command=toggled)
        return chip

    @staticmethod
    def _card(parent, **pack) -> tk.Frame:
        """White panel with a hairline border (tk.Frame: ttk frames cannot colour their border)."""
        card = tk.Frame(parent, bg=SURFACE, highlightbackground=BORDER, highlightcolor=BORDER, highlightthickness=1)
        card.pack(**pack)
        return card

    def _build(self) -> None:
        if self.frame is not None:
            self.frame.destroy()
        self.root.title(t("app_title"))
        outer = ttk.Frame(self.root, style="Body.TFrame")
        outer.pack(fill="both", expand=True)
        self.frame = outer

        # header: icon, name, tagline, language
        head = tk.Frame(outer, bg=SURFACE, highlightbackground=BORDER, highlightthickness=0)
        head.pack(fill="x")
        tk.Frame(outer, bg=BORDER, height=1).pack(fill="x")
        inner = ttk.Frame(head, padding=(20, 14))
        inner.pack(fill="x")
        # fixed-size controls on the right first, so a narrow window cuts the tagline, not them (LESSONS A18)
        lang = ttk.Combobox(inner, textvariable=self.var_lang, values=list(i18n.LANG_NAMES.values()), state="readonly", width=10)
        lang.pack(side="right")
        lang.bind("<<ComboboxSelected>>", self._on_lang)
        ttk.Label(inner, text=t("lbl_language"), style="Muted.TLabel").pack(side="right", padx=8)
        ttk.Button(inner, text=t("btn_settings"), command=self._show_settings).pack(side="right", padx=(0, 16))
        if self._icon is not None:
            ttk.Label(inner, image=self._icon).pack(side="left", padx=(0, 12))
        names = ttk.Frame(inner)
        names.pack(side="left", fill="x", expand=True)
        ttk.Label(names, text=t("app_title"), style="Title.TLabel").pack(anchor="w")
        ttk.Label(names, text=t("app_tagline"), style="Small.TLabel").pack(anchor="w")

        # the action bar is packed before the body so a short window squeezes the table, not the buttons
        bar = ttk.Frame(outer, padding=(20, 12))
        bar.pack(side="bottom", fill="x")
        tk.Frame(outer, bg=BORDER, height=1).pack(side="bottom", fill="x")
        body = ttk.Frame(outer, style="Body.TFrame", padding=(20, 16, 20, 16))
        body.pack(fill="both", expand=True)

        # source + options in one card
        card = self._card(body, fill="x")
        grid = ttk.Frame(card, padding=(18, 12, 18, 10))
        grid.pack(fill="x")
        grid.columnconfigure(1, weight=1)
        row = 0

        def field(text: str) -> None:
            ttk.Label(grid, text=text, style="Field.TLabel").grid(row=row, column=0, sticky="nw", padx=(0, 18), pady=(7, 0))

        field(t("source_label"))
        src = ttk.Frame(grid)
        src.grid(row=row, column=1, sticky="we", pady=(0, 8))
        # the button is packed first so a long path shrinks, not the button (LESSONS A15)
        ttk.Button(src, text=t("btn_pick_folder"), command=self._pick_source).pack(side="right", padx=(12, 0))
        self.lbl_source = ttk.Label(src, text=_short(self.source) if self.source else t("drop_hint"),
                                    style="Path.TLabel" if self.source else "Muted.TLabel")
        self.lbl_source.pack(side="left", fill="x", expand=True, pady=(5, 0))
        row += 1

        field(t("pattern_label"))
        pat = ttk.Frame(grid)
        pat.grid(row=row, column=1, sticky="we", pady=(0, 8))
        top = ttk.Frame(pat)
        top.pack(anchor="w", fill="x")
        self.pattern_box = ttk.Combobox(top, textvariable=self.var_pattern, values=list(prefs_mod.PRESETS), width=58)
        self.pattern_box.pack(side="left")
        # placeholder buttons: a click puts the placeholder where the cursor is
        chips = ttk.Frame(pat)
        chips.pack(anchor="w", fill="x", pady=(5, 0))
        for ph in PATTERN_BUTTONS:
            ttk.Button(chips, text=ph, style="Ph.TButton", width=-1,  # natural width; the theme's minimum is ~11 chars
                       command=lambda p=ph: self._insert_placeholder(p)).pack(side="left", padx=(0, 4))
        # live example: the selected (or first) file, or a sample song
        self.lbl_example = ttk.Label(pat, text="", style="Example.TLabel", justify="left")
        self.lbl_example.pack(anchor="w", fill="x", pady=(5, 0))
        self._wrap(self.lbl_example)
        row += 1

        field(t("dest_label"))
        where = ttk.Frame(grid)
        where.grid(row=row, column=1, sticky="we", pady=(0, 8))
        for value, key in (("inplace", "opt_in_place"), ("other", "opt_other_folder")):
            ttk.Radiobutton(where, text=t(key).rstrip(":："), value=value, variable=self.var_where,
                            style="Seg.Toolbutton", command=self._dest_changed).pack(side="left")
        self.lbl_dest = ttk.Label(where, text=_short(self.dest_other, 60), style="Muted.TLabel")
        self.lbl_dest.pack(side="left", padx=12)
        ttk.Button(where, text=t("btn_browse"), command=self._pick_dest).pack(side="left")
        row += 1

        field(t("lbl_mode"))
        flags = ttk.Frame(grid)
        flags.grid(row=row, column=1, sticky="w")
        for value, key in (("move", "opt_move"), ("copy", "opt_copy")):
            ttk.Radiobutton(flags, text=t(key), value=value, variable=self.var_mode, style="Seg.Toolbutton",
                            command=self._options_changed).pack(side="left")
        row += 1

        field(t("lbl_options"))
        chips = ttk.Frame(grid)
        chips.grid(row=row, column=1, sticky="w", pady=(8, 0))
        for key, var in (("opt_remove_empty", self.var_remove_empty), ("opt_sidecars", self.var_sidecars),
                         ("opt_include_untagged", self.var_untagged)):
            self._chip(chips, key, var, self._options_changed).pack(side="left", padx=(0, 8))

        nb = ttk.Notebook(body)
        nb.pack(fill="both", expand=True, pady=(16, 0))
        self.notebook = nb
        nb.add(self._build_organize(nb), text=t("tab_organize"))
        nb.add(self._build_dupes(nb), text=t("tab_dupes"))
        nb.add(self._build_artists(nb), text=t("tab_artists"))

        # action bar contents: buttons are packed first so they keep their width
        self.btn_run = ttk.Button(bar, text=t("btn_run"), style="Accent.TButton", command=self._run)
        self.btn_run.pack(side="right")
        self.btn_cancel = ttk.Button(bar, text=t("btn_cancel"), command=self.cancel.set, state="disabled")
        self.btn_cancel.pack(side="right", padx=(0, 8))
        self.btn_undo = ttk.Button(bar, text=t("btn_undo_history"), command=self._show_history)
        self.btn_undo.pack(side="right", padx=(0, 8))
        self.btn_export = ttk.Button(bar, text=t("btn_export_untagged"), command=self._export_untagged)
        self.btn_export.pack(side="right", padx=(0, 8))
        status = ttk.Frame(bar)
        status.pack(side="left", fill="x", expand=True, padx=(0, 16))
        self.lbl_status = ttk.Label(status, text="", style="Muted.TLabel")
        self.lbl_status.pack(anchor="w", fill="x")
        self._wrap(self.lbl_status)
        self.progress = ttk.Progressbar(status, mode="determinate", length=220, style="Accent.Horizontal.TProgressbar")
        self.progress.pack(anchor="w", pady=(6, 0))
        self._refresh_all()

    @staticmethod
    def _wrap(label: ttk.Label) -> None:
        """Wrap the label's text at its current width, so long hints never run off the edge."""
        label.bind("<Configure>", lambda e: label.configure(wraplength=max(200, e.width - 4)))

    def _tab(self, parent) -> ttk.Frame:
        return ttk.Frame(parent, padding=(16, 14, 16, 16))

    def _build_organize(self, parent) -> ttk.Frame:
        tab = self._tab(parent)
        head = ttk.Frame(tab)
        head.pack(fill="x", pady=(0, 10))
        # shown only while some files lack tags (see _fill_organize)
        self.btn_tag_filler = ttk.Button(head, text=t("btn_open_tag_filler"), command=self._open_tag_filler)
        self.lbl_summary = ttk.Label(head, text="", style="SummaryMuted.TLabel")
        self.lbl_summary.pack(side="left", fill="x", expand=True)
        self.lbl_summary.bind("<Configure>", lambda e: self.lbl_summary.configure(wraplength=max(200, e.width - 28)))
        cols = ("check", "current", "new", "status", "artist")
        box = self._table_box(tab)
        tree = ttk.Treeview(box, columns=cols, show="headings", selectmode="extended")
        for c, key, width, stretch in (("check", "", 40, False), ("current", "col_current", 380, True),
                                      ("new", "col_new", 380, True), ("status", "col_status", 130, False),
                                      ("artist", "col_artist", 220, False)):
            tree.heading(c, text=t(key) if key else ON, anchor="center" if c == "check" else "w")
            tree.column(c, width=width, stretch=stretch, anchor="center" if c == "check" else "w")
        tree.heading("check", command=self._toggle_all)
        self._tags(tree)
        tree.tag_configure("conflict", background=CONFLICT_BG)
        tree.tag_configure("dup", foreground=DUP_FG)
        tree.tag_configure("guess", foreground=GUESS_FG)
        tree.tag_configure("muted", foreground="#9aa1ab")
        self._attach_scroll(box, tree)
        tree.bind("<Button-1>", self._on_org_click)
        tree.bind("<space>", lambda e: self._toggle_selected())
        tree.bind("<<TreeviewSelect>>", lambda e: self._update_example())
        self.tree = tree
        self._build_drop_zone(box)
        return tab

    def _build_drop_zone(self, box) -> None:
        """Shown over the empty table until a folder is loaded; a click opens the picker."""
        zone = tk.Canvas(box, bg=SURFACE, highlightthickness=0, cursor="hand2")
        self.drop_zone = zone

        def draw(_event=None) -> None:
            zone.delete("all")
            w, h = zone.winfo_width(), zone.winfo_height()
            if w < 50 or h < 50:
                return
            m = 18
            zone.create_rectangle(m, m, w - m, h - m, outline="#b9e3cf", dash=(6, 4), width=2, fill="#fbfefc")
            cx, cy = w / 2, h / 2
            if self._icon_big is not None:
                zone.create_image(cx, cy - 46, image=self._icon_big)
            zone.create_text(cx, cy + 8, text=t("drop_hint"), font=self.f_drop, fill=TEXT)
            zone.create_text(cx, cy + 36, text=t("drop_sub"), font=self.f_small, fill=MUTED)
            zone.create_text(cx, cy + 66, text=t("btn_pick_folder"), font=(self.f_base[0], 10, "bold"), fill=ACCENT)

        zone.bind("<Configure>", draw)
        zone.bind("<Button-1>", lambda e: self._pick_source())

    def _build_dupes(self, parent) -> ttk.Frame:
        tab = self._tab(parent)
        bar = ttk.Frame(tab)
        bar.pack(fill="x", pady=(0, 8))
        ttk.Button(bar, text=t("btn_find_dupes"), command=self._find_dupes).pack(side="left")
        self._chip(bar, "opt_fingerprint", self.var_fp, self._fp_estimate).pack(side="left", padx=(10, 0))
        self.lbl_fp = ttk.Label(bar, text="", style="Muted.TLabel")
        self.lbl_fp.pack(side="left", padx=8)
        ttk.Radiobutton(bar, text=t("opt_dupes_trash"), value="trash", variable=self.var_dupes_action,
                        style="Seg.Toolbutton", command=self._options_changed).pack(side="right")
        ttk.Radiobutton(bar, text=t("opt_dupes_move", folder=self.prefs.dupes_name()), value="move",
                        variable=self.var_dupes_action, style="Seg.Toolbutton", command=self._options_changed).pack(side="right")
        hint = ttk.Label(tab, text=t("dupes_hint"), style="Small.TLabel", justify="left")
        hint.pack(fill="x", pady=(0, 10))
        self._wrap(hint)
        cols = ("apply", "keep", "path", "format", "bitrate", "length")
        box = self._table_box(tab)
        tree = ttk.Treeview(box, columns=cols, show="tree headings", selectmode="browse")
        tree.column("#0", width=370, stretch=False)
        tree.heading("#0", text=t("col_group"), anchor="w")
        for c, key, width, stretch in (("apply", "col_apply", 64, False), ("keep", "col_keep", 64, False),
                                      ("path", "col_current", 360, True), ("format", "col_format", 60, False),
                                      ("bitrate", "col_bitrate", 100, False), ("length", "col_length", 60, False)):
            tree.heading(c, text=t(key), anchor="center" if c in ("apply", "keep") else "w")
            tree.column(c, width=width, stretch=stretch, anchor="center" if c in ("apply", "keep") else "w")
        tree.column("bitrate", width=112)
        self._tags(tree)
        tree.tag_configure("group", background=HEAD_BG, font=(self.f_table[0], 10, "bold"))
        tree.tag_configure("nokeep", foreground=ERROR_FG)
        tree.tag_configure("off", foreground="#9aa1ab")
        self._attach_scroll(box, tree)
        tree.bind("<Button-1>", self._on_dupe_click)
        self.dtree = tree
        return tab

    def _build_artists(self, parent) -> ttk.Frame:
        tab = self._tab(parent)
        bar = ttk.Frame(tab)
        bar.pack(fill="x", pady=(0, 8))
        ttk.Button(bar, text=t("btn_rename_rep"), command=self._rename_rep).pack(side="left")
        ttk.Button(bar, text=t("btn_ungroup"), command=self._ungroup).pack(side="left", padx=(8, 0))
        ttk.Button(bar, text=t("btn_guess_yes"), command=lambda: self._answer_guess(True)).pack(side="left", padx=(20, 0))
        ttk.Button(bar, text=t("btn_guess_no"), command=lambda: self._answer_guess(False)).pack(side="left", padx=(8, 0))
        ttk.Button(bar, text=t("btn_open_artists"), command=self._open_artists).pack(side="right")
        # second row: the hint on the left, the name preference on the right
        row2 = ttk.Frame(tab)
        row2.pack(fill="x", pady=(0, 10))
        pref = ttk.Frame(row2)
        pref.pack(side="right")
        ttk.Label(pref, text=t("lbl_name_preference"), style="Muted.TLabel").pack(side="left", padx=(0, 8))
        for value, key in (("original", "opt_pref_original"), ("latin", "opt_pref_latin")):
            ttk.Radiobutton(pref, text=t(key), value=value, variable=self.var_pref, style="Seg.Toolbutton",
                            command=self._pref_changed).pack(side="left")
        self.lbl_alias = ttk.Label(row2, text=t("artists_hint"), style="Small.TLabel", justify="left")
        self.lbl_alias.pack(side="left", fill="x", expand=True, padx=(0, 16))
        self._wrap(self.lbl_alias)
        cols = ("variants", "files", "via")
        box = self._table_box(tab)
        tree = ttk.Treeview(box, columns=cols, show="tree headings", selectmode="browse")
        tree.column("#0", width=260, stretch=False)
        tree.heading("#0", text=t("col_rep_name"), anchor="w")
        for c, key, width in (("variants", "col_variants", 520), ("files", "col_files", 70), ("via", "col_via", 220)):
            tree.heading(c, text=t(key), anchor="w")
            tree.column(c, width=width, stretch=c == "variants")
        self._tags(tree)
        tree.tag_configure("guess", foreground=GUESS_FG)
        self._attach_scroll(box, tree)
        self.atree = tree
        return tab

    def _table_box(self, parent) -> tk.Frame:
        return self._card(parent, fill="both", expand=True)

    @staticmethod
    def _tags(tree: ttk.Treeview) -> None:
        tree.tag_configure("odd", background=STRIPE)  # configured first: later tags win

    @staticmethod
    def _attach_scroll(box, tree: ttk.Treeview) -> None:
        sb = ttk.Scrollbar(box, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        tree.pack(side="left", fill="both", expand=True)

    # ------------------------------------------------------------------ events
    def _on_lang(self, _event=None) -> None:
        name = self.var_lang.get()
        code = next((c for c, n in i18n.LANG_NAMES.items() if n == name), i18n.DEFAULT_LANG)
        i18n.set_lang(code)
        for win in (self.history, self.settings_win):
            if win is not None and win.winfo_exists():
                win.destroy()  # it would keep the old language
        self._style()  # the UI font follows the language
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
            self.lbl_dest.configure(text=_short(self.dest_other, 60))
            self._dest_changed()

    def _dest_changed(self) -> None:
        if self.var_where.get() == "other" and not self.dest_other:
            folder = filedialog.askdirectory(title=t("dest_label"))
            if not folder:
                self.var_where.set("inplace")
                return
            self.dest_other = os.path.abspath(folder)
            self.lbl_dest.configure(text=_short(self.dest_other, 60))
        if self.source:
            self.load_source(self.source)  # the dupes folder and a destination inside the source change what a scan sees

    def _options_changed(self) -> None:
        self.prefs.remove_empty = self.var_remove_empty.get()
        self.prefs.include_untagged = self.var_untagged.get()
        self.prefs.move_sidecars = self.var_sidecars.get()
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
        self.lbl_source.configure(text=_short(self.source), style="Path.TLabel")
        self._plan_gen += 1  # a plan still being built belongs to the old folder
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
            index, err = session.make_index(res.tracks, self.prefs, session.existing_folders(res, opts.dest))
            pref = session.preferred_paths(res, opts, index)
            groups = dedupe.find(res.tracks, index.rep, preferred=pref, cancel=self.cancel,
                                 progress=lambda ph, d, n: self.queue.put(("progress", d, n, "msg_hashing")))
            # every group is applied: its recommendation already keeps one file per album
            return ("scanned", (res, index, err, groups))

        self._start(work, "msg_scanning_short")

    def _reindex(self) -> None:
        if self.scan is None:
            return
        self.index, self.alias_error = session.make_index(self.scan.tracks, self.prefs,
                                                          session.existing_folders(self.scan, self.dest()))
        self._replan()

    def _replan(self) -> None:
        self._plan_job = None
        if self.scan is None or self.busy:
            self._refresh_all()
            return
        self._plan_gen += 1  # whatever is in flight now describes old settings
        # these tabs show the groups and the artist index, not the plan: never let them lag
        self._fill_dupes()
        self._fill_artists()
        problem = pattern_mod.validate(self.var_pattern.get())
        if problem:
            self.plan = None
            self.lbl_summary.configure(text=t(problem), style="SummaryError.TLabel")
            self.btn_run.configure(state="disabled")
            self._update_example()
            return
        if self.var_mode.get() == "copy" and scan_mod.key_of(self.dest()) == scan_mod.key_of(self.source):
            self.plan = None
            self.lbl_summary.configure(text=t("err_copy_in_place"), style="SummaryError.TLabel")
            self.btn_run.configure(state="disabled")
            return
        if self.planning:
            self._plan_again = True  # started again with the newest settings when this one returns
        else:
            self._start_plan()
        self._update_example()  # follows the typed pattern now, not when the plan arrives

    def _start_plan(self) -> None:
        """plan.build on a worker thread. It gets copies of what the window can change
        meanwhile (ticks, kept duplicates), and tk variables are read here, not there."""
        gen = self._plan_gen
        scan, index, opts, overrides = self.scan, self.index, self.options(), dict(self.overrides)
        groups = [dataclasses.replace(g, members=list(g.members), keep=set(g.keep)) for g in self.groups]
        self.planning, self._plan_again = True, False
        self.btn_run.configure(state="disabled")  # the plan on screen is about to change
        self._status_before_plan = self.lbl_status.cget("text")
        self.lbl_status.configure(text=t("msg_planning"))

        def work():
            try:
                self.queue.put(("planned", gen, plan_mod.build(scan, opts, index, groups, overrides)))
            except Exception as exc:  # keep the GUI alive and say what happened
                self.queue.put(("planned", gen, exc))

        threading.Thread(target=work, daemon=True).start()

    def _planned(self, gen: int, plan) -> None:
        self.planning = False
        if self.lbl_status.cget("text") == t("msg_planning"):
            self.lbl_status.configure(text=self._status_before_plan)
        if self._plan_again and self.scan is not None and not self.busy:
            self._replan()
            return
        if gen != self._plan_gen:
            self._refresh_all()  # stale (another folder, or the pattern became invalid): drop it
            return
        if isinstance(plan, Exception):
            self.plan = None
            self.lbl_summary.configure(text=str(plan), style="SummaryError.TLabel")
            self._refresh_all()
            return
        self.plan = plan
        self._refresh_all()

    def plan_ready(self) -> bool:
        """The plan on screen matches the current settings (nothing pending or in flight)."""
        return self.plan is not None and not self.planning and self._plan_job is None and not self.busy

    def _refresh_all(self) -> None:
        self._fill_organize()
        self._fill_dupes()
        self._fill_artists()
        self._fp_estimate()
        self._update_example()
        has_plan = self.plan is not None and not self.busy
        self.btn_run.configure(state="normal" if self.plan_ready() and self.plan.summary()["move"] + self.plan.summary()["dupes"] + self.plan.summary()["folders"] else "disabled")
        self.btn_export.configure(state="normal" if has_plan and self.plan.untagged() else "disabled")
        self.btn_undo.configure(state="disabled" if self.busy else "normal")

    def _fill_organize(self) -> None:
        """Rows go in FILL_CHUNK at a time with the event loop running in between:
        10,000 rows in one go held the window for ~0.4 s. The selection and the
        scroll position are put back after the last chunk (LESSONS A20)."""
        tree = self.tree
        if self._fill_job is not None:  # a refill still under way: it was going to restore these
            self.root.after_cancel(self._fill_job)
            self._fill_job = None
        else:
            self._fill_restore = (tree.selection(), tree.yview()[0])
        tree.delete(*tree.get_children())
        self._fill_chunk(self._organize_rows())

    def _fill_chunk(self, rows) -> None:
        self._fill_job = None
        tree = self.tree
        if not tree.winfo_exists():
            return
        chunk = list(itertools.islice(rows, FILL_CHUNK))
        for iid, tags, values in chunk:
            tree.insert("", "end", iid=iid, tags=tags, values=values)
        if len(chunk) == FILL_CHUNK:  # maybe more
            self._fill_job = self.root.after(1, self._fill_chunk, rows)
            return
        selected, top = self._fill_restore
        keep = [k for k in selected if tree.exists(k)]
        if keep and not tree.selection():  # unless a row was picked while the rows came in
            tree.selection_set(keep)
        tree.yview_moveto(top)
        self._update_example()  # the selected row is known only now

    @property
    def filling(self) -> bool:
        return self._fill_job is not None

    def _organize_rows(self):
        """Sets the summary line now; returns the (iid, tags, values) of each table
        row as an iterator, for the chunks to take from."""
        if self.scan is None and not self.busy:
            self.drop_zone.place(x=0, y=0, relwidth=1, relheight=1)  # empty state: a big drop target
        else:
            self.drop_zone.place_forget()
        has_untagged = self.plan is not None and bool(self.plan.untagged())
        packed = bool(self.btn_tag_filler.winfo_manager())  # winfo_ismapped is False on a hidden tab
        if has_untagged and not packed:
            # packed later than the label, so "before=" puts it first in line (LESSONS A15/A18)
            self.btn_tag_filler.pack(side="right", padx=(10, 0), before=self.lbl_summary)
        elif not has_untagged and packed:
            self.btn_tag_filler.pack_forget()
        if self.plan is None:
            if self.scan is None:
                self.lbl_summary.configure(text=t("drop_sub"), style="SummaryMuted.TLabel")
            return iter(())
        s = self.plan.summary()
        text = t("summary", **{k: s[k] for k in ("move", "same", "untagged", "dupes", "folders")})
        if s["sidecars"]:
            text += ", " + t("summary_sidecars", count=s["sidecars"])
        if s["untagged"]:
            text += "   ·   " + t("msg_untagged_hint")
        if self.alias_error:
            text += "   ·   " + t("err_artists_file", path=self.prefs.artists_file(), error=self.alias_error)
        self.lbl_summary.configure(text=text, style="Summary.TLabel")
        plan = self.plan  # a newer plan starts a new fill; this one keeps its own

        def rows():
            for n, item in enumerate(plan.items):
                tags = ["odd"] if n % 2 else []
                if item.status == plan_mod.CONFLICT:
                    tags.append("conflict")
                if item.status == plan_mod.DUP:
                    tags.append("dup")
                if item.guess:
                    tags.append("guess")
                if not item.moves:
                    tags.append("muted")
                new = t("lbl_trash") if item.action == plan_mod.DUPE_TRASH else _rel(item.dst, plan.options.dest)
                status = t(f"status_{item.status}")
                if item.keep_name:
                    status += " · " + t("status_keep_name")
                if item.truncated:
                    status += " · " + t("status_truncated")
                yield item.key, tags, (ON if item.checked else OFF, _rel(item.src, plan.options.root), new, status,
                                       item.artist_note)

        return rows()

    def _fill_dupes(self) -> None:
        tree = self.dtree
        tree.delete(*tree.get_children())
        for g in self.groups:
            label = t("dupe_group", n=g.id, stage=t(f"stage_{g.stage}"), count=len(g.members))
            if g.cross_album:
                label += " · " + t("dupe_cross", count=g.albums)
            tags = ["group"] + (["nokeep"] if not g.keep else ([] if g.applied else ["off"]))
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
        for n, c in enumerate(self.index.merged()):
            via = ", ".join(t(f"via_{v}") for v in sorted(c.via)) or t("via_alias")
            tree.insert("", "end", iid=f"c{c.id}", text=c.rep, tags=["odd"] if n % 2 else [],
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

    # ------------------------------------------------------------------ pattern helper
    def _insert_placeholder(self, ph: str) -> None:
        box = self.pattern_box
        try:
            if box.selection_present():
                box.delete("sel.first", "sel.last")
        except tk.TclError:
            pass
        box.insert("insert", ph)  # the variable's trace re-plans
        box.focus_set()

    def _update_example(self) -> None:
        """The file selected in the table (or the first one) through the current
        pattern; a sample song before a folder is open."""
        if not hasattr(self, "lbl_example"):
            return
        pattern = self.var_pattern.get().strip()
        if pattern_mod.validate(pattern):
            self.lbl_example.configure(text=f"{t('lbl_example')} —      ·  {t('pattern_buttons_hint')}")
            return
        item = None
        if self.plan is not None and self.plan.items:
            sel = self.tree.selection()
            item = next((i for i in self.plan.items if sel and i.key == sel[0]), None) or self.plan.items[0]
        stale = not self.plan_ready() or self.plan.options.pattern != pattern
        if item is not None and stale and item.action not in (plan_mod.DUPE_MOVE, plan_mod.DUPE_TRASH):
            # the new plan is still being built: show this file through the pattern as typed now
            segs = pattern_mod.render(pattern, item.track, self.plan.options.fallbacks,
                                      self.index.rep if self.index else (lambda s: s), item.multi_disc)
            if item.keep_name:
                segs[-1] = os.path.splitext(item.track.name)[0]
            text = f"{_rel(item.src, self.plan.options.root)}  →  {'/'.join(segs)}{os.path.splitext(item.src)[1]}"
        elif item is not None:
            dst = item.dst if item.dst else t("lbl_trash")
            text = f"{_rel(item.src, self.plan.options.root)}  →  {_rel(dst, self.plan.options.dest) if item.dst else dst}"
        else:
            sample = scan_mod.Track(os.path.join("C:\\", "Music", "track03.mp3"), "MP3", 1,
                                    tags=scan_mod.TagSet(title="Title", artist="Artist", album="Album", track="3",
                                                         disc="1/2", year="2024", genre="Pop"))
            fallbacks = {k: self.prefs.fallback(k) for k in ("artist", "album", "year", "genre")}
            segs = pattern_mod.render(pattern, sample, fallbacks)
            text = "track03.mp3  →  " + "/".join(segs) + ".mp3"
        # the "{a|b}" hint rides on this wrapping line so it is never cut off
        self.lbl_example.configure(text=f"{t('lbl_example')} {text}      ·  {t('pattern_buttons_hint')}")

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
        self._replan()  # _fill_organize keeps the selection and the scroll position

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
        if not self.plan_ready():  # a change is not in the plan yet: never run the old one
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

    def _known_logs(self) -> list[str]:
        """Logs whose runs the history shows: this session's, the source's and the
        destination's, and the last one from settings."""
        logs = [self.last_log] if self.last_log else []
        logs += undo_mod.candidate_logs(self.dest() if self.source else None, self.source or None)
        last = i18n.load_settings().get("last_log")
        if isinstance(last, str) and last:
            logs.append(last)
        return [p for p in dict.fromkeys(logs) if p and os.path.isfile(p)]

    def _undo(self) -> None:
        """Undo the newest run that can be undone, after a confirmation."""
        if self.busy:
            return
        run = next((r for r in undo_mod.runs_in(self._known_logs()) if r.can_undo), None)
        if run is None:
            messagebox.showinfo(t("btn_undo"), t("msg_no_log"))
            return
        if not messagebox.askokcancel(t("btn_undo"), t("confirm_undo", time=run.time.replace("T", " "),
                                                         count=run.count("move", "copy"), path=run.dest)):
            return
        self._undo_run(run)

    def _undo_run(self, run: undo_mod.RunInfo) -> None:
        logs = self._known_logs()

        def work():
            return ("undone", undo_mod.undo(run.log, progress=lambda d, n: self.queue.put(("progress", d, n, "msg_undoing")),
                                            cancel=self.cancel, run_id=run.id, other_logs=logs), run.source)

        self._start(work, "msg_undoing")

    # ------------------------------------------------------------------ settings window
    def _show_settings(self) -> None:
        if self.busy:
            return
        if self.settings_win is not None and self.settings_win.winfo_exists():
            self.settings_win.lift()
            return
        win = tk.Toplevel(self.root)
        win.title(t("dlg_settings_title"))
        win.configure(bg=SURFACE)
        win.transient(self.root)
        win.resizable(True, False)
        self.settings_win = win
        p = self.prefs
        self.set_vars = {
            **{f"fallback_{k}": tk.StringVar(value=p.fallbacks.get(k, "")) for k in prefs_mod.FALLBACK_KEYS},
            "dupes_folder": tk.StringVar(value=p.dupes_folder),
            "artists_path": tk.StringVar(value=p.artists_path),
            "fpcalc_path": tk.StringVar(value=p.fpcalc_path),
            "tag_filler_path": tk.StringVar(value=p.tag_filler_path),
        }
        self.set_pref = tk.StringVar(value=p.artist_name_preference)
        frame = ttk.Frame(win, padding=(20, 16))
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        row = 0

        def section(key: str) -> None:
            nonlocal row
            ttk.Label(frame, text=t(key), style="Field.TLabel").grid(row=row, column=0, columnspan=3, sticky="w",
                                                                     pady=(10 if row else 0, 6))
            row += 1

        def entry(field: str, default: str, browse=None) -> None:
            nonlocal row
            ttk.Label(frame, text=t(SETTING_LABELS[field])).grid(row=row, column=0, sticky="w", padx=(0, 14), pady=3)
            ttk.Entry(frame, textvariable=self.set_vars[field], width=44).grid(row=row, column=1, sticky="we", pady=3)
            if browse is not None:
                ttk.Button(frame, text=t("btn_browse"), command=browse).grid(row=row, column=2, sticky="w", padx=(8, 0))
            ttk.Label(frame, text=default, style="Small.TLabel").grid(row=row, column=3, sticky="w", padx=(10, 0))
            row += 1

        section("set_section_fallbacks")
        for k in prefs_mod.FALLBACK_KEYS:
            entry(f"fallback_{k}", t("set_default", value=t(f"fallback_{k}")))
        section("set_section_files")
        entry("dupes_folder", t("set_default", value=t("dupes_folder")))
        entry("artists_path", t("set_default", value="artists.json"),
              lambda: self._browse_setting("artists_path", save=True, kind="json"))
        found = dedupe.find_fpcalc("")
        entry("fpcalc_path", t("set_auto_found") if found else t("set_auto_missing"),
              lambda: self._browse_setting("fpcalc_path", kind="exe"))
        entry("tag_filler_path", t("set_auto"), lambda: self._browse_setting("tag_filler_path", kind="exe"))
        section("set_section_artists")
        prefs_row = ttk.Frame(frame)
        prefs_row.grid(row=row, column=0, columnspan=4, sticky="w")
        for value, key in (("original", "opt_pref_original"), ("latin", "opt_pref_latin")):
            ttk.Radiobutton(prefs_row, text=t(key), value=value, variable=self.set_pref,
                            style="Seg.Toolbutton").pack(side="left")
        row += 1
        bar = ttk.Frame(frame)
        bar.grid(row=row, column=0, columnspan=4, sticky="we", pady=(18, 0))
        ttk.Button(bar, text=t("btn_save"), style="Accent.TButton", command=self._save_settings).pack(side="right")
        ttk.Button(bar, text=t("btn_cancel"), command=win.destroy).pack(side="right", padx=(0, 8))
        ttk.Button(bar, text=t("btn_reset"), command=self._reset_settings).pack(side="left")

    def _browse_setting(self, field: str, save: bool = False, kind: str = "exe") -> None:
        types = [("JSON", "*.json")] if kind == "json" else [("exe", "*.exe")]
        current = self.set_vars[field].get()
        start = os.path.dirname(current) if current else i18n.app_dir()
        ask = filedialog.asksaveasfilename if save else filedialog.askopenfilename
        path = ask(parent=self.settings_win, initialdir=start, filetypes=types,
                   **({"defaultextension": ".json", "initialfile": "artists.json"} if save else {}))
        if path:
            self.set_vars[field].set(os.path.abspath(path))

    def _reset_settings(self) -> None:
        for var in self.set_vars.values():
            var.set("")
        self.set_pref.set("original")

    def _save_settings(self) -> None:
        values = {k: v.get().strip() for k, v in self.set_vars.items()}
        values["fallbacks"] = {k: values.pop(f"fallback_{k}") for k in prefs_mod.FALLBACK_KEYS}
        problems = prefs_mod.check(values)
        if problems:
            messagebox.showerror(t("dlg_settings_title"),
                                 "\n".join(t(key, field=t(SETTING_LABELS[f])) for f, key in problems),
                                 parent=self.settings_win)
            return
        p = self.prefs
        p.fallbacks = {k: v for k, v in values["fallbacks"].items() if v}
        p.set_dupes_folder(values["dupes_folder"])
        p.artists_path = values["artists_path"]
        p.fpcalc_path = values["fpcalc_path"]
        p.tag_filler_path = values["tag_filler_path"]
        p.artist_name_preference = self.set_pref.get()
        prefs_mod.save(p)
        self.var_pref.set(p.artist_name_preference)
        self.settings_win.destroy()
        self._build()  # the dupes folder name shows on a button
        if self.source and os.path.isdir(self.source):
            self.load_source(self.source)  # a new dupes name changes what a scan skips

    # ------------------------------------------------------------------ undo history window
    def _show_history(self) -> None:
        if self.busy:
            return
        if self.history is not None and self.history.winfo_exists():
            self.history.lift()
            self._fill_history()
            return
        win = tk.Toplevel(self.root)
        win.title(t("dlg_history_title"))
        win.configure(bg=SURFACE)
        win.geometry("980x560")
        win.minsize(760, 420)
        win.transient(self.root)
        self.history = win
        frame = ttk.Frame(win, padding=16)
        frame.pack(fill="both", expand=True)
        hint = ttk.Label(frame, text=t("history_hint"), style="Small.TLabel", justify="left")
        hint.pack(anchor="w", fill="x", pady=(0, 8))
        self._wrap(hint)
        bar = ttk.Frame(frame)
        bar.pack(side="bottom", fill="x", pady=(12, 0))
        ttk.Button(bar, text=t("btn_close"), command=win.destroy).pack(side="right")
        self.btn_undo_selected = ttk.Button(bar, text=t("btn_undo_selected"), style="Accent.TButton",
                                            command=self._undo_selected)
        self.btn_undo_selected.pack(side="right", padx=(0, 8))
        self.lbl_history_detail = ttk.Label(frame, text="", style="Muted.TLabel", justify="left")
        self.lbl_history_detail.pack(side="bottom", fill="x", pady=(10, 0))
        self._wrap(self.lbl_history_detail)
        box = self._card(frame, fill="both", expand=True)
        cols = ("time", "mode", "count", "where", "state")
        tree = ttk.Treeview(box, columns=cols, show="headings", selectmode="browse")
        for c, key, width, stretch in (("time", "col_time", 150, False), ("mode", "col_mode", 70, False),
                                      ("count", "col_files", 72, False), ("where", "col_where", 300, True),
                                      ("state", "col_state", 320, False)):
            tree.heading(c, text=t(key), anchor="w")
            tree.column(c, width=width, stretch=stretch)
        self._tags(tree)
        tree.tag_configure("done", foreground="#9aa1ab")
        self._attach_scroll(box, tree)
        tree.bind("<<TreeviewSelect>>", lambda e: self._history_selected())
        self.htree = tree
        win.protocol("WM_DELETE_WINDOW", win.destroy)
        self._fill_history()

    def _fill_history(self) -> None:
        if self.history is None or not self.history.winfo_exists():
            return
        self.history_runs = undo_mod.runs_in(self._known_logs())
        tree = self.htree
        tree.delete(*tree.get_children())
        for n, r in enumerate(self.history_runs):
            state = t("state_undoable") if r.can_undo else t("state_undone", time=_when(r.undone_time, today_short=True))
            if r.skipped:
                state += " " + t("state_skipped", count=len(r.skipped))
            tags = (["odd"] if n % 2 else []) + ([] if r.can_undo else ["done"])
            mode = t("opt_copy") if r.mode == "copy" else t("opt_move")
            tree.insert("", "end", iid=str(n), tags=tags,
                        values=(_when(r.time), mode, r.files, _short(r.dest, 48), state))
        if not self.history_runs:
            self.lbl_history_detail.configure(text=t("msg_no_log"))
            self.btn_undo_selected.configure(state="disabled")
            return
        first = next((str(i) for i, r in enumerate(self.history_runs) if r.can_undo), "0")
        tree.selection_set(first)
        tree.see(first)
        self._history_selected()

    def _history_run(self) -> undo_mod.RunInfo | None:
        sel = self.htree.selection()
        return self.history_runs[int(sel[0])] if sel else None

    def _history_selected(self) -> None:
        r = self._history_run()
        if r is None:
            return
        if scan_mod.key_of(r.source) == scan_mod.key_of(r.dest):
            where = t("history_inplace", path=_short(r.dest, 110))
        else:
            where = t("history_paths", source=_short(r.source, 60), dest=_short(r.dest, 60))
        lines = [where, t("history_counts", move=r.count("move"), copy=r.count("copy"), trash=r.count("trash"),
                          folders=r.count("rmdir"))]
        if r.skipped:
            lines.append(t("history_skipped_head", count=len(r.skipped)))
            lines += [f"  • {_rel(p, r.source)} — {why}" for p, why in r.skipped[:8]]
        blocked = undo_mod.blockers(r, self.history_runs) if r.can_undo else []
        if blocked:
            lines.append(t("msg_undo_blocked", time=_when(blocked[0].time), id=blocked[0].id))
        self.lbl_history_detail.configure(text="\n".join(lines))
        self.btn_undo_selected.configure(state="normal" if r.can_undo and not blocked and not self.busy else "disabled")

    def _undo_selected(self) -> None:
        r = self._history_run()
        if r is None or not r.can_undo or self.busy:
            return
        if not messagebox.askokcancel(t("btn_undo"), t("confirm_undo", time=r.time.replace("T", " "),
                                                         count=r.count("move", "copy"), path=r.dest),
                                      parent=self.history):
            return
        self._undo_run(r)

    # ------------------------------------------------------------------ hand-off to music-tag-filler
    def _find_tag_filler(self) -> str | None:
        """The saved path, else a music-tag-filler.exe next to this program or in the
        sibling repository's dist folder; else ask once and remember the answer."""
        here = i18n.app_dir()
        candidates = [self.prefs.tag_filler_path] if self.prefs.tag_filler_path else []
        for base in (here, os.path.dirname(here), os.path.dirname(os.path.dirname(here))):
            candidates += [os.path.join(base, TAG_FILLER_EXE),
                           os.path.join(base, "music-tag-filler", "dist", TAG_FILLER_EXE)]
        for c in candidates:
            if c and os.path.isfile(c):
                return c
        path = filedialog.askopenfilename(title=t("dlg_pick_tag_filler"), initialfile=TAG_FILLER_EXE,
                                          filetypes=[("music-tag-filler", "*.exe *.py")])
        if not path:
            return None
        self.prefs.tag_filler_path = os.path.abspath(path)
        prefs_mod.save(self.prefs)
        return self.prefs.tag_filler_path

    def _open_tag_filler(self) -> None:
        if self.plan is None or self.busy:
            return
        untagged = [i.track for i in self.plan.untagged() if not i.track.error]
        files = [tr.path for tr in untagged if os.path.splitext(tr.path)[1].lower() in TAG_FILLER_EXTS]
        skipped = len(self.plan.untagged()) - len(files)
        if not files:
            messagebox.showinfo(t("btn_open_tag_filler"), t("msg_tag_filler_none"))
            return
        exe = self._find_tag_filler()
        if exe is None:
            return
        args, folders = files, 0
        if sum(len(f) + 3 for f in files) > CMDLINE_LIMIT:  # Windows caps a command line at 32 767 characters
            args = sorted({os.path.dirname(f) for f in files})
            folders = len(args)
        cmd = [sys.executable, exe, *args] if exe.lower().endswith(".py") and not getattr(sys, "frozen", False) else [exe, *args]
        try:
            self._filler = subprocess.Popen(cmd, cwd=os.path.dirname(exe))
        except OSError as exc:
            messagebox.showerror(t("btn_open_tag_filler"), t("err_tag_filler_launch", error=exc))
            return
        text = t("msg_tag_filler_started", count=len(files))
        if skipped:
            text += " " + t("msg_tag_filler_skipped", count=skipped)
        if folders:
            text += " " + t("msg_tag_filler_folders", count=folders)
        self.lbl_status.configure(text=text)

    def _check_tag_filler(self) -> None:
        """Called from the poll loop: when music-tag-filler closes, read the tags again."""
        if self._filler is None or self._filler.poll() is None or self.busy:
            return
        self._filler = None
        if self.source and os.path.isdir(self.source):
            self._rescanned_note = True
            self.load_source(self.source)

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
        self._check_tag_filler()
        self.root.after(100, self._poll)

    def _handle(self, msg) -> None:
        kind = msg[0]
        if kind == "planned":  # not a "busy" job: the window stays usable meanwhile
            self._planned(msg[1], msg[2])
            return
        if kind == "progress":
            _, done, total, key = msg
            self.progress.configure(maximum=max(total, 1), value=done)
            self.lbl_status.configure(text=f"{t(key)} {done}/{total}")
            return
        self.busy = False
        self.btn_cancel.configure(state="disabled")
        self.progress.configure(value=0)  # an idle bar stays empty, not "full"
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
                if self._rescanned_note:  # back from music-tag-filler
                    self._rescanned_note = False
                    self.lbl_status.configure(text=t("msg_rescanned_after_fill", count=n))
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
        if res.blocked_by:
            first = res.blocked_by[0]
            messagebox.showwarning(t("btn_undo"), t("msg_undo_blocked", time=first.time.replace("T", " "), id=first.id))
        elif res.nothing:
            messagebox.showinfo(t("btn_undo"), t("msg_no_log"))
        elif res.log_error:
            messagebox.showerror(t("btn_undo"), res.log_error)
        else:
            lines = [t("msg_undo_done", count=res.restored)]
            if res.skipped:
                lines.append(t("msg_undo_skipped", count=len(res.skipped)))
                lines += [f"• {p} — {r}" for p, r in res.skipped[:MAX_LIST]]
            if res.trashed:
                lines.append(t("msg_undo_trashed", count=len(res.trashed)))
            if res.log_unsaved:
                lines.append(res.log_unsaved)
            warn = res.skipped or res.trashed or res.log_unsaved
            (messagebox.showwarning if warn else messagebox.showinfo)(t("btn_undo"), "\n".join(lines))
            self.lbl_status.configure(text=lines[0])
        self.cancel.clear()
        self._fill_history()
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
