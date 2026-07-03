import os
import re
import threading
import requests
from mutagen.flac import FLAC
from rapidfuzz import fuzz
from concurrent.futures import ThreadPoolExecutor, as_completed
import tkinter as tk
from tkinter import filedialog, scrolledtext, ttk

LRCLIB_API = "https://lrclib.net/api/get"
SESSION = requests.Session()
adapter = requests.adapters.HTTPAdapter(pool_connections=20, pool_maxsize=20)
SESSION.mount("https://", adapter)
TS_RE = re.compile(r"\[\d+:\d+\.\d+\]")
CPU_COUNT = os.cpu_count() or 4

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

def strip_timestamps(lrc):
    return "\n".join(TS_RE.sub("", line).strip() for line in lrc.splitlines()).strip()

def match(a, b):
    return fuzz.ratio(clean(a), clean(b)) > 85

def fetch_lrc(artist, title, album=""):
    try:
        r = SESSION.get(LRCLIB_API, params={
            "artist_name": artist,
            "track_name": title,
            "album_name": album
        }, timeout=10)
        if r.status_code != 200:
            return None, None, False
        data = r.json()
        lrc = data.get("syncedLyrics")
        pl = data.get("plainLyrics")
        instrumental = data.get("instrumental", False) or (
            not lrc and not pl
        ) or (
            pl and pl.strip().lower() == "instrumental"
        )
        return lrc, data, instrumental
    except:
        return None, None, False

def process_file(path, dry_run=False):
    try:
        audio = FLAC(path)
        artist = audio.get("artist", [""])[0]
        title = audio.get("title", [""])[0]
        album = audio.get("album", [""])[0]

        if not artist or not title:
            return ("skip", path, None)

        if audio.get("LYRICS") or audio.get("UNSYNCEDLYRICS"):
            return ("skip", title, "already has lyrics")

        lrc, raw, inst = fetch_lrc(artist, title, album)
        if inst or re.search(r"instrumental", title, re.I):
            return ("inst", title, None)
        if not lrc:
            return ("miss", title, None)
        if not match(artist, raw.get("artistName", "")) or not match(title, raw.get("trackName", "")):
            return ("reject", title, None)
        if dry_run:
            return ("preview", title, lrc[:200])

        audio["LYRICS"] = lrc
        audio["UNSYNCEDLYRICS"] = strip_timestamps(lrc)
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
    stats = {"ok":0,"miss":0,"reject":0,"skip":0,"error":0,"inst":0}
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
            elif status == "inst":
                log(f"[INST] {name}")
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
    log(f"\nInjection terminée sur {stats['ok']} fichier(s)")

def start():
    folder = folder_var.get()
    workers = int(thread_var.get())
    dry = dry_var.get()
    if not os.path.isdir(folder):
        log("Invalid folder")
        return
    threading.Thread(target=run, args=(folder, workers, dry)).start()

def check_files(folder):
    files = [
        os.path.join(r, f)
        for r, _, fs in os.walk(folder)
        for f in fs if f.lower().endswith(".flac")
    ]
    total = len(files)
    lines = []
    ok = fixed = 0
    for i, path in enumerate(files, 1):
        try:
            audio = FLAC(path)
            name = audio.get("title", [""])[0] or os.path.basename(path)
            has_l = "LYRICS" in audio
            has_u = "UNSYNCEDLYRICS" in audio
            if has_l and has_u:
                ok += 1
                lines.append(("ok", f"[OK] {name}"))
            elif has_l and not has_u:
                audio["UNSYNCEDLYRICS"] = strip_timestamps(audio["LYRICS"])
                audio.save()
                fixed += 1
                lines.append(("repair", f"[RÉPARÉ] {name} -> UNSYNCEDLYRICS ajouté"))
            elif has_u and not has_l:
                artist = audio.get("artist", [""])[0]
                title = audio.get("title", [""])[0]
                if not artist or not title:
                    lines.append(("partial", f"[PARTIEL] {name} -> pas d'artiste/titre"))
                else:
                    lrc, _, inst = fetch_lrc(artist, title)
                    if inst or re.search(r"instrumental", title, re.I):
                        lines.append(("inst", f"[INST] {name} -> instrumental"))
                    elif lrc:
                        audio["LYRICS"] = lrc
                        audio.save()
                        fixed += 1
                        lines.append(("repair", f"[RÉPARÉ] {name} -> LYRICS ajouté"))
                    else:
                        lines.append(("partial", f"[PARTIEL] {name} -> introuvable sur l'API"))
            else:
                lines.append(("missing", f"[MANQUE] {name} -> aucun tag"))
        except Exception as e:
            lines.append(("error", f"[ERREUR] {os.path.basename(path)} -> {e}"))
        update_progress(i, total)

    order = {"ok": 0, "repair": 1, "error": 2, "inst": 3, "partial": 4, "missing": 5}
    lines.sort(key=lambda x: order.get(x[0], 9))
    log(f"Vérification de {total} fichier(s) :\n")
    for _, msg in lines:
        log(msg)
    log(f"\n{ok}/{total} complets, {fixed} réparé(s)")

def clear_log():
    text_box.delete("1.0", tk.END)

def start_check():
    folder = folder_var.get()
    if not os.path.isdir(folder):
        log("Invalid folder")
        return
    threading.Thread(target=check_files, args=(folder,)).start()

def pick():
    folder_var.set(filedialog.askdirectory())

root = tk.Tk()
root.title("LRC Injector")
root.geometry("860x620")
root.minsize(680, 500)

try:
    import tkinter.font as tkfont
    default_font = tkfont.nametofont("TkDefaultFont")
    default_font.configure(family="Segoe UI", size=9)
except:
    pass

style = ttk.Style()
try:
    style.theme_use("clam")
except:
    pass

BG = "#f5f5f5"
CARD = "#ffffff"
ACCENT = "#2e7d32"
ACCENT_HOVER = "#388e3c"
ACCENT_PRESS = "#1b5e20"
BORDER = "#d0d0d0"
HEADER_BG = "#1a1a2e"
HEADER_FG = "#ffffff"
LOG_BG = "#1e1e1e"
LOG_FG = "#d4d4d4"

root.configure(bg=BG)

style.configure(".", font=("Segoe UI", 9))
style.configure("TLabel", padding=2, background=BG)
style.configure("TFrame", background=BG)
style.configure("TLabelframe", background=BG, bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER)
style.configure("TLabelframe.Label", background=BG, font=("Segoe UI", 9, "bold"))

card_style = {"background": CARD, "bordercolor": BORDER, "lightcolor": BORDER, "darkcolor": BORDER, "borderwidth": 1, "relief": tk.SOLID}

style.configure("Card.TLabelframe", **card_style)
style.configure("Card.TLabelframe.Label", background=CARD, font=("Segoe UI", 9, "bold"))
style.configure("TButton", padding=(12, 4), font=("Segoe UI", 9))
style.configure("Small.TButton", padding=(6, 2), font=("Segoe UI", 9))
style.configure("Start.TButton", font=("Segoe UI", 10, "bold"),
                background=ACCENT, foreground="white", padding=(24, 6))
style.map("Start.TButton", background=[("active", ACCENT_HOVER), ("pressed", ACCENT_PRESS)])
style.configure("Check.TButton", font=("Segoe UI", 10),
                background="#1565c0", foreground="white", padding=(20, 6))
style.map("Check.TButton", background=[("active", "#1976d2"), ("pressed", "#0d47a1")])
style.configure("Clear.TButton", padding=(8, 2), font=("Segoe UI", 8))

style.configure("TEntry", fieldbackground=CARD, font=("Segoe UI", 9))
style.configure("TCheckbutton", background=CARD, font=("Segoe UI", 9))
style.configure("Header.TLabel", font=("Segoe UI", 13, "bold"), foreground=HEADER_FG, background=HEADER_BG, padding=(16, 8))

# status bar
style.configure("Status.TLabel", font=("Segoe UI", 8), background=BG, foreground="#666666")

root.option_add("*TCombobox*Listbox.font", ("Segoe UI", 9))

folder_var = tk.StringVar()
thread_var = tk.StringVar(value="8")
dry_var = tk.BooleanVar(value=False)

# header bar
header = tk.Frame(root, bg=HEADER_BG, height=40)
header.pack(fill=tk.X)
tk.Label(header, text="LRC Injector", font=("Segoe UI", 13, "bold"),
         fg=HEADER_FG, bg=HEADER_BG).pack(side=tk.LEFT, padx=16, pady=8)

main = ttk.Frame(root, padding=(16, 10))
main.pack(fill=tk.BOTH, expand=True)

# options card
opts = ttk.LabelFrame(main, text="Options", style="Card.TLabelframe", padding=12)
opts.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 10))
opts.columnconfigure(1, weight=1)

ttk.Label(opts, text="FLAC Folder:").grid(row=0, column=0, sticky="w", pady=3)
ttk.Entry(opts, textvariable=folder_var).grid(row=0, column=1, sticky="ew", padx=8, pady=3)
ttk.Button(opts, text="Browse", command=pick).grid(row=0, column=2, pady=3)

ttk.Label(opts, text="Threads:").grid(row=1, column=0, sticky="w", pady=3)
ttk.Entry(opts, textvariable=thread_var, width=8).grid(row=1, column=1, sticky="w", padx=8, pady=3)

dry_frame = tk.Frame(opts, bg=CARD)
dry_frame.grid(row=2, column=0, columnspan=3, sticky="w", pady=6)
tk.Checkbutton(dry_frame, text="Dry Run (preview only)", variable=dry_var,
               font=("Segoe UI", 9), bg=CARD, activebackground=CARD,
               selectcolor=CARD).pack()

# buttons
btn_frame = ttk.Frame(main)
btn_frame.grid(row=1, column=0, columnspan=3, pady=(0, 8))
ttk.Button(btn_frame, text="▶  START", style="Start.TButton", command=start).pack(side=tk.LEFT, padx=5)
ttk.Button(btn_frame, text="CHECK", style="Check.TButton", command=start_check).pack(side=tk.LEFT, padx=5)

progress = ttk.Progressbar(main)
progress.grid(row=2, column=0, columnspan=3, pady=4, sticky="ew")

# log card
log_frame = ttk.LabelFrame(main, text="Log", style="Card.TLabelframe", padding=6)
log_frame.grid(row=3, column=0, columnspan=3, sticky="nsew")
main.rowconfigure(3, weight=1)
log_frame.columnconfigure(0, weight=1)
log_frame.rowconfigure(1, weight=1)

log_top = tk.Frame(log_frame, bg=CARD)
log_top.grid(row=0, column=0, columnspan=2, sticky="ew")
tk.Button(log_top, text="Clear", font=("Segoe UI", 8), command=clear_log,
          bg=BG, activebackground=BORDER, relief=tk.FLAT, padx=8, pady=1,
          cursor="hand2").pack(side=tk.RIGHT)

text_box = tk.Text(
    log_frame, height=18, font=("Consolas", 9), wrap=tk.NONE,
    bg=LOG_BG, fg=LOG_FG, insertbackground=LOG_FG,
    relief=tk.FLAT, borderwidth=0, highlightthickness=1,
    highlightcolor=BORDER, highlightbackground=BORDER)
text_box.grid(row=1, column=0, sticky="nsew")

vsb = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=text_box.yview)
vsb.grid(row=1, column=1, sticky="ns")
text_box.config(yscrollcommand=vsb.set)

hsb = ttk.Scrollbar(log_frame, orient=tk.HORIZONTAL, command=text_box.xview)
hsb.grid(row=2, column=0, sticky="ew")
text_box.config(xscrollcommand=hsb.set)

# status bar
status_bar = tk.Frame(root, bg=BG, height=22)
status_bar.pack(fill=tk.X, side=tk.BOTTOM)
tk.Label(status_bar, text="Ready", font=("Segoe UI", 8), fg="#888888",
         bg=BG).pack(side=tk.LEFT, padx=10)

root.mainloop()
