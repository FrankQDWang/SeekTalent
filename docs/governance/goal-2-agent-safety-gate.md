# Goal 2 Agent Safety Gate

Status: active for Goal 2 planning.

## Purpose

This document is the pre-Goal-2 safety gate for the conversational Agent. It records the Agent boundary before product code is written so Goal 2 does not accidentally create broad tools, direct provider access, unsafe session state, or sensitive trace payloads.

This is not a full production security program. It is the minimum gate required before the transcript-agent backend implementation starts.

## Scope

In scope:

- Agent tool boundary.
- Conversation-agent source boundary.
- Session and trace data rules.
- Approval-required Agent actions.
- GitHub security settings confirmation.
- Low-friction repository scanning through CodeQL, Dependabot, and local PR gates.

Out of scope:

- SonarQube or SonarCloud.
- SLSA provenance.
- DAST or ZAP.
- OPA policy platform.
- SBOM publishing.
- Security dashboard work.
- Goal 2 product implementation.

## Allowed Agent Surfaces

The Goal 2 Agent may call narrow runtime-control tools only through the conversation-agent service boundary.

Allowed tool families:

- requirement extraction;
- requirement revision update;
- requirement confirmation;
- workflow start;
- workflow status read;
- pause request;
- cancel request;
- resume request;
- next-round requirement amendment;
- detail question answer;
- final summary preparation.

## Forbidden Agent Surfaces

Conversation-agent code must not:

- import `seektalent.runtime` modules, including `seektalent.runtime.orchestrator.WorkflowRuntime`;
- import `seektalent.providers`;
- import `seektalent.source_adapters`;
- import `src/seektalent_ui/workbench_store.py`, `runtime_bridge.py`, or `runtime_graph.py`;
- import browser automation modules directly;
- execute shell or subprocess commands;
- read or write runtime-control SQLite state directly;
- expose a generic tool entrypoint shaped like `run_action(action: str, payload: dict)`;
- store raw provider payloads, raw resumes, browser storage, cookies, auth headers, or tokens in session state or trace payloads.

## Required Tool Shape

Each Agent tool must be a narrow operation with a typed request and response. A model may choose when to call a tool, but the application owns execution, validation, persistence, approval, and audit.

Preferred tool names:

- `extract_requirements`
- `update_requirement_revision`
- `confirm_requirements`
- `start_workflow`
- `get_workflow_status`
- `request_pause`
- `request_cancel`
- `resume_workflow`
- `add_next_round_requirement`
- `answer_detail_question`
- `prepare_final_summary`

Forbidden shape:

```python
def run_action(action: str, payload: dict[str, object]) -> object:
    ...
```

## Approval-Required Actions

These actions require explicit user approval before execution:

- starting a live provider search;
- connecting or binding a provider account;
- resuming a paused workflow that can trigger provider or browser behavior;
- exporting candidate results;
- deleting run or conversation data;
- enabling debug trace output that may include sensitive context.

## Session And Trace Rules

Allowed in session or trace payloads:

- conversation ids;
- runtime-control run ids;
- requirement revision ids;
- event cursors;
- safe status text;
- safe artifact references;
- redacted summaries.

Forbidden in session or trace payloads:

- API keys;
- provider cookies;
- browser storage state;
- raw provider responses;
- raw resume text;
- authorization headers;
- unredacted candidate profile payloads.

Production defaults must either disable sensitive trace payloads or store compact safe references. The rules in `conversational-agent-runtime-goal-pack/04-operating-policies-and-runtime-contracts.md` remain authoritative for artifact and trace modes.

## GitHub External Settings Confirmation

Repository files cannot enforce these settings by themselves. The owner must verify them in GitHub settings or with `gh api`.

### Required Branch And PR Settings

Confirmation state: pending owner verification. On 2026-06-09, `gh api repos/FrankQDWang/SeekTalent/rulesets` returned no repository rulesets.

- Require pull request before merging.
- Require approvals.
- Require review from Code Owners.
- Dismiss stale approvals.
- Require conversation resolution.
- Block force pushes.
- Block branch deletions.
- Require status checks:
  - `quality-python`

Workbench Contract and Governance are manual workflows. CodeQL runs weekly or manually; none of them should block direct-main iteration.

### Required Secret Settings

Confirmation state: partially verified on 2026-06-09 with `gh api`.

- Secret scanning enabled: verified.
- Push protection enabled: verified.
- Custom secret patterns considered for:
  - `SEEKTALENT_TEXT_LLM_API_KEY`
  - `SEEKTALENT_CTS_TENANT_KEY`
  - `SEEKTALENT_CTS_TENANT_SECRET`
  - provider storage state strings
  - provider bearer or cookie tokens

### Optional Verification Commands

Run these when `gh` is authenticated with repository admin visibility:

```bash
gh api repos/FrankQDWang/SeekTalent --jq '{secret_scanning: .security_and_analysis.secret_scanning.status, secret_scanning_push_protection: .security_and_analysis.secret_scanning_push_protection.status}'
gh api repos/FrankQDWang/SeekTalent/rulesets --jq '.[] | {name, target, enforcement}'
gh api repos/FrankQDWang/SeekTalent/code-scanning/alerts --jq 'length'
```

If any command fails for permission reasons, record the failure text in the final PR notes and ask the repository owner to verify the setting manually before Goal 2 product implementation begins.

## Goal 2 Entry Rule

Before Goal 2 product implementation starts, the Goal 2 `progress.md` preflight evidence should reference this gate's final PR or commit and state whether GitHub external settings were verified by the repository owner.
