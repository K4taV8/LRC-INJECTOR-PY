"""Client réseau BiniLyrics (API JSON + TTML).

Module séparé et désactivé par défaut : l'application ne l'importe que si
l'utilisateur coche la case correspondante. Aucune dépendance nouvelle :
requests est déjà requis par l'application principale.

L'API expose une recherche (track/artist requis, duration/album optionnels,
isrc pour l'exact) puis un fichier TTML par candidat. Le TTML est converti en
LRC par core.ttml_to_lrc (pur, stdlib uniquement).
"""
import os
import time

from core import (DURATION_TOL, LookupResult, RateLimiter, last_timestamp,
                  meets_threshold, similarity, ttml_to_lrc)

BASE_URL = "https://lyrics-api.binimum.org/"
USER_AGENT = "lrc-injector/1.2 (https://github.com/K4taV8/LRC-INJECTOR-PY)"
TRANSIENT = (429, 500, 502, 503, 504)
_MAX_BYTES = 2 * 1024 * 1024
_MAX_ATTEMPTS = 3

_DEBUG = os.environ.get("LRC_DEBUG") == "1"


class BiniProvider:
    """Recherche + téléchargement de TTML, rate-limité à 2 req/s par défaut."""

    def __init__(self, session=None, rate=2.0, min_rate=2.0, log=None,
                 sleep=None, clock=None):
        self._session = session
        self._log = log or (lambda msg: None)
        self._sleep = sleep or time.sleep
        self._lim = RateLimiter(rate, min_rate=min_rate, clock=clock,
                                sleep=self._sleep)

    # ── infra ──
    def _dbg(self, msg):
        if _DEBUG:
            self._log(f"[DEBUG] bini: {msg}")

    def _sess(self):
        if self._session is None:
            import requests
            s = requests.Session()
            s.headers.update({"User-Agent": USER_AGENT})
            self._session = s
        return self._session

    def _retry_after(self, r):
        try:
            return max(0.0, float(r.headers.get("Retry-After", 2)))
        except (AttributeError, TypeError, ValueError):
            return 2.0

    def _get(self, url, params=None, timeout=None):
        r = None
        for _ in range(_MAX_ATTEMPTS):
            self._lim.acquire()
            r = self._sess().get(url, params=params, timeout=timeout)
            if getattr(r, "status_code", None) not in TRANSIENT:
                self._lim.on_success()
                return r
            self._lim.on_throttle(self._retry_after(r))
            self._sleep(self._retry_after(r))
        return r

    # ── sélection ──
    def _score(self, res, artist, title, duration, isrc):
        exact_isrc = bool(
            isrc and res.get("isrc")
            and str(res["isrc"]).strip().lower() == str(isrc).strip().lower())
        if not res.get("lyricsUrl"):
            return None
        ra = similarity(res.get("artist_name", ""), artist)
        rt = similarity(res.get("track_name", ""), title)
        remote = res.get("duration")
        delta = None
        if isinstance(duration, (int, float)) and isinstance(remote, (int, float)):
            delta = abs(remote - duration)
        if not exact_isrc and not meets_threshold(ra, rt, delta):
            return None
        return {"res": res, "ra": ra, "rt": rt, "delta": delta, "isrc": exact_isrc,
                "artist_name": res.get("artist_name"),
                "track_name": res.get("track_name"),
                "duration": remote, "lyricsUrl": res.get("lyricsUrl")}

    def _rank(self, c):
        far = 0 if (c["delta"] is not None and c["delta"] <= DURATION_TOL) else 1
        delta = c["delta"] if c["delta"] is not None else 0.0
        return (far, delta, 0 if c["isrc"] else 1, -(c["ra"] + c["rt"]))

    def _queries(self, artist, title, album, duration, isrc):
        queries = []
        if isrc:
            queries.append({"isrc": isrc})
        if artist and title:
            base = {"track": title, "artist": artist}
            if duration:
                base["duration"] = int(round(duration))
            if album:
                q = dict(base)
                q["album"] = album
                queries.append(q)
            queries.append(dict(base))
            queries.append({"track": title, "artist": artist})
        return queries

    # ── API ──
    def search(self, artist, title, album="", duration=None, isrc=None):
        """Rend un LookupResult tri-état. results vide n'est pas une erreur."""
        queries = self._queries(artist, title, album, duration, isrc)
        if not queries:
            return LookupResult(LookupResult.INCONCLUSIVE)
        failed = 0
        candidates = []
        for params in queries:
            try:
                r = self._get(BASE_URL, params=params, timeout=(3, 8))
            except Exception as e:
                failed += 1
                self._dbg(f"search {params} -> exception {e!r}")
                continue
            code = getattr(r, "status_code", None)
            self._dbg(f"search {params} -> HTTP {code}")
            if code != 200:
                if code in TRANSIENT:
                    failed += 1
                continue
            try:
                data = r.json()
            except Exception:
                failed += 1
                continue
            results = data.get("results") or []
            self._dbg(f"search {params} -> {len(results)} résultat(s)")
            for res in results:
                c = self._score(res, artist, title, duration, isrc)
                if c is not None:
                    candidates.append(c)

        if not candidates:
            if failed:
                return LookupResult(LookupResult.INCONCLUSIVE)
            return LookupResult(LookupResult.NOT_FOUND)

        for c in sorted(candidates, key=self._rank):
            lrc, outcome = self._ttml(c, duration)
            if outcome == "ok":
                raw = {"artistName": c["artist_name"], "trackName": c["track_name"],
                       "duration": c["duration"]}
                return LookupResult(LookupResult.FOUND, lrc=lrc, raw=raw,
                                    inst=False, src="bini")
            if outcome == "transient":
                failed += 1
        if failed:
            return LookupResult(LookupResult.INCONCLUSIVE)
        return LookupResult(LookupResult.NOT_FOUND)

    def _ttml(self, c, duration):
        """Télécharge et convertit le TTML. Rend (lrc, outcome).

        outcome ∈ ok / notfound / transient. 404 sur ce candidat -> notfound
        (on passe au suivant) ; échec réseau ou 429 -> transient (INCONCLUSIVE).
        """
        url = c.get("lyricsUrl") or ""
        if not url.lower().startswith("https://"):
            return None, "notfound"
        try:
            r = self._get(url, timeout=(3, 8))
        except Exception:
            return None, "transient"
        code = getattr(r, "status_code", None)
        if code == 404:
            return None, "notfound"
        if code in TRANSIENT:
            return None, "transient"
        if code != 200:
            return None, "notfound"
        final = str(getattr(r, "url", url) or url)
        if not final.lower().startswith("https://"):
            return None, "notfound"
        text = getattr(r, "text", "") or ""
        if len(text.encode("utf-8", "ignore")) > _MAX_BYTES:
            return None, "notfound"
        lrc = ttml_to_lrc(text, duration=duration)
        if not lrc:
            return None, "notfound"
        lt = last_timestamp(lrc)
        if duration and lt is not None and lt > duration + 3:
            self._dbg(f"TTML {url} rejeté : dernier ts {lt:.1f}s > {duration + 3:.1f}s")
            return None, "notfound"
        return lrc, "ok"
