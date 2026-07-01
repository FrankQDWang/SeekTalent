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


def test_domi_runtime_smoke_script_passes_bash_syntax_check() -> None:
    result = subprocess.run(
        ["bash", "-n", str(SCRIPT)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
