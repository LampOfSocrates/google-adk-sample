#!/usr/bin/env bash
# Launch a local pdf_insight surface (git bash on Windows).
#
#   ./local_run.sh                 # adk web (dev UI / debugger) over backend/
#   ./local_run.sh server          # the FastAPI agent server (uvicorn :8000) — start this first
#   ./local_run.sh ui              # the Streamlit client (needs `server` running)
#   LLM_BACKEND=mock ./local_run.sh ui   # offline, no API key
#   ./local_run.sh api_server      # any other adk subcommand + args are passed through
#
# Why the two env tweaks:
#   * point adk at backend/ -> the dropdown lists real agents, not repo folders
#   * PYTHONPATH=repo root  -> the agents' `from backend.shared...` imports resolve
set -euo pipefail

# Repo root = this script's dir, so it works no matter where you call it from.
cd "$(dirname "$0")"

VENV=".venv/Scripts/activate"
if [[ ! -f "$VENV" ]]; then
  echo "error: $VENV not found. Create the venv first (python -m venv .venv && pip install -r requirements.txt)." >&2
  exit 1
fi

# Switch into the project venv (replaces conda 'base' on PATH so `adk` resolves).
# shellcheck disable=SC1090
source "$VENV"

export PYTHONPATH="$PWD"
export LLM_BACKEND="${LLM_BACKEND:-gemini}"   # override inline: LLM_BACKEND=mock ./local_run.sh

echo "venv : $VIRTUAL_ENV"
echo "adk  : $(command -v adk)"
echo "backend: $LLM_BACKEND   (gemini needs GOOGLE_API_KEY in .env)"
echo

# Dispatch:
#   (no args) -> adk web backend       (dev UI / debugger)
#   server    -> FastAPI agent server  (uvicorn; the Streamlit client calls this)
#   ui        -> streamlit client       (needs `server` running; API_BASE_URL env)
#   <other>   -> passed straight to adk
if [[ $# -eq 0 ]]; then
  exec adk web backend
elif [[ "$1" == "server" ]]; then
  shift
  exec uvicorn backend.server:app --reload --port "${PORT:-8000}" "$@"
elif [[ "$1" == "ui" ]]; then
  shift
  exec streamlit run apps/pages/streamlit_app.py "$@"
elif [[ "$1" == "all" ]]; then
  # Server in the background, Streamlit client in the foreground. Ctrl-C the
  # client; the trap stops the server too.
  uvicorn backend.server:app --port "${PORT:-8000}" &
  SERVER_PID=$!
  trap 'kill $SERVER_PID 2>/dev/null' EXIT
  sleep 2
  exec streamlit run apps/pages/streamlit_app.py
else
  exec adk "$@"
fi
