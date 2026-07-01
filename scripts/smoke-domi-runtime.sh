#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

DOMI_PYTHON="${DOMI_PYTHON:-/Applications/Domi.app/Contents/Resources/extraResources/python/runtime/bin/python}"
DOMI_RUNTIME_ROOT_RAW="${SEEKTALENT_DOMI_RUNTIME_ROOT:-${HOME}/.seektalent/domi-runtime}"
DOMI_WORKBENCH_PORT="${SEEKTALENT_DOMI_SMOKE_PORT:-8011}"
SEEKTALENT_DOMI_LLM_BASE_URL="${SEEKTALENT_DOMI_LLM_BASE_URL:-https://test-api-agent.hewa.cn/api/v1/runtime/llm-proxy/v1}"
SEEKTALENT_DOMI_LLM_CHANNEL="${SEEKTALENT_DOMI_LLM_CHANNEL:-seek_talent}"
SEEKTALENT_DOMI_SMOKE_MODEL="${SEEKTALENT_DOMI_SMOKE_MODEL:-deepseek-v4-flash}"

fail() {
  local reason_code="$1"
  local message="$2"
  echo "reason_code=${reason_code} ${message}" >&2
  exit 1
}

if [[ -z "${SEEKTALENT_DOMI_JWT:-}" ]]; then
  fail "seektalent_domi_jwt_missing" "SEEKTALENT_DOMI_JWT is required for Domi runtime smoke."
fi

if [[ ! -x "${DOMI_PYTHON}" ]]; then
  fail "domi_python_missing" "Domi Python runtime is not executable: ${DOMI_PYTHON}"
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

DOMI_VENV="${DOMI_RUNTIME_ROOT}/venv"
DOMI_DIST_DIR="${DOMI_RUNTIME_ROOT}/dist"
WORKBENCH_LOG="${DOMI_RUNTIME_ROOT}/workbench.log"

mkdir -p "${DOMI_RUNTIME_ROOT}" "${DOMI_DIST_DIR}"

if [[ ! -x "${DOMI_VENV}/bin/python" ]]; then
  echo "Creating Domi runtime venv under ${DOMI_RUNTIME_ROOT}" >&2
  "${DOMI_PYTHON}" -m venv "${DOMI_VENV}"
else
  echo "Reusing Domi runtime venv under ${DOMI_RUNTIME_ROOT}" >&2
fi

VENV_PYTHON="${DOMI_VENV}/bin/python"
SEEKTALENT_BIN="${DOMI_VENV}/bin/seektalent"
SEEKTALENT_OPENCLI_BIN="${DOMI_VENV}/bin/seektalent-opencli"

echo "Building and installing SeekTalent wheel with Domi runtime Python" >&2
"${VENV_PYTHON}" -m pip install --upgrade pip build
rm -f "${DOMI_DIST_DIR}"/seektalent-*.whl
"${VENV_PYTHON}" -m build --wheel --outdir "${DOMI_DIST_DIR}" .
WHEEL_PATH="$(find "${DOMI_DIST_DIR}" -maxdepth 1 -name 'seektalent-*.whl' -print -quit)"
if [[ -z "${WHEEL_PATH}" ]]; then
  fail "domi_wheel_missing" "SeekTalent wheel was not produced in ${DOMI_DIST_DIR}."
fi
"${VENV_PYTHON}" -m pip install --force-reinstall "${WHEEL_PATH}"

if [[ ! -x "${SEEKTALENT_BIN}" ]]; then
  fail "domi_seektalent_bin_missing" "Installed seektalent executable is missing: ${SEEKTALENT_BIN}"
fi
if [[ ! -x "${SEEKTALENT_OPENCLI_BIN}" ]]; then
  fail "domi_seektalent_opencli_bin_missing" "Installed seektalent-opencli executable is missing: ${SEEKTALENT_OPENCLI_BIN}"
fi

export SEEKTALENT_TEXT_LLM_PROVIDER_LABEL=domi
export SEEKTALENT_DOMI_JWT
export SEEKTALENT_DOMI_LLM_BASE_URL
export SEEKTALENT_DOMI_LLM_CHANNEL
export SEEKTALENT_DOMI_SMOKE_MODEL
export SEEKTALENT_RUNTIME_MODE=prod

echo "Running seektalent doctor in Domi provider mode" >&2
"${SEEKTALENT_BIN}" doctor --env-file /dev/null --json > "${DOMI_RUNTIME_ROOT}/doctor.json"

echo "Running Domi LLM proxy hello" >&2
"${VENV_PYTHON}" - <<'PY'
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

base_url = os.environ["SEEKTALENT_DOMI_LLM_BASE_URL"].rstrip("/")
channel = os.environ["SEEKTALENT_DOMI_LLM_CHANNEL"]
token = os.environ["SEEKTALENT_DOMI_JWT"]
model = os.environ["SEEKTALENT_DOMI_SMOKE_MODEL"]
url = f"{base_url}/chat/completions?{urllib.parse.urlencode({'channel': channel})}"
payload = {
    "model": model,
    "messages": [{"role": "user", "content": "hello"}],
    "stream": False,
}
request = urllib.request.Request(
    url,
    data=json.dumps(payload).encode("utf-8"),
    headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    },
    method="POST",
)


def safe_prefix(text: str) -> str:
    return text.replace(token, "[redacted]")[:200]


try:
    with urllib.request.urlopen(request, timeout=60) as response:
        body = response.read(4096).decode("utf-8", errors="replace")
        print(json.dumps({"status": response.status, "body_prefix": safe_prefix(body)}, ensure_ascii=False))
except urllib.error.HTTPError as exc:
    detail = exc.read(4096).decode("utf-8", errors="replace")
    print(
        json.dumps({"reason_code": "domi_llm_proxy_http_error", "status": exc.code, "body_prefix": safe_prefix(detail)}, ensure_ascii=False),
        file=sys.stderr,
    )
    raise SystemExit(1)
except urllib.error.URLError as exc:
    print(
        json.dumps({"reason_code": "domi_llm_proxy_unavailable", "error_prefix": safe_prefix(str(exc.reason))}, ensure_ascii=False),
        file=sys.stderr,
    )
    raise SystemExit(1)
PY

echo "Checking OpenCLI daemon status" >&2
if ! "${SEEKTALENT_OPENCLI_BIN}" daemon status > "${DOMI_RUNTIME_ROOT}/opencli-status.txt" 2>&1; then
  if [[ "${SEEKTALENT_DOMI_OPENCLI_RESTART:-0}" != "1" ]]; then
    fail "domi_opencli_status_unavailable" "OpenCLI status check failed; see ${DOMI_RUNTIME_ROOT}/opencli-status.txt. Set SEEKTALENT_DOMI_OPENCLI_RESTART=1 to restart it during smoke."
  fi
  echo "Restarting OpenCLI daemon via installed seektalent-opencli" >&2
  if ! "${SEEKTALENT_OPENCLI_BIN}" daemon restart > "${DOMI_RUNTIME_ROOT}/opencli-restart.txt" 2>&1; then
    fail "domi_opencli_restart_failed" "OpenCLI daemon restart failed; see ${DOMI_RUNTIME_ROOT}/opencli-restart.txt"
  fi
  if ! "${SEEKTALENT_OPENCLI_BIN}" daemon status > "${DOMI_RUNTIME_ROOT}/opencli-status.txt" 2>&1; then
    fail "domi_opencli_status_unavailable" "OpenCLI status check failed after restart; see ${DOMI_RUNTIME_ROOT}/opencli-status.txt"
  fi
fi
if ! grep -q "Extension: connected" "${DOMI_RUNTIME_ROOT}/opencli-status.txt"; then
  fail "domi_opencli_extension_disconnected" "OpenCLI extension is not connected; see ${DOMI_RUNTIME_ROOT}/opencli-status.txt"
fi

WORKBENCH_PID=""
cleanup() {
  if [[ -n "${WORKBENCH_PID}" ]] && kill -0 -- "-${WORKBENCH_PID}" 2>/dev/null; then
    kill -TERM -- "-${WORKBENCH_PID}" 2>/dev/null || true
    for _ in $(seq 1 10); do
      if ! kill -0 -- "-${WORKBENCH_PID}" 2>/dev/null; then
        return
      fi
      sleep 0.5
    done
    kill -KILL -- "-${WORKBENCH_PID}" 2>/dev/null || true
  fi
}
trap cleanup EXIT
trap 'cleanup; exit 130' INT
trap 'cleanup; exit 143' TERM

echo "Starting packaged Workbench with seektalent workbench --port ${DOMI_WORKBENCH_PORT}" >&2
WORKBENCH_PID="$("${VENV_PYTHON}" - "${SEEKTALENT_BIN}" "${DOMI_WORKBENCH_PORT}" "${WORKBENCH_LOG}" <<'PY'
import subprocess
import sys

seektalent_bin = sys.argv[1]
port = sys.argv[2]
log_path = sys.argv[3]

with open(log_path, "ab") as log:
    process = subprocess.Popen(
        [seektalent_bin, "workbench", "--port", port, "--host", "127.0.0.1"],
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
print(process.pid)
PY
)"

for _ in $(seq 1 60); do
  if "${VENV_PYTHON}" - "${DOMI_WORKBENCH_PORT}" <<'PY'
import sys
import urllib.request

port = sys.argv[1]
with urllib.request.urlopen(f"http://127.0.0.1:{port}/openapi.json", timeout=1) as response:
    response.read(1)
PY
  then
    echo "Domi runtime smoke passed. Workbench URL: http://127.0.0.1:${DOMI_WORKBENCH_PORT}/" >&2
    exit 0
  fi
  if ! kill -0 -- "-${WORKBENCH_PID}" 2>/dev/null; then
    fail "domi_workbench_exited" "Workbench exited before /openapi.json became ready; see ${WORKBENCH_LOG}"
  fi
  sleep 1
done

fail "domi_workbench_startup_timeout" "Workbench did not become ready; see ${WORKBENCH_LOG}"
