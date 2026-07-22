import os
import re
import json
import tempfile
import unicodedata
import threading

from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
import tkinter as tk
from tkinter import filedialog, scrolledtext, ttk
try:
    import sv_ttk
except ImportError:
    sv_ttk = None

LRCLIB_API = "https://lrclib.net/api/get"
SESSION = None

def get_session():
    global SESSION
    if SESSION is None:
        import requests
        from urllib3.util.retry import Retry
        SESSION = requests.Session()
        try:
            retry = Retry(total=2, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503],
                          allowed_methods=["GET"])
        except TypeError:
            retry = Retry(total=2, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503],
                          method_whitelist=["GET"])
        adapter = requests.adapters.HTTPAdapter(pool_connections=32, pool_maxsize=32, max_retries=retry)
        SESSION.mount("https://", adapter)
    return SESSION

_FLAC_CLS = None
def get_flac_cls():
    global _FLAC_CLS
    if _FLAC_CLS is None:
        from mutagen.flac import FLAC
        _FLAC_CLS = FLAC
    return _FLAC_CLS

_FUZZ = None
def get_fuzz():
    global _FUZZ
    if _FUZZ is None:
        from rapidfuzz import fuzz
        _FUZZ = fuzz
    return _FUZZ

MATCH_THRESHOLD = 85

def _first_tag(audio, key, default=""):
    val = audio.get(key)
    if val is None or len(val) == 0:
        return default
    return val[0]

def _save_flac(audio, path):
    try:
        audio.save()
        return True
    except Exception as e:
        log(f"[ERROR] _save_flac échec: {e}")
        return False

TS_RE = re.compile(r"\[\d+:\d+(?:\.\d+)?\]|<\d+:\d+\.\d+>")
META_RE = re.compile(r"^\[.*\]$")
CPU_COUNT = os.cpu_count() or 4

CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lrc_cache.json")
_cache = {}
_cache_lock = Lock()
_cache_dirty = False
_dirty_count = 0
_FLUSH_EVERY = 200

def _load_cache():
    global _cache
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        _cache = data if isinstance(data, dict) else {}
    except Exception:
        _cache = {}

def _save_cache():
    global _cache_dirty, _dirty_count
    with _cache_lock:
        if not _cache_dirty:
            return
        snapshot = dict(_cache)
        pending_count = _dirty_count
    try:
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(CACHE_FILE))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(snapshot, f)
            os.replace(tmp, CACHE_FILE)
            with _cache_lock:
                if _dirty_count == pending_count:
                    _cache_dirty = False
        except:
            os.unlink(tmp)
            raise
    except Exception as e:
        log(f"[ERROR] Cache save failed: {e}")

def _mark_dirty():
    global _dirty_count, _cache_dirty
    with _cache_lock:
        _dirty_count += 1
        _cache_dirty = True
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
    if msg.startswith("[SKIP]") or msg.startswith("[MANQUE]"):
        return "skip"
    if msg.startswith("[OK]") or "[RÉPARÉ]" in msg:
        return "ok"
    if msg.startswith("[MISS]") or msg.startswith("[REJECT]") or msg.startswith("[PARTIEL]"):
        return "warn"
    if msg.startswith("[ERROR]") or msg.startswith("[ERREUR]"):
        return "error"
    if msg.startswith("[INST]"):
        return "inst"
    return None

def log(msg):
    global _log_pending
    schedule = False
    with _log_lock:
        _log_buffer.append(msg)
        if not _log_pending:
            _log_pending = True
            schedule = True
    if schedule and root.winfo_exists():
        root.after(150, _flush_log)

def _flush_log():
    global _log_pending, _log_lines
    if not root.winfo_exists():
        return
    with _log_lock:
        msgs = _log_buffer[:]
        _log_buffer.clear()
        _log_pending = bool(_log_buffer)
        schedule = _log_pending
    if msgs:
        tag = _tag_for(msgs[0])
        start = 0
        for i, m in enumerate(msgs):
            t = _tag_for(m)
            if t != tag:
                text = "\n".join(msgs[start:i]) + "\n"
                text_box.insert(tk.END, text, tag if tag else ())
                _log_lines += text.count("\n")
                tag = t
                start = i
        text = "\n".join(msgs[start:]) + "\n"
        text_box.insert(tk.END, text, tag if tag else ())
        _log_lines += text.count("\n")
        text_box.see(tk.END)
        if _log_lines > 3000:
            deleted = min(1000, _log_lines)
            text_box.delete("1.0", f"{deleted + 1}.0")
            _log_lines -= deleted
    if schedule:
        root.after(150, _flush_log)

_pulsing = False
_cancel = threading.Event()
_worker_thread = None

def start_pulse():
    global _pulsing
    _pulsing = True
    root.after(0, lambda: (
        progress.config(mode="indeterminate"),
        progress.start(40)
    ))

def stop_pulse():
    global _pulsing
    _pulsing = False
    root.after(0, lambda: (
        progress.stop(),
        progress.config(mode="determinate", value=0, maximum=100),
        status_label.config(text="Ready"),
        prog_label.config(text="")
    ))

_last_prog = 0

def update_progress(val, maxv):
    global _pulsing, _last_prog
    step = max(1, maxv // 200) if maxv else 1
    if val - _last_prog < step and val != maxv:
        return
    _last_prog = val
    if _pulsing:
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
    lines = []
    for line in lrc.splitlines():
        line = TS_RE.sub("", line).strip()
        if line and not META_RE.match(line):
            lines.append(line)
    return "\n".join(lines).strip()

def match(a, b):
    fuzz = get_fuzz()
    ca, cb = clean(a), clean(b)
    return (fuzz.ratio(ca, cb) >= MATCH_THRESHOLD or
            fuzz.ratio(fold(ca), fold(cb)) >= MATCH_THRESHOLD)

def fold(s):
    return unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode()

SEARCH_API = "https://lrclib.net/api/search"

def _parse_result(data):
    lrc = data.get("syncedLyrics")
    pl = data.get("plainLyrics")
    if isinstance(lrc, list):
        lrc = "\n".join(lrc)
    if isinstance(pl, list):
        pl = "\n".join(pl)
    inst = data.get("instrumental", False) or (pl and pl.strip().lower() == "instrumental")
    return lrc, inst

def _search_fallback(artist, title, key):
    fuzz = get_fuzz()
    seen = set()
    ca, ct = clean(artist), clean(title)
    queries = [f"{artist} {title}", title, f"{fold(artist)} {fold(title)}"]
    best = None
    for q in queries:
        if q in seen:
            continue
        seen.add(q)
        try:
            s = get_session().get(SEARCH_API, params={"q": q}, timeout=8)
            if s.status_code != 200:
                continue
            for entry in s.json():
                if fuzz.ratio(ca, clean(entry.get("artistName", ""))) >= MATCH_THRESHOLD and fuzz.ratio(ct, clean(entry.get("trackName", ""))) >= MATCH_THRESHOLD:
                    lrc, inst = _parse_result(entry)
                    if lrc and (not best or not best[0]):
                        best = (lrc, entry, inst)
                    if lrc and not inst:
                        with _cache_lock:
                            _cache[key] = {"lrc": lrc, "inst": inst,
                                           "a": entry.get("artistName"), "t": entry.get("trackName"),
                                           "pl": entry.get("plainLyrics")}
                        _mark_dirty()
                        return lrc, entry, inst
                    if not best:
                        best = (lrc, entry, inst)
        except Exception:
            continue
    if best:
        lrc, entry, inst = best
        with _cache_lock:
            _cache[key] = {"lrc": lrc, "inst": inst,
                           "a": entry.get("artistName"), "t": entry.get("trackName"),
                           "pl": entry.get("plainLyrics"),
                           "no_sync": not lrc and not inst}
        _mark_dirty()
        return best
    with _cache_lock:
        _cache[key] = {"lrc": None, "inst": False, "no_sync": True,
                       "pl": None}
    _mark_dirty()
    return None, None, False

def fetch_lrc(artist, title, album=""):
    key = f"{artist.strip().lower()}\x00{title.strip().lower()}\x00{album.strip().lower()}"
    deleted = False
    with _cache_lock:
        if key in _cache:
            c = _cache[key]
            if c.get("lrc"):
                return c["lrc"], {"artistName": c["a"], "trackName": c["t"], "plainLyrics": c.get("pl")}, c.get("inst", False)
            if c.get("inst"):
                return None, None, True
            if c.get("no_sync"):
                if c.get("pl"):
                    return None, {"artistName": c.get("a"), "trackName": c.get("t"), "plainLyrics": c.get("pl")}, False
                return None, None, False
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
                                   "a": data.get("artistName"), "t": data.get("trackName"),
                                   "pl": data.get("plainLyrics")}
                _mark_dirty()
                return lrc, data, inst
            with _cache_lock:
                _cache[key] = {"lrc": None, "inst": False, "no_sync": True,
                               "a": data.get("artistName"), "t": data.get("trackName"),
                               "pl": data.get("plainLyrics")}
            _mark_dirty()
            return None, data, False

        return _search_fallback(artist, title, key)
    except Exception:
        lrc, data, inst = _search_fallback(artist, title, key)
        if lrc or inst:
            return lrc, data, inst
        return None, None, False

def process_file(path):
    FLAC = get_flac_cls()
    try:
        if _cancel.is_set():
            return ("skip", path, "annulé")
        audio = FLAC(path)
        artist = _first_tag(audio, "artist")
        title = _first_tag(audio, "title")
        album = _first_tag(audio, "album")

        if not artist or not title:
            return ("skip", os.path.basename(path), None)

        if _first_tag(audio, "LYRICS").strip() or _first_tag(audio, "UNSYNCEDLYRICS").strip():
            return ("skip", title, "already has lyrics")

        lrc, raw, inst = fetch_lrc(artist, title, album)
        if inst or re.search(r"instrumental", title, re.I):
            return ("inst", title, None)
        if not lrc:
            if raw and raw.get("plainLyrics"):
                if not match(artist, raw.get("artistName", "")) or not match(title, raw.get("trackName", "")):
                    return ("reject", title, None)
                audio["UNSYNCEDLYRICS"] = raw["plainLyrics"]
                if not _save_flac(audio, path):
                    k = f"{artist.strip().lower()}\x00{title.strip().lower()}\x00{album.strip().lower()}"
                    with _cache_lock:
                        _cache.pop(k, None)
                    _mark_dirty()
                    return ("error", title, "cannot write file (corrupted?)")
                return ("ok", title, "plain lyrics")
            return ("miss", title, None)
        if not match(artist, raw.get("artistName", "")) or not match(title, raw.get("trackName", "")):
            return ("reject", title, None)

        audio["LYRICS"] = lrc
        unsynced = strip_timestamps(lrc)
        if unsynced:
            audio["UNSYNCEDLYRICS"] = unsynced
        if not _save_flac(audio, path):
            k = f"{artist.strip().lower()}\x00{title.strip().lower()}\x00{album.strip().lower()}"
            with _cache_lock:
                _cache.pop(k, None)
            _mark_dirty()
            return ("error", title, "cannot write file (corrupted?)")
        return ("ok", title, None)
    except Exception as e:
        return ("error", path, str(e))

def run(folder, workers):
    global _last_prog
    _cancel.clear()
    _last_prog = 0
    start_pulse()
    try:
        if os.path.isfile(folder):
            if not folder.lower().endswith(".flac"):
                log(f"[SKIP] Not a .flac file: {os.path.basename(folder)}")
                return
            files = [folder]
        else:
            files = []
            for r, _, fs in os.walk(folder, onerror=lambda e: log(f"[ERROR] {e.filename} -> {e.strerror}")):
                for f in fs:
                    if f.lower().endswith(".flac"):
                        files.append(os.path.join(r, f))
        stats = {"ok":0,"miss":0,"reject":0,"skip":0,"error":0,"inst":0}
        total = len(files)
        if not files and os.path.isdir(folder):
            log("[WARN] No .flac files found in the specified path")
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
                    log(f"[OK] {name}" + (f" -> {extra}" if extra else ""))
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
        log(f"\nInjection terminée : {stats['ok']}/{total} fichier(s)")
    finally:
        try:
            _save_cache()
        except Exception as e:
            log(f"[ERROR] Cache save failed: {e}")
        stop_pulse()
        root.after(0, lambda: _set_busy(False))


def _set_busy(b):
    state = "disabled" if b else "normal"
    start_btn.config(state=state)
    check_btn.config(state=state)
    stop_btn.config(state="normal" if b else "disabled")
    clear_cache_btn.config(state=state)

def stop_processing():
    _cancel.set()
    log("[STOP] Annulation en cours...")

def start():
    global _worker_thread
    if _worker_thread and _worker_thread.is_alive():
        log("[ERROR] A treatment is already in progress")
        return
    _cancel.clear()
    p = folder_var.get()
    try:
        workers = max(1, min(32, int(thread_var.get())))
    except ValueError:
        workers = CPU_COUNT
    if not p or p == "Sélectionner un dossier ou fichier...":
        log("Select a folder or file")
        return
    if not os.path.exists(p):
        log("Path does not exist")
        return
    _set_busy(True)
    _worker_thread = threading.Thread(target=run, args=(p, workers), daemon=True)
    _worker_thread.start()

def check_one(path):
    FLAC = get_flac_cls()
    try:
        if _cancel.is_set():
            return ("ok", f"[SKIP] {os.path.basename(path)} -> annulé")
        audio = FLAC(path)
        name = _first_tag(audio, "title") or os.path.basename(path)
        has_l = bool(_first_tag(audio, "LYRICS").strip())
        has_u = bool(_first_tag(audio, "UNSYNCEDLYRICS").strip())
        if has_l and has_u:
            return ("ok", f"[OK] {name}")
        if has_l and not has_u:
            unsynced = strip_timestamps(_first_tag(audio, "LYRICS"))
            if unsynced:
                audio["UNSYNCEDLYRICS"] = unsynced
                if not _save_flac(audio, path):
                    return ("error", f"[ERREUR] {name} -> cannot write file")
                return ("repair", f"[RÉPARÉ] {name} -> UNSYNCEDLYRICS ajouté")
            return ("partial", f"[PARTIEL] {name} -> pas de texte dans LYRICS")
        if has_u and not has_l:
            artist = _first_tag(audio, "artist")
            title = _first_tag(audio, "title")
            if not artist or not title:
                return ("partial", f"[PARTIEL] {name} -> pas d'artiste/titre")
            album = _first_tag(audio, "album")
            lrc, raw, inst = fetch_lrc(artist, title, album)
            if inst or re.search(r"instrumental", title, re.I):
                return ("inst", f"[INST] {name} -> instrumental")
            if lrc and match(artist, raw.get("artistName", "")) and match(title, raw.get("trackName", "")):
                audio["LYRICS"] = lrc
                if not _save_flac(audio, path):
                    return ("error", f"[ERREUR] {name} -> cannot write file")
                return ("repair", f"[RÉPARÉ] {name} -> LYRICS ajouté")
            if lrc:
                return ("partial", f"[PARTIEL] {name} -> rejeté par similarité")
            return ("partial", f"[PARTIEL] {name} -> introuvable sur l'API")
        return ("missing", f"[MANQUE] {name} -> aucun tag")
    except Exception as e:
        return ("error", f"[ERREUR] {os.path.basename(path)} -> {e}")

def check_files(folder):
    global _last_prog
    _cancel.clear()
    _last_prog = 0
    log("[INFO] CHECK & REPAIR mode: files will be modified (tags added/fixed)")
    start_pulse()
    try:
        if os.path.isfile(folder):
            if not folder.lower().endswith(".flac"):
                log(f"[SKIP] Not a .flac file: {os.path.basename(folder)}")
                return
            files = [folder]
        else:
            files = []
            for r, _, fs in os.walk(folder, onerror=lambda e: log(f"[ERROR] {e.filename} -> {e.strerror}")):
                for f in fs:
                    if f.lower().endswith(".flac"):
                        files.append(os.path.join(r, f))
        total = len(files)
        lines = []
        try:
            workers = max(1, min(16, int(thread_var.get())))
        except ValueError:
            workers = min(CPU_COUNT, 16)
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
        if cancelled:
            log("[STOP] Vérification interrompue")
        log(f"Vérification de {total} fichier(s) :\n")
        for _, msg in lines:
            log(msg)
        log(f"\n{ok}/{total} complets, {fixed} réparé(s)")
    finally:
        try:
            _save_cache()
        except Exception as e:
            log(f"[ERROR] Cache save failed: {e}")
        stop_pulse()
        root.after(0, lambda: _set_busy(False))

def clear_log():
    global _log_lines
    text_box.delete("1.0", tk.END)
    _log_lines = 0

def clear_cache():
    global _cache
    with _cache_lock:
        _cache = {}
        try:
            os.remove(CACHE_FILE)
        except Exception:
            pass
    log("[CACHE vidé]")

def on_close():
    global _worker_thread
    _cancel.set()
    if _worker_thread and _worker_thread.is_alive():
        _worker_thread.join(timeout=30)
    try:
        _save_cache()
    except Exception:
        pass
    root.destroy()

def start_check():
    global _worker_thread
    if _worker_thread and _worker_thread.is_alive():
        log("[ERROR] A treatment is already in progress")
        return
    _cancel.clear()
    p = folder_var.get()
    if not p or p == "Sélectionner un dossier ou fichier...":
        log("Select a folder or file")
        return
    if not os.path.exists(p):
        log("Path does not exist")
        return
    _set_busy(True)
    _worker_thread = threading.Thread(target=check_files, args=(p,), daemon=True)
    _worker_thread.start()

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
except Exception:
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
    except Exception:
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
except Exception:
    pass

# ── Theme ──
if sv_ttk:
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

check_btn = ttk.Button(btn_frame, text="CHECK & REPAIR", style="Check.TButton", command=start_check)
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
clear_cache_btn = ttk.Button(status_bar, text="Clear Cache", command=clear_cache, style="Browse.TButton")
clear_cache_btn.pack(side=tk.RIGHT, padx=6)

root.protocol("WM_DELETE_WINDOW", on_close)
root.deiconify()
root.mainloop()
