from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    ("module", "specific_option"),
    [
        ("experiments.openclaw_baseline.run", "--gateway-base-url"),
        ("experiments.claude_code_baseline.run", "--timeout-seconds"),
        ("experiments.jd_text_baseline.run", "--notes-file"),
    ],
)
def test_experiment_run_module_help_smoke(module: str, specific_option: str) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    pythonpath = [str(repo_root), str(repo_root / "src")]
    if env.get("PYTHONPATH"):
        pythonpath.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(pythonpath)

    result = subprocess.run(
        [sys.executable, "-m", module, "--help"],
        cwd=repo_root,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    assert f"python -m {module}" in result.stdout
    for option in ("--job-title", "--jd", "--env-file", "--output-dir", "--json", specific_option):
        assert option in result.stdout
