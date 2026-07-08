#!/usr/bin/env sh
# Run the FastAPI server in the background, then the Streamlit client in the
# foreground. Killing the container stops both.
set -e

: "${PORT:=8000}"
export API_BASE_URL="http://localhost:${PORT}"

uvicorn backend.server:app --host 127.0.0.1 --port "${PORT}" &
SERVER_PID=$!
trap 'kill $SERVER_PID 2>/dev/null' EXIT INT TERM

# Wait for the server's /health before starting the UI (uses the bundled python,
# since slim has no curl/wget).
i=0
while [ "$i" -lt 30 ]; do
  if python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:${PORT}/health')" 2>/dev/null; then
    break
  fi
  i=$((i + 1))
  sleep 1
done

exec streamlit run apps/pages/streamlit_app.py \
  --server.address 0.0.0.0 \
  --server.port 8501 \
  --server.headless true \
  --browser.gatherUsageStats false
