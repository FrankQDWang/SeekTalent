# Generic Explicit Phrase Extraction For PRF v1.5 Design

## Goal

Replace the current regex-heavy PRF phrase extraction with a general explicit-phrase extraction layer that works across:

- English technical tokens
- Chinese technical and responsibility phrases
- mixed Chinese-English phrases

without introducing:

- maintained knowledge bases
- maintained domain lexicons
- maintained alias dictionaries
- free-form LLM query generation

The target is not a trained query rewriter. The target is a better `PRF v1.5` extractor that still fits inside the existing retrieval flywheel boundary.

## Decision Summary

This design makes six decisions:

1. Keep `typed second lane` and `PRF probe` as the runtime boundary.
2. Replace regex-first extraction with `model span proposal + deterministic gate`.
3. Do not introduce owned vocabulary assets such as term lists, company dictionaries, or domain ontologies.
4. Keep phrase extraction strictly extractive. The system may select and normalize explicit spans, but it must not freely generate new query phrases.
5. Use embeddings for familying and reranking, not for direct query generation.
6. Roll this out as an offline-evaluated extractor replacement before any online mainline switch.

## Why Change

The current extractor is useful for obvious technical tokens but weak for Chinese and mixed-language technical phrases.

The current implementation in [`src/seektalent/candidate_feedback/extraction.py`](/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/candidate_feedback/extraction.py) relies on:

- acronym, CamelCase, symbol-token, and short phrase regexes
- fixed generic-term filtering
- support counting over seed resumes
- a deterministic PRF gate in [`src/seektalent/candidate_feedback/policy.py`](/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/candidate_feedback/policy.py)

This works acceptably for phrases like:

- `Django`
- `FastAPI`
- `LangGraph`
- `Flink CDC`

but it also extracts obvious template fragments from Chinese requirement text.

Real examples from the current extractor:

```text
掌握至少一种OLAP引擎（如Doris/ClickHouse）
-> ClickHouse
-> 掌握至少一种
-> 引擎

精通Python及主流Web框架（FastAPI/Flask/Django）
-> FastAPI/Flask/Django
-> FastAPI
-> Flask
-> Django
-> 精通
-> 及主流
-> 框架
```

This is the main reason to upgrade the extractor. The current system can already count support and gate candidates. The missing piece is better phrase proposal.

## Non-Goals

This design does not:

- build a domain-specific phrase inventory
- build company entity resolution infrastructure
- revive explicit target company rewriting
- let an LLM invent rewrite terms
- replace the current `PRF gate`
- change the `70/30` second-lane budget policy
- change the company late-rescue isolation rule

## Requirements

The replacement extractor must satisfy all of the following:

1. Work on English, Chinese, and mixed-language explicit phrases.
2. Prefer extractive spans that are visibly supported by seed resumes.
3. Preserve replayability and attribution.
4. Keep the acceptance boundary deterministic.
5. Avoid dependence on manually curated domain vocabularies.
6. Stay local-first and productizable inside the repository.

## Design Options

### Option A: Better Regex And Phrase Rules

Keep the current extractor shape and improve it with more patterns and stop rules.

Pros:

- cheap
- easy to debug
- no additional model dependency

Cons:

- still weak on Chinese phrase boundaries
- still fragile on mixed-language technical phrases
- grows into the exact maintenance burden we want to avoid

### Option B: Model Span Proposal + Deterministic Gate

Use a general span extractor to propose explicit phrases from seed resumes, then keep normalization, support scoring, and gating deterministic.

Pros:

- handles English, Chinese, and mixed-language phrases better
- avoids hand-maintained lexicons
- fits the current retrieval flywheel boundary
- preserves replayability because the accepted phrase still comes from explicit seed evidence

Cons:

- adds model dependencies
- requires offline evaluation before rollout
- needs careful rejection of generic boilerplate spans

### Option C: LLM-Driven Structured Phrase Extraction

Use an LLM to emit structured PRF phrase proposals directly from seed resumes.

Pros:

- best raw flexibility
- easiest way to catch nuanced Chinese and mixed-language phrases

Cons:

- more expensive
- harder to replay and compare
- much easier to drift into free-form rewrite behavior

## Recommendation

Choose **Option B**.

This is the best fit for the current productization phase. It upgrades the weak part of the system, which is phrase proposal, without damaging the strong part, which is deterministic gating and replayable attribution.

## Chosen Architecture

`PRF v1.5` should be split into four layers.

### 1. Seed Selection

Keep the existing seed boundary from [`select_feedback_seed_resumes()`](/Users/frankqdwang/Agents/SeekTalent-0.2.4/src/seektalent/candidate_feedback/extraction.py:110).

Seed resumes remain:

- `fit_bucket == "fit"`
- `overall_score >= 75`
- `must_have_match_score >= 70`
- `risk_score <= 45`

This part is not the problem and should stay stable for Phase 1.5.

### 2. Span Proposal

Replace regex-first extraction with a general explicit-span proposer.

Input fields remain bounded to the same structured evidence surfaces:

- `evidence`
- `strengths`
- `matched_must_haves`
- `matched_preferences`

The proposer has two bounded inputs:

- a small rule path for obvious technical surface forms
- a general span model for explicit phrase extraction

The rule path stays only as a narrow helper for cases like:

- `C++`
- `FastAPI/Flask/Django`
- `Flink CDC`
- `LLM`
- `RAG`

The main proposer becomes a span model that extracts candidate phrases under a schema such as:

- `skill`
- `tool_or_framework`
- `product_or_platform`
- `technical_phrase`
- `responsibility_phrase`

This layer must remain extractive. It may only return spans grounded in seed text.

### 3. Normalization And Familying

Candidate spans should be normalized into phrase families without maintained vocabularies.

Normalization includes:

- whitespace normalization
- case normalization where appropriate
- punctuation and separator normalization
- lightweight canonicalization of mixed forms such as slash and hyphen variants

Familying should then cluster near-equivalent surface forms by embedding similarity.

Examples:

- `Flink CDC`
- `flink-cdc`
- `FlinkCDC`

should land in one family if their surface and embedding similarity indicate the same phrase.

This layer does not try to perform broad entity resolution. It only groups explicit phrase variants.

### 4. Support Scoring And Deterministic Gate

Keep the existing flywheel discipline:

- positive seed support
- negative support
- tried-family rejection
- generic template rejection
- company/entity rejection
- single accepted expression family per probe

The final acceptance boundary stays deterministic and replayable.

The system still outputs either:

- one accepted phrase family
- or no safe PRF phrase, followed by `generic_explore`

## Model Candidates

This design intentionally separates the extractor model from the embedding model.

### Span Extractor Candidates

#### Recommended first candidate: `fastino/gliner2-multi-v1`

Why:

- general schema-based extraction
- local inference path available
- multilingual line available
- better fit for explicit span extraction than free-form generation

This is the default `PRF v1.5` span-proposal candidate.

#### Higher-capacity candidate: `fastino/gliner2-large-v1`

Why:

- higher-capacity GLiNER2 variant

Why not the default:

- less clearly positioned as the multilingual first choice for our use case
- higher runtime cost

### Embedding Candidates

#### Recommended first candidate: `Alibaba-NLP/gte-multilingual-base`

Why:

- multilingual
- 305M size is practical
- supports long context
- suitable for phrase similarity, familying, and reranking

This is the best engineering default for the first offline comparison.

#### Stronger but heavier candidate: `BAAI/bge-multilingual-gemma2`

Why:

- stronger multilingual retrieval-oriented semantic quality

Why not the default:

- larger and heavier
- more expensive for broad local experimentation

#### Baseline utility candidate: `BAAI/bge-m3`

Why:

- still valuable as a multilingual hybrid baseline
- dense, sparse, and multi-vector support make it useful as a comparison point

Why not the default:

- not the clearest default engineering choice for this specific phrase-familying role anymore

## Runtime Boundary

The runtime contract does not change:

- `round 1`: exploit
- `round 2+`: exploit + typed second lane
- second lane: `prf_probe if safe else generic_explore`

The only behavior change is how `candidate_expressions` are proposed before the existing `PRF gate`.

This means:

- `PRFPolicyDecision` remains the acceptance boundary
- `SecondLaneDecision` remains the routing artifact
- `query_resume_hits` and `replay_snapshot` remain valid diagnostics

## Data Flow

The proposed `PRF v1.5` flow is:

1. Select high-quality seed resumes from round state.
2. Extract explicit candidate spans from structured seed evidence fields.
3. Normalize and family candidate spans.
4. Compute support and negative-support statistics per family.
5. Rerank candidate families with deterministic scores plus embedding-aware family grouping.
6. Apply the deterministic gate.
7. If one safe family survives, build `prf_probe`.
8. Otherwise fall back to `generic_explore`.

## Generic-Rejection Discipline

The new extractor must reject or demote these classes aggressively:

- JD template fragments
- generic verbs and evaluative boilerplate
- location, degree, compensation, and administrative phrases
- company mentions
- ungrounded inferred concepts

The key rule is:

**semantic flexibility is allowed at proposal time, but accepted query material must still be explicit, grounded, and replayable.**

## Evaluation Plan

Rollout should begin offline only.

Compare at least three extractors on the same saved seed-resume slices:

1. current regex extractor
2. `GLiNER2 multi + deterministic gate`
3. `GLiNER2 multi + normalization/familying + deterministic gate`

The evaluation target is not only retrieval gain. It is phrase quality before retrieval.

Primary phrase-quality metrics:

- accepted phrase looks like real query material
- template-fragment rate
- generic-boilerplate rate
- mixed-language phrase handling quality
- family normalization quality

Secondary retrieval metrics:

- marginal gain vs current PRF
- duplicate-only rate
- broad-noise rate
- drift-suspected rate

## Rollout Strategy

Phase 1:

- offline extractor bakeoff
- artifact logging for candidate spans, normalized families, and gate outcomes

Phase 2:

- shadow extraction inside runtime
- compare accepted phrase under old and new extractors without changing retrieval behavior

Phase 3:

- switch mainline `PRF v1.5` extractor for the second lane
- keep current fallback to `generic_explore`

## Testing Expectations

The implementation must include tests for:

- English technical phrase extraction
- Chinese technical phrase extraction
- mixed-language phrase extraction
- family normalization for separator and casing variants
- generic template rejection
- company/entity rejection
- replayability of accepted PRF phrase families
- no accepted phrase when only generic boilerplate survives

## Why This Fits The Product Plan

This design matches the long-term product direction already established in the repository:

- controlled retrieval workflow
- replayable policy comparison
- no open-ended agentic query rewriting
- no maintained vocabulary assets
- productizable local-first behavior

It also keeps the path open for later SFT without making the runtime dependent on a generated query model today.

## Sources

- [GLiNER2 GitHub](https://github.com/fastino-ai/GLiNER2)
- [fastino/gliner2-multi-v1](https://huggingface.co/fastino/gliner2-multi-v1)
- [BAAI/bge-multilingual-gemma2](https://huggingface.co/BAAI/bge-multilingual-gemma2)
- [Alibaba-NLP/gte-multilingual-base](https://huggingface.co/Alibaba-NLP/gte-multilingual-base)
- [BAAI/bge-m3](https://huggingface.co/BAAI/bge-m3)
- [Stanford IR Book: Relevance feedback and pseudo relevance feedback](https://nlp.stanford.edu/IR-book/html/htmledition/relevance-feedback-and-pseudo-relevance-feedback-1.html)
