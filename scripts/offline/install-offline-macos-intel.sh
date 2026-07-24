#!/usr/bin/env bash

if ! (return 0 2>/dev/null); then
  echo "reason_code=offline_installer_not_sourced Run this installer with: source ./install-offline.sh" >&2
  exit 1
fi

_seektalent_offline_fail() {
  local reason_code="$1"
  local message="$2"
  echo "reason_code=${reason_code} ${message}" >&2
  return 1
}

_seektalent_json_value() {
  local python="$1"
  local manifest="$2"
  local key="$3"
  "${python}" -c 'import json, pathlib, sys; print(json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))[sys.argv[2]])' "${manifest}" "${key}"
}

_seektalent_offline_install() {
  local bundle_root
  bundle_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  local manifest="${bundle_root}/bundle-manifest.json"
  local install_root="${HOME}/.seektalent"
  local domi_python="${DOMI_PYTHON:-}"
  local domi_node="${DOMI_NODE:-${SEEKTALENT_DOMI_NODE:-}}"

  if [[ -z "${domi_python}" ]]; then
    local python_candidate
    for python_candidate in \
      "/Applications/Domi.app/Contents/Resources/extraResources/python/runtime/bin/python" \
      "/Applications/Domi.app/Contents/Resources/extraResources/python/runtime/bin/python3" \
      "${HOME}/Library/Application Support/Domi/runtime/python/bin/python" \
      "${HOME}/Library/Application Support/Domi/runtime/python/bin/python3" \
      "${HOME}/.domi/runtime/python/bin/python" \
      "${HOME}/.domi/runtime/python/bin/python3"; do
      if [[ -x "${python_candidate}" ]]; then
        domi_python="${python_candidate}"
        break
      fi
    done
  fi

  if [[ -z "${domi_node}" ]]; then
    local node_candidate
    for node_candidate in \
      "/Applications/Domi.app/Contents/Resources/extraResources/node/runtime/bin/node" \
      "/Applications/Domi.app/Contents/Resources/extraResources/node/bin/node" \
      "/Applications/Domi.app/Contents/Resources/extraResources/node/node" \
      "${HOME}/Library/Application Support/Domi/runtime/node/node" \
      "${HOME}/Library/Application Support/Domi/runtime/node/bin/node" \
      "${HOME}/.domi/runtime/node/node" \
      "${HOME}/.domi/runtime/node/bin/node"; do
      if [[ -x "${node_candidate}" ]]; then
        domi_node="${node_candidate}"
        break
      fi
    done
  fi

  if [[ ! -x "${domi_python}" ]]; then
    _seektalent_offline_fail "domi_python_missing" "Domi Python was not found."
    return 1
  fi
  if [[ ! -x "${domi_node}" ]]; then
    _seektalent_offline_fail "domi_node_missing" "Domi Node was not found."
    return 1
  fi
  if [[ ! -f "${manifest}" ]]; then
    _seektalent_offline_fail "offline_manifest_missing" "bundle-manifest.json was not found."
    return 1
  fi
  if [[ ! -f "${bundle_root}/SHA256SUMS" ]]; then
    _seektalent_offline_fail "offline_manifest_missing" "SHA256SUMS was not found."
    return 1
  fi
  if ! command -v shasum >/dev/null 2>&1 || ! command -v unzip >/dev/null 2>&1; then
    _seektalent_offline_fail "offline_system_tool_missing" "The macOS shasum and unzip commands are required."
    return 1
  fi
  if ! (cd "${bundle_root}" && shasum -a 256 -c SHA256SUMS >/dev/null); then
    _seektalent_offline_fail "offline_bundle_checksum_mismatch" "One or more bundled resources failed SHA256 verification."
    return 1
  fi
  local admission_wheels=("${bundle_root}"/python-wheelhouse/seektalent-*-py3-none-any.whl)
  local admission_wheel="${admission_wheels[0]:-}"
  local admission_bundle="${bundle_root}/wtscli-browser-bridge"
  if [[ "${#admission_wheels[@]}" -ne 1 || ! -f "${admission_wheel}" ]]; then
    _seektalent_offline_fail "offline_resource_missing" "The exact SeekTalent wheel required for browser bridge admission was not found."
    return 1
  fi
  if [[ ! -f "${admission_bundle}/bridge-manifest.json" ]]; then
    _seektalent_offline_fail "offline_resource_missing" "The exact WTSCLI browser bridge bundle was not found."
    return 1
  fi
  if ! PYTHONPATH="${admission_wheel}" "${domi_python}" -c \
    'from pathlib import Path; import sys; from seektalent.browser_bridge_manifest import load_browser_bridge_bundle; load_browser_bridge_bundle(Path(sys.argv[1]))' \
    "${admission_bundle}"; then
    _seektalent_offline_fail "wtscli_bundle_invalid" "The exact SeekTalent WTSCLI bundle failed strict admission."
    return 1
  fi

  local version wtscli_version extension_version expected_python_version
  local browser_bridge_bundle browser_bridge_runtime browser_bridge_runtime_sha256
  local browser_bridge_build_id browser_bridge_extension_id
  version="$(_seektalent_json_value "${domi_python}" "${manifest}" "seektalent_version")" || return 1
  wtscli_version="$(_seektalent_json_value "${domi_python}" "${manifest}" "wtscli_version")" || return 1
  extension_version="$(_seektalent_json_value "${domi_python}" "${manifest}" "extension_version")" || return 1
  browser_bridge_bundle="$(_seektalent_json_value "${domi_python}" "${manifest}" "browser_bridge_bundle")" || return 1
  browser_bridge_runtime="$(_seektalent_json_value "${domi_python}" "${manifest}" "browser_bridge_runtime")" || return 1
  browser_bridge_runtime_sha256="$(_seektalent_json_value "${domi_python}" "${manifest}" "browser_bridge_runtime_sha256")" || return 1
  browser_bridge_build_id="$(_seektalent_json_value "${domi_python}" "${manifest}" "browser_bridge_build_id")" || return 1
  browser_bridge_extension_id="$(_seektalent_json_value "${domi_python}" "${manifest}" "browser_bridge_extension_id")" || return 1
  expected_python_version="$(_seektalent_json_value "${domi_python}" "${manifest}" "python_version")" || return 1
  if [[ "${browser_bridge_bundle}" != "wtscli-browser-bridge" ]]; then
    _seektalent_offline_fail "browser_bridge_bundle_invalid" "The offline bundle did not name the canonical WTSCLI browser bridge directory."
    return 1
  fi
  if [[ "${browser_bridge_runtime}" != "wtscli-runtime/wtscli-${wtscli_version}-runtime.zip" \
    || ! "${browser_bridge_runtime_sha256}" =~ ^[0-9a-f]{64}$ ]]; then
    _seektalent_offline_fail "browser_bridge_runtime_invalid" "The offline bundle did not name the exact prepared WTSCLI runtime."
    return 1
  fi

  local python_arch python_version
  python_arch="$("${domi_python}" -c 'import platform; print(platform.machine())')" || return 1
  python_version="$("${domi_python}" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')" || return 1
  if [[ "${python_arch}" != "x86_64" ]]; then
    _seektalent_offline_fail "domi_python_arch_mismatch" "Expected Domi Python x86_64 but found ${python_arch}."
    return 1
  fi
  if [[ "${python_version}" != "${expected_python_version}" ]]; then
    _seektalent_offline_fail "domi_python_version_mismatch" "Expected Domi Python ${expected_python_version} but found ${python_version}."
    return 1
  fi

  local wheelhouse="${bundle_root}/python-wheelhouse"
  local app_wheel="${wheelhouse}/seektalent-${version}-py3-none-any.whl"
  local pip_zipapp="${bundle_root}/tools/pip.pyz"
  local wtscli_bundle_dir="${bundle_root}/${browser_bridge_bundle}"
  local wtscli_runtime_archive="${bundle_root}/${browser_bridge_runtime}"
  local required_file
  for required_file in "${app_wheel}" "${pip_zipapp}" "${wtscli_bundle_dir}/bridge-manifest.json" "${wtscli_runtime_archive}"; do
    if [[ ! -f "${required_file}" ]]; then
      _seektalent_offline_fail "offline_resource_missing" "Required offline resource was not found: ${required_file}"
      return 1
    fi
  done
  if [[ "${app_wheel}" != "${admission_wheel}" ]]; then
    _seektalent_offline_fail "offline_resource_missing" "The admitted SeekTalent wheel did not match bundle-manifest.json."
    return 1
  fi
  local actual_runtime_sha256
  actual_runtime_sha256="$("${domi_python}" -c 'import hashlib, pathlib, sys; print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())' "${wtscli_runtime_archive}")" || return 1
  if [[ "${actual_runtime_sha256}" != "${browser_bridge_runtime_sha256}" ]]; then
    _seektalent_offline_fail "browser_bridge_runtime_checksum_mismatch" "The prepared WTSCLI runtime failed its bundle-manifest checksum."
    return 1
  fi

  local prefix="${install_root}/python-prefix/${version}"
  local site_packages="${prefix}/site-packages"
  local bin_dir="${install_root}/bin"
  local extension_install_dir="${install_root}/chrome-extension/wtscli"

  local candidate_root
  candidate_root="$(mktemp -d "${TMPDIR:-/tmp}/seektalent-offline-install.XXXXXX")" || return 1
  local candidate_prefix="${candidate_root}/python-prefix"
  local candidate_site_packages="${candidate_prefix}/site-packages"
  local prepared_runtime_dir="${candidate_root}/prepared-runtime"
  mkdir -p "${candidate_site_packages}" "${prepared_runtime_dir}" || {
    rm -rf -- "${candidate_root}"
    return 1
  }
  "${domi_python}" "${pip_zipapp}" install \
    --disable-pip-version-check \
    --no-index \
    --find-links "${wheelhouse}" \
    --upgrade \
    --ignore-installed \
    --no-warn-conflicts \
    --no-warn-script-location \
    --target "${candidate_site_packages}" \
    "${app_wheel}" || {
      rm -rf -- "${candidate_root}"
      _seektalent_offline_fail "seektalent_offline_install_failed" "Failed to install SeekTalent from the bundled wheelhouse."
      return 1
    }
  local candidate_version
  candidate_version="$(PYTHONPATH="${candidate_site_packages}${PYTHONPATH:+:${PYTHONPATH}}" \
    "${domi_python}" -m seektalent --version)" || {
      rm -rf -- "${candidate_root}"
      _seektalent_offline_fail "seektalent_offline_version_mismatch" "The staged SeekTalent candidate could not report its version."
      return 1
    }
  if [[ "${candidate_version}" != "${version}" ]]; then
    rm -rf -- "${candidate_root}"
    _seektalent_offline_fail "seektalent_offline_version_mismatch" "Expected staged SeekTalent ${version} but found ${candidate_version}."
    return 1
  fi

  if ! unzip -q "${wtscli_runtime_archive}" -d "${prepared_runtime_dir}"; then
    rm -rf -- "${candidate_root}"
    _seektalent_offline_fail "browser_bridge_runtime_extract_failed" "The prepared WTSCLI runtime could not be extracted."
    return 1
  fi
  if ! PYTHONPATH="${candidate_site_packages}${PYTHONPATH:+:${PYTHONPATH}}" \
    "${domi_python}" -m seektalent.domi_bootstrap \
      --package-version "${version}" \
      --python-path "${site_packages}" \
      --python-prefix-candidate "${candidate_prefix}" \
      --python-prefix-target "${prefix}" \
      --domi-python "${domi_python}" \
      --domi-node "${domi_node}" \
      --browser-bridge-bundle-dir "${wtscli_bundle_dir}" \
      --browser-bridge-prepared-runtime-dir "${prepared_runtime_dir}" \
      --bin-dir "${bin_dir}" \
      --print-json; then
    rm -rf -- "${candidate_root}"
    _seektalent_offline_fail "seektalent_domi_bootstrap_failed" "Failed to verify and install the exact WTSCLI bundle."
    return 1
  fi
  rm -rf -- "${candidate_root}"

  case ":${PATH}:" in
    *":${bin_dir}:"*) ;;
    *) export PATH="${bin_dir}:${PATH}" ;;
  esac

  local installed_version="${version}"
  local installed_wtscli_version="${wtscli_version}"
  local installed_extension_version="${extension_version}"

  echo "SeekTalent macOS Intel offline install ready."
  echo "SeekTalent version: ${installed_version}"
  echo "WTSCLI version: ${installed_wtscli_version}"
  echo "WTSCLI Browser Bridge version: ${installed_extension_version}"
  echo "WTSCLI Browser Bridge build: ${browser_bridge_build_id}"
  echo "WTSCLI extension ID: ${browser_bridge_extension_id}"
  echo "Chrome extension directory: ${extension_install_dir}"
  echo "Chrome setup: open chrome://extensions, enable Developer mode, and choose Load unpacked."
  echo "Run: export SEEKTALENT_DOMI_JWT='<new Domi JWT>'; seektalent workbench"
  return 0
}

if _seektalent_offline_install; then
  unset -f _seektalent_json_value _seektalent_offline_fail _seektalent_offline_install
  return 0
fi
unset -f _seektalent_json_value _seektalent_offline_fail _seektalent_offline_install
return 1
