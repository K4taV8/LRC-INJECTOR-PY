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
    def __init__(self, data, status=200):
        self._data = data
        self.status_code = status

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


def main():
    tests = [test_clean, test_fold, test_match, test_strip_timestamps,
             test_parse_result, test_cache_key, test_cache_roundtrip,
             test_cache_legacy, test_cache_unknown_version, test_cache_corruption,
             test_fetch_lrc_mock_direct, test_fetch_lrc_mock_fallback,
             test_cache_concurrent, test_collect_flac_dedup]
    for t in tests:
        t()
    print(f"OK — {len(tests)} tests passés")


if __name__ == "__main__":
    main()