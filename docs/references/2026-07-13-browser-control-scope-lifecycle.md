# Browser Control Scope 生命周期与崩溃恢复模型

日期：2026-07-13

实施状态（2026-07-15）：OpenCLI fork 已提供 fence、精确关闭和 60 秒 extension alarm；SeekTalent 已实现有界后台队列、可降级 SQLite ownership mirror，以及 search/expand 的 `finally` 回收提交。该生命周期尚未接入生产 worker 构造，需等离线 bundle 启动链路一起启用。

## 决策摘要

浏览器回收是旁路改善，绝不能成为 source run、重试或后续扩展的启动门槛。

- `source run` 只表达业务归属。
- 每次实际浏览器执行创建新的 `browser control scope`；重试或后续扩展即使复用同一个 source run，也必须使用新 scope。
- 每个 owned tab 使用独立、不透明的 OpenCLI session 和独立 60 秒 idle deadline。
- scope 结束时，正常路径只执行 non-throwing 的回收登记与唤醒；即使登记失败也立即离开，不等待 Chrome 关闭，不改变业务结果。
- 新 scope 立即取得同源命令权，不等待旧 tab 消失。旧 tab 继续显示锁定层并按原 deadline 自动回收。
- 同源串行约束的是“谁能继续发猎聘命令”，不是“浏览器里只能存在一个 tab”。
- 旧进程恢复后必须被 fencing 拒绝；它最多只能请求关闭自己原来拥有的精确 tab，不能继续导航、点击或填写。
- 任何回收失败都是独立的 cleanup 结果，不能覆盖成功、失败或取消的原始业务结果。
- 倒计时 UI、本地 ownership record、后台 reclaimer 或精确 close 任一环节故障，都只能降级为诊断事件，不能中止、延迟或取消正常 run。
- 如果浏览器命令通道本身不可用，只失败当前 Liepin source invocation；其他数据源和总体 runtime 继续按既有 source 隔离语义运行。

## 实施前基线（已处理）

以下是 2026-07-13 设计开始前的结构性问题，用于保留决策背景，不代表当前代码状态：

1. `OpenCliBrowserConfig` 只有一个固定 session，无法表达一 tab 一 session。
2. `liepin_site_adapter.py` 的 `seektalent.opencli_lease.v1` 只保存一个 page；`seektalent.opencli_owned_page.v1` 虽可保存多个 page，但都挂在同一个 session 下。
3. marker 通过 `opened_at + OWNED_PAGE_MARKER_TTL_SECONDS` 失效。到期只是不再信任 marker，并不能证明 tab 已关闭；反过来还会丢失回收线索。
4. 当前 marker 中的 `runtime_run_id`、`source_lane_run_id` 在正常新建路径里写成 `None`，`source_run_id` 又实际接收 `trace_id/source_lane_run_id`，名称与业务 Workbench source run 不一致。
5. `_OPENCLI_SEARCH_LOCK` 只保护单个 Python 进程，不能阻止另一进程或恢复后的旧进程继续发命令。
6. 当前测试明确要求 finalize 后保留 detail tabs，并拒绝 cleanup action；这些断言与新产品要求相反。
7. Workbench runtime job 有数据库 lease，但 lease 丢失不会停止已经运行的旧线程，因此不能把它当作浏览器命令 fencing。

## 身份与所有权

### 业务身份

`source_run_id`、`runtime_run_id` 和 `source_lane_run_id` 只用于追踪业务来源。它们可以写入 private ownership record，但不能单独授权关闭 tab。

### 浏览器控制身份

每次进入猎聘浏览器执行边界时生成：

```text
scope_id     = random UUID
lane_key     = hash(source_kind, browser_profile_id, provider_account_hash)
fence_token  = extension 为 lane 原子分配的单调递增整数
tab_token    = random UUID, 每个 tab 一个
session      = opaque(scope_id, tab_token), 每个 tab 一个
page_id      = OpenCLI/Chrome target identity
```

`scope_id` 不能从 `source_run_id` 派生。后续扩展可能复用旧 `source_run_id`，但必须生成新 scope 和更高 fence。

关闭授权必须同时满足：

1. SeekTalent record 指向 `scope_id + tab_token + session + page_id`；
2. OpenCLI extension registry 确认该 session 当前拥有同一个 page；
3. extension 确认 page 是 borrowed host window 中的 owned tab，而不是 user tab。

本地 record 只是所有权线索，不是单独的关闭凭证。record 损坏或不一致时宁可等待 extension idle alarm，也不能按 URL 猜测并关闭 tab。

## Source control lane 与 fencing

lane key 初期定义为：

```text
(source_kind, browser_profile_id, provider_account_hash)
```

同一 lane 同时只有一个 scope 拥有命令权；不同 source_kind 仍可并行。

新 scope 启动时向 extension 发送显式 activation。extension 原子分配并返回下一 `fence_token`，同时把前一个 scope 的 page-command 权限作废；本地 registry 只镜像结果。它不等待旧 tab 回收。

每个会改变或读取 provider page 状态的 OpenCLI 命令都携带 opaque `controlKey` 和 `fenceToken`。fork extension 为 control key 持久化当前最高 fence：

- token 等于当前值：允许命令；
- token 小于当前值：返回稳定错误 `stale_control_fence`，不得触碰页面；
- 新 token 通过显式 scope activation 提升当前值，不能由普通 page 命令偷偷提升。

extension 是 fence 的唯一分配者。这样本地 registry 损坏、丢失或暂时不可写时，新 scope 仍能立即取得更高 token；若 extension 本身不可达，则当前 Liepin browser invocation 以 transport unavailable 结束，而不是等待旧 tab 或拖住其他 source。

只在 SeekTalent 进程内检查 token 不够，因为旧进程可能在“检查通过”和“发出命令”之间暂停。最终检查必须在 extension 执行 page 命令前完成。该能力保持通用，不包含 Liepin 字样。

以下操作不受旧 scope 的 page-command 权限影响：

- extension 自己按 idle deadline 回收该 scope 的 tab；
- 用户通过 Chrome UI 关闭 tab；
- 使用精确 session/page 的幂等 close。旧 scope 可以关闭自己的旧 tab，但不能导航它，也不可能关闭新 scope 的 tab。

## 持久化 ownership record

使用独立的本地 SQLite registry，而不是继续堆叠 per-session JSON：

```text
~/.seektalent/browser-control/browser-control.sqlite3
```

该 registry 用于崩溃后 reconcile、后台回收和私有诊断，不承担命令授权、fence 分配或 60 秒关闭时钟。它必须是可降级的本地镜像：不可写、损坏或短暂锁住都不能阻止正常浏览器命令。也不应把浏览器生命周期表耦合进 Workbench 业务数据库。

### `browser_control_scopes`

| 字段 | 语义 |
|---|---|
| `scope_id` | 随机浏览器控制批次 ID |
| `lane_key_hash` | 所属 source control lane |
| `fence_token` | scope 获得的 fencing token |
| `state` | `active | superseded | reclaim_requested | reclaimed` |
| `created_at` | 创建时间 |
| `reclaim_requested_at` | 交给后台回收的时间 |
| `reclaimed_at` | 所有 tab 已确认消失的时间 |

### `browser_owned_tabs`

| 字段 | 语义 |
|---|---|
| `tab_token` | SeekTalent 生成的 tab 身份 |
| `scope_id` | 所属 browser control scope |
| `session` | 唯一 OpenCLI session |
| `page_id` | OpenCLI/Chrome target identity；创建确认前可为空 |
| `tab_kind` | `search | detail`，不保存候选人 URL |
| `state` | `allocating | owned | reclaim_requested | reclaim_failed | reclaimed | extension_fallback` |
| `created_at` | 创建意图落库时间 |
| `last_command_completed_at` | 最近一次已确认完成的浏览器命令 |
| `idle_deadline_at` | extension 返回的绝对 deadline；仅作 UI/观测镜像 |
| `reclaim_requested_at` | 后台回收请求时间 |
| `reclaimed_at` | 已验证关闭或不存在的时间 |
| `close_outcome` | `closed | already_missing | failed` |
| `last_error_code` | 失败时的稳定安全错误码 |

不保存完整猎聘 URL、候选人 ID、简历标识或页面文本。live registry 不是长期审计日志；tab 回收完成并写入安全事件后，可清除已经 reclaimed 的 scope/tab 记录。

## 防止“创建成功但来不及记账”

registry 可用时，新建 tab 使用两阶段记录：

1. 先生成 `tab_token + session`，插入 `allocating` record；
2. 用该 session 请求 OpenCLI 在 host window 创建 inactive tab；
3. 返回 page 后，把 record 更新为 `owned` 并保存 `page_id + idle_deadline_at`。

如果进程在第 2、3 步之间崩溃，extension registry 仍能按 session 找到并自动回收 tab；后续 reconciler 也可用 session 补回 page。绝不通过“扫描所有猎聘 URL”猜测 ownership。

如果第 1 步因本地 registry 故障失败，不能因此拒绝本次浏览器操作。extension 仍以唯一 session 建立 ownership、设置 60 秒 deadline 并返回精确 page；当前进程在内存中保留该身份并尽力请求关闭。进程同时崩溃时，由 extension 自身 registry 和 alarm 回收。也就是说，本地持久化提高可观测性和主动回收速度，但不是正常执行或最终关闭的单点依赖。

## Deadline 与 touch 语义

OpenCLI extension 的 `idleDeadlineAt` 是关闭时钟的唯一真相。SeekTalent registry 中的 deadline 只是 UI 和遥测镜像，不能自行触发关闭。

- `idleTimeout` 固定传 `60`，协议单位为秒。
- 每个已经到达 extension 并完成的 browser command，无论业务结果成功还是结构化失败，都刷新该 tab 的 deadline。
- 传输超时或连接断开时，SeekTalent 不猜测命令是否完成，也不自行 touch；等待 extension registry/reconcile 给出真相。
- OpenCLI page-scoped response 应返回本次命令完成后实际生效的 `idleDeadlineAt`。
- 一个 tab 的命令不能刷新另一个 tab；每个 tab 的独立 session 保证隔离。
- 页面内倒计时自行渲染，不能每秒发送 touch 命令。

当前 `_touch_lease()` 分散在 Liepin adapter 多个方法中，应删除；deadline 更新收口到 browser transport 的统一命令返回路径。

## Scope 和 tab 状态机

```text
scope:
  active
    -> superseded          新 scope 立即取得更高 fence
    -> reclaim_requested   当前工作正常/失败/取消结束
  superseded
    -> reclaim_requested   后台发现旧 scope
  reclaim_requested
    -> reclaimed           所有 owned tabs 已验证消失

tab:
  allocating
    -> owned               OpenCLI 返回精确 page
    -> extension_fallback  未取得 page，由 extension alarm 接管
  owned
    -> reclaim_requested   scope 结束，后台接管
    -> reclaimed           用户关闭或 idle alarm 已关闭
  reclaim_requested
    -> reclaimed           closed / already_missing
    -> reclaim_failed      精确关闭失败
```

`superseded` 和 `reclaim_failed` 都不会占用 source control lane，也不会阻止新 scope。

## 正常路径不等待回收

scope 使用 `try/finally`，但 finally 中禁止运行 SQLite 或同步 OpenCLI close：

1. 向有界、non-blocking 的本地生命周期队列提交 scope/tab 快照；
2. 立即返回原业务结果或继续传播原异常/取消；
3. 后台按提交顺序写 registry，再做一次精确 close。

后台 reclaimer 独立遍历该 scope 的精确 session/page 并请求关闭。单次 close 失败不做主动重试，由 extension alarm 兜底，避免回收增强形成额外负载或无界工作。

步骤 1 或 2 的异常必须在 cleanup 边界内收敛成安全诊断，禁止向外抛出。如果通知未送达、进程随后退出或 reclaimer 本身崩溃，extension 的独立 60 秒 alarm 仍负责兜底。因此不需要让 source run 等待任何 cleanup timeout。

## 故障隔离边界

所有附加的生命周期能力默认 fail-open 于业务执行、fail-safe 于 ownership：

- controlled-tab 灰色锁定层或页面倒计时渲染失败：不影响浏览器命令；extension deadline 继续生效。
- 本地 registry 写入、读取、迁移或 reconcile 失败：不影响浏览器命令；不根据不完整记录关闭任何 tab。
- 后台 reclaimer 启动、通知、精确 close 或验证失败：不影响业务返回；extension alarm 继续兜底。
- 安全事件或诊断上报失败：不影响业务，也不得形成无界重试。
- extension 的 ownership、fence activation 或 page-command 通道失败：不再安全发送该次 Liepin 命令，只结束当前 Liepin source invocation；不能取消其他 source 或整个 runtime。

这些边界不得靠调用方记得 `try/except`。生命周期模块暴露的 cleanup、record 和 telemetry API 本身必须 non-throwing，并有测试证明；真正的 browser command API 继续保留明确错误语义。

## 故障矩阵

| 场景 | 业务路径 | tab 结果 | 新 scope |
|---|---|---|---|
| 正常完成 | 登记后台回收后立即返回 | 后台立即关闭；60 秒 alarm 兜底 | 立即可启动 |
| 业务失败 | 保留原失败原因 | 同正常完成 | 立即可启动 |
| 用户取消 | 登记后继续传播 cancellation | 同正常完成 | 立即可启动 |
| SeekTalent 进程崩溃 | 无 finally | extension 按各自原 deadline 回收 | 立即用更高 fence 启动 |
| daemon 崩溃 | source 得到 transport 错误 | extension alarm 不依赖 daemon | 立即重连或由新 scope 决定 |
| extension service worker 重启 | 当前命令可能失败/重放 | 按持久化绝对 deadline reconcile | 不等待旧 tab |
| 用户手动关闭 tab | 当前 page 命令返回 missing | `already_missing`，清理 record | 不受影响 |
| 精确 close 失败 | 只写 cleanup warning | 保持锁定，由 alarm 兜底 | 不受影响 |
| 旧进程恢复 | 原业务结果不得覆盖新结果 | 旧 tab 仍可自我关闭 | page command 被 stale fence 拒绝 |
| registry 损坏 | 记录/回收能力降级，不改变业务结果 | 不按本地记录关闭；extension alarm 继续 | 由 extension 分配更高 fence，立即启动 |
| 锁定层/倒计时渲染失败 | browser command 正常继续 | extension deadline 不受影响 | 不受影响 |
| 安全事件写入失败 | 忽略上报失败，不重试阻塞 | ownership/deadline 不受影响 | 不受影响 |

## 幂等回收结果

OpenCLI 单 tab close 仍返回：

```json
{
  "requested": "<page-id>",
  "outcome": "closed | already_missing | failed",
  "verified": true,
  "errorCode": null
}
```

SeekTalent 后台汇总可以增加 `deferred`，表示没有等待 close 或 close 暂未成功、已交给 idle alarm。该汇总只进入安全事件与诊断：

```text
requested
closed
already_missing
deferred
failed
```

不得用 cleanup summary 改写 source run 的 `status`、`safe_reason_code` 或候选人结果。

## 观测事件

至少记录以下私有安全事件，不包含 URL/page/session 原值：

- `browser_control_scope_started`
- `browser_control_scope_superseded`
- `owned_tab_created`
- `owned_tab_reclaim_requested`
- `owned_tab_reclaimed`
- `owned_tab_reclaim_failed`
- `stale_browser_scope_rejected`

公开层只需要聚合数量与安全 reason code，例如 `browser_tab_cleanup_deferred`。页面 target 和 session 只允许以 hash 出现在 private diagnostics。

## 实现边界

### SeekTalent

- `src/seektalent/opencli_browser/contracts.py`：增加 scope、owned tab 和 close result 小型 dataclass。
- `src/seektalent/opencli_browser/lifecycle.py`：有界后台队列、精确 close 和启动恢复。
- `src/seektalent/opencli_browser/lifecycle_registry.py`：可降级的 SQLite ownership mirror；不保存 URL 或账号原值。
- `src/seektalent/opencli_browser/automation.py`：每条 page command 显式携带 session/page/control key/fence，并统一同步 deadline。
- `src/seektalent/providers/liepin/opencli_retriever.py`：每次 search/expand 调用进入新的 browser control scope；finally 只登记后台回收。
- `src/seektalent/providers/liepin/liepin_site_adapter.py`：删除固定 session 的 v1 lease/owned-page marker、TTL 猜测、tab select 依赖和散落的 `_touch_lease()`。
- `src/seektalent/providers/liepin/opencli_worker_client.py`：现有进程内 lock 可保留为减少竞争的快速路径，但不再承担正确性。

### OpenCLI 1.8.6 fork

- extension registry 保存 `controlKey -> highestFenceToken`，原子分配下一 token，并在执行 page command 前拒绝 stale token。
- scope activation 是显式协议和 fence 唯一分配入口，不由普通命令或本地数据库隐式提升 fence。
- page result 返回实际 `idleDeadlineAt`。
- extension alarm、用户 close 和精确 owned-session close 不依赖当前 active scope。
- 这些都是通用浏览器控制能力，不含 SeekTalent/Liepin 业务字段。

## Breaking-change 处理

不把 `seektalent.opencli_lease.v1` 或 `seektalent.opencli_owned_page.v1` 自动迁移成新 ownership。旧 marker 不包含可靠 scope/fence/session-per-tab 证据，迁移后自动关闭存在误关风险。

升级时隔离旧 marker，只让新 scope 创建新 registry。旧 tab 由用户手动关闭或由旧 OpenCLI 行为处理；绝不按旧 URL/TTL 声明 ownership。

## 验证清单

1. 同一 source run 的重试获得不同 scope/session 和更高 fence。
2. 新 scope 启动不等待旧 tab，也不改变旧 tab 的 60 秒 deadline。
3. 旧进程恢复后，任何 navigate/click/fill/state 都得到 `stale_control_fence`，页面没有变化。
4. 不同 source lane 可并行；同一 Liepin lane 只有最高 fence 可发 page command。
5. 正常完成、业务失败和 cancellation 都立即返回原结果；cleanup 延迟不出现在业务耗时关键路径。
6. 进程在 tab create 返回前后各崩溃一次，extension 最终都能按 session 回收。
7. service worker 重启不刷新 deadline；到原时间后真实关闭。
8. 用户手动关闭得到 `already_missing`，不重建 tab，也不报业务失败。
9. close remove 注入失败时，新 run 仍立即开始，旧 tab 保持锁定并由 alarm/后台重试处理。
10. registry 损坏时不关闭任何 user tab，新 scope 仍能使用新 fence 工作。
11. 任意数量 owned tabs 都有独立 record/session/deadline；不存在固定 tab 上限。
12. public event/result 不包含 URL、page、session、候选人标识或 provider account hash。
13. 分别注入倒计时 UI、本地 registry、reclaimer、close verification 和 telemetry 故障，业务结果、耗时关键路径及其他 source 均不受影响。
14. extension/daemon 命令通道故障只结束当前 Liepin source invocation，不取消同一 runtime 中其他 source。

## 对后续事项的影响

[验证完整受控 Tab 回收路径](https://github.com/FrankQDWang/SeekTalent/issues/295) 的成功标准必须增加：新 scope 不等待旧 tab；旧 scope 的 page command 被 fence 拒绝；cleanup 不计入 source run 关键路径。原来的“run 结束立即关闭”应解释为“立即提交后台关闭请求”，而不是“等待 tab 关闭后才返回”。
