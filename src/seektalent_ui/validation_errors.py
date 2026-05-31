from __future__ import annotations

from collections.abc import Mapping

from fastapi.exceptions import RequestValidationError


def public_validation_errors(exc: RequestValidationError) -> list[dict[str, object]]:
    public_errors: list[dict[str, object]] = []
    for error in exc.errors():
        if not isinstance(error, Mapping):
            continue
        public_errors.append(
            {
                "type": error.get("type", "value_error"),
                "loc": list(error.get("loc", ())),
                "msg": error.get("msg", "Invalid input."),
            }
        )
    return public_errors
