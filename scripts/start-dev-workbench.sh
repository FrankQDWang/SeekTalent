#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEB_DIR="$ROOT/apps/web-svelte"
PI_BIN="$WEB_DIR/node_modules/.bin/pi"
PI_EXTENSION="$ROOT/src/seektalent/providers/pi_agent/pi_extensions/bailian_deepseek.ts"
SECRET_PATH="$ROOT/.seektalent/liepin_account_binding_secret"
BACKEND_HOST="${SEEKTALENT_DEV_BACKEND_HOST:-127.0.0.1}"
BACKEND_PORT="${SEEKTALENT_DEV_BACKEND_PORT:-8012}"
FRONTEND_HOST="${SEEKTALENT_DEV_FRONTEND_HOST:-127.0.0.1}"
FRONTEND_PORT="${SEEKTALENT_DEV_FRONTEND_PORT:-5178}"

cd "$ROOT"

command -v bun >/dev/null 2>&1 || {
  echo "bun is required for the Svelte workbench dev server." >&2
  exit 1
}

if [[ ! -x "$PI_BIN" ]]; then
  echo "Installing Svelte workspace dependencies, including the repo-local Pi agent..." >&2
  (cd "$WEB_DIR" && bun install)
fi

if [[ ! -x "$PI_BIN" ]]; then
  echo "Repo-local Pi agent is missing after dependency install: apps/web-svelte/node_modules/.bin/pi" >&2
  exit 1
fi

if [[ ! -f "$PI_EXTENSION" ]]; then
  echo "SeekTalent Pi provider extension is missing: src/seektalent/providers/pi_agent/pi_extensions/bailian_deepseek.ts" >&2
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
PI_MODEL="$(env_or_file SEEKTALENT_LIEPIN_PI_MODEL_ID)"
if [[ -z "$PI_MODEL" ]]; then
  PI_MODEL="$(env_or_file SEEKTALENT_WORKBENCH_NOTE_WRITER_MODEL_ID)"
fi
if [[ -z "$PI_MODEL" ]]; then
  PI_MODEL="$(env_or_file SEEKTALENT_SCORING_MODEL_ID)"
fi
PI_MODEL="${PI_MODEL:-deepseek-v4-flash}"
CONFIGURED_PI_COMMAND="$(env_or_file SEEKTALENT_LIEPIN_PI_COMMAND)"
if [[ -n "$CONFIGURED_PI_COMMAND" && "$CONFIGURED_PI_COMMAND" != "pi --mode rpc --no-session" ]]; then
  PI_COMMAND="$CONFIGURED_PI_COMMAND"
else
  PI_COMMAND="$PI_BIN --mode rpc --no-session --extension $PI_EXTENSION --provider bailian --model $PI_MODEL"
fi
MCP_CONFIG_PATH="$(env_or_file SEEKTALENT_LIEPIN_PI_MCP_CONFIG_PATH)"
MCP_CONFIG_PATH="${MCP_CONFIG_PATH:-.pi/mcp.json}"
ACCOUNT_BINDING_SECRET="$(env_or_file SEEKTALENT_LIEPIN_ACCOUNT_BINDING_SECRET)"
if [[ -z "$ACCOUNT_BINDING_SECRET" || "$ACCOUNT_BINDING_SECRET" == "local-development" ]]; then
  mkdir -p "$(dirname "$SECRET_PATH")"
  if [[ ! -s "$SECRET_PATH" ]] || grep -qx 'local-development' "$SECRET_PATH"; then
    uv run python - <<'PY'
from pathlib import Path
import secrets

path = Path(".seektalent/liepin_account_binding_secret")
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(f"dev-{secrets.token_hex(32)}\n", encoding="utf-8")
PY
  fi
  ACCOUNT_BINDING_SECRET="$(
    uv run python - <<'PY'
from pathlib import Path

print(Path(".seektalent/liepin_account_binding_secret").read_text(encoding="utf-8").strip())
PY
  )"
fi

uv run seektalent pi-agent init \
  --project \
  --workspace-root "$ROOT" \
  --mcp-config-path "$MCP_CONFIG_PATH" \
  --write >/dev/null

backend_pid=""
cleanup() {
  if [[ -n "$backend_pid" ]]; then
    kill "$backend_pid" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

env \
  SEEKTALENT_WORKSPACE_ROOT="$WORKSPACE_ROOT" \
  SEEKTALENT_LIEPIN_WORKER_MODE="pi_agent" \
  SEEKTALENT_LIEPIN_PI_COMMAND="$PI_COMMAND" \
  SEEKTALENT_LIEPIN_PI_MCP_CONFIG_PATH="$MCP_CONFIG_PATH" \
  SEEKTALENT_LIEPIN_ACCOUNT_BINDING_SECRET="$ACCOUNT_BINDING_SECRET" \
  uv run seektalent-ui-api \
    --host "$BACKEND_HOST" \
    --port "$BACKEND_PORT" \
    --allowed-origin "http://$FRONTEND_HOST:$FRONTEND_PORT" \
    --allowed-origin "http://localhost:$FRONTEND_PORT" &
backend_pid=$!

echo "SeekTalent backend: http://$BACKEND_HOST:$BACKEND_PORT" >&2
echo "SeekTalent Svelte workbench: http://$FRONTEND_HOST:$FRONTEND_PORT" >&2
echo "Liepin worker mode: pi_agent via repo-local Pi dependency, Runtime text LLM provider, model $PI_MODEL" >&2

(
  cd "$WEB_DIR"
  ./node_modules/.bin/vite --host "$FRONTEND_HOST" --port "$FRONTEND_PORT" --strictPort
)
