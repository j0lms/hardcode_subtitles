"""
Backend for the subtitle burner.

Instead of pulling every frame into Python (MoviePy) and compositing NumPy arrays,
we hand the whole job to FFmpeg:  decode -> libass renders the subtitles -> encode.
Python only builds a small .ass file, launches FFmpeg and reads its progress output.
"""

from __future__ import annotations

import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Callable, Optional

import pysrt

# --------------------------------------------------------------------------- #
#  FFmpeg discovery
# --------------------------------------------------------------------------- #

_NO_WINDOW = getattr(
    subprocess, "CREATE_NO_WINDOW", 0
)  # hides console popups on Windows


@lru_cache(maxsize=1)
def ffmpeg_path() -> str:
    """Prefer an FFmpeg on PATH; fall back to the binary bundled with imageio-ffmpeg
    (which MoviePy already installs)."""
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "FFmpeg not found. Install it and make sure `ffmpeg` is on your"
            " PATH."
        ) from exc


def _run(args: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        [ffmpeg_path(), "-hide_banner", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=_NO_WINDOW,
    )


@lru_cache(maxsize=1)
def has_libass() -> bool:
    return " ass " in _run(["-filters"]).stdout


# --------------------------------------------------------------------------- #
#  Media probing (uses `ffmpeg -i`, so ffprobe is not required)
# --------------------------------------------------------------------------- #


@dataclass
class MediaInfo:
    duration: float
    width: int
    height: int
    audio_codec: Optional[str]


def probe(video_path: str) -> MediaInfo:
    text = _run(
        ["-i", video_path]
    ).stderr  # ffmpeg prints stream info to stderr

    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
    duration = (int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])) if m else 0.0

    vline = next(
        (l for l in text.splitlines() if "Video:" in l and "Stream" in l), ""
    )
    # strip things like "0x31637661" so the WxH regex can't match them
    vline_clean = re.sub(r"(?<!\w)0x[0-9a-fA-F]+", "", vline)
    m = re.search(r"[\s,](\d{2,5})x(\d{2,5})[\s,\[]", vline_clean)
    if not m:
        raise RuntimeError(
            f"Could not read video stream from: {os.path.basename(video_path)}"
        )
    width, height = int(m[1]), int(m[2])

    aline = next(
        (l for l in text.splitlines() if "Audio:" in l and "Stream" in l), ""
    )
    m = re.search(r"Audio:\s*(\w+)", aline)
    return MediaInfo(duration, width, height, m[1].lower() if m else None)


# --------------------------------------------------------------------------- #
#  Settings
# --------------------------------------------------------------------------- #


@dataclass
class Settings:
    encoder: str = "auto"  # "auto" or an FFmpeg encoder name
    quality: int = 22  # CRF / CQ style value: lower = better & bigger
    x264_preset: str = "veryfast"  # only used by libx264
    font: str = "Arial"
    font_pct: float = 5.0  # font size as % of video height
    outline_pct: float = 0.28  # outline thickness as % of video height
    bottom_pct: float = 6.0  # distance from bottom edge as % of video height
    max_width_pct: float = 70.0  # text block width as % of video width
    workers: int = 1  # videos encoded at the same time


# --------------------------------------------------------------------------- #
#  Encoder detection
# --------------------------------------------------------------------------- #

ENCODER_LABELS = {
    "h264_nvenc": "NVIDIA NVENC (GPU)",
    "h264_qsv": "Intel Quick Sync (GPU)",
    "h264_amf": "AMD AMF (GPU)",
    "h264_videotoolbox": "Apple VideoToolbox (GPU)",
    "libx264": "CPU (libx264)",
}
_GPU_PRIORITY = ["h264_nvenc", "h264_qsv", "h264_amf", "h264_videotoolbox"]


def encoder_args(name: str, s: Settings) -> list[str]:
    q = str(s.quality)
    if name == "libx264":
        return ["-c:v", "libx264", "-preset", s.x264_preset, "-crf", q]
    if name == "h264_nvenc":
        return [
            "-c:v",
            "h264_nvenc",
            "-preset",
            "p4",
            "-rc",
            "vbr",
            "-cq",
            q,
            "-b:v",
            "0",
        ]
    if name == "h264_qsv":
        return [
            "-c:v",
            "h264_qsv",
            "-global_quality",
            q,
            "-preset",
            "veryfast",
        ]
    if name == "h264_amf":
        return [
            "-c:v",
            "h264_amf",
            "-quality",
            "balanced",
            "-rc",
            "cqp",
            "-qp_i",
            q,
            "-qp_p",
            q,
        ]
    if name == "h264_videotoolbox":
        return [
            "-c:v",
            "h264_videotoolbox",
            "-q:v",
            str(max(1, min(100, round(100 - s.quality * 2)))),
        ]
    raise ValueError(f"Unknown encoder {name}")


@lru_cache(maxsize=1)
def available_encoders() -> tuple[str, ...]:
    """An encoder being *compiled in* doesn't mean the hardware exists, so each
    candidate gets a real one-frame test encode. Result is cached."""
    listed = _run(["-encoders"]).stdout
    working = []
    for name in _GPU_PRIORITY + ["libx264"]:
        if name not in listed:
            continue
        r = _run(
            [
                "-f",
                "lavfi",
                "-i",
                "color=c=black:s=320x240:d=0.2:r=25",
                *encoder_args(name, Settings()),
                "-pix_fmt",
                "yuv420p",
                "-f",
                "null",
                "-",
            ],
            timeout=30,
        )
        if r.returncode == 0:
            working.append(name)
    return tuple(working)


def resolve_encoder(choice: str) -> str:
    avail = available_encoders()
    if choice != "auto" and choice in avail:
        return choice
    return avail[0] if avail else "libx264"


# --------------------------------------------------------------------------- #
#  SRT -> ASS (so sizes are in real pixels and styling is fully under our control)
# --------------------------------------------------------------------------- #


def read_srt(path: str) -> pysrt.SubRipFile:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return pysrt.open(path, encoding=enc)
        except UnicodeDecodeError:
            continue
    raise RuntimeError(f"Could not decode subtitle file: {path}")


def _ass_time(t: pysrt.SubRipTime) -> str:
    total_cs = round(t.ordinal / 10)  # ordinal is milliseconds
    cs = total_cs % 100
    s = (total_cs // 100) % 60
    m = (total_cs // 6000) % 60
    h = total_cs // 360000
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _ass_text(text: str) -> str:
    text = text.replace("\r", "")
    for tag, code in (("i", "i"), ("b", "b"), ("u", "u")):
        text = re.sub(rf"<\s*{tag}\s*>", rf"{{\\{code}1}}", text, flags=re.I)
        text = re.sub(
            rf"<\s*/\s*{tag}\s*>", rf"{{\\{code}0}}", text, flags=re.I
        )
    text = re.sub(
        r"<[^>]*>", "", text
    )  # drop <font ...> and any other HTML-ish tags
    text = text.replace(
        "\n", r"\N"
    ).strip()  # real line breaks, not "words glued together"
    return text


def build_ass(
    subs: pysrt.SubRipFile, width: int, height: int, s: Settings
) -> str:
    fontsize = round(height * s.font_pct / 100)
    outline = max(1.0, round(height * s.outline_pct / 100, 2))
    margin_v = round(height * s.bottom_pct / 100)
    margin_h = round(width * (100 - s.max_width_pct) / 200)

    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        (
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour,"
            " OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut,"
            " ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow,"
            " Alignment, MarginL, MarginR, MarginV, Encoding"
        ),
        (
            "Style:"
            f" Default,{s.font},{fontsize},&H00FFFFFF,&H000000FF,&H00000000,&H00000000,"
            f"0,0,0,0,100,100,0,0,1,{outline},0,2,{margin_h},{margin_h},{margin_v},1"
        ),
        "",
        "[Events]",
        (
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR,"
            " MarginV, Effect, Text"
        ),
    ]
    for sub in subs:
        text = _ass_text(sub.text)
        if text:
            lines.append(
                "Dialogue:"
                f" 0,{_ass_time(sub.start)},{_ass_time(sub.end)},Default,,0,0,0,,{text}"
            )
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
#  Jobs
# --------------------------------------------------------------------------- #


@dataclass
class Job:
    video: str
    subtitle: str = ""
    status: str = "Queued"  # Queued | Working | Done | Failed | Cancelled
    progress: float = 0.0  # 0..1
    duration: float = 0.0  # seconds of video (filled in by probe)
    speed: float = 0.0  # x realtime
    eta: Optional[float] = None
    output: str = ""
    error: str = ""
    id: int = field(default_factory=lambda: next(_ids))


def _id_counter():
    n = 0
    while True:
        n += 1
        yield n


_ids = _id_counter()


def unique_output_path(video_path: str) -> str:
    base, _ = os.path.splitext(video_path)
    candidate, n = f"{base}_subtitles.mp4", 2
    while os.path.exists(candidate):
        candidate = f"{base}_subtitles_{n}.mp4"
        n += 1
    return candidate


_PROGRESS_KEYS = (
    "frame=",
    "fps=",
    "stream_",
    "bitrate=",
    "total_size=",
    "out_time",
    "dup_frames=",
    "drop_frames=",
    "speed=",
    "progress=",
)


def kill_tree(proc: subprocess.Popen) -> None:
    """Kill FFmpeg *and anything it spawned*.

    `ffmpeg` on PATH is sometimes only a launcher/shim (Scoop, Chocolatey, wrappers) that
    starts the real encoder as a child process. Killing just the launcher leaves the real
    encoder running - and holding our output pipe open - until the whole file is done.
    """
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                creationflags=_NO_WINDOW,
                timeout=10,
            )
        else:
            os.killpg(
                proc.pid, signal.SIGKILL
            )  # started with its own session, see Popen below
    except OSError, subprocess.SubprocessError:
        pass
    try:
        proc.kill()
    except OSError:
        pass


class _Cancelled(Exception):
    pass


def _remove_quietly(path: str) -> None:
    """Delete a file; on Windows the handle can linger a moment after the process dies."""
    import time

    for _ in range(10):
        try:
            os.remove(path)
            return
        except FileNotFoundError:
            return
        except OSError:
            time.sleep(0.2)


def _burn_one(
    job: Job,
    s: Settings,
    emit: Callable[[Job], None],
    procs: dict,
    stop: threading.Event,
) -> None:
    if stop.is_set():
        job.status = "Cancelled"
        emit(job)
        return

    part = ""
    try:
        if not has_libass():
            raise RuntimeError(
                "This FFmpeg build has no libass (subtitle rendering). "
                "Install a full FFmpeg build."
            )
        job.status = "Working"
        emit(job)

        info = probe(job.video)
        job.duration = info.duration
        subs = read_srt(job.subtitle)
        if stop.is_set():
            raise _Cancelled
        encoder = resolve_encoder(
            s.encoder
        )  # may run a few test encodes on first use
        if stop.is_set():
            raise _Cancelled
        output = unique_output_path(job.video)
        part = output + ".part"

        audio = (
            ["-c:a", "copy"]
            if info.audio_codec in ("aac", "mp3", "ac3", "eac3")
            else ["-c:a", "aac", "-b:a", "192k"]
        )

        with tempfile.TemporaryDirectory() as tmp:
            # Written next to FFmpeg's working dir so the filter argument is just
            # "subs.ass" - no drive-letter / backslash escaping problems on Windows.
            with open(
                os.path.join(tmp, "subs.ass"), "w", encoding="utf-8"
            ) as f:
                f.write(build_ass(subs, info.width, info.height, s))

            cmd = [
                ffmpeg_path(),
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-y",
                "-i",
                os.path.abspath(job.video),
                "-map",
                "0:v:0",
                "-map",
                "0:a?",
                "-vf",
                "ass=subs.ass",
                *encoder_args(encoder, s),
                "-pix_fmt",
                "yuv420p",
                *audio,
                "-movflags",
                "+faststart",
                "-f",
                "mp4",
                "-progress",
                "pipe:1",
                "-nostats",
                os.path.abspath(part),
            ]
            proc = subprocess.Popen(
                cmd,
                cwd=tmp,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=_NO_WINDOW,
                start_new_session=(sys.platform != "win32"),
            )
            procs[job.id] = proc
            if stop.is_set():  # cancel arrived while we were launching FFmpeg
                kill_tree(proc)

            # Read FFmpeg's output on a helper thread. This loop polls the stop flag, so
            # cancelling never depends on the pipe reaching EOF.
            out_lines: queue.Queue = queue.Queue()

            def _reader(stream=proc.stdout, q=out_lines):
                try:
                    for raw in stream:
                        q.put(raw)
                except OSError, ValueError:
                    pass
                q.put(None)

            threading.Thread(target=_reader, daemon=True).start()

            tail: list[str] = []
            while True:
                try:
                    line = out_lines.get(timeout=0.2)
                except queue.Empty:
                    if stop.is_set():
                        kill_tree(proc)
                        break
                    continue
                if line is None:
                    break
                line = line.strip()
                if (
                    line.startswith(("out_time_us=", "out_time_ms="))
                    and info.duration
                ):
                    try:
                        done = (
                            int(line.split("=")[1]) / 1_000_000
                        )  # both keys are microseconds
                    except ValueError:
                        continue
                    job.progress = max(0.0, min(1.0, done / info.duration))
                    emit(job)
                elif line.startswith("speed="):
                    try:
                        job.speed = float(line.split("=")[1].rstrip("x"))
                        remaining = info.duration * (1 - job.progress)
                        job.eta = (
                            remaining / job.speed if job.speed > 0 else None
                        )
                    except ValueError:
                        pass
                elif line and not line.startswith(_PROGRESS_KEYS):
                    tail = (tail + [line])[-15:]  # keep FFmpeg's error text

            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                kill_tree(proc)
            procs.pop(job.id, None)

        if stop.is_set():
            job.status = "Cancelled"
        elif proc.returncode != 0:
            raise RuntimeError(
                "\n".join(tail) or f"FFmpeg exited with code {proc.returncode}"
            )
        else:
            os.replace(part, output)
            part = ""
            job.status, job.progress, job.output, job.eta = (
                "Done",
                1.0,
                output,
                0.0,
            )
    except _Cancelled:
        job.status = "Cancelled"
    except Exception as exc:
        job.status, job.error = "Failed", str(exc)
    finally:
        procs.pop(job.id, None)
        if part:
            _remove_quietly(part)
        emit(job)


class BatchRunner:
    """Runs jobs on a thread pool. `emit(job)` is called from worker threads, so the
    GUI must hand it over to the Tk thread (see frontend.py)."""

    def __init__(
        self,
        jobs: list[Job],
        settings: Settings,
        emit: Callable[[Job], None],
        on_finished: Callable[[], None],
    ):
        self.jobs, self.settings, self.emit, self.on_finished = (
            jobs,
            settings,
            emit,
            on_finished,
        )
        self._stop = threading.Event()
        self._procs: dict[int, subprocess.Popen] = {}
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def cancel(self) -> None:
        self._stop.set()
        for p in list(self._procs.values()):
            kill_tree(p)

    def _run(self) -> None:
        with ThreadPoolExecutor(
            max_workers=max(1, self.settings.workers)
        ) as pool:
            for job in self.jobs:
                pool.submit(
                    _burn_one,
                    job,
                    self.settings,
                    self.emit,
                    self._procs,
                    self._stop,
                )
        self.on_finished()


# --------------------------------------------------------------------------- #
#  Single-frame preview (so you can tune the style without encoding anything)
# --------------------------------------------------------------------------- #


def render_preview(
    video: str, subtitle: str, s: Settings, out_png: str
) -> None:
    info = probe(video)
    subs = read_srt(subtitle)
    if not len(subs):
        raise RuntimeError("Subtitle file is empty.")
    mid = subs[len(subs) // 2]
    t = (
        mid.start.ordinal + mid.end.ordinal
    ) / 2000  # seconds, middle of a real subtitle

    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "subs.ass"), "w", encoding="utf-8") as f:
            f.write(build_ass(subs, info.width, info.height, s))
        r = subprocess.run(
            [
                ffmpeg_path(),
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-y",
                "-ss",
                f"{t:.3f}",
                "-copyts",
                "-i",
                os.path.abspath(video),
                "-vf",
                "ass=subs.ass,scale=960:-2",
                "-frames:v",
                "1",
                os.path.abspath(out_png),
            ],
            cwd=tmp,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            creationflags=_NO_WINDOW,
        )
        if r.returncode != 0 or not os.path.exists(out_png):
            raise RuntimeError(r.stderr.strip() or "Preview failed.")
