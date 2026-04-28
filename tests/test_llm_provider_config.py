from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError

from seektalent.candidate_feedback.proposal_runtime import model_dependency_gate_allows_mainline
from seektalent.config import AppSettings, load_process_env
from seektalent.controller.react_controller import ReActController
from seektalent.finalize.finalizer import Finalizer
from seektalent.llm import (
    build_model,
    build_model_settings,
    build_output_spec,
    model_provider,
    preflight_models,
)
from seektalent.prompting import LoadedPrompt
from seektalent.repair import _repair_with_model
from seektalent.resources import resolve_user_path
from seektalent.reflection.critic import ReflectionCritic
from seektalent.requirements.extractor import RequirementExtractor
from seektalent.scoring.scorer import ResumeScorer
from tests.settings_factory import make_settings


def _prompt(name: str) -> LoadedPrompt:
    return LoadedPrompt(name=name, path=Path(f"{name}.md"), content=f"{name} prompt", sha256="hash")


def test_app_settings_rejects_unqualified_model_ids() -> None:
    with pytest.raises(ValidationError, match="provider:model"):
        make_settings(requirements_model="gpt-5.4-mini")


def test_app_settings_accepts_fully_qualified_model_ids() -> None:
    settings = make_settings(
        requirements_model="openai-responses:gpt-5.4-mini",
        controller_model="openai-responses:gpt-5.4-mini",
        scoring_model="anthropic:claude-sonnet-4-5",
        finalize_model="google-gla:gemini-2.5-flash",
        reflection_model="openai-responses:gpt-5.4",
    )

    assert settings.requirements_model == "openai-responses:gpt-5.4-mini"
    assert settings.controller_model == "openai-responses:gpt-5.4-mini"
    assert settings.scoring_model == "anthropic:claude-sonnet-4-5"
    assert settings.finalize_model == "google-gla:gemini-2.5-flash"
    assert settings.effective_judge_model == "anthropic:claude-sonnet-4-5"


def test_app_settings_accepts_explicit_judge_model() -> None:
    settings = make_settings(
        scoring_model="openai-chat:deepseek-v3.2",
        judge_model="openai-chat:qwen-plus",
    )

    assert settings.effective_judge_model == "openai-chat:qwen-plus"


def test_app_settings_treats_empty_optional_model_env_vars_as_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEEKTALENT_JUDGE_MODEL", "")
    monkeypatch.setenv("SEEKTALENT_TUI_SUMMARY_MODEL", "")

    settings = AppSettings(_env_file=None)  # ty: ignore[unknown-argument]

    assert settings.judge_model is None
    assert settings.tui_summary_model is None


def test_app_settings_accepts_explicit_judge_reasoning_effort() -> None:
    settings = make_settings(
        reasoning_effort="off",
        judge_reasoning_effort="high",
    )

    assert settings.effective_judge_reasoning_effort == "high"


def test_prf_model_dependency_settings_are_explicit() -> None:
    settings = make_settings()

    assert settings.prf_span_model_name == "fastino/gliner2-multi-v1"
    assert settings.prf_embedding_model_name == "Alibaba-NLP/gte-multilingual-base"
    assert settings.prf_allow_remote_code is False


def test_sidecar_app_settings_expose_profile_and_mainline_gate_inputs() -> None:
    settings = make_settings()

    assert settings.prf_model_backend == "legacy"
    assert settings.prf_sidecar_profile == "host-local"
    assert settings.prf_sidecar_bind_host == "127.0.0.1"
    assert settings.prf_sidecar_endpoint == "http://127.0.0.1:8741"
    assert settings.prf_sidecar_endpoint_contract_version == "prf-sidecar-http-v1"
    assert settings.prf_sidecar_serve_mode == "dev-bootstrap"
    assert settings.prf_sidecar_timeout_seconds_shadow == 0.35
    assert settings.prf_sidecar_timeout_seconds_mainline == 1.5
    assert settings.prf_sidecar_max_batch_size == 32
    assert settings.prf_sidecar_max_payload_bytes == 262_144
    assert settings.prf_sidecar_bakeoff_promoted is False


def test_sidecar_default_env_tracks_profile_defaults() -> None:
    default_env = Path(__file__).resolve().parents[1] / "src" / "seektalent" / "default.env"
    settings = AppSettings(_env_file=default_env)  # ty: ignore[unknown-argument]

    assert settings.prf_sidecar_profile == "host-local"
    assert settings.prf_sidecar_bind_host == "127.0.0.1"
    assert settings.prf_sidecar_endpoint == "http://127.0.0.1:8741"
    assert settings.prf_sidecar_serve_mode == "dev-bootstrap"
    assert settings.prf_sidecar_bakeoff_promoted is False


def test_mainline_mode_requires_pinned_model_revisions() -> None:
    settings = make_settings(prf_v1_5_mode="mainline")

    assert model_dependency_gate_allows_mainline(settings) is False


def test_app_settings_accepts_stage_thinking_flags() -> None:
    settings = make_settings(
        controller_enable_thinking=True,
        reflection_enable_thinking=True,
    )

    assert settings.controller_enable_thinking is True
    assert settings.reflection_enable_thinking is True


def test_app_settings_enables_requirements_controller_and_reflection_thinking_by_default() -> None:
    settings = make_settings()

    assert settings.requirements_enable_thinking is True
    assert settings.controller_enable_thinking is True
    assert settings.reflection_enable_thinking is True


def test_app_settings_enables_controller_and_reflection_thinking_by_default() -> None:
    settings = make_settings()

    assert settings.controller_enable_thinking is True
    assert settings.reflection_enable_thinking is True


def test_app_settings_defaults_scoring_concurrency_to_recall_target() -> None:
    settings = make_settings()

    assert settings.scoring_max_concurrency == 10


def test_app_settings_runtime_mode_defaults_to_dev_paths() -> None:
    settings = make_settings()

    assert settings.runtime_mode == "dev"
    assert settings.artifacts_dir == "artifacts"
    assert settings.runs_dir == "runs"
    assert settings.llm_cache_dir == ".seektalent/cache"


def test_artifacts_dir_defaults_follow_runtime_mode() -> None:
    assert make_settings(runtime_mode="dev").artifacts_dir == "artifacts"
    assert make_settings(runtime_mode="prod").artifacts_dir == "~/.seektalent/artifacts"


def test_app_settings_prod_mode_defaults_to_global_user_paths() -> None:
    settings = make_settings(runtime_mode="prod")

    assert settings.runtime_mode == "prod"
    assert settings.artifacts_dir == "~/.seektalent/artifacts"
    assert settings.runs_dir == "~/.seektalent/runs"
    assert settings.llm_cache_dir == "~/.seektalent/cache"


def test_app_settings_rejects_invalid_runtime_mode() -> None:
    with pytest.raises(ValidationError, match="runtime_mode"):
        make_settings(runtime_mode="production")


def test_packaged_runtime_forces_prod_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEEKTALENT_PACKAGED", "1")

    settings = make_settings(runtime_mode="dev")

    assert settings.runtime_mode == "prod"
    assert settings.runs_dir == "~/.seektalent/runs"
    assert settings.llm_cache_dir == "~/.seektalent/cache"


def test_sys_frozen_runtime_forces_prod_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("seektalent.config.sys.frozen", True, raising=False)

    settings = make_settings(runtime_mode="dev")

    assert settings.runtime_mode == "prod"
    assert settings.runs_dir == "~/.seektalent/runs"
    assert settings.llm_cache_dir == "~/.seektalent/cache"


def test_explicit_paths_override_runtime_mode_defaults() -> None:
    settings = make_settings(
        runtime_mode="prod",
        runs_dir="/tmp/seektalent-runs",
        llm_cache_dir="/tmp/seektalent-cache",
    )

    assert settings.runs_dir == "/tmp/seektalent-runs"
    assert settings.llm_cache_dir == "/tmp/seektalent-cache"


def test_with_overrides_recomputes_mode_default_paths() -> None:
    settings = make_settings().with_overrides(runtime_mode="prod")

    assert settings.runtime_mode == "prod"
    assert settings.runs_dir == "~/.seektalent/runs"
    assert settings.llm_cache_dir == "~/.seektalent/cache"


def test_with_overrides_preserves_existing_explicit_paths_when_mode_changes() -> None:
    settings = make_settings(
        runs_dir="/tmp/custom-runs",
        llm_cache_dir="/tmp/custom-cache",
    ).with_overrides(runtime_mode="prod")

    assert settings.runtime_mode == "prod"
    assert settings.runs_dir == "/tmp/custom-runs"
    assert settings.llm_cache_dir == "/tmp/custom-cache"


def test_with_overrides_recomputes_mode_paths_after_non_mode_override() -> None:
    settings = make_settings().with_overrides(scoring_max_concurrency=7).with_overrides(runtime_mode="prod")

    assert settings.runtime_mode == "prod"
    assert settings.scoring_max_concurrency == 7
    assert settings.runs_dir == "~/.seektalent/runs"
    assert settings.llm_cache_dir == "~/.seektalent/cache"


def test_with_overrides_preserves_explicit_paths_after_non_mode_override() -> None:
    settings = make_settings(
        runs_dir="/tmp/custom-runs",
        llm_cache_dir="/tmp/custom-cache",
    ).with_overrides(scoring_max_concurrency=7).with_overrides(runtime_mode="prod")

    assert settings.runtime_mode == "prod"
    assert settings.scoring_max_concurrency == 7
    assert settings.runs_dir == "/tmp/custom-runs"
    assert settings.llm_cache_dir == "/tmp/custom-cache"


def test_app_settings_resolves_relative_paths_against_workspace_root(tmp_path: Path) -> None:
    settings = make_settings(
        workspace_root=str(tmp_path),
        artifacts_dir="artifacts",
        runs_dir="runs",
        llm_cache_dir=".seektalent/cache",
    )

    assert settings.project_root == tmp_path
    assert settings.artifacts_path == tmp_path / "artifacts"
    assert settings.runs_path == tmp_path / "runs"
    assert settings.llm_cache_path == tmp_path / ".seektalent" / "cache"


def test_artifacts_dir_resolves_against_workspace_root(tmp_path: Path) -> None:
    settings = make_settings(
        workspace_root=str(tmp_path),
        artifacts_dir="artifacts",
    )

    assert settings.artifacts_path == tmp_path / "artifacts"


def test_resolve_user_path_expands_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    assert resolve_user_path("~/.seektalent/runs") == tmp_path / ".seektalent" / "runs"


def test_resolve_user_path_preserves_relative_and_absolute_paths(tmp_path: Path) -> None:
    assert resolve_user_path("runs") == Path.cwd() / "runs"
    assert resolve_user_path(tmp_path / "absolute-runs") == tmp_path / "absolute-runs"
def test_app_settings_accepts_repair_cache_and_prompt_cache_settings() -> None:
    settings = make_settings(
        requirements_enable_thinking=False,
        structured_repair_model="openai-chat:qwen3.5-flash",
        structured_repair_reasoning_effort="off",
        llm_cache_dir="tmp/latency-cache",
        openai_prompt_cache_enabled=True,
        openai_prompt_cache_retention="48h",
    )

    assert settings.requirements_enable_thinking is False
    assert settings.structured_repair_model == "openai-chat:qwen3.5-flash"
    assert settings.structured_repair_reasoning_effort == "off"
    assert settings.llm_cache_dir == "tmp/latency-cache"
    assert settings.openai_prompt_cache_enabled is True
    assert settings.openai_prompt_cache_retention == "48h"


def test_app_settings_disable_eval_by_default() -> None:
    settings = make_settings()

    assert settings.enable_eval is False


def test_app_settings_weave_entity_falls_back_to_wandb_entity() -> None:
    settings = make_settings(
        wandb_entity="frankqdwang1-personal-creations",
    )

    assert settings.effective_weave_entity == "frankqdwang1-personal-creations"


def test_app_settings_rejects_max_rounds_above_ten() -> None:
    with pytest.raises(ValidationError, match="max_rounds must be <= 10"):
        make_settings(max_rounds=11)


def test_model_provider_returns_prefix() -> None:
    assert model_provider("openai-responses:gpt-5.4-mini") == "openai-responses"
    assert model_provider("anthropic:claude-sonnet-4-5") == "anthropic"


def test_build_model_routes_through_infer_model(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    loaded: list[bool] = []

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr("seektalent.llm.load_process_env", lambda: loaded.append(True))

    def fake_infer_model(model_id: str, provider_factory) -> object:  # noqa: ANN001
        calls.append(model_id)
        provider = provider_factory("openai-responses")
        assert provider.name == "openai"
        return object()

    monkeypatch.setattr("seektalent.llm.infer_model", fake_infer_model)

    build_model("openai-responses:gpt-5.4-mini")

    assert loaded == [True]
    assert calls == ["openai-responses:gpt-5.4-mini"]


def test_build_model_normalizes_openai_responses_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    def fake_infer_model(model_id: str, provider_factory) -> object:  # noqa: ANN001
        provider = provider_factory("openai-responses")
        assert str(provider.client.base_url) == "http://127.0.0.1:8317/v1/"
        return object()

    monkeypatch.setattr("seektalent.llm.infer_model", fake_infer_model)

    build_model("openai-responses:gpt-5.4", openai_base_url="http://127.0.0.1:8317/v1/responses")


def test_build_model_uses_explicit_openai_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "file-key")

    def fake_infer_model(model_id: str, provider_factory) -> object:  # noqa: ANN001
        provider = provider_factory("openai-responses")
        assert provider.client.api_key == "judge-key"
        return object()

    monkeypatch.setattr("seektalent.llm.infer_model", fake_infer_model)

    build_model("openai-responses:gpt-5.4", openai_api_key="judge-key")


def test_build_model_uses_fresh_openai_provider_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    providers = []
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    def fake_infer_model(model_id: str, provider_factory) -> object:  # noqa: ANN001
        providers.append(provider_factory("openai-responses"))
        return object()

    monkeypatch.setattr("seektalent.llm.infer_model", fake_infer_model)

    build_model("openai-responses:gpt-5.4-mini")
    build_model("openai-responses:gpt-5.4-mini")

    assert len(providers) == 2
    assert providers[0] is not providers[1]
    assert providers[0].client is not providers[1].client


def test_load_process_env_sets_missing_variables(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("OPENAI_API_KEY=file-key\nGOOGLE_API_KEY=google-key\n", encoding="utf-8")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    load_process_env(env_file)

    assert os.environ["OPENAI_API_KEY"] == "file-key"
    assert os.environ["GOOGLE_API_KEY"] == "google-key"


def test_load_process_env_does_not_override_existing_variables(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("OPENAI_API_KEY=file-key\n", encoding="utf-8")
    monkeypatch.setenv("OPENAI_API_KEY", "existing-key")

    load_process_env(env_file)

    assert os.environ["OPENAI_API_KEY"] == "existing-key"


def test_default_env_does_not_force_empty_prompt_cache_retention() -> None:
    default_env = Path(__file__).resolve().parents[1] / "src" / "seektalent" / "default.env"
    settings = AppSettings(_env_file=default_env)  # ty: ignore[unknown-argument]

    assert settings.openai_prompt_cache_retention is None


def test_build_output_spec_uses_native_output_for_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    model = build_model("openai-responses:gpt-5.4-mini")

    spec = build_output_spec("openai-responses:gpt-5.4-mini", model, dict[str, str])

    assert type(spec).__name__ == "NativeOutput"
    assert spec.strict is True


def test_build_output_spec_uses_prompted_output_for_openai_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    model = build_model("openai-chat:gpt-4.1-mini")

    spec = build_output_spec("openai-chat:gpt-4.1-mini", model, dict[str, str])

    assert type(spec).__name__ == "PromptedOutput"


def test_build_output_spec_uses_native_output_for_openai_chat_deepseek_v32(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    model = build_model("openai-chat:deepseek-v3.2")

    spec = build_output_spec("openai-chat:deepseek-v3.2", model, dict[str, str])

    assert type(spec).__name__ == "NativeOutput"
    assert spec.strict is True


def test_build_model_settings_keeps_openai_only_knobs_for_openai_models() -> None:
    settings = make_settings()

    model_settings = build_model_settings(settings, "openai-responses:gpt-5.4-mini")
    raw_settings = cast(dict[str, object], model_settings)

    assert model_settings["thinking"] == "medium"
    assert raw_settings["openai_reasoning_summary"] == "concise"
    assert raw_settings["openai_text_verbosity"] == "low"


def test_build_model_settings_supports_turning_thinking_off() -> None:
    settings = make_settings(reasoning_effort="off")

    openai_chat_settings = build_model_settings(settings, "openai-chat:gpt-4.1-mini")
    openai_responses_settings = build_model_settings(settings, "openai-responses:gpt-5.4-mini")

    assert openai_chat_settings == {"thinking": False}
    assert openai_responses_settings == {
        "thinking": False,
        "openai_text_verbosity": "low",
    }


def test_build_model_settings_adds_bailian_enable_thinking_for_deepseek_v32() -> None:
    settings = make_settings(reasoning_effort="off")

    model_settings = build_model_settings(
        settings,
        "openai-chat:deepseek-v3.2",
        enable_thinking=True,
    )

    assert model_settings == {
        "thinking": False,
        "extra_body": {"enable_thinking": True},
    }


def test_build_model_settings_adds_prompt_cache_fields_when_enabled() -> None:
    settings = make_settings(
        openai_prompt_cache_enabled=True,
        openai_prompt_cache_retention="24h",
    )

    model_settings = build_model_settings(
        settings,
        "openai-responses:gpt-5.4-mini",
        prompt_cache_key="requirements:abc",
    )
    raw_settings = cast(dict[str, object], model_settings)

    assert raw_settings["openai_prompt_cache_key"] == "requirements:abc"
    assert raw_settings["openai_prompt_cache_retention"] == "24h"


def test_build_model_settings_omits_prompt_cache_fields_when_disabled() -> None:
    settings = make_settings(openai_prompt_cache_enabled=False)

    model_settings = build_model_settings(
        settings,
        "openai-responses:gpt-5.4-mini",
        prompt_cache_key="requirements:abc",
    )

    assert "openai_prompt_cache_key" not in model_settings
    assert "openai_prompt_cache_retention" not in model_settings


def test_build_model_settings_omits_prompt_cache_fields_for_non_openai_models() -> None:
    settings = make_settings(openai_prompt_cache_enabled=True)

    model_settings = build_model_settings(
        settings,
        "anthropic:claude-sonnet-4-5",
        prompt_cache_key="requirements:abc",
    )

    assert "openai_prompt_cache_key" not in model_settings
    assert "openai_prompt_cache_retention" not in model_settings
def test_build_model_settings_keeps_bailian_enable_thinking_extra_body_with_prompt_cache() -> None:
    settings = make_settings(
        openai_prompt_cache_enabled=True,
        openai_prompt_cache_retention="24h",
    )

    model_settings = build_model_settings(
        settings,
        "openai-chat:deepseek-v3.2",
        enable_thinking=True,
        prompt_cache_key="controller:abc",
    )
    raw_settings = cast(dict[str, object], model_settings)

    assert model_settings["extra_body"] == {"enable_thinking": True}
    assert raw_settings["openai_prompt_cache_key"] == "controller:abc"
    assert raw_settings["openai_prompt_cache_retention"] == "24h"


def test_build_model_settings_ignores_enable_thinking_for_other_openai_models() -> None:
    settings = make_settings(reasoning_effort="off")

    openai_chat_settings = build_model_settings(
        settings,
        "openai-chat:gpt-4.1-mini",
        enable_thinking=True,
    )
    openai_responses_settings = build_model_settings(
        settings,
        "openai-responses:gpt-5.4-mini",
        enable_thinking=True,
    )

    assert openai_chat_settings == {"thinking": False}
    assert openai_responses_settings == {
        "thinking": False,
        "openai_text_verbosity": "low",
    }


def test_build_model_settings_supports_judge_reasoning_override() -> None:
    settings = make_settings(reasoning_effort="off", judge_reasoning_effort="high")

    model_settings = build_model_settings(
        settings,
        "openai-responses:gpt-5.4",
        reasoning_effort=settings.effective_judge_reasoning_effort,
    )
    raw_settings = cast(dict[str, object], model_settings)

    assert model_settings["thinking"] == "high"
    assert raw_settings["openai_reasoning_summary"] == "concise"


def test_build_model_settings_omits_openai_only_knobs_for_other_providers() -> None:
    settings = make_settings()

    model_settings = build_model_settings(settings, "anthropic:claude-sonnet-4-5")

    assert model_settings == {"thinking": "medium"}


def test_preflight_models_fails_when_native_structured_output_is_unsupported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeProfile:
        supports_json_schema_output = False

    class FakeModel:
        profile = FakeProfile()

    monkeypatch.setattr("seektalent.llm.build_model", lambda model_id, **kwargs: FakeModel())
    settings = make_settings()

    with pytest.raises(ValueError, match="native structured output"):
        preflight_models(settings)


def test_preflight_models_allows_openai_chat_without_native_structured_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeProfile:
        supports_json_schema_output = False

    class FakeModel:
        profile = FakeProfile()

    monkeypatch.setattr("seektalent.llm.build_model", lambda model_id, **kwargs: FakeModel())
    settings = make_settings(
        requirements_model="openai-chat:qwen-plus",
        controller_model="openai-chat:qwen-plus",
        scoring_model="openai-chat:qwen-plus",
        reflection_model="openai-chat:qwen-plus",
        finalize_model="openai-chat:qwen-plus",
    )

    preflight_models(settings)


def test_preflight_models_skips_judge_model_when_eval_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str | None, str | None]] = []

    class FakeProfile:
        supports_json_schema_output = True

    class FakeModel:
        profile = FakeProfile()

    def fake_build_model(  # noqa: ANN001
        model_id: str,
        *,
        openai_base_url: str | None = None,
        openai_api_key: str | None = None,
    ):
        calls.append((model_id, openai_base_url, openai_api_key))
        return FakeModel()

    monkeypatch.setattr("seektalent.llm.build_model", fake_build_model)
    settings = make_settings(
        judge_model="openai-responses:gpt-5.4",
        judge_openai_base_url="http://127.0.0.1:8317/v1/responses",
        enable_eval=False,
    )

    preflight_models(settings)

    assert (
        settings.effective_judge_model,
        settings.judge_openai_base_url,
        settings.judge_openai_api_key,
    ) not in calls


def test_preflight_models_checks_judge_model_when_eval_is_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str | None, str | None]] = []

    class FakeProfile:
        supports_json_schema_output = True

    class FakeModel:
        profile = FakeProfile()

    def fake_build_model(  # noqa: ANN001
        model_id: str,
        *,
        openai_base_url: str | None = None,
        openai_api_key: str | None = None,
    ):
        calls.append((model_id, openai_base_url, openai_api_key))
        return FakeModel()

    monkeypatch.setattr("seektalent.llm.build_model", fake_build_model)
    settings = make_settings(
        judge_model="openai-responses:gpt-5.4",
        judge_openai_base_url="http://127.0.0.1:8317/v1/responses",
        judge_openai_api_key="judge-key",
        enable_eval=True,
    )

    preflight_models(settings)

    assert (
        settings.effective_judge_model,
        settings.judge_openai_base_url,
        settings.judge_openai_api_key,
    ) in calls


def test_preflight_models_checks_explicit_extra_model_specs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str | None, str | None]] = []

    class FakeProfile:
        supports_json_schema_output = True

    class FakeModel:
        profile = FakeProfile()

    def fake_build_model(  # noqa: ANN001
        model_id: str,
        *,
        openai_base_url: str | None = None,
        openai_api_key: str | None = None,
    ):
        calls.append((model_id, openai_base_url, openai_api_key))
        return FakeModel()

    monkeypatch.setattr("seektalent.llm.build_model", fake_build_model)
    settings = make_settings()

    preflight_models(
        settings,
        extra_model_specs=[
            ("openai-chat:qwen3.5-flash", "https://dashscope.aliyuncs.com/compatible-mode/v1", "bailian-key"),
            ("openai-chat:qwen-plus", None, None),
            ("openai-chat:qwen-plus", None, None),
        ],
    )

    assert ("openai-chat:qwen3.5-flash", "https://dashscope.aliyuncs.com/compatible-mode/v1", "bailian-key") in calls
    assert calls.count(("openai-chat:qwen-plus", None, None)) == 1


@pytest.mark.parametrize(
    ("model_id", "expected_error"),
    [
        ("openai-responses:gpt-5.4-mini", "OPENAI_API_KEY"),
        ("anthropic:claude-sonnet-4-5", r'pydantic-ai-slim\[anthropic\]'),
        ("google-gla:gemini-2.5-pro", r'pydantic-ai-slim\[google\]'),
    ],
)
def test_preflight_models_surfaces_provider_credential_errors(
    monkeypatch: pytest.MonkeyPatch,
    model_id: str,
    expected_error: str,
) -> None:
    monkeypatch.setattr("seektalent.llm.load_process_env", lambda: None)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    settings = make_settings(
        requirements_model=model_id,
        controller_model=model_id,
        scoring_model=model_id,
        reflection_model=model_id,
        finalize_model=model_id,
    )

    with pytest.raises(Exception, match=expected_error):
        preflight_models(settings)


@pytest.mark.parametrize(
    ("builder", "prompt_name"),
    [
        (RequirementExtractor, "requirements"),
        (ReActController, "controller"),
        (ResumeScorer, "scoring"),
        (ReflectionCritic, "reflection"),
        (Finalizer, "finalize"),
    ],
)
def test_all_agents_use_two_output_retries_and_no_generic_retries(
    monkeypatch: pytest.MonkeyPatch,
    builder,
    prompt_name: str,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    settings = make_settings()
    component = builder(settings, _prompt(prompt_name))
    if isinstance(component, ResumeScorer):
        agent = component._build_agent()
    else:
        agent = component._get_agent()

    assert agent._max_tool_retries == 0
    assert agent._max_result_retries == 2
    assert type(agent._output_type).__name__ == "NativeOutput"
    assert agent._output_type.strict is True


def test_controller_and_reflection_pass_independent_thinking_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller_calls: list[dict[str, object]] = []
    reflection_calls: list[dict[str, object]] = []
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    def fake_controller_settings(settings, model_id: str, **kwargs: object):  # noqa: ANN001
        controller_calls.append(kwargs)
        return {"thinking": False}

    def fake_reflection_settings(settings, model_id: str, **kwargs: object):  # noqa: ANN001
        reflection_calls.append(kwargs)
        return {"thinking": False}

    monkeypatch.setattr(
        "seektalent.controller.react_controller.build_model_settings",
        fake_controller_settings,
    )
    monkeypatch.setattr(
        "seektalent.reflection.critic.build_model_settings",
        fake_reflection_settings,
    )
    settings = make_settings(
        controller_enable_thinking=True,
        reflection_enable_thinking=False,
    )

    ReActController(settings, _prompt("controller"))._get_agent()
    ReflectionCritic(settings, _prompt("reflection"))._get_agent()

    assert controller_calls[0]["enable_thinking"] is True
    assert reflection_calls[0]["enable_thinking"] is False


def test_requirement_extractor_passes_requirements_thinking_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    def fake_requirement_settings(settings, model_id: str, **kwargs: object):  # noqa: ANN001
        calls.append(kwargs)
        return {"thinking": False}

    monkeypatch.setattr(
        "seektalent.requirements.extractor.build_model_settings",
        fake_requirement_settings,
    )
    settings = make_settings(
        requirements_model="openai-chat:deepseek-v3.2",
        requirements_enable_thinking=True,
    )

    RequirementExtractor(settings, _prompt("requirements"))._get_agent(prompt_cache_key="requirements:abc")

    assert calls == [{"enable_thinking": True, "prompt_cache_key": "requirements:abc"}]


def test_repair_model_settings_force_non_thinking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def fake_repair_settings(settings, model_id: str, **kwargs: object):  # noqa: ANN001
        calls.append(kwargs)
        return {"thinking": False}

    class FakeAgent:
        @classmethod
        def __class_getitem__(cls, _item):  # noqa: ANN001
            return cls

        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

        async def run(self, user_prompt: str):  # noqa: ANN001
            class Result:
                output = "ok"

                @staticmethod
                def usage():
                    return type(
                        "Usage",
                        (),
                        {
                            "input_tokens": 7,
                            "output_tokens": 2,
                            "cache_read_tokens": 1,
                            "cache_write_tokens": 3,
                            "details": {"reasoning_tokens": 4},
                        },
                    )()

            del user_prompt
            return Result()

    monkeypatch.setattr("seektalent.repair.build_model_settings", fake_repair_settings)
    monkeypatch.setattr("seektalent.repair.build_model", lambda model_id: object())
    monkeypatch.setattr("seektalent.repair.build_output_spec", lambda model_id, model, output_type: output_type)
    monkeypatch.setattr("seektalent.repair.Agent", FakeAgent)
    settings = make_settings(
        structured_repair_model="openai-chat:deepseek-v3.2",
        structured_repair_reasoning_effort="off",
    )

    result, usage, artifact = asyncio.run(
        _repair_with_model(
            settings,
            prompt_name="repair_requirements",
            user_payload={"REPAIR_REASON": {"reason": "broken"}},
            output_type=str,
            system_prompt="repair prompt",
            user_prompt="repair payload",
        )
    )

    assert result == "ok"
    assert usage is not None
    assert usage.model_dump(mode="json") == {
        "input_tokens": 7,
        "output_tokens": 2,
        "total_tokens": 9,
        "cache_read_tokens": 1,
        "cache_write_tokens": 3,
        "details": {"reasoning_tokens": 4},
    }
    assert artifact["prompt_name"] == "repair_requirements"
    assert calls == [{"reasoning_effort": "off", "enable_thinking": False}]
