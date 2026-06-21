# Runtime Production Readiness Matrix

These gates are task-owned planned gates. A later task must add the named tests before it can use that row as runnable verification evidence.

For stacked Graphite PRs, run path governance against each PR's immediate parent branch while the stack is unmerged. The `origin/main` governance command in agent-goal manifests is the post-restack gate: run it after lower PRs have landed and the current PR has been restacked onto the updated main branch.

| Issue | Task | Gate |
| --- | --- | --- |
| Public API contract is weak | 2 | `uv run pytest tests/test_runtime_production_contract.py -q` |
| Runtime services and artifact lifecycle are implicit | 3 | `uv run pytest tests/test_runtime_services.py tests/test_runtime_artifacts.py -q` |
| Core commit and side-effect policy are missing | 4 | `uv run pytest tests/test_runtime_core_commit.py tests/test_runtime_side_effects.py -q` |
| Source degradation semantics are incomplete | 5 | `uv run pytest tests/test_runtime_source_degradation.py -q` |
| Protected attributes and hard constraints are unsafe | 6 | `uv run pytest tests/test_runtime_constraints.py -q` |
| Prompt safety is only textual | 7 | `uv run pytest tests/test_prompt_safety.py tests/test_llm_input_prompts.py -q` |
| Composition, plugin, settings, and runtime stage boundaries are blurred | 8 | `uv run pytest tests/test_composition_boundaries.py tests/test_provider_plugins.py tests/test_settings_sections.py tests/test_runtime_stage_contracts.py -q` |
| CLI, UI, and CTS modules are too broad | 9 | `uv run pytest tests/test_cli_import_boundaries.py tests/test_ui_server_decomposition.py tests/test_cts_client_split.py -q` |
| Full production gate is missing | 10 | Top of stack: `uv run pytest && uv run ruff check && uv run ty check src tests tools && scripts/verify-red-zone.sh && scripts/verify-source-decoupling.sh && scripts/verify-dev-workbench.sh`; per PR slice: `uv run python tools/check_pr_governance.py --base <immediate-parent-branch>` before merge and `uv run python tools/check_pr_governance.py --base origin/main` after restack. |
