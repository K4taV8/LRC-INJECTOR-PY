"""Noyau pur de LRC Injector : nettoyage, matching flou, parsing, cache disque.

Sans aucune dépendance Tkinter : importable et testable unitairement.
L'application (lrc-inject.py) importe ces fonctions et branche son logger via set_logger().
"""
import json
import os
import re
import tempfile
import unicodedata
from threading import Lock

MATCH_THRESHOLD = 85
_NO_SYNC_TTL = 30 * 24 * 3600
TS_RE = re.compile(r"\[\d+:\d+(?:\.\d+)?\]|<\d+:\d+\.\d+>")
META_RE = re.compile(r"^\[.*\]$")
FEAT_RE = re.compile(r"(?<!\w)feat\.(?=\s|$)")
FT_RE = re.compile(r"(?<!\w)ft\.(?=\s|$)")
SUFFIXES = ("(remastered)", "(official)", "(audio)")

_FUZZ = None

def get_fuzz():
    global _FUZZ
    if _FUZZ is None:
        from rapidfuzz import fuzz
        _FUZZ = fuzz
    return _FUZZ

def clean(t):
    if not t:
        return ""
    t = t.lower()
    t = FEAT_RE.sub("", t)
    t = FT_RE.sub("", t)
    for x in SUFFIXES:
        t = t.replace(x, "")
    return t.strip()

def fold(s):
    return unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode()

def match(a, b):
    fuzz = get_fuzz()
    ca, cb = clean(a), clean(b)
    if not ca or not cb:
        return False
    return (fuzz.ratio(ca, cb) >= MATCH_THRESHOLD or
            fuzz.ratio(fold(ca), fold(cb)) >= MATCH_THRESHOLD or
            fuzz.token_sort_ratio(ca, cb) >= MATCH_THRESHOLD)

def strip_timestamps(lrc):
    lines = []
    for line in lrc.splitlines():
        line = TS_RE.sub("", line).strip()
        if line and not META_RE.match(line):
            lines.append(line)
    return "\n".join(lines).strip()

def cache_key(artist, title, album=""):
    return f"{artist.strip().lower()}\x00{title.strip().lower()}\x00{album.strip().lower()}"

def _parse_result(data):
    lrc = data.get("syncedLyrics")
    pl = data.get("plainLyrics")
    if isinstance(lrc, list):
        lrc = "\n".join(lrc)
    if isinstance(pl, list):
        pl = "\n".join(pl)
    inst = bool(data.get("instrumental", False) or (pl and pl.strip().lower() == "instrumental"))
    return lrc, inst

CACHE_SCHEMA = 1
CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lrc_cache.json")
_cache = {}
_cache_lock = Lock()
_save_lock = Lock()
_cache_dirty = False
_dirty_count = 0
_FLUSH_EVERY = 200
_logger = None

def set_logger(fn):
    global _logger
    _logger = fn

def _load_cache():
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        _cache.clear()
        if isinstance(data, dict):
            if data.get("v") == CACHE_SCHEMA and isinstance(data.get("entries"), dict):
                _cache.update(data["entries"])
            elif "v" not in data:
                _cache.update(data)
    except Exception:
        _cache.clear()

def _save_cache():
    global _cache_dirty, _dirty_count
    with _save_lock:
        with _cache_lock:
            if not _cache_dirty:
                return
            snapshot = dict(_cache)
            pending_count = _dirty_count
        try:
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(CACHE_FILE))
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump({"v": CACHE_SCHEMA, "entries": snapshot}, f)
                os.replace(tmp, CACHE_FILE)
                with _cache_lock:
                    if _dirty_count == pending_count:
                        _cache_dirty = False
            except Exception:
                os.unlink(tmp)
                raise
        except Exception as e:
            if _logger:
                _logger(f"[ERROR] Cache save failed: {e}")

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

def clear_cache():
    with _save_lock, _cache_lock:
        _cache.clear()
        _cache_dirty = False
        _dirty_count = 0
    try:
        os.remove(CACHE_FILE)
    except Exception:
        pass

_load_cache()