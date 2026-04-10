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
