# 外部数据源契约

## 目标

把 CTS、Liepin 变成外部数据源实现，核心 runtime 只处理通用源接口。

## 推荐新增/重构模块

```text
src/seektalent/sources/
  __init__.py
  contracts.py
  registry.py
  planning.py
  public_events.py
  boundary_checks.py    # 可选：若机器检查留在 tools/，这里不需要
```

不要创建过多抽象层。只保留 runtime 真正需要的协议。

## SourceId

用普通 `str` 表示 source id，不再使用：

```python
Literal["cts", "liepin"]
```

source id 是注册表里的 key，不是 runtime 的分支条件。

## SourceCapabilities

必须表达真实能力，不表达具体 provider 品牌。

建议字段：

- `supports_card_search`
- `supports_detail_fetch`
- `supports_native_filters`
- `supports_incremental_detail`
- `requires_human_login`
- `max_safe_concurrency`
- `stable_external_id`
- `stable_dedup_key`

## SourceBudgetPolicy

当前 `RuntimeSourceBudgetPolicy` 混合了 CTS 和 Liepin 字段。目标是拆成：

- 通用 runtime budget：每个 source 的 card target、detail target、scan limit。
- source-local defaults：由 source adapter 提供。
- BFF policy：由 Workbench 读取 source registry 或 source admin API。

不要在 runtime model 中放 `liepin_max_cards`、`cts_page_size` 这类字段。

## SourcePlan

runtime 生成计划时只保留：

- `source_id`
- `source_plan_id`
- `runtime_run_id`
- `enabled`
- `mode`
- `budget`
- `safe_posture`
- `query_intents`

provider-specific posture 只能是安全摘要，不能泄漏 token、cookie、raw payload。

## SourceLaneRequest

通用字段：

- runtime/session ids
- job title、JD、notes
- requirement sheet
- source query terms
- query intent
- budget
- approved detail lease if any
- progress callback

禁止字段：

- `liepin_context`
- `opencli`
- CTS page field
- provider worker mode
- provider-specific connection payload

这些字段由 source adapter 内部从配置、BFF/session store 或 source-local context 获取。

## SourceLaneResult

通用字段：

- candidate updates
- normalized updates
- source evidence
- provider snapshot refs
- safe summary refs
- detail recommendations
- source events
- status
- public reason code
- retryable
- safe error summary

provider-specific raw data 不得进入 public result。raw provider payload 只能走 artifact/store，且带 privacy metadata。

## Detail Lease

当前 `RuntimeApprovedDetailLease` 是 Liepin-only。目标：

- 改为 generic `ApprovedDetailLease`
- source-specific approval material 留在 `provider_payload_ref` 或 source-local lease store
- runtime 只验证 lease 与 runtime/source/session/candidate 的关联
- source adapter 验证 provider-specific signature、idempotency key、day budget

## Public reason code

runtime 只认公共 code：

- `source_backend_unavailable`
- `source_timeout`
- `source_login_required`
- `source_risk_challenge`
- `source_filter_unavailable`
- `source_filter_unsupported`
- `source_budget_exhausted`
- `source_provider_error`
- `source_cancelled`
- `source_partial`

Liepin 的 `liepin_opencli_*` 只能存在于 Liepin provider/worker 边界，并映射成公共 code。

## Registry

目标 registry 行为：

```text
build_source_registry(settings, local_services) -> SourceRegistry
registry.enabled_sources(requested_ids) -> tuple[RegisteredSource, ...]
registered_source.plan(...)
registered_source.run_card_lane(...)
registered_source.run_detail_lane(...)
```

不要做 entry point 插件系统，除非现有部署真的需要。当前阶段用显式注册函数足够。

## 必须有的第三源测试

新增一个测试用 source，例如 `fixture_source`：

- 不放进 production 默认配置。
- 通过 registry 注册。
- 返回 1-2 个候选人和 evidence。
- runtime 不改代码即可执行它。

这是证明“runtime 与 CTS/Liepin 解耦”的关键验收。
