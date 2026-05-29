# Liepin Legacy Path Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the failed Pi/DokoBot Liepin product path while preserving deterministic OpenCLI retrieval, worker compatibility modes, and regression coverage for user-visible dual-source behavior.

**Architecture:** Stabilize the current e2e safety net first, then clean public CLI/docs/config contracts, then move active OpenCLI and Liepin policy code out of `providers.pi_agent`, then delete the remaining Pi/DokoBot modules and obsolete tests. `managed_local` and `external_http` remain as explicitly named worker compatibility modes in this pass.

**Tech Stack:** Python 3.12, Pydantic, pytest, FastAPI TestClient, Bun, Playwright, SvelteKit, existing Liepin worker contracts, existing OpenCLI browser runner.

---

## Execution Result

Status: completed on 2026-05-29.

The implementation was split into focused commits covering e2e baseline repair, public Pi/DokoBot contract removal, Liepin-owned policy and OpenCLI namespace migration, legacy `providers.pi_agent` deletion, worker compatibility guardrails, and final verification fixes.

Fresh verification included focused backend pytest coverage, Svelte check/lint/unit/build/e2e, Liepin worker boundary checks, `scripts/verify-dev-workbench.sh`, final stale-reference scan, and `git diff --check`.

---

## Spec Link

This plan implements:

`docs/superpowers/specs/2026-05-29-liepin-legacy-path-cleanup-design.md`

## Execution Notes

- Do not run a live Liepin website e2e in automated verification.
- Do not remove `managed_local` or `external_http` in this plan.
- Do not change CTS retrieval, query generation, scoring, reflection, finalization, or runtime graph semantics.
- Keep OpenCLI execution behind the Liepin provider helper; runtime and Workbench code must not execute `opencli` directly.
- Preserve historical Superpowers docs. Stale-string cleanup applies to active docs and active product/test surfaces, not archived design history.
- Use `git mv` for file moves so review remains readable.
- Stage only files touched by the current task.

## File Map

Frontend e2e guardrails:

- Modify: `apps/web-svelte/tests/e2e/dev-mode-dual-source.spec.ts`
- Modify: `apps/web-svelte/tests/e2e/workbench-spike.spec.ts`
- Modify: `apps/web-svelte/tests/e2e/workbench-parity.spec.ts`
- Modify: `apps/web-svelte/tests/e2e/parityMockApi.ts`

CLI, docs, and verification:

- Modify: `src/seektalent/cli.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_liepin_cli.py`
- Modify: `tests/test_dev_mode_readiness.py`
- Modify: `docs/configuration.md`
- Modify: `docs/development.md`
- Modify: `README.md`
- Modify: `.env.example`
- Modify: `src/seektalent/default.env`
- Modify: `scripts/verify-dev-workbench.sh`

Liepin-owned policy extraction:

- Create: `src/seektalent/providers/liepin/detail_grants.py`
- Create: `src/seektalent/providers/liepin/connection_safety.py`
- Modify: `src/seektalent/providers/liepin/policy.py`
- Modify: `src/seektalent/providers/liepin/adapter.py`
- Create: `tests/test_liepin_detail_grants.py`
- Modify: `tests/test_liepin_detail_policy.py`
- Rename: `tests/test_pi_agent_connection_safety.py` -> `tests/test_liepin_connection_safety.py`

OpenCLI namespace migration:

- Rename: `src/seektalent/providers/pi_agent/opencli_browser.py` -> `src/seektalent/providers/liepin/opencli_browser.py`
- Rename: `src/seektalent/providers/pi_agent/opencli_browser_cli.py` -> `src/seektalent/providers/liepin/opencli_browser_cli.py`
- Rename: `src/seektalent/providers/pi_agent/pi_extensions/seektalent_opencli_browser.ts` -> `src/seektalent/providers/liepin/opencli_extensions/seektalent_opencli_browser.ts`
- Rename: `src/seektalent/providers/pi_agent/boundary_registry.json` -> `src/seektalent/providers/liepin/browser_boundary_registry.json`
- Rename: `src/seektalent/providers/pi_agent/boundary_patterns.py` -> `src/seektalent/providers/liepin/browser_boundary_patterns.py`
- Rename: `tools/check_pi_agent_boundaries.py` -> `tools/check_liepin_browser_boundaries.py`
- Rename: `tests/test_pi_opencli_browser.py` -> `tests/test_liepin_opencli_browser.py`
- Rename: `tests/test_pi_agent_boundaries.py` -> `tests/test_liepin_browser_boundaries.py`
- Modify: `src/seektalent/providers/liepin/client.py`
- Modify: `src/seektalent/providers/liepin/opencli_retriever.py`
- Modify: `src/seektalent/providers/liepin/opencli_worker_client.py`
- Modify: `scripts/start-dev-workbench.sh`
- Modify: `apps/liepin-worker/scripts/checkBoundaries.ts`
- Modify: `apps/liepin-worker/tests/boundaries.test.ts`
- Modify: `tests/test_liepin_boundaries.py`
- Modify: `tests/test_liepin_opencli_retriever.py`
- Modify: `tests/test_liepin_opencli_workflow.py`
- Create: `tests/test_liepin_opencli_local_setup.py`
- Read before deletion: `tests/test_pi_dokobot_local_setup.py` for surviving OpenCLI launcher/setup coverage

Legacy deletion:

- Delete: `src/seektalent/providers/pi_agent/artifacts.py`
- Delete: `src/seektalent/providers/pi_agent/capabilities.py`
- Delete: `src/seektalent/providers/pi_agent/connection_safety.py`
- Delete: `src/seektalent/providers/pi_agent/contracts.py`
- Delete: `src/seektalent/providers/pi_agent/dokobot_client.py`
- Delete: `src/seektalent/providers/pi_agent/local_setup.py`
- Delete: `src/seektalent/providers/pi_agent/locks.py`
- Delete: `src/seektalent/providers/pi_agent/payload_firewall.py`
- Delete: `src/seektalent/providers/pi_agent/pi_external.py`
- Delete: `src/seektalent/providers/pi_agent/validation_errors.py`
- Delete: `src/seektalent/providers/pi_agent/pi_extensions/bailian_deepseek.ts`
- Delete: `src/seektalent/providers/pi_agent/pi_extensions/tsconfig.json`
- Delete: `src/seektalent/providers/pi_agent/__init__.py`
- Delete: `tests/test_dokobot_capabilities.py`
- Delete: `tests/test_pi_agent_artifacts.py`
- Delete: `tests/test_pi_agent_contracts.py`
- Delete: `tests/test_pi_dokobot_local_setup.py`
- Delete: `tests/test_pi_external_agent.py`
- Delete: `tests/test_pi_payload_firewall.py`
- Modify: `tests/test_workbench_note_writer.py`

---

### Task 1: Restore E2E Baseline And Replace Legacy Fixture Language

**Files:**
- Modify: `apps/web-svelte/tests/e2e/dev-mode-dual-source.spec.ts`
- Modify: `apps/web-svelte/tests/e2e/workbench-spike.spec.ts`
- Modify: `apps/web-svelte/tests/e2e/workbench-parity.spec.ts`
- Modify: `apps/web-svelte/tests/e2e/parityMockApi.ts`

- [x] **Step 1: Confirm the current e2e inventory**

Run:

```bash
cd apps/web-svelte && bun run test:e2e -- --list
```

Expected:

```text
Total: 13 tests in 3 files
```

- [x] **Step 2: Add a runtime graph mock to the dev-mode dual-source e2e**

In `apps/web-svelte/tests/e2e/dev-mode-dual-source.spec.ts`, add this route before the existing `/graph-candidates` route:

```ts
		if (requestUrl.pathname === `/api/workbench/sessions/${SESSION_ID}/runtime-graph`) {
			return json(runtimeGraph(sourceState));
		}
```

Add these helpers near `buildSession`:

```ts
function runtimeGraph(sourceState: typeof runtimeSourceState) {
	const liepinStatus = sourceState.coverageStatus === 'pending' ? 'queued' : 'blocked';
	const finalStatus = sourceState.coverageStatus === 'pending' ? 'queued' : 'degraded';
	return {
		sessionId: SESSION_ID,
		generatedAt: '2026-05-18T00:05:00Z',
		completionText: sourceState.coverageStatus === 'pending' ? null : 'CTS 已完成，猎聘通道降级。',
		nodes: [
			runtimeGraphNode(`${SESSION_ID}:job`, 'job', 'Dev Mode Svelte UI Engineer', 'completed', 'all'),
			runtimeGraphNode(`${SESSION_ID}:cts`, 'source_result', 'CTS 候选人', 'completed', 'cts'),
			runtimeGraphNode(`${SESSION_ID}:liepin`, 'source_result', '猎聘候选人', liepinStatus, 'liepin'),
			runtimeGraphNode(`${SESSION_ID}:final`, 'final', '最终短名单', finalStatus, 'all')
		],
		edges: [
			{ edgeId: `${SESSION_ID}:job-cts`, fromNodeId: `${SESSION_ID}:job`, toNodeId: `${SESSION_ID}:cts`, label: '检索' },
			{ edgeId: `${SESSION_ID}:job-liepin`, fromNodeId: `${SESSION_ID}:job`, toNodeId: `${SESSION_ID}:liepin`, label: '检索' },
			{ edgeId: `${SESSION_ID}:cts-final`, fromNodeId: `${SESSION_ID}:cts`, toNodeId: `${SESSION_ID}:final`, label: '合并' },
			{ edgeId: `${SESSION_ID}:liepin-final`, fromNodeId: `${SESSION_ID}:liepin`, toNodeId: `${SESSION_ID}:final`, label: '合并' }
		]
	};
}

function runtimeGraphNode(
	nodeId: string,
	kind: string,
	label: string,
	status: string,
	sourceKind: 'cts' | 'liepin' | 'all'
) {
	return {
		nodeId,
		kind,
		label,
		summaryText: label,
		status,
		stage: kind === 'final' ? 'finalization' : kind === 'job' ? 'intake' : 'retrieval',
		sourceKind,
		lane: sourceKind === 'all' ? 'shared' : sourceKind,
		roundNo: kind === 'job' ? 0 : kind === 'final' ? 2 : 1,
		candidateScope: {
			scopeKind: kind === 'final' ? 'final' : kind === 'job' ? 'none' : 'round_recall',
			sourceKind,
			roundNo: kind === 'job' ? null : kind === 'final' ? 2 : 1,
			reason: null
		},
		eventIds: [],
		detailSections: []
	};
}
```

- [x] **Step 3: Replace stale Pi/DokoBot dev-mode mock copy**

In `dev-mode-dual-source.spec.ts`, replace the `liepin_pi` component inside `devModeStatus.components` with:

```ts
		{
			name: 'liepin_opencli',
			label: '猎聘浏览器通道',
			status: 'needs_setup',
			reasonCode: 'liepin_opencli_extension_disconnected',
			authNote: '请确认本机浏览器助手已连接'
		}
```

Replace this assertion:

```ts
		await expect(page.getByText('Liepin Pi Agent')).toHaveCount(0);
```

with:

```ts
		await expect(page.getByText(/Liepin Pi Agent|DokoBot|dokobot|pi_agent/i)).toHaveCount(0);
```

- [x] **Step 4: Add a runtime graph mock to the spike e2e**

In `apps/web-svelte/tests/e2e/workbench-spike.spec.ts`, add this route before the existing `/graph-candidates` route:

```ts
		if (requestUrl.pathname === `/api/workbench/sessions/${SESSION_ID}/runtime-graph`) {
			return json(runtimeGraph());
		}
```

Add this helper near `const resumeSnapshot`:

```ts
function runtimeGraph() {
	return {
		sessionId: SESSION_ID,
		generatedAt: '2026-05-10T00:05:00Z',
		completionText: '完成 CTS 与猎聘候选人合并排序。',
		nodes: [
			{
				nodeId: `${SESSION_ID}:job`,
				kind: 'job',
				label: 'AI Recruiting Platform VP',
				summaryText: '岗位需求已进入检索工作流。',
				status: 'completed',
				stage: 'intake',
				sourceKind: 'all',
				lane: 'shared',
				roundNo: 0,
				candidateScope: { scopeKind: 'none', sourceKind: 'all', roundNo: null, reason: null },
				eventIds: [],
				detailSections: []
			},
			{
				nodeId: `${SESSION_ID}:final`,
				kind: 'final',
				label: '最终短名单',
				summaryText: '运行时已合并来源并生成最终候选池。',
				status: 'completed',
				stage: 'finalization',
				sourceKind: 'all',
				lane: 'shared',
				roundNo: 2,
				candidateScope: { scopeKind: 'final', sourceKind: 'all', roundNo: 2, reason: null },
				eventIds: [],
				detailSections: []
			}
		],
		edges: [
			{
				edgeId: `${SESSION_ID}:job-final`,
				fromNodeId: `${SESSION_ID}:job`,
				toNodeId: `${SESSION_ID}:final`,
				label: '合并'
			}
		]
	};
}
```

- [x] **Step 5: Strengthen active e2e no-leak strings**

In `workbench-parity.spec.ts`, extend `RAW_LEAK_STRINGS`:

```ts
const RAW_LEAK_STRINGS = [
	'parity-csrf-token',
	'secret-token',
	'raw_provider_payload',
	'Authorization',
	'/private/',
	'/Users/',
	'storage state',
	'Liepin Pi Agent',
	'DokoBot',
	'dokobot_action',
	'live-pi-agent',
	'pi_agent'
];
```

In `parityMockApi.ts`, change the blocked Liepin warning to OpenCLI-safe business copy:

```ts
const liepinWarning =
	sourceState === 'blocked'
		? '请先在本机 Chrome 登录猎聘并保持会话有效，系统会在检索时使用该登录态。'
		: sourceState === 'partial'
			? '猎聘已返回有效卡片，详情额度仍待审批。'
			: null;
```

Keep this copy free of `OpenCLI`, `Pi`, and `DokoBot`.

- [x] **Step 6: Lock the user-visible degraded-state contract**

Keep Workbench e2e assertions aligned with this state table:

| Fixture state | Required user-visible behavior | Forbidden leakage |
| --- | --- | --- |
| `blocked` | Source card explains that the user must keep the local Chrome Liepin session valid; CTS candidates and the final shortlist remain visible. | `OpenCLI`, `Pi`, `DokoBot`, `dokobot_action`, `live-pi-agent`, `pi_agent`, raw provider payloads, local file paths. |
| `partial` | Source card explains that Liepin cards returned but detail quota/approval is still pending; final shortlist remains visible. | Same forbidden leakage list as `blocked`. |
| browser unavailable / setup missing | Dev-mode readiness names the business-facing local browser channel and asks the operator to connect the browser helper; it does not name old implementation paths. | Same forbidden leakage list as `blocked`. |

Add or update e2e assertions so the `blocked` and `partial` fixtures verify:

```ts
await expect(page.getByText('请先在本机 Chrome 登录猎聘并保持会话有效，系统会在检索时使用该登录态。')).toBeVisible();
await expect(page.getByText('最终短名单')).toBeVisible();
await page.setViewportSize({ width: 390, height: 844 });
await expect(page.locator('body')).not.toHaveCSS('overflow-x', 'scroll');
```

For the partial fixture, assert:

```ts
await expect(page.getByText('猎聘已返回有效卡片，详情额度仍待审批。')).toBeVisible();
```

- [x] **Step 7: Run focused e2e tests**

Run:

```bash
cd apps/web-svelte && bun run test:e2e -- dev-mode-dual-source.spec.ts workbench-spike.spec.ts workbench-parity.spec.ts
```

Expected:

```text
13 passed
```

- [x] **Step 8: Commit the e2e baseline repair**

```bash
git add apps/web-svelte/tests/e2e/dev-mode-dual-source.spec.ts \
  apps/web-svelte/tests/e2e/workbench-spike.spec.ts \
  apps/web-svelte/tests/e2e/workbench-parity.spec.ts \
  apps/web-svelte/tests/e2e/parityMockApi.ts
git commit -m "test: stabilize liepin opencli workbench e2e"
```

---

### Task 2: Clean Public CLI And Documentation Contracts

**Files:**
- Modify: `src/seektalent/cli.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_liepin_cli.py`
- Modify: `docs/configuration.md`
- Modify: `docs/development.md`
- Modify: `README.md`
- Modify: `.env.example`
- Modify: `src/seektalent/default.env`

- [x] **Step 1: Add failing CLI tests for removed Pi worker mode**

In `tests/test_liepin_cli.py`, replace `test_liepin_smoke_preserves_explicit_pi_agent_mode` with:

```python
def test_liepin_smoke_rejects_removed_pi_agent_mode(capsys) -> None:
    status = main(["liepin-smoke", "--worker-mode", "pi_agent"])

    captured = capsys.readouterr()
    assert status == 2
    assert "invalid choice" in captured.err
    assert "pi_agent" in captured.err
```

Add:

```python
def test_liepin_smoke_live_uses_opencli_when_configured(monkeypatch, tmp_path: Path) -> None:
    db_path, gate_ref, connection_id, provider_account_hash = _approved_gate_and_connection(tmp_path)
    worker = RecordingSmokeWorker(connection_id=connection_id, provider_account_hash=provider_account_hash)
    built_settings: list[object] = []

    monkeypatch.setattr(
        cli,
        "AppSettings",
        lambda: make_settings(
            liepin_worker_mode="disabled",
            liepin_browser_action_backend="opencli",
            liepin_api_token="worker-token",
            liepin_detail_open_approval_secret="detail-approval-secret",
        ),
    )
    monkeypatch.setattr(
        cli,
        "build_liepin_worker_client",
        lambda settings: built_settings.append(settings) or worker,
        raising=False,
    )

    status = main(
        [
            "liepin-smoke",
            "--live",
            "--tenant-id",
            "tenant-a",
            "--workspace-id",
            "workspace-a",
            "--actor-id",
            "actor-a",
            "--connection-id",
            connection_id,
            "--compliance-gate-ref",
            gate_ref,
            "--worker-mode",
            "opencli",
            "--db-path",
            str(db_path),
        ]
    )

    assert status == 0
    assert built_settings[0].liepin_worker_mode == "opencli"
    assert built_settings[0].liepin_browser_action_backend == "opencli"
```

In `tests/test_cli.py`, remove assertions that advertise `pi-agent` from the inspect surface and add an assertion that the inspect output includes `liepin-smoke` OpenCLI examples but not `pi-agent`.

- [x] **Step 2: Run the failing CLI tests**

Run:

```bash
uv run pytest -q tests/test_liepin_cli.py::test_liepin_smoke_rejects_removed_pi_agent_mode \
  tests/test_liepin_cli.py::test_liepin_smoke_live_uses_opencli_when_configured
```

Expected: at least one test fails because `src/seektalent/cli.py` still accepts `pi_agent` and does not expose `opencli` for `liepin-smoke --worker-mode`.

- [x] **Step 3: Remove Pi/DokoBot CLI surfaces**

In `src/seektalent/cli.py`:

- remove imports of `build_pi_agent_local_setup_status`, `render_pi_agent_init_preview`, and `write_project_pi_mcp_config`;
- remove `pi-agent` from command groups and inspect output;
- remove `_pi_agent_init_command`;
- remove `PI_LOCAL_SETUP_ENV_KEYS`, `_pi_local_setup_env`, `_doctor_workspace_root_for_pi_setup`, `_liepin_pi_local_setup_check`, and `_liepin_pi_live_agent_check`;
- remove `doctor --live-pi-agent`;
- change `liepin-smoke --worker-mode` choices to:

```python
choices=["fake_fixture", "managed_local", "external_http", "opencli"]
```

- change live smoke settings override logic so `--worker-mode opencli` sets both:

```python
settings_data["liepin_worker_mode"] = "opencli"
settings_data["liepin_browser_action_backend"] = "opencli"
```

Keep `managed_local` and `external_http` behavior unchanged.

- [x] **Step 4: Update active docs and env examples**

In `docs/configuration.md`, replace the Pi/DokoBot Liepin section with:

```markdown
### Liepin Local Browser Retrieval

Local Liepin retrieval uses deterministic OpenCLI browser actions by default in the packaged Workbench configuration.

| Setting | Meaning |
| --- | --- |
| `SEEKTALENT_LIEPIN_WORKER_MODE=opencli` | Use the deterministic OpenCLI Liepin retriever. |
| `SEEKTALENT_LIEPIN_BROWSER_ACTION_BACKEND=opencli` | Enable the local browser action backend used by the OpenCLI retriever. |
| `SEEKTALENT_LIEPIN_OPENCLI_COMMAND=apps/web-svelte/node_modules/.bin/opencli` | OpenCLI command resolved from the code root when relative. |
| `SEEKTALENT_LIEPIN_OPENCLI_SESSION=seektalent-liepin` | Local OpenCLI browser session name. |

`managed_local` and `external_http` remain worker compatibility modes for the Bun `apps/liepin-worker` connector. They are not Pi/DokoBot fallbacks.
```

In `docs/development.md`, replace the old Pi live probe block with:

```markdown
For local Liepin browser readiness, run the Workbench launcher and check the OpenCLI browser helper:

```bash
scripts/start-dev-workbench.sh
uv run seektalent doctor --json
```

Automated tests do not run live Liepin website e2e. Use targeted smoke checks only when a human operator has prepared a local Chrome/OpenCLI session.
```

Update `.env.example`, `src/seektalent/default.env`, and `README.md` so active setup text names OpenCLI and worker compatibility only.

- [x] **Step 5: Run CLI and docs string checks**

Run:

```bash
uv run pytest -q tests/test_cli.py tests/test_liepin_cli.py tests/test_liepin_config.py
rg -n "pi-agent|live-pi-agent|dokobot_action|Liepin Pi Agent|DokoBot|SEEKTALENT_LIEPIN_PI|SEEKTALENT_LIEPIN_DOKOBOT" \
  README.md docs/configuration.md docs/development.md .env.example src/seektalent/default.env src/seektalent/cli.py
```

Expected:

```text
pytest passes
rg exits 1 with no matches
```

- [x] **Step 6: Commit public contract cleanup**

```bash
git add src/seektalent/cli.py tests/test_cli.py tests/test_liepin_cli.py \
  docs/configuration.md docs/development.md README.md .env.example src/seektalent/default.env
git commit -m "refactor: remove liepin pi dokobot public contract"
```

---

### Task 3: Move Liepin Policy And Connection Safety Out Of Pi Namespace

**Files:**
- Create: `src/seektalent/providers/liepin/detail_grants.py`
- Create: `src/seektalent/providers/liepin/connection_safety.py`
- Modify: `src/seektalent/providers/liepin/policy.py`
- Modify: `src/seektalent/providers/liepin/adapter.py`
- Create: `tests/test_liepin_detail_grants.py`
- Modify: `tests/test_liepin_detail_policy.py`
- Rename: `tests/test_pi_agent_connection_safety.py` -> `tests/test_liepin_connection_safety.py`

- [x] **Step 1: Write failing import-boundary test**

Create or extend `tests/test_liepin_boundaries.py` with:

```python
def test_liepin_provider_does_not_import_pi_agent_namespace() -> None:
    offenders: list[str] = []
    for path in Path("src/seektalent/providers/liepin").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "seektalent.providers.pi_agent" in text:
            offenders.append(path.as_posix())

    assert offenders == []
```

- [x] **Step 2: Run the boundary test and verify it fails**

Run:

```bash
uv run pytest -q tests/test_liepin_boundaries.py::test_liepin_provider_does_not_import_pi_agent_namespace
```

Expected: fails with `policy.py` and `adapter.py` in the offender list.

- [x] **Step 3: Add Liepin-owned detail grant types**

Create `src/seektalent/providers/liepin/detail_grants.py`:

```python
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


NonEmptyStr = Annotated[str, Field(min_length=1)]


class LiepinBoundaryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class LiepinDetailFailureCode(StrEnum):
    DETAIL_OPEN_GRANT_MISSING = "detail_open_grant_missing"
    DETAIL_OPEN_GRANT_EXPIRED = "detail_open_grant_expired"
    DETAIL_OPEN_GRANT_CANDIDATE_MISMATCH = "detail_open_grant_candidate_mismatch"
    DETAIL_OPEN_GRANT_SOURCE_RUN_MISMATCH = "detail_open_grant_source_run_mismatch"


def require_timezone_aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value


class DetailOpenGrant(LiepinBoundaryModel):
    schema_version: Literal["detail-open-grant-v1"]
    approval_id: NonEmptyStr
    budget_reservation_id: NonEmptyStr
    candidate_ref: NonEmptyStr
    source_run_id: NonEmptyStr
    provider: Literal["liepin"]
    max_detail_opens: int = Field(default=1, ge=1, le=1)
    expires_at: datetime
    issued_by: Literal["workflow_runtime"]
    idempotency_key: NonEmptyStr
    grant_signature: str = Field(min_length=1, repr=False)

    @field_validator("expires_at")
    @classmethod
    def expires_at_must_be_timezone_aware(cls, value: datetime) -> datetime:
        return require_timezone_aware(value, "expires_at")
```

- [x] **Step 4: Preserve detail grant model validation coverage**

Create `tests/test_liepin_detail_grants.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from seektalent.providers.liepin.detail_grants import DetailOpenGrant, LiepinDetailFailureCode


def _grant(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "schema_version": "detail-open-grant-v1",
        "approval_id": "approval-1",
        "budget_reservation_id": "budget-1",
        "candidate_ref": "candidate-1",
        "source_run_id": "source-run-1",
        "provider": "liepin",
        "max_detail_opens": 1,
        "expires_at": datetime.now(UTC) + timedelta(minutes=5),
        "issued_by": "workflow_runtime",
        "idempotency_key": "detail-open-1",
        "grant_signature": "signed",
    }
    data.update(overrides)
    return data


def test_detail_open_grant_accepts_valid_payload() -> None:
    grant = DetailOpenGrant.model_validate(_grant())

    assert grant.provider == "liepin"
    assert grant.max_detail_opens == 1


def test_detail_open_grant_requires_timezone_aware_expiry() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        DetailOpenGrant.model_validate(_grant(expires_at=datetime(2026, 5, 29, 12, 0, 0)))


def test_detail_open_grant_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        DetailOpenGrant.model_validate(_grant(raw_provider_payload={"secret": "value"}))


def test_liepin_detail_failure_codes_match_public_policy_values() -> None:
    assert {item.value for item in LiepinDetailFailureCode} == {
        "detail_open_grant_missing",
        "detail_open_grant_expired",
        "detail_open_grant_candidate_mismatch",
        "detail_open_grant_source_run_mismatch",
    }
```

These tests replace the `DetailOpenGrant` coverage currently hidden in `tests/test_pi_agent_contracts.py`; do not delete that old test file until these tests pass.

- [x] **Step 5: Add Liepin-owned connection safety types**

Create `src/seektalent/providers/liepin/connection_safety.py`:

```python
from __future__ import annotations

from datetime import datetime
from typing import Literal, NoReturn

from pydantic import field_validator

from seektalent.providers.liepin.detail_grants import (
    LiepinBoundaryModel,
    NonEmptyStr,
    require_timezone_aware,
)


DEFAULT_SENSITIVE_MATERIAL_POLICY_ID = "liepin-sensitive-material-protection-v1"
TransportMode = Literal["local_only", "remote_e2e_allowed"]


class ProviderConnectionSafetyValidationError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ProviderConnectionSafetyRecord(LiepinBoundaryModel):
    schema_version: Literal["provider-connection-safety-v1"]
    provider: Literal["liepin"]
    connection_id: NonEmptyStr
    workspace_id: NonEmptyStr
    user_id: NonEmptyStr
    provider_account_hash: NonEmptyStr
    login_state: Literal["verified", "expired", "verification_required"]
    connection_owner_verified: bool
    sensitive_material_policy_id: NonEmptyStr
    transport_policy: Literal["local_only", "remote_e2e_allowed", "remote_forbidden"]
    verified_at: datetime
    expires_at: datetime
    issued_by: Literal["workflow_runtime"]
    policy_version: NonEmptyStr

    @field_validator("verified_at", "expires_at")
    @classmethod
    def datetimes_must_be_timezone_aware(cls, value: datetime, info: object) -> datetime:
        field_name = getattr(info, "field_name", "datetime")
        return require_timezone_aware(value, field_name)


def validate_provider_connection_safety(
    record: ProviderConnectionSafetyRecord | None,
    *,
    provider: Literal["liepin"],
    connection_id: str,
    workspace_id: str,
    user_id: str,
    provider_account_hash: str,
    transport: TransportMode,
    now: datetime,
    sensitive_material_policy_id: str = DEFAULT_SENSITIVE_MATERIAL_POLICY_ID,
) -> None:
    require_timezone_aware(now, "now")
    if record is None:
        _raise("connection_safety_missing")
    assert record is not None
    if record.provider != provider:
        _raise("connection_safety_provider_mismatch")
    if record.connection_id != connection_id:
        _raise("connection_safety_connection_mismatch")
    if record.workspace_id != workspace_id:
        _raise("connection_safety_workspace_mismatch")
    if record.user_id != user_id:
        _raise("connection_safety_user_mismatch")
    if not record.connection_owner_verified:
        _raise("connection_safety_owner_unverified")
    if record.expires_at <= now:
        _raise("connection_safety_expired")
    if record.login_state != "verified":
        _raise("connection_safety_login_unverified")
    if record.provider_account_hash != provider_account_hash:
        _raise("connection_safety_provider_account_mismatch")
    if record.sensitive_material_policy_id != sensitive_material_policy_id:
        _raise("connection_safety_material_policy_mismatch")
    if not _transport_allowed(record.transport_policy, transport):
        _raise("connection_safety_transport_denied")


def _transport_allowed(record_policy: str, requested_transport: TransportMode) -> bool:
    if requested_transport == "local_only":
        return record_policy in {"local_only", "remote_e2e_allowed", "remote_forbidden"}
    return record_policy == "remote_e2e_allowed"


def _raise(code: str) -> NoReturn:
    raise ProviderConnectionSafetyValidationError(code)
```

- [x] **Step 6: Update imports**

In `src/seektalent/providers/liepin/policy.py`, replace:

```python
from seektalent.providers.pi_agent.contracts import DetailOpenGrant, PiAgentFailureCode
```

with:

```python
from seektalent.providers.liepin.detail_grants import DetailOpenGrant, LiepinDetailFailureCode
```

Replace `PiAgentFailureCode` references with `LiepinDetailFailureCode`.

In `src/seektalent/providers/liepin/adapter.py`, replace:

```python
from seektalent.providers.pi_agent.connection_safety import (
```

with:

```python
from seektalent.providers.liepin.connection_safety import (
```

Update `tests/test_liepin_detail_policy.py` and renamed `tests/test_liepin_connection_safety.py` imports to the Liepin-owned modules.

- [x] **Step 7: Run focused policy tests**

Run:

```bash
uv run pytest -q tests/test_liepin_detail_grants.py tests/test_liepin_detail_policy.py tests/test_liepin_connection_safety.py \
  tests/test_liepin_boundaries.py::test_liepin_provider_does_not_import_pi_agent_namespace
```

Expected:

```text
passes
```

- [x] **Step 8: Commit policy extraction**

```bash
git add src/seektalent/providers/liepin/detail_grants.py \
  src/seektalent/providers/liepin/connection_safety.py \
  src/seektalent/providers/liepin/policy.py \
  src/seektalent/providers/liepin/adapter.py \
  tests/test_liepin_detail_grants.py \
  tests/test_liepin_detail_policy.py \
  tests/test_liepin_connection_safety.py \
  tests/test_liepin_boundaries.py
git add -u tests/test_pi_agent_connection_safety.py
git commit -m "refactor: move liepin policy types out of pi namespace"
```

---

### Task 4: Move Active OpenCLI Code Out Of Pi Namespace

**Files:**
- Rename: `src/seektalent/providers/pi_agent/opencli_browser.py` -> `src/seektalent/providers/liepin/opencli_browser.py`
- Rename: `src/seektalent/providers/pi_agent/opencli_browser_cli.py` -> `src/seektalent/providers/liepin/opencli_browser_cli.py`
- Rename: `src/seektalent/providers/pi_agent/pi_extensions/seektalent_opencli_browser.ts` -> `src/seektalent/providers/liepin/opencli_extensions/seektalent_opencli_browser.ts`
- Rename: `src/seektalent/providers/pi_agent/boundary_registry.json` -> `src/seektalent/providers/liepin/browser_boundary_registry.json`
- Rename: `src/seektalent/providers/pi_agent/boundary_patterns.py` -> `src/seektalent/providers/liepin/browser_boundary_patterns.py`
- Rename: `tools/check_pi_agent_boundaries.py` -> `tools/check_liepin_browser_boundaries.py`
- Rename: `tests/test_pi_opencli_browser.py` -> `tests/test_liepin_opencli_browser.py`
- Rename: `tests/test_pi_agent_boundaries.py` -> `tests/test_liepin_browser_boundaries.py`
- Modify: `src/seektalent/providers/liepin/client.py`
- Modify: `src/seektalent/providers/liepin/opencli_retriever.py`
- Modify: `src/seektalent/providers/liepin/opencli_worker_client.py`
- Modify: `scripts/start-dev-workbench.sh`
- Modify: `apps/liepin-worker/scripts/checkBoundaries.ts`
- Modify: `apps/liepin-worker/tests/boundaries.test.ts`
- Modify: `tests/test_liepin_boundaries.py`
- Modify: `tests/test_liepin_opencli_retriever.py`
- Modify: `tests/test_liepin_opencli_workflow.py`

- [x] **Step 1: Move files with git mv**

Run:

```bash
mkdir -p src/seektalent/providers/liepin/opencli_extensions
git mv src/seektalent/providers/pi_agent/opencli_browser.py src/seektalent/providers/liepin/opencli_browser.py
git mv src/seektalent/providers/pi_agent/opencli_browser_cli.py src/seektalent/providers/liepin/opencli_browser_cli.py
git mv src/seektalent/providers/pi_agent/pi_extensions/seektalent_opencli_browser.ts \
  src/seektalent/providers/liepin/opencli_extensions/seektalent_opencli_browser.ts
git mv src/seektalent/providers/pi_agent/boundary_registry.json \
  src/seektalent/providers/liepin/browser_boundary_registry.json
git mv src/seektalent/providers/pi_agent/boundary_patterns.py \
  src/seektalent/providers/liepin/browser_boundary_patterns.py
git mv tools/check_pi_agent_boundaries.py tools/check_liepin_browser_boundaries.py
git mv tests/test_pi_opencli_browser.py tests/test_liepin_opencli_browser.py
git mv tests/test_pi_agent_boundaries.py tests/test_liepin_browser_boundaries.py
```

- [x] **Step 2: Update Python imports and monkeypatch paths**

List the remaining references:

```bash
rg -l "providers\\.pi_agent\\.opencli_browser|providers\\.pi_agent\\.opencli_browser_cli|check_pi_agent_boundaries|test_pi_opencli_browser|test_pi_agent_boundaries" \
  src tests tools scripts apps
```

Patch each listed file explicitly with `apply_patch`; do not use broad in-place shell rewrites for this migration. Expected active files include:

- `src/seektalent/providers/liepin/client.py`
- `src/seektalent/providers/liepin/opencli_browser.py`
- `src/seektalent/providers/liepin/opencli_browser_cli.py`
- `scripts/start-dev-workbench.sh`
- `tools/check_liepin_browser_boundaries.py`
- `tests/test_liepin_opencli_browser.py`
- `tests/test_liepin_opencli_retriever.py`
- `tests/test_liepin_opencli_workflow.py`
- `tests/test_liepin_browser_boundaries.py`
- `tests/test_liepin_boundaries.py`
- `apps/liepin-worker/scripts/checkBoundaries.ts`
- `apps/liepin-worker/tests/boundaries.test.ts`

Then open the changed files and verify imports are readable. Do not keep compatibility imports from `providers.pi_agent`.

- [x] **Step 3: Update the OpenCLI extension helper module**

In `src/seektalent/providers/liepin/opencli_extensions/seektalent_opencli_browser.ts`, set:

```ts
const HELPER_MODULE = "seektalent.providers.liepin.opencli_browser_cli";
```

- [x] **Step 4: Update internal module self-invocation**

In `src/seektalent/providers/liepin/opencli_browser.py`, replace the `watch_idle_lease` module tuple with:

```python
(sys.executable, "-m", "seektalent.providers.liepin.opencli_browser_cli", "watch_idle_lease")
```

In `src/seektalent/providers/liepin/opencli_browser_cli.py`, import from the moved module:

```python
from seektalent.providers.liepin.opencli_browser import (
```

- [x] **Step 5: Update boundary registry loader and worker boundary checker**

In `src/seektalent/providers/liepin/browser_boundary_patterns.py`, load the renamed JSON:

```python
_REGISTRY_PATH = Path(__file__).with_name("browser_boundary_registry.json")
```

In `tools/check_liepin_browser_boundaries.py`, import:

```python
from seektalent.providers.liepin.browser_boundary_patterns import (
```

Set scan roots:

```python
_PYTHON_SCAN_ROOTS = (
    Path("src/seektalent/providers/liepin"),
)
```

In `apps/liepin-worker/scripts/checkBoundaries.ts`, update the registry path:

```ts
const registryUrl = new URL("../../../src/seektalent/providers/liepin/browser_boundary_registry.json", import.meta.url);
```

- [x] **Step 6: Update OpenCLI allowlists**

In `tests/test_liepin_boundaries.py`, replace `OPENCLI_PYTHON_ALLOWLIST` with:

```python
OPENCLI_PYTHON_ALLOWLIST = {
    "src/seektalent/providers/liepin/client.py",
    "src/seektalent/providers/liepin/opencli_worker_client.py",
    "src/seektalent/providers/liepin/opencli_retriever.py",
    "src/seektalent/providers/liepin/opencli_browser.py",
    "src/seektalent/providers/liepin/opencli_browser_cli.py",
}
```

- [x] **Step 7: Migrate surviving OpenCLI launcher/setup coverage**

Create `tests/test_liepin_opencli_local_setup.py` with the surviving OpenCLI assertions from `tests/test_pi_dokobot_local_setup.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

from seektalent.dev_mode import build_dev_mode_env_diagnostics


def _write_opencli_binary(root: Path) -> Path:
    opencli_bin = root / "apps" / "web-svelte" / "node_modules" / ".bin" / "opencli"
    opencli_bin.parent.mkdir(parents=True, exist_ok=True)
    opencli_bin.write_text("#!/usr/bin/env node\n", encoding="utf-8")
    opencli_bin.chmod(0o755)
    return opencli_bin


def test_env_diagnostics_reports_configured_opencli_without_legacy_mcp(tmp_path: Path) -> None:
    opencli_bin = _write_opencli_binary(tmp_path)

    status = build_dev_mode_env_diagnostics(
        {
            "SEEKTALENT_TEXT_LLM_API_KEY": "sk-test",
            "SEEKTALENT_CTS_TENANT_KEY": "tenant-key",
            "SEEKTALENT_CTS_TENANT_SECRET": "tenant-secret",
            "SEEKTALENT_LIEPIN_WORKER_MODE": "opencli",
            "SEEKTALENT_LIEPIN_BROWSER_ACTION_BACKEND": "opencli",
            "SEEKTALENT_LIEPIN_ACCOUNT_BINDING_SECRET": "account-secret",
            "SEEKTALENT_LIEPIN_OPENCLI_COMMAND": str(opencli_bin),
        },
        workspace_root=tmp_path,
    )

    public = status.model_dump(mode="json")
    raw = json.dumps(public, sort_keys=True)
    components = {item["name"]: item for item in public["components"]}
    assert status.overallStatus in {"ready", "warning"}
    assert components["liepin_opencli_browser"]["status"] == "configured"
    assert "dokobot_mcp" not in raw
    assert "liepin_pi" not in raw
    assert str(tmp_path) not in raw


def test_dev_launcher_uses_liepin_opencli_helper_without_legacy_mcp_adapter() -> None:
    script = Path("scripts/start-dev-workbench.sh").read_text(encoding="utf-8")

    assert "seektalent.providers.liepin.opencli_browser_cli cleanup_orphaned_tabs" in script
    assert "seektalent.providers.pi_agent.opencli_browser_cli" not in script
    assert "node_modules/pi-mcp-adapter/index.ts" not in script
    assert "SEEKTALENT_LIEPIN_DOKOBOT_MCP_COMMAND" not in script
    assert "DOKOBOT_MCP_COMMAND" not in script
```

Keep the existing OpenCLI raw-env diagnostics in `tests/test_dev_mode_readiness.py`; do not remove those checks when deleting the legacy setup test file.

- [x] **Step 8: Run focused OpenCLI and boundary tests**

Run:

```bash
uv run pytest -q tests/test_liepin_opencli_browser.py tests/test_liepin_opencli_retriever.py \
  tests/test_liepin_opencli_worker_client.py tests/test_liepin_opencli_workflow.py \
  tests/test_liepin_browser_boundaries.py tests/test_liepin_boundaries.py \
  tests/test_liepin_opencli_local_setup.py tests/test_dev_mode_readiness.py
```

Expected:

```text
passes
```

If monkeypatch paths fail, update them from `seektalent.providers.pi_agent.opencli_browser...` to `seektalent.providers.liepin.opencli_browser...`.

- [x] **Step 9: Commit OpenCLI namespace migration**

```bash
git add src/seektalent/providers/liepin/opencli_browser.py \
  src/seektalent/providers/liepin/opencli_browser_cli.py \
  src/seektalent/providers/liepin/opencli_extensions/seektalent_opencli_browser.ts \
  src/seektalent/providers/liepin/browser_boundary_registry.json \
  src/seektalent/providers/liepin/browser_boundary_patterns.py \
  tools/check_liepin_browser_boundaries.py \
  tests/test_liepin_opencli_browser.py \
  tests/test_liepin_browser_boundaries.py \
  src/seektalent/providers/liepin/client.py \
  src/seektalent/providers/liepin/opencli_retriever.py \
  src/seektalent/providers/liepin/opencli_worker_client.py \
  scripts/start-dev-workbench.sh \
  apps/liepin-worker/scripts/checkBoundaries.ts \
  apps/liepin-worker/tests/boundaries.test.ts \
  tests/test_liepin_boundaries.py \
  tests/test_liepin_opencli_retriever.py \
  tests/test_liepin_opencli_workflow.py \
  tests/test_liepin_opencli_local_setup.py \
  tests/test_dev_mode_readiness.py
git add -u src/seektalent/providers/pi_agent/opencli_browser.py \
  src/seektalent/providers/pi_agent/opencli_browser_cli.py \
  src/seektalent/providers/pi_agent/pi_extensions/seektalent_opencli_browser.ts \
  src/seektalent/providers/pi_agent/boundary_registry.json \
  src/seektalent/providers/pi_agent/boundary_patterns.py \
  tools/check_pi_agent_boundaries.py \
  tests/test_pi_opencli_browser.py \
  tests/test_pi_agent_boundaries.py
git commit -m "refactor: move liepin opencli browser code out of pi namespace"
```

---

### Task 5: Delete Obsolete Pi/DokoBot Modules And Tests

**Files:**
- Delete: `src/seektalent/providers/pi_agent/artifacts.py`
- Delete: `src/seektalent/providers/pi_agent/capabilities.py`
- Delete: `src/seektalent/providers/pi_agent/connection_safety.py`
- Delete: `src/seektalent/providers/pi_agent/contracts.py`
- Delete: `src/seektalent/providers/pi_agent/dokobot_client.py`
- Delete: `src/seektalent/providers/pi_agent/local_setup.py`
- Delete: `src/seektalent/providers/pi_agent/locks.py`
- Delete: `src/seektalent/providers/pi_agent/payload_firewall.py`
- Delete: `src/seektalent/providers/pi_agent/pi_external.py`
- Delete: `src/seektalent/providers/pi_agent/validation_errors.py`
- Delete: `src/seektalent/providers/pi_agent/pi_extensions/bailian_deepseek.ts`
- Delete: `src/seektalent/providers/pi_agent/pi_extensions/tsconfig.json`
- Delete: `src/seektalent/providers/pi_agent/__init__.py`
- Delete: `tests/test_dokobot_capabilities.py`
- Delete: `tests/test_pi_agent_artifacts.py`
- Delete: `tests/test_pi_agent_contracts.py`
- Delete: `tests/test_pi_dokobot_local_setup.py`
- Delete: `tests/test_pi_external_agent.py`
- Delete: `tests/test_pi_payload_firewall.py`
- Modify: `scripts/verify-dev-workbench.sh`
- Modify: `tests/test_liepin_config.py`
- Modify: `tests/test_liepin_boundaries.py`
- Modify: `tests/test_workbench_note_writer.py`

- [x] **Step 1: Migrate active stale test strings before deletion**

Before running the broad stale-reference scan, update active tests that use old provider names as ordinary fixture copy rather than historical documentation.

In `tests/test_workbench_note_writer.py`, replace user-visible fixture strings such as:

```python
"DokoBot provider 已经返回结果。"
"pi_agent source_lane_run_id 已更新。"
```

with neutral Liepin/browser wording, for example:

```python
"猎聘浏览器通道已经返回结果。"
"liepin source_lane_run_id 已更新。"
```

Confirm that `tests/test_liepin_opencli_local_setup.py` and `tests/test_dev_mode_readiness.py` now preserve the OpenCLI setup/launcher coverage that used to be mixed into `tests/test_pi_dokobot_local_setup.py`.

- [x] **Step 2: Verify no active imports remain**

Run:

```bash
rg -n "seektalent\\.providers\\.pi_agent|providers/pi_agent|Liepin Pi Agent|DokoBot|dokobot_action|live-pi-agent|SEEKTALENT_LIEPIN_PI|SEEKTALENT_LIEPIN_DOKOBOT" \
  src tests tools scripts apps README.md docs/configuration.md docs/development.md .env.example src/seektalent/default.env
```

Expected: only files scheduled for deletion or historical docs outside the active docs set appear.

- [x] **Step 3: Delete obsolete files**

Run:

```bash
git rm src/seektalent/providers/pi_agent/artifacts.py \
  src/seektalent/providers/pi_agent/capabilities.py \
  src/seektalent/providers/pi_agent/connection_safety.py \
  src/seektalent/providers/pi_agent/contracts.py \
  src/seektalent/providers/pi_agent/dokobot_client.py \
  src/seektalent/providers/pi_agent/local_setup.py \
  src/seektalent/providers/pi_agent/locks.py \
  src/seektalent/providers/pi_agent/payload_firewall.py \
  src/seektalent/providers/pi_agent/pi_external.py \
  src/seektalent/providers/pi_agent/validation_errors.py \
  src/seektalent/providers/pi_agent/pi_extensions/bailian_deepseek.ts \
  src/seektalent/providers/pi_agent/pi_extensions/tsconfig.json \
  src/seektalent/providers/pi_agent/__init__.py \
  tests/test_dokobot_capabilities.py \
  tests/test_pi_agent_artifacts.py \
  tests/test_pi_agent_contracts.py \
  tests/test_pi_dokobot_local_setup.py \
  tests/test_pi_external_agent.py \
  tests/test_pi_payload_firewall.py
```

If the now-empty `src/seektalent/providers/pi_agent/pi_extensions` directory remains, leave it untracked and empty.

- [x] **Step 4: Update verification script test list**

In `scripts/verify-dev-workbench.sh`, remove:

```bash
  tests/test_pi_external_agent.py \
  tests/test_pi_payload_firewall.py \
```

Keep the Liepin runtime/config/workbench tests in the script.

- [x] **Step 5: Strengthen deleted-namespace boundary test**

In `tests/test_liepin_boundaries.py`, add:

```python
def test_removed_pi_agent_package_is_not_active_source() -> None:
    assert not Path("src/seektalent/providers/pi_agent").exists()
```

If an empty compatibility package is intentionally kept, use this stricter content check instead:

```python
def test_removed_pi_agent_package_contains_no_active_modules() -> None:
    package = Path("src/seektalent/providers/pi_agent")
    if not package.exists():
        return
    assert sorted(path.name for path in package.rglob("*") if path.is_file()) == ["__init__.py"]
    assert package.joinpath("__init__.py").read_text(encoding="utf-8").strip() == ""
```

Use one of the two tests, not both.

- [x] **Step 6: Run deletion-focused tests**

Run:

```bash
uv run pytest -q tests/test_liepin_config.py tests/test_liepin_boundaries.py \
  tests/test_liepin_detail_grants.py tests/test_liepin_detail_policy.py tests/test_liepin_connection_safety.py \
  tests/test_liepin_opencli_local_setup.py tests/test_dev_mode_readiness.py tests/test_workbench_note_writer.py
rg -n "seektalent\\.providers\\.pi_agent|providers/pi_agent|Liepin Pi Agent|DokoBot|dokobot_action|live-pi-agent|SEEKTALENT_LIEPIN_PI|SEEKTALENT_LIEPIN_DOKOBOT" \
  src tests tools scripts apps README.md docs/configuration.md docs/development.md .env.example src/seektalent/default.env
```

Expected:

```text
pytest passes
rg exits 1 with no matches
```

- [x] **Step 7: Commit legacy deletion**

```bash
git add scripts/verify-dev-workbench.sh tests/test_liepin_config.py tests/test_liepin_boundaries.py \
  tests/test_workbench_note_writer.py tests/test_liepin_opencli_local_setup.py tests/test_dev_mode_readiness.py
git add -u src/seektalent/providers/pi_agent tests
git commit -m "refactor: delete obsolete pi dokobot liepin path"
```

---

### Task 6: Preserve Worker Compatibility As Explicit Compatibility

**Files:**
- Modify: `src/seektalent/providers/liepin/client.py`
- Modify: `src/seektalent/providers/liepin/runtime_lane.py`
- Modify: `src/seektalent/runtime/source_lanes.py`
- Modify: `tests/test_liepin_runtime_source_lane.py`
- Modify: `tests/test_runtime_source_lanes.py`
- Modify: `tests/test_liepin_cli.py`
- Modify: `docs/configuration.md`

- [x] **Step 1: Add worker compatibility posture tests**

In `tests/test_liepin_runtime_source_lane.py`, extend the backend posture test with:

```python
    assert liepin_backend_posture(make_settings(liepin_worker_mode="managed_local")) == {
        "backend_mode": "worker_compat",
        "reason": "managed_local",
    }
    assert liepin_backend_posture(make_settings(liepin_worker_mode="external_http")) == {
        "backend_mode": "worker_compat",
        "reason": "external_http",
    }
```

In `tests/test_liepin_cli.py`, keep tests proving `managed_local` and `external_http` still work. Add:

```python
def test_liepin_smoke_worker_mode_choices_document_worker_compatibility(capsys) -> None:
    status = main(["liepin-smoke", "--worker-mode", "managed_local"])

    captured = capsys.readouterr()
    assert status != 2
    assert "invalid choice" not in captured.err
```

- [x] **Step 2: Run compatibility tests**

Run:

```bash
uv run pytest -q tests/test_liepin_runtime_source_lane.py::test_liepin_backend_posture \
  tests/test_runtime_source_lanes.py tests/test_liepin_cli.py
```

Expected: passes after previous tasks preserve compatibility mode behavior.

- [x] **Step 3: Verify docs do not call compatibility a failed fallback**

Run:

```bash
rg -n "DokoBot|Pi|pi_agent|dokobot_action|fallback" docs/configuration.md docs/development.md README.md
```

Expected: no references to Pi/DokoBot, and any `fallback` match describes user-facing degraded behavior rather than the old failed browser-agent path.

- [x] **Step 4: Commit compatibility preservation**

```bash
git add src/seektalent/providers/liepin/client.py \
  src/seektalent/providers/liepin/runtime_lane.py \
  src/seektalent/runtime/source_lanes.py \
  tests/test_liepin_runtime_source_lane.py \
  tests/test_runtime_source_lanes.py \
  tests/test_liepin_cli.py \
  docs/configuration.md
git commit -m "test: preserve liepin worker compatibility posture"
```

---

### Task 7: Run Final Verification Gates

**Files:**
- Modify only if verification exposes a defect in files changed by this plan.

- [x] **Step 1: Run focused backend verification**

Run:

```bash
uv run pytest -q \
  tests/test_liepin_config.py \
  tests/test_liepin_worker_client.py \
  tests/test_liepin_opencli_worker_client.py \
  tests/test_liepin_opencli_retriever.py \
  tests/test_liepin_opencli_browser.py \
  tests/test_liepin_opencli_workflow.py \
  tests/test_liepin_opencli_local_setup.py \
  tests/test_liepin_detail_grants.py \
  tests/test_liepin_runtime_source_lane.py \
  tests/test_runtime_source_lanes.py \
  tests/test_runtime_source_adapter_boundary.py \
  tests/test_liepin_boundaries.py \
  tests/test_liepin_browser_boundaries.py \
  tests/test_liepin_cli.py \
  tests/test_cli.py \
  tests/test_dev_mode_readiness.py \
  tests/test_workbench_note_writer.py \
  tests/test_workbench_liepin_browser_session_probe.py \
  tests/test_workbench_api.py
```

Expected:

```text
passes
```

- [x] **Step 2: Run frontend verification**

Run:

```bash
cd apps/web-svelte
bun run check
bun run lint
bun run test
bun run build
bun run test:e2e
```

Expected:

```text
all commands pass
```

- [x] **Step 3: Run worker boundary checks**

Run:

```bash
cd apps/liepin-worker
bun test tests/boundaries.test.ts
bun run boundary-check
```

Expected:

```text
passes
```

- [x] **Step 4: Run package-level workbench verification**

Run:

```bash
./scripts/verify-dev-workbench.sh
```

Expected:

```text
passes
```

- [x] **Step 5: Run final stale reference scan**

Run:

```bash
rg -n "seektalent\\.providers\\.pi_agent|providers/pi_agent|Liepin Pi Agent|DokoBot|dokobot_action|live-pi-agent|SEEKTALENT_LIEPIN_PI|SEEKTALENT_LIEPIN_DOKOBOT" \
  src tests tools scripts apps README.md docs/configuration.md docs/development.md .env.example src/seektalent/default.env
```

Expected:

```text
no matches
```

- [x] **Step 6: Run git hygiene checks**

Run:

```bash
git diff --check
git status --short
```

Expected:

```text
git diff --check exits 0
git status --short shows only intentional changes or is clean after commits
```

- [x] **Step 7: Commit final verification fixes if any were needed**

If Step 1 through Step 6 required small fixes to already-tracked files, commit them:

```bash
git add -u src tests tools scripts apps README.md docs/configuration.md docs/development.md .env.example src/seektalent/default.env
git diff --cached --quiet || git commit -m "test: verify liepin legacy cleanup"
```

If no fixes were needed, do not create an empty commit.

---

## Self-Review

Spec coverage:

- E2E baseline repair and current degraded Liepin UI behavior are covered by Task 1.
- CLI/docs/env public contract cleanup is covered by Task 2.
- Liepin-owned policy, detail-grant model validation, and connection-safety extraction are covered by Task 3.
- Active OpenCLI namespace migration and launcher/setup regression coverage are covered by Task 4.
- Pi/DokoBot deletion, including migration of active test strings before stale scans, is covered by Task 5.
- Worker compatibility preservation is covered by Task 6.
- Verification gates are covered by Task 7.

Placeholder scan:

- The plan avoids placeholder markers and intentionally vague test-writing steps.
- Every code-changing task includes concrete files, commands, and expected outcomes.

Type consistency:

- New policy enum is named `LiepinDetailFailureCode`.
- New grant model remains `DetailOpenGrant`.
- New connection-safety module exports the same public names as the old module so adapter changes stay import-only.
- OpenCLI browser imports move from `seektalent.providers.pi_agent.opencli_browser` to `seektalent.providers.liepin.opencli_browser`.
