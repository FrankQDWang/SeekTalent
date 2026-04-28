# PRF Model Sidecar Deployment v0.1 Design

Date: 2026-04-28

## Context

`PRF v1.5` now has the right application-side boundary:

- typed proposal artifacts
- exact-offset extractive enforcement
- replayable proposal metadata and version vectors
- shadow vs mainline rollout
- deterministic PRF gate
- legacy fallback

What is still missing is real model serving.

Today, the runtime does not actually load `GLiNER2` or a multilingual embedding model inside the request path. The current orchestrator calls [`build_prf_span_extractor(..., backend=None)`](/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/runtime/orchestrator.py:2136), which intentionally falls back to [`LegacyRegexSpanExtractor`](/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/candidate_feedback/proposal_runtime.py:137). Familying also still defaults to exact-surface similarity unless an embedding similarity backend is explicitly supplied.

This is deliberate. The repository first established replayability, artifact identity, shadow/mainline boundaries, and deterministic gate inputs before connecting real local models.

The next step is to deploy real local model inference without breaking those boundaries.

## Goals

- Deploy a real local span-proposal model for `PRF v1.5`.
- Deploy a real local multilingual embedding model for familying similarity.
- Keep all request sandboxes free of direct model imports and direct Hugging Face loading.
- Reuse one local model service across many request sandboxes on the same host.
- Keep model downloads off the request path.
- Pin models by revision and make runtime behavior reproducible.
- Preserve the current `shadow -> mainline` rollout contract.
- Preserve legacy fallback when the sidecar is unavailable or disallowed.

## Non-Goals

- No GPU requirement in Phase 1.
- No per-sandbox model loading.
- No model downloads during an active request.
- No free-form query generation.
- No movement of PRF gate logic into the model service.
- No embedding-driven query generation.
- No knowledge base, alias dictionary, or maintained term lexicon.
- No cross-host distributed inference system in Phase 1.

## Decision Summary

This design makes eight decisions:

1. Run one shared local `PRF model sidecar` per host.
2. Deploy it as a Docker container in CPU-only mode.
3. Let sandboxes access it only through localhost HTTP.
4. Let the sidecar serve both span proposal and embedding inference.
5. Cache model weights in a host-mounted volume keyed by `model + revision`.
6. Keep deterministic alignment, familying guardrails, gating, artifacts, and replay assembly in the main app.
7. Keep runtime fallback to the legacy extractor when dependency gates or sidecar availability fail.
8. Allow `mainline` use only when pinned revisions, dependency gate, and bakeoff promotion criteria are satisfied.

## Why One Shared Sidecar

Three deployment shapes were considered.

### Option A: Per-Sandbox Model Loading

Each request sandbox loads `GLiNER2` and the embedding model locally.

Pros:

- strong process isolation
- no local service dependency

Cons:

- repeated model initialization on every request
- poor CPU-only latency
- repeated memory cost
- repeated model-cache coordination
- mixes model-serving concerns into the sandbox runtime

### Option B: One Shared Local Sidecar

One host-level service loads the models once and serves all request sandboxes on that machine.

Pros:

- best fit for CPU-only Phase 1
- avoids repeated startup cost
- keeps model dependencies out of sandboxes
- clearer replay and dependency boundaries
- naturally compatible with future cloud deployment

Cons:

- shared hotspot under load
- requires local service lifecycle management

### Option C: Separate Sidecars For Span And Embedding

Pros:

- cleaner service boundaries
- easier independent scaling later

Cons:

- more moving parts now
- two service lifecycles
- two health checks
- two failure surfaces

## Recommendation

Choose **Option B** for Phase 1.

This is the smallest deployment step that gives real model inference without collapsing the current productization boundary. It keeps sandboxes small, avoids model downloads during requests, and leaves the deterministic PRF decision logic in the main application where it already belongs.

## Architecture

Phase 1 introduces four runtime roles.

### 1. Request Sandbox

Each workflow request still runs in its own sandbox.

The sandbox:

- may construct PRF proposal inputs
- may call the local sidecar over HTTP
- may perform exact-offset alignment and extractive validation
- may build phrase families
- may run the deterministic PRF gate
- may persist artifacts and replay metadata

The sandbox must not:

- import or initialize `GLiNER2`
- import or initialize the embedding model
- download model weights
- decide its own model revisions

### 2. PRF Model Sidecar

One host-local sidecar serves both:

- span proposal inference
- embedding inference

The sidecar is responsible for:

- loading pinned model revisions
- owning the local model cache
- exposing health and inference endpoints
- enforcing that only configured model/revision pairs are active

The sidecar is not responsible for:

- exact-offset enforcement
- candidate span validation
- family merge policy
- PRF acceptance policy
- provider query construction
- artifact persistence

### 3. Host Model Cache Volume

All model weights are stored in a host-mounted cache volume.

Cache keys must include at least:

- model name
- model revision
- tokenizer revision when applicable

Expected behavior:

- first startup for a new revision downloads once
- subsequent sidecar restarts reuse cached artifacts
- request sandboxes never trigger downloads

### 4. Main Application Adapters

The main application must introduce HTTP-backed implementations of the existing seams instead of inventing a second runtime path.

At minimum:

- `HttpSpanModelBackend`
- `HttpEmbeddingBackend`

These backends plug into the existing proposal runtime boundary rather than bypassing it.

## Service Boundary

The sandbox and sidecar communicate only over localhost HTTP.

Examples:

- `http://127.0.0.1:8741/healthz`
- `http://127.0.0.1:8741/v1/span-extract`
- `http://127.0.0.1:8741/v1/embed`

The sandbox must not mount the model cache volume directly.

The sandbox must not call external Hugging Face endpoints.

The sidecar may access the model cache volume, but only during startup or explicit warmup.

## Model Choices

Phase 1 deployment candidate defaults remain:

- span proposal candidate: `fastino/gliner2-multi-v1`
- embedding candidate: `Alibaba-NLP/gte-multilingual-base`

These remain candidates, not already-proven winners. Bakeoff and shadow evaluation still decide whether the model-backed path is promoted.

The deployment system must support revision pinning for both models and must not assume "latest".

## HTTP API

The sidecar exposes three endpoints.

### `GET /healthz`

Purpose:

- liveness and readiness
- model/revision visibility

Response fields must include:

- `status`
- `span_model_loaded`
- `embedding_model_loaded`
- `span_model_name`
- `span_model_revision`
- `span_tokenizer_revision`
- `embedding_model_name`
- `embedding_model_revision`

### `POST /v1/span-extract`

Purpose:

- return raw model span proposals for one or more text slices

Request must include:

- `texts`
- `labels`
- `schema_version`
- `model_name`
- `model_revision`

Response rows must include:

- `surface`
- `label`
- `score`

Important rule:

The sidecar is not trusted to define final offsets. The sidecar may return raw surfaces only. The sandbox remains responsible for deterministic source alignment and exact extractive validation.

### `POST /v1/embed`

Purpose:

- return embeddings for phrase surfaces used in familying

Request must include:

- `phrases`
- `model_name`
- `model_revision`

Response returns embedding vectors.

Phase 1 keeps similarity calculation in the main app so that familying rules, thresholds, and replay artifacts stay transparent and versioned inside the PRF proposal contract.

## Startup And Warmup

The sidecar startup sequence is:

1. read configured model names and pinned revisions
2. verify local cache presence
3. download missing revisions to the cache volume
4. load span model
5. load embedding model
6. expose `ready` health state

The request path must never perform:

- model download
- tokenizer download
- remote code retrieval

If startup cannot satisfy the pinned dependency contract, the sidecar must fail readiness rather than starting in a partially defined state.

## Revision And Dependency Gate

The deployment contract must align with the existing PRF dependency gate.

Required for `mainline`:

- non-empty span model revision
- non-empty tokenizer revision
- non-empty embedding model revision
- explicit schema version
- explicit remote-code policy

Phase 1 keeps these rules:

- `shadow` may fall back
- `mainline` must be pinned

No sidecar configuration may silently float to an unpinned model revision.

## Remote Code Policy

Remote code is treated as a deployment decision, not a request-time flag.

Rules:

- request sandboxes never enable remote code
- sidecar runtime config must not freely toggle remote code per request
- if a model requires custom code, that code path must be reviewed, pinned, and baked into the approved deployment setup

This matters in particular for embedding candidates that may rely on `trust_remote_code=True`.

## Integration With Current PRF v1.5 Runtime

Add one explicit backend selector:

- `prf_model_backend = "legacy" | "http_sidecar"`

Behavior:

- `legacy`: current regex extractor path only
- `http_sidecar`: use HTTP backends for span proposal and embedding similarity when dependency gate allows it

Rollout remains two-stage.

### Shadow Mode

- `prf_v1_5_mode = "shadow"`
- `prf_model_backend = "http_sidecar"`

In shadow mode:

- the sandbox calls the sidecar
- span proposals and familying artifacts are written
- replay snapshot carries sidecar-backed version info
- `SecondLaneDecision.selected_lane_type` must not change because of the new extractor

### Mainline Mode

- `prf_v1_5_mode = "mainline"`
- `prf_model_backend = "http_sidecar"`

Mainline is allowed only when:

- bakeoff promotion criteria passed
- model dependency gate passed
- sidecar is ready
- revisions are pinned

Then and only then may sidecar-backed proposal outputs drive `prf_probe`.

## Failure Behavior

Failure handling must stay simple and explicit.

If any of the following happen:

- sidecar unreachable
- sidecar timeout
- sidecar schema mismatch
- sidecar returns malformed response
- configured revision unavailable
- dependency gate fails
- embedding backend unavailable

then the runtime must:

1. record the failure reason
2. mark proposal metadata as legacy fallback
3. use the legacy extractor path
4. continue the retrieval workflow

This is a fallback to the current known behavior, not a retry storm or alternate model chain.

## Docker Deployment Shape

Phase 1 deployment uses one Docker container for the sidecar.

Recommended runtime characteristics:

- CPU-only
- one container per host
- host-mounted cache volume
- host-local port binding only

Example logical mounts:

- `/var/lib/seektalent-model-cache` -> sidecar model cache
- host-local loopback port for HTTP only

The sidecar container should be independently restartable from request sandboxes.

## Observability

The sidecar must emit enough metadata for operations, but the main app remains the source of PRF replay truth.

Sidecar observability:

- loaded model names
- loaded revisions
- startup duration
- request counts
- error counts
- request latency buckets

Main-app observability and replay:

- proposal artifact refs
- span model name and revision
- tokenizer revision
- embedding model name and revision
- familying version and thresholds
- runtime mode
- fallback reason when applicable

## Why Similarity Stays In The Main App

Even with a sidecar, familying logic should stay in the main app because it is not "just inference". It affects:

- support counting
- negative support
- tried-family rejection
- final accepted expression-family selection

That is PRF policy input, not just vector math. The sidecar should supply embeddings; the main app should continue to own family merge semantics.

## Testing Expectations

Phase 1 implementation must cover:

1. sidecar health and readiness behavior
2. host-cache reuse across restarts
3. no request-path model downloads
4. sandbox HTTP-only access pattern
5. shadow mode writes sidecar-backed artifacts but does not change lane routing
6. mainline mode requires pinned revisions and ready sidecar
7. unreachable sidecar triggers legacy fallback
8. replay snapshot includes sidecar model metadata

## Acceptance Criteria

The design is considered successfully implemented when all of the following are true:

1. A CPU-only Docker sidecar can load a pinned span model and a pinned embedding model from a host-mounted cache.
2. Request sandboxes can obtain span proposals and embeddings only through localhost HTTP.
3. No request path downloads model artifacts.
4. The current PRF v1.5 artifact and replay contract remains intact.
5. Shadow mode produces sidecar-backed proposal artifacts without changing second-lane behavior.
6. Mainline mode can use the sidecar-backed extractor only when dependency gates and promotion gates pass.
7. Any sidecar failure cleanly falls back to the legacy extractor.

## Future Follow-Up

This design intentionally stops at single-host local deployment.

Possible later work:

- split span and embedding into separate services if load demands it
- introduce host-level warmup orchestration
- move from localhost-only sidecar to a network service in multi-host cloud deployment
- add model-pool observability for production operations

Those are later optimizations, not Phase 1 requirements.
