#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEB_DIR="$ROOT/apps/web-svelte"
OPENCLI_BIN="$WEB_DIR/node_modules/.bin/opencli"
BACKEND_HOST="${SEEKTALENT_DEV_BACKEND_HOST:-127.0.0.1}"
BACKEND_PORT="${SEEKTALENT_DEV_BACKEND_PORT:-8012}"
FRONTEND_HOST="${SEEKTALENT_DEV_FRONTEND_HOST:-127.0.0.1}"
FRONTEND_PORT="${SEEKTALENT_DEV_FRONTEND_PORT:-5178}"

cd "$ROOT"

command -v bun >/dev/null 2>&1 || {
  echo "bun is required for the Svelte workbench dev server." >&2
  exit 1
}

if [[ ! -x "$OPENCLI_BIN" ]]; then
  echo "Installing Svelte workspace dependencies, including the repo-local OpenCLI browser helper..." >&2
  (cd "$WEB_DIR" && bun install)
fi

if [[ ! -x "$OPENCLI_BIN" ]]; then
  echo "Repo-local OpenCLI browser helper is missing after dependency install: apps/web-svelte/node_modules/.bin/opencli" >&2
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

opencli_extension_connected() {
  "$OPENCLI_BIN" daemon status 2>/dev/null | grep -q "Extension: connected"
}

opencli_daemon_stale() {
  "$OPENCLI_BIN" daemon status 2>/dev/null | grep -q "Daemon: stale"
}

wait_for_opencli_extension() {
  local attempt
  for attempt in {1..15}; do
    if opencli_extension_connected; then
      return 0
    fi
    sleep 1
  done
  return 1
}

OPENCLI_START_DAEMON="$(env_or_file SEEKTALENT_LIEPIN_OPENCLI_START_DAEMON)"
if [[ "$OPENCLI_START_DAEMON" == "1" || "$OPENCLI_START_DAEMON" == "true" ]]; then
  echo "Starting OpenCLI browser bridge daemon for Liepin local browser actions..." >&2
  if ! "$OPENCLI_BIN" daemon restart >&2; then
    echo "OpenCLI browser bridge daemon did not start; Liepin OpenCLI source will fail closed." >&2
  elif ! wait_for_opencli_extension; then
    echo "OpenCLI browser bridge extension is not connected; Liepin OpenCLI source will fail closed." >&2
  fi
elif opencli_daemon_stale; then
  echo "OpenCLI browser bridge daemon is stale; restarting daemon and waiting..." >&2
  if ! "$OPENCLI_BIN" daemon restart >&2 || ! wait_for_opencli_extension; then
    echo "OpenCLI browser bridge extension is not connected; Liepin OpenCLI source will fail closed." >&2
  fi
elif ! "$OPENCLI_BIN" daemon status >/dev/null 2>&1; then
  echo "OpenCLI browser bridge daemon is not running; Liepin OpenCLI source will fail closed." >&2
elif ! opencli_extension_connected; then
  echo "OpenCLI browser bridge daemon is running but the extension is not connected; restarting daemon and waiting..." >&2
  if ! "$OPENCLI_BIN" daemon restart >&2 || ! wait_for_opencli_extension; then
    echo "OpenCLI browser bridge extension is not connected; Liepin OpenCLI source will fail closed." >&2
  fi
fi

backend_pid=""
cleanup() {
  if [[ -x "$OPENCLI_BIN" ]]; then
    env \
      NODE_PATH="$WEB_DIR/node_modules" \
      PYTHONPATH="$ROOT/src" \
      SEEKTALENT_LIEPIN_OPENCLI_COMMAND="$OPENCLI_BIN" \
      SEEKTALENT_LIEPIN_OPENCLI_LEASE_DIR="$WORKSPACE_ROOT/.seektalent/opencli_leases" \
      uv run python -m seektalent.providers.liepin.opencli_browser_cli cleanup_orphaned_tabs \
        <<< '{"force":true}' >/dev/null 2>&1 || true
  fi
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
  SEEKTALENT_LIEPIN_OPENCLI_COMMAND="$OPENCLI_BIN" \
  SEEKTALENT_LIEPIN_OPENCLI_TIMEOUT_SECONDS="${SEEKTALENT_LIEPIN_OPENCLI_TIMEOUT_SECONDS:-900}" \
  SEEKTALENT_LIEPIN_OPENCLI_DETAIL_OPEN_TIMEOUT_SECONDS="${SEEKTALENT_LIEPIN_OPENCLI_DETAIL_OPEN_TIMEOUT_SECONDS:-90}" \
  uv run seektalent-ui-api \
    --host "$BACKEND_HOST" \
    --port "$BACKEND_PORT" \
    --allowed-origin "http://$FRONTEND_HOST:$FRONTEND_PORT" \
    --allowed-origin "http://localhost:$FRONTEND_PORT" &
backend_pid=$!

echo "SeekTalent backend: http://$BACKEND_HOST:$BACKEND_PORT" >&2
echo "SeekTalent Svelte workbench: http://$FRONTEND_HOST:$FRONTEND_PORT" >&2
echo "Liepin worker mode: opencli via deterministic local browser retrieval" >&2

(
  cd "$WEB_DIR"
  ./node_modules/.bin/vite --host "$FRONTEND_HOST" --port "$FRONTEND_PORT" --strictPort
)
