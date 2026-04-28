from __future__ import annotations

from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse
import uvicorn

from seektalent.config import AppSettings
from seektalent.prf_sidecar.models import (
    EmbedRequest,
    EmbedResponse,
    SpanExtractRequest,
    SpanExtractResponse,
)
from seektalent.prf_sidecar.service import (
    BatchLimitError,
    ErrorResponse,
    LiveResponse,
    PayloadLimitError,
    ReadyResponse,
    SidecarService,
    build_default_sidecar_service,
    resolve_sidecar_bind_host,
)


def _error_response(*, code: str, message: str, request_id: str | None, status_code: int) -> JSONResponse:
    payload = ErrorResponse(code=code, message=message, request_id=request_id)
    return JSONResponse(status_code=status_code, content=payload.model_dump(mode="json"))


def create_sidecar_app(
    *,
    service: SidecarService,
) -> FastAPI:
    app = FastAPI(title="PRF Model Sidecar", version="0.1.0")

    @app.get("/livez", response_model=LiveResponse)
    def livez() -> LiveResponse:
        return service.live()

    @app.get("/readyz", response_model=ReadyResponse)
    def readyz(response: Response) -> ReadyResponse:
        ready = service.ready()
        if ready.status != "ready":
            response.status_code = 503
        return ready

    @app.post("/v1/span-extract", response_model=SpanExtractResponse)
    def span_extract(request: SpanExtractRequest):
        try:
            return service.span_extract(request)
        except BatchLimitError as exc:
            return _error_response(
                code="batch_limit_exceeded",
                message=str(exc),
                request_id=exc.request_id,
                status_code=413,
            )
        except PayloadLimitError as exc:
            return _error_response(
                code="payload_limit_exceeded",
                message=str(exc),
                request_id=exc.request_id,
                status_code=413,
            )

    @app.post("/v1/embed", response_model=EmbedResponse)
    def embed(request: EmbedRequest):
        try:
            return service.embed(request)
        except BatchLimitError as exc:
            return _error_response(
                code="batch_limit_exceeded",
                message=str(exc),
                request_id=exc.request_id,
                status_code=413,
            )
        except PayloadLimitError as exc:
            return _error_response(
                code="payload_limit_exceeded",
                message=str(exc),
                request_id=exc.request_id,
                status_code=413,
            )

    return app


def main() -> None:
    settings = AppSettings()
    service = build_default_sidecar_service(settings=settings, load_models=True)
    uvicorn.run(
        create_sidecar_app(service=service),
        host=resolve_sidecar_bind_host(settings),
        port=8741,
    )


if __name__ == "__main__":  # pragma: no cover
    main()
