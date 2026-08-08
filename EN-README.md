# ` 🎤 `︲LRC Injector : Automatic synchronized lyrics for .FLAC libraries

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white&style=for-the-badge">
  <img src="https://img.shields.io/badge/CustomTkinter-GUI-green?style=for-the-badge">
  <img src="https://img.shields.io/badge/FLAC-mutagen-4b0082?style=for-the-badge">
  <img src="https://img.shields.io/badge/LRCLIB-API-orange?style=for-the-badge">
  <img src="https://img.shields.io/badge/Threaded-ThreadPoolExecutor-critical?style=for-the-badge">
  <img src="https://img.shields.io/badge/API_Mode-SPEED_%7C_COOL-8A2BE2?style=for-the-badge">
  <img src="https://img.shields.io/badge/License-MIT-lightgrey?style=for-the-badge">
  <img src="https://img.shields.io/badge/AI_Powered-DeepSeek_V4_|_Claude-8A2BE2?style=for-the-badge">
</p>

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

* ` 🌐 `︲**Fetching via the LRCLIB API** : direct query (`artist`/`track`/`album`) with the **track duration** (`duration`, read from local FLAC tags — official LRCLIB recommendation : ±2 s matching, fewer false positives), then automatic fallback to the search endpoint with several query variants (exact name, title only, ASCII transliterated version) — all within a bounded budget (12 s).

* ` 🧠 `︲**Fuzzy similarity validation** (`rapidfuzz`) : API results are compared against the local file's artist and title (threshold `85%`) before injection, to filter out false positive.

* ` 🎼 `︲**Dual synchronized/plain injection** : every processed track receives both `LYRICS` (with timestamps) and `UNSYNCEDLYRICS` (timestamps removed), generated from the same source.

* ` 🎹 `︲**Instrumental track detection** : based on the `instrumental` flag returned by the API or the word "instrumental" in the title. These tracks are explicitly ignored rather than treated as failures.

* ` 🩹 `︲**Repairing CHECK mode** : if a file has `LYRICS` but not `UNSYNCEDLYRICS` (or the reverse), the tool regenerates the missing tag without a new network request when possible (local derivation by stripping timestamps).

* ` 💾 `︲**Persistent local cache** (`lrc_cache.json`) : every artist/title pair already resolved (found, not found, or instrumental) is cached to avoid re-querying the API on later runs. Periodic writes (every 200 changes) plus a save on exit. "Not found" entries carry a **30-day TTL** : a track absent from the API today will be retried automatically in a month.

* ` ⚙️ `︲**Multithreaded processing** : `ThreadPoolExecutor` with an adjustable thread count in the interface (1–32 for injection, 1–16 for checking).

* ` 🔁 `︲**Resilient HTTP requests** : shared `requests` session with a retry strategy (2 attempts, exponential backoff) on status codes `500`, `502`, `503` — `429` (rate limit) is handled separately via a coordinated pause honoring `Retry-After`. Two selectable paces: **SPEED** (unlimited bursts) or **COOL** (smoothed rate, see [Configuration](#configuration)).

* ` 🚦 `︲**SPEED / COOL API mode** : SPEED (default) does unlimited bursts — the scan runs at maximum throughput until LRCLIB rate-limits, takes a single coordinated pause, then resumes. COOL enforces a smooth flow of about 10 requests/s (token bucket) for polite and quiet scans.

* ` ⏹️ `︲**Clean cancellation** : a `STOP` button interrupts the current batch (`threading.Event`), without corrupting the cache or leaving orphan threads.

* ` 🖥️ `︲**Native dark interface** : `customtkinter` theme (custom palette matching the `style_v2.css` design mock, rounded cards, fixed paddings per component type, colored `START`/`STOP`/`SPEED`/`COOL` buttons), status-colored log (`OK`, `MISS`, `REJECT`, `SKIP`, `ERROR`, `INST`), smooth determinate/indeterminate progress bar. Windows-only polish, silently skipped elsewhere: DPI awareness (per-monitor, `ctypes`) and dark title bar (`DwmSetWindowAttribute`).

---

<a id="preview"></a>
# `🖼️`︲Preview.

---

<details>
  <summary>📸︲Main interface</summary>
  <img width="1220" height="980" alt="image" src="https://github.com/user-attachments/assets/ca5f23f3-a2d4-4927-8688-b3f0042ada76" />
</details>

---

<details>
  <summary>📸︲Log in progress.</summary>
  <img width="1220" height="980" alt="image" src="https://github.com/user-attachments/assets/4a687a0b-e159-405c-8354-f562b8e19592" />
  <img width="1220" height="980" alt="image" src="https://github.com/user-attachments/assets/b3d258a6-5b8b-4785-8290-15ec2d66d8f4" />
  <img width="1220" height="978" alt="image" src="https://github.com/user-attachments/assets/29edc5ae-2c61-4326-8f0d-c6a744f52714" />
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
pip install requests rapidfuzz mutagen customtkinter
```

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

**6️⃣ Run the tests (14 tests, ~ 2 s).**

```bash
python test_core.py
```

**7️⃣ (Optional) Diagnose a failing startup.**

* `diag.bat` (Windows) : double-click it — the terminal stays open, prints the Python interpreter, the state of the dependencies, then writes the startup trace to `diag.log` and the exit code.
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
* ` 🚀 ` **API mode (SPEED / COOL)** : two buttons in the Options card, in the "API rate" row.
  - **SPEED** *(default)* : unlimited bursts — the library is scanned at the maximum throughput your threads and connection allow. LRCLIB eventually throttles (`429`), the tool takes a single coordinated `[Rate limit]` pause (honoring `Retry-After`) then resumes at full speed. **Use it** for fast cold mass scans, when the cache is still empty.
  - **COOL** : smoothed throughput of about 10 requests/s via a shared token bucket. **Use it** when frequent rate limits get annoying (small libraries, partial re-runs, polite sources / ban avoidance), or simply for a perfectly smooth flow without hiccups.
  - `Retry-After` is always honored in both modes (LRCLIB requirement, else temporary ban). See the [official docs](https://lrclib.net/docs) for recommendations: mandatory User-Agent, 200–500 ms throttling, `duration` encouraged.
* ` 🪪 `︲**User-Agent** : set to `LRC-Injekt/1.0.1 (+https://github.com/LRC-Injekt)` — mandatory per the LRCLIB documentation to avoid a ban.
* ` ⏱️ `︲**Track duration sent**: the FLAC `length` tag (when available) is passed as `duration` to the API — ±2 s matching as recommended, which filters out false positives.
* ` 💾 ` **Cache** : `lrc_cache.json` file, generated automatically next to the script (versioned schema `v:1`, backward-compatible — an unknown schema is ignored). "Not found" entries have a **30-day TTL** and are re-queried automatically after expiry. No custom path option so far.
* ` 🎯 `︲**Similarity threshold** : hardcoded to `85%` (`rapidfuzz.fuzz.ratio`) in the source code, not exposed in the interface.
* ` 📄 `︲**File log (optional)** : set the `LRC_LOG_FILE` environment variable to a `.log` file to keep a persistent trace (appended in batches) in addition to the window.

---

<a id="architecture"></a>
# `🧩`︲Project architecture.

---

> [!IMPORTANT]
> The project is structured as **pure core + interface** :
>
> - `core.py` — pure logic (cleaning, fuzzy matching, LRC parsing, disk cache), **no Tkinter**, importable and testable on its own.
> - `lrc-inject.py` — CustomTkinter interface + network/threads orchestration.
> - `test_core.py` — assert-based test suite (14 tests, `python test_core.py`).
> - `diag.py` / `diag.bat` — startup inspection build (interpreter, dependencies, trace written to `diag.log`, exit code).

| Functional block                     | Role                                                                |
|--------------------------------------|---------------------------------------------------------------------|
| `get_session()`                      | Shared `requests` session with HTTP retry strategy.                 |
| `_load_cache()` / `_save_cache()` / `_mark_dirty()`    | Disk cache management (`lrc_cache.json`) — `core.py`, periodic flush, `v:1` schema. |
| `fetch_lrc()` / `_search_fallback()` / `_parse_result()` | Lyrics fetching via the LRCLIB API (direct query + budgeted search fallback, best candidate). |
| `clean()` / `match()` / `strip_timestamps()` | String normalization, fuzzy comparison, `LYRICS` → `UNSYNCEDLYRICS` derivation — `core.py`. |
| `_collect_flac()`                    | Recursive scan + inode-based deduplication (hardlinks/duplicates).  |
| `process_file()` / `run()`           | **Injection** mode logic (START) on a file / a batch.               |
| `check_one()` / `check_files()`      | **Audit/repair** mode logic (CHECK) on a file / a batch.            |
| `log()` / `_flush_log()`             | Log queue + batched rendering to the `Text` widget, colored per tag (optional `LRC_LOG_FILE`). |
| `_wait_token()` / `_rate_limit_pause()` / `_api_get()` | Rate policy: optional token bucket (COOL, ~10 req/s) and coordinated pause on `429` honoring `Retry-After` (all modes). |
| `start_pulse()` / `stop_pulse()` / `update_progress()` | Progress bar control (indeterminate then determinate). |
| `window_v2` / `Palette` / `Body` sections | CustomTkinter UI building (palette matching the `style_v2.css` mock, options/log cards, buttons, status bar). |
| `diag.bat` + `ctrl_c()` / `raise_soft_exit()` | Startup diagnostics (interpreter, dependencies, trace in `diag.log`) and soft cancellation (`CTRL+C` on terminal interruption). |

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

* ` 🪣 `︲**Token bucket for COOL mode** : smooths the flow to ~10 req/s (packet-based token consumption, under lock) — a steady, burst-free rhythm for LRCLIB.

* ` 🎯 `︲**Budgeted fallback search** (12 s max) : the best candidate is selected by combined scoring (artist ratio + title ratio) without ever exceeding the total budget.

* ` 📊 `︲**Throttled progress updates** : the bar refreshes only ~200 steps per batch (not a `set()` per file) — the Tkinter loop stays smooth on large volumes.

* ` 🍃 `︲**Minimal memory footprint** : ~22 MB in RAM with the CustomTkinter theme (vs ~20 MB with raw Tk, 150–200 MB for a web-based interface like pywebview).

---

<a id="choices"></a>
# `⚖️`︲Technical Choices & limitations.

> The tool has undergone **11 consecutive audits** and **38 bugs fixed** before reaching this maturity level. The choices below are deliberate and documented to avoid false positives on future reviews.

| Choice | Rationale |
|--------|-----------|
| **In-place FLAC writing** (`audio.save()` without temp file) | mutagen 1.46+ does not support `save(tmp)` to a new file — it checks the FLAC header of the output file, which fails on an empty one. In-place `save()` writes the new Vorbis tags at the head of the file. If the Vorbis block changes size (systematically the case with `LYRICS` + `UNSYNCEDLYRICS`), `resize_bytes` physically shifts the audio data on disk. A crash/power loss *during this shift* can truncate the audio, not just the tags. This is very unlikely (a few-ms window per file), but documented for transparency. For maximum safety, back up your library before batch processing. |
| **Broad `except Exception`** | GUI application : a silent failure logged with context is better than an unhandled traceback that closes the window. Every error is logged with its context. |
| **Extracted pure core (`core.py`)** | The pure logic (cleaning, matching, parsing, cache) was extracted from `lrc-inject.py` (~750 lines, GUI + orchestration) — now unit-testable (14 tests). |
| **`daemon=True` on workers** | The `join(timeout=30)` in `on_close()` leaves time to finish. If the timeout expires, the thread is killed ; the in-place write can leave a file mid-shift in an unstable state. Ideally, wait for the processing to finish before closing the app. |
| **No-TTL cache** | The `no_sync` and `inst` entries are permanent; "not found" entries expire after **30 days**. The user has a `Clear Cache` button to start from scratch if needed. |
| **Two API rates** | No fixed rate limiter in the code : **COOL** uses a shared token bucket (~10 req/s), **SPEED** (default) allows bursts and relies on a single coordinated pause on `429` — `Retry-After` always honored. |
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
