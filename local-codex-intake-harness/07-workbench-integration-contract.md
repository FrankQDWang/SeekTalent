# Workbench Integration Contract

## Existing Workbench Flow

Current Workbench already supports the downstream path:

```text
POST /api/workbench/sessions
POST /api/workbench/sessions/{session_id}/requirements/prepare
PUT  /api/workbench/sessions/{session_id}/requirements
POST /api/workbench/sessions/{session_id}/requirements/approve
POST /api/workbench/sessions/{session_id}/start
```

The intake harness must reuse this flow.

## Handoff Rule

After intake confirmation:

```text
Confirmed draft
  -> create Workbench session
  -> start requirement preparation
  -> navigate user to the Workbench session
```

The intake harness must not:

- call `WorkflowRuntime.run(...)`;
- call `WorkflowRuntime.extract_requirements(...)`;
- import runtime modules;
- approve requirement review automatically;
- start sourcing before requirement approval.

## Workflow Visibility

After a Workbench session exists, the intake conversation may answer progress and result questions by reading existing Workbench state.

Allowed read surfaces:

- Workbench session response;
- requirement review response;
- Workbench event stream or event list;
- runtime graph response;
- candidate/final-top result responses.

Forbidden initial-version operations:

- pausing a run;
- resuming a run;
- cancelling a run;
- changing source kinds after start;
- editing approved requirement sheets during a run;
- injecting candidates or evidence;
- calling runtime internals directly.

The user experience can still feel agentic because the user asks the transcript about progress. Under the hood, the first version is a read-only supervisor after workflow start.

## Session Creation Payload

The confirmed draft maps directly to:

```json
{
  "jobTitle": "Senior Backend Engineer",
  "jdText": "Build real-time data platform services...",
  "notes": "Prefer Shanghai or Hangzhou.",
  "sourceIds": ["cts", "liepin"]
}
```

The intake draft uses `sourceIds` from the current Workbench source catalog. The Workbench bridge maps those source ids to the current Workbench session creation payload. If the Workbench API still accepts `sourceKinds`, the bridge performs the compatibility mapping at the boundary. The intake contract must not hard-code CTS/Liepin as the complete source universe.

## Requirement Preparation

Once the Workbench session exists, intake calls the existing requirement preparation path through the Workbench bridge. The expected visible state is a Workbench session with requirement review in draft/preparing state.

The user still reviews and approves the requirement sheet in the existing UI.

## Idempotency

Confirming the same intake conversation twice must not create duplicate Workbench sessions.

Required behavior:

- if a conversation already has `workbench_session_id`, return that session id;
- do not create a second session;
- do not enqueue duplicate requirement preparation work;
- return reason code `intake_already_confirmed` only if the API needs to tell the frontend that the action was already completed.

## Source Catalog

The intake UI and service must obtain selectable sources from the current Workbench source catalog or registry-facing API.

Initial examples may include `cts` and `liepin` if those ids are registered locally. Those ids are current source ids, not architecture constants.

Default selection:

- use the catalog's default selected sources when available;
- otherwise use all enabled local sources returned by the catalog;
- never invent a source id that is not in the catalog.

If the user explicitly asks for only one source, use that source only if it is present in the catalog. If the source is not registered or unavailable, ask a clarification question or return a named validation error.

Invalid or empty source ids must be rejected before Workbench handoff.

## Auth And CSRF

All intake mutation endpoints must use the same local Workbench auth and CSRF posture as Workbench session creation.

Expected:

- read endpoints require current user;
- create/message/edit/confirm/reset endpoints require CSRF user;
- conversations are scoped to the current Workbench user and workspace.

## Audit

The bridge should record enough safe state to debug handoff:

- intake conversation id;
- draft revision id;
- Workbench session id;
- handoff timestamp;
- public reason code on failure.

Do not log raw Codex provider payloads, API keys, cookies, local absolute paths, or raw browser/provider state.
