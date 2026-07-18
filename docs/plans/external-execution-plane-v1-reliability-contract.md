# External Execution Plane v1 生产可靠性验收契约

- 状态：冻结候选（Issue #321）
- 适用范围：SeekTalent External Execution Plane v1 及其完整桌面发布物
- 上游决议：[#320](https://github.com/FrankQDWang/SeekTalent/issues/320)
- 下游任务：[#323](https://github.com/FrankQDWang/SeekTalent/issues/323)、[#324](https://github.com/FrankQDWang/SeekTalent/issues/324)、[#326](https://github.com/FrankQDWang/SeekTalent/issues/326)
研究输入：[Local Durable Execution Technology Research](../references/2026-07-17-local-durable-execution-technology-research.md)

## 1. 目的和结论

本契约回答一个发布问题：**给定一个 SeekTalent External Execution Plane v1 版本，团队如何判断它能不能交付给真实用户，以及不能交付时究竟缺什么。**

v1 不在用户电脑部署 Temporal、Restate、Prefect 或 Celery。它加固现有 SQLite durable control plane，并把 browser/source execution 收敛到受监督的本地 sidecar，通过明确的 Source Execution Port 与主应用交互。

当前代码已经具备 durable run、lease/attempt fencing、checkpoint、command、candidate truth 等可复用基础，但安装、运行、恢复、诊断和发布验证尚未闭环。因此：

> **当前 0.7.49 不满足本契约，只能作为内部测试版本，不能作为 External Execution Plane v1 生产版本发布。**

主要缺口不是单一猎聘 case，而是：

1. 用户拿到的不是一个经过验证的完整产品发布物；
2. 已接受任务仍依赖进程内 wake/thread，而不是持续消费 SQLite durable queue；
3. browser/source 边界还不是可版本化、可幂等、可恢复的进程间协议；
4. 很多内部原因最终被压平为“浏览器不可用/跑不了”，无法现场归因；
5. 目前没有 exact-artifact、clean-machine、跨平台故障注入的强制发布门禁。

## 2. 规范语言与适用对象

本文中的“必须”“不得”是 release-blocking 要求；“应该”是默认设计，偏离时必须在 PR 或 release evidence 中记录理由；“可以”不构成发布承诺。

本契约验收的是**完整产品发布物**，不是单独的 Python wheel、源码仓库、开发机或某个浏览器脚本。

“发布版本”指一组不可拆分的、可追溯的组件：

- SeekTalent Python 应用与 UI；
- 固定版本的 Python runtime 或经验证的宿主 runtime；
- Node runtime 与 WTSCLI/source sidecar；
- browser bridge、Chrome extension 及其 manifest；
- SQLite runtime/control schema 与实际 SQLite build；
- 安装器、升级器、回滚元数据和组件清单；
- 该版本对应的诊断 schema、redaction policy 和测试证据。

只验证 wheel、只验证源码 checkout、只在开发机成功，均不构成生产验收。

## 3. v1 支持边界

### 3.1 OS、架构和浏览器

v1 目标平台为：

| 平台 | v1 决议 | 发布含义 |
| --- | --- | --- |
| Windows 11 x64 | 支持目标 | 必须有独立完整发布物及 clean-machine 证据 |
| macOS arm64 | 支持目标 | 必须签名、公证，并有独立完整发布物及 clean-machine 证据 |
| macOS x86_64 | 支持目标 | 必须签名、公证，并有独立完整发布物及 clean-machine 证据 |
| Windows ARM、Linux | v1 非目标 | 必须在 preflight 中明确拒绝，不得尝试后静默失败 |
| Chrome Stable | 唯一生产浏览器 | 支持范围由每次 release receipt 固定到实际验证的版本窗口 |
| Chrome Beta/Dev/Canary、Edge 和其他 Chromium | v1 非目标 | 不得以“理论兼容”宣称支持 |

本契约不凭空指定最低 OS 小版本或 Chrome 主版本。每次发布必须由 capability receipt 列出实际验证过的 OS build、CPU 架构、Chrome 版本窗口、Domi 版本、runtime 和 extension/bridge 协议版本。receipt 之外的组合是“未验证”，不是“已支持”。

### 3.2 安装与升级路径

生产支持路径必须是 GUI/Domi 集成的完整安装器。安装器必须：

- 验证发布清单、签名/公证、组件哈希和架构；
- 安装或验证 Python、Node、WTSCLI、bridge、extension 和应用的兼容组合；
- 创建 capability receipt，并能由 `doctor` 和 support bundle 读取；
- 在切换 active slot 前完成 staging、自检和数据迁移可恢复性检查；
- 失败时保留上一可用版本和用户数据；
- 升级后第一次启动若失败，能够自动回退应用 slot，或给出不破坏数据的明确人工回滚动作。

以下行为可以用于开发或内部 staging，但**不得**出现在生产安装/恢复说明中：

- 要求用户执行 pip、uv、npm、git 或导出环境变量；
- 要求用户启用 Chrome developer mode 并 load unpacked extension；
- 要求用户手动杀端口/进程、删除 SQLite 或清空应用目录；
- 让用户自行判断 Python、Node、bridge、extension 的版本组合；
- 把 PyPI wheel 当作完整产品发布物。

### 3.3 Chrome profile、登录和用户交互

v1 的默认支持方式是：使用用户已有的 Chrome Stable 登录会话，但产品必须确定性绑定一个 profile、一个 extension 实例和一个猎聘账号主体。运行期间：

- 只允许控制产品创建并登记的 owned tab；
- 不得接管、关闭或导航 user tab、host tab 或其他非 owned tab；
- 必须使用 `BrowserControlScope(scope_id, control_key, fence_token)` 或等价 fencing，过期控制者不得继续产生副作用；
- profile、extension、账号主体不明确或发生变化时，必须在执行前阻塞并给出可操作原因；
- 自动恢复不得跨 profile、跨账号或跨 extension 实例猜测执行。

允许的人工交互只有：

1. 首次安装/升级时确认系统和 extension 权限；
2. 用户在 Chrome 中完成猎聘登录、短信/二维码/验证码、风控挑战；
3. 存在多个 profile 或账号时做一次明确选择；
4. 网站明确需要人工确认时，在 `needs_attention` 状态下完成确认后恢复。

产品不得采集用户密码、自动绕过风控、把验证码外发，或用隐藏 profile 伪装登录。专用产品 profile 可作为后续方案，但在真实账号、企业策略、extension 分发和用户迁移完成专项验证前，不是 v1 发布前置条件。

### 3.4 网络、企业代理和 CA

v1 支持直接联网，以及 release matrix 中明确验证过的系统/环境代理路径。对于 TLS interception、自定义企业 CA、认证代理或被企业策略管理的 Chrome：

- 若发布物没有显式配置、可诊断性和 clean-machine 证据，则该组合属于 v1 未支持范围；
- preflight 必须识别可识别的代理/证书/策略阻塞，并返回 typed reason；
- 不得把证书失败伪装成 LLM、猎聘或 extension 故障；
- 不得通过关闭 TLS 校验、信任任意证书或静默降级来“兼容”。

## 4. 端到端成功定义

### 4.1 发布级 canary 成功

一个平台/发布物只有同时满足以下条件，才通过端到端发布 canary：

1. 从本次候选发布的 exact artifact 在 clean machine 完成安装或受支持升级；
2. 组件签名/哈希、版本和 capability receipt 一致；
3. 应用启动，SQLite schema/integrity、磁盘、daemon/sidecar、bridge、extension、Chrome/profile/account readiness 全部得到明确结论；
4. 一个 source run 在向 UI 返回 accepted 前已经写入 durable SQLite queue；
5. sidecar 以稳定的 operation ID、request hash、deadline 和 attempt/fence 接收执行；
6. 在受控测试源中至少一个 fixture candidate 及其 evidence 被写入 safe-boundary checkpoint；
7. 杀死并重启主应用后，该 candidate 仍可见且 run 可继续或已得到正确终态；
8. UI 显示明确 outcome，support evidence 能关联同一 run/operation/attempt；
9. 退出或 cleanup 不改变已经提交的业务结果，也不影响用户非 owned tab。

### 4.2 用户业务运行成功

真实业务运行与发布 canary 的“有候选”要求不同。猎聘实际执行成功但零匹配是有效结果，必须表现为 `succeeded_empty`，不得因为“required source 为空”自动归为可靠性失败。

一次业务运行必须同时具备：

- durable acceptance：用户看到 accepted 时任务已持久化；
- source execution evidence：能够证明搜索已实际执行，而不是配置缺失或执行未发生；
- terminal outcome 或明确的 `needs_attention`；
- 与 outcome 相符的 durable candidate truth；
- 失败时的 typed failure 与用户下一步动作。

## 5. 运行状态与产品 outcome

控制平面的 lifecycle state 与产品 outcome 是两个维度，禁止继续把它们压缩为一个模糊 `success/failed` 字段。

### 5.1 控制状态

控制平面可继续使用现有 `queued`、`starting`、`running`、pause/resume/cancel 请求态和 `cancelled`、`completed`、`failed`。`needs_attention` 可以由 #324 映射为 durable pause/wait state，但必须满足：

- 非 terminal；
- 不占用失效的 executor lease；
- 保存恢复所需 checkpoint 和 action；
- 用户完成动作后以新 attempt 恢复，而不是重建一个无关联 run。

### 5.2 产品 outcome

| Outcome | 含义 | 候选可见性 | 默认重试 |
| --- | --- | --- | --- |
| `succeeded_with_results` | 所有 required source 在支持边界内执行完成且有结果 | 全部已提交 candidate truth | 不需要 |
| `succeeded_empty` | source 确实执行，零匹配是业务事实 | 空集合也是 durable 结果 | 不需要 |
| `degraded_with_results` | 至少一个 required path 未完成，但已提交结果仍可信可用 | 已提交结果必须保留并标明 coverage | 只重试缺失 operation |
| `needs_attention` | 可由用户动作解除的登录、风控、权限、账号/profile 选择等阻塞 | 已提交结果保留 | 用户动作后恢复 |
| `failed` | 运行无法在契约内完成，且不存在等待用户动作的安全恢复路径 | 已提交结果不得丢失，但不得伪装完整 | 根据 failure envelope 决定 |
| `cancelled` | 用户或系统明确取消 | 已提交 safe-boundary 结果按产品政策展示 | 不自动重试 |

`partial` 不是“随便返回已有列表”。只有已经在 safe boundary 原子提交的 candidate/evidence 才能进入 `degraded_with_results`。内存中、临时 DOM 中或旧 attempt 产生的结果不能计入。

## 6. 最低可靠性保证

### 6.1 Durable acceptance 和持续消费

- UI/API 只有在 run 与初始 command 已提交 SQLite 后才能返回 accepted。
- SQLite queue 是唯一任务真相；进程内线程、event 或 wake 只能降低延迟，不能决定任务是否存在。
- 应用启动时必须自动启动一个受监督的 continuous worker，并主动扫描 queued、可恢复和 lease-expired run。
- queue 暂时为空时 worker 可以等待，但不得永久退出并依赖下一次偶然 wake 才恢复。
- 同一 durable queue 只能有一个有效控制者；多进程场景必须通过 lease/fence 而不是“通常只有一个进程”保证。

### 6.2 Crash、重启和 checkpoint

- 主应用、worker、sidecar 或 Chrome 在任意注入点崩溃后，run 只能从命名的 safe boundary 恢复。
- checkpoint 必须与 compact candidate truth 在同一事务中提交；写入失败不得留下“UI 已看到但数据库没有”的候选。
- startup recovery 必须默认恢复可恢复 checkpoint，不能统一使用 `resume_recoverable=False` 把可恢复任务标成失败。
- corrupt/unsupported checkpoint 必须 typed fail，保留诊断引用，不得静默从头执行并重复外部副作用。
- cleanup failure 与业务结果隔离：业务结果一旦提交，关 tab、回收 lease、清理临时文件失败只能产生 cleanup warning，不能把成功改写为失败。

### 6.3 Attempt fencing、晚到结果和重复请求

- 每次执行必须有单调递增 attempt 和不可伪造的 fence token。
- 旧 attempt、过期 lease 或已 cancelled run 的 event、checkpoint、stage output、candidate 和 completion 写入必须被存储层拒绝。
- 晚到结果必须记录为被拒绝的诊断事件，不得覆盖新 attempt 的状态。
- 相同 idempotency key 且 request hash 相同，必须返回同一逻辑 operation/result；相同 key 但 hash 不同必须返回 conflict，不得猜测复用。
- Source Execution Port 的任何网络/IPC 重试必须复用 operation ID，而不是创建不可关联的新请求。

### 6.4 外部副作用已发生、本地未提交

v1 不承诺外部系统的 exactly-once side effect。它承诺：

- 本地 command/outbox 至少一次交付；
- 本地提交幂等且受 attempt/fence 保护；
- 对已发送但未确认的 browser/source operation，恢复逻辑先按 operation ID 与 sidecar journal reconcile；
- 能证明未执行时才可安全重发；
- 能证明已执行时只补交本地结果；
- 无法判断时进入 `needs_attention` 或 typed `failed`，保留 cause reference，**不得盲目重放**搜索、详情打开、消息发送或其他外部动作。

### 6.5 Sidecar 监督

- sidecar 必须有唯一 lifecycle owner、明确的启动/ready/drain/stop 状态和有界 timeout；
- 固定端口冲突、旧 daemon、错误 extension/bridge 或多个实例必须在 readiness 阶段被识别；
- 生产协议必须包含本次安装生成的本地认证凭据或等价安全绑定，不能只凭 `127.0.0.1` 和静态 header 信任请求；
- sidecar 重启不得丢失 operation journal；主应用重启不得凭内存推断 sidecar 状态；
- 退出时先停止接受新 operation，再 drain/持久化，然后释放 owned tab 和进程资源。

## 7. Source Execution Port 硬约束

#323 可以选择最简单的本地 transport，但其协议必须满足：

1. request/result/event 都是纯数据、显式版本化、可序列化的契约；
2. 不跨进程传递 Python callback、数据库连接、browser 对象、Pydantic 私有实现对象或任意 `object`；
3. 每个 request 包含 `contract_version`、`operation_id`、`run_id`、`source`、`request_hash`、`attempt_no`、`fence_token`、`deadline`、`idempotency_key`；
4. 每个 result 包含 operation identity、完成状态、typed failure、result/evidence references 和 sidecar build/protocol identity；
5. progress event 可丢失，但最终状态和安全 checkpoint 不得依赖 progress callback；
6. 协议必须支持查询 operation、取消、reconcile、drain 和 readiness；
7. 不兼容版本在执行前 fail closed，不能在运行中猜测字段或使用隐式 fallback；
8. transport 断连不等于 operation 失败，调用方必须先 reconcile 再决定恢复；
9. 所有 deadline 使用明确时基和单位，超时后旧执行者的写入仍由 fence 阻断。

## 8. Failure Envelope 与用户可见错误

任何 `failed` 或 `needs_attention` outcome 必须产生一个版本化 Failure Envelope，至少包含：

- `schema_version`；
- `failure_id`、`correlation_id`、`run_id`、`operation_id`、`attempt_no`；
- `domain`（install/runtime/storage/browser/source/network/provider/policy/user_action/cleanup）；
- `reason_code` 与保留原始原因的 `cause_ref`；
- 发生组件及 app/sidecar/bridge/extension build identity；
- `retryable`、`safe_to_retry`、`user_action`、`support_action`；
- 当前 outcome、source coverage、last safe boundary；
- `occurred_at`、redaction policy/version。

UI 可以把内部细节翻译为简洁中文，但不得把不同故障全部压平成“浏览器不可用”“后端不可用”或“请重试”。内部 reason 必须被保留，并映射到具体用户动作，例如：

- 打开 Chrome 完成登录；
- 选择正确 profile/账号；
- 修复 extension/bridge 版本不匹配；
- 检查企业代理/CA 策略；
- 释放产品自己的端口/daemon 冲突；
- 导出 support bundle 联系支持。

每个 release gate 都要求未知/未分类失败为零。新增未知 failure 必须先进入 taxonomy，再允许发布。

## 9. 本地诊断、隐私与 support bundle

### 9.1 `doctor` 最低检查

生产 `doctor` 必须在不运行真实猎聘任务的情况下检查并报告：

- OS build、架构、安装来源、active/previous slot；
- app/Python/Node/WTSCLI/bridge/extension/SQLite build 和协议版本；
- capability receipt、manifest、哈希/签名状态；
- runtime DB path、schema、integrity、migration/backup 状态和可用磁盘；
- worker/sidecar lifecycle owner、端口、认证、readiness；
- Chrome Stable、profile、extension pairing、账号主体是否可判定；
- 代理/CA/企业策略的可判定事实；
- 最近失败的 failure ID 和用户下一步动作。

### 9.2 Support bundle

#322 负责冻结具体 schema 和故障注入基线；本契约冻结其产品要求：

- 默认 local-only，不自动上传；
- 必须由用户主动导出，并在导出前提供内容类别预览；
- 使用 allowlist 投影和版本化 redaction policy；
- 默认不得包含 cookie、token、密码、JWT、完整简历/JD、完整 prompt/response、完整 DOM、网页截图、浏览历史或用户其他 tab 内容；
- 稳定包含 manifest/receipt、组件版本、sanitized state transition、failure envelope、operation/attempt 时间线、DB integrity 摘要和测试所需的非敏感环境事实；
- bundle 必须有唯一 ID、生成时间、schema/version、完整性哈希和明确保留/删除说明；
- debug/full-local 模式必须用户显式开启，并与默认 support bundle 分离。

缺少五位历史失败用户的日志不是本契约 blocker。因为当前没有遥测，无法回溯的历史不得被伪造为证据；v1 要做的是保证未来每次“跑不了”都能在本地得到可比较、隐私安全的事实。

## 10. 升级、迁移与回滚

- 完整产品使用 A/B application slots 或等价的原子 active pointer；不得边运行边覆盖 active slot。
- 每次 schema migration 前必须完成数据库 backup，并记录 source/target schema 与 app build。
- 新版本首次成功启动、完成 migration、自检和最低 smoke test 后才能提交 active slot。
- 旧版本不得打开比自身支持范围更新的数据库 schema；回滚若需要恢复 backup，必须先保护升级后产生的新用户数据并给出明确选择。
- extension 可能由浏览器商店或企业策略独立升级，不能假设随 desktop 回滚。desktop/sidecar 必须至少兼容声明窗口内的当前和上一 extension 协议，窗口外 fail closed。
- crash-mid-install、crash-mid-migration、首次启动失败和 sidecar 升级不一致都必须进入 release matrix。
- 回滚不得要求用户删除整个应用目录或 runtime DB。

## 11. 度量口径与 SLO 决议

当前没有足够的隐私安全现场基线，因此本契约不制造“成功率 99.x%”之类数字。v1 先冻结测量分母和确定性发布门禁。

### 11.1 Eligible attempt

进入现场可靠性分母的 attempt 必须：

- 来自 capability receipt 内的支持组合；
- 完成安装完整性和 runtime preflight；
- 用户账号已经 ready，或由产品明确记录为 `needs_attention`；
- run 已 durable accepted。

用户在 accepted 前取消、明确使用 unsupported OS/browser/policy 的尝试不进入“执行失败率”分母，但必须单独计数。`needs_attention`、`succeeded_empty`、`degraded_with_results`、`failed` 必须分别统计，禁止把它们合并为单一 success/failure。

重启产生的新 attempt 仍属于同一个 logical run；可靠性统计必须同时提供 logical-run 分母和 attempt 分母，避免通过自动重试美化结果。

### 11.2 v1 确定性门禁

在形成现场基线前，发布判断使用：

- mandatory matrix 的每一个 case 必须 100% 通过；
- exact-artifact clean-machine 安装/升级/回滚 case 必须全部通过；
- stale attempt 非法写入、durable accepted job 丢失、用户 tab 被误控制、默认 support bundle 泄露敏感内容的数量必须为零；
- 未知/未分类 failure 必须为零。

这些是有限测试集合的 deterministic gate，不是对真实世界成功率的宣传。

至少收集一个正式发布周期的隐私安全基线后，产品与工程再根据 eligible-attempt 分布、failure taxonomy、平台样本和置信区间设置 field SLO。没有 field SLO 数字不阻塞 v1；没有上述 deterministic evidence 阻塞 v1。

## 12. 验收矩阵

### 12.1 PR gate

每个改变 runtime、source、browser、storage、installer 或 diagnostics contract 的 PR 必须执行：

| 类别 | 必须证明 |
| --- | --- |
| State/FSM | 所有合法/非法 transition、terminal immutability、needs-attention 映射 |
| Durable queue | accepted 前提交；启动自动消费；空闲后仍能消费新任务 |
| Attempt/fence | stale event/checkpoint/result/completion 全部被拒绝 |
| Idempotency | same-key/same-hash 复用；same-key/different-hash conflict |
| Checkpoint | candidate truth 与 checkpoint 原子提交；corrupt schema typed fail |
| Source port | schema round-trip、版本拒绝、deadline、disconnect + reconcile |
| Failure contract | 每个 failure domain 产生稳定 envelope 和用户动作 |
| Privacy | 默认 bundle allowlist、secret/JD/resume/DOM/screenshot 负向测试 |
| Packaging metadata | manifest/receipt/schema 结构验证，版本组合可计算 |

纯文档 PR 至少运行 diff whitespace/link/contract-structure 检查。代码 PR 不得因为路径过滤而跳过对应 gate。

### 12.2 Nightly gate

Nightly 在各目标原生 OS 上从构建候选运行，不使用开发机缓存：

- queued/starting/running/safe-boundary 每个注入点 kill 主应用和 worker；
- kill/restart sidecar、Chrome、bridge transport，验证 reconcile；
- 在外部操作后、本地 commit 前断电/进程终止；
- 制造 stale attempt、晚到结果、重复 command、同 key 异 hash；
- 制造固定端口占用、旧 daemon、多个实例和错误 lifecycle owner；
- 制造 extension 缺失、错误 build/protocol、未 pairing、profile/account mismatch；
- 覆盖 login required、risk challenge、用户迟到完成动作和取消；
- 覆盖磁盘不足、DB locked、corrupt checkpoint、migration rollback；
- 覆盖 direct network、已支持 proxy，以及 unsupported CA/enterprise policy 的 typed failure；
- 验证退出/cleanup 不动 user tab、不改写已提交 outcome；
- 对生成的 support bundle 做敏感内容扫描。

夜间自动化使用 contract site/fixture，不依赖真实个人账号。真实猎聘测试账号的 smoke canary 另行人工执行并只记录 sanitized outcome。

### 12.3 Release gate

每个平台、每个发布候选都必须使用最终签名的 exact artifact 在 clean machine 完成：

1. 全新安装；
2. 从当前生产版本升级；
3. 安装中断和升级中断恢复；
4. 新版本首次启动失败回滚；
5. 数据 migration 与旧版本 schema 拒绝/安全恢复；
6. Chrome Stable + 生产分发 extension 的 pairing；
7. Domi/GUI 启动和认证交接，不使用 shell 环境变量；
8. 发布 canary 的首个 candidate 持久化、主应用重启后仍可见；
9. `succeeded_empty`、partial/degraded、needs-attention 和 typed failure 的 UI 验证；
10. doctor、support bundle、签名、manifest、receipt 完整性；
11. 卸载/回滚不删除用户数据，不影响非 owned tab；
12. 所有 release blocker 均有可追溯证据链接。

任一目标平台没有完整 artifact 或没有 clean-machine evidence，则只能发布已经通过的平台；不得用另一个平台或开发机结果替代。若产品承诺三平台同时发布，任一平台失败即阻塞整个 release train。

## 13. Code truth evidence

以下证据来自本契约起草时的 code/test/build/release truth；文档与代码冲突时，应修复代码或更新本契约，不能用旧文档证明已实现。

| 事实 | 当前证据 | 契约影响 |
| --- | --- | --- |
| Durable control 基础已存在 | `src/seektalent_runtime_control/models.py`、`fsm.py` 定义 run/event/lease/checkpoint/command/candidate truth | 复用并加固，不引入本地 Temporal |
| Stale attempt 已可被存储层 fencing | `tests/test_control_plane_crash_recovery.py` 覆盖旧 attempt 写入拒绝 | 扩展到 source IPC/result/side effect reconcile |
| Checkpoint 可原子同步 candidate truth | `RuntimeControlStore.write_checkpoint()` 使用事务；`tests/test_runtime_control_candidate_truth.py` 验证 compact truth | candidate safe boundary 是 partial success 唯一依据 |
| 恢复核心已存在 | `src/seektalent_runtime_control/recovery.py` 可过期 lease、恢复 checkpoint、拒绝 corrupt checkpoint | 生产 runner 必须实际启用 recoverable resume |
| 当前 runner 不是持续 durable worker | `src/seektalent_workbench_v2/runtime_runner.py` spawn-on-wake、queue 空时退出，并以 `resume_recoverable=False` 恢复 | #324 必须 hard cut；wake 只能是提示 |
| App lifespan 未启动该 runner | `src/seektalent_ui/server.py` 只启动 workflow/extraction outbox runner；相关测试冻结当前行为 | 已接受任务可因进程生命周期而搁置，阻塞发布 |
| Source seam 仍含进程内对象 | `src/seektalent/source_contracts/runtime_lanes.py` request 含 callback、domain object、宽泛 `object` | #323 必须定义纯数据 IPC contract |
| Browser fencing 词汇已有雏形 | `src/seektalent/opencli_browser/contracts.py` 定义 control scope、owned tab、host facts | 保留语义并扩展到 sidecar lifecycle/operation |
| 当前 daemon 身份不足 | `daemon_transport.py` 使用固定 `127.0.0.1:19825`，请求仅静态 `X-OpenCLI` header | 需要唯一 owner、本地认证、ready/drain 和冲突诊断 |
| 内部 reason 丰富但公共错误被压平 | `opencli_browser/reason_codes.py` 有细分原因；`sources/liepin/reason_codes.py` 和 `workbench_response.py` 将多种原因映射为通用不可用 | 需要 versioned Failure Envelope 和保真 cause |
| 有效空结果当前可能被判失败 | `production_contract.py` 将 required source 的 `empty` 推导为 overall failed | 必须引入 `succeeded_empty` 语义 |
| 当前生产安装只装 Python 包 | Domi `install-seektalent-domi.sh/.ps1` 通过 pip 安装包，不安装完整 WTSCLI/bridge/extension | wheel 不是生产发布物 |
| 现有离线包只覆盖 macOS Intel | `build_offline_macos_intel.py` 与手工 workflow；README 要求 load unpacked extension/JWT | 只能内部 staging，不能代表三平台 GA |
| 当前版本离线约束不完整 | builder 要求版本对应 constraints，但仓库缺少 `constraints-0.7.49-macos-intel.txt` | 0.7.49 无法从现有流程产出可重复 Intel bundle |
| 发布流水线只自动发布 wheel | `publish-pypi.yml` 在 GitHub release 构建/发布 Python dist；offline/governance 多为手工 workflow | #326 必须建立完整 artifact release train |
| 更新与整包回滚未闭环 | CLI update 只打印 pip/pipx；offline install 无全产品 A/B slot | 必须实现原子切换、迁移备份和 whole-product rollback |
| `doctor` 不覆盖 execution plane | 当前检查 prompt/schema/output/provider/local root，不验证 OS/receipt/sidecar/Chrome/profile/extension | #322/#326 必须补齐生产诊断 |
| 隐私基线可复用 | `artifact_policy.py` 的 prod/dev/debug 模式和敏感字段 redaction | 扩为 allowlisted support bundle，不自动上传 |
| SQLite 使用 rollback journal | store 配置 busy timeout，未显式启用 WAL | 维持 rollback journal；WAL 需固定 SQLite build + 跨平台 crash matrix 后另决 |

## 14. Decision table

| 决策 | v1 决议 | 为什么 | 变更条件 |
| --- | --- | --- | --- |
| Workflow runtime | 加固 SQLite control plane，不部署通用 workflow server | 本地桌面、交付压力、现有核心可复用 | replacement-only spike 证明显著更低复杂度与更高可靠性 |
| Execution topology | 主应用 + 唯一受监督 source/browser sidecar | 隔离不可靠 browser 边界，同时保持本地部署简单 | 不得退回线程内 callback/object 边界 |
| Product artifact | 完整、签名、可验证的平台 bundle | wheel 无法代表真实用户链路 | 无 |
| Browser | Chrome Stable；现有用户 profile 的确定性绑定 | 最贴近当前真实登录路径，避免专用 profile 阻塞交付 | 专用 profile 完成真实账号和迁移专项后可替换 |
| Extension | 生产分发/管理的签名 extension | load unpacked/developer mode 不可作为用户方案 | 内部 staging 可例外但不得标 GA |
| Queue | SQLite durable truth + continuous worker | 防止 wake/thread 时序丢任务 | 无 |
| Delivery semantics | 外部至少一次 + 本地幂等/fenced commit + reconcile | 外部副作用无法承诺 exactly once | 无 |
| Empty result | `succeeded_empty` | 零匹配是业务事实，不是系统失败 | 无 |
| Human action | durable `needs_attention` 后恢复 | 登录/风控无法可靠自动化 | 无 |
| Diagnostics | local-only、主动导出、allowlisted bundle | 在可支持性与用户隐私之间建立硬边界 | 自动遥测需独立产品/隐私决议 |
| SQLite journal | v1 保持 rollback journal | 当前未有 pinned-build WAL crash evidence | 完成跨平台 WAL matrix 后 ADR 决定 |
| Field SLO | 暂不设虚构百分比；先冻结分母与 deterministic gate | 当前无现场基线 | 一个正式周期的可比较数据后设定 |

## 15. Unresolved decisions

以下项目不是 #321 blocker，但必须由下游任务给出可执行结果：

| Owner | 尚待决定 | 已冻结的边界 |
| --- | --- | --- |
| #322 | support bundle 的最终 JSON schema、CLI/UI 导出入口、故障注入 harness | 必须 local-only、allowlist、可预览、默认无敏感正文 |
| #323 | IPC transport、sidecar process supervisor 的具体实现、专用 profile spike 是否继续 | 必须满足第 7 节；v1 默认不以专用 profile 为前置条件 |
| #324 | `needs_attention` 在现有 FSM/DB 的精确映射、safe boundary 列表、outbox/reconcile schema | 不得丢 accepted job；恢复必须保留 logical run 和 candidate truth |
| #326 | 各平台安装器技术、签名证书、exact OS/Chrome version window、CI runner/clean-machine provider | 必须产出完整 signed artifact、receipt、A/B rollback 和三平台 evidence |
| 产品 + 工程（有基线后） | 现场 field SLO 初始阈值 | 必须使用第 11 节分母，不能用重试掩盖 logical-run failure |

若下游发现上述实现选择无法满足硬约束，应回到 #321 修改契约并记录证据；不得在实现中静默弱化发布标准。

## 16. 对 #323 / #324 / #326 的影响

### #323：切分 Source Execution Port 与受监督 sidecar

#323 现在可以并行设计，但合入前必须证明：

- 纯数据、版本化 IPC contract；
- operation identity、hash、deadline、idempotency、attempt/fence；
- readiness/query/cancel/reconcile/drain；
- 唯一 lifecycle owner 和本地认证；
- transport 断连不会直接误判 operation failure；
- owned-tab 边界和 user-tab 不可侵犯。

任何保留 callback、任意 object、进程内 browser/session 对象的方案都不满足 v1。

### #324：加固 durable runtime 和恢复语义

#324 必须：

- 把当前 spawn-on-wake runner hard cut 为 startup-supervised continuous worker；
- 默认执行 recoverable checkpoint 恢复；
- 把 candidate truth/safe boundary、late result rejection、outbox reconcile 纳入同一 durable 语义；
- 明确定义 `succeeded_empty`、`degraded_with_results`、`needs_attention`；
- 保证 cleanup failure 不改写业务 outcome。

### #326：安装、发布与可支持性

#326 必须把 release unit 从 PyPI wheel 改为完整平台 bundle，并建立：

- Windows 11 x64、macOS arm64、macOS x86_64 的最终 artifact；
- 签名、公证、manifest、capability receipt；
- 生产 extension 分发和兼容窗口；
- A/B upgrade/rollback 与 DB migration backup；
- exact-artifact clean-machine matrix；
- 扩展后的 doctor 与 #322 support bundle；
- PR/nightly/release evidence 聚合和 release blocker 展示。

在 #326 完成前，可以继续内部开发和 staging，但不得把单独 wheel、Intel 手工离线包或开发机成功标记为 External Execution Plane v1 生产 ready。

## 17. 非目标与延后项

v1 明确不做：

- 在用户电脑部署 Temporal/Restate/Prefect/Celery；
- 云端 workflow service、多用户调度或远程控制平面；
- 自动上传遥测、原始 DB/log/prompt/resume/DOM；
- 自动登录猎聘、存储密码或绕过验证码/风控；
- Linux、Windows ARM、Edge/Chrome 非 Stable 的生产支持；
- 任意企业代理/CA 的无条件兼容承诺；
- 外部副作用 exactly-once 承诺；
- 为了重构而全仓清理死代码、重排目录或拆分所有大文件；
- 把专用 Chrome profile、Native Messaging 或 DBOS 作为 v1 必选技术。

技术债治理采用 vertical hard cut：只重构当前可靠性切片触达的边界，删除被新路径替代的旧实现，并用 contract test 阻止双轨长期共存。大规模仓库整理不得与 runtime、browser、storage、diagnostics、packaging 任一关键切片绑在同一 PR。

## 18. 发布裁决模板

每次 release candidate 必须产生一份机器可追溯的裁决：

```text
Release candidate: <version/build>
Artifact manifest: <id/hash>
Supported receipts: <platform + OS/Chrome/runtime window>
PR gate: PASS|FAIL + evidence
Nightly gate: PASS|FAIL + evidence
Clean-machine release matrix: PASS|FAIL + evidence
Unknown failures: 0|required blocker
Privacy violations: 0|required blocker
Open release blockers: <issue ids>
Verdict: INTERNAL_ONLY|RELEASEABLE_ON_<platforms>|BLOCKED
Reason: <typed, evidence-linked reasons>
```

只有 `PR gate = PASS`、`Nightly gate = PASS`、所有承诺平台的 `Clean-machine release matrix = PASS`、unknown/privacy blocker 为零，且没有开放的 release blocker 时，版本才可裁决为 production releaseable。
