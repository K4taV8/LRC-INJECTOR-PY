import os
import threading
import requests
from mutagen.flac import FLAC
from rapidfuzz import fuzz
from concurrent.futures import ThreadPoolExecutor, as_completed
import tkinter as tk
from tkinter import filedialog, scrolledtext, ttk

LRCLIB_API = "https://lrclib.net/api/get"

def log(msg):
    text_box.after(0, lambda: (
        text_box.insert(tk.END, msg + "\n"),
        text_box.see(tk.END)
    ))

def update_progress(val, maxv):
    progress.after(0, lambda: progress.config(value=val, maximum=maxv))

def clean(t):
    if not t:
        return ""
    t = t.lower()
    for x in ["feat.", "ft.", "(remastered)", "(official)", "(audio)"]:
        t = t.replace(x, "")
    return t.strip()

def match(a, b):
    return fuzz.ratio(clean(a), clean(b)) > 85

def fetch_lrc(artist, title, album=""):
    try:
        r = requests.get(LRCLIB_API, params={
            "artist_name": artist,
            "track_name": title,
            "album_name": album
        }, timeout=10)
        if r.status_code != 200:
            return None, None
        data = r.json()
        lrc = data.get("syncedLyrics")
        return lrc, data
    except:
        return None, None

def process_file(path, dry_run=False):
    try:
        audio = FLAC(path)
        artist = audio.get("artist", [""])[0]
        title = audio.get("title", [""])[0]
        album = audio.get("album", [""])[0]

        if not artist or not title:
            return ("skip", path, None)

        if audio.get("LYRICS") or audio.get("UNSYNCED LYRICS") or audio.get("UNSYNCEDLYRICS"):
            return ("skip", title, "already has lyrics")

        lrc, raw = fetch_lrc(artist, title, album)
        if not lrc:
            return ("miss", title, None)
        if not match(artist, raw.get("artistName", "")) or not match(title, raw.get("trackName", "")):
            return ("reject", title, None)
        if dry_run:
            return ("preview", title, lrc[:200])

        audio["UNSYNCED LYRICS"] = lrc
        audio.save()
        return ("ok", title, None)
    except Exception as e:
        return ("error", path, str(e))

def run(folder, workers, dry_run):
    files = [
        os.path.join(r, f)
        for r, _, fs in os.walk(folder)
        for f in fs if f.lower().endswith(".flac")
    ]
    stats = {"ok":0,"miss":0,"reject":0,"skip":0,"error":0}
    total = len(files)
    log(f"Files: {total} | Threads: {workers} | Dry-run: {dry_run}")

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(process_file, f, dry_run): f for f in files}
        for i, fu in enumerate(as_completed(futures), 1):
            status, name, extra = fu.result()
            stats[status] += 1
            if status == "preview":
                log(f"[PREVIEW] {name}\n{extra}\n---")
            elif status == "ok":
                log(f"[OK] {name}")
            elif status == "miss":
                log(f"[MISS] {name}")
            elif status == "reject":
                log(f"[REJECT] {name}")
            elif status == "skip":
                log(f"[SKIP] {name}" + (f" -> {extra}" if extra else ""))
            else:
                log(f"[ERROR] {name} -> {extra}")
            update_progress(i, total)

    log("\n=== STATS ===")
    for k, v in stats.items():
        log(f"{k.upper()}: {v}")

def start():
    folder = folder_var.get()
    workers = int(thread_var.get())
    dry = dry_var.get()
    if not os.path.isdir(folder):
        log("Invalid folder")
        return
    threading.Thread(target=run, args=(folder, workers, dry)).start()

def pick():
    folder_var.set(filedialog.askdirectory())

root = tk.Tk()
root.title("LRC Injector")
root.geometry("800x580")

folder_var = tk.StringVar()
thread_var = tk.StringVar(value="8")
dry_var = tk.BooleanVar(value=False)

main = ttk.Frame(root, padding=12)
main.pack(fill=tk.BOTH, expand=True)

ttk.Label(main, text="FLAC Folder:").grid(row=0, column=0, sticky="w", pady=2)
ttk.Entry(main, textvariable=folder_var).grid(row=0, column=1, sticky="ew", padx=4, pady=2)
ttk.Button(main, text="Browse", command=pick).grid(row=0, column=2, pady=2)
main.columnconfigure(1, weight=1)

ttk.Label(main, text="Threads:").grid(row=1, column=0, sticky="w", pady=2)
ttk.Entry(main, textvariable=thread_var, width=8).grid(row=1, column=1, sticky="w", padx=4, pady=2)

ttk.Checkbutton(main, text="Dry Run (preview only)", variable=dry_var).grid(row=2, column=0, columnspan=3, sticky="w", pady=4)

ttk.Button(main, text="START", command=start).grid(row=3, column=0, columnspan=3, pady=6)

progress = ttk.Progressbar(main)
progress.grid(row=4, column=0, columnspan=3, pady=4, sticky="ew")

text_box = scrolledtext.ScrolledText(main, height=22)
text_box.grid(row=5, column=0, columnspan=3, sticky="nsew")
main.rowconfigure(5, weight=1)

root.mainloop()
