# Subtitle Burner

A small Tkinter app that burns (hardcodes) `.srt` subtitles into video files. It's built for batches: drop in a whole season, tune the look once, and let it run.

Rendering is done by **FFmpeg + libass**, with GPU encoding used automatically when your machine supports it. Python only builds the subtitle file, launches FFmpeg and reads its progress.

## Features

- **Batch queue** with per-file status, progress, speed and ETA, plus an overall progress bar
- **Fast**: FFmpeg does the decoding, subtitle rendering and encoding. Nothing is pulled frame by frame into Python
- **GPU encoding**: NVIDIA NVENC, Intel Quick Sync, AMD AMF and Apple VideoToolbox are detected and tested at startup. It falls back to CPU (x264) if none work
- **Auto-pairing**: `episode01.mp4` picks up `episode01.srt` (or `episode01.en.srt`) automatically
- **Style preview**: render a real frame with your current font, size and margins before encoding anything
- **Styling controls**: font, size, outline thickness, bottom margin and maximum text width. Settings are remembered between runs
- **Subtitle formatting**: `<i>`, `<b>` and `<u>` tags are honored, as is `{\an8}`-style positioning. Line breaks are preserved
- **Encoding safety**: output is written to a temporary `.part` file and only renamed on success. Cancelling never leaves a broken video, and existing files are never overwritten (`_subtitles_2.mp4`, and so on)
- **Robust input**: reads UTF-8 and legacy Windows-1252/Latin-1 `.srt` files. A bad file fails on its own row and the rest of the batch keeps going
- Optional **drag & drop** of files onto the window (needs `tkinterdnd2`)

## Requirements

- Python 3.14+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip
- **FFmpeg with libass** on your `PATH`
  - Windows: `choco install ffmpeg`
  - macOS: `brew install ffmpeg`
  - Debian/Ubuntu: `sudo apt install ffmpeg`

If no `ffmpeg` is found on your `PATH`, the app falls back to the binary bundled with `imageio-ffmpeg`. A system FFmpeg is still the better choice, because it's newer and usually has more hardware encoders.

Tkinter ships with most Python installs. On some Linux distros you need `sudo apt install python3-tk`.

## Installation

```bash
git clone https://github.com/j0lms/hardcode_subtitles.git
cd hardcode_subtitles
uv sync
uv run hardcode-subtitles
```

## Usage

1. Click **Add videos…** (or drop files onto the window). Matching `.srt` files are paired automatically.
2. For any row marked "none", double-click it (or use **Set subtitles…**) to choose a subtitle file.
3. Adjust the encoder, quality and subtitle style, and use **Preview style** to check the result.
4. Click **Start**. Output files are saved next to the originals as `<name>_subtitles.mp4`.

Supported input videos: `.mp4`, `.mkv`, `.mov`, `.avi`, `.webm` and `.m4v`. Output is always H.264 in an MP4 container. Audio is copied untouched when it's already AAC, MP3 or AC-3, and re-encoded to AAC otherwise.

### Example `.srt`

```
1
00:00:48,120 --> 00:00:49,240
John Doe found.
```

### Settings at a glance

| Setting | What it does |
|---|---|
| Encoder | *Auto* uses the best working encoder. You can force CPU or a specific GPU |
| Quality | Lower is better quality and a bigger file (CRF/CQ scale, default 22) |
| CPU speed | x264 preset. Faster presets encode quicker but make bigger files |
| Videos at once | Parallel encodes. Useful with GPU encoders. On CPU, leave it at 1, since x264 already uses every core |
| Size / Outline / Margins | All expressed as a percentage of the video's size, so they scale with resolution |

## How it works

```
.srt ──► pysrt ──► .ass (styled, sized in real pixels)
                     │
video ──► FFmpeg: decode ─► libass renders subtitles ─► encoder (x264 / NVENC / QSV / AMF / VideoToolbox) ─► .mp4
                     │
             progress via `-progress pipe:1` ──► GUI
```

Converting to `.ass` first keeps font sizes in real pixels (plain SRT styling in libass is scaled against a 288px-high canvas) and avoids any path-escaping problems in FFmpeg filter strings.

## Project layout

```
src/hardcode_subtitles/
├── __init__.py    # exposes main() for the console script
├── main.py        # creates the window
├── frontend.py    # Tkinter UI (dark theme, queue table, preview)
└── backend.py     # FFmpeg discovery, encoder detection, SRT→ASS, batch runner
```

All UI updates from worker threads go through a queue and are applied on the Tk thread, because Tkinter widgets aren't thread-safe.

## Troubleshooting

**"FFmpeg problem" or "no libass" in the status bar.** Install a full FFmpeg build (see Requirements). Minimal builds without libass can't render subtitles.

**My GPU isn't in the encoder list.** Each encoder is tested with a real one-frame encode at startup. If it isn't listed, your FFmpeg build lacks it or the driver isn't available. Try updating your GPU drivers or using a newer FFmpeg build.

**The font looks different from what I chose.** Subtitle fonts are resolved by the system through libass. If the font name isn't installed, a substitute is used. Use **Preview style** to confirm before a long batch.

**Something failed.** Failed rows show the last line of FFmpeg's error, and a dialog shows more detail.
