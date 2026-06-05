from __future__ import annotations

import textwrap
from pathlib import Path

from tools.check_source_boundaries import collect_source_boundary_failures


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).strip() + "\n", encoding="utf-8")


def test_runtime_provider_import_is_reported(tmp_path: Path) -> None:
    _write(
        tmp_path / "src/seektalent/runtime/orchestrator.py",
        """
        from seektalent.providers import registry
        """,
    )

    failures = collect_source_boundary_failures(tmp_path)

    assert failures == [
        "src/seektalent/runtime/orchestrator.py:1: runtime must not import seektalent.providers"
    ]


def test_provider_runtime_import_is_reported(tmp_path: Path) -> None:
    _write(
        tmp_path / "src/seektalent/providers/liepin/runtime_lane.py",
        """
        import seektalent.runtime.source_lanes
        """,
    )

    failures = collect_source_boundary_failures(tmp_path)

    assert failures == [
        "src/seektalent/providers/liepin/runtime_lane.py:1: providers must not import seektalent.runtime"
    ]


def test_runtime_opencli_and_literal_source_kind_are_reported(tmp_path: Path) -> None:
    _write(
        tmp_path / "src/seektalent/runtime/source_lanes.py",
        """
        from typing import Literal

        SourceKind = Literal["cts", "liepin"]
        REASON = "liepin_opencli_filter_unavailable"
        """,
    )

    failures = collect_source_boundary_failures(tmp_path)

    assert failures == [
        "src/seektalent/runtime/source_lanes.py:3: runtime must not define CTS/Liepin-only SourceKind",
        "src/seektalent/runtime/source_lanes.py:4: runtime must not contain OpenCLI/Liepin reason codes",
    ]


def test_tach_runtime_provider_dual_dependency_is_reported(tmp_path: Path) -> None:
    _write(
        tmp_path / "tach.toml",
        """
        [[modules]]
        path = "seektalent.providers"
        depends_on = ["seektalent.runtime"]

        [[modules]]
        path = "seektalent.runtime"
        depends_on = ["seektalent.providers"]
        """,
    )

    failures = collect_source_boundary_failures(tmp_path)

    assert failures == [
        "tach.toml: seektalent.providers must not depend on seektalent.runtime",
        "tach.toml: seektalent.runtime must not depend on seektalent.providers",
    ]


def test_source_neutral_boundaries_pass(tmp_path: Path) -> None:
    _write(
        tmp_path / "src/seektalent/runtime/orchestrator.py",
        """
        from seektalent.sources.registry import SourceRegistry
        """,
    )
    _write(
        tmp_path / "src/seektalent/providers/fixture/source.py",
        """
        from seektalent.sources.contracts import SourceLaneRequest
        """,
    )
    _write(
        tmp_path / "tach.toml",
        """
        [[modules]]
        path = "seektalent.providers"
        depends_on = ["seektalent.sources"]

        [[modules]]
        path = "seektalent.runtime"
        depends_on = ["seektalent.sources"]
        """,
    )

    assert collect_source_boundary_failures(tmp_path) == []
