# Product Requirements Document: Links to Listens

**Version:** 1.9
**Date:** 2026-03-22
**Status:** Implemented

---

## 1. Overview

A fully local system that monitors a text file for URLs, scrapes and summarizes the content using a local LLM, converts the summary to audio using a local TTS engine, and serves all generated episodes through a local web UI.

The pipeline is split into two independent processing stages — one for script generation (URL → text) and one for audio synthesis (text → MP3) — each with its own web UI, job queue, and browser-cookie-based job history. Both stages are mounted as routers inside the main FastAPI app and served on a single port (8080).

---

## 2. Goals

- Automatically convert web articles/pages into listenable podcast-style audio summaries.
- Run entirely offline (except for scraping external URLs).
- Require minimal manual intervention — drop a URL, get an episode.
- Expose each processing stage as an independently callable HTTP API.
- Support long-running jobs with a "come back later" UX — no blocking waits.

## 3. Non-Goals

- Cloud-hosted deployment or SaaS functionality.
- Multi-user support or authentication.
- Mobile app or browser extension.
- Running separate server processes for script and audio APIs (all now served via the main app on port 8080).
- Job result persistence across server restarts.

---

## 4. User Stories

| # | As a... | I want to... | So that... |
|---|---------|-------------|------------|
| 1 | User | Add a URL to `urls.txt` | It gets automatically processed into an audio episode |
| 1a | User | Paste a URL into the home page form | It gets appended to the watcher queue without editing files manually |
| 2 | User | Listen to a conversational summary of an article | I can consume content hands-free |
| 3 | User | See all generated episodes in a web UI | I can browse and replay past summaries |
| 4 | User | See the source URL and timestamp for each episode | I can trace back the original content |
| 5 | User | Paste a URL into a web form and get a podcast script | I can generate a one-off script without editing files |
| 6 | User | Paste or upload a script and get an MP3 download | I can convert a script to audio on demand |
| 7 | User | Close the page while a job runs and come back later | I don't have to wait watching a spinner |
| 8 | Developer | Call the script and audio stages as independent HTTP APIs | I can build other tools on top of each stage |

---

## 5. Functional Requirements

### 5.1 URL Watcher
- **FR-01:** The system MUST monitor `urls.txt` for newly added URLs.
- **FR-02:** Each URL MUST be processed exactly once (no re-processing on restart).
- **FR-03:** The watcher MUST handle invalid/unreachable URLs gracefully without crashing.
- **FR-03a:** The watcher MUST delegate to `generate_script()` and `generate_audio()` service functions so changes to either stage apply consistently across watcher and API usage.

### 5.2 Web Scraper
- **FR-04:** The system MUST scrape the main content of a given URL, stripping navigation, ads, and boilerplate.
- **FR-04a:** The scraper MUST extract the page's thumbnail image (og:image or twitter:image) when available.
- **FR-05:** The scraper MUST handle common content types: articles, blog posts, and documentation pages.
- **FR-06:** The scraper MUST time out after a configurable period (default: 15 seconds).

### 5.3 LLM Summarizer (via Ollama)
- **FR-07:** The system MUST send scraped content to a local Ollama model to generate a podcast-style, conversational script.
- **FR-08:** The script MUST sound natural for audio — no bullet points, headers, or markdown.
- **FR-09:** The model and prompt MUST be configurable.

### 5.4 TTS Engine
- **FR-10:** The system MUST convert the generated script to an MP3 audio file using **VibeVoice**.
- **FR-11:** Audio files for the watcher pipeline MUST be saved to the `output/` directory. Audio files generated via the Audio API MUST be saved to `output/api_audio/`.
- **FR-12:** Watcher-generated filenames MUST be deterministic and human-readable (slug of title + UTC timestamp).

### 5.5 Episode Metadata
- **FR-13:** For each watcher-generated episode, the system MUST store: title, description, thumbnail URL, source URL, generation timestamp, and audio file path.
- **FR-13a:** The title and description MUST be extracted from the article text by the LLM, not derived from the podcast script.
- **FR-14:** Metadata MUST persist across restarts (stored as JSON).

### 5.6 Web UI (Port 8080)
- **FR-15:** The system MUST expose a local web app listing all generated episodes.
- **FR-16:** Each episode entry MUST display: thumbnail image tile, title, description, source URL, timestamp, and an embedded HTML5 audio player.
- **FR-16a:** Thumbnail images MUST be served through a local proxy (`/img?url=...`) to avoid CORS/mixed-content issues.
- **FR-16b:** The home page MUST provide a URL submission form that validates HTTP/HTTPS links and queues them for watcher processing.
- **FR-17:** The UI MUST be accessible at `http://localhost:<port>` (default port configurable).
- **FR-18:** No login or authentication required.

### 5.6a Queue Intake API
- **FR-18a:** The app MUST expose a `POST /api/urls` endpoint that appends new URLs to `urls.txt`.
- **FR-18b:** The queue intake endpoint MUST return clear statuses for `queued`, `already_queued`, and `already_processed` submissions.
- **FR-18c:** The queue intake endpoint MUST reject invalid URLs with a client error.

### 5.7 Script Generation API (mounted at `/generate-script`)
- **FR-19:** The system MUST provide a `POST /generate-script/submit` endpoint that accepts `{"url": "..."}` and returns a job ID immediately.
- **FR-20:** The endpoint MUST enqueue the job and process it in the background with a single worker (no concurrent LLM calls).
- **FR-21:** A `GET /generate-script/jobs/{id}` endpoint MUST return job status (`pending`, `running`, `done`, `failed`), queue position when pending, and the full result (title, description, thumbnail_url, script) when done.
- **FR-22:** The API MUST serve a web UI at `GET /generate-script` for submitting URLs and viewing results.
- **FR-23:** The `generate_script(url, settings)` function MUST be importable directly for in-process use by the watcher.

### 5.8 Audio Generation API (mounted at `/generate-audio`)
- **FR-24:** The system MUST provide a `POST /generate-audio/submit` endpoint that accepts `{"script": "...", "title": "..."}` and returns a job ID immediately.
- **FR-25:** The endpoint MUST enqueue the job and process it with a single worker (no concurrent TTS calls).
- **FR-26:** A `GET /generate-audio/jobs/{id}` endpoint MUST return job status and a `file_available` flag when done.
- **FR-27:** A `GET /generate-audio/jobs/{id}/download` endpoint MUST serve the generated MP3 as a file download.
- **FR-28:** The API MUST serve a web UI at `GET /generate-audio` supporting both paste-text and file-upload (`.txt`) input modes.
- **FR-29:** The `generate_audio(script, output_path, settings)` function MUST be importable directly for in-process use by the watcher.
- **FR-29a:** Audio generation MUST fail fast with clear errors when the script is empty, `tts_chunk_sentences` is invalid, or `ffmpeg` is unavailable.
- **FR-29b:** Each `synthesize()` call MUST run VibeVoice in a dedicated `multiprocessing` subprocess (using the `spawn` start method) so that all GPU/MPS memory is reclaimed on process exit with no residual state between runs. A parent-process lock MUST serialise concurrent calls so only one synthesis subprocess runs at a time. A hard timeout of 30 minutes MUST apply per synthesis call.
- **FR-29c:** Before VibeVoice processing, the script MUST be normalized so embedded newlines do not produce raw non-`Speaker N:` lines for the upstream parser.
- **FR-29d:** Across processes (watcher vs. web app), only one full pipeline run (scrape → summarize → TTS) MUST be allowed at a time via a cross-process lock (e.g. file lock) to prevent loading the TTS model twice and OOM.

### 5.9 Job Queue
- **FR-30:** Each API MUST use a single-worker FIFO queue (`job_queue.py`) so that at most one script generation job and one queued audio API job run at a time per process.
- **FR-31:** Job submissions MUST return a job ID without blocking.
- **FR-32:** Jobs MUST expose: `id`, `status`, `created_at`, `started_at`, `finished_at`, `result`, `error`, and `queue_position` (when pending).

### 5.11 Watcher Pipeline State Machine
- **FR-36:** For each URL processed by the watcher, the system MUST create a run directory `output/pipeline/{run-id}/` and write a `state.json` file tracking: run ID, URL, current stage (`pending`, `script`, `tts`, `done`, `failed`), timestamps (`created_at`, `updated_at`), output paths, and error message.
- **FR-37:** After script generation, the system MUST save the raw Ollama output to `script.txt` and the Speaker-labelled VibeVoice input to `tts_input.txt` within the run directory.
- **FR-38:** `state.json` and the final MP3 MUST never be auto-deleted. Intermediate files (`script.txt`, `tts_input.txt`) MUST be pruned automatically after `intermediate_retention_days` days (default: 3). Pruning MUST run at watcher startup and then once per day.
- **FR-39:** The pipeline state machine MUST cover the watcher pipeline only. API jobs (Script API, Audio API) continue to use the existing in-memory job queue.

### 5.12 Podbean Publishing
- **FR-40:** The admin panel MUST provide a "Publish to Podbean" button per episode when `podbean_client_id` and `podbean_client_secret` are configured.
- **FR-41:** Publishing MUST upload the episode MP3 to Podbean via OAuth 2.0 + presigned URL, then create the episode with title and description.
- **FR-42:** Publishing MUST run in a background thread and return immediately. The episode's `podbean_episode_id` and `podbean_episode_url` MUST be persisted to metadata on success.
- **FR-43:** An already-published episode MUST show a "Published" link to the Podbean URL instead of a Publish button.
- **FR-43a:** Clicking "Publish" MUST open an editable form pre-filled with the episode's title, description, and thumbnail preview. The user MAY edit title and description, and optionally upload a new thumbnail image (JPG/PNG/GIF, max 2 MB) before confirming. Edited values are sent to Podbean only — local episode data is unchanged.
- **FR-44:** The Publish button MUST be hidden entirely when Podbean credentials are not configured.
- **FR-45:** `config.yaml` MUST be gitignored. `config.yaml.sample` is the committed template with safe defaults and no secrets.

### 5.13 Telegram Bot
- **FR-46:** The system MUST support an optional Telegram bot that accepts URLs via chat messages and queues them for processing using the same `enqueue_url()` mechanism as the web UI.
- **FR-46a:** The bot MUST extract URLs from messages that contain surrounding text (e.g., "Check out https://example.com please"), using Telegram entity detection with a regex fallback. If no URL is found, the bot MUST reply with "No URL found".
- **FR-47:** The bot MUST reply with one of five statuses: queued, already queued, already processed, invalid URL, or no URL found.
- **FR-48:** The bot MUST support optional access control via `telegram_allowed_user_ids` (comma-separated). When empty, any user may submit URLs.
- **FR-49:** The bot MUST run as a separate process, launched conditionally by `run.sh` when `telegram_bot_token` is configured.
- **FR-50:** The bot MUST respond to `/start` with a welcome message.

### 5.10 Browser Cookie Job Tracking
- **FR-33:** Both web UIs MUST store submitted job IDs in browser cookies (up to 20 per UI, 90-day expiry).
- **FR-34:** On page load, the UI MUST automatically resume polling any in-progress job found in the cookie.
- **FR-35:** The history section MUST display all prior jobs with their status and appropriate actions (copy script / download MP3).

---

## 6. Non-Functional Requirements

- **NFR-01 Language:** All code in Python 3.10+.
- **NFR-02 LLM:** Must use Ollama as the local LLM runtime.
- **NFR-03 TTS:** Must use a local TTS engine (no cloud APIs).
- **NFR-04 Privacy:** No user data or scraped content leaves the local machine.
- **NFR-05 Performance:** End-to-end processing (scrape → summarize → TTS) SHOULD complete within 3 minutes per URL on consumer hardware.
- **NFR-06 Reliability:** The watcher process MUST recover from individual URL processing failures without stopping.
- **NFR-07 Concurrency:** Each API server MUST process at most one job at a time to prevent resource exhaustion from concurrent LLM or TTS workloads.
- **NFR-08 TTS Safety:** On Apple Silicon, accelerator-specific cache flushing MUST only run when MPS is actually available, to avoid post-chunk crashes during CPU-only runs.

---

## 7. System Architecture

```
urls.txt
    │
    ▼
[URL Watcher]  (watcher.py)
    │  calls generate_script() + generate_audio() in-process
    ├─────────────────────────────────────────────────────────┐
    ▼                                                         │
[Script API]  (script_api.py — script_router)                │
    │  mounted at /generate-script inside app.py              │
    │  single-worker job queue                                │
    ├── scraper.py  (httpx + trafilatura)                     │
    └── summarizer.py  (Ollama REST)                          │
         │  ScriptResult                                      │
         ▼                                                    │
[Audio API]  (audio_api.py — audio_router)                   │
    │  mounted at /generate-audio inside app.py               │
    │  single-worker job queue                                │
    │  plus process-wide TTS serialization in tts.py          │
    └── tts.py  (VibeVoice)                                   │
         │  MP3 → output/api_audio/                           │
         │  (watcher: output/)  ◄────────────────────────────┘
         ▼
[Metadata Store]  (metadata.json)
    │
    ▼
[Web UI + Admin + Script API + Audio API]  (app.py — port 8080)
    └── http://localhost:8080
```

---

## 8. File & Directory Structure

```
links-to-listens/
├── urls.txt                  # Input: one URL per line
├── output/                   # Watcher-generated MP3 files
│   ├── api_audio/            # Audio API job MP3 files
│   └── pipeline/             # Per-run state dirs: {run-id}/state.json, script.txt, tts_input.txt
├── metadata.json             # Episode metadata store (auto-created)
├── config.yaml               # Configurable settings
├── run.sh                    # Convenience launcher (starts all services)
│
├── job_queue.py              # Shared single-worker FIFO job queue
├── pipeline_state.py         # Watcher pipeline state machine (Stage, PipelineRun, PipelineStateStore)
├── script_api.py             # Script Generation API + generate_script()
├── audio_api.py              # Audio Generation API + generate_audio()
├── watcher.py                # URL watcher + orchestrator; drives pipeline state machine
├── scraper.py                # Web scraping logic
├── summarizer.py             # Ollama LLM integration
├── tts.py                    # VibeVoice TTS integration
├── app.py                    # FastAPI web UI + admin
├── metadata.py               # Thread-safe JSON episode store
├── models.py                 # Episode dataclass
├── config.py                 # Settings loader, env-var overrides, validation
│
├── templates/
│   ├── base.html             # Shared layout, nav bar, design system
│   ├── partials/
│   │   └── nav.html          # Top bar (Episodes, Admin, Generate Script, Generate Audio)
│   ├── index.html            # Public podcast player (extends base)
│   ├── admin.html            # Admin panel (extends base)
│   ├── script_ui.html       # URL → script UI (extends base)
│   └── audio_ui.html        # Script → audio UI (extends base)
│
├── tests/
│   ├── unit/                 # No external dependencies
│   └── integration/          # FastAPI + watcher pipeline (mocked)
└── requirements.txt
```

---

## 9. Configuration (`config.yaml`)

| Key | Default | Description |
|-----|---------|-------------|
| `ollama_model` | `gpt-oss:20b` | Ollama model to use |
| `ollama_url` | `http://localhost:11434` | Ollama API endpoint |
| `ollama_prompt` | *(see config.yaml)* | System prompt for podcast script generation |
| `tts_engine` | `vibevoice` | TTS engine name |
| `tts_voice` | `default` | Voice profile |
| `tts_voice_sample` | `""` | Path to a reference WAV for voice cloning (24kHz mono) |
| `tts_ddpm_steps` | `15` | Diffusion steps (1–50); higher = better fidelity, slower |
| `tts_cfg_scale` | `1.3` | Classifier-free guidance (1.0–2.0) for voice consistency |
| `tts_mp3_bitrate` | `192` | MP3 bitrate: 128, 192, 256, or 320 kbps |
| `tts_use_float32` | `false` | If true, use float32 on GPU/MPS (better quality, ~2× memory) |
| `tts_chunk_sentences` | `10` | Sentences per TTS inference call; must be greater than `0` |
| `scrape_timeout_sec` | `15` | HTTP request timeout |
| `output_dir` | `./output` | Directory for watcher MP3 files |
| `web_port` | `8080` | Web UI + admin port |
| `script_api_port` | `8081` | Script API port (standalone/dev use only — not used when running via app.py) |
| `audio_api_port` | `8082` | Audio API port (standalone/dev use only — not used when running via app.py) |
| `poll_interval_sec` | `5` | How often to check `urls.txt` |
| `max_input_tokens` | `4096` | Max tokens of scraped content sent to LLM |
| `intermediate_retention_days` | `3` | Days to keep `script.txt` / `tts_input.txt` before auto-deletion |

All keys can be overridden at runtime via `PODCAST_<KEY>` environment variables.

---

## 10. Out of Scope (v1 / v1.7)

- Scheduling / cron-based processing
- Push notifications when an episode is ready
- Support for paywalled content
- Podcast RSS feed generation
- Job persistence across server restarts
- Admin authentication

---

## 11. Open Questions

| # | Question | Owner |
|---|----------|-------|
| ~~Q1~~ | ~~Which TTS engine (VibeVoice vs. Coqui TTS vs. Piper)?~~ | **Resolved: VibeVoice** |
| ~~Q2~~ | ~~Metadata store: flat JSON vs. SQLite?~~ | **Resolved: flat JSON (v1)** |
| ~~Q3~~ | ~~Web framework: Flask vs. FastAPI?~~ | **Resolved: FastAPI** |
| ~~Q4~~ | ~~How to handle very long articles that exceed LLM context window?~~ | **Resolved: truncate to max input tokens (configurable)** |
| Q5 | Should audio API job files be cleaned up after N days? | Open |
| Q6 | Should job results survive server restart (e.g. SQLite job store)? | Open |

---

## 12. Implementation Notes

### v1.1
- **`models.py` / `metadata.py` added** — the dataclass and store were split into their own modules for testability.
- **`config.py` added** — settings loading, env-var overrides, and fail-fast validation extracted to a dedicated module.
- **`ollama_prompt` is configurable** — the system prompt is a first-class config key.
- **`run.sh` launcher** — starts both services, runs preflight checks, shuts down cleanly on Ctrl+C.
- **Path traversal guard on `/audio/{filename}`** — the audio endpoint rejects filenames containing directory separators.
- **`/health` endpoint** — added on the web UI for basic operational visibility.
- **Home page queue flow** — the public index page now includes a URL submission form backed by `POST /api/urls`, so users can enqueue watcher work without editing `urls.txt` manually.

### v1.2
- **Pipeline split into two independent APIs** — `script_api.py` (URL → script) and `audio_api.py` (script → MP3) each run as independent FastAPI apps on separate ports. Both expose service functions (`generate_script`, `generate_audio`) that are directly importable by the watcher.
- **`job_queue.py`** — shared single-worker FIFO queue. Each API has its own queue instance, guaranteeing at most one LLM call and one TTS synthesis run at a time. Accepts `**kwargs`, returns a job ID immediately, and stores results in memory.
- **TTS guardrails** — `tts.py` rejects blank scripts early, validates `tts_chunk_sentences`, checks `ffmpeg` before both chunk concat and MP3 encoding, normalizes embedded whitespace before `Speaker N:` labeling, and uses a process-wide lock to serialize synthesis across watcher, API, and admin-triggered runs.
- **Async job UIs** — `templates/script_ui.html` and `templates/audio_ui.html` are modern dark-themed single-page interfaces (Tailwind CSS, Inter font) that poll job status every 3 seconds, resume in-progress jobs on page reload via browser cookies, and show a full job history panel.
- **Cookie-based job tracking** — JS cookie stores up to 20 job IDs per UI with 90-day expiry. No server-side session required.
- **Audio API file storage** — audio files generated via the HTTP API are written to `output/api_audio/{uuid}.mp3` and served via a dedicated `/audio/jobs/{id}/download` endpoint. The `file_available` flag in the job status response tells the UI whether the file still exists.
- **Watcher refactored** — `watcher.process_url()` now delegates to `generate_script()` and `generate_audio()` service functions rather than calling scraper/summarizer/tts directly.
- **`script_api_port` and `audio_api_port`** added to `config.yaml` and `config.py` with port range validation.

### v1.3
- **APIs consolidated onto port 8080** — `script_api.py` and `audio_api.py` now export `script_router` and `audio_router` (FastAPI `APIRouter` instances) that are included in `app.py` at prefixes `/generate-script` and `/generate-audio` respectively. All endpoints are served by the single uvicorn process on port 8080. `run.sh` now starts one uvicorn process plus the watcher (previously three uvicorn processes).
- **New URL structure** — script endpoints moved from `/script` and `/script/jobs/{id}` (port 8081) to `/generate-script/submit` and `/generate-script/jobs/{id}` (port 8080). Audio endpoints moved from `/audio` and `/audio/jobs/{id}` (port 8082) to `/generate-audio/submit` and `/generate-audio/jobs/{id}` (port 8080).
- **Navigation bar** — a consistent nav bar was added to all four templates (`index.html`, `admin.html`, `script_ui.html`, `audio_ui.html`) so users can move between sections without manually editing the URL.
- **`api_prefix` Jinja2 variable** — `script_ui.html` and `audio_ui.html` use an `api_prefix` template variable (e.g. `/generate-script`) when constructing API call URLs. This keeps the templates portable: when running `script_api.py` or `audio_api.py` standalone for development, the router serves them at `/` and passes an empty prefix; when mounted inside `app.py` the correct prefix is injected.
- **`script_api_port` / `audio_api_port`** — these config keys are retained for standalone/dev use when running each API module directly (`python script_api.py`, `python audio_api.py`). They have no effect when the app is started via `app.py`.
- **TTS hardening** — synthesis is serialized within the process to avoid overlapping VibeVoice runs from the watcher, audio API, and admin regenerate flow. Empty scripts and invalid `tts_chunk_sentences` values now fail fast, `ffmpeg` is checked before both WAV concat and MP3 encoding, and embedded newlines are flattened before `Speaker N:` labeling so VibeVoice does not emit noisy parse warnings. MPS cache flushing is only used when MPS is truly available.
- **Public URL queueing** — `watcher.py` now exposes a shared enqueue helper used by `app.py`, and the home page can append validated URLs directly into the watcher input queue.

### v1.4
- **Cross-process pipeline lock** — Only one full pipeline run (watcher or admin Regenerate) at a time system-wide. `watcher.py` uses a file lock (`.pipeline.lock`) around `process_url()` so the web app and watcher never run TTS in parallel in different processes, avoiding double-loading VibeVoice and OOM.
- **Responsive UI and shared top bar** — All four UIs extend `base.html` and include `partials/nav.html`. Consistent top bar (brand + Episodes, Admin, Generate Script, Generate Audio); responsive with hamburger menu on narrow screens. Script and audio UIs use the same design system (no Tailwind).
- **Higher-fidelity TTS options** — Config: `tts_ddpm_steps`, `tts_cfg_scale`, `tts_mp3_bitrate`, `tts_use_float32`. Validation and env overrides in `config.py`. README “Higher-fidelity audio” section with presets.
- **TTS memory and subprocess hygiene** — Chunk inference uses try/finally so device memory is flushed on save errors; docstrings note that `subprocess.run()` reaps ffmpeg (no zombies).
- **Config and launcher** — `run.sh` port reading made robust (empty/null ports default to 8081/8082). Ollama prompt in `config.yaml` improved; VibeVoice `verbose=False` to reduce log noise.

### v1.5
- **VibeVoice subprocess isolation** — `synthesize()` in `tts.py` now spawns a fresh `multiprocessing` subprocess (using the `spawn` start method) for every synthesis call. The subprocess loads the model, generates all audio chunks, writes the merged WAV, and exits; process exit reclaims all GPU/MPS memory with no residual state between runs. A parent-process lock serialises concurrent calls; a hard 30-minute timeout applies per call. Tests bypass the subprocess via `PODCAST_TTS_IN_PROCESS=1` so mocks remain visible. All other synthesis behaviour (chunked generation, voice sample, ffmpeg MP3 conversion, configurable DDPM steps/CFG scale/bitrate) is unchanged.

### v1.6
- **Pipeline state machine** — `pipeline_state.py` adds a lightweight state machine for the watcher pipeline. `Stage` enum tracks `pending → script → tts → done | failed`. `PipelineRun` dataclass captures run ID, URL, stage, timestamps, output paths, and error. `PipelineStateStore` creates `output/pipeline/{run-id}/` per URL run, writes `state.json`, saves `script.txt` (raw Ollama output) and `tts_input.txt` (Speaker-labelled VibeVoice input), and prunes intermediates older than `intermediate_retention_days` days.
- **`watcher.py` state integration** — `process_url()` drives the state machine through all stages (PENDING → SCRIPT → TTS → DONE|FAILED), saving `script.txt` after `generate_script()` and passing `tts_input_path` to `generate_audio()`. `run()` creates `PipelineStateStore`, prunes at startup, and re-prunes once per day.
- **`tts.py`** — `synthesize()` accepts an optional `save_tts_input: Path` parameter and writes the Speaker-labelled formatted script to that path before synthesis.
- **`audio_api.py`** — `generate_audio()` accepts an optional `tts_input_path: Path` and passes it through to `synthesize()`.
- **`config.py` / `config.yaml`** — new field `intermediate_retention_days: int = 3`; `pipeline_path` derived from `output_path / "pipeline"`.

### v1.7
- **Admin regenerate double-creation fix** (`app.py`) — The cross-process guard uses `metadata.json` as the coordination point (not a `_failed_urls` Python set, which only lives in one process). The old episode is **not deleted** in the HTTP handler; it stays in `metadata.json` so the watcher sees `is_processed(url)=True` and skips the URL. A background thread calls `process_url()`, and only on success does it delete the old episode and audio file. On failure the old episode remains visible and the watcher continues to skip the URL. The `_failed_urls` guard and `_pipeline_store` module-level export were removed from `watcher.py` (unreachable cross-process).
- **Pipeline state for admin regen** (`app.py`) — `_pipeline_store` in `watcher.py` was always `None` in the web-app process because `watcher.run()` is never called there. `app.py` now creates its own `PipelineStateStore` instance at module level pointing to `output/pipeline/`, so admin-triggered regens create `output/pipeline/{run-id}/` state directories just like watcher runs.
- **TTS subprocess audit** — all three entry points that call TTS (`_run_once`, `admin_regenerate`, `_audio_worker`) confirmed to route through `generate_audio → synthesize`, which spawns the VibeVoice subprocess. The Script API (`generate_script`) does not call TTS.
