from __future__ import annotations

import json
import os
import site
import subprocess
import sys
import zipfile
from pathlib import Path

from seektalent.resources import REQUIRED_PROMPTS


def _bin_dir(venv_dir: Path) -> Path:
    return venv_dir / ("Scripts" if os.name == "nt" else "bin")


def test_built_wheel_runs_outside_repo(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    subprocess.run(["uv", "build"], cwd=repo_root, check=True)
    wheel = max((repo_root / "dist").glob("seektalent-*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        archive_names = set(archive.namelist())
    for name in REQUIRED_PROMPTS:
        assert f"seektalent/prompts/{name}.md" in archive_names

    venv_dir = tmp_path / "venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)
    bin_dir = _bin_dir(venv_dir)
    python = bin_dir / ("python.exe" if os.name == "nt" else "python")
    cli = bin_dir / ("seektalent.exe" if os.name == "nt" else "seektalent")

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
    assert "seektalent" in help_result.stdout
    assert "update" in help_result.stdout
    assert "inspect" in help_result.stdout
    assert "SEEKTALENT_TEXT_LLM_API_KEY" in help_result.stdout

    version_result = subprocess.run(
        [str(cli), "version"],
        cwd=work_dir,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert version_result.stdout.strip()

    update_result = subprocess.run(
        [str(cli), "update"],
        cwd=work_dir,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "pip install -U seektalent" in update_result.stdout
    assert "pipx upgrade seektalent" in update_result.stdout

    inspect_result = subprocess.run(
        [str(cli), "inspect", "--json"],
        cwd=work_dir,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    inspect_payload = json.loads(inspect_result.stdout)
    assert inspect_payload["tool"] == "seektalent"
    assert "inspect" in inspect_payload["commands"]
    assert inspect_payload["environment"]["required_for_default_run"] == [
        "SEEKTALENT_TEXT_LLM_API_KEY",
        "SEEKTALENT_CTS_TENANT_KEY",
        "SEEKTALENT_CTS_TENANT_SECRET",
    ]

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
    doctor_env.write_text(
        "SEEKTALENT_TEXT_LLM_API_KEY=test-key\nSEEKTALENT_CTS_TENANT_KEY=cts-key\nSEEKTALENT_CTS_TENANT_SECRET=cts-secret\n",
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
    payload = json.loads(doctor_result.stdout)
    assert payload["ok"] is True
