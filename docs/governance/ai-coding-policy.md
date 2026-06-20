# AI Coding Governance Policy

SeekTalent allows fast AI-assisted coding, but `main` is protected by boundaries and evidence.

## Risk Zones

Red-zone paths require owner review and focused verification:

- `src/seektalent/runtime/**`
- `src/seektalent/models.py`
- `src/seektalent/config.py`
- `.env.example`
- `src/seektalent/default.env`
- `src/seektalent/prompts/**`
- `src/seektalent/providers/**`
- `src/seektalent/core/retrieval/**`
- `src/seektalent_ui/workbench_store.py`
- `src/seektalent_ui/runtime_bridge.py`
- `src/seektalent_ui/runtime_graph.py`
- `src/seektalent_conversation_agent/**`
- `.github/**`
- `tools/**`
- `scripts/verify-dev-workbench.sh`
- `scripts/verify-red-zone.sh`

Yellow-zone paths may be delegated, but require contract tests and Workbench verification:

- `src/seektalent_ui/server.py`
- `src/seektalent_ui/workbench_routes.py`
- `src/seektalent_ui/models.py`
- `src/seektalent_ui/job_runner.py`
- `src/seektalent_ui/*projection*.py`
- `apps/web-react/src/lib/api/schema.d.ts`
- Workbench graph, note, candidate, and source-card projections

Green-zone paths are lower-risk display, docs, fixtures, and black-box test changes.

## PR Size Rules

- Ordinary PRs should touch one layer.
- Ordinary PRs should keep non-generated changed files at or below 30.
- Major-refactor goal PRs may exceed the ordinary file budget, but should stay at or below 60 non-generated changed files.
- New or growing code files should stay at or below 2,500 lines for production files and 5,000 lines for test files.
- Ordinary PRs above 500 changed lines must explain why the change is not split. This slice enforces file count, line count, and path spread by machine first.
- Red-zone PRs must be draft until verification evidence is present.
- PRs must not combine prompt, runtime, provider, BFF, frontend, and config changes. If a plan needs multiple layers, split the work into stacked PRs or land a separate owner-reviewed governance change that adjusts the gate.

## Required Evidence

- Green: relevant lint/test command.
- Yellow: relevant contract tests plus `scripts/verify-dev-workbench.sh`.
- Red: focused runtime/provider tests plus `scripts/verify-red-zone.sh`; add Workbench verification if a Workbench path changed.

## Model Permission

Low-cost or unfamiliar models may propose red-zone patches, but the patch must stay draft until owner review and red-zone verification are complete.
