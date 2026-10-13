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
DURATION_TOL = 2.0
# Une entrée "trouvé mais sans paroles synchronisées" est réessayée au bout
# de 7 jours : plus court que les 30 jours d'origine, pour laisser une
# chance à une version synchronisée d'apparaître sans requêter à chaque lot.
_NO_SYNC_TTL = 7 * 24 * 3600
_MISS_TTL = 3 * 24 * 3600
TS_RE = re.compile(r"\[\d+:\d+(?:\.\d+)?\]|<\d+:\d+\.\d+>")
META_RE = re.compile(r"^\[.*\]$")
FEAT_RE = re.compile(r"(?<!\w)feat\.(?=\s|$)")
FT_RE = re.compile(r"(?<!\w)ft\.(?=\s|$)")
SUFFIXES = ("(remastered)", "(official)", "(audio)")

# Marqueurs de version qui changent la nature de la piste : on ne les retire
# jamais, et leur présence d'un seul côté interdit le match.
SIGNIFICANT_MARKERS = ("live", "acoustic", "demo", "remix", "instrumental", "unplugged")
# Marqueurs purement éditoriaux : ils ne changent pas la chanson, on les retire.
INSIGNIFICANT_MARKERS = ("explicit", "remastered", "remaster", "deluxe",
                         "single version", "radio edit", "mono", "stereo",
                         "official", "audio", "edition", "version")
_BRACKET_RE = re.compile(r"[\(\[\{]([^\)\]\}]*)[\)\]\}]")
_REMASTER_SUFFIX_RE = re.compile(r"\s*-\s*remaster(?:ed)?.*$", re.I)
_FEAT_TAIL_RE = re.compile(r"\b(?:feat|ft|featuring)\.?\b.*$", re.I)
_FEAT_TOKEN_RE = re.compile(r"\b(?:feat|ft|featuring)\.?\b", re.I)
_ARTIST_SEP_RE = re.compile(r"\s*(?:;|,|&|\bfeat\.?\b|\bft\.?\b|\bfeaturing\b)\s*", re.I)
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WS_RE = re.compile(r"\s+")

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

def _strip_brackets(s):
    """Retire les crochets non significatifs, conserve les marqueurs de version.

    (Explicit), (Deluxe Edition), (Official Audio) disparaissent ; (Live),
    (Acoustic) restent pour que "Song (Live)" ne matche pas "Song".
    """
    def repl(m):
        inner = m.group(1).strip().lower()
        if any(k in inner for k in SIGNIFICANT_MARKERS):
            return " " + inner + " "
        if any(k in inner for k in INSIGNIFICANT_MARKERS):
            return " "
        return " " + inner + " "
    return _BRACKET_RE.sub(repl, s)


def _normalize(s, drop_feat_tail=True):
    if not s:
        return ""
    s = fold(s).lower()
    s = _REMASTER_SUFFIX_RE.sub(" ", s)
    s = _strip_brackets(s)
    s = s.replace("&", " and ")
    if drop_feat_tail:
        s = _FEAT_TAIL_RE.sub(" ", s)
    else:
        s = _FEAT_TOKEN_RE.sub(" ", s)
    s = _PUNCT_RE.sub(" ", s)
    return _WS_RE.sub(" ", s).strip()


def _variants(s):
    """Formes normalisées candidates d'un libellé.

    On garde la chaîne entière (avec et sans retrait de la queue "feat ...")
    et chaque artiste séparé par ; , & ou feat, afin de prendre le meilleur
    appariement sans jamais recourir à token_set_ratio.
    """
    s = s or ""
    out = set()
    parts = [p for p in _ARTIST_SEP_RE.split(s) if p and p.strip()]
    for p in (parts or [s]):
        for drop in (True, False):
            v = _normalize(p, drop)
            if v:
                out.add(v)
    for drop in (True, False):
        v = _normalize(s, drop)
        if v:
            out.add(v)
    return out


def similarity(a, b):
    """Similarité 0-100 unifiée (match() et _score_entry la partagent).

    Prend le meilleur ratio (brut ou par mots triés) sur les variantes
    normalisées. token_sort tolère l'ordre des mots, jamais la présence de
    mots en trop : "Love" ne matche pas "Love Me Do".
    """
    va, vb = _variants(a), _variants(b)
    if not va or not vb:
        return 0.0
    fuzz = get_fuzz()
    best = 0.0
    for x in va:
        for y in vb:
            r = fuzz.ratio(x, y)
            t = fuzz.token_sort_ratio(x, y)
            if r > best:
                best = r
            if t > best:
                best = t
    return best


def match(a, b):
    return similarity(a, b) >= MATCH_THRESHOLD


def meets_threshold(ra, rt, delta):
    """Seuil à deux niveaux (phase 1.4).

    Standard : ra et rt >= 85. Repli durée : ra >= 85, rt >= 70 et écart de
    durée <= 2 s. Sans durée connue, le repli ne s'applique pas.
    """
    if ra >= MATCH_THRESHOLD and rt >= MATCH_THRESHOLD:
        return True
    if (ra >= MATCH_THRESHOLD and rt >= 70 and delta is not None
            and delta <= DURATION_TOL):
        return True
    return False


class LookupResult:
    """Résultat tri-état d'une résolution de paroles.

    FOUND        : un candidat valide a été retenu (lrc ou plain).
    NOT_FOUND    : toutes les requêtes ont abouti (200) sans candidat valide,
                   seul cas qui mérite d'être mis en cache comme "miss".
    INCONCLUSIVE : au moins une requête a échoué (réseau, 429 épuisé, budget,
                   exception) — rien n'est mis en cache, on retentera.
    """

    FOUND = "FOUND"
    NOT_FOUND = "NOT_FOUND"
    INCONCLUSIVE = "INCONCLUSIVE"

    def __init__(self, status, lrc=None, raw=None, inst=False, plain=None, src=None):
        self.status = status
        self.lrc = lrc
        self.raw = raw
        self.inst = inst
        self.plain = plain
        self.src = src


def last_timestamp(lrc):
    """Dernier timestamp [mm:ss(.xx)] ou <mm:ss.xx> d'un LRC, en secondes.

    Rend None si le texte est vide ou ne contient aucun timestamp.
    """
    if not lrc:
        return None
    last = None
    for m in re.finditer(r"[\[<](\d{1,4}):(\d{1,2})(?:[.:](\d{1,3}))?[\]>]", lrc):
        val = int(m.group(1)) * 60 + int(m.group(2))
        frac = m.group(3)
        if frac:
            val += int(frac) / (10 ** len(frac))
        if last is None or val > last:
            last = val
    return last

def strip_timestamps(lrc):
    lines = []
    for line in lrc.splitlines():
        line = TS_RE.sub("", line).strip()
        if line and not META_RE.match(line):
            lines.append(line)
    return "\n".join(lines).strip()

_LYRIC_TS_RE = re.compile(r"^\s*[<\[]\d{1,4}:\d{1,2}(?:[.:]\d{1,3})?[>\]]")
# [offset:] décale la synchro sans être une méta à jeter : on le conserve.
_OFFSET_RE = re.compile(r"^\s*\[offset:", re.I)
_MANUAL_META_RE = re.compile(r"^\s*\[(ar|ti|al|by|length|re|ve|tool):", re.I)


def parse_manual_lyrics(text, duration=None):
    """Analyse un LRC/texte collé manuellement.

    Rend {"kind": "lrc"|"plain"|"empty", "lrc", "plain", "warnings"}.
    LRC si au moins 50 % des lignes non vides hors méta portent un timestamp.
    """
    warnings = []
    if text is None:
        text = ""
    text = text.replace("\ufeff", "")
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return {"kind": "empty", "lrc": None, "plain": None, "warnings": warnings}

    lines = [ln for ln in text.split("\n") if ln.strip()]
    content = [ln for ln in lines
               if not _MANUAL_META_RE.match(ln) and not _OFFSET_RE.match(ln)]
    if not content:
        return {"kind": "empty", "lrc": None, "plain": None, "warnings": warnings}

    stamped = [ln for ln in content if _LYRIC_TS_RE.match(ln)]
    if len(stamped) * 2 >= len(content):
        lrc_lines = [ln for ln in lines if not _MANUAL_META_RE.match(ln)]
        lrc = "\n".join(lrc_lines).strip()
        last = last_timestamp(lrc)
        if duration and last is not None and last > duration + 3:
            warnings.append(
                f"dernier timestamp {last:.1f}s > durée {duration:.1f}s + 3s")
        return {"kind": "lrc", "lrc": lrc, "plain": strip_timestamps(lrc),
                "warnings": warnings}

    return {"kind": "plain", "lrc": None, "plain": text, "warnings": warnings}


def _ttml_time(value):
    """Convertit un temps TTML en secondes. None si le format est inconnu."""
    if not value:
        return None
    s = value.strip()
    if s.endswith("s"):
        s = s[:-1]
    try:
        if ":" in s:
            parts = s.split(":")
            if len(parts) == 3:
                h, m, sec = parts
                return int(h) * 3600 + int(m) * 60 + float(sec)
            if len(parts) == 2:
                m, sec = parts
                return int(m) * 60 + float(sec)
            return None
        return float(s)
    except ValueError:
        return None


def _fmt_lrc_time(seconds):
    """[mm:ss.xx], centièmes arrondis, minutes non plafonnées à 59."""
    total = int(round(seconds * 100))
    if total < 0:
        total = 0
    mm, rest = divmod(total, 6000)
    ss, cs = divmod(rest, 100)
    return f"[{mm:02d}:{ss:02d}.{cs:02d}]"


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def ttml_to_lrc(xml_text, duration=None):
    """Convertit un TTML (paroliers Apple/Bini) en LRC.

    Pure stdlib, sans dépendance. Rend None + log si le document est refusé
    (DOCTYPE/ENTITY, > 2 Mo, illisible, ou sans ligne exploitable).
    """
    warnings = []
    if not xml_text:
        return None
    if len(xml_text.encode("utf-8", "ignore")) > 2 * 1024 * 1024:
        warnings.append("document TTML > 2 Mo")
    if "<!DOCTYPE" in xml_text or "<!ENTITY" in xml_text:
        warnings.append("DOCTYPE/ENTITY interdit")
    if warnings:
        if _logger:
            _logger("[WARN] ttml_to_lrc : " + "; ".join(warnings))
        return None

    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        if _logger:
            _logger(f"[WARN] ttml_to_lrc : XML invalide ({e})")
        return None

    body_dur = None
    rows = []
    for el in root.iter():
        name = _local(el.tag)
        if name == "body" and body_dur is None:
            body_dur = _ttml_time(el.attrib.get("dur"))
        if name != "p":
            continue
        begin = _ttml_time(el.attrib.get("begin"))
        if begin is None:
            continue
        parts = [el.text] if el.text else []

        def walk(node):
            for child in node:
                role = None
                for k, v in child.attrib.items():
                    if _local(k) == "role":
                        role = v
                if role in ("x-bg", "x-translation", "x-roman"):
                    if child.tail:
                        parts.append(child.tail)
                    continue
                if child.text:
                    parts.append(child.text)
                walk(child)
                if child.tail:
                    parts.append(child.tail)

        walk(el)
        text = _WS_RE.sub(" ", "".join(p for p in parts if p)).strip()
        if text:
            rows.append((round(begin, 3), text))

    if not rows:
        return None
    rows.sort(key=lambda r: r[0])
    seen = set()
    out = []
    for begin, text in rows:
        key = (begin, text)
        if key in seen:
            continue
        seen.add(key)
        out.append(_fmt_lrc_time(begin) + text)
    if not out:
        return None
    lrc = "\n".join(out)
    last = last_timestamp(lrc)
    ref = duration if duration is not None else body_dur
    if ref and last is not None and last > ref + 3:
        if _logger:
            _logger(f"[WARN] ttml_to_lrc : dernier {last:.1f}s > durée {ref:.1f}s + 3s")
    return lrc


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
    global _cache_dirty, _dirty_count
    with _save_lock, _cache_lock:
        _cache.clear()
        _cache_dirty = False
        _dirty_count = 0
    try:
        os.remove(CACHE_FILE)
    except Exception:
        pass

_load_cache()