# HardConstraints

`RequirementSheet.hard_constraints` 与 `SearchExecutionPlan_t.projected_filters` 共用的稳定约束结构。

```text
HardConstraints = {
  locations,
  min_years,
  max_years,
  company_names,
  school_names,
  degree_requirement,
  school_type_requirement,
  gender_requirement,
  min_age,
  max_age
}
```

## 稳定字段组

- 地点：`locations`
- 最少年限：`min_years`
- 最大年限：`max_years`
- 目标公司名单：`company_names`
- 目标学校名单：`school_names`
- 学历下界：`degree_requirement`
- 学校类型约束：`school_type_requirement`
- 性别约束：`gender_requirement`
- 最小年龄：`min_age`
- 最大年龄：`max_age`

## Invariants

- `locations` 只表达确定性过滤条件，不承载业务偏好。
- `min_years / max_years` 表达业务经验范围，不是 CTS bucket code。
- `company_names / school_names / school_type_requirement` 都必须是保序去重的字符串数组。
- `degree_requirement` 表达 canonical 最低学历文本，不是 CTS enum code；固定集合为 `null / 大专及以上 / 本科及以上 / 硕士及以上 / 博士及以上`。
- `gender_requirement` 表达 canonical 文本约束，不是偏好话术。
- `min_age / max_age` 表达业务年龄范围，不是 CTS enum code。
- 不是所有 `HardConstraints` 都要求进入排序层 fit gate；评分层只读取其中“具备稳定候选侧信号”的子集。
- `position` 与 `work_content` 属于检索执行时可复用的 derived projection signal，不属于 `HardConstraints`。

## 最小示例

```yaml
locations: ["Shanghai"]
min_years: 5
max_years: 10
company_names: ["阿里巴巴", "蚂蚁集团"]
school_names: ["复旦大学", "上海交通大学"]
degree_requirement: "本科及以上"
school_type_requirement: ["985", "211"]
gender_requirement: "男"
min_age: null
max_age: 35
```

## 相关

- [[RequirementSheet]]
- [[SearchExecutionPlan_t]]
