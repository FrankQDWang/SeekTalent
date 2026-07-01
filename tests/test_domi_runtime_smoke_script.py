from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "smoke-domi-runtime.sh"


def test_domi_runtime_smoke_script_has_expected_contract() -> None:
    script = SCRIPT.read_text(encoding="utf-8")

    expected_strings = [
        "/Applications/Domi.app/Contents/Resources/extraResources/python/runtime/bin/python",
        ".seektalent/domi-runtime",
        "SEEKTALENT_DOMI_JWT",
        "SEEKTALENT_TEXT_LLM_PROVIDER_LABEL=domi",
        "SEEKTALENT_DOMI_LLM_BASE_URL",
        "SEEKTALENT_DOMI_LLM_CHANNEL",
        "test-api-agent.hewa.cn",
        "seektalent doctor",
        "workbench --port",
        "seektalent-opencli",
    ]

    for expected in expected_strings:
        assert expected in script


def test_domi_runtime_smoke_script_has_process_cleanup_contract() -> None:
    script = SCRIPT.read_text(encoding="utf-8")

    assert "start_new_session=True" in script
    assert 'kill -TERM -- "-${WORKBENCH_PID}"' in script
    assert 'kill -KILL -- "-${WORKBENCH_PID}"' in script


def test_domi_runtime_smoke_script_rejects_app_bundle_runtime_root() -> None:
    script = SCRIPT.read_text(encoding="utf-8")

    assert 'case "${DOMI_RUNTIME_ROOT}" in' in script
    assert "/Applications/Domi.app" in script
    assert "domi_runtime_root_forbidden" in script


def test_domi_runtime_smoke_script_does_not_restart_opencli_by_default() -> None:
    script = SCRIPT.read_text(encoding="utf-8")

    status_index = script.index('"${SEEKTALENT_OPENCLI_BIN}" daemon status')
    restart_index = script.index('"${SEEKTALENT_OPENCLI_BIN}" daemon restart')

    assert "SEEKTALENT_DOMI_OPENCLI_RESTART" in script
    assert status_index < restart_index


def test_domi_runtime_smoke_script_isolates_stale_ambient_env() -> None:
    script = SCRIPT.read_text(encoding="utf-8")

    assert "SETUP_ENV=(" in script
    assert "DOMI_ENV=(" in script
    assert "OPENCLI_ENV=(" in script
    assert "SEEKTALENT_PROVIDER_NAME=liepin" in script
    assert 'env -i "${SETUP_ENV[@]}" "${VENV_PYTHON}" -m pip install' in script
    assert 'env -i "${SETUP_ENV[@]}" "${VENV_PYTHON}" -m build' in script
    assert 'env -i "${DOMI_ENV[@]}" "${SEEKTALENT_BIN}" doctor' in script
    assert 'env -i "${DOMI_ENV[@]}" "${VENV_PYTHON}" -' in script
    assert 'env -i "${OPENCLI_ENV[@]}" "${SEEKTALENT_OPENCLI_BIN}" daemon status' in script
    assert 'env -i "${OPENCLI_ENV[@]}" "${SEEKTALENT_OPENCLI_BIN}" daemon restart' in script
    assert "export SEEKTALENT_DOMI_JWT" not in script


def test_domi_runtime_smoke_script_passes_bash_syntax_check() -> None:
    result = subprocess.run(
        ["bash", "-n", str(SCRIPT)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
