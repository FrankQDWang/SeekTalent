"""Stable, privacy-safe diagnostics boundary errors."""


class DiagnosticsReason:
    RAW_INPUT_REQUIRED = "diagnostics_raw_input_required"
    INVALID_UTF8 = "diagnostics_invalid_utf8"
    INVALID_JSON = "diagnostics_invalid_json"
    DUPLICATE_KEY = "diagnostics_duplicate_key"
    ILLEGAL_NUMBER = "diagnostics_illegal_number"
    INVALID_UNICODE = "diagnostics_invalid_unicode"
    ROOT_NOT_OBJECT = "diagnostics_root_not_object"
    UNKNOWN_SCHEMA = "diagnostics_unknown_schema"
    PAYLOAD_TOO_LARGE = "diagnostics_payload_too_large"
    SCHEMA_VALIDATION = "diagnostics_schema_validation"


class DiagnosticsSchemaError(ValueError):
    def __init__(self, reason: str, location: tuple[str | int, ...] = ()) -> None:
        self.reason = reason
        self.location = location
        super().__init__(reason)
