import os
import re
import json
import unicodedata
import threading

from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
import tkinter as tk
from tkinter import filedialog, scrolledtext, ttk
import sv_ttk

LRCLIB_API = "https://lrclib.net/api/get"
SESSION = None

def get_session():
    global SESSION
    if SESSION is None:
        import requests
        from urllib3.util.retry import Retry
        SESSION = requests.Session()
        retry = Retry(total=2, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503],
                      allowed_methods=["GET"])
        adapter = requests.adapters.HTTPAdapter(pool_connections=20, pool_maxsize=20, max_retries=retry)
        SESSION.mount("https://", adapter)
    return SESSION
TS_RE = re.compile(r"\[\d+:\d+\.\d+\]")
CPU_COUNT = os.cpu_count() or 4

CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lrc_cache.json")
_cache = {}
_cache_lock = Lock()
_dirty_count = 0
_FLUSH_EVERY = 200

def _load_cache():
    global _cache
    try:
        with open(CACHE_FILE, "r") as f:
            _cache = json.load(f)
    except:
        _cache = {}

def _save_cache():
    with _cache_lock:
        snapshot = dict(_cache)
    with open(CACHE_FILE, "w") as f:
        json.dump(snapshot, f, indent=2)

def _mark_dirty():
    global _dirty_count
    with _cache_lock:
        _dirty_count += 1
        flush = _dirty_count >= _FLUSH_EVERY
        if flush:
            _dirty_count = 0
    if flush:
        _save_cache()

_load_cache()

_log_buffer = []
_log_lock = Lock()
_log_pending = False
_log_lines = 0

def _tag_for(msg):
    if msg.startswith("[OK]") or "already has lyrics" in msg or "[RÉPARÉ]" in msg:
        return "ok"
    if msg.startswith("[MISS]") or msg.startswith("[REJECT]") or msg.startswith("[PARTIEL]"):
        return "warn"
    if msg.startswith("[ERROR]") or msg.startswith("[ERREUR]"):
        return "error"
    if msg.startswith("[SKIP]") or msg.startswith("[MANQUE]"):
        return "skip"
    if msg.startswith("[INST]"):
        return "inst"
    return None

def log(msg):
    global _log_pending
    with _log_lock:
        _log_buffer.append(msg)
        if not _log_pending:
            _log_pending = True
            root.after(150, _flush_log)

def _flush_log():
    global _log_pending, _log_lines
    with _log_lock:
        msgs = _log_buffer[:]
        _log_buffer.clear()
        _log_pending = bool(_log_buffer)
    if msgs:
        tag = _tag_for(msgs[0])
        start = 0
        for i, m in enumerate(msgs):
            t = _tag_for(m)
            if t != tag:
                text_box.insert(tk.END, "\n".join(msgs[start:i]) + "\n", tag if tag else ())
                tag = t
                start = i
        text_box.insert(tk.END, "\n".join(msgs[start:]) + "\n", tag if tag else ())
        text_box.see(tk.END)
        _log_lines += len(msgs)
        if _log_lines > 3000:
            text_box.delete("1.0", "1000.0")
            _log_lines -= 1000
    if _log_pending:
        root.after(150, _flush_log)

_pulsing = False
_spin_id = None
_cancel = threading.Event()

def start_pulse():
    global _pulsing
    _pulsing = True
    root.after(0, lambda: (
        progress.config(mode="indeterminate"),
        progress.start(40)
    ))

def stop_pulse():
    global _pulsing, _spin_id
    _pulsing = False
    if _spin_id:
        root.after_cancel(_spin_id)
        _spin_id = None
    root.after(0, lambda: (
        progress.stop(),
        progress.config(mode="determinate", value=0, maximum=100),
        status_label.config(text="Ready"),
        prog_label.config(text="")
    ))

_last_prog = 0

def update_progress(val, maxv):
    global _pulsing, _spin_id, _last_prog
    step = max(1, maxv // 200) if maxv else 1
    if val - _last_prog < step and val != maxv:
        return
    _last_prog = val
    if _pulsing:
        if _spin_id:
            root.after_cancel(_spin_id)
            _spin_id = None
        root.after(0, lambda: (
            progress.stop(),
            progress.config(mode="determinate"),
            status_label.config(text="Processing...")
        ))
        _pulsing = False
    def _up():
        progress.config(value=val, maximum=maxv)
        prog_label.config(text=f"{val}/{maxv}")
    root.after(0, _up)

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
    from rapidfuzz import fuzz
    return fuzz.ratio(clean(a), clean(b)) > 85

SEARCH_API = "https://lrclib.net/api/search"

def _parse_result(data):
    lrc = data.get("syncedLyrics")
    pl = data.get("plainLyrics")
    if isinstance(lrc, list):
        lrc = "\n".join(lrc)
    if isinstance(pl, list):
        pl = "\n".join(pl)
    inst = data.get("instrumental", False) or (not lrc and not pl) or (pl and pl.strip().lower() == "instrumental")
    return lrc, inst

def _search_fallback(artist, title, key):
    seen = set()
    ca, ct = clean(artist), clean(title)
    def fold(s):
        return unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode()
    queries = [f"{artist} {title}", title, f"{fold(artist)} {fold(title)}"]
    for q in queries:
        if q in seen:
            continue
        seen.add(q)
        try:
            s = get_session().get(SEARCH_API, params={"q": q}, timeout=8)
            if s.status_code != 200:
                continue
            for entry in s.json():
                if fuzz.ratio(ca, clean(entry.get("artistName", ""))) > 85 and fuzz.ratio(ct, clean(entry.get("trackName", ""))) > 85:
                    lrc, inst = _parse_result(entry)
                    with _cache_lock:
                        _cache[key] = {"lrc": lrc, "inst": inst,
                                       "a": entry.get("artistName"), "t": entry.get("trackName")}
                    _mark_dirty()
                    return lrc, entry, inst
        except:
            continue
    return None, None, False

def fetch_lrc(artist, title, album=""):
    key = f"{artist.strip().lower()}||{title.strip().lower()}"
    deleted = False
    with _cache_lock:
        if key in _cache:
            c = _cache[key]
            if c.get("lrc"):
                return c["lrc"], {"artistName": c["a"], "trackName": c["t"]}, c.get("inst", False)
            if c.get("inst"):
                return None, None, True
            del _cache[key]
            deleted = True
    if deleted:
        _mark_dirty()
    try:
        r = get_session().get(LRCLIB_API, params={
            "artist_name": artist,
            "track_name": title,
            "album_name": album
        }, timeout=(3, 8))
        if r.status_code == 200:
            data = r.json()
            lrc, inst = _parse_result(data)
            if lrc or inst:
                with _cache_lock:
                    _cache[key] = {"lrc": lrc, "inst": inst,
                                   "a": data.get("artistName"), "t": data.get("trackName")}
                _mark_dirty()
                return lrc, data, inst

        return _search_fallback(artist, title, key)
    except:
        lrc, data, inst = _search_fallback(artist, title, key)
        if lrc or inst:
            return lrc, data, inst
        return None, None, False

def process_file(path):
    from mutagen.flac import FLAC
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

        audio["LYRICS"] = lrc
        audio["UNSYNCEDLYRICS"] = strip_timestamps(lrc)
        audio.save()
        return ("ok", title, None)
    except Exception as e:
        return ("error", path, str(e))

def run(folder, workers):
    _cancel.clear()
    start_pulse()
    if os.path.isfile(folder):
        files = [folder]
    else:
        files = [
            os.path.join(r, f)
            for r, _, fs in os.walk(folder)
            for f in fs if f.lower().endswith(".flac")
        ]
    stats = {"ok":0,"miss":0,"reject":0,"skip":0,"error":0,"inst":0}
    total = len(files)
    log(f"Files: {total} | Threads: {workers}")

    cancelled = False
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(process_file, f) for f in files]
        for i, fu in enumerate(as_completed(futures), 1):
            if _cancel.is_set():
                for f in futures:
                    f.cancel()
                cancelled = True
                break
            status, name, extra = fu.result()
            stats[status] += 1
            if status == "ok":
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
    _save_cache()
    stop_pulse()
    _set_busy(False)


def _set_busy(b):
    state = "disabled" if b else "normal"
    start_btn.config(state=state)
    check_btn.config(state=state)
    stop_btn.config(state="normal" if b else "disabled")

def stop_processing():
    _cancel.set()
    log("[STOP] Annulation en cours...")

def start():
    _cancel.clear()
    p = folder_var.get()
    try:
        workers = max(1, min(32, int(thread_var.get())))
    except ValueError:
        workers = CPU_COUNT
    if not p:
        log("Select a folder or file")
        return
    _set_busy(True)
    threading.Thread(target=run, args=(p, workers), daemon=True).start()

def check_one(path):
    from mutagen.flac import FLAC
    try:
        audio = FLAC(path)
        name = audio.get("title", [""])[0] or os.path.basename(path)
        has_l = "LYRICS" in audio
        has_u = "UNSYNCEDLYRICS" in audio
        if has_l and has_u:
            return ("ok", f"[OK] {name}")
        if has_l and not has_u:
            audio["UNSYNCEDLYRICS"] = strip_timestamps(audio["LYRICS"])
            audio.save()
            return ("repair", f"[RÉPARÉ] {name} -> UNSYNCEDLYRICS ajouté")
        if has_u and not has_l:
            artist = audio.get("artist", [""])[0]
            title = audio.get("title", [""])[0]
            if not artist or not title:
                return ("partial", f"[PARTIEL] {name} -> pas d'artiste/titre")
            lrc, _, inst = fetch_lrc(artist, title)
            if inst or re.search(r"instrumental", title, re.I):
                return ("inst", f"[INST] {name} -> instrumental")
            if lrc:
                audio["LYRICS"] = lrc
                audio.save()
                return ("repair", f"[RÉPARÉ] {name} -> LYRICS ajouté")
            return ("partial", f"[PARTIEL] {name} -> introuvable sur l'API")
        return ("missing", f"[MANQUE] {name} -> aucun tag")
    except Exception as e:
        return ("error", f"[ERREUR] {os.path.basename(path)} -> {e}")

def check_files(folder):
    _cancel.clear()
    start_pulse()
    if os.path.isfile(folder):
        files = [folder]
    else:
        files = [
            os.path.join(r, f)
            for r, _, fs in os.walk(folder)
            for f in fs if f.lower().endswith(".flac")
        ]
    total = len(files)
    lines = []
    try:
        workers = max(1, min(16, int(thread_var.get())))
    except ValueError:
        workers = CPU_COUNT
    cancelled = False
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(check_one, f) for f in files]
        for i, fu in enumerate(as_completed(futures), 1):
            if _cancel.is_set():
                for f in futures:
                    f.cancel()
                cancelled = True
                break
            lines.append(fu.result())
            update_progress(i, total)

    order = {"ok": 0, "repair": 1, "error": 2, "inst": 3, "partial": 4, "missing": 5}
    lines.sort(key=lambda x: order.get(x[0], 9))
    ok = sum(1 for t, _ in lines if t == "ok")
    fixed = sum(1 for t, _ in lines if t == "repair")
    _save_cache()
    stop_pulse()
    _set_busy(False)
    if cancelled:
        log("[STOP] Vérification interrompue")
    log(f"Vérification de {total} fichier(s) :\n")
    for _, msg in lines:
        log(msg)
    log(f"\n{ok}/{total} complets, {fixed} réparé(s)")

def clear_log():
    text_box.delete("1.0", tk.END)

def clear_cache():
    global _cache
    with _cache_lock:
        _cache = {}
        try:
            os.remove(CACHE_FILE)
        except:
            pass
    log("[CACHE vidé]")

def on_close():
    _save_cache()
    root.destroy()

def start_check():
    _cancel.clear()
    p = folder_var.get()
    _set_busy(True)
    threading.Thread(target=check_files, args=(p,), daemon=True).start()

def pick():
    folder_var.set(filedialog.askdirectory())

def pick_file():
    p = filedialog.askopenfilename(filetypes=[("FLAC files", "*.flac")])
    if p:
        folder_var.set(p)

# ── DPI Awareness (anti-flou W11) ──
try:
    from ctypes import windll
    windll.shcore.SetProcessDpiAwareness(1)
except:
    pass

# ── Barre de titre Windows sombre ──
def _force_dark_titlebar(win):
    try:
        from ctypes import windll, c_int, byref, sizeof
        HWND = windll.user32.GetParent(win.winfo_id())
        DWMWA_USE_IMMERSIVE_DARK_MODE = 20
        value = c_int(1)
        windll.dwmapi.DwmSetWindowAttribute(
            HWND, DWMWA_USE_IMMERSIVE_DARK_MODE, byref(value), sizeof(value)
        )
    except:
        pass

# ── Fenêtre ──
root = tk.Tk()
root.withdraw()
_force_dark_titlebar(root)
root.title("LRC Injector")
W, H = 1218, 948
sw = root.winfo_screenwidth()
sh = root.winfo_screenheight()
x = (sw - W) // 2
y = (sh - H) // 2
root.geometry(f"{W}x{H}+{x}+{y}")
root.minsize(680, 500)

# ── Polices ──
try:
    import tkinter.font as tkfont
    default_font = tkfont.nametofont("TkDefaultFont")
    default_font.configure(family="Segoe UI", size=9)
except:
    pass

# ── Theme ──
sv_ttk.set_theme("dark")
style = ttk.Style()
ACCENT = "#3b82f6"
style.configure("Start.TButton", font=("Segoe UI", 10, "bold"), padding=(28, 10))
style.map("Start.TButton",
    background=[("!disabled", ACCENT), ("active", "#2563eb")],
    foreground=[("!disabled", "#ffffff")])
style.configure("Check.TButton", font=("Segoe UI", 10), padding=(22, 8))
style.configure("Browse.TButton", padding=(10, 3), font=("Segoe UI", 9))
style.configure("Card.TLabelframe", padding=12)
style.configure("Card.TLabelframe.Label", font=("Segoe UI", 10, "bold"))

BG = style.lookup("TFrame", "background") or "#1c1c1c"
root.configure(bg=BG)

# ── Variables ──
folder_var = tk.StringVar(value="Sélectionner un dossier ou fichier...")
thread_var = tk.StringVar(value=str(min(CPU_COUNT * 2, 16)))

# ── Body ──
main = ttk.Frame(root)
main.pack(fill=tk.BOTH, expand=True, padx=20, pady=(16, 14))
main.columnconfigure(0, weight=1)
main.rowconfigure(3, weight=1)

# ── Card Options ──
opts = ttk.LabelFrame(main, text="Options", style="Card.TLabelframe", padding=(18, 14))
opts.grid(row=0, column=0, sticky="ew", pady=(0, 16))
opts.columnconfigure(1, weight=1)

ttk.Label(opts, text="Source:").grid(row=0, column=0, sticky="w", pady=3)
entry_path = ttk.Entry(opts, textvariable=folder_var)
entry_path.grid(row=0, column=1, sticky="ew", padx=10, pady=3)
entry_path.bind("<FocusIn>", lambda e: folder_var.set("") if folder_var.get() == "Sélectionner un dossier ou fichier..." else None)
btn_src = ttk.Frame(opts)
btn_src.grid(row=0, column=2, pady=3)
ttk.Button(btn_src, text="Folder", style="Browse.TButton", command=pick).pack(side=tk.LEFT, padx=1)
ttk.Button(btn_src, text="File", style="Browse.TButton", command=pick_file).pack(side=tk.LEFT, padx=1)

ttk.Label(opts, text="Threads:").grid(row=1, column=0, sticky="w", pady=3)
ttk.Entry(opts, textvariable=thread_var, width=8).grid(row=1, column=1, sticky="w", padx=10, pady=3)

# ── Buttons ──
btn_frame = ttk.Frame(main)
btn_frame.grid(row=1, column=0, pady=(0, 16))

start_btn = ttk.Button(btn_frame, text="START", style="Start.TButton", command=start)
start_btn.pack(side=tk.LEFT, padx=6)

stop_btn = ttk.Button(btn_frame, text="STOP", command=stop_processing)
stop_btn.pack(side=tk.LEFT, padx=6)
stop_btn.config(state="disabled")

check_btn = ttk.Button(btn_frame, text="CHECK", style="Check.TButton", command=start_check)
check_btn.pack(side=tk.LEFT, padx=6)

# ── Progress ──
prog_frame = ttk.Frame(main)
prog_frame.grid(row=2, column=0, pady=(0, 6), sticky="ew")
prog_frame.columnconfigure(0, weight=1)
progress = ttk.Progressbar(prog_frame)
progress.grid(row=0, column=0, sticky="ew")
prog_label = ttk.Label(prog_frame, text="", font=("Segoe UI", 9))
prog_label.grid(row=0, column=1, padx=(8, 0))

# ── Card Log ──
log_frame = ttk.LabelFrame(main, text="Log", style="Card.TLabelframe", padding=(14, 10))
log_frame.grid(row=3, column=0, sticky="nsew")
log_frame.columnconfigure(0, weight=1)
log_frame.rowconfigure(1, weight=1)

log_top = ttk.Frame(log_frame)
log_top.grid(row=0, column=0, columnspan=2, sticky="ew")
ttk.Button(log_top, text="Clear", command=clear_log, style="Browse.TButton").pack(side=tk.RIGHT)

text_box = tk.Text(log_frame, wrap=tk.NONE, font=("Consolas", 9),
                   bg="#141414", fg="#d4d4d4", insertbackground="#d4d4d4",
                   relief=tk.FLAT, borderwidth=0, highlightthickness=0,
                   spacing1=1, spacing3=2)
text_box.grid(row=1, column=0, sticky="nsew")

vsb = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=text_box.yview)
vsb.grid(row=1, column=1, sticky="ns")
text_box.config(yscrollcommand=vsb.set)
text_box.tag_config("ok",     foreground="#4ade80")
text_box.tag_config("warn",   foreground="#fbbf24")
text_box.tag_config("error",  foreground="#f87171")
text_box.tag_config("skip",   foreground="#9ca3af")
text_box.tag_config("inst",   foreground="#60a5fa")

hsb = ttk.Scrollbar(log_frame, orient=tk.HORIZONTAL, command=text_box.xview)
hsb.grid(row=2, column=0, sticky="ew")
text_box.config(xscrollcommand=hsb.set)

# ── Status ──
status_bar = ttk.Frame(root)
status_bar.pack(fill=tk.X, side=tk.BOTTOM)
status_label = ttk.Label(status_bar, text="Ready", font=("Segoe UI", 9))
status_label.pack(side=tk.LEFT, padx=12)
ttk.Button(status_bar, text="Clear Cache", command=clear_cache, style="Browse.TButton").pack(side=tk.RIGHT, padx=6)

root.protocol("WM_DELETE_WINDOW", on_close)
root.deiconify()
root.mainloop()
