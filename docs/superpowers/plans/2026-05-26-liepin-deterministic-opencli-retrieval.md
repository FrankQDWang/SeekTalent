# Liepin Deterministic OpenCLI Retrieval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Liepin Pi child-agent retrieval with deterministic OpenCLI resume retrieval and remove the obsolete Liepin Pi path after parity.

**Architecture:** Add an `opencli` Liepin worker mode that drives the existing OpenCLI browser runner directly from Python. The worker maps deterministic detail-backed resume output into the existing Liepin worker contracts and runtime source-lane results, while raw browser evidence and normalized resume artifacts remain protected backend artifacts. Once the deterministic worker is active and tested, delete the Liepin-specific Pi executor, worker client, prompt, and config branches.

**Tech Stack:** Python 3.12, Pydantic, pytest, existing OpenCLI browser runner, existing Liepin worker contracts, existing runtime source-lane contracts, Svelte frontend source display tests for stale reason-code cleanup.

---

## Spec Link

This plan implements:

`docs/superpowers/specs/2026-05-26-liepin-deterministic-opencli-retrieval-design.md`

## Execution Notes

- Do not change CTS retrieval behavior.
- Do not change requirement extraction, scoring, reflection, finalizer, or runtime graph contracts.
- Do not add LLM calls.
- Do not preserve a production Pi fallback after the deterministic OpenCLI path passes tests.
- Any remaining `Pi` references in this plan must be one of:
  - existing file paths that currently house reusable OpenCLI runner code
  - explicit deletion targets
  - temporary Task 3 migration state that is removed by Task 6
  Final active Liepin runtime execution must not use a Pi child agent, Pi worker mode, Pi prompt, or Pi setup branch.
- Use fake OpenCLI runners in unit tests; use live OpenCLI only in the final manual smoke step.
- Keep source-status reason codes public-safe.
- The current worktree is dirty; stage and commit only files changed for this plan.

## File Map

Deterministic parser and retriever:

- Create: `src/seektalent/providers/liepin/opencli_resume_parser.py`
  - Own deterministic raw-page-to-normalized-resume parsing and noise removal.
- Create: `src/seektalent/providers/liepin/opencli_retriever.py`
  - Own deterministic source-lane resume retrieval orchestration over `OpenCliBrowserRunner`.
- Modify: `src/seektalent/providers/pi_agent/opencli_browser.py`
  - Delegate detail payload normalization to `opencli_resume_parser`.
  - Add `search_liepin_resumes()` so the browser runner can execute a full deterministic search/detail/finalize flow without Pi.
- Modify: `src/seektalent/providers/pi_agent/opencli_browser_cli.py`
  - Add a `search_resumes` action for direct debugging and parity tests.

Worker wiring:

- Create: `src/seektalent/providers/liepin/opencli_worker_client.py`
  - Implement `LiepinWorkerClient` for `liepin_worker_mode="opencli"`.
- Modify: `src/seektalent/providers/liepin/client.py`
  - Add `opencli` to live worker modes and build the OpenCLI worker client.
- Modify: `src/seektalent/config.py`
  - Add `opencli` to `LiepinWorkerMode`.
  - Remove Liepin Pi config fields after cleanup.
- Modify: `src/seektalent/default.env`
  - Switch local workbench Liepin mode to `opencli`.
- Modify: `.env.example`
  - Document the OpenCLI mode without Pi fields.
- Modify: `scripts/start-dev-workbench.sh`
  - Start backend with OpenCLI mode only.

Runtime and budget verification:

- Modify: `src/seektalent/runtime/source_query_intent.py`
  - Keep Liepin count caps at exploit 2 / explore 1 and add regressions around first-round exploit-only behavior.
- Modify: `src/seektalent/providers/liepin/runtime_lane.py`
  - Ensure detail-backed OpenCLI output populates `candidate_store_updates`, source evidence refs, provider snapshot refs, and source-lane events. Keep `normalized_store_updates` empty unless the existing runtime contract already has a concrete normalized-resume object to write.

Cleanup:

- Delete: `src/seektalent/providers/liepin/pi_executor.py`
- Delete: `src/seektalent/providers/liepin/pi_worker_client.py`
- Delete: `src/seektalent/providers/liepin/pi_resume_contract.py`
- Delete: `src/seektalent/providers/pi_agent/pi_skills/liepin_search_cards.md`
- Modify or delete tests that only validate Liepin Pi behavior:
  - `tests/test_liepin_pi_executor.py`
  - `tests/test_liepin_pi_worker_client.py`
  - `tests/test_pi_external_agent.py`
  - `tests/test_liepin_pi_skills.py`
  - `tests/test_liepin_live_pi_agent.py`
- Keep reusable OpenCLI browser-runner files only when still used by deterministic OpenCLI or unrelated non-Liepin tests, even if their current directory is still named `pi_agent`.

Tests:

- Create: `tests/test_liepin_opencli_resume_parser.py`
- Create: `tests/test_liepin_opencli_retriever.py`
- Create: `tests/test_liepin_opencli_worker_client.py`
- Modify: `tests/test_liepin_config.py`
- Modify: `tests/test_liepin_runtime_source_lane.py`
- Modify: `tests/test_runtime_multi_source_round_dispatch.py`
- Modify: `tests/test_runtime_source_lanes.py`
- Modify: `tests/test_workbench_api.py`
- Modify: `apps/web-svelte/src/lib/workbench/sourceDisplay.test.ts`

---

### Task 1: Add Deterministic Resume Parser Tests

**Files:**
- Create: `tests/test_liepin_opencli_resume_parser.py`
- Create: `src/seektalent/providers/liepin/opencli_resume_parser.py`

- [ ] **Step 1: Write the failing parser tests**

Create `tests/test_liepin_opencli_resume_parser.py`:

```python
from __future__ import annotations

from seektalent.providers.liepin.opencli_resume_parser import build_liepin_opencli_detail_payload


def test_opencli_resume_parser_removes_page_chrome_and_keeps_resume_sections() -> None:
    raw_text = """
    首页
    搜索
    筛选
    推荐职位
    联系候选人
    查看联系方式
    下载简历
    当前职位：数据开发专家
    当前公司：恒生电子股份有限公司
    工作经历
    负责数据平台、ETL、数据治理和自动化任务建设。
    项目经历
    建设大规模数据仓库和日志分析平台。
    教育经历
    浙江大学 本科 计算机科学
    技能
    Python SQL Flink ClickHouse
    """

    payload = build_liepin_opencli_detail_payload(raw_text)

    assert payload["currentTitle"] == "数据开发专家"
    assert payload["currentCompany"] == "恒生电子股份有限公司"
    assert "工作经历" in payload["fullText"]
    assert "负责数据平台" in payload["fullText"]
    assert "浙江大学" in payload["fullText"]
    assert "联系候选人" not in payload["fullText"]
    assert "查看联系方式" not in payload["fullText"]
    assert "推荐职位" not in payload["fullText"]


def test_opencli_resume_parser_deduplicates_lines_without_semantic_filtering() -> None:
    raw_text = """
    当前职位：数据开发专家
    当前职位：数据开发专家
    工作经历
    负责数据平台。
    负责数据平台。
    低匹配但仍是候选人真实经历，应保留。
    """

    payload = build_liepin_opencli_detail_payload(raw_text)

    assert payload["fullText"].count("当前职位：数据开发专家") == 1
    assert payload["fullText"].count("负责数据平台。") == 1
    assert "低匹配但仍是候选人真实经历，应保留。" in payload["fullText"]
```

- [ ] **Step 2: Run the parser tests and verify they fail because the module does not exist**

Run:

```bash
uv run pytest tests/test_liepin_opencli_resume_parser.py -q
```

Expected:

```text
ModuleNotFoundError: No module named 'seektalent.providers.liepin.opencli_resume_parser'
```

- [ ] **Step 3: Implement the deterministic parser**

Create `src/seektalent/providers/liepin/opencli_resume_parser.py`:

```python
from __future__ import annotations

import re
from collections.abc import Sequence


_CONTACT_TEXT_PATTERN = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b|(?:\+?86[-\s]?)?1[3-9]\d{9}\b|"
    r"(?:手机|电话|邮箱|微信|weixin|wechat|wx[:：])",
    re.IGNORECASE,
)

_DROP_LINE_MARKERS = (
    "首页",
    "搜索",
    "筛选",
    "推荐职位",
    "联系候选人",
    "查看联系方式",
    "聊天",
    "下载简历",
    "付费",
    "购买",
    "广告",
    "人才推荐",
)


def build_liepin_opencli_detail_payload(text: str) -> dict[str, object]:
    lines = _resume_lines(text)
    if not lines:
        raise ValueError("liepin_opencli_resume_text_empty")
    full_text = _bounded_public_text("\n".join(lines), max_chars=12_000)
    company, title = _company_title_from_text(full_text)
    title = title or _field_value(full_text, ("当前职位", "职位", "求职意向"))
    education_items = [
        {"school": school, "degree": _education_from_text(full_text), "speciality": None}
        for school in _school_names_from_text(full_text)
    ]
    return {
        "fullText": full_text,
        "currentTitle": title,
        "currentCompany": company,
        "workExperienceList": [
            {"company": company, "title": title, "summary": _recent_experience_from_text(full_text)}
        ],
        "educationList": education_items,
        "skills": _skill_tags_from_text(full_text),
        "locations": [city] if (city := _city_from_text(full_text)) else [],
    }


def _resume_lines(text: str) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw_line in text.splitlines():
        line = re.sub(r"\[[^\]]+\]", "", raw_line)
        line = re.sub(r"<[^>]*>", " ", line)
        line = re.sub(r"\s+", " ", line).strip()
        if not line:
            continue
        if _CONTACT_TEXT_PATTERN.search(line):
            continue
        if any(marker in line for marker in _DROP_LINE_MARKERS):
            continue
        if line in seen:
            continue
        seen.add(line)
        result.append(line)
    return result


def _bounded_public_text(text: str, *, max_chars: int) -> str:
    return text[:max_chars]


def _field_value(text: str, labels: Sequence[str]) -> str | None:
    for label in labels:
        match = re.search(rf"{re.escape(label)}[:：]\s*([^\n]{{2,80}})", text)
        if match:
            return _bounded_public_text(match.group(1).strip(), max_chars=80)
    return None


def _company_title_from_text(text: str) -> tuple[str | None, str | None]:
    company = _field_value(text, ("当前公司", "公司"))
    title = _field_value(text, ("当前职位", "职位"))
    return company, title


def _recent_experience_from_text(text: str) -> str:
    match = re.search(r"(工作经历|项目经历)\n(?P<body>[\s\S]{1,800})", text)
    return _bounded_public_text((match.group("body") if match else text).strip(), max_chars=600)


def _education_from_text(text: str) -> str | None:
    for degree in ("博士", "硕士", "本科", "大专"):
        if degree in text:
            return degree
    return None


def _school_names_from_text(text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(re.findall(r"([\u4e00-\u9fa5A-Za-z0-9]{2,30}(?:大学|学院|University))", text)))


def _skill_tags_from_text(text: str) -> list[str]:
    tags = []
    for skill in ("Python", "SQL", "Flink", "Spark", "Kafka", "ClickHouse", "MySQL", "ETL"):
        if re.search(re.escape(skill), text, re.IGNORECASE):
            tags.append(skill)
    return tags


def _city_from_text(text: str) -> str | None:
    for city in ("北京", "上海", "深圳", "杭州", "广州", "成都"):
        if city in text:
            return city
    return None
```

- [ ] **Step 4: Run parser tests**

Run:

```bash
uv run pytest tests/test_liepin_opencli_resume_parser.py -q
```

Expected:

```text
2 passed
```

- [ ] **Step 5: Commit parser tests and parser**

Run:

```bash
git add tests/test_liepin_opencli_resume_parser.py src/seektalent/providers/liepin/opencli_resume_parser.py
git commit -m "test: add deterministic Liepin resume parser"
```

---

### Task 2: Add Deterministic OpenCLI Resume Retrieval Tests

**Files:**
- Create: `tests/test_liepin_opencli_retriever.py`
- Create: `src/seektalent/providers/liepin/opencli_retriever.py`
- Modify: `src/seektalent/providers/pi_agent/opencli_browser.py`
- Modify: `src/seektalent/providers/pi_agent/opencli_browser_cli.py`

- [ ] **Step 1: Write failing retriever tests**

Create `tests/test_liepin_opencli_retriever.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from seektalent.providers.liepin.opencli_retriever import (
    LiepinOpenCliResumeRequest,
    LiepinOpenCliResumeRetriever,
)
from seektalent.providers.pi_agent.opencli_browser import OpenCliBrowserResult


@dataclass
class FakeOpenCliRunner:
    opened_refs: list[str]
    captured_ranks: list[int]
    artifact_root: Path

    def status(self) -> OpenCliBrowserResult:
        return OpenCliBrowserResult(ok=True, action="status")

    def search_liepin_resumes(
        self,
        *,
        source_run_id: str,
        query: str,
        target_resumes: int,
        max_pages: int,
        max_cards: int,
        native_filters: dict[str, object] | None = None,
    ) -> dict[str, object]:
        del native_filters
        self.opened_refs.extend(["ref-1", "ref-2", "ref-3"][:target_resumes])
        self.captured_ranks.extend(range(1, target_resumes + 1))
        resumes = [
            {
                "provider_rank": index,
                "provider_candidate_key_material_ref": f"artifact://protected/liepin-opencli/provider-key/{source_run_id}/{index}.txt",
                "candidate_resume_id": f"liepin-opencli-{index}",
                "protected_snapshot_ref": f"artifact://protected/liepin-opencli/raw/{source_run_id}/{index}.json",
                "normalized_snapshot_ref": f"artifact://protected/liepin-opencli/normalized/{source_run_id}/{index}.json",
                "detail_payload": {
                    "fullText": f"数据平台 Python resume {index}",
                    "currentTitle": "数据开发专家",
                    "currentCompany": "Example",
                    "workExperienceList": [],
                    "educationList": [],
                    "skills": ["Python"],
                    "locations": ["杭州"],
                },
                "normalized_text": f"数据平台 Python resume {index}",
            }
            for index in range(1, target_resumes + 1)
        ]
        return {
            "schema_version": "seektalent.liepin_opencli_resumes.v1",
            "status": "succeeded",
            "stop_reason": "completed",
            "source_run_id": source_run_id,
            "query": query,
            "cards_seen": max_cards,
            "resumes_returned": target_resumes,
            "pages_visited": max_pages,
            "detail_pages_opened": target_resumes,
            "action_trace_ref": f"artifact://protected/liepin-opencli/trace/{source_run_id}/action-trace.json",
            "protected_snapshot_refs": [resume["protected_snapshot_ref"] for resume in resumes],
            "resumes": resumes,
        }


def test_opencli_retriever_opens_only_target_ranked_details(tmp_path: Path) -> None:
    runner = FakeOpenCliRunner(opened_refs=[], captured_ranks=[], artifact_root=tmp_path)
    retriever = LiepinOpenCliResumeRetriever(runner=runner)

    response = retriever.search_resumes(
        LiepinOpenCliResumeRequest(
            source_run_id="run-1",
            keyword_query="数据开发 Python",
            query_terms=("数据开发", "Python"),
            target_resumes=2,
            max_cards=10,
            max_pages=1,
            requirement_sheet={"job_title": "数据开发专家"},
            native_filters=None,
        )
    )

    assert runner.captured_ranks == [1, 2]
    assert len(response.resumes) == 2
    assert response.raw_candidate_count == 10
    assert response.resumes[0].normalized_text == "数据平台 Python resume 1"
    assert response.resumes[0].payload["normalizedSnapshotRef"].startswith(
        "artifact://protected/liepin-opencli/normalized/"
    )


def test_opencli_retriever_returns_blocked_reason_when_browser_not_ready(tmp_path: Path) -> None:
    class BlockedRunner(FakeOpenCliRunner):
        def status(self) -> OpenCliBrowserResult:
            return OpenCliBrowserResult(
                ok=False,
                action="status",
                safe_reason_code="liepin_opencli_extension_disconnected",
            )

    retriever = LiepinOpenCliResumeRetriever(
        runner=BlockedRunner(opened_refs=[], captured_ranks=[], artifact_root=tmp_path)
    )

    with pytest.raises(RuntimeError, match="liepin_opencli_extension_disconnected"):
        retriever.search_resumes(
            LiepinOpenCliResumeRequest(
                source_run_id="run-1",
                keyword_query="数据开发 Python",
                query_terms=("数据开发", "Python"),
                target_resumes=2,
                max_cards=10,
                max_pages=1,
                requirement_sheet={"job_title": "数据开发专家"},
                native_filters=None,
            )
        )
```

- [ ] **Step 2: Run the retriever tests and verify they fail because the module does not exist**

Run:

```bash
uv run pytest tests/test_liepin_opencli_retriever.py -q
```

Expected:

```text
ModuleNotFoundError: No module named 'seektalent.providers.liepin.opencli_retriever'
```

- [ ] **Step 3: Implement the retriever boundary**

Create `src/seektalent/providers/liepin/opencli_retriever.py`:

```python
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, cast

from seektalent.providers.liepin.worker_contracts import (
    LiepinResumeSearchResponse,
    LiepinWorkerCandidateDetail,
)


class OpenCliResumeRunner(Protocol):
    def status(self): ...

    def search_liepin_resumes(
        self,
        *,
        source_run_id: str,
        query: str,
        target_resumes: int,
        max_pages: int,
        max_cards: int,
        native_filters: dict[str, object] | None = None,
    ) -> dict[str, object]: ...


@dataclass(frozen=True, kw_only=True)
class LiepinOpenCliResumeRequest:
    source_run_id: str
    keyword_query: str
    query_terms: Sequence[str]
    target_resumes: int
    max_cards: int
    max_pages: int
    requirement_sheet: Mapping[str, object]
    native_filters: dict[str, object] | None = None


class LiepinOpenCliResumeRetriever:
    def __init__(self, *, runner: OpenCliResumeRunner) -> None:
        self._runner = runner

    def search_resumes(self, request: LiepinOpenCliResumeRequest) -> LiepinResumeSearchResponse:
        status = self._runner.status()
        if not status.ok:
            raise RuntimeError(str(status.safe_reason_code))
        envelope = self._runner.search_liepin_resumes(
            source_run_id=request.source_run_id,
            query=request.keyword_query,
            target_resumes=request.target_resumes,
            max_pages=request.max_pages,
            max_cards=request.max_cards,
            native_filters=request.native_filters,
        )
        return _response_from_opencli_envelope(envelope)


def _response_from_opencli_envelope(envelope: Mapping[str, object]) -> LiepinResumeSearchResponse:
    status = envelope.get("status")
    if status not in {"succeeded", "partial"}:
        reason = envelope.get("safe_reason_code") or envelope.get("stop_reason") or "failed_provider_error"
        raise RuntimeError(str(reason))
    raw_resumes = envelope.get("resumes")
    if not isinstance(raw_resumes, list):
        raise RuntimeError("liepin_opencli_malformed_state")
    resumes = [
        _detail_from_resume_payload(cast(Mapping[str, object], resume))
        for resume in raw_resumes
        if isinstance(resume, Mapping)
    ]
    return LiepinResumeSearchResponse(
        resumes=resumes,
        exhausted=status == "succeeded",
        requestPayload={
            "source": "liepin",
            "backend": "opencli",
            "opencliStatus": status,
            "safeReasonCode": envelope.get("safe_reason_code") or envelope.get("stop_reason"),
            "actionTraceRef": envelope.get("action_trace_ref"),
        },
        rawCandidateCount=int(envelope.get("cards_seen") or len(resumes)),
    )


def _detail_from_resume_payload(resume: Mapping[str, object]) -> LiepinWorkerCandidateDetail:
    provider_rank = int(resume.get("provider_rank") or 0)
    payload = dict(cast(Mapping[str, object], resume.get("detail_payload") or {}))
    payload["providerRank"] = provider_rank
    payload["protectedSnapshotRef"] = resume.get("protected_snapshot_ref")
    payload["normalizedSnapshotRef"] = resume.get("normalized_snapshot_ref")
    payload["actionTraceRef"] = resume.get("action_trace_ref")
    normalized_text = str(resume.get("normalized_text") or payload.get("fullText") or "")
    fingerprint = str(resume.get("candidate_resume_id") or f"liepin-opencli-{provider_rank}")
    return LiepinWorkerCandidateDetail(
        payload=payload,
        normalized_text=normalized_text,
        provider_subject_id=fingerprint,
        provider_listing_id=None,
        synthetic_candidate_fingerprint=fingerprint,
        identity_confidence="synthetic_fingerprint",
        extraction_source="dom_fallback",
        extractor_version="liepin-opencli-deterministic-v1",
        pii_classification="no_direct_contact",
        retention_policy="provider_snapshot_7d",
        access_scope="local_run_only",
        redaction_state="raw_provider_payload",
    )
```

- [ ] **Step 4: Add `search_liepin_resumes()` to the browser runner**

In `src/seektalent/providers/pi_agent/opencli_browser.py`, add a method that uses existing private search helpers and detail helpers:

```python
    def search_liepin_resumes(
        self,
        *,
        source_run_id: str,
        query: str,
        target_resumes: int,
        max_pages: int,
        max_cards: int,
        native_filters: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        if target_resumes < 1 or target_resumes > 10:
            raise OpenCliBrowserError("liepin_opencli_forbidden_command")
        cards = self.search_liepin_cards(
            source_run_id=source_run_id,
            query=query,
            max_pages=max_pages,
            max_cards=max_cards,
            native_filters=native_filters,
        )
        if cards.get("status") != "succeeded":
            return {
                "schema_version": "seektalent.liepin_opencli_resumes.v1",
                "status": "blocked",
                "stop_reason": cards.get("stop_reason") or "failed_provider_error",
                "safe_reason_code": cards.get("safe_reason_code") or cards.get("stop_reason") or "failed_provider_error",
                "source_run_id": source_run_id,
                "query": query,
                "cards_seen": int(cards.get("cards_seen") or 0),
                "resumes_returned": 0,
                "pages_visited": int(cards.get("pages_visited") or 0),
                "detail_pages_opened": 0,
                "action_trace_ref": cards.get("action_trace_ref"),
                "protected_snapshot_refs": [],
                "resumes": [],
            }
        visible = self.extract_visible_liepin_cards(source_run_id=source_run_id, max_cards=max_cards)
        if not visible.ok:
            return self._blocked_resumes_envelope(
                source_run_id=source_run_id,
                query=query,
                safe_reason_code=visible.safe_reason_code,
                cards_seen=int(cards.get("cards_seen") or 0),
            )
        raw_cards = visible.observation.get("cards") if isinstance(visible.observation, Mapping) else None
        card_items = raw_cards if isinstance(raw_cards, list) else []
        opened = 0
        for card in card_items:
            if opened >= target_resumes:
                break
            if not isinstance(card, Mapping):
                continue
            ref = card.get("ref")
            rank = int(card.get("provider_rank") or opened + 1)
            if not isinstance(ref, str) or not ref:
                continue
            open_result = self.open_liepin_detail(source_run_id=source_run_id, ref=ref, rank=rank)
            if not open_result.ok:
                continue
            capture_result = self.capture_liepin_detail_resume(source_run_id=source_run_id, rank=rank)
            if capture_result.ok:
                opened += 1
        return self.finalize_liepin_resumes(
            source_run_id=source_run_id,
            query=query,
            max_pages=max_pages,
            max_cards=max_cards,
            cards_seen=int(cards.get("cards_seen") or len(card_items)),
            target_resumes=target_resumes,
        )
```

Add `_blocked_resumes_envelope()` next to `_resumes_envelope()`:

```python
    def _blocked_resumes_envelope(
        self,
        *,
        source_run_id: str,
        query: str,
        safe_reason_code: str,
        cards_seen: int,
    ) -> dict[str, object]:
        return {
            "schema_version": "seektalent.liepin_opencli_resumes.v1",
            "status": "blocked",
            "stop_reason": safe_reason_code,
            "safe_reason_code": safe_reason_code,
            "source_run_id": source_run_id,
            "query": query,
            "cards_seen": cards_seen,
            "resumes_returned": 0,
            "pages_visited": 1,
            "detail_pages_opened": 0,
            "action_trace_ref": None,
            "protected_snapshot_refs": [],
            "resumes": [],
        }
```

- [ ] **Step 5: Route detail normalization through the new parser**

In `src/seektalent/providers/pi_agent/opencli_browser.py`, import:

```python
from seektalent.providers.liepin.opencli_resume_parser import build_liepin_opencli_detail_payload
```

Then replace the body of `_safe_detail_payload_from_state()` with:

```python
def _safe_detail_payload_from_state(text: str) -> dict[str, object]:
    try:
        return build_liepin_opencli_detail_payload(text)
    except ValueError as exc:
        raise OpenCliBrowserError("liepin_opencli_malformed_state") from exc
```

Also update `capture_liepin_detail_resume()` so each captured detail writes separate raw and normalized protected artifacts before appending the collected resume. Use the existing artifact-write helper if that is the smallest diff; the important demo-facing contract is the `liepin-opencli/...` artifact path, not preserving old `pi-*` paths:

```python
url_result = self.get_url()
page_url_hash = (
    hashlib.sha256(str(url_result.observation.get("url") or "").encode("utf-8")).hexdigest()
    if url_result.ok
    else None
)
raw_snapshot_ref = self._write_pi_artifact(
    "protected",
    f"liepin-opencli/raw/{safe_run_id}/{rank}.json",
    {
        "schema_version": "seektalent.liepin_opencli_detail_raw.v1",
        "source_run_id": source_run_id,
        "provider_rank": rank,
        "page_text": _bounded_public_text(detail_text, max_chars=20_000),
        "page_url_hash": page_url_hash,
    },
)
normalized_snapshot_ref = self._write_pi_artifact(
    "protected",
    f"liepin-opencli/normalized/{safe_run_id}/{rank}.json",
    {
        "schema_version": "seektalent.liepin_opencli_detail_normalized.v1",
        "source_run_id": source_run_id,
        "provider_rank": rank,
        **payload,
    },
)
```

Then set the collected resume refs to those artifact refs:

```python
resume["protected_snapshot_ref"] = raw_snapshot_ref
resume["normalized_snapshot_ref"] = normalized_snapshot_ref
```

- [ ] **Step 6: Add the CLI action**

In `src/seektalent/providers/pi_agent/opencli_browser_cli.py`, add this branch to `_run_action()`:

```python
    if action == "search_resumes":
        native_filters = payload.get("nativeFilters") or payload.get("native_filters")
        return runner.search_liepin_resumes(
            source_run_id=str(payload.get("sourceRunId") or payload.get("source_run_id") or ""),
            query=str(payload.get("query") or ""),
            target_resumes=_payload_int(payload, "targetResumes", "target_resumes", default=2),
            max_pages=_payload_int(payload, "maxPages", "max_pages", default=1),
            max_cards=_payload_int(payload, "maxCards", "max_cards", default=10),
            native_filters=cast(Mapping[str, object], native_filters) if isinstance(native_filters, dict) else None,
        )
```

- [ ] **Step 7: Run retriever and parser tests**

Run:

```bash
uv run pytest tests/test_liepin_opencli_resume_parser.py tests/test_liepin_opencli_retriever.py -q
```

Expected:

```text
4 passed
```

- [ ] **Step 8: Commit deterministic retriever**

Run:

```bash
git add src/seektalent/providers/liepin/opencli_retriever.py src/seektalent/providers/pi_agent/opencli_browser.py src/seektalent/providers/pi_agent/opencli_browser_cli.py tests/test_liepin_opencli_retriever.py
git commit -m "feat: add deterministic Liepin OpenCLI retriever"
```

---

### Task 3: Add OpenCLI Worker Mode

**Files:**
- Create: `src/seektalent/providers/liepin/opencli_worker_client.py`
- Create: `tests/test_liepin_opencli_worker_client.py`
- Modify: `src/seektalent/providers/liepin/client.py`
- Modify: `src/seektalent/config.py`
- Modify: `tests/test_liepin_config.py`

- [ ] **Step 1: Write worker client tests**

Create `tests/test_liepin_opencli_worker_client.py`:

```python
from __future__ import annotations

from dataclasses import dataclass

import pytest

from seektalent.core.retrieval.provider_contract import SearchRequest
from seektalent.providers.liepin.opencli_worker_client import LiepinOpenCliWorkerClient
from seektalent.providers.liepin.worker_contracts import (
    LiepinResumeSearchResponse,
    LiepinWorkerPartialSearchError,
)


@dataclass
class FakeRetriever:
    calls: list[object]

    def search_resumes(self, request):
        self.calls.append(request)
        return LiepinResumeSearchResponse(
            resumes=[],
            exhausted=True,
            requestPayload={"backend": "opencli"},
            rawCandidateCount=3,
        )


async def test_opencli_worker_forwards_runtime_request_to_deterministic_retriever() -> None:
    retriever = FakeRetriever(calls=[])
    client = LiepinOpenCliWorkerClient(
        retriever=retriever,
        connection_id="liepin-opencli",
        provider_account_hash="local-opencli",
    )

    result = await client.search(
        SearchRequest(
            query_terms=["数据开发", "Python"],
            query_role="primary",
            keyword_query="数据开发 Python",
            adapter_notes=[],
            runtime_constraints=[],
            fetch_mode="detail",
            page_size=2,
            provider_context={
                "liepin_requirement_sheet_json": "{\"job_title\":\"数据开发专家\"}",
                "liepin_max_cards": "10",
                "liepin_max_pages": "1",
            },
        ),
        round_no=1,
        trace_id="run-1",
    )

    assert result.raw_candidate_count == 3
    assert retriever.calls[0].target_resumes == 2
    assert retriever.calls[0].max_cards == 10
    assert retriever.calls[0].requirement_sheet == {"job_title": "数据开发专家"}


async def test_opencli_worker_raises_partial_error_with_captured_candidates() -> None:
    class PartialRetriever(FakeRetriever):
        def search_resumes(self, request):
            response = super().search_resumes(request)
            return response.model_copy(
                update={
                    "request_payload": {
                        "backend": "opencli",
                        "opencliStatus": "partial",
                        "safeReasonCode": "partial_timeout",
                    }
                }
            )

    client = LiepinOpenCliWorkerClient(
        retriever=PartialRetriever(calls=[]),
        connection_id="liepin-opencli",
        provider_account_hash="local-opencli",
    )

    with pytest.raises(LiepinWorkerPartialSearchError) as error:
        await client.search(
            SearchRequest(
                query_terms=["数据开发", "Python"],
                query_role="primary",
                keyword_query="数据开发 Python",
                adapter_notes=[],
                runtime_constraints=[],
                fetch_mode="detail",
                page_size=2,
                provider_context={
                    "liepin_requirement_sheet_json": "{\"job_title\":\"数据开发专家\"}",
                },
            ),
            round_no=1,
            trace_id="run-1",
        )

    assert error.value.code == "partial_timeout"
    assert error.value.partial_search_result.raw_candidate_count == 3


async def test_opencli_worker_session_status_is_ready() -> None:
    client = LiepinOpenCliWorkerClient(
        retriever=FakeRetriever(calls=[]),
        connection_id="liepin-opencli",
        provider_account_hash="local-opencli",
    )

    status = await client.session_status(connection_id="liepin-opencli")

    assert status.status == "ready"
    assert status.provider_account_hash == "local-opencli"


async def test_opencli_worker_session_status_echoes_bound_provider_hash() -> None:
    client = LiepinOpenCliWorkerClient(
        retriever=FakeRetriever(calls=[]),
        connection_id="liepin-opencli",
        provider_account_hash="local-opencli",
    )

    status = await client.session_status(
        connection_id="liepin-opencli",
        provider_account_hash="workbench-bound-hash",
    )

    assert status.status == "ready"
    assert status.provider_account_hash == "workbench-bound-hash"
```

- [ ] **Step 2: Run worker tests and confirm import failure**

Run:

```bash
uv run pytest tests/test_liepin_opencli_worker_client.py -q
```

Expected:

```text
ModuleNotFoundError: No module named 'seektalent.providers.liepin.opencli_worker_client'
```

- [ ] **Step 3: Implement `LiepinOpenCliWorkerClient`**

Create `src/seektalent/providers/liepin/opencli_worker_client.py`:

```python
from __future__ import annotations

import json
from typing import cast

from seektalent.core.retrieval.provider_contract import SearchRequest, SearchResult
from seektalent.providers.liepin.client import liepin_resume_search_response_to_search_result
from seektalent.providers.liepin.opencli_retriever import (
    LiepinOpenCliResumeRequest,
    LiepinOpenCliResumeRetriever,
)
from seektalent.providers.liepin.worker_contracts import (
    LiepinDetailOpenRequest,
    LiepinDetailOpenResponse,
    LiepinWorkerModeError,
    LiepinWorkerPartialSearchError,
    LoginHandoff,
    LoginRelayCompleteResult,
    LoginRelayInputResult,
    LoginRelaySnapshot,
    SessionStatus,
)


class LiepinOpenCliWorkerClient:
    def __init__(
        self,
        *,
        retriever: LiepinOpenCliResumeRetriever,
        connection_id: str,
        provider_account_hash: str,
    ) -> None:
        self._retriever = retriever
        self._connection_id = connection_id
        self._provider_account_hash = provider_account_hash

    async def ensure_ready(self, *, on_event=None) -> None:
        del on_event
        return None

    async def search(
        self,
        request: SearchRequest,
        *,
        round_no: int,
        trace_id: str,
        provider_account_hash: str | None = None,
    ) -> SearchResult:
        del round_no
        requirement_sheet = _json_object(request.provider_context.get("liepin_requirement_sheet_json"))
        if requirement_sheet is None:
            raise LiepinWorkerModeError(
                "Liepin OpenCLI resume search requires the canonical requirement sheet.",
                code="requirement_sheet_missing",
            )
        try:
            response = self._retriever.search_resumes(
                LiepinOpenCliResumeRequest(
                    source_run_id=trace_id,
                    keyword_query=request.keyword_query or " ".join(request.query_terms),
                    query_terms=tuple(request.query_terms),
                    target_resumes=request.page_size,
                    max_cards=_positive_int(request.provider_context.get("liepin_max_cards"), default=request.page_size),
                    max_pages=_positive_int(request.provider_context.get("liepin_max_pages"), default=1),
                    requirement_sheet=requirement_sheet,
                    native_filters=_native_filters_from_request(request),
                )
            )
        except RuntimeError as exc:
            raise LiepinWorkerModeError("Liepin OpenCLI resume search blocked.", code=str(exc)) from exc
        search_result = liepin_resume_search_response_to_search_result(response)
        if response.request_payload.get("opencliStatus") == "partial":
            raise LiepinWorkerPartialSearchError(
                "Liepin OpenCLI resume search returned partial resumes.",
                code=str(response.request_payload.get("safeReasonCode") or "partial_timeout"),
                partial_search_result=search_result,
                cards_collected=len(search_result.candidates),
            )
        return search_result

    async def session_status(
        self,
        *,
        connection_id: str,
        tenant: str | None = None,
        workspace: str | None = None,
        provider_account_hash: str | None = None,
    ) -> SessionStatus:
        del tenant, workspace
        return SessionStatus(
            connectionId=connection_id or self._connection_id,
            status="ready",
            providerAccountHash=provider_account_hash or self._provider_account_hash,
        )

    async def open_details(self, request: LiepinDetailOpenRequest) -> LiepinDetailOpenResponse:
        del request
        raise LiepinWorkerModeError("Liepin OpenCLI worker performs detail-backed search directly.")

    async def login_handoff(self, *, connection_id: str, tenant_id=None, workspace_id=None, provider_account_hash=None) -> LoginHandoff:
        del connection_id, tenant_id, workspace_id, provider_account_hash
        raise LiepinWorkerModeError("Liepin OpenCLI worker uses the user's logged-in local Chrome state.", code="liepin_opencli_login_required")

    async def login_relay_snapshot(self, *, connection_id: str) -> LoginRelaySnapshot:
        del connection_id
        raise LiepinWorkerModeError("Liepin OpenCLI worker does not expose login relay snapshots.")

    async def submit_login_relay_input(self, *, connection_id: str, action: str, x=None, y=None, text=None, key=None) -> LoginRelayInputResult:
        del connection_id, action, x, y, text, key
        raise LiepinWorkerModeError("Liepin OpenCLI worker does not accept login relay input.")

    async def complete_login_relay(self, *, connection_id: str) -> LoginRelayCompleteResult:
        del connection_id
        raise LiepinWorkerModeError("Liepin OpenCLI worker does not complete login relay.")


def _positive_int(value: object, *, default: int) -> int:
    try:
        parsed = int(cast(object, value))
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _json_object(value: object) -> dict[str, object] | None:
    if not isinstance(value, str) or not value.strip():
        return None
    parsed = json.loads(value)
    return parsed if isinstance(parsed, dict) else None


def _native_filters_from_request(request: SearchRequest) -> dict[str, object] | None:
    raw = request.provider_context.get("liepin_native_filters_json")
    if not isinstance(raw, str) or not raw.strip():
        return None
    parsed = json.loads(raw)
    return parsed if isinstance(parsed, dict) else None
```

- [ ] **Step 4: Add `opencli` mode to config and client builder**

In `src/seektalent/config.py`, change the type:

```python
LiepinWorkerMode = Literal["disabled", "fake_fixture", "managed_local", "external_http", "pi_agent", "opencli"]
```

This is a temporary migration state for Tasks 3-5 only. Task 6 removes `pi_agent` from the type and from the builder after OpenCLI tests pass.

In `src/seektalent/providers/liepin/client.py`, add `opencli` to live modes:

```python
LIVE_LIEPIN_WORKER_MODES = frozenset({"managed_local", "external_http", "pi_agent", "opencli"})
```

This is also temporary until Task 6. The final live modes must not include `pi_agent`.

Add this branch to `build_liepin_worker_client()` before the `pi_agent` branch:

```python
    if settings.liepin_worker_mode == "opencli":
        return build_liepin_opencli_worker_client(settings)
```

Add the builder:

```python
def build_liepin_opencli_worker_client(settings: AppSettings) -> LiepinWorkerClient:
    from seektalent.providers.liepin.opencli_retriever import LiepinOpenCliResumeRetriever
    from seektalent.providers.liepin.opencli_worker_client import LiepinOpenCliWorkerClient
    from seektalent.providers.pi_agent.opencli_browser import (
        OpenCliBrowserConfig,
        OpenCliBrowserRunner,
        default_liepin_opencli_policy,
    )

    runner = OpenCliBrowserRunner(
        config=OpenCliBrowserConfig(
            command=settings.liepin_opencli_command_argv,
            session=settings.liepin_opencli_session,
            timeout_seconds=settings.liepin_opencli_timeout_seconds,
            detail_open_timeout_seconds=settings.liepin_opencli_detail_open_timeout_seconds,
            lease_dir=settings.project_root / ".seektalent" / "opencli_leases",
            artifact_root=settings.artifacts_path,
            idle_close_seconds=settings.liepin_opencli_idle_close_seconds,
            close_blank_window=settings.liepin_opencli_close_blank_window,
            pacing_enabled=settings.liepin_opencli_pacing_enabled,
            pacing_min_ms=settings.liepin_opencli_pacing_min_ms,
            pacing_max_ms=settings.liepin_opencli_pacing_max_ms,
            policy=default_liepin_opencli_policy(
                allowed_hosts=settings.liepin_opencli_allowed_hosts,
                allowed_start_urls=settings.liepin_opencli_allowed_start_urls,
            ),
        )
    )
    return LiepinOpenCliWorkerClient(
        retriever=LiepinOpenCliResumeRetriever(runner=runner),
        connection_id="liepin-opencli",
        provider_account_hash="liepin-opencli-local",
    )
```

- [ ] **Step 5: Add config regression tests**

In `tests/test_liepin_config.py`, add:

```python
def test_liepin_worker_mode_accepts_opencli() -> None:
    settings = AppSettings(liepin_worker_mode="opencli", liepin_browser_action_backend="opencli")

    assert settings.liepin_worker_mode == "opencli"
    assert settings.liepin_browser_action_backend == "opencli"
```

- [ ] **Step 6: Run worker/config tests**

Run:

```bash
uv run pytest tests/test_liepin_opencli_worker_client.py tests/test_liepin_config.py -q
```

Expected:

```text
passed
```

- [ ] **Step 7: Commit OpenCLI worker mode**

Run:

```bash
git add src/seektalent/providers/liepin/opencli_worker_client.py src/seektalent/providers/liepin/client.py src/seektalent/config.py tests/test_liepin_opencli_worker_client.py tests/test_liepin_config.py
git commit -m "feat: add Liepin OpenCLI worker mode"
```

---

### Task 4: Verify Runtime Budget And Source-Lane Integration

**Files:**
- Modify: `tests/test_runtime_multi_source_round_dispatch.py`
- Modify: `tests/test_liepin_runtime_source_lane.py`
- Modify: `src/seektalent/providers/liepin/runtime_lane.py`
- Modify: `src/seektalent/runtime/source_query_intent.py`

- [ ] **Step 1: Add budget tests for Liepin-specific requested counts**

In `tests/test_runtime_multi_source_round_dispatch.py`, add:

```python
def test_source_query_intents_keep_cts_10_and_cap_liepin_to_2_plus_1() -> None:
    dispatches = (
        _dispatch("exploit", 7),
        _dispatch("generic_explore", 3),
    )

    intents = build_runtime_source_query_intents(
        source_kinds=("cts", "liepin"),
        logical_dispatches=dispatches,
        filter_intents=(),
        location_intent=None,
        age_intent=None,
        source_budget_policy=RuntimeSourceBudgetPolicy(
            liepin_exploit_resume_target=2,
            liepin_explore_resume_target=1,
            liepin_max_cards=30,
        ),
    )

    assert [item.requested_count for item in intents["cts"]] == [7, 3]
    assert [item.requested_count for item in intents["liepin"]] == [2, 1]


def test_first_round_liepin_uses_exploit_only_budget() -> None:
    intents = build_runtime_source_query_intents(
        source_kinds=("liepin",),
        logical_dispatches=(_dispatch("exploit", 7),),
        filter_intents=(),
        location_intent=None,
        age_intent=None,
        source_budget_policy=RuntimeSourceBudgetPolicy(
            liepin_exploit_resume_target=2,
            liepin_explore_resume_target=1,
            liepin_max_cards=30,
        ),
    )

    assert [(item.lane_type, item.requested_count) for item in intents["liepin"]] == [("exploit", 2)]
```

Add imports if missing:

```python
from seektalent.runtime.source_query_intent import build_runtime_source_query_intents
from seektalent.runtime.source_lanes import RuntimeSourceBudgetPolicy
```

- [ ] **Step 2: Run budget tests**

Run:

```bash
uv run pytest tests/test_runtime_multi_source_round_dispatch.py::test_source_query_intents_keep_cts_10_and_cap_liepin_to_2_plus_1 tests/test_runtime_multi_source_round_dispatch.py::test_first_round_liepin_uses_exploit_only_budget -q
```

Expected:

```text
2 passed
```

If either test fails because an older test still expects Liepin `[7, 3]`, update that older assertion to expect CTS `[7, 3]` and Liepin `[2, 1]`.

- [ ] **Step 3: Add source-lane test for detail-backed candidate refs**

In `tests/test_liepin_runtime_source_lane.py`, add a fake worker result that returns one detail-backed resume and assert candidate text, detail evidence, and protected artifact refs are populated. Do not require `normalized_store_updates` in this plan unless the existing runtime normalized-store contract is expanded separately.

```python
def test_liepin_detail_backed_opencli_candidates_populate_candidate_refs() -> None:
    worker = FakeWorker()
    worker.search_result = SearchResult(
        candidates=[
            ResumeCandidate(
                resume_id="liepin-opencli-1",
                source_resume_id="liepin-opencli-1",
                snapshot_sha256="sha256:abc",
                dedup_key="liepin-opencli-1",
                search_text="数据平台 Python resume",
                raw={
                    "source_completeness": "normalized_detail",
                    "provider_snapshot_ref": "artifact://protected/liepin-opencli/raw/run-1/1.json",
                    "normalized_snapshot_ref": "artifact://protected/liepin-opencli/normalized/run-1/1.json",
                },
            )
        ],
        provider_snapshots=[],
        exhausted=True,
        raw_candidate_count=1,
    )

    result = asyncio.run(
        run_liepin_source_lane(
            settings=make_settings(liepin_worker_mode="opencli", liepin_browser_action_backend="opencli"),
            request=RuntimeSourceLaneRequest(
                source="liepin",
                lane_mode="card",
                job_title="数据开发专家",
                jd="负责数据平台建设",
                notes="Python",
                requirement_sheet=_requirement_sheet(),
                source_query_terms=("数据开发", "Python"),
                logical_query_instance_id="q-exploit",
                logical_query_role="exploit",
                logical_keyword_query="数据开发 Python",
                logical_requested_count=2,
                logical_provider_scan_limit=10,
            ),
            worker_client=worker,
        )
    )

    assert result.status == "completed"
    assert result.candidate_store_updates["liepin-opencli-1"].search_text == "数据平台 Python resume"
    assert result.source_evidence_updates[0].evidence_level == "detail"
    assert result.source_evidence_updates[0].provider_snapshot_ref == (
        "artifact://protected/liepin-opencli/raw/run-1/1.json"
    )
    assert result.normalized_store_updates == {}
```

In `src/seektalent/providers/liepin/runtime_lane.py`, add `liepin_opencli_detail_not_opened` to `OPENCLI_SAFE_REASON_CODES` so the spec-listed blocked state remains public-safe through runtime source-lane projection.

- [ ] **Step 4: Run source-lane tests**

Run:

```bash
uv run pytest tests/test_liepin_runtime_source_lane.py -q
```

Expected:

```text
passed
```

- [ ] **Step 5: Commit runtime budget and lane verification**

Run:

```bash
git add tests/test_runtime_multi_source_round_dispatch.py tests/test_liepin_runtime_source_lane.py src/seektalent/providers/liepin/runtime_lane.py src/seektalent/runtime/source_query_intent.py
git commit -m "test: verify Liepin OpenCLI runtime budgets"
```

---

### Task 5: Switch Dev Defaults To OpenCLI

**Files:**
- Modify: `src/seektalent/default.env`
- Modify: `.env.example`
- Modify: `scripts/start-dev-workbench.sh`
- Modify: `tests/test_workbench_api.py`
- Modify: `apps/web-svelte/src/lib/workbench/sourceDisplay.test.ts`

- [ ] **Step 1: Change default local workbench mode**

In `src/seektalent/default.env` and `.env.example`, use:

```env
SEEKTALENT_LIEPIN_WORKER_MODE=opencli
SEEKTALENT_LIEPIN_BROWSER_ACTION_BACKEND=opencli
SEEKTALENT_LIEPIN_OPENCLI_COMMAND=apps/web-svelte/node_modules/.bin/opencli
SEEKTALENT_LIEPIN_OPENCLI_TIMEOUT_SECONDS=900
SEEKTALENT_LIEPIN_OPENCLI_DETAIL_OPEN_TIMEOUT_SECONDS=90
```

Remove active `SEEKTALENT_LIEPIN_PI_*` example lines from the default local path.

- [ ] **Step 2: Change dev startup script**

In `scripts/start-dev-workbench.sh`, replace the Liepin launch environment with:

```bash
export SEEKTALENT_LIEPIN_WORKER_MODE="${SEEKTALENT_LIEPIN_WORKER_MODE:-opencli}"
export SEEKTALENT_LIEPIN_BROWSER_ACTION_BACKEND="${SEEKTALENT_LIEPIN_BROWSER_ACTION_BACKEND:-opencli}"
export SEEKTALENT_LIEPIN_OPENCLI_COMMAND="${SEEKTALENT_LIEPIN_OPENCLI_COMMAND:-apps/web-svelte/node_modules/.bin/opencli}"
export SEEKTALENT_LIEPIN_OPENCLI_TIMEOUT_SECONDS="${SEEKTALENT_LIEPIN_OPENCLI_TIMEOUT_SECONDS:-900}"
export SEEKTALENT_LIEPIN_OPENCLI_DETAIL_OPEN_TIMEOUT_SECONDS="${SEEKTALENT_LIEPIN_OPENCLI_DETAIL_OPEN_TIMEOUT_SECONDS:-90}"
```

- [ ] **Step 3: Update public source-display tests**

In `apps/web-svelte/src/lib/workbench/sourceDisplay.test.ts`, remove Pi-only reason codes from the active expected set and keep OpenCLI public labels:

```ts
expect(sourceReasonLabel('liepin_opencli_login_required')).toContain('登录猎聘');
expect(sourceReasonLabel('liepin_opencli_extension_disconnected')).not.toMatch(
  /OpenCLI|DokoBot|MCP|pi_agent|cookie|authorization/i,
);
```

- [ ] **Step 4: Run backend and frontend targeted tests**

Run:

```bash
uv run pytest tests/test_workbench_api.py tests/test_liepin_config.py -q
cd apps/web-svelte && bun run test -- src/lib/workbench/sourceDisplay.test.ts
```

Expected:

```text
passed
```

- [ ] **Step 5: Commit dev default switch**

Run:

```bash
git add src/seektalent/default.env .env.example scripts/start-dev-workbench.sh tests/test_workbench_api.py apps/web-svelte/src/lib/workbench/sourceDisplay.test.ts
git commit -m "chore: switch Liepin dev mode to deterministic OpenCLI"
```

---

### Task 6: Remove Liepin Pi Runtime Path

**Files:**
- Delete: `src/seektalent/providers/liepin/pi_executor.py`
- Delete: `src/seektalent/providers/liepin/pi_worker_client.py`
- Delete: `src/seektalent/providers/liepin/pi_resume_contract.py`
- Delete: `src/seektalent/providers/pi_agent/pi_skills/liepin_search_cards.md`
- Modify: `src/seektalent/providers/liepin/client.py`
- Modify: `src/seektalent/config.py`
- Modify: `src/seektalent/default.env`
- Modify: `.env.example`
- Modify/Delete: Pi-only tests listed in the File Map

- [ ] **Step 1: Remove Pi mode from active config**

In `src/seektalent/config.py`, change:

```python
LiepinWorkerMode = Literal["disabled", "fake_fixture", "managed_local", "external_http", "opencli"]
```

Remove these fields from `AppSettings` after confirming no non-test active imports remain:

```python
liepin_pi_command
liepin_pi_timeout_seconds
liepin_pi_resume_capture_idle_timeout_seconds
liepin_pi_skill_path
liepin_pi_mcp_config_path
liepin_pi_dokobot_tool_name
liepin_pi_model_id
liepin_dokobot_mcp_server_name
liepin_dokobot_mcp_command
liepin_dokobot_mcp_args_json
liepin_dokobot_direct_tools_json
liepin_dokobot_observed_tools_json
```

Remove validators and properties that only support those fields.

- [ ] **Step 2: Remove Pi worker builder branch**

In `src/seektalent/providers/liepin/client.py`, remove:

```python
    if settings.liepin_worker_mode == "pi_agent":
        return build_liepin_pi_worker_client(settings)
```

Delete `build_liepin_pi_worker_client()`.

- [ ] **Step 3: Delete Liepin Pi implementation files**

Run:

```bash
git rm src/seektalent/providers/liepin/pi_executor.py
git rm src/seektalent/providers/liepin/pi_worker_client.py
git rm src/seektalent/providers/liepin/pi_resume_contract.py
git rm src/seektalent/providers/pi_agent/pi_skills/liepin_search_cards.md
```

- [ ] **Step 4: Remove or rewrite Pi-only tests**

Delete tests that only validate removed Liepin Pi behavior:

```bash
git rm tests/test_liepin_pi_executor.py
git rm tests/test_liepin_pi_worker_client.py
git rm tests/test_liepin_pi_skills.py
git rm tests/test_liepin_live_pi_agent.py
```

Keep `tests/test_pi_external_agent.py` only if non-Liepin code still imports `PiRpcAgentClient`. If it becomes unused after cleanup, delete it with:

```bash
git rm tests/test_pi_external_agent.py
```

- [ ] **Step 5: Run import cleanup scans**

Run:

```bash
rg -n "PiLiepin|LiepinPi|pi_liepin|liepin_pi|liepin_search_cards|seektalent\\.pi_liepin" src tests apps/web-svelte/src .env.example scripts
```

Expected:

```text
no matches
```

If `src/seektalent/providers/pi_agent/opencli_browser.py` remains, do not remove it merely because of the directory name; it is still the deterministic OpenCLI browser runner until a separate move is planned.

- [ ] **Step 6: Run cleanup tests**

Run:

```bash
uv run pytest tests/test_liepin_opencli_resume_parser.py tests/test_liepin_opencli_retriever.py tests/test_liepin_opencli_worker_client.py tests/test_liepin_config.py tests/test_liepin_runtime_source_lane.py tests/test_runtime_multi_source_round_dispatch.py tests/test_runtime_source_lanes.py tests/test_workbench_api.py -q
```

Expected:

```text
passed
```

- [ ] **Step 7: Commit Pi cleanup**

Run:

```bash
git add src/seektalent/providers/liepin/client.py src/seektalent/config.py src/seektalent/default.env .env.example
git add -u src/seektalent/providers/liepin src/seektalent/providers/pi_agent/pi_skills tests
git commit -m "refactor: remove Liepin Pi retrieval path"
```

---

### Task 7: Full Verification And Live Smoke Handoff

**Files:**
- No code files unless verification exposes a direct bug in files changed by this plan.

- [ ] **Step 1: Run backend test suite slice**

Run:

```bash
uv run pytest tests/test_liepin_opencli_resume_parser.py tests/test_liepin_opencli_retriever.py tests/test_liepin_opencli_worker_client.py tests/test_liepin_config.py tests/test_liepin_runtime_source_lane.py tests/test_runtime_multi_source_round_dispatch.py tests/test_runtime_source_lanes.py tests/test_workbench_api.py -q
```

Expected:

```text
passed
```

- [ ] **Step 2: Run frontend verification**

Run:

```bash
cd apps/web-svelte && bun run test -- src/lib/workbench/sourceDisplay.test.ts
cd apps/web-svelte && bun run check && bun run lint && bun run build
```

Expected:

```text
passed
```

- [ ] **Step 3: Run cleanup scans**

Run:

```bash
rg -n "PiLiepin|LiepinPi|pi_liepin|liepin_pi|liepin_search_cards|seektalent\\.pi_liepin" src tests apps/web-svelte/src .env.example scripts
rg -n "liepin_worker_mode.*pi_agent|SEEKTALENT_LIEPIN_WORKER_MODE=pi_agent" src tests apps/web-svelte/src .env.example scripts
```

Expected:

```text
no matches
```

- [ ] **Step 4: Start the local app using the canonical script**

Run:

```bash
scripts/start-dev-workbench.sh
```

Expected:

```text
backend and frontend start with SEEKTALENT_LIEPIN_WORKER_MODE=opencli
```

- [ ] **Step 5: Manual smoke path**

Use the in-app browser and the user's existing QA account:

```text
qa@example.com
st-USAcnY9BZjLkb1Ui
```

Manual assertions:

- create session does not auto-start retrieval
- requirement confirmation still gates retrieval
- first Liepin round opens at most 2 details
- second Liepin round opens at most 2 exploit and 1 explore details
- opened Liepin details appear as candidates in node details
- raw and normalized artifact refs exist under protected OpenCLI artifact paths
- no source node reports Pi or LLM child-agent failure reasons

- [ ] **Step 6: Commit verification-only fixes if needed**

If Step 5 exposes a small bug in the files changed by this plan, write a focused failing test, fix it, rerun relevant tests, and commit:

```bash
git add <changed-files>
git commit -m "fix: stabilize deterministic Liepin OpenCLI retrieval"
```

## Self-Review

- Spec coverage: The tasks cover deterministic retrieval, raw/normalized artifacts, Liepin 2/1 budgets, long bounded browser waits, source-lane integration, dev-mode switch, Pi cleanup, tests, scans, and manual smoke.
- Placeholder scan: No task relies on deferred implementation language or unspecified files.
- Type consistency: `LiepinOpenCliResumeRequest`, `LiepinOpenCliResumeRetriever`, `LiepinOpenCliWorkerClient`, `LiepinResumeSearchResponse`, and `SearchResult` names match across tasks.

## Ready For Plan Review

After this file is saved, run `fw-plan-review`. Because this touches source execution architecture and public source-status labels, plan engineering review is required; design review is not required unless the reviewer decides the frontend label cleanup changes visible UX enough to justify it.
