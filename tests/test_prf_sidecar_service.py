from __future__ import annotations

import os

import pytest

from seektalent.config import AppSettings
from seektalent.prf_sidecar.loaders import (
    FakeEmbeddingModel,
    FakeSpanModel,
    RemoteCodePolicyError,
    build_embedding_model,
    build_span_model,
)
from seektalent.prf_sidecar.models import SpanExtractRow
from seektalent.prf_sidecar.models import SidecarDependencyManifest
from seektalent.prf_sidecar.prefetch import build_prefetch_plan
from seektalent.prf_sidecar.service import (
    MissingPinnedModelCacheError,
    build_dependency_manifest,
    build_default_sidecar_service,
    ensure_prod_cache_requirements,
    resolve_sidecar_bind_host,
)


def _settings(**overrides: object) -> AppSettings:
    defaults = {
        "prf_sidecar_max_batch_size": 2,
        "prf_sidecar_max_payload_bytes": 80,
        "prf_span_model_revision": "rev-span",
        "prf_span_tokenizer_revision": "rev-tokenizer",
        "prf_embedding_model_revision": "rev-embed",
    }
    defaults.update(overrides)
    return AppSettings(_env_file=None, **defaults)  # ty: ignore[unknown-argument]


def _manifest() -> SidecarDependencyManifest:
    return SidecarDependencyManifest(
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
        embedding_dimension=2,
        dtype="float32",
        max_input_tokens=8192,
    )


def test_docker_internal_profile_binds_container_safe_host() -> None:
    settings = _settings(prf_sidecar_profile="docker-internal")

    assert resolve_sidecar_bind_host(settings) == "0.0.0.0"


def test_host_local_profile_uses_configured_bind_host() -> None:
    settings = _settings(prf_sidecar_bind_host="127.0.0.2")

    assert resolve_sidecar_bind_host(settings) == "127.0.0.2"


def test_ready_reports_not_ready_without_loaded_models() -> None:
    service = build_default_sidecar_service(
        settings=_settings(),
        dependency_manifest=_manifest(),
    )

    ready = service.ready()

    assert ready.status == "not_ready"
    assert ready.span_model_loaded is False
    assert ready.embedding_model_loaded is False


def test_ready_reports_ready_with_models_and_manifest() -> None:
    service = build_default_sidecar_service(
        settings=_settings(),
        span_model=FakeSpanModel(),
        embedding_model=FakeEmbeddingModel(),
        dependency_manifest=_manifest(),
    )

    ready = service.ready()

    assert ready.status == "ready"
    assert ready.dependency_manifest_hash == _manifest().compute_hash()


def test_build_default_sidecar_service_generates_dependency_manifest_when_loading_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("seektalent.prf_sidecar.service.ensure_prod_cache_requirements", lambda settings: None)
    monkeypatch.setattr("seektalent.prf_sidecar.service.build_span_model", lambda settings: FakeSpanModel())
    monkeypatch.setattr("seektalent.prf_sidecar.service.build_embedding_model", lambda settings: FakeEmbeddingModel())
    monkeypatch.setattr("seektalent.prf_sidecar.service._python_lockfile_hash", lambda: "lock-hash")
    monkeypatch.setattr("seektalent.prf_sidecar.service._package_version", lambda name: "1.0.0")
    monkeypatch.setattr("seektalent.prf_sidecar.service._optional_package_version", lambda name: "1.0.0")
    monkeypatch.setenv("SEEKTALENT_PRF_SIDECAR_IMAGE_DIGEST", "sha256:image")

    service = build_default_sidecar_service(
        settings=_settings(prf_allow_remote_code=False),
        load_models=True,
    )

    ready = service.ready()

    assert ready.status == "ready"
    assert ready.dependency_manifest_hash is not None
    assert ready.sidecar_image_digest == "sha256:image"


def test_prod_readyz_fails_when_pinned_cache_missing() -> None:
    settings = _settings(prf_sidecar_serve_mode="prod-serve")

    with pytest.raises(MissingPinnedModelCacheError):
        ensure_prod_cache_requirements(settings, cache_state={"span": False, "embed": False})


def test_build_dependency_manifest_tracks_embedding_runtime_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("seektalent.prf_sidecar.service._python_lockfile_hash", lambda: "lock-hash")
    monkeypatch.setattr("seektalent.prf_sidecar.service._package_version", lambda name: "1.0.0")
    monkeypatch.setattr("seektalent.prf_sidecar.service._optional_package_version", lambda name: "1.0.0")
    monkeypatch.setenv("SEEKTALENT_PRF_SIDECAR_IMAGE_DIGEST", "sha256:image")

    manifest = build_dependency_manifest(
        _settings(prf_allow_remote_code=True, prf_remote_code_audit_revision="audit-rev"),
        embedding_model=FakeEmbeddingModel(
            embedding_dimension=3,
            normalized=False,
            pooling="cls",
            dtype="float16",
            max_input_tokens=4096,
        ),
    )

    assert manifest.sidecar_image_digest == "sha256:image"
    assert manifest.embedding_dimension == 3
    assert manifest.embedding_normalization is False
    assert manifest.dtype == "float16"
    assert manifest.max_input_tokens == 4096
    assert manifest.remote_code_policy == "approved_baked_code"
    assert manifest.remote_code_commit == "audit-rev"


def test_prod_loader_uses_local_files_only_and_offline_env(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, object] = {}

    class FakeGLiNER2:
        @staticmethod
        def from_pretrained(*args, **kwargs):
            calls["gliner"] = kwargs
            return object()

    monkeypatch.setattr(
        "seektalent.prf_sidecar.loaders._load_gliner_runtime_class",
        lambda: FakeGLiNER2,
    )
    settings = _settings(prf_sidecar_serve_mode="prod-serve")

    build_span_model(settings)

    assert os.environ["HF_HUB_OFFLINE"] == "1"
    assert calls["gliner"]["local_files_only"] is True


def test_real_span_model_extract_returns_rows_from_runtime_shim(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeRuntime:
        def predict_entities(self, text: str, labels: list[str]):
            if "Flink CDC" not in text:
                return []
            return [
                {
                    "text": "Flink CDC",
                    "label": "technical_phrase",
                    "score": 0.91,
                    "start": text.index("Flink CDC"),
                    "end": text.index("Flink CDC") + len("Flink CDC"),
                }
            ]

    class FakeGLiNER2:
        @staticmethod
        def from_pretrained(*args, **kwargs):
            return FakeRuntime()

    monkeypatch.setattr(
        "seektalent.prf_sidecar.loaders._load_gliner_runtime_class",
        lambda: FakeGLiNER2,
    )
    span_model = build_span_model(_settings(prf_sidecar_serve_mode="dev-bootstrap"))

    rows = span_model.extract(
        ["first text", "Need Flink CDC ownership"],
        ["technical_phrase"],
    )

    assert rows == [
        SpanExtractRow(
            request_text_index=1,
            surface="Flink CDC",
            label="technical_phrase",
            score=0.91,
            model_start_char=5,
            model_end_char=14,
            alignment_hint_only=True,
        )
    ]


def test_prod_embedding_loader_uses_local_files_only(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, object] = {}

    def fake_sentence_transformer(*args, **kwargs):
        calls["kwargs"] = kwargs
        return object()

    monkeypatch.setattr("seektalent.prf_sidecar.loaders._load_sentence_transformers_dependency", lambda: None)
    monkeypatch.setattr("seektalent.prf_sidecar.loaders.SentenceTransformer", fake_sentence_transformer)
    settings = _settings(
        prf_sidecar_serve_mode="prod-serve",
        prf_allow_remote_code=True,
        prf_remote_code_audit_revision="audit-rev",
    )

    build_embedding_model(settings)

    assert calls["kwargs"]["local_files_only"] is True


def test_embedding_loader_rejects_disallowed_remote_code() -> None:
    settings = _settings(
        prf_sidecar_serve_mode="prod-serve",
        prf_allow_remote_code=False,
    )

    with pytest.raises(RemoteCodePolicyError, match="requires approved remote code"):
        build_embedding_model(settings)


def test_embedding_loader_honors_remote_code_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, object] = {}

    def fake_sentence_transformer(*args, **kwargs):
        calls["kwargs"] = kwargs
        return object()

    monkeypatch.setattr("seektalent.prf_sidecar.loaders._load_sentence_transformers_dependency", lambda: None)
    monkeypatch.setattr("seektalent.prf_sidecar.loaders.SentenceTransformer", fake_sentence_transformer)
    settings = _settings(
        prf_allow_remote_code=True,
        prf_remote_code_audit_revision="audit-rev",
        prf_embedding_model_name="Alibaba-NLP/gte-multilingual-base",
    )

    build_embedding_model(settings)

    assert calls["kwargs"]["trust_remote_code"] is True


def test_embedding_loader_requires_audited_remote_code_revision() -> None:
    settings = _settings(
        prf_allow_remote_code=True,
        prf_remote_code_audit_revision=None,
        prf_embedding_model_name="Alibaba-NLP/gte-multilingual-base",
    )

    with pytest.raises(RemoteCodePolicyError, match="requires approved remote code"):
        build_embedding_model(settings)


def test_service_marks_not_ready_when_remote_code_policy_blocks_embedding_loader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("seektalent.prf_sidecar.service.build_span_model", lambda settings: FakeSpanModel())
    monkeypatch.setattr(
        "seektalent.prf_sidecar.service.build_embedding_model",
        lambda settings: (_ for _ in ()).throw(RemoteCodePolicyError("remote code disallowed")),
    )
    service = build_default_sidecar_service(
        settings=_settings(
            prf_sidecar_serve_mode="dev-bootstrap",
            prf_allow_remote_code=False,
        ),
        dependency_manifest=_manifest(),
        load_models=True,
    )

    ready = service.ready()

    assert ready.status == "not_ready"
    assert ready.not_ready_reason == "remote_code_disallowed"


def test_prefetch_plan_uses_pinned_revisions() -> None:
    settings = _settings()

    plan = build_prefetch_plan(settings)

    assert [entry["revision"] for entry in plan] == ["rev-span", "rev-tokenizer", "rev-embed"]
