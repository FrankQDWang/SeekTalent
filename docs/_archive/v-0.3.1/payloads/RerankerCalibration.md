# RerankerCalibration

给定 reranker 模型的固定校准参数快照。

```text
RerankerCalibration = { model_id, normalization, temperature, offset, clip_min, clip_max, calibration_version }
```

## 稳定字段组

- 模型 id：`model_id`
- 归一化方式：`normalization`
- temperature：`temperature`
- 偏移：`offset`
- clip 下界：`clip_min`
- clip 上界：`clip_max`
- 校准版本：`calibration_version`

## Direct Producer / Direct Consumers

- Direct producer：runtime calibration registry
- Direct consumers：[[FreezeScoringPolicy]]

## Invariants

- `temperature` 属于模型校准，不属于业务偏好。
- 同一 `calibration_version` 下，相同 raw score 必须映射到稳定 normalized score。
- `normalization` 只能来自 runtime 支持的有限集合。

## 最小示例

```yaml
model_id: "qwen3-8b-reranker"
normalization: "sigmoid"
temperature: 2.4
offset: 0.0
clip_min: -12
clip_max: 12
calibration_version: "2026-04-07-v1"
```

## 相关

- [[FreezeScoringPolicy]]
- [[ScoringPolicy]]
- [[ScoreSearchResults]]
