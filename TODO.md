# TODO

## Status
All v1.5 functional requirements implemented and verified.

## Completed

### v1 — Core Pipeline
- [x] TASK-01 Project scaffold (`models.py`, `config.py`, `config.yaml`, `requirements.txt`)
- [x] TASK-02 Web scraper with thumbnail extraction (`scraper.py`)
- [x] TASK-03 Ollama summarizer + metadata extraction (`summarizer.py`)
- [x] TASK-04 TTS engine — VibeVoice HuggingFace integration (`tts.py`)
- [x] TASK-05 Metadata store with CRUD ops (`metadata.py`)
- [x] TASK-06 Modern podcast player UI with image tiles, title, description (`templates/index.html`)
- [x] TASK-07 URL watcher / orchestrator with retry protection (`watcher.py`)
- [x] TASK-08 Logging & error handling (in every module)
- [x] TASK-09 Configuration validation (fail-fast in `config.py`)
- [x] TASK-10 Tests — 100 passing (68 unit, 32 integration)
- [x] TASK-11 `run.sh` convenience launcher (preflight checks, clean shutdown)
- [x] LLM-extracted article title and description
- [x] E2E verified: 5.1 MB MP3 generated from real URL
- [x] Bug fix: titles and descriptions no longer truncated mid-sentence
- [x] Bug fix: voice sample changed from noise to silence to eliminate background hiss
- [x] Configurable voice sample (`tts_voice_sample` in config.yaml)
- [x] Thumbnail extraction from og:image / twitter:image with local proxy + caching
- [x] Modern podcast player UI with tile layout, now-playing bar, responsive design
- [x] Admin UI at `/admin` — hide, delete, regenerate episodes

### v1.2 — API Split + Async UIs
- [x] TASK-12 Split pipeline into two independent FastAPI APIs
  - `script_api.py` — URL → podcast script (port 8081)
  - `audio_api.py` — script → MP3 (port 8082)
  - Both expose importable service functions (`generate_script`, `generate_audio`)
- [x] TASK-13 Single-worker FIFO job queue (`job_queue.py`)
  - At most one job runs per API at a time
  - Remaining requests are queued FIFO
  - Jobs expose status, timestamps, queue position, result, error
- [x] TASK-14 URL → Script web UI (`templates/script_ui.html`)
  - URL input with async job submission
  - Live polling (3 s interval) with status states (pending / running / done / failed)
  - Result display with inline script textarea and copy button
  - Cookie-based job history (20 jobs, 90-day expiry)
  - Resumes in-progress jobs automatically on page reload
- [x] TASK-15 Script → Audio web UI (`templates/audio_ui.html`)
  - Paste script or upload `.txt` file (with drag-and-drop)
  - Async job submission with live progress polling
  - Download MP3 button when job completes
  - Cookie-based job history with download links
  - Resumes in-progress jobs automatically on page reload
- [x] TASK-16 Config additions: `script_api_port` (8081), `audio_api_port` (8082)
  - Port range validation added for both new ports
- [x] TASK-17 Watcher refactored to use service functions
  - `process_url()` delegates to `generate_script()` + `generate_audio()`
  - Removed direct imports of scraper/summarizer/tts from watcher

### v1.3 — Port Consolidation + Nav Bar + TTS Fixes
- [x] TASK-18 APIs consolidated onto port 8080
  - `script_api.py` and `audio_api.py` export `script_router` / `audio_router` (FastAPI `APIRouter`)
  - Both routers mounted in `app.py` at `/generate-script` and `/generate-audio`
  - `run.sh` now starts one uvicorn process + watcher (previously three uvicorn processes)
  - All API endpoint paths updated (e.g. `POST /generate-script/submit`)
  - Navigation bar added to all four templates
  - `api_prefix` Jinja2 variable for portable URL construction in UIs
- [x] TASK-19 TTS stability fixes (`tts.py`)
  - Fixed device synchronization between chunks: `torch.mps.synchronize()` / `torch.cuda.synchronize()` called before cache clearing so async GPU/MPS operations from chunk N complete before chunk N+1 starts
  - Suppressed per-chunk tqdm output by passing `show_progress_bar=False` to `model.generate()`
  - Voice configuration: set `tts_voice_sample` in `config.yaml` to a WAV file path, or leave empty for silent fallback. The VibeVoice GitHub `.pt` embeddings (Carter, Emma, etc.) are for the Realtime 0.5B model only and are incompatible with the 1.5B model used here.
  - Normalized embedded newlines and repeated whitespace before `Speaker 0:` labeling to reduce VibeVoice `Could not parse line` warnings
- [x] TASK-20 Home-page URL queueing
  - Added a “Queue a New URL” form to the public index page
  - Added `POST /api/urls` to validate and append URLs to `urls.txt`
  - Prevents duplicate queued URLs and reports already-processed URLs cleanly
  - Added integration tests for queued / duplicate / processed / invalid URL submissions

### v1.4 — Pipeline Lock + UI + TTS Quality
- [x] Cross-process pipeline lock
  - Only one full pipeline run (watcher or admin Regenerate) at a time; file lock `.pipeline.lock` in project root
  - Prevents double-loading VibeVoice and OOM when watcher and web app run concurrently
- [x] Responsive UI and shared top bar
  - Shared `templates/base.html` and `templates/partials/nav.html`; all four UIs extend base
  - Consistent top bar: Episodes, Admin, Generate Script, Generate Audio; hamburger on narrow screens
  - Script and audio UIs no longer use Tailwind; same design system as index/admin
- [x] Higher-fidelity TTS options
  - Config: `tts_ddpm_steps`, `tts_cfg_scale`, `tts_mp3_bitrate`, `tts_use_float32`; validation and env overrides in `config.py`
  - README “Higher-fidelity audio” section with presets
- [x] TTS memory and subprocess hygiene
  - `_generate_chunk_wav()` try/finally so device memory is flushed on save errors
  - Docstrings note `subprocess.run()` reaps ffmpeg (no zombies)
- [x] Config and launcher
  - `run.sh` port reading robust: empty/null/invalid ports default to 8081/8082 so uvicorn never gets invalid `--port`
  - Ollama prompt in `config.yaml` improved (length, rules, no generic intros); VibeVoice `verbose=False` to reduce log noise

### v1.5 — VibeVoice Subprocess Isolation
- [x] TASK-26 VibeVoice runs in a dedicated subprocess per synthesis call
  - `synthesize()` spawns a fresh `multiprocessing` subprocess (`spawn` context) for every call
  - Subprocess loads model, generates all audio chunks, writes merged WAV, then exits — all GPU/MPS memory reclaimed cleanly
  - `_tts_lock` in parent process serialises concurrent `synthesize()` calls
  - Hard timeout: 30 minutes (`WORKER_TIMEOUT_SEC = 1800`) per synthesis call
  - Tests bypass subprocess via `PODCAST_TTS_IN_PROCESS=1` (set in `tests/conftest.py`) so mocks remain visible
  - All existing behaviour unchanged: chunked synthesis, voice sample, ffmpeg MP3 conversion, configurable DDPM steps/CFG scale/bitrate

## Pre-launch checklist
- [ ] Install Ollama and pull a model: `ollama pull llama3`
- [ ] Install VibeVoice: `pip install vibevoice`
- [ ] Install ffmpeg: `brew install ffmpeg`
- [ ] Optionally configure a voice sample: `tts_voice_sample` in `config.yaml`
- [ ] Optionally tune TTS quality: `tts_ddpm_steps`, `tts_cfg_scale`, `tts_mp3_bitrate`, `tts_use_float32` in `config.yaml` (see README “Higher-fidelity audio”)
- [ ] Run the full end-to-end smoke test: `./run.sh`

## Out of scope (future)
- [ ] RSS feed generation
- [ ] Push notifications when an episode is ready
- [ ] Support for paywalled content
- [ ] Process supervision (`launchd`, `supervisord`)
- [ ] AI-generated cover art when no og:image exists
- [ ] Admin authentication
- [ ] Persistent job store (survive server restart)
- [ ] Audio API job file cleanup / TTL policy
- [ ] Tests for `script_api.py`, `audio_api.py`, `job_queue.py`

## Project workflow
- [x] Update `README.md`, `PRD.md`, `plan.md`, and `TODO.md` whenever code changes alter product behavior, APIs, or implementation details
