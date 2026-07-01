from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "start-domi-workbench.sh"


def test_domi_workbench_start_script_runs_smoke_then_foreground_workbench() -> None:
    script = SCRIPT.read_text(encoding="utf-8")

    smoke_index = script.index("scripts/smoke-domi-runtime.sh")
    exec_index = script.index('exec env -i "${DOMI_ENV[@]}" "${SEEKTALENT_BIN}" workbench')

    assert smoke_index < exec_index
    assert 'SEEKTALENT_DOMI_SMOKE_PORT="${DOMI_WORKBENCH_PORT}"' in script
    assert 'SEEKTALENT_RUNTIME_MODE=prod' in script
    assert 'SEEKTALENT_TEXT_LLM_PROVIDER_LABEL=domi' in script
    assert 'SEEKTALENT_WORKSPACE_ROOT=${HOME}' in script
    assert 'SEEKTALENT_LIEPIN_WORKER_MODE=opencli' in script


def test_domi_workbench_start_script_is_not_a_background_launcher() -> None:
    script = SCRIPT.read_text(encoding="utf-8")

    assert 'exec env -i "${DOMI_ENV[@]}" "${SEEKTALENT_BIN}" workbench' in script
    assert "start_new_session=True" not in script
    assert 'kill -TERM -- "-${WORKBENCH_PID}"' not in script
    assert 'trap cleanup EXIT' not in script


def test_domi_workbench_start_script_passes_bash_syntax_check() -> None:
    result = subprocess.run(
        ["bash", "-n", str(SCRIPT)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
