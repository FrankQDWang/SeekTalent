from __future__ import annotations

from pathlib import Path

import pytest
from pydantic_ai.models.test import TestModel

from seektalent.llm_config import build_llm_binding, inspect_llm_callpoints, resolve_llm_config
from seektalent.models import BranchEvaluationDraft_t, SearchRunSummaryDraft_t


def _write_env(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_resolve_llm_config_uses_openai_defaults_and_global_key(tmp_path: Path) -> None:
    env_file = _write_env(
        tmp_path / ".env",
        "OPENAI_API_KEY=test-openai-key\n",
    )

    config = resolve_llm_config("requirement_extraction", env_file=env_file)

    assert config.provider == "openai"
    assert config.model == "gpt-5.4-mini"
    assert config.base_url is None
    assert config.api_key == "test-openai-key"
    assert config.requested_output_mode == "auto"
    assert config.resolved_output_mode == "native"


def test_resolve_llm_config_requires_base_url_and_key_for_dashscope(tmp_path: Path) -> None:
    env_file = _write_env(
        tmp_path / ".env",
        "\n".join(
            [
                "SEEKTALENT_SEARCH_CONTROLLER_DECISION_PROVIDER=dashscope",
                "SEEKTALENT_SEARCH_CONTROLLER_DECISION_MODEL=qwen-max",
            ]
        ),
    )

    with pytest.raises(ValueError, match="SEEKTALENT_SEARCH_CONTROLLER_DECISION_BASE_URL"):
        resolve_llm_config("search_controller_decision", env_file=env_file)


def test_resolve_llm_config_rejects_prompted_for_controller(tmp_path: Path) -> None:
    env_file = _write_env(
        tmp_path / ".env",
        "\n".join(
            [
                "OPENAI_API_KEY=test-openai-key",
                "SEEKTALENT_SEARCH_CONTROLLER_DECISION_OUTPUT_MODE=prompted",
            ]
        ),
    )

    with pytest.raises(ValueError, match="not allowed for search_controller_decision"):
        resolve_llm_config("search_controller_decision", env_file=env_file)


def test_build_llm_binding_returns_configured_tool_output(tmp_path: Path) -> None:
    env_file = _write_env(
        tmp_path / ".env",
        "\n".join(
            [
                "OPENAI_API_KEY=test-openai-key",
                "SEEKTALENT_BRANCH_OUTCOME_EVALUATION_OUTPUT_MODE=tool",
            ]
        ),
    )

    binding = build_llm_binding(
        BranchEvaluationDraft_t,
        callpoint="branch_outcome_evaluation",
        env_file=env_file,
    )

    assert binding.audit_output_mode == "ToolOutput(strict=True)"
    assert binding.audit_model_name == "openai:gpt-5.4-mini"


def test_build_llm_binding_keeps_injected_model_on_native_output() -> None:
    model = TestModel(custom_output_args={"run_summary": "ok"})

    binding = build_llm_binding(
        SearchRunSummaryDraft_t,
        callpoint="search_run_finalization",
        model=model,
    )

    assert binding.model is model
    assert binding.audit_output_mode == "NativeOutput(strict=True)"
    assert binding.audit_model_name == "test"


def test_inspect_llm_callpoints_reports_resolved_modes(tmp_path: Path) -> None:
    env_file = _write_env(
        tmp_path / ".env",
        "\n".join(
            [
                "OPENAI_API_KEY=test-openai-key",
                "SEEKTALENT_REQUIREMENT_EXTRACTION_OUTPUT_MODE=prompted",
                "SEEKTALENT_SEARCH_CONTROLLER_DECISION_OUTPUT_MODE=tool",
            ]
        ),
    )

    payload = inspect_llm_callpoints(env_file)

    assert payload["requirement_extraction"].requested_output_mode == "prompted"
    assert payload["requirement_extraction"].resolved_output_mode == "prompted"
    assert payload["search_controller_decision"].requested_output_mode == "tool"
    assert payload["search_controller_decision"].resolved_output_mode == "tool"
