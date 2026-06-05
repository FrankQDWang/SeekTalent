from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.settings_factory import make_settings
from tools.run_global_benchmark import (
    build_policy_comparison_env,
    build_policy_comparison_settings,
    effective_policy_comparison_mode,
)


TEXT_READER_ENTRYPOINTS = (
    Path("src/seektalent/cli.py"),
    Path("experiments/openclaw_baseline/run.py"),
    Path("experiments/claude_code_baseline/run.py"),
    Path("experiments/jd_text_baseline/run.py"),
)


def test_inline_or_file_text_readers_have_single_home() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    helper_path = repo_root / "src" / "seektalent" / "text_inputs.py"

    assert helper_path.exists()
    helper_source = helper_path.read_text(encoding="utf-8")
    assert "def read_required_inline_or_file_text" in helper_source
    assert "def read_optional_inline_or_file_text" in helper_source

    for relative_path in TEXT_READER_ENTRYPOINTS:
        source = (repo_root / relative_path).read_text(encoding="utf-8")
        assert "def _read_text" not in source
        assert "def _read_optional_text" not in source
        assert "from seektalent.text_inputs import" in source


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


def test_build_policy_comparison_env_primary_no_longer_emits_removed_company_flags() -> None:
    env = build_policy_comparison_env(mode="primary")

    assert env == {}


def test_build_policy_comparison_settings_primary_preserves_current_defaults() -> None:
    settings = make_settings()
    compared = build_policy_comparison_settings(base_settings=settings, mode="primary")

    assert compared.model_dump() == settings.model_dump()


def test_effective_policy_comparison_mode_normalizes_removed_primary_behavior() -> None:
    assert effective_policy_comparison_mode(mode="none") == "none"
    assert effective_policy_comparison_mode(mode="primary") == "none"


def test_active_runtime_source_has_no_company_discovery_references() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    orchestrator_source = (repo_root / "src" / "seektalent" / "runtime" / "orchestrator.py").read_text(
        encoding="utf-8"
    )

    assert "CompanyDiscoveryService" not in orchestrator_source
    assert "web_company_discovery" not in orchestrator_source
    assert "target_company_enabled" not in orchestrator_source
