import os
import re
import time
import threading
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
import tkinter as tk
from tkinter import filedialog, messagebox
import customtkinter as ctk

from core import (MATCH_THRESHOLD, RateLimiter, build_get_params, clean, cache_key,
                  clear_cache as _purge_cache, entry_expired,
                  fold, get_fuzz, match, opening_geometry, resolve_asset,
                  strip_timestamps,
                  _cache, _cache_lock,
                  _mark_dirty, _parse_result, _save_cache, set_logger as _set_logger)

LRCLIB_API = "https://lrclib.net/api/get"
SESSION = None
RATE_MAX = 25.0
RATE_MIN = 2.0
TRANSIENT = (429, 500, 502, 503, 504)
_MAX_ATTEMPTS = 3

_LIM = RateLimiter(RATE_MAX, min_rate=RATE_MIN)
_pause_lock = threading.Lock()
_pause_until = 0.0

def set_rate_ceiling(rate):
    _LIM.set_ceiling(rate)

def _hold(seconds):
    global _pause_until
    with _pause_lock:
        _pause_until = max(_pause_until, time.time() + seconds)

def _rate_limit_pause():
    while True:
        with _pause_lock:
            wait = _pause_until - time.time()
        if wait <= 0 or _cancel.is_set():
            return
        time.sleep(min(0.2, wait))

def _retry_after(r):
    try:
        return max(0.0, float(r.headers.get("Retry-After", 2)))
    except (AttributeError, TypeError, ValueError):
        return 2.0

def _api_error(r):
    try:
        data = r.json()
    except Exception:
        return "paramètres invalides"
    if isinstance(data, dict) and data.get("message"):
        return str(data["message"])
    return "paramètres invalides"

# ponytail: débit unique lissé à RATE_MAX (curseur), abaissé automatiquement
# sur 429/5xx via Retry-After puis remonté après une série de succès.
def _api_get(url, params=None, timeout=None):
    """Rend la réponse après avoir lissé le débit et réessayé les erreurs
    transitoires. Un 429/5xx est réessayé _MAX_ATTEMPTS fois en honorant
    Retry-After, en divisant le débit par deux à chaque tentative. Au terme,
    la dernière réponse est rendue telle quelle : l'appelant doit alors
    ne rien mettre en cache, une erreur transitoire n'est pas un verdict.
    """
    r = None
    for _ in range(_MAX_ATTEMPTS):
        _LIM.acquire(cancel=_cancel.is_set)
        _rate_limit_pause()
        r = get_session().get(url, params=params, timeout=timeout)
        if r.status_code not in TRANSIENT:
            _LIM.on_success()
            return r
        retry_after = _retry_after(r)
        rate = _LIM.on_throttle(retry_after)
        log(f"[Rate limit] HTTP {r.status_code} — pause {retry_after:.0f}s, "
            f"débit ajusté à {rate:.1f} req/s")
        _hold(retry_after)
    return r

def get_session():
    global SESSION
    if SESSION is None:
        import requests
        from urllib3.util.retry import Retry
        SESSION = requests.Session()
        SESSION.headers.update({"User-Agent": "lrc-injector/1.2 (https://github.com/K4taV8/LRC-INJECTOR-PY)"})
        try:
            retry = Retry(total=2, backoff_factor=0.5, backoff_jitter=0.5,
                          status_forcelist=[500, 502, 503],
                          allowed_methods=["GET"])
        except TypeError:
            retry = Retry(total=2, backoff_factor=0.5,
                          status_forcelist=[500, 502, 503],
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

CPU_COUNT = os.cpu_count() or 4
_cache_hits = 0
_cache_misses = 0
_stat_lock = Lock()

def _stat_hit():
    global _cache_hits
    with _stat_lock:
        _cache_hits += 1

def _stat_miss():
    global _cache_misses
    with _stat_lock:
        _cache_misses += 1

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

def _schedule_flush():
    try:
        if root.winfo_exists():
            root.after(150, _flush_log)
    except Exception:
        pass

def log(msg):
    global _log_pending
    schedule = False
    with _log_lock:
        _log_buffer.append(msg)
        if not _log_pending:
            _log_pending = True
            schedule = True
    if schedule:
        _schedule_flush()

_set_logger(log)

def _append_log_file(msgs):
    path = os.environ.get("LRC_LOG_FILE")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n".join(msgs) + "\n")
    except OSError as e:
        log(f"[ERROR] Log file write failed: {e}")

def _flush_log():
    global _log_pending, _log_lines
    try:
        if not root.winfo_exists():
            return
    except Exception:
        return
    with _log_lock:
        msgs = _log_buffer[:]
        _log_buffer.clear()
        _log_pending = bool(_log_buffer)
        schedule = _log_pending
    if msgs:
        _append_log_file(msgs)
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
        _schedule_flush()

_pulsing = False
_cancel = threading.Event()
_worker_thread = None

def start_pulse():
    global _pulsing
    _pulsing = True
    root.after(0, lambda: (
        progress.configure(mode="indeterminate"),
        progress.start()
    ))

def stop_pulse():
    global _pulsing
    _pulsing = False
    root.after(0, lambda: (
        progress.stop(),
        progress.configure(mode="determinate"),
        progress.set(0),
        status_label.configure(text="Ready"),
        prog_label.configure(text="")
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
            progress.configure(mode="determinate"),
            status_label.configure(text="Processing...")
        ))
        _pulsing = False

    def _up():
        progress.set(val / maxv if maxv else 0)
        prog_label.configure(text=f"{val}/{maxv}")
    root.after(0, _up)

SEARCH_API = "https://lrclib.net/api/search"
_FALLBACK_BUDGET = 12.0
_DURATION_TOL = 2.0
_DURATION_WEIGHT = MATCH_THRESHOLD * 2

def _search_queries(artist, title):
    queries = []
    seen = set()
    for q in (f"{artist} {title}", title, f"{fold(artist)} {fold(title)}"):
        q = q.strip()
        if q and q not in seen:
            seen.add(q)
            queries.append({"q": q})
    if title.strip():
        queries.append({"track_name": title.strip(), "artist_name": artist.strip()})
    return queries

def _score_entry(entry, ca, ct, duration):
    """Score de pertinence d'un résultat de recherche.

    Le titre et l'artiste ouvrent la porte (seuil de similarité), puis la
    durée départage les versions : c'est le même critère que celui qu'utilise
    /api/get (±2 s), et sans lui un live ou un remaster gagne trop souvent.
    """
    fuzz = get_fuzz()
    ra = fuzz.ratio(ca, clean(entry.get("artistName", "")))
    rt = fuzz.ratio(ct, clean(entry.get("trackName", "")))
    if ra < MATCH_THRESHOLD or rt < MATCH_THRESHOLD:
        return None
    score = ra + rt
    remote = entry.get("duration")
    if duration and isinstance(remote, (int, float)):
        delta = abs(remote - duration)
        score += _DURATION_WEIGHT if delta <= _DURATION_TOL else max(0.0, 20.0 - delta)
    return score

def _search_fallback(artist, title, key, duration=None):
    ca, ct = clean(artist), clean(title)
    best_lrc = None
    best_plain = None
    score_lrc = -1
    score_plain = -1
    t0 = time.time()
    for params in _search_queries(artist, title):
        remaining = _FALLBACK_BUDGET - (time.time() - t0)
        if remaining <= 0:
            break
        try:
            s = _api_get(SEARCH_API, params=params, timeout=min(8, remaining))
            if s.status_code != 200:
                continue
            for entry in s.json():
                score = _score_entry(entry, ca, ct, duration)
                if score is None:
                    continue
                lrc, inst = _parse_result(entry)
                if lrc and score > score_lrc:
                    best_lrc = (lrc, entry, inst)
                    score_lrc = score
                elif not lrc and score > score_plain:
                    best_plain = (lrc, entry, inst)
                    score_plain = score
        except Exception:
            continue
    if best_lrc:
        lrc, entry, inst = best_lrc
        with _cache_lock:
            _cache[key] = {"lrc": lrc, "inst": inst,
                           "a": entry.get("artistName"), "t": entry.get("trackName"),
                           "pl": entry.get("plainLyrics")}
        _mark_dirty()
        return lrc, entry, inst
    if best_plain:
        lrc, entry, inst = best_plain
        with _cache_lock:
            _cache[key] = {"lrc": None, "inst": False, "no_sync": True,
                           "a": entry.get("artistName"), "t": entry.get("trackName"),
                           "pl": entry.get("plainLyrics"), "ts": time.time()}
        _mark_dirty()
        return None, entry, False
    with _cache_lock:
        _cache[key] = {"lrc": None, "inst": False, "miss": True,
                       "pl": None, "ts": time.time()}
    _mark_dirty()
    return None, None, False

def fetch_lrc(artist, title, album="", duration=None):
    key = cache_key(artist, title, album)
    deleted = False
    with _cache_lock:
        if key in _cache:
            _stat_hit()
            c = _cache[key]
            if entry_expired(c, time.time()):
                del _cache[key]
                deleted = True
            elif c.get("lrc"):
                return c["lrc"], {"artistName": c["a"], "trackName": c["t"], "plainLyrics": c.get("pl")}, c.get("inst", False)
            elif c.get("inst"):
                return None, None, True
            elif c.get("no_sync") or c.get("miss"):
                if c.get("pl"):
                    return None, {"artistName": c.get("a"), "trackName": c.get("t"), "plainLyrics": c.get("pl")}, False
                return None, None, False
            else:
                del _cache[key]
                deleted = True
    if deleted:
        _mark_dirty()
    try:
        params, dropped = build_get_params(artist, title, album, duration)
        if params is None:
            log(f"[ERROR] Signature inexploitable (artiste ou titre vide) : "
                f"{artist!r} - {title!r}")
            return None, None, False
        _stat_miss()
        if dropped:
            log(f"[WARN] /api/get : {', '.join(dropped)} ignoré pour {artist} - {title}")
        r = _api_get(LRCLIB_API, params=params, timeout=(3, 8))
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
                               "pl": data.get("plainLyrics"), "ts": time.time()}
            _mark_dirty()
            return None, data, False

        if r.status_code == 404:
            return _search_fallback(artist, title, key, duration)
        if r.status_code == 400:
            log(f"[ERROR] /api/get 400 sur {artist} - {title} : {_api_error(r)}")
            return None, None, False
        log(f"[WARN] /api/get {r.status_code} sur {artist} - {title} "
            f"— abandon, rien mis en cache (erreur transitoire)")
        return None, None, False
    except Exception:
        lrc, data, inst = _search_fallback(artist, title, key, duration)
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

        lrc, raw, inst = fetch_lrc(artist, title, album, getattr(audio.info, "length", None))
        if inst or re.search(r"instrumental", title, re.I):
            return ("inst", title, None)
        if not lrc:
            if raw and raw.get("plainLyrics"):
                if not match(artist, raw.get("artistName", "")) or not match(title, raw.get("trackName", "")):
                    return ("reject", title, None)
                audio["UNSYNCEDLYRICS"] = raw["plainLyrics"]
                if not _save_flac(audio, path):
                    k = cache_key(artist, title, album)
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
            k = cache_key(artist, title, album)
            with _cache_lock:
                _cache.pop(k, None)
            _mark_dirty()
            return ("error", title, "cannot write file (corrupted?)")
        return ("ok", title, None)
    except Exception as e:
        return ("error", path, str(e))

def _collect_flac(folder):
    if os.path.isfile(folder):
        if not folder.lower().endswith(".flac"):
            log(f"[SKIP] Not a .flac file: {os.path.basename(folder)}")
            return []
        return [folder]
    files = []
    for r, _, fs in os.walk(folder, onerror=lambda e: log(f"[ERROR] {e.filename} -> {e.strerror}")):
        for f in fs:
            if f.lower().endswith(".flac"):
                files.append(os.path.join(r, f))
    if not files:
        log("[WARN] No .flac files found in the specified path")
    seen = set()
    unique = []
    dup = 0
    for p in files:
        try:
            ident = os.stat(p).st_ino or os.path.realpath(p)
        except OSError:
            ident = p
        if ident in seen:
            dup += 1
            continue
        seen.add(ident)
        unique.append(p)
    if dup:
        log(f"[WARN] {dup} doublon(s) ignoré(s) (même fichier listé plusieurs fois)")
    return unique

def run(folder, workers):
    global _last_prog, _cache_hits, _cache_misses
    _cancel.clear()
    _last_prog = 0
    _cache_hits = 0
    _cache_misses = 0
    t0 = time.time()
    start_pulse()
    try:
        files = _collect_flac(folder)
        if os.path.isfile(folder) and not files:
            return
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
        tot = _cache_hits + _cache_misses
        rate = f"{_cache_hits / tot * 100:.0f}%" if tot else "n/a"
        log(f"Durée: {time.time() - t0:.1f}s | Cache: {_cache_hits} hits / {_cache_misses} appels API ({rate})")
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
    start_btn.configure(state=state)
    check_btn.configure(state=state)
    stop_btn.configure(state="normal" if b else "disabled")
    clear_cache_btn.configure(state=state)

def stop_processing():
    _cancel.set()
    log("[STOP] Annulation en cours...")

def _confirm_batch(p):
    if os.path.isdir(p):
        if not messagebox.askyesno(
                "Modification en place",
                "Les fichiers .flac seront modifiés en place (aucune copie temporaire).\n\n"
                "En cas de crash ou de coupure pendant l'écriture, les fichiers en cours\n"
                "d'écriture peuvent être endommagés.\n\n"
                "Avez-vous sauvegardé votre bibliothèque ?\n\n"
                "Choisissez « Non » pour annuler le lot."):
            log("Annulé par l'utilisateur (confirmation demandée)")
            return False
    return True

def start():
    global _worker_thread
    if _worker_thread and _worker_thread.is_alive():
        log("[ERROR] A treatment is already in progress")
        return
    _cancel.clear()
    p = folder_var.get()
    if auto_threads.get():
        workers = CPU_COUNT
    else:
        try:
            workers = max(1, min(32, int(thread_var.get())))
        except ValueError:
            workers = CPU_COUNT
    thread_var.set(str(workers))
    if not p or p == "Sélectionner un dossier ou fichier...":
        log("Select a folder or file")
        return
    if not os.path.exists(p):
        log("Path does not exist")
        return
    if not _confirm_batch(p):
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
            lrc, raw, inst = fetch_lrc(artist, title, album, getattr(audio.info, "length", None))
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
    global _last_prog, _cache_hits, _cache_misses
    _cancel.clear()
    _last_prog = 0
    _cache_hits = 0
    _cache_misses = 0
    t0 = time.time()
    log("[INFO] CHECK & REPAIR mode: files will be modified (tags added/fixed)")
    start_pulse()
    try:
        files = _collect_flac(folder)
        if os.path.isfile(folder) and not files:
            return
        total = len(files)
        lines = []
        try:
            if auto_threads.get():
                workers = min(CPU_COUNT, 16)
            else:
                workers = max(1, min(16, int(thread_var.get())))
        except ValueError:
            workers = min(CPU_COUNT, 16)
        thread_var.set(str(workers))
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
        tot = _cache_hits + _cache_misses
        rate = f"{_cache_hits / tot * 100:.0f}%" if tot else "n/a"
        log(f"Durée: {time.time() - t0:.1f}s | Cache: {_cache_hits} hits / {_cache_misses} appels API ({rate})")
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
    _purge_cache()
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
    if not _confirm_batch(p):
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

# ── Identité d'application (icône de la barre des tâches / Alt-Tab) ──
# Sans AppUserModelID explicite, le regroupement de la barre des tâches se
# fait sur le chemin de l'exécutable : elle affiche donc l'icône de
# python.exe (le logo Py) et ignore celle de la fenêtre. Doit être appelé
# AVANT la création de la fenêtre, sinon Windows a déjà fait son grouping.
APP_USER_MODEL_ID = "K4taV8.LRC-INJECTOR-PY"

def _set_app_user_model_id():
    try:
        from ctypes import windll
        windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception as e:
        log(f"[WARN] AppUserModelID non défini : {e}")

_set_app_user_model_id()

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

# ── Palette (maquette style_v2.css) ──
ctk.set_appearance_mode("dark")
BG       = "#121318"
SURFACE  = "#1b1d23"
ELEV     = "#292b34"
ELEV_H   = "#343742"
BG_IN    = "#0f1014"
BORDER   = "#2d303a"
BORDER_IN = "#454a56"
TXT      = "#f4f5f8"
TXT_SEC  = "#a4a7b4"
TXT_MUT  = "#767880"
GREEN    = "#3a8f56"
GREEN_H  = "#4aa263"
RED      = "#c13b30"
SM, MD, LG = 8, 10, 14
F_UI   = ("Segoe UI", 12)
F_ACC  = ("Segoe UI", 12, "bold")
F_SEC  = ("Segoe UI", 11, "bold")
F_SUB  = ("Segoe UI", 11)
F_LOG  = ("Consolas", 11)

# ── Fenêtre ──
root = ctk.CTk(fg_color=BG)
_force_dark_titlebar(root)
root.title("LRC Injector")

# ── Logo (fenêtre, barre des tâches, coin haut-gauche de l'app) ──
APP_DIR = os.path.dirname(os.path.abspath(__file__))
LOGO_SIZE = 32
LOGO_PATH = resolve_asset(APP_DIR, "Logo.png")
# Les deux objets doivent survivre au garbage collector, sans quoi Tk les
# libère et l'icône disparaît ou vire au noir.
_logo_photo = None
_logo_ctk = None

def _load_logo():
    global _logo_photo, _logo_ctk
    if not LOGO_PATH:
        return
    try:
        _logo_photo = tk.PhotoImage(file=LOGO_PATH)
        root.iconphoto(True, _logo_photo)
    except Exception as e:
        _logo_photo = None
        log(f"[WARN] Icône de fenêtre ignorée : {e}")
    try:
        from PIL import Image
        _logo_ctk = ctk.CTkImage(Image.open(LOGO_PATH), size=(LOGO_SIZE, LOGO_SIZE))
    except Exception as e:
        _logo_ctk = None
        log(f"[WARN] Logo de l'interface ignoré : {e}")

_load_logo()

# Taille d'ouverture annoncee en pixels physiques, convertie en unites Tk
# selon le DPI reel de l'ecran (852x952 a 125% -> geometry() 682x762).
OPEN_W_PX = 852
OPEN_H_PX = 952

def _display_scale(win):
    try:
        from ctypes import windll
        return max(1.0, windll.user32.GetDpiForWindow(win.winfo_id()) / 96.0)
    except Exception:
        return 1.0

def _client_border(win):
    """Decalage (gauche, haut) entre le cadre de la fenetre et sa zone client.

    Sans lui, un centrage calculé sur la seule zone client decale le cadre
    visible de la largeur d'une bordure.
    """
    try:
        from ctypes import windll, byref, c_long, Structure
        hwnd = windll.user32.GetParent(win.winfo_id()) or win.winfo_id()

        class _RECT(Structure):
            _fields_ = [("l", c_long), ("t", c_long), ("r", c_long), ("b", c_long)]

        rc = _RECT()
        if not windll.user32.GetWindowRect(hwnd, byref(rc)):
            return 0, 0
        return max(0, win.winfo_rootx() - rc.l), max(0, win.winfo_rooty() - rc.t)
    except Exception:
        return 0, 0

W, H, GX, GY = opening_geometry(OPEN_W_PX, OPEN_H_PX,
                                root.winfo_screenwidth(), root.winfo_screenheight(),
                                _display_scale(root),
                                *_client_border(root))
root.geometry(f"{W}x{H}+{GX}+{GY}")
root.minsize(680, 500)

# ── Variables ──
folder_var = tk.StringVar(value="Sélectionner un dossier ou fichier...")
thread_var = tk.StringVar(value=str(CPU_COUNT))
auto_threads = tk.BooleanVar(value=True)

def _toggle_auto():
    entry_thr.configure(state="disabled" if auto_threads.get() else "normal")
    if auto_threads.get():
        thread_var.set(str(CPU_COUNT))

# ── Body ──
main = ctk.CTkFrame(root, fg_color="transparent")
main.pack(fill="both", expand=True, padx=24, pady=(18, 12))
main.grid_columnconfigure(0, weight=1)
main.grid_rowconfigure(5, weight=1)

# ── Header (logo + nom, coin haut-gauche de la fenêtre) ──
header = ctk.CTkFrame(main, fg_color="transparent")
header.grid(row=0, column=0, sticky="ew", pady=(0, 14))
if _logo_ctk is not None:
    ctk.CTkLabel(header, image=_logo_ctk, text="").pack(side="left", padx=(0, 10))
ctk.CTkLabel(header, text="LRC Injector", font=("Segoe UI", 15, "bold"),
             text_color=TXT).pack(side="left")

# ── Card Options ──
opts = ctk.CTkFrame(main, fg_color=SURFACE, corner_radius=LG,
                    border_width=1, border_color=BORDER)
opts.grid(row=1, column=0, sticky="ew", pady=(0, 16))

opts_in = ctk.CTkFrame(opts, fg_color="transparent")
opts_in.pack(fill="both", expand=True, padx=18, pady=(16, 16))
opts_in.grid_columnconfigure(1, weight=1)

ctk.CTkLabel(opts_in, text="OPTIONS", font=F_SEC, text_color=TXT_SEC,
             anchor="w").grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 12))

def _opt_label(txt, row):
    ctk.CTkLabel(opts_in, text=txt, font=F_UI, text_color=TXT_SEC, width=70,
                 anchor="w").grid(row=row, column=0, sticky="w", pady=5)

def _opt_btn(parent, text, command, width=78, height=38, color=TXT_SEC):
    return ctk.CTkButton(parent, text=text, command=command, width=width, height=height,
                         font=F_UI, fg_color=ELEV, hover_color=ELEV_H, text_color=color,
                         corner_radius=SM, border_width=1, border_color=BORDER)

# Source
_opt_label("Source:", 1)
entry_path = ctk.CTkEntry(opts_in, textvariable=folder_var, font=F_UI, height=38,
                          fg_color=BG_IN, border_color=BORDER_IN, corner_radius=SM,
                          text_color=TXT_SEC)
entry_path.grid(row=1, column=1, sticky="ew", padx=12, pady=5)
entry_path.bind("<FocusIn>", lambda e: folder_var.set("") if folder_var.get() == "Sélectionner un dossier ou fichier..." else None)
src_btns = ctk.CTkFrame(opts_in, fg_color="transparent")
src_btns.grid(row=1, column=2, pady=5)
_opt_btn(src_btns, "Folder", pick, width=74).pack(side="left", padx=(0, 6))
_opt_btn(src_btns, "File", pick_file, width=74).pack(side="left")

# Threads
_opt_label("Threads:", 2)
thr_frame = ctk.CTkFrame(opts_in, fg_color="transparent")
thr_frame.grid(row=2, column=1, sticky="w", padx=12, pady=5)
entry_thr = ctk.CTkEntry(thr_frame, textvariable=thread_var, width=66, height=38,
                         font=F_UI, fg_color=BG_IN, border_color=BORDER_IN,
                         corner_radius=SM, text_color=TXT, state="disabled")
entry_thr.pack(side="left")
auto_chk = ctk.CTkCheckBox(thr_frame, text="Auto", variable=auto_threads, command=_toggle_auto,
                           checkbox_width=18, checkbox_height=18, corner_radius=4,
                           fg_color="#f5f5f6", hover_color="#c8c8cd",
                           checkmark_color="#0a0a0c", border_color=BORDER_IN,
                           font=F_UI, text_color=TXT)
auto_chk.pack(side="left", padx=(10, 0))

# API rate
_opt_label("API rate:", 3)
rate_var = tk.IntVar(value=int(RATE_MAX))

def _on_rate_change(value):
    ceiling = max(1.0, float(value))
    set_rate_ceiling(ceiling)
    rate_label.configure(text=f"{int(round(ceiling))} req/s")

rate_frame = ctk.CTkFrame(opts_in, fg_color="transparent")
rate_frame.grid(row=3, column=1, sticky="w", padx=12, pady=5)
rate_slider = ctk.CTkSlider(rate_frame, from_=1, to=int(RATE_MAX),
                            number_of_steps=int(RATE_MAX) - 1,
                            command=_on_rate_change, width=140, height=24,
                            button_color=ELEV_H, button_hover_color=TXT_SEC,
                            progress_color=GREEN, fg_color=BORDER)
rate_slider.set(RATE_MAX)
rate_slider.grid(row=0, column=0, sticky="w")
rate_label = ctk.CTkLabel(rate_frame, text=f"{int(RATE_MAX)} req/s", font=F_SUB,
                          text_color=TXT_MUT, width=72, anchor="w")
rate_label.grid(row=0, column=1, padx=(10, 0))

clear_cache_btn = _opt_btn(opts_in, "Clear Cache", clear_cache, width=112)
clear_cache_btn.grid(row=3, column=2, sticky="e", pady=5)

# ── Actions ──
acts = ctk.CTkFrame(main, fg_color="transparent")
acts.grid(row=2, column=0, pady=(0, 10))
start_btn = ctk.CTkButton(acts, text="START", command=start, font=F_ACC,
                          fg_color=GREEN, hover_color=GREEN_H, text_color=TXT,
                          corner_radius=MD, height=42, width=140)
start_btn.pack(side="left", padx=7)
stop_btn = ctk.CTkButton(acts, text="STOP", command=stop_processing, font=F_ACC,
                         fg_color=ELEV, hover_color=ELEV_H, text_color=RED,
                         corner_radius=MD, height=42, width=140,
                         border_width=1, border_color=BORDER)
stop_btn.pack(side="left", padx=7)
stop_btn.configure(state="disabled")
check_btn = ctk.CTkButton(acts, text="CHECK & REPAIR", command=start_check, font=F_ACC,
                          fg_color=ELEV, hover_color=ELEV_H, text_color=TXT_SEC,
                          corner_radius=MD, height=42, width=150)
check_btn.pack(side="left", padx=7)

# ── Progress ──
prog_frame = ctk.CTkFrame(main, fg_color="transparent")
prog_frame.grid(row=3, column=0, sticky="ew", pady=(0, 4))
prog_frame.grid_columnconfigure(0, weight=1)
progress = ctk.CTkProgressBar(prog_frame, height=8, corner_radius=0,
                              fg_color=BG_IN, progress_color=GREEN)
progress.grid(row=0, column=0, sticky="ew")
progress.set(0)
prog_label = ctk.CTkLabel(prog_frame, text="", font=F_SUB, text_color=TXT_MUT)
prog_label.grid(row=0, column=1, padx=(10, 0))

# ── Separator ──
sep = ctk.CTkFrame(main, fg_color=BORDER, height=1, corner_radius=0)
sep.grid(row=4, column=0, sticky="ew", pady=(2, 12))

# ── Card Log ──
log_frame = ctk.CTkFrame(main, fg_color=SURFACE, corner_radius=LG,
                         border_width=1, border_color=BORDER)
log_frame.grid(row=5, column=0, sticky="nsew")

log_in = ctk.CTkFrame(log_frame, fg_color="transparent")
log_in.pack(fill="both", expand=True, padx=16, pady=(14, 14))
log_in.grid_columnconfigure(0, weight=1)
log_in.grid_rowconfigure(1, weight=1)

log_top = ctk.CTkFrame(log_in, fg_color="transparent")
log_top.grid(row=0, column=0, sticky="ew", pady=(0, 6))
ctk.CTkLabel(log_top, text="LOG", font=F_SEC, text_color=TXT_SEC).pack(side="left")
ctk.CTkButton(log_top, text="Clear", command=clear_log, width=78, height=30,
              font=F_SUB, fg_color=ELEV, hover_color=ELEV_H, text_color=TXT_SEC,
              corner_radius=SM, border_width=1, border_color=BORDER).pack(side="right")

text_box = ctk.CTkTextbox(log_in, wrap="none", font=F_LOG,
                          fg_color=BG_IN, text_color="#d8dae0",
                          border_width=1, border_color=BORDER_IN,
                          corner_radius=MD,
                          scrollbar_button_color=ELEV,
                          scrollbar_button_hover_color=ELEV_H)
text_box.grid(row=1, column=0, sticky="nsew")
text_box.tag_config("ok",    foreground="#4ade80")
text_box.tag_config("warn",  foreground="#fbbf24")
text_box.tag_config("error", foreground="#f87171")
text_box.tag_config("skip",  foreground="#9ca3af")
text_box.tag_config("inst",  foreground="#60a5fa")

# ── Status ──
status_bar = ctk.CTkFrame(root, fg_color=BG, height=26, corner_radius=0)
status_bar.pack(fill="x", side="bottom")
status_label = ctk.CTkLabel(status_bar, text="Ready", font=F_SUB, text_color=TXT_MUT)
status_label.pack(side="left", padx=12)

root.protocol("WM_DELETE_WINDOW", on_close)

def main():
    root.mainloop()

if __name__ == "__main__":
    main()
