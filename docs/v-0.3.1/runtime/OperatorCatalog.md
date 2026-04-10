# OperatorCatalog

```text
OperatorCatalog = {
  core_precision,
  must_have_alias,
  relaxed_floor,
  pack_expansion,
  cross_pack_bridge,
  generic_expansion,
  crossover_compose
}
```

## 语义

- `core_precision`：围绕核心精准 seed 扩展
- `must_have_alias`：围绕 must-have 表达变体扩展
- `relaxed_floor`：围绕降维兜底 seed 扩展
- `pack_expansion`：围绕单 pack 的领域上下文扩展
- `cross_pack_bridge`：围绕多 pack 桥接 seed 扩展
- `generic_expansion`：围绕通用扩展 seed 扩展
- `crossover_compose`：从 active node 与 donor node 的共享锚点做交叉

## Invariants

- `pack_expansion / cross_pack_bridge` 只有在 `knowledge_pack_ids` 非空时才合法
- `crossover_compose` 永不作为 round-0 seed operator

## 相关

- [[FrontierSeedSpecification]]
- [[GenerateSearchControllerDecision]]
