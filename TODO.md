# TODO

## Status
All v1.3 functional requirements implemented and verified.

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

## Pre-launch checklist
- [ ] Install Ollama and pull a model: `ollama pull llama3`
- [ ] Install VibeVoice: `pip install vibevoice`
- [ ] Install ffmpeg: `brew install ffmpeg`
- [ ] Optionally configure a voice sample: `tts_voice_sample` in `config.yaml`
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
