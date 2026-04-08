# Agent Trace: case-crossover-legal

## Trace Meta

```yaml
case_id: "case-crossover-legal"
audience: "agent"
paired_business_trace: "[[trace-business-case-crossover-legal]]"
start_payloads: ["RequirementSheet", "FrontierState_t", "ScoringPolicy"]
terminal_artifact: "stop_reason / continue_flag"
terminal_status: "continue_after_search"
node_id_rendering: "human-readable case-local example ids; runtime id owner remains operator specs"
```

## Scenario Inputs

```yaml
RequirementSheet:
  role_title: "Senior Python / LLM Engineer"
  must_have_capabilities:
    - "Python backend"
    - "LLM application"
    - "retrieval or ranking experience"
  hard_constraints:
    locations: ["Shanghai"]
    min_years: 5
FrontierState_t:
  open_frontier_node_ids:
    - "child_agent_core_01"
    - "child_search_domain_01"
    - "seed_platform"
  run_shortlist_candidate_ids: ["c17", "c32", "c91"]
  remaining_budget: 4
ScoringPolicy:
  fit_gate_constraints:
    locations: ["Shanghai"]
    min_years: 5
```

## Operator Steps

### 1. SelectActiveFrontierNode

```yaml
operator_name: "SelectActiveFrontierNode"
operator_inputs:
  open_frontier_node_ids:
    - "child_agent_core_01"
    - "child_search_domain_01"
    - "seed_platform"
tools_or_services: ["runtime deterministic priority scoring", "runtime donor packing"]
operator_outputs:
  SearchControllerContext_t:
    active_frontier_node_summary:
      frontier_node_id: "child_agent_core_01"
      selected_operator_name: "must_have_alias"
      node_query_term_pool: ["agent engineer", "rag", "python"]
      node_shortlist_candidate_ids: ["c32", "c44"]
    donor_candidate_node_summaries:
      - frontier_node_id: "child_search_domain_01"
        shared_anchor_terms: ["rag"]
        expected_incremental_coverage: ["retrieval or ranking experience"]
        reward_score: 3.9
    allowed_operator_names:
      - "must_have_alias"
      - "strict_core"
      - "domain_company"
      - "crossover_compose"
key_assertions:
  - "Only donor candidates with shared anchor and reward evidence are packed."
failure_or_reject_reason: null
```

### 2. GenerateSearchControllerDecision

```yaml
operator_name: "GenerateSearchControllerDecision"
operator_inputs:
  active_frontier_node_id: "child_agent_core_01"
  donor_candidate_ids: ["child_search_domain_01"]
tools_or_services: ["SearchControllerDecisionLLM", "runtime deterministic normalization"]
llm_audit:
  output_mode: "provider_native_strict_structured_output"
  retries: 0
  output_retries: 1
  validator_retry_count: 0
operator_outputs:
  SearchControllerDecision_t:
    action: "search_cts"
    target_frontier_node_id: "child_agent_core_01"
    selected_operator_name: "crossover_compose"
    operator_args:
      donor_frontier_node_id: "child_search_domain_01"
      shared_anchor_terms: ["rag"]
      donor_terms_used: ["retrieval engineer", "ranking"]
    expected_gain_hypothesis: "Keep the agent/rag anchor while adding retrieval coverage."
key_assertions:
  - "The donor id is constrained to the packed donor candidate list."
failure_or_reject_reason: null
```

### 3. MaterializeSearchExecutionPlan

```yaml
operator_name: "MaterializeSearchExecutionPlan"
operator_inputs:
  parent_frontier_node_id: "child_agent_core_01"
  donor_frontier_node_id: "child_search_domain_01"
  shared_anchor_terms: ["rag"]
  donor_terms_used: ["retrieval engineer", "ranking"]
tools_or_services: ["runtime deterministic logic"]
operator_outputs:
  SearchExecutionPlan_t:
    query_terms: ["rag", "retrieval engineer", "ranking"]
    projected_filters:
      locations: ["Shanghai"]
      min_years: 5
    runtime_only_constraints:
      must_have_keywords:
        - "Python backend"
        - "LLM application"
        - "retrieval or ranking experience"
        - "rag"
        - "retrieval engineer"
        - "ranking"
      negative_keywords:
        - "data analyst"
        - "pure algorithm research"
    source_card_ids:
      - "role_alias.llm_agent_rag_engineering.backend_agent_engineer"
      - "role_alias.search_ranking_retrieval_engineering.retrieval_engineer"
    child_frontier_node_stub:
      frontier_node_id: "child_crossover_03"
      parent_frontier_node_id: "child_agent_core_01"
      donor_frontier_node_id: "child_search_domain_01"
      selected_operator_name: "crossover_compose"
key_assertions:
  - "Shared anchor satisfies crossover guard and donor lineage is preserved."
failure_or_reject_reason: null
```

### 4. ExecuteSearchPlan

```yaml
operator_name: "ExecuteSearchPlan"
operator_inputs:
  query_terms: ["rag", "retrieval engineer", "ranking"]
  projected_filters:
    locations: ["Shanghai"]
    min_years: 5
tools_or_services: ["CTS.search", "runtime deterministic filtering"]
operator_outputs:
  SearchExecutionResult_t:
    raw_candidates: ["c07", "c19", "c51", "c77", "c77"]
    deduplicated_candidates: ["c07", "c19", "c51", "c77"]
    search_page_statistics:
      pages_fetched: 2
      duplicate_rate: 0.25
      latency_ms: 1800
    search_observation:
      unique_candidate_ids: ["c07", "c19", "c51", "c77"]
      shortage_after_last_page: false
key_assertions:
  - "CTS results are filtered and deduplicated before scoring."
failure_or_reject_reason: null
```

### 5. ScoreSearchResults

```yaml
operator_name: "ScoreSearchResults"
operator_inputs:
  deduplicated_candidate_ids: ["c07", "c19", "c51", "c77"]
  fit_gate_constraints:
    locations: ["Shanghai"]
    min_years: 5
tools_or_services: ["RerankService", "runtime deterministic fusion"]
operator_outputs:
  SearchScoringResult_t:
    node_shortlist_candidate_ids: ["c07", "c19", "c51"]
    explanation_candidate_ids: ["c07", "c19", "c51"]
    top_three_statistics:
      average_fusion_score_top_three: 0.735
    scored_candidates:
      - {candidate_id: "c07", fit: 1, fusion_score: 0.804}
      - {candidate_id: "c19", fit: 1, fusion_score: 0.763}
      - {candidate_id: "c51", fit: 1, fusion_score: 0.637}
      - {candidate_id: "c77", fit: 0, fusion_score: 0.385}
key_assertions:
  - "Shortlist is produced by rerank plus deterministic fusion, not by LLM ranking."
failure_or_reject_reason: null
```

### 6. EvaluateBranchOutcome

```yaml
operator_name: "EvaluateBranchOutcome"
operator_inputs:
  parent_frontier_node_id: "child_agent_core_01"
  donor_frontier_node_id: "child_search_domain_01"
  node_shortlist_candidate_ids: ["c07", "c19", "c51"]
tools_or_services: ["BranchOutcomeEvaluationLLM", "runtime deterministic normalization"]
llm_audit:
  output_mode: "provider_native_strict_structured_output"
  retries: 0
  output_retries: 1
  validator_retry_count: 0
operator_outputs:
  BranchEvaluation_t:
    novelty_score: 0.66
    usefulness_score: 0.74
    branch_exhausted: false
    repair_operator_hint: "strict_core"
    evaluation_notes: "Shared-anchor crossover improved retrieval and ranking coverage."
key_assertions:
  - "Branch remains open because the round added useful fit candidates."
failure_or_reject_reason: null
```

### 7. ComputeNodeRewardBreakdown

```yaml
operator_name: "ComputeNodeRewardBreakdown"
operator_inputs:
  parent_shortlist_candidate_ids: ["c32", "c44"]
  node_shortlist_candidate_ids: ["c07", "c19", "c51"]
  prior_run_shortlist_candidate_ids: ["c17", "c32", "c91"]
tools_or_services: ["runtime deterministic logic"]
operator_outputs:
  NodeRewardBreakdown_t:
    delta_top_three: 0.19
    must_have_gain: 0.67
    new_fit_yield: 3
    novelty: 0.66
    usefulness: 0.74
    diversity: 0.44
    stability_risk_penalty: 0.12
    hard_constraint_violation: 0.0
    duplicate_penalty: 0.25
    cost_penalty: 0.30
    reward_score: 4.26
key_assertions:
  - "Positive reward comes from net-new shortlist yield and stronger top-three quality."
failure_or_reject_reason: null
```

### 8. UpdateFrontierState

```yaml
operator_name: "UpdateFrontierState"
operator_inputs:
  parent_frontier_node_id: "child_agent_core_01"
  child_frontier_node_id: "child_crossover_03"
  reward_score: 4.26
tools_or_services: ["runtime deterministic logic"]
operator_outputs:
  FrontierState_t1:
    open_frontier_node_ids:
      - "child_search_domain_01"
      - "seed_platform"
      - "child_crossover_03"
    closed_frontier_node_ids:
      - "seed_agent_core"
      - "seed_search_domain"
      - "child_agent_core_01"
    run_shortlist_candidate_ids: ["c07", "c17", "c19", "c51", "c32", "c91"]
    semantic_hashes_seen:
      - "hash_seed_01"
      - "hash_seed_02"
      - "hash_crossover_03"
    remaining_budget: 3
key_assertions:
  - "The parent closes and the new child carries reward and evaluation."
failure_or_reject_reason: null
```

### 9. EvaluateStopCondition

```yaml
operator_name: "EvaluateStopCondition"
operator_inputs:
  remaining_budget: 3
  open_frontier_node_ids:
    - "child_search_domain_01"
    - "seed_platform"
    - "child_crossover_03"
  action: "search_cts"
  branch_exhausted: false
  reward_score: 4.26
tools_or_services: ["runtime stop guard"]
operator_outputs:
  stop_reason: null
  continue_flag: true
key_assertions:
  - "The run continues because budget remains, open nodes remain, and the branch is not low gain."
failure_or_reject_reason: null
```

## Invariant Checks

- `donor_candidate_node_summaries` contains exactly one legal donor
- `SearchExecutionPlan_t.child_frontier_node_stub.donor_frontier_node_id = "child_search_domain_01"`
- `reward_score > 1.5`
- `stop_reason = null`

## Terminal Outcome

```yaml
terminal_artifact: "stop_reason / continue_flag"
stop_reason: null
continue_flag: true
next_required_operator: "SelectActiveFrontierNode"
```

## Judge Packet

```yaml
expected_route: "crossover_search"
expected_allowed_operators: ["must_have_alias", "strict_core", "domain_company", "crossover_compose"]
expected_tool_calls:
  - "SearchControllerDecisionLLM"
  - "CTS.search"
  - "RerankService"
  - "BranchOutcomeEvaluationLLM"
expected_terminal_state:
  open_frontier_node_ids:
    - "child_search_domain_01"
    - "seed_platform"
    - "child_crossover_03"
  continue_flag: true
must_hold:
  - "The donor is selected from packed donor candidates."
  - "Materialization preserves donor lineage and shared anchor."
  - "The parent frontier node is closed after update."
must_not_hold:
  - "failure_or_reject_reason = crossover_requires_shared_anchor"
  - "stop_reason = controller_stop"
  - "child_frontier_node_stub.donor_frontier_node_id = null"
```
