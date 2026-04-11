# Implementation Owners

This document is the only maintained implementation anchor for the canonical model in `SYSTEM_MODEL.md`. It maps model sections to the smallest set of code owners and trace windows needed for implementation, debugging, and review.

| Model Section | Primary Code Owner | Shared Helper / Trace Owner |
| --- | --- | --- |
| Inputs and Derived State | `requirements/normalization.py`, `bootstrap_llm.py` | `SearchRunBootstrapArtifact.input_truth`, `SearchRunBootstrapArtifact.requirement_sheet` |
| Bootstrap Routing | `bootstrap_ops.route_domain_knowledge_pack` | `SearchRunBootstrapArtifact.routing_result` |
| Scoring Policy Freeze | `bootstrap_ops.freeze_scoring_policy` | `SearchRunBootstrapArtifact.scoring_policy` |
| Rerank Query Construction | `rerank_text.build_rerank_query_text` | `ScoringPolicy.rerank_query_text` |
| Round-0 Seed Generation | `bootstrap_llm.py`, `bootstrap_ops.generate_bootstrap_output` | `SearchRunBootstrapArtifact.bootstrap_output` |
| Frontier Initialization | `bootstrap_ops.initialize_frontier_state` | `SearchRunBootstrapArtifact.frontier_state` |
| Runtime Entry | `runtime/orchestrator.py`, `api.py` | `SearchRunBundle`, `run_dir` artifacts |
| Phase Progression | `runtime_budget.build_runtime_budget_state` | `SearchControllerContext_t.runtime_budget_state`, `SearchRunBundle.eval` |
| Active Node Selection | `frontier_ops.select_active_frontier_node` | `active_selection_breakdown`, `selection_ranking` |
| Operator Legality Surface | `frontier_ops._allowed_operator_names`, `frontier_ops._donor_candidate_summaries` | `allowed_operator_names`, `operator_surface_override_reason` |
| Query Rewrite Normalization | `frontier_ops.generate_search_controller_decision_with_trace` | `controller_decision`, `rewrite_choice_trace` |
| GA-lite Rewrite Ranking | `frontier_ops._ga_lite_query_rewrite`, `frontier_ops._rewrite_fitness` | `rewrite_choice_trace` |
| Rewrite Evidence Term Pool | `rewrite_evidence.build_rewrite_term_pool` | `rewrite_term_pool` |
| CTS Plan Materialization | `search_ops.materialize_search_execution_plan` | `execution_plan` |
| CTS Execution | `search_ops.execute_search_plan_sidecar`, `clients/cts_client.py` | `execution_result`, `search_page_statistics` |
| Candidate Scoring | `search_ops.score_search_results` | `scoring_result` |
| Shared Text Match Semantics | `query_terms.query_terms_hit` | reused by selection, rewrite, evidence, scoring |
| Reward Computation | `runtime_ops.compute_node_reward_breakdown` | `reward_breakdown` |
| Frontier Update | `runtime_ops.update_frontier_state` | `frontier_state_after` |
| Stop Guard | `runtime_ops.build_effective_stop_guard`, `runtime_ops.evaluate_stop_condition` | `effective_stop_guard`, `stop_reason` |
| Run Finalization | `runtime_llm.py`, `runtime_ops.finalize_search_run` | `final_result`, `run_summary` |
| Prompt Surfaces | `prompt_surfaces.py` | `LLMCallAudit.prompt_surface` |
| Run Diagnostics | `run_artifacts.build_search_run_eval` | `SearchRunBundle.eval`, `eval.json` |
| Offline Replay Tuning | `replay_tuning.py`, `scripts/replay_tuning.py` | replay tuning report JSON / Markdown |
