# FitGateConstraints

排序层与控制器只读使用的稳定 fit gate 结构。

```text
FitGateConstraints = {
  locations,
  min_years,
  max_years,
  company_names,
  school_names,
  degree_requirement,
  gender_requirement,
  min_age,
  max_age
}
```

## 稳定字段组

- 地点约束：`locations`
- 最少年限：`min_years`
- 最大年限：`max_years`
- 公司约束：`company_names`
- 学校约束：`school_names`
- 学历约束：`degree_requirement`
- 性别约束：`gender_requirement`
- 最小年龄：`min_age`
- 最大年龄：`max_age`

## Invariants

- `locations` 是候选人通过 gate 的允许地点集合。
- `min_years / max_years` 为 `null` 时表示该侧不设经验 gate。
- `min_age / max_age` 为 `null` 时表示该侧不设年龄 gate。
- `company_names / school_names` 只保留稳定 allowlist，不携带模糊查询语法。
- `degree_requirement` 固定使用 `null / 大专及以上 / 本科及以上 / 硕士及以上 / 博士及以上`，显式“不限”在上游已折叠为 `null`。
- `FitGateConstraints` 是 `HardConstraints` 的“可门槛化子集”，不强行复制缺少稳定候选侧信号的字段；`school_type_requirement` 因默认缺少稳定候选侧标签，不进入此结构。

## 最小示例

```yaml
locations: ["Shanghai"]
min_years: 5
max_years: 10
company_names: ["阿里巴巴", "蚂蚁集团"]
school_names: ["复旦大学", "上海交通大学"]
degree_requirement: "本科及以上"
gender_requirement: "男"
min_age: null
max_age: 35
```

## 相关

- [[ScoringPolicy]]
- [[SearchControllerContext_t]]
