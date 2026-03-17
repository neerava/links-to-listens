# URL to Podcast — Claude Code Guide

## Overview

Fully local URL-to-podcast pipeline. No cloud APIs. Monitors `urls.txt`, scrapes URLs, summarizes via Ollama (local LLM), synthesises audio via VibeVoice (local TTS), serves episodes through a FastAPI web UI.

## Architecture

```
urls.txt → watcher.py → script_api.py (scrape+LLM) → audio_api.py (TTS) → MP3 + web UI
```

Single FastAPI app on port 8080 (`app.py`). Script and audio APIs are routers mounted at `/generate-script` and `/generate-audio`. Watcher runs as a separate process.

## Key Files

| File | Purpose |
|------|---------|
| `app.py` | FastAPI app: web UI, admin, mounts script/audio routers |
| `watcher.py` | Polls `urls.txt`, drives the pipeline state machine |
| `script_api.py` | URL → podcast script via Ollama; router + standalone |
| `audio_api.py` | Script → MP3 via VibeVoice; router + standalone |
| `pipeline_state.py` | Stage enum, PipelineRun, PipelineStateStore |
| `job_queue.py` | Single-worker FIFO queue (shared by both APIs) |
| `tts.py` | VibeVoice TTS; each synthesis runs in a fresh subprocess |
| `scraper.py` | Web scraping (httpx + trafilatura) |
| `summarizer.py` | Ollama LLM integration |
| `metadata.py` | Thread-safe atomic JSON episode store (`metadata.json`) |
| `config.py` | Settings loader; env-var overrides via `PODCAST_<KEY>` |
| `models.py` | Episode dataclass |
| `templates/` | Jinja2 HTML templates (extend `base.html`) |

## Running

```bash
./run.sh                          # starts app + watcher together
uvicorn app:app --port 8080       # app only
python watcher.py                 # watcher only
```

## Testing

```bash
.venv/bin/python -m pytest tests/ -v          # all tests
.venv/bin/python -m pytest tests/unit/ -v     # unit only
.venv/bin/python -m pytest tests/integration/ -v
```

Tests set `PODCAST_TTS_IN_PROCESS=1` (via `tests/conftest.py`) so TTS mocks work without subprocess isolation.

## Configuration

Edit `config.yaml`. All settings overridable at runtime with `PODCAST_<KEY>` env vars.

Key settings: `ollama_model`, `ollama_url`, `tts_ddpm_steps`, `tts_cfg_scale`, `tts_mp3_bitrate`, `tts_voice_sample`, `output_dir`, `web_port`, `intermediate_retention_days`.

## Important Behaviours

- **Pipeline lock:** `.pipeline.lock` serialises concurrent pipeline runs across watcher and web app to prevent OOM from dual TTS loads.
- **Admin regenerate guard:** Old episode stays in `metadata.json` until new one succeeds, preventing watcher from double-processing.
- **TTS subprocess isolation:** Each `synthesize()` call spawns a fresh `spawn`-method subprocess; exits to reclaim GPU/MPS memory.
- **Intermediate files:** `output/pipeline/{run-id}/` — `state.json` kept forever; `input_text.txt` (scraped article), `prompt.txt` (full Ollama prompt), `script.txt`, `tts_input.txt` pruned after `intermediate_retention_days` days.
- **Comments in urls.txt:** Lines starting with `#` are ignored.

## Documentation Workflow

When behaviour changes in code, update `README.md`, `PRD.md`, `plan.md`, and `TODO.md` in the same change.
