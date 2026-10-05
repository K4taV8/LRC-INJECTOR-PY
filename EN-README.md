# ` 🎤 `︲LRC Injector : Automatic synchronized lyrics for .FLAC libraries

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white&style=for-the-badge">
  <img src="https://img.shields.io/badge/CustomTkinter-GUI-green?style=for-the-badge">
  <img src="https://img.shields.io/badge/FLAC-mutagen-4b0082?style=for-the-badge">
  <img src="https://img.shields.io/badge/LRCLIB-API-orange?style=for-the-badge">
  <img src="https://img.shields.io/badge/Threaded-ThreadPoolExecutor-critical?style=for-the-badge">
  <img src="https://img.shields.io/badge/API_rate-1_%E2%86%92_25_req%2Fs-8A2BE2?style=for-the-badge">
  <img src="https://img.shields.io/badge/License-MIT-lightgrey?style=for-the-badge">
  <img src="https://img.shields.io/badge/AI_Powered-DeepSeek_V4_|_Claude-8A2BE2?style=for-the-badge">
</p>

---

<p align="center">Made with care, but with AI!</p>

---

**LRC Injector** is a desktop tool (CustomTkinter) that scans a music library in `FLAC` format, queries the [LRCLIB](https://lrclib.net/) API to fetch synchronized lyrics for each track, and injects them directly into the file tags (`LYRICS` and `UNSYNCEDLYRICS`). Additionally, a `CHECK` mode lets you audit an existing library and automatically repair incomplete tags.

---

## `📑`︲Table of contents

1. [`📘`︲Overview.](#overview)
2. [`✨`︲Features.](#features)
3. [`🖼️`︲Preview.](#preview)
4. [`🛠️`︲Installation.](#installation)
5. [`▶️`︲Usage.](#usage)
6. [`⚙️`︲Configuration.](#configuration)
7. [`🧩`︲Project architecture.](#architecture)
8. [`⚡`︲Performance and optimizations.](#performance)
9. [`⚖️`︲Technical choices & limitations.](#choices)
10. [`🧰`︲Technologies used.](#technologies)
11. [`🗺️`︲Roadmap.](#roadmap)
12. [`🤝`︲Contributing.](#contributing)
13. [`📜`︲License.](#license)

---

<a id="overview"></a>
# `📘`︲Overview.

---

> [!NOTE]
> **Problem solved:** many `FLAC` files have no lyrics at all, or only an unsynchronized version. Manually finding and pasting a `.lrc` file per track is not an option on a library of several thousand titles.
>
> **Goal:** fully automate this task (search, validation, injection and verification) across an entire library, parallelizing requests and avoiding re-querying the API for already-processed tracks.

The script specifically targets the `FLAC` format and relies on two Vorbis Comment tags:

| Tag             | Content                                      |
|-----------------|-----------------------------------------------|
| `LYRICS`        | Synchronized lyrics, `LRC` format (`[mm:ss.xx]`) |
| `UNSYNCEDLYRICS`| Plain lyrics, without timestamps              |

---

<a id="features"></a>
# `✨`︲Features.

---

> [!IMPORTANT]
> **Two operating modes, accessible from the same interface:**
>
> - ` 💉 ` **START** ︲ Inject missing lyrics on a folder or a `.flac` file.
> - ` 🔍 ` **CHECK**︲ Audit + automatic repair of tags that are present but incomplete.

* ` 🌐 `︲**Fetching via the LRCLIB API** : direct query (`artist`/`track`/`album`) with the **track duration** (`duration`, read from local FLAC tags — official LRCLIB recommendation : ±2 s matching, fewer false positives; `duration` outside the 1–3600 s range is dropped and reported), then automatic fallback to the search endpoint with several query variants (exact name, title only, ASCII transliterated version, structured `track_name`+`artist_name`) where the local duration also breaks ties — all within a bounded budget (12 s).

* ` 🧠 `︲**Fuzzy similarity validation** (`rapidfuzz`) : API results are compared against the local file's artist and title (threshold `85%`) before injection, to filter out false positive.

* ` 🎼 `︲**Dual synchronized/plain injection** : every processed track receives both `LYRICS` (with timestamps) and `UNSYNCEDLYRICS` (timestamps removed), generated from the same source.

* ` 🎹 `︲**Instrumental track detection** : based on the `instrumental` flag returned by the API or the word "instrumental" in the title. These tracks are explicitly ignored rather than treated as failures.

* ` 🩹 `︲**Repairing CHECK mode** : if a file has `LYRICS` but not `UNSYNCEDLYRICS` (or the reverse), the tool regenerates the missing tag without a new network request when possible (local derivation by stripping timestamps).

* ` 💾 `︲**Persistent local cache** (`lrc_cache.json`) : every artist/title pair already resolved (found, not found, or instrumental) is cached to avoid re-querying the API on later runs. Periodic writes (every 200 changes) plus a save on exit. **Two distinct TTLs** : a *not found* track (`404`) is retried after **3 days** — LRCLIB picks missing tracks up in the background, so a miss is not final — while a track *found but without synced lyrics* is kept for **30 days**. Successful lookups never expire.

* ` ⚙️ `︲**Multithreaded processing** : `ThreadPoolExecutor` with an adjustable thread count in the interface (1–32 for injection, 1–16 for checking).

* ` 🔁 `︲**Resilient HTTP requests** : shared `requests` session with a retry strategy on status codes `500`, `502`, `503`; `429` (rate limit) and `5xx` are handled by `_api_get()`, which honors `Retry-After` and halves the effective rate instead of only pausing once. A transient error never gets cached.

* ` 🚦 `︲**Adjustable API rate (1 → 25 req/s)** : a shared token bucket (`RateLimiter`) always smooths the flow, whatever the thread count — no more unlimited bursts. The UI slider sets the ceiling (25 req/s by default). On `429`/`503`, `Retry-After` is honored, the rate is halved (2 req/s floor) and climbs back by steps after 25 healthy responses, never above the slider.

* ` ⏹️ `︲**Clean cancellation** : a `STOP` button interrupts the current batch (`threading.Event`), without corrupting the cache or leaving orphan threads.

* ` 🎨 `︲**Application logo** (`Logo.png`) : shown in the title bar, in the **taskbar** and in the header at the top left of the window. An explicit `AppUserModelID` (`K4taV8.LRC-INJECTOR-PY`) is set **before** the window is created: without it, Windows groups the window under the executable path and shows `python.exe`'s icon (the Py logo) instead of the window's. Missing file = the app still starts, with the default icon.

* ` 🖥️ `︲**Native dark interface** : `customtkinter` theme (custom palette matching the `style_v2.css` design mock, logo + app name header, rounded cards, fixed paddings per component type, colored `START`/`STOP` buttons, rate slider, `Auto` checkbox), status-colored log (`OK`, `MISS`, `REJECT`, `SKIP`, `ERROR`, `INST`), smooth determinate/indeterminate progress bar. Windows-only polish, silently skipped elsewhere: dark title bar (`DwmSetWindowAttribute`).

* ` 📐 `︲**Window sized in real pixels** : startup targets **852 × 952 pixels** whatever the screen scaling. Tk applies the DPI factor to the *size* while Windows reads the *position* in physical pixels, so both are converted separately (`opening_geometry()`), and the window frame border is measured at startup for pixel-perfect centering.

---

<a id="preview"></a>
# `🖼️`︲Preview.

---

<details open>
  <summary>📸︲Interface gallery and logs.</summary>
  <br>
  <table>
    <tr>
      <td width="50%">
        <img src="https://github.com/user-attachments/assets/ca5f23f3-a2d4-4927-8688-b3f0042ada76" alt="Interface principale" width="100%" />
      </td>
      <td width="50%">
        <img src="https://github.com/user-attachments/assets/4a687a0b-e159-405c-8354-f562b8e19592" alt="Log en cours d'exécution 1" width="100%" />
      </td>
    </tr>
    <tr>
      <td width="50%">
        <img src="https://github.com/user-attachments/assets/b3d258a6-5b8b-4785-8290-15ec2d66d8f4" alt="Log en cours d'exécution 2" width="100%" />
      </td>
      <td width="50%">
        <img src="https://github.com/user-attachments/assets/29edc5ae-2c61-4326-8f0d-c6a744f52714" alt="Log en cours d'exécution 3" width="100%" />
      </td>
    </tr>
  </table>
</details>

---

<a id="installation"></a>
# `🛠️`︲Installation.

---

> [!NOTE]
> The interface relies on `Tkinter`. Some visual optimizations (DPI, dark title bar) are Windows-specific and are silently ignored on other systems ; the core script remains cross-platform.

---

**1️⃣ Prerequisites.**

* **Python** `3.10+` recommended *(minimum version not formally tested, to be validated)*.
* **pip** up to date.

---

**2️⃣ Clone the repository.**

```bash
git clone <URL_OF_THE_REPOSITORY>
cd <NAME_OF_THE_FOLDER>
```

---

**3️⃣ Install the dependencies.**

```bash
pip install -r requirements.txt
```

`pillow` is required for the in-app logo (`CTkImage` needs it, but CustomTkinter does not declare it as a dependency).

`tkinter` is part of the Python standard library on most distributions. On Linux, it may require a separate system package (e.g. `python3-tk`).

---

**4️⃣ Launch the application.**

```bash
python lrc-inject.py
```

**5️⃣ (Optional) Save the log to a file.**

```bash
# PowerShell
$env:LRC_LOG_FILE = "C:\path\to\lrc-inject.log"
python lrc-inject.py
```

**6️⃣ Run the tests (45 tests, ~ 2 s).**

```bash
python test_core.py
```

**7️⃣ (Optional) Diagnose a failing startup.**

* From a terminal : `python lrc-inject.py` prints the full traceback on error.
* In VS Code, "Run Active File" uses the Python interpreter selected at the bottom right of the window : make sure it is the one where the dependencies are installed (`pip show customtkinter`).

---

<a id="usage"></a>
# `▶️`︲Usage.

---

> [!CAUTION]
> **Before any batch processing, make a backup copy of your FLAC files.** In-place writing modifies the original files (see [Technical Choices & limitations](#choices)). The risk is very low — a few milliseconds per file — but in the event of a crash at the exact moment of writing, metadata or even audio can be affected. A backup lets you revert without worry.

**1️⃣ Select a source.**

* **Folder** : processes every `.flac` in the folder (and subfolders).
* **File** : processes a single `.flac` file.

---

**2️⃣ Adjust the number of threads (optional).**

`Threads` field : number of parallel requests/processes. The **Auto** checkbox (enabled by default) uses the CPU core count automatically; uncheck it to enter a value manually.

---

**3️⃣ Launch the desired operation.**

| Button | Action |
|--------|--------|
| `START` | Injects missing lyrics on files without `LYRICS`/`UNSYNCEDLYRICS`. |
| `CHECK & REPAIR` | Audits already-tagged files and repairs incomplete tags. |
| `STOP` | Interrupts the current processing. |

---

**4️⃣ Read the log.**

Every processed track generates a line prefixed by its status:

```
[OK]        Lyrics already present / injection succeeded
[REJECT]    Result found but insufficient similarity with the local tag
[MISS]      No result found on LRCLIB
[SKIP]      File ignored (already complete, or missing artist/title)
[INST]      Track identified as instrumental
[ERROR]     File read/write error
[PARTIAL]   Incomplete tag that cannot be repaired (CHECK mode)
[MISSING]   File without any lyrics tag (CHECK mode)
[REPAIRED]  Missing tag regenerated (CHECK mode)
```

A concise numeric summary (`STATS`) is displayed at the end of the processing.

---

**5️⃣ Clear the cache if necessary.**

`Clear Cache` button (Options card, in the API rate row) : deletes `lrc_cache.json` and starts from a clean state. Useful after a change in the source of truth for the metadata, or to force a retry before the TTLs expire.

---

<a id="configuration"></a>
# `⚙️`︲Configuration.

---

> [!NOTE]
> The project has no external configuration file : the only settings available are the ones exposed in the interface (plus one environment variable for the log).

* ` 🧵 `︲**Threads** : numeric field in the interface (automatically clamped to 1–32 depending on the mode — the value actually used is reflected in the field). The **Auto** checkbox (enabled by default) uses the CPU core count automatically, ideal if you don't know what to enter.
* ` 🚀 ` **API rate (slider 1 → 25 req/s)** : a slider in the Options card sets the **ceiling** in requests per second (25 by default).
  - Smoothing is permanent: a shared token bucket (`RateLimiter`, in `core.py`) paces the requests whatever the thread count. There is no more "unlimited burst" mode.
  - **On `429`/`503`**: `Retry-After` is honored, the effective rate is **halved** (2 req/s floor) and the request is retried up to 3 times. After 25 healthy responses in a row the rate climbs back by 1 req/s steps — **never above the slider**.
  - A transient error (`429`, `5xx`) is **never cached**: the track will be retried on the next run. A `400`, on the other hand, is an invalid request: it is reported as-is in the log, with no fallback triggered.
  - LRCLIB being a free service, the slider lets you stay cautious on a large library. The [official docs](https://lrclib.net/docs) recommend 200–500 ms between requests (~2–5 req/s) and threaten a temporary ban if `Retry-After` is ignored.
* ` 🪪 `︲**User-Agent** : set to `LRC-Injekt/1.0.1 (+https://github.com/LRC-Injekt)` — mandatory per the LRCLIB documentation to avoid a ban.
* ` ⏱️ `︲**Track duration sent**: the FLAC `length` tag (when available) is passed as `duration` to the API — ±2 s matching as recommended, which filters out false positives.
* ` 💾 ` **Cache** : `lrc_cache.json` file, generated automatically next to the script (versioned schema `v:1`, backward-compatible — an unknown schema is ignored). **3-day TTL on "not found" entries** (`miss`, e.g. `404`) and **30 days on "found without synced lyrics"** (`no_sync`): both expire on their own and trigger a retry. Existing `v:1` caches are picked up as-is, a `no_sync` entry without `plainLyrics` being reinterpreted as a `miss`. No custom path option so far.
* ` 🎯 `︲**Similarity threshold** : hardcoded to `85%` (`rapidfuzz.fuzz.ratio`) in the source code, not exposed in the interface.

* ` ⏱️ `︲**Duration as tie-breaker** : when falling back to `/api/search`, the local duration feeds the score (bonus within 2 s, decreasing penalty beyond) — the same criterion `/api/get` uses, without which a live or a remaster too often wins. Structured `track_name`+`artist_name` search completes the three `q` queries.
* ` 📄 `︲**File log (optional)** : set the `LRC_LOG_FILE` environment variable to a `.log` file to keep a persistent trace (appended in batches) in addition to the window.

---

<a id="architecture"></a>
# `🧩`︲Project architecture.

---

> [!IMPORTANT]
> The project is structured as **pure core + interface** :
>
> - `core.py` — pure logic **with no Tkinter**, importable and testable on its own: `clean()` / `match()` / `strip_timestamps()` / `_parse_result()`, `build_get_params()` (parameter validation), `entry_expired()` (cache TTL), `RateLimiter` (token bucket + `429` fallback), `scaled_size()` / `opening_geometry()` (real-pixel sizing and centering), `resolve_asset()` (optional assets), disk cache.
> - `lrc-inject.py` — CustomTkinter interface + network/threads orchestration.
> - `test_core.py` — assert-based test suite (45 tests, `python test_core.py`).
> - `Logo.png` — application logo (title bar, taskbar, header). Optional: a missing file does not prevent startup.

| Functional block                     | Role                                                                |
|--------------------------------------|---------------------------------------------------------------------|
| `get_session()`                      | Shared `requests` session with HTTP retry strategy.                 |
| `_load_cache()` / `_save_cache()` / `_mark_dirty()`    | Disk cache management (`lrc_cache.json`) — `core.py`, periodic flush, `v:1` schema. |
| `fetch_lrc()` / `_search_fallback()` / `_parse_result()` | Lyrics fetching via the LRCLIB API (direct query + budgeted search fallback, best candidate). |
| `clean()` / `match()` / `strip_timestamps()` | String normalization, fuzzy comparison, `LYRICS` → `UNSYNCEDLYRICS` derivation — `core.py`. |
| `build_get_params()` / `entry_expired()` / `resolve_asset()` | Pure validations — `core.py`: `/api/get` query conforming to the LRCLIB docs (empty params purged, `duration` bounded to 1–3600), cache TTL (3 d / 30 d), optional asset. |
| `scaled_size()` / `opening_geometry()` | Real-pixel startup size and exact centering — `core.py` (DPI conversion, physical-pixel position, frame border). |
| `_score_entry()` / `_search_queries()` | `/api/search` fallback: local duration as the deciding factor (±2 s) + structured `track_name`+`artist_name` query. |
| `_set_app_user_model_id()` / `_load_logo()` | App identity and logo: `AppUserModelID` before the window (taskbar icon instead of the Py logo), `Logo.png` in the title bar and header. |
| `_collect_flac()`                    | Recursive scan + inode-based deduplication (hardlinks/duplicates).  |
| `process_file()` / `run()`           | **Injection** mode logic (START) on a file / a batch.               |
| `check_one()` / `check_files()`      | **Audit/repair** mode logic (CHECK) on a file / a batch.            |
| `log()` / `_flush_log()`             | Log queue + batched rendering to the `Text` widget, colored per tag (optional `LRC_LOG_FILE`). |
| `_LIM.acquire()` / `_rate_limit_pause()` / `_api_get()` / `set_rate_ceiling()` | Rate policy: shared smoothed token bucket (`RateLimiter`), ceiling set by the slider, `429`/`5xx` fallback honoring `Retry-After` and halving the rate. |
| `start_pulse()` / `stop_pulse()` / `update_progress()` | Progress bar control (indeterminate then determinate). |
| `window_v2` / `Header` / `Palette` / `Body` sections | CustomTkinter UI building (palette matching the `style_v2.css` mock, logo + app name header, options/log cards, buttons, status bar). |
| `stop_processing()` / `on_close()` | Cancellation of the running batch (`threading.Event`), cache flush and orderly shutdown. |

---

<a id="performance"></a>
# `⚡`︲Performance and optimizations.

---

> I/O optimization is at the heart of the tool's speed. Every disk bottleneck has been addressed :

* ` ⚡ `︲**Direct in-place FLAC write** : no temporary file, no `os.replace`. `audio.save()` writes the tags directly at the head of the file without recopying the audio data or double I/O. Result : one injection takes **a few milliseconds** per file instead of several seconds with a full copy.

* ` 🗂️ `︲**Deduplication of collected files** : two paths pointing to the same physical file (hardlink, tree duplicates) are processed **only once** (inode-based identifier) — avoids any concurrent write to the same file.

* ` 🔓 `︲**Lock-free cache snapshot** : the lock is released before the JSON disk write. Worker threads never block on a cache flush. A `pending_count` versioning mechanism guarantees that no entry is lost if the cache changes during I/O.

* ` 🧵 `︲**Parallelization via `ThreadPoolExecutor`** : I/O-bound processing (network requests, FLAC writes) benefits from multithreading despite the GIL, each task spending most of its time waiting on the network/disk.

* ` 💾 `︲**Cache key `artist\x00title\x00album`** : removes redundant network requests over repeated runs or large partially-processed libraries. The `\x00` separator is impossible in music metadata, avoiding collisions.

* ` 📉 `︲**Batch cache flush** (every 200 changes) rather than on each write : fewer disk accesses on large volumes.

* ` 🪶 `︲**Batched log rendering** (`_flush_log`, throttled at 150 ms, grouping same-status lines) : avoids saturating the Tkinter event loop with one `insert()` per line on high-throughput runs.

* ` 🧹 `︲**Purge of the displayed log** beyond 3000 lines : prevents UI degradation on very long sessions.

* ` 🔁 `︲**Bounded HTTP retry** (2 attempts, `0.5s` backoff) : tolerates transient API errors without blocking a thread indefinitely.

* ` 🪣 `︲**Shared token bucket** : smooths the flow under a lock, ceiling adjustable from 1 to 25 req/s — a steady, burst-free rhythm for LRCLIB.

* ` 🎯 `︲**Budgeted fallback search** (12 s max) : the best candidate is selected by combined scoring (artist ratio + title ratio) without ever exceeding the total budget.

* ` 📊 `︲**Throttled progress updates** : the bar refreshes only ~200 steps per batch (not a `set()` per file) — the Tkinter loop stays smooth on large volumes.

* ` 🍃 `︲**Minimal memory footprint** : ~22 MB in RAM with the CustomTkinter theme (vs ~20 MB with raw Tk, 150–200 MB for a web-based interface like pywebview).

---

<a id="choices"></a>
# `⚖️`︲Technical Choices & limitations.

| Choice | Rationale |
|--------|-----------|
| **In-place FLAC writing** (`audio.save()` without temp file) | mutagen 1.46+ does not support `save(tmp)` to a new file — it checks the FLAC header of the output file, which fails on an empty one. In-place `save()` writes the new Vorbis tags at the head of the file. If the Vorbis block changes size (systematically the case with `LYRICS` + `UNSYNCEDLYRICS`), `resize_bytes` physically shifts the audio data on disk. A crash/power loss *during this shift* can truncate the audio, not just the tags. This is very unlikely (a few-ms window per file), but documented for transparency. For maximum safety, back up your library before batch processing. |
| **Broad `except Exception`** | GUI application : a silent failure logged with context is better than an unhandled traceback that closes the window. Every error is logged with its context. |
| **Extracted pure core (`core.py`)** | The pure logic (cleaning, matching, parsing, cache, rate limiter, geometry) was extracted from `lrc-inject.py` (GUI + orchestration) — now unit-testable (45 tests). |
| **`daemon=True` on workers** | The `join(timeout=30)` in `on_close()` leaves time to finish. If the timeout expires, the thread is killed ; the in-place write can leave a file mid-shift in an unstable state. Ideally, wait for the processing to finish before closing the app. |
| **No-TTL cache** | The `no_sync` and `inst` entries are permanent; "not found" entries expire after **30 days**. The user has a `Clear Cache` button to start from scratch if needed. |
| **Single smoothed API rate** | No more unlimited bursts: a shared token bucket capped by a slider (1 → 25 req/s, 25 by default). On `429`/`503`, `Retry-After` is honored, the rate is halved then climbed back by steps, never above the slider. A transient error is never cached. |
| **Split cache TTLs** | "Not found" entries (`miss`, e.g. `404`) expire after **3 days** (LRCLIB picks missing tracks up in the background); "found without synced lyrics" (`no_sync`) after **30 days**; lyrics, instrumental answers and successful lookups stay forever. `Clear Cache` button to start from zero. |
| **`_save_flac(audio, path)` ignores `path`** | Signature kept for compatibility. `audio.save()` always uses the file's internal path. |

---

<a id="technologies"></a>
# `🧰`︲Technologies used.

---

* **Python 3** : main language.
* **Tkinter** : graphical interface (standard library).
* **customtkinter** : modern widgets and dark theme (custom palette).
* **mutagen** : reading/writing `FLAC` tags.
* **rapidfuzz** : fuzzy string comparison (artist/title validation).
* **requests** + **urllib3** : HTTP calls to the LRCLIB API, with retry handling.
* **customtkinter** also brings in **darkdetect** and **packaging** transitively.
* **ctypes** (Windows only) : DPI awareness and dark title bar via `SetProcessDpiAwarenessContext` / `DwmSetWindowAttribute`.
* **LRCLIB API** ([`🌐`](https://lrclib.net/)) : source of synchronized lyrics.
* **concurrent.futures (ThreadPoolExecutor)** : processing parallelization.

---

<a id="roadmap"></a>
# `🗺️`︲Roadmap.

---

> [!NOTE]
> No official roadmap has been provided. Evolution tracks identified from the current code, to be validated/prioritized:

* Expose the similarity threshold (`85%`) in the interface ?
* Support for other audio formats than `FLAC` (MP3...)?
* File log option directly in the interface (instead of the environment variable)?

---

<a id="contributing"></a>
# `🤝`︲Contributing.

---

> [!NOTE]
> No formal contribution conventions have been defined for this project : the procedure below is a standard base to adapt.

**1️⃣ Fork** the repository.

**2️⃣ Create a dedicated branch:**

```bash
git checkout -b feature/your-feature
```

**3️⃣ Commit the changes** with clear, atomic messages.

**4️⃣ Open a Pull Request** describing the context, the problem solved and possible side effects.

> [!TIP]
> Any change affecting `fetch_lrc`, the cache or the matching logic must specify its impact on the compatibility of the existing `lrc_cache.json` file.

---

<a id="license"></a>
# `📜`︲License.

---

> [!IMPORTANT]
> Project distributed under **MIT** license — see the `LICENSE` file at the root of the repository !

---
