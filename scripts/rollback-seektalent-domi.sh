#!/usr/bin/env bash

set -u
umask 077

backup_dir="${1:-}"
install_home="${SEEKTALENT_INSTALL_HOME:-${HOME}}"
rollback_root="${install_home}/.seektalent-rollbacks"
case "${backup_dir}" in
  "${rollback_root}"/*) ;;
  *)
    echo "reason_code=rollback_snapshot_invalid" >&2
    exit 1
    ;;
esac
if [[ ! -d "${backup_dir}" || -L "${backup_dir}" || ! -f "${backup_dir}/.available" ]]; then
  echo "reason_code=rollback_snapshot_unavailable" >&2
  exit 1
fi

quarantine_root="${install_home}/.seektalent-failed-installs"
if [[ -L "${quarantine_root}" ]] || ! mkdir -p "${quarantine_root}"; then
  echo "reason_code=rollback_quarantine_unavailable" >&2
  exit 1
fi
quarantine_dir="$(mktemp -d "${quarantine_root}/restore.XXXXXX")" || exit 1
current="${install_home}/.seektalent"
current_moved=0
if [[ -e "${current}" || -L "${current}" ]]; then
  if ! mv "${current}" "${quarantine_dir}/seektalent"; then
    echo "reason_code=rollback_quarantine_failed" >&2
    exit 1
  fi
  current_moved=1
fi
if [[ -d "${backup_dir}/seektalent" ]]; then
  if ! mv "${backup_dir}/seektalent" "${current}"; then
    if [[ "${current_moved}" -eq 1 ]]; then
      mv "${quarantine_dir}/seektalent" "${current}" || {
        echo "reason_code=rollback_restore_and_recovery_failed" >&2
        exit 1
      }
    fi
    echo "reason_code=rollback_restore_failed" >&2
    exit 1
  fi
fi
mv "${backup_dir}/.available" "${backup_dir}/.used"
echo "SeekTalent rollback restored from: ${backup_dir}"
