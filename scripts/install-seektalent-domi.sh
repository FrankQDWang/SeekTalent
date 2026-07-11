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
  local version="${1:-0.7.39}"
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

  local prefix="${HOME}/.seektalent/python-prefix/${version}"
  local site_packages="${prefix}/site-packages"
  local bin_dir="${HOME}/.seektalent/bin"
  mkdir -p "${site_packages}" "${bin_dir}" || {
    _seektalent_domi_fail "seektalent_bootstrap_directory_failed" "Failed to create ${site_packages} or ${bin_dir}."
    return 1
  }

  "${domi_python}" -m pip install --upgrade --ignore-installed --no-cache-dir --target "${site_packages}" "seektalent==${version}" || {
    _seektalent_domi_fail "seektalent_pypi_install_failed" "Failed to install seektalent==${version} with Domi Python."
    return 1
  }

  PYTHONPATH="${site_packages}${PYTHONPATH:+:${PYTHONPATH}}" \
    "${domi_python}" -m seektalent.domi_bootstrap \
      --package-version "${version}" \
      --python-path "${site_packages}" \
      --domi-python "${domi_python}" \
      --domi-node "${domi_node}" \
      --bin-dir "${bin_dir}" \
      --print-json || {
        _seektalent_domi_fail "seektalent_domi_bootstrap_failed" "Failed to prepare the seektalent command shim."
        return 1
      }

  case ":${PATH}:" in
    *":${bin_dir}:"*) ;;
    *) export PATH="${bin_dir}:${PATH}" ;;
  esac

  echo "SeekTalent Domi install ready. Run: seektalent workbench"
}

if _seektalent_domi_install "$@"; then
  unset -f _seektalent_domi_fail _seektalent_domi_install
  return 0
fi
unset -f _seektalent_domi_fail _seektalent_domi_install
return 1
