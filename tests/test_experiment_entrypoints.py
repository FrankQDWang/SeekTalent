from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from seektalent.runtime import WorkflowRuntime
from seektalent.tracing import RunTracer
from tests.settings_factory import make_settings
from tests.test_runtime_state_flow import _install_broaden_stubs, _python_feedback_seed_scorecards, _sample_inputs
from tools.run_global_benchmark import build_policy_comparison_env, build_policy_comparison_settings


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


def test_build_policy_comparison_env_primary_disables_company_isolation_flags() -> None:
    env = build_policy_comparison_env(mode="primary")

    assert env["SEEKTALENT_TARGET_COMPANY_ENABLED"] == "false"
    assert env["SEEKTALENT_COMPANY_DISCOVERY_ENABLED"] == "false"


def test_active_runtime_source_has_no_company_discovery_references() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    orchestrator_source = (repo_root / "src" / "seektalent" / "runtime" / "orchestrator.py").read_text(
        encoding="utf-8"
    )

    assert "CompanyDiscoveryService" not in orchestrator_source
    assert "web_company_discovery" not in orchestrator_source
    assert "target_company_enabled" not in orchestrator_source
