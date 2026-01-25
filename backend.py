import os
import pysrt
import numpy as np
from moviepy.editor import VideoFileClip, CompositeVideoClip, ImageClip
from PIL import Image, ImageDraw, ImageFont

# ---------------- Batch Processing State ---------------- #

job_queue = []
encoding_speed_factor = 0.5


# ---------------- Windows Font Auto-Detection ---------------- #

def find_windows_font():
    possible_fonts = [
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/verdana.ttf",
        "C:/Windows/Fonts/tahoma.ttf",
        "C:/Windows/Fonts/calibri.ttf",
    ]
    for f in possible_fonts:
        if os.path.exists(f):
            return f
    raise FileNotFoundError("No valid Windows font found.")

FONT_PATH = find_windows_font()


# ---------------- Subtitle Sanitizer ---------------- #

def clean_subtitle_text(text):
    bad_chars = ['\\', '/', '<', '>', '{', '}', '|']
    for c in bad_chars:
        text = text.replace(c, '')

    text = ''.join(ch for ch in text if ch.isprintable())
    text = text.replace('\r', '').replace('\n', ' ').strip()

    return text


# ---------------- Utility Functions ---------------- #

def time_to_seconds(t):
    return (
        t.hours * 3600 +
        t.minutes * 60 +
        t.seconds +
        t.milliseconds / 1000
    )


def render_subtitle_image(text, video_width, video_height):
    text = clean_subtitle_text(text)

    fontsize = int(video_height * 0.045)
    outline = int(video_height * 0.003)

    font = ImageFont.truetype(FONT_PATH, fontsize)

    img = Image.new("RGBA", (video_width, video_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    max_width = int(video_width * 0.70)
    lines = []
    words = text.split(" ")
    current = ""

    for w in words:
        test = current + " " + w if current else w
        if draw.textlength(test, font=font) <= max_width:
            current = test
        else:
            lines.append(current)
            current = w
    if current:
        lines.append(current)

    y = int(video_height * 0.82)

    for line in lines:
        w = draw.textlength(line, font=font)
        x = (video_width - w) // 2

        for ox in range(-outline, outline + 1):
            for oy in range(-outline, outline + 1):
                draw.text((x + ox, y + oy), line, font=font, fill="black")

        draw.text((x, y), line, font=font, fill="white")

        y += fontsize + 10

    return img


def create_subtitle_clips(subtitles, videosize):
    video_width, video_height = videosize
    clips = []

    for sub in subtitles:
        start = time_to_seconds(sub.start)
        end = time_to_seconds(sub.end)
        duration = end - start

        pil_img = render_subtitle_image(sub.text, video_width, video_height)

        # 🔥 FIX: Convert PIL image → NumPy array → MoviePy ImageClip
        np_img = np.array(pil_img)

        clip = (
            ImageClip(np_img)
            .set_start(start)
            .set_duration(duration)
        )

        clips.append(clip)

    return clips


# ---------------- Queue Helpers ---------------- #

def estimate_total_time():
    total = 0
    for video_path, _ in job_queue:
        try:
            clip = VideoFileClip(video_path)
            total += clip.duration * encoding_speed_factor
            clip.close()
        except:
            pass
    return total


# ---------------- Video Processing ---------------- #

def process_video(video_path, subtitle_path, callback):
    try:
        subs = pysrt.open(subtitle_path)
        video = VideoFileClip(video_path)

        subtitle_clips = create_subtitle_clips(subs, video.size)
        final = CompositeVideoClip([video] + subtitle_clips)

        base, ext = os.path.splitext(video_path)
        output_path = f"{base}_subtitles.mp4"

        final.write_videofile(output_path, codec="libx264", audio_codec="aac")

        callback(True, output_path)

    except Exception as e:
        callback(False, str(e))
