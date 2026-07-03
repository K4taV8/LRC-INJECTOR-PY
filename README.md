# LRC-Injector

Injecte les paroles synchronisées (timestamps) dans les fichiers FLAC à partir de [LRCLIB](https://lrclib.net).

Écrit deux tags : `LYRICS` (synced) et `UNSYNCEDLYRICS` (plain text). Ignore les fichiers déjà tagués, détecte les instrumentales, et peut réparer les fichiers partiels.

## Prérequis

- Python 3.8+
- `pip install requests mutagen rapidfuzz`

## Lancer avec VSCodium

1. Ouvrir le dossier du script dans VSCodium
2. Ouvrir un terminal intégré (`Ctrl+J` ou `⌘+J`)
3. `pip install requests mutagen rapidfuzz` (une seule fois)
4. `python lrc-inject.py`

Ou ouvrir `lrc-inject.py` et cliquer sur le triangle ▶ "Run Python File" en haut à droite.

## Utilisation

1. Cliquer **Browse** et choisir un dossier contenant des fichiers FLAC
2. Ajuster le nombre de threads (défaut: 8)
3. **START** → injecte les paroles dans tous les FLAC du dossier
4. **CHECK** → scanne et répare les fichiers (comble les tags manquants)

## Structure des tags

| Tag | Contenu |
|-----|---------|
| `LYRICS` | Paroles synchronisées `[mm:ss.xx] texte` |
| `UNSYNCEDLYRICS` | Paroles brutes, sans timestamps |
