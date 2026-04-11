# SeekTalent Runtime Sequence

This page is a compact timing view of the active `v0.3.2` runtime. It complements [SYSTEM_MODEL](/Users/frankqdwang/Agents/SeekTalent/docs/v-0.3.2/SYSTEM_MODEL.md): the model defines the semantics, while this page shows the execution order.

## 1. End-to-End Run

```mermaid
sequenceDiagram
    autonumber
    actor User
    participant Entry as "CLI / API"
    participant Bootstrap as "Bootstrap"
    participant ReqLLM as "Requirement LLM"
    participant SeedLLM as "Bootstrap Keyword LLM"
    participant Runtime as "WorkflowRuntime"
    participant CtrlLLM as "Controller LLM"
    participant Search as "Search Ops"
    participant CTS as "CTS"
    participant Rerank as "Rerank API"
    participant BranchLLM as "Branch Eval LLM"
    participant FinalLLM as "Finalization LLM"
    participant Artifacts as "Run Artifacts"

    User->>Entry: JD + notes
    Entry->>Bootstrap: bootstrap_round0_async(...)
    Bootstrap->>ReqLLM: extract requirement draft
    ReqLLM-->>Bootstrap: normalized requirement draft
    Bootstrap->>Bootstrap: normalize RequirementSheet
    Bootstrap->>Bootstrap: route packs + freeze scoring policy
    Bootstrap->>SeedLLM: generate round-0 seeds
    SeedLLM-->>Bootstrap: seed keywords
    Bootstrap->>Bootstrap: initialize frontier state
    Bootstrap-->>Runtime: bootstrap artifacts

    loop runtime round t
        Runtime->>Runtime: build RuntimeBudgetState
        Runtime->>Runtime: select active frontier node
        Runtime->>CtrlLLM: request controller draft
        CtrlLLM-->>Runtime: SearchControllerDecisionDraft
        Runtime->>Runtime: normalize decision + rewrite legality + GA-lite

        alt action = search_cts
            Runtime->>Search: materialize search plan
            Search->>CTS: search(SearchExecutionPlan)
            CTS-->>Search: retrieved candidates
            Search->>Rerank: rerank(query text, resume text[])
            Rerank-->>Search: rerank scores
            Search->>Search: score candidates + fit gate
            Search-->>Runtime: scoring result
            Runtime->>BranchLLM: request branch evaluation draft
            BranchLLM-->>Runtime: BranchEvaluationDraft
            Runtime->>Runtime: normalize branch evaluation
            Runtime->>Runtime: build rewrite evidence pool
            Runtime->>Runtime: compute reward + update frontier
        else action = stop
            Runtime->>Runtime: evaluate phase-gated stop
        end

        Runtime->>Artifacts: append SearchRoundArtifact
    end

    Runtime->>FinalLLM: request run summary draft
    FinalLLM-->>Runtime: SearchRunSummaryDraft
    Runtime->>Runtime: finalize SearchRunResult
    Runtime->>Artifacts: write bundle.json / final_result.json / eval.json
    Runtime-->>Entry: SearchRunBundle
    Entry-->>User: run_dir + stop_reason + shortlist + summary
```

### Notes

- Bootstrap freezes the requirement sheet and scoring policy before runtime starts.
- Runtime is frontier-based, not single-query iterative overwrite.
- A round either executes CTS search or exits through a stop decision.
- Artifacts are written as structured bundle data, not ad hoc logs.

## 2. Single Search Round

```mermaid
sequenceDiagram
    autonumber
    participant Runtime as "WorkflowRuntime"
    participant Frontier as "Frontier State"
    participant CtrlLLM as "Controller LLM"
    participant Rewrite as "Rewrite Normalizer"
    participant Search as "Search Ops"
    participant CTS as "CTS"
    participant Rerank as "Rerank API"
    participant BranchLLM as "Branch Eval LLM"
    participant Update as "Reward / Frontier Update"

    Runtime->>Frontier: read open nodes + operator stats
    Runtime->>Runtime: compute phase(t), max_query_terms
    Runtime->>Runtime: build SearchControllerContext
    Runtime->>CtrlLLM: controller prompt
    CtrlLLM-->>Runtime: draft operator + args
    Runtime->>Rewrite: validate legality
    Rewrite->>Rewrite: optional GA-lite rewrite search
    Rewrite-->>Runtime: normalized controller decision
    Runtime->>Search: materialize SearchExecutionPlan
    Search->>CTS: search(query_terms, projected_filters)
    CTS-->>Search: deduplicated candidates
    Search->>Rerank: rerank(rerank_query_text, candidate.search_text[])
    Rerank-->>Search: scores
    Search->>Search: must-have / preferred / risk / fit / fusion
    Search-->>Runtime: SearchScoringResult
    Runtime->>BranchLLM: branch evaluation prompt
    BranchLLM-->>Runtime: novelty / usefulness / exhausted draft
    Runtime->>Update: rewrite evidence + reward + frontier transition
    Update-->>Runtime: SearchRoundArtifact + next FrontierState
```

### Notes

- The controller does not own the frontier; it only chooses a local action for the active node.
- Rewrite normalization is deterministic after the LLM draft returns.
- Candidate scoring is `rerank + deterministic scoring + binary fit gate`.
- Reward update and stop evaluation are deterministic owners.

## 3. Timing-Critical Boundaries

- `RequirementSheet` is frozen before runtime; downstream stages should not re-derive requirements from raw JD text.
- `query_terms_hit(...)` is the shared text-match owner across selection, rewrite evidence, and scoring.
- Non-crossover search rounds execute a rewritten full query, not `parent query + appended terms`.
- `controller_stop` and `exhausted_low_gain` are phase-gated by the same runtime budget state.

## Related Docs

- [System Model](/Users/frankqdwang/Agents/SeekTalent/docs/v-0.3.2/SYSTEM_MODEL.md)
- [Implementation Owners](/Users/frankqdwang/Agents/SeekTalent/docs/v-0.3.2/IMPLEMENTATION_OWNERS.md)
- [Architecture](/Users/frankqdwang/Agents/SeekTalent/docs/architecture.md)
