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
  if ! command -v shasum >/dev/null 2>&1 || ! command -v unzip >/dev/null 2>&1; then
    _seektalent_offline_fail "offline_system_tool_missing" "The macOS shasum and unzip commands are required."
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

  if ! (cd "${bundle_root}" && shasum -a 256 -c SHA256SUMS >/dev/null); then
    _seektalent_offline_fail "offline_bundle_checksum_mismatch" "One or more bundled resources failed SHA256 verification."
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
  local actual_runtime_sha256
  actual_runtime_sha256="$("${domi_python}" -c 'import hashlib, pathlib, sys; print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())' "${wtscli_runtime_archive}")" || return 1
  if [[ "${actual_runtime_sha256}" != "${browser_bridge_runtime_sha256}" ]]; then
    _seektalent_offline_fail "browser_bridge_runtime_checksum_mismatch" "The prepared WTSCLI runtime failed its bundle-manifest checksum."
    return 1
  fi

  local prefix="${install_root}/python-prefix/${version}"
  local site_packages="${prefix}/site-packages"
  local bin_dir="${install_root}/bin"
  local wtscli_install_dir="${install_root}/wtscli-runtime/wtscli/${wtscli_version}"
  local wtscli_main="${wtscli_install_dir}/node_modules/wtscli/dist/src/main.js"
  local extension_install_dir="${install_root}/chrome-extension/wtscli"
  local extension_manifest="${extension_install_dir}/manifest.json"
  local installed_bridge_manifest="${install_root}/browser-bridge/bridge-manifest.json"

  rm -rf "${prefix}"
  mkdir -p "${site_packages}" "${bin_dir}" || return 1
  "${domi_python}" "${pip_zipapp}" install \
    --disable-pip-version-check \
    --no-index \
    --find-links "${wheelhouse}" \
    --upgrade \
    --ignore-installed \
    --no-warn-conflicts \
    --no-warn-script-location \
    --target "${site_packages}" \
    "${app_wheel}" || {
      _seektalent_offline_fail "seektalent_offline_install_failed" "Failed to install SeekTalent from the bundled wheelhouse."
      return 1
    }

  local prepared_runtime_dir
  prepared_runtime_dir="$(mktemp -d "${TMPDIR:-/tmp}/seektalent-wtscli-runtime.XXXXXX")" || return 1
  if ! unzip -q "${wtscli_runtime_archive}" -d "${prepared_runtime_dir}"; then
    rm -rf "${prepared_runtime_dir}"
    _seektalent_offline_fail "browser_bridge_runtime_extract_failed" "The prepared WTSCLI runtime could not be extracted."
    return 1
  fi
  if ! PYTHONPATH="${site_packages}${PYTHONPATH:+:${PYTHONPATH}}" \
    "${domi_python}" -m seektalent.domi_bootstrap \
      --package-version "${version}" \
      --python-path "${site_packages}" \
      --domi-python "${domi_python}" \
      --domi-node "${domi_node}" \
      --browser-bridge-bundle-dir "${wtscli_bundle_dir}" \
      --browser-bridge-prepared-runtime-dir "${prepared_runtime_dir}" \
      --bin-dir "${bin_dir}" \
      --print-json; then
    rm -rf "${prepared_runtime_dir}"
    _seektalent_offline_fail "seektalent_domi_bootstrap_failed" "Failed to verify and install the exact WTSCLI bundle."
    return 1
  fi
  rm -rf "${prepared_runtime_dir}"

  case ":${PATH}:" in
    *":${bin_dir}:"*) ;;
    *) export PATH="${bin_dir}:${PATH}" ;;
  esac

  local installed_version installed_wtscli_version installed_extension_version
  installed_version="$("${bin_dir}/seektalent" --version)" || return 1
  installed_wtscli_version="$("${domi_node}" "${wtscli_main}" --version)" || return 1
  installed_extension_version="$(_seektalent_json_value "${domi_python}" "${extension_manifest}" "version")" || return 1
  if [[ "${installed_version}" != "${version}" ]]; then
    _seektalent_offline_fail "seektalent_offline_version_mismatch" "Expected SeekTalent ${version} but found ${installed_version}."
    return 1
  fi
  if [[ "${installed_wtscli_version}" != "${wtscli_version}" ]]; then
    _seektalent_offline_fail "wtscli_offline_probe_failed" "Expected WTSCLI ${wtscli_version} but found ${installed_wtscli_version}."
    return 1
  fi
  if [[ "${installed_extension_version}" != "${extension_version}" || ! -f "${installed_bridge_manifest}" ]]; then
    _seektalent_offline_fail "browser_bridge_pair_mismatch" "The installed WTSCLI extension and bundle manifest did not remain paired."
    return 1
  fi

  echo "SeekTalent macOS Intel offline install ready."
  echo "SeekTalent version: ${installed_version}"
  echo "WTSCLI version: ${installed_wtscli_version}"
  echo "WTSCLI Browser Bridge version: ${installed_extension_version}"
  echo "WTSCLI Browser Bridge build: ${browser_bridge_build_id}"
  echo "WTSCLI extension ID: ${browser_bridge_extension_id}"
  echo "Chrome extension directory: ${extension_install_dir}"
  echo "Chrome setup: open chrome://extensions, enable Developer mode, and choose Load unpacked."
  echo "Run: export SEEKTALENT_DOMI_JWT='<new Domi JWT>'; seektalent workbench"
}

if _seektalent_offline_install; then
  unset -f _seektalent_json_value _seektalent_offline_fail _seektalent_offline_install
  return 0
fi
unset -f _seektalent_json_value _seektalent_offline_fail _seektalent_offline_install
return 1
