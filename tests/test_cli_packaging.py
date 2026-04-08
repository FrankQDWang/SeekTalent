from __future__ import annotations

import json
import os
import shutil
import site
import subprocess
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
    subprocess.run(["python3", "-m", "venv", str(venv_dir)], check=True)
    bin_dir = _bin_dir(venv_dir)
    python = bin_dir / ("python.exe" if os.name == "nt" else "python")
    cli = bin_dir / ("seektalent.exe" if os.name == "nt" else "seektalent")
    ui_cli = bin_dir / ("seektalent-ui-api.exe" if os.name == "nt" else "seektalent-ui-api")

    subprocess.run([str(python), "-m", "pip", "install", "--no-deps", str(wheel)], check=True)

    current_site_packages = site.getsitepackages()
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(current_site_packages)

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
    assert "Phase 1 status" in help_result.stdout
    assert not ui_cli.exists()

    version_result = subprocess.run(
        [str(cli), "version"],
        cwd=work_dir,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert version_result.stdout.strip() == "0.3.0a1"

    inspect_result = subprocess.run(
        [str(cli), "inspect", "--json"],
        cwd=work_dir,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    inspect_payload = json.loads(inspect_result.stdout)
    assert inspect_payload["phase"] == "phase1"

    subprocess.run(
        [str(cli), "init"],
        cwd=work_dir,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert (work_dir / ".env").exists()

    doctor_env = work_dir / "doctor.env"
    doctor_env.write_text("SEEKTALENT_MOCK_CTS=true\n", encoding="utf-8")
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

    run_result = subprocess.run(
        [str(cli), "run", "--jd", "Python agent engineer", "--json"],
        cwd=work_dir,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert run_result.returncode == 1
    error_payload = json.loads(run_result.stderr)
    assert error_payload["error_type"] == "Phase1RuntimeGateError"
