from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


def _bin_dir(venv_dir: Path) -> Path:
    return venv_dir / ("Scripts" if os.name == "nt" else "bin")


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is required for wheel packaging tests")
def test_built_wheel_runs_outside_repo(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    subprocess.run(["uv", "build"], cwd=repo_root, check=True)
    wheel = max((repo_root / "dist").glob("seektalent-*.whl"))

    venv_dir = tmp_path / "venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)
    bin_dir = _bin_dir(venv_dir)
    python = bin_dir / ("python.exe" if os.name == "nt" else "python")
    cli = bin_dir / ("seektalent.exe" if os.name == "nt" else "seektalent")

    subprocess.run([str(python), "-m", "pip", "install", str(wheel)], check=True)

    env = os.environ.copy()
    env.pop("PYTHONPATH", None)

    work_dir = tmp_path / "work"
    work_dir.mkdir()

    help_result = subprocess.run(
        [str(cli), "--help"],
        cwd=work_dir,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "Human entry" in help_result.stdout
    assert "Agent entry" in help_result.stdout

    version_result = subprocess.run(
        [str(cli), "version"],
        cwd=work_dir,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert version_result.stdout.strip() == "0.3.3"

    inspect_result = subprocess.run(
        [str(cli), "inspect", "--json"],
        cwd=work_dir,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    inspect_payload = json.loads(inspect_result.stdout)
    assert inspect_payload["phase"] == "v0.3.3_active"

    init_result = subprocess.run(
        [str(cli), "init"],
        cwd=work_dir,
        env=env,
        capture_output=True,
        text=True,
    )
    assert init_result.returncode == 1
    assert "repo_env_template_not_found" in init_result.stderr
    assert not (work_dir / ".env").exists()

    doctor_env = work_dir / "doctor.env"
    doctor_env.write_text(
        "OPENAI_API_KEY=test-openai-key\nSEEKTALENT_MOCK_CTS=true\n",
        encoding="utf-8",
    )
    doctor_result = subprocess.run(
        [str(cli), "doctor", "--env-file", str(doctor_env), "--json"],
        cwd=work_dir,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    doctor_payload = json.loads(doctor_result.stdout)
    assert doctor_payload["ok"] is True
