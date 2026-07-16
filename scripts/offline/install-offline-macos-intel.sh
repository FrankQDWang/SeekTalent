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

_seektalent_verify_browser_bridge() {
  local python="$1"
  local bridge_manifest="$2"
  local runtime_dir="$3"
  local extension_dir="$4"
  "${python}" - "${bridge_manifest}" "${runtime_dir}" "${extension_dir}" <<'PY'
import hashlib
import json
import pathlib
import sys

manifest_path, runtime_dir, extension_dir = map(pathlib.Path, sys.argv[1:])
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
if manifest.get("schemaVersion") != "seektalent.browser_bridge_bundle.v1":
    raise SystemExit("browser bridge schema mismatch")
if manifest.get("implementation") != "seektalent-opencli":
    raise SystemExit("browser bridge implementation mismatch")

identity_path = runtime_dir / "node_modules" / "@jackwener" / "opencli" / "bridge-identity.json"
identity = json.loads(identity_path.read_text(encoding="utf-8"))
for key in ("implementation", "bridgeBuildId", "protocolVersion", "capabilities"):
    if identity.get(key) != manifest.get(key):
        raise SystemExit(f"runtime browser bridge identity mismatch: {key}")

extension = manifest["extension"]
actual_files = []
for path in sorted(extension_dir.rglob("*")):
    if path.is_symlink():
        raise SystemExit(f"extension contains a symlink: {path}")
    if not path.is_file():
        continue
    content = path.read_bytes()
    actual_files.append(
        {
            "path": path.relative_to(extension_dir).as_posix(),
            "size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
    )
if actual_files != extension.get("files"):
    raise SystemExit("extension file manifest mismatch")
tree = "".join(f"{item['sha256']}  {item['path']}\n" for item in actual_files).encode()
if hashlib.sha256(tree).hexdigest() != extension.get("treeSha256"):
    raise SystemExit("extension tree hash mismatch")
extension_manifest = extension_dir / "manifest.json"
if hashlib.sha256(extension_manifest.read_bytes()).hexdigest() != extension.get("manifestSha256"):
    raise SystemExit("extension manifest hash mismatch")
if json.loads(extension_manifest.read_text(encoding="utf-8")).get("version") != extension.get("version"):
    raise SystemExit("extension version mismatch")
PY
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
  if ! command -v unzip >/dev/null 2>&1 || ! command -v shasum >/dev/null 2>&1; then
    _seektalent_offline_fail "offline_system_tool_missing" "The macOS unzip and shasum commands are required."
    return 1
  fi

  local version opencli_version extension_version extension_sha256 expected_python_version
  local browser_bridge_manifest browser_bridge_build_id
  version="$(_seektalent_json_value "${domi_python}" "${manifest}" "seektalent_version")" || return 1
  opencli_version="$(_seektalent_json_value "${domi_python}" "${manifest}" "opencli_version")" || return 1
  extension_version="$(_seektalent_json_value "${domi_python}" "${manifest}" "extension_version")" || return 1
  extension_sha256="$(_seektalent_json_value "${domi_python}" "${manifest}" "extension_sha256")" || return 1
  browser_bridge_manifest="$(_seektalent_json_value "${domi_python}" "${manifest}" "browser_bridge_manifest")" || return 1
  browser_bridge_build_id="$(_seektalent_json_value "${domi_python}" "${manifest}" "browser_bridge_build_id")" || return 1
  expected_python_version="$(_seektalent_json_value "${domi_python}" "${manifest}" "python_version")" || return 1

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
  local opencli_archive="${bundle_root}/opencli/opencli-${opencli_version}-runtime.zip"
  local extension_archive="${bundle_root}/chrome-extension/wtscli-extension-v${extension_version}.zip"
  local bridge_manifest_source="${bundle_root}/${browser_bridge_manifest}"
  local required_file
  for required_file in "${app_wheel}" "${pip_zipapp}" "${opencli_archive}" "${extension_archive}" "${bridge_manifest_source}"; do
    if [[ ! -f "${required_file}" ]]; then
      _seektalent_offline_fail "offline_resource_missing" "Required offline resource was not found: ${required_file}"
      return 1
    fi
  done

  local actual_extension_sha256
  actual_extension_sha256="$("${domi_python}" -c 'import hashlib, pathlib, sys; print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())' "${extension_archive}")" || return 1
  if [[ "${actual_extension_sha256}" != "${extension_sha256}" ]]; then
    _seektalent_offline_fail "opencli_extension_checksum_mismatch" "Expected Browser Bridge SHA256 ${extension_sha256} but found ${actual_extension_sha256}."
    return 1
  fi

  local prefix="${install_root}/python-prefix/${version}"
  local site_packages="${prefix}/site-packages"
  local bin_dir="${install_root}/bin"
  local opencli_install_dir="${install_root}/opencli-runtime/opencli/${opencli_version}"
  local opencli_main="${opencli_install_dir}/node_modules/@jackwener/opencli/dist/src/main.js"
  local opencli_stage_dir="${install_root}/opencli-runtime/opencli/${opencli_version}.stage.$$"
  local opencli_stage_main="${opencli_stage_dir}/node_modules/@jackwener/opencli/dist/src/main.js"
  local opencli_backup_dir="${install_root}/opencli-runtime/opencli/${opencli_version}.previous.$$"
  local extension_install_dir="${install_root}/chrome-extension/opencli"
  local extension_stage_dir="${install_root}/chrome-extension/opencli.stage.$$"
  local extension_backup_dir="${install_root}/chrome-extension/opencli.previous.$$"
  local extension_manifest="${extension_stage_dir}/manifest.json"
  local installed_bridge_manifest="${install_root}/browser-bridge/bridge-manifest.json"
  local bridge_manifest_backup="${install_root}/browser-bridge/bridge-manifest.previous.$$"

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

  rm -rf "${opencli_stage_dir}" "${opencli_backup_dir}"
  mkdir -p "${opencli_stage_dir}" || return 1
  unzip -q "${opencli_archive}" -d "${opencli_stage_dir}" || {
    rm -rf "${opencli_stage_dir}"
    _seektalent_offline_fail "opencli_offline_extract_failed" "Failed to extract the bundled WTSCLI runtime."
    return 1
  }
  if [[ ! -f "${opencli_stage_main}" ]]; then
    rm -rf "${opencli_stage_dir}"
    _seektalent_offline_fail "opencli_offline_install_incomplete" "The bundled WTSCLI runtime did not contain the expected entrypoint."
    return 1
  fi

  rm -rf "${extension_stage_dir}"
  mkdir -p "${extension_stage_dir}" || {
    rm -rf "${opencli_stage_dir}"
    return 1
  }
  if ! unzip -q "${extension_archive}" -d "${extension_stage_dir}"; then
    rm -rf "${opencli_stage_dir}" "${extension_stage_dir}"
    _seektalent_offline_fail "opencli_extension_extract_failed" "Failed to extract the bundled Browser Bridge extension."
    return 1
  fi
  if [[ ! -f "${extension_manifest}" ]]; then
    rm -rf "${opencli_stage_dir}" "${extension_stage_dir}"
    _seektalent_offline_fail "opencli_extension_manifest_missing" "The bundled Browser Bridge extension did not contain manifest.json."
    return 1
  fi

  local installed_extension_version
  installed_extension_version="$(_seektalent_json_value "${domi_python}" "${extension_manifest}" "version")" || {
    rm -rf "${opencli_stage_dir}" "${extension_stage_dir}"
    return 1
  }
  if [[ "${installed_extension_version}" != "${extension_version}" ]]; then
    rm -rf "${opencli_stage_dir}" "${extension_stage_dir}"
    _seektalent_offline_fail "opencli_extension_version_mismatch" "Expected Browser Bridge ${extension_version} but found ${installed_extension_version}."
    return 1
  fi

  if ! _seektalent_verify_browser_bridge \
    "${domi_python}" "${bridge_manifest_source}" "${opencli_stage_dir}" "${extension_stage_dir}"; then
    rm -rf "${opencli_stage_dir}" "${extension_stage_dir}"
    _seektalent_offline_fail "browser_bridge_pair_mismatch" "The bundled runtime and extension did not match their bridge manifest."
    return 1
  fi
  if [[ "$("${domi_node}" "${opencli_stage_main}" --version)" != "${opencli_version}" ]]; then
    rm -rf "${opencli_stage_dir}" "${extension_stage_dir}"
    _seektalent_offline_fail "opencli_offline_probe_failed" "The staged WTSCLI runtime failed its version probe."
    return 1
  fi

  rm -rf "${opencli_backup_dir}" "${extension_backup_dir}"
  rm -f "${bridge_manifest_backup}"
  mkdir -p "$(dirname "${installed_bridge_manifest}")" || return 1
  if [[ -d "${opencli_install_dir}" ]] && ! mv "${opencli_install_dir}" "${opencli_backup_dir}"; then
    return 1
  fi
  if [[ -d "${extension_install_dir}" ]] && ! mv "${extension_install_dir}" "${extension_backup_dir}"; then
    [[ ! -d "${opencli_backup_dir}" ]] || mv "${opencli_backup_dir}" "${opencli_install_dir}"
    return 1
  fi
  if [[ -f "${installed_bridge_manifest}" ]] && ! mv "${installed_bridge_manifest}" "${bridge_manifest_backup}"; then
    [[ ! -d "${extension_backup_dir}" ]] || mv "${extension_backup_dir}" "${extension_install_dir}"
    [[ ! -d "${opencli_backup_dir}" ]] || mv "${opencli_backup_dir}" "${opencli_install_dir}"
    return 1
  fi

  if ! mv "${opencli_stage_dir}" "${opencli_install_dir}" \
    || ! mv "${extension_stage_dir}" "${extension_install_dir}" \
    || ! cp "${bridge_manifest_source}" "${installed_bridge_manifest}"; then
    rm -rf "${opencli_install_dir}" "${extension_install_dir}"
    rm -f "${installed_bridge_manifest}"
    [[ ! -d "${opencli_backup_dir}" ]] || mv "${opencli_backup_dir}" "${opencli_install_dir}"
    [[ ! -d "${extension_backup_dir}" ]] || mv "${extension_backup_dir}" "${extension_install_dir}"
    [[ ! -f "${bridge_manifest_backup}" ]] || mv "${bridge_manifest_backup}" "${installed_bridge_manifest}"
    _seektalent_offline_fail "browser_bridge_switch_failed" "Failed to switch the verified browser bridge pair."
    return 1
  fi
  rm -rf "${opencli_backup_dir}" "${extension_backup_dir}"
  rm -f "${bridge_manifest_backup}"

  PYTHONPATH="${site_packages}${PYTHONPATH:+:${PYTHONPATH}}" \
    "${domi_python}" -m seektalent.domi_bootstrap \
      --package-version "${version}" \
      --python-path "${site_packages}" \
      --domi-python "${domi_python}" \
      --domi-node "${domi_node}" \
      --bin-dir "${bin_dir}" \
      --print-json || {
        _seektalent_offline_fail "seektalent_domi_bootstrap_failed" "Failed to generate the SeekTalent command shim."
        return 1
      }

  case ":${PATH}:" in
    *":${bin_dir}:"*) ;;
    *) export PATH="${bin_dir}:${PATH}" ;;
  esac

  local installed_version installed_opencli_version
  installed_version="$("${bin_dir}/seektalent" --version)" || return 1
  installed_opencli_version="$("${domi_node}" "${opencli_main}" --version)" || return 1
  if [[ "${installed_version}" != "${version}" ]]; then
    _seektalent_offline_fail "seektalent_offline_version_mismatch" "Expected SeekTalent ${version} but found ${installed_version}."
    return 1
  fi
  if [[ "${installed_opencli_version}" != "${opencli_version}" ]]; then
    _seektalent_offline_fail "opencli_offline_probe_failed" "Expected WTSCLI ${opencli_version} but found ${installed_opencli_version}."
    return 1
  fi

  echo "SeekTalent macOS Intel offline install ready."
  echo "SeekTalent version: ${installed_version}"
  echo "WTSCLI version: ${installed_opencli_version}"
  echo "WTSCLI Browser Bridge version: ${installed_extension_version}"
  echo "WTSCLI Browser Bridge build: ${browser_bridge_build_id}"
  echo "Chrome extension directory: ${extension_install_dir}"
  echo "Chrome setup: open chrome://extensions, enable Developer mode, and choose Load unpacked."
  echo "Run: export SEEKTALENT_DOMI_JWT='<new Domi JWT>'; seektalent workbench"
}

if _seektalent_offline_install; then
  unset -f _seektalent_verify_browser_bridge _seektalent_json_value _seektalent_offline_fail _seektalent_offline_install
  return 0
fi
unset -f _seektalent_verify_browser_bridge _seektalent_json_value _seektalent_offline_fail _seektalent_offline_install
return 1
