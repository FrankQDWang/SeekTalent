# Prompt Files

- `bootstrap_requirement_extraction.md`
  owner: `bootstrap_llm.py`
  input: `SearchInputTruth`
  output: `RequirementExtractionDraft`

- `bootstrap_keyword_generation.md`
  owner: `bootstrap_llm.py`
  input: bootstrap keyword packet
  output: `BootstrapKeywordDraft`

- `search_controller_decision.md`
  owner: `controller_llm.py`
  input: `SearchControllerContext_t`
  output: `SearchControllerDecisionDraft_t`

- `branch_outcome_evaluation.md`
  owner: `runtime_llm.py`
  input: branch evaluation packet
  output: `BranchEvaluationDraft_t`

- `search_run_finalization.md`
  owner: `runtime_llm.py`
  input: finalization packet
  output: `SearchRunSummaryDraft_t`
