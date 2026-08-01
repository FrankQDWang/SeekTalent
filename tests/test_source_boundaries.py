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


def test_runtime_concrete_source_import_is_reported(tmp_path: Path) -> None:
    _write(
        tmp_path / "src/seektalent/runtime/orchestrator.py",
        """
        from seektalent.sources.liepin.runtime_lane import run_liepin_source_lane
        from seektalent.sources.cts.filter_projection import project_constraints_to_cts
        """,
    )

    failures = collect_source_boundary_failures(tmp_path)

    assert failures == [
        "src/seektalent/runtime/orchestrator.py:1: runtime must not import concrete source implementation",
        "src/seektalent/runtime/orchestrator.py:2: runtime must not import concrete source implementation",
    ]


def test_runtime_source_adapter_import_is_reported(tmp_path: Path) -> None:
    _write(
        tmp_path / "src/seektalent/runtime/orchestrator.py",
        """
        from seektalent.source_adapters.liepin.adapter import build_liepin_source
        """,
    )

    failures = collect_source_boundary_failures(tmp_path)

    assert failures == [
        "src/seektalent/runtime/orchestrator.py:1: runtime must not import concrete source adapter"
    ]


def test_runtime_source_membership_whitelist_is_reported(tmp_path: Path) -> None:
    _write(
        tmp_path / "src/seektalent/runtime/source_lanes.py",
        """
        def validate_source(source: str, provider_name: str) -> None:
            if source not in {"cts", "liepin"}:
                raise ValueError(source)
            if provider_name != "liepin":
                return
        """,
    )

    failures = collect_source_boundary_failures(tmp_path)

    assert failures == [
        "src/seektalent/runtime/source_lanes.py:2: runtime must not compare against concrete source ids",
        "src/seektalent/runtime/source_lanes.py:4: runtime must not compare against concrete source ids",
    ]


def test_runtime_concrete_source_dispatch_map_is_reported(tmp_path: Path) -> None:
    _write(
        tmp_path / "src/seektalent/runtime/orchestrator.py",
        """
        def run_source_lane() -> None:
            pass

        _SOURCE_LANE_REQUEST_RUNNERS = {"liepin": run_source_lane}

        def choose(source_plan_by_source: dict[str, object]) -> object:
            return source_plan_by_source["cts"]
        """,
    )

    failures = collect_source_boundary_failures(tmp_path)

    assert failures == [
        "src/seektalent/runtime/orchestrator.py:4: runtime must not dispatch through concrete source id maps",
        "src/seektalent/runtime/orchestrator.py:7: runtime must not index source plans by concrete source id",
    ]


def test_runtime_match_case_and_get_concrete_source_are_reported(tmp_path: Path) -> None:
    _write(
        tmp_path / "src/seektalent/runtime/orchestrator.py",
        """
        def choose(source: str, runners: dict[str, object]) -> object | None:
            match source:
                case "cts":
                    return None
            return runners.get("liepin")
        """,
    )

    failures = collect_source_boundary_failures(tmp_path)

    assert failures == [
        "src/seektalent/runtime/orchestrator.py:3: runtime must not compare against concrete source ids",
        "src/seektalent/runtime/orchestrator.py:5: runtime must not index source plans by concrete source id",
    ]


def test_runtime_budget_detail_reason_and_default_leakage_are_reported(tmp_path: Path) -> None:
    _write(
        tmp_path / "src/seektalent/runtime/source_lanes.py",
        """
        from dataclasses import dataclass

        @dataclass(frozen=True)
        class RuntimeSourceBudgetPolicy:
            cts_page_size: int = 10
            liepin_max_cards: int = 30

        class RuntimeApprovedDetailLease:
            def validate(self) -> None:
                raise ValueError("RuntimeApprovedDetailLease currently supports only liepin.")

        async def _run_cts_source_lane() -> None:
            pass

        selected_sources = ("cts", "liepin")
        """,
    )

    failures = collect_source_boundary_failures(tmp_path)

    assert failures == [
        "src/seektalent/runtime/source_lanes.py:5: runtime must not contain source-specific runtime budget/detail/reason leakage",
        "src/seektalent/runtime/source_lanes.py:6: runtime must not contain source-specific runtime budget/detail/reason leakage",
        "src/seektalent/runtime/source_lanes.py:10: runtime must not contain source-specific runtime budget/detail/reason leakage",
        "src/seektalent/runtime/source_lanes.py:12: runtime must not contain source-specific runtime budget/detail/reason leakage",
        "src/seektalent/runtime/source_lanes.py:15: runtime must not contain source-specific runtime budget/detail/reason leakage",
    ]


def test_runtime_legacy_cts_query_names_are_reported(tmp_path: Path) -> None:
    _write(
        tmp_path / "src/seektalent/runtime/retrieval_runtime.py",
        """
        from seektalent.models import CTSQuery
        from seektalent.retrieval.query_builder import CTSQueryBuildInput, build_cts_query

        def run() -> None:
            stage = "search_cts"
            artifact = "cts_queries"
            exhausted = "cts_exhausted"
            metric = "cts_raw_hits"
            del stage, artifact, exhausted, metric
        """,
    )

    failures = collect_source_boundary_failures(tmp_path)

    assert failures == [
        "src/seektalent/runtime/retrieval_runtime.py:1: runtime must not contain source-specific runtime budget/detail/reason leakage",
        "src/seektalent/runtime/retrieval_runtime.py:2: runtime must not contain source-specific runtime budget/detail/reason leakage",
        "src/seektalent/runtime/retrieval_runtime.py:5: runtime must not contain source-specific runtime budget/detail/reason leakage",
        "src/seektalent/runtime/retrieval_runtime.py:6: runtime must not contain source-specific runtime budget/detail/reason leakage",
        "src/seektalent/runtime/retrieval_runtime.py:7: runtime must not contain source-specific runtime budget/detail/reason leakage",
        "src/seektalent/runtime/retrieval_runtime.py:8: runtime must not contain source-specific runtime budget/detail/reason leakage",
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
        from seektalent.source_contracts import SourceRegistry
        """,
    )
    _write(
        tmp_path / "src/seektalent/providers/fixture/source.py",
        """
        from seektalent.source_contracts import SourceLaneRequest
        """,
    )
    _write(
        tmp_path / "tach.toml",
        """
        [[modules]]
        path = "seektalent.providers"
        depends_on = ["seektalent.source_contracts"]

        [[modules]]
        path = "seektalent.runtime"
        depends_on = ["seektalent.source_contracts"]
        """,
    )

    assert collect_source_boundary_failures(tmp_path) == []
