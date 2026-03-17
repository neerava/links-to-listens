# Agentic Development Plan: URL-to-Podcast

**Generated:** 2026-03-15
**Updated:** 2026-03-16
**Source:** PRD v1.4
**Status:** All tasks completed

---

## 1. System Architecture

```
urls.txt (input)
    │
    ▼
URLWatcher (watcher.py)              — polls every N seconds, tracks processed URLs
    │  calls service functions in-process
    │
    ├─────────────────────────────────────────────────────────────────┐
    │                                                                 │
    ▼                                                                 │
ScriptAPI (script_api.py — script_router)                            │
    │  mounted at /generate-script inside app.py (port 8080)         │
    │  JobQueue: single worker, FIFO                                  │
    ├── generate_script(url, settings) → ScriptResult                 │
    │       ├── WebScraper (scraper.py)    — httpx + trafilatura      │
    │       └── OllamaSummarizer (summarizer.py) — local LLM          │
    │                                                                 │
    ▼                                                                 │
AudioAPI (audio_api.py — audio_router)                               │
    │  mounted at /generate-audio inside app.py (port 8080)          │
    │  JobQueue: single worker, FIFO                                  │
    └── generate_audio(script, path, settings) → Path                │
            └── TTSEngine (tts.py) — VibeVoice                       │
                   │  MP3 → output/api_audio/                        │
                   │  (watcher: output/) ◄────────────────────────────┘
                   ▼
            MetadataStore (metadata.json)
                   │
                   ▼
            WebUI + Admin + Script API + Audio API (app.py — port 8080)
```

**Data flow contracts:**
- Scraper → Summarizer: plain UTF-8 string, max `max_input_tokens` tokens
- Summarizer → TTS: plain prose string (no markdown/bullets)
- TTS → Store: relative filename of generated MP3
- Store → UI: list of `Episode` dicts from `metadata.json`
- ScriptAPI → caller: `ScriptResult(title, description, thumbnail_url, script)`
- AudioAPI → caller: `Path` to generated MP3

---

## 2. Tech Stack Decisions

| Concern | Choice | Rationale |
|---------|--------|-----------|
| Language | Python 3.10+ | Mandated by NFR-01 |
| Web scraping | `httpx` + `trafilatura` | `trafilatura` excels at main-content extraction; `httpx` for timeout control |
| LLM runtime | Ollama REST API (`httpx`) | Mandated by NFR-02; no extra SDK needed |
| TTS | VibeVoice Python API | Mandated by FR-10 |
| Web framework | FastAPI + Jinja2 | Serves static MP3s, HTML UIs, and JSON APIs |
| Job queue | In-memory threading (`job_queue.py`) | Simple, sufficient for single-user local use; no Redis/Celery needed |
| Metadata store | Flat JSON (`metadata.json`) | Simple for v1; easy to inspect |
| Config | `config.yaml` + PyYAML | Human-readable; env-var override pattern |
| File watching | Polling loop (no inotify) | Cross-platform; sufficient at 5s intervals |
| Testing | pytest + pytest-httpx + respx | Lightweight, async-compatible |
| Packaging | `requirements.txt` | Simple; no packaging overhead for v1 |
| Frontend | Shared base template + custom CSS | `base.html` + `partials/nav.html`; all four UIs extend base; responsive with hamburger nav; no Tailwind in script/audio UIs |

---

## 3. Development Milestones

| Milestone | Deliverable | Status |
|-----------|-------------|--------|
| M0 — Scaffold | `models.py`, `config.py`, `config.yaml`, `requirements.txt` | ✅ Done |
| M1 — Scraper | `scraper.py` with timeout + content extraction | ✅ Done |
| M2 — Summarizer | `summarizer.py` with Ollama integration | ✅ Done |
| M3 — TTS | `tts.py` wrapping VibeVoice | ✅ Done |
| M4 — Metadata | `metadata.py` — thread-safe atomic JSON store | ✅ Done |
| M5 — Web UI | `app.py` + `templates/index.html` + `templates/admin.html` | ✅ Done |
| M6 — Watcher | `watcher.py` — poll loop, graceful shutdown | ✅ Done |
| M7 — Hardening | Per-module logging, typed errors, config validation, path traversal guard | ✅ Done |
| M8 — Launcher | `run.sh` — preflight checks, starts all services, cleans up on exit | ✅ Done |
| M9 — API Split | `script_api.py` + `audio_api.py` as independent FastAPI apps | ✅ Done |
| M10 — Job Queue | `job_queue.py` — single-worker FIFO, in-memory job store | ✅ Done |
| M11 — Async UIs | `templates/script_ui.html` + `templates/audio_ui.html` with cookie tracking | ✅ Done |
| M12 — Config | `script_api_port`, `audio_api_port` in config.yaml + validation | ✅ Done |
| M13 — Port consolidation + nav | Routers mounted in app.py on port 8080; nav bar added to all four templates | ✅ Done |
| M14 — Pipeline lock + UI + TTS quality | Cross-process pipeline lock; responsive shared top bar; higher-fidelity TTS config; TTS memory cleanup; run.sh port handling | ✅ Done |

---

## 4. Key Design Decisions

### 4.1 Service function pattern
Both `script_api.py` and `audio_api.py` expose their core logic as importable Python functions (`generate_script`, `generate_audio`). The watcher calls these directly (in-process, no HTTP overhead). External tools use the HTTP APIs. This means:
- No code duplication between watcher and API code paths.
- The watcher picks up any changes to the service functions automatically.
- Unit tests can call the service functions directly without running a server.

### 4.2 Single-worker job queue
`job_queue.py` runs a background daemon thread that processes one job at a time. This is intentional:
- LLM inference (Ollama) and TTS synthesis (VibeVoice) are CPU/GPU-bound and memory-intensive. Concurrent calls would cause OOM or severe slowdown.
- The queue position is exposed in the API so UIs can show "Position 2 in queue".
- Each API instance has its own queue — script generation and audio synthesis can proceed in parallel with each other, just not concurrently within the same stage.

### 4.2a Process-wide TTS serialization
The watcher and admin regenerate flow call `generate_audio()` directly, so the audio API queue alone is not enough to prevent overlapping synthesis. `tts.py` therefore also owns a process-wide lock around `synthesize()`. This means:
- Only one VibeVoice run can execute at a time inside a given app process.
- The watcher, audio API worker, and admin regenerate thread share the same protection.
- We avoid double-loading or overlapping execution on constrained CPU/GPU/MPS hardware.

### 4.2b Cross-process pipeline lock
The watcher runs as a separate process from the main app (uvicorn). If both process a URL at once (e.g. watcher picks up a URL and the user triggers “Regenerate” for another), each process would load VibeVoice independently, leading to high memory use and possible OOM. `watcher.py` therefore uses a file lock (`.pipeline.lock`) around the full pipeline so that only one pipeline run (watcher or app) executes at a time system-wide. The lock is acquired at the start of `process_url()` and released when the pipeline completes.

### 4.3 Cookie-based job tracking (no server-side sessions)
Job IDs are stored in browser cookies (client-side). The server only needs `GET /generate-script/jobs/{id}` and `GET /generate-audio/jobs/{id}`. This means:
- No session store required.
- Works across page refreshes and tab closes.
- Script job cookies and audio job cookies use distinct cookie names to avoid mixing (both UIs are on the same origin, port 8080).

### 4.4 Audio file lifetime
Audio files generated via the HTTP API are written to `output/api_audio/` and kept indefinitely. The `file_available` flag in the job status response reflects whether the file exists on disk. A future cleanup policy (e.g. TTL) can be added without changing the API contract.

### 4.5 Home-page queue flow
The watcher still uses `urls.txt` as its source of truth, but the public home page now provides a submission form backed by `POST /api/urls`. This means:
- Users can queue URLs without opening the filesystem.
- The app reuses watcher-side queue semantics instead of creating a second intake system.
- Duplicate queued URLs and already-processed URLs can be handled consistently before the watcher poll loop runs.

---

## 5. Task List

### Completed Tasks (v1)

**TASK-01 — Project Scaffold**
- `models.py`, `config.py`, `config.yaml`, `requirements.txt`

**TASK-02 — Web Scraper (`scraper.py`)**
- `scrape(url, settings) → ScrapeResult`
- httpx + trafilatura, thumbnail extraction, truncation, `ScraperError`

**TASK-03 — Ollama Summarizer (`summarizer.py`)**
- `extract_metadata(text, settings) → ArticleMetadata`
- `summarize(text, settings) → str`
- JSON extraction with fallback, `SummarizerError`

**TASK-04 — TTS Engine (`tts.py`)**
- `synthesize(script, output_path, settings) → Path`
- VibeVoice Python API, chunk-based inference, ffmpeg concat + MP3 encode
- Process-wide synthesis lock plus fast-fail validation for empty scripts and invalid chunk settings
- Normalize embedded whitespace before `Speaker 0:` labeling so VibeVoice's line parser receives clean speaker-formatted input

**TASK-05 — Metadata Store (`metadata.py`)**
- `MetadataStore`: `append`, `load`, `is_processed`, `get_by_id`, `update`, `delete`
- Thread-safe atomic writes

**TASK-06 — Web UI (`app.py` + templates)**
- Public player, admin panel, audio serving, image proxy + cache
- `/health` endpoint
- Home-page URL submission form backed by `POST /api/urls`

**TASK-07 — URL Watcher (`watcher.py`)**
- Poll loop, graceful SIGINT/SIGTERM shutdown, per-URL failure isolation

**TASK-08 — Logging & Error Handling**
- Structured logging in every module

**TASK-09 — Config Validation**
- Fail-fast on bad Ollama URL, bad port, unwritable output dir, missing voice sample
- Validate `tts_chunk_sentences > 0`

**TASK-10 — Tests**
- 100+ passing (unit + integration, no external deps required)

**TASK-11 — `run.sh` Launcher**
- Preflight checks, starts all services, trap-based cleanup

---

### Completed Tasks (v1.2)

**TASK-12 — API Split**
- `script_api.py`: `generate_script()` function + FastAPI app with `POST /script`, `GET /script/jobs/{id}`, `GET /`, `GET /health`
- `audio_api.py`: `generate_audio()` function + FastAPI app with `POST /audio`, `GET /audio/jobs/{id}`, `GET /audio/jobs/{id}/download`, `GET /`, `GET /health`

**TASK-13 — Job Queue (`job_queue.py`)**
- `Job` dataclass with `to_dict()` serialization
- `JobQueue(worker_fn)`: `submit(**kwargs) → str`, `get(id) → Job | None`, `queue_position(id) → int`
- Background daemon thread, thread-safe with `threading.Lock` + `threading.Event`

**TASK-14 — Script UI (`templates/script_ui.html`)**
- Dark theme (bg-gray-950, indigo accent)
- URL input → `POST /script` → async polling
- States: pending (queue position), running (indeterminate bar), done (script textarea + copy), failed
- Cookie history with expand/copy per job

**TASK-15 — Audio UI (`templates/audio_ui.html`)**
- Dark theme (bg-gray-950, teal accent)
- Two-tab input: paste textarea / drag-and-drop `.txt` upload
- Async polling with states: pending, running (with "you can close this page" note), done (download button), failed
- Cookie history with download links per completed job

**TASK-16 — Config Additions**
- `script_api_port: 8081` and `audio_api_port: 8082` in `Settings`, `config.yaml`
- Port validation loop covers all three ports

**TASK-17 — Watcher Refactor**
- Removed direct imports of `scraper`, `summarizer`, `tts`
- `process_url()` calls `generate_script()` → `generate_audio()` service functions
- `_derive_title()` helper removed (now handled inside `generate_script` via `extract_metadata`)

---

### Completed Tasks (v1.3)

**TASK-18 — Port Consolidation + Navigation Bar**
- Consolidated Script and Audio APIs onto port 8080 via FastAPI router includes. `script_api.py` and `audio_api.py` now export `script_router` and `audio_router` (FastAPI `APIRouter` instances) that are included in `app.py` at prefixes `/generate-script` and `/generate-audio` respectively.
- `run.sh` now starts one uvicorn process (`app:app` on port 8080) plus the watcher — previously three uvicorn processes.
- All API endpoints updated to new paths: `POST /generate-script/submit`, `GET /generate-script/jobs/{id}`, `POST /generate-audio/submit`, `GET /generate-audio/jobs/{id}`, `GET /generate-audio/jobs/{id}/download`.
- Added navigation bar to all four templates (`index.html`, `admin.html`, `script_ui.html`, `audio_ui.html`) for consistent in-app navigation.
- `api_prefix` Jinja2 variable injected into `script_ui.html` and `audio_ui.html` so API call URLs are constructed portably. When mounted in `app.py` the prefix is `/generate-script` or `/generate-audio`; when running standalone the prefix is empty, preserving backwards compatibility for dev use.

**TASK-19 — TTS Stability Fixes (`tts.py`)**
- **Chunk device synchronization** — added `torch.mps.synchronize()` / `torch.cuda.synchronize()` before cache clearing in `_flush_device_cache()` so all async GPU/MPS operations from chunk N complete before chunk N+1 starts. Previously, async operations could bleed across chunk boundaries causing silent corruption or crashes.
- **Safe MPS guard** — MPS cache flushing now runs only when `torch.backends.mps.is_available()` is true, avoiding post-chunk crashes on CPU-only macOS runs where `torch.mps` exists but is not usable.
- **Suppressed per-chunk progress bars** — `show_progress_bar=False` passed to `model.generate()` to eliminate noisy tqdm output per chunk.
- **Voice configuration** — voice is configured via `tts_voice_sample` (path to a WAV file) in `config.yaml`. If empty or the file is not found, a 3-second silent WAV is generated as a fallback. The VibeVoice GitHub pre-built `.pt` embeddings (Carter, Emma, etc.) are precomputed for the Realtime 0.5B streaming model and are incompatible with the 1.5B model this project uses; they should not be used.
- **Global TTS serialization** — `synthesize()` now uses a process-wide lock so watcher jobs, audio API jobs, and admin-triggered regeneration cannot overlap inside the same process.
- **Fast-fail TTS validation** — blank scripts, invalid `tts_chunk_sentences`, and missing `ffmpeg` now fail early with clear `TTSError`s.
- **Script normalization for VibeVoice** — embedded newlines and repeated whitespace are flattened before sentence labeling so the upstream parser does not warn about raw lines that lack a `Speaker N:` prefix.

**TASK-20 — Home-page URL Queueing**
- Added a public “Queue a New URL” form to `templates/index.html`.
- Added `POST /api/urls` in `app.py` to validate and enqueue URLs.
- Added `watcher.enqueue_url()` so the web app and watcher share the same `urls.txt` queueing behavior.
- Added integration coverage for queued, duplicate, processed, and invalid submissions.

---

### Completed Tasks (v1.4)

**TASK-21 — Cross-process pipeline lock**
- Only one full pipeline run (scrape → summarize → TTS) at a time across watcher and web app.
- File lock (`.pipeline.lock`) in project root; `process_url()` acquires it so admin “Regenerate” and watcher never run TTS concurrently in different processes. Prevents double-loading VibeVoice and OOM.

**TASK-22 — Responsive UI and shared top bar**
- All four UIs use a shared `templates/base.html` and `templates/partials/nav.html` with a consistent top bar (brand + Episodes, Admin, Generate Script, Generate Audio).
- Responsive: nav collapses to hamburger menu on narrow screens. Unified design system (CSS variables, cards, buttons). Script and audio UIs no longer use Tailwind; they extend base and use the same tokens.

**TASK-23 — Higher-fidelity TTS options**
- Config: `tts_ddpm_steps` (1–50, default 15), `tts_cfg_scale` (1.0–2.0), `tts_mp3_bitrate` (128/192/256/320), `tts_use_float32` (optional float32 on MPS/CUDA). Validation and env overrides in `config.py`.
- TTS uses these in model load, generate, and MP3 encode. README documents “Higher-fidelity audio” with practical presets.

**TASK-24 — TTS memory and subprocess hygiene**
- `_generate_chunk_wav()` uses try/finally so tensors are deleted and device cache flushed even when save fails, avoiding device memory leaks.
- Docstrings note that `subprocess.run()` reaps ffmpeg children (no zombie processes).

**TASK-25 — Config and launcher**
- `run.sh` port reading: `get_ports()` uses `os.getcwd()` and normalizes empty/null/invalid port values to defaults so script_api_port and audio_api_port never produce invalid uvicorn args.
- Ollama prompt in `config.yaml` improved (length guidance, rules list, no generic intros, output-only instruction). VibeVoice generate called with `verbose=False` to suppress “Samples [0] reached EOS” logs.

---

## 6. Testing Strategy

### Unit Tests (`tests/unit/`)
| File | Tests |
|------|-------|
| `test_scraper.py` | Mock HTTP responses; timeout, 404, empty extraction |
| `test_summarizer.py` | Mock Ollama API; prose output, error handling, metadata extraction, JSON parsing, fallback |
| `test_tts.py` | Mock VibeVoice; path creation, empty-script rejection, embedded-newline normalization, MPS guard, TTSError on missing binary |
| `test_metadata.py` | CRUD ops, atomic write, duplicate detection, empty file, backward compat |
| `test_config.py` | Valid config loads, invalid values raise ConfigError, chunk-size validation |

### Integration Tests (`tests/integration/`)
| File | Tests |
|------|-------|
| `test_app.py` | FastAPI endpoints with seeded metadata, home-page URL queueing |
| `test_watcher.py` | One poll cycle with fully mocked pipeline |

### Gaps (future work)
- `test_job_queue.py` — submit/poll, single-worker guarantee, queue ordering
- `test_script_api.py` — POST /script, GET /script/jobs/{id}, error cases
- `test_audio_api.py` — POST /audio, GET /audio/jobs/{id}/download, 409/410 cases

### Test Conventions
- Use `pytest` with `pytest-asyncio` for async tests
- Use `respx` for mocking `httpx` calls
- Mark tests requiring real Ollama/VibeVoice with `@pytest.mark.integration`

---

## 7. Deployment Plan

### Local Run (Primary Use Case)

**Prerequisites:**
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
brew install ollama ffmpeg    # macOS
ollama pull llama3
pip install vibevoice
```

**Start all services (recommended):**
```bash
./run.sh
```

**Or manually in separate terminals:**
```bash
uvicorn app:app --host 0.0.0.0 --port 8080   # All-in-one: Web UI + admin + Script API + Audio API
python watcher.py                              # URL watcher
```

**Standalone API modules (optional, for development):**
```bash
python script_api.py   # Script API standalone (uses script_api_port from config.yaml)
python audio_api.py    # Audio API standalone (uses audio_api_port from config.yaml)
```

### Configuration Override
```bash
PODCAST_OLLAMA_MODEL=mistral ./run.sh
PODCAST_SCRIPT_API_PORT=9081 python script_api.py   # standalone only
```

### Directory Permissions
- `output/` — writable by running user
- `output/api_audio/` — created automatically by `audio_api.py` on startup
- `metadata.json` — writable by running user
- `urls.txt` — writable by running user

### Project Hygiene
- Update `README.md`, `PRD.md`, `plan.md`, and `TODO.md` whenever code changes alter product behavior, APIs, or implementation details.

### Known Limitations (v1.4)
- Job results are in-memory only; restart loses all pending/running/done job state.
- No process supervision (`supervisord`, `launchd`) — add for persistent background operation.
- No RSS feed.
- No authentication on any endpoint.
- Pipeline lock is best-effort across processes (file lock); if a process crashes without releasing, the lock file remains until the next successful run overwrites it.
