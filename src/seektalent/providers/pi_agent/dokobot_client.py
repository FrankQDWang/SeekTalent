from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from time import monotonic
from typing import Any, Literal, cast

from pydantic import AnyUrl, TypeAdapter

from seektalent.providers.pi_agent.contracts import DokoBotReadResult, PiArtifactRef, ProtectedArtifactClass


RunCommand = Callable[[list[str], int], subprocess.CompletedProcess[str]]
ArtifactWriter = Callable[[bytes, ProtectedArtifactClass, str], PiArtifactRef]
VerticalStopReason = Literal["end_of_scroll", "limit_reached", "timeout", "unknown"]

PROTECTED_SNAPSHOT_POLICY_ID = "liepin-protected-snapshot-v1"
COMMAND_ERROR_REDACTION_POLICY_ID = "dokobot-command-error-redaction-v1"
ANY_URL_ADAPTER = TypeAdapter(AnyUrl)


class DokoBotExecutionError(RuntimeError):
    def __init__(self, error_code: str, *, stderr_redacted_ref: PiArtifactRef | None = None) -> None:
        super().__init__(error_code)
        self.error_code = error_code
        self.stderr_redacted_ref = stderr_redacted_ref


class DokoBotClient:
    def __init__(
        self,
        *,
        run_command: RunCommand | None = None,
        artifact_writer: ArtifactWriter | None = None,
        json_capable_surface: bool = False,
        transport_mode: Literal["local_only", "remote_e2e_allowed"] = "local_only",
    ) -> None:
        self._run_command = run_command or _run_subprocess_command
        self._artifact_writer = artifact_writer or _missing_artifact_writer
        self._json_capable_surface = json_capable_surface
        self._transport_mode = transport_mode

    def read_url(
        self,
        url: str,
        *,
        timeout_seconds: int = 30,
        output_format: Literal["text", "chunks"] = "text",
        screens: int = 1,
        session_id: str | None = None,
        reuse_tab: bool = False,
    ) -> DokoBotReadResult:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if screens < 1 or screens > 100:
            raise ValueError("screens must be between 1 and 100")
        validated_url = ANY_URL_ADAPTER.validate_python(url)

        command = ["dokobot", "read", str(validated_url), "--format", output_format, "--screens", str(screens)]
        if self._transport_mode == "local_only":
            command.append("--local")
        if session_id is not None:
            command.extend(["--session-id", session_id])
        if reuse_tab:
            command.append("--reuse-tab")

        started_at = monotonic()
        try:
            result = self._run_command(command, timeout_seconds + 10)
        except subprocess.TimeoutExpired as exc:
            stderr_ref = self._write_redacted_stderr_ref("dokobot read timed out")
            raise DokoBotExecutionError("dokobot_read_timeout", stderr_redacted_ref=stderr_ref) from exc

        duration_ms = int((monotonic() - started_at) * 1000)
        if result.returncode != 0:
            stderr_ref = self._write_redacted_stderr_ref(result.stderr)
            error_code = "dokobot_local_transport_failed" if self._transport_mode == "local_only" else "dokobot_read_failed"
            raise DokoBotExecutionError(error_code, stderr_redacted_ref=stderr_ref)

        return self._read_result_from_process(
            url=validated_url,
            stdout=result.stdout,
            stderr=result.stderr,
            screens=screens,
            duration_ms=duration_ms,
        )

    def _read_result_from_process(
        self,
        *,
        url: AnyUrl,
        stdout: str,
        stderr: str,
        screens: int,
        duration_ms: int,
    ) -> DokoBotReadResult:
        stderr_ref = self._write_redacted_stderr_ref(stderr)
        parsed = _json_payload(stdout) if self._json_capable_surface else None
        session_id = _string_value(parsed, "sessionId") if parsed is not None else None
        session_id = session_id or _session_id_from_stderr(stderr)
        text_content = _read_text(stdout, parsed)
        chunks_content = _read_chunks(parsed)

        text_ref = (
            self._artifact_writer(
                text_content.encode("utf-8"),
                ProtectedArtifactClass.PROTECTED_PROVIDER_SNAPSHOT,
                PROTECTED_SNAPSHOT_POLICY_ID,
            )
            if text_content
            else None
        )
        chunks_ref = (
            self._artifact_writer(
                json.dumps(chunks_content, ensure_ascii=False, sort_keys=True).encode("utf-8"),
                ProtectedArtifactClass.PROTECTED_PROVIDER_SNAPSHOT,
                PROTECTED_SNAPSHOT_POLICY_ID,
            )
            if chunks_content is not None
            else None
        )
        vertical_has_more = _vertical_has_more(parsed, session_id)
        vertical_stop_reason = _vertical_stop_reason(parsed)
        screens_used = _screens_used(parsed, screens)

        return DokoBotReadResult(
            schema_version="dokobot-read-result-v1",
            url=url,
            text_ref=text_ref,
            chunks_ref=chunks_ref,
            session_id=session_id,
            vertical_has_more=vertical_has_more,
            vertical_stop_reason=vertical_stop_reason,
            screens_used=screens_used,
            duration_ms=duration_ms,
            stderr_redacted_ref=stderr_ref,
        )

    def _write_redacted_stderr_ref(self, stderr: str) -> PiArtifactRef | None:
        if not stderr:
            return None
        return self._artifact_writer(
            _redact_command_stderr(stderr).strip().encode("utf-8"),
            ProtectedArtifactClass.REDACTED_EVIDENCE,
            COMMAND_ERROR_REDACTION_POLICY_ID,
        )


def _run_subprocess_command(command: list[str], process_timeout_seconds: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=False,
        text=True,
        capture_output=True,
        timeout=process_timeout_seconds,
    )


def _missing_artifact_writer(
    _content: bytes,
    _artifact_class: ProtectedArtifactClass,
    _policy_id: str,
) -> PiArtifactRef:
    raise RuntimeError("artifact_writer is required")


def _json_payload(stdout: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _json_data(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if payload is None:
        return None
    data = payload.get("data")
    return data if isinstance(data, dict) else payload


def _read_text(stdout: str, payload: dict[str, Any] | None) -> str:
    data = _json_data(payload)
    if data is None:
        return stdout
    text = data.get("text")
    return text if isinstance(text, str) else stdout


def _read_chunks(payload: dict[str, Any] | None) -> Any | None:
    data = _json_data(payload)
    if data is None:
        return None
    chunks = data.get("chunks")
    return chunks if chunks is not None else None


def _string_value(payload: dict[str, Any] | None, key: str) -> str | None:
    data = _json_data(payload)
    if data is None:
        return None
    value = data.get(key)
    return value if isinstance(value, str) and value else None


def _session_id_from_stderr(stderr: str) -> str | None:
    match = re.search(r"(?m)^Session:\s+(\S+)", stderr)
    return match.group(1) if match else None


def _vertical_has_more(payload: dict[str, Any] | None, session_id: str | None) -> bool:
    data = _json_data(payload)
    if data is None:
        return session_id is not None
    vertical = data.get("vertical")
    if isinstance(vertical, dict) and isinstance(vertical.get("hasMore"), bool):
        return vertical["hasMore"]
    if isinstance(data.get("hasMore"), bool):
        return data["hasMore"]
    if isinstance(data.get("canContinue"), bool):
        return data["canContinue"]
    return session_id is not None


def _vertical_stop_reason(
    payload: dict[str, Any] | None,
) -> VerticalStopReason:
    allowed = {"end_of_scroll", "limit_reached", "timeout", "unknown"}
    data = _json_data(payload)
    if data is None:
        return "unknown"
    vertical = data.get("vertical")
    if isinstance(vertical, dict):
        stop_reason = vertical.get("stopReason")
        if isinstance(stop_reason, str) and stop_reason in allowed:
            return cast(VerticalStopReason, stop_reason)
    stop_reason = data.get("stopReason")
    if isinstance(stop_reason, str) and stop_reason in allowed:
        return cast(VerticalStopReason, stop_reason)
    return "unknown"


def _screens_used(payload: dict[str, Any] | None, fallback: int) -> int:
    data = _json_data(payload)
    if data is None:
        return fallback
    screens = data.get("screens")
    return screens if isinstance(screens, int) and screens >= 0 else fallback


def _redact_command_stderr(stderr: str) -> str:
    redacted = re.sub(r"(?m)^Session:\s+\S+", "Session: [REDACTED_SESSION]", stderr)
    redacted = re.sub(r"Bearer\s+\S+", "Bearer [REDACTED_TOKEN]", redacted)
    redacted = re.sub(r"(?i)(secret|token|api[_-]?key)[=:]\s*\S+", r"\1=[REDACTED]", redacted)
    return re.sub(r"\b1[3-9]\d{9}\b", "[REDACTED_PHONE]", redacted)
