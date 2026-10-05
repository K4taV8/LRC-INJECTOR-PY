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
        sess = _FakeSession([_FakeResp({}, 404), _FakeResp([], 200)])
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
        ])])
        app.get_session = lambda: sess
        lrc, raw, inst = app._search_fallback("Queen", "Radio Ga Ga", "k", 177.4)
        _check(lrc == "[00:00.00]version courte",
               f"la durée départage les versions (obtenu {lrc!r})")
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
             test_resolve_asset_ignores_directories]
    for t in tests:
        t()
    print(f"OK — {len(tests)} tests passés")


if __name__ == "__main__":
    main()