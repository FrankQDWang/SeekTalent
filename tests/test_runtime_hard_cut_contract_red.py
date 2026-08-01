from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).parents[1]
SRC = ROOT / "src"


def test_deprecated_execution_packages_are_absent() -> None:
    assert not (SRC / "seektalent_conversation_agent").exists()
    assert not (SRC / "seektalent_agent_memory").exists()


def test_production_ui_has_one_runtime_composition_without_legacy_imports() -> None:
    server = (SRC / "seektalent_ui" / "server.py").read_text(encoding="utf-8")
    assert "build_agent_service" not in server
    assert "agent_memory_service" not in server
    assert "workflow_start_outbox_runner" not in server
    assert "requirement_extraction_outbox_runner" not in server
    assert "agent_workbench_routes" not in server
    assert "from seektalent_ui.runtime_execution import" in server


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

