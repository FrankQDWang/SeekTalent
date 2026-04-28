from __future__ import annotations

from uuid import uuid4

import httpx
from pydantic import ValidationError

from seektalent.prf_sidecar.models import (
    EmbedRequest,
    EmbedResponse,
    SpanExtractRequest,
    SpanExtractResponse,
)
from seektalent.prf_sidecar.service import ReadyResponse


class SidecarTimeout(RuntimeError):
    """The sidecar did not respond before the configured timeout."""


class SidecarUnavailable(RuntimeError):
    """The sidecar could not be reached or returned an HTTP error."""


class SidecarSchemaMismatch(RuntimeError):
    """The sidecar returned a payload that violates the expected contract."""


class SidecarMalformedResponse(RuntimeError):
    """The sidecar returned a non-JSON or otherwise malformed response body."""


class SidecarRevisionMismatch(RuntimeError):
    """The sidecar served a different model revision than requested."""


class SidecarEmbeddingUnavailable(SidecarUnavailable):
    """The embedding endpoint could not provide a usable response."""


class HttpSpanModelBackend:
    def __init__(
        self,
        *,
        endpoint: str,
        model_name: str,
        model_revision: str,
        schema_version: str,
        timeout_seconds: float,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.model_name = model_name
        self.model_revision = model_revision
        self.schema_version = schema_version
        self.timeout_seconds = timeout_seconds
        self.http_client = http_client

    def extract(self, *, text: str, labels: list[str]) -> list[dict[str, object]]:
        request = SpanExtractRequest(
            request_id=f"span-{uuid4().hex}",
            texts=[text],
            labels=labels,
            schema_version=self.schema_version,
            model_name=self.model_name,
            model_revision=self.model_revision,
        )
        payload = _post_json(
            endpoint=self.endpoint,
            path="/v1/span-extract",
            request_payload=request.model_dump(mode="json"),
            timeout_seconds=self.timeout_seconds,
            http_client=self.http_client,
            unavailable_exc=SidecarUnavailable,
        )
        try:
            response = SpanExtractResponse.model_validate(payload)
        except ValidationError as exc:
            raise SidecarSchemaMismatch("sidecar returned an invalid span response") from exc

        _validate_model_revision(
            model_name=response.model_name,
            expected_model_name=self.model_name,
            model_revision=response.model_revision,
            expected_model_revision=self.model_revision,
            context="span",
        )
        rows: list[dict[str, object]] = []
        for row in response.rows:
            if row.request_text_index != 0:
                raise SidecarSchemaMismatch("sidecar span row points at an unexpected request_text_index")
            rows.append(row.model_dump(mode="json"))
        return rows


class HttpEmbeddingBackend:
    def __init__(
        self,
        *,
        endpoint: str,
        model_name: str,
        model_revision: str,
        timeout_seconds: float,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.model_name = model_name
        self.model_revision = model_revision
        self.timeout_seconds = timeout_seconds
        self.http_client = http_client
        self.last_response: EmbedResponse | None = None
        self.last_failure_reason: str | None = None

    def embed(self, phrases: list[str]) -> EmbedResponse:
        request = EmbedRequest(
            request_id=f"embed-{uuid4().hex}",
            phrases=phrases,
            model_name=self.model_name,
            model_revision=self.model_revision,
        )
        payload = _post_json(
            endpoint=self.endpoint,
            path="/v1/embed",
            request_payload=request.model_dump(mode="json"),
            timeout_seconds=self.timeout_seconds,
            http_client=self.http_client,
            unavailable_exc=SidecarEmbeddingUnavailable,
        )
        try:
            response = EmbedResponse.model_validate(payload)
        except ValidationError as exc:
            raise SidecarSchemaMismatch("sidecar returned an invalid embedding response") from exc

        _validate_model_revision(
            model_name=response.model_name,
            expected_model_name=self.model_name,
            model_revision=response.model_revision,
            expected_model_revision=self.model_revision,
            context="embedding",
        )
        if len(response.vectors) != len(phrases):
            raise SidecarSchemaMismatch("sidecar returned a mismatched number of embedding vectors")
        for vector in response.vectors:
            if len(vector) != response.embedding_dimension:
                raise SidecarSchemaMismatch("sidecar returned an embedding vector with the wrong dimension")
        self.last_failure_reason = None
        self.last_response = response
        return response


def fetch_sidecar_readyz(
    *,
    endpoint: str,
    timeout_seconds: float,
    http_client: httpx.Client | None = None,
) -> ReadyResponse:
    response = None
    try:
        if http_client is None:
            with httpx.Client(timeout=timeout_seconds) as client:
                response = client.get(f"{endpoint.rstrip('/')}/readyz")
        else:
            response = http_client.get(f"{endpoint.rstrip('/')}/readyz", timeout=timeout_seconds)
    except httpx.TimeoutException as exc:
        raise SidecarTimeout("sidecar readyz request timed out") from exc
    except httpx.HTTPError as exc:
        raise SidecarUnavailable("sidecar readyz request failed") from exc
    try:
        ready = ReadyResponse.model_validate(response.json())
    except ValueError as exc:
        raise SidecarMalformedResponse("sidecar readyz response was not valid JSON") from exc
    except ValidationError as exc:
        raise SidecarSchemaMismatch("sidecar readyz response violated the expected contract") from exc
    if response.status_code not in {200, 503}:
        raise SidecarUnavailable("sidecar readyz request returned an unexpected status")
    return ready


def _post_json(
    *,
    endpoint: str,
    path: str,
    request_payload: dict[str, object],
    timeout_seconds: float,
    http_client: httpx.Client | None,
    unavailable_exc: type[RuntimeError],
) -> dict[str, object]:
    response = None
    try:
        if http_client is None:
            with httpx.Client(timeout=timeout_seconds) as client:
                response = client.post(f"{endpoint}{path}", json=request_payload)
        else:
            response = http_client.post(f"{endpoint}{path}", json=request_payload, timeout=timeout_seconds)
        response.raise_for_status()
    except httpx.TimeoutException as exc:
        raise SidecarTimeout("sidecar request timed out") from exc
    except httpx.HTTPError as exc:
        raise unavailable_exc("sidecar request failed") from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise SidecarMalformedResponse("sidecar response was not valid JSON") from exc
    if not isinstance(payload, dict):
        raise SidecarMalformedResponse("sidecar response root must be a JSON object")
    return payload


def _validate_model_revision(
    *,
    model_name: str,
    expected_model_name: str,
    model_revision: str,
    expected_model_revision: str,
    context: str,
) -> None:
    if model_name != expected_model_name or model_revision != expected_model_revision:
        raise SidecarRevisionMismatch(f"sidecar returned an unexpected {context} model revision")
