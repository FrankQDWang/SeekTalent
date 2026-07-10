from __future__ import annotations

import hashlib
import io
import json
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from seektalent.providers.liepin import opencli_browser_cli
from seektalent.opencli_browser.automation import OpenCliBrowserAutomation
from seektalent.opencli_browser.contracts import (
    OpenCliBrowserConfig,
    OpenCliBrowserError,
    OpenCliBrowserResult,
    OpenCliBrowserTiming,
)
from seektalent.providers.liepin.liepin_opencli_policy import (
    LIEPIN_OPENCLI_ALLOWED_HOSTS,
    LIEPIN_RECRUITER_SEARCH_URL,
    LIEPIN_RECRUITER_SEARCH_URLS,
)
from seektalent.providers.liepin.liepin_site_payloads import cards_envelope
from seektalent.providers.liepin.liepin_site_adapter import (
    LiepinOpenCliSiteConfig,
    LiepinOpenCliTimingRecorder,
    LiepinSiteAdapter,
    build_observation,
    bucket_text,
    classify_liepin_state,
    extract_allowed_click_refs,
    extract_liepin_card_summaries,
    extract_liepin_search_button_ref,
    extract_liepin_search_input_ref,
)
from seektalent.providers.liepin.liepin_site_parsing import (
    _liepin_structured_cards_payload_probe_script,
    _safe_detail_payload_from_probe_output,
    _safe_structured_cards_from_probe_output,
    stable_liepin_detail_candidate_key_hash,
)
from seektalent.providers.liepin.opencli_workflow import workflow_steps_from_action_events
from seektalent.providers.liepin.worker_contracts import LiepinSafeCardSummary


LIEPIN_SEARCH_URL = LIEPIN_RECRUITER_SEARCH_URL
ANY_STRUCTURED_CARD_PROBE = "__structured_card_probe__"


def _safe_card_summary_contract_fields(card: Mapping[str, object]) -> dict[str, object]:
    metadata_keys = {"provider_rank", "ref", "display_name_masked"}
    return {key: value for key, value in card.items() if key not in metadata_keys}


FORBIDDEN_CARD_TEXT_KEYS = (
    "visible_text",
    "normalized_card_text",
    "normalizedCardText",
    "raw_html",
    "inner_html",
    "inner_text",
    "fullText",
    "full_text",
    "rawText",
    "page_text",
    "pageText",
)


def _assert_no_card_text_keys(value: object) -> None:
    if isinstance(value, Mapping):
        assert not (set(value) & set(FORBIDDEN_CARD_TEXT_KEYS))
        for item in value.values():
            _assert_no_card_text_keys(item)
    elif isinstance(value, list | tuple):
        for item in value:
            _assert_no_card_text_keys(item)


def test_cards_envelope_sanitizes_card_summary_before_artifacts() -> None:
    writes: dict[tuple[str, str], object] = {}

    def write_pi_artifact(visibility: str, path: str, payload: object) -> str:
        writes[(visibility, path)] = payload
        return f"artifact://{visibility}/{path}"

    long_title = "AI平台工程师" + ("x" * 500)
    sentinel = "SENTINEL_RAW_CARD_TEXT"
    card_summary = {
        "provider_rank": 99,
        "ref": "70",
        "display_name_masked": False,
        "masked_name": True,
        "display_title": long_title,
        "current_or_recent_company": "结构化科技",
        "current_or_recent_title": "AI平台工程师",
        "gender": "男",
        "age": 34,
        "work_years": 12,
        "city": "上海",
        "expected_city": "杭州",
        "education_level": "硕士",
        "job_intention": "AI平台专家",
        "active_status": "近期活跃",
        "badges": ["统招本科"],
        "school_names": ["齐齐哈尔大学"],
        "major_names": ["计算机科学与技术"],
        "skill_tags": ["Python", "RAG"],
        "experience_preview": [
            {
                "company": "结构化科技",
                "title": "AI平台工程师",
                "date_range": "2021.04-至今",
                "duration": "3年",
                "is_current": True,
                "normalizedCardText": sentinel,
                "unsupported": {"pageText": sentinel},
            }
        ],
        "education_preview": [
            {
                "school": "齐齐哈尔大学",
                "major": "计算机科学与技术",
                "degree": "本科",
                "recruitment_type": "统招",
                "date_range": "2017.08-2021.07",
                "full_text": sentinel,
                "unsupported": sentinel,
            }
        ],
        "normalizedCardText": sentinel,
        "pageText": sentinel,
        "full_text": sentinel,
        "visible_text": sentinel,
        "normalized_card_text": sentinel,
        "raw_html": sentinel,
        "unknown_nested": {"safe_note": "must not pass", "pageText": sentinel},
        "unknown_list": [{"safe_note": "must not pass"}],
    }
    expected_summary = {
        "display_title": long_title[:180],
        "current_or_recent_company": "结构化科技",
        "current_or_recent_title": "AI平台工程师",
        "gender": "男",
        "city": "上海",
        "expected_city": "杭州",
        "education_level": "硕士",
        "job_intention": "AI平台专家",
        "active_status": "近期活跃",
        "age": 34,
        "work_years": 12,
        "masked_name": True,
        "badges": ["统招本科"],
        "school_names": ["齐齐哈尔大学"],
        "major_names": ["计算机科学与技术"],
        "skill_tags": ["Python", "RAG"],
        "experience_preview": [
            {
                "company": "结构化科技",
                "title": "AI平台工程师",
                "date_range": "2021.04-至今",
                "duration": "3年",
                "is_current": True,
            }
        ],
        "education_preview": [
            {
                "school": "齐齐哈尔大学",
                "major": "计算机科学与技术",
                "degree": "本科",
                "recruitment_type": "统招",
                "date_range": "2017.08-2021.07",
            }
        ],
    }
    digest = hashlib.sha256(json.dumps(expected_summary, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:12]

    envelope = cards_envelope(
        source_run_id="run-allowlist",
        query="AI平台工程师",
        safe_run_id="run-allowlist",
        pages_visited=1,
        events=(),
        state_text="safe state",
        cards=(card_summary,),
        write_pi_artifact=write_pi_artifact,
    )

    public_summary = writes[("public-summary", "pi-card/run-allowlist/1.json")]
    protected_snapshot = writes[("protected", "pi-card/run-allowlist/1.json")]
    assert public_summary == expected_summary
    assert isinstance(protected_snapshot, Mapping)
    assert protected_snapshot["summary"] == expected_summary
    assert envelope["cards"][0]["safe_card_summary"] == expected_summary
    assert envelope["cards"][0]["candidate_resume_id"] == f"liepin-opencli-run-allowlist-1-{digest}"
    assert envelope["cards"][0]["provider_candidate_key_material_ref"] == (
        "artifact://protected/pi-provider-key/run-allowlist/1.txt"
    )
    assert writes[("protected", "pi-provider-key/run-allowlist/1.txt")] == (f"liepin-opencli:run-allowlist:1:{digest}")
    encoded = json.dumps([envelope, public_summary, protected_snapshot], ensure_ascii=False)
    assert sentinel not in encoded
    assert "must not pass" not in encoded
    for unsupported in ("provider_rank", "ref", "display_name_masked", "unknown_nested", "unknown_list"):
        assert unsupported not in public_summary
    assert "unsupported" not in public_summary["experience_preview"][0]
    assert "unsupported" not in public_summary["education_preview"][0]
    _assert_no_card_text_keys(envelope)
    _assert_no_card_text_keys(public_summary)
    _assert_no_card_text_keys(protected_snapshot)


def _structured_cards_probe_json(*refs: str) -> str:
    cards: list[dict[str, object]] = []
    for rank, ref in enumerate(refs or ("70",), start=1):
        is_second = ref.endswith("1")
        cards.append(
            {
                "provider_rank": rank,
                "ref": ref,
                "masked_name": True,
                "gender": "男",
                "age": 36 if is_second else 40,
                "work_years": 11 if is_second else 14,
                "city": "上海",
                "expected_city": "上海",
                "education_level": "硕士",
                "current_or_recent_company": "云栖数据" if is_second else "海光集成电路",
                "current_or_recent_title": "数据平台负责人" if is_second else "高级主管工程师",
                "job_intention": "数据平台专家" if is_second else "数据开发专家",
                "skill_tags": ["Python", "Spark"] if is_second else ["Python", "Hive"],
            }
        )
    return json.dumps(
        {
            "ok": True,
            "schema_version": "seektalent.liepin_structured_cards_probe.v1",
            "cards": cards,
        },
        ensure_ascii=False,
    )


def _empty_structured_cards_probe_json() -> str:
    return json.dumps(
        {
            "ok": True,
            "schema_version": "seektalent.liepin_structured_cards_probe.v1",
            "cards": [],
        },
        ensure_ascii=False,
    )


def _search_query_value_probe_json(value: str) -> str:
    return json.dumps(
        {
            "ok": True,
            "schema_version": "seektalent.liepin_search_query_value.v1",
            "value": value,
        },
        ensure_ascii=False,
    )


def _has_probe_between(calls: Sequence[tuple[str, ...]], start: int, end: int) -> bool:
    return any(_is_probe_call(call) for call in calls[start:end])


def _is_probe_call(call: tuple[str, ...]) -> bool:
    if call == ("opencli", "daemon", "status"):
        return True
    if len(call) >= 4 and call[:3] == ("opencli", "browser", "seektalent-liepin"):
        return call[3] in {"get", "state", "eval"} or call[3:] == ("tab", "list")
    return False


class FakeCommands:
    def __init__(
        self,
        *,
        outputs: dict[tuple[str, ...], str | list[str]] | None = None,
        fail: bool = False,
    ) -> None:
        self.outputs = outputs or {}
        self.fail = fail
        self.calls: list[tuple[str, ...]] = []
        self.envs: list[Mapping[str, str] | None] = []
        self.search_query_value = ""

    def run(self, argv: Sequence[str], *, timeout: int, env: Mapping[str, str] | None = None) -> str:
        del timeout
        call = tuple(argv)
        self.calls.append(call)
        self.envs.append(env)
        if self.fail:
            raise subprocess.TimeoutExpired(cmd=list(argv), timeout=1)
        if len(call) >= 5 and call[3] == "eval" and "seektalent.liepin_search_query_value.v1" in call[4]:
            return _search_query_value_probe_json(self.search_query_value)
        if len(call) >= 5 and call[3] == "eval" and "seektalent.liepin_structured_cards_probe.v1" in call[4]:
            output = self.outputs.get((ANY_STRUCTURED_CARD_PROBE,), _structured_cards_probe_json("70"))
            return self._resolve_output(output)
        output = self.outputs.get(call, "{}")
        if output == "{}" and len(call) == 6 and call[3:5] == ("tab", "new"):
            return json.dumps({"page": "page-1", "url": call[5]})
        resolved = self._resolve_output(output)
        if len(call) >= 6 and call[3] == "fill":
            self.search_query_value = call[-1]
        return resolved

    def _resolve_output(self, output: object) -> str:
        if isinstance(output, list):
            if output:
                item = output.pop(0)
                if isinstance(item, BaseException):
                    raise item
                return item
            return "{}"
        if isinstance(output, BaseException):
            raise output
        return str(output)

    def prepend_output(self, call: tuple[str, ...], output: str) -> None:
        existing = self.outputs.get(call)
        if existing is None:
            self.outputs[call] = output
            return
        if isinstance(existing, list):
            existing.insert(0, output)


class EvalCommands(FakeCommands):
    def __init__(self, *, eval_output: str, outputs: dict[tuple[str, ...], str | list[str]] | None = None) -> None:
        super().__init__(outputs=outputs)
        self.eval_output = eval_output

    def run(self, argv: Sequence[str], *, timeout: int, env: Mapping[str, str] | None = None) -> str:
        call = tuple(argv)
        if len(call) >= 4 and call[3] == "eval":
            script = call[4] if len(call) > 4 else ""
            if "seektalent.liepin_search_query_value.v1" in script:
                return super().run(argv, timeout=timeout, env=env)
            if "seektalent.liepin_structured_cards_probe.v1" in script:
                return super().run(argv, timeout=timeout, env=env)
            del timeout
            self.calls.append(call)
            self.envs.append(env)
            return self.eval_output
        return super().run(argv, timeout=timeout, env=env)


class RefEvalCommands(FakeCommands):
    def __init__(
        self,
        *,
        eval_outputs_by_ref: dict[str, str | list[str]],
        default_eval_output: str = "null",
        outputs: dict[tuple[str, ...], str | list[str]] | None = None,
    ) -> None:
        super().__init__(outputs=outputs)
        self.eval_outputs_by_ref = eval_outputs_by_ref
        self.default_eval_output = default_eval_output

    def run(self, argv: Sequence[str], *, timeout: int, env: Mapping[str, str] | None = None) -> str:
        call = tuple(argv)
        if len(call) >= 4 and call[3] == "eval":
            script = call[4] if len(call) > 4 else ""
            if "seektalent.liepin_search_query_value.v1" in script:
                return super().run(argv, timeout=timeout, env=env)
            del timeout
            self.calls.append(call)
            self.envs.append(env)
            if (
                "seektalent.liepin_structured_cards_probe.v1" in script
                and ANY_STRUCTURED_CARD_PROBE in self.eval_outputs_by_ref
            ):
                return self._resolve_output(self.eval_outputs_by_ref[ANY_STRUCTURED_CARD_PROBE])
            if "seektalent.liepin_structured_cards_probe.v1" in script:
                refs = tuple(ref for ref in self.eval_outputs_by_ref if ref != ANY_STRUCTURED_CARD_PROBE)
                return _structured_cards_probe_json(*refs)
            for ref, output in self.eval_outputs_by_ref.items():
                if ref == ANY_STRUCTURED_CARD_PROBE:
                    continue
                if f'data-opencli-ref="{ref}"' in script:
                    return self._resolve_output(output)
            return self.default_eval_output
        return super().run(argv, timeout=timeout, env=env)


def _runner(
    commands: FakeCommands,
    *,
    allowed_click_refs: tuple[str, ...] = (),
    lease_dir: Path | None = None,
    detail_open_timeout_seconds: int = 5,
    pacing_enabled: bool = False,
    pacing_min_ms: int = 0,
    pacing_max_ms: int = 0,
) -> LiepinSiteAdapter:
    browser_config = OpenCliBrowserConfig(
        command=("opencli",),
        session="seektalent-liepin",
        timeout_seconds=10,
        pacing_enabled=pacing_enabled,
        pacing_min_ms=pacing_min_ms,
        pacing_max_ms=pacing_max_ms,
    )
    site_config = LiepinOpenCliSiteConfig(
        allowed_hosts=("www.liepin.com", "h.liepin.com"),
        allowed_start_urls=(LIEPIN_RECRUITER_SEARCH_URL,),
        allowed_click_refs=allowed_click_refs,
        lease_dir=lease_dir,
        artifact_root=lease_dir,
        detail_open_timeout_seconds=detail_open_timeout_seconds,
    )
    return LiepinSiteAdapter(
        browser_config=browser_config,
        site_config=site_config,
        automation=OpenCliBrowserAutomation(
            config=browser_config,
            commands=commands,
        ),
    )


def test_liepin_opencli_timing_recorder_skips_prod_artifact(tmp_path: Path) -> None:
    recorder = LiepinOpenCliTimingRecorder(artifact_root=tmp_path)

    recorder.record(
        OpenCliBrowserTiming(
            command="browser.fill",
            session="seektalent-liepin",
            argv_len=6,
            duration_ms=12.5,
            ok=True,
        )
    )

    assert not (tmp_path / "protected" / "opencli-timing").exists()


def test_liepin_opencli_timing_recorder_writes_safe_dev_artifact(tmp_path: Path) -> None:
    recorder = LiepinOpenCliTimingRecorder(artifact_root=tmp_path, output_mode="dev")

    recorder.record(
        OpenCliBrowserTiming(
            command="browser.fill",
            session="seektalent-liepin",
            argv_len=6,
            duration_ms=12.5,
            ok=True,
        )
    )

    timing_files = list((tmp_path / "protected" / "opencli-timing").glob("*.jsonl"))
    assert len(timing_files) == 1
    raw_log = timing_files[0].read_text(encoding="utf-8")
    record = json.loads(raw_log)
    assert record["command"] == "browser.fill"
    assert record["session"] == "seektalent-liepin"
    assert record["ok"] is True
    assert record["argv_len"] == 6
    assert record["duration_ms"] == 12.5
    assert "safe_reason_code" not in record
    assert "敏感关键词" not in raw_log


def test_liepin_opencli_timing_recorder_persists_automation_metadata_without_argv_text(tmp_path: Path) -> None:
    commands = FakeCommands(
        outputs={("opencli", "browser", "seektalent-liepin", "fill", "26", "敏感关键词"): '{"filled":true}'}
    )
    browser_config = OpenCliBrowserConfig(
        command=("opencli",),
        session="seektalent-liepin",
        timeout_seconds=10,
        pacing_enabled=False,
    )
    automation = OpenCliBrowserAutomation(
        config=browser_config,
        commands=commands,
        timing_recorder=LiepinOpenCliTimingRecorder(artifact_root=tmp_path, output_mode="dev"),
    )

    automation.fill(target_args=("26", "敏感关键词"), text_size=5)

    raw_log = next((tmp_path / "protected" / "opencli-timing").glob("*.jsonl")).read_text(encoding="utf-8")
    record = json.loads(raw_log)
    assert record["command"] == "browser.fill"
    assert record["session"] == "seektalent-liepin"
    assert record["argv_len"] == 6
    assert record["ok"] is True
    assert "敏感关键词" not in raw_log


def _single_tab_list(*, page_id: str = "page-1", url: str = LIEPIN_SEARCH_URL) -> str:
    return json.dumps([{"page": page_id, "url": url, "active": True}])


def test_recover_connection_restarts_opencli_daemon_without_current_chrome_tab_opener(monkeypatch) -> None:
    monkeypatch.setattr("seektalent.providers.liepin.liepin_site_adapter.time.sleep", lambda _: None)
    commands = FakeCommands(
        outputs={
            ("opencli", "daemon", "status"): [
                "Daemon: stale\nExtension: disconnected",
                "Daemon: running\nExtension: connected",
            ],
            ("opencli", "daemon", "restart"): "Daemon restarted successfully\n",
        }
    )
    result = _runner(commands).recover_connection()

    assert result.ok is True
    assert result.counts == {"restarted": 1}
    assert commands.calls == [
        ("opencli", "daemon", "status"),
        ("opencli", "daemon", "restart"),
        ("opencli", "daemon", "status"),
    ]
    assert not any(call[1:3] == ("browser", "seektalent-liepin") for call in commands.calls)


def _current_window_open_outputs(
    *, page_id: str = "page-1", url: str = LIEPIN_SEARCH_URL
) -> dict[tuple[str, ...], str]:
    return {
        ("opencli", "browser", "seektalent-liepin", "tab", "list"): "[]",
        ("opencli", "browser", "seektalent-liepin", "tab", "new", url): json.dumps({"page": page_id, "url": url}),
        ("opencli", "browser", "seektalent-liepin", "get", "url"): url,
    }


def _liepin_detail_payload_json(
    *,
    candidate_name: str = "王**",
    summary_text: str = "王** 40岁 工作14年 硕士 上海\n当前职位：数据开发专家",
) -> str:
    return json.dumps(
        {
            "candidate_name": candidate_name,
            "activeStatus": "7天内活跃",
            "jobStatus": "离职，正在找工作",
            "gender": "男",
            "age": 40,
            "city": "上海",
            "education": "硕士",
            "workYears": 14,
            "currentTitle": "数据开发专家",
            "currentCompany": "海光集成电路",
            "jobIntention": {"expectedRole": "数据开发专家", "expectedCity": "上海"},
            "workExperienceList": [
                {
                    "company": "海光集成电路",
                    "title": "高级主管工程师",
                    "dateRange": "2023.10-至今",
                    "summary": summary_text,
                    "description": summary_text,
                }
            ],
            "educationList": [{"school": "北京大学", "degree": "本科", "major": "计算机"}],
            "skills": ["Python", "Hive"],
        },
        ensure_ascii=False,
    )


detail_state = (
    "王** 40岁 工作14年 硕士 上海\n"
    "当前职位：数据开发专家\n"
    "海光集成电路 · 高级主管工程师 2023.10-至今\n"
    "负责数据仓库、数据治理、Python 平台和 Hive 数仓。\n"
    "北京大学 · 本科 · 计算机"
)

detail70_state = (
    "王** 40岁 工作14年 硕士 上海\n当前职位：数据开发专家\n负责数据仓库、数据治理、Python 平台和 Hive 数仓。"
)


def test_build_observation_does_not_block_browser_markup_text() -> None:
    observation = build_observation("<html><script></script>localStorage cookie=placeholder</html>")

    assert observation["chars"] > 0
    assert "<html>" in str(observation["text"])


def test_opencli_mutating_actions_apply_pacing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("seektalent.opencli_browser.automation.time.sleep", sleeps.append)
    monkeypatch.setattr("seektalent.opencli_browser.automation.random.uniform", lambda low, high: low)
    commands = RefEvalCommands(
        eval_outputs_by_ref={},
        default_eval_output=_liepin_detail_payload_json(summary_text=detail_state),
        outputs={
            ("opencli", "browser", "seektalent-liepin", "fill", "--role", "combobox", "--nth", "0", "python"): "{}",
        },
    )
    runner = _runner(
        commands,
        lease_dir=tmp_path,
        pacing_enabled=True,
        pacing_min_ms=700,
        pacing_max_ms=1800,
    )

    runner.fill(target="搜索", text="python")

    assert sleeps == [0.7]


def test_extract_structured_liepin_cards_returns_structured_evidence_without_card_text(tmp_path: Path) -> None:
    state_text = "<div id=resultList>共1位人选</div>"
    probe_output = json.dumps(
        {
            "ok": True,
            "schema_version": "seektalent.liepin_structured_cards_probe.v1",
            "cards": [
                {
                    "provider_rank": 1,
                    "ref": "70",
                    "masked_name": True,
                    "gender": "男",
                    "age": 40,
                    "work_years": 14,
                    "city": "上海",
                    "expected_city": "上海",
                    "education_level": "硕士",
                    "current_or_recent_company": "海光集成电路",
                    "current_or_recent_title": "高级主管工程师",
                    "job_intention": "数据开发专家",
                    "active_status": "7天内活跃",
                    "badges": ["统招本科"],
                    "skill_tags": ["Python", "Hive", "数据仓库"],
                    "experience_preview": [
                        {
                            "company": "海光集成电路",
                            "title": "高级主管工程师",
                            "date_range": "2023.10-至今",
                        }
                    ],
                    "education_preview": [
                        {
                            "school": "北京大学",
                            "major": "计算机",
                            "degree": "本科",
                        }
                    ],
                }
            ],
        },
        ensure_ascii=False,
    )
    commands = RefEvalCommands(
        eval_outputs_by_ref={ANY_STRUCTURED_CARD_PROBE: probe_output},
        default_eval_output=_liepin_detail_payload_json(summary_text=detail_state),
        outputs={
            ("opencli", "browser", "seektalent-liepin", "get", "url"): (
                "https://h.liepin.com/search/getConditionItem#session"
            ),
            ("opencli", "browser", "seektalent-liepin", "state"): state_text,
        },
    )

    result = _runner(commands, lease_dir=tmp_path).extract_structured_liepin_cards(source_run_id="run-1", max_cards=10)

    assert result.ok is True
    payload = json.loads(result.private_output)
    assert payload["schema_version"] == "seektalent.opencli_liepin_structured_cards.v1"
    assert result.to_tool_payload()["observation"] == payload
    first = payload["cards"][0]
    assert first["provider_rank"] == 1
    assert first["ref"] == "70"
    assert first["masked_name"] is True
    assert first["current_or_recent_company"] == "海光集成电路"
    assert first["current_or_recent_title"] == "高级主管工程师"
    assert first["education_level"] == "硕士"
    assert first["work_years"] == 14
    assert first["skill_tags"] == ["Python", "Hive", "数据仓库"]
    assert first["experience_preview"][0]["company"] == "海光集成电路"
    assert first["education_preview"][0]["school"] == "北京大学"
    LiepinSafeCardSummary.model_validate(_safe_card_summary_contract_fields(first))
    encoded = json.dumps(payload, ensure_ascii=False)
    for forbidden in ("visible_text", "normalized_card_text", "raw_html", "inner_text", "fullText", "rawText"):
        assert forbidden not in encoded


def test_extract_visible_liepin_cards_delegates_to_structured_payload_without_card_text(tmp_path: Path) -> None:
    probe_output = json.dumps(
        {
            "ok": True,
            "schema_version": "seektalent.liepin_structured_cards_probe.v1",
            "cards": [
                {
                    "provider_rank": 1,
                    "ref": "70",
                    "masked_name": True,
                    "current_or_recent_company": "海光集成电路",
                    "current_or_recent_title": "高级主管工程师",
                }
            ],
        },
        ensure_ascii=False,
    )
    commands = RefEvalCommands(
        eval_outputs_by_ref={ANY_STRUCTURED_CARD_PROBE: probe_output},
        outputs={
            ("opencli", "browser", "seektalent-liepin", "get", "url"): (
                "https://h.liepin.com/search/getConditionItem#session"
            ),
            ("opencli", "browser", "seektalent-liepin", "state"): "<div id=resultList>共1位人选</div>",
        },
    )

    result = _runner(commands, lease_dir=tmp_path).extract_visible_liepin_cards(source_run_id="run-1", max_cards=10)

    assert result.ok is True
    assert result.action == "extract_visible_liepin_cards"
    payload = json.loads(result.private_output)
    assert payload["schema_version"] == "seektalent.opencli_liepin_structured_cards.v1"
    assert payload["cards"][0]["masked_name"] is True
    encoded = json.dumps(payload, ensure_ascii=False)
    for forbidden in ("visible_text", "normalized_card_text", "raw_html", "inner_text", "fullText", "rawText"):
        assert forbidden not in encoded


@pytest.mark.parametrize(
    ("method_name", "expected_action"),
    [
        ("extract_structured_liepin_cards", "extract_structured_liepin_cards"),
        ("extract_visible_liepin_cards", "extract_visible_liepin_cards"),
    ],
)
def test_liepin_card_extractors_sanitize_terminal_state_failures(
    method_name: str,
    expected_action: str,
    tmp_path: Path,
) -> None:
    raw_sentinel = "SENTINEL_RAW_PAGE_TEXT visible_text normalized_card_text fullText rawText"
    commands = RefEvalCommands(
        eval_outputs_by_ref={ANY_STRUCTURED_CARD_PROBE: _structured_cards_probe_json("70")},
        outputs={
            ("opencli", "browser", "seektalent-liepin", "get", "url"): (
                "https://h.liepin.com/search/getConditionItem#session"
            ),
            ("opencli", "browser", "seektalent-liepin", "state"): f"请登录后继续 {raw_sentinel}",
        },
    )
    runner = _runner(commands, lease_dir=tmp_path)

    result = getattr(runner, method_name)(source_run_id="run-1", max_cards=10)

    assert result.ok is False
    assert result.action == expected_action
    assert result.safe_reason_code == "liepin_opencli_login_required"
    assert result.private_output == ""
    assert "text" not in result.observation
    encoded = json.dumps(result.to_tool_payload(), ensure_ascii=False)
    for forbidden in ("visible_text", "normalized_card_text", "fullText", "rawText", "SENTINEL_RAW_PAGE_TEXT"):
        assert forbidden not in encoded


def test_extract_visible_liepin_cards_binds_ref_to_same_card_summary(tmp_path: Path) -> None:
    state_text = "<div id=resultList>共2位人选</div>"
    probe_output = json.dumps(
        {
            "ok": True,
            "schema_version": "seektalent.liepin_structured_cards_probe.v1",
            "cards": [
                {
                    "provider_rank": 1,
                    "ref": "71",
                    "masked_name": True,
                    "gender": "女",
                    "age": 29,
                    "work_years": 6,
                    "city": "杭州",
                    "expected_city": "杭州",
                    "education_level": "本科",
                    "current_or_recent_company": "杭州科技公司",
                    "current_or_recent_title": "实时数仓工程师",
                    "skill_tags": ["Flink", "Spark", "实时数仓"],
                }
            ],
        },
        ensure_ascii=False,
    )
    commands = RefEvalCommands(
        eval_outputs_by_ref={ANY_STRUCTURED_CARD_PROBE: probe_output},
        default_eval_output=_liepin_detail_payload_json(summary_text=detail70_state),
        outputs={
            ("opencli", "browser", "seektalent-liepin", "get", "url"): (
                "https://h.liepin.com/search/getConditionItem#session"
            ),
            ("opencli", "browser", "seektalent-liepin", "state"): state_text,
        },
    )

    result = _runner(commands, lease_dir=tmp_path).extract_structured_liepin_cards(source_run_id="run-1", max_cards=10)

    assert result.ok is True
    payload = json.loads(result.private_output)
    assert payload["card_count"] == 1
    card = payload["cards"][0]
    assert card["ref"] == "71"
    assert card["masked_name"] is True
    assert card["current_or_recent_company"] == "杭州科技公司"
    assert card["current_or_recent_title"].startswith("实时数仓工程师")
    assert "visible_text" not in card
    assert "normalized_card_text" not in card


def test_structured_liepin_cards_parser_preserves_bool_masked_name_and_rejects_display_string() -> None:
    output = json.dumps(
        {
            "ok": True,
            "schema_version": "seektalent.liepin_structured_cards_probe.v1",
            "cards": [
                {
                    "provider_rank": 1,
                    "ref": "70",
                    "masked_name": True,
                    "gender": "男",
                    "age": 40,
                    "work_years": 14,
                    "city": "上海",
                    "expected_city": "上海",
                    "education_level": "硕士",
                    "current_or_recent_company": "海光集成电路",
                    "current_or_recent_title": "高级主管工程师",
                    "job_intention": "数据开发专家",
                    "active_status": "7天内活跃",
                    "badges": ["统招本科"],
                    "skill_tags": ["Python", "Hive", "数据仓库"],
                    "experience_preview": [{"company": "海光集成电路", "title": "高级主管工程师"}],
                    "education_preview": [{"school": "北京大学", "major": "计算机", "degree": "本科"}],
                }
            ],
        },
        ensure_ascii=False,
    )

    cards = _safe_structured_cards_from_probe_output(output, max_cards=10)

    assert cards[0]["masked_name"] is True
    LiepinSafeCardSummary.model_validate(_safe_card_summary_contract_fields(cards[0]))

    bad_output = json.loads(output)
    bad_output["cards"][0]["masked_name"] = "王**"
    with pytest.raises(OpenCliBrowserError):
        _safe_structured_cards_from_probe_output(json.dumps(bad_output, ensure_ascii=False), max_cards=10)


@pytest.mark.parametrize(
    "forbidden_patch",
    [
        {"visible_text": "raw visible card text"},
        {"normalized_card_text": "legacy normalized card text"},
        {"experience_preview": [{"title": "高级主管工程师", "visible_text": "nested card text"}]},
        {"education_preview": [{"school": "北京大学", "normalized_card_text": "nested normalized text"}]},
    ],
)
def test_structured_card_probe_rejects_forbidden_card_text_fields(
    forbidden_patch: dict[str, object],
) -> None:
    card = {
        "provider_rank": 1,
        "ref": "70",
        "current_or_recent_title": "高级主管工程师",
    }
    card.update(forbidden_patch)
    output = json.dumps(
        {
            "ok": True,
            "schema_version": "seektalent.liepin_structured_cards_probe.v1",
            "cards": [card],
        },
        ensure_ascii=False,
    )

    with pytest.raises(OpenCliBrowserError):
        _safe_structured_cards_from_probe_output(output, max_cards=10)


@pytest.mark.parametrize("ok_value", [None, False, "true", 1])
def test_structured_liepin_cards_parser_requires_true_ok(ok_value: object) -> None:
    payload: dict[str, object] = {
        "schema_version": "seektalent.liepin_structured_cards_probe.v1",
        "cards": [],
    }
    if ok_value is not None:
        payload["ok"] = ok_value

    with pytest.raises(OpenCliBrowserError):
        _safe_structured_cards_from_probe_output(json.dumps(payload, ensure_ascii=False), max_cards=10)


def test_structured_liepin_cards_parser_drops_preview_fields_outside_worker_contract() -> None:
    output = json.dumps(
        {
            "ok": True,
            "schema_version": "seektalent.liepin_structured_cards_probe.v1",
            "cards": [
                {
                    "provider_rank": 1,
                    "ref": "70",
                    "masked_name": True,
                    "experience_preview": [
                        {
                            "company": "海光集成电路",
                            "title": "高级主管工程师",
                            "date_range": "2023.10-至今",
                            "duration": "2年",
                            "is_current": True,
                            "industry": "芯片",
                            "location": "上海",
                        }
                    ],
                    "education_preview": [
                        {
                            "school": "北京大学",
                            "major": "计算机",
                            "degree": "本科",
                            "recruitment_type": "统招",
                            "date_range": "2002.09-2006.07",
                            "duration": "4年",
                        }
                    ],
                }
            ],
        },
        ensure_ascii=False,
    )

    cards = _safe_structured_cards_from_probe_output(output, max_cards=10)

    experience = cards[0]["experience_preview"][0]
    education = cards[0]["education_preview"][0]
    assert experience["is_current"] is True
    assert experience["duration"] == "2年"
    assert "industry" not in experience
    assert "location" not in experience
    assert education["recruitment_type"] == "统招"
    assert education["date_range"] == "2002.09-2006.07"
    assert "duration" not in education
    LiepinSafeCardSummary.model_validate(_safe_card_summary_contract_fields(cards[0]))


def test_structured_liepin_cards_probe_script_is_structured_and_ranks_after_ref_filter() -> None:
    script = _liepin_structured_cards_payload_probe_script(max_cards=10)

    assert "seektalent.liepin_structured_cards_probe.v1" in script
    assert "provider_rank: cards.length + 1" in script
    assert "provider_rank: index + 1" not in script
    assert "masked_name: Boolean(maskedNameFrom(text))" in script
    assert "masked_name: maskedNameFrom(text)" not in script
    for forbidden in ("visible_text", "normalized_card_text", "raw_html", "inner_text", "fullText", "rawText"):
        assert forbidden not in script


def test_status_maps_opencli_doctor_success() -> None:
    commands = RefEvalCommands(
        eval_outputs_by_ref={},
        default_eval_output=_liepin_detail_payload_json(summary_text=detail_state),
        outputs={
            ("opencli", "daemon", "status"): (
                "Daemon: running (PID 123)\nVersion: 1.8.0\nExtension: connected (v1.8.0)\nProfiles: default v1.8.0\n"
            )
        },
    )
    result = _runner(commands).status()

    assert result.ok is True
    assert result.safe_reason_code == "configured"
    assert commands.calls == [("opencli", "daemon", "status")]


def test_status_does_not_call_doctor_or_start_browser_probe() -> None:
    commands = FakeCommands(outputs={("opencli", "daemon", "status"): "Daemon: not running\n"})

    result = _runner(commands).status()

    assert result.ok is False
    assert result.safe_reason_code == "liepin_opencli_daemon_not_running"
    assert commands.calls == [("opencli", "daemon", "status")]
    assert all("doctor" not in call for call in commands.calls for call in call)


def test_status_blocks_when_daemon_is_stale() -> None:
    commands = FakeCommands(outputs={("opencli", "daemon", "status"): "Daemon: stale\nExtension: connected\n"})

    result = _runner(commands).status()

    assert result.ok is False
    assert result.safe_reason_code == "liepin_opencli_daemon_stale"


def test_status_reports_unavailable_for_malformed_daemon_output() -> None:
    commands = FakeCommands(outputs={("opencli", "daemon", "status"): "unexpected status text\n"})

    result = _runner(commands).status()

    assert result.ok is False
    assert result.safe_reason_code == "liepin_opencli_status_unavailable"


def test_opencli_commands_inherit_background_window_mode() -> None:
    commands = RefEvalCommands(
        eval_outputs_by_ref={},
        default_eval_output=_liepin_detail_payload_json(summary_text=detail70_state),
        outputs={
            ("opencli", "browser", "seektalent-liepin", "get", "url"): (
                "https://h.liepin.com/search/getConditionItem#session"
            )
        },
    )

    result = _runner(commands).get_url()

    assert result.ok is True
    assert commands.envs
    assert commands.envs[-1] is not None
    assert commands.envs[-1]["OPENCLI_WINDOW"] == "background"


def test_status_blocks_when_extension_is_disconnected() -> None:
    commands = RefEvalCommands(
        eval_outputs_by_ref={},
        default_eval_output=_liepin_detail_payload_json(summary_text=detail_state),
        outputs={
            ("opencli", "daemon", "status"): ("Daemon: running (PID 123)\nVersion: 1.8.0\nExtension: disconnected\n")
        },
    )

    result = _runner(commands).status()

    assert result.ok is False
    assert result.safe_reason_code == "liepin_opencli_extension_disconnected"


def test_open_liepin_tab_rejects_wrong_host_before_opencli_call() -> None:
    commands = FakeCommands()
    with pytest.raises(OpenCliBrowserError) as error:
        _runner(commands).open_liepin_tab("https://example.com/")

    assert error.value.safe_reason_code == "liepin_opencli_host_blocked"
    assert commands.calls == []


def test_open_liepin_tab_rejects_unapproved_start_url() -> None:
    commands = FakeCommands()
    with pytest.raises(OpenCliBrowserError) as error:
        _runner(commands).open_liepin_tab("https://www.liepin.com/")

    assert error.value.safe_reason_code == "liepin_opencli_start_url_blocked"
    assert commands.calls == []


def test_open_liepin_tab_reuses_verified_owned_lease_instead_of_opening_duplicate_tab(tmp_path: Path) -> None:
    liepin_url = "https://h.liepin.com/search/getConditionItem#session"
    commands = RefEvalCommands(
        eval_outputs_by_ref={},
        default_eval_output=_liepin_detail_payload_json(summary_text=detail70_state),
        outputs={
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): (f'[{{"page":"page-0","url":"{liepin_url}"}}]'),
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-0"): "{}",
        },
    )
    owner_nonce = "nonce-owned-0"
    runner = _runner(commands, lease_dir=tmp_path)
    runner._write_owned_page_marker(
        page_id="page-0",
        url=liepin_url,
        runtime_run_id="run-opencli-test",
        source_lane_run_id="run-opencli-test:source:liepin:lane:1",
        owner_nonce=owner_nonce,
        opened_at=9_999_999_999.0,
    )
    (tmp_path / "seektalent-liepin.json").write_text(
        json.dumps(
            {
                "schema_version": "seektalent.opencli_lease.v1",
                "session": "seektalent-liepin",
                "page_id": "page-0",
                "url": liepin_url,
                "last_activity_at": 9_999_999_999,
                "owner_nonce": owner_nonce,
            }
        ),
        encoding="utf-8",
    )

    result = runner.open_liepin_tab(liepin_url)

    assert result.ok is True
    assert result.counts == {"reused": 1}
    assert commands.calls == [
        ("opencli", "browser", "seektalent-liepin", "tab", "list"),
        ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-0"),
        ("opencli", "browser", "seektalent-liepin", "open", "--tab", "page-0", liepin_url),
    ]
    lease = json.loads((tmp_path / "seektalent-liepin.json").read_text(encoding="utf-8"))
    assert lease["page_id"] == "page-0"


def test_open_liepin_tab_reuses_canonical_search_marker_without_lease(tmp_path: Path) -> None:
    liepin_url = "https://h.liepin.com/search/getConditionItem#session"
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-search-old"): "{}",
            ("opencli", "browser", "seektalent-liepin", "get", "url"): liepin_url,
            ("opencli", "browser", "seektalent-liepin", "tab", "new", liepin_url): (
                f'{{"url":"{liepin_url}","page":"page-search-newly-opened"}}'
            ),
        }
    )
    runner = _runner(commands, lease_dir=tmp_path)
    runner._write_owned_page_marker(
        page_id="page-search-new",
        url=liepin_url,
        runtime_run_id="run-opencli-test",
        source_lane_run_id="run-opencli-test:source:liepin:lane:2",
        owner_nonce="nonce-new",
        opened_at=9_999_999_999.0,
    )
    runner._write_owned_page_marker(
        page_id="page-search-old",
        url=liepin_url,
        runtime_run_id="run-opencli-test",
        source_lane_run_id="run-opencli-test:source:liepin:lane:1",
        owner_nonce="nonce-old",
        opened_at=9_999_999_900.0,
    )

    result = runner.open_liepin_tab(liepin_url)

    assert result.ok is True
    assert result.counts == {"reused": 1}
    assert ("opencli", "browser", "seektalent-liepin", "tab", "new", liepin_url) not in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-search-new") not in commands.calls
    assert commands.calls == [
        ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-search-old"),
        ("opencli", "browser", "seektalent-liepin", "get", "url"),
        ("opencli", "browser", "seektalent-liepin", "open", "--tab", "page-search-old", liepin_url),
    ]
    lease = json.loads((tmp_path / "seektalent-liepin.json").read_text(encoding="utf-8"))
    assert lease["page_id"] == "page-search-old"


def test_open_liepin_tab_skips_stale_canonical_marker_when_reset_fails(tmp_path: Path) -> None:
    liepin_url = "https://h.liepin.com/search/getConditionItem#session"
    reset_error = subprocess.CalledProcessError(1, ["opencli"], stderr="status unavailable")
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-search-stale"): "{}",
            ("opencli", "browser", "seektalent-liepin", "get", "url"): liepin_url,
            ("opencli", "browser", "seektalent-liepin", "open", "--tab", "page-search-stale", liepin_url): reset_error,
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): json.dumps(
                [{"page": "page-search-live", "url": liepin_url, "active": False}]
            ),
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-search-live"): "{}",
            ("opencli", "browser", "seektalent-liepin", "open", "--tab", "page-search-live", liepin_url): "{}",
        }
    )
    runner = _runner(commands, lease_dir=tmp_path)
    runner._write_owned_page_marker(
        page_id="page-search-stale",
        url=liepin_url,
        runtime_run_id="run-opencli-test",
        source_lane_run_id="run-opencli-test:source:liepin:lane:1",
        owner_nonce="nonce-stale",
        opened_at=9_999_999_900.0,
    )

    result = runner.open_liepin_tab(liepin_url)

    assert result.ok is True
    assert result.counts == {"reused": 1}
    assert ("opencli", "browser", "seektalent-liepin", "tab", "new", liepin_url) not in commands.calls
    lease = json.loads((tmp_path / "seektalent-liepin.json").read_text(encoding="utf-8"))
    assert lease["page_id"] == "page-search-live"
    owned_pages = json.loads((tmp_path / "seektalent-liepin-owned-pages.json").read_text(encoding="utf-8"))
    assert "page-search-stale" not in owned_pages
    assert "page-search-live" in owned_pages


def test_open_liepin_tab_selects_existing_search_tab_when_current_active_tab_is_workbench(tmp_path: Path) -> None:
    liepin_url = "https://h.liepin.com/search/getConditionItem#session"
    workbench_url = "http://127.0.0.1:8123/sessions/session_bd4363d1c367424d"
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): json.dumps(
                [
                    {"page": "page-workbench", "url": workbench_url, "active": True},
                    {"page": "page-search", "url": liepin_url, "active": False},
                ]
            ),
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-search"): "{}",
            ("opencli", "browser", "seektalent-liepin", "open", "--tab", "page-search", liepin_url): "{}",
        }
    )
    result = _runner(
        commands,
        lease_dir=tmp_path,
    ).open_liepin_tab(liepin_url)

    assert result.ok is True
    assert result.counts == {"reused": 1}
    assert ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-search") in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "tab", "new", liepin_url) not in commands.calls
    lease = json.loads((tmp_path / "seektalent-liepin.json").read_text(encoding="utf-8"))
    assert lease["page_id"] == "page-search"


def test_session_status_probe_prepares_search_surface_from_non_liepin_active_tab(tmp_path: Path) -> None:
    state_text = "\n".join(
        [
            f"URL: {LIEPIN_SEARCH_URL}",
            "包含全部关键词",
            "[ref=search-input] <input id=rc_select_1 role=combobox />",
        ]
    )
    commands = FakeCommands(
        outputs={
            ("opencli", "daemon", "status"): "Daemon: running\nExtension: connected",
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): json.dumps(
                [{"page": "page-github", "url": "https://github.com/", "active": True}]
            ),
            ("opencli", "browser", "seektalent-liepin", "get", "url"): LIEPIN_SEARCH_URL,
            ("opencli", "browser", "seektalent-liepin", "state"): state_text,
        }
    )

    status = _runner(commands, lease_dir=tmp_path).session_status_probe(
        connection_id="liepin-opencli",
        provider_account_hash="caller-provider-hash",
    )

    assert status.status == "ready"
    assert status.provider_account_hash == "liepin-opencli-local-browser-profile"
    assert status.current_url == LIEPIN_SEARCH_URL
    assert status.search_surface_ready is True
    assert status.result_surface_ready is True
    assert ("opencli", "browser", "seektalent-liepin", "tab", "new", LIEPIN_SEARCH_URL) in commands.calls
    assert status.model_dump(by_alias=True)["providerAccountHash"] == "liepin-opencli-local-browser-profile"
    assert status.model_dump(by_alias=True)["currentUrl"] == LIEPIN_SEARCH_URL
    assert status.model_dump(by_alias=True)["searchSurfaceReady"] is True


def test_session_status_probe_prepares_search_surface_from_liepin_homepage(tmp_path: Path) -> None:
    homepage_url = "https://h.liepin.com/?time=1783577138392"
    state_text = "\n".join(
        [
            f"URL: {LIEPIN_SEARCH_URL}",
            "包含全部关键词",
            "[ref=search-input] <input id=rc_select_1 role=combobox />",
        ]
    )
    commands = FakeCommands(
        outputs={
            ("opencli", "daemon", "status"): "Daemon: running\nExtension: connected",
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): [
                json.dumps([{"page": "page-home", "url": homepage_url, "active": True}]),
                json.dumps(
                    [
                        {"page": "page-home", "url": homepage_url, "active": False},
                        {"page": "page-search", "url": LIEPIN_SEARCH_URL, "active": True},
                    ]
                ),
            ],
            ("opencli", "browser", "seektalent-liepin", "tab", "new", LIEPIN_SEARCH_URL): json.dumps(
                {"page": "page-search", "url": LIEPIN_SEARCH_URL}
            ),
            ("opencli", "browser", "seektalent-liepin", "get", "url"): LIEPIN_SEARCH_URL,
            ("opencli", "browser", "seektalent-liepin", "state"): state_text,
        }
    )

    status = _runner(commands, lease_dir=tmp_path).session_status_probe(
        connection_id="liepin-opencli",
        provider_account_hash="caller-provider-hash",
    )

    assert status.status == "ready"
    assert status.current_url == LIEPIN_SEARCH_URL
    assert ("opencli", "browser", "seektalent-liepin", "tab", "new", LIEPIN_SEARCH_URL) in commands.calls


def test_session_status_probe_prepares_search_surface_from_liepin_detail_page(tmp_path: Path) -> None:
    detail_url = "https://h.liepin.com/resume/showresumedetail/?res_id_encode=abc&type=normal"
    state_text = "\n".join(
        [
            f"URL: {LIEPIN_SEARCH_URL}",
            "包含全部关键词",
            "[ref=search-input] <input id=rc_select_1 role=combobox />",
        ]
    )
    commands = FakeCommands(
        outputs={
            ("opencli", "daemon", "status"): "Daemon: running\nExtension: connected",
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): [
                json.dumps([{"page": "page-detail", "url": detail_url, "active": True}]),
                json.dumps(
                    [
                        {"page": "page-detail", "url": detail_url, "active": False},
                        {"page": "page-search", "url": LIEPIN_SEARCH_URL, "active": True},
                    ]
                ),
            ],
            ("opencli", "browser", "seektalent-liepin", "tab", "new", LIEPIN_SEARCH_URL): json.dumps(
                {"page": "page-search", "url": LIEPIN_SEARCH_URL}
            ),
            ("opencli", "browser", "seektalent-liepin", "get", "url"): LIEPIN_SEARCH_URL,
            ("opencli", "browser", "seektalent-liepin", "state"): state_text,
        }
    )

    status = _runner(commands, lease_dir=tmp_path).session_status_probe(
        connection_id="liepin-opencli",
        provider_account_hash="caller-provider-hash",
    )

    assert status.status == "ready"
    assert status.current_url == LIEPIN_SEARCH_URL
    assert ("opencli", "browser", "seektalent-liepin", "tab", "new", LIEPIN_SEARCH_URL) in commands.calls


def test_session_status_probe_does_not_accept_blank_page_after_search_open(tmp_path: Path) -> None:
    commands = FakeCommands(
        outputs={
            ("opencli", "daemon", "status"): "Daemon: running\nExtension: connected",
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): [
                json.dumps([{"page": "page-blank", "url": "data:text/html,<html></html>", "active": True}]),
                json.dumps([{"page": "page-blank", "url": "data:text/html,<html></html>", "active": True}]),
            ],
            ("opencli", "browser", "seektalent-liepin", "tab", "new", LIEPIN_SEARCH_URL): json.dumps(
                {"page": "page-search", "url": LIEPIN_SEARCH_URL}
            ),
            ("opencli", "browser", "seektalent-liepin", "get", "url"): "data:text/html,<html></html>",
            ("opencli", "browser", "seektalent-liepin", "state"): "URL: data:text/html,<html></html>",
        }
    )

    status = _runner(commands, lease_dir=tmp_path).session_status_probe(
        connection_id="liepin-opencli",
        provider_account_hash="caller-provider-hash",
    )

    assert status.status == "missing"
    assert status.safe_reason_code == "liepin_opencli_search_not_ready"
    assert status.current_url == "data:text/html,<html></html>"


def test_session_status_probe_resets_opened_blank_page_to_search_surface(tmp_path: Path) -> None:
    state_text = "\n".join(
        [
            f"URL: {LIEPIN_SEARCH_URL}",
            "包含全部关键词",
            "[ref=search-input] <input id=rc_select_1 role=combobox />",
        ]
    )
    commands = FakeCommands(
        outputs={
            ("opencli", "daemon", "status"): "Daemon: running\nExtension: connected",
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): [
                json.dumps([{"page": "page-blank", "url": "data:text/html,<html></html>", "active": True}]),
                json.dumps([{"page": "page-blank", "url": "data:text/html,<html></html>", "active": True}]),
            ],
            ("opencli", "browser", "seektalent-liepin", "tab", "new", LIEPIN_SEARCH_URL): json.dumps(
                {"page": "page-search", "url": LIEPIN_SEARCH_URL}
            ),
            ("opencli", "browser", "seektalent-liepin", "open", "--tab", "page-search", LIEPIN_SEARCH_URL): "{}",
            ("opencli", "browser", "seektalent-liepin", "get", "url"): [
                "data:text/html,<html></html>",
                LIEPIN_SEARCH_URL,
            ],
            ("opencli", "browser", "seektalent-liepin", "state"): state_text,
        }
    )

    status = _runner(commands, lease_dir=tmp_path).session_status_probe(
        connection_id="liepin-opencli",
        provider_account_hash="caller-provider-hash",
    )

    assert status.status == "ready"
    assert status.current_url == LIEPIN_SEARCH_URL
    assert ("opencli", "browser", "seektalent-liepin", "open", "--tab", "page-search", LIEPIN_SEARCH_URL) in commands.calls


def test_session_status_probe_returns_status_when_reset_followup_state_fails(tmp_path: Path) -> None:
    status_error = subprocess.CalledProcessError(1, ["opencli"], stderr="status unavailable")
    commands = FakeCommands(
        outputs={
            ("opencli", "daemon", "status"): "Daemon: running\nExtension: connected",
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): [
                json.dumps([{"page": "page-blank", "url": "data:text/html,<html></html>", "active": True}]),
                json.dumps([{"page": "page-blank", "url": "data:text/html,<html></html>", "active": True}]),
            ],
            ("opencli", "browser", "seektalent-liepin", "tab", "new", LIEPIN_SEARCH_URL): json.dumps(
                {"page": "page-search", "url": LIEPIN_SEARCH_URL}
            ),
            ("opencli", "browser", "seektalent-liepin", "open", "--tab", "page-search", LIEPIN_SEARCH_URL): "{}",
            ("opencli", "browser", "seektalent-liepin", "get", "url"): [
                "data:text/html,<html></html>",
                status_error,
                "data:text/html,<html></html>",
            ],
        }
    )

    status = _runner(commands, lease_dir=tmp_path).session_status_probe(
        connection_id="liepin-opencli",
        provider_account_hash="caller-provider-hash",
    )

    assert status.status == "missing"
    assert status.safe_reason_code == "liepin_opencli_status_unavailable"
    assert status.current_url == "data:text/html,<html></html>"


def test_session_status_probe_lets_opencli_browser_command_start_stopped_daemon(tmp_path: Path) -> None:
    state_text = "\n".join(
        [
            f"URL: {LIEPIN_SEARCH_URL}",
            "包含全部关键词",
            "[ref=search-input] <input id=rc_select_1 role=combobox />",
        ]
    )
    commands = FakeCommands(
        outputs={
            ("opencli", "daemon", "status"): "Daemon: not running\n",
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): json.dumps(
                [{"page": "page-existing", "url": LIEPIN_SEARCH_URL, "active": True}]
            ),
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-existing"): "{}",
            ("opencli", "browser", "seektalent-liepin", "open", "--tab", "page-existing", LIEPIN_SEARCH_URL): "{}",
            ("opencli", "browser", "seektalent-liepin", "get", "url"): LIEPIN_SEARCH_URL,
            ("opencli", "browser", "seektalent-liepin", "state"): state_text,
        }
    )

    status = _runner(commands, lease_dir=tmp_path).session_status_probe(
        connection_id="liepin-opencli",
        provider_account_hash=None,
    )

    assert status.status == "ready"
    assert status.provider_account_hash == "liepin-opencli-local-browser-profile"
    assert status.current_url == LIEPIN_SEARCH_URL
    assert commands.calls[:2] == [
        ("opencli", "daemon", "status"),
        ("opencli", "browser", "seektalent-liepin", "tab", "list"),
    ]


def test_open_liepin_tab_rejects_malformed_page_id(tmp_path: Path) -> None:
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): "[]",
            ("opencli", "browser", "seektalent-liepin", "tab", "new", LIEPIN_SEARCH_URL): (
                f'[{{"page":"bad/page","url":"{LIEPIN_SEARCH_URL}"}}]'
            ),
        }
    )

    with pytest.raises(OpenCliBrowserError) as error:
        _runner(commands, lease_dir=tmp_path).open_liepin_tab(LIEPIN_SEARCH_URL)

    assert error.value.safe_reason_code == "liepin_opencli_tab_response_malformed"


def test_open_liepin_tab_accepts_singleton_tab_new_list_response(tmp_path: Path) -> None:
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): "[]",
            ("opencli", "browser", "seektalent-liepin", "tab", "new", LIEPIN_SEARCH_URL): json.dumps(
                [{"id": "page-2", "url": LIEPIN_SEARCH_URL}]
            ),
        }
    )

    result = _runner(commands, lease_dir=tmp_path).open_liepin_tab(LIEPIN_SEARCH_URL)

    assert result.ok is True
    lease = json.loads((tmp_path / "seektalent-liepin.json").read_text(encoding="utf-8"))
    assert lease["page_id"] == "page-2"


def test_open_liepin_tab_recovers_page_id_from_tab_list_when_tab_new_output_is_unexpected(tmp_path: Path) -> None:
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): [
                "[]",
                "[]",
                json.dumps([{"id": "page-2", "url": LIEPIN_SEARCH_URL, "active": True}]),
            ],
            ("opencli", "browser", "seektalent-liepin", "tab", "new", LIEPIN_SEARCH_URL): "opened",
        }
    )

    result = _runner(commands, lease_dir=tmp_path).open_liepin_tab(LIEPIN_SEARCH_URL)

    assert result.ok is True
    lease = json.loads((tmp_path / "seektalent-liepin.json").read_text(encoding="utf-8"))
    assert lease["page_id"] == "page-2"


def test_open_liepin_tab_recovers_page_id_from_redirected_liepin_search_url(tmp_path: Path) -> None:
    redirected_url = "https://h.liepin.com/search/getConditionItem?city=010#session"
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): [
                "[]",
                "[]",
                json.dumps([{"id": "page-2", "url": redirected_url, "active": True}]),
            ],
            ("opencli", "browser", "seektalent-liepin", "tab", "new", LIEPIN_SEARCH_URL): "opened",
        }
    )

    result = _runner(commands, lease_dir=tmp_path).open_liepin_tab(LIEPIN_SEARCH_URL)

    assert result.ok is True
    lease = json.loads((tmp_path / "seektalent-liepin.json").read_text(encoding="utf-8"))
    assert lease["page_id"] == "page-2"


def test_open_liepin_tab_uses_current_session_when_opencli_tab_targets_are_unusable(tmp_path: Path) -> None:
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): [
                "[]",
                json.dumps([{"tabId": 2018717768, "url": LIEPIN_SEARCH_URL, "active": True}]),
                json.dumps([{"tabId": 2018717768, "url": LIEPIN_SEARCH_URL, "active": True}]),
            ],
            ("opencli", "browser", "seektalent-liepin", "tab", "new", LIEPIN_SEARCH_URL): json.dumps(
                {"url": LIEPIN_SEARCH_URL}
            ),
            ("opencli", "browser", "seektalent-liepin", "bind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "get", "url"): [
                "data:text/html,<html></html>",
                LIEPIN_SEARCH_URL,
            ],
            ("opencli", "browser", "seektalent-liepin", "open", LIEPIN_SEARCH_URL): json.dumps(
                {"url": LIEPIN_SEARCH_URL}
            ),
        }
    )

    result = _runner(commands, lease_dir=tmp_path).open_liepin_tab(LIEPIN_SEARCH_URL)

    assert result.ok is True
    assert result.counts == {"opened": 1, "unleased": 1}
    assert ("opencli", "browser", "seektalent-liepin", "open", LIEPIN_SEARCH_URL) in commands.calls
    assert not (tmp_path / "seektalent-liepin.json").exists()


def test_open_detail_tab_uses_current_session_when_opencli_tab_targets_are_unusable(tmp_path: Path) -> None:
    detail_url = "https://h.liepin.com/resume/showresumedetail/?res_id_encode=abc&type=normal"
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): [
                "[]",
                json.dumps([{"tabId": 2018717768, "url": detail_url, "active": True}]),
                json.dumps([{"tabId": 2018717768, "url": detail_url, "active": True}]),
            ],
            ("opencli", "browser", "seektalent-liepin", "tab", "new", detail_url): json.dumps(
                {"url": detail_url}
            ),
            ("opencli", "browser", "seektalent-liepin", "bind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "get", "url"): [
                "data:text/html,<html></html>",
                detail_url,
                detail_url,
            ],
            ("opencli", "browser", "seektalent-liepin", "open", detail_url): json.dumps({"url": detail_url}),
        }
    )

    opened = _runner(commands, lease_dir=tmp_path)._open_liepin_detail_cached_url(
        source_run_id="source-1",
        ref="70",
        rank=1,
        detail_url=detail_url,
        emit_events=False,
    )

    assert opened.ok is True
    assert ("opencli", "browser", "seektalent-liepin", "open", detail_url) in commands.calls
    assert not (tmp_path / "seektalent-liepin.json").exists()


def test_open_detail_tab_uses_current_session_when_bind_fails_but_current_url_matches(tmp_path: Path) -> None:
    detail_url = "https://h.liepin.com/resume/showresumedetail/?res_id_encode=abc&type=normal"
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): [
                "[]",
                json.dumps([{"tabId": 2018717768, "url": detail_url, "active": True}]),
            ],
            ("opencli", "browser", "seektalent-liepin", "tab", "new", detail_url): json.dumps(
                {"url": detail_url}
            ),
            ("opencli", "browser", "seektalent-liepin", "bind"): subprocess.CalledProcessError(
                1,
                ["opencli", "browser", "seektalent-liepin", "bind"],
                stderr="bind failed",
            ),
            ("opencli", "browser", "seektalent-liepin", "open", detail_url): json.dumps({"url": detail_url}),
            ("opencli", "browser", "seektalent-liepin", "get", "url"): detail_url,
        }
    )

    page_id = _runner(commands, lease_dir=tmp_path)._open_new_liepin_tab(url=detail_url, source_run_id="source-1")

    assert page_id is None
    assert ("opencli", "browser", "seektalent-liepin", "open", detail_url) in commands.calls
    assert not (tmp_path / "seektalent-liepin.json").exists()


def test_open_liepin_tab_binds_new_window_before_recovering_opened_page_id(tmp_path: Path) -> None:
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): [
                "[]",
                "[]",
                json.dumps([{"id": "page-2", "url": LIEPIN_SEARCH_URL, "active": True}]),
            ],
            ("opencli", "browser", "seektalent-liepin", "tab", "new", LIEPIN_SEARCH_URL): "opened",
            ("opencli", "browser", "seektalent-liepin", "bind"): "{}",
        }
    )

    result = _runner(commands, lease_dir=tmp_path).open_liepin_tab(LIEPIN_SEARCH_URL)

    assert result.ok is True
    assert ("opencli", "browser", "seektalent-liepin", "bind") in commands.calls
    lease = json.loads((tmp_path / "seektalent-liepin.json").read_text(encoding="utf-8"))
    assert lease["page_id"] == "page-2"


def test_open_liepin_tab_ignores_before_tab_list_status_unavailable(tmp_path: Path) -> None:
    tab_list_error = subprocess.CalledProcessError(1, ["opencli"], stderr="status unavailable")
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): [
                tab_list_error,
                json.dumps([{"id": "page-2", "url": LIEPIN_SEARCH_URL, "active": True}]),
            ],
            ("opencli", "browser", "seektalent-liepin", "tab", "new", LIEPIN_SEARCH_URL): json.dumps(
                {"page": "page-2", "url": LIEPIN_SEARCH_URL}
            ),
        }
    )

    result = _runner(commands, lease_dir=tmp_path).open_liepin_tab(LIEPIN_SEARCH_URL)

    assert result.ok is True
    lease = json.loads((tmp_path / "seektalent-liepin.json").read_text(encoding="utf-8"))
    assert lease["page_id"] == "page-2"


def test_open_liepin_tab_recovers_when_tab_new_reports_status_unavailable_but_window_opened(tmp_path: Path) -> None:
    tab_new_error = subprocess.CalledProcessError(1, ["opencli"], stderr="status unavailable")
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): [
                "[]",
                "[]",
                json.dumps([{"id": "page-2", "url": LIEPIN_SEARCH_URL, "active": True}]),
            ],
            ("opencli", "browser", "seektalent-liepin", "tab", "new", LIEPIN_SEARCH_URL): tab_new_error,
        }
    )

    result = _runner(commands, lease_dir=tmp_path).open_liepin_tab(LIEPIN_SEARCH_URL)

    assert result.ok is True
    lease = json.loads((tmp_path / "seektalent-liepin.json").read_text(encoding="utf-8"))
    assert lease["page_id"] == "page-2"


def test_open_liepin_tab_allows_bound_liepin_page_when_page_id_is_unavailable(tmp_path: Path) -> None:
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): [
                "[]",
                "[]",
                "[]",
            ],
            ("opencli", "browser", "seektalent-liepin", "tab", "new", LIEPIN_SEARCH_URL): "opened",
            ("opencli", "browser", "seektalent-liepin", "bind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "get", "url"): LIEPIN_SEARCH_URL,
        }
    )

    result = _runner(commands, lease_dir=tmp_path).open_liepin_tab(LIEPIN_SEARCH_URL)

    assert result.ok is True
    assert result.counts == {"opened": 1, "unleased": 1}
    assert not (tmp_path / "seektalent-liepin.json").exists()


def test_open_liepin_tab_keeps_failing_when_bound_page_is_not_liepin(tmp_path: Path) -> None:
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): [
                "[]",
                "[]",
                "[]",
            ],
            ("opencli", "browser", "seektalent-liepin", "tab", "new", LIEPIN_SEARCH_URL): "opened",
            ("opencli", "browser", "seektalent-liepin", "bind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "get", "url"): "https://example.com/",
        }
    )

    with pytest.raises(OpenCliBrowserError) as error:
        _runner(commands, lease_dir=tmp_path).open_liepin_tab(LIEPIN_SEARCH_URL)

    assert error.value.safe_reason_code == "liepin_opencli_tab_response_malformed"


def test_open_liepin_tab_quarantines_malformed_owned_marker_and_writes_fresh_marker(tmp_path: Path) -> None:
    commands = FakeCommands(
        outputs={
            **_current_window_open_outputs(page_id="page-2"),
        }
    )
    marker_path = tmp_path / "seektalent-liepin-owned-pages.json"
    marker_path.write_text("{not-json", encoding="utf-8")

    result = _runner(commands, lease_dir=tmp_path).open_liepin_tab(LIEPIN_SEARCH_URL)

    assert result.ok is True
    owned_pages = json.loads(marker_path.read_text(encoding="utf-8"))
    assert set(owned_pages) == {"page-2"}
    assert owned_pages["page-2"]["url"] == LIEPIN_SEARCH_URL
    assert list(tmp_path.glob("seektalent-liepin-owned-pages.json.malformed-*"))


def test_open_liepin_tab_quarantines_malformed_lease_and_opens_new_tab(tmp_path: Path) -> None:
    commands = FakeCommands(
        outputs={
            **_current_window_open_outputs(page_id="page-2"),
        }
    )
    lease_path = tmp_path / "seektalent-liepin.json"
    lease_path.write_text("{not-json", encoding="utf-8")

    result = _runner(commands, lease_dir=tmp_path).open_liepin_tab(LIEPIN_SEARCH_URL)

    assert result.ok is True
    lease = json.loads(lease_path.read_text(encoding="utf-8"))
    assert lease["page_id"] == "page-2"
    assert list(tmp_path.glob("seektalent-liepin.json.malformed-*"))


def test_fill_rejects_long_or_sensitive_text() -> None:
    commands = FakeCommands()
    with pytest.raises(OpenCliBrowserError) as error:
        _runner(commands).fill(target="16", text="x" * 81)

    assert error.value.safe_reason_code == "liepin_opencli_forbidden_text"
    assert commands.calls == []


def test_fill_allows_short_keyword_text() -> None:
    commands = FakeCommands(
        outputs={
            (
                "opencli",
                "browser",
                "seektalent-liepin",
                "fill",
                "16",
                "数据开发专家",
            ): '{"filled":true}'
        }
    )

    result = _runner(commands).fill(target="16", text="数据开发专家")

    assert result.ok is True
    assert commands.calls == [("opencli", "browser", "seektalent-liepin", "fill", "16", "数据开发专家")]


@pytest.mark.parametrize(
    "target",
    [
        "查看完整简历",
        "简历详情",
        "联系候选人",
        "聊天",
        "下载简历",
        "payment button",
        "resume detail",
    ],
)
def test_click_rejects_detail_or_contact_targets_before_opencli_call(target: str) -> None:
    commands = FakeCommands()
    with pytest.raises(OpenCliBrowserError) as error:
        _runner(commands).click(target=target)

    assert error.value.safe_reason_code == "liepin_opencli_forbidden_command"
    assert commands.calls == []


@pytest.mark.parametrize("target", ["16", "ref=16", "[ref=16]"])
def test_click_rejects_opaque_targets_before_opencli_call(target: str) -> None:
    commands = FakeCommands()
    with pytest.raises(OpenCliBrowserError) as error:
        _runner(commands).click(target=target)

    assert error.value.safe_reason_code == "liepin_opencli_forbidden_command"
    assert commands.calls == []


def test_click_allows_explicit_search_target() -> None:
    commands = FakeCommands(
        outputs={
            (
                "opencli",
                "browser",
                "seektalent-liepin",
                "click",
                "--role",
                "button",
                "--name",
                "搜 索",
            ): '{"clicked":true}'
        }
    )

    result = _runner(commands).click(target="搜索")

    assert result.ok is True
    assert commands.calls == [
        ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "搜 索")
    ]


def test_click_allows_state_derived_ref_target() -> None:
    commands = FakeCommands(
        outputs={
            (
                "opencli",
                "browser",
                "seektalent-liepin",
                "click",
                "--role",
                "button",
                "--name",
                "搜 索",
            ): '{"clicked":true}'
        }
    )

    result = _runner(commands, allowed_click_refs=("16",)).click(target="16")

    assert result.ok is True
    assert commands.calls == [
        ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "搜 索")
    ]


def test_click_allows_state_derived_ref_marker() -> None:
    commands = FakeCommands(
        outputs={
            (
                "opencli",
                "browser",
                "seektalent-liepin",
                "click",
                "--role",
                "button",
                "--name",
                "搜 索",
            ): '{"clicked":true}'
        }
    )

    result = _runner(commands, allowed_click_refs=("16",)).click(target="ref=16")

    assert result.ok is True
    assert commands.calls == [
        ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "搜 索")
    ]


def test_fill_rejects_contact_or_detail_targets_before_opencli_call() -> None:
    commands = FakeCommands()
    with pytest.raises(OpenCliBrowserError) as error:
        _runner(commands).fill(target="联系输入框", text="数据开发专家")

    assert error.value.safe_reason_code == "liepin_opencli_forbidden_command"
    assert commands.calls == []


def test_forbidden_opencli_command_is_rejected() -> None:
    commands = FakeCommands()
    with pytest.raises(OpenCliBrowserError) as error:
        _runner(commands)._run_browser_command("eval", ("document.cookie",))

    assert error.value.safe_reason_code == "liepin_opencli_forbidden_command"
    assert commands.calls == []


def test_internal_detail_url_probe_rejects_unknown_probe_name(tmp_path: Path) -> None:
    commands = FakeCommands()
    runner = _runner(commands, lease_dir=tmp_path)

    with pytest.raises(OpenCliBrowserError) as error:
        runner._run_fixed_readonly_eval_probe(probe_name="arbitrary", ref="70")

    assert error.value.safe_reason_code == "liepin_opencli_forbidden_command"
    assert commands.calls == []


def test_run_maps_opencli_structured_stale_ref_error(tmp_path: Path) -> None:
    error = subprocess.CalledProcessError(
        1,
        ["opencli"],
        output='{"error":{"code":"stale_ref","message":"target disappeared","hint":"refresh state"}}',
        stderr="",
    )
    commands = FakeCommands(outputs={("opencli", "browser", "seektalent-liepin", "click", "44"): error})
    runner = _runner(commands, lease_dir=tmp_path, allowed_click_refs=("44",))

    with pytest.raises(OpenCliBrowserError) as raised:
        runner._click_native_filter_ref("44")

    assert raised.value.safe_reason_code == "liepin_opencli_stale_ref"


def test_run_maps_opencli_structured_selector_error(tmp_path: Path) -> None:
    error = subprocess.CalledProcessError(
        1,
        ["opencli"],
        output="",
        stderr='{"error":{"code":"selector_not_found","message":"not found"}}',
    )
    commands = FakeCommands(
        outputs={("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "城市"): error}
    )
    runner = _runner(commands, lease_dir=tmp_path)

    with pytest.raises(OpenCliBrowserError) as raised:
        runner._click_native_filter_menu("city")

    assert raised.value.safe_reason_code == "liepin_opencli_selector_not_found"


def test_restricted_command_shape_rejects_forbidden_click_target() -> None:
    commands = FakeCommands()
    with pytest.raises(OpenCliBrowserError) as error:
        _runner(commands)._run_browser_command("click", ("联系候选人",))

    assert error.value.safe_reason_code == "liepin_opencli_forbidden_command"
    assert commands.calls == []


def test_public_payload_does_not_include_raw_output() -> None:
    commands = FakeCommands(
        outputs={
            (
                "opencli",
                "browser",
                "seektalent-liepin",
                "get",
                "url",
            ): "https://h.liepin.com/search/getConditionItem#session",
            ("opencli", "browser", "seektalent-liepin", "state"): "搜索职位、公司 [ref=16]",
        }
    )

    result = _runner(commands).state()

    payload = result.to_public_payload()
    assert payload == {"ok": True, "action": "state", "safeReasonCode": "configured", "counts": {}}
    assert "搜索职位" not in json.dumps(payload, ensure_ascii=False)


def test_state_classifier_blocks_login_and_risk_pages_before_next_action() -> None:
    assert classify_liepin_state(url="https://h.liepin.com/search/getConditionItem#session", text="请登录后继续") == (
        "liepin_opencli_login_required"
    )
    assert classify_liepin_state(
        url="https://h.liepin.com/search/getConditionItem#session", text="安全验证 请完成验证码"
    ) == ("liepin_opencli_risk_page")
    assert classify_liepin_state(url="https://safe.liepin.com/v/intercept/verifysms", text="") == (
        "liepin_opencli_risk_page"
    )
    assert classify_liepin_state(url="https://lpt.liepin.com/", text="请选择招聘身份") == (
        "liepin_opencli_identity_intercept"
    )
    assert classify_liepin_state(url="https://www.liepin.com/resume/detail/123", text="候选人详情") == (
        "liepin_opencli_unknown_modal"
    )


def test_state_classifier_does_not_block_recruiter_search_page_copy() -> None:
    assert (
        classify_liepin_state(
            url="https://h.liepin.com/search/getConditionItem#session",
            text="找简历\n你好，夏诚\n安全退出\n使用本机 Chrome 登录态",
        )
        is None
    )


def test_state_classifier_ignores_hidden_add_resume_drawer_on_search_page() -> None:
    state_text = (
        "URL: https://h.liepin.com/search/getConditionItem#session\n"
        "<span>包含全部关键词</span>\n"
        "[27]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[30]<button><span>搜 索</span></button>\n"
        "<div id=addResume />\n"
        "<div>新增人才</div>\n"
        "<form name=form />\n"
        "<input name=chineseName autocomplete=off placeholder=姓名 />"
    )

    assert classify_liepin_state(url=LIEPIN_SEARCH_URL, text=state_text) is None


def test_classifier_allows_recruiter_resume_search_surface_with_result_dom() -> None:
    state_text = (
        "URL: https://h.liepin.com/resume/search\n"
        "<span>包含全部关键词</span>\n"
        "[27]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "<div id=resultList><div class=detail-resume-card-wrap>查看完整简历</div></div>"
    )

    assert classify_liepin_state(url="https://h.liepin.com/resume/search", text=state_text) is None


def test_classifier_allows_recruiter_search_surface_initial_state_without_result_dom() -> None:
    assert classify_liepin_state(url="https://h.liepin.com/search/getConditionItem#session", text="找简历") is None
    assert classify_liepin_state(url="https://h.liepin.com/resume/search", text="找简历") is None


def test_state_classifier_allows_owned_liepin_resume_detail_page() -> None:
    state_text = (
        "URL: https://h.liepin.com/resume/showresumedetail/?res_id_encode=abc&type=normal\n"
        "<span>候选人详情</span>\n"
        "<button><span>立即沟通</span></button>\n"
        "<button><span>查看联系方式</span></button>\n"
        "<button><span>下载简历</span></button>\n"
        "<div id=addResume />\n"
        "<div>新增人才</div>"
    )

    assert (
        classify_liepin_state(
            url="https://h.liepin.com/resume/showresumedetail/?res_id_encode=abc&type=normal",
            text=state_text,
        )
        is None
    )


def test_state_reads_dom_before_classifying_resume_search_url() -> None:
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "get", "url"): "https://h.liepin.com/resume/search",
            ("opencli", "browser", "seektalent-liepin", "state"): (
                "URL: https://h.liepin.com/resume/search\n"
                "<span>包含全部关键词</span>\n"
                "[27]<input type=search autocomplete=off role=combobox id=rc_select_1 />"
            ),
        }
    )

    result = _runner(commands).state()

    assert result.ok is True
    assert commands.calls == [
        ("opencli", "browser", "seektalent-liepin", "get", "url"),
        ("opencli", "browser", "seektalent-liepin", "state"),
    ]


def test_state_classifies_against_url_reported_by_state_output() -> None:
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "get", "url"): (
                "https://h.liepin.com/search/getConditionItem#session"
            ),
            ("opencli", "browser", "seektalent-liepin", "state"): (
                "URL: https://www.liepin.com/resume/detail/123\n候选人详情"
            ),
        }
    )

    result = _runner(commands).state()

    assert result.ok is False
    assert result.safe_reason_code == "liepin_opencli_unknown_modal"
    assert commands.calls == [
        ("opencli", "browser", "seektalent-liepin", "get", "url"),
        ("opencli", "browser", "seektalent-liepin", "state"),
    ]


def test_state_blocks_forbidden_url_after_reading_page_text() -> None:
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "get", "url"): ("https://www.liepin.com/resume/detail/123"),
            ("opencli", "browser", "seektalent-liepin", "state"): "raw detail resume text",
        }
    )

    result = _runner(commands).state()

    assert result.ok is False
    assert result.safe_reason_code == "liepin_opencli_unknown_modal"
    assert result.to_tool_payload()["observation"] == {
        "text": "raw detail resume text",
        "chars": len("raw detail resume text"),
        "truncated": False,
        "terminal": True,
    }
    assert commands.calls == [
        ("opencli", "browser", "seektalent-liepin", "get", "url"),
        ("opencli", "browser", "seektalent-liepin", "state"),
    ]


def test_state_reads_owned_liepin_resume_detail_page_without_click_allowing_actions() -> None:
    detail_url = "https://h.liepin.com/resume/showresumedetail/?res_id_encode=abc&type=normal"
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "get", "url"): detail_url,
            ("opencli", "browser", "seektalent-liepin", "state"): (
                "候选人详情\n"
                "[15]<button><span>立即沟通</span></button>\n"
                "[20]<button><span>查看联系方式</span></button>\n"
                "[30]<button><span>下载简历</span></button>"
            ),
        }
    )

    result = _runner(commands).state()

    assert result.ok is True
    payload = result.to_tool_payload()
    assert payload["observation"]["terminal"] is False
    assert "allowedClickRefs" not in payload["observation"]


def test_state_reports_safe_liepin_intercept_as_risk_page_before_reading_text() -> None:
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "get", "url"): (
                "https://safe.liepin.com/v/intercept/verifysms?backurl=https://api-h.liepin.com"
            ),
            ("opencli", "browser", "seektalent-liepin", "state"): "安全中心-风险提示",
        }
    )

    result = _runner(commands).state()

    assert result.ok is False
    assert result.safe_reason_code == "liepin_opencli_risk_page"
    assert commands.calls == [("opencli", "browser", "seektalent-liepin", "get", "url")]


def test_state_returns_terminal_classification_to_pi_payload_only() -> None:
    commands = FakeCommands(
        outputs={
            (
                "opencli",
                "browser",
                "seektalent-liepin",
                "get",
                "url",
            ): "https://h.liepin.com/search/getConditionItem#session",
            ("opencli", "browser", "seektalent-liepin", "state"): "请登录后继续 [ref=login]",
        }
    )

    result = _runner(commands).state()

    assert result.ok is False
    assert result.safe_reason_code == "liepin_opencli_login_required"
    pi_payload = result.to_tool_payload()
    assert pi_payload["observation"]["terminal"] is True
    public_payload = result.to_public_payload()
    assert "请登录" not in json.dumps(public_payload, ensure_ascii=False)


def test_state_returns_bounded_observation_to_pi_only() -> None:
    commands = FakeCommands(
        outputs={
            (
                "opencli",
                "browser",
                "seektalent-liepin",
                "get",
                "url",
            ): "https://h.liepin.com/search/getConditionItem#session",
            ("opencli", "browser", "seektalent-liepin", "state"): "搜索职位、公司 [ref=16]",
        }
    )

    result = _runner(commands).state()

    pi_payload = result.to_tool_payload()
    public_payload = result.to_public_payload()
    assert pi_payload["observation"]["text"] == "搜索职位、公司 [ref=16]"
    assert pi_payload["observation"]["terminal"] is False
    assert "搜索职位" not in json.dumps(public_payload, ensure_ascii=False)


def test_state_exposes_only_safe_click_refs_to_pi() -> None:
    commands = FakeCommands(
        outputs={
            (
                "opencli",
                "browser",
                "seektalent-liepin",
                "get",
                "url",
            ): "https://h.liepin.com/search/getConditionItem#session",
            ("opencli", "browser", "seektalent-liepin", "state"): (
                "button 搜索 [ref=16]\n"
                "button 查看完整简历 [ref=99]\n"
                "button 下一页 [ref=next]\n"
                "[29]<button />\n"
                "  <span>搜 索</span>\n"
                "[30]<input type=search />\n"
                "text 14年经验 [ref=profile]"
            ),
        }
    )

    result = _runner(commands).state()

    assert result.ok is True
    assert result.to_tool_payload()["observation"]["allowedClickRefs"] == ("16", "next", "29")
    assert "allowedClickRefs" not in result.to_public_payload()


def test_build_observation_exposes_structured_liepin_detail_targets_to_pi() -> None:
    text = (
        "候选人 张某\n"
        "数据开发专家\n"
        "10年经验\n"
        "上海\n"
        "数据治理 Python 离线数仓\n"
        "button 查看完整简历 [ref=99]\n"
        "button 下一页 [ref=next]"
    )

    observation = build_observation(text)

    assert observation["detailTargets"] == (
        {
            "rank": 1,
            "ref": "99",
            "summary": "候选人 张某\n数据开发专家\n10年经验\n上海\n数据治理 Python 离线数仓\n查看完整简历",
            "score": 0,
        },
    )


def test_open_liepin_detail_without_claim_reports_timeout(tmp_path: Path) -> None:
    commands = FakeCommands(
        outputs={
            (
                "opencli",
                "browser",
                "seektalent-liepin",
                "get",
                "url",
            ): "https://h.liepin.com/search/getConditionItem#session",
            ("opencli", "browser", "seektalent-liepin", "state"): (
                "王** 40岁 工作14年 硕士 上海\n"
                "数据仓库 数据治理 Python Hive\n"
                "[70]<button><span>查看完整简历</span></button>"
            ),
            ("opencli", "browser", "seektalent-liepin", "click", "70"): subprocess.TimeoutExpired(
                cmd=["opencli", "browser", "seektalent-liepin", "click", "70"],
                timeout=8,
            ),
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "5"): "{}",
        }
    )

    result = _runner(commands, lease_dir=tmp_path).open_liepin_detail(source_run_id="run-1", ref="70", rank=1)

    assert result.ok is False
    assert result.safe_reason_code == "liepin_opencli_timeout"
    events = json.loads((tmp_path / "protected" / "pi-trace" / "run-1" / "agent-events.json").read_text())
    assert {"action_kind": "open_detail", "route_kind": "detail", "ref": "70", "rank": 1} in events["events"]
    assert {
        "action_kind": "open_detail_timeout",
        "route_kind": "detail",
        "ref": "70",
        "rank": 1,
        "safe_reason_code": "liepin_opencli_timeout",
    } in events["events"]


def test_open_liepin_detail_waits_for_delayed_detail_tab_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("seektalent.opencli_browser.automation.time.sleep", lambda _: None)
    monkeypatch.setattr("seektalent.providers.liepin.liepin_site_adapter.time.sleep", lambda _: None)
    detail_url = "https://h.liepin.com/resume/showresumedetail/?res_id_encode=abc"
    commands = FakeCommands(
        outputs={
            ("opencli", "browser", "seektalent-liepin", "get", "url"): [
                "https://h.liepin.com/search/getConditionItem#session",
            ],
            ("opencli", "browser", "seektalent-liepin", "state"): (
                "王** 40岁 工作14年 硕士 上海\n"
                "数据仓库 数据治理 Python Hive\n"
                "[70]<button><span>查看完整简历</span></button>"
            ),
            ("opencli", "browser", "seektalent-liepin", "click", "70"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): [
                '[{"page":"SEARCHPAGE1","url":"https://h.liepin.com/search/getConditionItem#session","active":true}]',
                '[{"page":"SEARCHPAGE1","url":"https://h.liepin.com/search/getConditionItem#session","active":true}]',
                '[{"page":"SEARCHPAGE1","url":"https://h.liepin.com/search/getConditionItem#session","active":true}]',
                (
                    '[{"page":"SEARCHPAGE1","url":"https://h.liepin.com/search/getConditionItem#session","active":false},'
                    f'{{"page":"DETAILPAGE1","url":"{detail_url}","active":true}}]'
                ),
            ],
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "DETAILPAGE1"): "{}",
        }
    )

    result = _runner(
        commands,
        lease_dir=tmp_path,
        detail_open_timeout_seconds=4,
    ).open_liepin_detail(source_run_id="run-1", ref="70", rank=1)

    assert result.ok is True
    assert commands.calls.count(("opencli", "browser", "seektalent-liepin", "tab", "list")) == 4
    events = json.loads((tmp_path / "protected" / "pi-trace" / "run-1" / "agent-events.json").read_text())
    assert all(event["action_kind"] != "open_detail_timeout" for event in events["events"])


def test_open_liepin_detail_opens_card_detail_url_in_controlled_tab(tmp_path: Path) -> None:
    search_url = "https://h.liepin.com/search/getConditionItem#session"
    detail_url = (
        "https://h.liepin.com/resume/showresumedetail/?res_id_encode=778882227ddfWf393e2b5fdad"
        "&index=5&position=5&cur_page=0&pageSize=30&sfrom=RES_SEARCH&res_source=1&type=normal"
    )
    commands = RefEvalCommands(
        eval_outputs_by_ref={"357": detail_url},
        outputs={
            ("opencli", "browser", "seektalent-liepin", "get", "url"): [search_url, detail_url],
            ("opencli", "browser", "seektalent-liepin", "state"): (
                "摆** 31岁 工作7年 本科 北京\n"
                "数据开发 ETL Python\n"
                "[357]<div class=detail-resume-card-wrap>查看完整简历</div>"
            ),
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): _single_tab_list(
                page_id="page-detail-357",
                url=detail_url,
            ),
            ("opencli", "browser", "seektalent-liepin", "tab", "new", detail_url): (
                json.dumps({"page": "page-detail-357", "url": detail_url})
            ),
        },
    )

    result = _runner(commands, lease_dir=tmp_path).open_liepin_detail(source_run_id="run-1", ref="357", rank=1)

    assert result.ok is True
    assert ("opencli", "browser", "seektalent-liepin", "click", "357") not in commands.calls
    assert any(call[3] == "eval" for call in commands.calls)
    lease = json.loads((tmp_path / "seektalent-liepin.json").read_text(encoding="utf-8"))
    assert lease["page_id"] == "page-detail-357"
    assert lease["url"] == detail_url


def test_open_liepin_detail_waits_for_controlled_tab_navigation_before_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("seektalent.providers.liepin.liepin_site_adapter.time.sleep", lambda _: None)
    search_url = "https://h.liepin.com/search/getConditionItem#session"
    detail_url = (
        "https://h.liepin.com/resume/showresumedetail/?res_id_encode=778882227ddfWf393e2b5fdad"
        "&index=5&position=5&cur_page=0&pageSize=30&sfrom=RES_SEARCH&res_source=1&type=normal"
    )
    commands = RefEvalCommands(
        eval_outputs_by_ref={"357": detail_url},
        outputs={
            ("opencli", "browser", "seektalent-liepin", "get", "url"): [
                search_url,
                "about:blank",
                detail_url,
            ],
            ("opencli", "browser", "seektalent-liepin", "state"): (
                "摆** 31岁 工作7年 本科 北京\n"
                "数据开发 ETL Python\n"
                "[357]<div class=detail-resume-card-wrap>查看完整简历</div>"
            ),
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): _single_tab_list(
                page_id="page-search",
                url=search_url,
            ),
            ("opencli", "browser", "seektalent-liepin", "tab", "new", detail_url): (
                json.dumps({"page": "page-detail-357", "url": detail_url})
            ),
        },
    )

    result = _runner(commands, lease_dir=tmp_path).open_liepin_detail(source_run_id="run-1", ref="357", rank=1)

    assert result.ok is True
    assert commands.calls.count(("opencli", "browser", "seektalent-liepin", "get", "url")) == 3


def test_open_liepin_detail_reuses_already_opened_ref_without_duplicate_click(tmp_path: Path) -> None:
    commands = FakeCommands()
    runner = _runner(commands, lease_dir=tmp_path)
    runner._append_agent_event(
        "run-1",
        {"action_kind": "open_detail_succeeded", "route_kind": "detail", "ref": "70", "rank": 1},
    )

    result = runner.open_liepin_detail(source_run_id="run-1", ref="70", rank=1)

    assert result.ok is True
    assert result.counts == {"rank": 1, "reused": 1}
    assert commands.calls == []


def test_append_agent_event_preserves_agent_events_dict_schema(tmp_path: Path) -> None:
    runner = _runner(FakeCommands(), lease_dir=tmp_path)
    path = tmp_path / "protected" / "pi-trace" / "run-1" / "agent-events.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": "seektalent.opencli_agent_events.v1",
                "events": [{"action_kind": "open_search", "route_kind": "search"}],
            }
        ),
        encoding="utf-8",
    )

    runner._append_agent_event("run-1", {"action_kind": "open_detail", "route_kind": "detail", "rank": 1})

    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded == {
        "schema_version": "seektalent.opencli_agent_events.v1",
        "events": [
            {"action_kind": "open_search", "route_kind": "search"},
            {"action_kind": "open_detail", "route_kind": "detail", "rank": 1},
        ],
    }


def test_failed_detail_open_does_not_mark_ref_reusable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("seektalent.opencli_browser.automation.time.sleep", lambda _: None)
    monkeypatch.setattr("seektalent.providers.liepin.liepin_site_adapter.time.sleep", lambda _: None)
    commands = EvalCommands(
        eval_output="null",
        outputs={
            ("opencli", "browser", "seektalent-liepin", "get", "url"): [
                "https://h.liepin.com/search/getConditionItem#session",
                "https://h.liepin.com/search/getConditionItem#session",
            ],
            ("opencli", "browser", "seektalent-liepin", "state"): (
                "王** 40岁 工作14年 硕士 上海\n[70]<button><span>查看完整简历</span></button>"
            ),
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): "[]",
            ("opencli", "browser", "seektalent-liepin", "click", "70"): subprocess.CalledProcessError(
                1,
                ["opencli"],
            ),
        },
    )
    runner = _runner(commands, lease_dir=tmp_path)

    first = runner.open_liepin_detail(source_run_id="run-1", ref="70", rank=1)
    second = runner.open_liepin_detail(source_run_id="run-1", ref="70", rank=1)

    assert first.ok is False
    assert second.counts.get("reused") != 1
    assert commands.calls.count(("opencli", "browser", "seektalent-liepin", "click", "70")) == 2


def test_captured_detail_resume_reuse_is_allowed_without_duplicate_open(tmp_path: Path) -> None:
    runner = _runner(FakeCommands(outputs={}), lease_dir=tmp_path)
    safe_run_id = "run-1"
    runner._write_collected_resumes(
        safe_run_id,
        [
            {
                "provider_rank": 1,
                "candidate_resume_id": "liepin-opencli-detail-run-1-1",
                "protected_snapshot_ref": "artifact://protected/pi-detail/run-1/1.json",
                "normalized_text": "Python RAG",
            }
        ],
    )

    result = runner.open_liepin_detail(source_run_id="run-1", ref="70", rank=1)

    assert result.ok is True
    assert result.counts["reused"] == 1


def test_open_liepin_detail_claims_new_detail_tab_without_binding_current_window(tmp_path: Path) -> None:
    detail_url = "https://h.liepin.com/resume/showresumedetail/?res_id_encode=abc"
    commands = RefEvalCommands(
        eval_outputs_by_ref={},
        default_eval_output=_liepin_detail_payload_json(summary_text=detail70_state),
        outputs={
            ("opencli", "browser", "seektalent-liepin", "get", "url"): [
                "https://h.liepin.com/search/getConditionItem#session",
            ],
            ("opencli", "browser", "seektalent-liepin", "state"): (
                "王** 40岁 工作14年 硕士 上海\n"
                "数据仓库 数据治理 Python Hive\n"
                "[70]<button><span>查看完整简历</span></button>"
            ),
            ("opencli", "browser", "seektalent-liepin", "click", "70"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): [
                '[{"page":"SEARCHPAGE1","url":"https://h.liepin.com/search/getConditionItem#session","active":true}]',
                (
                    '[{"page":"SEARCHPAGE1","url":"https://h.liepin.com/search/getConditionItem#session","active":false},'
                    f'{{"page":"DETAILPAGE1","url":"{detail_url}","active":true}}]'
                ),
            ],
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "DETAILPAGE1"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "5"): "{}",
        },
    )

    result = _runner(commands, lease_dir=tmp_path).open_liepin_detail(source_run_id="run-1", ref="70", rank=1)

    assert result.ok is True
    lease = json.loads((tmp_path / "seektalent-liepin.json").read_text(encoding="utf-8"))
    assert lease["page_id"] == "DETAILPAGE1"
    assert lease["url"] == detail_url
    owned_pages = json.loads((tmp_path / "seektalent-liepin-owned-pages.json").read_text(encoding="utf-8"))
    assert owned_pages["DETAILPAGE1"]["url"] == detail_url
    assert all(call[3] not in {"bind", "unbind"} for call in commands.calls)


def test_state_exposes_liepin_result_card_refs_as_detail_targets(tmp_path: Path) -> None:
    state_text = "id=resultList\n立即沟通\n共 30 位人选"
    cards_payload = json.dumps(
        {
            "entries": [
                {
                    "ref": "448",
                    "visible": True,
                    "text": "张某 32岁 工作10年 本科 上海 求职期望 数据开发专家 数据治理 Python 离线数仓",
                },
                {"ref": "449", "visible": True, "text": "立即沟通"},
            ],
            "matches_n": 2,
        },
        ensure_ascii=False,
    )
    commands = RefEvalCommands(
        eval_outputs_by_ref={},
        default_eval_output=_liepin_detail_payload_json(summary_text=detail_state),
        outputs={
            (
                "opencli",
                "browser",
                "seektalent-liepin",
                "get",
                "url",
            ): "https://h.liepin.com/search/getConditionItem#session",
            ("opencli", "browser", "seektalent-liepin", "state"): state_text,
            (
                "opencli",
                "browser",
                "seektalent-liepin",
                "find",
                "--css",
                "#resultList .detail-resume-card-wrap",
                "--limit",
                "20",
                "--text-max",
                "1200",
            ): cards_payload,
        },
    )

    result = _runner(commands, lease_dir=tmp_path).state()

    assert result.ok is True
    assert result.to_tool_payload()["observation"]["detailTargets"] == (
        {
            "rank": 1,
            "ref": "448",
            "summary": "张某 32岁 工作10年 本科 上海 求职期望 数据开发专家 数据治理 Python 离线数仓",
            "score": 0,
        },
    )


def test_extract_allowed_click_refs_supports_opencli_ref_forms() -> None:
    text = "button 搜索 [ref=16]\nbutton 下一页 ref=next\nbutton 查询 [query-ref]"

    assert extract_allowed_click_refs(text) == ("16", "next", "query-ref")


def test_extract_liepin_search_input_ref_uses_keyword_combobox_near_label() -> None:
    text = (
        "<span>包含全部关键词</span>\n"
        "  [25]<div />\n"
        "    [26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "<span>职位名称：</span>\n"
        "  [139]<input autocomplete=off placeholder=岁 id=ageLow type=text />"
    )

    assert extract_liepin_search_input_ref(text) == "26"


def test_extract_liepin_search_button_ref_uses_visible_search_button() -> None:
    text = (
        "<span>包含全部关键词</span>\n"
        "  [26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>\n"
        "[70]<button><span>查看完整简历</span></button>"
    )

    assert extract_liepin_search_button_ref(text) == "29"


def test_extract_liepin_search_input_ref_falls_back_to_keyword_input_id() -> None:
    text = (
        "[316]<input type=search autocomplete=off role=combobox id=rc_select_0 />\n"
        "[26]<input type=search autocomplete=off role=combobox value=数据开发 id=rc_select_1 />\n"
        "[30]<input type=search autocomplete=off role=combobox id=rc_select_2 />\n"
    )

    assert extract_liepin_search_input_ref(text) == "26"


def test_bucket_text_is_count_only() -> None:
    assert bucket_text("数据开发专家") == {"chars": 6}


def test_search_liepin_cards_runs_bounded_opencli_flow_and_writes_valid_artifacts(tmp_path: Path) -> None:
    state_before = (
        "<span>包含全部关键词</span>\n"
        "  [25]<div />\n"
        "    [26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>"
    )
    state_after = (
        "王** 男 40岁 工作14年 硕士 上海\n"
        "求职期望：上海 数据开发专家\n"
        "海光集成电路 · 高级主管工程师 2023.10-至今\n"
        "FTI SDP CXL Pcie verilog"
    )
    structured_cards = json.dumps(
        {
            "ok": True,
            "schema_version": "seektalent.liepin_structured_cards_probe.v1",
            "cards": [
                {
                    "provider_rank": 1,
                    "ref": "70",
                    "masked_name": True,
                    "gender": "男",
                    "age": 40,
                    "work_years": 14,
                    "city": "上海",
                    "expected_city": "上海",
                    "education_level": "硕士",
                    "current_or_recent_company": "海光集成电路",
                    "current_or_recent_title": "高级主管工程师",
                    "job_intention": "数据开发专家",
                    "skill_tags": ["FTI", "SDP", "CXL", "Pcie", "verilog"],
                    "experience_preview": [
                        {
                            "company": "海光集成电路",
                            "title": "高级主管工程师",
                            "date_range": "2023.10-至今",
                            "duration": "8个月",
                            "is_current": True,
                        }
                    ],
                    "education_preview": [
                        {
                            "school": "北京大学",
                            "major": "计算机",
                            "degree": "本科",
                            "recruitment_type": "统招",
                            "date_range": "2002.09-2006.07",
                        }
                    ],
                }
            ],
        },
        ensure_ascii=False,
    )
    commands = RefEvalCommands(
        eval_outputs_by_ref={ANY_STRUCTURED_CARD_PROBE: structured_cards},
        default_eval_output=_liepin_detail_payload_json(summary_text=detail70_state),
        outputs={
            **_current_window_open_outputs(page_id="page-1"),
            ("opencli", "browser", "seektalent-liepin", "state"): [
                state_before,
                state_before,
                state_after,
                state_after,
            ],
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发专家"): '{"filled":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "29"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "3"): "{}",
        },
    )

    envelope = _runner(commands, lease_dir=tmp_path).search_liepin_cards(
        source_run_id="run-1",
        query="数据开发专家",
        max_pages=1,
        max_cards=10,
    )

    assert envelope["schema_version"] == "seektalent.pi_liepin_cards.v1"
    assert envelope["status"] == "succeeded"
    assert envelope["cards_returned"] == 1
    assert envelope["cards"][0]["safe_card_summary"]["current_or_recent_company"] == "海光集成电路"
    assert envelope["cards"][0]["safe_card_summary"]["current_or_recent_title"] == "高级主管工程师"
    assert envelope["cards"][0]["safe_card_summary"]["work_years"] == 14
    assert envelope["cards"][0]["safe_card_summary"]["experience_preview"][0]["company"] == "海光集成电路"
    assert "provider_rank" not in envelope["cards"][0]["safe_card_summary"]
    assert "ref" not in envelope["cards"][0]["safe_card_summary"]
    _assert_no_card_text_keys(envelope)
    serialized_envelope = json.dumps(envelope, ensure_ascii=False)
    for forbidden in FORBIDDEN_CARD_TEXT_KEYS:
        assert forbidden not in serialized_envelope
    assert envelope["cards"][0]["safe_card_summary_ref"].startswith("artifact://public-summary/pi-card/run-1/")
    assert (tmp_path / "protected" / "pi-trace" / "run-1" / "action-trace.json").is_file()
    public_summary_path = tmp_path / "public-summary" / "pi-card" / "run-1" / "1.json"
    assert public_summary_path.is_file()
    public_summary = json.loads(public_summary_path.read_text(encoding="utf-8"))
    assert public_summary["current_or_recent_company"] == "海光集成电路"
    assert public_summary["skill_tags"] == ["FTI", "SDP", "CXL", "Pcie", "verilog"]
    assert public_summary["experience_preview"][0]["title"] == "高级主管工程师"
    assert public_summary["education_preview"][0]["school"] == "北京大学"
    assert "provider_rank" not in public_summary
    assert "ref" not in public_summary
    _assert_no_card_text_keys(public_summary)
    assert ("opencli", "browser", "seektalent-liepin", "tab", "list") in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "tab", "new", LIEPIN_SEARCH_URL) in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "get", "url") in commands.calls
    lease = json.loads((tmp_path / "seektalent-liepin.json").read_text(encoding="utf-8"))
    assert lease["page_id"] == "page-1"
    assert (
        "opencli",
        "browser",
        "seektalent-liepin",
        "fill",
        "26",
        "数据开发专家",
    ) in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "click", "29") in commands.calls
    fill_index = commands.calls.index(
        (
            "opencli",
            "browser",
            "seektalent-liepin",
            "fill",
            "26",
            "数据开发专家",
        )
    )
    click_index = commands.calls.index(("opencli", "browser", "seektalent-liepin", "click", "29"))
    assert fill_index < click_index
    assert ("opencli", "browser", "seektalent-liepin", "state") not in commands.calls[fill_index + 1 : click_index]


def test_search_liepin_cards_ignores_add_resume_copy_without_closing_it(tmp_path: Path) -> None:
    state_before = (
        "<span>包含全部关键词</span>\n"
        "[26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>\n"
        "<div id=addResume />\n"
        "<div>新增人才</div>\n"
        "[88]<button><span>关闭</span></button>"
    )
    state_after = (
        "王** 男 40岁 工作14年 硕士 上海\n求职期望：上海 数据开发专家\n海光集成电路 · 高级主管工程师 2023.10-至今"
    )
    commands = FakeCommands(
        outputs={
            **_current_window_open_outputs(page_id="page-1"),
            ("opencli", "browser", "seektalent-liepin", "state"): [
                state_before,
                state_before,
                state_after,
                state_after,
            ],
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发专家"): '{"filled":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "29"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "3"): "{}",
        }
    )

    envelope = _runner(commands, lease_dir=tmp_path).search_liepin_cards(
        source_run_id="run-1",
        query="数据开发专家",
        max_pages=1,
        max_cards=10,
    )

    assert envelope["status"] == "succeeded"
    assert ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发专家") in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "click", "29") in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "click", "88") not in commands.calls


def test_search_liepin_cards_probes_around_search_and_filter_mutations(tmp_path: Path) -> None:
    state_before = (
        "<span>包含全部关键词</span>\n"
        "[26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>"
    )
    state_after_search = "[41]<button><span>城市</span></button>\n王** 男 34岁 工作5年 硕士 上海"
    state_city_menu = "[41]<button><span>城市</span></button>\n[44]<label>上海</label>"
    state_after_city = "已选 上海\n王** 男 34岁 工作5年 硕士 上海"
    commands = FakeCommands(
        outputs={
            **_current_window_open_outputs(page_id="page-1"),
            ("opencli", "browser", "seektalent-liepin", "state"): [
                state_before,
                state_before,
                state_after_search,
                state_city_menu,
                state_after_city,
            ],
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发专家"): '{"filled":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "29"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "城市"): (
                '{"clicked":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "click", "44"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "1"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "3"): "{}",
        }
    )

    envelope = _runner(commands, lease_dir=tmp_path).search_liepin_cards(
        source_run_id="run-1",
        query="数据开发专家",
        max_pages=1,
        max_cards=10,
        native_filters={"city": "上海"},
    )

    assert envelope["status"] == "succeeded"
    tab_new_call = ("opencli", "browser", "seektalent-liepin", "tab", "new", LIEPIN_SEARCH_URL)
    fill_call = ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发专家")
    search_click_call = ("opencli", "browser", "seektalent-liepin", "click", "29")
    option_click_call = ("opencli", "browser", "seektalent-liepin", "click", "44")
    search_click_index = commands.calls.index(search_click_call)
    option_click_index = commands.calls.index(option_click_call)
    filter_menu_call = next(
        call
        for call in commands.calls[search_click_index + 1 : option_click_index]
        if len(call) >= 4 and call[:4] == ("opencli", "browser", "seektalent-liepin", "click")
    )
    mutating_calls = [tab_new_call, fill_call, search_click_call, filter_menu_call, option_click_call]
    for call in mutating_calls:
        assert call in commands.calls
    assert _has_probe_between(commands.calls, 0, commands.calls.index(mutating_calls[0]))
    for previous, current in (
        (tab_new_call, fill_call),
        (search_click_call, filter_menu_call),
        (filter_menu_call, option_click_call),
    ):
        previous_index = commands.calls.index(previous)
        current_index = commands.calls.index(current)
        assert _has_probe_between(commands.calls, previous_index + 1, current_index)
    assert _has_probe_between(commands.calls, commands.calls.index(mutating_calls[-1]) + 1, len(commands.calls))


def test_search_liepin_cards_trace_uses_verified_search_filter_phase_actions(tmp_path: Path) -> None:
    state_before = (
        "<span>包含全部关键词</span>\n"
        "[26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>"
    )
    state_after_search = "[41]<button><span>城市</span></button>\n王** 男 34岁 工作5年 硕士 上海"
    state_city_menu = "[41]<button><span>城市</span></button>\n[44]<label>上海</label>"
    state_after_city = "已选 上海\n王** 男 34岁 工作5年 硕士 上海"
    commands = FakeCommands(
        outputs={
            **_current_window_open_outputs(page_id="page-1"),
            ("opencli", "browser", "seektalent-liepin", "state"): [
                state_before,
                state_before,
                state_after_search,
                state_city_menu,
                state_after_city,
            ],
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发专家"): '{"filled":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "29"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "城市"): (
                '{"clicked":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "click", "44"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "1"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "3"): "{}",
        }
    )

    envelope = _runner(commands, lease_dir=tmp_path).search_liepin_cards(
        source_run_id="run-1",
        query="数据开发专家",
        max_pages=1,
        max_cards=10,
        native_filters={"city": "上海"},
    )

    assert envelope["status"] == "succeeded"
    trace = json.loads((tmp_path / "protected" / "pi-trace" / "run-1" / "action-trace.json").read_text())
    action_kinds = [event["action_kind"] for event in trace["events"] if "action_kind" in event]
    for expected in (
        "open_search",
        "wait_search_ready",
        "fill_search",
        "click_search",
        "observe_results",
        "apply_native_filter",
        "extract_structured_cards",
    ):
        assert expected in action_kinds
    assert all(kind == kind.casefold() and "-" not in kind for kind in action_kinds)
    workflow_steps = workflow_steps_from_action_events(
        trace["events"],
        final_status="succeeded",
        resumes_returned=0,
        action_trace_ref="artifact://protected/pi-trace/run-1/action-trace.json",
    )
    assert any(step["step_name"] == "apply_filters" and step["status"] == "completed" for step in workflow_steps)
    assert any(step["step_name"] == "submit_search" and step["status"] == "completed" for step in workflow_steps)
    assert any(step["step_name"] == "observe_cards" and step["status"] == "completed" for step in workflow_steps)


def test_search_liepin_cards_success_path_uses_state_conditions_not_fixed_time_waits(tmp_path: Path) -> None:
    state_before = (
        "<span>包含全部关键词</span>\n"
        "[26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>"
    )
    state_after_search = "[41]<button><span>城市</span></button>\n王** 男 34岁 工作5年 硕士 上海"
    state_city_menu = "[41]<button><span>城市</span></button>\n[44]<label>上海</label>"
    state_after_city = "已选 上海\nid=resultList\n王** 男 34岁 工作5年 硕士 上海"
    commands = FakeCommands(
        outputs={
            **_current_window_open_outputs(page_id="page-1"),
            ("opencli", "browser", "seektalent-liepin", "state"): [
                state_before,
                state_before,
                state_after_search,
                state_city_menu,
                state_after_city,
            ],
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发专家"): '{"filled":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "29"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "城市"): (
                '{"clicked":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "click", "44"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "wait", "selector", "#resultList"): "{}",
        }
    )

    envelope = _runner(commands, lease_dir=tmp_path).search_liepin_cards(
        source_run_id="run-1",
        query="数据开发专家",
        max_pages=1,
        max_cards=10,
        native_filters={"city": "上海"},
    )

    assert envelope["status"] == "succeeded"
    assert [
        call
        for call in commands.calls
        if len(call) >= 6 and call[:4] == ("opencli", "browser", "seektalent-liepin", "wait") and call[4] == "time"
    ] == []


def test_search_liepin_cards_waits_for_result_evidence_after_stale_search_form(tmp_path: Path) -> None:
    class ResultReadyCommands(FakeCommands):
        def __init__(self, **kwargs) -> None:
            super().__init__(**kwargs)
            self.result_ready = False

        def run(self, argv: Sequence[str], *, timeout: int, env: Mapping[str, str] | None = None) -> str:
            call = tuple(argv)
            if len(call) >= 5 and call[3] == "eval" and "seektalent.liepin_structured_cards_probe.v1" in call[4]:
                assert self.result_ready, "structured cards must not be probed from a stale search form"
            if call == ("opencli", "browser", "seektalent-liepin", "wait", "selector", "#resultList"):
                self.result_ready = True
            return super().run(argv, timeout=timeout, env=env)

    state_before = (
        "<span>包含全部关键词</span>\n"
        "[26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>"
    )
    stale_search_form = (
        "<span>包含全部关键词</span>\n"
        "[26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>\n"
        "URL: https://h.liepin.com/search/getConditionItem#session"
    )
    result_state = (
        "URL: https://h.liepin.com/search/getConditionItem#session\n"
        "id=resultList\n"
        "共 1 位人选\n"
        "王** 男 40岁 工作14年 硕士 上海\n"
        "求职期望：上海 数据开发专家"
    )
    commands = ResultReadyCommands(
        outputs={
            **_current_window_open_outputs(page_id="page-1"),
            ("opencli", "browser", "seektalent-liepin", "state"): [
                state_before,
                state_before,
                stale_search_form,
                result_state,
            ],
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发专家"): '{"filled":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "29"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "wait", "selector", "#resultList"): "{}",
        }
    )

    envelope = _runner(commands, lease_dir=tmp_path).search_liepin_cards(
        source_run_id="run-1",
        query="数据开发专家",
        max_pages=1,
        max_cards=10,
    )

    assert envelope["status"] == "succeeded"
    wait_index = commands.calls.index(("opencli", "browser", "seektalent-liepin", "wait", "selector", "#resultList"))
    eval_index = next(
        index
        for index, call in enumerate(commands.calls)
        if len(call) >= 5 and call[3] == "eval" and "seektalent.liepin_structured_cards_probe.v1" in call[4]
    )
    assert wait_index < eval_index


def test_search_liepin_cards_blocks_stale_results_when_keyword_fill_is_unapplied(tmp_path: Path) -> None:
    class UnappliedKeywordCommands(FakeCommands):
        def run(self, argv: Sequence[str], *, timeout: int, env: Mapping[str, str] | None = None) -> str:
            call = tuple(argv)
            if len(call) >= 5 and call[3] == "eval" and "seektalent.liepin_search_query_value.v1" in call[4]:
                del timeout
                self.calls.append(call)
                self.envs.append(env)
                return json.dumps(
                    {
                        "ok": True,
                        "schema_version": "seektalent.liepin_search_query_value.v1",
                        "value": "",
                    },
                    ensure_ascii=False,
                )
            return super().run(argv, timeout=timeout, env=env)

    state_before = (
        "<span>包含全部关键词</span>\n"
        "[26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>"
    )
    stale_results = (
        "URL: https://h.liepin.com/search/getConditionItem#session\n"
        "id=resultList\n"
        "王** 男 40岁 工作14年 硕士 上海\n"
        "求职期望：上海 旧关键词"
    )
    commands = UnappliedKeywordCommands(
        outputs={
            **_current_window_open_outputs(page_id="page-1"),
            ("opencli", "browser", "seektalent-liepin", "state"): [
                state_before,
                stale_results,
            ],
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发专家"): '{"filled":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "29"): '{"clicked":true}',
        }
    )

    envelope = _runner(commands, lease_dir=tmp_path).search_liepin_cards(
        source_run_id="run-1",
        query="数据开发专家",
        max_pages=1,
        max_cards=10,
    )

    assert envelope["status"] == "blocked"
    assert envelope["safe_reason_code"] == "liepin_opencli_search_input_unapplied"
    assert not any(
        len(call) >= 5 and call[3] == "eval" and "seektalent.liepin_structured_cards_probe.v1" in call[4]
        for call in commands.calls
    )


def test_search_liepin_cards_does_not_treat_filter_only_state_as_result_ready(tmp_path: Path) -> None:
    class NoProbeCommands(FakeCommands):
        def run(self, argv: Sequence[str], *, timeout: int, env: Mapping[str, str] | None = None) -> str:
            call = tuple(argv)
            if len(call) >= 5 and call[3] == "eval" and "seektalent.liepin_structured_cards_probe.v1" in call[4]:
                raise AssertionError("filter-only state must not trigger card extraction")
            return super().run(argv, timeout=timeout, env=env)

    state_before = (
        "<span>包含全部关键词</span>\n"
        "[26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>"
    )
    filter_only_state = "\n".join(
        [
            "URL: https://h.liepin.com/search/getConditionItem#session",
            "[10]<label>目前城市：</label>",
            "[20]<label>期望城市：</label>",
            "[30]<label>教育经历：</label>",
            "[40]<label>统招要求：</label>",
            "[50]<label>院校要求：</label>",
        ]
    )
    commands = NoProbeCommands(
        outputs={
            **_current_window_open_outputs(page_id="page-1"),
            ("opencli", "browser", "seektalent-liepin", "state"): [
                state_before,
                filter_only_state,
                filter_only_state,
                filter_only_state,
            ],
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发专家"): '{"filled":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "29"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "wait", "selector", "#resultList"): "{}",
        }
    )

    envelope = _runner(commands, lease_dir=tmp_path).search_liepin_cards(
        source_run_id="run-1",
        query="数据开发专家",
        max_pages=1,
        max_cards=10,
    )

    assert envelope["status"] == "blocked"
    assert ("opencli", "browser", "seektalent-liepin", "wait", "selector", "#resultList") in commands.calls


def test_search_liepin_filter_failure_exposes_only_safe_reason_codes(tmp_path: Path) -> None:
    state_before = (
        "[26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>"
    )
    state_after_search = "[41]<button><span>城市</span></button>\n王** 男 34岁 工作5年 硕士 上海"
    state_city_menu = "[41]<button><span>城市</span></button>\n[44]<label>上海</label>"
    state_after_bad_click = "[41]<button><span>城市</span></button>\n王** 男 34岁 工作5年 硕士 北京"
    commands = FakeCommands(
        outputs={
            **_current_window_open_outputs(page_id="page-1"),
            ("opencli", "browser", "seektalent-liepin", "state"): [
                state_before,
                state_before,
                state_after_search,
                state_city_menu,
                state_after_bad_click,
            ],
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发专家"): '{"filled":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "29"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "城市"): (
                '{"clicked":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "click", "44"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "1"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "2"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "3"): "{}",
        }
    )

    envelope = _runner(commands, lease_dir=tmp_path).search_liepin_cards(
        source_run_id="run-1",
        query="数据开发专家",
        max_pages=1,
        max_cards=10,
        native_filters={"city": "上海"},
    )

    assert envelope["status"] == "blocked"
    assert envelope["safe_reason_code"] == "liepin_opencli_filter_unapplied"
    trace = json.loads((tmp_path / "protected" / "pi-trace" / "run-1" / "action-trace.json").read_text())
    serialized = json.dumps({"trace": trace, "envelope": envelope}, ensure_ascii=False)
    assert "precondition_failed" not in serialized
    assert "postcondition_failed" not in serialized
    assert all(
        event["safe_reason_code"].startswith("liepin_opencli_")
        for event in trace["events"]
        if isinstance(event.get("safe_reason_code"), str)
    )


def test_search_liepin_cards_reobserves_search_ref_after_stale_submit(tmp_path: Path) -> None:
    state_before = (
        "<span>包含全部关键词</span>\n"
        "[26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>"
    )
    state_retry = (
        "<span>包含全部关键词</span>\n"
        "[26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[31]<button><span>搜 索</span></button>"
    )
    state_after = (
        "王** 男 40岁 工作14年 硕士 上海\n"
        "求职期望：上海 数据开发专家\n"
        "海光集成电路 · 高级主管工程师 2023.10-至今\n"
        "数据仓库 数据治理 Python Hive"
    )
    commands = FakeCommands(
        outputs={
            **_current_window_open_outputs(page_id="page-1"),
            ("opencli", "browser", "seektalent-liepin", "state"): [
                state_before,
                state_before,
                state_retry,
                state_after,
            ],
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发专家"): '{"filled":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "29"): subprocess.CalledProcessError(
                1,
                ["opencli"],
                output='{"error":{"code":"stale_ref","message":"target disappeared"}}',
            ),
            ("opencli", "browser", "seektalent-liepin", "click", "31"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "2"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "3"): "{}",
        }
    )

    envelope = _runner(commands, lease_dir=tmp_path).search_liepin_cards(
        source_run_id="run-1",
        query="数据开发专家",
        max_pages=1,
        max_cards=10,
    )

    assert envelope["status"] == "succeeded"
    assert ("opencli", "browser", "seektalent-liepin", "click", "29") in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "click", "31") in commands.calls
    trace = json.loads((tmp_path / "protected" / "pi-trace" / "run-1" / "action-trace.json").read_text())
    assert {
        "action_kind": "click_search_retry",
        "route_kind": "search",
        "safe_reason_code": "liepin_opencli_stale_ref",
    } in trace["events"]


def test_search_liepin_cards_retries_transient_status_after_search_click(tmp_path: Path) -> None:
    state_before = (
        "<span>包含全部关键词</span>\n"
        "[26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>"
    )
    state_after = (
        "王** 男 40岁 工作14年 硕士 上海\n"
        "求职期望：上海 数据开发专家\n"
        "海光集成电路 · 高级主管工程师 2023.10-至今\n"
        "数据仓库 数据治理 Python Hive"
    )
    commands = FakeCommands(
        outputs={
            **_current_window_open_outputs(page_id="page-1"),
            ("opencli", "browser", "seektalent-liepin", "state"): [
                state_before,
                state_before,
                subprocess.CalledProcessError(1, ["opencli"]),
                state_after,
            ],
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发专家"): '{"filled":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "29"): '{"clicked":true}',
        }
    )

    envelope = _runner(commands, lease_dir=tmp_path).search_liepin_cards(
        source_run_id="run-1",
        query="数据开发专家",
        max_pages=1,
        max_cards=10,
    )

    assert envelope["status"] == "succeeded"
    assert envelope["cards_returned"] == 1
    trace = json.loads((tmp_path / "protected" / "pi-trace" / "run-1" / "action-trace.json").read_text())
    assert {
        "action_kind": "observe_results_retry",
        "route_kind": "search",
        "safe_reason_code": "liepin_opencli_status_unavailable",
    } in trace["events"]


def test_search_liepin_cards_retries_stale_observe_results_once(tmp_path: Path) -> None:
    state_before = (
        "<span>包含全部关键词</span>\n"
        "[26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>"
    )
    state_after = (
        "王** 男 40岁 工作14年 硕士 上海\n"
        "求职期望：上海 数据开发专家\n"
        "海光集成电路 · 高级主管工程师 2023.10-至今\n"
        "数据仓库 数据治理 Python Hive"
    )
    commands = FakeCommands(
        outputs={
            **_current_window_open_outputs(page_id="page-1"),
            ("opencli", "browser", "seektalent-liepin", "state"): [
                state_before,
                state_before,
                subprocess.CalledProcessError(
                    1,
                    ["opencli"],
                    output='{"error":{"code":"stale_ref","message":"target disappeared","hint":"refresh state"}}',
                    stderr="",
                ),
                state_after,
            ],
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发专家"): '{"filled":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "搜 索"): (
                '{"clicked":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "3"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "2"): "{}",
        }
    )

    envelope = _runner(commands, lease_dir=tmp_path).search_liepin_cards(
        source_run_id="run-1",
        query="数据开发专家",
        max_pages=1,
        max_cards=10,
    )

    assert envelope["status"] == "succeeded"
    assert envelope["cards_returned"] == 1
    trace = json.loads((tmp_path / "protected" / "pi-trace" / "run-1" / "action-trace.json").read_text())
    assert {
        "action_kind": "observe_results_retry",
        "route_kind": "search",
        "safe_reason_code": "liepin_opencli_stale_ref",
    } in trace["events"]


def test_agent_driven_detail_tools_capture_and_finalize_resume_envelope(tmp_path: Path) -> None:
    search_url = "https://h.liepin.com/search/getConditionItem#session"
    detail_url = "https://h.liepin.com/resume/showresumedetail?id=70"
    search_state = (
        "王** 男 40岁 工作14年 硕士 上海\n"
        "求职期望：上海 数据开发专家\n"
        "海光集成电路 · 高级主管工程师 2023.10-至今\n"
        "数据仓库 数据治理 Python Hive\n"
        "[70]<button><span>查看完整简历</span></button>\n"
    )
    detail_state = (
        "王** 40岁 工作14年 硕士 上海\n"
        "当前职位：数据开发专家\n"
        "海光集成电路 · 高级主管工程师 2023.10-至今\n"
        "负责数据仓库、数据治理、Python 平台和 Hive 数仓。\n"
        "北京大学 · 本科 · 计算机"
    )
    commands = RefEvalCommands(
        eval_outputs_by_ref={},
        default_eval_output=_liepin_detail_payload_json(summary_text=detail_state),
        outputs={
            ("opencli", "browser", "seektalent-liepin", "get", "url"): [
                search_url,
                search_url,
            ],
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): [
                json.dumps([{"page": "page-search", "url": search_url, "active": True}]),
                json.dumps(
                    [
                        {"page": "page-search", "url": search_url, "active": False},
                        {"page": "page-detail-70", "url": detail_url, "active": True},
                    ]
                ),
            ],
            ("opencli", "browser", "seektalent-liepin", "state"): [search_state, detail_state],
            ("opencli", "browser", "seektalent-liepin", "click", "70"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-detail-70"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "2"): "{}",
        },
    )
    runner = _runner(commands, lease_dir=tmp_path)

    opened = runner.open_liepin_detail(source_run_id="run-1", ref="70", rank=1)
    captured = runner.capture_liepin_detail_resume(source_run_id="run-1", rank=1)
    finalized = runner.finalize_liepin_resumes(
        source_run_id="run-1",
        query="数据开发专家",
        max_pages=1,
        max_cards=7,
        cards_seen=1,
    )

    assert opened.ok is True
    assert captured.ok is True
    assert finalized["schema_version"] == "seektalent.liepin_opencli_resumes.v1"
    assert finalized["resumes_returned"] == 1
    assert finalized["detail_pages_opened"] == 1
    assert finalized["cards_excluded"] == []
    assert finalized["resumes"][0]["detail_payload"]["workExperienceList"][0]["summary"].startswith("王** 40岁")
    assert finalized["resumes"][0]["normalized_text"].count(detail_state) == 1
    assert ("opencli", "browser", "seektalent-liepin", "click", "70") in commands.calls
    trace_ref = str(finalized["action_trace_ref"]).removeprefix("artifact://protected/")
    trace = json.loads((tmp_path / "protected" / trace_ref).read_text())
    assert {"action_kind": "open_detail", "route_kind": "detail", "ref": "70", "rank": 1} in trace["events"]


def test_finalize_liepin_resumes_leaves_owned_detail_tabs_for_user_cleanup(tmp_path: Path) -> None:
    search_url = "https://h.liepin.com/search/getConditionItem#session"
    detail_url = (
        "https://h.liepin.com/resume/showresumedetail/?res_id_encode=778882227ddfWf393e2b5fdad"
        "&index=5&position=5&cur_page=0&pageSize=30&sfrom=RES_SEARCH&res_source=1&type=normal"
    )
    search_state = (
        "摆** 31岁 工作7年 本科 北京\n数据开发 ETL Python\n[357]<div class=detail-resume-card-wrap>查看完整简历</div>"
    )
    detail_state = "摆** 31岁 工作7年 本科 北京\n当前职位：数据开发专家\n负责 ETL、Python、离线数仓和数据治理。"
    commands = RefEvalCommands(
        eval_outputs_by_ref={"357": detail_url},
        default_eval_output=_liepin_detail_payload_json(candidate_name="摆**", summary_text=detail_state),
        outputs={
            ("opencli", "browser", "seektalent-liepin", "unbind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "bind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "get", "url"): [search_url, detail_url],
            ("opencli", "browser", "seektalent-liepin", "state"): [search_state, detail_state],
            ("opencli", "browser", "seektalent-liepin", "tab", "new", detail_url): (
                f'{{"url":"{detail_url}","page":"page-detail-357"}}'
            ),
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-detail-357"): "{}",
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-search"): "{}",
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): [
                json.dumps(
                    [
                        {"page": "page-detail-357", "url": detail_url, "active": True},
                        {"page": "user-github", "url": "https://github.com/", "active": False},
                    ]
                ),
                json.dumps([{"page": "user-github", "url": "https://github.com/", "active": False}]),
            ],
            ("opencli", "browser", "seektalent-liepin", "tab", "close", "page-detail-357"): "{}",
        },
    )
    runner = _runner(commands, lease_dir=tmp_path)
    runner._write_owned_page_marker(
        page_id="page-search",
        url=search_url,
        source_run_id=None,
        runtime_run_id="run-1",
        source_lane_run_id="run-1:source:liepin:round:1:lane:1",
        owner_nonce="owned-search",
        opened_at=9_999_999_999.0,
    )

    assert runner.open_liepin_detail(source_run_id="run-1", ref="357", rank=1).ok is True
    assert runner.capture_liepin_detail_resume(source_run_id="run-1", rank=1).ok is True
    finalized = runner.finalize_liepin_resumes(
        source_run_id="run-1",
        query="数据开发专家",
        max_pages=1,
        max_cards=7,
        cards_seen=1,
    )

    assert finalized["resumes_returned"] == 1
    assert ("opencli", "browser", "seektalent-liepin", "tab", "close", "page-detail-357") not in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "tab", "close", "user-github") not in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "tab", "close", "page-search") not in commands.calls
    marker_path = tmp_path / "seektalent-liepin-owned-pages.json"
    owned_pages = json.loads(marker_path.read_text(encoding="utf-8")) if marker_path.exists() else {}
    assert any(marker.get("url") == detail_url for marker in owned_pages.values())
    assert "page-search" in owned_pages


def test_agent_driven_open_detail_restores_search_tab_for_next_ref(tmp_path: Path) -> None:
    search_url = "https://h.liepin.com/search/getConditionItem#session"
    detail70_url = "https://h.liepin.com/resume/showresumedetail?id=70"
    detail71_url = "https://h.liepin.com/resume/showresumedetail?id=71"
    search_state = (
        "王** 男 40岁 工作14年 硕士 上海\n"
        "求职期望：上海 数据开发专家\n"
        "海光集成电路 · 高级主管工程师 2023.10-至今\n"
        "数据仓库 数据治理 Python Hive\n"
        "[70]<button><span>查看完整简历</span></button>\n"
        "张** 女 36岁 工作11年 硕士 上海\n"
        "求职期望：上海 数据平台专家\n"
        "云栖数据 · 数据平台负责人 2020.01-至今\n"
        "数据治理 Python Spark\n"
        "[71]<button><span>查看完整简历</span></button>"
    )
    detail70_state = (
        "王** 40岁 工作14年 硕士 上海\n当前职位：数据开发专家\n负责数据仓库、数据治理、Python 平台和 Hive 数仓。"
    )
    detail71_state = "张** 36岁 工作11年 硕士 上海\n当前职位：数据平台专家\n负责数据治理、Python 和 Spark 平台。"
    commands = RefEvalCommands(
        eval_outputs_by_ref={},
        default_eval_output=_liepin_detail_payload_json(summary_text=detail70_state),
        outputs={
            ("opencli", "browser", "seektalent-liepin", "unbind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "bind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "tab", "new", search_url): (
                '{"url":"https://h.liepin.com/search/getConditionItem#session","page":"page-search"}'
            ),
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-search"): "{}",
            ("opencli", "browser", "seektalent-liepin", "get", "url"): [search_url] * 10,
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): [
                json.dumps([{"page": "page-search", "url": search_url, "active": True}]),
                json.dumps([{"page": "page-search", "url": search_url, "active": True}]),
                json.dumps(
                    [
                        {"page": "page-detail-70", "url": detail70_url, "active": True},
                        {"page": "page-search", "url": search_url, "active": False},
                    ]
                ),
                json.dumps(
                    [
                        {"page": "page-detail-70", "url": detail70_url, "active": False},
                        {"page": "page-search", "url": search_url, "active": True},
                    ]
                ),
                json.dumps(
                    [
                        {"page": "page-detail-71", "url": detail71_url, "active": True},
                        {"page": "page-search", "url": search_url, "active": False},
                    ]
                ),
            ],
            ("opencli", "browser", "seektalent-liepin", "state"): [
                search_state,
                search_state,
                detail70_state,
                search_state,
                search_state,
                detail71_state,
            ],
            ("opencli", "browser", "seektalent-liepin", "click", "70"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "71"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-detail-70"): "{}",
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-detail-71"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "2"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "5"): "{}",
        },
    )
    runner = _runner(commands, lease_dir=tmp_path)

    assert runner.open_liepin_tab(search_url).ok is True
    assert runner.open_liepin_detail(source_run_id="run-1", ref="70", rank=1).ok is True
    assert runner.capture_liepin_detail_resume(source_run_id="run-1", rank=1).ok is True
    runner._select_and_mark_owned_liepin_tab(page_id="page-search", url=search_url, source_run_id="run-1")
    commands.default_eval_output = _liepin_detail_payload_json(
        candidate_name="张**",
        summary_text=detail71_state,
    )
    assert runner.open_liepin_detail(source_run_id="run-1", ref="71", rank=2).ok is True
    assert runner.capture_liepin_detail_resume(source_run_id="run-1", rank=2).ok is True

    second_select = [
        index
        for index, call in enumerate(commands.calls)
        if call == ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-search")
    ][0]
    assert second_select < commands.calls.index(("opencli", "browser", "seektalent-liepin", "click", "71"))


def test_search_liepin_resumes_leaves_detail_tabs_open_and_restores_search_for_next_capture(tmp_path: Path) -> None:
    search_url = "https://h.liepin.com/search/getConditionItem#session"
    detail70_url = "https://h.liepin.com/resume/showresumedetail/?res_id_encode=abc70"
    detail71_url = "https://h.liepin.com/resume/showresumedetail/?res_id_encode=abc71"
    search_form_state = (
        "<span>包含全部关键词</span>\n"
        "[26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>"
    )
    search_results_state = (
        "id=resultList\n"
        "王** 40岁 工作14年 硕士 上海\n"
        "求职期望：上海 数据开发专家\n"
        "海光集成电路 · 高级主管工程师 2023.10-至今\n"
        "[70]<div class=detail-resume-card-wrap><span>查看完整简历</span></div>\n"
        "张** 36岁 工作11年 硕士 上海\n"
        "求职期望：上海 数据平台专家\n"
        "云栖数据 · 数据平台负责人 2020.01-至今\n"
        "[71]<div class=detail-resume-card-wrap><span>查看完整简历</span></div>"
    )
    detail70_state = "王** 40岁 工作14年 硕士 上海\n当前职位：数据开发专家\n负责数据仓库、数据治理和 Python 平台。"
    detail71_state = "张** 36岁 工作11年 硕士 上海\n当前职位：数据平台专家\n负责数据治理、Python 和 Spark 平台。"
    commands = RefEvalCommands(
        eval_outputs_by_ref={"70": detail70_url, "71": detail71_url},
        default_eval_output=_liepin_detail_payload_json(),
        outputs={
            ("opencli", "browser", "seektalent-liepin", "unbind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "bind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "tab", "new", search_url): (
                f'{{"url":"{search_url}","page":"page-search"}}'
            ),
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-search"): "{}",
            ("opencli", "browser", "seektalent-liepin", "get", "url"): [search_url] * 80,
            ("opencli", "browser", "seektalent-liepin", "state"): [
                search_form_state,
                *([search_results_state] * 12),
                *([detail70_state] * 4),
                *([search_results_state] * 8),
                *([detail71_state] * 6),
            ],
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发专家"): '{"filled":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "搜 索"): (
                '{"clicked":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "3"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "2"): "{}",
            ("opencli", "browser", "seektalent-liepin", "tab", "new", detail70_url): (
                f'{{"url":"{detail70_url}","page":"page-detail-70"}}'
            ),
            ("opencli", "browser", "seektalent-liepin", "tab", "new", detail71_url): (
                f'{{"url":"{detail71_url}","page":"page-detail-71"}}'
            ),
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-detail-70"): "{}",
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-detail-71"): "{}",
            ("opencli", "browser", "seektalent-liepin", "tab", "close", "page-detail-70"): "{}",
            ("opencli", "browser", "seektalent-liepin", "tab", "close", "page-detail-71"): "{}",
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): [
                json.dumps([{"page": "page-search", "url": search_url, "active": True}]),
                json.dumps([{"page": "page-search", "url": search_url, "active": True}]),
                json.dumps(
                    [
                        {"page": "page-search", "url": search_url, "active": False},
                        {"page": "page-detail-70", "url": detail70_url, "active": True},
                    ]
                ),
                json.dumps([{"page": "page-search", "url": search_url, "active": True}]),
                json.dumps([{"page": "page-search", "url": search_url, "active": True}]),
                json.dumps(
                    [
                        {"page": "page-search", "url": search_url, "active": False},
                        {"page": "page-detail-71", "url": detail71_url, "active": True},
                    ]
                ),
                json.dumps([{"page": "page-search", "url": search_url, "active": True}]),
            ],
        },
    )

    envelope = _runner(commands, lease_dir=tmp_path).search_liepin_resumes(
        source_run_id="run-1",
        query="数据开发专家",
        target_resumes=2,
        max_pages=1,
        max_cards=2,
    )

    assert envelope["status"] == "succeeded"
    assert envelope["resumes_returned"] == 2
    workflow_steps = envelope["workflow_steps"]
    assert not any(step["step_name"] == "cleanup_" + "detail_tabs" for step in workflow_steps)
    assert any(step["step_name"] == "finalize" and step["status"] == "completed" for step in workflow_steps)
    search_select_indexes = [
        index
        for index, call in enumerate(commands.calls)
        if call == ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-search")
    ]
    assert search_select_indexes
    assert ("opencli", "browser", "seektalent-liepin", "tab", "new", detail71_url) in commands.calls
    assert any(len(call) > 4 and call[3] == "eval" and 'data-opencli-ref="71"' in call[4] for call in commands.calls)
    assert ("opencli", "browser", "seektalent-liepin", "tab", "close", "page-detail-70") not in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "tab", "close", "page-detail-71") not in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "tab", "close", "page-search") not in commands.calls
    trace_ref = str(envelope["action_trace_ref"]).removeprefix("artifact://protected/")
    trace = json.loads((tmp_path / "protected" / trace_ref).read_text())
    assert any(event.get("action_kind") == "return_to_search_after_capture" for event in trace["events"])


def test_search_liepin_resumes_does_not_open_details_after_filter_failure(tmp_path: Path) -> None:
    state_before = (
        "[26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>"
    )
    state_after_search = "[41]<button><span>城市</span></button>\n王** 男 34岁 工作5年 硕士 上海"
    state_city_menu = "[41]<button><span>城市</span></button>\n[44]<label>上海</label>"
    commands = RefEvalCommands(
        eval_outputs_by_ref={},
        default_eval_output=_liepin_detail_payload_json(),
        outputs={
            **_current_window_open_outputs(page_id="page-1"),
            ("opencli", "browser", "seektalent-liepin", "state"): [
                state_before,
                state_after_search,
                state_city_menu,
                state_city_menu,
            ],
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发专家"): '{"filled":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "搜 索"): (
                '{"clicked":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "城市"): (
                '{"clicked":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "click", "44"): subprocess.CalledProcessError(1, ["opencli"]),
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "1"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "2"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "3"): "{}",
        },
    )

    envelope = _runner(commands, lease_dir=tmp_path).search_liepin_resumes(
        source_run_id="run-1",
        query="数据开发专家",
        target_resumes=2,
        max_pages=1,
        max_cards=10,
        native_filters={"city": "上海"},
    )

    assert envelope["status"] == "blocked"
    assert envelope["safe_reason_code"] == "liepin_opencli_filter_unapplied"
    assert envelope["resumes"] == []
    assert all("showresumedetail" not in " ".join(call) for call in commands.calls)


def test_search_liepin_resumes_uses_cached_detail_urls_when_refresh_after_return_loses_cards(
    tmp_path: Path,
) -> None:
    search_url = "https://h.liepin.com/search/getConditionItem#session"
    detail70_url = "https://h.liepin.com/resume/showresumedetail/?res_id_encode=abc70"
    detail71_url = "https://h.liepin.com/resume/showresumedetail/?res_id_encode=abc71"
    search_form_state = (
        "<span>包含全部关键词</span>\n"
        "[26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>"
    )
    search_results_state = (
        "id=resultList\n"
        "王** 40岁 工作14年 硕士 上海\n"
        "求职期望：上海 数据开发专家\n"
        "海光集成电路 · 高级主管工程师 2023.10-至今\n"
        "[70]<div class=detail-resume-card-wrap><span>查看完整简历</span></div>\n"
        "张** 36岁 工作11年 硕士 上海\n"
        "求职期望：上海 数据平台专家\n"
        "云栖数据 · 数据平台负责人 2020.01-至今\n"
        "[71]<div class=detail-resume-card-wrap><span>查看完整简历</span></div>"
    )
    empty_search_state = "id=resultList\n暂无数据"
    detail70_state = (
        "王** 40岁 工作14年 硕士 上海\n当前职位：数据开发专家\n负责数据仓库、数据治理、Python 平台和 Hive 数仓。"
    )
    detail71_state = "张** 36岁 工作11年 硕士 上海\n当前职位：数据平台专家\n负责数据治理、Python 和 Spark 平台。"
    commands = RefEvalCommands(
        eval_outputs_by_ref={
            "70": detail70_url,
            "71": detail71_url,
            ANY_STRUCTURED_CARD_PROBE: [
                _structured_cards_probe_json("70", "71"),
                _structured_cards_probe_json("70", "71"),
                _empty_structured_cards_probe_json(),
            ],
        },
        default_eval_output=_liepin_detail_payload_json(),
        outputs={
            ("opencli", "browser", "seektalent-liepin", "unbind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "bind"): "{}",
            ("opencli", "browser", "seektalent-liepin", "tab", "new", search_url): (
                f'{{"url":"{search_url}","page":"page-search"}}'
            ),
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-search"): "{}",
            ("opencli", "browser", "seektalent-liepin", "get", "url"): [search_url] * 80,
            ("opencli", "browser", "seektalent-liepin", "state"): [
                search_form_state,
                *([search_results_state] * 10),
                *([detail70_state] * 4),
                *([empty_search_state] * 5),
                *([detail71_state] * 6),
            ],
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发专家"): '{"filled":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "搜 索"): (
                '{"clicked":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "3"): "{}",
            ("opencli", "browser", "seektalent-liepin", "tab", "new", detail70_url): (
                f'{{"url":"{detail70_url}","page":"page-detail-70"}}'
            ),
            ("opencli", "browser", "seektalent-liepin", "tab", "new", detail71_url): (
                f'{{"url":"{detail71_url}","page":"page-detail-71"}}'
            ),
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-detail-70"): "{}",
            ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-detail-71"): "{}",
            ("opencli", "browser", "seektalent-liepin", "tab", "close", "page-detail-70"): "{}",
            ("opencli", "browser", "seektalent-liepin", "tab", "close", "page-detail-71"): "{}",
            ("opencli", "browser", "seektalent-liepin", "tab", "list"): [
                json.dumps([{"page": "page-search", "url": search_url, "active": True}]),
                json.dumps([{"page": "page-search", "url": search_url, "active": True}]),
                json.dumps(
                    [
                        {"page": "page-search", "url": search_url, "active": False},
                        {"page": "page-detail-70", "url": detail70_url, "active": True},
                    ]
                ),
                json.dumps([{"page": "page-search", "url": search_url, "active": True}]),
                json.dumps([{"page": "page-search", "url": search_url, "active": True}]),
                json.dumps(
                    [
                        {"page": "page-search", "url": search_url, "active": False},
                        {"page": "page-detail-71", "url": detail71_url, "active": True},
                    ]
                ),
            ],
        },
    )

    envelope = _runner(commands, lease_dir=tmp_path).search_liepin_resumes(
        source_run_id="run-1",
        query="数据开发专家",
        target_resumes=2,
        max_pages=1,
        max_cards=2,
    )

    assert envelope["status"] == "succeeded"
    assert envelope["resumes_returned"] == 2
    assert ("opencli", "browser", "seektalent-liepin", "tab", "close", "page-detail-70") not in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "tab", "new", detail71_url) in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "tab", "select", "page-search") in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "tab", "close", "page-search") not in commands.calls
    trace_ref = str(envelope["action_trace_ref"]).removeprefix("artifact://protected/")
    trace = json.loads((tmp_path / "protected" / trace_ref).read_text())
    assert any(
        event.get("action_kind") == "visible_cards_refreshed_after_return" and event.get("visible_cards") == 0
        for event in trace["events"]
    )
    assert any(
        event.get("action_kind") == "open_detail_succeeded"
        and event.get("rank") == 2
        and event.get("open_mode") == "cached_url"
        for event in trace["events"]
    )
    assert [
        (event.get("action_kind"), event.get("rank"), event.get("open_mode"))
        for event in trace["events"]
        if event.get("action_kind") in {"open_detail", "open_detail_succeeded"}
    ] == [
        ("open_detail", 1, "visible_card"),
        ("open_detail_succeeded", 1, "visible_card"),
        ("open_detail", 2, "cached_url"),
        ("open_detail_succeeded", 2, "cached_url"),
    ]
    assert [
        (event.get("action_kind"), event.get("rank"))
        for event in trace["events"]
        if event.get("action_kind") == "observe_detail"
    ] == [("observe_detail", 1), ("observe_detail", 2)]


def test_finalize_liepin_resumes_marks_partial_when_target_is_not_met(tmp_path: Path) -> None:
    commands = RefEvalCommands(
        eval_outputs_by_ref={},
        default_eval_output=_liepin_detail_payload_json(),
        outputs={
            ("opencli", "browser", "seektalent-liepin", "state"): (
                "URL: https://h.liepin.com/resume/showresumedetail?id=70\n"
                "王** 40岁 工作14年 硕士 上海\n"
                "当前职位：数据开发专家\n"
                "负责数据仓库、数据治理、Python 平台和 Hive 数仓。"
            )
        },
    )
    runner = _runner(commands, lease_dir=tmp_path)

    assert runner.capture_liepin_detail_resume(source_run_id="run-1", rank=1).ok is True
    finalized = runner.finalize_liepin_resumes(
        source_run_id="run-1",
        query="数据开发专家",
        max_pages=1,
        max_cards=1,
        cards_seen=1,
        target_resumes=2,
    )

    assert finalized["status"] == "partial"
    assert finalized["stop_reason"] == "partial_timeout"
    assert finalized["resumes_returned"] == 1
    assert finalized["workflow_steps"][-1]["step_name"] == "finalize"
    assert finalized["workflow_steps"][-1]["status"] == "partial"
    assert finalized["workflow_steps"][-1]["safe_reason_code"] == "partial_timeout"
    assert finalized["workflow_steps"][-1]["safe_counts"] == {"resumes_returned": 1}
    trace_ref = str(finalized["action_trace_ref"]).removeprefix("artifact://protected/")
    trace = json.loads((tmp_path / "protected" / trace_ref).read_text())
    assert trace["status"] == "partial"
    assert trace["stop_reason"] == "partial_timeout"
    assert trace["target_resumes"] == 2
    assert trace["max_cards"] == 1
    assert trace["cards_seen"] == 1
    assert trace["resumes_returned"] == 1


def test_stable_detail_candidate_key_hash_is_subject_stable_and_rejects_invalid_urls() -> None:
    detail_url = (
        "https://h.liepin.com/resume/showresumedetail/?res_id_encode=sameSubject"
        "&index=5&position=5&cur_page=0"
    )

    first = stable_liepin_detail_candidate_key_hash(detail_url)
    second = stable_liepin_detail_candidate_key_hash(
        "https://h.liepin.com/resume/showresumedetail/?position=9&res_id_encode=sameSubject&index=9"
    )

    assert first is not None
    assert first == second
    assert len(first) == 64
    assert first == hashlib.sha256("liepin:res_id_encode:v1:sameSubject".encode("utf-8")).hexdigest()
    assert stable_liepin_detail_candidate_key_hash(
        "https://h.liepin.com/resume/showresumedetail/?res_id_encode=one&res_id_encode=two"
    ) is None
    assert stable_liepin_detail_candidate_key_hash(
        "https://h.liepin.com/resume/showresumedetail/"
    ) is None
    assert stable_liepin_detail_candidate_key_hash(
        "https://h.liepin.com/resume/showresumedetail/?res_id_encode=not-valid"
    ) is None
    assert stable_liepin_detail_candidate_key_hash(
        "https://h.liepin.com/resume/showresumedetail-extra/?res_id_encode=sameSubject"
    ) is None
    assert stable_liepin_detail_candidate_key_hash(
        "https://example.test/resume/showresumedetail/?res_id_encode=sameSubject"
    ) is None


def test_capture_liepin_detail_resume_carries_only_opaque_candidate_key(tmp_path: Path) -> None:
    detail_url = (
        "https://h.liepin.com/resume/showresumedetail/?res_id_encode=778882227ddfWf393e2b5fdad"
        "&index=5&position=5&cur_page=0&pageSize=30&sfrom=RES_SEARCH&res_source=1&type=normal"
    )
    commands = RefEvalCommands(
        eval_outputs_by_ref={},
        default_eval_output=_liepin_detail_payload_json(),
        outputs={
            ("opencli", "browser", "seektalent-liepin", "state"): (
                "URL: https://h.liepin.com/resume/showresumedetail/?res_id_encode=778882227ddfWf393e2b5fdad\n"
                "王** 40岁 工作14年 硕士 上海\n"
                "当前职位：数据开发专家\n"
                "负责数据仓库、数据治理、Python 平台和 Hive 数仓。"
            ),
            ("opencli", "browser", "seektalent-liepin", "get", "url"): detail_url,
        },
    )
    runner = _runner(commands, lease_dir=tmp_path)

    captured = runner.capture_liepin_detail_resume(source_run_id="run-1", rank=1)

    assert captured.ok is True
    collected = json.loads((tmp_path / "protected" / "pi-detail" / "run-1" / "collected-resumes.json").read_text())
    candidate_key_hash = stable_liepin_detail_candidate_key_hash(detail_url)
    assert candidate_key_hash is not None
    assert collected["resumes"][0]["provider_candidate_key_hash"] == candidate_key_hash
    assert "sourceUrl" not in collected["resumes"][0]["detail_payload"]
    assert "778882227ddfWf393e2b5fdad" not in json.dumps(collected, ensure_ascii=False)


def test_claim_aware_capture_rejects_mismatched_candidate_key_before_artifact_write(tmp_path: Path) -> None:
    expected_key = stable_liepin_detail_candidate_key_hash(
        "https://h.liepin.com/resume/showresumedetail/?res_id_encode=expectedSubject"
    )
    assert expected_key is not None
    commands = RefEvalCommands(
        eval_outputs_by_ref={},
        default_eval_output=_liepin_detail_payload_json(),
        outputs={
            ("opencli", "browser", "seektalent-liepin", "state"): (
                "URL: https://h.liepin.com/resume/showresumedetail/?res_id_encode=capturedSubject\n"
                "王** 40岁 工作14年 硕士 上海\n当前职位：数据开发专家"
            ),
            ("opencli", "browser", "seektalent-liepin", "get", "url"): (
                "https://h.liepin.com/resume/showresumedetail/?res_id_encode=capturedSubject"
            ),
        },
    )

    captured = _runner(commands, lease_dir=tmp_path)._capture_liepin_detail_resume(
        source_run_id="run-1",
        rank=1,
        require_ready=True,
        emit_events=False,
        claim_aware=True,
        expected_provider_candidate_key_hash=expected_key,
    )

    assert captured.ok is False
    assert captured.safe_reason_code == "liepin_opencli_candidate_identity_mismatch"
    assert not (tmp_path / "protected").exists()


def test_claim_aware_capture_persists_only_the_matched_opaque_key(tmp_path: Path) -> None:
    detail_url = "https://h.liepin.com/resume/showresumedetail/?res_id_encode=sameSubject"
    expected_key = stable_liepin_detail_candidate_key_hash(detail_url)
    assert expected_key is not None
    commands = RefEvalCommands(
        eval_outputs_by_ref={},
        default_eval_output=_liepin_detail_payload_json(),
        outputs={
            ("opencli", "browser", "seektalent-liepin", "state"): (
                "URL: https://h.liepin.com/resume/showresumedetail/?res_id_encode=sameSubject\n"
                "王** 40岁 工作14年 硕士 上海\n当前职位：数据开发专家"
            ),
            ("opencli", "browser", "seektalent-liepin", "get", "url"): detail_url,
        },
    )

    captured = _runner(commands, lease_dir=tmp_path)._capture_liepin_detail_resume(
        source_run_id="run-1",
        rank=1,
        require_ready=True,
        emit_events=False,
        claim_aware=True,
        expected_provider_candidate_key_hash=expected_key,
    )

    assert captured.ok is True
    collected = json.loads((tmp_path / "protected" / "pi-detail" / "run-1" / "collected-resumes.json").read_text())
    resume = collected["resumes"][0]
    assert resume["claim_aware"] is True
    assert resume["provider_candidate_key_hash"] == expected_key
    assert "provider_candidate_key_material_ref" not in resume
    assert "candidate_resume_id" not in resume
    assert "sourceUrl" not in resume["detail_payload"]


def test_capture_liepin_detail_resume_preserves_collected_resumes_dict_schema_under_update(tmp_path: Path) -> None:
    detail_url = "https://h.liepin.com/resume/showresumedetail/?res_id_encode=778882227ddfWf393e2b5fdad"
    path = tmp_path / "protected" / "pi-detail" / "run-1" / "collected-resumes.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": "seektalent.opencli_collected_resumes.v1",
                "resumes": [
                    {
                        "provider_rank": 2,
                        "candidate_resume_id": "liepin-opencli-detail-run-1-2",
                        "protected_snapshot_ref": "artifact://protected/pi-detail/run-1/2.json",
                        "normalized_snapshot_ref": "artifact://protected/pi-detail/run-1/2-normalized.json",
                        "normalized_text": "existing",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    commands = RefEvalCommands(
        eval_outputs_by_ref={},
        default_eval_output=_liepin_detail_payload_json(),
        outputs={
            ("opencli", "browser", "seektalent-liepin", "state"): (
                "URL: https://h.liepin.com/resume/showresumedetail/?res_id_encode=778882227ddfWf393e2b5fdad\n"
                "王** 40岁 工作14年 硕士 上海\n当前职位：数据开发专家"
            ),
            ("opencli", "browser", "seektalent-liepin", "get", "url"): detail_url,
        },
    )

    captured = _runner(commands, lease_dir=tmp_path).capture_liepin_detail_resume(source_run_id="run-1", rank=1)

    assert captured.ok is True
    assert captured.counts == {"resumes": 2, "rank": 1}
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert set(loaded) == {"schema_version", "resumes"}
    assert loaded["schema_version"] == "seektalent.opencli_collected_resumes.v1"
    assert [resume["provider_rank"] for resume in loaded["resumes"]] == [1, 2]
    assert "sourceUrl" not in loaded["resumes"][0]["detail_payload"]
    assert loaded["resumes"][0]["provider_candidate_key_hash"] == stable_liepin_detail_candidate_key_hash(detail_url)


def test_capture_liepin_detail_resume_waits_until_detail_page_is_ready(tmp_path: Path) -> None:
    class DetailReadyCommands(FakeCommands):
        def __init__(self) -> None:
            super().__init__(
                outputs={
                    ("opencli", "browser", "seektalent-liepin", "state"): [
                        "URL: about:blank url: about:blank title: viewport: 1512x707 --- interactive: 0",
                        detail_state,
                    ],
                    (
                        "opencli",
                        "browser",
                        "seektalent-liepin",
                        "wait",
                        "selector",
                        "#resume-detail-basic-info",
                    ): "{}",
                    ("opencli", "browser", "seektalent-liepin", "get", "url"): (
                        "https://h.liepin.com/resume/showresumedetail/?res_id_encode=778882227ddfWf393e2b5fdad"
                    ),
                }
            )
            self.detail_ready = False

        def run(self, argv: Sequence[str], *, timeout: int, env: Mapping[str, str] | None = None) -> str:
            call = tuple(argv)
            if len(call) >= 4 and call[3] == "eval":
                del timeout
                self.calls.append(call)
                self.envs.append(env)
                if not self.detail_ready:
                    return json.dumps({"ok": False, "safeReasonCode": "liepin_opencli_detail_not_opened"})
                return _liepin_detail_payload_json(summary_text=detail_state)
            output = super().run(argv, timeout=timeout, env=env)
            if call == ("opencli", "browser", "seektalent-liepin", "state") and "当前职位" in output:
                self.detail_ready = True
            return output

    commands = DetailReadyCommands()
    captured = _runner(commands, lease_dir=tmp_path).capture_liepin_detail_resume(source_run_id="run-1", rank=1)

    assert captured.ok is True
    wait_call = (
        "opencli",
        "browser",
        "seektalent-liepin",
        "wait",
        "selector",
        "#resume-detail-basic-info",
    )
    assert commands.calls.index(wait_call) < next(
        index for index, call in enumerate(commands.calls) if len(call) >= 4 and call[3] == "eval"
    )
    assert [
        call
        for call in commands.calls
        if len(call) >= 6 and call[:4] == ("opencli", "browser", "seektalent-liepin", "wait") and call[4] == "time"
    ] == []


def test_capture_liepin_detail_resume_rejects_blank_detail_probe(tmp_path: Path) -> None:
    commands = EvalCommands(
        eval_output=json.dumps({"ok": False, "safeReasonCode": "liepin_opencli_detail_not_opened"}),
        outputs={
            ("opencli", "browser", "seektalent-liepin", "state"): (
                "URL: about:blank url: about:blank title: viewport: 1512x707 --- interactive: 0 | iframes: 0"
            ),
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "2"): "{}",
        },
    )

    captured = _runner(commands, lease_dir=tmp_path).capture_liepin_detail_resume(source_run_id="run-1", rank=1)

    assert captured.ok is False
    assert captured.safe_reason_code == "liepin_opencli_detail_not_opened"
    assert not (tmp_path / "protected" / "pi-detail" / "run-1" / "collected-resumes.json").exists()


def test_capture_liepin_detail_resume_rejects_whole_page_text_aliases_before_artifact_write(
    tmp_path: Path,
) -> None:
    payload = json.loads(_liepin_detail_payload_json(summary_text=detail_state))
    payload["fullText"] = "SENTINEL_TOP_LEVEL_DETAIL_TEXT"
    payload["jobIntention"]["rawText"] = "SENTINEL_NESTED_DETAIL_TEXT"
    commands = EvalCommands(
        eval_output=json.dumps(payload, ensure_ascii=False),
        outputs={
            ("opencli", "browser", "seektalent-liepin", "state"): (
                "URL: https://h.liepin.com/resume/showresumedetail/?res_id_encode=778882227ddfWf393e2b5fdad\n"
                "王** 40岁 工作14年 硕士 上海\n"
                "当前职位：数据开发专家"
            ),
        },
    )

    captured = _runner(commands, lease_dir=tmp_path).capture_liepin_detail_resume(source_run_id="run-1", rank=1)

    assert captured.ok is False
    assert captured.safe_reason_code == "liepin_opencli_malformed_state"
    assert not (tmp_path / "protected").exists()


def test_detail_probe_payload_rejects_whole_page_text_extra_alias() -> None:
    payload = json.loads(_liepin_detail_payload_json(summary_text=detail_state))
    payload["extra"] = {"wholePageText": "SENTINEL_WHOLE_PAGE_TEXT"}

    with pytest.raises(OpenCliBrowserError) as error:
        _safe_detail_payload_from_probe_output(json.dumps(payload, ensure_ascii=False))

    assert error.value.safe_reason_code == "liepin_opencli_malformed_state"


def test_generic_click_still_rejects_liepin_detail_targets() -> None:
    with pytest.raises(OpenCliBrowserError) as error:
        _runner(FakeCommands()).click(target="查看完整简历")

    assert error.value.safe_reason_code == "liepin_opencli_forbidden_command"


def test_card_state_classification_still_rejects_detail_url() -> None:
    assert (
        classify_liepin_state(url="https://h.liepin.com/resume/detail?id=1", text="完整简历")
        == "liepin_opencli_unknown_modal"
    )


def test_search_liepin_cards_applies_native_filters_before_reading_cards(tmp_path: Path) -> None:
    state_before = (
        "<span>包含全部关键词</span>\n"
        "  [25]<div />\n"
        "    [26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>"
    )
    state_after_search = (
        "[41]<button><span>城市</span></button>\n"
        "[42]<button><span>工作经验</span></button>\n"
        "[43]<button><span>年龄</span></button>\n"
        "[90]<div>王** 男 34岁 工作5年 硕士 上海</div>"
    )
    state_city_menu = "[41]<button><span>城市</span></button>\n[44]<label>上海</label>\n[45]<label>北京</label>"
    state_after_city = "已选 上海\n[42]<button><span>工作经验</span></button>\n[43]<button><span>年龄</span></button>"
    state_experience_menu = "已选 上海\n[42]<button><span>工作经验</span></button>\n[45]<label>3-5年</label>"
    state_after_experience = "已选 上海 3-5年\n[43]<button><span>年龄</span></button>"
    state_age_menu = "已选 上海 3-5年\n[43]<button><span>年龄</span></button>\n[46]<label>35岁以下</label>"
    state_after_filters = (
        "已选 上海 3-5年 35岁以下\n"
        "王** 男 34岁 工作5年 硕士 上海\n"
        "求职期望：上海 数据开发专家\n"
        "某数据公司 · 数据开发专家 2021.01-至今"
    )
    commands = FakeCommands(
        outputs={
            **_current_window_open_outputs(page_id="page-1"),
            ("opencli", "browser", "seektalent-liepin", "state"): [
                state_before,
                state_after_search,
                state_city_menu,
                state_after_city,
                state_experience_menu,
                state_after_experience,
                state_age_menu,
                state_after_filters,
            ],
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发专家"): '{"filled":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "搜 索"): (
                '{"clicked":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "城市"): (
                '{"clicked":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "click", "44"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "工作经验"): (
                '{"clicked":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "click", "45"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "年龄"): (
                '{"clicked":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "click", "46"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "1"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "3"): "{}",
        }
    )

    envelope = _runner(commands, lease_dir=tmp_path).search_liepin_cards(
        source_run_id="run-1",
        query="数据开发专家",
        max_pages=1,
        max_cards=10,
        native_filters={
            "city": "上海",
            "experience": {"minYears": 3, "maxYears": 5},
            "age": {"max": 35},
        },
    )

    assert envelope["status"] == "succeeded"
    trace = json.loads((tmp_path / "protected" / "pi-trace" / "run-1" / "action-trace.json").read_text())
    assert {
        "action_kind": "apply_native_filter",
        "filter": "city",
        "section": "legacy",
        "value": "上海",
        "ok": True,
    } in trace["events"]
    click_search_index = next(
        index
        for index, call in enumerate(commands.calls)
        if call
        in {
            ("opencli", "browser", "seektalent-liepin", "click", "29"),
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "搜 索"),
        }
    )
    assert click_search_index < len(commands.calls)
    filter_events = [
        (event.get("filter"), event.get("section"), event.get("value"))
        for event in trace["events"]
        if event.get("action_kind") == "apply_native_filter"
    ]
    assert filter_events == [
        ("city", "legacy", "上海"),
        ("experience", "legacy", "3-5年"),
        ("age", "legacy", "35岁以下"),
    ]


def test_search_liepin_cards_clears_existing_filters_before_keyword_search(tmp_path: Path) -> None:
    dirty_state = (
        "<span>包含全部关键词</span>\n"
        "[26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>\n"
        "[54]<span />\n"
        "  清空筛选条件\n"
        "<span>期望城市：</span>\n"
        "[66]<label>上海</label>\n"
        "[67]<label>美国</label>"
    )
    clean_state = (
        "<span>包含全部关键词</span>\n"
        "[26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>\n"
        "<span>期望城市：</span>\n"
        "[65]<span>不限</span>"
    )
    result_state = (
        "王** 男 34岁 工作12年 硕士 上海\n求职期望：上海 AI技术负责人\n某科技公司 · AI技术负责人 2021.01-至今"
    )
    commands = FakeCommands(
        outputs={
            **_current_window_open_outputs(page_id="page-1"),
            ("opencli", "browser", "seektalent-liepin", "state"): [
                dirty_state,
                clean_state,
                clean_state,
                result_state,
            ],
            ("opencli", "browser", "seektalent-liepin", "click", "54"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "AI 技术负责人"): '{"filled":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "搜 索"): (
                '{"clicked":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "1"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "3"): "{}",
        }
    )

    envelope = _runner(commands, lease_dir=tmp_path).search_liepin_cards(
        source_run_id="run-1",
        query="AI 技术负责人",
        max_pages=1,
        max_cards=10,
    )

    assert envelope["status"] == "succeeded"
    clear_index = commands.calls.index(("opencli", "browser", "seektalent-liepin", "click", "54"))
    fill_index = commands.calls.index(("opencli", "browser", "seektalent-liepin", "fill", "26", "AI 技术负责人"))
    assert clear_index < fill_index
    trace = json.loads((tmp_path / "protected" / "pi-trace" / "run-1" / "action-trace.json").read_text())
    assert {"action_kind": "clear_native_filters", "route_kind": "search", "ok": True} in trace["events"]


def test_search_liepin_cards_does_not_clear_again_for_same_workflow_and_filters(tmp_path: Path) -> None:
    dirty_state_1 = (
        "[26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>\n"
        "[54]<span />\n"
        "  清空筛选条件"
    )
    clean_state_1 = (
        "[26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>"
    )
    result_state_1 = (
        "王** 男 34岁 工作12年 硕士 上海\n求职期望：上海 AI技术负责人\n某科技公司 · AI技术负责人 2021.01-至今"
    )
    dirty_state_2 = (
        "[26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>\n"
        "[154]<span />\n"
        "  清空筛选条件"
    )
    clean_state_2 = clean_state_1
    result_state_2 = (
        "张** 男 36岁 工作14年 硕士 上海\n求职期望：上海 大模型负责人\n某智能公司 · 大模型负责人 2020.01-至今"
    )
    commands = FakeCommands(
        outputs={
            **_current_window_open_outputs(page_id="page-1"),
            ("opencli", "browser", "seektalent-liepin", "state"): [
                dirty_state_1,
                clean_state_1,
                clean_state_1,
                result_state_1,
                dirty_state_2,
                clean_state_2,
                clean_state_2,
                result_state_2,
            ],
            ("opencli", "browser", "seektalent-liepin", "click", "54"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "154"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "AI 技术负责人"): '{"filled":true}',
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "大模型负责人"): '{"filled":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "搜 索"): (
                '{"clicked":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "1"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "3"): "{}",
        }
    )
    runner = _runner(commands, lease_dir=tmp_path)

    first = runner.search_liepin_cards(
        source_run_id="run_abc-source-0-liepin-round-1-lane-1-target-1",
        query="AI 技术负责人",
        max_pages=1,
        max_cards=10,
    )
    second = runner.search_liepin_cards(
        source_run_id="run_abc-source-0-liepin-round-2-lane-1-target-1",
        query="大模型负责人",
        max_pages=1,
        max_cards=10,
    )

    assert first["status"] == "succeeded"
    assert second["status"] == "succeeded"
    assert ("opencli", "browser", "seektalent-liepin", "click", "54") in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "click", "154") not in commands.calls


def test_search_liepin_cards_clicks_filters_in_named_sections(tmp_path: Path) -> None:
    state_before = (
        "[26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>"
    )
    state_after_search = """
id=resultList
[10]<label>目前城市：</label>
[11]<label>北京</label>
[20]<label>期望城市：</label>
[21]<label>北京</label>
[30]<label>教育经历：</label>
[31]<label>本科</label>
[40]<label>统招要求：</label>
[126]<div />
  [122]<div />
    [121]<span title=统招/非统招（不限）>统招/非统招（不限）</span>
[50]<label>院校要求：</label>
[51]<label>211</label>
[52]<label>985</label>
"""
    state_recruitment_menu = """
[0]<div>已选 期望城市北京 本科</div>
[10]<label>目前城市：</label>
[11]<label>北京</label>
[20]<label>期望城市：</label>
[21]<label>北京</label>
[30]<label>教育经历：</label>
[31]<label>本科</label>
[40]<label>统招要求：</label>
[126]<div />
  [122]<div />
    [121]<span title=统招/非统招（不限）>统招/非统招（不限）</span>
[42]<label>统招本科</label>
[50]<label>院校要求：</label>
[51]<label>211</label>
[52]<label>985</label>
"""
    state_after_expected_city = f"已选 期望城市北京\n{state_after_search}"
    state_after_degree = f"已选 期望城市北京 本科\n{state_after_search}"
    state_after_recruitment = f"已选 期望城市北京 本科 统招\n{state_after_search}"
    state_after_school_211 = f"已选 期望城市北京 本科 统招 211\n{state_after_search}"
    state_after_filters = (
        "已选 期望城市北京 本科 统招 211 985\n"
        "王** 男 34岁 工作5年 本科 北京\n"
        "求职期望：北京 数据开发专家\n"
        "某数据公司 · 数据开发专家 2021.01-至今"
    )
    commands = FakeCommands(
        outputs={
            **_current_window_open_outputs(page_id="page-1"),
            ("opencli", "browser", "seektalent-liepin", "state"): [
                state_before,
                state_after_search,
                state_after_expected_city,
                state_after_degree,
                state_recruitment_menu,
                state_after_recruitment,
                state_after_school_211,
                state_after_filters,
            ],
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发 ETL"): '{"filled":true}',
            (
                "opencli",
                "browser",
                "seektalent-liepin",
                "click",
                "--role",
                "button",
                "--name",
                "搜 索",
            ): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "21"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "31"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "121"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "统招要求"): (
                '{"clicked":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "click", "42"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "51"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "52"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "1"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "3"): "{}",
        }
    )

    result = _runner(commands, lease_dir=tmp_path).search_liepin_cards(
        source_run_id="source-1",
        query="数据开发 ETL",
        max_pages=1,
        max_cards=10,
        native_filters={
            "city": {"section": "expected", "label": "北京"},
            "degree": {"section": "education", "label": "本科"},
            "recruitmentType": {"section": "recruitment_type", "label": "统招本科"},
            "schoolTypes": [
                {"section": "school_type", "label": "211"},
                {"section": "school_type", "label": "985"},
            ],
        },
    )

    assert result["status"] == "succeeded"
    trace = json.loads((tmp_path / "protected" / "pi-trace" / "source-1" / "action-trace.json").read_text())
    filter_events = [
        (event.get("filter"), event.get("section"), event.get("value"))
        for event in trace["events"]
        if event.get("action_kind") == "apply_native_filter"
    ]
    assert filter_events == [
        ("city", "expected", "北京"),
        ("degree", "education", "本科"),
        ("recruitmentType", "recruitment_type", "统招本科"),
        ("schoolTypes", "school_type", "211"),
        ("schoolTypes", "school_type", "985"),
    ]
    assert any(
        event.get("action_kind") == "open_native_filter_menu"
        and event.get("filter") == "recruitmentType"
        and event.get("section") == "recruitment_type"
        for event in trace["events"]
    )


def test_search_liepin_cards_does_not_retry_school_type_toggle_after_unverified_click(tmp_path: Path) -> None:
    state_before = (
        "[26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>"
    )
    state_after_search = """
[50]<label>院校要求：</label>
[52]<label>985</label>
王** 男 34岁 工作5年 本科 北京
"""
    state_after_school_click = """
[50]<label>院校要求：</label>
[52]<label>985</label>
王** 男 34岁 工作5年 本科 北京
求职期望：北京 数据开发专家
某数据公司 · 数据开发专家 2021.01-至今
"""
    commands = FakeCommands(
        outputs={
            **_current_window_open_outputs(page_id="page-1"),
            ("opencli", "browser", "seektalent-liepin", "state"): [
                state_before,
                state_before,
                state_after_search,
                state_after_school_click,
                state_after_school_click,
                state_after_school_click,
                state_after_school_click,
                state_after_school_click,
                state_after_school_click,
            ],
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发 ETL"): '{"filled":true}',
            (
                "opencli",
                "browser",
                "seektalent-liepin",
                "click",
                "--role",
                "button",
                "--name",
                "搜 索",
            ): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "52"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "1"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "2"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "3"): "{}",
        }
    )

    result = _runner(commands, lease_dir=tmp_path).search_liepin_cards(
        source_run_id="source-1",
        query="数据开发 ETL",
        max_pages=1,
        max_cards=10,
        native_filters={
            "schoolTypes": [
                {"section": "school_type", "label": "985"},
            ],
            "optionalFilterNames": ["schoolTypes"],
        },
    )

    assert result["status"] == "succeeded"
    assert commands.calls.count(("opencli", "browser", "seektalent-liepin", "click", "52")) == 1
    trace = json.loads((tmp_path / "protected" / "pi-trace" / "source-1" / "action-trace.json").read_text())
    assert {
        "action_kind": "observe_after_unverified_toggle_filter",
        "filter": "schoolTypes",
        "section": "school_type",
        "ok": True,
    } in trace["events"]
    assert not any(
        event.get("action_kind") == "apply_native_filter_retry" and event.get("filter") == "schoolTypes"
        for event in trace["events"]
    )


def test_search_liepin_cards_retries_school_type_when_click_command_fails(tmp_path: Path) -> None:
    state_before = (
        "[26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>"
    )
    state_after_search = """
[50]<label>院校要求：</label>
[52]<label>985</label>
王** 男 34岁 工作5年 本科 北京
"""
    state_retry = """
[50]<label>院校要求：</label>
[53]<label>985</label>
王** 男 34岁 工作5年 本科 北京
"""
    state_after_school_click = """
[50]<label>院校要求：</label>
[53]<label class=ant-checkbox-wrapper ant-checkbox-wrapper-checked>985</label>
王** 男 34岁 工作5年 本科 北京
求职期望：北京 数据开发专家
某数据公司 · 数据开发专家 2021.01-至今
"""
    commands = FakeCommands(
        outputs={
            **_current_window_open_outputs(page_id="page-1"),
            ("opencli", "browser", "seektalent-liepin", "state"): [
                state_before,
                state_before,
                state_after_search,
                state_retry,
                state_after_school_click,
            ],
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发 ETL"): '{"filled":true}',
            (
                "opencli",
                "browser",
                "seektalent-liepin",
                "click",
                "--role",
                "button",
                "--name",
                "搜 索",
            ): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "52"): subprocess.CalledProcessError(
                1, ["opencli"], stderr="stale ref"
            ),
            ("opencli", "browser", "seektalent-liepin", "click", "53"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "1"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "2"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "3"): "{}",
        }
    )

    result = _runner(commands, lease_dir=tmp_path).search_liepin_cards(
        source_run_id="source-1",
        query="数据开发 ETL",
        max_pages=1,
        max_cards=10,
        native_filters={
            "schoolTypes": [
                {"section": "school_type", "label": "985"},
            ],
            "requiredFilterNames": ["schoolTypes"],
        },
    )

    assert result["status"] == "succeeded"
    assert commands.calls.count(("opencli", "browser", "seektalent-liepin", "click", "52")) == 1
    assert commands.calls.count(("opencli", "browser", "seektalent-liepin", "click", "53")) == 1
    trace = json.loads((tmp_path / "protected" / "pi-trace" / "source-1" / "action-trace.json").read_text())
    assert any(
        event.get("action_kind") == "apply_native_filter_retry"
        and event.get("filter") == "schoolTypes"
        and event.get("section") == "school_type"
        and event.get("value") == "985"
        for event in trace["events"]
    )


def test_search_liepin_cards_blocks_when_required_native_filter_click_fails(tmp_path: Path) -> None:
    state_before = (
        "[26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>"
    )
    state_after_search = "[41]<button><span>城市</span></button>\n王** 男 34岁 工作5年 硕士 上海"
    state_city_menu = "[41]<button><span>城市</span></button>\n[44]<label>上海</label>"
    commands = FakeCommands(
        outputs={
            **_current_window_open_outputs(page_id="page-1"),
            ("opencli", "browser", "seektalent-liepin", "state"): [
                state_before,
                state_after_search,
                state_city_menu,
                state_city_menu,
                state_city_menu,
            ],
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发专家"): '{"filled":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "搜 索"): (
                '{"clicked":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "城市"): (
                '{"clicked":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "click", "44"): subprocess.CalledProcessError(1, ["opencli"]),
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "1"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "2"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "3"): "{}",
        }
    )

    envelope = _runner(commands, lease_dir=tmp_path).search_liepin_cards(
        source_run_id="run-1",
        query="数据开发专家",
        max_pages=1,
        max_cards=10,
        native_filters={"city": "上海"},
    )

    assert envelope["status"] == "blocked"
    assert envelope["safe_reason_code"] == "liepin_opencli_filter_unapplied"
    assert envelope["cards"] == []
    trace = json.loads((tmp_path / "protected" / "pi-trace" / "run-1" / "action-trace.json").read_text())
    assert {
        "action_kind": "apply_native_filter",
        "filter": "city",
        "section": "legacy",
        "value": "上海",
        "ok": False,
        "safe_reason_code": "liepin_opencli_filter_unapplied",
        "blocking": True,
    } in trace["events"]


def test_search_liepin_cards_accepts_selected_filter_chip_state(tmp_path: Path) -> None:
    state_before = (
        "[26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>"
    )
    state_after_search = """
[20]<label>期望城市：</label>
[21]<label>北京</label>
王** 男 34岁 工作5年 硕士 北京
"""
    state_after_expected_city = """
[20]<label>期望城市：</label>
[21]<label>北京</label>
[50]<label title=期望城市 />
  <span>北京</span>
  [51]<span role=img tabindex=-1 />
王** 男 34岁 工作5年 硕士 北京
求职期望：北京 数据开发专家
某数据公司 · 数据开发专家 2021.01-至今
"""
    commands = FakeCommands(
        outputs={
            **_current_window_open_outputs(page_id="page-1"),
            ("opencli", "browser", "seektalent-liepin", "state"): [
                state_before,
                state_after_search,
                state_after_expected_city,
            ],
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发专家"): '{"filled":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "搜 索"): (
                '{"clicked":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "click", "21"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "1"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "3"): "{}",
        }
    )

    envelope = _runner(commands, lease_dir=tmp_path).search_liepin_cards(
        source_run_id="run-1",
        query="数据开发专家",
        max_pages=1,
        max_cards=10,
        native_filters={"city": {"section": "expected", "label": "北京"}},
    )

    assert envelope["status"] == "succeeded"
    trace = json.loads((tmp_path / "protected" / "pi-trace" / "run-1" / "action-trace.json").read_text())
    assert {
        "action_kind": "apply_native_filter",
        "filter": "city",
        "section": "expected",
        "value": "北京",
        "ok": True,
    } in trace["events"]


def test_search_liepin_cards_blocks_when_filter_click_does_not_apply_selection(tmp_path: Path) -> None:
    state_before = (
        "[26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>"
    )
    state_after_search = "[41]<button><span>城市</span></button>\n王** 男 34岁 工作5年 硕士 上海"
    state_city_menu = "[41]<button><span>城市</span></button>\n[44]<label>上海</label>"
    state_after_bad_click = "[41]<button><span>城市</span></button>\n王** 男 34岁 工作5年 硕士 北京"
    commands = FakeCommands(
        outputs={
            **_current_window_open_outputs(page_id="page-1"),
            ("opencli", "browser", "seektalent-liepin", "state"): [
                state_before,
                state_after_search,
                state_city_menu,
                state_after_bad_click,
            ],
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发专家"): '{"filled":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "搜 索"): (
                '{"clicked":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "城市"): (
                '{"clicked":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "click", "44"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "1"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "2"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "3"): "{}",
        }
    )

    envelope = _runner(commands, lease_dir=tmp_path).search_liepin_cards(
        source_run_id="run-1",
        query="数据开发专家",
        max_pages=1,
        max_cards=10,
        native_filters={"city": "上海"},
    )

    assert envelope["status"] == "blocked"
    assert envelope["safe_reason_code"] == "liepin_opencli_filter_unapplied"


def test_search_liepin_cards_retries_unconfirmed_filter_before_blocking(tmp_path: Path) -> None:
    state_before = (
        "[26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>"
    )
    state_after_search = """
[20]<label>期望城市：</label>
[21]<label>北京</label>
王** 男 34岁 工作5年 硕士 北京
"""
    state_after_bad_click = """
[20]<label>期望城市：</label>
王** 男 34岁 工作5年 硕士 上海
"""
    state_after_delayed_chip = """
[20]<label>期望城市：</label>
[21]<label>北京</label>
[50]<label title=期望城市 />
  <span>北京</span>
王** 男 34岁 工作5年 硕士 北京
求职期望：北京 数据开发专家
某数据公司 · 数据开发专家 2021.01-至今
"""
    commands = FakeCommands(
        outputs={
            **_current_window_open_outputs(page_id="page-1"),
            ("opencli", "browser", "seektalent-liepin", "state"): [
                state_before,
                state_before,
                state_after_search,
                state_after_search,
                state_after_bad_click,
                state_after_delayed_chip,
                state_after_search,
                state_after_delayed_chip,
                state_after_delayed_chip,
            ],
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发专家"): '{"filled":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "搜 索"): (
                '{"clicked":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "click", "21"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "1"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "2"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "3"): "{}",
        }
    )

    envelope = _runner(commands, lease_dir=tmp_path).search_liepin_cards(
        source_run_id="run-1",
        query="数据开发专家",
        max_pages=1,
        max_cards=10,
        native_filters={"city": {"section": "expected", "label": "北京"}},
    )

    assert envelope["status"] == "succeeded"
    trace = json.loads((tmp_path / "protected" / "pi-trace" / "run-1" / "action-trace.json").read_text())
    assert any(
        event.get("action_kind") == "apply_native_filter_retry"
        and event.get("safe_reason_code") == "liepin_opencli_filter_unapplied"
        for event in trace["events"]
    )
    assert any(
        event.get("action_kind") == "verify_native_filter"
        and event.get("already_applied") is True
        and event.get("ok") is True
        for event in trace["events"]
    )


def test_search_liepin_cards_skips_optional_filter_after_retries(tmp_path: Path) -> None:
    state_before = (
        "[26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>"
    )
    state_after_search = (
        "[30]<label>教育经历：</label>\n"
        "[31]<label>本科</label>\n"
        "王** 男 34岁 工作5年 硕士 北京\n"
        "求职期望：北京 数据开发专家\n"
        "某数据公司 · 数据开发专家 2021.01-至今"
    )
    state_after_bad_click = (
        "[30]<label>教育经历：</label>\n"
        "[31]<label>本科</label>\n"
        "王** 男 34岁 工作5年 硕士 北京\n"
        "求职期望：北京 数据开发专家\n"
        "某数据公司 · 数据开发专家 2021.01-至今"
    )
    commands = FakeCommands(
        outputs={
            **_current_window_open_outputs(page_id="page-1"),
            ("opencli", "browser", "seektalent-liepin", "state"): [
                state_before,
                state_after_search,
                state_after_bad_click,
                state_after_bad_click,
                state_after_bad_click,
                state_after_bad_click,
                state_after_bad_click,
                state_after_bad_click,
                state_after_bad_click,
            ],
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发专家"): '{"filled":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "搜 索"): (
                '{"clicked":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "click", "31"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "1"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "2"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "3"): "{}",
        }
    )

    envelope = _runner(commands, lease_dir=tmp_path).search_liepin_cards(
        source_run_id="run-1",
        query="数据开发专家",
        max_pages=1,
        max_cards=10,
        native_filters={
            "degree": {"section": "education", "label": "本科"},
            "optionalFilterNames": ["degree"],
            "sourceTarget": {"phase": "balanced", "batchNo": 1, "requestedCount": 10},
        },
    )

    assert envelope["status"] == "succeeded"
    trace = json.loads((tmp_path / "protected" / "pi-trace" / "run-1" / "action-trace.json").read_text())
    assert {
        "action_kind": "apply_native_filter",
        "filter": "degree",
        "section": "education",
        "value": "本科",
        "ok": False,
        "safe_reason_code": "liepin_opencli_filter_unapplied",
        "blocking": False,
    } in trace["events"]
    assert {
        "action_kind": "skip_native_filter",
        "filter": "degree",
        "ok": True,
        "safe_reason_code": "liepin_opencli_filter_unapplied",
    } in trace["events"]


def test_search_liepin_cards_retries_transient_native_filter_status(tmp_path: Path) -> None:
    state_before = (
        "[26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>"
    )
    state_after_search = "[41]<button><span>城市</span></button>\n王** 男 34岁 工作5年 硕士 上海"
    state_city_menu = "[41]<button><span>城市</span></button>\n[44]<label>上海</label>"
    state_after_filter = "已选 上海\n王** 男 34岁 工作5年 硕士 上海\n求职期望：上海 数据开发专家"
    commands = FakeCommands(
        outputs={
            **_current_window_open_outputs(page_id="page-1"),
            ("opencli", "browser", "seektalent-liepin", "state"): [
                state_before,
                state_after_search,
                state_city_menu,
                state_city_menu,
                state_after_filter,
            ],
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发专家"): '{"filled":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "搜 索"): (
                '{"clicked":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "城市"): (
                '{"clicked":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "click", "44"): [
                subprocess.CalledProcessError(1, ["opencli"]),
                '{"clicked":true}',
            ],
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "1"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "2"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "3"): "{}",
        }
    )

    envelope = _runner(commands, lease_dir=tmp_path).search_liepin_cards(
        source_run_id="run-1",
        query="数据开发专家",
        max_pages=1,
        max_cards=10,
        native_filters={"city": "上海"},
    )

    assert envelope["status"] == "succeeded"
    trace = json.loads((tmp_path / "protected" / "pi-trace" / "run-1" / "action-trace.json").read_text())
    assert {
        "action_kind": "apply_native_filter_retry",
        "filter": "city",
        "section": "legacy",
        "value": "上海",
        "safe_reason_code": "liepin_opencli_status_unavailable",
    } in trace["events"]
    assert {
        "action_kind": "apply_native_filter",
        "filter": "city",
        "section": "legacy",
        "value": "上海",
        "ok": True,
    } in trace["events"]


def test_search_liepin_cards_waits_for_results_after_native_filter_refresh_before_probe(tmp_path: Path) -> None:
    class ReadyAwareCommands(FakeCommands):
        def __init__(self, **kwargs) -> None:
            super().__init__(**kwargs)
            self.ready_seen = False

        def run(self, argv: Sequence[str], *, timeout: int, env: Mapping[str, str] | None = None) -> str:
            call = tuple(argv)
            if len(call) >= 5 and call[3] == "eval" and "seektalent.liepin_structured_cards_probe.v1" in call[4]:
                self.calls.append(call)
                self.envs.append(env)
                if self.ready_seen:
                    return _structured_cards_probe_json("70")
                return _empty_structured_cards_probe_json()
            if call == ("opencli", "browser", "seektalent-liepin", "wait", "selector", "#resultList"):
                self.ready_seen = True
            output = super().run(argv, timeout=timeout, env=env)
            if call == ("opencli", "browser", "seektalent-liepin", "state") and self.ready_seen:
                return state_after_cards
            return output

    state_before = (
        "[26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>"
    )
    state_after_search = "id=resultList\n[41]<button><span>城市</span></button>"
    state_city_menu = "[41]<button><span>城市</span></button>\n[44]<label>北京</label>"
    state_after_filter = "已选 北京\n正在加载\n"
    state_after_cards = "id=resultList\n共 1 位人选\n王** 男 40岁 工作14年 硕士 北京\n求职期望：北京 数据开发专家"
    commands = ReadyAwareCommands(
        outputs={
            **_current_window_open_outputs(page_id="page-1"),
            ("opencli", "browser", "seektalent-liepin", "state"): [
                state_before,
                state_after_search,
                state_city_menu,
                state_after_filter,
            ],
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发专家"): '{"filled":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "29"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "搜 索"): (
                '{"clicked":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "城市"): (
                '{"clicked":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "click", "44"): '{"clicked":true}',
            ("opencli", "browser", "seektalent-liepin", "wait", "text", "北京"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "selector", "#resultList"): "{}",
        }
    )

    envelope = _runner(commands, lease_dir=tmp_path).search_liepin_cards(
        source_run_id="run-1",
        query="数据开发专家",
        max_pages=1,
        max_cards=10,
        native_filters={"city": "北京"},
    )

    assert envelope["status"] == "succeeded"
    assert envelope["cards_returned"] == 1
    wait_selector_call = ("opencli", "browser", "seektalent-liepin", "wait", "selector", "#resultList")
    assert wait_selector_call in commands.calls
    wait_selector_index = commands.calls.index(wait_selector_call)
    structured_probe_index = next(
        index
        for index, call in enumerate(commands.calls)
        if len(call) >= 5 and call[3] == "eval" and "seektalent.liepin_structured_cards_probe.v1" in call[4]
    )
    assert wait_selector_index < structured_probe_index
    trace = json.loads((tmp_path / "protected" / "pi-trace" / "run-1" / "action-trace.json").read_text())
    assert any(
        event.get("action_kind") == "observe_results_after_native_filters"
        and event.get("ready") is False
        and event.get("ok") is True
        for event in trace["events"]
    )


def test_extract_liepin_card_summaries_strips_opencli_accessibility_markup() -> None:
    text = (
        "[247]<span title=智能排序>智能排序</span>\n"
        "[251]<span />\n"
        "[250]<span role=img aria-label=down />\n"
        "[249]<svg /> <div /> table 今天活跃周**25岁工作4年本科常州\n"
        "求职期望：杭州 数据分析师\n"
        "中创新航技术研究院(江苏)有限公司 · 大数据开发工程师2022.08-至今(3年9个月)\n"
        "沈阳工业大学 · 本科"
    )

    cards = extract_liepin_card_summaries(text, max_cards=10)

    assert len(cards) == 1
    summary = cards[0]
    serialized = json.dumps(summary, ensure_ascii=False)
    assert "normalized_card_text" not in summary
    assert "<" not in serialized
    assert "role=" not in serialized
    assert "aria-label" not in serialized
    assert {"span", "svg", "div", "table"}.isdisjoint(set(summary["skill_tags"]))


def test_search_liepin_cards_returns_blocked_envelope_when_state_is_terminal(tmp_path: Path) -> None:
    commands = FakeCommands(
        outputs={
            **_current_window_open_outputs(page_id="page-1"),
            ("opencli", "browser", "seektalent-liepin", "state"): "安全验证 请完成验证码",
        }
    )

    envelope = _runner(commands, lease_dir=tmp_path).search_liepin_cards(
        source_run_id="run-1",
        query="数据开发专家",
        max_pages=1,
        max_cards=10,
    )

    assert envelope["status"] == "blocked"
    assert envelope["safe_reason_code"] == "liepin_opencli_risk_page"
    assert envelope["cards"] == []
    assert (tmp_path / "protected" / "pi-trace" / "run-1" / "action-trace.json").is_file()


def test_search_liepin_cards_retries_stale_search_input_ref(tmp_path: Path) -> None:
    search_state = (
        "URL: https://h.liepin.com/search/getConditionItem#session\n"
        "<span>包含全部关键词</span>\n"
        "  [25]<div />\n"
        "    [26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>"
    )
    retry_state = search_state.replace("[26]", "[41]")
    result_state = (
        "王** 男 40岁 工作14年 硕士 上海\n求职期望：上海 数据开发专家\n海光集成电路 · 高级主管工程师 2023.10-至今"
    )
    commands = FakeCommands(
        outputs={
            **_current_window_open_outputs(page_id="page-1"),
            ("opencli", "browser", "seektalent-liepin", "state"): [search_state, retry_state, result_state],
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发专家"): [
                subprocess.CalledProcessError(1, ["opencli"], stderr="stale ref"),
            ],
            ("opencli", "browser", "seektalent-liepin", "fill", "41", "数据开发专家"): '{"filled":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "搜 索"): (
                '{"clicked":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "3"): "{}",
        }
    )

    envelope = _runner(commands, lease_dir=tmp_path).search_liepin_cards(
        source_run_id="run-1",
        query="数据开发专家",
        max_pages=1,
        max_cards=10,
    )

    assert envelope["status"] == "succeeded"
    assert ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发专家") in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "fill", "41", "数据开发专家") in commands.calls
    trace = json.loads((tmp_path / "protected" / "pi-trace" / "run-1" / "action-trace.json").read_text())
    assert {"action_kind": "fill_search_retry", "route_kind": "search", "chars": 6} in trace["events"]


def test_search_liepin_cards_retries_structured_stale_search_input_ref(tmp_path: Path) -> None:
    search_state = (
        "URL: https://h.liepin.com/search/getConditionItem#session\n"
        "<span>包含全部关键词</span>\n"
        "  [25]<div />\n"
        "    [26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>"
    )
    retry_state = search_state.replace("[26]", "[41]")
    result_state = (
        "王** 男 40岁 工作14年 硕士 上海\n求职期望：上海 数据开发专家\n海光集成电路 · 高级主管工程师 2023.10-至今"
    )
    commands = FakeCommands(
        outputs={
            **_current_window_open_outputs(page_id="page-1"),
            ("opencli", "browser", "seektalent-liepin", "state"): [search_state, retry_state, result_state],
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发专家"): [
                subprocess.CalledProcessError(
                    1,
                    ["opencli"],
                    output='{"error":{"code":"stale_ref","message":"target disappeared","hint":"refresh state"}}',
                    stderr="",
                ),
            ],
            ("opencli", "browser", "seektalent-liepin", "fill", "41", "数据开发专家"): '{"filled":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "搜 索"): (
                '{"clicked":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "3"): "{}",
        }
    )

    envelope = _runner(commands, lease_dir=tmp_path).search_liepin_cards(
        source_run_id="run-1",
        query="数据开发专家",
        max_pages=1,
        max_cards=10,
    )

    assert envelope["status"] == "succeeded"
    assert ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发专家") in commands.calls
    assert ("opencli", "browser", "seektalent-liepin", "fill", "41", "数据开发专家") in commands.calls
    trace = json.loads((tmp_path / "protected" / "pi-trace" / "run-1" / "action-trace.json").read_text())
    assert {
        "action_kind": "fill_search_retry",
        "route_kind": "search",
        "chars": 6,
        "safe_reason_code": "liepin_opencli_stale_ref",
    } in trace["events"]


def test_search_liepin_cards_retries_stale_search_button_ref(tmp_path: Path) -> None:
    search_state = (
        "URL: https://h.liepin.com/search/getConditionItem#session\n"
        "<span>包含全部关键词</span>\n"
        "  [25]<div />\n"
        "    [26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>"
    )
    result_state = (
        "王** 男 40岁 工作14年 硕士 上海\n求职期望：上海 数据开发专家\n海光集成电路 · 高级主管工程师 2023.10-至今"
    )
    commands = FakeCommands(
        outputs={
            **_current_window_open_outputs(page_id="page-1"),
            ("opencli", "browser", "seektalent-liepin", "state"): [
                search_state,
                search_state,
                search_state,
                result_state,
            ],
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发专家"): '{"filled":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "29"): [
                subprocess.CalledProcessError(
                    1,
                    ["opencli"],
                    output='{"error":{"code":"stale_ref","message":"target disappeared","hint":"refresh state"}}',
                    stderr="",
                ),
                '{"clicked":true}',
            ],
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "2"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "3"): "{}",
        }
    )

    envelope = _runner(commands, lease_dir=tmp_path).search_liepin_cards(
        source_run_id="run-1",
        query="数据开发专家",
        max_pages=1,
        max_cards=10,
    )

    assert envelope["status"] == "succeeded"
    assert commands.calls.count(("opencli", "browser", "seektalent-liepin", "click", "29")) == 2
    trace = json.loads((tmp_path / "protected" / "pi-trace" / "run-1" / "action-trace.json").read_text())
    assert {
        "action_kind": "click_search_retry",
        "route_kind": "search",
        "safe_reason_code": "liepin_opencli_stale_ref",
    } in trace["events"]


def test_search_liepin_cards_retries_repeated_transient_fill_status(tmp_path: Path) -> None:
    search_state = (
        "URL: https://h.liepin.com/search/getConditionItem#session\n"
        "<span>包含全部关键词</span>\n"
        "  [25]<div />\n"
        "    [26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>"
    )
    result_state = (
        "王** 男 40岁 工作14年 硕士 上海\n求职期望：上海 数据开发专家\n海光集成电路 · 高级主管工程师 2023.10-至今"
    )
    commands = FakeCommands(
        outputs={
            **_current_window_open_outputs(page_id="page-1"),
            ("opencli", "browser", "seektalent-liepin", "state"): [
                search_state,
                search_state,
                search_state,
                result_state,
            ],
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发专家"): [
                subprocess.CalledProcessError(1, ["opencli"], stderr="status unavailable"),
                subprocess.CalledProcessError(1, ["opencli"], stderr="status unavailable"),
                '{"filled":true}',
            ],
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "搜 索"): (
                '{"clicked":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "2"): "{}",
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "3"): "{}",
        }
    )

    envelope = _runner(commands, lease_dir=tmp_path).search_liepin_cards(
        source_run_id="run-1",
        query="数据开发专家",
        max_pages=1,
        max_cards=10,
    )

    assert envelope["status"] == "succeeded"
    assert commands.calls.count(("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发专家")) == 3
    trace = json.loads((tmp_path / "protected" / "pi-trace" / "run-1" / "action-trace.json").read_text())
    assert [event["action_kind"] for event in trace["events"]].count("fill_search_retry") == 2


def test_search_liepin_cards_rechecks_transient_unready_state(tmp_path: Path) -> None:
    search_state = (
        "URL: https://h.liepin.com/search/getConditionItem#session\n"
        "<span>包含全部关键词</span>\n"
        "  [26]<input type=search autocomplete=off role=combobox id=rc_select_1 />\n"
        "[29]<button><span>搜 索</span></button>"
    )
    result_state = (
        "王** 男 40岁 工作14年 硕士 上海\n求职期望：上海 数据开发专家\n海光集成电路 · 高级主管工程师 2023.10-至今"
    )
    commands = FakeCommands(
        outputs={
            **_current_window_open_outputs(page_id="page-1"),
            ("opencli", "browser", "seektalent-liepin", "state"): ["安全验证 请稍候", search_state, result_state],
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "2"): "{}",
            ("opencli", "browser", "seektalent-liepin", "fill", "26", "数据开发专家"): '{"filled":true}',
            ("opencli", "browser", "seektalent-liepin", "click", "--role", "button", "--name", "搜 索"): (
                '{"clicked":true}'
            ),
            ("opencli", "browser", "seektalent-liepin", "wait", "time", "3"): "{}",
        }
    )

    envelope = _runner(commands, lease_dir=tmp_path).search_liepin_cards(
        source_run_id="run-1",
        query="数据开发专家",
        max_pages=1,
        max_cards=10,
    )

    assert envelope["status"] == "succeeded"
    trace = json.loads((tmp_path / "protected" / "pi-trace" / "run-1" / "action-trace.json").read_text())
    assert any(event["action_kind"] == "observe_retry_after_unready" for event in trace["events"])


def test_classify_liepin_state_does_not_treat_doris_as_risk_page() -> None:
    text = "求职期望：深圳大数据开发 Python SQL DorisKafka Spark Hadoop Hive"

    assert classify_liepin_state(url="https://h.liepin.com/search/getConditionItem#session", text=text) is None


def test_cli_rejects_unknown_action(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["opencli_browser_cli", "network"])
    monkeypatch.setattr("sys.stdin", io.StringIO("{}"))

    rc = opencli_browser_cli.main()

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["safeReasonCode"] == "liepin_opencli_forbidden_command"


def test_cli_state_returns_pi_observation(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands = FakeCommands(
        outputs={
            (
                "opencli",
                "browser",
                "seektalent-liepin",
                "get",
                "url",
            ): "https://h.liepin.com/search/getConditionItem#session",
            ("opencli", "browser", "seektalent-liepin", "state"): "搜索职位、公司 [ref=16]",
        }
    )
    monkeypatch.setattr("sys.argv", ["opencli_browser_cli", "state"])
    monkeypatch.setattr("sys.stdin", io.StringIO("{}"))
    monkeypatch.setattr(opencli_browser_cli, "_runner_from_env", lambda: _runner(commands))

    rc = opencli_browser_cli.main()

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["observation"]["text"] == "搜索职位、公司 [ref=16]"


@pytest.mark.parametrize(
    "removed_action",
    (
        "cleanup_" + "idle_lease",
        "cleanup_" + "orphaned_tabs",
        "watch_" + "idle_lease",
    ),
)
def test_cli_rejects_removed_cleanup_actions(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    removed_action: str,
) -> None:
    monkeypatch.setattr("sys.argv", ["opencli_browser_cli", removed_action])
    monkeypatch.setattr("sys.stdin", io.StringIO('{"force":true}'))
    monkeypatch.setattr(opencli_browser_cli, "_runner_from_env", lambda: _runner(FakeCommands()))

    rc = opencli_browser_cli.main()

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["action"] == removed_action
    assert payload["safeReasonCode"] == "liepin_opencli_forbidden_command"


@pytest.mark.parametrize(
    "removed_env_key",
    (
        "SEEKTALENT_LIEPIN_OPENCLI_IDLE_" + "CLOSE_SECONDS",
        "SEEKTALENT_LIEPIN_OPENCLI_CLOSE_" + "BLANK_WINDOW",
    ),
)
def test_cli_rejects_removed_cleanup_env_config(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    removed_env_key: str,
) -> None:
    monkeypatch.setenv(removed_env_key, "1")
    monkeypatch.setattr("sys.argv", ["opencli_browser_cli", "status"])
    monkeypatch.setattr("sys.stdin", io.StringIO("{}"))
    monkeypatch.setattr(
        opencli_browser_cli,
        "_run_action",
        lambda runner, action, payload: OpenCliBrowserResult(ok=True, action=action),
    )

    rc = opencli_browser_cli.main()

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["action"] == "status"
    assert payload["safeReasonCode"] == "liepin_opencli_removed_config"


def test_cli_search_cards_prints_strict_envelope(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("sys.argv", ["opencli_browser_cli", "search_cards"])
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO('{"sourceRunId":"run-1","query":"数据开发专家","maxPages":1,"maxCards":10}'),
    )
    monkeypatch.setattr(
        opencli_browser_cli,
        "_runner_from_env",
        lambda: _runner(FakeCommands(fail=True), lease_dir=tmp_path),
    )

    rc = opencli_browser_cli.main()

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == "seektalent.pi_liepin_cards.v1"
    assert payload["status"] == "blocked"
    assert payload["safe_reason_code"] == "liepin_opencli_timeout"


def test_cli_runner_uses_shell_safe_command_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEEKTALENT_LIEPIN_OPENCLI_COMMAND", '"/tmp/open cli" --profile "qa user"')

    runner = opencli_browser_cli._runner_from_env()

    assert runner._browser_config.command == ("/tmp/open cli", "--profile", "qa user")


def test_cli_runner_from_env_uses_default_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SEEKTALENT_LIEPIN_OPENCLI_ALLOWED_HOSTS_JSON", raising=False)
    monkeypatch.delenv("SEEKTALENT_LIEPIN_OPENCLI_ALLOWED_START_URLS_JSON", raising=False)

    runner = opencli_browser_cli._runner_from_env()

    assert runner._site_config.allowed_hosts == LIEPIN_OPENCLI_ALLOWED_HOSTS
    assert runner._site_config.allowed_start_urls == LIEPIN_RECRUITER_SEARCH_URLS


def test_cli_runner_reads_state_derived_click_refs_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEEKTALENT_LIEPIN_OPENCLI_ALLOWED_CLICK_REFS_JSON", '["16","next"]')

    runner = opencli_browser_cli._runner_from_env()

    assert runner._site_config.allowed_click_refs == ("16", "next")


def test_cli_runner_reads_lease_dir_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SEEKTALENT_LIEPIN_OPENCLI_LEASE_DIR", str(tmp_path))

    runner = opencli_browser_cli._runner_from_env()

    assert runner._site_config.lease_dir == tmp_path


def test_cli_runner_from_env_wires_prod_timing_policy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SEEKTALENT_LIEPIN_OPENCLI_COMMAND", "opencli")
    monkeypatch.setenv("SEEKTALENT_RUNTIME_ARTIFACT_OUTPUT_MODE", "prod")
    monkeypatch.setenv("SEEKTALENT_PI_ARTIFACT_ROOT", str(tmp_path))
    commands = FakeCommands(
        outputs={("opencli", "browser", "seektalent-liepin", "fill", "26", "敏感关键词"): '{"filled":true}'}
    )
    runner = opencli_browser_cli._runner_from_env()
    runner._automation.commands = commands

    result = runner.fill(target="26", text="敏感关键词")

    assert result.ok is True
    assert not (tmp_path / "protected" / "opencli-timing").exists()


def test_cli_runner_from_env_wires_dev_timing_policy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SEEKTALENT_LIEPIN_OPENCLI_COMMAND", "opencli")
    monkeypatch.setenv("SEEKTALENT_RUNTIME_ARTIFACT_OUTPUT_MODE", "dev")
    monkeypatch.setenv("SEEKTALENT_PI_ARTIFACT_ROOT", str(tmp_path))
    commands = FakeCommands(
        outputs={("opencli", "browser", "seektalent-liepin", "fill", "26", "敏感关键词"): '{"filled":true}'}
    )
    runner = opencli_browser_cli._runner_from_env()
    runner._automation.commands = commands

    result = runner.fill(target="26", text="敏感关键词")

    assert result.ok is True
    raw_log = next((tmp_path / "protected" / "opencli-timing").glob("*.jsonl")).read_text(encoding="utf-8")
    assert "browser.fill" in raw_log
    assert "敏感关键词" not in raw_log
