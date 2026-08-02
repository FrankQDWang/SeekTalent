from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).parents[1]
SRC = ROOT / "src"


def test_deprecated_execution_packages_are_absent() -> None:
    for package_name in ("seektalent_conversation_agent", "seektalent_agent_memory"):
        package_root = SRC / package_name
        assert not tuple(package_root.glob("*.py"))


def test_production_ui_has_one_runtime_composition_without_legacy_imports() -> None:
    server = (SRC / "seektalent_ui" / "server.py").read_text(encoding="utf-8")
    assert "build_agent_service" not in server
    assert "agent_memory_service" not in server
    assert "workflow_start_outbox_runner" not in server
    assert "requirement_extraction_outbox_runner" not in server
    assert "agent_workbench_routes" not in server
    assert "from seektalent_ui.runtime_execution import" in server
    assert "event_routes" not in server
    assert "workbench_note_writer_agent_factory" not in server


def test_dead_generic_workbench_modules_are_not_packaged() -> None:
    dead_modules = {
        "event_routes.py",
        "job_runner.py",
        "workbench_routes.py",
        "workbench_store.py",
        "artifact_repair_import.py",
        "runtime_bridge.py",
        "runtime_workbench_bridge.py",
        "workbench_liepin_start_probe.py",
    }
    assert not dead_modules.intersection(path.name for path in (SRC / "seektalent_ui").glob("*.py"))


def test_generated_frontend_contract_contains_only_active_routes_and_schemas() -> None:
    schema = (ROOT / "apps/web-react/src/lib/api/schema.d.ts").read_text(encoding="utf-8")
    for legacy in ("/api/agent/conversations", "/api/agent/memory", "/api/workbench/events", "AgentWorkbench"):
        assert legacy not in schema
    assert "/api/agent/workbench/v2/conversations" in schema


def test_active_python_sources_do_not_import_deleted_products() -> None:
    for path in (SRC / "seektalent_ui").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert not module.startswith(("seektalent_conversation_agent", "seektalent_agent_memory")), path
            elif isinstance(node, ast.Import):
                assert all(
                    not alias.name.startswith(("seektalent_conversation_agent", "seektalent_agent_memory"))
                    for alias in node.names
                ), path


def test_prepare_readiness_has_no_fixed_sidecar_receipt_or_manual_effect_factory() -> None:
    source = (SRC / "seektalent" / "liepin_cards_source_operation.py").read_text(encoding="utf-8")
    gate = (SRC / "seektalent" / "liepin_verify_session_gate.py").read_text(encoding="utf-8")
    assert "accepted_sidecar_generation=1" not in source
    assert "accepted_sidecar_journal_revision=1" not in source
    assert "create_wtscli_verify_session_effect" not in gate
