# Agent Trace: case-stop-exhausted-low-gain-and-finalize

## Trace Meta

```yaml
case_id: "case-stop-exhausted-low-gain-and-finalize"
audience: "agent"
paired_business_trace: "[[trace-business-case-stop-exhausted-low-gain-and-finalize]]"
start_payloads:
  - "RequirementSheet"
  - "FrontierState_t"
  - "SearchControllerDecision_t"
  - "SearchExecutionPlan_t"
  - "SearchExecutionResult_t"
  - "SearchScoringResult_t"
terminal_artifact: "SearchRunResult"
terminal_status: "stopped_and_finalized"
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
FrontierState_t:
  open_frontier_node_ids:
    - "seed_platform"
    - "child_search_domain_01"
  run_shortlist_candidate_ids: ["c07", "c17", "c19"]
  remaining_budget: 3
SearchControllerDecision_t:
  action: "search_cts"
  target_frontier_node_id: "seed_platform"
  selected_operator_name: "domain_company"
SearchExecutionPlan_t:
  query_terms: ["enterprise saas", "workflow orchestration", "to-b delivery"]
  child_frontier_node_stub:
    frontier_node_id: "child_platform_02"
    parent_frontier_node_id: "seed_platform"
    donor_frontier_node_id: null
    selected_operator_name: "domain_company"
SearchExecutionResult_t:
  deduplicated_candidates: ["c17", "c88", "c94"]
  search_page_statistics:
    pages_fetched: 2
    duplicate_rate: 0.5
    latency_ms: 1700
SearchScoringResult_t:
  node_shortlist_candidate_ids: ["c17"]
  top_three_statistics:
    average_fusion_score_top_three: 0.58
```

## Operator Steps

### 1. EvaluateBranchOutcome

```yaml
operator_name: "EvaluateBranchOutcome"
operator_inputs:
  parent_frontier_node_id: "seed_platform"
  query_terms: ["enterprise saas", "workflow orchestration", "to-b delivery"]
  node_shortlist_candidate_ids: ["c17"]
tools_or_services: ["BranchOutcomeEvaluationLLM", "runtime deterministic normalization"]
llm_audit:
  output_mode: "provider_native_strict_structured_output"
  retries: 0
  output_retries: 1
  validator_retry_count: 0
operator_outputs:
  BranchEvaluation_t:
    novelty_score: 0.14
    usefulness_score: 0.18
    branch_exhausted: true
    repair_operator_hint: "strict_core"
    evaluation_notes: "This branch mostly recycled existing shortlist evidence and added little coverage."
key_assertions:
  - "The branch is exhausted despite producing one already-known shortlist candidate."
failure_or_reject_reason: null
```

### 2. ComputeNodeRewardBreakdown

```yaml
operator_name: "ComputeNodeRewardBreakdown"
operator_inputs:
  parent_shortlist_candidate_ids: []
  node_shortlist_candidate_ids: ["c17"]
  prior_run_shortlist_candidate_ids: ["c07", "c17", "c19"]
tools_or_services: ["runtime deterministic logic"]
operator_outputs:
  NodeRewardBreakdown_t:
    delta_top_three: 0.01
    must_have_gain: 0.24
    new_fit_yield: 0
    novelty: 0.14
    usefulness: 0.18
    diversity: 0.0
    stability_risk_penalty: 0.2
    hard_constraint_violation: 0.0
    duplicate_penalty: 0.5
    cost_penalty: 0.30
    reward_score: 0.46
key_assertions:
  - "Reward stays below the stop floor because the branch adds no net-new fit yield."
failure_or_reject_reason: null
```

### 3. UpdateFrontierState

```yaml
operator_name: "UpdateFrontierState"
operator_inputs:
  parent_frontier_node_id: "seed_platform"
  child_frontier_node_id: "child_platform_02"
  branch_exhausted: true
  reward_score: 0.46
tools_or_services: ["runtime deterministic logic"]
operator_outputs:
  FrontierState_t1:
    open_frontier_node_ids: ["child_search_domain_01"]
    closed_frontier_node_ids:
      - "seed_agent_core"
      - "seed_search_domain"
      - "seed_platform"
      - "child_platform_02"
    run_shortlist_candidate_ids: ["c07", "c17", "c19"]
    remaining_budget: 2
key_assertions:
  - "The parent closes and the exhausted child does not remain open."
failure_or_reject_reason: null
```

### 4. EvaluateStopCondition

```yaml
operator_name: "EvaluateStopCondition"
operator_inputs:
  remaining_budget: 2
  open_frontier_node_ids: ["child_search_domain_01"]
  action: "search_cts"
  branch_exhausted: true
  novelty_score: 0.14
  usefulness_score: 0.18
  reward_score: 0.46
tools_or_services: ["runtime stop guard"]
operator_outputs:
  stop_reason: "exhausted_low_gain"
  continue_flag: false
key_assertions:
  - "Low-gain stop fires even though one other open node remains."
failure_or_reject_reason: null
```

### 5. FinalizeSearchRun

```yaml
operator_name: "FinalizeSearchRun"
operator_inputs:
  ranked_shortlist_candidate_ids: ["c07", "c17", "c19"]
  stop_reason: "exhausted_low_gain"
tools_or_services: ["SearchRunFinalizationLLM", "runtime deterministic logic"]
llm_audit:
  output_mode: "provider_native_strict_structured_output"
  retries: 0
  output_retries: 1
  validator_retry_count: 0
operator_outputs:
  SearchRunResult:
    final_shortlist_candidate_ids: ["c07", "c17", "c19"]
    stop_reason: "exhausted_low_gain"
    run_summary: "The final shortlist remains the previously accumulated top candidates because the latest branch added too little value."
key_assertions:
  - "Finalization preserves the existing shortlist because the last branch did not improve it."
failure_or_reject_reason: null
```

## Invariant Checks

- `branch_exhausted = true`
- `reward_score < 1.5`
- `stop_reason = "exhausted_low_gain"`
- `remaining_budget > 0` and `open_frontier_node_ids` is non-empty

## Terminal Outcome

```yaml
terminal_artifact: "SearchRunResult"
stop_reason: "exhausted_low_gain"
final_shortlist_candidate_ids: ["c07", "c17", "c19"]
```

## Judge Packet

```yaml
expected_route: "search_then_exhausted_low_gain"
expected_allowed_operators: ["must_have_alias", "strict_core", "domain_company", "crossover_compose"]
expected_tool_calls:
  - "BranchOutcomeEvaluationLLM"
  - "runtime stop guard"
  - "SearchRunFinalizationLLM"
expected_terminal_state:
  stop_reason: "exhausted_low_gain"
  continue_flag: false
must_hold:
  - "The branch is marked exhausted."
  - "novelty_score and usefulness_score are both below the stop floors."
  - "reward_score is below the reward floor."
must_not_hold:
  - "stop_reason = budget_exhausted"
  - "stop_reason = no_open_node"
  - "final_shortlist_candidate_ids changes due to the low-gain branch"
```
