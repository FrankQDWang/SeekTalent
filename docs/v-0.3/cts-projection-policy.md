# SeekTalent v0.3 CTS Projection Policy

## 0. 文档定位

本页定义 `v0.3` 如何把 `SearchExecutionPlan_t` 落到真实 `CTS` API。

- `SearchExecutionPlan_t.projected_filters` 是业务层稳定约束，不是原始 CTS payload
- `CTS Adapter` 负责把这些稳定约束安全映射成真实请求字段，并执行协议级 payload 校验 / 转码
- `SearchExecutionPlan_t.runtime_only_constraints` 仍由 runtime 本地执行，不要求 CTS 原生支持
- `SearchExecutionPlan_t.derived_position` / `derived_work_content` 是显式 plan 字段，adapter 只能从 plan 读取，不得回头读取 `RequirementSheet`
- round 内的地点 dispatch、分页补拉、无进展停止等编排逻辑由 runtime 持有，不下沉到 `CTSClient.search(...)`

本页是 `v0.3` 的 CTS 投影 owner。`docs/v-0.2/cts-enum-observations.md` 只是观测证据，不再是实现时必须翻回去的规范入口。

## 1. 继承结论

`v0.3` 继续复用当前代码库已经验证过的 CTS adapter 特性，不重写以下边界：

1. 不把完整 `JD` 或自由 prompt 直接发给 CTS
2. 不把未确认安全的枚举值直接投给 CTS
3. 不发送 synthetic “不限” 或近似无效 filter code
4. 允许 runtime 围绕单次 `CTS.search(...)` 复用现有 CTS 特有编排逻辑，例如地点 dispatch、分页补拉、无进展停止与审计记录
5. 允许 adapter 继续复用现有协议级转码、payload 校验与安全枚举映射

因此，`ExecuteSearchPlan` 里的 `CTS.search(cts_request_t)` 表示“在 runtime 编排下调用现有真实 CTS adapter 能力”，不是“直接拼裸 HTTP payload”。

当前已验证过的 CTS enum / bucket substrate 继续以现有实现为唯一真相：

- `src/seektalent/retrieval/filter_projection.py`
- `src/seektalent/clients/cts_client.py`
- `tests/test_filter_projection.py`

`v0.3` 文档只持有业务投影边界，不复制第二份 code table。

## 2. 当前 v0.3 的投影边界

当前 `v0.3` 的稳定业务 schema 中，`HardConstraints` 已正式包含：

- `locations`
- `min_years`
- `max_years`
- `company_names`
- `school_names`
- `degree_requirement`
- `school_type_requirement`
- `gender_requirement`
- `min_age`
- `max_age`

对应规则如下。

其中，当前代码库已验证过的 CTS 安全映射 substrate 继续直接复用：

- `company_names -> company`
- `school_names -> school`
- `degree_requirement -> degree`
- `school_type_requirement -> schoolType`
- `min_years / max_years -> workExperienceRange`
- `gender_requirement -> gender`
- `min_age / max_age -> age`
- `SearchExecutionPlan_t.derived_position -> position`
- `SearchExecutionPlan_t.derived_work_content -> workContent`

### 2.1 `locations`

- `projected_filters.locations` 表达允许地点集合，不是最终 CTS 协议值
- runtime 必须继续复用现有地点执行逻辑
- 如果现有 CTS 路径要求单城市 dispatch，则 runtime 在 `CTS.search(...)` 外围逐城市执行
- runtime 侧不维护新的 geography taxonomy；地点别名映射继续信任 CTS 上游能力
- 若 `locations` 为空，则 CTS 请求中省略地点字段

### 2.2 `min_years / max_years`

- `projected_filters.min_years / max_years` 表达业务经验范围，不是 CTS 枚举 code
- adapter 只在存在 runtime-safe mapping 时，才把该范围映射到 CTS 的 `workExperienceRange`
- 当前 safe mapping 继续继承已验证区间：
  - `< 1` 年 -> `1`
  - `1-3` 年 -> `2`
  - `3-5` 年 -> `3`
  - `5-10` 年 -> `4`
  - `10+` 年 -> `5`
- adapter 按当前已验证的 CTS bucket substrate 选择“重叠最大、tie-break 最稳定”的可用 bucket；这一步以真实 CTS 枚举兼容为准，不额外承诺完全保真地表达业务真值范围
- 跨 bucket 的业务范围允许落到相邻但更粗的 CTS bucket，例如 `3-8` 年当前会落到 `5-10年`
- 若无法稳定归入当前已验证 bucket，则必须省略该 CTS 字段
- CTS 端省略后，经验约束仍由 `ScoringPolicy.fit_gate_constraints` 和 `passes_fit_gate(...)` 在排序阶段兜底

### 2.3 `company_names / school_names`

- 这两个字段都是稳定 allowlist，不是自由检索字符串拼接入口
- adapter 继续把它们投影到 CTS 的 `company` / `school`
- 归一化后若为空，则省略对应 CTS 字段

### 2.4 `degree_requirement / school_type_requirement / gender_requirement`

- 三者都表达业务 canonical 值，不直接暴露 CTS enum code
- adapter 只在存在 runtime-safe enum mapping 时才下推到 `degree` / `schoolType` / `gender`
- 显式“不限”不得下推 CTS，也不生成 runtime-only 失败约束
- `school_type_requirement` 即使未进入排序层 fit gate，也仍然是正式的 retrieval hard constraint

### 2.5 `min_age / max_age`

- `projected_filters.min_age / max_age` 表达业务年龄范围，不是 CTS enum code
- adapter 只在存在 runtime-safe mapping 时，才把它映射到 CTS 的 `age`
- adapter 同样按当前已验证 age bucket substrate 选择最稳定的可用 bucket；若范围跨越过宽或无法稳定归桶，允许保留为 runtime / score 层约束

### 2.6 `derived_position / derived_work_content`

- 两者都是 `SearchExecutionPlan_t` 的显式可选字段
- `derived_position` 来自 `RequirementSheet.role_title`
- `derived_work_content` 来自 `RequirementSheet.must_have_capabilities` 的稳定摘要，不是自由 prompt
- adapter 可以直接把它们投影到 CTS 的 `position` / `workContent`
- 若字段为空，则省略对应 CTS 字段

## 3. `runtime_only_constraints` 的继承边界

`v0.3` 明确保留两类 runtime-only 逻辑：

- `negative_keywords`
- `must_have_keywords`

处理规则固定为：

1. `negative_keywords` 本地过滤，不要求 CTS 支持
2. `must_have_keywords` 只做 runtime audit tag，不做 CTS 过滤，也不是硬 reject
3. candidate 去重发生在 runtime-only 过滤之后

这部分逻辑不下推 CTS，继续由 runtime 执行。

## 4. 禁止事项

以下行为在 `v0.3` 中明确禁止：

- 把 `projected_filters` 直接当 CTS 裸 payload 发出
- 为了“尽量发过滤器”而发送未确认 label/code
- 发送观测上等价于“近似无过滤”的占位 code
- 因 CTS 不支持某字段就静默丢失业务约束，不留后续 gate
- 在执行层偷偷回读 `RequirementSheet` 来补 `position` / `workContent`

## 5. 扩展规则

如果未来 `HardConstraints` 再加入新的稳定字段，必须遵守同一模式：

1. 先进入业务层稳定 schema
2. 只在存在 runtime-safe enum mapping 时投影到 CTS
3. 否则保留为 runtime/score 层约束
4. 不得在 operator 文档里直接塞枚举目录

如果未来要改变 `derived_position / derived_work_content` 的生成方式，也必须先改 `SearchExecutionPlan_t` owner，再改 adapter；不得在执行层偷偷回读其他对象。

## 6. 与其他文档的关系

- 计划层 owner：[[SearchExecutionPlan_t]]
- 计划物化：[[MaterializeSearchExecutionPlan]]
- 执行层 owner：[[ExecuteSearchPlan]]
- 候选结果 owner：[[RetrievedCandidate_t]]、[[ScoringCandidate_t]]
- 排序 gate：[[FreezeScoringPolicy]]、[[ScoreSearchResults]]
- 证据参考：`docs/v-0.2/cts-enum-observations.md`
