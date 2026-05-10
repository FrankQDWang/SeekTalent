# TODOS

## Product

### Static TREC-Pooling Benchmark And First-Party Search Engine

**What:** Build a static TREC-pooling benchmark and first-party resume search engine from governed corpus exports.

**Why:** This enables repeatable evaluation of retrieval strategies, model changes, source adapters, and ranking quality without depending only on live provider searches.

**Context:** The current workbench plan preserves provenance such as `jd_doc_id`, query fingerprint, provider/page/rank, `resume_doc_id`, `observation_id`, detail ledger state, and human actions, but deliberately does not create workbench-owned qrels, pool versions, benchmark manifests, or search-engine tables. Start from the existing CorpusStore raw-payload boundary and benchmark CLI/evaluation code, then design immutable corpus exports, pool versions, qrels, redaction policy, and execution-result storage outside `seektalent_ui`.

**Effort:** XL
**Priority:** P2
**Depends on:** Multi-source workbench candidate evidence, human review actions, authorized raw artifact access, and corpus provenance from M2/M4/M5.

### Post-Run Learning Capsules

**What:** Add personalized post-run learning capsules that give recruiters short domain or search-strategy tips during idle moments or after a session.

**Why:** Recruiters often search across unfamiliar domains; lightweight learning can improve their judgment and keyword strategy over time.

**Context:** The current workbench plan defers this because memory, candidate feedback, and privacy boundaries must stabilize first. The feature should use redacted recruiter/workflow memory, not raw resumes, contact details, private candidate text, or sensitive evaluations. Useful starting points are session outcomes, user-approved notes/actions, repeated search failures, and high-level domain concepts.

**Effort:** L
**Priority:** P3
**Depends on:** Candidate actions/notes, memory firewall, enough real session history, and user-configurable tip frequency.

## Frontend

### Storybook Component Catalog

**What:** Introduce Storybook for stable workbench components such as source cards, candidate cards, detail approval queues, and session rail states.

**Why:** A component catalog will make complex UI states easier to review once the product shape stabilizes.

**Context:** M0-M6 should not start with Storybook. The current plan uses Playwright plus `odiff-bin` for page-level visual smoke tests because the immediate risk is structural layout drift from the reference HTML. Revisit Storybook after source card, candidate card, detail approval queue, and session rail components are stable enough to avoid story churn.

**Effort:** M
**Priority:** P3
**Depends on:** Stable M2/M3 component shapes and repeated UI states worth cataloging.

## Infrastructure

### Cloud Deployment Migration

**What:** Design the cloud deployment version with domain, HTTPS, Postgres, formal queue/worker, backups, monitoring, and stronger multi-user tenant isolation.

**Why:** The current V1 is an internal LAN experiment, but the product direction includes many users on a cloud server later.

**Context:** The workbench plan keeps V1 on SQLite plus a local SourceRun job runner with explicit LAN mode and public internet exposure out of scope. The state model, source-run job boundary, detail ledger, audit events, and tenant/workspace/user scoping should make a later Postgres/queue migration possible without changing the business contract.

**Effort:** XL
**Priority:** P2
**Depends on:** M0-M6 internal workbench validation, real usage feedback, stable source-run/job/ledger state machines, and a separate cloud security review.

## Completed
