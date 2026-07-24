#!/usr/bin/env bash
set -euo pipefail

VERSION="${1:-0.7.49}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
STAGING_ROOT="${SEEKTALENT_STAGING_ROOT:-${HOME}/.seektalent-staging}"
STAGING_HOME="${STAGING_ROOT}/home"
VENV="${STAGING_ROOT}/venv"
BIN_DIR="${STAGING_ROOT}/bin"
RUNTIME_DIR="${STAGING_ROOT}/runtime"
WTSCLI_REPOSITORY="https://github.com/FrankQDWang/wtscli.git"
WTSCLI_COMMIT="709622fc3fb3463f15551467fdf0d28571dfd049"
WTSCLI_NPM_VERSION="10.9.2"

fail() {
  echo "reason_code=$1 $2" >&2
  exit 1
}

command -v uv >/dev/null 2>&1 || fail "seektalent_staging_uv_missing" "uv is required."
command -v node >/dev/null 2>&1 || fail "seektalent_staging_node_missing" "Standalone Node is required."

NODE="$(command -v node)"
case "${NODE}" in
  *"/Application Support/Domi/"*|*"/Domi.app/"*)
    fail "seektalent_staging_domi_node_rejected" "staging refuses the Domi Node runtime: ${NODE}"
    ;;
esac

mkdir -p "${STAGING_HOME}" "${BIN_DIR}" "${RUNTIME_DIR}"
uv venv --python 3.13 --allow-existing "${VENV}"
uv pip install \
  --python "${VENV}/bin/python" \
  --upgrade \
  --refresh-package seektalent \
  "seektalent==${VERSION}"

INSTALLED_VERSION="$(${VENV}/bin/python -c 'import seektalent; print(seektalent.__version__)')"
if [[ "${INSTALLED_VERSION}" != "${VERSION}" ]]; then
  fail "seektalent_staging_version_mismatch" "Expected SeekTalent ${VERSION}, found ${INSTALLED_VERSION}."
fi

BUNDLE_DIR="${SEEKTALENT_STAGING_WTSCLI_BUNDLE_DIR:-}"
TEMP_ROOT=""
cleanup() {
  if [[ -n "${TEMP_ROOT}" && -d "${TEMP_ROOT}" ]]; then
    rm -rf "${TEMP_ROOT}"
  fi
}
trap cleanup EXIT

if [[ -z "${BUNDLE_DIR}" ]]; then
  command -v git >/dev/null 2>&1 || fail "seektalent_staging_git_missing" "git is required to build WTSCLI."
  command -v npx >/dev/null 2>&1 || fail "seektalent_staging_npx_missing" "npx is required to build WTSCLI."
  WTSCLI_NPM=(npx --yes "npm@${WTSCLI_NPM_VERSION}")
  TEMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/seektalent-staging.XXXXXX")"
  WTSCLI_ROOT="${TEMP_ROOT}/wtscli"
  BUNDLE_DIR="${TEMP_ROOT}/browser-bridge"
  git clone --filter=blob:none --no-checkout "${WTSCLI_REPOSITORY}" "${WTSCLI_ROOT}"
  git -C "${WTSCLI_ROOT}" checkout "${WTSCLI_COMMIT}"
  (cd "${WTSCLI_ROOT}" && "${WTSCLI_NPM[@]}" ci --ignore-scripts)
  (cd "${WTSCLI_ROOT}/extension" && "${WTSCLI_NPM[@]}" ci --ignore-scripts)
  (cd "${WTSCLI_ROOT}" && "${WTSCLI_NPM[@]}" run build:seektalent-bundle -- --out "${BUNDLE_DIR}")
fi

"${VENV}/bin/python" "${SCRIPT_DIR}/install_staging_browser_bridge.py" \
  --bundle-dir "${BUNDLE_DIR}" \
  --staging-home "${STAGING_HOME}" \
  --node "${NODE}"

install -m 0755 "${SCRIPT_DIR}/run_seektalent_staging.py" "${RUNTIME_DIR}/run_seektalent_staging.py"
if [[ -f "${REPO_ROOT}/.env" ]]; then
  PYTHONPATH="${REPO_ROOT}" "${VENV}/bin/python" -c \
    'from pathlib import Path; from scripts.run_seektalent_staging import write_staging_llm_config; import sys; write_staging_llm_config(Path(sys.argv[1]), Path(sys.argv[2]))' \
    "${REPO_ROOT}/.env" "${STAGING_ROOT}/config.env"
fi

WRAPPER="${BIN_DIR}/seektalent-staging"
python3 - "${WRAPPER}" <<'PY'
from pathlib import Path
import sys

wrapper = Path(sys.argv[1])
wrapper.write_text(
    """#!/bin/sh
set -eu
BIN_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
STAGING_ROOT=${SEEKTALENT_STAGING_ROOT:-$(dirname -- "$BIN_DIR")}
NODE=${SEEKTALENT_WTSCLI_NODE:-$(command -v node || true)}
if [ -z "$NODE" ]; then
  echo "reason_code=seektalent_staging_node_missing Standalone Node is required." >&2
  exit 1
fi
case "$NODE" in
  *"/Application Support/Domi/"*|*"/Domi.app/"*)
    echo "reason_code=seektalent_staging_domi_node_rejected staging refuses the Domi Node runtime: $NODE" >&2
    exit 1
    ;;
esac
export SEEKTALENT_STAGING_ROOT="$STAGING_ROOT"
export SEEKTALENT_WTSCLI_NODE="$NODE"
export HOME="$STAGING_ROOT/home"
export PATH="$STAGING_ROOT/venv/bin:$PATH"
exec "$STAGING_ROOT/venv/bin/python" "$STAGING_ROOT/runtime/run_seektalent_staging.py" "$@"
""",
    encoding="utf-8",
)
wrapper.chmod(0o755)
PY

echo "SeekTalent staging installed from the published prod package."
echo "SeekTalent version: ${INSTALLED_VERSION}"
echo "Python: ${VENV}/bin/python"
echo "Node: ${NODE}"
echo "Chrome extension: ${STAGING_HOME}/.seektalent/chrome-extension/wtscli"
echo "Load that unpacked extension in chrome://extensions before running the check."
echo "Check: ${WRAPPER} --check"
echo "Run: ${WRAPPER}"
