#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

DOMI_PYTHON="${DOMI_PYTHON:-/Applications/Domi.app/Contents/Resources/extraResources/python/runtime/bin/python}"
DOMI_RUNTIME_ROOT_RAW="${SEEKTALENT_DOMI_RUNTIME_ROOT:-${HOME}/.seektalent/domi-runtime}"
DOMI_WORKBENCH_HOST="${SEEKTALENT_DOMI_WORKBENCH_HOST:-127.0.0.1}"
DOMI_WORKBENCH_PORT="${SEEKTALENT_DOMI_WORKBENCH_PORT:-8011}"
SEEKTALENT_DOMI_LLM_BASE_URL="${SEEKTALENT_DOMI_LLM_BASE_URL:-https://api-domi.hewa.cn/api/v1/runtime/llm-proxy/v1}"
SEEKTALENT_DOMI_LLM_CHANNEL="${SEEKTALENT_DOMI_LLM_CHANNEL:-seek_talent}"

fail() {
  local reason_code="$1"
  local message="$2"
  echo "reason_code=${reason_code} ${message}" >&2
  exit 1
}

if [[ ! -x "${DOMI_PYTHON}" ]]; then
  fail "domi_python_missing" "Domi Python runtime is not executable: ${DOMI_PYTHON}"
fi

if [[ -z "${SEEKTALENT_DOMI_JWT:-}" ]]; then
  fail "seektalent_domi_jwt_missing" "SEEKTALENT_DOMI_JWT is required for Domi Workbench mode."
fi

DOMI_RUNTIME_ROOT="$("${DOMI_PYTHON}" - "${DOMI_RUNTIME_ROOT_RAW}" <<'PY'
from pathlib import Path
import sys

print(Path(sys.argv[1]).expanduser().resolve())
PY
)"
case "${DOMI_RUNTIME_ROOT}" in
  /Applications/Domi.app|/Applications/Domi.app/*)
    fail "domi_runtime_root_forbidden" "SEEKTALENT_DOMI_RUNTIME_ROOT must not point inside /Applications/Domi.app."
    ;;
esac

export SEEKTALENT_DOMI_LLM_BASE_URL
export SEEKTALENT_DOMI_LLM_CHANNEL
export SEEKTALENT_DOMI_SMOKE_PORT="${DOMI_WORKBENCH_PORT}"

if [[ "${SEEKTALENT_DOMI_SKIP_SMOKE:-0}" != "1" ]]; then
  scripts/smoke-domi-runtime.sh
fi

SEEKTALENT_BIN="${DOMI_RUNTIME_ROOT}/venv/bin/seektalent"
if [[ ! -x "${SEEKTALENT_BIN}" ]]; then
  fail "domi_seektalent_bin_missing" "Installed seektalent executable is missing: ${SEEKTALENT_BIN}"
fi

SMOKE_COMMON_ENV=(
  "HOME=${HOME}"
  "PATH=${PATH:-/usr/bin:/bin}"
  "TMPDIR=${TMPDIR:-/tmp}"
)
if [[ -n "${LANG:-}" ]]; then
  SMOKE_COMMON_ENV+=("LANG=${LANG}")
fi
if [[ -n "${LC_ALL:-}" ]]; then
  SMOKE_COMMON_ENV+=("LC_ALL=${LC_ALL}")
fi
if [[ -n "${SHELL:-}" ]]; then
  SMOKE_COMMON_ENV+=("SHELL=${SHELL}")
fi
if [[ -n "${USER:-}" ]]; then
  SMOKE_COMMON_ENV+=("USER=${USER}")
fi

DOMI_ENV=(
  "${SMOKE_COMMON_ENV[@]}"
  "SEEKTALENT_TEXT_LLM_PROVIDER_LABEL=domi"
  "SEEKTALENT_DOMI_JWT=${SEEKTALENT_DOMI_JWT}"
  "SEEKTALENT_DOMI_LLM_BASE_URL=${SEEKTALENT_DOMI_LLM_BASE_URL}"
  "SEEKTALENT_DOMI_LLM_CHANNEL=${SEEKTALENT_DOMI_LLM_CHANNEL}"
  "SEEKTALENT_RUNTIME_MODE=prod"
  "SEEKTALENT_WORKSPACE_ROOT=${HOME}"
  "SEEKTALENT_PROVIDER_NAME=liepin"
  "SEEKTALENT_LIEPIN_WORKER_MODE=opencli"
  "SEEKTALENT_LIEPIN_BROWSER_ACTION_BACKEND=opencli"
)

echo "Starting Domi Workbench at http://${DOMI_WORKBENCH_HOST}:${DOMI_WORKBENCH_PORT}/" >&2
cd "${HOME}"
exec env -i "${DOMI_ENV[@]}" "${SEEKTALENT_BIN}" workbench --host "${DOMI_WORKBENCH_HOST}" --port "${DOMI_WORKBENCH_PORT}"
