# Task: Phase 2.2.2 Generic Anchor Hygiene

## Goal

Fix the narrow zero-recall regression exposed by the `0.4.7` generic baseline without restoring Agent/LLM-specific retrieval rules.

The immediate target is generic job-title anchor cleanup for titles shaped like `brand/business-line - role`, where the searchable role is on the right side of a separator.

## Why Now

`docs/plans/roadmap.md` records one zero-final regression in `0.4.7`: `agent_jd_007` dropped from 10 final candidates in the Phase 2.2 disable-eval pilot to 0 final candidates in the generic baseline.

Trace evidence:

- `agent_jd_007` title is `千问-AI Agent工程师`.
- `search_diagnostics.json` shows 4 retrieval rounds, 21 sent queries, and 0 raw candidates.
- Every query kept the anchor `"千问-AI Agent"`, for example `"千问-AI Agent" Java`.
- Terminal stop guidance was `low_quality_exhausted`, so the issue is not early stopping.

## Non-goals

- Do not enter Phase 2.3 schema slimming in this task.
- Do not enable eval or judge replay for acceptance.
- Do not replay the full 12-row benchmark unless this plan is updated.
- Do not restore Agent/LLM-specific anchor rewrites, broad-domain injection, domain router, domain overlay, or active surface aliases.
- Do not change scoring, controller, reflection, finalizer, CTS clients, W&B/Weave, or CLI benchmark semantics.

## Done Criteria

- `千问-AI Agent工程师` compiles to a generic role anchor that drops the left separator prefix and title suffix.
- Existing title behavior stays stable for ordinary titles without separator prefixes.
- Focused compiler/query tests pass.
- Only `agent_jd_004` and `agent_jd_007` are replayed, with `--disable-eval`.
- Replay artifacts include `search_diagnostics.json` and `term_surface_audit.json` for both rows.
- Results and any remaining regression are recorded in this plan.

## Repo Entrypoints

Read first:

1. `AGENTS.md`
2. `docs/plans/roadmap.md`
3. `docs/plans/completed/phase-2-2-1-generic-retrieval-baseline-de-specialization.md`
4. `src/seektalent/retrieval/query_compiler.py`
5. `tests/test_query_compiler.py`
6. `src/seektalent/cli.py`
7. `artifacts/benchmarks/phase_2_2_pilot.jsonl`

Likely edit:

- `src/seektalent/retrieval/query_compiler.py`
- `tests/test_query_compiler.py`
- This plan file

Do not edit unless this plan is updated first:

- `src/seektalent/retrieval/query_plan.py`
- `src/seektalent/runtime/`
- `src/seektalent/controller/`
- `src/seektalent/reflection/`
- `src/seektalent/scoring/`
- `src/seektalent/finalize/`
- `src/seektalent/clients/`
- `src/seektalent/cli.py`

Ignore unless reading generated evidence:

- `runs/`
- `.seektalent/`
- `.venv/`
- `.pytest_cache/`
- `__pycache__/`

## Current Reality

Observed behavior:

- `_compile_role_anchors()` currently cleans `job_title` and `title_anchor_term`, strips known title suffixes, and returns one role anchor.
- `_strip_title_suffix("千问-AI Agent工程师")` returns `千问-AI Agent`.
- `agent_jd_007` kept `"千问-AI Agent"` in every query and got 0 CTS raw candidates.
- `seektalent benchmark` accepts one JSONL file and supports `--disable-eval`.

Known invariants:

- Compiler output must remain deterministic and small.
- Query terms must still come from compiler-admitted terms.
- Title cleanup must be generic, not a hand-maintained company or domain list.

## Target Behavior

- For titles with a short left prefix and a role-looking right side separated by `-`, `－`, `–`, `—`, `|`, `｜`, `:`, or `：`, compile the right side as the role anchor.
- Strip title suffixes after selecting the right side.
- Do not split ordinary titles that have no separator.
- Do not split `/` titles because terms like `搜索/推荐算法工程师` can be a single role/domain phrase.
- Do not invent aliases that are absent from the input.

Examples:

- `千问-AI Agent工程师` -> `AI Agent`
- `某业务线-后端开发工程师` -> `后端`
- `AI Agent工程师` -> `AI Agent`

## Milestones

### M1. Confirm Change Surface

Steps:

- Read compiler and compiler tests.
- Confirm benchmark replay can be narrowed through a filtered JSONL file.
- Record the no-eval replay constraint in this plan.

Acceptance:

- Edit surface is limited to the likely edit list.

Validation:

- `uv run pytest tests/test_query_compiler.py`
- Expected: baseline or final focused tests pass before replay.

### M2. Implement Generic Separator-Aware Anchor Cleanup

Steps:

- Add a small helper in `query_compiler.py` that selects the right-side role from separator-prefixed titles.
- Reuse existing suffix stripping.
- Add tests for prefixed role titles and ordinary titles.

Acceptance:

- No domain-specific string table is added.
- `千问-AI Agent工程师` compiles to `AI Agent`.
- Existing Agent/LLM de-specialization tests remain true.

Validation:

- `uv run pytest tests/test_query_compiler.py tests/test_requirement_extraction.py tests/test_query_plan.py`
- Expected: all targeted tests pass.

### M3. Replay Only Affected Rows Without Eval

Steps:

- Build a temporary filtered JSONL containing only `agent_jd_004` and `agent_jd_007`.
- Run benchmark with `--disable-eval`.
- Inspect each run's `search_diagnostics.json` and `term_surface_audit.json`.

Validation:

```bash
tmp_jds="$(mktemp)"
jq -c 'select(.jd_id == "agent_jd_004" or .jd_id == "agent_jd_007")' \
  artifacts/benchmarks/phase_2_2_pilot.jsonl > "$tmp_jds"
uv run seektalent benchmark \
  --jds-file "$tmp_jds" \
  --env-file .env \
  --output-dir runs/phase_2_2_2_anchor_hygiene_no_eval_$(date +%Y%m%d_%H%M%S) \
  --benchmark-max-concurrency 1 \
  --disable-eval \
  --json
```

Expected:

- Command completes for 2 rows.
- `evaluation_result` is `null` for both rows.
- Both rows write `search_diagnostics.json` and `term_surface_audit.json`.
- `agent_jd_007` no longer uses `"千问-AI Agent"` as the role anchor.

## Decision Log

- 2026-04-21: User requested replay only for previous problem rows and with eval disabled.
- 2026-04-21: Treat `agent_jd_007` as an anchor hygiene issue because all CTS raw counts were 0 while every query kept `"千问-AI Agent"`.
- 2026-04-21: Implemented separator-aware title cleanup in `src/seektalent/retrieval/query_compiler.py`; it handles hyphen/colon/bar prefixes but intentionally does not split `/` titles.
- 2026-04-21: Focused tests passed: `uv run pytest tests/test_query_compiler.py tests/test_requirement_extraction.py tests/test_query_plan.py` reported 24 passed.
- 2026-04-21: No-eval replay ran only `agent_jd_004` and `agent_jd_007`. Summary path: `runs/phase_2_2_2_anchor_hygiene_no_eval_20260421_085556/benchmark_summary_20260421_090749.json`.
- 2026-04-21: Replay results: `agent_jd_004` produced 12 raw, 8 unique, 8 final candidates; `agent_jd_007` produced 24 raw, 20 unique, 10 final candidates. Both rows had `evaluation_result: null`.
- 2026-04-21: `agent_jd_007` used `AI Agent` as the role anchor in all retrieval rounds; `"千问-AI Agent"` no longer appears as the active role anchor.

## Risks and Unknowns

- Dropping a left separator prefix can be wrong if the left side is an essential role qualifier rather than a company or business-line prefix.
- If `agent_jd_007` still has 0 raw candidates after anchor cleanup, the next issue is likely resume-side phrase inventory or filters, not schema slimming.
- No-eval replay can confirm recall/final candidate count but cannot measure precision or nDCG.

## Stop Rules

- Stop if focused tests fail for reasons unrelated to this change.
- Stop if the fix requires domain-specific terms or aliases.
- Stop if replay needs eval/judge to proceed.
- Stop if more than `query_compiler.py`, `tests/test_query_compiler.py`, and this plan need changes.

## Status

- Current milestone: Done
- Last completed: M3 no-eval replay of affected rows.
- Next action: Decide separately whether to run a broader no-eval smoke or proceed to Phase 2.3.
- Blockers: None.

## Done Checklist

- [x] Goal satisfied
- [x] Non-goals preserved
- [x] Focused tests pass
- [x] Only affected rows replayed
- [x] Replay used `--disable-eval`
- [x] Decision log updated
- [x] Status reflects final state
