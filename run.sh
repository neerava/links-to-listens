#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$SCRIPT_DIR/.venv"
PYTHON="$VENV/bin/python"
UVICORN="$VENV/bin/uvicorn"

# ── Colours ────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[run.sh]${NC} $*"; }
warn()  { echo -e "${YELLOW}[run.sh]${NC} $*"; }
error() { echo -e "${RED}[run.sh]${NC} $*" >&2; }

# ── Cleanup on exit ────────────────────────────────────────────────────────
PIDS=()
cleanup() {
  if [ ${#PIDS[@]} -gt 0 ]; then
    info "Shutting down..."
    for pid in "${PIDS[@]}"; do
      kill "$pid" 2>/dev/null || true
    done
    wait "${PIDS[@]}" 2>/dev/null || true
  fi
  info "Done."
}
trap cleanup EXIT INT TERM

# ── Preflight checks ───────────────────────────────────────────────────────
check_python() {
  if [ ! -x "$PYTHON" ]; then
    error "Virtual environment not found at $VENV"
    error "Run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    exit 1
  fi
}

check_ollama() {
  if ! command -v ollama &>/dev/null; then
    warn "ollama not found on PATH — summarization will fail until it is installed."
    return
  fi
  if ! curl -sf "http://localhost:11434" &>/dev/null; then
    warn "Ollama does not appear to be running. Start it with: ollama serve"
  else
    info "Ollama reachable at http://localhost:11434"
  fi
}

check_vibevoice() {
  if ! "$PYTHON" -c "import vibevoice" &>/dev/null; then
    warn "VibeVoice not found — TTS will fail until it is installed (pip install vibevoice)."
    return
  fi
  info "VibeVoice package installed"
}

check_ffmpeg() {
  if ! command -v ffmpeg &>/dev/null; then
    warn "ffmpeg not found — needed for WAV→MP3 conversion. Install with: brew install ffmpeg"
  else
    info "ffmpeg available"
  fi
}

# ── Read ports from config.yaml ────────────────────────────────────────────
get_ports() {
  "$PYTHON" - <<'EOF'
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent))
try:
    import yaml
    cfg = yaml.safe_load(open("config.yaml")) or {}
    print(cfg.get("web_port", 8080), cfg.get("script_api_port", 8081), cfg.get("audio_api_port", 8082))
except Exception:
    print(8080, 8081, 8082)
EOF
}

# ── Main ───────────────────────────────────────────────────────────────────
cd "$SCRIPT_DIR"

check_python
check_ollama
check_vibevoice
check_ffmpeg

read -r PORT _ _ < <(get_ports)

info "Starting web server on http://localhost:$PORT"
"$UVICORN" app:app --host 0.0.0.0 --port "$PORT" --log-level warning &
PIDS+=($!)

# Give uvicorn a moment to bind the port
sleep 1

info "Starting watcher (polling urls.txt)..."
"$PYTHON" watcher.py &
PIDS+=($!)

info "All services running. Press Ctrl+C to stop."
info "  Episodes      → http://localhost:$PORT/"
info "  Admin         → http://localhost:$PORT/admin"
info "  Generate Script → http://localhost:$PORT/generate-script"
info "  Generate Audio  → http://localhost:$PORT/generate-audio"
info "Add URLs to urls.txt to queue them for processing."

# Poll until one of the services dies.
# (macOS ships Bash 3.2 which does not support `wait -n`.)
while true; do
  for pid in "${PIDS[@]}"; do
    if ! kill -0 "$pid" 2>/dev/null; then
      error "A service (PID $pid) exited unexpectedly — shutting down."
      exit 1
    fi
  done
  sleep 2
done
