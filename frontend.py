import os
import threading
import webbrowser
import tkinter as tk
from tkinter import filedialog, messagebox

import backend


# ---------------- UI Logic ---------------- #

output_file_path = ""  # last successful output path


def refresh_queue_list():
    queue_list.delete(0, tk.END)
    for v, s in backend.job_queue:
        queue_list.insert(tk.END, f"{os.path.basename(v)}  |  {os.path.basename(s)}")


def remove_selected():
    selection = queue_list.curselection()
    if not selection:
        return
    index = selection[0]
    backend.job_queue.pop(index)
    refresh_queue_list()


def process_next_job():
    if not backend.job_queue:
        progress_label.config(text="All videos processed.")
        process_button.config(state="normal")
        return

    video_path, subtitle_path = backend.job_queue.pop(0)
    refresh_queue_list()

    base, ext = os.path.splitext(video_path)
    expected_output = f"{base}_subtitles.mp4"
    output_note.config(text=f"Output will be saved as:\n{expected_output}")

    progress_label.config(text=f"Processing: {os.path.basename(video_path)}")

    thread = threading.Thread(
        target=backend.process_video,
        args=(video_path, subtitle_path, on_processing_done)
    )
    thread.start()


def select_video():
    path = filedialog.askopenfilename(
        title="Select Video File",
        filetypes=[("Video Files", "*.mp4 *.mov *.avi *.mkv")]
    )
    video_entry.delete(0, tk.END)
    video_entry.insert(0, path)


def select_subtitle():
    path = filedialog.askopenfilename(
        title="Select Subtitle File",
        filetypes=[("Subtitle Files", "*.srt")]
    )
    subtitle_entry.delete(0, tk.END)
    subtitle_entry.insert(0, path)


def add_to_queue():
    video_path = video_entry.get()
    subtitle_path = subtitle_entry.get()

    if not video_path or not subtitle_path:
        messagebox.showerror("Error", "Please select both video and subtitle files.")
        return

    backend.job_queue.append((video_path, subtitle_path))
    refresh_queue_list()


def start_batch():
    if not backend.job_queue:
        messagebox.showerror("Error", "Queue is empty.")
        return

    total_est = backend.estimate_total_time()
    progress_label.config(
        text=f"Estimated total time: {int(total_est//60)}m {int(total_est%60)}s"
    )

    process_button.config(state="disabled")
    process_next_job()


def open_output_file():
    if not output_file_path:
        messagebox.showinfo("Info", "No output file available yet.")
        return
    webbrowser.open(output_file_path)


def on_processing_done(success, output):
    global output_file_path

    if success:
        output_file_path = output
        progress_label.config(text=f"Done: {os.path.basename(output)}")
        output_note.config(text=f"Saved to:\n{output}")
        download_button.pack(pady=10)
    else:
        progress_label.config(text="")
        messagebox.showerror("Error", output)

    process_next_job()


# ---------------- Tkinter UI ---------------- #

def build_ui(root):
    global video_entry, subtitle_entry, queue_list
    global process_button, progress_label, output_note, download_button

    root.title("Subtitle Overlay Tool")
    root.geometry("650x500")

    # Input fields
    frame_inputs = tk.Frame(root)
    frame_inputs.pack(pady=10)

    tk.Label(frame_inputs, text="Video File:").grid(row=0, column=0, sticky="w")
    video_entry = tk.Entry(frame_inputs, width=50)
    video_entry.grid(row=0, column=1)
    tk.Button(frame_inputs, text="Browse", command=select_video).grid(row=0, column=2)

    tk.Label(frame_inputs, text="Subtitle File (.srt):").grid(row=1, column=0, sticky="w")
    subtitle_entry = tk.Entry(frame_inputs, width=50)
    subtitle_entry.grid(row=1, column=1)
    tk.Button(frame_inputs, text="Browse", command=select_subtitle).grid(row=1, column=2)

    tk.Button(root, text="Add to Queue", command=add_to_queue).pack(pady=5)

    # Queue list
    tk.Label(root, text="Queue:").pack()
    queue_list = tk.Listbox(root, width=80, height=8)
    queue_list.pack()

    tk.Button(root, text="Remove Selected", command=remove_selected).pack(pady=5)

    # Processing controls
    process_button = tk.Button(root, text="Start Batch Processing", command=start_batch)
    process_button.pack(pady=10)

    progress_label = tk.Label(root, text="", fg="blue")
    progress_label.pack()

    output_note = tk.Label(
        root,
        text="Output files will be saved next to the original video,\nwith '_subtitles' added to the filename.",
        fg="gray",
        justify="center"
    )
    output_note.pack(pady=5)

    download_button = tk.Button(root, text="Open Output File", command=open_output_file)
    # initially not packed
