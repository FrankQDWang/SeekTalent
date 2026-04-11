# Prompt Files

Complete prompt behavior is split across two layers:

- `src/seektalent/prompts/*.md`: static instruction prompts for each callpoint
- `src/seektalent/prompt_surfaces.py`: dynamic context sections rendered into `PromptSurfaceSnapshot.input_text`

Rule of thumb:

- put reusable task policy, rubrics, output contracts, and few-shot examples in the `.md` instruction prompt
- put run-specific facts, derived helper facts, and ordered dynamic context in `prompt_surfaces.py`
- do not put few-shot examples into dynamic surfaces

- `bootstrap_requirement_extraction.md`
  owner: `bootstrap_llm.py`
  input: `PromptSurfaceSnapshot(surface_id=requirement_extraction).input_text`
  output: `RequirementExtractionDraft`

- `bootstrap_keyword_generation.md`
  owner: `bootstrap_llm.py`
  input: `PromptSurfaceSnapshot(surface_id=bootstrap_keyword_generation).input_text`
  output: `BootstrapKeywordDraft`

- `search_controller_decision.md`
  owner: `controller_llm.py`
  input: `PromptSurfaceSnapshot(surface_id=search_controller_decision).input_text`
  output: `SearchControllerDecisionDraft_t`

- `branch_outcome_evaluation.md`
  owner: `runtime_llm.py`
  input: `PromptSurfaceSnapshot(surface_id=branch_outcome_evaluation).input_text`
  output: `BranchEvaluationDraft_t`

- `search_run_finalization.md`
  owner: `runtime_llm.py`
  input: `PromptSurfaceSnapshot(surface_id=search_run_finalization).input_text`
  output: `SearchRunSummaryDraft_t`
