#!/usr/bin/env bash

if ! (return 0 2>/dev/null); then
  echo "reason_code=domi_bootstrap_shell_not_sourced source this script so it can update PATH for the current shell." >&2
  echo "Run the release install command with source, then run the delivered start-seektalent-domi.sh script." >&2
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
  local domi_python="${SEEKTALENT_DOMI_PYTHON:-${DOMI_PYTHON:-}}"
  local domi_node="${DOMI_NODE:-${SEEKTALENT_DOMI_NODE:-}}"
  local install_home="${SEEKTALENT_INSTALL_HOME:-${HOME}}"
  local script_path=""
  if [[ -n "${BASH_SOURCE[0]:-}" ]]; then
    script_path="${BASH_SOURCE[0]}"
  elif [[ -n "${ZSH_VERSION:-}" ]]; then
    script_path="${(%):-%x}"
  else
    _seektalent_domi_fail \
      "domi_bootstrap_shell_unsupported" \
      "The install script must be sourced from bash or zsh."
    return 1
  fi
  local script_dir="${script_path%/*}"
  if [[ "${script_dir}" == "${script_path}" ]]; then
    script_dir="."
  fi
  local wtscli_bundle_dir="${requested_bundle_dir:-${script_dir}/wtscli-browser-bridge}"
  local prepared_runtime_archive="${SEEKTALENT_WTSCLI_PREPARED_RUNTIME:-${script_dir}/wtscli-runtime.zip}"
  local admission_helper="${SEEKTALENT_BROWSER_BRIDGE_HELPER:-${script_dir}/install_staging_browser_bridge.py}"
  local delivery_manifest="${SEEKTALENT_DELIVERY_MANIFEST:-${script_dir}/delivery-manifest.json}"
  local runtime_verifier="${script_dir}/verify_domi_host_runtime.py"
  local wheelhouse="${script_dir}/python-wheelhouse"
  local product_wheel=""
  local product_wheel_count
  product_wheel_count="$(
    find "${script_dir}" -maxdepth 1 -type f -name 'seektalent-*.whl' \
      -print | awk 'END { print NR + 0 }'
  )"
  if [[ "${product_wheel_count}" -eq 1 ]]; then
    product_wheel="$(
      find "${script_dir}" -maxdepth 1 -type f -name 'seektalent-*.whl' \
        -print
    )"
  elif [[ "${product_wheel_count}" -gt 1 ]]; then
    _seektalent_domi_fail \
      "seektalent_wheel_ambiguous" \
      "The delivery directory must contain exactly one SeekTalent wheel."
    return 1
  fi

 if [[ ! -x "${domi_python}" ]]; then
    _seektalent_domi_fail "domi_python_missing" "Set DOMI_PYTHON or SEEKTALENT_DOMI_PYTHON to the Domi-provided Python executable: ${domi_python}"
   return 1
 fi

 if [[ -z "${domi_node}" || ! -x "${domi_node}" ]]; then
    _seektalent_domi_fail "domi_node_missing" "Set DOMI_NODE or SEEKTALENT_DOMI_NODE to the Domi-provided Node executable."
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
  if [[ -z "${product_wheel}" || ! -f "${delivery_manifest}" || ! -f "${runtime_verifier}" || ! -d "${wheelhouse}" ]]; then
    _seektalent_domi_fail "delivery_manifest_missing" "The exact manifest, verifier, wheel, and offline wheelhouse are required."
    return 1
  fi
  if ! "${domi_python}" "${runtime_verifier}" validate-delivery \
    --node "${domi_node}" --manifest "${delivery_manifest}"; then
    _seektalent_domi_fail "delivery_preflight_failed" "The delivery payload or Domi runtime failed exact validation."
    return 1
  fi
  local delivery_manifest_args=(--delivery-manifest "${delivery_manifest}")

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

  "${domi_python}" -m pip install --no-index --find-links "${wheelhouse}" \
    --upgrade --ignore-installed --no-cache-dir --target "${candidate_site_packages}" \
    "${product_wheel}" || {
    rm -rf -- "${candidate_root}"
    _seektalent_domi_fail "seektalent_offline_install_failed" "Failed to install the exact SeekTalent wheel from its offline wheelhouse."
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
      --product-wheel "${product_wheel}" \
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

  echo "SeekTalent Domi install ready. Start with: ${script_dir}/start-seektalent-domi.sh"
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
