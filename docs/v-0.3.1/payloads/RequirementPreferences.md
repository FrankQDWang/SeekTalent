# RequirementPreferences

`RequirementSheet.preferences` 使用的稳定偏好槽位。

```text
RequirementPreferences = { preferred_domains, preferred_backgrounds }
```

## 稳定字段组

- 期望领域：`preferred_domains`
- 期望背景：`preferred_backgrounds`

## Invariants

- 所有字段都必须是字符串数组。
- 缺失字段回写为空数组。

## 最小示例

```yaml
preferred_domains: ["enterprise ai"]
preferred_backgrounds: ["search platform"]
```

## 相关

- [[RequirementSheet]]
- [[ExtractRequirements]]
