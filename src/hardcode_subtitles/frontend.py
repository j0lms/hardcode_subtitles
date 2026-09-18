"""
Tkinter front-end. Everything the worker threads want to show goes through
`App.post()`, which queues a callable that is executed on the Tk thread
(Tkinter widgets must only be touched from the main thread).
"""

from __future__ import annotations

import glob
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
from dataclasses import asdict, fields
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont

from . import backend
from .backend import BatchRunner, Job, Settings

# ------------------------------------------------------------------ palette
BG, PANEL, FIELD = "#16181d", "#1e2128", "#262a33"
FG, MUTED, ACCENT = "#e6e8ee", "#8b91a1", "#4f8cff"
OK, BAD, WARN = "#4cc38a", "#ff6b6b", "#f0b429"

VIDEO_TYPES = [
    ("Video files", "*.mp4 *.mkv *.mov *.avi *.webm *.m4v"),
    ("All files", "*.*"),
]
CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".hardcode_subtitles.json")
AUTO_LABEL = "Auto (best available)"


def fmt_time(seconds) -> str:
    if seconds is None or seconds != seconds:  # None or NaN
        return "—"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def find_matching_srt(video: str) -> str:
    """episode01.mp4 -> episode01.srt, or episode01.en.srt etc."""
    stem = os.path.splitext(video)[0]
    exact = stem + ".srt"
    if os.path.exists(exact):
        return exact
    loose = sorted(glob.glob(glob.escape(stem) + ".*.srt"))
    return loose[0] if loose else ""


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.jobs: list[Job] = []
        self.runner: BatchRunner | None = None
        self.batch: list[Job] = []
        self.ui_calls: queue.Queue = queue.Queue()
        self.enc_choices: dict[str, str] = {AUTO_LABEL: "auto"}
        self.preview_img = None
        self.cancelling = False

        root.title("Subtitle Burner")
        root.geometry("980x720")
        root.minsize(860, 600)
        root.configure(bg=BG)
        self._style()
        self._build()
        self._load_config()
        self._enable_drop()

        root.protocol("WM_DELETE_WINDOW", self._on_close)
        root.after(80, self._drain)
        threading.Thread(target=self._detect_encoders, daemon=True).start()

    # ------------------------------------------------------------ plumbing
    def post(self, fn, *args):
        """Thread-safe: schedule fn(*args) on the Tk thread."""
        self.ui_calls.put((fn, args))

    def _drain(self):
        try:
            while True:
                fn, args = self.ui_calls.get_nowait()
                fn(*args)
        except queue.Empty:
            pass
        self.root.after(80, self._drain)

    # --------------------------------------------------------------- style
    def _style(self):
        r = self.root
        st = ttk.Style(r)
        st.theme_use("clam")
        st.configure(
            ".",
            background=BG,
            foreground=FG,
            fieldbackground=FIELD,
            bordercolor=FIELD,
            lightcolor=FIELD,
            darkcolor=FIELD,
            troughcolor=FIELD,
            font=("Segoe UI", 10),
        )
        st.configure("TFrame", background=BG)
        st.configure("Panel.TLabelframe", background=PANEL, bordercolor=FIELD)
        st.configure(
            "Panel.TLabelframe.Label",
            background=PANEL,
            foreground=MUTED,
            font=("Segoe UI", 9, "bold"),
        )
        st.configure("Panel.TFrame", background=PANEL)
        st.configure("TLabel", background=BG, foreground=FG)
        st.configure("Panel.TLabel", background=PANEL, foreground=FG)
        st.configure("Muted.TLabel", background=BG, foreground=MUTED)
        st.configure("PanelMuted.TLabel", background=PANEL, foreground=MUTED)
        st.configure(
            "TButton",
            background=FIELD,
            foreground=FG,
            padding=(12, 6),
            borderwidth=0,
        )
        st.map(
            "TButton",
            background=[("active", "#323846"), ("disabled", PANEL)],
            foreground=[("disabled", MUTED)],
        )
        st.configure(
            "Accent.TButton",
            background=ACCENT,
            foreground="white",
            font=("Segoe UI", 10, "bold"),
        )
        st.map(
            "Accent.TButton",
            background=[("active", "#6ea0ff"), ("disabled", "#2a3a5c")],
            foreground=[("disabled", "#8fa3c9")],
        )
        st.configure("TEntry", insertcolor=FG, padding=4)
        st.configure(
            "TCombobox",
            arrowcolor=FG,
            padding=4,
            selectbackground=FIELD,
            selectforeground=FG,
        )
        st.map(
            "TCombobox",
            fieldbackground=[("readonly", FIELD)],
            selectbackground=[("readonly", FIELD)],
            selectforeground=[("readonly", FG)],
        )
        st.configure("TSpinbox", arrowcolor=FG, padding=4)
        st.configure(
            "Horizontal.TScale",
            background=ACCENT,
            troughcolor="#3a4050",
            bordercolor=PANEL,
            lightcolor=ACCENT,
            darkcolor=ACCENT,
        )
        st.configure(
            "Horizontal.TProgressbar",
            background=ACCENT,
            troughcolor=FIELD,
            thickness=10,
        )
        st.configure(
            "Treeview",
            background=PANEL,
            fieldbackground=PANEL,
            foreground=FG,
            rowheight=28,
            borderwidth=0,
        )
        st.configure(
            "Treeview.Heading",
            background=FIELD,
            foreground=MUTED,
            relief="flat",
            padding=6,
            font=("Segoe UI", 9, "bold"),
        )
        st.map(
            "Treeview",
            background=[("selected", "#2c3a57")],
            foreground=[("selected", FG)],
        )
        st.map("Treeview.Heading", background=[("active", FIELD)])
        r.option_add("*TCombobox*Listbox.background", FIELD)
        r.option_add("*TCombobox*Listbox.foreground", FG)
        r.option_add("*TCombobox*Listbox.selectBackground", ACCENT)

    # ---------------------------------------------------------------- build
    def _build(self):
        pad = dict(padx=16)
        outer = ttk.Frame(self.root)
        outer.pack(fill="both", expand=True)

        # toolbar
        bar = ttk.Frame(outer)
        bar.pack(fill="x", pady=(14, 8), **pad)
        ttk.Button(bar, text="＋ Add videos…", command=self.add_videos).pack(
            side="left"
        )
        ttk.Button(
            bar, text="Set subtitles…", command=self.set_subtitles
        ).pack(side="left", padx=6)
        ttk.Button(bar, text="Remove", command=self.remove_selected).pack(
            side="left"
        )
        ttk.Button(
            bar, text="Clear finished", command=self.clear_finished
        ).pack(side="left", padx=6)
        self.preview_btn = ttk.Button(
            bar, text="Preview style", command=self.preview
        )
        self.preview_btn.pack(side="right")

        # queue
        box = ttk.Frame(outer)
        box.pack(fill="both", expand=True, **pad)
        cols = ("video", "subs", "length", "status", "progress")
        self.tree = ttk.Treeview(
            box, columns=cols, show="headings", selectmode="extended"
        )
        for c, title, w, anchor in (
            ("video", "Video", 300, "w"),
            ("subs", "Subtitles", 220, "w"),
            ("length", "Length", 70, "e"),
            ("status", "Status", 100, "w"),
            ("progress", "Progress", 200, "w"),
        ):
            self.tree.heading(c, text=title, anchor=anchor)
            self.tree.column(
                c, width=w, anchor=anchor, stretch=c in ("video", "subs")
            )
        sb = ttk.Scrollbar(box, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        for tag, colour in (
            ("done", OK),
            ("failed", BAD),
            ("missing", WARN),
            ("working", ACCENT),
        ):
            self.tree.tag_configure(tag, foreground=colour)
        self.tree.bind("<Double-1>", lambda e: self.set_subtitles())
        self.tree.bind("<Delete>", lambda e: self.remove_selected())
        self.hint = ttk.Label(
            box,
            text=(
                "Drop videos here, or click “Add videos…”\n"
                "Matching .srt files (same name) are picked up automatically."
            ),
            style="Muted.TLabel",
            justify="center",
            background=PANEL,
        )
        self.hint.place(relx=0.5, rely=0.5, anchor="center")

        # settings
        row = ttk.Frame(outer)
        row.pack(fill="x", pady=12, **pad)
        row.columnconfigure((0, 1), weight=1, uniform="a")
        self._build_encoding(row).grid(
            row=0, column=0, sticky="nsew", padx=(0, 6)
        )
        self._build_style(row).grid(
            row=0, column=1, sticky="nsew", padx=(6, 0)
        )

        # bottom bar
        foot = ttk.Frame(outer)
        foot.pack(fill="x", pady=(0, 14), **pad)
        self.overall = ttk.Progressbar(foot, maximum=1.0)
        self.overall.pack(fill="x", pady=(0, 8))
        line = ttk.Frame(foot)
        line.pack(fill="x")
        self.status = ttk.Label(
            line, text="Add some videos to get started.", style="Muted.TLabel"
        )
        self.status.pack(side="left")
        self.start_btn = ttk.Button(
            line, text="▶  Start", style="Accent.TButton", command=self.start
        )
        self.start_btn.pack(side="right")
        self.cancel_btn = ttk.Button(
            line, text="Cancel", command=self.cancel, state="disabled"
        )
        self.cancel_btn.pack(side="right", padx=6)
        ttk.Button(line, text="Open folder", command=self.open_folder).pack(
            side="right"
        )

    def _labelled(self, parent, r, c, text, widget, span=1):
        ttk.Label(parent, text=text, style="PanelMuted.TLabel").grid(
            row=r, column=c, sticky="w", pady=4, padx=(0, 8)
        )
        widget.grid(row=r, column=c + 1, columnspan=span, sticky="ew", pady=4)

    def _build_encoding(self, parent):
        f = ttk.LabelFrame(
            parent, text="ENCODING", style="Panel.TLabelframe", padding=12
        )
        f.columnconfigure(1, weight=1)
        self.encoder_var = tk.StringVar(value=AUTO_LABEL)
        self.encoder_cb = ttk.Combobox(
            f,
            textvariable=self.encoder_var,
            state="readonly",
            values=[AUTO_LABEL],
        )
        self._labelled(f, 0, 0, "Encoder", self.encoder_cb)

        self.quality_var = tk.IntVar(value=22)
        qf = ttk.Frame(f, style="Panel.TFrame")
        ttk.Scale(
            qf,
            from_=16,
            to=30,
            variable=self.quality_var,
            style="Horizontal.TScale",
            command=lambda v: self.quality_var.set(round(float(v))),
        ).pack(side="left", fill="x", expand=True)
        self.q_label = ttk.Label(qf, width=18, style="PanelMuted.TLabel")
        self.q_label.pack(side="left", padx=(8, 0))
        self.quality_var.trace_add("write", self._update_q_label)
        self._labelled(f, 1, 0, "Quality", qf)

        self.preset_var = tk.StringVar(value="veryfast")
        self._labelled(
            f,
            2,
            0,
            "CPU speed",
            ttk.Combobox(
                f,
                textvariable=self.preset_var,
                state="readonly",
                values=[
                    "ultrafast",
                    "superfast",
                    "veryfast",
                    "faster",
                    "fast",
                    "medium",
                ],
            ),
        )

        self.workers_var = tk.IntVar(value=1)
        self._labelled(
            f,
            3,
            0,
            "Videos at once",
            ttk.Spinbox(
                f, from_=1, to=4, width=5, textvariable=self.workers_var
            ),
        )
        self._update_q_label()
        return f

    def _update_q_label(self, *_):
        q = self.quality_var.get()
        self.q_label.config(
            text=(
                f"{q} "
                f" ({'high' if q <= 20 else 'balanced' if q <= 24 else 'small file'})"
            )
        )

    def _build_style(self, parent):
        f = ttk.LabelFrame(
            parent,
            text="SUBTITLE STYLE",
            style="Panel.TLabelframe",
            padding=12,
        )
        f.columnconfigure(1, weight=1)
        self.font_var = tk.StringVar(value="Arial")
        families = sorted(set(tkfont.families()))
        self._labelled(
            f,
            0,
            0,
            "Font",
            ttk.Combobox(f, textvariable=self.font_var, values=families),
        )
        self.size_var = tk.DoubleVar(value=5.0)
        self.outline_var = tk.DoubleVar(value=0.28)
        self.bottom_var = tk.DoubleVar(value=6.0)
        self.width_var = tk.DoubleVar(value=70.0)
        for i, (label, var, lo, hi, inc) in enumerate(
            (
                ("Size (% of height)", self.size_var, 2, 10, 0.5),
                ("Outline (%)", self.outline_var, 0.05, 1.0, 0.05),
                ("Bottom margin (%)", self.bottom_var, 1, 30, 1),
                ("Max width (%)", self.width_var, 30, 100, 5),
            ),
            start=1,
        ):
            self._labelled(
                f,
                i,
                0,
                label,
                ttk.Spinbox(
                    f,
                    from_=lo,
                    to=hi,
                    increment=inc,
                    width=7,
                    textvariable=var,
                ),
            )
        return f

    # -------------------------------------------------------------- settings
    def current_settings(self) -> Settings:
        return Settings(
            encoder=self.enc_choices.get(self.encoder_var.get(), "auto"),
            quality=int(self.quality_var.get()),
            x264_preset=self.preset_var.get(),
            font=self.font_var.get() or "Arial",
            font_pct=float(self.size_var.get()),
            outline_pct=float(self.outline_var.get()),
            bottom_pct=float(self.bottom_var.get()),
            max_width_pct=float(self.width_var.get()),
            workers=int(self.workers_var.get()),
        )

    def _load_config(self):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                data = json.load(f)
        except OSError, ValueError:
            return
        try:
            self.quality_var.set(int(data.get("quality", 22)))
            self.preset_var.set(data.get("x264_preset", "veryfast"))
            self.font_var.set(data.get("font", "Arial"))
            self.size_var.set(float(data.get("font_pct", 5.0)))
            self.outline_var.set(float(data.get("outline_pct", 0.28)))
            self.bottom_var.set(float(data.get("bottom_pct", 6.0)))
            self.width_var.set(float(data.get("max_width_pct", 70.0)))
            self.workers_var.set(int(data.get("workers", 1)))
            self._saved_encoder = data.get("encoder", "auto")
        except TypeError, ValueError:
            pass

    def _save_config(self):
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(asdict(self.current_settings()), f, indent=2)
        except OSError, tk.TclError, ValueError:
            pass

    # ---------------------------------------------------- encoder detection
    def _detect_encoders(self):
        try:
            avail = backend.available_encoders()
            libass = backend.has_libass()
        except Exception as exc:
            self.post(self._set_status, f"FFmpeg problem: {exc}", BAD)
            return
        self.post(self._encoders_ready, avail, libass)

    def _encoders_ready(self, avail, libass):
        best = (
            backend.ENCODER_LABELS.get(avail[0], avail[0])
            if avail
            else "CPU (libx264)"
        )
        self.enc_choices = {f"{AUTO_LABEL} → {best}": "auto"}
        self.enc_choices.update(
            {backend.ENCODER_LABELS.get(n, n): n for n in avail}
        )
        self.encoder_cb.config(values=list(self.enc_choices))
        saved = getattr(self, "_saved_encoder", "auto")
        pick = next(
            (lbl for lbl, n in self.enc_choices.items() if n == saved),
            next(iter(self.enc_choices)),
        )
        self.encoder_var.set(pick)
        if not libass:
            self._set_status(
                "This FFmpeg build has no libass, so subtitles can't be"
                " rendered.",
                BAD,
            )
            self.start_btn.config(state="disabled")

    # -------------------------------------------------------------- drag&drop
    def _enable_drop(self):
        if not hasattr(self.root, "drop_target_register"):
            return  # tkinterdnd2 not installed: everything still works via the buttons
        try:
            self.root.drop_target_register("DND_Files")
            self.root.dnd_bind(
                "<<Drop>>",
                lambda e: self._handle_paths(self.root.tk.splitlist(e.data)),
            )
        except Exception:
            pass

    # ----------------------------------------------------------- queue edits
    def add_videos(self):
        paths = filedialog.askopenfilenames(
            title="Select videos", filetypes=VIDEO_TYPES
        )
        self._handle_paths(paths)

    def _handle_paths(self, paths):
        videos = [p for p in paths if not p.lower().endswith(".srt")]
        srts = [p for p in paths if p.lower().endswith(".srt")]
        for v in videos:
            job = Job(video=v, subtitle=find_matching_srt(v))
            self.jobs.append(job)
            threading.Thread(
                target=self._probe_duration, args=(job,), daemon=True
            ).start()
        for (
            s
        ) in srts:  # a dropped .srt goes to the first job that still needs one
            target = next((j for j in self.jobs if not j.subtitle), None)
            if target:
                target.subtitle = s
        self.refresh()

    def _probe_duration(self, job: Job):
        try:
            job.duration = backend.probe(job.video).duration
        except Exception:
            job.duration = 0.0
        self.post(self.refresh)

    def set_subtitles(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo(
                "Subtitles", "Select a video in the list first."
            )
            return
        job = next(j for j in self.jobs if str(j.id) == sel[0])
        path = filedialog.askopenfilename(
            title="Select .srt",
            filetypes=[("SubRip", "*.srt")],
            initialdir=os.path.dirname(job.video),
        )
        if path:
            job.subtitle = path
            if job.status == "Failed":
                job.status, job.error = "Queued", ""
            self.refresh()

    def remove_selected(self):
        if self.runner:
            return
        ids = set(self.tree.selection())
        self.jobs = [j for j in self.jobs if str(j.id) not in ids]
        self.refresh()

    def clear_finished(self):
        if self.runner:
            return
        self.jobs = [j for j in self.jobs if j.status != "Done"]
        self.refresh()

    def refresh(self):
        selected = set(self.tree.selection())
        self.tree.delete(*self.tree.get_children())
        for j in self.jobs:
            self._insert_row(j)
        keep = [i for i in self.tree.get_children() if i in selected]
        if keep:
            self.tree.selection_set(keep)
        (
            self.hint.place_forget()
            if self.jobs
            else self.hint.place(relx=0.5, rely=0.5, anchor="center")
        )
        self._update_overall()

    def _row_values(self, j: Job):
        subs = (
            os.path.basename(j.subtitle)
            if j.subtitle
            else "⚠ none — double-click to choose"
        )
        if j.status == "Working":
            prog = (
                f"{j.progress:.0%}  ·  {j.speed:.1f}x  ·  {fmt_time(j.eta)}"
                " left"
            )
        elif j.status == "Done":
            prog = os.path.basename(j.output)
        elif j.status == "Failed":
            prog = (j.error.splitlines() or ["error"])[-1][:60]
        else:
            prog = ""
        return (
            os.path.basename(j.video),
            subs,
            fmt_time(j.duration) if j.duration else "…",
            j.status,
            prog,
        )

    def _tag(self, j: Job):
        if j.status == "Done":
            return ("done",)
        if j.status == "Failed":
            return ("failed",)
        if j.status == "Working":
            return ("working",)
        return ("missing",) if not j.subtitle else ()

    def _insert_row(self, j: Job):
        self.tree.insert(
            "",
            "end",
            iid=str(j.id),
            values=self._row_values(j),
            tags=self._tag(j),
        )

    # ---------------------------------------------------------------- running
    def start(self):
        todo = [j for j in self.jobs if j.status != "Done"]
        if not todo:
            messagebox.showinfo(
                "Nothing to do", "Add at least one video first."
            )
            return
        missing = [os.path.basename(j.video) for j in todo if not j.subtitle]
        if missing:
            messagebox.showerror(
                "Missing subtitles",
                f"{len(missing)} video(s) have no subtitle file yet:\n\n"
                + "\n".join(missing[:8])
                + ("\n…" if len(missing) > 8 else "")
                + "\n\nSelect each one and click “Set subtitles…”.",
            )
            return
        for j in todo:
            j.status, j.progress, j.error, j.speed, j.eta = (
                "Queued",
                0.0,
                "",
                0.0,
                None,
            )
        self.batch = todo
        self._save_config()
        self.start_btn.config(state="disabled")
        self.cancel_btn.config(state="normal")
        self.preview_btn.config(state="disabled")
        self.runner = BatchRunner(
            todo,
            self.current_settings(),
            emit=lambda j: self.post(self._on_job_update, j),
            on_finished=lambda: self.post(self._on_finished),
        )
        self.runner.start()
        self.refresh()

    def cancel(self):
        if self.runner and not self.cancelling:
            self.cancelling = True
            self.cancel_btn.config(state="disabled")
            self._set_status("Cancelling…", WARN)
            self.runner.cancel()

    def _on_job_update(self, job: Job):
        if self.tree.exists(str(job.id)):
            self.tree.item(
                str(job.id), values=self._row_values(job), tags=self._tag(job)
            )
        self._update_overall()

    def _on_finished(self):
        self.runner = None
        was_cancelled, self.cancelling = self.cancelling, False
        self.start_btn.config(state="normal")
        self.cancel_btn.config(state="disabled")
        self.preview_btn.config(state="normal")
        done = sum(j.status == "Done" for j in self.batch)
        failed = sum(j.status == "Failed" for j in self.batch)
        cancelled = sum(j.status == "Cancelled" for j in self.batch)
        parts = (
            [f"{done} finished"]
            + ([f"{failed} failed"] if failed else [])
            + ([f"{cancelled} cancelled"] if cancelled else [])
        )
        self._set_status(
            "  ·  ".join(parts),
            BAD if failed else WARN if was_cancelled else OK,
        )
        self.overall.config(
            value=1.0 if done == len(self.batch) else self.overall["value"]
        )
        self.refresh()
        if failed:
            first = next(j for j in self.batch if j.status == "Failed")
            messagebox.showerror(
                "Something failed",
                f"{os.path.basename(first.video)}\n\n{first.error[-600:]}",
            )

    def _update_overall(self):
        if not self.batch or not self.runner:
            self.overall.config(value=0)
            return
        if self.cancelling:
            return  # keep showing "Cancelling…" until the workers have stopped
        total = sum(max(j.duration, 1.0) for j in self.batch)
        done = sum(
            max(j.duration, 1.0) * (1.0 if j.status == "Done" else j.progress)
            for j in self.batch
        )
        self.overall.config(value=done / total)
        speed = sum(j.speed for j in self.batch if j.status == "Working")
        remaining = total - done
        eta = remaining / speed if speed > 0 else None
        n_done = sum(j.status == "Done" for j in self.batch)
        self._set_status(
            f"{n_done}/{len(self.batch)} done  ·  {done / total:.0%}  ·  about"
            f" {fmt_time(eta)} left",
            FG,
        )

    def _set_status(self, text, colour=MUTED):
        self.status.config(text=text, foreground=colour)

    # ---------------------------------------------------------------- preview
    def preview(self):
        sel = self.tree.selection()
        job = next(
            (j for j in self.jobs if str(j.id) in sel and j.subtitle), None
        ) or next((j for j in self.jobs if j.subtitle), None)
        if not job:
            messagebox.showinfo(
                "Preview", "Add a video with a subtitle file first."
            )
            return
        settings = self.current_settings()
        self._set_status("Rendering preview…", MUTED)
        self.preview_btn.config(state="disabled")

        def work():
            out = os.path.join(
                tempfile.gettempdir(), f"subburner_preview_{os.getpid()}.png"
            )
            try:
                backend.render_preview(job.video, job.subtitle, settings, out)
                self.post(self._show_preview, out, job)
            except Exception as exc:
                self.post(self._preview_failed, str(exc))

        threading.Thread(target=work, daemon=True).start()

    def _preview_failed(self, msg):
        self.preview_btn.config(state="normal")
        self._set_status("Preview failed", BAD)
        messagebox.showerror("Preview failed", msg[-600:])

    def _show_preview(self, png, job):
        self.preview_btn.config(state="normal")
        self._set_status("Preview ready", OK)
        win = tk.Toplevel(self.root, bg=BG)
        win.title(f"Preview — {os.path.basename(job.video)}")
        self.preview_img = tk.PhotoImage(
            file=png
        )  # keep a reference or Tk garbage-collects it
        tk.Label(win, image=self.preview_img, bg=BG, bd=0).pack(
            padx=10, pady=10
        )
        ttk.Label(
            win,
            text=(
                "A frame from the middle of your subtitles, rendered with the"
                " current style."
            ),
            style="Muted.TLabel",
        ).pack(pady=(0, 10))

    # ---------------------------------------------------------------- misc
    def open_folder(self):
        target = next(
            (j.output for j in reversed(self.jobs) if j.output), None
        ) or (self.jobs[-1].video if self.jobs else None)
        if not target:
            return
        folder = os.path.dirname(os.path.abspath(target))
        if sys.platform.startswith("win"):
            os.startfile(folder)  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", folder])
        else:
            subprocess.Popen(["xdg-open", folder])

    def _on_close(self):
        if self.runner:
            if not messagebox.askyesno(
                "Quit", "Encoding is still running. Cancel it and quit?"
            ):
                return
            self.runner.cancel()
        self._save_config()
        self.root.destroy()


def build_ui(root: tk.Tk) -> App:
    return App(root)
