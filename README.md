## Subtitle Overlay Tool

A small Tkinter app that burns .srt subtitles directly into .mp4 video files using MoviePy.
It’s built to make subtitle hardcoding simple — whether you’re doing one episode or an entire season.
Features
- Batch processing – queue multiple videos and process them automatically
- ETA display – shows an estimated processing time based on video length
- Netflix‑style subtitles – clean white text with a thin black outline
- Responsive UI – encoding runs in the background
- Smart output naming – saves files next to the original video with _subtitles added

## What It Does

This tool hardcodes subtitles directly into the video (burned‑in).
Currently supports:

- .mp4 video files
- .srt subtitle files

Example .srt format
<pre>
  1
  00:00:48,120 --> 00:00:49,240
  John Doe found.
</pre>

# Planned Improvements
  - Support for more video formats
  - More subtitle styling options
  - Optional FFmpeg backend for faster encoding

## Requirements
# Software

    - Python 3.10+
    - FFmpeg (MoviePy uses it internally)

# Python Packages
<pre>
  pip install moviepy pysrt imageio
</pre>

(Tkinter is included with most Python installations.)
Installation
bash
  <pre>
    git clone https://github.com/YOUR_USERNAME/hardcode_subtitles.git
    cd hardcode_subtitles
    pip install -r requirements.txt
    python hardcode_subtitle_app.py
</pre>

The UI will open, and you can start adding videos + subtitles to the queue.
