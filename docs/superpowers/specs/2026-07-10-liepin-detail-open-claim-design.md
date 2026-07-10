# Liepin Detail Open Claim Design

> **Date:** 2026-07-10
> **Status:** Approved design
> **Owner:** SeekTalent Liepin/OpenCLI provider

## Summary

The default Liepin/OpenCLI retrieval path is detail-backed: it extracts a visible card, opens the browser detail page, captures structured detail, and only then returns a `ResumeCandidate` to runtime identity merge. Current duplicate prevention is local to one workflow invocation and keyed by visible rank. It cannot prevent the same person from being opened by two logical queries or in later rounds.

This design adds a run-owned, click-before-open claim ledger. A stable opaque candidate key is derived from the provider card's canonical detail identity before any browser detail side effect. The same key is then used for post-capture provider identity, so the browser-side and runtime-side invariants agree.

## Confirmed Decisions

- The default OpenCLI detail-backed search path is the target. The existing approved-detail `LiepinStore` ledger is a different workflow and is not reused through an unsupported `open_details` call.
- A candidate must be claimed before `open_detail` is called.
- The claim key is an opaque hash derived from the canonical Liepin detail identity, not card rank, workflow run ID, artifact path, display name, or raw full text.
- The raw `res_id_encode`, detail URL, and source ref are private provider inputs. The new claim path must not add them, or the opaque claim hash, to public events, BFF DTOs, React state, or user-facing diagnostics.
- The ledger is run-owned and checkpointable across lanes and rounds. It is not a global daily provider-budget database.
- The existing `_DETAIL_OPEN_MAX_ATTEMPTS == 2` retry remains inside one granted claim. Once a browser-open action has occurred without successful capture, the claim is terminal for every later lane and round.
- If a card lacks a safe stable identity, it is not opened. Runtime records a safe reason and evaluates another card.
- The same opaque key becomes the OpenCLI detail candidate's provider subject/dedup identity input, replacing artifact-run/rank-derived identity.

## Goals

1. Open the same Liepin person at most once per run, across logical queries, lanes, rounds, and rank changes.
2. Make claim state atomic even when source dispatch is asynchronous.
3. Keep browser-open behavior and runtime candidate identity aligned on one provider key.
4. Preserve the existing bounded OpenCLI navigation retry behavior without allowing unlimited detail retries.
5. Surface safe counters for opened, skipped-as-seen, and terminal failures without leaking provider identifiers.

## Non-Goals

- Do not turn the default OpenCLI path into the approved-detail workflow.
- Do not make CTS or another provider implement the Liepin claim ledger.
- Do not introduce a global provider identity database.
- Do not retain artifact-reference or rank identity as a fallback for detail opening.
- Do not collect or restore `fullText`, `rawText`, or any raw resume page dump.
- Do not redesign the external OpenCLI browser daemon or add a second browser backend.
- Do not change daily detail budget policy semantics in `LiepinStore`.

## Current Failure Flow

```text
logical query
  -> detail-backed Liepin workflow
  -> extract card(ref, rank)
  -> open detail browser tab
  -> capture structured detail
  -> map ResumeCandidate
  -> runtime candidate/identity merge
```

`attempted_ranks` protects only the current card list. It resets for the next logical query and has no relation to candidate identity. Existing detail identity can be derived from a safe artifact reference that includes the source run and rank, so post-open cross-lane deduplication is not reliable either.

## Design

### 1. Stable Pre-Click Candidate Key

Add a Liepin-private helper that derives an opaque key from the card's canonical detail identity:

```python
def stable_liepin_detail_candidate_key_hash(detail_url: str) -> str | None: ...
```

The helper must:

1. let the workflow use the existing safe detail-URL parser for the card ref;
2. extract exactly one stable provider-side detail subject token from that canonical URL;
3. normalize the provider identity deterministically;
4. hash `{provider: "liepin", canonical_subject: ...}` with SHA-256;
5. return only the opaque hash to the workflow.

It must return `None` for malformed, non-Liepin, non-canonical, or ambiguous detail URLs. The caller records `liepin_opencli_candidate_identity_missing`, skips that card, and never falls back to rank, display text, artifact path, or a weak text fingerprint.

`LiepinCardItem` carries the opaque key internally alongside its existing reference and rank. The public structured card contract remains free of the raw token and detail URL.

### 2. Run-Owned Detail Claim Ledger

Add persisted claim records to `RunState` and expose mutations through a small runtime-owned synchronized facade. The provider receives the facade through its runtime context/request; it does not access `RunState` or checkpoints directly.

```python
class DetailOpenClaimState(StrEnum):
    CLAIMED = "claimed"
    OPENED = "opened"
    TERMINAL_FAILED = "terminal_failed"


class DetailOpenClaim(BaseModel):
    state: DetailOpenClaimState
    browser_open_attempt_count: int = 0
    last_safe_reason_code: str | None = None


class DetailOpenClaimLedger(Protocol):
    def try_claim(self, provider_candidate_key_hash: str) -> bool: ...

    def mark_opened(self, *, provider_candidate_key_hash: str) -> None: ...

    def record_browser_open_attempt(self, *, provider_candidate_key_hash: str) -> None: ...

    def has_browser_open_attempt(self, *, provider_candidate_key_hash: str) -> bool: ...

    def mark_terminal_failed(
        self,
        *,
        provider_candidate_key_hash: str,
        safe_reason_code: str,
    ) -> None: ...

    def release_unattempted(self, *, provider_candidate_key_hash: str) -> None: ...
```

The facade uses a lock around the entire read-check-transition-write operation. Runtime checkpoints serialize the claim records, so resume/recovery does not forget a successful open.

### 3. Claim Transition Rules

| Existing state | Claim result | Next state | Browser open allowed? |
| --- | --- | --- | --- |
| none | granted | `claimed`, attempt count 0 | yes |
| `claimed` | denied | unchanged | no |
| `opened` | denied | unchanged | no |
| `claimed`, no browser action attempted | release | no record | future valid attempt may claim |
| `claimed`, browser action attempted without capture | terminal failure | `terminal_failed` | no |
| `terminal_failed` | `terminal_failed` | unchanged | no |

`mark_opened` occurs only after structured detail capture succeeds. `record_browser_open_attempt` occurs before each actual browser open. The existing workflow may make its bounded two browser-open attempts while it owns one granted claim. If capture still does not succeed after an action, `mark_terminal_failed` prevents any later lane or round from reopening that candidate. `release_unattempted` is permitted only when no browser-open action was sent.

### 4. Workflow Integration

The OpenCLI workflow changes from:

```text
select card -> open detail -> wait -> capture
```

to:

```text
select card
-> derive opaque candidate key
-> claim key
-> skip if claim denied
-> open detail
-> wait detail ready
-> capture structured detail
-> mark opened or terminal failed
-> restore/continue
```

The workflow receives current `round_no` and `query_instance_id` from the existing logical query request. It increments safe counters:

```text
detail_claim_granted_count
detail_opened_count
detail_open_skipped_seen_count
detail_open_terminal_failure_count
```

Only counts and safe reason codes may be added to source-lane events or Workbench-facing diagnostics by this path. The claim key itself remains provider-private.

### 5. Identity Alignment After Capture

Change the OpenCLI retriever/mapper path so the pre-click `provider_candidate_key_hash` is carried through capture and becomes the source of:

```text
provider_subject_id
provider_candidate_key_hash
stable dedup input
```

The artifact reference remains an artifact locator, not identity material. Candidate mapping must reject a detail payload whose carried opaque key does not match the selected card's claim key.

This makes candidate identity resilient to:

- query lane changes;
- source run ID changes;
- visible card ordering changes;
- artifact path changes;
- retry attempts.

### 6. Boundaries and Privacy

The following values are provider-private and must be sanitized before any public output:

```text
res_id_encode
raw card ref
canonical detail URL
canonical provider subject
provider_candidate_key_hash
```

The public system may expose aggregate counts and safe reason codes only. Existing structured evidence (`safeCardSummary`, `safeDetail`, `wtsDetail`) remains the only candidate evidence path for downstream normalization and scoring.

### 7. Existing Approved-Detail Ledger

`LiepinStore.reserve_detail_attempt` stays in the approved-detail workflow, where it enforces daily budget/idempotency for a separately authorized detail operation. This design shares only conceptual state names and test discipline; it does not call that store from OpenCLI detail-backed search and does not alter daily budget counting.

## Failure Semantics

| Situation | Action | Safe result |
| --- | --- | --- |
| No stable card identity | do not click; try next card | `liepin_opencli_candidate_identity_missing` |
| Key claimed by another lane | skip card | no browser click; increment skipped count |
| Key already opened | skip card | no browser click; increment skipped count |
| Browser action attempted but capture does not complete | allow the current workflow's bounded retry, then mark terminal failed | existing safe reason retained |
| No browser action was sent | release claim and try another candidate | existing safe reason retained |
| Capture key mismatch | fail closed; do not map candidate | `liepin_opencli_candidate_identity_mismatch` |

## Migration

- New runs initialize an empty detail-claim ledger.
- Existing historic runs receive no key reconstruction and no automatic migration.
- Existing approved-detail `LiepinStore` records are untouched.
- The public source-lane schema gains only safe aggregate count fields; raw identity values are never added.

## Acceptance Criteria

1. The same `res_id_encode` reached through two logical queries yields one browser detail open.
2. The same candidate reached in a later round yields no additional browser detail open.
3. A change in visible rank does not change the opaque provider key.
4. A malformed/unknown card identity causes zero browser opens for that card.
5. The current workflow can make at most two browser-open attempts under one granted claim.
6. A browser-open failure that cannot capture detail cannot be reopened by another lane or round.
7. Candidate identity after capture is stable across lane/run/rank changes and matches the original claim key.
8. Public source events and Workbench payloads contain neither raw card refs nor detail URLs nor opaque key hashes.
9. Existing approved-detail ledger tests retain their current daily budget/idempotency behavior.

## Test Strategy

Add focused tests before implementation for:

- provider key derivation from a canonical detail URL and rejection of malformed or ambiguous URLs;
- same candidate in two logical query bundles;
- same candidate in two rounds using the restored checkpoint ledger;
- same candidate at different visible ranks;
- concurrent claim attempts and one granted opener;
- two bounded browser-open attempts under one granted claim;
- an attempted capture failure becoming terminal across a second lane;
- key mismatch between selection and capture failing closed;
- mapper identity stability without artifact-path dependence;
- source event/public payload sanitization;
- regression coverage for current OpenCLI navigation/recovery tests and approved-detail `LiepinStore` tests.

Run the focused provider, source-lane, runtime state, and Workbench privacy suites before merge.
