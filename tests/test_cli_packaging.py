from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


def _bin_dir(venv_dir: Path) -> Path:
    return venv_dir / ("Scripts" if os.name == "nt" else "bin")


def _chat_response(*, message: dict[str, object], finish_reason: str) -> dict[str, object]:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 0,
        "model": "fake-openai",
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": finish_reason,
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def _requirement_draft_payload() -> dict[str, object]:
    return {
        "role_title_candidate": "Senior Python / LLM Engineer",
        "role_summary_candidate": "Build Python and retrieval systems.",
        "must_have_capability_candidates": [
            "Python backend",
            "LLM application",
            "retrieval pipeline",
        ],
        "preferred_capability_candidates": ["workflow orchestration"],
        "exclusion_signal_candidates": ["frontend"],
        "preference_candidates": {
            "preferred_domains": [],
            "preferred_backgrounds": [],
        },
        "hard_constraint_candidates": {},
        "scoring_rationale_candidate": "Prioritize core must-have fit.",
    }


def _bootstrap_keyword_draft_payload() -> dict[str, object]:
    return {
        "candidate_seeds": [
            {
                "intent_type": "core_precision",
                "keywords": ["agent engineer", "rag", "python backend"],
                "source_knowledge_pack_ids": [],
                "reasoning": "anchor the route",
            },
            {
                "intent_type": "must_have_alias",
                "keywords": ["llm application", "retrieval pipeline"],
                "source_knowledge_pack_ids": [],
                "reasoning": "cover aliases",
            },
            {
                "intent_type": "relaxed_floor",
                "keywords": ["python backend", "retrieval"],
                "source_knowledge_pack_ids": [],
                "reasoning": "widen recall",
            },
            {
                "intent_type": "pack_bridge",
                "keywords": ["workflow orchestration", "tool calling"],
                "source_knowledge_pack_ids": ["llm_agent_rag_engineering"],
                "reasoning": "use pack hints",
            },
            {
                "intent_type": "vocabulary_bridge",
                "keywords": ["backend engineer", "agent workflow"],
                "source_knowledge_pack_ids": [],
                "reasoning": "extra route",
            },
        ],
        "negative_keywords": ["frontend"],
    }


def _branch_evaluation_payload() -> dict[str, object]:
    return {
        "novelty_score": 0.8,
        "usefulness_score": 0.7,
        "branch_exhausted": True,
        "repair_operator_hint": "core_precision",
        "evaluation_notes": "Good expansion.",
    }


class _FakeLLMHandler(BaseHTTPRequestHandler):
    controller_calls = 0

    def do_POST(self) -> None:
        if self.path != "/v1/chat/completions":
            self.send_error(404)
            return
        payload = json.loads(self.rfile.read(int(self.headers.get("content-length", "0"))).decode())
        if "tools" in payload:
            response = self._tool_response(payload)
        else:
            response = self._prompted_response(payload)
        body = json.dumps(response).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _prompted_response(self, payload: dict[str, object]) -> dict[str, object]:
        messages = payload.get("messages", [])
        system_text = ""
        if isinstance(messages, list):
            for message in messages:
                if isinstance(message, dict) and message.get("role") == "system":
                    content = message.get("content")
                    if isinstance(content, str):
                        system_text = content
                        break
        if "role_title_candidate" in system_text:
            content = json.dumps(_requirement_draft_payload(), ensure_ascii=False)
        elif "candidate_seeds" in system_text:
            content = json.dumps(_bootstrap_keyword_draft_payload(), ensure_ascii=False)
        elif "novelty_score" in system_text:
            content = json.dumps(_branch_evaluation_payload(), ensure_ascii=False)
        elif "run_summary" in system_text:
            content = json.dumps({"run_summary": "The shortlist is ready for review."}, ensure_ascii=False)
        else:
            raise AssertionError(f"unexpected_prompted_request: {system_text}")
        return _chat_response(
            message={"role": "assistant", "content": content},
            finish_reason="stop",
        )

    def _tool_response(self, payload: dict[str, object]) -> dict[str, object]:
        _FakeLLMHandler.controller_calls += 1
        if _FakeLLMHandler.controller_calls == 1:
            arguments = {
                "action": "search_cts",
                "selected_operator_name": "core_precision",
                "operator_args": {"query_terms": ["python backend"]},
                "expected_gain_hypothesis": "Tighten around the strongest terms.",
            }
        else:
            arguments = {
                "action": "stop",
                "selected_operator_name": "must_have_alias",
                "operator_args": {},
                "expected_gain_hypothesis": "Stop now.",
            }
        tool = payload["tools"][0]
        name = tool["function"]["name"]
        return _chat_response(
            message={
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": f"call_{_FakeLLMHandler.controller_calls}",
                        "type": "function",
                        "function": {
                            "name": name,
                            "arguments": json.dumps(
                                {"response": arguments},
                                ensure_ascii=False,
                            ),
                        },
                    }
                ],
            },
            finish_reason="tool_calls",
        )

    def log_message(self, format: str, *args) -> None:
        del format, args


class _FakeRerankHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        if self.path != "/api/rerank":
            self.send_error(404)
            return
        payload = json.loads(self.rfile.read(int(self.headers.get("content-length", "0"))).decode())
        document_ids = [document["id"] for document in payload["documents"]]
        if document_ids and document_ids[0].startswith("mock-r"):
            scores = {
                "mock-r001": 2.0,
                "mock-r002": 1.8,
                "mock-r003": 1.6,
                "mock-r004": 1.0,
            }
        else:
            scores = {
                "llm_agent_rag_engineering": 1.2,
                "finance_risk_control_ai": 0.1,
            }
        ranked_ids = sorted(document_ids, key=lambda item_id: (-scores[item_id], document_ids.index(item_id)))
        response = {
            "model": "fake-reranker",
            "results": [
                {
                    "id": item_id,
                    "index": document_ids.index(item_id),
                    "score": scores[item_id],
                    "rank": rank,
                }
                for rank, item_id in enumerate(ranked_ids, start=1)
            ],
        }
        body = json.dumps(response).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:
        del format, args


class _ServerHandle:
    def __init__(self, handler: type[BaseHTTPRequestHandler]) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self) -> None:
        self.server.shutdown()
        self.thread.join(timeout=5)


def _wheel_contains_runtime_assets(wheel: Path) -> bool:
    import zipfile

    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
    expected = [
        "seektalent/_bundled/env.example",
        "seektalent/_bundled/artifacts/runtime/active.json",
        "seektalent/_bundled/artifacts/runtime/calibrations/qwen3-reranker-8b-mxfp8-2026-04-07-v1.json",
        "seektalent/_bundled/artifacts/runtime/policies/business-default-2026-04-09-v1.json",
        "seektalent/_bundled/artifacts/runtime/registries/school_types.json",
        "seektalent/_bundled/artifacts/knowledge/packs/llm_agent_rag_engineering.json",
    ]
    return all(any(name.endswith(target) for name in names) for target in expected)


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is required for wheel packaging tests")
def test_built_wheel_runs_outside_repo(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    subprocess.run(["uv", "build"], cwd=repo_root, check=True)
    wheel = max((repo_root / "dist").glob("seektalent-*.whl"))
    assert _wheel_contains_runtime_assets(wheel)

    venv_dir = tmp_path / "venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)
    bin_dir = _bin_dir(venv_dir)
    python = bin_dir / ("python.exe" if os.name == "nt" else "python")
    cli = bin_dir / ("seektalent.exe" if os.name == "nt" else "seektalent")

    subprocess.run([str(python), "-m", "pip", "install", str(wheel)], check=True)

    env = os.environ.copy()
    env.pop("PYTHONPATH", None)

    work_dir = tmp_path / "work"
    work_dir.mkdir()

    help_result = subprocess.run(
        [str(cli), "--help"],
        cwd=work_dir,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "Human entry" in help_result.stdout
    assert "Agent entry" in help_result.stdout

    version_result = subprocess.run(
        [str(cli), "version"],
        cwd=work_dir,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert version_result.stdout.strip() == "0.3.6"

    inspect_result = subprocess.run(
        [str(cli), "inspect", "--json"],
        cwd=work_dir,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    inspect_payload = json.loads(inspect_result.stdout)
    assert inspect_payload["phase"] == "v0.3.3_active"

    init_result = subprocess.run(
        [str(cli), "init"],
        cwd=work_dir,
        env=env,
        capture_output=True,
        text=True,
    )
    assert init_result.returncode == 0
    assert (work_dir / ".env").exists()
    assert "SEEKTALENT_REQUIREMENT_EXTRACTION_PROVIDER" in (work_dir / ".env").read_text(encoding="utf-8")

    doctor_env = work_dir / "doctor.env"
    doctor_env.write_text(
        "OPENAI_API_KEY=test-openai-key\nSEEKTALENT_MOCK_CTS=true\n",
        encoding="utf-8",
    )
    doctor_result = subprocess.run(
        [str(cli), "doctor", "--env-file", str(doctor_env), "--json"],
        cwd=work_dir,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    doctor_payload = json.loads(doctor_result.stdout)
    assert doctor_payload["ok"] is True

    _FakeLLMHandler.controller_calls = 0
    llm_server = _ServerHandle(_FakeLLMHandler)
    rerank_server = _ServerHandle(_FakeRerankHandler)
    try:
        run_env = work_dir / "run.env"
        run_env.write_text(
            "\n".join(
                [
                    "OPENAI_API_KEY=test-openai-key",
                    f"OPENAI_BASE_URL={llm_server.base_url}/v1",
                    "SEEKTALENT_MOCK_CTS=true",
                    f"SEEKTALENT_RERANK_BASE_URL={rerank_server.base_url}",
                    "SEEKTALENT_REQUIREMENT_EXTRACTION_OUTPUT_MODE=prompted",
                    "SEEKTALENT_BOOTSTRAP_KEYWORD_GENERATION_OUTPUT_MODE=prompted",
                    "SEEKTALENT_SEARCH_CONTROLLER_DECISION_OUTPUT_MODE=tool",
                    "SEEKTALENT_BRANCH_OUTCOME_EVALUATION_OUTPUT_MODE=prompted",
                    "SEEKTALENT_SEARCH_RUN_FINALIZATION_OUTPUT_MODE=prompted",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        request_file = work_dir / "request.json"
        request_file.write_text(
            json.dumps(
                {
                    "job_description": "Senior Python / LLM Engineer",
                    "hiring_notes": "Shanghai preferred",
                    "top_k": 3,
                    "round_budget": 20,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        run_result = subprocess.run(
            [
                str(cli),
                "run",
                "--request-file",
                str(request_file),
                "--env-file",
                str(run_env),
                "--json",
                "--progress",
                "off",
            ],
            cwd=work_dir,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
    finally:
        llm_server.close()
        rerank_server.close()

    bundle = json.loads(run_result.stdout)
    final_cards = bundle["final_result"]["final_candidate_cards"]
    assert bundle["phase"] == "v0.3.3_active"
    assert bundle["final_result"]["stop_reason"] == "controller_stop"
    assert isinstance(final_cards, list)
    assert len(final_cards) <= 3
