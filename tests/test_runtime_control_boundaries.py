from __future__ import annotations

import textwrap
from pathlib import Path

from tools.check_source_boundaries import collect_source_boundary_failures


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).strip() + "\n", encoding="utf-8")


def test_runtime_control_service_provider_import_is_reported(tmp_path: Path) -> None:
    _write(
        tmp_path / "src/seektalent_runtime_control/service.py",
        """
        from seektalent.providers import get_provider_adapter
        """,
    )

    failures = collect_source_boundary_failures(tmp_path)

    assert failures == [
        "src/seektalent_runtime_control/service.py:1: runtime-control service modules must not import seektalent.providers"
    ]


def test_runtime_control_only_executor_adapter_may_import_workflow_runtime(tmp_path: Path) -> None:
    _write(
        tmp_path / "src/seektalent_runtime_control/service.py",
        """
        from seektalent.runtime.orchestrator import WorkflowRuntime
        """,
    )
    _write(
        tmp_path / "src/seektalent_runtime_control/executor.py",
        """
        from seektalent.runtime.orchestrator import WorkflowRuntime
        """,
    )

    failures = collect_source_boundary_failures(tmp_path)

    assert failures == [
        "src/seektalent_runtime_control/service.py:1: only runtime-control executor adapter may import WorkflowRuntime"
    ]


def test_runtime_control_service_source_adapter_import_is_reported(tmp_path: Path) -> None:
    _write(
        tmp_path / "src/seektalent_runtime_control/requirements.py",
        """
        from seektalent.source_adapters import build_source_enabled_runtime
        """,
    )

    failures = collect_source_boundary_failures(tmp_path)

    assert failures == [
        "src/seektalent_runtime_control/requirements.py:1: only runtime-control executor adapter may import source adapters"
    ]
