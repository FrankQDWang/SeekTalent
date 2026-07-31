#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEB_DIR="$ROOT/apps/web-react"
VITE_BIN="$WEB_DIR/node_modules/.bin/vite"
BACKEND_HOST="${SEEKTALENT_DEV_BACKEND_HOST:-127.0.0.1}"
BACKEND_PORT="${SEEKTALENT_DEV_BACKEND_PORT:-8012}"
FRONTEND_HOST="${SEEKTALENT_DEV_FRONTEND_HOST:-127.0.0.1}"
FRONTEND_PORT="${SEEKTALENT_DEV_FRONTEND_PORT:-5178}"

if [[ -z "${SEEKTALENT_WTSCLI_NODE:-}" && -z "${SEEKTALENT_DOMI_NODE:-}" && -z "${DOMI_NODE:-}" ]]; then
  SEEKTALENT_WTSCLI_NODE="$(command -v node || true)"
  if [[ -n "$SEEKTALENT_WTSCLI_NODE" ]]; then
    export SEEKTALENT_WTSCLI_NODE
  fi
fi

cd "$ROOT"

PNPM_CMD=()
if command -v corepack >/dev/null 2>&1; then
  PNPM_CMD=(corepack pnpm)
elif command -v pnpm >/dev/null 2>&1; then
  PNPM_CMD=(pnpm)
else
  echo "pnpm is required for the React workbench dev server." >&2
  exit 1
fi

if [[ ! -x "$VITE_BIN" ]]; then
  echo "Installing React workspace dependencies for the workbench dev server..." >&2
  if [[ -f "$WEB_DIR/pnpm-lock.yaml" ]]; then
    (cd "$WEB_DIR" && "${PNPM_CMD[@]}" install --frozen-lockfile)
  else
    (cd "$WEB_DIR" && "${PNPM_CMD[@]}" install)
  fi
fi

if [[ ! -x "$VITE_BIN" ]]; then
  echo "Vite dev server is missing after dependency install: apps/web-react/node_modules/.bin/vite" >&2
  exit 1
fi

read_env_value() {
  uv run python - "$ROOT/.env" "$1" <<'PY'
from pathlib import Path
import sys

env_path = Path(sys.argv[1])
target = sys.argv[2]
if not env_path.exists():
    raise SystemExit(0)
for raw_line in env_path.read_text(encoding="utf-8").splitlines():
    line = raw_line.strip()
    if not line or line.startswith("#"):
        continue
    if line.startswith("export "):
        line = line[7:].strip()
    if "=" not in line:
        continue
    key, value = line.split("=", 1)
    if key.strip() != target:
        continue
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    print(value)
    break
PY
}

env_or_file() {
  local key="$1"
  local value="${!key:-}"
  if [[ -n "$value" ]]; then
    printf '%s\n' "$value"
    return
  fi
  read_env_value "$key"
}

WORKSPACE_ROOT="$(env_or_file SEEKTALENT_WORKSPACE_ROOT)"
WORKSPACE_ROOT="${WORKSPACE_ROOT:-$ROOT}"
CODE_ROOT="$(env_or_file SEEKTALENT_CODE_ROOT)"
CODE_ROOT="${CODE_ROOT:-$ROOT}"

wait_for_backend_ready() {
  local timeout="${SEEKTALENT_DEV_BACKEND_READY_TIMEOUT_SECONDS:-60}"
  uv run python - "$BACKEND_HOST" "$BACKEND_PORT" "$timeout" "$backend_pid" <<'PY'
import os
import socket
import sys
import time

host = sys.argv[1]
port = int(sys.argv[2])
timeout = float(sys.argv[3])
backend_pid = int(sys.argv[4])
deadline = time.monotonic() + timeout

while True:
    try:
        with socket.create_connection((host, port), timeout=0.2):
            raise SystemExit(0)
    except OSError:
        pass

    try:
        os.kill(backend_pid, 0)
    except OSError:
        print("SeekTalent backend exited before it was ready.", file=sys.stderr)
        raise SystemExit(1)

    if time.monotonic() >= deadline:
        print(f"Timed out waiting for SeekTalent backend at {host}:{port}.", file=sys.stderr)
        raise SystemExit(1)

    time.sleep(0.2)
PY
}

backend_pid=""
cleanup() {
  if [[ -n "$backend_pid" ]]; then
    kill "$backend_pid" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

env \
  NODE_PATH="$WEB_DIR/node_modules" \
  SEEKTALENT_WORKSPACE_ROOT="$WORKSPACE_ROOT" \
  SEEKTALENT_CODE_ROOT="$CODE_ROOT" \
  SEEKTALENT_LIEPIN_WORKER_MODE="opencli" \
  SEEKTALENT_LIEPIN_BROWSER_ACTION_BACKEND="opencli" \
  SEEKTALENT_LIEPIN_OPENCLI_WINDOW_MODE="${SEEKTALENT_LIEPIN_OPENCLI_WINDOW_MODE:-background}" \
  SEEKTALENT_LIEPIN_OPENCLI_TIMEOUT_SECONDS="${SEEKTALENT_LIEPIN_OPENCLI_TIMEOUT_SECONDS:-900}" \
  SEEKTALENT_LIEPIN_OPENCLI_DETAIL_OPEN_TIMEOUT_SECONDS="${SEEKTALENT_LIEPIN_OPENCLI_DETAIL_OPEN_TIMEOUT_SECONDS:-90}" \
  SEEKTALENT_LIEPIN_OPENCLI_SEARCH_NAVIGATION_TIMEOUT_SECONDS="${SEEKTALENT_LIEPIN_OPENCLI_SEARCH_NAVIGATION_TIMEOUT_SECONDS:-10}" \
  uv run seektalent-ui-api \
    --host "$BACKEND_HOST" \
    --port "$BACKEND_PORT" \
    --allowed-origin "http://$FRONTEND_HOST:$FRONTEND_PORT" \
    --allowed-origin "http://localhost:$FRONTEND_PORT" &
backend_pid=$!

echo "SeekTalent backend: http://$BACKEND_HOST:$BACKEND_PORT" >&2
echo "Waiting for SeekTalent backend to accept connections..." >&2
wait_for_backend_ready
echo "SeekTalent backend is ready." >&2
echo "SeekTalent React workbench: http://$FRONTEND_HOST:$FRONTEND_PORT" >&2
echo "Liepin worker mode: opencli via deterministic local browser retrieval" >&2

(
  cd "$WEB_DIR"
  "${PNPM_CMD[@]}" exec vite --host "$FRONTEND_HOST" --port "$FRONTEND_PORT" --strictPort
)
