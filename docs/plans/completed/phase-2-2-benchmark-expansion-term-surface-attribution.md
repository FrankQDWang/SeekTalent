# Task: Phase 2.2 Benchmark Expansion and Term/Surface Attribution

## Goal

实现 Phase 2.2 的第一轮 pilot：用当前 12 条混合 JD 跑通 benchmark，并为每个 run 产出可审计的 `term_surface_audit.json`。

业务目标：

- 先收集搜索词和检索表面词证据，不直接改变 compiler/runtime 搜索行为。
- 让后续 human-in-the-loop 能看到每个 term 的 queryability、family、是否被使用、对应 query 的 CTS recall、新候选贡献和 eval 贡献。
- 把 `AI Agent` vs `Agent`、`MultiAgent 架构` vs `MultiAgent` 这类 surface form 问题持久化为可验证证据，而不是凭经验直接写 active rule。

## Non-goals

- 不升级版本号，不发布 release。
- 不改 `compile_query_term_pool()` 的现有清洗规则。
- 不把 `AI Agent -> Agent`、`MultiAgent 架构 -> MultiAgent` 变成 active retrieval rule。
- 不接入 domain router 到 runtime 决策。
- 不实现 query policy 微服务、规则 DSL、插件系统或数据库。
- 不要求人工在 replay 前预先写完整理想搜索答案。
- 不在第一轮 pilot 里跑 full eval；先跑 `--disable-eval` smoke，eval 作为后续验收步骤。

## Done Criteria

- 本地生成 12 条混合 JD 的 pilot JSONL，包含：
  - `artifacts/benchmarks/agent_jds.jsonl`: 8 条
  - `artifacts/benchmarks/llm_training.jsonl`: 2 条
  - `artifacts/benchmarks/bigdata.jsonl`: 2 条
- `seektalent benchmark --jds-file <pilot> --disable-eval --json` 能跑完 12 条。
- 每个 run 根目录都有 `term_surface_audit.json`。
- `term_surface_audit.json` 能追溯：
  - compiled term pool
  - used query terms
  - query term metadata
  - query-level CTS raw/unique counts for queries containing the term
  - candidate retrieval surface transforms, if any
  - nullable judge fields when eval is disabled
- `benchmark_summary_*.json` 能指向每条 run 的 `term_surface_audit.json`。
- Focused tests 和 lint 通过。

## Repo Entrypoints

Read first:

1. `AGENTS.md`
2. `docs/plans/roadmap.md`
3. `docs/outputs.md`
4. `src/seektalent/models.py`
5. `src/seektalent/retrieval/query_compiler.py`
6. `src/seektalent/retrieval/query_plan.py`
7. `src/seektalent/runtime/orchestrator.py`
8. `src/seektalent/cli.py`
9. `tests/test_runtime_audit.py`
10. `tests/test_cli.py`

Likely edit:

- `src/seektalent/runtime/orchestrator.py`
- `src/seektalent/cli.py`
- `docs/outputs.md`
- `tests/test_runtime_audit.py`
- `tests/test_cli.py`

Allowed if it keeps code smaller:

- `src/seektalent/retrieval/query_plan.py`
- `tests/test_query_plan.py`

Do not edit in this phase unless this plan is updated first:

- `src/seektalent/retrieval/query_compiler.py`
- `src/seektalent/requirements/normalization.py`
- `src/seektalent/scoring/`
- `src/seektalent/finalize/`
- `src/seektalent/clients/`
- `src/seektalent_ui/`

Ignore unless reading generated evidence:

- `runs/`
- `.seektalent/`
- `.venv/`
- `.pytest_cache/`
- `dist/`

## Current Reality

- The repo is a deterministic workflow: requirement extraction -> controlled CTS retrieval -> scoring -> reflection -> finalization.
- `seektalent benchmark` currently accepts one `--jds-file`; it does not accept multiple benchmark files.
- `_load_benchmark_rows()` requires `job_title` and `job_description`; it accepts extra JSON fields but does not currently preserve benchmark group metadata in the summary.
- Current local benchmark inputs are ignored artifacts:
  - `artifacts/benchmarks/agent_jds.jsonl`, 8 rows, `agent_jd_001` through `agent_jd_008`
  - `artifacts/benchmarks/llm_training.jsonl`, 2 rows, `llm_training_jd_001` through `llm_training_jd_002`
  - `artifacts/benchmarks/bigdata.jsonl`, 2 rows, `bigdata_jd_001` through `bigdata_jd_002`
- `search_diagnostics.json` already records per-round `query_terms`, `keyword_query`, `query_term_details`, `sent_queries`, raw candidate count, unique new count, scoring and reflection summaries.
- `search_diagnostics.json` does not currently record the full term pool, retrieval surface transforms, or benchmark-level term contribution summaries.
- Runtime writes `search_diagnostics.json` before eval. Runtime has `evaluation_result` later in the same `run_async()` flow, so term/surface audit should be written after the eval/skipped branch and before `run_finished`.

## Target Behavior

### Pilot Benchmark File

Create a local ignored combined benchmark file:

```text
artifacts/benchmarks/phase_2_2_pilot.jsonl
```

Each row keeps the existing fields and may include extra grouping fields for downstream audit:

```json
{
  "jd_id": "agent_jd_001",
  "benchmark_group": "agent_llm_app",
  "job_title": "...",
  "job_description": "...",
  "hiring_notes": "..."
}
```

The current CLI may ignore `benchmark_group` at runtime; grouping can still be recovered from `jd_id` prefix or the pilot file.

### Per-run Artifact

Write this file under every run root:

```text
runs/<timestamp>_<run_id>/term_surface_audit.json
```

First-version schema:

```json
{
  "run_id": "...",
  "input": {
    "job_title": "...",
    "jd_sha256": "...",
    "notes_sha256": "..."
  },
  "summary": {
    "term_count": 0,
    "used_term_count": 0,
    "candidate_surface_rule_count": 0,
    "eval_enabled": false
  },
  "terms": [
    {
      "term": "AI Agent",
      "source": "job_title",
      "category": "role_anchor",
      "retrieval_role": "role_anchor",
      "queryability": "admitted",
      "family": "role.agent",
      "active": true,
      "used_rounds": [1, 2],
      "sent_query_count": 2,
      "queries_containing_term_raw_candidate_count": 0,
      "queries_containing_term_unique_new_count": 0,
      "queries_containing_term_duplicate_count": 0,
      "final_candidate_count_from_used_rounds": 0,
      "judge_positive_count_from_used_rounds": null,
      "human_label": null
    }
  ],
  "surfaces": [
    {
      "original_term": "AI Agent",
      "retrieval_term": "AI Agent",
      "canonical_surface": "Agent",
      "surface_family": "role.agent",
      "surface_transform": "candidate_alias_not_applied",
      "surface_transform_reason": "Candidate resume surface may use broader Agent more often than AI Agent.",
      "used_in_query": true,
      "cts_raw_hits": 0,
      "unique_new_count": 0,
      "judge_positive_count": null
    }
  ],
  "candidate_surface_rules": [
    {
      "from_original_term": "AI Agent",
      "to_retrieval_term": "Agent",
      "domain": "agent_llm",
      "applies_to": "retrieval_only",
      "status": "candidate",
      "evidence_status": "needs_surface_probe"
    }
  ]
}
```

Important naming rule: do not call query-level aggregates exact causal contribution. For multi-term queries, first-version audit can say "queries containing term", not "this term alone caused X candidates." Exact marginal lift requires an explicit A/B surface probe.

### Benchmark Summary

Extend benchmark result rows minimally:

```json
{
  "jd_id": "...",
  "run_id": "...",
  "run_dir": "...",
  "term_surface_audit_path": "..."
}
```

Do not add W&B report columns in Phase 2.2 pilot.

## Milestones

### M0. Build and Validate Pilot Inputs

Steps:

- Generate `artifacts/benchmarks/phase_2_2_pilot.jsonl` from the three local JSONL files.
- Add `benchmark_group` values:
  - `agent_llm_app` for `agent_jds.jsonl`
  - `llm_training_infra` for `llm_training.jsonl`
  - `bigdata_control` for `bigdata.jsonl`
- Validate JSONL parse, unique `jd_id`, and count 12.

Validation:

```bash
uv run python - <<'PY'
import json
from pathlib import Path

sources = [
    ("agent_llm_app", Path("artifacts/benchmarks/agent_jds.jsonl")),
    ("llm_training_infra", Path("artifacts/benchmarks/llm_training.jsonl")),
    ("bigdata_control", Path("artifacts/benchmarks/bigdata.jsonl")),
]
out = Path("artifacts/benchmarks/phase_2_2_pilot.jsonl")
rows = []
for group, path in sources:
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        row["benchmark_group"] = group
        rows.append(row)
ids = [row["jd_id"] for row in rows]
assert len(rows) == 12, len(rows)
assert len(ids) == len(set(ids)), ids
out.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
print({"count": len(rows), "output": str(out)})
PY
```

Expected: prints `count: 12`.

### M1. Write Per-run Term/Surface Audit

Steps:

- Add one small builder in `WorkflowRuntime`, likely `_build_term_surface_audit(...)`.
- Write `term_surface_audit.json` after eval or eval-skipped is known, before `run_finished`.
- Use existing `RunState` data:
  - `run_state.retrieval_state.query_term_pool`
  - `run_state.retrieval_state.sent_query_history`
  - `run_state.round_history`
  - `final_result`
  - `evaluation_result`, nullable
- Include candidate surface transforms as evidence only:
  - `AI Agent -> Agent`
  - terms containing `MultiAgent` plus suffix-like context such as `架构` -> `MultiAgent`
- Do not change query terms sent to CTS.

Acceptance:

- Eval-off run writes `term_surface_audit.json` with judge fields set to `null`.
- Existing `search_diagnostics.json` remains unchanged except if a tiny reference field is needed.
- Artifact is compact and does not dump raw resumes.

Validation:

```bash
uv run pytest tests/test_runtime_audit.py -q
```

Expected: tests pass, including new assertions for `term_surface_audit.json`.

### M2. Link Audit from Benchmark Summary

Steps:

- Update `_benchmark_command()` so each row includes `term_surface_audit_path` when the file exists under `result.run_dir`.
- Keep `--jds-file` single-file behavior; do not add multi-file CLI support in this milestone.
- Update CLI tests around benchmark JSON output.

Acceptance:

- `benchmark_summary_*.json` includes `term_surface_audit_path` for each successful row.
- Existing benchmark stdout/json contract remains backward compatible.

Validation:

```bash
uv run pytest tests/test_cli.py -q
```

Expected: tests pass.

### M3. Documentation

Steps:

- Update `docs/outputs.md` to list `term_surface_audit.json`.
- Mention that exact marginal term contribution requires a separate surface probe; current fields are query-containing aggregates.

Acceptance:

- A new agent can read `docs/outputs.md` and know when to inspect `term_surface_audit.json`.

Validation:

```bash
rg -n "term_surface_audit|surface" docs/outputs.md docs/plans/active/phase-2-2-benchmark-expansion-term-surface-attribution.md
```

Expected: both docs mention the artifact.

### M4. Focused Local Validation

Run:

```bash
uv run ruff check \
  src/seektalent/runtime/orchestrator.py \
  src/seektalent/cli.py \
  tests/test_runtime_audit.py \
  tests/test_cli.py

uv run pytest \
  tests/test_runtime_audit.py \
  tests/test_cli.py \
  tests/test_query_compiler.py \
  tests/test_query_plan.py \
  -q
```

Expected:

- Ruff passes.
- Targeted pytest passes.

Optional if time allows:

```bash
uv run pytest -q
```

### M5. 12-row Disable-eval Smoke

Run:

```bash
uv run seektalent benchmark \
  --jds-file artifacts/benchmarks/phase_2_2_pilot.jsonl \
  --env-file .env \
  --output-dir runs/phase_2_2_term_surface_pilot_$(date +%Y%m%d_%H%M%S) \
  --disable-eval \
  --json
```

Validate:

```bash
SUMMARY="$(find runs/phase_2_2_term_surface_pilot_* -maxdepth 1 -name 'benchmark_summary_*.json' | sort | tail -1)"

uv run python - "$SUMMARY" <<'PY'
import json
import sys
from pathlib import Path

summary = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert summary["count"] == 12, summary["count"]
for row in summary["runs"]:
    run_dir = Path(row["run_dir"])
    assert (run_dir / "search_diagnostics.json").exists(), row["jd_id"]
    audit_path = run_dir / "term_surface_audit.json"
    assert audit_path.exists(), row["jd_id"]
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    assert audit["terms"], row["jd_id"]
    assert "surfaces" in audit, row["jd_id"]
print("ok", summary["count"])
PY
```

Expected: prints `ok 12`.

Do not enable eval in this milestone unless the user explicitly asks; the first goal is artifact correctness and new-domain runtime stability.

## Stop Rules

- Stop if any focused test fails for a reason not explained by the current edit.
- Stop if the 12-row smoke fails before producing `search_diagnostics.json`; inspect the failing run instead of weakening assertions.
- Stop if a new-domain JD fails because compiler produces no admitted anchor/non-anchor; record the JD and trace path, then decide whether the fix belongs in Phase 2.2 or Phase 2.2.1.
- Stop if term contribution fields would require guessing exact causal attribution. Rename fields to query-containing aggregates or defer to a surface probe.
- Stop if implementing this requires a new service, database, large class hierarchy, or broad orchestrator rewrite.

## Decision Log

- 2026-04-20: Phase 2.2 starts as an audit pilot, not a policy-change phase.
- 2026-04-20: Current pilot sample count is 12, not the final 15-20 target. This is enough to validate artifact design before adding more JD rows.
- 2026-04-20: Benchmark remains single-file for now. Generate `phase_2_2_pilot.jsonl` locally instead of adding multi-file CLI support.
- 2026-04-20: `AI Agent -> Agent` and `MultiAgent 架构 -> MultiAgent` are candidate surface rules only. They must not change retrieval until trace evidence and benchmark gates support promotion.
- 2026-04-20: First-version audit records query-containing aggregates. Exact marginal term/surface lift requires a later A/B surface probe.

## Done Checklist

- [ ] `artifacts/benchmarks/phase_2_2_pilot.jsonl` generated locally with 12 rows.
- [ ] `term_surface_audit.json` written for eval-off and eval-on runs.
- [ ] Benchmark summary rows include `term_surface_audit_path`.
- [ ] `docs/outputs.md` documents the new artifact.
- [ ] Focused ruff passes.
- [ ] Focused pytest passes.
- [ ] 12-row `--disable-eval` smoke completes and validates all audit artifacts.
- [ ] Any zero-final, shortage, or new-domain failure is recorded with run path before moving to eval.
