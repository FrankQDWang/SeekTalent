# SeekTalent Target Company Discovery Design

Date: 2026-04-22

## Goal

Add target-company sourcing as a first-class retrieval dimension in SeekTalent.

The feature covers two phases:

1. Explicit target companies from JD/notes are available from the first search round.
2. When recall or quality is weak, SeekTalent can use Bocha web search plus a lightweight Bailian/Qwen model to discover relevant target companies, inject them into the query term pool, and run one controlled company-seed search round.

This is not a general web research agent. It is a bounded target-company discovery workflow that produces a structured `TargetCompanyPlan` and then reuses the existing controller/runtime/retrieval/scoring flow.

## Confirmed Decisions

- Use scheme A for flags:
  - `SEEKTALENT_TARGET_COMPANY_ENABLED=true` by default.
  - `SEEKTALENT_COMPANY_DISCOVERY_ENABLED=false` by default.
- Use the runtime-side `CompanyDiscoveryService` design. Controller does not browse, search, or execute external tools.
- Feature flags live only in high-level orchestration. Lower-level modules do not read feature flags.
- Target-company query conversion uses Top-K batch search:
  - accept up to 6-8 high-confidence companies,
  - search at most one company per round,
  - use keyword lanes by default,
  - do not globally apply CTS `company` hard filters unless the input explicitly says the candidate must come from those companies.
- Web search uses Bocha Web Search API.
- Web discovery LLM calls use the configured Bailian OpenAI-compatible `.env` settings with default model `openai-chat:qwen3.5-flash`.
- Bocha Tier 1 QPS is 5, so first implementation keeps Bocha search calls serial.
- First version does not add a new controller action.

## Non-goals

- Do not build an open-ended Deep Research agent.
- Do not let the controller perform web search or call Bocha directly.
- Do not add fallback provider chains for network failures.
- Do not add retry/backoff logic for Bocha failures.
- Do not make target companies a global hard filter by default.
- Do not rewrite requirement extraction, scoring, CTS client, or UI.
- Do not add database storage or a company knowledge graph in this phase.
- Do not introduce broad manager/helper abstractions.

## Current Repository Fit

The current repository already has the right seams:

- `WorkflowRuntime` owns orchestration, budget, pagination, dedup, scoring, reflection, artifacts, and runtime overrides.
- `ReActController` can only return `search_cts` or `stop`; it does not execute tools.
- `canonicalize_controller_query_terms` requires terms to exist in the current query term pool.
- `filter_projection.py` already supports projecting `company_names` to CTS `company`, but that is a hard filter path and should stay separate from target-company sourcing.
- `RunTracer` and per-round artifacts already make search behavior auditable.

Therefore target-company discovery should be a runtime-side module that emits structured plans and injects company terms into the existing query term pool.

## Hard Design Rule: High-level Routing Only

Feature flags must not be scattered through low-level code.

Allowed flag checks:

- in runtime bootstrap after requirement extraction,
- in runtime company-discovery trigger gates before controller planning or stop rescue,
- in public run config/artifact summaries.

Not allowed:

- `if settings.target_company_enabled` inside requirement normalization,
- `if settings.company_discovery_enabled` inside query compiler classification,
- feature-flag checks inside CTS request construction,
- feature-flag checks inside scoring,
- feature-flag checks inside controller validators,
- feature-flag checks inside Bocha provider logic.

Lower-level modules are pure units:

- explicit target extraction extracts when called,
- web discovery runs when called,
- Bocha provider searches when called,
- query injection injects when called,
- scheduler chooses company terms when called.

## Settings

Add settings to `AppSettings`:

```dotenv
SEEKTALENT_TARGET_COMPANY_ENABLED=true
SEEKTALENT_COMPANY_DISCOVERY_ENABLED=false
SEEKTALENT_COMPANY_DISCOVERY_PROVIDER=bocha
SEEKTALENT_BOCHA_API_KEY=
SEEKTALENT_COMPANY_DISCOVERY_MODEL=openai-chat:qwen3.5-flash
SEEKTALENT_COMPANY_DISCOVERY_REASONING_EFFORT=off
SEEKTALENT_COMPANY_DISCOVERY_MAX_SEARCH_CALLS=4
SEEKTALENT_COMPANY_DISCOVERY_MAX_RESULTS_PER_QUERY=30
SEEKTALENT_COMPANY_DISCOVERY_MAX_OPEN_PAGES=8
SEEKTALENT_COMPANY_DISCOVERY_MAX_LLM_CALLS=8
SEEKTALENT_COMPANY_DISCOVERY_TIMEOUT_SECONDS=25
SEEKTALENT_COMPANY_DISCOVERY_ACCEPTED_COMPANY_LIMIT=8
SEEKTALENT_COMPANY_DISCOVERY_MIN_CONFIDENCE=0.65
```

`SEEKTALENT_BOCHA_API_KEY` is required only when web discovery is actually enabled or triggered. Missing key should fail loudly rather than falling back to guessed companies.

## Data Model

Keep models small and close to usage. New models can live under `src/seektalent/company_discovery/models.py`.

```python
CompanyEvidence(
    source: Literal["explicit_notes", "explicit_jd", "web_search", "page_read"],
    title: str,
    url: str | None,
    snippet: str,
)

TargetCompanyCandidate(
    name: str,
    aliases: list[str],
    source: Literal["explicit", "web_inferred"],
    intent: Literal["target", "preferred_source", "exclude", "client_company", "holdout"],
    confidence: float,
    evidence: list[CompanyEvidence],
    search_usage: Literal["keyword", "keyword_and_skill", "holdout", "exclude"],
)

TargetCompanyPlan(
    accepted_companies: list[TargetCompanyCandidate],
    holdout_companies: list[TargetCompanyCandidate],
    rejected_companies: list[TargetCompanyCandidate],
    explicit_company_count: int,
    web_discovery_attempted: bool,
)
```

Extend query term literals:

```python
QueryTermSource += "target_company" | "company_discovery"
QueryTermCategory += "company"
QueryRetrievalRole += "target_company"
```

A company term looks like:

```json
{
  "term": "火山引擎",
  "source": "company_discovery",
  "category": "company",
  "retrieval_role": "target_company",
  "queryability": "admitted",
  "family": "company.volcengine",
  "priority": 20,
  "first_added_round": 2,
  "active": true
}
```

`hard_constraints.company_names` keeps its current meaning: explicit company-experience constraints that may be projected to CTS `company`. Target companies live in `TargetCompanyPlan` and enter search as query terms unless the user explicitly says they are mandatory source companies.

## Module Layout

Add one isolated package:

```text
src/seektalent/company_discovery/
  __init__.py
  models.py
  service.py
  explicit.py
  providers.py
  bocha_provider.py
  prompts.py
  page_reader.py
  query_injection.py
  scheduler.py
```

Responsibilities:

- `explicit.py`: extracts explicit target/preferred/excluded/client companies from existing requirement fields and simple JD/notes patterns.
- `providers.py`: defines a minimal web-search protocol and normalized `WebSearchResult`.
- `bocha_provider.py`: calls Bocha Web Search API using `httpx`.
- `page_reader.py`: fetches a small number of selected pages with `httpx`; records failures without failing the run.
- `prompts.py`: renders discovery LLM prompts for planner, triage, evidence extraction, and reducer.
- `service.py`: coordinates explicit bootstrap and web discovery workflows.
- `query_injection.py`: injects accepted companies into `query_term_pool`.
- `scheduler.py`: chooses the next company seed query terms.

The package should not depend on controller, scoring, finalizer, UI, or CTS client.

## Runtime Flow

### Explicit Bootstrap

After `_build_run_state` creates the `RunState`:

```python
if self.settings.target_company_enabled:
    plan = await self.company_discovery.bootstrap_explicit(...)
    run_state.retrieval_state.target_company_plan = plan
    run_state.retrieval_state.query_term_pool = inject_target_company_terms(...)
    tracer.write_json("company_discovery/bootstrap_plan.json", plan.model_dump(mode="json"))
```

If the plan has accepted explicit target companies, runtime should ensure the first retrieval validates a target-company seed. This can be a runtime override that builds a normal `SearchControllerDecision` with:

```python
[role_anchor, top_company]
```

This keeps target-company sourcing from being accidentally skipped by the controller in round 1.

### Web Discovery Trigger

Web discovery can trigger before controller planning, starting from round 2, after at least one retrieval observation exists:

```python
should_trigger =
    company_discovery_enabled
    and not discovery_state.web_discovery_attempted
    and not target_company_plan.has_usable_untried_companies
    and round_no >= 2
    and rounds_remaining_after_current >= 1
    and (
        latest.unique_new_count <= 1
        or latest.shortage_count >= target_new - 1
        or stop_guidance.top_pool_strength in {"empty", "weak"}
        or stop_guidance.zero_gain_round_count >= 1
    )
```

Stop rescue:

- If controller returns `stop`, top pool is weak/empty, and web discovery has not been attempted, runtime runs company discovery first.
- If accepted companies are found, runtime overrides stop with a company-seed search.
- If no usable companies are found, runtime continues with the existing stop/broaden behavior.

## Web Discovery Workflow

The web workflow is fixed and bounded:

1. Build a redacted `CompanyDiscoveryInput` from requirement sheet:
   - role title,
   - title anchor,
   - top must-have capabilities,
   - preferred domains/backgrounds,
   - locations,
   - exclusions.
2. Use Qwen 3.5 Flash to plan up to 3-4 search queries. The model outputs search tasks only, not company conclusions.
3. Call Bocha Web Search API serially:
   - endpoint: `https://api.bochaai.com/v1/web-search`,
   - header: `Authorization: Bearer <SEEKTALENT_BOCHA_API_KEY>`,
   - `summary=true`,
   - `count` from settings, default 30.
4. Normalize and dedupe search results.
5. Use Qwen 3.5 Flash to triage title/url/snippet/summary and select up to 8 URLs to open.
6. Read selected pages with `httpx`.
7. Use Qwen 3.5 Flash to extract company evidence from page text or Bocha summaries.
8. Reduce evidence cards into a final `TargetCompanyPlan`.

The model must not accept a company only because it is famous. Accepted companies need evidence and must fit the role as a talent-source company, not merely as a user of a technology.

The workflow has a hard wall-clock budget. `SEEKTALENT_COMPANY_DISCOVERY_TIMEOUT_SECONDS` is a discovery budget, not a whole-run failure threshold:

- When the budget is reached, stop planning/searching/reading new inputs.
- Run the reducer on evidence already collected.
- If enough evidence exists, produce a partial `TargetCompanyPlan` and continue to a company-seed search.
- If evidence is insufficient, write a completed discovery artifact with `stop_reason="timeout_no_accepted_companies"` and return to the existing controller/broaden/stop flow.

This timeout behavior is not a fallback chain. It is the normal bounded-stop condition for an optional sourcing strategy, similar to page or round budget exhaustion in retrieval.

## Bocha Provider

The provider is intentionally narrow:

```python
class WebSearchProvider(Protocol):
    async def search(self, query: str, *, count: int) -> list[WebSearchResult]:
        ...
```

Bocha response fields map to normalized result fields:

- `name` -> `title`
- `url` -> `url`
- `siteName` -> `site_name`
- `snippet` -> `snippet`
- `summary` -> `summary`
- `datePublished` -> `published_at`

Search calls are serial to respect Tier 1 QPS=5. No retry chain is added.

## Company Query Scheduler

The scheduler turns many target companies into small CTS query surfaces.

Rules:

- Max accepted companies: default 8.
- Search explicit companies before web-inferred companies.
- Within a source bucket, order by confidence, evidence count, and fit-axis coverage.
- Each round uses at most one company.
- Do not combine many companies in one keyword query.
- Default query shape:

```text
[role_anchor, company]
```

Later rounds may use:

```text
[role_anchor, company, strongest_core_skill]
```

The third term is optional and only used when it does not crowd out the company lane or repeat a family.

Avoid repeats:

- A `company.<canonical>` family used in `sent_query_history` is not selected again.
- Alias terms are not used in the same round as the canonical company.
- Alias retry is allowed only if the canonical term had zero recall and enough budget remains.

Default CTS behavior:

- company term goes into keyword query,
- no CTS `company` filter.

CTS `company` filter is allowed only when JD/notes explicitly say candidates must come from those companies.

## Controller Prompt Change

Keep the controller action schema unchanged.

Add one controller prompt rule:

> When target-company terms are visible and explicit from JD/notes, prefer at least one early target-company-backed search unless runtime has already executed one or the family produced zero gain.

Runtime still owns the deterministic company-seed override, so this prompt rule is guidance rather than the only enforcement mechanism.

## Artifacts

Add top-level and per-round artifacts:

```text
company_discovery/bootstrap_plan.json
rounds/round_XX/company_discovery_input.json
rounds/round_XX/company_search_queries.json
rounds/round_XX/company_search_results.json
rounds/round_XX/company_search_triage.json
rounds/round_XX/company_page_reads.json
rounds/round_XX/company_evidence_cards.json
rounds/round_XX/company_discovery_plan.json
```

Existing artifacts remain the source of truth for retrieval execution:

- `controller_context.json`
- `controller_decision.json`
- `retrieval_plan.json`
- `cts_queries.json`
- `sent_query_history.json`
- `search_observation.json`
- `search_diagnostics.json`

Discovery artifacts should store enough evidence to explain why a company was accepted, held out, or rejected.

Discovery progress should use the existing runtime `progress_callback` rather than a new streaming subsystem. Emit compact events such as:

- `company_explicit_bootstrap_completed`
- `company_discovery_started`
- `company_search_queries_planned`
- `company_search_completed`
- `company_search_triaged`
- `company_pages_read`
- `company_discovery_timeout`
- `company_discovery_completed`

These events are for UX and observability only. They do not change timeout or recovery behavior.

## TUI Company Discovery Trace

The current TUI already renders progress events into a readable business trace. Company discovery should integrate into that trace with first-class business blocks, not dim technical one-liners.

The desired style is similar to a web-search reasoning trace:

- short stage heading,
- concise explanation of why the step ran,
- `Found N web pages` style search summary,
- `Read N pages` style reading summary,
- small list of page titles,
- accepted/holdout/rejected company summary,
- clear next search action.

Do not print raw URLs, full Bocha payloads, full page text, prompt text, or artifact internals in the main TUI. Those stay in JSON artifacts.

### Explicit Company Only

When `SEEKTALENT_TARGET_COMPANY_ENABLED=true` and web discovery is off, the TUI should show a compact bootstrap block only if explicit companies are found:

```text
目标公司线索 · 来自 JD/Notes
识别到 5 家目标/偏好公司：阿里云、火山引擎、腾讯云、百度智能云、华为云。
已排除 1 家：客户公司 A。

下一步搜索
第 1 轮会先验证目标公司来源：大模型、阿里云。
```

The following round summary should label the lane clearly:

```text
第 1 轮 · 目标公司检索：大模型、阿里云

本轮候选人情况
搜到 10 人，新增 6 人，6 人进入评分；fit 3 人，not fit 3 人。
```

If no explicit companies are found, emit no company bootstrap block.

### Web Discovery Only

When web discovery triggers after low recall or weak quality, render a bounded discovery trace:

```text
公司发现 · 低召回触发
上一轮新增 0 人，top pool 仍 weak。开始搜索相似人才来源公司。

Found 47 web pages
搜索覆盖：大模型平台、推理服务、GPU/Kubernetes、AI Infra 招聘。
筛选出 8 个可能相关页面，优先阅读行业列表、招聘页、官方产品页和技术博客。

Read 6 pages
- 2026 年中国 AI Infra 公司盘点 ↗
- 火山引擎大模型服务平台产品页 ↗
- 阿里云百炼平台介绍 ↗
- 腾讯云 TI 平台招聘页 ↗

发现目标公司
接受 6 家：火山引擎、阿里云、腾讯云、百度智能云、华为云、MiniMax。
保留观察 3 家：商汤科技、第四范式、阶跃星辰。
拒绝 5 家：仅是报道来源或只使用大模型 API，缺少相似团队证据。

下一步搜索
第 3 轮会验证目标公司来源：大模型、火山引擎。
```

If timeout occurs with usable partial evidence:

```text
公司发现 · 达到 25 秒预算
已搜索 3 次，找到 38 个网页，阅读 4 页。
证据足够，先使用 partial plan：火山引擎、阿里云、腾讯云。
```

If timeout occurs without enough evidence:

```text
公司发现 · 达到 25 秒预算
已搜索 3 次，找到 38 个网页，阅读 4 页。
没有足够证据接受目标公司，回到常规技能检索。
```

### Both Flags Enabled

When both flags are enabled:

1. Show explicit target-company bootstrap first.
2. Do not trigger web discovery while explicit companies still have usable untried company terms.
3. If explicit company validation still yields weak recall or weak quality, web discovery may run as supplemental discovery.
4. The TUI must label supplemental companies as new web-discovered companies and identify duplicates that were not re-added.

Example:

```text
公司发现 · 显式目标公司验证后仍低召回
已验证显式公司 2 家，新增候选人不足。开始补充发现相邻人才来源公司。

Found 42 web pages
Read 6 pages

补充目标公司
新增接受 4 家：百度智能云、华为云、MiniMax、智谱 AI。
未重复添加：阿里云、火山引擎。

下一步搜索
第 4 轮会验证目标公司来源：大模型、百度智能云。
```

### Rendering Rules

- Company discovery business blocks use normal brightness.
- Provider details, query ids, artifact paths, and raw counts beyond the summary stay dim or hidden.
- Page lists show at most 4-6 titles with a terminal-friendly `↗` marker.
- If more pages/results exist, show a short line such as `另有 31 条结果见 company_search_results.json`.
- If a round query includes a `target_company` term, the round summary title should use `目标公司检索` instead of generic `检索词`.
- If both company flags are off, existing TUI trace behavior remains unchanged.

## Error Handling

Fail fast where failure changes trust in the workflow:

- `company_discovery_enabled=true` with missing Bocha key fails loudly.
- Bocha authentication, request-shape, or provider protocol failures fail the run.
- Company discovery LLM structured output failure follows existing bounded output retry rules.
- Page read failure is non-fatal; record `read_success=false` and keep using Bocha result summary/snippet.
- Whole discovery timeout is non-fatal. It stops discovery, runs the reducer on collected evidence, and either emits a partial accepted plan or returns an empty accepted plan with an explicit timeout stop reason.
- Empty accepted company plan is non-fatal; runtime records the plan and continues existing controller/broaden/stop behavior.

No fallback model chain, fallback search provider, or hidden recovery path is part of this design.

## Testing

Focused tests:

- `tests/test_query_compiler.py`
  - company terms can be injected with `category=company`, `retrieval_role=target_company`, and stable `company.*` family.
  - duplicate company canonical keys are deduped.
- `tests/test_query_plan.py`
  - company terms are valid admitted non-anchor terms.
  - duplicate company families are rejected in one query.
- `tests/test_runtime_state_flow.py`
  - explicit company bootstrap injects pool terms.
  - runtime can force a company-seed round.
  - `sent_query_history` records the company query.
- New `tests/test_company_discovery.py`
  - Bocha provider builds the expected payload and normalizes search results.
  - search planner/triage/reducer can be stubbed with structured outputs.
  - scheduler picks top companies one at a time and skips tried families.
- TUI progress tests
  - explicit company bootstrap renders a `目标公司线索` block when accepted explicit companies exist.
  - web discovery renders `Found N web pages`, `Read N pages`, accepted/holdout/rejected summaries, and next search.
  - timeout with partial evidence and timeout without accepted companies render different business messages.
  - when both flags are off, existing progress rendering remains unchanged.
- Config tests
  - defaults match the confirmed flag strategy.
  - missing Bocha key fails only when web discovery is enabled/triggered.

No unit test should call the real Bocha API.

Validation commands:

```bash
uv run pytest tests/test_query_compiler.py tests/test_query_plan.py tests/test_runtime_state_flow.py
uv run pytest tests/test_company_discovery.py
uv run pytest tests/test_filter_projection.py tests/test_requirement_extraction.py
```

Run the broader suite if these changes touch shared models or runtime behavior more widely than expected:

```bash
uv run pytest
```

## Documentation Updates

Update:

- `docs/configuration.md` with target-company and company-discovery env vars.
- `docs/outputs.md` with company discovery artifacts.
- `src/seektalent/default.env` with commented or default values.

## Acceptance Criteria

- With `SEEKTALENT_TARGET_COMPANY_ENABLED=true`, explicit target/preferred companies from JD/notes can appear in the first retrieval as admitted target-company query terms.
- With `SEEKTALENT_COMPANY_DISCOVERY_ENABLED=false`, no Bocha call is made.
- With web discovery enabled and low recall/weak quality, runtime can trigger one bounded Bocha discovery workflow.
- Discovered companies are accepted only with evidence.
- Accepted companies are injected into the query term pool.
- Runtime can force one company-seed search after successful discovery.
- Company seed queries use one company per round.
- Target companies are not applied as global CTS hard filters unless explicitly mandatory.
- All discovery decisions and subsequent company queries are auditable in run artifacts.
- TUI renders explicit-company and web-discovery trace blocks in business-readable form when the corresponding progress events occur.
- TUI labels target-company search rounds distinctly from generic keyword rounds.
- Existing non-company retrieval behavior remains unchanged when both flags are off.
