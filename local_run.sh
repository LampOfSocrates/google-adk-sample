#!/usr/bin/env bash
# Launch a local pdf_insight surface (git bash on Windows).
#
#   ./local_run.sh                 # adk web (dev UI / debugger) over apps/
#   ./local_run.sh ui              # the Claude-style Streamlit product UI (PDF upload + chat)
#   LLM_BACKEND=mock ./local_run.sh ui   # offline, no API key
#   ./local_run.sh api_server      # any other adk subcommand + args are passed through
#
# Why the two env tweaks:
#   * point adk at apps/  -> the dropdown lists real agents, not repo folders
#   * PYTHONPATH=repo root -> the agents' `from shared...` imports resolve
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
#   (no args) -> adk web apps          (dev UI / debugger)
#   ui        -> streamlit product UI  (PDF upload + Claude-style chat)
#   <other>   -> passed straight to adk
if [[ $# -eq 0 ]]; then
  exec adk web apps
elif [[ "$1" == "ui" ]]; then
  shift
  exec streamlit run streamlit_app.py "$@"
else
  exec adk "$@"
fi
