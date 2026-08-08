# ` 🎤 `︲LRC Injector : Synchronisation automatique de paroles pour bibliothèques .FLAC

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white&style=for-the-badge">
  <img src="https://img.shields.io/badge/CustomTkinter-GUI-green?style=for-the-badge">
  <img src="https://img.shields.io/badge/FLAC-mutagen-4b0082?style=for-the-badge">
  <img src="https://img.shields.io/badge/LRCLIB-API-orange?style=for-the-badge">
  <img src="https://img.shields.io/badge/Threaded-ThreadPoolExecutor-critical?style=for-the-badge">
  <img src="https://img.shields.io/badge/Mode_API-SPEED_%7C_COOL-8A2BE2?style=for-the-badge">
  <img src="https://img.shields.io/badge/License-MIT-lightgrey?style=for-the-badge">
  <img src="https://img.shields.io/badge/AI_Powered-DeepSeek_V4_|_Claude-8A2BE2?style=for-the-badge">
</p>

---

**LRC Injector** est un outil de bureau (CustomTkinter) qui parcourt une bibliothèque musicale au format `FLAC`, interroge l'API [LRCLIB](https://lrclib.net/) pour récupérer les paroles synchronisées de chaque piste, puis les injecte directement dans les tags du fichier (`LYRICS` et `UNSYNCEDLYRICS`). Un mode `CHECK` permet en plus d'auditer une bibliothèque existante et de réparer automatiquement les tags incomplets.

---

## `📑`︲Sommaire

1. [`📘`︲Présentation.](#presentation)
2. [`✨`︲Fonctionnalités.](#fonctionnalites)
3. [`🖼️`︲Aperçu.](#apercu)
4. [`🛠️`︲Installation.](#installation)
5. [`▶️`︲Utilisation.](#utilisation)
6. [`⚙️`︲Configuration.](#configuration)
7. [`🧩`︲Architecture du projet.](#architecture)
8. [`⚡`︲Performances et optimisations.](#performances)
9. [`🧰`︲Technologies utilisées.](#technologies)
10. [`⚖️`︲Choix techniques & limitations.](#choices)
11. [`🗺️`︲Roadmap.](#roadmap)
12. [`🤝`︲Contribution.](#contribution)
13. [`📜`︲Licence.](#licence)

---

<a id="presentation"></a>
# `📘`︲Présentation.

---

> [!NOTE]
> **Problème résolu :** de nombreux fichiers `FLAC` n'embarquent aucune parole, ou seulement une version non synchronisée. Retrouver et coller manuellement un `.lrc` par piste n'est pas envisageable sur une bibliothèque de plusieurs milliers de titres.
>
> **Objectif :** automatiser entièrement ce travail (recherche, validation, injection et vérification) sur l'ensemble d'une discothèque, en parallélisant les requêtes et en évitant de re-interroger l'API pour des titres déjà traités.

Le script cible spécifiquement le format `FLAC` et s'appuie sur deux tags Vorbis Comment :

| Tag             | Contenu                                      |
|-----------------|-----------------------------------------------|
| `LYRICS`        | Paroles synchronisées, format `LRC` (`[mm:ss.xx]`) |
| `UNSYNCEDLYRICS`| Paroles brutes, sans timestamps               |

---

<a id="fonctionnalites"></a>
# `✨`︲Fonctionnalités.

---

> [!IMPORTANT]
> **Deux modes de fonctionnement, accessibles depuis la même interface :**
>
> - ` 💉 ` **START** ︲ Injection des paroles manquantes sur un dossier ou un fichier `.flac`.
> - ` 🔍 ` **CHECK** ︲ Audit + réparation automatique des tags déjà présents mais incomplets.

* ` 🌐 `︲**Récupération via l'API LRCLIB** : requête directe (`artist`/`track`/`album`), avec la **durée de la piste** transmise (`duration`, dérivée des tags FLAC locaux — recommandation officielle LRCLIB : matching ±2 s, moins de faux positifs), puis repli automatique sur l'endpoint de recherche avec plusieurs variantes de requête (nom exact, titre seul, version translittérée ASCII) si la première tentative échoue — le tout dans un budget temporel borné (12 s).

* ` 🧠 `︲**Validation par similarité floue** (`rapidfuzz`) : les résultats renvoyés par l'API sont comparés à l'artiste et au titre du fichier local (seuil `85%`) avant injection, pour écarter les faux positifs.

* ` 🎼 `︲**Double injection synchronisée/brute** : chaque piste traitée reçoit à la fois `LYRICS` (avec timestamps) et `UNSYNCEDLYRICS` (timestamps supprimés), générées à partir de la même source.

* ` 🎹 `︲**Détection des morceaux instrumentaux** : via le flag `instrumental` renvoyé par l'API ou la présence du mot « instrumental » dans le titre. Ces pistes sont explicitement ignorées plutôt que traitées comme des échecs.

* ` 🩹 `︲**Mode CHECK réparateur** : si un fichier possède `LYRICS` mais pas `UNSYNCEDLYRICS` (ou l'inverse), l'outil régénère le tag manquant sans reformuler de requête réseau quand c'est possible (dérivation locale par suppression des timestamps).

* ` 💾 `︲**Cache local persistant** (`lrc_cache.json`) : chaque couple artiste/titre déjà résolu (trouvé, introuvable ou instrumental) est mis en cache pour éviter de re-solliciter l'API lors des passages suivants. Écriture périodique (tous les 200 changements) + à la fermeture. Les entrées « introuvable » portent un **TTL de 30 jours** : une piste absente aujourd'hui sera re-tentée automatiquement dans un mois.

* ` ⚙️ `︲**Traitement multithread** : `ThreadPoolExecutor` avec un nombre de threads réglable depuis l'interface (1 à 32 en injection, 1 à 16 en vérification).

* ` 🔁 `︲**Requêtes HTTP résilientes** : session `requests` mutualisée (pool de 32 connexions) avec stratégie de retry (2 tentatives, backoff exponentiel) sur les codes `500`, `502`, `503` — le `429` (rate limit) est géré séparément via une pause coordonnée honorant `Retry-After`. Un **User-Agent nominatif** (`lrc-injector/1.2`) est envoyé sur chaque requête — exigence LRCLIB (403 sinon).

* ` 🚦 `︲**Deux modes d'allure API (`SPEED` / `COOL`)** : SPEED *(défaut)* = rafales illimitées au débit maximum offert par vos threads ; COOL = débit lissé à ~10 requêtes/s (token bucket partagé). Dans les deux modes, `Retry-After` est obligatoirement honoré (exigence LRCLIB, sinon bannissement temporaire). Détails en [Configuration](#configuration).

* ` ⏹️ `︲**Annulation propre** : un bouton `STOP` interrompt le lot en cours (`threading.Event`), sans corrompre le cache ni laisser de threads orphelins.

* ` 🖥️ `︲**Interface sombre moderne** : `customtkinter` (palette sur mesure calquée sur une maquette HTML/CSS, cartes arrondies `Options`/`Log` avec marges internes, boutons colorés `START`/`STOP`/`SPEED`/`COOL`, case `Auto`), log coloré par statut (`OK`, `MISS`, `REJECT`, `SKIP`, `ERROR`, `INST`), barre de progression lisse déterminée/indéterminée selon la phase. Rendu net sur écrans haute-DPI (DPI awareness forcée) et barre de titre Windows sombre native.

---

<a id="apercu"></a>
# `🖼️`︲Aperçu.

---

<details>
  <summary>📸︲Interface principale.</summary>
  <img width="1220" height="980" alt="image" src="https://github.com/user-attachments/assets/ca5f23f3-a2d4-4927-8688-b3f0042ada76" />
</details>

---

<details>
  <summary>📸︲Log en cours d'exécution.</summary>
  <img width="1220" height="980" alt="image" src="https://github.com/user-attachments/assets/4a687a0b-e159-405c-8354-f562b8e19592" />
  <img width="1220" height="980" alt="image" src="https://github.com/user-attachments/assets/b3d258a6-5b8b-4785-8290-15ec2d66d8f4" />
  <img width="1220" height="978" alt="image" src="https://github.com/user-attachments/assets/29edc5ae-2c61-4326-8f0d-c6a744f52714" />
</details>

---

<a id="installation"></a>
# `🛠️`︲Installation.

---

> [!NOTE]
> L'interface graphique repose sur `Tkinter`. Certaines optimisations visuelles (DPI, barre de titre sombre) sont spécifiques à Windows et s'ignorent silencieusement sur les autres systèmes ; le cœur du script reste multiplateforme.

---

1️⃣︲**Prérequis.**

* ` 🐍 `︲**Python** `3.10+` recommandé *(version minimale non testée formellement, à valider)*.
* ` 📦 `︲**pip** à jour.

---

2️⃣︲**Cloner le dépôt.**

```bash
git clone <URL_DU_DEPOT_A_COMPLETER>
cd <NOM_DU_DOSSIER_A_COMPLETER>
```

---

3️⃣︲**Installer les dépendances.**

```bash
pip install requests rapidfuzz mutagen customtkinter
```

`tkinter` fait partie de la bibliothèque standard Python sur la plupart des distributions ; sous Linux, il peut nécessiter un paquet système séparé (ex. `python3-tk`).

---

4️⃣︲**Lancer l'application.**

```bash
python lrc-inject.py
```

5️⃣︲**(Optionnel) Sauvegarder le log dans un fichier.**

```bash
# PowerShell
$env:LRC_LOG_FILE = "C:\chemin\vers\lrc-inject.log"
python lrc-inject.py
```

6️⃣︲**Lancer les tests (14 tests, ~2 s).**

```bash
python test_core.py
```

7️⃣︲**(Optionnel) Diagnostiquer un démarrage qui échoue.**

* `diag.bat` (Windows) : double-cliquez — le terminal reste ouvert, affiche l'interpréteur Python, l'état des dépendances, puis écrit la trace de démarrage dans `diag.log` et le code de sortie.
* Depuis un terminal : `python lrc-inject.py` affiche la traceback complète en cas d'erreur.
* Sous VS Code, « Run Active File » utilise l'interpréteur Python sélectionné en bas à droite de la fenêtre : vérifiez que c'est bien celui où les dépendances sont installées (`pip show customtkinter`).

---

<a id="utilisation"></a>
# `▶️`︲Utilisation.

---

> [!CAUTION]
> **Avant tout traitement de masse, faites une copie de sauvegarde de vos fichiers FLAC.** L'écriture in-place modifie les fichiers originaux (cf. [Choix techniques](#choices)). Le risque est très faible — fenêtre de quelques ms par fichier — mais en cas de crash au moment précis de l'écriture, les métadonnées voire l'audio peuvent être affectés. Une sauvegarde vous permet de revenir en arrière en toute sérénité.

1️⃣︲**Sélectionner une source.**

* ` 📁 ` ︲**Folder** : traite récursivement tous les `.flac` du dossier (et sous-dossiers).
* ` 📄 ` ︲**File** : traite un unique fichier `.flac`.

---

2️⃣︲**Ajuster le nombre de threads (optionnel).**

Champ `Threads` : nombre de requêtes/traitements parallèles. La case **Auto** (activée par défaut) utilise le nombre de cœurs CPU ; décochez-la pour saisir une valeur manuellement.

---

3️⃣︲**Lancer le traitement souhaité.**

| Bouton  | Action                                                                 |
|---------|-------------------------------------------------------------------------|
| `START` | Injecte les paroles manquantes sur les fichiers sans tag `LYRICS`/`UNSYNCEDLYRICS`. |
| `CHECK & REPAIR` | Audite les fichiers déjà tagués et répare les tags incomplets.        |
| `STOP`  | Interrompt le traitement en cours.                                     |

---

4️⃣︲**Lire le log.**

Chaque piste traitée génère une ligne préfixée par son statut :

```
[OK]      Paroles déjà présentes / injection réussie
[REJECT]  Résultat trouvé mais similarité insuffisante avec le tag local
[MISS]    Aucun résultat trouvé sur LRCLIB
[SKIP]    Fichier ignoré (déjà complet, ou artiste/titre manquant)
[INST]    Piste identifiée comme instrumentale
[ERROR]   Erreur de lecture/écriture du fichier
[PARTIEL] Tag incomplet non réparable (mode CHECK)
[MANQUE]  Fichier sans aucun tag paroles (mode CHECK)
[RÉPARÉ]  Tag manquant régénéré (mode CHECK)
```

Un récapitulatif chiffré (`STATS`) s'affiche en fin de traitement.

---

5️⃣︲**Vider le cache si nécessaire.**

Bouton `Clear Cache` (carte Options, ligne de débit) : supprime `lrc_cache.json` et repart d'un état propre. Utile après un changement de source de vérité sur les métadonnées, ou pour forcer une re-tentative avant l'expiration des TTL.

---

<a id="configuration"></a>
# `⚙️`︲Configuration.

---

> [!NOTE]
> Le projet ne comporte pas de fichier de configuration externe : les seuls réglages disponibles sont ceux exposés dans l'interface (plus une variable d'environnement pour le log).

* ` 🧵 `︲**Threads** : champ numérique dans l'interface (borné automatiquement à 1–32 selon le mode — la valeur réellement utilisée est reflétée dans le champ). La case **Auto** (activée par défaut) utilise automatiquement le nombre de cœurs CPU, idéal si vous ne savez pas quoi saisir.
* ` 🚦 `︲**Mode API (SPEED / COOL)** : deux boutons dans la carte Options (ligne de réglage du débit).
  - **SPEED** *(défaut)* : rafales sans limite — la bibliothèque est scannée au débit maximum offert par vos threads et votre connexion. Si LRCLIB coupe (`429`), l'outil marque une pause `[Rate limit]` unique et **coordonnée** (tous les threads attendent, `Retry-After` honoré) puis repart à fond. **À utiliser** pour un passage de masse rapide à froid, quand le cache n'a pas encore ses entrées.
  - **COOL** : débit lissé à ~10 requêtes/s via un **token bucket partagé** (`_wait_token`). **À utiliser** quand le rate-limit fréquent agace (petites bibliothèques, re-runs partiels) ou pour un flux parfaitement fluide sans à-coups.
  - Dans les deux modes, `Retry-After` est obligatoirement honoré (exigence [LRCLIB](https://lrclib.net/docs), sinon bannissement temporaire). Consultez la doc officielle : User-Agent obligatoire, throttling 200–500 ms conseillé, `duration` fortement recommandé.
* ` 👤 `︲**User-Agent nominatif** : `lrc-injector/1.2 (https://github.com/K4taV8/LRC-INJECTOR-PY)` envoyé sur chaque requête — exigence LRCLIB, faute de quoi l'API refuse (403).
* ` ⏱️ `︲**Durée (`duration`)** : la durée de chaque piste (issue des tags FLAC locaux) est transmise lors de la requête directe — recommandation officielle LRCLIB : matching aux ±2 s, moins de faux résultats.
* ` 💾 `︲**Cache** : fichier `lrc_cache.json`, généré automatiquement à côté du script (schéma versionné `v:1`, compatibilité ascendante — un schéma inconnu est ignoré). TTL de 30 jours sur les entrées « introuvable » (`no_sync`) : elles expirent seules et déclenchent une re-tentative. Aucune option de chemin personnalisé actuellement.
* ` 🎯 `︲**Seuil de similarité** : fixé en dur à `85%` (`rapidfuzz.fuzz.ratio`) dans le code source, non exposé dans l'interface.
* ` 📄 `︲**Log fichier (optionnel)** : définissez la variable d'environnement `LRC_LOG_FILE` vers un fichier `.log` pour conserver une trace persistante (append par paquet) en plus de la fenêtre.

---

<a id="architecture"></a>
# `🧩`︲Architecture du projet.

---

> [!IMPORTANT]
> Le projet est structuré en **noyau pur + interface** :
>
> - `core.py` — logique pure (nettoyage, matching flou, parsing LRC, cache disque), **sans Tkinter**, importable et testable seul.
> - `lrc-inject.py` — interface CustomTkinter + orchestration réseau/threads.
> - `test_core.py` — suite de test assert-based (14 tests, `python test_core.py`).
> - `diag.py` / `diag.bat` — mouture d'inspection du démarrage (interpréteur, dépendances, trace écrite dans `diag.log`, code de sortie).

| Bloc fonctionnel                     | Rôle                                                                 |
|---------------------------------------|-----------------------------------------------------------------------|
| `get_session()`                       | Session `requests` mutualisée avec stratégie de retry HTTP.          |
| `_load_cache()` / `_save_cache()` / `_mark_dirty()` | Gestion du cache disque (`lrc_cache.json`) — `core.py`, flush périodique, schéma `v:1`. |
| `fetch_lrc()` / `_search_fallback()` / `_parse_result()` | Récupération des paroles via l'API LRCLIB (requête directe + repli recherche budgeté, meilleur candidat). |
| `clean()` / `match()` / `strip_timestamps()` | Normalisation de chaînes, comparaison floue, dérivation `LYRICS` → `UNSYNCEDLYRICS` — `core.py`. |
| `_collect_flac()`                     | Parcours récursif + déduplication par inode (hardlinks/doublons).    |
| `process_file()` / `run()`            | Logique du mode **injection** (START) sur un fichier / un lot.       |
| `check_one()` / `check_files()`       | Logique du mode **audit/réparation** (CHECK) sur un fichier / un lot. |
| `log()` / `_flush_log()`              | File d'attente de log + rendu par lots dans le composant `Text`, coloré par tag (option fichier `LRC_LOG_FILE`). |
| `_wait_token()` / `_rate_limit_pause()` / `_api_get()` | Politique de débit : token bucket optionnel (COOL, ~10 req/s) et pause coordonnée sur `429` honorant `Retry-After` (tous modes). |
| `start_pulse()` / `stop_pulse()` / `update_progress()` | Pilotage de la barre de progression (indéterminée puis déterminée). |
| Section `Fenêtre` / `Palette` / `Body` | Construction de l'interface CustomTkinter (palette maquette `style_v2.css`, cartes `Options`/`Log`, boutons, status bar). |

---

<a id="performances"></a>
# `⚡`︲Performances et optimisations.

---

> L'optimisation I/O est le cœur de la vitesse de l'outil. Chaque goulot d'étranglement disque a été traité :

* ` ⚡ `︲**Écriture FLAC directe in-place** : pas de fichier temporaire, pas de `os.replace`. `audio.save()` écrit les tags directement en tête du fichier sans recopier les données audio ni double I/O. Résultat : une injection prend **quelques millisecondes** par fichier au lieu de plusieurs secondes avec une copie complète.

* ` 🗂️ `︲**Déduplication des fichiers collectés** : deux chemins pointant sur le même fichier physique (hardlink, doublons d'arborescence) ne sont traités **qu'une seule fois** (identifiant par inode) — évite toute écriture concurrente sur le même fichier.

* ` 🔓 `︲**Snapshot cache sans contention** : le verrou est libéré avant l'écriture disque du JSON. Les threads workers ne bloquent jamais sur un flush cache. Un mécanisme de version (`pending_count`) garantit qu'aucune entrée n'est perdue si le cache est modifié pendant l'I/O.

* ` 🧵 `︲**Parallélisation via `ThreadPoolExecutor`** : le traitement I/O-bound (requêtes réseau, écriture FLAC) tire parti du multithreading malgré le GIL, chaque tâche passant l'essentiel de son temps en attente réseau/disque.

* ` 💾 `︲**Cache clé `artiste\x00titre\x00album`** : élimine les requêtes réseau redondantes sur des exécutions répétées ou de larges bibliothèques partiellement traitées. Séparateur `\x00` (impossible dans les métadonnées musicales, évite les collisions).

* ` 📉 `︲**Flush de cache par lot** (tous les 200 changements) plutôt qu'à chaque écriture : réduit les accès disque sur de gros volumes.

* ` 🪶 `︲**Rendu du log par lots** (`_flush_log`, throttlé à 150 ms, groupage des lignes de même statut) : évite de saturer la boucle Tkinter avec un `insert()` par ligne sur des traitements à haut débit.

* ` 🧹 `︲**Purge du log affiché** au-delà de 3000 lignes : empêche la dégradation de l'interface sur les très longues sessions.

* ` 🔁 `︲**Retry HTTP borné** (2 tentatives, backoff `0.5s`) : tolère les erreurs transitoires de l'API sans bloquer indéfiniment un thread.

* ` 🪣 `︲**Token bucket pour le mode COOL** : lissage du débit à ~10 req/s (consommations par paquets de tokens sous verrou) — flux régulier sans à-coups pour LRCLIB.

* ` 🎯 `︲**Recherche de repli budgetée** (12 s max) : le meilleur candidat est choisi par score combiné (ratio artiste + ratio titre) sans jamais dépasser le budget total.

* ` 📊 `︲**Progression throttlée** : la barre ne se met à jour que ~200 pas par lot (pas de `set()` par fichier) — la boucle Tkinter reste fluide sur de gros volumes.

* ` 🍃 `︲**Empreinte mémoire minimale** : ~22 Mo en RAM avec le thème CustomTkinter (contre ~20 Mo en Tk brut, 150–200 Mo pour une interface web type pywebview).

---

<a id="choices"></a>
# `⚖️`︲Choix techniques & limitations.

> L'outil a subi **11 audits consécutifs** et **38 bugs corrigés** avant d'atteindre ce niveau de maturité. Les choix ci-dessous sont délibérés et documentés pour éviter les faux positifs lors de futures relectures.

| Choix | Justification |
|-------|--------------|
| **Écriture FLAC in-place** (`audio.save()` sans fichier temp) | mutagen 1.46+ ne supporte pas `save(tmp)` vers un fichier neuf — il vérifie le header FLAC du fichier de sortie, ce qui échoue sur un fichier vide. `save()` in-place écrit les nouveaux tags Vorbis en tête de fichier. Si le bloc Vorbis change de taille (cas systématique avec `LYRICS` + `UNSYNCEDLYRICS`), `resize_bytes` décale physiquement les données audio sur le disque. Un crash/coupure *pendant ce décalage* peut tronquer l'audio et pas seulement les tags. Ce scénario est très improbable (fenêtre de quelques ms par fichier sur un lot de 189 fichiers, seuls les fichiers en cours d'écriture au moment précis du crash sont à risque), mais documenté pour transparence. Pour une sécurité maximale, sauvegardez votre bibliothèque avant un traitement de masse. |
| **`except Exception` généralisés** | Application GUI : un crash silencieux avec message dans le log est préférable à un traceback non géré qui ferme la fenêtre. Chaque erreur est loggée avec son contexte. |
| **Noyau pur extrait (`core.py`)** | La logique pure (nettoyage, matching, parsing, cache) a été extraite de `lrc-inject.py` (GUI + orchestration) — désormais testable unitairement (14 tests). |
| **`daemon=True` sur les workers** | Le `join(timeout=30)` dans `on_close()` laisse le temps de finir. Si le timeout expire, le thread est tué ; l'écriture in-place peut laisser un fichier en cours de décalage audio dans un état instable. Idéalement, attendre la fin du traitement avant de fermer l'application. |
| **TTL ciblé sur le cache** | Les entrées "introuvable" (`no_sync`) expirent après 30 jours (re-tentative automatique) ; les paroles, réponses instrumentales et réussites restent permanentes. Bouton `Clear Cache` pour repartir de zéro immédiatement. |
| **Rate limiting : SPEED par défaut, COOL en option** | Défaut = rafales illimitées (débit max, idéal cache à froid) + pause `429`/`Retry-After` **coordonnée** entre threads. Option COOL = token bucket ~10 req/s, pour des sources plus régulières ou des re-runs fréquents. Choix exposé dans l'interface (`SPEED`/`COOL`). |
| **`_save_flac(audio, path)` ignore `path`** | Signature conservée pour compatibilité. `audio.save()` utilise toujours le chemin interne du fichier. |

---

<a id="technologies"></a>
# `🧰`︲Technologies utilisées.

---

* ` 🐍 ` ︲**Python 3** : langage principal.
* ` 🖼️ ` ︲**Tkinter** : interface graphique (bibliothèque standard).
* ` 🎨 ` ︲**customtkinter** : widgets modernes et thème sombre (palette sur mesure, cartes rondées, converti de la maquette HTML/CSS). Famille transitive : `darkdetect`, `packaging`.
* ` 🖥️ ` ︲**ctypes / windll** (Windows) : DPI awareness forcée + barre de titre sombre native (ignorés silencieusement sur les autres OS).
* ` 🎧 ` ︲**mutagen** : lecture/écriture des tags `FLAC`.
* ` 🔎 ` ︲**rapidfuzz** : comparaison floue de chaînes (validation artiste/titre, seuil 85 %).
* ` 🌐 ` ︲**requests** + **urllib3** : appels HTTP vers l'API LRCLIB, retry borné, pooling 32 connexions (transitives : `charset_normalizer`, `idna`, `certifi`).
* ` 🎼 ` ︲**LRCLIB API** ︲[`🌐`](https://lrclib.net/) : source des paroles synchronisées.
* ` 🧵 ` ︲**concurrent.futures (ThreadPoolExecutor)** : parallélisation des traitements.

---

<a id="roadmap"></a>
# `🗺️`︲Roadmap ?

---

> [!NOTE]
> Aucune feuille de route officielle n'a été fournie. Pistes d'évolution identifiées à partir du code actuel, à valider/prioriser :

* ` ⚙️ `︲Exposition du seuil de similarité (`85%`) dans l'interface ?
* ` 🗂️ `︲Support d'autres formats audio que `FLAC` (`MP3`...) ?
* ` 🖥️ `︲Option log fichier directement dans l'interface (au lieu de la variable d'environnement) ?

---

<a id="contribution"></a>
# `🤝`︲Contribution.

---

> [!NOTE]
> Aucune convention de contribution formelle n'a été définie pour ce projet : la marche à suivre ci-dessous est une base standard à adapter.

1️⃣︲**Fork** du dépôt.

2️⃣︲**Créer une branche dédiée :**

```bash
git checkout -b feature/ma-fonctionnalite
```

3️⃣︲**Committer les changements** avec des messages clairs et atomiques.

4️⃣︲**Ouvrir une Pull Request** en décrivant le contexte, le problème résolu et les éventuels effets de bord.

> [!TIP]
> Toute modification touchant à `fetch_lrc`, au cache ou à la logique de matching doit préciser son impact sur la compatibilité du fichier `lrc_cache.json` existant.

---

<a id="licence"></a>
# `📜`︲Licence.

---

> [!IMPORTANT]
> Projet distribué sous licence **MIT** voir le fichier `LICENSE` à la racine du dépôt !

---
