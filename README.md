# URL to Podcast

Drop a URL into a text file. Get a listenable audio summary.

A fully local system that monitors `urls.txt` for new URLs, scrapes and summarizes each page using a local LLM (Ollama), converts the summary to an MP3 using VibeVoice, and serves all generated episodes through a local web UI.

No cloud APIs. No subscriptions. Everything stays on your machine.

---

## How it works

```
urls.txt  →  Watcher  →  Script API  →  Audio API  →  MP3 + Web UI
                         (scrape +       (TTS)
                          summarize)
```

The pipeline is split into two independent, queueing APIs:

1. **Script API** (`script_api.py`) — takes a URL, scrapes the article, and generates a conversational podcast script via Ollama.
2. **Audio API** (`audio_api.py`) — takes a script, synthesises it to MP3 via VibeVoice.

Both APIs are mounted as routers inside the main `app.py` FastAPI application and served on port 8080. The watcher calls the same service functions directly (in-process). Each API processes one job at a time; extra requests are queued.

---

## Services at a glance

| Service | Port | UI | Purpose |
|---------|------|----|---------|
| Web UI + admin + Script API + Audio API | 8080 | `http://localhost:8080` | All routes served on one port |

All routes are served by a single FastAPI app (`app.py`) on port 8080. The script and audio routers are mounted at `/generate-script` and `/generate-audio` respectively.

---

## Requirements

- Python 3.10+
- [Ollama](https://ollama.ai) running locally
- [VibeVoice](https://vibevoice.ai) installed
- `ffmpeg` (required for chunked WAV concatenation and MP3 encoding)

---

## Setup

```bash
# 1. Clone and create a virtual environment
git clone <repo-url> url-to-podcast
cd url-to-podcast
python3 -m venv .venv
source .venv/bin/activate

# 2. Install Python dependencies
pip install -r requirements.txt

# 3. Install and start Ollama, then pull a model
brew install ollama          # macOS
ollama pull llama3

# 4. Install VibeVoice and ffmpeg
pip install vibevoice
brew install ffmpeg
```

---

## Usage

**Start everything with one command:**
```bash
./run.sh
```

`run.sh` checks that Ollama is reachable and VibeVoice is installed, starts the combined app (web UI + script API + audio API) and the watcher, and shuts them all down cleanly on Ctrl+C.

**Or manually:**
```bash
# Combined app: Web UI + admin + Script API + Audio API (all on port 8080)
uvicorn app:app --host 0.0.0.0 --port 8080

# URL watcher (automatic processing)
python watcher.py
```

**Standalone API modules (optional, for development):**
```bash
# Script API standalone on its own port (uses script_api_port from config.yaml)
python script_api.py

# Audio API standalone on its own port (uses audio_api_port from config.yaml)
python audio_api.py
```

**Add a URL for automatic processing:**
```bash
echo "https://example.com/some-article" >> urls.txt
```

Or queue it directly from the home page at `http://localhost:8080/` using the built-in URL submission form.

The watcher detects queued URLs, generates a script, synthesises audio, and the episode appears in the web UI at `http://localhost:8080`.

---

## Web UIs

All four UIs share a **consistent top bar** (brand + Episodes, Admin, Generate Script, Generate Audio) and the same dark theme. Layouts are **responsive**: on narrow screens the nav collapses into a hamburger menu. Templates extend a shared `base.html` with a common design system (CSS variables, cards, buttons).

### Episode Player — `http://localhost:8080/`
Browse and play all generated podcast episodes. Each episode shows thumbnail, title, description, source link, and an embedded audio player. A “now playing” bar appears at the bottom when any episode is playing. The top of the page also includes a “Queue a New URL” form that appends validated links to `urls.txt` for watcher pickup.

### Admin Panel — `http://localhost:8080/admin`
- **Hide/Show** episodes from the public player
- **Delete** episodes permanently (removes the audio file)
- **Regenerate** episodes from their original URL (runs the full pipeline again)

### URL → Script UI — `http://localhost:8080/generate-script`
Paste any article URL and get a ready-to-record podcast script. Jobs run in the background — the page polls for completion automatically. Job history is tracked in your browser cookies so you can close the page and come back later.

### Script → Audio UI — `http://localhost:8080/generate-audio`
Paste a script or upload a `.txt` file to synthesise an MP3. Blank scripts are rejected before synthesis starts. Same async job model — submit, close the page, return when done to download the file. Job history with download links is stored in browser cookies.

---

## API reference

All routes are on port 8080.

### Queue API

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/urls` | Queue a URL for watcher processing |

**Queue a URL from the home page flow:**
```bash
curl -X POST http://localhost:8080/api/urls \
  -H 'Content-Type: application/json' \
  -d '{"url": "https://example.com/article"}'
# → {"status": "queued", "message": "..."}
```

### Script API

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/generate-script` | Web UI |
| `POST` | `/generate-script/submit` | Submit a URL; returns `{"job_id": "..."}` |
| `GET` | `/generate-script/jobs/{id}` | Poll job status and result |
| `GET` | `/health` | Health check |

**Submit a script job:**
```bash
curl -X POST http://localhost:8080/generate-script/submit \
  -H 'Content-Type: application/json' \
  -d '{"url": "https://example.com/article"}'
# → {"job_id": "3f2a1b..."}
```

**Poll for result:**
```bash
curl http://localhost:8080/generate-script/jobs/3f2a1b...
# → {"status": "done", "result": {"title": "...", "script": "..."}, ...}
```

### Audio API

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/generate-audio` | Web UI |
| `POST` | `/generate-audio/submit` | Submit a script; returns `{"job_id": "..."}` |
| `GET` | `/generate-audio/jobs/{id}` | Poll job status |
| `GET` | `/generate-audio/jobs/{id}/download` | Download the generated MP3 |
| `GET` | `/health` | Health check |

**Submit an audio job:**
```bash
curl -X POST http://localhost:8080/generate-audio/submit \
  -H 'Content-Type: application/json' \
  -d '{"script": "Welcome to the show...", "title": "My Episode"}'
# → {"job_id": "9c4d2e..."}
```

**Download when done:**
```bash
curl -OJ http://localhost:8080/generate-audio/jobs/9c4d2e.../download
```

---

## Configuration

Edit `config.yaml` to change any default:

| Key | Default | Description |
|-----|---------|-------------|
| `ollama_model` | `llama3` | Ollama model to use |
| `ollama_url` | `http://localhost:11434` | Ollama API endpoint |
| `ollama_prompt` | *(see config.yaml)* | System prompt for script generation |
| `tts_voice` | `default` | VibeVoice voice profile |
| `tts_voice_sample` | `""` | Path to a reference WAV for voice cloning (24 kHz mono recommended) |
| `tts_ddpm_steps` | `15` | Diffusion steps (1–50); higher = better fidelity, slower (try 20 for best) |
| `tts_cfg_scale` | `1.3` | Classifier-free guidance (1.0–2.0); 1.4–1.5 can improve voice clarity |
| `tts_mp3_bitrate` | `192` | MP3 bitrate in kbps: 128, 192, 256, or 320 (256/320 = higher fidelity) |
| `tts_use_float32` | `false` | If true, use float32 on GPU/MPS for higher quality (~2× memory; may OOM) |
| `tts_chunk_sentences` | `10` | Sentences per TTS inference call; must be greater than `0` |
| `scrape_timeout_sec` | `15` | HTTP request timeout |
| `output_dir` | `./output` | Directory for generated MP3 files |
| `web_port` | `8080` | Web UI + admin port |
| `script_api_port` | `8081` | Script API port when run standalone (optional; empty/null falls back to 8081) |
| `audio_api_port` | `8082` | Audio API port when run standalone (optional; empty/null falls back to 8082) |
| `poll_interval_sec` | `5` | How often the watcher checks `urls.txt` |
| `max_input_tokens` | `4096` | Max tokens of article text sent to LLM |

Any setting can also be overridden at runtime with a `PODCAST_` environment variable:

```bash
PODCAST_OLLAMA_MODEL=mistral ./run.sh
PODCAST_SCRIPT_API_PORT=9001 python script_api.py   # standalone only
PODCAST_TTS_DDPM_STEPS=20 PODCAST_TTS_MP3_BITRATE=320 ./run.sh   # higher fidelity
```

### Higher-fidelity audio

The default setup (VibeVoice-1.5B, float16, 15 diffusion steps, 192 kbps MP3) balances quality and speed. For better fidelity:

1. **Voice sample** — Use a clear 24 kHz mono WAV (3–10 seconds of speech) in `tts_voice_sample`. This has the biggest impact.
2. **Diffusion steps** — Set `tts_ddpm_steps: 20` (or up to 50) for smoother, more detailed audio; synthesis will be slower.
3. **MP3 bitrate** — Set `tts_mp3_bitrate: 256` or `320` to reduce compression artifacts.
4. **Guidance** — Try `tts_cfg_scale: 1.4` or `1.5` for stronger voice consistency (too high can sound overdriven).
5. **Float32** — Set `tts_use_float32: true` to run the model in full precision on MPS/CUDA. Improves clarity but uses roughly twice the memory and can cause OOM on smaller machines.
6. **Larger model** — VibeVoice-7B yields higher quality but needs ~18 GB+ VRAM; the codebase currently uses the 1.5B model only.

---

## Project structure

```
url-to-podcast/
├── urls.txt              # Input: one URL per line
├── output/               # Generated MP3 files (watcher output)
│   └── api_audio/        # MP3 files generated via the Audio API
├── metadata.json         # Episode metadata (auto-created)
├── .pipeline.lock       # File lock for pipeline serialization (auto-created, in .gitignore)
├── config.yaml           # All configurable settings
├── run.sh                # Convenience launcher (starts app + watcher; port from config)
│
├── job_queue.py          # Single-worker FIFO job queue (shared by both APIs)
├── script_api.py         # Script Generation router + generate_script() (also runnable standalone)
├── audio_api.py          # Audio Generation router + generate_audio() (also runnable standalone)
├── watcher.py            # URL poll loop — calls generate_script() + generate_audio()
├── scraper.py            # Web scraping (httpx + trafilatura)
├── summarizer.py         # Ollama LLM integration
├── tts.py                # VibeVoice TTS
├── metadata.py           # Thread-safe atomic JSON episode store
├── app.py                # FastAPI app (port 8080): mounts script_router + audio_router, web UI, admin
├── models.py             # Episode dataclass
├── config.py             # Settings loader, env-var overrides, validation
│
├── templates/
│   ├── base.html         # Shared layout, nav bar, and design system
│   ├── partials/
│   │   └── nav.html      # Top bar (Episodes, Admin, Generate Script, Generate Audio)
│   ├── index.html        # Public podcast player (extends base)
│   ├── admin.html        # Admin panel — hide, delete, regenerate (extends base)
│   ├── script_ui.html    # URL → script web UI (extends base)
│   └── audio_ui.html     # Script → audio web UI (extends base)
│
├── tests/
│   ├── unit/             # No external dependencies
│   └── integration/      # FastAPI endpoints + watcher pipeline
│
└── requirements.txt
```

---

## Running tests

```bash
# All tests (no Ollama or VibeVoice needed)
.venv/bin/python -m pytest tests/ -v

# Unit tests only
.venv/bin/python -m pytest tests/unit/ -v

# Integration tests only
.venv/bin/python -m pytest tests/integration/ -v
```

---

## Notes

- **No reprocessing:** URLs already in `metadata.json` are skipped on restart.
- **Fault isolation:** A failure on one URL (bad page, Ollama error, TTS error) is logged and skipped — the watcher continues with the next URL.
- **Home-page URL queueing:** `POST /api/urls` validates and appends new links to `urls.txt`. Duplicate queued URLs are ignored, and already-processed URLs are reported without being re-added.
- **Single worker per API:** `job_queue.py` guarantees at most one `generate_script` and one `generate_audio` run at a time. Additional requests wait in a FIFO queue. This prevents resource exhaustion from concurrent LLM or TTS calls.
- **VibeVoice subprocess isolation:** Each call to `synthesize()` in `tts.py` spawns a fresh `multiprocessing` subprocess (using the `spawn` start method). The subprocess loads the VibeVoice model, generates all audio chunks, writes the merged WAV, and then exits. Process exit reclaims all GPU/MPS memory cleanly with no residual state between runs. A parent-process lock (`_tts_lock`) serialises concurrent calls so only one synthesis subprocess runs at a time. A hard timeout of 30 minutes applies per synthesis call. Tests bypass the subprocess via `PODCAST_TTS_IN_PROCESS=1` (set in `tests/conftest.py`) so mocks remain visible to the test process.
- **Pipeline lock:** Only one full pipeline run (scrape → summarize → TTS) executes at a time across the watcher and the web app. A file lock (`.pipeline.lock` in the project root) ensures that if you trigger “Regenerate” from the admin UI while the watcher is already processing a URL, the second run waits for the first to finish. This avoids loading the TTS model twice and prevents out-of-memory errors on machines with limited RAM.
- **Job persistence:** Job results are held in memory for the lifetime of the process. If you restart a server, in-flight jobs are lost. The audio files in `output/api_audio/` persist across restarts.
- **Browser cookie tracking:** The script and audio UIs store job IDs in browser cookies (90-day expiry, up to 20 per UI). On return visits the page automatically resumes polling any in-progress jobs and shows completed job history.
- **Long articles:** Content is truncated to `max_input_tokens` before being sent to the LLM.
- **TTS validation:** Audio generation fails fast with clear errors when the script is empty, `tts_chunk_sentences` is invalid, or `ffmpeg` is unavailable.
- **TTS script normalization:** Before handing text to VibeVoice, embedded newlines and repeated whitespace are flattened so each emitted line cleanly matches the `Speaker N:` format expected by the upstream parser. This reduces noisy `Could not parse line` warnings during synthesis.
- **Apple Silicon safety:** MPS cache flushing is only used when MPS is actually available, which avoids post-chunk crashes on CPU-only macOS runs.
- **Comments in urls.txt:** Lines starting with `#` are ignored.
- **Thumbnails:** Extracted from `og:image` / `twitter:image` meta tags. Served through a local proxy (`/img?url=...`) with on-disk caching to avoid CORS and repeated fetches.
- **Health checks:** `GET /health` available on the main server (port 8080). Also available when running `script_api.py` or `audio_api.py` standalone.
- **Single process:** `run.sh` starts one uvicorn process (`app:app` on port 8080) plus the watcher. There is no longer a separate process for the script or audio APIs.
- **Documentation workflow:** When behavior changes in code, the repo docs (`README.md`, `PRD.md`, `plan.md`, `TODO.md`) should be updated in the same change.
