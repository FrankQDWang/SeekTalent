from __future__ import annotations

from pathlib import Path

from seektalent.prf_sidecar.models import (
    EmbedRequest,
    EmbedResponse,
    SidecarDependencyManifest,
    SpanExtractRequest,
    SpanExtractResponse,
    SpanExtractRow,
)


def test_sidecar_embed_request_and_response_track_replay_critical_metadata() -> None:
    request = EmbedRequest(
        request_id="req-1",
        phrases=["Flink CDC", "实时数仓"],
        model_name="Alibaba-NLP/gte-multilingual-base",
        model_revision="rev-embed",
    )
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
        vectors=[[0.1, 0.2], [0.3, 0.4]],
    )

    assert request.request_id == "req-1"
    assert response.embedding_dimension == 768
    assert response.normalized is True


def test_sidecar_span_extract_models_track_batch_provenance() -> None:
    request = SpanExtractRequest(
        request_id="req-1",
        texts=["Flink CDC 链路开发", "FastAPI/Flask/Django"],
        labels=["technical_phrase", "tool_or_framework"],
        schema_version="gliner2-schema-v1",
        model_name="fastino/gliner2-multi-v1",
        model_revision="rev-span",
    )
    response = SpanExtractResponse(
        schema_version="prf-sidecar-span-v1",
        model_name="fastino/gliner2-multi-v1",
        model_revision="rev-span",
        rows=[
            SpanExtractRow(
                request_text_index=1,
                surface="FastAPI",
                label="tool_or_framework",
                score=0.91,
                model_start_char=0,
                model_end_char=7,
                alignment_hint_only=True,
            )
        ],
    )

    assert request.schema_version == "gliner2-schema-v1"
    assert response.rows[0].request_text_index == 1
    assert response.rows[0].alignment_hint_only is True


def test_sidecar_dependency_manifest_hash_is_deterministic() -> None:
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


def test_sidecar_default_install_does_not_require_model_serving_dependencies() -> None:
    pyproject_text = Path("pyproject.toml").read_text(encoding="utf-8")
    base_section, _optional_section = pyproject_text.split("[project.optional-dependencies]", maxsplit=1)

    assert "torch>=" not in base_section
    assert "transformers>=" not in base_section
    assert "sentence-transformers>=" not in base_section
    assert "huggingface-hub>=" not in base_section
