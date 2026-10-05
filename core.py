"""Noyau pur de LRC Injector : nettoyage, matching flou, parsing, cache disque.

Sans aucune dépendance Tkinter : importable et testable unitairement.
L'application (lrc-inject.py) importe ces fonctions et branche son logger via set_logger().
"""
import json
import os
import re
import tempfile
import time
import unicodedata
from threading import Lock

MATCH_THRESHOLD = 85
DURATION_MIN = 1
DURATION_MAX = 3600
_NO_SYNC_TTL = 30 * 24 * 3600
_MISS_TTL = 3 * 24 * 3600
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

def build_get_params(artist, title, album="", duration=None):
    """Construit les query params de /api/get.

    Retourne (params, dropped). params est None quand la signature est
    inexploitable. dropped liste les paramètres écartés pour cause de
    plage invalide, afin de pouvoir le signaler plutôt que l'écarter
    en silence.
    """
    artist = (artist or "").strip()
    title = (title or "").strip()
    if not artist or not title:
        return None, []
    params = {"artist_name": artist, "track_name": title}
    dropped = []
    album = (album or "").strip()
    if album:
        params["album_name"] = album
    if duration is not None:
        seconds = int(round(duration))
        if DURATION_MIN <= seconds <= DURATION_MAX:
            params["duration"] = seconds
        else:
            dropped.append("duration")
    return params, dropped

def scaled_size(px_w, px_h, scale, max_w=None, max_h=None):
    """Convertit une taille exprimée en pixels physiques vers les unités Tk.

    Tk raisonne en unités mises à l'échelle par le DPI : à 125%, une
    geometry() de 682x782 est rendue à 852x952 pixels par l'écran. On
    announces donc toujours la taille en pixels, et on borne le résultat
    à la zone de travail pour ne pas déborder.
    """
    scale = max(1e-6, float(scale or 1.0))
    w, h = int(round(px_w / scale)), int(round(px_h / scale))
    if max_w:
        w = min(w, int(max_w))
    if max_h:
        h = min(h, int(max_h))
    return max(1, w), max(1, h)

def opening_geometry(px_w, px_h, screen_w_px, screen_h_px, scale,
                     border_left=0, border_top=0):
    """(w, h, x, y) à passer à geometry() à l'ouverture.

    Mélange de deux unités, mesuré sur Windows : Tk met la **taille** à
    l'échelle du DPI, mais le gestionnaire de fenêtres interprète la
    **position** en pixels physiques. La position est donc calculée sur la
    taille réellement rendue, en retranchant la bordure du cadre pour
    centrer la zone client et non le cadre entier.
    """
    sw, sh = scaled_size(screen_w_px, screen_h_px, scale)
    w, h = scaled_size(px_w, px_h, scale, max_w=sw, max_h=sh)
    x = int((screen_w_px - w * scale) / 2) - border_left
    y = int((screen_h_px - h * scale) / 2) - border_top
    return w, h, max(0, x), max(0, y)

def resolve_asset(app_dir, filename):
    """Chemin d'un asset optionnel, ou None s'il est absent.

    L'application doit demarrer même sans logo : mieux vaut une icône par
    defaut qu'une fenetre refuse de s'ouvrir.
    """
    try:
        path = os.path.join(app_dir, filename)
    except Exception:
        return None
    return path if os.path.isfile(path) else None

def entry_expired(entry, now):
    """Une entrée 404 est réessayée vite : LRCLIB récupère en arrière-plan
    les pistes manquantes. Une entrée trouvée non synchronisée, elle, reste
    longue. Les réussites n'expirent jamais.
    """
    if entry.get("lrc") or entry.get("inst"):
        return False
    ts = entry.get("ts")
    if not ts:
        return False
    if entry.get("miss") or (entry.get("no_sync") and not entry.get("pl")):
        return False if now - ts <= _MISS_TTL else True
    if entry.get("no_sync"):
        return now - ts > _NO_SYNC_TTL
    return False

class RateLimiter:
    """Token bucket lisse, avec repli additif/multiplicatif sur 429.

    acquire() lisse le débit quel que soit le nombre de threads. Le débit
    effectif ne peut jamais dépasser le plafond fixé par le curseur ;
    on_throttle() le divise par deux, on_success() le remonte après une
    série de succès.
    """
    recover_after = 25
    _step = 1.0
    _slice = 0.05
    _eps = 1e-9

    def __init__(self, rate, min_rate=2.0, clock=None, sleep=None):
        self._clock = clock or time.monotonic
        self._sleep = sleep or time.sleep
        self._min_rate = float(min_rate)
        self._ceiling = float(rate)
        self._rate = float(rate)
        self._tokens = self._rate
        self._last = self._clock()
        self._streak = 0
        self._lock = Lock()

    @property
    def rate(self):
        return self._rate

    @property
    def ceiling(self):
        return self._ceiling

    def set_ceiling(self, rate):
        with self._lock:
            self._ceiling = max(1.0, float(rate))
            self._rate = min(self._rate, self._ceiling)
            self._tokens = min(self._tokens, self._ceiling)

    def _refill(self):
        now = self._clock()
        self._tokens = min(self._tokens + (now - self._last) * self._rate, self._rate)
        self._last = now

    def acquire(self, cancel=None):
        while True:
            with self._lock:
                self._refill()
                # epsilon : sans lui, un délai plus fin que la résolution de
                # l'horloge ne ferait pas avancer t et la boucle burning CPU
                if self._tokens >= 1.0 - self._eps:
                    self._tokens -= 1.0
                    return True
                delay = (1.0 - self._tokens) / self._rate
            if cancel and cancel():
                return False
            self._sleep(min(delay, self._slice))
            if cancel and cancel():
                return False

    def on_throttle(self, retry_after=2.0):
        with self._lock:
            self._rate = max(self._min_rate, self._rate / 2)
            self._tokens = min(self._tokens, self._rate)
            self._streak = 0
            return self._rate

    def on_success(self):
        with self._lock:
            self._streak += 1
            if self._streak >= self.recover_after:
                self._streak = 0
                if self._rate < self._ceiling:
                    self._rate = min(self._ceiling, self._rate + self._step)

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