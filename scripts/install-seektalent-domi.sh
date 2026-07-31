#!/usr/bin/env bash

if ! (return 0 2>/dev/null); then
  echo "reason_code=domi_bootstrap_shell_not_sourced source this script so it can update PATH for the current shell." >&2
  echo "Run the release install command with source, then run: seektalent workbench" >&2
  exit 1
fi

_seektalent_domi_fail() {
  local reason_code="$1"
  local message="$2"
  echo "reason_code=${reason_code} ${message}" >&2
  return 1
}

_seektalent_domi_install() {
  local version="${1:-0.8.0rc1}"
  local requested_bundle_dir="${2:-${SEEKTALENT_WTSCLI_BUNDLE_DIR:-}}"
  local domi_python="${DOMI_PYTHON:-}"
  local domi_node="${DOMI_NODE:-${SEEKTALENT_DOMI_NODE:-}}"
  local install_home="${SEEKTALENT_INSTALL_HOME:-${HOME}}"
  local script_path="${BASH_SOURCE[0]}"
  local script_dir="${script_path%/*}"
  if [[ "${script_dir}" == "${script_path}" ]]; then
    script_dir="."
  fi
  local wtscli_bundle_dir="${requested_bundle_dir:-${script_dir}/wtscli-browser-bridge}"
  local prepared_runtime_archive="${SEEKTALENT_WTSCLI_PREPARED_RUNTIME:-${script_dir}/wtscli-runtime.zip}"
  local admission_helper="${SEEKTALENT_BROWSER_BRIDGE_HELPER:-${script_dir}/install_staging_browser_bridge.py}"
  local delivery_manifest="${SEEKTALENT_DELIVERY_MANIFEST:-${script_dir}/delivery-manifest.json}"
  local product_wheels=("${script_dir}"/seektalent-*.whl)
  local product_wheel=""
  if [[ "${#product_wheels[@]}" -eq 1 && -f "${product_wheels[0]}" ]]; then
    product_wheel="${product_wheels[0]}"
  fi

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
  if [[ ! -x "${domi_python}" ]]; then
    _seektalent_domi_fail "domi_python_missing" "Domi Python was not found: ${domi_python}"
    return 1
  fi

  if [[ -z "${domi_node}" ]]; then
    local candidate
    for candidate in \
      "/Applications/Domi.app/Contents/Resources/extraResources/node/runtime/bin/node" \
      "/Applications/Domi.app/Contents/Resources/extraResources/node/bin/node" \
      "/Applications/Domi.app/Contents/Resources/extraResources/node/node" \
      "${HOME}/Library/Application Support/Domi/runtime/node/node" \
      "${HOME}/Library/Application Support/Domi/runtime/node/bin/node" \
      "${HOME}/.domi/runtime/node/node" \
      "${HOME}/.domi/runtime/node/bin/node"; do
      if [[ -x "${candidate}" ]]; then
        domi_node="${candidate}"
        break
      fi
    done
  fi
  if [[ -z "${domi_node}" || ! -x "${domi_node}" ]]; then
    _seektalent_domi_fail "domi_node_missing" "Domi Node was not found. Set DOMI_NODE or SEEKTALENT_DOMI_NODE to the Domi node executable path."
    return 1
  fi
  if [[ ! -f "${wtscli_bundle_dir}/bridge-manifest.json" ]]; then
    _seektalent_domi_fail "wtscli_bundle_missing" "The exact WTSCLI bundle was not found in the SeekTalent product package: ${wtscli_bundle_dir}"
    return 1
  fi
  if [[ ! -f "${admission_helper}" ]]; then
    _seektalent_domi_fail "wtscli_bundle_admission_unavailable" "The shared SeekTalent browser bridge admission helper was not found: ${admission_helper}"
    return 1
  fi
  if ! PYTHONPATH="${product_wheel}${PYTHONPATH:+:${PYTHONPATH}}" "${domi_python}" "${admission_helper}" \
    --bundle-dir "${wtscli_bundle_dir}" \
    --verify-only >/dev/null; then
    _seektalent_domi_fail "wtscli_bundle_invalid" "The exact SeekTalent WTSCLI bundle failed strict admission."
    return 1
  fi
  if [[ ! -f "${prepared_runtime_archive}" ]]; then
    _seektalent_domi_fail "wtscli_runtime_missing" "The prepared WTSCLI runtime was not found in the SeekTalent product package: ${prepared_runtime_archive}"
    return 1
  fi
  local delivery_manifest_args=()
  if [[ -n "${product_wheel}" || -f "${delivery_manifest}" || "${version}" == "0.8.0rc1" ]]; then
    if [[ -z "${product_wheel}" || ! -f "${delivery_manifest}" ]]; then
      _seektalent_domi_fail "delivery_manifest_missing" "The exact SeekTalent wheel and delivery manifest are required."
      return 1
    fi
    if ! "${domi_python}" - "${delivery_manifest}" "${product_wheel}" "${version}" <<'PY'
import hashlib
import json
import pathlib
import sys

manifest_path = pathlib.Path(sys.argv[1])
wheel_path = pathlib.Path(sys.argv[2])
version = sys.argv[3]
payload = json.loads(manifest_path.read_text(encoding="utf-8"))
actual = hashlib.sha256(wheel_path.read_bytes()).hexdigest()
if (
    payload.get("schema_version") != 1
    or payload.get("product_version") != version
    or payload.get("seektalent_wheel") != wheel_path.name
    or payload.get("seektalent_wheel_sha256") != actual
):
    raise SystemExit(1)
PY
    then
      _seektalent_domi_fail "delivery_manifest_identity_mismatch" "The exact SeekTalent wheel does not match the delivery manifest."
      return 1
    fi
    delivery_manifest_args=(--delivery-manifest "${delivery_manifest}")
  fi

  local prefix="${install_home}/.seektalent/python-prefix/${version}"
  local site_packages="${prefix}/site-packages"
  local bin_dir="${install_home}/.seektalent/bin"
  local candidate_root
  candidate_root="$(mktemp -d "${TMPDIR:-/tmp}/seektalent-domi-install.XXXXXX")" || {
    _seektalent_domi_fail "seektalent_bootstrap_directory_failed" "Failed to create the temporary SeekTalent candidate."
    return 1
  }
  local candidate_prefix="${candidate_root}/python-prefix"
  local candidate_site_packages="${candidate_prefix}/site-packages"
  local prepared_runtime_dir="${candidate_root}/wtscli-runtime"
  mkdir -p "${candidate_site_packages}" "${prepared_runtime_dir}" || {
    rm -rf -- "${candidate_root}"
    _seektalent_domi_fail "seektalent_bootstrap_directory_failed" "Failed to create the temporary SeekTalent candidate."
    return 1
  }

  local seektalent_install_source="seektalent==${version}"
  if [[ -n "${product_wheel}" ]]; then
    seektalent_install_source="${product_wheel}"
  fi
  "${domi_python}" -m pip install --upgrade --ignore-installed --no-cache-dir --target "${candidate_site_packages}" "${seektalent_install_source}" || {
    rm -rf -- "${candidate_root}"
    _seektalent_domi_fail "seektalent_pypi_install_failed" "Failed to install seektalent==${version} with Domi Python."
    return 1
  }
  if ! "${domi_python}" -m zipfile -e "${prepared_runtime_archive}" "${prepared_runtime_dir}"; then
    rm -rf -- "${candidate_root}"
    _seektalent_domi_fail "wtscli_runtime_invalid" "The prepared WTSCLI runtime could not be extracted."
    return 1
  fi

  local rollback_root="${install_home}/.seektalent-rollbacks"
  local rollback_dir
  (umask 077 && mkdir -p "${rollback_root}") || {
    rm -rf -- "${candidate_root}"
    _seektalent_domi_fail "rollback_snapshot_failed" "Could not create the private rollback root."
    return 1
  }
  rollback_dir="$(mktemp -d "${rollback_root}/${version}.XXXXXX")" || {
    rm -rf -- "${candidate_root}"
    _seektalent_domi_fail "rollback_snapshot_failed" "Could not create a unique rollback snapshot."
    return 1
  }
  if [[ -d "${install_home}/.seektalent" ]]; then
    cp -a "${install_home}/.seektalent" "${rollback_dir}/seektalent" || {
      rm -rf -- "${candidate_root}"
      _seektalent_domi_fail "rollback_snapshot_failed" "Could not preserve the existing SeekTalent install."
      return 1
    }
  fi
  (umask 077 && : > "${rollback_dir}/.available") || {
    rm -rf -- "${candidate_root}"
    _seektalent_domi_fail "rollback_snapshot_failed" "Could not seal the rollback snapshot."
    return 1
  }

  PYTHONPATH="${candidate_site_packages}${PYTHONPATH:+:${PYTHONPATH}}" \
    "${domi_python}" -m seektalent.domi_bootstrap \
      --package-version "${version}" \
      --home "${install_home}" \
      --python-path "${site_packages}" \
      --python-prefix-candidate "${candidate_prefix}" \
      --python-prefix-target "${prefix}" \
      --domi-python "${domi_python}" \
      --domi-node "${domi_node}" \
      --browser-bridge-bundle-dir "${wtscli_bundle_dir}" \
      --browser-bridge-prepared-runtime-dir "${prepared_runtime_dir}" \
      "${delivery_manifest_args[@]}" \
      --bin-dir "${bin_dir}" \
      --print-json || {
        rm -rf -- "${candidate_root}"
        if [[ -x "${script_dir}/rollback-seektalent-domi.sh" ]]; then
          SEEKTALENT_INSTALL_HOME="${install_home}" \
            "${script_dir}/rollback-seektalent-domi.sh" "${rollback_dir}" >/dev/null
        fi
        _seektalent_domi_fail "seektalent_domi_bootstrap_failed" "Failed to prepare the seektalent command shim."
        return 1
      }
  rm -rf -- "${candidate_root}"

  case ":${PATH}:" in
    *":${bin_dir}:"*) ;;
    *) export PATH="${bin_dir}:${PATH}" ;;
  esac

  echo "SeekTalent Domi install ready. Run: seektalent workbench"
  echo "Chrome 扩展目录：${install_home}/.seektalent/chrome-extension/wtscli"
  echo "打开 chrome://extensions，启用“开发者模式”，选择“加载已解压的扩展程序”，并选择上面的唯一目录。"
  echo "升级后请在该页面点击 WTSCLI 的“重新加载”；若仍显示旧版本，请完全退出并重启 Chrome。"
  echo "检查：seektalent browser-check"
  echo "Rollback snapshot: ${rollback_dir}"
  echo "Rollback command: SEEKTALENT_INSTALL_HOME=\"${install_home}\" \"${script_dir}/rollback-seektalent-domi.sh\" \"${rollback_dir}\""
  return 0
}

if _seektalent_domi_install "$@"; then
  unset -f _seektalent_domi_fail _seektalent_domi_install
  return 0
fi
unset -f _seektalent_domi_fail _seektalent_domi_install
return 1
