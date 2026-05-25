from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import inspect
import re
from typing import Any

from seektalent.config import AppSettings
from seektalent.locations import normalize_locations
from seektalent.models import RequirementExtractionDraft
from seektalent.prompting import PromptRegistry
from seektalent.progress import ProgressCallback
from seektalent.providers.liepin.client import LiepinWorkerClient
from seektalent.providers.liepin.worker_contracts import LiepinWorkerModeError
from seektalent.requirements import build_input_truth
from seektalent.requirements.extractor import requirement_cache_key
from seektalent.runtime.exact_llm_cache import put_cached_json
from seektalent.runtime.source_lanes import RuntimeApprovedDetailLease, RuntimeSourceLaneRequest
from seektalent_ui.workbench_store import (
    LIEPIN_DAILY_DETAIL_OPEN_LIMIT,
    WorkbenchLiepinDetailOpenJobContext,
    WorkbenchRuntimeSourcingJobContext,
    WorkbenchSourceRunJobContext,
    WorkbenchStore,
)


RuntimeFactory = Callable[[AppSettings], object]


@dataclass(frozen=True)
class ExtractedRequirementTriage:
    must_haves: list[str]
    nice_to_haves: list[str]
    synonyms: list[str]
    seniority_filters: list[str]
    exclusions: list[str]
    generated_query_hints: list[str]


@dataclass(frozen=True)
class StructuredJdFilterDefaults:
    locations: list[str]
    degree_requirement: str | None
    school_type_requirement: list[str]
    experience_requirement: str | None
    age_requirement: str | None


def extract_requirement_triage(
    *,
    session,
    settings: AppSettings,
    runtime_factory: RuntimeFactory,
    progress_callback: ProgressCallback | None = None,
) -> ExtractedRequirementTriage:
    runtime = runtime_factory(settings)
    extractor = getattr(runtime, "extract_requirements", None)
    if extractor is None:
        raise RuntimeError("Runtime does not support requirement extraction.")
    extract_kwargs: dict[str, object] = {
        "job_title": session.job_title,
        "jd": session.jd_text,
        "notes": session.notes,
        "progress_callback": progress_callback,
    }
    if _callable_accepts_keyword(extractor, "requirement_cache_scope"):
        extract_kwargs["requirement_cache_scope"] = session.session_id
    requirement_sheet = extractor(**extract_kwargs)
    return _triage_from_requirement_sheet(requirement_sheet)


def run_cts_source_run(
    *,
    context: WorkbenchSourceRunJobContext,
    store: WorkbenchStore,
    settings: AppSettings,
    runtime_factory: RuntimeFactory,
    progress_callback: ProgressCallback | None = None,
) -> None:
    runtime = runtime_factory(settings)
    run_method = getattr(runtime, "run", None)
    if not callable(run_method):
        raise RuntimeError("Runtime does not support CTS source runs.")
    run_kwargs: dict[str, object] = {
        "job_title": context.session.job_title,
        "jd": context.session.jd_text,
        "notes": _notes_with_triage(context),
        "progress_callback": progress_callback,
    }
    if _runtime_run_accepts_start_callback(run_method):
        run_kwargs["runtime_start_callback"] = lambda run_id: store.attach_source_run_runtime_run_id(
            context=context,
            runtime_run_id=run_id,
        )
    if _callable_accepts_keyword(run_method, "requirement_cache_scope"):
        _seed_approved_requirement_cache(context=context, settings=settings, notes=str(run_kwargs["notes"]))
        run_kwargs["requirement_cache_scope"] = context.session.session_id
    artifacts = run_method(**run_kwargs)
    store.complete_cts_source_run_with_candidate_results(context=context, artifacts=artifacts)


def run_runtime_sourcing_job(
    *,
    context: WorkbenchRuntimeSourcingJobContext,
    store: WorkbenchStore,
    settings: AppSettings,
    runtime_factory: RuntimeFactory,
    progress_callback: ProgressCallback | None = None,
) -> None:
    runtime = runtime_factory(settings)
    run_method = getattr(runtime, "run", None)
    if not callable(run_method):
        raise RuntimeError("Runtime does not support Workbench sourcing jobs.")
    notes = _notes_with_triage(context)
    run_kwargs: dict[str, object] = {
        "job_title": context.session.job_title,
        "jd": context.session.jd_text,
        "notes": notes,
        "progress_callback": progress_callback,
        "source_kinds": context.job.source_kinds,
        "requirement_cache_scope": context.session.session_id,
    }
    if _runtime_run_accepts_start_callback(run_method):
        run_kwargs["runtime_start_callback"] = lambda run_id: store.attach_runtime_sourcing_job_runtime_run_id(
            context=context,
            runtime_run_id=run_id,
        )
    connection = store.get_liepin_source_connection_for_job_context(context=context)
    if connection is not None and "liepin" in context.job.source_kinds:
        run_kwargs["liepin_context"] = {
            "tenant_id": "local",
            "workspace_id": context.session.workspace_id,
            "actor_id": context.session.owner_user_id,
            "connection_id": connection.connection_id,
            "compliance_gate_ref": connection.compliance_gate_ref,
            "provider_account_hash": connection.provider_account_hash,
        }
        run_kwargs["liepin_posture"] = {
            "connection_status": connection.status,
            "auth_state": "connected" if connection.status == "connected" else "login_required",
        }
    if not _callable_accepts_keyword(run_method, "source_kinds"):
        run_kwargs.pop("source_kinds", None)
    if not _callable_accepts_keyword(run_method, "requirement_cache_scope"):
        run_kwargs.pop("requirement_cache_scope", None)
    else:
        _seed_approved_requirement_cache(context=context, settings=settings, notes=notes)
    if not _callable_accepts_keyword(run_method, "liepin_context"):
        run_kwargs.pop("liepin_context", None)
    if not _callable_accepts_keyword(run_method, "liepin_posture"):
        run_kwargs.pop("liepin_posture", None)
    artifacts = run_method(**run_kwargs)
    store.reconcile_runtime_public_events_from_artifacts(context=context, artifacts=artifacts)
    store.complete_runtime_sourcing_job_with_artifacts(context=context, artifacts=artifacts)


def run_liepin_card_source_run(
    *,
    context: WorkbenchSourceRunJobContext,
    store: WorkbenchStore,
    settings: AppSettings,
    runtime_factory: RuntimeFactory,
    worker_client: LiepinWorkerClient | None = None,
) -> None:
    connection = store.get_liepin_source_connection_for_job_context(context=context)
    if connection is None or connection.status != "connected" or connection.provider_account_hash is None:
        raise LiepinWorkerModeError("Liepin source run requires a connected source account.")
    runtime = runtime_factory(settings)
    run_source_lane = getattr(runtime, "run_source_lane", None)
    if not callable(run_source_lane):
        raise RuntimeError("Runtime does not support source lane runs.")
    result = run_source_lane(
        RuntimeSourceLaneRequest(
            source="liepin",
            lane_mode="card",
            job_title=context.session.job_title,
            jd=context.session.jd_text,
            notes=_notes_with_triage(context),
            runtime_run_id=context.job.job_id,
            source_plan_id=f"{context.job.job_id}:source:liepin",
            source_lane_run_id=f"{context.job.job_id}:lane:liepin:card",
            source_query_terms=tuple(_query_terms(context)),
            liepin_context={
                "tenant_id": "local",
                "workspace_id": context.session.workspace_id,
                "actor_id": context.session.owner_user_id,
                "connection_id": connection.connection_id,
                "compliance_gate_ref": connection.compliance_gate_ref,
                "provider_account_hash": connection.provider_account_hash,
            },
        ),
        liepin_worker_client=worker_client,
    )
    store.complete_liepin_card_source_run_with_lane_result(context=context, result=result)


def run_liepin_detail_open_intent(
    *,
    context: WorkbenchLiepinDetailOpenJobContext,
    store: WorkbenchStore,
    settings: AppSettings,
    runtime_factory: RuntimeFactory,
    worker_client: LiepinWorkerClient | None = None,
) -> None:
    runtime = runtime_factory(settings)
    run_source_lane = getattr(runtime, "run_source_lane", None)
    if not callable(run_source_lane):
        raise RuntimeError("Runtime does not support source lane runs.")
    runtime_run_id = context.runtime_run_id or f"{context.intent_id}:runtime"
    source_plan_id = f"{runtime_run_id}:source:liepin:detail"
    source_lane_run_id = f"{source_plan_id}:request:{context.request_id}"
    result = run_source_lane(
        RuntimeSourceLaneRequest(
            source="liepin",
            lane_mode="detail",
            job_title=context.session.job_title,
            jd=context.session.jd_text,
            notes=_notes_with_triage(context),
            runtime_run_id=runtime_run_id,
            source_plan_id=source_plan_id,
            source_lane_run_id=source_lane_run_id,
            source_query_terms=tuple(_query_terms(context)),
            approved_detail_lease=RuntimeApprovedDetailLease(
                lease_ref=f"lease://workbench/{context.ledger_id}",
                lease_id=context.ledger_id,
                runtime_run_id=runtime_run_id,
                source_plan_id=source_plan_id,
                source_lane_run_id=source_lane_run_id,
                source="liepin",
                source_evidence_id=context.candidate_evidence_id,
                request_id=context.request_id,
                ledger_id=context.ledger_id,
                candidate_evidence_id=context.candidate_evidence_id,
                candidate_resume_id=context.candidate_resume_id,
                provider_candidate_key_hash=context.provider_candidate_key_hash,
                connection_id=context.connection_id,
                compliance_gate_ref=context.compliance_gate_ref,
                provider_account_hash=context.provider_account_hash,
                detail_candidates_json=context.detail_candidates_json,
                daily_budget=LIEPIN_DAILY_DETAIL_OPEN_LIMIT,
                budget_date=context.budget_day,
                provider_day_key=f"liepin:{context.provider_account_hash}:{context.budget_day}",
                timezone="Asia/Shanghai",
                open_policy_version="detail-policy-v1",
                expires_at=context.lease_expires_at,
            ),
            liepin_context={
                "tenant_id": "local",
                "workspace_id": context.session.workspace_id,
                "actor_id": context.session.owner_user_id,
                "connection_id": context.connection_id,
                "compliance_gate_ref": context.compliance_gate_ref,
                "provider_account_hash": context.provider_account_hash,
            },
        ),
        liepin_worker_client=worker_client,
    )
    store.complete_liepin_detail_open_intent_with_lane_result(context=context, result=result)


def _notes_with_triage(
    context: WorkbenchSourceRunJobContext | WorkbenchRuntimeSourcingJobContext | WorkbenchLiepinDetailOpenJobContext,
) -> str:
    triage = context.triage
    sections = [
        context.session.notes.strip(),
        "Approved requirement triage:",
        f"must_haves: {_bounded_join(triage.must_haves)}",
        f"nice_to_haves: {_bounded_join(triage.nice_to_haves)}",
        f"synonyms: {_bounded_join(triage.synonyms)}",
        f"seniority_filters: {_bounded_join(triage.seniority_filters)}",
        f"exclusions: {_bounded_join(triage.exclusions)}",
        f"generated_query_hints: {_bounded_join(triage.generated_query_hints)}",
    ]
    return "\n".join(section for section in sections if section)


def _seed_approved_requirement_cache(
    *,
    context: WorkbenchSourceRunJobContext | WorkbenchRuntimeSourcingJobContext,
    settings: AppSettings,
    notes: str,
) -> None:
    input_truth = build_input_truth(job_title=context.session.job_title, jd=context.session.jd_text, notes=notes)
    prompt = PromptRegistry(settings.prompt_dir).load("requirements")
    key = requirement_cache_key(
        settings,
        prompt=prompt,
        input_truth=input_truth,
        cache_scope=context.session.session_id,
    )
    draft = _requirement_draft_from_approved_triage(context)
    put_cached_json(settings, namespace="requirements", key=key, payload=draft.model_dump(mode="json"))


def _requirement_draft_from_approved_triage(
    context: WorkbenchSourceRunJobContext | WorkbenchRuntimeSourcingJobContext,
) -> RequirementExtractionDraft:
    triage = context.triage
    structured_defaults = _structured_jd_filter_defaults(context.session.jd_text)
    title_anchor_terms = _title_anchor_terms(context.session.job_title)
    anchor_keys = {term.casefold() for term in title_anchor_terms}
    jd_query_terms = [
        term
        for term in _unique_bounded_strings(
            [
                *triage.generated_query_hints,
                *triage.must_haves,
                *triage.synonyms,
            ],
            max_items=8,
        )
        if term.casefold() not in anchor_keys
    ]
    if not jd_query_terms:
        jd_query_terms = [_fallback_non_anchor_term(context.session.job_title, anchor_keys=anchor_keys)]
    return RequirementExtractionDraft(
        role_title=context.session.job_title,
        title_anchor_terms=title_anchor_terms,
        title_anchor_rationale="来自已确认岗位标准的标题锚点。",
        jd_query_terms=jd_query_terms,
        notes_query_terms=_unique_bounded_strings(triage.synonyms, max_items=6),
        role_summary=_role_summary_from_triage(context),
        must_have_capabilities=_unique_bounded_strings(triage.must_haves, max_items=8),
        preferred_capabilities=_unique_bounded_strings([*triage.nice_to_haves, *triage.seniority_filters], max_items=8),
        exclusion_signals=_unique_bounded_strings(triage.exclusions, max_items=8),
        locations=structured_defaults.locations,
        degree_requirement=structured_defaults.degree_requirement,
        school_type_requirement=structured_defaults.school_type_requirement,
        experience_requirement=structured_defaults.experience_requirement,
        age_requirement=structured_defaults.age_requirement,
        preferred_query_terms=_unique_bounded_strings(triage.generated_query_hints, max_items=8),
        scoring_rationale="优先匹配已确认的硬性条件，再参考加分条件和排除项。",
    )


def _structured_jd_filter_defaults(jd_text: str) -> StructuredJdFilterDefaults:
    fields = _structured_jd_fields(jd_text)
    location_values: list[str] = []
    for label in ("工作城市", "招聘城市", "工作地点", "工作地", "办公地点", "所在城市", "城市"):
        for value in fields.get(label, []):
            location_values.extend(_city_values_from_structured_field(value))

    education_text = _first_structured_field(fields, ("学历要求", "学历", "教育背景"))
    experience_text = _first_structured_field(fields, ("工作年限", "工作经验", "经验要求", "年限要求"))
    age_text = _first_structured_field(fields, ("年龄要求", "年龄"))
    return StructuredJdFilterDefaults(
        locations=normalize_locations(location_values),
        degree_requirement=_degree_requirement_from_structured_field(education_text),
        school_type_requirement=_school_type_requirement_from_structured_field(education_text),
        experience_requirement=_constraint_text_from_structured_field(experience_text),
        age_requirement=_constraint_text_from_structured_field(age_text),
    )


def _structured_jd_fields(jd_text: str) -> dict[str, list[str]]:
    lines = [line.strip() for line in jd_text.splitlines()]
    fields: dict[str, list[str]] = {}
    for index, line in enumerate(lines):
        match = re.match(r"^([A-Za-z0-9\u4e00-\u9fff/·（）() ]{1,24})\s*[:：]\s*(.*?)\s*$", line)
        if match is None:
            continue
        label = match.group(1).strip()
        value = match.group(2).strip()
        if not value:
            value = _next_structured_value(lines, index + 1)
        if value:
            fields.setdefault(label, []).append(value)
    return fields


def _next_structured_value(lines: list[str], start_index: int) -> str:
    for line in lines[start_index : start_index + 4]:
        if not line:
            continue
        if re.match(r"^[A-Za-z0-9\u4e00-\u9fff/·（）() ]{1,24}\s*[:：]\s*", line):
            return ""
        return line
    return ""


def _first_structured_field(fields: dict[str, list[str]], labels: tuple[str, ...]) -> str:
    for label in labels:
        values = fields.get(label)
        if values:
            return values[0]
    return ""


def _city_values_from_structured_field(value: str) -> list[str]:
    before_detail = re.split(r"(?:招聘|详细地址|办公地址|地址|岗位|职位|薪资)", value, maxsplit=1)[0]
    pieces = re.split(r"[、,，;/；|｜\s]+", before_detail)
    cities: list[str] = []
    for piece in pieces:
        token = piece.strip()
        if not token:
            continue
        matched_city = _known_city_prefix(token)
        if matched_city:
            cities.append(matched_city)
            continue
        generic_match = re.match(r"^([\u4e00-\u9fff]{2,6})(?:市|地区|区域)?$", token)
        if generic_match is not None:
            cities.append(generic_match.group(1))
    return cities


def _known_city_prefix(token: str) -> str:
    for city in (
        "北京",
        "上海",
        "广州",
        "深圳",
        "杭州",
        "成都",
        "南京",
        "苏州",
        "武汉",
        "西安",
        "天津",
        "重庆",
        "青岛",
        "厦门",
        "宁波",
        "无锡",
        "合肥",
        "郑州",
        "长沙",
        "大连",
        "福州",
        "济南",
        "佛山",
        "东莞",
    ):
        if token.startswith(city):
            return city
    return ""


def _degree_requirement_from_structured_field(value: str) -> str | None:
    text = value.strip()
    if not text:
        return None
    if "不限" in text:
        return "不限"
    for degree in ("博士及以上", "硕士及以上", "本科及以上", "大专及以上", "博士", "硕士", "本科", "大专"):
        if degree in text:
            return degree
    return None


def _school_type_requirement_from_structured_field(value: str) -> list[str]:
    text = value.strip()
    if not text or "不限" in text:
        return []
    school_types: list[str] = []
    if "统招" in text or "全日制" in text:
        school_types.append("统招")
    for item in ("985", "211", "双一流", "海外", "强基计划", "双高计划"):
        if item in text:
            school_types.append(item)
    if any(token in text.casefold() for token in ("qs100", "qs前100", "the100", "top100", "世界前100")):
        school_types.append("THE100")
    return _unique_bounded_strings(school_types, max_items=8)


def _constraint_text_from_structured_field(value: str) -> str | None:
    text = " ".join(value.split()).strip()
    return text or None


def _title_anchor_terms(job_title: str) -> list[str]:
    title = job_title.strip()
    return [title] if title else ["目标岗位"]


def _fallback_non_anchor_term(job_title: str, *, anchor_keys: set[str]) -> str:
    for token in job_title.replace("/", " ").replace("｜", " ").replace("|", " ").split():
        text = token.strip()
        if text and text.casefold() not in anchor_keys:
            return text
    compact = job_title.strip()
    if len(compact) > 4:
        return compact[:4]
    return "岗位匹配"


def _role_summary_from_triage(context: WorkbenchSourceRunJobContext | WorkbenchRuntimeSourcingJobContext) -> str:
    terms = _unique_bounded_strings([*context.triage.must_haves, *context.triage.nice_to_haves], max_items=3)
    if terms:
        return f"{context.session.job_title}，重点关注{'、'.join(terms)}。"
    return context.session.job_title


def _query_terms(
    context: WorkbenchSourceRunJobContext | WorkbenchRuntimeSourcingJobContext | WorkbenchLiepinDetailOpenJobContext,
) -> list[str]:
    source_terms = [
        *context.triage.generated_query_hints,
        *context.triage.must_haves,
        *context.triage.synonyms,
        context.session.job_title,
    ]
    terms: list[str] = []
    seen: set[str] = set()
    for value in source_terms:
        text = value.strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        terms.append(text)
        if len(terms) >= 8:
            break
    return terms or [context.session.job_title]


def _runtime_run_accepts_start_callback(run_method: Callable[..., object]) -> bool:
    try:
        signature = inspect.signature(run_method)
    except (AttributeError, TypeError, ValueError):
        return False
    parameters = signature.parameters
    if "runtime_start_callback" in parameters:
        return True
    return any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values())


def _callable_accepts_keyword(callable_object: Callable[..., object], keyword: str) -> bool:
    try:
        signature = inspect.signature(callable_object)
    except (TypeError, ValueError):
        return False
    parameters = signature.parameters
    if keyword in parameters:
        return True
    return any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values())


def _triage_from_requirement_sheet(requirement_sheet: object) -> ExtractedRequirementTriage:
    return ExtractedRequirementTriage(
        must_haves=_unique_bounded_strings(_object_list(requirement_sheet, "must_have_capabilities")),
        nice_to_haves=_unique_bounded_strings(_object_list(requirement_sheet, "preferred_capabilities")),
        synonyms=[],
        seniority_filters=[],
        exclusions=_unique_bounded_strings(_object_list(requirement_sheet, "exclusion_signals")),
        generated_query_hints=_query_hints_from_requirement_sheet(requirement_sheet),
    )


def _query_hints_from_requirement_sheet(requirement_sheet: object) -> list[str]:
    terms: list[object] = [
        *_object_list(requirement_sheet, "initial_query_term_pool"),
        *_object_list(requirement_sheet, "title_anchor_terms"),
    ]
    values: list[str] = []
    for term in terms:
        if isinstance(term, str):
            values.append(term)
            continue
        term_value = _object_attr(term, "term")
        if isinstance(term_value, str):
            values.append(term_value)
    return _unique_bounded_strings(values, max_items=12)


def _object_list(value: object, attr: str) -> list[object]:
    item = _object_attr(value, attr)
    if item is None:
        return []
    if isinstance(item, list):
        return item
    if isinstance(item, tuple):
        return list(item)
    return []


def _object_attr(value: object, attr: str) -> Any:
    if isinstance(value, dict):
        return {str(key): item for key, item in value.items()}.get(attr)
    return getattr(value, attr, None)


def _unique_bounded_strings(values: Sequence[object], *, max_items: int = 20, max_chars: int = 180) -> list[str]:
    results: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        if not text:
            continue
        if len(text) > max_chars:
            text = text[:max_chars].rstrip()
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        results.append(text)
        if len(results) >= max_items:
            break
    return results


def _bounded_join(values: list[str], *, max_items: int = 12, max_chars: int = 800) -> str:
    text = "; ".join(values[:max_items])
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "..."
