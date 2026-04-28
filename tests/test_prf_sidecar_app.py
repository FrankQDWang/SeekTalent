from __future__ import annotations

from fastapi.testclient import TestClient

from seektalent.prf_sidecar.app import create_sidecar_app
from seektalent.prf_sidecar.models import (
    EmbedResponse,
    SpanExtractResponse,
    SpanExtractRow,
)
from seektalent.prf_sidecar.service import BatchLimitError, ErrorResponse, ReadyResponse


class FakeService:
    def __init__(self, *, ready: bool) -> None:
        self._ready = ready
        self._max_batch_size = 32

    def live(self) -> dict[str, str]:
        return {"status": "alive"}

    def ready(self) -> ReadyResponse:
        return ReadyResponse(
            status="ready" if self._ready else "not_ready",
            endpoint_contract_version="prf-sidecar-http-v1",
            dependency_manifest_hash="manifest-hash",
            sidecar_image_digest="sha256:image",
            span_model_loaded=self._ready,
            embedding_model_loaded=self._ready,
            span_model_name="fastino/gliner2-multi-v1",
            span_model_revision="rev-span",
            span_tokenizer_revision="rev-tokenizer",
            embedding_model_name="Alibaba-NLP/gte-multilingual-base",
            embedding_model_revision="rev-embed",
        )

    def span_extract(self, request):  # pragma: no cover - exercised through the API
        if len(request.texts) > self._max_batch_size:
            raise BatchLimitError(request_id=request.request_id, limit=self._max_batch_size)
        return SpanExtractResponse(
            schema_version="prf-sidecar-span-v1",
            model_name=request.model_name,
            model_revision=request.model_revision,
            rows=[
                SpanExtractRow(
                    request_text_index=0,
                    surface="Flink CDC",
                    label="technical_phrase",
                    score=0.9,
                    model_start_char=0,
                    model_end_char=9,
                    alignment_hint_only=True,
                )
            ],
        )

    def embed(self, request):  # pragma: no cover - exercised through the API
        if len(request.phrases) > self._max_batch_size:
            raise BatchLimitError(request_id=request.request_id, limit=self._max_batch_size)
        return EmbedResponse(
            schema_version="prf-sidecar-embed-v1",
            model_name=request.model_name,
            model_revision=request.model_revision,
            embedding_dimension=2,
            normalized=True,
            pooling="mean",
            dtype="float32",
            max_input_tokens=8192,
            truncation=True,
            vectors=[[0.1, 0.2] for _ in request.phrases],
        )


def test_livez_returns_alive_status() -> None:
    client = TestClient(create_sidecar_app(service=FakeService(ready=False)))

    response = client.get("/livez")

    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


def test_readyz_returns_503_when_models_not_loaded() -> None:
    client = TestClient(create_sidecar_app(service=FakeService(ready=False)))

    response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"


def test_readyz_returns_200_when_models_loaded() -> None:
    client = TestClient(create_sidecar_app(service=FakeService(ready=True)))

    response = client.get("/readyz")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_span_extract_returns_structured_response() -> None:
    client = TestClient(create_sidecar_app(service=FakeService(ready=True)))

    response = client.post(
        "/v1/span-extract",
        json={
            "request_id": "req-1",
            "texts": ["Flink CDC"],
            "labels": ["technical_phrase"],
            "schema_version": "gliner2-schema-v1",
            "model_name": "fastino/gliner2-multi-v1",
            "model_revision": "rev-span",
        },
    )

    assert response.status_code == 200
    assert response.json()["rows"][0]["surface"] == "Flink CDC"


def test_embed_returns_structured_response() -> None:
    client = TestClient(create_sidecar_app(service=FakeService(ready=True)))

    response = client.post(
        "/v1/embed",
        json={
            "request_id": "req-2",
            "phrases": ["Flink CDC"],
            "model_name": "Alibaba-NLP/gte-multilingual-base",
            "model_revision": "rev-embed",
        },
    )

    assert response.status_code == 200
    assert response.json()["embedding_dimension"] == 2


def test_span_extract_returns_structured_error_for_batch_limit() -> None:
    client = TestClient(create_sidecar_app(service=FakeService(ready=True)))

    response = client.post(
        "/v1/span-extract",
        json={
            "request_id": "req-3",
            "texts": ["a" for _ in range(33)],
            "labels": ["technical_phrase"],
            "schema_version": "gliner2-schema-v1",
            "model_name": "fastino/gliner2-multi-v1",
            "model_revision": "rev-span",
        },
    )

    assert response.status_code == 413
    payload = ErrorResponse.model_validate(response.json())
    assert payload.code == "batch_limit_exceeded"
