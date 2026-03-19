# TODO

## Status
All v1.7 functional requirements implemented and verified.

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

### v1.7 — Admin Regen Fix + Pipeline State for Admin + TTS Audit
- [x] TASK-28 Admin regenerate double-creation bug fix, pipeline state for admin regen, TTS subprocess audit
  - Cross-process guard uses `metadata.json` (not `_failed_urls`): the old episode is **not deleted** in the HTTP handler so the watcher sees `is_processed(url)=True` and skips the URL; a background thread calls `process_url()` and only on success deletes the old episode + audio and writes the new one; on failure the old episode remains visible
  - `_failed_urls` guard and `_pipeline_store` module-level export removed from `watcher.py` (never reachable cross-process since the watcher is a separate OS process)
  - `app.py` creates its own `PipelineStateStore` at module level (pointing to `output/pipeline/`) so admin-triggered regens appear in `output/pipeline/` just like watcher runs (`_pipeline_store` in `watcher.py` was always `None` in the web-app process)
  - TTS subprocess audit confirmed: all three callers of `generate_audio` (`_run_once`, `admin_regenerate`, `_audio_worker`) route through `synthesize()` and the VibeVoice subprocess; Script API does not call TTS

### v1.6 — Pipeline State Machine
- [x] TASK-27 Watcher pipeline state machine (`pipeline_state.py`)
  - `Stage` enum: `pending → script → tts → done | failed`
  - `PipelineRun` dataclass tracks id, url, stage, timestamps, paths, error
  - `PipelineStateStore` creates `output/pipeline/{run-id}/` per URL run; writes `state.json`, `script.txt`, `tts_input.txt`
  - `state.json` and final MP3 never auto-deleted; intermediates pruned after `intermediate_retention_days` days (default 3)
  - Auto-prune runs at watcher startup and then once per day
  - `watcher.py` drives state machine through PENDING → SCRIPT → TTS → DONE|FAILED
  - `tts.py` `synthesize()` accepts optional `save_tts_input: Path` to persist Speaker-labelled input
  - `audio_api.py` `generate_audio()` accepts optional `tts_input_path: Path` and passes it to `synthesize()`
  - `config.py` / `config.yaml`: new `intermediate_retention_days` field; `pipeline_path` derived from `output_path / "pipeline"`

## Pre-launch checklist
- [ ] Install Ollama and pull a model: `ollama pull gpt-oss:20b`
- [ ] Install VibeVoice: `pip install vibevoice`
- [ ] Install ffmpeg: `brew install ffmpeg`
- [ ] Optionally configure a voice sample: `tts_voice_sample` in `config.yaml`
- [ ] Optionally tune TTS quality: `tts_ddpm_steps`, `tts_cfg_scale`, `tts_mp3_bitrate`, `tts_use_float32` in `config.yaml` (see README “Higher-fidelity audio”)
- [ ] Run the full end-to-end smoke test: `./run.sh`

## Out of scope (future)
- [ ] Publish episodes to rss.com via API
  - Two-step flow: presigned S3 upload → episode creation
  - API: `POST https://api.rss.com/v4/podcasts/{podcast_id}/assets/presigned-uploads` then `POST .../episodes`
  - Auth: `X-Api-Key` header (requires rss.com Network plan)
  - Config: `rsscom_api_key`, `rsscom_podcast_id` (empty = disabled)
  - New file: `rsscom.py` (presigned upload + S3 PUT + episode creation)
  - Admin UI: "Publish" button per episode (only shown when configured), runs in background thread
  - Episode model: add `rsscom_episode_id` field to track published state
- [ ] RSS feed generation
- [ ] Push notifications when an episode is ready
- [ ] Support for paywalled content
- [ ] Process supervision (`launchd`, `supervisord`)
- [ ] AI-generated episode thumbnails via Adobe Firefly
  - Use Ollama to generate an image prompt from article text
  - Call Firefly API (`POST https://firefly-api.adobe.io/v3/images/generate-async`) to generate artwork
  - Auth: Adobe Developer Console client_id + client_secret → OAuth token via `ims-na1.adobelogin.com`
  - Config: `firefly_client_id`, `firefly_client_secret` (empty = disabled, falls back to scraped og:image)
  - New file: `imagegen.py` (prompt generation + Firefly integration)
  - Save generated images to `output/thumbnails/{episode_id}.jpg`
  - Always generate when configured; scraped thumbnail as fallback on failure
- [ ] Admin authentication
- [ ] Persistent job store (survive server restart)
- [ ] Audio API job file cleanup / TTL policy

## Refactoring backlog

### High priority
- [ ] Incomplete `requirements.txt` — missing `torch`, `transformers`, `vibevoice`, `numpy`; fresh install cannot run TTS
- [ ] Config defaults mismatch between `config.py` and `config.yaml`:
  - `tts_use_float32`: yaml=`true`, py=`False`
  - `tts_chunk_sentences`: yaml=`50`, py=`10`
  - `scrape_timeout_sec`: yaml=`20`, py=`15`
- [ ] Test coverage gaps — no tests for `pipeline_state.py`, `job_queue.py`, `script_api.py`, `audio_api.py`, `models.py`
- [ ] Duplicate `import os` in `tts.py:13,18`

### Medium priority
- [ ] `app.py` overloaded — image proxy (lines 164-200), admin API (78-141), web UI, URL submission all in one file; extract `image_proxy.py` and `admin_service.py`
- [ ] Duplicated API boilerplate — `script_api.py` and `audio_api.py` have near-identical job queue setup, `/submit` + `/jobs/{id}` endpoints, and standalone app scaffolding; extract shared `create_api_router()` helper
- [ ] Duplicated HTTP error handling — `scraper.py` and `summarizer.py` both wrap httpx timeout/request errors identically; extract to `http_utils.py`
- [ ] No shared exception hierarchy — `ScraperError`, `SummarizerError`, `TTSError` each inherit from `Exception` independently; create `exceptions.py` with `PipelineError` base class
- [ ] Atomic file write duplication — `metadata.py` and `pipeline_state.py` both implement `.tmp` → rename pattern; extract to shared utility

### Low priority
- [ ] Template SVG icon repetition — same podcast icon in `base.html`, `index.html`, `admin.html`; extract to Jinja2 macro in `partials/icons.html`
- [ ] Flat file structure — fine at ~2.4K lines, consider packages (`core/`, `apis/`, `web/`) if project grows past ~3K
- [ ] `.gitignore` minor gaps — missing `*.egg-info/`, `dist/`, `build/`, `*.log`

## Project workflow
- [x] Update `README.md`, `docs/PRD.md`, `docs/plan.md`, and `docs/TODO.md` whenever code changes alter product behavior, APIs, or implementation details
