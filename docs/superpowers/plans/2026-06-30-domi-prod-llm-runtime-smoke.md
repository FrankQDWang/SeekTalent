# Domi Prod LLM Runtime Smoke Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Domi production LLM transport and a Domi-runtime smoke path without changing source-checkout development defaults.

**Architecture:** Keep model and stage configuration in the existing `SEEKTALENT_TEXT_LLM_*` system. Add `provider_label=domi` as a transport branch that uses Domi JWT, Domi proxy base URL, and Domi channel query while reusing the existing Bailian-compatible model capability policy. Add a smoke script that uses Domi's bundled Python only as the interpreter for an isolated `~/.seektalent/domi-runtime` install.

**Tech Stack:** Python 3.12+, Pydantic settings, pydantic-ai OpenAIChatModel, OpenAI Python `AsyncOpenAI`, pytest, Bash.

---

## File Structure

- Modify `src/seektalent/config.py`: accept the `domi` text LLM provider label, add Domi transport settings, expose them through `TextLLMSettings`, and validate Domi uses the OpenAI-compatible protocol.
- Modify `src/seektalent/llm.py`: resolve Domi base URL/JWT/channel, reuse Bailian model capabilities for Domi, and construct an `AsyncOpenAI` client with `default_query={"channel": "seek_talent"}` by default.
- Modify `src/seektalent/product_env.py`: pass the minimal Domi provider variables through the packaged Workbench environment.
- Modify `src/seektalent/cli.py`: make doctor and `seektalent workbench` preflight require the credential for the selected provider.
- Create `scripts/smoke-domi-runtime.sh`: build/install the current wheel under `~/.seektalent/domi-runtime` using Domi's Python runtime, run doctor, perform a Domi LLM proxy hello call, check OpenCLI, and start the packaged Workbench when the environment is ready.
- Create `tests/test_domi_runtime_smoke_script.py`: static and syntax coverage for the smoke script.
- Modify `tests/test_llm_provider_config.py`: settings, capability, and client-construction tests.
- Modify `tests/test_product_env.py`: product environment passthrough tests.
- Modify `tests/test_cli.py`: doctor and workbench preflight tests.
- Modify `docs/development.md`: document the Domi runtime smoke command and success criteria.

Existing dirty files `src/seektalent/providers/liepin/liepin_site_adapter.py` and `tests/test_liepin_opencli_browser.py` are unrelated. Do not stage or edit them.

---

### Task 1: Add Domi Provider Settings

**Files:**
- Modify: `src/seektalent/config.py`
- Test: `tests/test_llm_provider_config.py`

- [ ] **Step 1: Add failing settings tests**

Add these tests near `test_canonical_text_llm_defaults_use_dual_protocol_surface` in `tests/test_llm_provider_config.py`:

```python
def test_domi_provider_keeps_stage_model_defaults_and_adds_transport_defaults() -> None:
    settings = make_settings(text_llm_provider_label="domi", domi_jwt="domi-test-jwt")

    assert settings.text_llm_provider_label == "domi"
    assert settings.text_llm_protocol_family == "openai_chat_completions_compatible"
    assert settings.text_llm_endpoint_kind == "bailian_openai_chat_completions"
    assert settings.requirements_model_id == "deepseek-v4-pro"
    assert settings.controller_model_id == "deepseek-v4-pro"
    assert settings.scoring_model_id == "deepseek-v4-flash"
    assert settings.workbench_conversation_model_id == "deepseek-v4-flash"
    assert settings.domi_jwt == "domi-test-jwt"
    assert settings.domi_llm_base_url == "https://test-api-agent.hewa.cn/api/v1/runtime/llm-proxy/v1"
    assert settings.domi_llm_channel == "seek_talent"
    assert settings.text_llm.domi_jwt == "domi-test-jwt"
    assert settings.text_llm.domi_llm_base_url == settings.domi_llm_base_url
    assert settings.text_llm.domi_llm_channel == "seek_talent"


def test_domi_provider_requires_openai_compatible_protocol() -> None:
    with pytest.raises(ValidationError, match="domi"):
        make_settings(
            text_llm_provider_label="domi",
            text_llm_protocol_family="anthropic_messages_compatible",
            text_llm_endpoint_kind="bailian_anthropic_messages",
            domi_jwt="domi-test-jwt",
        )


def test_empty_domi_base_url_and_channel_are_rejected() -> None:
    with pytest.raises(ValidationError, match="domi_llm_base_url"):
        make_settings(text_llm_provider_label="domi", domi_jwt="domi-test-jwt", domi_llm_base_url="")

    with pytest.raises(ValidationError, match="domi_llm_channel"):
        make_settings(text_llm_provider_label="domi", domi_jwt="domi-test-jwt", domi_llm_channel="")
```

- [ ] **Step 2: Run settings tests and verify failure**

Run:

```bash
uv run pytest tests/test_llm_provider_config.py::test_domi_provider_keeps_stage_model_defaults_and_adds_transport_defaults tests/test_llm_provider_config.py::test_domi_provider_requires_openai_compatible_protocol tests/test_llm_provider_config.py::test_empty_domi_base_url_and_channel_are_rejected -q
```

Expected: fail because `domi` is not accepted as `TextLLMProviderLabel` and the Domi fields do not exist.

- [ ] **Step 3: Implement settings fields**

In `src/seektalent/config.py`, replace the provider label type with:

```python
TextLLMProviderLabel = Literal["bailian", "domi"]
```

Add these constants near the existing default path constants:

```python
DEFAULT_DOMI_LLM_BASE_URL = "https://test-api-agent.hewa.cn/api/v1/runtime/llm-proxy/v1"
DEFAULT_DOMI_LLM_CHANNEL = "seek_talent"
```

Extend `TextLLMSettings` with:

```python
    domi_jwt: str | None
    domi_llm_base_url: str
    domi_llm_channel: str
```

Add these `AppSettings` fields immediately after `text_llm_api_key`:

```python
    domi_jwt: str | None = None
    domi_llm_base_url: str = DEFAULT_DOMI_LLM_BASE_URL
    domi_llm_channel: str = DEFAULT_DOMI_LLM_CHANNEL
```

Add this focused validator after `normalize_empty_prompt_cache_retention`:

```python
    @field_validator("domi_llm_base_url", "domi_llm_channel", mode="before")
    @classmethod
    def validate_non_empty_domi_strings(cls, value: object, info: ValidationInfo) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError(f"{info.field_name} must not be empty")
        return normalized.rstrip("/") if info.field_name == "domi_llm_base_url" else normalized
```

Update `validate_text_llm_surface` so Domi is OpenAI-compatible only:

```python
    @model_validator(mode="after")
    def validate_text_llm_surface(self) -> "AppSettings":
        expected_endpoint_kind = TEXT_LLM_ENDPOINT_KIND_BY_PROTOCOL_FAMILY[self.text_llm_protocol_family]
        if self.text_llm_endpoint_kind != expected_endpoint_kind:
            raise ValueError(
                "text_llm_endpoint_kind must match text_llm_protocol_family "
                f"({self.text_llm_protocol_family} -> {expected_endpoint_kind})"
            )
        if (
            self.text_llm_provider_label == "domi"
            and self.text_llm_protocol_family != "openai_chat_completions_compatible"
        ):
            raise ValueError("domi text LLM provider requires openai_chat_completions_compatible protocol")
        return self
```

Update the `text_llm` property constructor with:

```python
            domi_jwt=self.domi_jwt,
            domi_llm_base_url=self.domi_llm_base_url,
            domi_llm_channel=self.domi_llm_channel,
```

- [ ] **Step 4: Run settings tests and verify pass**

Run:

```bash
uv run pytest tests/test_llm_provider_config.py::test_domi_provider_keeps_stage_model_defaults_and_adds_transport_defaults tests/test_llm_provider_config.py::test_domi_provider_requires_openai_compatible_protocol tests/test_llm_provider_config.py::test_empty_domi_base_url_and_channel_are_rejected -q
```

Expected: all three tests pass.

- [ ] **Step 5: Commit settings work**

Run:

```bash
git add src/seektalent/config.py tests/test_llm_provider_config.py
git commit -m "feat: add Domi LLM provider settings"
```

Expected: commit succeeds and does not include unrelated Liepin files.

---

### Task 2: Build Domi OpenAI-Compatible Client

**Files:**
- Modify: `src/seektalent/llm.py`
- Test: `tests/test_llm_provider_config.py`

- [ ] **Step 1: Add failing LLM client tests**

Add these imports in `tests/test_llm_provider_config.py`:

```python
from seektalent.llm import resolve_text_llm_api_key
```

Add these tests near the current OpenAI client tests:

```python
def test_domi_stage_resolves_transport_without_changing_model_defaults() -> None:
    settings = make_settings(text_llm_provider_label="domi", domi_jwt="domi-test-jwt")

    stage = resolve_stage_model_config(settings, stage="requirements")

    assert stage.provider_label == "domi"
    assert stage.model_id == "deepseek-v4-pro"
    assert stage.base_url == "https://test-api-agent.hewa.cn/api/v1/runtime/llm-proxy/v1"
    assert stage.api_key == "domi-test-jwt"
    assert stage.domi_llm_channel == "seek_talent"
    assert resolve_text_llm_api_key(settings) == "domi-test-jwt"


def test_domi_provider_reuses_bailian_model_capabilities() -> None:
    stage = resolve_stage_model_config(
        make_settings(text_llm_provider_label="domi", domi_jwt="domi-test-jwt"),
        stage="workbench_conversation",
    )
    output_spec = build_output_spec(stage, _json_schema_capable_model(), dict)
    policy = build_provider_request_policy(stage)

    assert stage.provider_label == "domi"
    assert stage.model_id == "deepseek-v4-flash"
    assert stage.thinking_mode is True
    assert stage.reasoning_effort == "max"
    assert policy.extra_body == {"enable_thinking": True, "reasoning_effort": "max"}
    assert resolve_structured_output_mode(stage) == "native_json_schema"
    assert isinstance(output_spec, NativeOutput)


def test_domi_openai_client_uses_base_url_jwt_and_channel_query() -> None:
    stage = resolve_stage_model_config(
        make_settings(
            text_llm_provider_label="domi",
            domi_jwt="domi-test-jwt",
            domi_llm_base_url="https://test-api-agent.hewa.cn/api/v1/runtime/llm-proxy/v1/",
            domi_llm_channel="seek_talent",
        ),
        stage="requirements",
    )

    model = build_model(stage)

    assert isinstance(model, OpenAIChatModel)
    assert str(model.client.base_url) == "https://test-api-agent.hewa.cn/api/v1/runtime/llm-proxy/v1/"
    assert model.client.api_key == "domi-test-jwt"
    assert model.client.default_query == {"channel": "seek_talent"}
    assert model.client.max_retries == 2


def test_domi_openai_client_missing_jwt_fails_with_domi_message() -> None:
    stage = resolve_stage_model_config(
        make_settings(text_llm_provider_label="domi"),
        stage="requirements",
    )

    with pytest.raises(ValueError, match="SEEKTALENT_DOMI_JWT"):
        build_model(stage)
```

- [ ] **Step 2: Run LLM client tests and verify failure**

Run:

```bash
uv run pytest tests/test_llm_provider_config.py::test_domi_stage_resolves_transport_without_changing_model_defaults tests/test_llm_provider_config.py::test_domi_provider_reuses_bailian_model_capabilities tests/test_llm_provider_config.py::test_domi_openai_client_uses_base_url_jwt_and_channel_query tests/test_llm_provider_config.py::test_domi_openai_client_missing_jwt_fails_with_domi_message -q
```

Expected: fail because `ResolvedTextModelConfig` has no Domi channel and `build_model` does not branch for Domi.

- [ ] **Step 3: Extend resolved model config**

In `src/seektalent/llm.py`, extend `ResolvedTextModelConfig` with:

```python
    domi_llm_channel: str | None
```

Change `resolve_text_llm_base_url` to:

```python
def resolve_text_llm_base_url(settings: AppSettings) -> str:
    if settings.text_llm_provider_label == "domi":
        return settings.domi_llm_base_url.rstrip("/")
    if settings.text_llm_base_url_override:
        if settings.text_llm_protocol_family == "openai_chat_completions_compatible":
            return _normalize_openai_base_url(settings.text_llm_base_url_override) or ""
        return settings.text_llm_base_url_override.rstrip("/")
    key = (
        settings.text_llm_protocol_family,
        settings.text_llm_endpoint_kind,
        settings.text_llm_endpoint_region,
    )
    try:
        return TEXT_LLM_BASE_URLS[key]
    except KeyError as exc:
        raise ValueError(f"Unsupported text LLM endpoint mapping: {key!r}") from exc
```

Change `resolve_text_llm_api_key` to:

```python
def resolve_text_llm_api_key(settings: AppSettings) -> str | None:
    if settings.text_llm_provider_label == "domi":
        return settings.domi_jwt
    return settings.text_llm_api_key
```

Set the new field in `resolve_stage_model_config`:

```python
        domi_llm_channel=settings.domi_llm_channel if settings.text_llm_provider_label == "domi" else None,
```

- [ ] **Step 4: Reuse Bailian model capabilities for Domi**

Add this helper near `_resolve_text_llm_capability`:

```python
def _capability_provider_label(provider_label: str) -> str:
    return "bailian" if provider_label == "domi" else provider_label
```

Change `_resolve_text_llm_capability` to:

```python
def _resolve_text_llm_capability(config: ResolvedTextModelConfig) -> TextLLMCapability | None:
    return TEXT_LLM_CAPABILITIES.get(
        (
            _capability_provider_label(config.provider_label),
            config.protocol_family,
            config.endpoint_kind,
            config.endpoint_region,
            config.model_id,
        )
    )
```

- [ ] **Step 5: Add the Domi client branch**

Add this helper near `_build_resolved_model`:

```python
def _build_domi_openai_model(
    config: ResolvedTextModelConfig,
    *,
    provider_max_retries: int | None = None,
) -> Model:
    if not config.api_key:
        raise ValueError("SEEKTALENT_DOMI_JWT is required for Domi LLM proxy configuration.")
    if not config.domi_llm_channel:
        raise ValueError("SEEKTALENT_DOMI_LLM_CHANNEL is required for Domi LLM proxy configuration.")
    if provider_max_retries is not None:
        client = AsyncOpenAI(
            base_url=config.base_url,
            api_key=config.api_key,
            default_query={"channel": config.domi_llm_channel},
            http_client=_http_client(),
            max_retries=provider_max_retries,
        )
    else:
        client = AsyncOpenAI(
            base_url=config.base_url,
            api_key=config.api_key,
            default_query={"channel": config.domi_llm_channel},
            http_client=_http_client(),
        )
    return OpenAIChatModel(
        config.model_id,
        provider=OpenAIProvider(openai_client=client),
    )
```

Change the top of `_build_resolved_model` to:

```python
def _build_resolved_model(
    config: ResolvedTextModelConfig,
    *,
    provider_max_retries: int | None = None,
) -> Model:
    if config.provider_label == "domi":
        if config.protocol_family != "openai_chat_completions_compatible":
            raise ValueError("Domi LLM proxy supports only openai_chat_completions_compatible protocol.")
        return _build_domi_openai_model(config, provider_max_retries=provider_max_retries)
    if not config.api_key:
        raise ValueError(
            "SEEKTALENT_TEXT_LLM_API_KEY is required for canonical text LLM configuration."
        )
    if config.protocol_family == "openai_chat_completions_compatible":
```

Keep the existing Bailian OpenAI and Anthropic branches after this new prefix.

- [ ] **Step 6: Run LLM tests and verify pass**

Run:

```bash
uv run pytest tests/test_llm_provider_config.py::test_domi_stage_resolves_transport_without_changing_model_defaults tests/test_llm_provider_config.py::test_domi_provider_reuses_bailian_model_capabilities tests/test_llm_provider_config.py::test_domi_openai_client_uses_base_url_jwt_and_channel_query tests/test_llm_provider_config.py::test_domi_openai_client_missing_jwt_fails_with_domi_message tests/test_llm_provider_config.py::test_openai_path_builds_chat_model_not_responses_model tests/test_llm_provider_config.py::test_workbench_conversation_stage_uses_bailian_native_strict_schema -q
```

Expected: all selected tests pass.

- [ ] **Step 7: Commit LLM client work**

Run:

```bash
git add src/seektalent/llm.py tests/test_llm_provider_config.py
git commit -m "feat: route Domi LLM calls through proxy"
```

Expected: commit succeeds and only includes `src/seektalent/llm.py` plus the test file.

---

### Task 3: Wire Domi Credentials Through Product Env And CLI Preflight

**Files:**
- Modify: `src/seektalent/product_env.py`
- Modify: `src/seektalent/cli.py`
- Test: `tests/test_product_env.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Add failing product env test**

Add this test to `tests/test_product_env.py`:

```python
def test_build_workbench_command_env_passes_minimal_domi_llm_keys(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    env = build_workbench_command_env(
        {
            "HOME": str(home),
            "PATH": "/usr/bin",
            "SEEKTALENT_TEXT_LLM_PROVIDER_LABEL": "domi",
            "SEEKTALENT_DOMI_JWT": "domi-test-jwt",
            "SEEKTALENT_DOMI_LLM_BASE_URL": "https://test-api-agent.hewa.cn/api/v1/runtime/llm-proxy/v1",
            "SEEKTALENT_DOMI_LLM_CHANNEL": "seek_talent",
            "SEEKTALENT_TEXT_LLM_API_KEY": "must-not-be-required",
            "SEEKTALENT_CTS_TENANT_KEY": "must-not-leak",
        }
    )

    assert env["SEEKTALENT_TEXT_LLM_PROVIDER_LABEL"] == "domi"
    assert env["SEEKTALENT_DOMI_JWT"] == "domi-test-jwt"
    assert env["SEEKTALENT_DOMI_LLM_BASE_URL"] == "https://test-api-agent.hewa.cn/api/v1/runtime/llm-proxy/v1"
    assert env["SEEKTALENT_DOMI_LLM_CHANNEL"] == "seek_talent"
    assert env["SEEKTALENT_RUNTIME_MODE"] == "prod"
    assert env["SEEKTALENT_PROVIDER_NAME"] == "liepin"
    assert "SEEKTALENT_CTS_TENANT_KEY" not in env
```

- [ ] **Step 2: Add failing CLI preflight tests**

Add these tests near `test_workbench_command_requires_text_llm_key_before_launch` in `tests/test_cli.py`:

```python
def test_workbench_command_requires_domi_jwt_for_domi_provider(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls = []
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("SEEKTALENT_TEXT_LLM_PROVIDER_LABEL", "domi")
    monkeypatch.delenv("SEEKTALENT_DOMI_JWT", raising=False)
    monkeypatch.delenv("SEEKTALENT_TEXT_LLM_API_KEY", raising=False)

    def fake_run(argv, **_kwargs):
        calls.append(argv)
        raise AssertionError("workbench server should not launch without SEEKTALENT_DOMI_JWT")

    monkeypatch.setattr("seektalent.cli.subprocess.run", fake_run)

    assert main(["workbench"]) == 1

    captured = capsys.readouterr()
    assert "reason_code=seektalent_domi_jwt_missing" in captured.err
    assert "SEEKTALENT_DOMI_JWT" in captured.err
    assert "SEEKTALENT_TEXT_LLM_API_KEY" not in captured.err
    assert calls == []


def test_workbench_command_accepts_domi_jwt_without_text_llm_api_key(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SEEKTALENT_TEXT_LLM_PROVIDER_LABEL", "domi")
    monkeypatch.setenv("SEEKTALENT_DOMI_JWT", "domi-test-jwt")
    monkeypatch.delenv("SEEKTALENT_TEXT_LLM_API_KEY", raising=False)
    opencli_actions: list[str] = []
    launch_calls: list[tuple[list[str], dict[str, str] | None]] = []

    class Runtime:
        node = tmp_path / "node"
        opencli_main = tmp_path / "opencli-main.js"
        node_bin_dir = tmp_path

    class Completed:
        def __init__(self, *, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(argv, **kwargs):
        argv_list = list(argv)
        if "seektalent.providers.liepin.opencli_browser_cli" in argv_list:
            action = argv_list[-1]
            opencli_actions.append(action)
            return Completed(stdout=json.dumps({"ok": True, "action": action, "safeReasonCode": "configured"}))
        launch_calls.append((argv_list, kwargs.get("env")))
        return Completed()

    monkeypatch.setattr("seektalent.opencli_launcher.ensure_opencli_runtime", lambda: Runtime())
    monkeypatch.setattr("seektalent.cli._console_script_path", lambda name: name)
    monkeypatch.setattr("seektalent.cli.subprocess.run", fake_run)

    assert main(["workbench", "--port", "8123"]) == 0

    assert opencli_actions == ["recover_connection", "open_liepin_tab", "state"]
    assert launch_calls[0][0][0] == "seektalent-ui-api"
    assert launch_calls[0][1]["SEEKTALENT_TEXT_LLM_PROVIDER_LABEL"] == "domi"
    assert launch_calls[0][1]["SEEKTALENT_DOMI_JWT"] == "domi-test-jwt"
```

Add this doctor test near `test_doctor_json_success`:

```python
def test_doctor_json_success_for_domi_provider_without_text_llm_api_key(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "SEEKTALENT_TEXT_LLM_PROVIDER_LABEL=domi",
                "SEEKTALENT_DOMI_JWT=domi-test-jwt",
                "SEEKTALENT_DOMI_LLM_BASE_URL=https://test-api-agent.hewa.cn/api/v1/runtime/llm-proxy/v1",
                "SEEKTALENT_DOMI_LLM_CHANNEL=seek_talent",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    assert main(["doctor", "--env-file", str(env_file), "--output-dir", str(tmp_path / "runs"), "--json"]) == 0

    output = capsys.readouterr().out
    assert "domi-test-jwt" not in output
    payload = json.loads(output)
    provider_check = next(item for item in payload["checks"] if item["name"] == "provider_credentials")
    assert provider_check["ok"] is True
```

- [ ] **Step 3: Run env and CLI tests and verify failure**

Run:

```bash
uv run pytest tests/test_product_env.py::test_build_workbench_command_env_passes_minimal_domi_llm_keys tests/test_cli.py::test_workbench_command_requires_domi_jwt_for_domi_provider tests/test_cli.py::test_workbench_command_accepts_domi_jwt_without_text_llm_api_key tests/test_cli.py::test_doctor_json_success_for_domi_provider_without_text_llm_api_key -q
```

Expected: fail because Domi variables are not passed and preflight still requires `SEEKTALENT_TEXT_LLM_API_KEY`.

- [ ] **Step 4: Update product env passthrough**

In `src/seektalent/product_env.py`, change `PRODUCT_USER_ENV_VARS` to:

```python
PRODUCT_USER_ENV_VARS = frozenset(
    {
        "SEEKTALENT_TEXT_LLM_API_KEY",
        "SEEKTALENT_TEXT_LLM_PROVIDER_LABEL",
        "SEEKTALENT_DOMI_JWT",
        "SEEKTALENT_DOMI_LLM_BASE_URL",
        "SEEKTALENT_DOMI_LLM_CHANNEL",
    }
)
```

No other runtime, CTS, or Liepin user variables should be added.

- [ ] **Step 5: Update CLI provider credential selection**

In `src/seektalent/cli.py`, replace `PROVIDER_ENV_VAR_BY_PROTOCOL_FAMILY` with:

```python
PROVIDER_ENV_VAR_BY_PROVIDER_LABEL = {
    "bailian": "SEEKTALENT_TEXT_LLM_API_KEY",
    "domi": "SEEKTALENT_DOMI_JWT",
}
```

Update `_required_provider_env_vars` to:

```python
def _required_provider_env_vars(settings: AppSettings) -> list[str]:
    if settings.text_llm_provider_label == "domi":
        return [] if settings.domi_jwt else ["SEEKTALENT_DOMI_JWT"]
    if settings.text_llm_api_key:
        return []
    env_var = PROVIDER_ENV_VAR_BY_PROVIDER_LABEL.get(settings.text_llm_provider_label)
    if env_var is None:
        return []
    return [env_var]
```

If any tests import `PROVIDER_ENV_VAR_BY_PROTOCOL_FAMILY`, update them to use the new name or remove the import.

- [ ] **Step 6: Update workbench startup preflight**

In `_workbench_startup_preflight`, replace the first credential check with:

```python
def _workbench_startup_preflight(env: Mapping[str, str]) -> bool:
    provider_label = str(env.get("SEEKTALENT_TEXT_LLM_PROVIDER_LABEL") or "bailian").strip().lower() or "bailian"
    if provider_label == "domi":
        if not str(env.get("SEEKTALENT_DOMI_JWT") or "").strip():
            _print_workbench_reason(
                "seektalent_domi_jwt_missing",
                "SEEKTALENT_DOMI_JWT is required for Domi LLM proxy mode.",
            )
            return False
    elif not str(env.get("SEEKTALENT_TEXT_LLM_API_KEY") or "").strip():
        _print_workbench_reason(
            "seektalent_text_llm_api_key_missing",
            "SEEKTALENT_TEXT_LLM_API_KEY is required. Set it in the shell or ~/.seektalent/.env.",
        )
        return False
```

Keep the existing OpenCLI bootstrap and Liepin preflight code after this credential block.

- [ ] **Step 7: Run env and CLI tests and verify pass**

Run:

```bash
uv run pytest tests/test_product_env.py tests/test_cli.py::test_workbench_command_requires_text_llm_key_before_launch tests/test_cli.py::test_workbench_command_requires_domi_jwt_for_domi_provider tests/test_cli.py::test_workbench_command_accepts_domi_jwt_without_text_llm_api_key tests/test_cli.py::test_doctor_json_success tests/test_cli.py::test_doctor_json_success_for_domi_provider_without_text_llm_api_key -q
```

Expected: selected tests pass.

- [ ] **Step 8: Commit product env and CLI work**

Run:

```bash
git add src/seektalent/product_env.py src/seektalent/cli.py tests/test_product_env.py tests/test_cli.py
git commit -m "feat: support Domi credentials in prod workbench"
```

Expected: commit succeeds and does not stage unrelated Liepin files.

---

### Task 4: Add Domi Runtime Smoke Script

**Files:**
- Create: `scripts/smoke-domi-runtime.sh`
- Create: `tests/test_domi_runtime_smoke_script.py`

- [ ] **Step 1: Add failing script tests**

Create `tests/test_domi_runtime_smoke_script.py` with:

```python
from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "smoke-domi-runtime.sh"


def test_domi_runtime_smoke_script_has_expected_contract() -> None:
    text = SCRIPT.read_text(encoding="utf-8")

    assert "/Applications/Domi.app/Contents/Resources/extraResources/python/runtime/bin/python" in text
    assert ".seektalent/domi-runtime" in text
    assert "SEEKTALENT_DOMI_JWT" in text
    assert "SEEKTALENT_TEXT_LLM_PROVIDER_LABEL=domi" in text
    assert "SEEKTALENT_DOMI_LLM_BASE_URL" in text
    assert "SEEKTALENT_DOMI_LLM_CHANNEL" in text
    assert "test-api-agent.hewa.cn" in text
    assert "seektalent doctor" in text
    assert "workbench --port" in text
    assert "seektalent-opencli" in text


def test_domi_runtime_smoke_script_passes_bash_syntax_check() -> None:
    completed = subprocess.run(["bash", "-n", str(SCRIPT)], check=False, capture_output=True, text=True)

    assert completed.returncode == 0, completed.stderr
```

- [ ] **Step 2: Run script tests and verify failure**

Run:

```bash
uv run pytest tests/test_domi_runtime_smoke_script.py -q
```

Expected: fail because the script file does not exist.

- [ ] **Step 3: Create the smoke script**

Create `scripts/smoke-domi-runtime.sh` with this content:

```bash
#!/usr/bin/env bash
set -euo pipefail

DOMI_PYTHON="${DOMI_PYTHON:-/Applications/Domi.app/Contents/Resources/extraResources/python/runtime/bin/python}"
DOMI_RUNTIME_ROOT="${SEEKTALENT_DOMI_RUNTIME_ROOT:-${HOME}/.seektalent/domi-runtime}"
DOMI_VENV="${DOMI_RUNTIME_ROOT}/venv"
DOMI_DIST_DIR="${DOMI_RUNTIME_ROOT}/dist"
DOMI_WORKBENCH_PORT="${SEEKTALENT_DOMI_SMOKE_PORT:-8011}"
SEEKTALENT_DOMI_LLM_BASE_URL="${SEEKTALENT_DOMI_LLM_BASE_URL:-https://test-api-agent.hewa.cn/api/v1/runtime/llm-proxy/v1}"
SEEKTALENT_DOMI_LLM_CHANNEL="${SEEKTALENT_DOMI_LLM_CHANNEL:-seek_talent}"
SEEKTALENT_DOMI_SMOKE_MODEL="${SEEKTALENT_DOMI_SMOKE_MODEL:-deepseek-v4-flash}"
WORKBENCH_LOG="${DOMI_RUNTIME_ROOT}/workbench.log"

if [[ -z "${SEEKTALENT_DOMI_JWT:-}" ]]; then
  echo "reason_code=seektalent_domi_jwt_missing SEEKTALENT_DOMI_JWT is required for Domi runtime smoke." >&2
  exit 1
fi

if [[ ! -x "${DOMI_PYTHON}" ]]; then
  echo "reason_code=domi_python_missing Domi Python runtime is not executable: ${DOMI_PYTHON}" >&2
  exit 1
fi

mkdir -p "${DOMI_RUNTIME_ROOT}" "${DOMI_DIST_DIR}"

echo "Domi Python: ${DOMI_PYTHON}" >&2
"${DOMI_PYTHON}" -m venv "${DOMI_VENV}"

VENV_PYTHON="${DOMI_VENV}/bin/python"
VENV_PIP="${DOMI_VENV}/bin/pip"
SEEKTALENT_BIN="${DOMI_VENV}/bin/seektalent"
SEEKTALENT_OPENCLI_BIN="${DOMI_VENV}/bin/seektalent-opencli"

"${VENV_PYTHON}" -m pip install --upgrade pip build
"${VENV_PYTHON}" -m build --wheel --outdir "${DOMI_DIST_DIR}" .
"${VENV_PIP}" install --force-reinstall "${DOMI_DIST_DIR}"/seektalent-*.whl

export SEEKTALENT_TEXT_LLM_PROVIDER_LABEL=domi
export SEEKTALENT_DOMI_JWT
export SEEKTALENT_DOMI_LLM_BASE_URL
export SEEKTALENT_DOMI_LLM_CHANNEL
export SEEKTALENT_DOMI_SMOKE_MODEL
export SEEKTALENT_RUNTIME_MODE=prod

echo "Running seektalent doctor in Domi provider mode" >&2
"${SEEKTALENT_BIN}" doctor --env-file /dev/null --json > "${DOMI_RUNTIME_ROOT}/doctor.json"

echo "Running Domi LLM proxy hello" >&2
"${VENV_PYTHON}" - <<'PY'
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

base_url = os.environ["SEEKTALENT_DOMI_LLM_BASE_URL"].rstrip("/")
channel = os.environ["SEEKTALENT_DOMI_LLM_CHANNEL"]
token = os.environ["SEEKTALENT_DOMI_JWT"]
model = os.environ["SEEKTALENT_DOMI_SMOKE_MODEL"]
url = f"{base_url}/chat/completions?{urllib.parse.urlencode({'channel': channel})}"
payload = {
    "model": model,
    "messages": [{"role": "user", "content": "你好"}],
    "stream": False,
}
request = urllib.request.Request(
    url,
    data=json.dumps(payload).encode("utf-8"),
    headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    },
    method="POST",
)
try:
    with urllib.request.urlopen(request, timeout=60) as response:
        body = response.read(4096).decode("utf-8", errors="replace")
        print(json.dumps({"status": response.status, "body_prefix": body[:200]}, ensure_ascii=False))
except urllib.error.HTTPError as exc:
    detail = exc.read(4096).decode("utf-8", errors="replace")
    print(json.dumps({"status": exc.code, "body_prefix": detail[:200]}, ensure_ascii=False), file=sys.stderr)
    raise SystemExit(1)
PY

echo "Restarting OpenCLI daemon" >&2
if ! "${SEEKTALENT_OPENCLI_BIN}" daemon restart > "${DOMI_RUNTIME_ROOT}/opencli-restart.txt" 2>&1; then
  echo "reason_code=domi_opencli_restart_failed OpenCLI daemon restart failed; see ${DOMI_RUNTIME_ROOT}/opencli-restart.txt" >&2
  exit 1
fi

echo "Checking OpenCLI daemon status" >&2
if ! "${SEEKTALENT_OPENCLI_BIN}" daemon status > "${DOMI_RUNTIME_ROOT}/opencli-status.txt" 2>&1; then
  echo "reason_code=domi_opencli_status_unavailable OpenCLI status check failed; see ${DOMI_RUNTIME_ROOT}/opencli-status.txt" >&2
  exit 1
fi
if ! grep -q "Extension: connected" "${DOMI_RUNTIME_ROOT}/opencli-status.txt"; then
  echo "reason_code=domi_opencli_extension_disconnected OpenCLI extension is not connected; see ${DOMI_RUNTIME_ROOT}/opencli-status.txt" >&2
  exit 1
fi

echo "Starting packaged Workbench on port ${DOMI_WORKBENCH_PORT}" >&2
"${SEEKTALENT_BIN}" workbench --port "${DOMI_WORKBENCH_PORT}" > "${WORKBENCH_LOG}" 2>&1 &
WORKBENCH_PID="$!"

cleanup() {
  if kill -0 "${WORKBENCH_PID}" 2>/dev/null; then
    kill "${WORKBENCH_PID}" 2>/dev/null || true
    wait "${WORKBENCH_PID}" 2>/dev/null || true
  fi
}
trap cleanup EXIT

for _ in $(seq 1 60); do
  if "${VENV_PYTHON}" - <<PY
import urllib.request
urllib.request.urlopen("http://127.0.0.1:${DOMI_WORKBENCH_PORT}/openapi.json", timeout=1).read(1)
PY
  then
    echo "Domi runtime smoke passed. Workbench URL: http://127.0.0.1:${DOMI_WORKBENCH_PORT}/" >&2
    exit 0
  fi
  if ! kill -0 "${WORKBENCH_PID}" 2>/dev/null; then
    echo "reason_code=domi_workbench_exited Workbench exited before openapi.json became ready; see ${WORKBENCH_LOG}" >&2
    exit 1
  fi
  sleep 1
done

echo "reason_code=domi_workbench_startup_timeout Workbench did not become ready; see ${WORKBENCH_LOG}" >&2
exit 1
```

- [ ] **Step 4: Make script executable**

Run:

```bash
chmod +x scripts/smoke-domi-runtime.sh
```

Expected: file mode becomes executable.

- [ ] **Step 5: Run script tests and verify pass**

Run:

```bash
uv run pytest tests/test_domi_runtime_smoke_script.py -q
bash -n scripts/smoke-domi-runtime.sh
```

Expected: pytest passes and `bash -n` exits 0.

- [ ] **Step 6: Commit smoke script work**

Run:

```bash
git add scripts/smoke-domi-runtime.sh tests/test_domi_runtime_smoke_script.py
git commit -m "test: add Domi runtime smoke script"
```

Expected: commit succeeds.

---

### Task 5: Document And Verify The Slice

**Files:**
- Modify: `docs/development.md`
- Verify: `src/seektalent/config.py`
- Verify: `src/seektalent/llm.py`
- Verify: `src/seektalent/product_env.py`
- Verify: `src/seektalent/cli.py`
- Verify: `tests/test_llm_provider_config.py`
- Verify: `tests/test_product_env.py`
- Verify: `tests/test_cli.py`
- Verify: `tests/test_domi_runtime_smoke_script.py`
- Verify: `scripts/smoke-domi-runtime.sh`

- [ ] **Step 1: Add Domi smoke documentation**

Add this section after the packaged Workbench build instructions in `docs/development.md`:

````markdown
## Domi Runtime Smoke

Use this smoke only for validating the packaged Workbench shape inside the Domi-provided runtime on a local Mac with Domi installed.

Required input:

```bash
export SEEKTALENT_DOMI_JWT="<domi jwt>"
```

Run:

```bash
scripts/smoke-domi-runtime.sh
```

Defaults:

- Domi Python: `/Applications/Domi.app/Contents/Resources/extraResources/python/runtime/bin/python`
- isolated install root: `~/.seektalent/domi-runtime`
- Domi LLM proxy: `https://test-api-agent.hewa.cn/api/v1/runtime/llm-proxy/v1`
- Domi channel: `seek_talent`

The smoke builds the current repository wheel, installs it into the isolated Domi runtime venv, runs `seektalent doctor`, sends a Domi LLM proxy hello request, checks OpenCLI daemon status, and starts the packaged Workbench long enough to verify `/openapi.json`.

It does not read Domi Electron storage by default and does not run a complete live Liepin recruiting workflow.
````

- [ ] **Step 2: Run focused tests**

Run:

```bash
uv run pytest \
  tests/test_llm_provider_config.py \
  tests/test_product_env.py \
  tests/test_cli.py::test_workbench_command_requires_text_llm_key_before_launch \
  tests/test_cli.py::test_workbench_command_requires_domi_jwt_for_domi_provider \
  tests/test_cli.py::test_workbench_command_accepts_domi_jwt_without_text_llm_api_key \
  tests/test_cli.py::test_doctor_json_success \
  tests/test_cli.py::test_doctor_json_success_for_domi_provider_without_text_llm_api_key \
  tests/test_domi_runtime_smoke_script.py \
  -q
```

Expected: all selected tests pass.

- [ ] **Step 3: Run lint and shell syntax checks**

Run:

```bash
uv run ruff check \
  src/seektalent/config.py \
  src/seektalent/llm.py \
  src/seektalent/product_env.py \
  src/seektalent/cli.py \
  tests/test_llm_provider_config.py \
  tests/test_product_env.py \
  tests/test_cli.py \
  tests/test_domi_runtime_smoke_script.py
bash -n scripts/smoke-domi-runtime.sh
```

Expected: Ruff passes and `bash -n` exits 0.

- [ ] **Step 4: Run Domi runtime smoke manually if JWT is available**

Run only when `SEEKTALENT_DOMI_JWT` is available in the shell:

```bash
export SEEKTALENT_DOMI_JWT="<domi jwt>"
scripts/smoke-domi-runtime.sh
```

Expected when the environment is fully ready:

- Domi Python creates `~/.seektalent/domi-runtime`.
- Current repository wheel installs into the venv.
- `~/.seektalent/domi-runtime/doctor.json` is written.
- Domi LLM proxy hello returns HTTP 200.
- OpenCLI status check writes `~/.seektalent/domi-runtime/opencli-status.txt`.
- Packaged Workbench becomes ready at `http://127.0.0.1:8011/`.

Expected when OpenCLI extension is not connected:

- script exits nonzero with an OpenCLI readiness reason;
- no JWT appears in terminal output or log files.

- [ ] **Step 5: Inspect staged diff before final commit**

Run:

```bash
git status --short --branch
git diff --stat
git diff -- docs/development.md
```

Expected: only Domi LLM/runtime-smoke files are changed plus the pre-existing unrelated Liepin files remain unstaged.

- [ ] **Step 6: Commit docs and verification updates**

Run:

```bash
git add docs/development.md
git commit -m "docs: document Domi runtime smoke"
```

Expected: commit succeeds if `docs/development.md` changed. If the documentation was already committed with Task 4, skip this commit and record the reason in the final implementation summary.

---

## Final Verification

Run:

```bash
uv run pytest \
  tests/test_llm_provider_config.py \
  tests/test_product_env.py \
  tests/test_cli.py::test_workbench_command_requires_text_llm_key_before_launch \
  tests/test_cli.py::test_workbench_command_requires_domi_jwt_for_domi_provider \
  tests/test_cli.py::test_workbench_command_accepts_domi_jwt_without_text_llm_api_key \
  tests/test_cli.py::test_doctor_json_success \
  tests/test_cli.py::test_doctor_json_success_for_domi_provider_without_text_llm_api_key \
  tests/test_domi_runtime_smoke_script.py \
  -q
uv run ruff check \
  src/seektalent/config.py \
  src/seektalent/llm.py \
  src/seektalent/product_env.py \
  src/seektalent/cli.py \
  tests/test_llm_provider_config.py \
  tests/test_product_env.py \
  tests/test_cli.py \
  tests/test_domi_runtime_smoke_script.py
bash -n scripts/smoke-domi-runtime.sh
git status --short --branch
```

Expected:

- pytest passes;
- ruff passes;
- Bash syntax check passes;
- git status shows only the known pre-existing unrelated Liepin dirty files, unless the executor intentionally leaves implementation commits ahead of origin.

Manual smoke with a real JWT is recommended before pushing implementation:

```bash
export SEEKTALENT_DOMI_JWT="<domi jwt>"
scripts/smoke-domi-runtime.sh
```
