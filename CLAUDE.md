# Links to Listens — Claude Code Guide

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
| `script_api.py` | URL → podcast script via Ollama; router + standalone; per-request model override |
| `audio_api.py` | Script → MP3 via VibeVoice; router + standalone |
| `scrape_api.py` | URL → extracted article text + thumbnail; router mounted at `/scrape` |
| `pipeline_api.py` | Pipeline run management: list, retry, delete; router mounted at `/pipeline/api` |
| `pipeline_state.py` | Stage enum, PipelineRun, PipelineStateStore |
| `job_queue.py` | Single-worker FIFO queue (shared by both APIs) |
| `tts.py` | VibeVoice TTS; each synthesis runs in a fresh subprocess |
| `scraper.py` | Web scraping (httpx + trafilatura) |
| `summarizer.py` | Ollama LLM integration |
| `metadata.py` | Thread-safe atomic JSON episode store (`output/metadata.json`) |
| `config.py` | Settings loader; env-var overrides via `PODCAST_<KEY>` |
| `models.py` | Episode dataclass |
| `podbean.py` | Podbean API client (OAuth + upload + publish) |
| `telegram_bot.py` | Telegram bot; receives URLs via chat, queues them |
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

Copy `config.yaml.sample` to `config.yaml` and edit. All settings overridable at runtime with `PODCAST_<KEY>` env vars. `config.yaml` is in `.gitignore` (secrets stay local); `config.yaml.sample` is the committed template.

Key settings: `ollama_model`, `ollama_url`, `ollama_prompt`, `ollama_metadata_prompt`, `tts_ddpm_steps`, `tts_cfg_scale`, `tts_mp3_bitrate`, `tts_voice_sample`, `tts_timeout_sec`, `output_dir`, `web_port`, `intermediate_retention_days`, `podbean_client_id`, `podbean_client_secret`, `telegram_bot_token`, `telegram_allowed_user_ids`, `telegram_poll_interval_sec`.

## Important Behaviours

- **Pipeline lock:** `.pipeline.lock` serialises concurrent pipeline runs across watcher and web app to prevent OOM from dual TTS loads.
- **Admin regenerate guard:** Old episode stays in `output/metadata.json` until new one succeeds, preventing watcher from double-processing.
- **TTS subprocess isolation:** Each `synthesize()` call spawns a fresh `spawn`-method subprocess; exits to reclaim GPU/MPS memory.
- **Intermediate files:** `output/pipeline/{run-id}/` — `state.json` kept forever; `input_text.txt` (scraped article), `prompt.txt` (full Ollama prompt), `script.txt`, `tts_input.txt` pruned after `intermediate_retention_days` days.
- **Restartable pipeline:** Pipeline UI at `/pipeline` shows all runs with sortable columns, stage badges, and a detail link per run. Detail page at `/pipeline/{run_id}` shows all work items (scraped text, metadata, prompt, script, TTS input, audio) with editable fields. Any run (including completed) can be restarted from any stage with custom inputs via `restart_pipeline()`. Failed runs can be retried from the failed stage via `resume_pipeline()`. Admin regen offers "Full Regen" or "From TTS only". Article metadata saved in `state.json` for TTS-only retries.
- **Browser-like scraping:** `scraper.py` sends realistic Chrome browser headers to avoid 403 rejections. On 403, retries with Playwright headless Chromium if installed (optional dependency). Sites with CAPTCHA (e.g. Forbes/DataDome) still fail.
- **Comments in urls.txt:** Lines starting with `#` are ignored.
- **Telegram bot:** Optional bot that accepts URLs via Telegram chat and queues them for processing. Extracts URLs from messages with surrounding text (entity detection + regex fallback). Runs as a separate process. Requires `telegram_bot_token` in `config.yaml`. Optional access control via `telegram_allowed_user_ids`. Configurable poll interval via `telegram_poll_interval_sec` (default 30s).
- **Podbean publishing:** Admin UI shows an editable publish form (title, description, optional thumbnail upload) before uploading to Podbean. Edited values go to Podbean only; local episode data unchanged. Requires `podbean_client_id` and `podbean_client_secret` in `config.yaml`. Runs in a background thread (same pattern as regenerate).

## Documentation Workflow

When behaviour changes in code, update `README.md`, `docs/PRD.md`, `docs/plan.md`, and `docs/TODO.md` in the same change.
