# SeekTalent v0.3.1 Trace Spec

当前 canonical trace 仍是双视图：

- `Agent Trace`
- `Business Trace`

## 必须覆盖的 bootstrap cases

- `case-bootstrap-explicit-pack`
- `case-bootstrap-inferred-single-pack`
- `case-bootstrap-close-high-score-multi-pack`
- `case-bootstrap-out-of-domain-generic`

## 当前关键信号

- `routing_mode`
- `selected_knowledge_pack_ids`
- round-0 seed 数量
- 每轮 `operator / knowledge_pack_ids / stop_reason`
- `rewrite_choice_trace`
- `search_round_indexes`
- `search_phase_by_search_round`
- `selected_operator_by_search_round`
- `eligible_open_node_count_by_search_round`
- `selection_margin_by_search_round`
- `must_have_query_coverage_by_search_round`
- `net_new_shortlist_gain_by_search_round`
- `run_shortlist_size_after_search_round`
- `operator_distribution_explore / balance / harvest`
