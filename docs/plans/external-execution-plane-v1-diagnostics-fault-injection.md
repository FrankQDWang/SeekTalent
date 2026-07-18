# External Execution Plane v1 可诊断性与故障注入基线

状态：Wayfinder #322 范围契约；待 #324 交叉复核

Tracks: #322

主线输入：

- `e522f51c`：External Execution Plane v1 可靠性契约；
- `f28bc529`：External Execution Plane v1 runtime topology；
- GitHub issue #322 当前 body；
- 当前 `code/schema/tests/build/release scripts`。文档与代码冲突时，以代码为准。

## 1. 结论与边界

本任务不引入 Temporal、远程 telemetry backend 或第二套业务状态机。External Execution Plane v1 需要的是一条本地、持久、可关联、可脱敏导出的证据链：每个组件继续持有自己的运行职责，main-owned SQLite 继续持有业务真相；canonical diagnostic journal 只记录发生了什么、在哪个边界发生、由哪个已验证组件观察到，以及哪些事实仍未知。

本文件唯一拥有：

- canonical structured event identity、顺序和因果引用；
- `FailureEnvelope` 的事实字段与错误分类；
- `MachineCapabilityReceipt`、`StartupReceipt`、`OperationEvidence`；
- support bundle v1 的 allowlist、redaction、保留、大小和导出契约；
- clean-machine、fault-injection matrix 和 gate 分层；
- 未知用户机器故障从本地证据到 release-blocking regression 的闭环。

本文件明确不拥有：

- `RetryPosture`、`ProductOutcome` 和重试许可；这些只由 #324 定义。`FailureEnvelope` 不包含 `retryable`、`safe_to_retry` 或任何等价布尔值；
- Source Execution Port wire DTO；#325 只能传输或引用本文件的 canonical 对象；
- Release Manifest、签名和产品包内容；这些只由 #326 定义。本文件只定义 `release_manifest_ref` 的引用位置；
- 自动上传、支持后台、原始日志打包、历史五位用户不存在的日志；
- 大规模运行时代码重构。

### 1.1 必须守住的不变量

1. 业务恢复、source disposition、最终产品结果不能从诊断 journal 推导或回写；诊断丢失不能改变业务结果。
2. wall-clock 只用于展示和时间窗，不能替代 durable sequence、attempt fence 或 causal link。
3. 每一个导出字段都必须进入 schema allowlist；“先收集全部再靠正则删敏感字段”不合格。
4. raw DB、WAL/SHM、cookies、token、DOM、截图、完整 URL、JD/简历/查询正文和用户路径默认不得进入 bundle。
5. 不确定事实必须写成 `unknown`，不得用当前进程在线、无异常或最后一条日志推断成功。
6. stale attempt、stale profile generation 和 stale browser control fence 产生的写入不得成为可信 evidence。

## 2. 当前代码事实、缺口与决策

以下是本任务检查主线代码后的结果，不是目标架构假设。

| 区域 | verified fact | gap | v1 decision |
|---|---|---|---|
| runtime event | `seektalent_runtime_control.models.RuntimeControlEventInput` 有 `event_id/runtime_run_id/event_type/stage/round/source/status/payload`；store 为每个 run 分配 `event_seq`，支持 event/idempotency uniqueness | event 未持久化 operation/attempt/component/build/cause identity；run 内 sequence 不能关联浏览器、worker 和 provider journals | 保留现有事件作为 producer input；投影到本文件的 canonical event，不把现有 public event 当 canonical journal |
| executor authority | executor lease 持有 `attempt_no`；`append_executor_event` 在写前校验 active executor/attempt，能拒绝 stale write | attempt 只参与 guard，没有进入已存事件证据 | canonical event 和 Operation Evidence 固定记录 `attempt_no` 与 authority reference |
| runtime persistence | runtime-control SQLite 有 schema migration、备份、`integrity_check`、事务 checkpoint/candidate truth；event payload 上限为 16 KiB | 现有 retention 面向 runtime/debug，不是跨组件 support evidence budget | 复用 16 KiB 单事件上限、migration/integrity 原语；新增独立 canonical projection 和明确 budget |
| event privacy | `seektalent_runtime_control.events` 会按敏感 key/summary 词过滤；public event 是有界用户投影 | key-fragment heuristic 不是版本化 allowlist；public event 缺少原因链和环境证据 | producer 先做安全 projection，canonical sink 再按 event-name allowlist 验证；双层 fail-closed |
| Liepin journal | `liepin_events` 按 subject 分配 sequence；`append_event` 拒绝明显不安全 payload；账号只保存 HMAC hash/加密状态摘要 | 与 runtime journal 无共同 operation/attempt/cause identity；`redaction_state` 语义不一致 | 保留安全拒绝和 opaque account ref；映射到统一 event/receipt taxonomy，不直接导出表 |
| worker | external worker health 只返回 `status` 和可选 `workerVersion`；请求已有 `traceId`，OpenCLI path 将其当 `source_run_id` | health 无 implementation/build/protocol/capabilities；`traceId` 语义过载且没有 durable causal chain | receipt 显式记录 component identity/capability；旧 `traceId` 只作 legacy correlation input，不能单独证明因果 |
| browser bridge | 已有严格 implementation/build/protocol/capability 校验、typed reason codes、command ID、deadline、control scope/fence | daemon transport 与 lifecycle registry 没有 durable canonical evidence；registry 明确 fail-open，异常只留 exception type | 保留 bridge identity、reason code、scope/fence；registry 仅作为 producer，不能成为 authority 或成功证明 |
| Chrome lifecycle | 当前可以探测 extension/daemon/profile/tab 状态 | MV3 worker 会终止和重启；“现在在线”不证明之前 command 已持久接受或完成 | Startup Receipt 区分 browser/profile startup、extension install/update、worker generation 和 command boundary |
| operator health | `operator_health.py` 已报告磁盘、DB/WAL/SHM 大小、schema version、integrity reason；`operator-health` maintenance 命令有测试 | `seektalent doctor` 未聚合 DB health、Chrome/extension/profile/runtime component identity | 复用 operator health 为 Machine Capability input；#322 后续实现把 receipts 暴露给 doctor/bundle |
| diagnostics artifacts | `runtime_diagnostics.py`、replay/eval artifacts 包含大量业务调试数据；artifact lifecycle 已有 `support_bundle_only` 元数据 | 这些 artifacts 不是 privacy-safe support bundle，不能整体复用 | 只复用 hash/size/lifecycle primitives；默认 bundle 禁止导出业务 artifacts |
| local storage | local lifecycle 已识别 `support-bundles` storage class，默认 7 天清理；prod 总本地预算 2 GB | 没有 bundle schema、生成器、preview 或 per-bundle size cap | bundle v1 沿用 7 天默认 TTL，并增加本文件的独立大小上限 |
| packaging | `build_offline_macos_intel.py` 和手动 workflow 能生成/校验 macOS x86_64 离线包及 browser bridge hashes | 没有 Win11 x64/macOS arm64 对等产品 artifact；现有 bundle manifest 不是 #326 Release Manifest | #322 只把三平台 artifact 当 release gate 输入；不扩展 manifest schema |
| CI | `python-quality` 在 PR 自动运行；governance、workbench-contract、macOS Intel build 都是 `workflow_dispatch` | 无自动 fault-injection、clean-machine、三平台或 real Liepin release gate | 采用 PR/nightly/release/manual 四层 gate；实现归相应后续任务，不假装当前已存在 |

### 2.1 Preserve / migrate / delete

- **Preserve**：runtime-control 事务和 stale-attempt guard、SQLite migration/integrity check、OpenCLI typed reason codes/bridge identity/fence、Liepin unsafe-payload rejection、opaque account/profile refs、operator-health DB 摘要、artifact hash/size/lifecycle。
- **Migrate**：各自事件/health/result 映射为 canonical event、receipt 和 Operation Evidence；映射层是 allowlist projection，不是复制 raw payload。
- **Delete from the diagnostic path**：以自由文本异常作为机器分类主键、导出 raw SQLite/log/DOM、用当前在线状态推断历史成功、用 fake fixture 替代 real Liepin canary、由 Failure Envelope 决定重试或产品结果。

## 3. Canonical identity 与因果链

### 3.1 identity 层级

| 字段 | 语义 | 生成与稳定性 |
|---|---|---|
| `correlation_id` | 一次 product run/user intent 下所有 source operations 的关联 ID | main 在 durable run identity 建立时生成的随机 opaque ID；跨 source/attempt 保持不变 |
| `diagnostic_trace_id` | 一次 operation attempt 的跨组件 trace ID | 128-bit 随机、32 位小写 hex、非全零；不能由用户、机器或业务内容派生；同一 attempt 跨进程重启保持不变 |
| `operation_id` | main-owned 逻辑操作 ID | 由 main 在 durable acceptance 前分配；若对应 runtime run，保留 `runtime_run_id` 作为 domain reference |
| `attempt_no` | 同一 operation 的执行 attempt | 来自 main authority；从 1 单调递增；不能由 sidecar 自增后自认有效 |
| `event_id` | 单个 canonical event ID | producer 生成的随机 opaque ID；重投保持不变 |
| `journal_seq` | canonical journal 的观察顺序 | sink 在 durable append 时分配、全 journal 单调递增；不代表跨进程发生顺序 |
| `component_instance_id` | 某组件某次进程/worker generation | 每次进程启动或 extension service-worker generation 新建；重启必须改变 |
| `component_event_seq` | 单 instance 内 producer 顺序 | 从 1 单调递增；发现回退或重复且 payload 不同即记录 protocol failure |
| `span_id` | 一个有界 component action | 64-bit 随机、16 位小写 hex、非全零；只用于本地 causal tree |
| `parent_span_id` | 直接调用方 action | 可空；若存在，必须与同一 trace 关联 |
| `caused_by_event_id` | 直接触发当前事实的已知 event | 可空；不能跨 trace，不能成环；未知原因写空并给出 `cause_certainty=unknown` |

`diagnostic_trace_id/span_id` 采用 W3C 字段形状，v1 不要求传播 HTTP `traceparent`，也不引入 OpenTelemetry SDK。任何外部传入 identity 都是不可信输入；格式不合法、全零或与 authority 不一致时，生成本地新 trace，并记录 `external_trace_rejected`。

### 3.2 顺序规则

1. `occurred_at` 是 producer wall time；`observed_at` 是 canonical sink 首次观察时间；两者均为 UTC RFC 3339，允许偏差，不用于 fencing。
2. 同一 component instance 以内用 `component_event_seq` 排序；sink 的接收顺序用 `journal_seq` 排序；跨组件先后只由 `parent_span_id/caused_by_event_id` 和 durable boundary fact 建立。
3. producer 重投相同 `event_id` 和相同 canonical hash 是幂等；相同 ID 不同内容产生 `diagnostic_event_id_conflict`，原记录不可覆盖。
4. 父 event 可能因 crash 未到达；sink 保留 orphan event，并在 Operation Evidence 中报告 `missing_cause_refs`，不能编造父节点。
5. authority 校验失败的事件进入单独的 rejected counter，只保存安全 identity/reason，不进入可信 operation timeline。
6. terminal evidence revision 生成后才到达的合法事件标记 `arrival_class=late`，保留原 `occurred_at` 并生成新的 Operation Evidence revision；重投标记 `replayed`。旧 attempt 或旧 fence 的 late write 是 `stale`，只能进入 rejected counter，不能借“迟到”绕过 authority。

## 4. Canonical Structured Event v1

schema ID：`seektalent.diagnostic-event/v1`。

### 4.1 固定字段

| 字段 | 类型/约束 | 说明 |
|---|---|---|
| `schema_version` | 固定 string | `seektalent.diagnostic-event/v1` |
| `event_id` | opaque string，1..96 | 幂等 identity |
| `journal_seq` | positive int | sink 分配 |
| `correlation_id` | nullable opaque string | operation events 必填；machine capability event 可空 |
| `diagnostic_trace_id` | 32 lowercase hex | operation attempt 或 startup attempt trace |
| `span_id` | 16 lowercase hex | local action identity |
| `parent_span_id` | nullable 16 lowercase hex | causal parent |
| `caused_by_event_id` | nullable opaque string | 直接原因引用 |
| `operation_id` | nullable opaque string | machine/startup events 可空 |
| `runtime_run_id` | nullable opaque string | 仅在已有 runtime run 时填写 |
| `attempt_no` | nullable positive int | operation event 必填；machine event 可空 |
| `component` | bounded enum | `main/controller/sidecar/worker/wtscli/extension/chrome/provider/llm/sqlite/installer/exporter` |
| `component_instance_id` | opaque string | 进程或 worker generation |
| `component_event_seq` | positive int | producer-local sequence |
| `release_manifest_ref` | nullable opaque/hash ref | 只引用 #326 对象 |
| `component_build_ref` | bounded string/hash ref | implementation/build/version identity |
| `event_name` | versioned dotted token | 稳定结构类别，如 `component.startup.completed` |
| `phase` | enum | `capability/startup/accept/dispatch/execute/observe/commit/cleanup/shutdown/export` |
| `severity` | enum | `debug/info/warn/error/fatal`；严重度不决定产品结果 |
| `status` | enum | `started/completed/partial/rejected/failed/unknown` |
| `arrival_class` | enum | `on_time/late/replayed`；stale/rejected 不进入可信 timeline |
| `reason_code` | nullable snake-case token | 稳定可聚类分类；错误/拒绝/unknown 时必填 |
| `occurred_at` | UTC timestamp | producer 发生时间 |
| `observed_at` | UTC timestamp | sink 首次观察时间 |
| `authority_refs` | bounded object | 只含 fence/profile generation/scope 的安全引用或 hash |
| `attributes` | event-name-specific object | 只接受注册 schema 中的 allowlist fields |
| `redaction` | object | `policy_version/projection/result/redacted_field_count` |

每条序列化 JSON 不得超过 16 KiB。`attributes` 禁止任意嵌套 map；最大深度 3、最大 64 keys、单 string 最大 256 chars、数组最大 32 items。超过上限时 producer 必须输出 `diagnostic_projection_oversize` 的安全替代事件；禁止截断后假装原事件完整。

### 4.2 event registry

每个 `event_name` 必须注册：owner component、允许的 phase/status、attributes schema、允许的 reason-code family、是否要求 operation/attempt、允许的 authority refs 和 redaction tests。未注册 event 默认拒绝。v1 至少覆盖：

- `machine.capability.evaluated`；
- `component.startup.started/completed/failed`；
- `component.readiness.observed`；
- `operation.accepted`、`operation.dispatch.started/completed`；
- `operation.side_effect.observed`；
- `operation.result.persisted`、`operation.main_commit.completed`；
- `operation.cleanup.completed/failed`；
- `component.process.exited`、`component.protocol.rejected`；
- `authority.write.rejected`；
- `storage.transaction.failed`、`storage.integrity.observed`；
- `support_bundle.export.started/completed/failed`。

自由文本 message 不是 canonical 字段。面向用户的本地化说明由 `reason_code + safe attributes` 渲染；原始 exception 只允许投影为 `exception_type`、官方数值错误码和注册过的安全 token。

## 5. Failure Envelope v1

schema ID：`seektalent.failure-envelope/v1`。它是事实快照，不是异常对象、控制命令或业务结果。

| 字段 | 约束 |
|---|---|
| `schema_version/failure_id` | 固定 schema + opaque ID |
| `correlation_id/diagnostic_trace_id/operation_id/runtime_run_id/attempt_no` | 关联当前失败的 product run、逻辑 operation 与 attempt；未知项明确为 null |
| `first_failure_event_id/last_observed_event_id` | 锚定 canonical journal；至少 first 必填 |
| `component/component_instance_id/component_build_ref` | 观察或报告失败的组件 identity |
| `phase` | 使用 canonical phase |
| `failure_domain` | `capability/startup/process/protocol/authority/browser/provider/network/storage/model/cleanup/packaging/unknown` |
| `reason_code` | 稳定 token；不得直接使用任意异常文本 |
| `cause` | `cause_event_id`、`cause_failure_id`、`cause_code`、`cause_certainty`；certainty 为 `observed/derived/unknown` |
| `boundary_facts` | `acceptance/dispatch/side_effect/result_persistence/main_commit/cleanup` 各自为 `not_started/not_observed/observed/unknown`，适用时填写 durable record ref |
| `last_safe_boundary` | 已有 checkpoint/safe-boundary token 或 `none/unknown`；必须来自 durable fact |
| `authority_refs` | attempt/profile/browser fence 的安全引用；不得包含 control key/token 原文 |
| `source_coverage` | source ID、started/completed/partial/unknown、safe counts；不得含候选人内容 |
| `product_outcome_ref` | 可选；只引用 #324 产生的结果对象，不在 envelope 内重定义 |
| `user_action_code/support_action_code` | 可选稳定 token；只描述可执行动作，不授予重试许可 |
| `occurred_at/observed_at/redaction` | 与 event 相同语义 |

### 5.1 分类与 cause 规则

- 原始 SQLite/OS/HTTP/Chrome code 保存在注册过的 `cause_code`，再映射稳定 `reason_code`。例如 `SQLITE_FULL`、`SQLITE_CORRUPT`、`SQLITE_READONLY` 不能都压成 `storage_failed`。
- 现有 OpenCLI reason codes 是 migration input；v1 不为同一事实创造第二个同义 code。跨组件映射表必须有 exhaustive test。
- `cause_certainty=derived` 只能来自明确规则，例如“process exit 后 pipe EOF”；规则 ID 进入安全 attributes。没有直接证据时必须是 `unknown`。
- 多个并发失败用多个 envelope，通过同一 trace 和 cause refs 形成 DAG；禁止覆盖“最后一个异常”。
- #324 可读取 boundary facts 决定 `RetryPosture/ProductOutcome`，但 #322 的 producer、sink、bundle exporter 都不得作该决定。

## 6. Receipts 与 Operation Evidence

所有 receipt/evidence 都是不可变 JSON 对象，写入时带 schema version、canonical hash、created/observed time、release reference 和 redaction policy version。更新意味着产生新 revision，不能原地改写历史事实。

| 对象 | 唯一 owner/producer | canonical persistence | 只引用/消费 |
|---|---|---|---|
| Release Manifest | #326 release pipeline | #326 定义的签名 artifact 与 installed copy | capability/startup receipts 只保存 `release_manifest_ref` |
| Machine Capability Receipt | main preflight/doctor 汇总 installer、OS、browser、DB probes | local diagnostic store | Startup Receipt、doctor、bundle、#326 release evidence |
| Startup Receipt | 各 component 产生事实，canonical sink 校验 | local diagnostic store | Operation Evidence、doctor、bundle |
| Operation Evidence | main-owned evidence projector 在每个 attempt boundary 生成 | local diagnostic store，immutable revisions | #324 outcome/retry authority、#325 transport reference、bundle/support |

### 6.1 Machine Capability Receipt

schema ID：`seektalent.machine-capability-receipt/v1`。每次安装后、升级后、启动前 preflight 和手动 doctor 生成。

Allowlist：

- `receipt_id/revision/generated_at/release_manifest_ref`；
- OS family、OS build bucket、architecture、Python/Node/SQLite/Chrome major version 和 channel；
- install channel、artifact platform/arch、component build refs、bridge implementation/protocol/capability names；
- Chrome profile mode、profile binding hash/generation、extension version/ID hash、provider account hash；
- daemon endpoint ownership status，不能包含认证 header 或完整命令行；
- DB logical name、schema version、journal mode、integrity result、file/WAL/SHM size bucket；
- disk free size bucket、writable/executable checks；
- network posture flags：`offline/system_proxy_present/custom_ca_present/chrome_managed`，不记录 proxy URL、证书主体、SSID 或 IP；
- capability result：`supported/unsupported/indeterminate` 以及稳定 `gap_codes`。

原始 hostname、username、home/workspace/profile path 不得保存。路径只用 logical label（如 `runtime_control_db`）和每安装随机 salt 的 HMAC ref；bundle 不导出 salt。

### 6.2 Startup Receipt

schema ID：`seektalent.startup-receipt/v1`。每个 main/controller/sidecar/worker/WTSCLI/extension generation 各自产生。

必填事实：

- `startup_receipt_id`、`component`、`component_instance_id`、`parent_instance_id`；
- capability receipt ref、release/build/protocol/capability refs；
- `startup_kind=fresh/restart/upgrade_rebind/wake`；
- `started_at/readiness_observed_at/exited_at`；
- `readiness=ready/not_ready` 和 stable reason code；
- bounded restart count/budget ref、previous instance ref、last exit cause ref；
- profile binding generation、browser scope ref、extension install generation、service-worker generation 等适用 identity；
- DB schema/integrity refs 和 endpoint ownership ref。

`extension installed`、`browser/profile started`、`service worker awakened`、`daemon ready` 必须是不同事实。单一 `started=true` 不构成 Startup Receipt。

### 6.3 Operation Evidence

schema ID：`seektalent.operation-evidence/v1`。每个 attempt 完成、失败、取消或进入 reconciliation unknown 时生成一个 immutable revision。

必填事实：

- correlation/run/operation/attempt/trace identity 和 authority refs；
- capability/startup receipt refs；
- source ID 和 operation kind token；
- first/last event、Failure Envelope 和 checkpoint refs；
- acceptance、dispatch、side-effect、result persistence、main commit、cleanup boundary facts；
- safe result/count/coverage summary；
- observed `SourceOperationDisposition` ref/value。该 enum 和映射由 #324 唯一拥有，本文件不重定义；
- observed `ProductOutcome` ref（若已产生）；
- missing evidence refs、rejected stale-write count、journal truncation state；
- canonical hash 和 redaction result。

Operation Evidence 可以证明“哪些事实已观察到/未观察到”，不能证明未发生的 side effect，也不能授予下一次执行权限。

## 7. Canonical journal 与保留预算

canonical journal 是本地 append-only evidence projection，不是主业务 queue，也不替代组件自己的 authority store。

### 7.1 写入与降级

1. producer 先按 event registry 生成 allowlisted event；sink 再验证 schema、size、identity、authority 和 redaction。
2. sink durable append 成功后才返回 receipt；pipe/HTTP 已发送不等于 journal 已接受。
3. sink 不可用不能阻止 main 记录业务 durable truth，但必须在恢复后生成 `diagnostic_gap_detected`，并在 Operation Evidence 标记 gap。
4. journal 自身写失败只允许写到固定大小的 local emergency ring：最多 128 records、每条 2 KiB，仅含 event identity/component/reason/time。ring 不可用时在 operator health 暴露 counter；禁止回退到 raw stderr dump。
5. journal migration 使用现有 backup + integrity-check 原语；schema ahead、corrupt、read-only、full 必须产生不同 capability/failure code。

### 7.2 v1 budgets

| 对象 | 上限/保留 |
|---|---|
| 单 canonical event | 16 KiB serialized JSON |
| 单 Failure Envelope/receipt/Operation Evidence | 32 KiB serialized JSON |
| journal event rows | 50,000 |
| journal 总大小 | 64 MiB（含 WAL/SHM 计入 budget） |
| journal 时间窗 | 14 天；active operation 及其最近 evidence 不按 TTL 删除 |
| emergency ring | 128 × 2 KiB |
| 已导出 support bundle | 默认 7 天后进入现有 local-storage cleanup；用户可立即删除 |

行数、大小、时间任一先到即触发 deterministic compaction。保留优先级从高到低：active operation；Failure Envelope 与 receipts；每个 terminal operation 最新 Operation Evidence；其 causal spine；普通 info/debug event。任何删减都增加 `dropped_by_class` 和 `oldest_retained_at`，禁止静默丢弃。核心 evidence 仍无法满足 64 MiB 时停止接受新普通 event、保留 failure/capability facts，并报告 `diagnostic_budget_exhausted`；不得删除 main business truth。

## 8. Support Bundle v1

schema ID：`seektalent.support-bundle-manifest/v1`。bundle 默认只在本机生成，不自动上传；用户必须先看到 preview，再手动导出。导出目录权限 POSIX 为 `0700`、文件为 `0600`；Windows 使用当前用户 ACL。

### 8.1 固定内容

```text
support-bundle-<opaque-id>/
  manifest.json
  machine-capability.json
  startup-receipts.jsonl
  operation-evidence.jsonl
  failure-envelopes.jsonl
  diagnostic-events.jsonl
  health-summary.json
  reproduction-recipe.json
  redaction-report.json
  checksums.sha256
```

`manifest.json` 只含 bundle/schema/redaction version、created time、release reference、选择的 operation/time window、文件 rows/bytes/hash、truncation、missing evidence 和 exporter build ref。`checksums.sha256` 覆盖其余固定文件；文件按稳定 key 排序，使相同 evidence snapshot 可重复验证。

### 8.2 allowlist 与禁止项

允许：本文件各 canonical schema 的 allowlisted projection、component/version/protocol/capability、safe state transition/reason code、opaque/HMAC refs、size/count bucket、DB schema/integrity、redaction summary。

默认明确禁止：

- cookies、Authorization、token、password、secret、API key、handoff/control key、browser debug endpoint；
- 原始 provider/LLM request 或 response、prompt、JD、简历、候选人姓名/公司/学校、查询词；
- DOM、HTML、inner/visible text、截图、download、clipboard；
- 完整 URL/query string、IP、SSID、proxy URL、certificate subject；
- hostname、username、绝对 home/workspace/Chrome profile path；
- raw SQLite/DB/WAL/SHM、raw stdout/stderr/log、crash dump、memory dump；
- artifact/replay/eval/debug 内容，即使 artifact metadata 标记 `support_bundle_only` 也必须先通过独立 projection。

每个 event-name/receipt schema 都有 golden forbidden-value corpus 和生成式敏感 key/value scan。命中禁止内容时该 record fail-closed，不进入 bundle；`redaction-report.json` 只记录 schema、field path token、rule ID 和 count，不保存命中的原值。

### 8.3 preview、大小与导出

- preview 显示时间窗、operation count、failure clusters、文件/row/bytes、被排除类别、redaction count、truncation/missing evidence；用户可以取消或缩小范围。
- 压缩前最大 100 MiB、压缩包最大 25 MiB。导出优先保留 receipts、failures、Operation Evidence 和 causal spine，再按时间从近到远纳入普通 events；所有裁剪写入 manifest。
- 核心 evidence 本身超过任一上限时导出 fail-closed，返回 `support_bundle_core_evidence_oversize`，建议缩小 operation/time window；禁止生成未声明的不完整包。
- 导出使用一致性 read snapshot，不暂停当前 operation；临时文件与最终包同目录原子 rename，失败后清理 partial archive。
- “debug/full-local” 是另一个用户显式开启的本地模式，不得伪装成默认 support bundle，也不得自动上传。

## 9. Privacy-safe failure cluster 与 reproduction recipe

### 9.1 两级指纹

`failure_cluster_id` 是以下 canonical tuple 的 SHA-256：

```text
failure-envelope schema major
redaction policy major
component
phase
failure_domain
reason_code
cause_code bucket
last_safe_boundary
capability gap codes
protocol compatibility bucket
```

它排除 release、随机 ID、时间、用户/机器/账号/path 和业务内容，用于跨机器聚类。`release_occurrence_key` 再加入 `release_manifest_ref + artifact platform/arch + OS/Chrome major bucket`，用于确认某一产品包回归。cluster 原始 tuple 同时保存在 bundle，避免 hash 无法解释。

### 9.2 reproduction recipe

schema ID：`seektalent.reproduction-recipe/v1`，只含：

- failure cluster tuple、release/artifact ref、OS/arch/Chrome major/channel；
- profile mode、bridge component refs、capability gap codes；
- network/storage posture flags与 SQLite version/journal mode；
- operation kind、boundary facts、必要的 startup sequence；
- synthetic scenario ID 或经过批准的 real-site canary ID；
- expected Failure Envelope、Operation Evidence 和 invariant assertions。

recipe 不含用户原始 JD/查询/简历。无法在 synthetic contract site 复现的 provider drift，进入受控 real Liepin canary；fixture 只能验证本地协议与 failure plumbing，不能替代真实站点证据。

### 9.3 从“用户跑不了”到回归 gate

```text
用户本地 doctor/失败页
  -> preview + 手动导出 bundle
  -> 校验 checksum/schema/redaction
  -> failure_cluster_id 聚类
  -> 生成最小 reproduction recipe
  -> synthetic/clean-machine/real-canary 复现
  -> 固化 fault 或 regression case
  -> 分配 PR/nightly/release gate
```

分类规则：

1. privacy leak、stale authority write 被接受、用户 tab 被误控制、durable accepted job 丢失，单次可信证据即为 release blocker。
2. supported environment 上的 clean install/startup/core Liepin operation 可重复失败，一旦内部复现即为 release blocker。
3. 尚未内部复现的 unknown cluster，在两个独立 installation 的同一 release occurrence 上出现，升级为 release-blocking regression candidate；release 前必须在对应 clean-machine posture 复现或给出明确 unsupported capability classification，不能以“开发机正常”关闭。
4. 单个 bundle 仍进入 triage backlog；缺失历史五位用户日志不会阻塞本基线，也不能被猜测成某个原因。
5. 修复完成的 cluster 必须有 deterministic case、预期 envelope/evidence 和至少一个自动或 release gate；只修代码、不固化证据合同不算关闭。

## 10. Fault-injection matrix

每个场景都要断言四类结果：main business truth、canonical event/envelope、重启/恢复后的 integrity、用户可见 outcome/disposition reference。后者的枚举与映射来自 #324。

| ID | 注入点 | 必须观察的 evidence | 最低 gate |
|---|---|---|---|
| F01 | main 在 durable acceptance 前退出 | 无 accepted record；startup/process envelope；无虚假 operation accepted | PR process test |
| F02 | main 在 accepted commit 后、dispatch 前退出 | accepted boundary ref；重启可恢复同 operation/next attempt；无丢 job | nightly |
| F03 | dispatch intent durable 后 sidecar 未收到即退出 | dispatch boundary、attempt identity、cause unknown/pipe close；无假完成 | nightly |
| F04 | sidecar 收到后、ack durable 前被 kill | receipt 缺失、side-effect `unknown`、process exit cause | nightly |
| F05 | side effect observed 后、result persist 前 kill | observed boundary、result unknown、禁止诊断层自动重试 | release fault gate |
| F06 | result durable 后、main commit 前 kill | result ref 可恢复、main commit not observed、无重复业务提交 | nightly |
| F07 | main commit 后、cleanup 前 kill | committed fact 保留；cleanup pending/failed 单独 envelope | nightly |
| F08 | worker/sidecar pipe EOF、timeout、malformed/oversize frame | protocol reason、last good component seq、missing cause refs | PR + nightly |
| F09 | wrong implementation/build/protocol/capability | Startup Receipt `not_ready` + 现有 typed bridge reason | PR |
| F10 | stale runtime attempt 写 event/result | rejected counter + `authority.write.rejected`；可信 timeline 不含写入 | PR blocker |
| F11 | stale profile binding generation | authority envelope；不启动 provider action | PR blocker |
| F12 | stale browser control fence/control scope | `opencli_stale_control_fence`；用户 tab 无 mutation | PR + real browser |
| F13 | daemon port 已被其他进程占用/daemon stale | endpoint ownership receipt、process/startup reason，不 kill 非 owned 进程 | nightly clean machine |
| F14 | extension missing/disconnected/wrong ID | capability/startup gap，不把 daemon alive 当 ready | nightly clean machine |
| F15 | MV3 service worker 在 command 前/中/后显式终止 | generation change、durable accept/complete boundary、未知项不推断 | nightly browser |
| F16 | Chrome/profile 未启动、locked、被删除或账号 binding 变化 | capability/profile generation reason；无旧账号 session 复用 | release matrix |
| F17 | host tab ambiguous/owned tab missing/user closes tab | typed browser reason、scope ownership、无用户 tab mutation | nightly browser |
| F18 | selector/DOM drift/page not ready/risk control | provider/browser domain 分离；safe page capability facts；无 DOM export | nightly synthetic + real canary |
| F19 | DNS/offline/system proxy/custom CA/TLS failure | network posture + safe OS/network code；不泄露 endpoint/证书 | release matrix |
| F20 | SQLite lock/busy timeout | extended result code、logical DB、transaction boundary、恢复 integrity | PR file test |
| F21 | SQLite full/read-only/cantopen/I/O error，第 N 次 I/O 单次及持续失败 | 不同 cause codes、无 half commit、恢复后 integrity | nightly storage |
| F22 | SQLite corruption/schema ahead/migration 中断 | capability unsupported/indeterminate、backup/integrity refs、fail closed | release upgrade gate |
| F23 | journal 超 event/row/byte budget | oversize/budget reason、deterministic compaction counters、业务 truth 不受影响 | PR |
| F24 | canonical sink/ring 同时不可用 | diagnostic gap counter、Operation Evidence missing refs、无 raw stderr fallback | nightly |
| F25 | LLM request timeout/cancel/process interruption，或 response schema validation 失败 | model-domain cause 分型；只有 structured-output parse failure 可记录现有 bounded retry evidence，其他失败不得套用该例外 | PR + nightly |
| F26 | provider HTTP auth/rate/risk-control/unknown response | safe worker reason、source coverage/possible consumption facts | PR + real canary |
| F27 | install/upgrade 在 active operation 中发生，或 installer 在 stage/switch 中途被 kill | drain/startup receipts、旧新 build refs 不混用、authority rotation、atomic switch/rollback evidence | release upgrade gate |
| F28 | N-1 升级、回滚、DB migration backup 恢复 | release refs、schema/integrity、operation truth 可读 | release upgrade gate |
| F29 | cleanup/reclaim 失败或 lifecycle registry fail-open | cleanup envelope；registry 不作为成功 authority | nightly |
| F30 | bundle 中注入 forbidden canary values | export fail-closed/redaction report；压缩包零敏感值 | PR blocker |
| F31 | bundle 导出中断、磁盘满、超过 size cap | partial file 清理、manifest 不发布、typed export reason | PR file test |
| F32 | wall clock 回退/跳跃、component sequence 重复/冲突 | causal/sequence rule 生效；不按 timestamp 重排 authority | PR |

进程 crash 场景必须使用独立进程硬终止，不只 mock exception。SQLite 场景按实际文件、实际 journal mode、每个 durable boundary 推进 failpoint；解除故障后运行 `PRAGMA integrity_check`。Chrome worker 场景要显式终止 service worker，不能依赖调试器附着下不会发生的自然 lifecycle。

## 11. Clean-machine matrix

### 11.1 release artifact 行

三行均是 release blocking；具体签名、manifest 和包内容由 #326 提供。

| Artifact | clean install | N-1 upgrade | rollback | offline/first start | real Liepin canary |
|---|---:|---:|---:|---:|---:|
| Windows 11 x64 | required | required | required | required | required |
| macOS arm64 | required | required | required | required | required |
| macOS x86_64 | required | required | required | required | required |

当前主线只有 macOS x86_64 offline build/smoke，且 workflow 是手动触发；这不是三平台 gate 已完成的证据。

### 11.2 每个平台的 posture cases

| 维度 | 必测 cases |
|---|---|
| 权限/路径 | standard user、non-admin install、空格与 Unicode 用户目录、不可写目录、低磁盘；bundle 不导出原路径 |
| Chrome | #326 声明的 supported Stable window；fresh/existing/multiple/locked profile；extension missing/disabled/update；MV3 restart |
| profile/account | dedicated profile spike、existing-profile compatibility、未登录、账号切换、binding generation 改变 |
| coexistence | Domi/WTSCLI 与 legacy OpenCLI remnants；端口、进程 ownership、state/env/profile/extension origin 必须隔离 |
| network | direct、offline、system proxy、custom CA、managed Chrome；未支持 posture 必须 capability-classified，不得悬空失败 |
| storage | product 实际 SQLite/journal mode、near-full、read-only、lock contention、corrupt/schema-ahead、backup recovery |
| lifecycle | fresh start、component crash/restart、OS/browser restart、upgrade during idle/active、rollback |
| site | synthetic contract site 验证协议/DOM failure；真实 Liepin 账号 canary 验证 real-site drift/risk-control |

real canary 只保存 operation/evidence IDs、safe counts、reason codes 和 opaque account ref；不保存页面内容、候选人或账号凭据。dedicated profile 的 spike evidence 是生产 gate，是否最终采用 dedicated profile 由后续产品决策决定。

## 12. Gate 分层

| Gate | 运行时机 | 内容 | 阻断条件 |
|---|---|---|---|
| PR deterministic | 每个相关 PR | schema/registry validation、identity/cause DAG、reason mapping、redaction golden/property、size/budget、deterministic bundle、stale fence、SQLite file failpoints、synthetic protocol faults | 任一 invariant、privacy scan 或 deterministic case 失败 |
| nightly integration | 每夜/受控 host | real process kill/restart、pipe faults、SQLite I/O/crash、MV3 termination、synthetic browser contract、journal degradation | evidence 缺失、half commit、authority violation、不可解释 unknown |
| release clean-machine | 候选 release；使用 #326 exact artifacts | 三平台 install/upgrade/rollback、posture matrix、real Liepin canary、dedicated-profile spike、support bundle preview/export/re-import | supported matrix 任一失败；canary/signed artifact/evidence 不完整 |
| manual incident regression | 导入用户 bundle 后 | checksum/schema/redaction 校验、cluster、recipe、复现、固定 regression | blocker cluster 无 regression case 或无明确 unsupported classification |

PR gate 必须保持快且 deterministic；不得把真实账号 secret 放入普通 PR CI。real Liepin canary 在隔离 release environment 运行，结果只输出本文件 allowlist evidence。当前 `python-quality` 自动运行而 governance/workbench-contract/build workflow 多为手动触发；后续落地必须明确哪些 workflow 提升为 required check，不能在文档中把手动命令算作已执行 gate。

## 13. 交付顺序与验收

### 13.1 最小落地顺序

1. 先实现纯 schema/registry/redaction/cluster/bundle fixtures，不接业务控制。
2. 复用 runtime-control、Liepin、OpenCLI、operator-health 的现有安全原语做 producer adapters。
3. 建 canonical local journal、retention 和 emergency ring；证明诊断故障不改变业务 truth。
4. 接 doctor/本地失败页 preview/manual export。
5. 建 deterministic fault harness，再建 nightly process/storage/browser gates。
6. 接收 #324 disposition/outcome refs、#325 transport refs、#326 release refs；只引用，不复制 owner 语义。
7. 最后建立三平台 exact-artifact release matrix 和 real Liepin canary。

这条顺序隔离技术债：超大 runtime/browser/provider 文件不因 #322 被整体重写；先以小的 schema 和 producer adapter 收敛证据边界，再按真实 fault coverage 逐个拆分高风险模块。

### 13.2 Definition of Done

- canonical event、Failure Envelope、三类 receipt/evidence 均有 versioned schema、validation 和 golden examples；
- Failure Envelope schema scan 证明没有 retry permission 字段；
- support bundle 默认 local-only、allowlist、可 preview/manual export，且 forbidden corpus scan 为零泄漏；
- journal 与 bundle 的 retention/size/truncation 行为 deterministic；
- fault matrix 的每个 scenario 有 owner、injection seam、expected evidence 和 gate；
- clean-machine matrix 在 #326 exact artifacts 上生成可校验 receipts；
- real Liepin canary 不能被 fake/synthetic fixture 替代；
- 两个独立未知机器 bundle 可形成相同 privacy-safe cluster 并生成无业务正文 recipe；
- blocker cluster 修复后必须留下 release-blocking regression；
- #324 交叉复核确认 #322 未定义 RetryPosture/ProductOutcome；#325/#326 只消费 refs。

## Appendix A. 外部一方约束（不是本项目已实现事实）

以下内容只提炼官方规范和一方实现文档对本地诊断设计的约束。它们不证明 SeekTalent 当前已经产生这些事件、回执或测试证据，也不要求引入远程 telemetry backend。

### 事件 identity 与记录形状

- 如果采用 W3C trace identity，`trace-id` 必须是 16 字节、32 位小写十六进制且不能全零；`parent-id` 必须是 8 字节、16 位小写十六进制且不能全零。无效输入应被拒绝或重新建立上下文，不能作为可信关联键继续传播。随机生成且全局唯一的 `trace-id` 优于编码机器、用户或业务信息的 ID。[W3C Trace Context：`traceparent` 字段](https://www.w3.org/TR/trace-context/#traceparent-header-field-values) [W3C Trace Context：ID 生成](https://www.w3.org/TR/trace-context/#considerations-for-trace-id-field-generation)
- `traceparent` 和 `tracestate` 的用途仅限关联；规范明确禁止在其中放入个人可识别信息或其他敏感信息，并要求把入站值当作可能恶意的数据进行长度、字符和格式校验。因此，本地 support bundle 中的关联 ID 应为随机不透明值，不能由账号、简历、职位、路径、主机名或页面内容派生。[W3C Trace Context：隐私](https://www.w3.org/TR/trace-context/#privacy-considerations) [W3C Trace Context：安全](https://www.w3.org/TR/trace-context/#security-considerations)
- OpenTelemetry Logs 的稳定数据模型把 `Timestamp`（源端发生时间）和 `ObservedTimestamp`（收集端观察时间）分开，并为 `TraceId`、`SpanId`、`Severity*`、`Body`、`Attributes`、`EventName` 定义独立语义。SeekTalent 的本地 canonical event 可以采用这些语义作为字段设计约束，但不因此引入 OpenTelemetry SDK、OTLP 或远程导出器。[OpenTelemetry Logs data model](https://opentelemetry.io/docs/specs/otel/logs/data-model/#log-and-event-record-definition)
- `EventName` 应表示稳定的事件类别，变化频繁或实例特有的信息放在结构化 attributes；自由文本 `Body` 不能成为机器聚类所需的唯一事实。对 support bundle 的直接设计含义是：聚类键必须来自有界、可枚举、已脱敏的结构化字段，原始异常文本和页面内容默认不进入 bundle。[OpenTelemetry Logs：EventName](https://opentelemetry.io/docs/specs/otel/logs/data-model/#field-eventname) [OpenTelemetry Logs：Attributes](https://opentelemetry.io/docs/specs/otel/logs/data-model/#field-attributes)

### SQLite 故障注入与恢复证据

- SQLite 自身用可替换 VFS 在第 N 次 I/O 操作注入错误，并分别覆盖“只失败一次”和“首次后持续失败”；它还通过独立进程崩溃、重排/破坏尚未同步的写入来模拟掉电。项目故障矩阵应复用这个测试原则，在每个持久化边界推进故障点，而不是只写一个固定的“磁盘失败”单测。这里的 VFS 是 SQLite 自身测试模型，不是本项目可假定存在的 SQL 开关；SeekTalent 需要自己的受控存储测试 seam。[SQLite：I/O error 与 crash testing](https://www.sqlite.org/testing.html#io_error_testing)
- 故障解除后，SQLite 的官方测试使用 `PRAGMA integrity_check` 验证数据库未损坏，并断言事务要么完整提交、要么完整回滚。故障注入 gate 因而至少要同时验证：对外 Failure Envelope、重启后的可读性、`integrity_check`、以及操作事实没有半提交。[SQLite：How SQLite Is Tested](https://www.sqlite.org/testing.html#crash_testing) [SQLite：PRAGMA integrity_check](https://www.sqlite.org/pragma.html#pragma_integrity_check)
- 不应把存储故障压成一个字符串：`SQLITE_FULL`、`SQLITE_IOERR`、`SQLITE_CORRUPT`、`SQLITE_READONLY`、`SQLITE_CANTOPEN` 和锁竞争具有不同含义；例如磁盘满通常返回 `SQLITE_FULL`，且可能来自临时文件所在的另一分区。fault matrix 和 evidence 必须保留原始/扩展 result code，再映射为本项目稳定错误分类。[SQLite result codes](https://www.sqlite.org/rescode.html)
- SQLite 的原子提交依赖操作系统、文件系统和同步原语满足其假设；官方文档明确指出损坏的 `fsync` 语义可能在掉电时造成损坏。因此 clean-machine gate 不能只依赖内存数据库或 mock，至少要在产品实际 journal mode、实际文件系统路径和打包后的 SQLite 版本上执行真实文件测试。[SQLite Atomic Commit：hardware assumptions](https://www.sqlite.org/atomiccommit.html#hardware_assumptions)

### Chrome extension service-worker 生命周期证据

- Manifest V3 extension service worker 通常会在空闲约 30 秒后终止，也可能因单次请求超过 5 分钟或 `fetch()` 响应超过 30 秒而终止；后续事件会重新唤醒它。所有全局变量都会随终止丢失。因此 worker 是否“曾启动/处理/确认”不能从内存变量或当前在线状态推断，关键 evidence 必须在确认前写入可恢复存储。[Chrome：extension service-worker lifecycle](https://developer.chrome.com/docs/extensions/develop/concepts/service-workers/lifecycle#idle-and-shutdown)
- Chrome 官方的终止测试会显式取得 service-worker target、关闭 worker，再发送消息验证它被重新唤醒且仍能正确响应；官方同时说明 Selenium/ChromeDriver 的 debugger 附着会阻止 worker 像正常使用时那样自动终止。因此 fault-injection gate 应显式终止并断言终止后的行为，不能把“等待自然休眠”当作可重复证据。[Chrome：Puppeteer termination test](https://developer.chrome.com/docs/extensions/how-to/test/test-serviceworker-termination-with-puppeteer) [Chrome：extension E2E testing](https://developer.chrome.com/docs/extensions/how-to/test/end-to-end-testing#testing-service-worker-termination)
- `chrome.runtime.onInstalled`、`activate`、profile 启动时的 `chrome.runtime.onStartup` 与 worker 被事件唤醒不是同一个生命周期事实；其中 profile 启动不会触发 service-worker lifecycle events。Startup Receipt 必须分别记录 extension install/update、browser/profile startup 与 worker generation，不能用单一 `started=true` 混为一谈。[Chrome：installation 与 extension startup](https://developer.chrome.com/docs/extensions/develop/concepts/service-workers/lifecycle#installation)
- `chrome.storage.session` 可跨 service-worker 休眠保留内存状态，但在扩展 disable/reload/update 或浏览器重启时清空；`chrome.storage.local` 持续到扩展被移除。两者可以分别提供“本次浏览器/扩展会话”和“跨重启安装实例”的证据边界，但写入是异步且有容量限制，不能把成功调用前的内存状态当作已持久化回执。[Chrome Storage API：storage areas](https://developer.chrome.com/docs/extensions/reference/api/storage#storage-areas)
- Chrome Storage API 默认可能把部分存储区暴露给 content scripts，并提供 `setAccessLevel()` 收紧访问。extension lifecycle evidence 应只保存有界元数据和不透明关联 ID，显式限制 content-script 访问；Cookie、页面 DOM、账号、简历、JD、聊天内容和完整 URL 不属于该证据层。[Chrome Storage API](https://developer.chrome.com/docs/extensions/reference/api/storage)
