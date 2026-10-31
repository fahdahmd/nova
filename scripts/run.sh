#!/usr/bin/env bash
# One-command start for Nova on macOS and Linux.
#
#   ./scripts/run.sh                 start normally
#   PORT=9000 ./scripts/run.sh       start on another port
#   SKIP_CHECKS=1 ./scripts/run.sh   skip the Ollama and voice checks
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"

venv_python=".venv/bin/python"
model="${NOVA_OLLAMA_MODEL:-qwen3.5:4b}"

step() { printf '\033[36m==> %s\033[0m\n' "$1"; }
warn() { printf '\033[33m!!  %s\033[0m\n' "$1"; }

# ---------------------------------------------------------------- environment
if [[ ! -x "$venv_python" ]]; then
  step "Creating the virtual environment"
  if command -v uv > /dev/null 2>&1; then
    uv venv --python 3.12
    uv pip install -e ".[dev]"
  else
    python3 -m venv .venv
    "$venv_python" -m pip install --upgrade pip
    "$venv_python" -m pip install -e ".[dev]"
  fi
else
  step "Virtual environment found"
fi

# -------------------------------------------------------------------- checks
if [[ -z "${SKIP_CHECKS:-}" ]]; then
  if ! command -v ollama > /dev/null 2>&1; then
    warn "Ollama was not found on PATH. Install it from https://ollama.com"
  elif ! curl -fsS --max-time 3 http://127.0.0.1:11434/api/tags > /dev/null 2>&1; then
    warn "Ollama is not responding. Start it in another terminal with: ollama serve"
  elif ! ollama list 2> /dev/null | awk 'NR>1 {print $1}' | grep -qx "$model"; then
    warn "The model '$model' is not installed. Pulling it now (this is a one-off)."
    ollama pull "$model"
  else
    step "Model '$model' is ready"
  fi

  if ! ls models/piper/*.onnx > /dev/null 2>&1; then
    step "Downloading a Piper voice (about 60 MB, one-off)"
    "$venv_python" scripts/download_voice.py
  fi
fi

# --------------------------------------------------------------------- start
[[ -n "${PORT:-}" ]] && export NOVA_PORT="$PORT"

step "Starting Nova"
exec "$venv_python" -m nova "$@"
