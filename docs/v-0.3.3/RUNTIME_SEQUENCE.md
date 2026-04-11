# SeekTalent Runtime Sequence

This page is a compact timing view of the active `v0.3.3` runtime. It complements [SYSTEM_MODEL](/Users/frankqdwang/Agents/SeekTalent/docs/v-0.3.3/SYSTEM_MODEL.md): the model defines the semantics, while this page shows the execution order.

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
    Bootstrap->>Bootstrap: initialize frontier state with persistent root anchors
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
            CTS-->>Search: raw candidates
            Search->>Search: deduplicate + project candidates
            Search->>Rerank: rerank(query text, resume text[])
            Rerank-->>Search: rerank scores
            Search->>Search: score candidates + fit gate + candidate evidence cards
            Search-->>Runtime: scoring result
            Runtime->>Runtime: build rewrite evidence pool
            Runtime->>BranchLLM: request branch evaluation draft
            BranchLLM-->>Runtime: BranchEvaluationDraft
            Runtime->>Runtime: normalize branch evaluation
            Runtime->>Runtime: compute reward + update frontier
            Runtime->>Runtime: root anchors stay open; repair branches may close
        else action = stop
            Runtime->>Runtime: evaluate phase-gated stop
        end

        Runtime->>Artifacts: append SearchRoundArtifact
    end

    Runtime->>FinalLLM: request run summary draft
    FinalLLM-->>Runtime: SearchRunSummaryDraft_t
    Runtime->>Runtime: finalize final candidate cards + reviewer summary
    Runtime->>Artifacts: write bundle.json / final_result.json / eval.json
    Runtime-->>Entry: SearchRunBundle
```

## 2. Key Runtime Semantics

- Bootstrap seeds are `root_anchor` branches.
- Post-bootstrap child nodes are `repair_hypothesis` branches.
- Root anchors remain open after execution and receive the latest branch evaluation and reward snapshot.
- Final output is reviewer-ready: candidate evidence cards and reviewer summary are first-class outputs.
- Normal stop semantics can end on `controller_stop`, `budget_exhausted`, `exhausted_low_gain`, or `no_productive_open_path`.

## Related docs

- [System Model](/Users/frankqdwang/Agents/SeekTalent/docs/v-0.3.3/SYSTEM_MODEL.md)
- [Implementation Owners](/Users/frankqdwang/Agents/SeekTalent/docs/v-0.3.3/IMPLEMENTATION_OWNERS.md)
- [Outputs](/Users/frankqdwang/Agents/SeekTalent/docs/outputs.md)
