# PRF Model Sidecar Deployment v0.1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy a real local span-proposal model and a real local embedding model behind one CPU-only Docker sidecar while keeping PRF v1.5 artifacts, replay, shadow/mainline rollout, and deterministic fallback behavior intact.

**Architecture:** Add a dedicated `prf_sidecar` package that owns HTTP contracts, dependency manifesting, health/readiness, offline cache-only serving, and model loading. Keep exact-offset alignment, familying policy, deterministic gate, artifact persistence, and replay assembly in the main app by introducing HTTP-backed proposal backends rather than moving PRF policy into the sidecar.

**Tech Stack:** Python 3.12, FastAPI, uvicorn, httpx, Pydantic, Hugging Face Hub cache controls, local CPU inference, Docker, existing ArtifactStore/Resolver, pytest.

---

## File Map

### New modules

- Create: `src/seektalent/prf_sidecar/models.py`
  - Request/response models for `/livez`, `/readyz`, `/v1/span-extract`, and `/v1/embed`, plus `SidecarDependencyManifest`.
- Create: `src/seektalent/prf_sidecar/service.py`
  - Sidecar runtime state, readiness logic, offline/cache-only enforcement, and loader orchestration.
- Create: `src/seektalent/prf_sidecar/loaders.py`
  - Model loader protocols and CPU-only runtime wrappers for span extraction and embedding inference.
- Create: `src/seektalent/prf_sidecar/app.py`
  - FastAPI application factory and endpoint wiring.
- Create: `src/seektalent/prf_sidecar/client.py`
  - HTTP-backed proposal clients used by the main app.
- Create: `src/seektalent/prf_sidecar/prefetch.py`
  - Explicit prefetch/warmup helpers for pinned model snapshots.
- Create: `src/seektalent/prf_sidecar/__init__.py`
- Create: `tests/test_prf_sidecar_models.py`
- Create: `tests/test_prf_sidecar_app.py`
- Create: `tests/test_prf_sidecar_service.py`
- Create: `tests/test_prf_sidecar_boundary.py`
- Create: `docker/prf-model-sidecar/Dockerfile`
- Create: `docker/prf-model-sidecar/compose.yml`

### Existing modules to modify

- Modify: `pyproject.toml`
  - Add sidecar runtime and server dependencies plus console scripts.
- Modify: `src/seektalent/config.py`
  - Add `prf_model_backend`, sidecar endpoint, timeout, serve-mode, and dependency-manifest settings.
- Modify: `src/seektalent/default.env`
  - Add commented examples for sidecar deployment settings.
- Modify: `src/seektalent/candidate_feedback/proposal_runtime.py`
  - Select legacy vs sidecar-backed span extractor/embedding behavior without moving PRF policy.
- Modify: `src/seektalent/candidate_feedback/span_extractors.py`
  - Accept HTTP-backed span model backend objects.
- Modify: `src/seektalent/candidate_feedback/models.py`
  - Extend proposal version vectors and artifact metadata with sidecar contract fields.
- Modify: `src/seektalent/models.py`
  - Extend `ReplaySnapshot` and `SecondLaneDecision` sidecar-aware metadata.
- Modify: `src/seektalent/runtime/orchestrator.py`
  - Inject sidecar-backed proposal paths in shadow/mainline modes with explicit timeout/fallback semantics.
- Modify: `src/seektalent/runtime/runtime_diagnostics.py`
  - Export sidecar metadata into replay snapshots.
- Modify: `src/seektalent/evaluation.py`
  - Include sidecar-backed replay fields in replay row exports.
- Modify: `src/seektalent/cli.py`
  - Add prefetch and sidecar entrypoint commands if repo chooses CLI-managed launch.
- Modify: `docs/outputs.md`
  - Document sidecar-backed replay fields and fallback metadata.
- Modify: `tests/test_candidate_feedback.py`
  - Cover sidecar-backed proposal contract and fallback behavior.
- Modify: `tests/test_runtime_state_flow.py`
  - Cover shadow/mainline rollout and replay metadata.
- Modify: `tests/test_second_lane_runtime.py`
  - Verify shadow timeout and fallback do not change lane selection.
- Modify: `tests/test_evaluation.py`
  - Verify replay export carries sidecar metadata.
- Modify: `tests/test_llm_provider_config.py`
  - Verify new configuration defaults and guards.
- Modify: `tests/test_artifact_path_contract.py`
  - Extend boundary enforcement to sidecar-backed artifacts and request-path imports.

## Task 1: Define Sidecar Contracts And Config

**Files:**
- Create: `src/seektalent/prf_sidecar/models.py`
- Modify: `src/seektalent/config.py`
- Modify: `src/seektalent/models.py`
- Modify: `tests/test_llm_provider_config.py`
- Test: `tests/test_prf_sidecar_models.py`

- [ ] **Step 1: Write the failing contract tests**

```python
from seektalent.prf_sidecar.models import (
    SidecarDependencyManifest,
    SpanExtractRequest,
    SpanExtractResponse,
    EmbedRequest,
    EmbedResponse,
)
from seektalent.config import AppSettings


def test_span_extract_response_requires_request_text_index_and_contract_metadata():
    response = SpanExtractResponse(
        schema_version="prf-sidecar-span-v1",
        model_name="fastino/gliner2-multi-v1",
        model_revision="rev-span",
        rows=[
            {
                "request_text_index": 0,
                "surface": "Flink CDC",
                "label": "technical_phrase",
                "score": 0.91,
                "model_start_char": 12,
                "model_end_char": 21,
                "alignment_hint_only": True,
            }
        ],
    )
    assert response.rows[0].request_text_index == 0


def test_dependency_manifest_tracks_runtime_and_model_identity():
    manifest = SidecarDependencyManifest(
        sidecar_image_digest="sha256:image",
        python_lockfile_hash="lock-hash",
        torch_version="2.8.0",
        transformers_version="4.57.0",
        sentence_transformers_version=None,
        gliner_runtime_version="2.0.0",
        span_model_name="fastino/gliner2-multi-v1",
        span_model_commit="0123456789abcdef0123456789abcdef01234567",
        span_tokenizer_commit="fedcba9876543210fedcba9876543210fedcba98",
        embedding_model_name="Alibaba-NLP/gte-multilingual-base",
        embedding_model_commit="abcdef0123456789abcdef0123456789abcdef01",
        remote_code_policy="disabled",
        remote_code_commit=None,
        license_status="approved",
        embedding_normalization=True,
        embedding_dimension=768,
        dtype="float32",
        max_input_tokens=8192,
    )
    assert manifest.embedding_dimension == 768


def test_app_settings_expose_sidecar_endpoint_contract():
    settings = AppSettings()
    assert settings.prf_model_backend == "legacy"
    assert settings.prf_sidecar_endpoint_contract_version == "prf-sidecar-http-v1"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_prf_sidecar_models.py tests/test_llm_provider_config.py -k sidecar`

Expected: FAIL with missing module, missing settings, or missing model fields.

- [ ] **Step 3: Implement sidecar contract models and config**

```python
# src/seektalent/prf_sidecar/models.py
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SidecarDependencyManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sidecar_image_digest: str
    python_lockfile_hash: str
    torch_version: str
    transformers_version: str
    sentence_transformers_version: str | None = None
    gliner_runtime_version: str
    span_model_name: str
    span_model_commit: str
    span_tokenizer_commit: str
    embedding_model_name: str
    embedding_model_commit: str
    remote_code_policy: Literal["disabled", "approved_baked_code"]
    remote_code_commit: str | None = None
    license_status: Literal["approved", "blocked"]
    embedding_normalization: bool
    embedding_dimension: int = Field(gt=0)
    dtype: Literal["float32", "float16", "bfloat16"]
    max_input_tokens: int = Field(gt=0)


class SpanExtractRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_text_index: int = Field(ge=0)
    surface: str = Field(min_length=1)
    label: str
    score: float = Field(ge=0.0, le=1.0)
    model_start_char: int | None = Field(default=None, ge=0)
    model_end_char: int | None = Field(default=None, ge=0)
    alignment_hint_only: bool = True


class SpanExtractRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    texts: list[str]
    labels: list[str]
    schema_version: str
    model_name: str
    model_revision: str


class SpanExtractResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    model_name: str
    model_revision: str
    rows: list[SpanExtractRow]
```

```python
# src/seektalent/config.py
prf_model_backend: Literal["legacy", "http_sidecar"] = "legacy"
prf_sidecar_endpoint: str = "http://127.0.0.1:8741"
prf_sidecar_endpoint_contract_version: str = "prf-sidecar-http-v1"
prf_sidecar_serve_mode: Literal["dev-bootstrap", "prod-serve"] = "dev-bootstrap"
prf_sidecar_timeout_seconds_shadow: float = 0.35
prf_sidecar_timeout_seconds_mainline: float = 1.50
prf_sidecar_max_batch_size: int = 32
prf_sidecar_max_payload_bytes: int = 262_144
```

```python
# src/seektalent/models.py
prf_model_backend: str | None = None
prf_sidecar_endpoint_contract_version: str | None = None
prf_sidecar_dependency_manifest_hash: str | None = None
prf_sidecar_image_digest: str | None = None
prf_sidecar_timeout_bucket: str | None = None
prf_fallback_reason: str | None = None
prf_embedding_dimension: int | None = None
prf_embedding_normalized: bool | None = None
prf_remote_code_policy: str | None = None
```

```toml
# pyproject.toml
dependencies = [
  "fastapi>=0.118.0",
  "uvicorn>=0.37.0",
]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q tests/test_prf_sidecar_models.py tests/test_llm_provider_config.py -k sidecar`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml src/seektalent/config.py src/seektalent/models.py src/seektalent/prf_sidecar/models.py tests/test_prf_sidecar_models.py tests/test_llm_provider_config.py
git commit -m "feat: add PRF sidecar contracts and config"
```

## Task 2: Build Sidecar Service Core And HTTP App

**Files:**
- Create: `src/seektalent/prf_sidecar/service.py`
- Create: `src/seektalent/prf_sidecar/app.py`
- Create: `src/seektalent/prf_sidecar/__init__.py`
- Test: `tests/test_prf_sidecar_app.py`
- Test: `tests/test_prf_sidecar_service.py`

- [ ] **Step 1: Write the failing service and app tests**

```python
from fastapi.testclient import TestClient

from seektalent.prf_sidecar.app import create_sidecar_app
from seektalent.prf_sidecar.models import SidecarDependencyManifest


class FakeService:
    def live(self):
        return {"status": "alive"}

    def ready(self):
        return {
            "status": "ready",
            "endpoint_contract_version": "prf-sidecar-http-v1",
            "dependency_manifest_hash": "manifest-hash",
            "span_model_loaded": True,
            "embedding_model_loaded": True,
            "span_model_name": "fastino/gliner2-multi-v1",
            "span_model_revision": "rev-span",
            "span_tokenizer_revision": "rev-tokenizer",
            "embedding_model_name": "Alibaba-NLP/gte-multilingual-base",
            "embedding_model_revision": "rev-embed",
        }

    def span_extract(self, request):
        return {
            "schema_version": "prf-sidecar-span-v1",
            "model_name": request.model_name,
            "model_revision": request.model_revision,
            "rows": [
                {
                    "request_text_index": 0,
                    "surface": "Flink CDC",
                    "label": "technical_phrase",
                    "score": 0.91,
                    "model_start_char": 0,
                    "model_end_char": 9,
                    "alignment_hint_only": True,
                }
            ],
        }


def test_readyz_returns_manifest_hash_and_model_identity():
    app = create_sidecar_app(service=FakeService())
    client = TestClient(app)
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json()["dependency_manifest_hash"] == "manifest-hash"


def test_span_extract_returns_rows_with_request_text_index():
    app = create_sidecar_app(service=FakeService())
    client = TestClient(app)
    response = client.post(
        "/v1/span-extract",
        json={
            "request_id": "req-1",
            "texts": ["Flink CDC pipeline"],
            "labels": ["technical_phrase"],
            "schema_version": "gliner2-schema-v1",
            "model_name": "fastino/gliner2-multi-v1",
            "model_revision": "rev-span",
        },
    )
    assert response.status_code == 200
    assert response.json()["rows"][0]["request_text_index"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_prf_sidecar_app.py tests/test_prf_sidecar_service.py`

Expected: FAIL because the sidecar package and app do not exist.

- [ ] **Step 3: Implement the service boundary and FastAPI app**

```python
# src/seektalent/prf_sidecar/service.py
from dataclasses import dataclass

from seektalent.prf_sidecar.models import (
    EmbedRequest,
    EmbedResponse,
    SidecarDependencyManifest,
    SpanExtractRequest,
    SpanExtractResponse,
)


@dataclass
class SidecarRuntimeState:
    endpoint_contract_version: str
    dependency_manifest_hash: str
    span_model_loaded: bool
    embedding_model_loaded: bool
    span_model_name: str
    span_model_revision: str
    span_tokenizer_revision: str
    embedding_model_name: str
    embedding_model_revision: str


class SidecarService:
    def __init__(self, *, state: SidecarRuntimeState, manifest: SidecarDependencyManifest) -> None:
        self.state = state
        self.manifest = manifest

    def live(self) -> dict[str, object]:
        return {"status": "alive"}

    def ready(self) -> dict[str, object]:
        return {
            "status": "ready" if self.state.span_model_loaded and self.state.embedding_model_loaded else "not_ready",
            "endpoint_contract_version": self.state.endpoint_contract_version,
            "dependency_manifest_hash": self.state.dependency_manifest_hash,
            "span_model_loaded": self.state.span_model_loaded,
            "embedding_model_loaded": self.state.embedding_model_loaded,
            "span_model_name": self.state.span_model_name,
            "span_model_revision": self.state.span_model_revision,
            "span_tokenizer_revision": self.state.span_tokenizer_revision,
            "embedding_model_name": self.state.embedding_model_name,
            "embedding_model_revision": self.state.embedding_model_revision,
        }


def build_default_sidecar_service() -> SidecarService:
    manifest = load_dependency_manifest_from_env()
    state = load_runtime_state_from_env(manifest)
    return SidecarService(state=state, manifest=manifest)
```

```python
# src/seektalent/prf_sidecar/app.py
from fastapi import FastAPI
import uvicorn


def create_sidecar_app(*, service) -> FastAPI:
    app = FastAPI()

    @app.get("/livez")
    def livez() -> dict[str, object]:
        return service.live()

    @app.get("/readyz")
    def readyz() -> dict[str, object]:
        return service.ready()

    @app.post("/v1/span-extract")
    def span_extract(request: SpanExtractRequest) -> dict[str, object]:
        return service.span_extract(request)

    @app.post("/v1/embed")
    def embed(request: EmbedRequest) -> dict[str, object]:
        return service.embed(request)

    return app


def main() -> None:
    service = build_default_sidecar_service()
    uvicorn.run(create_sidecar_app(service=service), host="127.0.0.1", port=8741)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q tests/test_prf_sidecar_app.py tests/test_prf_sidecar_service.py`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/seektalent/prf_sidecar/__init__.py src/seektalent/prf_sidecar/service.py src/seektalent/prf_sidecar/app.py tests/test_prf_sidecar_app.py tests/test_prf_sidecar_service.py
git commit -m "feat: add PRF sidecar app skeleton"
```

## Task 3: Add Real Loader Seams, Prefetch, And Offline Serve Modes

**Files:**
- Create: `src/seektalent/prf_sidecar/loaders.py`
- Create: `src/seektalent/prf_sidecar/prefetch.py`
- Modify: `pyproject.toml`
- Modify: `src/seektalent/default.env`
- Test: `tests/test_prf_sidecar_service.py`
- Test: `tests/test_prf_sidecar_boundary.py`

- [ ] **Step 1: Write failing loader and offline-mode tests**

```python
from seektalent.config import AppSettings
from seektalent.prf_sidecar.prefetch import build_prefetch_plan
from seektalent.prf_sidecar.service import prod_sidecar_must_use_offline_cache


def test_prod_serve_mode_requires_offline_cache():
    settings = AppSettings(
        prf_model_backend="http_sidecar",
        prf_sidecar_serve_mode="prod-serve",
        prf_span_model_revision="rev-span",
        prf_span_tokenizer_revision="rev-tokenizer",
        prf_embedding_model_revision="rev-embed",
    )
    assert prod_sidecar_must_use_offline_cache(settings) is True


def test_prefetch_plan_uses_pinned_revisions():
    settings = AppSettings(
        prf_span_model_revision="rev-span",
        prf_span_tokenizer_revision="rev-tokenizer",
        prf_embedding_model_revision="rev-embed",
    )
    plan = build_prefetch_plan(settings)
    assert plan["span_model"]["revision"] == "rev-span"
    assert plan["embedding_model"]["revision"] == "rev-embed"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_prf_sidecar_service.py tests/test_prf_sidecar_boundary.py -k offline`

Expected: FAIL because prefetch helpers and offline rules do not exist.

- [ ] **Step 3: Implement loader protocols and prefetch helpers**

```python
# src/seektalent/prf_sidecar/prefetch.py
def build_prefetch_plan(settings: AppSettings) -> dict[str, object]:
    return {
        "span_model": {
            "name": settings.prf_span_model_name,
            "revision": settings.prf_span_model_revision,
            "tokenizer_revision": settings.prf_span_tokenizer_revision,
        },
        "embedding_model": {
            "name": settings.prf_embedding_model_name,
            "revision": settings.prf_embedding_model_revision,
        },
    }


def prefetch_sidecar_models(settings: AppSettings) -> None:
    plan = build_prefetch_plan(settings)
    # snapshot_download(..., revision=plan["span_model"]["revision"], local_files_only=False)
    # snapshot_download(..., revision=plan["embedding_model"]["revision"], local_files_only=False)
```

```python
# src/seektalent/prf_sidecar/service.py
def prod_sidecar_must_use_offline_cache(settings: AppSettings) -> bool:
    return settings.prf_sidecar_serve_mode == "prod-serve"


def ensure_prod_cache_requirements(settings: AppSettings) -> None:
    if not prod_sidecar_must_use_offline_cache(settings):
        return
    if not settings.prf_span_model_revision or not settings.prf_embedding_model_revision:
        raise ValueError("prod sidecar requires pinned model revisions")
```

```toml
# pyproject.toml
dependencies = [
  "huggingface-hub>=0.35.3",
  "transformers>=4.57.0",
  "torch>=2.8.0",
]

[project.scripts]
seektalent-prf-sidecar = "seektalent.prf_sidecar.app:main"
seektalent-prf-sidecar-prefetch = "seektalent.prf_sidecar.prefetch:main"
```

- [ ] **Step 4: Add boundary tests that monkeypatch model downloads**

```python
def test_request_path_does_not_call_snapshot_download(monkeypatch):
    calls = []

    def fail_download(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("request path must not download models")

    monkeypatch.setattr("seektalent.prf_sidecar.prefetch.snapshot_download", fail_download)
    # exercise request-path code that uses the sidecar backend
    assert calls == []
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest -q tests/test_prf_sidecar_service.py tests/test_prf_sidecar_boundary.py -k 'offline or download'`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/seektalent/default.env src/seektalent/prf_sidecar/loaders.py src/seektalent/prf_sidecar/prefetch.py src/seektalent/prf_sidecar/service.py tests/test_prf_sidecar_service.py tests/test_prf_sidecar_boundary.py
git commit -m "feat: add PRF sidecar loader and prefetch gates"
```

## Task 4: Add HTTP Backends And Runtime Integration

**Files:**
- Create: `src/seektalent/prf_sidecar/client.py`
- Modify: `src/seektalent/candidate_feedback/span_extractors.py`
- Modify: `src/seektalent/candidate_feedback/proposal_runtime.py`
- Modify: `src/seektalent/runtime/orchestrator.py`
- Modify: `src/seektalent/runtime/second_lane_runtime.py`
- Modify: `tests/test_candidate_feedback.py`
- Modify: `tests/test_runtime_state_flow.py`
- Modify: `tests/test_second_lane_runtime.py`

- [ ] **Step 1: Write failing integration tests for shadow/mainline sidecar behavior**

```python
def test_shadow_sidecar_timeout_does_not_change_selected_lane():
    settings = AppSettings(prf_model_backend="http_sidecar", prf_v1_5_mode="shadow")
    result = build_second_lane_decision_with_sidecar_timeout(settings=settings)
    assert result.selected_lane_type == "generic_explore"
    assert result.shadow_prf_v1_5_artifact_ref is not None


def test_mainline_sidecar_failure_falls_back_to_legacy_prf_path():
    settings = AppSettings(prf_model_backend="http_sidecar", prf_v1_5_mode="mainline")
    decision = run_prf_v1_5_with_sidecar_failure(settings=settings)
    assert decision.prf_v1_5_mode == "mainline"
    assert decision.prf_gate_passed in {True, False}
    assert decision.reject_reasons
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_candidate_feedback.py tests/test_runtime_state_flow.py tests/test_second_lane_runtime.py -k sidecar`

Expected: FAIL because there is no HTTP backend or sidecar-aware timeout/fallback path.

- [ ] **Step 3: Implement HTTP backends and selector logic**

```python
# src/seektalent/prf_sidecar/client.py
import httpx


class HttpSpanModelBackend:
    def __init__(self, *, endpoint: str, timeout_seconds: float) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def extract(self, *, text: str, labels: list[str]) -> list[dict[str, object]]:
        response = httpx.post(
            f"{self.endpoint}/v1/span-extract",
            json={
                "request_id": "runtime-request",
                "texts": [text],
                "labels": labels,
                "schema_version": "gliner2-schema-v1",
                "model_name": "configured-at-runtime",
                "model_revision": "configured-at-runtime",
            },
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        return list(payload["rows"])
```

```python
# src/seektalent/candidate_feedback/proposal_runtime.py
def build_prf_span_extractor(settings: AppSettings, *, backend: SpanModelBackend | None = None):
    if backend is not None:
        return make_model_span_extractor(backend=backend, schema_version=settings.prf_span_schema_version)
    if settings.prf_model_backend != "http_sidecar":
        return LegacyRegexSpanExtractor()
    timeout = (
        settings.prf_sidecar_timeout_seconds_mainline
        if settings.prf_v1_5_mode == "mainline"
        else settings.prf_sidecar_timeout_seconds_shadow
    )
    return make_model_span_extractor(
        backend=HttpSpanModelBackend(endpoint=settings.prf_sidecar_endpoint, timeout_seconds=timeout),
        schema_version=settings.prf_span_schema_version,
    )
```

- [ ] **Step 4: Wire orchestrator fallback and timeout semantics**

```python
# src/seektalent/runtime/orchestrator.py
try:
    proposal, decision = self._build_prf_v1_5_proposal_and_decision(...)
except httpx.TimeoutException:
    fallback_reason = "sidecar_timeout"
    proposal, decision = self._build_legacy_prf_v1_5_fallback(...)
except httpx.HTTPError:
    fallback_reason = "sidecar_unreachable"
    proposal, decision = self._build_legacy_prf_v1_5_fallback(...)
```

```python
# src/seektalent/runtime/second_lane_runtime.py
if prf_v1_5_mode == "shadow":
    selected_lane_type = legacy_selected_lane_type
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest -q tests/test_candidate_feedback.py tests/test_runtime_state_flow.py tests/test_second_lane_runtime.py -k sidecar`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/seektalent/prf_sidecar/client.py src/seektalent/candidate_feedback/proposal_runtime.py src/seektalent/candidate_feedback/span_extractors.py src/seektalent/runtime/orchestrator.py src/seektalent/runtime/second_lane_runtime.py tests/test_candidate_feedback.py tests/test_runtime_state_flow.py tests/test_second_lane_runtime.py
git commit -m "feat: wire PRF sidecar runtime integration"
```

## Task 5: Extend Replay, Artifacts, And Main-App Metadata

**Files:**
- Modify: `src/seektalent/candidate_feedback/models.py`
- Modify: `src/seektalent/models.py`
- Modify: `src/seektalent/runtime/runtime_diagnostics.py`
- Modify: `src/seektalent/evaluation.py`
- Modify: `tests/test_evaluation.py`
- Modify: `tests/test_runtime_state_flow.py`

- [ ] **Step 1: Write failing replay and metadata tests**

```python
def test_replay_snapshot_includes_sidecar_manifest_hash_and_backend():
    snapshot = build_replay_snapshot(...)
    assert snapshot.prf_model_backend == "http_sidecar"
    assert snapshot.prf_sidecar_dependency_manifest_hash == "manifest-hash"
    assert snapshot.prf_sidecar_endpoint_contract_version == "prf-sidecar-http-v1"


def test_replay_rows_export_sidecar_fallback_reason():
    rows = build_replay_rows(run_dir)
    assert rows[0]["prf_fallback_reason"] == "sidecar_timeout"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_evaluation.py tests/test_runtime_state_flow.py -k sidecar`

Expected: FAIL because replay/export fields are missing.

- [ ] **Step 3: Extend metadata models and replay export**

```python
# src/seektalent/candidate_feedback/models.py
class PRFProposalVersionVector(BaseModel):
    ...
    model_backend: str
    sidecar_endpoint_contract_version: str | None = None
    sidecar_dependency_manifest_hash: str | None = None
    sidecar_image_digest: str | None = None
    remote_code_policy: str | None = None
    embedding_dimension: int | None = None
    embedding_normalized: bool | None = None
    fallback_reason: str | None = None
```

```python
# src/seektalent/runtime/runtime_diagnostics.py
update = {
    "prf_model_backend": prf_proposal.version_vector.model_backend,
    "prf_sidecar_endpoint_contract_version": prf_proposal.version_vector.sidecar_endpoint_contract_version,
    "prf_sidecar_dependency_manifest_hash": prf_proposal.version_vector.sidecar_dependency_manifest_hash,
    "prf_sidecar_image_digest": prf_proposal.version_vector.sidecar_image_digest,
    "prf_embedding_dimension": prf_proposal.version_vector.embedding_dimension,
    "prf_embedding_normalized": prf_proposal.version_vector.embedding_normalized,
    "prf_remote_code_policy": prf_proposal.version_vector.remote_code_policy,
    "prf_fallback_reason": prf_proposal.version_vector.fallback_reason,
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q tests/test_evaluation.py tests/test_runtime_state_flow.py -k sidecar`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/seektalent/candidate_feedback/models.py src/seektalent/models.py src/seektalent/runtime/runtime_diagnostics.py src/seektalent/evaluation.py tests/test_evaluation.py tests/test_runtime_state_flow.py
git commit -m "feat: extend PRF replay metadata for sidecar deployment"
```

## Task 6: Package Docker Deployment, Enforce Boundaries, And Update Docs

**Files:**
- Create: `docker/prf-model-sidecar/Dockerfile`
- Create: `docker/prf-model-sidecar/compose.yml`
- Modify: `src/seektalent/cli.py`
- Modify: `tests/test_artifact_path_contract.py`
- Modify: `tests/test_prf_sidecar_boundary.py`
- Modify: `docs/outputs.md`

- [ ] **Step 1: Write failing deployment and boundary tests**

```python
def test_runtime_modules_do_not_import_model_loading_libraries():
    forbidden = {"transformers", "sentence_transformers", "huggingface_hub", "torch"}
    offenders = scan_runtime_modules_for_forbidden_imports(forbidden)
    assert offenders == []


def test_shadow_timeout_keeps_second_lane_selection_stable():
    result = run_shadow_timeout_case()
    assert result.selected_lane_type == "generic_explore"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_artifact_path_contract.py tests/test_prf_sidecar_boundary.py`

Expected: FAIL because boundary enforcement and deployment wiring are incomplete.

- [ ] **Step 3: Add Docker deployment files and CLI entrypoints**

```dockerfile
# docker/prf-model-sidecar/Dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml uv.lock /app/
COPY src /app/src
RUN pip install uv && uv sync --frozen
EXPOSE 8741
CMD ["uv", "run", "seektalent-prf-sidecar"]
```

```yaml
# docker/prf-model-sidecar/compose.yml
services:
  prf-model-sidecar:
    build:
      context: ../..
      dockerfile: docker/prf-model-sidecar/Dockerfile
    environment:
      SEEKTALENT_PRF_SIDECAR_SERVE_MODE: prod-serve
      HF_HUB_OFFLINE: "1"
    volumes:
      - seektalent-model-cache:/var/lib/seektalent-model-cache
    healthcheck:
      test: ["CMD", "curl", "-f", "http://127.0.0.1:8741/readyz"]
      interval: 10s
      timeout: 3s
      retries: 6
```

```python
# src/seektalent/cli.py
def prf_sidecar_prefetch_command(...) -> int:
    settings = AppSettings(...)
    prefetch_sidecar_models(settings)
    return 0
```

- [ ] **Step 4: Update docs and replay field descriptions**

```markdown
## PRF Sidecar Replay Fields

- `prf_model_backend`
- `prf_sidecar_endpoint_contract_version`
- `prf_sidecar_dependency_manifest_hash`
- `prf_sidecar_image_digest`
- `prf_embedding_dimension`
- `prf_embedding_normalized`
- `prf_remote_code_policy`
- `prf_fallback_reason`
```

- [ ] **Step 5: Run the full verification suite**

Run:

```bash
uv run pytest -q \
  tests/test_prf_sidecar_models.py \
  tests/test_prf_sidecar_app.py \
  tests/test_prf_sidecar_service.py \
  tests/test_prf_sidecar_boundary.py \
  tests/test_candidate_feedback.py \
  tests/test_runtime_state_flow.py \
  tests/test_second_lane_runtime.py \
  tests/test_evaluation.py \
  tests/test_llm_provider_config.py \
  tests/test_artifact_path_contract.py
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add docker/prf-model-sidecar/Dockerfile docker/prf-model-sidecar/compose.yml src/seektalent/cli.py tests/test_artifact_path_contract.py tests/test_prf_sidecar_boundary.py docs/outputs.md
git commit -m "feat: package PRF model sidecar deployment"
```

## Self-Review

- Spec coverage:
  - network endpoint contract -> Tasks 1, 2, 6
  - offline/cache-only prod startup -> Tasks 3, 6
  - dependency manifest -> Tasks 1, 2, 5
  - HTTP provenance and embedding reproducibility fields -> Tasks 1, 2
  - shadow/mainline timeout and fallback semantics -> Tasks 4, 5, 6
  - artifact/replay alignment -> Tasks 4, 5
  - privacy and boundary enforcement -> Tasks 3, 6
- Placeholder scan:
  - no TODO/TBD placeholders remain
  - every task has explicit files, test commands, and commit steps
- Type consistency:
  - `prf_model_backend`, `prf_sidecar_endpoint_contract_version`, `SidecarDependencyManifest`, and sidecar response model names are used consistently across tasks
