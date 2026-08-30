#!/usr/bin/env bash
# Start BOTH services for local development.
#
# The dashboard is a separate process from the API: uvicorn serves JSON on
# :8000, Streamlit serves the UI on :8501. Running only uvicorn gives you an
# API with no front end.
#
#   ./scripts/run_local.sh
#
# Ctrl-C stops both.

set -euo pipefail
cd "$(dirname "$0")/.."

API_PORT="${API_PORT:-8000}"
UI_PORT="${UI_PORT:-8501}"

# Prefer the project venv if there is one, otherwise whatever is on PATH.
if [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
else
  PY="$(command -v python3 || command -v python)"
fi

if ! "$PY" -c "import streamlit" >/dev/null 2>&1; then
  echo "streamlit is not installed for $PY" >&2
  echo "run:  $PY -m pip install -r requirements.txt" >&2
  exit 1
fi

cleanup() {
  echo ""
  echo "stopping..."
  kill "${API_PID:-}" "${UI_PID:-}" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "starting API on http://localhost:${API_PORT}"
"$PY" -m uvicorn app.main:app --host 0.0.0.0 --port "$API_PORT" &
API_PID=$!

# Wait for the API to answer before starting the UI, so the dashboard does not
# render its "backend unreachable" state on first paint.
for _ in $(seq 1 30); do
  if "$PY" - "$API_PORT" <<'PROBE' >/dev/null 2>&1
import sys, urllib.request
urllib.request.urlopen(f"http://127.0.0.1:{sys.argv[1]}/health", timeout=1)
PROBE
  then break; fi
  sleep 1
done

echo "starting dashboard on http://localhost:${UI_PORT}"
API_BASE_URL="http://localhost:${API_PORT}" \
  "$PY" -m streamlit run frontend/streamlit_app.py \
  --server.port "$UI_PORT" \
  --server.address 0.0.0.0 \
  --server.headless true \
  --browser.gatherUsageStats false &
UI_PID=$!

echo ""
echo "  dashboard : http://localhost:${UI_PORT}    <-- open this one"
echo "  API docs  : http://localhost:${API_PORT}/docs"
echo ""
wait
