# Task: Phase 2.2.1 Generic Retrieval Baseline and De-specialization

## Goal

实现下一阶段 generic-only baseline：移除或降级当前检索链路里的 Agent/LLM 领域特异 active 策略，只保留通用搜索卫生规则、通用 query discipline、prompt discipline、去重补拉和 audit，并作为 `0.4.7` 启用 judge/eval 跑一次版本化 benchmark。

业务目标：

- 接受短期 Agent benchmark 指标可能下降，换取更通用、可解释、可迁移的基础能力。
- 不维护人工领域词表、领域 alias 或 domain overlay。
- 用当前 12 条 Phase 2.2 pilot eval benchmark 建立去特异化后的 `0.4.7` baseline，后续所有策略变化都和它对比。

## Why Now

- `docs/plans/roadmap.md` 已把 Phase 2.2.1 标为下一步：Generic Retrieval Baseline and De-specialization。
- Phase 2.2 pilot 已完成 12 条混合 JD disable-eval smoke，并证明 `term_surface_audit.json` 可用。
- 12 条 trace 显示：主岗位 anchor 是稳定召回来源；过窄新术语、寻访备注、目标公司范围、合规/沟通问题不适合直接进入 CTS keyword search。
- 用户明确要求：不人工维护任何词表，后续注意力放在通用能力；领域适应放到很后期，并做成数据驱动循环。

## Non-goals

- 不发布 release，不打 tag。
- 不改 W&B report 代码；如 `.env` 已配置 W&B，沿用现有 eval logging side effect。
- 不做 domain router、domain overlay、query policy 微服务、规则 DSL、插件系统或数据库。
- 不把 `AI Agent -> Agent`、`MultiAgent 架构 -> MultiAgent` 变成 active retrieval rule。
- 不为了挽回 Agent 样本下降而加入新的领域特例。
- 不重写 orchestrator、scoring、finalizer、CTS client 或 UI。
- 不删除 `term_surface_audit.json` 里的 candidate surface rule 记录；它们继续只做 audit，不影响 query。

## Done Criteria

- `src/seektalent/retrieval/query_compiler.py` 不再有 Agent/LLM 专属 active 策略：
  - 不再按 `agent` / `智能体` 特判生成 `AI Agent` 或 `Agent` anchor。
  - 不再自动注入 `大模型` broad-domain term。
  - 不再靠手工 `KNOWN_FRAMEWORKS` / `KNOWN_SKILLS` / `DOMAIN_FAMILIES` 给 query ordering 提供领域偏置。
  - notes-derived terms 默认不进入 CTS keyword search，除非后续计划另行证明一个通用例外。
- 通用策略仍保留：
  - 文本清理、去重、职位后缀清理。
  - hard constraints 和 keyword search 分离。
  - filter-only / score-only / blocked 通用分类。
  - compiler-admitted-only、每轮少量词、family 不重复。
  - 同轮补拉、跨轮去重、`search_diagnostics.json`、`term_surface_audit.json`。
- Tests and lint pass for the focused surface.
- 本地版本 bump 到 `0.4.7`，`uv run seektalent version` 输出 `0.4.7`。
- 当前 12 条 pilot 能用 `--enable-eval --benchmark-max-concurrency 3` replay 完成，并记录和 Phase 2.2 pilot 的差异。
- 每条 benchmark row 都有 non-null `evaluation_result`。
- 如果有 Agent 样本下降，记录为 de-specialization cost，不用领域规则补回。
- Prompt 优化只编码通用抽取纪律，不编码领域 alias、领域词表或 Agent/LLM 特例。

## Repo Entrypoints

Read first:

1. `AGENTS.md`
2. `README.md`
3. `docs/plans/roadmap.md`
4. `docs/outputs.md`
5. `src/seektalent/retrieval/query_compiler.py`
6. `src/seektalent/retrieval/query_plan.py`
7. `src/seektalent/requirements/normalization.py`
8. `src/seektalent/prompts/requirements.md`
9. `tests/test_query_compiler.py`
10. `tests/test_query_plan.py`
11. `tests/test_requirement_extraction.py`
12. `tests/test_runtime_audit.py`
13. `tests/test_evaluation.py`

Likely edit:

- `src/seektalent/retrieval/query_compiler.py`
- `src/seektalent/prompts/requirements.md`
- `tests/test_query_compiler.py`
- `tests/test_query_plan.py`
- `tests/test_requirement_extraction.py`
- `tests/test_evaluation.py`
- `pyproject.toml`
- `src/seektalent/__init__.py`
- `src/seektalent/evaluation.py`
- `docs/plans/roadmap.md`
- This plan file

Allowed if needed:

- `src/seektalent/retrieval/query_plan.py`
- `src/seektalent/requirements/normalization.py`
- `tests/test_runtime_audit.py`

Do not edit unless this plan is updated first:

- `src/seektalent/runtime/orchestrator.py`
- `src/seektalent/scoring/`
- `src/seektalent/finalize/`
- `src/seektalent/clients/`
- `src/seektalent_ui/`
- W&B report code

Ignore unless reading generated evidence:

- `runs/`
- `.seektalent/`
- `.venv/`
- `.pytest_cache/`
- `dist/`

## Current Reality

- `compile_query_term_pool()` lives in `src/seektalent/retrieval/query_compiler.py`.
- Current compiler behavior includes Agent/LLM-specific logic:
  - `_compile_role_anchors()` maps titles containing `agent` / `智能体` to `AI Agent` or `Agent`.
  - `_needs_large_model_domain()` can inject `大模型`.
  - `KNOWN_FRAMEWORKS`, `KNOWN_SKILLS`, and `DOMAIN_FAMILIES` assign hand-maintained families and roles.
  - `_merge_query_terms()` interleaves `jd_query_terms` and `notes_query_terms`.
- Current requirements prompt says JD and notes are equally important retrieval sources and asks the model to emit `notes_query_terms`.
- `query_plan.py` enforces the runtime contract:
  - controller terms must come from compiled term pool.
  - query terms must be admitted.
  - each query must contain exactly one admitted anchor.
  - each query has 1-2 non-anchor terms depending on round.
  - duplicate families are rejected.
- Phase 2.2 pilot benchmark file exists locally at `artifacts/benchmarks/phase_2_2_pilot.jsonl` with 12 rows.
- Previous Phase 2.2 pilot summary is `runs/phase_2_2_term_surface_pilot_20260420_152036/benchmark_summary_20260420_155444.json`.
- Current package version is `0.4.6` in `pyproject.toml`, `src/seektalent/__init__.py`, and the fallback in `src/seektalent/evaluation.py`.
- `seektalent benchmark --benchmark-max-concurrency N` uses a `ThreadPoolExecutor(max_workers=N)` to run rows in parallel.
- Judge concurrency is per run, not global across benchmark rows:
  - `ResumeJudge.judge_many()` creates `asyncio.Semaphore(settings.judge_max_concurrency)` inside each run.
  - With `--benchmark-max-concurrency 3` and `SEEKTALENT_JUDGE_MAX_CONCURRENCY=5`, up to roughly 15 judge calls can be active if three runs reach eval at the same time.
  - The per-run limit still works, but there is no cross-run global judge limiter in current code.
- Concurrent eval runs write to the shared local SQLite judge cache. SQLite serializes writes, but if `database is locked` appears, stop and rerun with lower concurrency or add an explicit global limiter in a separate scoped change.
- Worktree is already dirty from prior Phase 2.2/roadmap changes; do not revert unrelated files.

## Target Behavior

### Compiler

- Role anchor is compiled generically:
  - clean `title_anchor_term`;
  - strip common job-title suffixes;
  - fallback to cleaned `job_title`;
  - do not special-case Agent, LLM, intelligent-agent terms, or any domain.
- No deterministic broad-domain injection:
  - remove or disable `_needs_large_model_domain()`;
  - do not add `大模型` unless it is explicitly present in JD-derived query terms and survives generic classification.
- No hand-maintained domain role/family promotion:
  - remove static known framework/skill/domain classification from active compiler behavior;
  - keep generic family names from compacted term text;
  - keep role anchor / filter-only / score-only / blocked categories.
- Notes are not search terms by default:
  - notes-derived terms should be `score_only`, `filter_only`, or `blocked`, not active keyword terms;
  - if this causes a JD to have no admitted non-anchor, stop and inspect rather than adding a notes exception silently.

### Prompt

- Update `src/seektalent/prompts/requirements.md` so it no longer tells the model to treat notes and JD as equally important retrieval-term sources.
- Notes should still inform hard constraints, preferences, exclusions, scoring rationale, and recruiter context.
- `notes_query_terms` may remain in the schema for compatibility, but prompt should discourage using recruiter process questions, company targeting, salary, availability, compliance, or communication checks as retrieval terms.
- Add generic extraction guidance for searchable concepts:
  - prefer stable resume-searchable capability/tool/concept nouns from the JD;
  - avoid long responsibility phrases, internal project wording, marketing adjectives, interview logistics, and recruiter screening questions as query terms;
  - when the JD contains an over-composed phrase such as `X 架构`, `X 平台`, `X 系统`, `X 方案`, `X 能力`, or `X 落地`, prefer the searchable core concept `X` only if `X` is explicitly present in the input and would plausibly appear on resumes;
  - do not invent aliases, synonyms, or broader domain terms that are not in the input.
- Do not add domain examples like Agent/LLM/Flink/RAG-specific rewrite rules to the prompt.

### Cleaning vs Prompt Boundary

Put deterministic, low-semantic-risk work in code:

- whitespace, punctuation, and duplicate cleanup;
- title suffix stripping;
- hard constraints and recruiter logistics separated from keyword search;
- obvious filter-only concepts such as degree, school type, experience, age, gender, city, salary, interview process, availability, and target company lists;
- obvious blocked junk or internal artifacts;
- compiler-admitted-only validation, per-round term budget, family uniqueness, same-round refill, cross-round dedup, and audit output.

Put judgment-heavy extraction in the LLM prompt:

- choose one stable role anchor from the job title without adding broader or narrower aliases;
- decide whether a JD phrase is a resume-searchable skill/tool/concept or merely a responsibility sentence;
- split over-composed JD wording into shorter searchable concepts when the shorter concept is present in the input;
- keep notes as constraints, preferences, exclusions, screening context, and scoring context rather than keyword source;
- avoid leaking target companies, salary, location logistics, compliance rules, or process questions into search terms.

Do not put the following in either layer during Phase 2.2.1:

- manually maintained domain dictionaries;
- domain-specific alias rewrites;
- prompt examples that encode Agent/LLM/bigdata-specific preferred terms;
- data-driven policy generation or external query-policy service.

### Audit

- Keep `term_surface_audit.json` behavior.
- Candidate surface rules may still appear as evidence, but they must not change CTS query terms.

### Evaluation and Concurrency

- Run the acceptance benchmark as version `0.4.7`.
- Enable judge/eval for the 12-row benchmark.
- Use benchmark row parallelism `3`.
- Do not assume `judge_max_concurrency` is global:
  - it limits concurrent judge calls inside one run;
  - total process-level judge concurrency can be `benchmark_max_concurrency * judge_max_concurrency`;
  - for a conservative run, set `SEEKTALENT_JUDGE_MAX_CONCURRENCY=1` before the benchmark to keep total judge concurrency around 3 while preserving row parallelism 3.
- Do not add a global judge limiter in this phase unless the benchmark fails due to provider rate limits or SQLite lock contention.

## Milestones

### M0. Confirm Baseline Inputs and Old Evidence

Steps:

- Validate current pilot JSONL count.
- Validate old Phase 2.2 summary exists.
- Inspect one old `term_surface_audit.json` if needed to confirm fields.

Validation:

```bash
uv run python - <<'PY'
import json
from pathlib import Path

pilot = Path("artifacts/benchmarks/phase_2_2_pilot.jsonl")
rows = [json.loads(line) for line in pilot.read_text(encoding="utf-8").splitlines() if line.strip()]
assert len(rows) == 12, len(rows)
assert len({row["jd_id"] for row in rows}) == 12
old_summary = Path("runs/phase_2_2_term_surface_pilot_20260420_152036/benchmark_summary_20260420_155444.json")
assert old_summary.exists(), old_summary
print("ok", len(rows), old_summary)
PY
```

Expected: prints `ok 12 ...benchmark_summary_20260420_155444.json`.

### M1. De-specialize Compiler

Steps:

- Change `_compile_role_anchors()` to generic suffix-stripping logic only.
- Remove or stop calling `_needs_large_model_domain()`.
- Remove static known framework/skill/domain promotion from `_classify_term()`.
- Keep:
  - `TITLE_SUFFIXES`
  - school/degree/age/gender/experience filter-only classification
  - obvious soft-skill / abstract score-only classification
  - blocked junk classification
  - role-anchor detection for the compiled title anchor itself
- Add a small generic recruiter-notes demotion path:
  - notes source terms should not become active CTS keyword terms;
  - common recruiter-note concepts should be `score_only` or `filter_only`.

Acceptance:

- Agent titles are not rewritten into broader/narrower aliases.
- `LLM Agent算法工程师` does not auto-add `大模型`.
- `LangChain`, `Python`, `Flink`, `C++`, etc. can still exist as terms, but their role/family is generic unless future data-driven policy promotes them.
- Notes terms are not active keyword terms.

Validation:

```bash
uv run pytest tests/test_query_compiler.py tests/test_requirement_extraction.py -q
```

Expected: tests pass after updating expectations for generic behavior.

### M2. Keep Query Planner Contract Generic

Steps:

- Prefer not to edit `query_plan.py`.
- If tests need adjustment, update tests to assert the generic contract instead of high-signal domain priority.
- Keep canonical query validation:
  - terms must be compiled;
  - terms must be admitted;
  - exactly one anchor;
  - max 3 terms;
  - no duplicate families.

Acceptance:

- Existing planner rules still protect runtime from arbitrary controller terms.
- No Agent/LLM-specific ordering expectation remains in tests.

Validation:

```bash
uv run pytest tests/test_query_plan.py -q
```

Expected: tests pass.

### M3. Update Requirements Prompt Discipline

Steps:

- Edit `src/seektalent/prompts/requirements.md`.
- Remove the instruction that notes and JD are equally important retrieval sources.
- Add concise prompt guidance:
  - `jd_query_terms` should come from JD role responsibilities and required capabilities.
  - `notes` should mostly populate constraints, preferences, exclusions, and scoring context.
  - avoid putting recruiter process questions, salary, availability, compliance, target company lists, or interview logistics into retrieval terms.
- Add generic guidance that query terms should be short resume-searchable concepts from the input, not long responsibility phrases.
- Add generic guidance that over-composed terms can be shortened only to an input-present core concept; do not invent aliases.
- Avoid examples that encode Agent/LLM/Flink/RAG/bigdata-specific policy.
- Keep schema compatibility; do not rename fields.

Acceptance:

- Prompt no longer encourages notes leakage into keyword search.
- Prompt makes the data-cleaning vs LLM-judgment boundary explicit enough for future maintainers.
- Prompt improves generic term selection without creating a hidden domain wordlist.
- Requirement extraction tests pass.

Validation:

```bash
uv run pytest tests/test_requirement_extraction.py -q
```

Expected: tests pass.

### M4. Bump Version to 0.4.7

Steps:

- Update version metadata to `0.4.7`:
  - `pyproject.toml`
  - `src/seektalent/__init__.py`
  - `src/seektalent/evaluation.py` fallback in `_app_version()`
  - `tests/test_evaluation.py` hard-coded version expectations
- Do not change runtime behavior in this milestone.

Acceptance:

- CLI reports `0.4.7`.
- Evaluation/W&B config tests expect `0.4.7`.

Validation:

```bash
uv run seektalent version
uv run pytest tests/test_evaluation.py::test_evaluate_run_logs_weave_and_wandb -q
```

Expected:

- `seektalent version` prints `0.4.7`.
- Targeted test passes.

### M5. Focused Validation

Run:

```bash
uv run seektalent version

uv run ruff check \
  src/seektalent/retrieval/query_compiler.py \
  src/seektalent/retrieval/query_plan.py \
  src/seektalent/requirements/normalization.py \
  tests/test_query_compiler.py \
  tests/test_query_plan.py \
  tests/test_requirement_extraction.py \
  tests/test_evaluation.py

uv run pytest \
  tests/test_query_compiler.py \
  tests/test_query_plan.py \
  tests/test_requirement_extraction.py \
  tests/test_runtime_audit.py \
  tests/test_cli.py \
  tests/test_evaluation.py \
  -q
```

Expected:

- `seektalent version` prints `0.4.7`.
- Ruff passes.
- Targeted pytest passes.

### M6. Run 12-row 0.4.7 Generic Eval Benchmark

Run:

```bash
uv run seektalent benchmark \
  --jds-file artifacts/benchmarks/phase_2_2_pilot.jsonl \
  --env-file .env \
  --output-dir runs/phase_2_2_1_generic_baseline_0_4_7_$(date +%Y%m%d_%H%M%S) \
  --benchmark-max-concurrency 3 \
  --enable-eval \
  --json
```

Validate:

```bash
SUMMARY="$(find runs/phase_2_2_1_generic_baseline_0_4_7_* -maxdepth 1 -name 'benchmark_summary_*.json' | sort | tail -1)"

uv run python - "$SUMMARY" <<'PY'
import json
import sys
from pathlib import Path

summary = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert summary["count"] == 12, summary["count"]
zero_final = []
missing_eval = []
for row in summary["runs"]:
    run_dir = Path(row["run_dir"])
    assert (run_dir / "search_diagnostics.json").exists(), row["jd_id"]
    assert (run_dir / "term_surface_audit.json").exists(), row["jd_id"]
    assert (run_dir / "evaluation" / "evaluation.json").exists(), row["jd_id"]
    if row["evaluation_result"] is None:
        missing_eval.append(row["jd_id"])
    diagnostics = json.loads((run_dir / "search_diagnostics.json").read_text(encoding="utf-8"))
    if diagnostics["summary"]["final_candidate_count"] == 0:
        zero_final.append(row["jd_id"])
assert len(zero_final) <= 2, zero_final
assert not missing_eval, missing_eval
print("ok", summary["count"], "zero_final", zero_final)
PY
```

Expected:

- Benchmark completes with eval enabled.
- Prints `ok 12`.
- Every row has `evaluation_result`.
- `zero_final` count is 0-2. If more than 2, stop and inspect traces instead of adding a domain fix.

Concurrency note:

- With current code, judge concurrency is per run.
- If `.env` keeps `SEEKTALENT_JUDGE_MAX_CONCURRENCY=5`, this benchmark can produce up to roughly 15 concurrent judge calls when 3 rows reach eval together.
- If provider rate limits or token budget are a concern, set `SEEKTALENT_JUDGE_MAX_CONCURRENCY=1` for this benchmark run so total judge concurrency stays around 3.

### M7. Compare Against Phase 2.2 Pilot and Record Results

Run:

```bash
OLD="runs/phase_2_2_term_surface_pilot_20260420_152036/benchmark_summary_20260420_155444.json"
NEW="$(find runs/phase_2_2_1_generic_baseline_0_4_7_* -maxdepth 1 -name 'benchmark_summary_*.json' | sort | tail -1)"

uv run python - "$OLD" "$NEW" <<'PY'
import json
import sys
from pathlib import Path

def rows(path):
    summary = json.loads(Path(path).read_text(encoding="utf-8"))
    out = {}
    for row in summary["runs"]:
        diagnostics = json.loads((Path(row["run_dir"]) / "search_diagnostics.json").read_text(encoding="utf-8"))
        out[row["jd_id"]] = diagnostics["summary"]
    return out

old = rows(sys.argv[1])
new = rows(sys.argv[2])
for jd_id in sorted(new):
    before = old[jd_id]
    after = new[jd_id]
    print(
        jd_id,
        "final", before["final_candidate_count"], "->", after["final_candidate_count"],
        "unique", before["total_unique_new_candidates"], "->", after["total_unique_new_candidates"],
        "rounds", before["rounds_executed"], "->", after["rounds_executed"],
    )
PY
```

Then update:

- this plan's Decision Log / Status;
- `docs/plans/roadmap.md` with the new summary path, eval means, and observed de-specialization tradeoff.

Expected:

- Comparison output is saved in the terminal or copied into the plan/roadmap as concise bullets.

## Decision Log

- 2026-04-20: Phase 2.2.1 intentionally accepts possible short-term metric regression to establish a generic baseline.
- 2026-04-20: Domain-specific adaptation is deferred to a final data-driven loop; no manually maintained domain dictionary or alias list in this phase.
- 2026-04-20: Notes should not be equal retrieval sources for keyword search. They remain useful for constraints, preferences, exclusions, scoring, and recruiter context.
- 2026-04-20: Candidate surface aliases remain audit-only in this phase.
- 2026-04-20: Phase 2.2.1 acceptance should run as version `0.4.7` with eval/judge enabled and benchmark row parallelism `3`.
- 2026-04-20: Current judge concurrency limit is per run, not a global benchmark-level limit. Do not add a global limiter unless rate limits or SQLite lock contention make it necessary.
- 2026-04-20: Initial eval replay with W&B/Weave enabled exposed a concurrent W&B run-state failure, so the accepted local benchmark disabled W&B/Weave side effects and kept row parallelism `3` plus per-run judge concurrency `2`.
- 2026-04-20: Accepted `0.4.7` combined benchmark summary is `runs/phase_2_2_1_generic_baseline_0_4_7_zz_combined_20260420_172644/benchmark_summary_20260420_172644.json`.
- 2026-04-20: Generic baseline produced one zero-final row, `agent_jd_007`, and one partial-shortlist row, `agent_jd_004` with 5 final candidates. Record these as de-specialization cost; do not add Agent/LLM rules to compensate.

## Risks and Unknowns

- Removing Agent/LLM-specific anchor logic may lower Agent recall. Record the cost; do not compensate with new Agent rules.
- Removing known framework/skill/domain role promotion may reduce query ordering quality. This is acceptable for baseline if failures are diagnosable.
- Demoting all notes terms may hurt cases where notes contain the only real technical requirement. If this causes no admitted non-anchor term, stop and inspect; do not silently add a domain exception.
- `term_surface_audit.json` currently has query-containing aggregates, not exact causal attribution. Do not promote rules from those fields.
- With `--benchmark-max-concurrency 3`, effective judge concurrency can exceed `SEEKTALENT_JUDGE_MAX_CONCURRENCY` at the whole-process level because each run creates its own semaphore.
- Concurrent eval runs share `.seektalent/judge_cache.sqlite3`; stop on SQLite lock errors instead of adding silent retry logic.
- Current worktree already has unrelated uncommitted changes from prior work. Preserve them.

## Stop Rules

- Stop if a validation command fails for a reason not explained by the current milestone.
- Stop if implementing this requires a new service, database, broad orchestrator rewrite, or new abstraction layer.
- Stop if more than two of the 12 pilot rows produce zero final candidates after generic replay.
- Stop if the compiler produces no admitted role anchor or no admitted non-anchor terms for multiple benchmark rows; inspect traces before adding fallback behavior.
- Stop if a proposed fix is domain-specific, such as an Agent/LLM/bigdata word list or alias.
- Stop if eval benchmark fails due to provider rate limits or SQLite lock contention; decide explicitly whether to lower `SEEKTALENT_JUDGE_MAX_CONCURRENCY` or add a separate global limiter.
- Do not proceed to eval benchmark while focused tests are failing.

## Status

- Current milestone: Completed
- Last completed: M7 old/new comparison recorded
- Next action: use `0.4.7` combined summary as the generic baseline for future comparisons
- Blockers: none

## Done Checklist

- [x] M0 baseline inputs validated.
- [x] M1 compiler de-specialized.
- [x] M2 query planner contract preserved.
- [x] M3 requirements prompt updated.
- [x] M4 version bumped to `0.4.7`.
- [x] M5 focused ruff and pytest pass.
- [x] M6 12-row `0.4.7` eval benchmark completes with row parallelism 3.
- [x] M7 old/new comparison recorded.
- [x] `docs/plans/roadmap.md` updated with result summary.
- [x] Non-goals preserved.
- [x] Status and decision log updated.
