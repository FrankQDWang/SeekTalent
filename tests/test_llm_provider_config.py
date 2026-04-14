from __future__ import annotations

import os
from pathlib import Path

import pytest
from pydantic import ValidationError

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
from seektalent.reflection.critic import ReflectionCritic
from seektalent.requirements.extractor import RequirementExtractor
from seektalent.scoring.scorer import ResumeScorer


def _prompt(name: str) -> LoadedPrompt:
    return LoadedPrompt(name=name, path=Path(f"{name}.md"), content=f"{name} prompt", sha256="hash")


def test_app_settings_rejects_unqualified_model_ids() -> None:
    with pytest.raises(ValidationError, match="provider:model"):
        AppSettings(_env_file=None, requirements_model="gpt-5.4-mini")


def test_app_settings_accepts_fully_qualified_model_ids() -> None:
    settings = AppSettings(
        _env_file=None,
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
    settings = AppSettings(
        _env_file=None,
        scoring_model="openai-chat:deepseek-v3.2",
        judge_model="openai-chat:qwen-plus",
    )

    assert settings.effective_judge_model == "openai-chat:qwen-plus"


def test_app_settings_accepts_explicit_judge_reasoning_effort() -> None:
    settings = AppSettings(
        _env_file=None,
        reasoning_effort="off",
        judge_reasoning_effort="high",
    )

    assert settings.effective_judge_reasoning_effort == "high"


def test_app_settings_disable_eval_by_default() -> None:
    settings = AppSettings(_env_file=None)

    assert settings.enable_eval is False


def test_app_settings_weave_entity_falls_back_to_wandb_entity() -> None:
    settings = AppSettings(
        _env_file=None,
        wandb_entity="frankqdwang1-personal-creations",
    )

    assert settings.effective_weave_entity == "frankqdwang1-personal-creations"


def test_app_settings_rejects_max_rounds_above_ten() -> None:
    with pytest.raises(ValidationError, match="max_rounds must be <= 10"):
        AppSettings(_env_file=None, max_rounds=11)


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


def test_build_model_settings_keeps_openai_only_knobs_for_openai_models() -> None:
    settings = AppSettings(_env_file=None)

    model_settings = build_model_settings(settings, "openai-responses:gpt-5.4-mini")

    assert model_settings["thinking"] == "medium"
    assert model_settings["openai_reasoning_summary"] == "concise"
    assert model_settings["openai_text_verbosity"] == "low"


def test_build_model_settings_supports_turning_thinking_off() -> None:
    settings = AppSettings(_env_file=None, reasoning_effort="off")

    openai_chat_settings = build_model_settings(settings, "openai-chat:gpt-4.1-mini")
    openai_responses_settings = build_model_settings(settings, "openai-responses:gpt-5.4-mini")

    assert openai_chat_settings == {"thinking": False}
    assert openai_responses_settings == {
        "thinking": False,
        "openai_text_verbosity": "low",
    }


def test_build_model_settings_supports_judge_reasoning_override() -> None:
    settings = AppSettings(_env_file=None, reasoning_effort="off", judge_reasoning_effort="high")

    model_settings = build_model_settings(
        settings,
        "openai-responses:gpt-5.4",
        reasoning_effort=settings.effective_judge_reasoning_effort,
    )

    assert model_settings["thinking"] == "high"
    assert model_settings["openai_reasoning_summary"] == "concise"


def test_build_model_settings_omits_openai_only_knobs_for_other_providers() -> None:
    settings = AppSettings(_env_file=None)

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
    settings = AppSettings(_env_file=None)

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
    settings = AppSettings(
        _env_file=None,
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
    settings = AppSettings(
        _env_file=None,
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
    settings = AppSettings(
        _env_file=None,
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


@pytest.mark.parametrize(
    ("model_id", "env_var"),
    [
        ("openai-responses:gpt-5.4-mini", "OPENAI_API_KEY"),
        ("anthropic:claude-sonnet-4-5", "ANTHROPIC_API_KEY"),
        ("google-gla:gemini-2.5-pro", "GOOGLE_API_KEY"),
    ],
)
def test_preflight_models_surfaces_provider_credential_errors(
    monkeypatch: pytest.MonkeyPatch,
    model_id: str,
    env_var: str,
) -> None:
    monkeypatch.setattr("seektalent.llm.load_process_env", lambda: None)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    settings = AppSettings(_env_file=None).with_overrides(
        requirements_model=model_id,
        controller_model=model_id,
        scoring_model=model_id,
        reflection_model=model_id,
        finalize_model=model_id,
    )

    with pytest.raises(Exception, match=env_var):
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
def test_all_agents_use_one_output_retry_and_no_generic_retries(
    monkeypatch: pytest.MonkeyPatch,
    builder,
    prompt_name: str,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    settings = AppSettings(_env_file=None)
    component = builder(settings, _prompt(prompt_name))
    if isinstance(component, ResumeScorer):
        agent = component._build_agent()
    else:
        agent = component._get_agent()

    assert agent._max_tool_retries == 0
    assert agent._max_result_retries == 1
    assert type(agent._output_type).__name__ == "NativeOutput"
    assert agent._output_type.strict is True
