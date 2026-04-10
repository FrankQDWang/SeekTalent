# GenerateBootstrapOutput

把 `BootstrapKeywordDraft` 收口成 round-0 可执行 seeds。

## Signature

```text
GenerateBootstrapOutput : (
  RequirementSheet,
  BootstrapRoutingResult,
  DomainKnowledgePack[],
  BootstrapKeywordDraft
) -> BootstrapOutput
```

## 当前规则

- 先把 `candidate_seeds` 规范化成 materializable seed specs
- 再强制保留 `core_precision / relaxed_floor`
- single-pack 额外强制保留 `pack_expansion`
- multi-pack 额外强制保留 `cross_pack_bridge`
- 其余候选按 Jaccard overlap 做 greedy orthogonal prune

## 最终数量

- `generic_fallback`：4 条
- `explicit_pack / inferred_single_pack / inferred_multi_pack`：5 条

## 相关

- [[BootstrapKeywordDraft]]
- [[FrontierSeedSpecification]]
