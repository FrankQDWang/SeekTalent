#!/bin/sh
set -eu

fail() {
  reason_code="$1"
  message="$2"
  echo "reason_code=${reason_code} ${message}" >&2
  exit 1
}

install_home=${SEEKTALENT_INSTALL_HOME:-${HOME:-}}
[ -n "${install_home}" ] || fail "seektalent_install_home_missing" "SEEKTALENT_INSTALL_HOME or HOME is required."

[ -n "${SEEKTALENT_DOMI_JWT:-}" ] || fail \
  "seektalent_domi_jwt_missing" \
  "The Domi host must inject SEEKTALENT_DOMI_JWT before starting SeekTalent."

domi_python=${SEEKTALENT_DOMI_PYTHON:-${DOMI_PYTHON:-}}
[ -n "${domi_python}" ] || fail \
  "domi_python_missing" \
  "The Domi host must provide DOMI_PYTHON."
[ -x "${domi_python}" ] || fail "domi_python_missing" "Domi Python is not executable: ${domi_python}"

domi_node=${SEEKTALENT_DOMI_NODE:-${DOMI_NODE:-}}
[ -n "${domi_node}" ] || fail \
  "domi_node_missing" \
  "The Domi host must provide DOMI_NODE."
if [ -d "${domi_node}" ]; then
  domi_node="${domi_node}/node"
fi
[ -x "${domi_node}" ] || fail "domi_node_missing" "Domi Node is not executable: ${domi_node}"

seektalent_root="${install_home}/.seektalent"
receipt="${seektalent_root}/install-receipt.json"
runtime_verifier="${seektalent_root}/verify_domi_host_runtime.py"
bridge_manifest="${seektalent_root}/browser-bridge/bridge-manifest.json"
runtime_root="${seektalent_root}/wtscli-runtime"
extension_root="${seektalent_root}/chrome-extension/wtscli"

[ -f "${receipt}" ] || fail "seektalent_receipt_missing" "The exact SeekTalent install receipt is missing."
[ -f "${runtime_verifier}" ] || fail "domi_host_runtime_verifier_missing" "The installed host runtime verifier is missing."
if ! "${domi_python}" "${runtime_verifier}" validate-receipt \
  --node "${domi_node}" --receipt "${receipt}"; then
  exit 1
fi

product_version="$("${domi_python}" -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["productVersion"])' "${receipt}")" \
  || fail "seektalent_receipt_invalid" "The installed SeekTalent receipt cannot be read."
release_prefix="${seektalent_root}/python-prefix/${product_version}"
if [ -d "${release_prefix}/Lib/site-packages" ]; then
  release_site_packages="${release_prefix}/Lib/site-packages"
else
  release_site_packages="${release_prefix}/site-packages"
fi
[ -d "${release_site_packages}" ] || fail "seektalent_release_prefix_missing" "The exact installed SeekTalent Python prefix is missing."

if ! (
  cd / || exit 1
  PYTHONPATH="${release_site_packages}${PYTHONPATH:+:${PYTHONPATH}}" \
    PYTHONNOUSERSITE=1 "${domi_python}" -m seektalent.installed_domi_release \
      --home "${install_home}" >/dev/null
); then
  fail "seektalent_exact_release_invalid" "The installed SeekTalent package and WTSCLI pair failed exact validation."
fi

[ -f "${bridge_manifest}" ] || fail "wtscli_bundle_missing" "The installed WTSCLI bridge manifest is missing."
[ -d "${runtime_root}" ] || fail "wtscli_runtime_missing" "The installed WTSCLI runtime is missing."
[ -d "${extension_root}" ] || fail "wtscli_extension_missing" "The installed WTSCLI extension tree is missing."
seektalent_bin="${seektalent_root}/bin/seektalent"
[ -x "${seektalent_bin}" ] || fail "seektalent_bin_missing" "The installed SeekTalent command is missing."

export SEEKTALENT_DOMI_JWT
export SEEKTALENT_DOMI_PYTHON="${domi_python}"
export SEEKTALENT_DOMI_NODE="${domi_node}"
export DOMI_NODE="${domi_node}"
export SEEKTALENT_TEXT_LLM_PROVIDER_LABEL=domi
export SEEKTALENT_RUNTIME_MODE=prod
export SEEKTALENT_RUNTIME_ARTIFACT_OUTPUT_MODE=prod
export SEEKTALENT_WORKSPACE_ROOT="${install_home}"
export SEEKTALENT_PROVIDER_NAME=liepin
export SEEKTALENT_LIEPIN_WORKER_MODE=opencli
export SEEKTALENT_LIEPIN_BROWSER_ACTION_BACKEND=opencli
export SEEKTALENT_DOMI_LLM_CHANNEL="${SEEKTALENT_DOMI_LLM_CHANNEL:-seek_talent}"

exec "${seektalent_bin}" workbench "$@"
