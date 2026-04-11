# GenerateBootstrapOutput

把 `BootstrapKeywordDraft` 收口成 round-0 可执行 seeds。

## Signature

```text
GenerateBootstrapOutput : (
  RequirementSheet,
  BootstrapRoutingResult,
  DomainKnowledgePack[],
  BootstrapKeywordDraft,
  max_seed_terms
) -> BootstrapOutput
```

## 当前规则

- 先把 `candidate_seeds` 规范化成 materializable seed specs
- 每条 seed 的 `seed_terms` 都会按 `max_seed_terms` 截断
- 再强制保留 `core_precision / relaxed_floor`
- single-pack 额外强制保留 `pack_expansion`
- multi-pack 额外强制保留 `cross_pack_bridge`
- 其余候选按 Jaccard overlap 做 greedy orthogonal prune

## round-0 term cap

- `max_seed_terms` 不是 bootstrap 私有配置。
- 它必须直接等于 `RuntimeTermBudgetPolicy.explore_max_query_terms`。
- 也就是说，round-0 seed 从一开始就按 `explore` 的 CTS 交集语义执行，不再允许 bootstrap 单独放宽到 4 词。

## 最终数量

- `generic_fallback`：4 条
- `explicit_pack / inferred_single_pack / inferred_multi_pack`：5 条

## 相关

- [[BootstrapKeywordDraft]]
- [[FrontierSeedSpecification]]
- [[RuntimeTermBudgetPolicy]]
