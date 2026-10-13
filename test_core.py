"""Tests unitaires du noyau pur (core.py). Lancement : python test_core.py"""
import os
import sys
import json
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core


def _check(cond, msg):
    if not cond:
        raise AssertionError(msg)


def test_clean():
    _check(core.clean(None) == "", "None -> vide")
    _check(core.clean("  Queen  ") == "queen", "lower + strip")
    _check(core.clean("FT. Drake") == "drake", "suffixe ft. supprimé")
    _check(core.clean("Drake (remastered)") == "drake", "suffixe (remastered) supprimé")
    _check(core.clean("The After.Something") == "the after.something", "ft. pas tronqué en mot (BUG-006)")


def test_fold():
    _check(core.fold("héllo") == "hello", "accent é -> e")
    _check(core.fold("Ångström") == "Angstrom", "accent Å -> A")


def test_match():
    _check(core.match("Abba", "ABBA"), "casse différente")
    _check(core.match("Métallica", "Metallica"), "accent plié")
    _check(core.match("Drake ft. Future", "Drake - Future"), "suffixe ft. toléré")
    _check(core.match("Back in Black", "Black Back In"), "ordre des mots toléré (token_sort)")
    _check(not core.match("Coldplay", "Radiohead"), "pistes différentes")
    _check(not core.match("", ""), "deux vides -> faux (BUG-005)")
    _check(not core.match("Coldplay", ""), "côté API vide -> faux (BUG-005)")


def test_strip_timestamps():
    lrc = "[00:12.34]Première ligne\n[02:04.5]Deuxième ligne\n[ar:Editeur]\n"
    _check(core.strip_timestamps(lrc) == "Première ligne\nDeuxième ligne",
           "timestamps + ligne méta supprimés")
    _check(core.strip_timestamps("Sans timestamps") == "Sans timestamps", "texte brut conservé")
    _check(core.strip_timestamps("[00:01.00][00:05.50]Couplet") == "Couplet",
           "multi-timestamps sur une ligne")


def test_parse_result():
    _check(core._parse_result({"syncedLyrics": "a\nb", "plainLyrics": "p"}) == ("a\nb", False),
           "lrc str")
    _check(core._parse_result({"syncedLyrics": ["a", "b"]}) == ("a\nb", False), "lrc liste")
    _check(core._parse_result({"plainLyrics": "instrumental"})[1] is True, "instrumental plain")
    _check(core._parse_result({"instrumental": True})[1] is True, "flag instrumental")
    _check(core._parse_result({})[0] is None, "absence de paroles")


def test_cache_roundtrip():
    parent = tempfile.mkdtemp()
    old_file = core.CACHE_FILE
    core.CACHE_FILE = os.path.join(parent, "cache.json")
    try:
        core._cache.clear()
        core._dirty_count = 0
        core._cache["k1"] = {"lrc": "x"}
        core._mark_dirty()
        core._save_cache()
        with open(core.CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        _check(data.get("v") == 1, "schéma versionné")
        _check(data.get("entries") == {"k1": {"lrc": "x"}}, "roundtrip disque")
        core._cache.clear()
        core._load_cache()
        _check(core._cache == {"k1": {"lrc": "x"}}, "rechargement")
    finally:
        core.CACHE_FILE = old_file


def test_cache_legacy():
    parent = tempfile.mkdtemp()
    old_file = core.CACHE_FILE
    core.CACHE_FILE = os.path.join(parent, "cache.json")
    try:
        with open(core.CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump({"k1": {"lrc": "x"}}, f)
        core._cache.clear()
        core._load_cache()
        _check(core._cache == {"k1": {"lrc": "x"}}, "cache pré-v1 chargé (compat)")
    finally:
        core.CACHE_FILE = old_file


def test_cache_unknown_version():
    parent = tempfile.mkdtemp()
    old_file = core.CACHE_FILE
    core.CACHE_FILE = os.path.join(parent, "cache.json")
    try:
        with open(core.CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump({"v": 99, "entries": {"k1": {"lrc": "x"}}}, f)
        core._cache.clear()
        core._load_cache()
        _check(core._cache == {}, "schéma inconnu -> cache vide")
    finally:
        core.CACHE_FILE = old_file


def test_cache_key():
    _check(core.cache_key("  Queen ", "Radio Ga Ga", "") == "queen\x00radio ga ga\x00",
           "clé normalisée")


class _FakeResp:
    def __init__(self, data, status=200, headers=None):
        self._data = data
        self.status_code = status
        self.headers = headers or {}

    def json(self):
        return self._data


class _FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        return self.responses.pop(0)


class _RaiseAfterFirst:
    """Rend une réponse la première fois, puis lève (timeout réseau simulé)."""

    def __init__(self, first):
        self.first = first
        self.n = 0
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        self.n += 1
        if self.n == 1:
            return self.first
        raise RuntimeError("timeout simulé")


class _RaisingSession:
    def __init__(self):
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        raise RuntimeError("réseau indisponible")


def _fresh_cache(prefix):
    old_file = core.CACHE_FILE
    core.CACHE_FILE = os.path.join(tempfile.mkdtemp(), prefix + ".json")
    core._cache.clear()
    core._dirty_count = 0
    core._cache_dirty = False
    return old_file


def _restore_cache(old_file):
    core.CACHE_FILE = old_file
    core._cache.clear()
    core._dirty_count = 0
    core._cache_dirty = False
    # un test qui simule un 429 abaisse le débit global du rate limiter :
    # on repart d'un limiteur neuf pour que les tests suivants ne Crawlent pas
    app = sys.modules.get("lrc-inject")
    if app is not None:
        app._LIM = core.RateLimiter(app.RATE_MAX, min_rate=app.RATE_MIN)
        app._pause_until = 0.0


def test_fetch_lrc_mock_direct():
    import importlib
    app = importlib.import_module("lrc-inject")
    old_file = _fresh_cache("direct")
    try:
        sess = _FakeSession([_FakeResp({
            "syncedLyrics": "[00:00.50]Hello world", "plainLyrics": "Hello world",
            "artistName": "Queen", "trackName": "Radio Ga Ga", "instrumental": False})])
        app.get_session = lambda: sess
        lrc, raw, inst = app.fetch_lrc("Queen", "Radio Ga Ga", "")
        _check(lrc == "[00:00.50]Hello world", "paroles sync en requête directe")
        _check(inst is False, "piste non instrumentale")
        _check(len(sess.calls) == 1, "une seule requête (pas de repli)")
        k = core.cache_key("Queen", "Radio Ga Ga", "")
        _check(core._cache.get(k, {}).get("lrc") == "[00:00.50]Hello world", "entrée cache écrite")
    finally:
        _restore_cache(old_file)


def test_fetch_lrc_mock_fallback():
    import importlib
    app = importlib.import_module("lrc-inject")
    old_file = _fresh_cache("fall")
    try:
        sess = _FakeSession([
            _FakeResp({}, 404),
            _FakeResp([{"syncedLyrics": "[00:10.00]Fallback",
                        "plainLyrics": "Fallback",
                        "artistName": "Queen", "trackName": "Radio Ga Ga",
                        "instrumental": False}])])
        app.get_session = lambda: sess
        lrc, raw, inst = app.fetch_lrc("Queen", "Radio Ga Ga", "")
        _check(lrc == "[00:10.00]Fallback", "repli recherche utilisé")
        _check(sess.calls[1][0] == app.SEARCH_API, "2e requête sur /api/search")
    finally:
        _restore_cache(old_file)


def test_collect_flac_dedup():
    import importlib
    app = importlib.import_module("lrc-inject")
    parent = tempfile.mkdtemp()
    f1 = os.path.join(parent, "a.flac")
    with open(f1, "wb") as f:
        f.write(b"x")
    f2 = os.path.join(parent, "b.flac")
    try:
        os.link(f1, f2)
    except OSError:
        _check(True, "hardlinks indisponibles sur ce FS — scénario non testé")
        return
    got = app._collect_flac(parent)
    names = [os.path.basename(p) for p in got]
    _check(len(got) == 1, "2 chemins, 1 inode -> 1 seul fichier collecté")
    _check(names == ["a.flac"] or names == ["b.flac"], str(names))


def test_cache_concurrent():
    old_file = _fresh_cache("conc")
    errors = []

    def writer(i):
        try:
            for j in range(50):
                core._cache[f"k{i}-{j}"] = {"lrc": "x"}
                core._mark_dirty()
            core._save_cache()
        except Exception as e:
            errors.append(e)

    try:
        threads = [threading.Thread(target=writer, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        _check(not errors, f"écritures concurrentes sans erreur: {errors[:3]}")
        core._cache.clear()
        core._load_cache()
        _check(len(core._cache) == 400, "400 entrées persistées par les 8 threads")
    finally:
        _restore_cache(old_file)


def test_cache_corruption():
    parent = tempfile.mkdtemp()
    old_file = core.CACHE_FILE
    core.CACHE_FILE = os.path.join(parent, "cache.json")
    try:
        with open(core.CACHE_FILE, "w", encoding="utf-8") as f:
            f.write("pas du json {")
        core._cache.clear()
        core._load_cache()
        _check(core._cache == {}, "fichier corrompu -> cache vide")
    finally:
        core.CACHE_FILE = old_file


DAY = 86400


def test_build_params_drops_empty_album():
    params, dropped = core.build_get_params("Queen", "Radio Ga Ga", "", None)
    _check(params == {"artist_name": "Queen", "track_name": "Radio Ga Ga"},
           "album vide non envoyé (l'API rejette les params vides)")
    _check(dropped == [], "rien à signaler")


def test_build_params_keeps_in_range_duration():
    params, _ = core.build_get_params("Queen", "Radio Ga Ga", "News", 424.6)
    _check(params["duration"] == 425, "durée arrondie à l'entier, en secondes")


def test_build_params_drops_out_of_range_duration():
    for bad in (0, 4000, -5):
        params, dropped = core.build_get_params("Q", "T", "A", bad)
        _check("duration" not in params, f"duration={bad} hors [1,3600] non envoyé")
        _check(dropped == ["duration"], f"duration={bad} signalé")


def test_build_params_rejects_blank_signature():
    _check(core.build_get_params("  ", "T", "A", 100)[0] is None,
           "artiste vide -> requête invalide")
    _check(core.build_get_params("A", "", "A", 100)[0] is None,
           "titre vide -> requête invalide")


def test_entry_expired_hard_miss_uses_short_ttl():
    entry = {"lrc": None, "inst": False, "miss": True, "ts": 1000}
    _check(not core.entry_expired(entry, 1000 + 2 * DAY),
           "404cached depuis 2j -> pas encore expiré (TTL court)")
    _check(core.entry_expired(entry, 1000 + (core._MISS_TTL + DAY)),
           "404 cached au-delà du TTL court -> re-tenté")


def test_entry_expired_legacy_entry_migrates_to_miss():
    entry = {"lrc": None, "inst": False, "no_sync": True, "pl": None, "ts": 1000}
    _check(core.entry_expired(entry, 1000 + (core._MISS_TTL + DAY)),
           "cache v1 sans plainLyrics = 404 historique -> TTL court")


def test_entry_expired_found_unsynced_keeps_long_ttl():
    entry = {"lrc": None, "inst": False, "no_sync": True, "pl": "la la", "ts": 1000}
    _check(not core.entry_expired(entry, 1000 + (core._NO_SYNC_TTL - DAY)),
           "trouvé mais non synchronisé -> TTL long conservé")


def test_entry_expired_hit_never_expires():
    _check(not core.entry_expired({"lrc": "[00:00.00]x"}, 1000 + 3650 * DAY),
           "une réussite en cache n'expire jamais")


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


def _limiter(rate=25.0, min_rate=2.0):
    clk = _Clock()
    return core.RateLimiter(rate, min_rate=min_rate, clock=clk, sleep=clk.advance), clk


def test_rate_limiter_paces_requests():
    rl, clk = _limiter(rate=10.0)
    for _ in range(25):
        rl.acquire()
    _check(abs(clk.t - 1.5) < 0.05,
           f"25 requêtes à 10/s = 1.5s, mesuré {clk.t:.3f}s")


def test_rate_limiter_allows_burst_up_to_ceiling():
    rl, clk = _limiter(rate=10.0)
    for _ in range(10):
        rl.acquire()
    _check(clk.t == 0.0, "le plafond part immédiatement (burst = rate)")


def test_rate_limiter_halves_on_throttle_to_floor():
    rl, _ = _limiter(rate=25.0, min_rate=2.0)
    rl.on_throttle(retry_after=1)
    _check(rl.rate == 12.5, f"429 -> débit divisé par 2, obtenu {rl.rate}")
    for _ in range(10):
        rl.on_throttle(retry_after=1)
    _check(rl.rate == 2.0, f"plancher min_rate respecté, obtenu {rl.rate}")


def test_rate_limiter_recovers_after_success_streak():
    rl, _ = _limiter(rate=25.0, min_rate=2.0)
    rl.on_throttle(retry_after=1)
    throttled = rl.rate
    for _ in range(rl.recover_after):
        rl.on_success()
    _check(rl.rate > throttled, "remontée progressive après une rafale de succès")
    for _ in range(500):
        rl.on_success()
    _check(rl.rate == 25.0, f"retour au plafond, jamais au-delà (obtenu {rl.rate})")


def test_rate_limiter_ceiling_follows_slider():
    rl, _ = _limiter(rate=25.0)
    rl.set_ceiling(8.0)
    _check(rl.rate == 8.0, f"le curseur abaisse le débit effectif, obtenu {rl.rate}")


def test_fetch_lrc_400_does_not_fall_back_to_search():
    import importlib
    app = importlib.import_module("lrc-inject")
    old_file = _fresh_cache("bad400")
    try:
        sess = _FakeSession([_FakeResp({}, 400)])
        app.get_session = lambda: sess
        app.fetch_lrc("Queen", "Radio Ga Ga", "")
        _check(len(sess.calls) == 1,
               f"400 = requête invalide, aucun repli search (appels={len(sess.calls)})")
        _check(core._cache == {}, f"un 400 ne pollue pas le cache ({core._cache})")
    finally:
        _restore_cache(old_file)


def test_fetch_lrc_429_is_transient_and_uncached():
    import importlib
    app = importlib.import_module("lrc-inject")
    old_file = _fresh_cache("throttled")
    try:
        sess = _FakeSession([_FakeResp({}, 429, {"Retry-After": "0"}) for _ in range(4)])
        app.get_session = lambda: sess
        app.fetch_lrc("Queen", "Radio Ga Ga", "")
        _check(core._cache == {}, "429 transient -> aucune entrée négative en cache")
        _check(all(app.SEARCH_API not in c[0] for c in sess.calls),
               "429 -> pas de repli search qui adds de la charge")
    finally:
        _restore_cache(old_file)


def test_fetch_lrc_404_is_cached_as_miss():
    import importlib
    app = importlib.import_module("lrc-inject")
    old_file = _fresh_cache("miss404")
    try:
        sess = _FakeSession([_FakeResp({}, 404)] + [_FakeResp([], 200) for _ in range(4)])
        app.get_session = lambda: sess
        app.fetch_lrc("Nobody", "Nothing", "")
        entry = core._cache.get(core.cache_key("Nobody", "Nothing", ""), {})
        _check(entry.get("miss") is True, f"un 404 est caché comme miss (entrée={entry})")
        _check(not entry.get("no_sync"), "et non comme no_sync, pour prendre le TTL court")
    finally:
        _restore_cache(old_file)


def test_search_fallback_prefers_duration_match():
    import importlib
    app = importlib.import_module("lrc-inject")
    old_file = _fresh_cache("durationscore")
    try:
        sess = _FakeSession([_FakeResp([
            {"trackName": "Radio Ga Ga", "artistName": "Queen", "albumName": "News",
             "duration": 426.0, "syncedLyrics": "[00:00.00]version longue",
             "plainLyrics": "a", "instrumental": False},
            {"trackName": "Radio Ga Ga", "artistName": "Queen", "albumName": "News (Deluxe)",
             "duration": 177.0, "syncedLyrics": "[00:00.00]version courte",
             "plainLyrics": "b", "instrumental": False},
        ])] + [_FakeResp([], 200) for _ in range(3)])
        app.get_session = lambda: sess
        res = app._search_fallback("Queen", "Radio Ga Ga", "k", 177.4)
        _check(res.lrc == "[00:00.00]version courte",
               f"la durée départage les versions (obtenu {res.lrc!r})")
    finally:
        _restore_cache(old_file)


def test_search_fallback_uses_structured_query():
    import importlib
    app = importlib.import_module("lrc-inject")
    old_file = _fresh_cache("structured")
    try:
        sess = _FakeSession([_FakeResp([]), _FakeResp([]), _FakeResp([]), _FakeResp([])])
        app.get_session = lambda: sess
        app._search_fallback("Queen", "Radio Ga Ga", "k", 177)
        params = [p for _, p in sess.calls]
        _check(any("track_name" in p and "artist_name" in p for p in params),
               "la recherche structurée track_name+artist_name est utilisée")
    finally:
        _restore_cache(old_file)


def test_fetch_lrc_invalid_signature_makes_no_call():
    import importlib
    app = importlib.import_module("lrc-inject")
    old_file = _fresh_cache("badsig")
    try:
        sess = _FakeSession([])
        app.get_session = lambda: sess
        app.fetch_lrc("", "Titre", "", 200)
        _check(len(sess.calls) == 0,
               f"signature inexploitable -> aucun appel HTTP (appels={len(sess.calls)})")
        _check(core._cache == {}, "et rien mis en cache")
    finally:
        _restore_cache(old_file)


def test_fetch_lrc_invalid_signature_is_not_counted_as_api_call():
    import importlib
    app = importlib.import_module("lrc-inject")
    old_file = _fresh_cache("badsig2")
    try:
        sess = _FakeSession([])
        app.get_session = lambda: sess
        before = app._cache_misses
        app.fetch_lrc("", "Titre", "", 200)
        _check(app._cache_misses == before,
               "un refus local n'est pas un appel API et ne doit pas gonfler le stat")
    finally:
        _restore_cache(old_file)


def test_score_entry_two_tier_duration_rule():
    import importlib
    app = importlib.import_module("lrc-inject")
    e = {"artistName": "A", "trackName": "abce", "duration": 100.0}
    _check(app._score_entry(e, "A", "abcd", 100.0) is not None,
           "rt ~75 accepté si écart de durée <= 2 s")
    _check(app._score_entry(e, "A", "abcd", 120.0) is None,
           "rt ~75 refusé si écart de durée > 2 s")
    e2 = {"artistName": "A", "trackName": "abcd", "duration": 100.0}
    _check(app._score_entry(e2, "A", "abcd", 140.0) is not None,
           "rt = 100 -> seuil standard, durée ignorée")


def test_lookup_all_queries_200_empty_is_cached_miss():
    import importlib
    app = importlib.import_module("lrc-inject")
    old_file = _fresh_cache("all200")
    try:
        sess = _FakeSession([_FakeResp({}, 404)] + [_FakeResp([], 200) for _ in range(4)])
        app.get_session = lambda: sess
        res = app.fetch_lrc_ex("Nobody", "Nothing", "")
        _check(res.status == core.LookupResult.NOT_FOUND, f"statut {res.status}")
        entry = core._cache.get(core.cache_key("Nobody", "Nothing", ""), {})
        _check(entry.get("miss") is True, f"un 404 + 200 vides = miss caché ({entry})")
    finally:
        _restore_cache(old_file)


def test_lookup_exception_on_search_is_inconclusive_uncached():
    import importlib
    app = importlib.import_module("lrc-inject")
    old_file = _fresh_cache("exceptsearch")
    try:
        app.get_session = lambda: _RaiseAfterFirst(_FakeResp({}, 404))
        res = app.fetch_lrc_ex("Nobody", "Nothing", "")
        _check(res.status == core.LookupResult.INCONCLUSIVE, f"statut {res.status}")
        _check(core._cache == {}, f"une exception ne cache rien ({core._cache})")
    finally:
        _restore_cache(old_file)


def test_lookup_429_on_search_is_inconclusive_uncached():
    import importlib
    app = importlib.import_module("lrc-inject")
    old_file = _fresh_cache("throttledsearch")
    try:
        sess = _FakeSession([_FakeResp({}, 404)]
                            + [_FakeResp({}, 429, {"Retry-After": "0"}) for _ in range(12)])
        app.get_session = lambda: sess
        res = app.fetch_lrc_ex("Nobody", "Nothing", "")
        _check(res.status == core.LookupResult.INCONCLUSIVE, f"statut {res.status}")
        _check(core._cache == {}, f"un 429 ne cache rien ({core._cache})")
    finally:
        _restore_cache(old_file)


def test_lookup_exception_on_get_is_inconclusive_uncached():
    import importlib
    app = importlib.import_module("lrc-inject")
    old_file = _fresh_cache("exceptget")
    try:
        app.get_session = lambda: _RaisingSession()
        res = app.fetch_lrc_ex("Queen", "Radio Ga Ga", "")
        _check(res.status == core.LookupResult.INCONCLUSIVE, f"statut {res.status}")
        _check(core._cache == {}, "échec /api/get -> rien en cache")
    finally:
        _restore_cache(old_file)


def test_lookup_invalid_signature_is_inconclusive():
    import importlib
    app = importlib.import_module("lrc-inject")
    old_file = _fresh_cache("badsigex")
    try:
        app.get_session = lambda: _FakeSession([])
        res = app.fetch_lrc_ex("", "Titre", "", 200)
        _check(res.status == core.LookupResult.INCONCLUSIVE, f"statut {res.status}")
        _check(core._cache == {}, "rien en cache")
    finally:
        _restore_cache(old_file)


def test_cascade_get_without_album():
    import importlib
    app = importlib.import_module("lrc-inject")
    old_file = _fresh_cache("cascade")
    try:
        sess = _FakeSession([
            _FakeResp({}, 404),
            _FakeResp({"syncedLyrics": "[00:00.00]x", "plainLyrics": "x",
                       "artistName": "Queen", "trackName": "Radio Ga Ga",
                       "instrumental": False}),
        ])
        app.get_session = lambda: sess
        res = app.fetch_lrc_ex("Queen", "Radio Ga Ga", "The Album", 177)
        _check(res.lrc == "[00:00.00]x", f"repli get sans album ({res.lrc!r})")
        _check(sess.calls[1][0] == app.LRCLIB_API and "album_name" not in sess.calls[1][1],
               "2e appel /api/get sans album_name, durée conservée")
        _check(sess.calls[1][1].get("duration") == 177, "durée conservée dans le repli")
    finally:
        _restore_cache(old_file)


def test_cascade_not_triggered_when_album_empty():
    import importlib
    app = importlib.import_module("lrc-inject")
    old_file = _fresh_cache("noalbum")
    try:
        sess = _FakeSession([_FakeResp({}, 404)] + [_FakeResp([], 200) for _ in range(4)])
        app.get_session = lambda: sess
        app.fetch_lrc_ex("Queen", "Radio Ga Ga", "")
        get_calls = [c for c in sess.calls if c[0] == app.LRCLIB_API]
        _check(len(get_calls) == 1,
               f"album vide -> pas de repli get sans album ({len(get_calls)} appels)")
    finally:
        _restore_cache(old_file)


def test_get_plain_only_prefers_synced_search():
    import importlib
    app = importlib.import_module("lrc-inject")
    old_file = _fresh_cache("plainpref")
    try:
        sess = _FakeSession([
            _FakeResp({"plainLyrics": "plain text", "artistName": "Queen",
                       "trackName": "Radio Ga Ga"}),
            _FakeResp([{"syncedLyrics": "[00:00.00]sync", "plainLyrics": "sync",
                        "artistName": "Queen", "trackName": "Radio Ga Ga",
                        "duration": 177, "instrumental": False}]),
            _FakeResp([], 200), _FakeResp([], 200),
        ])
        app.get_session = lambda: sess
        res = app.fetch_lrc_ex("Queen", "Radio Ga Ga", "", 177)
        _check(res.lrc == "[00:00.00]sync",
               f"version synchronisée préférée au plain ({res.lrc!r})")
    finally:
        _restore_cache(old_file)


def test_get_plain_only_falls_back_to_plain():
    import importlib
    app = importlib.import_module("lrc-inject")
    old_file = _fresh_cache("plainfall")
    try:
        sess = _FakeSession([_FakeResp({"plainLyrics": "plain text",
                                        "artistName": "Queen", "trackName": "Radio Ga Ga"})]
                            + [_FakeResp([], 200) for _ in range(4)])
        app.get_session = lambda: sess
        res = app.fetch_lrc_ex("Queen", "Radio Ga Ga", "", 177)
        _check(res.lrc is None and res.plain == "plain text",
               "plain conservé si aucune version synchronisée")
        entry = core._cache.get(core.cache_key("Queen", "Radio Ga Ga", ""), {})
        _check(entry.get("no_sync") is True and entry.get("ts"),
               f"entrée no_sync horodatée ({entry})")
    finally:
        _restore_cache(old_file)


def test_search_rejects_lrc_past_local_duration():
    import importlib
    app = importlib.import_module("lrc-inject")
    old_file = _fresh_cache("toolong")
    try:
        sess = _FakeSession([_FakeResp([{
            "syncedLyrics": "[00:00.00]a\n[05:00.00]b", "plainLyrics": "p",
            "artistName": "Queen", "trackName": "Radio Ga Ga",
            "duration": 100.0, "instrumental": False}])]
            + [_FakeResp([], 200) for _ in range(3)])
        app.get_session = lambda: sess
        res = app._search_fallback("Queen", "Radio Ga Ga", "k", 100)
        _check(res.status == core.LookupResult.NOT_FOUND,
               f"un LRC dont le dernier timestamp dépasse durée+3s est écarté ({res.status})")
        _check(res.lrc is None, "aucun lrc retenu")
    finally:
        _restore_cache(old_file)


class _BiniResp:
    def __init__(self, data=None, status=200, headers=None, text="", url=None):
        self._data = data if data is not None else {}
        self.status_code = status
        self.headers = headers or {}
        self.text = text
        self.url = url

    def json(self):
        return self._data


class _FakeBini:
    def __init__(self, result, calls=None):
        self.result = result
        self.calls = calls if calls is not None else []

    def search(self, artist, title, album="", duration=None, isrc=None):
        self.calls.append({"artist": artist, "title": title, "album": album,
                           "duration": duration, "isrc": isrc})
        return self.result


def test_bini_not_called_on_inconclusive():
    import importlib
    app = importlib.import_module("lrc-inject")
    old_file = _fresh_cache("biniinc")
    try:
        app.get_session = lambda: _RaisingSession()
        fake = _FakeBini(core.LookupResult(core.LookupResult.FOUND, lrc="[00:00.00]x"))
        app._get_bini_provider = lambda: fake
        res = app.fetch_lrc_ex("Queen", "Radio Ga Ga", "", 177, bini=True)
        _check(res.status == core.LookupResult.INCONCLUSIVE, f"statut {res.status}")
        _check(not fake.calls, "Bini jamais appelé sur INCONCLUSIVE")
    finally:
        _restore_cache(old_file)


def test_bini_called_on_not_found_with_isrc():
    import importlib
    app = importlib.import_module("lrc-inject")
    old_file = _fresh_cache("bininf")
    try:
        sess = _FakeSession([_FakeResp({}, 404)] + [_FakeResp([], 200) for _ in range(4)])
        app.get_session = lambda: sess
        fake = _FakeBini(core.LookupResult(core.LookupResult.FOUND, lrc="[00:00.00]b",
                                           raw={"artistName": "Queen", "trackName": "Radio Ga Ga"}))
        app._get_bini_provider = lambda: fake
        res = app.fetch_lrc_ex("Queen", "Radio Ga Ga", "", 177, bini=True, isrc="US123")
        _check(res.lrc == "[00:00.00]b", f"Bini appelé sur NOT_FOUND ({res.lrc!r})")
        _check(fake.calls and fake.calls[0]["isrc"] == "US123", "isrc transmis à Bini")
        entry = core._cache.get("bini:" + core.cache_key("Queen", "Radio Ga Ga", ""), {})
        _check(entry.get("lrc") == "[00:00.00]b", "résultat Bini caché sous clé bini:")
        _check(entry.get("src") == "bini", "source bini mémorisée")
    finally:
        _restore_cache(old_file)


def test_bini_called_on_plain_only_and_preferred():
    import importlib
    app = importlib.import_module("lrc-inject")
    old_file = _fresh_cache("biniplain")
    try:
        sess = _FakeSession([_FakeResp({"plainLyrics": "pl", "artistName": "Queen",
                                        "trackName": "Radio Ga Ga"})]
                            + [_FakeResp([], 200) for _ in range(4)])
        app.get_session = lambda: sess
        fake = _FakeBini(core.LookupResult(core.LookupResult.FOUND, lrc="[00:00.00]b",
                                           raw={"artistName": "Queen", "trackName": "Radio Ga Ga"}))
        app._get_bini_provider = lambda: fake
        res = app.fetch_lrc_ex("Queen", "Radio Ga Ga", "", 177, bini=True)
        _check(fake.calls, "Bini appelé sur plain-only")
        _check(res.lrc == "[00:00.00]b", "version synchronisée Bini préférée au plain")
    finally:
        _restore_cache(old_file)


def test_bini_not_called_when_synced_found():
    import importlib
    app = importlib.import_module("lrc-inject")
    old_file = _fresh_cache("binisync")
    try:
        sess = _FakeSession([_FakeResp({"syncedLyrics": "[00:00.00]x", "plainLyrics": "x",
                                        "artistName": "Queen", "trackName": "Radio Ga Ga"})])
        app.get_session = lambda: sess
        fake = _FakeBini(core.LookupResult(core.LookupResult.FOUND, lrc="[00:00.00]y"))
        app._get_bini_provider = lambda: fake
        res = app.fetch_lrc_ex("Queen", "Radio Ga Ga", "", 177, bini=True)
        _check(res.lrc == "[00:00.00]x" and not fake.calls,
               "Bini non appelé quand LRCLIB trouve du synchronisé")
    finally:
        _restore_cache(old_file)


def test_bini_no_cache_entry_on_transient():
    import importlib
    app = importlib.import_module("lrc-inject")
    old_file = _fresh_cache("binitrans")
    try:
        sess = _FakeSession([_FakeResp({}, 404)] + [_FakeResp([], 200) for _ in range(4)])
        app.get_session = lambda: sess
        fake = _FakeBini(core.LookupResult(core.LookupResult.INCONCLUSIVE))
        app._get_bini_provider = lambda: fake
        app.fetch_lrc_ex("Queen", "Radio Ga Ga", "", 177, bini=True)
        bkey = "bini:" + core.cache_key("Queen", "Radio Ga Ga", "")
        _check(bkey not in core._cache, "aucune entrée bini: sur erreur transitoire")
    finally:
        _restore_cache(old_file)


def test_bini_queries_put_isrc_first():
    import importlib
    from providers.bini import BiniProvider
    _ = importlib.import_module("lrc-inject")
    prov = BiniProvider(session=_FakeSession([]))
    queries = prov._queries("Artist", "Title", "Album", 100, "US1")
    _check(list(queries[0].keys()) == ["isrc"], f"isrc d'abord ({queries[0]})")
    _check(queries[1].get("album") == "Album" and queries[1].get("duration") == 100,
           f"puis track+artist+duration+album ({queries[1]})")
    _check("album" not in queries[2] and queries[2].get("duration") == 100,
           f"puis track+artist+duration ({queries[2]})")
    _check("duration" not in queries[3] and "album" not in queries[3],
           f"enfin track+artist ({queries[3]})")


def test_bini_isrc_exact_accepts_divergent_title_within_duration():
    from providers.bini import BiniProvider
    ttml = ('<tt xmlns="http://www.w3.org/ns/ttml"><body><div>'
            '<p begin="0">Hi</p><p begin="90">Bye</p></div></body></tt>')
    sess = _FakeSession([
        _BiniResp({"total": 1, "results": [{
            "id": 1, "track_name": "Totally Different", "artist_name": "Someone Else",
            "album_name": "", "duration": 100.0, "isrc": "US123",
            "timing_type": "line", "lyricsUrl": "https://cdn.example/1.ttml"}]}),
        _BiniResp({"total": 0, "results": []}),
        _BiniResp({"total": 0, "results": []}),
        _BiniResp(status=200, text=ttml, url="https://cdn.example/1.ttml"),
    ])
    clk = _Clock()
    prov = BiniProvider(session=sess, clock=clk, sleep=clk.advance)
    res = prov.search("Artist", "Title", "", 100, isrc="US123")
    _check(res.status == core.LookupResult.FOUND and res.lrc is not None,
           f"ISRC exact accepté malgré un titre divergent ({res.status})")
    _check("[01:30.00]Bye" in res.lrc, f"TTML converti ({res.lrc!r})")


def test_bini_isrc_exact_rejected_when_past_duration():
    from providers.bini import BiniProvider
    ttml = ('<tt xmlns="http://www.w3.org/ns/ttml"><body><div>'
            '<p begin="0">Hi</p><p begin="200">Way too late</p></div></body></tt>')
    sess = _FakeSession([
        _BiniResp({"total": 1, "results": [{
            "id": 1, "track_name": "Totally Different", "artist_name": "Someone Else",
            "album_name": "", "duration": 100.0, "isrc": "US123",
            "timing_type": "line", "lyricsUrl": "https://cdn.example/1.ttml"}]}),
        _BiniResp({"total": 0, "results": []}),
        _BiniResp({"total": 0, "results": []}),
        _BiniResp(status=200, text=ttml, url="https://cdn.example/1.ttml"),
    ])
    clk = _Clock()
    prov = BiniProvider(session=sess, clock=clk, sleep=clk.advance)
    res = prov.search("Artist", "Title", "", 100, isrc="US123")
    _check(res.status == core.LookupResult.NOT_FOUND,
           f"dernier timestamp > durée+3s -> candidat écarté ({res.status})")


def test_bini_empty_results_is_not_error():
    from providers.bini import BiniProvider
    sess = _FakeSession([_BiniResp({"total": 0, "results": []})
                         for _ in range(4)])
    clk = _Clock()
    prov = BiniProvider(session=sess, clock=clk, sleep=clk.advance)
    res = prov.search("Artist", "Title", "", 100, isrc="US1")
    _check(res.status == core.LookupResult.NOT_FOUND,
           f"results vide = NOT_FOUND, pas une erreur ({res.status})")


def test_scaled_size_is_identity_at_100_percent():
    _check(core.scaled_size(852, 952, 1.0) == (852, 952),
           "DPI 100% -> pas de conversion")


def test_scaled_size_converts_physical_pixels_to_tk_units():
    _check(core.scaled_size(852, 952, 1.25) == (682, 762),
           f"125% -> 682x762 (obtenu {core.scaled_size(852, 952, 1.25)})")


def test_scaled_size_is_never_below_minimum():
    _check(core.scaled_size(852, 952, 4.0) == (213, 238),
           f"un DPI très élevé rétrécit sans casser (obtenu {core.scaled_size(852, 952, 4.0)})")


def test_scaled_size_never_exceeds_work_area():
    _check(core.scaled_size(4000, 3000, 1.0, max_w=1920, max_h=1040) == (1920, 1040),
           "borné par la zone de travail pour ne pas déborder de l'écran")


def _client_center(gx, gy, w, h, scale, border_left=0, border_top=0):
    """Centre de la zone client, en pixels reels, d'apres une geometry()."""
    return (gx + border_left + w * scale / 2, gy + border_top + h * scale / 2)


def test_opening_geometry_centers_client_area_at_125_percent():
    w, h, gx, gy = core.opening_geometry(852, 952, 1920, 1080, 1.25,
                                         border_left=9, border_top=38)
    _check((w, h) == (682, 762), f"taille en unites Tk (obtenu {w}x{h})")
    cx, cy = _client_center(gx, gy, w, h, 1.25, 9, 38)
    _check(abs(cx - 960) <= 1 and abs(cy - 540) <= 1,
           f"zone client centree sur l'ecran (obtenu {cx:.0f},{cy:.0f} pour 960,540)")


def test_opening_geometry_centers_at_100_percent():
    got = core.opening_geometry(852, 952, 1920, 1080, 1.0)
    _check(got == (852, 952, 534, 64), f"DPI 100%, pixels = unites (obtenu {got})")


def test_opening_geometry_fits_entirely_on_screen():
    w, h, gx, gy = core.opening_geometry(852, 952, 1920, 1080, 1.25)
    _check(gx >= 0 and gy >= 0, "jamais de coordonnee negative")
    _check(gx + w * 1.25 <= 1920 and gy + h * 1.25 <= 1080,
           f"fenetre entierement visible : {gx + w * 1.25:.0f}<=1920, {gy + h * 1.25:.0f}<=1080")


def test_opening_geometry_clamps_window_larger_than_screen():
    w, h, gx, gy = core.opening_geometry(4000, 3000, 1920, 1080, 1.25)
    _check(w * 1.25 <= 1920 and h * 1.25 <= 1080,
           f"fenetre bornee a l'ecran (obtenu {w}x{h})")
    _check(gx >= 0 and gy >= 0, "et position non negative")


def test_resolve_asset_returns_path_when_present():
    got = core.resolve_asset(os.path.dirname(os.path.abspath(core.__file__)), "Logo.png")
    _check(got is not None and os.path.isfile(got), "logo présent -> chemin renvoyé")


def test_resolve_asset_returns_none_when_missing():
    got = core.resolve_asset(os.path.dirname(os.path.abspath(core.__file__)),
                             "un_fichier_qui_nexiste_pas.png")
    _check(got is None, "asset absent -> None, l'app doit demarrer quand meme")


def test_resolve_asset_ignores_directories():
    _check(core.resolve_asset(os.path.dirname(os.path.abspath(core.__file__)), "__pycache__") is None,
           "un dossier n'est pas un asset")


def test_similarity_ignores_non_significant_markers():
    _check(core.similarity("Moana (Explicit)", "Moana") >= core.MATCH_THRESHOLD,
           "marqueur (Explicit) non significatif")
    _check(core.similarity("Song - Remastered 2012", "Song") >= core.MATCH_THRESHOLD,
           "suffixe - Remastered 2012 ignoré")
    _check(core.similarity("A; B", "A") >= core.MATCH_THRESHOLD,
           "meilleur appariement d'artistes séparés")
    _check(core.similarity("Rock & Roll", "Rock and Roll") >= core.MATCH_THRESHOLD,
           "& équivaut à and")


def test_similarity_keeps_significant_version_markers():
    _check(core.similarity("Song (Live)", "Song") < core.MATCH_THRESHOLD,
           "live est un marqueur significatif")
    _check(core.similarity("Song (Acoustic)", "Song (Live)") < core.MATCH_THRESHOLD,
           "acoustic != live")
    _check(core.similarity("Love", "Love Me Do") < core.MATCH_THRESHOLD,
           "token_set interdit sur les titres")


def test_meets_threshold_two_tiers():
    _check(core.meets_threshold(90, 90, 10.0), "standard : ra/rt >= 85, durée ignorée")
    _check(core.meets_threshold(90, 75, 1.5), "durée : rt 75 accepté si delta <= 2s")
    _check(not core.meets_threshold(90, 75, 5.0), "durée : rt 75 refusé si delta > 2s")
    _check(not core.meets_threshold(80, 95, 0.0), "ra < 85 refusé")
    _check(not core.meets_threshold(90, 75, None), "rt 75 sans durée refusé")


def test_last_timestamp():
    lrc = "[00:12.34]a\n[01:02.00]b\n[ar:Editeur]"
    _check(abs(core.last_timestamp(lrc) - 62.0) < 1e-6, f"dernier = 62s")
    _check(core.last_timestamp("") is None, "vide -> None")
    _check(core.last_timestamp("pas de timestamp") is None, "aucun timestamp -> None")


def test_entry_expired_no_sync_uses_seven_days():
    entry = {"lrc": None, "inst": False, "no_sync": True, "pl": "la", "ts": 1000}
    _check(not core.entry_expired(entry, 1000 + 6 * DAY), "6j -> encore valide")
    _check(core.entry_expired(entry, 1000 + 8 * DAY), "8j -> expiré (TTL 7j)")


def test_parse_manual_lyrics_lrc():
    txt = "[ar:Ed]\n[ti:T]\n[offset:250]\n[00:01.00]Ligne un\n[00:15.50]Ligne deux\n"
    r = core.parse_manual_lyrics(txt)
    _check(r["kind"] == "lrc", f"détecté LRC ({r['kind']})")
    _check("[offset:250]" in r["lrc"], "offset conservé")
    _check("[ar:Ed]" not in r["lrc"] and "[ti:T]" not in r["lrc"], "méta supprimée")
    _check(r["plain"] == "Ligne un\nLigne deux", f"plain dérivé ({r['plain']!r})")


def test_parse_manual_lyrics_plain():
    r = core.parse_manual_lyrics("Juste du texte\nsur deux lignes")
    _check(r["kind"] == "plain", "texte sans timestamps -> plain")
    _check(r["lrc"] is None, "pas de lrc")
    _check("Juste du texte" in r["plain"], "plain conservé")


def test_parse_manual_lyrics_empty():
    for t in (None, "", "   \n  "):
        r = core.parse_manual_lyrics(t)
        _check(r["kind"] == "empty", f"{t!r} -> empty")
        _check(r["lrc"] is None and r["plain"] is None, "rien à injecter")


def test_parse_manual_lyrics_crlf_bom_and_enhanced():
    r = core.parse_manual_lyrics("\ufeff[00:01.00]A\r\n[00:02.00]B\r\n")
    _check(r["kind"] == "lrc" and "\r" not in r["lrc"], "CRLF + BOM normalisés")
    r2 = core.parse_manual_lyrics("<00:01.00>Bonjour\n<00:05.25>Monde")
    _check(r2["kind"] == "lrc", "timestamps <mm:ss.xx> -> LRC")
    _check(r2["plain"] == "Bonjour\nMonde", f"plain dérivé ({r2['plain']!r})")


def test_parse_manual_lyrics_warns_past_duration():
    r = core.parse_manual_lyrics("[00:01.00]A\n[09:00.00]Trop loin", duration=100)
    _check(r["warnings"], "warning si dernier timestamp > durée + 3s")


def test_ttml_to_lrc_line_level():
    xml = ('<tt xmlns="http://www.w3.org/ns/ttml"><body><div>'
           '<p begin="1.0">Hello</p><p begin="2.5">World</p></div></body></tt>')
    lrc = core.ttml_to_lrc(xml)
    _check(lrc is not None and "[00:01.00]Hello" in lrc and "[00:02.50]World" in lrc,
           f"lrc={lrc!r}")


def test_ttml_to_lrc_syllable_level_has_no_extra_spaces():
    xml = ('<tt xmlns="http://www.w3.org/ns/ttml"><body><div>'
           '<p begin="0"><span begin="0">Ba</span><span begin="0.2">by</span></p>'
           '</div></body></tt>')
    lrc = core.ttml_to_lrc(xml)
    _check(lrc is not None and "Baby" in lrc, f"syllabes collées ({lrc!r})")


def test_ttml_to_lrc_ignores_background_voice():
    xml = ('<tt xmlns="http://www.w3.org/ns/ttml" '
           'xmlns:ttm="http://www.w3.org/ns/ttml#metadata">'
           '<body><div><p begin="1"><span>Main</span>'
           '<span ttm:role="x-bg">bg voice</span></p></div></body></tt>')
    lrc = core.ttml_to_lrc(xml)
    _check(lrc is not None and "bg voice" not in lrc and "Main" in lrc,
           f"voix de fond ignorée ({lrc!r})")


def test_ttml_to_lrc_time_formats():
    xml = ('<tt xmlns="http://www.w3.org/ns/ttml"><body><div>'
           '<p begin="3.465">A</p><p begin="1:02.500">B</p>'
           '<p begin="1:00:00.000">C</p></div></body></tt>')
    lrc = core.ttml_to_lrc(xml)
    _check(lrc is not None, "formats acceptés")
    _check("[00:03.46]A" in lrc, f"secondes décimales ({lrc!r})")
    _check("[01:02.50]B" in lrc, f"m:ss.mmm ({lrc!r})")
    _check("[60:00.00]C" in lrc, f"h:mm:ss, minutes non plafonnées ({lrc!r})")


def test_ttml_to_lrc_sorts_and_dedupes():
    xml = ('<tt xmlns="http://www.w3.org/ns/ttml"><body><div>'
           '<p begin="5.0">second</p><p begin="2.0">first</p>'
           '<p begin="2.0">first</p></div></body></tt>')
    lrc = core.ttml_to_lrc(xml)
    _check(lrc.index("first") < lrc.index("second"), f"trié par begin ({lrc!r})")
    _check(lrc.count("first") == 1, f"doublon (begin,texte) supprimé ({lrc!r})")


def test_ttml_to_lrc_empty_and_invalid():
    _check(core.ttml_to_lrc("") is None, "vide -> None")
    _check(core.ttml_to_lrc('<tt xmlns="http://www.w3.org/ns/ttml"><body/></tt>') is None,
           "aucune ligne -> None")
    xml = '<?xml?><!DOCTYPE tt [<!ENTITY x "y">]><tt><body><p begin="1">A</p></body></tt>'
    _check(core.ttml_to_lrc(xml) is None, "DOCTYPE/ENTITY rejeté")
    big = "x" * (2 * 1024 * 1024 + 10)
    _check(core.ttml_to_lrc(big) is None, "document > 2 Mo rejeté")


def main():
    tests = [test_clean, test_fold, test_match, test_strip_timestamps,
             test_parse_result, test_cache_key, test_cache_roundtrip,
             test_cache_legacy, test_cache_unknown_version, test_cache_corruption,
             test_fetch_lrc_mock_direct, test_fetch_lrc_mock_fallback,
             test_cache_concurrent, test_collect_flac_dedup,
             test_build_params_drops_empty_album,
             test_build_params_keeps_in_range_duration,
             test_build_params_drops_out_of_range_duration,
             test_build_params_rejects_blank_signature,
             test_entry_expired_hard_miss_uses_short_ttl,
             test_entry_expired_legacy_entry_migrates_to_miss,
             test_entry_expired_found_unsynced_keeps_long_ttl,
             test_entry_expired_hit_never_expires,
             test_rate_limiter_paces_requests,
             test_rate_limiter_allows_burst_up_to_ceiling,
             test_rate_limiter_halves_on_throttle_to_floor,
             test_rate_limiter_recovers_after_success_streak,
             test_rate_limiter_ceiling_follows_slider,
             test_fetch_lrc_400_does_not_fall_back_to_search,
             test_fetch_lrc_429_is_transient_and_uncached,
             test_fetch_lrc_404_is_cached_as_miss,
             test_search_fallback_prefers_duration_match,
             test_search_fallback_uses_structured_query,
             test_fetch_lrc_invalid_signature_makes_no_call,
             test_fetch_lrc_invalid_signature_is_not_counted_as_api_call,
             test_score_entry_two_tier_duration_rule,
             test_lookup_all_queries_200_empty_is_cached_miss,
             test_lookup_exception_on_search_is_inconclusive_uncached,
             test_lookup_429_on_search_is_inconclusive_uncached,
             test_lookup_exception_on_get_is_inconclusive_uncached,
             test_lookup_invalid_signature_is_inconclusive,
             test_cascade_get_without_album,
             test_cascade_not_triggered_when_album_empty,
             test_get_plain_only_prefers_synced_search,
             test_get_plain_only_falls_back_to_plain,
             test_search_rejects_lrc_past_local_duration,
             test_bini_not_called_on_inconclusive,
             test_bini_called_on_not_found_with_isrc,
             test_bini_called_on_plain_only_and_preferred,
             test_bini_not_called_when_synced_found,
             test_bini_no_cache_entry_on_transient,
             test_bini_queries_put_isrc_first,
             test_bini_isrc_exact_accepts_divergent_title_within_duration,
             test_bini_isrc_exact_rejected_when_past_duration,
             test_bini_empty_results_is_not_error,
             test_scaled_size_is_identity_at_100_percent,
             test_scaled_size_converts_physical_pixels_to_tk_units,
             test_scaled_size_is_never_below_minimum,
             test_scaled_size_never_exceeds_work_area,
             test_opening_geometry_centers_client_area_at_125_percent,
             test_opening_geometry_centers_at_100_percent,
             test_opening_geometry_fits_entirely_on_screen,
             test_opening_geometry_clamps_window_larger_than_screen,
             test_resolve_asset_returns_path_when_present,
             test_resolve_asset_returns_none_when_missing,
             test_resolve_asset_ignores_directories,
             test_similarity_ignores_non_significant_markers,
             test_similarity_keeps_significant_version_markers,
             test_meets_threshold_two_tiers,
             test_last_timestamp,
             test_entry_expired_no_sync_uses_seven_days,
             test_parse_manual_lyrics_lrc,
             test_parse_manual_lyrics_plain,
             test_parse_manual_lyrics_empty,
             test_parse_manual_lyrics_crlf_bom_and_enhanced,
             test_parse_manual_lyrics_warns_past_duration,
             test_ttml_to_lrc_line_level,
             test_ttml_to_lrc_syllable_level_has_no_extra_spaces,
             test_ttml_to_lrc_ignores_background_voice,
             test_ttml_to_lrc_time_formats,
             test_ttml_to_lrc_sorts_and_dedupes,
             test_ttml_to_lrc_empty_and_invalid]
    for t in tests:
        t()
    print(f"OK — {len(tests)} tests passés")


if __name__ == "__main__":
    main()