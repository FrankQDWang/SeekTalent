#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEB_DIR="$ROOT/apps/web-svelte"
PI_BIN="$WEB_DIR/node_modules/.bin/pi"
OPENCLI_BIN="$WEB_DIR/node_modules/.bin/opencli"
PI_EXTENSION="$ROOT/src/seektalent/providers/pi_agent/pi_extensions/bailian_deepseek.ts"
PI_OPENCLI_EXTENSION="$ROOT/src/seektalent/providers/pi_agent/pi_extensions/seektalent_opencli_browser.ts"
PI_MCP_ADAPTER_EXTENSION="$WEB_DIR/node_modules/pi-mcp-adapter/index.ts"
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

if [[ ! -f "$PI_MCP_ADAPTER_EXTENSION" ]]; then
  echo "Installing Svelte workspace dependencies, including the repo-local Pi MCP adapter..." >&2
  (cd "$WEB_DIR" && bun install)
fi

if [[ ! -x "$OPENCLI_BIN" ]]; then
  echo "Installing Svelte workspace dependencies, including the repo-local OpenCLI browser helper..." >&2
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

if [[ ! -f "$PI_OPENCLI_EXTENSION" ]]; then
  echo "SeekTalent Pi OpenCLI extension is missing: src/seektalent/providers/pi_agent/pi_extensions/seektalent_opencli_browser.ts" >&2
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
SECRET_PATH="$WORKSPACE_ROOT/.seektalent/liepin_account_binding_secret"
PI_MODEL="$(env_or_file SEEKTALENT_LIEPIN_PI_MODEL_ID)"
if [[ -z "$PI_MODEL" ]]; then
  PI_MODEL="$(env_or_file SEEKTALENT_WORKBENCH_NOTE_WRITER_MODEL_ID)"
fi
if [[ -z "$PI_MODEL" ]]; then
  PI_MODEL="$(env_or_file SEEKTALENT_SCORING_MODEL_ID)"
fi
PI_MODEL="${PI_MODEL:-deepseek-v4-flash}"
DOKOBOT_MCP_SERVER_NAME="$(env_or_file SEEKTALENT_LIEPIN_DOKOBOT_MCP_SERVER_NAME)"
DOKOBOT_MCP_COMMAND="$(env_or_file SEEKTALENT_LIEPIN_DOKOBOT_MCP_COMMAND)"
DOKOBOT_MCP_ARGS_JSON="$(env_or_file SEEKTALENT_LIEPIN_DOKOBOT_MCP_ARGS_JSON)"
DOKOBOT_DIRECT_TOOLS_JSON="$(env_or_file SEEKTALENT_LIEPIN_DOKOBOT_DIRECT_TOOLS_JSON)"
DOKOBOT_OBSERVED_TOOLS_JSON="$(env_or_file SEEKTALENT_LIEPIN_DOKOBOT_OBSERVED_TOOLS_JSON)"
DOKOBOT_MCP_SERVER_NAME="${DOKOBOT_MCP_SERVER_NAME:-dokobot}"
DOKOBOT_MCP_ARGS_JSON="${DOKOBOT_MCP_ARGS_JSON:-[]}"
DOKOBOT_DIRECT_TOOLS_JSON="${DOKOBOT_DIRECT_TOOLS_JSON:-[]}"
DOKOBOT_OBSERVED_TOOLS_JSON="${DOKOBOT_OBSERVED_TOOLS_JSON:-[]}"
BROWSER_ACTION_BACKEND="$(env_or_file SEEKTALENT_LIEPIN_BROWSER_ACTION_BACKEND)"
BROWSER_ACTION_BACKEND="${BROWSER_ACTION_BACKEND:-opencli}"
PI_MCP_ADAPTER_EXTENSION_ARG=""
if [[ "$BROWSER_ACTION_BACKEND" != "opencli" ]]; then
  if [[ ! -f "$PI_MCP_ADAPTER_EXTENSION" ]]; then
    echo "Pi MCP adapter is missing; starting Workbench with Liepin browser channel blocked." >&2
  else
    PI_MCP_ADAPTER_EXTENSION_ARG="--extension $PI_MCP_ADAPTER_EXTENSION"
  fi
fi
CONFIGURED_PI_COMMAND="$(env_or_file SEEKTALENT_LIEPIN_PI_COMMAND)"
if [[ -n "$CONFIGURED_PI_COMMAND" && "$CONFIGURED_PI_COMMAND" != "pi --mode rpc --no-session" ]]; then
  PI_COMMAND="$CONFIGURED_PI_COMMAND"
elif [[ "$BROWSER_ACTION_BACKEND" == "opencli" ]]; then
  PI_COMMAND="$PI_BIN --mode rpc --no-session --extension $PI_EXTENSION --extension $PI_OPENCLI_EXTENSION --provider bailian --model $PI_MODEL"
else
  PI_COMMAND="$PI_BIN --mode rpc --no-session --extension $PI_EXTENSION $PI_MCP_ADAPTER_EXTENSION_ARG --provider bailian --model $PI_MODEL"
fi
MCP_CONFIG_PATH="$(env_or_file SEEKTALENT_LIEPIN_PI_MCP_CONFIG_PATH)"
MCP_CONFIG_PATH="${MCP_CONFIG_PATH:-.pi/mcp.json}"
ACCOUNT_BINDING_SECRET="$(env_or_file SEEKTALENT_LIEPIN_ACCOUNT_BINDING_SECRET)"
if [[ -z "$ACCOUNT_BINDING_SECRET" || "$ACCOUNT_BINDING_SECRET" == "local-development" ]]; then
  mkdir -p "$(dirname "$SECRET_PATH")"
  if [[ ! -s "$SECRET_PATH" ]] || grep -qx 'local-development' "$SECRET_PATH"; then
    SECRET_PATH="$SECRET_PATH" uv run python - <<'PY'
import os
from pathlib import Path
import secrets

path = Path(os.environ["SECRET_PATH"])
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(f"dev-{secrets.token_hex(32)}\n", encoding="utf-8")
PY
  fi
  ACCOUNT_BINDING_SECRET="$(
    SECRET_PATH="$SECRET_PATH" uv run python - <<'PY'
import os
from pathlib import Path

print(Path(os.environ["SECRET_PATH"]).read_text(encoding="utf-8").strip())
PY
  )"
fi

if [[ "$BROWSER_ACTION_BACKEND" != "opencli" && -z "$DOKOBOT_MCP_COMMAND" ]]; then
  echo "DokoBot MCP command is not configured; starting Workbench with Liepin browser channel blocked." >&2
fi

if [[ "$BROWSER_ACTION_BACKEND" == "opencli" && ! -x "$OPENCLI_BIN" ]]; then
  echo "OpenCLI browser helper is not installed; Liepin OpenCLI source will fail closed." >&2
fi

opencli_extension_connected() {
  "$OPENCLI_BIN" daemon status 2>/dev/null | grep -q "Extension: connected"
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

if [[ "$BROWSER_ACTION_BACKEND" == "opencli" && -x "$OPENCLI_BIN" ]]; then
  OPENCLI_START_DAEMON="$(env_or_file SEEKTALENT_LIEPIN_OPENCLI_START_DAEMON)"
  if [[ "$OPENCLI_START_DAEMON" == "1" || "$OPENCLI_START_DAEMON" == "true" ]]; then
    echo "Starting OpenCLI browser bridge daemon for Liepin local browser actions..." >&2
    if ! "$OPENCLI_BIN" daemon restart >&2; then
      echo "OpenCLI browser bridge daemon did not start; Liepin OpenCLI source will fail closed." >&2
    elif ! wait_for_opencli_extension; then
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
fi

backend_pid=""
cleanup() {
  if [[ "$BROWSER_ACTION_BACKEND" == "opencli" && -x "$OPENCLI_BIN" ]]; then
    env \
      NODE_PATH="$WEB_DIR/node_modules" \
      PYTHONPATH="$ROOT/src" \
      SEEKTALENT_LIEPIN_OPENCLI_COMMAND="$OPENCLI_BIN" \
      SEEKTALENT_LIEPIN_OPENCLI_LEASE_DIR="$WORKSPACE_ROOT/.seektalent/opencli_leases" \
      uv run python -m seektalent.providers.pi_agent.opencli_browser_cli cleanup_orphaned_tabs \
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
  SEEKTALENT_LIEPIN_WORKER_MODE="pi_agent" \
  SEEKTALENT_LIEPIN_BROWSER_ACTION_BACKEND="$BROWSER_ACTION_BACKEND" \
  SEEKTALENT_LIEPIN_OPENCLI_COMMAND="$OPENCLI_BIN" \
  SEEKTALENT_LIEPIN_PI_COMMAND="$PI_COMMAND" \
  SEEKTALENT_LIEPIN_PI_MCP_CONFIG_PATH="$MCP_CONFIG_PATH" \
  SEEKTALENT_LIEPIN_ACCOUNT_BINDING_SECRET="$ACCOUNT_BINDING_SECRET" \
  SEEKTALENT_LIEPIN_DOKOBOT_MCP_COMMAND="$DOKOBOT_MCP_COMMAND" \
  SEEKTALENT_LIEPIN_DOKOBOT_MCP_SERVER_NAME="$DOKOBOT_MCP_SERVER_NAME" \
  SEEKTALENT_LIEPIN_DOKOBOT_MCP_ARGS_JSON="$DOKOBOT_MCP_ARGS_JSON" \
  SEEKTALENT_LIEPIN_DOKOBOT_DIRECT_TOOLS_JSON="$DOKOBOT_DIRECT_TOOLS_JSON" \
  SEEKTALENT_LIEPIN_DOKOBOT_OBSERVED_TOOLS_JSON="$DOKOBOT_OBSERVED_TOOLS_JSON" \
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
