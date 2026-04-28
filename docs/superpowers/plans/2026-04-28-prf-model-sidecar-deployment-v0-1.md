# PRF Model Sidecar Deployment v0.1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy a real local span-proposal model and a real local embedding model behind one CPU-only Docker sidecar while keeping PRF v1.5 artifacts, replay, shadow/mainline rollout, and deterministic legacy fallback behavior intact.

**Architecture:** Add a dedicated `prf_sidecar` package that owns sidecar-only dependencies, HTTP contracts, dependency-manifest hashing, readiness, offline/cache-only serving, and real model loading. Keep exact-offset alignment, familying merge semantics, deterministic PRF gate, artifact persistence, and typed second-lane routing in the main app by introducing HTTP-backed span and embedding backends rather than moving policy into the sidecar.

**Tech Stack:** Python 3.12, Pydantic, FastAPI, uvicorn, httpx, Docker Compose, Hugging Face Hub cache controls, optional sidecar-only model dependencies, pytest.

---

## File Map

### New modules

- Create: `src/seektalent/prf_sidecar/models.py`
  - Pydantic contracts for `/livez`, `/readyz`, `/v1/span-extract`, `/v1/embed`, structured errors, and `SidecarDependencyManifest`.
- Create: `src/seektalent/prf_sidecar/service.py`
  - Sidecar runtime state, readiness and liveness behavior, dependency-manifest hashing, payload-limit enforcement, privacy-safe logging hooks.
- Create: `src/seektalent/prf_sidecar/loaders.py`
  - Loader protocols plus sidecar-only real loaders for GLiNER2 and multilingual embeddings.
- Create: `src/seektalent/prf_sidecar/app.py`
  - FastAPI app factory, `/livez`, `/readyz`, `/v1/span-extract`, `/v1/embed`, and HTTP status handling.
- Create: `src/seektalent/prf_sidecar/client.py`
  - `HttpSpanModelBackend`, `HttpEmbeddingBackend`, response validation, and sidecar exception taxonomy.
- Create: `src/seektalent/prf_sidecar/prefetch.py`
  - Explicit prefetch/warmup job for pinned snapshots.
- Create: `src/seektalent/prf_sidecar/__init__.py`
- Create: `tests/test_prf_sidecar_models.py`
- Create: `tests/test_prf_sidecar_app.py`
- Create: `tests/test_prf_sidecar_service.py`
- Create: `tests/test_prf_sidecar_boundary.py`
- Create: `docker/prf-model-sidecar/Dockerfile`
- Create: `docker/prf-model-sidecar/compose.yml`

### Existing modules to modify

- Modify: `pyproject.toml`
  - Move all sidecar-serving dependencies into `optional-dependencies.prf-sidecar`; keep sandbox base install free of model-serving libraries.
- Modify: `src/seektalent/config.py`
  - Add sidecar deployment-profile settings, endpoint contract, bakeoff-promotion gate, timeout budgets, and sidecar bind-host settings.
- Modify: `src/seektalent/default.env`
  - Add commented examples for host-local and docker-internal sidecar profiles.
- Modify: `src/seektalent/artifacts/registry.py`
  - Add a logical artifact for the run-local sidecar dependency manifest snapshot.
- Modify: `tests/test_artifact_store.py`
  - Verify sidecar dependency manifest artifact is resolver-backed.
- Modify: `tests/test_artifact_path_contract.py`
  - Enforce no direct model-loading imports or request-path download helpers outside `src/seektalent/prf_sidecar/`.
- Modify: `src/seektalent/candidate_feedback/proposal_runtime.py`
  - Select legacy vs sidecar-backed span and embedding backends without moving PRF policy into the sidecar.
- Modify: `src/seektalent/candidate_feedback/familying.py`
  - Accept embedding similarity from an injected backend while keeping merge semantics local.
- Modify: `src/seektalent/candidate_feedback/span_extractors.py`
  - Accept HTTP-backed span backend objects.
- Modify: `src/seektalent/candidate_feedback/models.py`
  - Extend proposal version vectors with sidecar contract, embedding metadata, manifest hash, and fallback taxonomy.
- Modify: `src/seektalent/models.py`
  - Extend `ReplaySnapshot` and `SecondLaneDecision` with sidecar metadata and fallback fields.
- Modify: `src/seektalent/runtime/orchestrator.py`
  - Inject sidecar-backed proposal paths in shadow/mainline modes with timeout, readiness, bakeoff-promotion, and legacy fallback gates.
- Modify: `src/seektalent/runtime/runtime_diagnostics.py`
  - Export sidecar metadata into replay snapshots.
- Modify: `src/seektalent/evaluation.py`
  - Include sidecar-backed replay fields in replay rows.
- Modify: `src/seektalent/cli.py`
  - Add sidecar prefetch command and optional sidecar launch helper.
- Modify: `docs/outputs.md`
  - Document sidecar dependency manifest artifact and replay/export fields.
- Modify: `tests/test_candidate_feedback.py`
  - Cover sidecar-backed proposal contract, familying fallback, and legacy fallback behavior.
- Modify: `tests/test_candidate_feedback_familying.py`
  - Cover embedding-backed family similarity with local surface guards.
- Modify: `tests/test_runtime_state_flow.py`
  - Cover shadow/mainline rollout, readiness gate, and replay metadata.
- Modify: `tests/test_second_lane_runtime.py`
  - Verify shadow timeout/failure never changes selected lane and mainline gate requires bakeoff promotion plus ready sidecar.
- Modify: `tests/test_evaluation.py`
  - Verify replay export carries full sidecar metadata.
- Modify: `tests/test_llm_provider_config.py`
  - Verify new settings defaults and optional dependency boundary.

## Task 1: Define Sidecar Contracts, Config, And Dependency Boundary

**Files:**
- Create: `src/seektalent/prf_sidecar/models.py`
- Modify: `pyproject.toml`
- Modify: `src/seektalent/config.py`
- Modify: `src/seektalent/models.py`
- Modify: `tests/test_llm_provider_config.py`
- Test: `tests/test_prf_sidecar_models.py`

- [ ] **Step 1: Write the failing contract and dependency-boundary tests**

```python
from seektalent.prf_sidecar.models import (
    SidecarDependencyManifest,
    SpanExtractRequest,
    SpanExtractResponse,
    EmbedRequest,
    EmbedResponse,
)
from seektalent.config import AppSettings


def test_embed_response_tracks_replay_critical_metadata():
    response = EmbedResponse(
        schema_version="prf-sidecar-embed-v1",
        model_name="Alibaba-NLP/gte-multilingual-base",
        model_revision="rev-embed",
        embedding_dimension=768,
        normalized=True,
        pooling="mean",
        dtype="float32",
        max_input_tokens=8192,
        truncation=True,
        vectors=[[0.1, 0.2]],
    )
    assert response.embedding_dimension == 768
    assert response.normalized is True


def test_sidecar_manifest_hash_is_deterministic():
    manifest = SidecarDependencyManifest(
        sidecar_image_digest="sha256:image",
        python_lockfile_hash="lock-hash",
        torch_version="2.8.0",
        transformers_version="4.57.0",
        sentence_transformers_version="5.1.1",
        gliner_runtime_version="2.0.0",
        span_model_name="fastino/gliner2-multi-v1",
        span_model_commit="0123456789abcdef0123456789abcdef01234567",
        span_tokenizer_commit="fedcba9876543210fedcba9876543210fedcba98",
        embedding_model_name="Alibaba-NLP/gte-multilingual-base",
        embedding_model_commit="abcdef0123456789abcdef0123456789abcdef01",
        remote_code_policy="approved_baked_code",
        remote_code_commit="00112233445566778899aabbccddeeff00112233",
        license_status="approved",
        embedding_normalization=True,
        embedding_dimension=768,
        dtype="float32",
        max_input_tokens=8192,
    )
    assert manifest.compute_hash() == manifest.compute_hash()


def test_default_install_does_not_require_sidecar_model_dependencies():
    pyproject_text = Path("pyproject.toml").read_text(encoding="utf-8")
    assert "torch>=" not in pyproject_text.split("[project.optional-dependencies]")[0]
    assert "transformers>=" not in pyproject_text.split("[project.optional-dependencies]")[0]


def test_app_settings_expose_profile_and_mainline_gate_inputs():
    settings = AppSettings()
    assert settings.prf_model_backend == "legacy"
    assert settings.prf_sidecar_profile == "host-local"
    assert settings.prf_sidecar_endpoint_contract_version == "prf-sidecar-http-v1"
    assert settings.prf_sidecar_bakeoff_promoted is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_prf_sidecar_models.py tests/test_llm_provider_config.py -k sidecar`

Expected: FAIL with missing sidecar models, missing config fields, or missing dependency-boundary behavior.

- [ ] **Step 3: Implement contract models, manifest hashing, and config**

```python
# src/seektalent/prf_sidecar/models.py
import json
from hashlib import sha256
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

    def compute_hash(self) -> str:
        payload = self.model_dump(mode="json", exclude_none=False)
        blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return sha256(blob.encode("utf-8")).hexdigest()


class EmbedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str
    phrases: list[str]
    model_name: str
    model_revision: str


class EmbedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["prf-sidecar-embed-v1"]
    model_name: str
    model_revision: str
    embedding_dimension: int = Field(gt=0)
    normalized: bool
    pooling: str
    dtype: Literal["float32", "float16", "bfloat16"]
    max_input_tokens: int = Field(gt=0)
    truncation: bool
    vectors: list[list[float]]
```

```python
# src/seektalent/config.py
prf_model_backend: Literal["legacy", "http_sidecar"] = "legacy"
prf_sidecar_profile: Literal["host-local", "docker-internal", "linux-host-network"] = "host-local"
prf_sidecar_bind_host: str = "127.0.0.1"
prf_sidecar_endpoint: str = "http://127.0.0.1:8741"
prf_sidecar_endpoint_contract_version: str = "prf-sidecar-http-v1"
prf_sidecar_serve_mode: Literal["dev-bootstrap", "prod-serve"] = "dev-bootstrap"
prf_sidecar_timeout_seconds_shadow: float = 0.35
prf_sidecar_timeout_seconds_mainline: float = 1.50
prf_sidecar_max_batch_size: int = 32
prf_sidecar_max_payload_bytes: int = 262_144
prf_sidecar_bakeoff_promoted: bool = False
```

```toml
# pyproject.toml
[project.optional-dependencies]
prf-sidecar = [
  "fastapi>=0.118.0",
  "uvicorn>=0.37.0",
  "huggingface-hub>=0.35.3",
  "transformers>=4.57.0",
  "torch>=2.8.0",
  "sentence-transformers>=5.1.1",
]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q tests/test_prf_sidecar_models.py tests/test_llm_provider_config.py -k sidecar`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml src/seektalent/config.py src/seektalent/models.py src/seektalent/prf_sidecar/models.py tests/test_prf_sidecar_models.py tests/test_llm_provider_config.py
git commit -m "feat: add PRF sidecar contracts and dependency boundary"
```

## Task 2: Build Sidecar Service, Readyz Semantics, And Deployment Profiles

**Files:**
- Create: `src/seektalent/prf_sidecar/service.py`
- Create: `src/seektalent/prf_sidecar/app.py`
- Create: `src/seektalent/prf_sidecar/__init__.py`
- Test: `tests/test_prf_sidecar_app.py`
- Test: `tests/test_prf_sidecar_service.py`

- [ ] **Step 1: Write the failing service/app tests**

```python
from fastapi.testclient import TestClient

from seektalent.prf_sidecar.app import create_sidecar_app


class FakeService:
    def live(self):
        return {"status": "alive"}

    def ready(self):
        return {
            "status": "not_ready",
            "endpoint_contract_version": "prf-sidecar-http-v1",
            "dependency_manifest_hash": "manifest-hash",
            "span_model_loaded": False,
            "embedding_model_loaded": False,
            "span_model_name": "fastino/gliner2-multi-v1",
            "span_model_revision": "rev-span",
            "span_tokenizer_revision": "rev-tokenizer",
            "embedding_model_name": "Alibaba-NLP/gte-multilingual-base",
            "embedding_model_revision": "rev-embed",
        }


def test_readyz_returns_503_when_models_not_loaded():
    app = create_sidecar_app(service=FakeService())
    client = TestClient(app)
    response = client.get("/readyz")
    assert response.status_code == 503


def test_docker_internal_profile_binds_container_safe_host():
    settings = AppSettings(prf_sidecar_profile="docker-internal")
    bind_host = resolve_sidecar_bind_host(settings)
    assert bind_host == "0.0.0.0"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_prf_sidecar_app.py tests/test_prf_sidecar_service.py -k 'readyz or bind'`

Expected: FAIL because ready status and profile-aware bind host are not implemented.

- [ ] **Step 3: Implement service/app with 503 readiness and profile-aware bind host**

```python
# src/seektalent/prf_sidecar/service.py
from dataclasses import dataclass


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


def resolve_sidecar_bind_host(settings: AppSettings) -> str:
    if settings.prf_sidecar_profile == "docker-internal":
        return "0.0.0.0"
    return settings.prf_sidecar_bind_host
```

```python
# src/seektalent/prf_sidecar/app.py
from fastapi import FastAPI, Response
import uvicorn


@app.get("/readyz")
def readyz(response: Response) -> ReadyResponse:
    ready = service.ready()
    if ready.status != "ready":
        response.status_code = 503
    return ready


def main() -> None:
    settings = AppSettings()
    service = build_default_sidecar_service(settings=settings)
    uvicorn.run(
        create_sidecar_app(service=service),
        host=resolve_sidecar_bind_host(settings),
        port=8741,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q tests/test_prf_sidecar_app.py tests/test_prf_sidecar_service.py -k 'readyz or bind'`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/seektalent/prf_sidecar/__init__.py src/seektalent/prf_sidecar/service.py src/seektalent/prf_sidecar/app.py tests/test_prf_sidecar_app.py tests/test_prf_sidecar_service.py
git commit -m "feat: add sidecar app and readiness semantics"
```

## Task 3: Add Fake Loaders, Real Sidecar-Only Loaders, And Offline Cache-Only Serve Mode

**Files:**
- Create: `src/seektalent/prf_sidecar/loaders.py`
- Create: `src/seektalent/prf_sidecar/prefetch.py`
- Modify: `src/seektalent/default.env`
- Test: `tests/test_prf_sidecar_service.py`
- Test: `tests/test_prf_sidecar_boundary.py`

- [ ] **Step 1: Write failing loader and offline-mode tests**

```python
from seektalent.config import AppSettings
from seektalent.prf_sidecar.prefetch import build_prefetch_plan
from seektalent.prf_sidecar.service import ensure_prod_cache_requirements


def test_prod_loader_uses_local_files_only_and_offline_env(monkeypatch):
    calls = {}

    def fake_from_pretrained(*args, **kwargs):
        calls["kwargs"] = kwargs
        return object()

    monkeypatch.setattr("seektalent.prf_sidecar.loaders.AutoTokenizer.from_pretrained", fake_from_pretrained)
    settings = AppSettings(
        prf_sidecar_serve_mode="prod-serve",
        prf_span_model_revision="rev-span",
        prf_span_tokenizer_revision="rev-tokenizer",
        prf_embedding_model_revision="rev-embed",
    )
    build_span_loader(settings)
    assert calls["kwargs"]["local_files_only"] is True


def test_prod_readyz_fails_when_pinned_cache_missing():
    settings = AppSettings(
        prf_sidecar_serve_mode="prod-serve",
        prf_span_model_revision="rev-span",
        prf_span_tokenizer_revision="rev-tokenizer",
        prf_embedding_model_revision="rev-embed",
    )
    with pytest.raises(MissingPinnedModelCacheError):
        ensure_prod_cache_requirements(settings, cache_state={"span": False, "embed": False})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_prf_sidecar_service.py tests/test_prf_sidecar_boundary.py -k 'offline or local_files_only or cache_missing'`

Expected: FAIL because loaders and offline cache enforcement do not exist.

- [ ] **Step 3: Implement fake loaders, real loaders, and prefetch**

```python
# src/seektalent/prf_sidecar/loaders.py
class SpanInferenceLoader(Protocol):
    def load(self) -> object:
        pass


class EmbeddingInferenceLoader(Protocol):
    def load(self) -> object:
        pass


def build_span_loader(settings: AppSettings):
    from transformers import AutoTokenizer  # sidecar-only import

    local_only = settings.prf_sidecar_serve_mode == "prod-serve"
    return AutoTokenizer.from_pretrained(
        settings.prf_span_model_name,
        revision=settings.prf_span_tokenizer_revision,
        local_files_only=local_only,
        trust_remote_code=False,
    )


def build_embedding_loader(settings: AppSettings):
    local_only = settings.prf_sidecar_serve_mode == "prod-serve"
    return SentenceTransformer(
        settings.prf_embedding_model_name,
        revision=settings.prf_embedding_model_revision,
        local_files_only=local_only,
        trust_remote_code=False,
    )
```

```python
# src/seektalent/prf_sidecar/prefetch.py
from huggingface_hub import snapshot_download


def prefetch_sidecar_models(settings: AppSettings) -> None:
    snapshot_download(
        repo_id=settings.prf_span_model_name,
        revision=settings.prf_span_model_revision,
        local_files_only=False,
    )
    snapshot_download(
        repo_id=settings.prf_embedding_model_name,
        revision=settings.prf_embedding_model_revision,
        local_files_only=False,
    )
```

- [ ] **Step 4: Add request-path no-download tests**

```python
def test_request_runtime_never_calls_model_download_apis(monkeypatch):
    monkeypatch.setattr("huggingface_hub.snapshot_download", fail_download)
    monkeypatch.setattr("transformers.AutoModel.from_pretrained", fail_download)
    monkeypatch.setattr("transformers.AutoTokenizer.from_pretrained", fail_download)
    monkeypatch.setattr("sentence_transformers.SentenceTransformer", fail_download)
    exercise_request_runtime_with_http_sidecar()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest -q tests/test_prf_sidecar_service.py tests/test_prf_sidecar_boundary.py -k 'offline or local_files_only or download'`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/seektalent/default.env src/seektalent/prf_sidecar/loaders.py src/seektalent/prf_sidecar/prefetch.py src/seektalent/prf_sidecar/service.py tests/test_prf_sidecar_service.py tests/test_prf_sidecar_boundary.py
git commit -m "feat: add sidecar-only loaders and offline cache gates"
```

## Task 4: Add HTTP Span And Embedding Backends, Response Validation, And Familying Integration

**Files:**
- Create: `src/seektalent/prf_sidecar/client.py`
- Modify: `src/seektalent/candidate_feedback/span_extractors.py`
- Modify: `src/seektalent/candidate_feedback/familying.py`
- Modify: `src/seektalent/candidate_feedback/proposal_runtime.py`
- Modify: `tests/test_candidate_feedback.py`
- Modify: `tests/test_candidate_feedback_familying.py`

- [ ] **Step 1: Write failing client/familying tests**

```python
def test_http_embedding_backend_returns_validated_vectors():
    backend = HttpEmbeddingBackend(
        endpoint="http://prf-model-sidecar:8741",
        model_name="Alibaba-NLP/gte-multilingual-base",
        model_revision="rev-embed",
        timeout_seconds=0.5,
    )
    response = backend.embed(["Flink CDC", "flink-cdc"])
    assert response.embedding_dimension == 768


def test_embedding_failure_falls_back_to_exact_surface_familying():
    span = CandidateSpan.build(
        source_resume_id="resume-1",
        source_field="evidence",
        source_text_index=0,
        start_char=0,
        end_char=9,
        raw_surface="Flink CDC",
        normalized_surface="Flink CDC",
        model_label="technical_phrase",
        model_score=0.9,
        extractor_schema_version="gliner2-schema-v1",
    )
    families = build_phrase_families(
        positive_spans=[span],
        negative_spans=[],
        embedding_similarity=None,
    )
    assert families
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_candidate_feedback.py tests/test_candidate_feedback_familying.py -k 'embedding or sidecar'`

Expected: FAIL because `HttpEmbeddingBackend` and embedding-based familying are not wired.

- [ ] **Step 3: Implement client exceptions and both HTTP backends**

```python
# src/seektalent/prf_sidecar/client.py
class SidecarTimeout(RuntimeError): pass
class SidecarUnavailable(RuntimeError): pass
class SidecarSchemaMismatch(RuntimeError): pass
class SidecarMalformedResponse(RuntimeError): pass
class SidecarRevisionMismatch(RuntimeError): pass
class SidecarEmbeddingUnavailable(RuntimeError): pass


class HttpSpanModelBackend:
    def __init__(self, *, endpoint: str, model_name: str, model_revision: str, schema_version: str, timeout_seconds: float) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.model_name = model_name
        self.model_revision = model_revision
        self.schema_version = schema_version
        self.timeout_seconds = timeout_seconds

    def extract(self, *, text: str, labels: list[str]) -> list[dict[str, object]]:
        payload = SpanExtractResponse.model_validate(response.json())
        if payload.model_name != self.model_name or payload.model_revision != self.model_revision:
            raise SidecarRevisionMismatch("sidecar returned unexpected span model revision")
        return [row.model_dump(mode="json") for row in payload.rows]


class HttpEmbeddingBackend:
    def __init__(self, *, endpoint: str, model_name: str, model_revision: str, timeout_seconds: float) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.model_name = model_name
        self.model_revision = model_revision
        self.timeout_seconds = timeout_seconds

    def embed(self, phrases: list[str]) -> EmbedResponse:
        payload = EmbedResponse.model_validate(response.json())
        if payload.model_name != self.model_name or payload.model_revision != self.model_revision:
            raise SidecarRevisionMismatch("sidecar returned unexpected embedding model revision")
        return payload
```

- [ ] **Step 4: Wire embedding backend into proposal runtime**

```python
# src/seektalent/candidate_feedback/proposal_runtime.py
span_backend = HttpSpanModelBackend(
    endpoint=settings.prf_sidecar_endpoint,
    model_name=settings.prf_span_model_name,
    model_revision=settings.prf_span_model_revision,
    schema_version=settings.prf_span_schema_version,
    timeout_seconds=settings.prf_sidecar_timeout_seconds_shadow,
)
embedding_backend = HttpEmbeddingBackend(
    endpoint=settings.prf_sidecar_endpoint,
    model_name=settings.prf_embedding_model_name,
    model_revision=settings.prf_embedding_model_revision,
    timeout_seconds=settings.prf_sidecar_timeout_seconds_shadow,
)

proposal = build_prf_proposal_bundle(
    positive_seed_resumes=seeds,
    negative_seed_resumes=negatives,
    extractor=make_model_span_extractor(backend=span_backend, schema_version=settings.prf_span_schema_version),
    metadata=metadata,
    round_no=retrieval_plan.round_no,
    embedding_similarity=build_embedding_similarity(embedding_backend),
)
```

```python
# src/seektalent/candidate_feedback/familying.py
def build_embedding_similarity(backend: HttpEmbeddingBackend) -> Callable[[CandidateSpan, CandidateSpan], float]:
    def similarity(left: CandidateSpan, right: CandidateSpan) -> float:
        response = backend.embed([left.normalized_surface, right.normalized_surface])
        left_vector, right_vector = response.vectors
        return cosine_similarity(left_vector, right_vector)

    return similarity
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest -q tests/test_candidate_feedback.py tests/test_candidate_feedback_familying.py -k 'embedding or sidecar'`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/seektalent/prf_sidecar/client.py src/seektalent/candidate_feedback/span_extractors.py src/seektalent/candidate_feedback/familying.py src/seektalent/candidate_feedback/proposal_runtime.py tests/test_candidate_feedback.py tests/test_candidate_feedback_familying.py
git commit -m "feat: add HTTP span and embedding backends"
```

## Task 5: Add Mainline Readiness Gate, Legacy Fallback Taxonomy, And Replay/Artifact Alignment

**Files:**
- Modify: `src/seektalent/artifacts/registry.py`
- Modify: `tests/test_artifact_store.py`
- Modify: `src/seektalent/candidate_feedback/models.py`
- Modify: `src/seektalent/models.py`
- Modify: `src/seektalent/runtime/orchestrator.py`
- Modify: `src/seektalent/runtime/runtime_diagnostics.py`
- Modify: `src/seektalent/evaluation.py`
- Modify: `tests/test_evaluation.py`
- Modify: `tests/test_runtime_state_flow.py`
- Modify: `tests/test_second_lane_runtime.py`

- [ ] **Step 1: Write failing gating and replay tests**

```python
def test_mainline_requires_bakeoff_promotion_and_ready_sidecar():
    ready = ReadyResponse(
        status="ready",
        endpoint_contract_version="prf-sidecar-http-v1",
        dependency_manifest_hash="manifest-hash",
        span_model_loaded=True,
        embedding_model_loaded=True,
        span_model_name="fastino/gliner2-multi-v1",
        span_model_revision="rev-span",
        span_tokenizer_revision="rev-tokenizer",
        embedding_model_name="Alibaba-NLP/gte-multilingual-base",
        embedding_model_revision="rev-embed",
    )
    settings = AppSettings(prf_v1_5_mode="mainline", prf_model_backend="http_sidecar", prf_sidecar_bakeoff_promoted=False)
    assert sidecar_dependency_gate_allows_mainline(settings, ready) is False


def test_replay_snapshot_includes_sidecar_manifest_hash_and_artifact_ref():
    snapshot = ReplaySnapshot(
        prf_sidecar_dependency_manifest_hash="manifest-hash",
        prf_candidate_span_artifact_ref="round.02.retrieval.prf_span_candidates",
    )
    assert snapshot.prf_sidecar_dependency_manifest_hash == "manifest-hash"
    assert snapshot.prf_candidate_span_artifact_ref == "round.02.retrieval.prf_span_candidates"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_evaluation.py tests/test_runtime_state_flow.py tests/test_second_lane_runtime.py tests/test_artifact_store.py -k sidecar`

Expected: FAIL because mainline gating, artifact mapping, and replay fields are incomplete.

- [ ] **Step 3: Implement readiness gate and full replay fields**

```python
# src/seektalent/runtime/orchestrator.py
def sidecar_dependency_gate_allows_mainline(settings: AppSettings, readyz: ReadyResponse) -> bool:
    return (
        settings.prf_v1_5_mode == "mainline"
        and settings.prf_model_backend == "http_sidecar"
        and settings.prf_sidecar_bakeoff_promoted is True
        and bool(settings.prf_span_model_revision)
        and bool(settings.prf_span_tokenizer_revision)
        and bool(settings.prf_embedding_model_revision)
        and readyz.status == "ready"
        and readyz.span_model_revision == settings.prf_span_model_revision
        and readyz.embedding_model_revision == settings.prf_embedding_model_revision
        and bool(readyz.dependency_manifest_hash)
    )
```

```python
# src/seektalent/candidate_feedback/models.py
class PRFProposalVersionVector(BaseModel):
    span_extractor_version: str
    span_model_name: str
    span_model_revision: str
    span_tokenizer_revision: str
    span_schema_version: str
    span_thresholds_version: str
    embedding_model_name: str
    embedding_model_revision: str
    familying_version: str
    familying_thresholds: dict[str, float]
    runtime_mode: str
    top_n_candidate_cap: int
    model_backend: str
    sidecar_endpoint_contract_version: str | None = None
    sidecar_dependency_manifest_hash: str | None = None
    sidecar_image_digest: str | None = None
    embedding_dimension: int | None = None
    embedding_normalized: bool | None = None
    embedding_dtype: str | None = None
    embedding_pooling: str | None = None
    embedding_truncation: bool | None = None
    fallback_reason: str | None = None
```

- [ ] **Step 4: Add run-local sidecar dependency manifest artifact**

```python
# src/seektalent/artifacts/registry.py
"runtime.prf_sidecar_dependency_manifest": {
    "path": "runtime/prf_sidecar_dependency_manifest.json",
    "content_type": "application/json",
    "schema_version": "v1",
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest -q tests/test_evaluation.py tests/test_runtime_state_flow.py tests/test_second_lane_runtime.py tests/test_artifact_store.py -k sidecar`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/seektalent/artifacts/registry.py tests/test_artifact_store.py src/seektalent/candidate_feedback/models.py src/seektalent/models.py src/seektalent/runtime/orchestrator.py src/seektalent/runtime/runtime_diagnostics.py src/seektalent/evaluation.py tests/test_evaluation.py tests/test_runtime_state_flow.py tests/test_second_lane_runtime.py
git commit -m "feat: add PRF sidecar gating and replay metadata"
```

## Task 6: Package Docker Deployment, Privacy Guards, And Boundary Enforcement

**Files:**
- Create: `docker/prf-model-sidecar/Dockerfile`
- Create: `docker/prf-model-sidecar/compose.yml`
- Modify: `src/seektalent/cli.py`
- Modify: `docs/outputs.md`
- Modify: `tests/test_prf_sidecar_boundary.py`
- Modify: `tests/test_artifact_path_contract.py`

- [ ] **Step 1: Write failing deployment and privacy tests**

```python
def test_request_runtime_modules_do_not_import_sidecar_model_libraries():
    forbidden = {"transformers", "sentence_transformers", "huggingface_hub", "torch"}
    offenders = scan_non_sidecar_modules_for_forbidden_imports(forbidden)
    assert offenders == []


def test_sidecar_logs_do_not_include_raw_text_by_default(caplog):
    service = build_sidecar_service_for_test()
    service.log_span_request(request_id="req-1", texts=["secret text"], debug_raw_text=False)
    assert "secret text" not in caplog.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_prf_sidecar_boundary.py tests/test_artifact_path_contract.py -k 'privacy or import or compose'`

Expected: FAIL because privacy and deployment boundary enforcement are incomplete.

- [ ] **Step 3: Add Docker deployment files and CLI prefetch entrypoint**

```dockerfile
# docker/prf-model-sidecar/Dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml uv.lock /app/
COPY src /app/src
RUN pip install uv && uv sync --frozen --extra prf-sidecar
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
      SEEKTALENT_PRF_SIDECAR_PROFILE: docker-internal
      SEEKTALENT_PRF_SIDECAR_BIND_HOST: 0.0.0.0
      SEEKTALENT_PRF_SIDECAR_SERVE_MODE: prod-serve
      HF_HOME: /var/lib/seektalent-model-cache
      HF_HUB_CACHE: /var/lib/seektalent-model-cache/hub
      HF_HUB_OFFLINE: "1"
    networks:
      - prf-sidecar-net
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8741/readyz', timeout=2).read()"]
      interval: 10s
      timeout: 3s
      retries: 6
      start_period: 30s

networks:
  prf-sidecar-net:
    internal: true
```

```python
# src/seektalent/cli.py
def prf_sidecar_prefetch_command(*, env_file: str | Path | None = ".env") -> int:
    settings = AppSettings(_env_file=env_file)
    prefetch_sidecar_models(settings)
    return 0
```

- [ ] **Step 4: Update docs and replay-field descriptions**

```markdown
## PRF Sidecar Replay Fields

- `prf_model_backend`
- `prf_sidecar_endpoint_contract_version`
- `prf_sidecar_dependency_manifest_hash`
- `prf_sidecar_image_digest`
- `prf_span_model_name`
- `prf_span_model_revision`
- `prf_span_tokenizer_revision`
- `prf_span_schema_version`
- `prf_embedding_model_name`
- `prf_embedding_model_revision`
- `prf_embedding_dimension`
- `prf_embedding_normalized`
- `prf_embedding_dtype`
- `prf_embedding_pooling`
- `prf_embedding_truncation`
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
  tests/test_candidate_feedback_familying.py \
  tests/test_runtime_state_flow.py \
  tests/test_second_lane_runtime.py \
  tests/test_evaluation.py \
  tests/test_llm_provider_config.py \
  tests/test_artifact_store.py \
  tests/test_artifact_path_contract.py
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add docker/prf-model-sidecar/Dockerfile docker/prf-model-sidecar/compose.yml src/seektalent/cli.py docs/outputs.md tests/test_prf_sidecar_boundary.py tests/test_artifact_path_contract.py
git commit -m "feat: package PRF sidecar deployment"
```

## Self-Review

- Spec coverage:
  - config-driven network endpoint and deployment profiles -> Tasks 1, 2, 6
  - sidecar-only optional dependencies -> Task 1
  - real span and embedding serving -> Tasks 3 and 4
  - prod offline/cache-only mode -> Task 3
  - readiness, bakeoff promotion, and mainline gates -> Tasks 2 and 5
  - artifact/replay alignment -> Task 5
  - privacy and import-boundary enforcement -> Task 6
- Placeholder scan:
  - no TODO/TBD placeholders remain
  - no fake “configured-at-runtime” request values remain
  - all tasks include explicit tests, commands, and commit points
- Type consistency:
  - `EmbedRequest`, `EmbedResponse`, `SidecarDependencyManifest.compute_hash()`, `prf_sidecar_bakeoff_promoted`, `HttpEmbeddingBackend`, and `sidecar_dependency_gate_allows_mainline()` are introduced before later tasks depend on them
