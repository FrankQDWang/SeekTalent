# BusinessPolicyPack

run 级业务偏好包。

```text
BusinessPolicyPack = { knowledge_pack_id_override, fusion_weight_preferences, fit_gate_overrides, stability_policy, explanation_preferences }
```

## 稳定字段组

- 单领域显式 override：`knowledge_pack_id_override`
- 融合权重偏好：`fusion_weight_preferences`
- fit gate 覆盖：`fit_gate_overrides`
- 稳定性策略：`stability_policy`
- 解释偏好：`explanation_preferences`

## Direct Producer / Direct Consumers

- Direct producer：runtime policy file
- Direct consumers：[[RouteDomainKnowledgePack]]、[[FreezeScoringPolicy]]

## Invariants

- `BusinessPolicyPack` 表达偏好，不表达需求真相。
- `knowledge_pack_id_override` 一旦显式填写，就必须直接驱动 routing。
- 当前只允许显式指定一个领域，不再支持多领域 override。
- `fit_gate_overrides` 只能收紧 truth gate，不能放宽 `RequirementSheet.hard_constraints`。

## 相关

- [[DomainKnowledgePack]]
- [[ScoringPolicy]]
