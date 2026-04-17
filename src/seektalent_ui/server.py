from __future__ import annotations

import argparse
import json
import re
import threading
import uuid
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

from pydantic import ValidationError

from seektalent.config import AppSettings, load_process_env
from seektalent.runtime import WorkflowRuntime
from seektalent_ui.mapper import build_ui_payloads
from seektalent_ui.models import CandidateDetailResponse, RunCreateRequest, RunCreateResponse, RunStatus, RunStatusResponse


@dataclass
class UiRunRecord:
    run_id: str
    job_title: str
    jd_text: str
    sourcing_preference_text: str
    status: RunStatus = "queued"
    error_message: str | None = None
    final_shortlist: list = field(default_factory=list)
    candidate_details: dict[str, CandidateDetailResponse] = field(default_factory=dict)


class RunNotFoundError(KeyError):
    pass


class CandidateNotFoundError(KeyError):
    pass


class RunNotReadyError(RuntimeError):
    pass


class RunRegistry:
    def __init__(
        self,
        settings: AppSettings,
        *,
        runtime_factory=WorkflowRuntime,
    ) -> None:
        self.settings = settings
        self.runtime_factory = runtime_factory
        self._lock = threading.Lock()
        self._runs: dict[str, UiRunRecord] = {}

    def create_run(self, *, job_title: str, jd_text: str, sourcing_preference_text: str) -> RunCreateResponse:
        job_title = job_title.strip()
        jd_text = jd_text.strip()
        sourcing_preference_text = sourcing_preference_text.strip()
        if not job_title:
            raise ValueError("jobTitle must not be empty.")
        if not jd_text:
            raise ValueError("jdText must not be empty.")
        run_id = f"web-{uuid.uuid4().hex[:8]}"
        record = UiRunRecord(
            run_id=run_id,
            job_title=job_title,
            jd_text=jd_text,
            sourcing_preference_text=sourcing_preference_text,
        )
        with self._lock:
            self._runs[run_id] = record
        worker = threading.Thread(
            target=self._run_workflow,
            args=(run_id,),
            name=f"seektalent-ui-{run_id}",
            daemon=True,
        )
        worker.start()
        return RunCreateResponse(runId=run_id, status="queued")

    def get_run_response(self, run_id: str) -> RunStatusResponse:
        record = self._get_record(run_id)
        return RunStatusResponse(
            runId=record.run_id,
            status=record.status,
            errorMessage=record.error_message,
            finalShortlist=record.final_shortlist,
        )

    def get_candidate_detail(self, run_id: str, candidate_id: str) -> CandidateDetailResponse:
        record = self._get_record(run_id)
        if record.status != "completed":
            raise RunNotReadyError(f"Run {run_id} is not completed yet.")
        detail = record.candidate_details.get(candidate_id)
        if detail is None:
            raise CandidateNotFoundError(candidate_id)
        return detail

    def _run_workflow(self, run_id: str) -> None:
        runtime = self.runtime_factory(self.settings)
        with self._lock:
            self._runs[run_id].status = "running"
        try:
            artifacts = runtime.run(
                job_title=self._runs[run_id].job_title,
                jd=self._runs[run_id].jd_text,
                notes=self._runs[run_id].sourcing_preference_text,
            )
            shortlist, details = build_ui_payloads(
                artifacts.final_result,
                artifacts.candidate_store,
                artifacts.normalized_store,
            )
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                record = self._runs[run_id]
                record.status = "failed"
                record.error_message = str(exc) or "Run failed."
            return

        with self._lock:
            record = self._runs[run_id]
            record.status = "completed"
            record.error_message = None
            record.final_shortlist = shortlist
            record.candidate_details = details

    def _get_record(self, run_id: str) -> UiRunRecord:
        with self._lock:
            record = self._runs.get(run_id)
            if record is None:
                raise RunNotFoundError(run_id)
            return record


def create_server(host: str, port: int, registry: RunRegistry) -> ThreadingHTTPServer:
    class UiApiHandler(BaseHTTPRequestHandler):
        server_version = "SeekTalentUiApi/0.1"

        def log_message(self, format: str, *args) -> None:  # noqa: A003
            return

        def do_OPTIONS(self) -> None:  # noqa: N802
            self.send_response(HTTPStatus.NO_CONTENT)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

        def do_POST(self) -> None:  # noqa: N802
            if urlparse(self.path).path != "/api/runs":
                self._send_not_found()
                return
            try:
                payload = self._read_json()
                request = RunCreateRequest.model_validate(payload)
                response = registry.create_run(
                    job_title=request.jobTitle.strip(),
                    jd_text=request.jdText.strip(),
                    sourcing_preference_text=request.sourcingPreferenceText.strip(),
                )
            except json.JSONDecodeError as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": f"Invalid JSON body: {exc.msg}"})
                return
            except ValidationError as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": exc.errors()})
                return
            except ValueError as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            self._send_json(HTTPStatus.CREATED, response.model_dump(mode="json"))

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            detail_match = re.fullmatch(r"/api/runs/([^/]+)/candidates/([^/]+)", path)
            if detail_match is not None:
                run_id = unquote(detail_match.group(1))
                candidate_id = unquote(detail_match.group(2))
                try:
                    detail = registry.get_candidate_detail(run_id, candidate_id)
                except RunNotFoundError:
                    self._send_not_found()
                    return
                except CandidateNotFoundError:
                    self._send_not_found()
                    return
                except RunNotReadyError as exc:
                    self._send_json(HTTPStatus.CONFLICT, {"error": str(exc)})
                    return
                self._send_json(HTTPStatus.OK, detail.model_dump(mode="json"))
                return

            run_match = re.fullmatch(r"/api/runs/([^/]+)", path)
            if run_match is None:
                self._send_not_found()
                return

            run_id = unquote(run_match.group(1))
            try:
                payload = registry.get_run_response(run_id)
            except RunNotFoundError:
                self._send_not_found()
                return
            self._send_json(HTTPStatus.OK, payload.model_dump(mode="json"))

        def _read_json(self) -> dict[str, object]:
            raw_length = self.headers.get("Content-Length")
            if raw_length is None:
                raise ValueError("Missing Content-Length header.")
            content_length = int(raw_length)
            body = self.rfile.read(content_length)
            return json.loads(body.decode("utf-8"))

        def _send_json(self, status: HTTPStatus, payload: object) -> None:
            encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(encoded)

        def _send_not_found(self) -> None:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Not found."})

    server = ThreadingHTTPServer((host, port), UiApiHandler)
    server.daemon_threads = True
    return server


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local API server for the SeekTalent minimal web UI.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8011)
    parser.add_argument("--mock-cts", dest="mock_cts", action="store_true", default=None)
    parser.add_argument("--real-cts", dest="mock_cts", action="store_false")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    load_process_env()
    settings = AppSettings().with_overrides(mock_cts=args.mock_cts)
    registry = RunRegistry(settings)
    server = create_server(args.host, args.port, registry)
    print(f"SeekTalent UI API listening on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
