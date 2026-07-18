# External Execution Plane v1 可诊断性与故障注入基线

状态：Wayfinder #322 范围契约；已按 #324 集成审查修订，待复核

Tracks: #322

主线输入：

- `e522f51c`：External Execution Plane v1 可靠性契约；
- `f28bc529`：External Execution Plane v1 runtime topology；
- GitHub issue #322 当前 body；
- Draft PR #332 `a6d83ab9`：task semantics ownership boundary，仅作合并前交叉复核输入；
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
7. #324 只从 main-owned durable run/source/checkpoint/candidate truth 与经 #325 reconciliation 提交的业务事实决定 `RetryPosture/ProductOutcome`；#322 对象只是这些事实的 allowlisted projection/reference，永远不是执行 authority。

## 2. 当前代码事实、缺口与决策

以下是本任务检查主线代码后的结果，不是目标架构假设。

| 区域 | verified fact | gap | v1 decision |
|---|---|---|---|
| runtime event | `seektalent_runtime_control.models.RuntimeControlEventInput` 有 `event_id/runtime_run_id/event_type/stage/round/source/status/payload`；store 为每个 run 分配 `event_seq`，支持 event/idempotency uniqueness | event 未持久化 canonical `operation_id/attempt_no/component/build/cause_ref`；run 内 sequence 不能关联浏览器、worker 和 provider journals | producer adapter 将当前代码唯一的 `runtime_run_id` 映射为 canonical `run_id`；canonical schema 不再并存第二个同义 run ID；现有 public event 不是 canonical journal |
| executor authority | executor lease 持有 main-owned `attempt_no`；`append_executor_event` 在写前校验 active executor/attempt，能拒绝 stale write | attempt 只参与 guard，没有进入已存事件证据；当前 `(executor_id, attempt_no)` 还不是目标 opaque fence | canonical event 和 `OperationEvidence` 记录同一 main runtime executor `attempt_no` 与 `runtime_attempt_fence_token` 的非敏感 authority ref；不得映射 sidecar/browser/source 的局部重试计数 |
| runtime persistence | runtime-control SQLite 有 schema migration、备份、`integrity_check`、事务 checkpoint/candidate truth；event payload 上限为 16 KiB | 现有 retention 面向 runtime/debug，不是跨组件 support evidence budget | 复用 16 KiB 单事件上限、migration/integrity 原语；新增独立 canonical projection 和明确 budget |
| event privacy | `seektalent_runtime_control.events` 会按敏感 key/summary 词过滤；public event 是有界用户投影 | key-fragment heuristic 不是版本化 allowlist；public event 缺少原因链和环境证据 | producer 先做安全 projection，canonical sink 再按 event-name allowlist 验证；双层 fail-closed |
| Liepin journal/account state | `liepin_events` 按 subject 分配 sequence，`append_event` 拒绝明显不安全 payload；但 `liepin_connections.observed_provider_account_subject` 是 `TEXT`，binding 路径会把观察到的账号主体明文写入后再计算 HMAC | 当前 provider connection DB 含敏感明文状态，且与 runtime journal 无共同 run/operation/attempt/cause-ref identity；现有 unsafe-event guard 不能证明该 DB 可安全导出 | canonical producer adapter 只允许 opaque/HMAC account ref，永不读取或投影 `observed_provider_account_subject`；加入 exact field/value forbidden-corpus negative test；后续 migration 清理或加密/HMAC 现存列，本票不改代码 |
| worker | external worker health 只返回 `status` 和可选 `workerVersion`；请求已有 `traceId`，OpenCLI path 将其当 `source_run_id` | health 无 implementation/build/protocol/capabilities；`traceId` 语义过载且没有 durable causal chain | receipt 显式记录 component identity/capability；旧 `traceId` 只作 legacy correlation input，不能单独证明因果 |
| browser bridge | 已有严格 implementation/build/protocol/capability 校验、typed reason codes、command ID、deadline、control scope/fence | daemon transport 与 lifecycle registry 没有 durable canonical evidence；registry 明确 fail-open，异常只留 exception type | 保留 bridge identity、reason code、scope/fence；registry 仅作为 producer，不能成为 authority 或成功证明 |
| Chrome lifecycle | 当前可以探测 extension/daemon/profile/tab 状态 | MV3 worker 会终止和重启；“现在在线”不证明之前 command 已持久接受或完成 | `StartupReceipt` 区分 browser/profile startup、extension install/update、worker generation 和 command boundary |
| operator health | `operator_health.py` 已报告磁盘、DB/WAL/SHM 大小、schema version、integrity reason；`operator-health` maintenance 命令有测试 | `seektalent doctor` 未聚合 DB health、Chrome/extension/profile/runtime component identity | 复用 operator health 为 `MachineCapabilityReceipt` input；#322 后续实现把 receipts 暴露给 doctor/bundle |
| diagnostics ownership | 当前 runtime-control、Liepin、browser lifecycle 各有局部事件/状态存储；没有一个实现了本文件 canonical schema 的统一 sink | 若让各组件直接写 canonical journal，会产生多个 `journal_seq`、authority validator、retention 和 export owner | main 内 diagnostics service 是唯一 canonical sink/journal/store 和 receipt issuer；其他组件只发送 allowlisted producer facts；sidecar operation journal 仍只属于 #325 reconciliation，不是第二个 diagnostic journal |
| diagnostics artifacts | `runtime_diagnostics.py`、replay/eval artifacts 包含大量业务调试数据；artifact lifecycle 已有 `support_bundle_only` 元数据 | 这些 artifacts 不是 privacy-safe support bundle，不能整体复用 | 只复用 hash/size/lifecycle primitives；默认 bundle 禁止导出业务 artifacts |
| local storage | local lifecycle 已识别 `support-bundles` storage class，默认 7 天清理；prod 总本地预算 2 GB | 没有 bundle schema、生成器、preview 或 per-bundle size cap | bundle v1 沿用 7 天默认 TTL，并增加本文件的独立大小上限 |
| packaging | 仓库保留 macOS x86_64 offline builder/workflow path，脚本会校验 bridge hashes；当前项目版本是 `0.7.49`，builder 必须读取 `constraints-0.7.49-macos-intel.txt`，但仓库只有 0.7.46/0.7.47 constraints | checked-in inputs 不能生成当前 0.7.49 product artifact；也没有 Win11 x64/macOS arm64 对等 artifact；现有 bundle manifest 不是 #326 Release Manifest | 把该路径标为历史/不完整 builder evidence，不把它算作当前 artifact PASS；#326 补齐 exact artifact 后，#322 matrix 才能消费其 ref |
| CI | `python-quality.yml` 的 PR path filter 不含 `docs/**`，因此当前 docs-only PR 不会自动触发；governance、workbench-contract、macOS Intel build 均只支持 `workflow_dispatch` | 当前 #333 没有这些自动 status checks，也无自动 fault-injection、clean-machine 或 real Liepin release gate | 本文四层 gate 是目标 contract；当前文档变更本地手工运行现有脚本，后续实现 PR 才把相应 deterministic gates 配成 required checks |

### 2.1 Preserve / migrate / delete

- **Preserve**：runtime-control 事务和 stale-attempt guard、SQLite migration/integrity check、OpenCLI typed reason codes/bridge identity/fence、Liepin unsafe-payload rejection、opaque account/profile refs、operator-health DB 摘要、artifact hash/size/lifecycle。
- **Migrate**：各自事件/health/result 经 main diagnostics service 映射为 canonical event、receipt 和 `OperationEvidence`；映射层是 allowlist projection，不是复制 raw payload。`observed_provider_account_subject` 的现存数据必须在后续 storage migration 中清理或加密/HMAC，迁移前也不得进入 producer projection。
- **Delete from the diagnostic path**：以自由文本异常作为机器分类主键、导出 raw SQLite/log/DOM、用当前在线状态推断历史成功、用 fake fixture 替代 real Liepin canary、由 Failure Envelope 决定重试或产品结果。

### 2.2 Canonical diagnostics runtime owner

main 进程内的 **diagnostics service** 是 v1 唯一 canonical runtime owner。它唯一负责：

- 接收 main runtime-control/source acceptance 已分配的 `run_id/operation_id/attempt_no`，并在其上分配/确认 `correlation_id/diagnostic_trace_id`；
- 接收 sidecar/worker/WTSCLI/extension/Chrome/provider/installer 的 allowlisted producer facts；
- 对照 main durable truth 和三类 authority reference 做 schema/authority validation；
- durable append、幂等冲突检查与 `journal_seq` 分配；
- receipt/envelope/evidence revision、compaction、retention、emergency ring；
- support export 的一致性 snapshot、preview 和 archive。

producer 不能直接打开或写 canonical journal DB，也不能分配 `journal_seq`、提升 evidence revision、触发 compaction 或导出 bundle。sidecar 为 #325 reconciliation 保留的 bounded operation journal 是 source business evidence；它可被 main 查询并投影，但不是 canonical diagnostic journal，也不能直接改变 main run/source state。

## 3. Canonical identity 与因果链

### 3.1 identity 层级

跨 #322/#324/#325 的业务 identity 只有以下一套语义：

| 字段 | 语义 | 生成与稳定性 |
|---|---|---|
| `correlation_id` | 一次 product intent/linked support context 的非权威关联 ID | main 生成的随机 opaque ID；可跨同一 run 的 source operations，用于关联而不替代任何业务 identity 或 authority |
| `run_id` | 一个 main-owned logical product run | canonical wire/evidence 字段；当前代码的 `runtime_run_id` 由 producer adapter 一对一映射为它，canonical object 不再同时携带 `runtime_run_id` |
| `operation_id` | 一个 `run_id` 内由 main 分配的 source operation | main 在 source dispatch intent 前持久化；一次 source verify/search/detail/reconcile 等业务 operation 各有自己的 ID，不是整个 run，也不是 browser scope |
| `attempt_no` | 该 run 内 main runtime executor attempt/generation | 对应 #324 的单调 executor attempt，并绑定 `runtime_attempt_fence_token`；不是 sidecar 网络重试、source adapter retry count、LLM retry 或 browser control scope |
| `diagnostic_trace_id` | 一段诊断 trace 的关联 ID | 128-bit 随机、32 位小写 hex、非全零；不能由用户、机器或业务内容派生；它不是 run/operation/attempt identity 或 authority |
| `browser_control_scope_id` | 一次连续 browser-control attempt 的 correlation identity | 只进入 `correlation_refs`；scope mismatch 是 correlation/protocol fault，scope ID 永不授予控制权 |
| `event_id` | 单个 canonical event ID | producer 生成的随机 opaque ID；重投保持不变 |
| `journal_seq` | canonical journal 的观察顺序 | sink 在 durable append 时分配、全 journal 单调递增；不代表跨进程发生顺序 |
| `component_instance_id` | 某组件某次进程/worker generation | 每次进程启动或 extension service-worker generation 新建；重启必须改变 |
| `component_event_seq` | 单 instance 内 producer 顺序 | 从 1 单调递增；发现回退或重复且 payload 不同即记录 protocol failure |
| `span_id` | 一个有界 component action | 64-bit 随机、16 位小写 hex、非全零；只用于本地 causal tree |
| `parent_span_id` | 直接调用方 action | 可空；若存在，必须与同一 trace 关联 |
| `caused_by_event_id` | 直接触发当前事实的已知 event | 可空；不能跨 trace，不能成环；未知原因写空并给出 `cause_ref.certainty=unknown` |

`diagnostic_trace_id/span_id` 采用 W3C 字段形状，v1 不要求传播 HTTP `traceparent`，也不引入 OpenTelemetry SDK。任何外部传入 identity 都是不可信输入；格式不合法、全零或与 authority 不一致时，生成本地新 trace，并记录 `external_trace_rejected`。

#324 以 `run_id + attempt_no + runtime_attempt_fence_token` 保护 main durable mutation；#325 的每个 source operation 以同一个 `run_id + operation_id + attempt_no` 关联 request/result/reconciliation。`profile_binding_generation` 与 browser control fence 是另外两种 authority；`browser_control_scope_id` 和 sidecar 内部 command ID 只用于 correlation。它们都不能写入或递增 `attempt_no` 来冒充 main executor generation。

### 3.2 顺序规则

1. `occurred_at` 是 producer wall time；`observed_at` 是 canonical sink 首次观察时间；两者均为 UTC RFC 3339，允许偏差，不用于 fencing。
2. 同一 component instance 以内用 `component_event_seq` 排序；sink 的接收顺序用 `journal_seq` 排序；跨组件先后只由 `parent_span_id/caused_by_event_id` 和 durable boundary fact 建立。
3. producer 重投相同 `event_id` 和相同 canonical hash 是幂等；相同 ID 不同内容产生 `diagnostic_event_id_conflict`，原记录不可覆盖。
4. 父 event 可能因 crash 未到达；sink 保留 orphan event，并在 `OperationEvidence` 中报告 `missing_cause_refs`，不能编造父节点。
5. authority 校验失败的事件进入单独的 rejected counter，只保存安全 identity/reason，不进入可信 operation timeline。
6. terminal evidence revision 生成后才到达的合法事件标记 `arrival_class=late`，保留原 `occurred_at` 并生成新的 `OperationEvidence` revision；重投标记 `replayed`。旧 attempt 或旧 fence 的 late write 是 `stale`，只能进入 rejected counter，不能借“迟到”绕过 authority。

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
| `run_id` | nullable opaque string | run/operation event 必填；由当前代码 `runtime_run_id` 一对一映射 |
| `operation_id` | nullable opaque string | machine/startup events 可空 |
| `attempt_no` | nullable positive int | operation event 必填；语义固定为 main runtime executor attempt；machine event 可空 |
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
| `authority_refs` | bounded object | 只允许 `runtime_attempt_fence_ref/profile_binding_generation/browser_control_fence_ref`；均为非敏感引用，绝不含 raw token/control key |
| `correlation_refs` | bounded object | 可含 `browser_control_scope_id`、sidecar command ref；不得被任何 validator 当 authority |
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
| `schema_version` | 固定为 `seektalent.failure-envelope/v1` |
| `failure_id` | opaque failure identity；所有 revision 保持不变 |
| `revision` | positive int；main diagnostics service 单调分配 |
| `correlation_id/run_id/operation_id/attempt_no` | 冻结的最小业务 identity；`run_id` 由当前代码 `runtime_run_id` 一对一映射；不适用的 operation 可为 null，run failure 的 `run_id` 不可空 |
| `diagnostic_trace_id` | 诊断关联字段，不替代上面的业务 identity |
| `first_failure_event_id/last_observed_event_id` | 锚定 canonical journal。通常 first 必填；canonical sink 与 emergency ring 同时不可用时可为 null，但必须填写 `diagnostic_gap` 与 `observed_boundary_ref` |
| `component/component_instance_id/component_build_ref` | 观察或报告失败的组件 identity |
| `phase` | 使用 canonical phase |
| `domain` | `install/runtime/storage/browser/source/network/provider/policy/user_action/cleanup/unknown`；与 reliability contract §8 字面一致并仅增加 `unknown` |
| `failure_kind` | `capability_mismatch/startup_failure/process_exit/protocol_violation/authority_rejected/model_failure/package_integrity_failure/resource_exhausted/operation_failure/unknown` |
| `reason_code` | 稳定 token；不得直接使用任意异常文本 |
| `cause_ref` | bounded structured ref；见 §5.1。字段名不可改成 `cause` |
| `detail` | event/reason-specific bounded safe object；不得放 raw exception/message/payload |
| `boundary_facts` | `acceptance/dispatch/side_effect/result_persistence/main_commit/cleanup` 各自为 `not_started/not_observed/observed/unknown`，适用时填写 durable record ref |
| `last_safe_boundary` | 已有 checkpoint/safe-boundary token 或 `none/unknown`；必须来自 durable fact |
| `authority_refs` | 只允许 `runtime_attempt_fence_ref/profile_binding_generation/browser_control_fence_ref`；不得包含 control key/token 原文或 `browser_control_scope_id` |
| `correlation_refs` | 可含 `browser_control_scope_id`；scope mismatch 只能分类为 correlation/protocol fault |
| `diagnostic_gap/observed_boundary_ref` | sink/ring gap 的 reason、counter 与 main durable boundary ref；有 event anchor 时可空，无 `first_failure_event_id` 时必填 |
| `source_coverage` | source ID、started/completed/partial/unknown、safe counts；不得含候选人内容 |
| `current_outcome` | nullable #324 `ProductOutcome` snapshot；初建可空，诊断层不得自行选择或修改 |
| `user_action` | nullable bounded safe object：`code/instruction_key/affected_scope_ref`；`needs_attention` revision 必须恰有一个 concrete action |
| `support_action` | nullable bounded safe object：`code/instruction_key`；不得包含机器路径、账号或自由文本异常 |
| `occurred_at/observed_at/redaction` | 与 event 相同语义 |

### 5.1 `domain`、`failure_kind` 与 `cause_ref`

`domain` 表示失败所属的产品边界，不能被更细实现类别替换。现有细类别按以下 registry 映射：

| observed boundary | canonical `domain` | allowed `failure_kind` examples |
|---|---|---|
| installer、manifest/hash/signature、active/previous slot | `install` | `capability_mismatch/package_integrity_failure/startup_failure` |
| main/controller/worker lifecycle、runtime fence、internal LLM | `runtime` | `startup_failure/process_exit/protocol_violation/authority_rejected/model_failure/resource_exhausted` |
| SQLite/open/migration/integrity/disk | `storage` | `capability_mismatch/resource_exhausted/operation_failure` |
| Chrome/profile/extension/WTSCLI browser command/fence | `browser` | `capability_mismatch/startup_failure/process_exit/protocol_violation/authority_rejected/operation_failure` |
| source dispatch/observation/reconciliation/adapter contract | `source` | `protocol_violation/authority_rejected/operation_failure` |
| DNS/proxy/CA/TLS/connectivity | `network` | `capability_mismatch/resource_exhausted/operation_failure` |
| remote provider auth/risk-control/HTTP/site behavior | `provider` | `protocol_violation/resource_exhausted/operation_failure` |
| compliance/product policy refusal | `policy` | `capability_mismatch/operation_failure` |
| login/CAPTCHA/profile selection等具体可执行用户动作 | `user_action` | `operation_failure` |
| tab/process/artifact reclamation after business boundary | `cleanup` | `process_exit/operation_failure` |
| 尚未分类 | `unknown` | `unknown` |

event/reason registry 必须为每个 producer reason exhaustive 地指定一个 `domain + failure_kind` pair；未映射、映射多个或使用 generic unavailable 的 reason 都使 schema/gate 失败。旧的 capability/startup/process/protocol/authority/model/packaging 分类不得继续充当 canonical `domain`。

`cause_ref` 固定承载以下 bounded fields：

- `kind=event/failure/durable_fact/external_code/unknown`；
- 与 kind 对应的一个 `ref_id`，或 null；
- 可选 `code`，只允许注册过的 SQLite/OS/HTTP/Chrome/producer code；
- `certainty=observed/derived/unknown`；
- derived 时必填 `derivation_rule_id`。

- 原始 SQLite/OS/HTTP/Chrome code 保存在注册过的 `cause_ref.code`，再映射稳定 `reason_code`。例如 `SQLITE_FULL`、`SQLITE_CORRUPT`、`SQLITE_READONLY` 不能都压成 `storage_failed`。
- 现有 OpenCLI reason codes 是 migration input；v1 不为同一事实创造第二个同义 code。跨组件映射表必须有 exhaustive test。
- `cause_ref.certainty=derived` 只能来自明确规则，例如“process exit 后 pipe EOF”；规则 ID 进入 `cause_ref.derivation_rule_id`。没有直接证据时必须是 `unknown`。
- 多个并发失败用多个 envelope，通过同一 trace 和 `cause_ref` 形成 DAG；禁止覆盖“最后一个异常”。

`first_failure_event_id=null` 只允许用于 canonical sink 与 emergency ring 同时不可用的窗口。producer/main 必须从 main-owned durable boundary 生成 `observed_boundary_ref`，并记录 `diagnostic_gap.reason_code/counter`；不得补造 event ID 或用后来的无关 event 充当 anchor。

### 5.2 Outcome association without diagnostic authority

#324 只从 main-owned durable run/source/checkpoint/candidate truth，以及经 #325 reconciliation 后提交进 main 的业务事实决定 `RetryPosture/ProductOutcome`。它不从 Failure Envelope、journal 或 `OperationEvidence` 推导或授权该决定。

同一个 main-owned durable boundary fact 可以有两个输出：一边进入 #324 的业务 transaction，另一边进入 main-owned projection outbox，随后由 diagnostics service 物化为 envelope/event/evidence。final `failed` 或 `needs_attention` commit 必须在 main transaction 中同时保存稳定的 `failure_id + envelope revision` link 和 projection outbox record；对应 envelope revision 的 `current_outcome` 必须等于该 transaction 已决定的 outcome。初始、尚未产生 outcome 的 envelope revision 可以为 null。

这个 link 是业务 commit 的输出，不是输入。diagnostic sink 暂时不可用、`OperationEvidence` 缺失或 support export 失败，只会让 projection pending/产生 diagnostic gap；不得阻塞、回滚或改写 main business commit。Failure Envelope 始终不包含任何 retry permission。

## 6. `MachineCapabilityReceipt`、`StartupReceipt` 与 `OperationEvidence`

所有 receipt/evidence 都由 main diagnostics service 签发为不可变 JSON 对象，写入时带 schema version、canonical hash、created/observed time、release reference 和 redaction policy version。其他组件只能贡献 producer facts；更新意味着产生新 revision，不能原地改写历史事实。

| 对象 | 唯一 owner/producer | canonical persistence | 引用/消费（均非执行 authority） |
|---|---|---|---|
| Release Manifest | #326 release pipeline | #326 定义的签名 artifact 与 installed copy | capability/startup receipts 只保存 `release_manifest_ref` |
| `MachineCapabilityReceipt` | main diagnostics service；installer 只提供 #326 installer/release evidence ref，或调用 main issuer | local diagnostic store | `StartupReceipt`、doctor、bundle、#326 release evidence |
| `StartupReceipt` | main diagnostics service；各 component 只产生 allowlisted startup facts | local diagnostic store | `OperationEvidence`、doctor、bundle |
| `OperationEvidence` | main diagnostics service 从 main-owned durable facts/reconciliation refs 投影 | local diagnostic store，immutable revisions | #325 只传输 `operation_evidence_id + revision + canonical_hash` ref；support/bundle/test 可消费；#324 只可做一致性审计，不能据此决定 outcome/retry |

### 6.1 `MachineCapabilityReceipt`

schema ID：`seektalent.machine-capability-receipt/v1`。每次安装后、升级后、启动前 preflight 和手动 doctor 都由 main diagnostics service 签发。installer 不得直接签发或持有另一份 canonical receipt；它只贡献 #326 installer/release evidence ref，或调用 main-owned issuer。

#### Local canonical receipt

本机 canonical receipt 保存 exact-artifact/support 判断所需的安全精确事实，不把所有值预先降为 major/bucket：

- `receipt_id/revision/generated_at/release_manifest_ref`；
- product version、Domi/product-host version/build、install channel、artifact platform/arch；
- OS family、精确 OS build、architecture，以及 Python/Node/SQLite/Chrome 的精确安全版本和 channel；
- active slot、previous slot，以及 slot switch/rollback status；
- Release Manifest ref/hash、artifact hash 和 manifest/artifact signature verification status；
- component exact build refs、bridge implementation/build/protocol/capability names；
- Chrome profile mode、profile binding hash/generation、extension version/ID hash、provider account hash；
- daemon endpoint ownership status，不能包含认证 header 或完整命令行；
- DB logical name、schema version、journal mode、integrity result、file/WAL/SHM size bucket；
- disk free size bucket、writable/executable checks；
- network posture flags：`offline/system_proxy_present/custom_ca_present/chrome_managed`，不记录 proxy URL、证书主体、SSID 或 IP；
- capability result：`supported/unsupported/indeterminate` 以及稳定 `gap_codes`。

原始 hostname、username、home/workspace/profile path 不得保存。路径只用 logical label（如 `runtime_control_db`）和每安装随机 salt 的 HMAC ref；bundle 不导出 salt。

#### Exported bundle projection

support bundle 只导出上述 receipt 的 versioned allowlist projection：product/Domi/component/release manifest/artifact/bridge 的 exact version/hash verification facts 保留，以便判断 exact artifact；可能增加机器指纹的 OS build、磁盘、文件大小、Chrome patch 和环境姿态按 policy bucket 或省略。projection 必须记录 `source_receipt_id/revision` 与 redaction policy version，不能把 bucketed bundle 反向覆盖本地 canonical receipt。

### 6.2 `StartupReceipt`

schema ID：`seektalent.startup-receipt/v1`。main diagnostics service 为每个 main/controller/sidecar/worker/WTSCLI/extension generation 签发；对应组件只产生 startup facts。

必填事实：

- `startup_receipt_id`、`component`、`component_instance_id`、`parent_instance_id`；
- capability receipt ref、release/build/protocol/capability refs；
- `startup_kind=fresh/restart/upgrade_rebind/wake`；
- `started_at/readiness_observed_at/exited_at`；
- `readiness=ready/not_ready` 和 stable reason code；
- bounded restart count/budget ref、previous instance ref、last exit cause ref；
- profile binding generation、browser scope ref、extension install generation、service-worker generation 等适用 identity；
- DB schema/integrity refs 和 endpoint ownership ref。

`extension installed`、`browser/profile started`、`service worker awakened`、`daemon ready` 必须是不同事实。单一 `started=true` 不构成 `StartupReceipt`。

### 6.3 `OperationEvidence`

schema ID：`seektalent.operation-evidence/v1`。每个 source operation 在一个 main attempt 内完成、失败、取消或进入 reconciliation unknown 时，由 main diagnostics service 生成一个 immutable revision。

必填事实：

- `operation_evidence_id`、positive `revision`、`canonical_hash`；
- `correlation_id/run_id/operation_id/attempt_no/diagnostic_trace_id` 和 authority refs；其语义与 §3 完全相同；
- capability/startup receipt refs；
- source ID 和 operation kind token；
- first/last event、Failure Envelope 和 checkpoint refs；
- acceptance、dispatch、side-effect、result persistence、main commit、cleanup boundary facts；
- safe result/count/coverage summary；
- main-owned committed `SourceOperationDisposition` ref/value 的 allowlisted projection。该 enum 和映射由 #324 唯一拥有，本文件不重定义；
- main-owned committed `ProductOutcome` ref/value 的 allowlisted projection（若已产生）；
- missing evidence refs、rejected stale-write count、journal truncation state；
- redaction result。

`OperationEvidence` 可以证明“哪些事实已观察到/未观察到”，不能证明未发生的 side effect，也不能授予下一次执行权限。#325 只传输/引用它的 stable ref，不复制或另造 evidence schema。它缺失时，#324 仍从 main durable truth 运行；不得把 evidence 补齐作为业务 commit 前置条件。

业务 authority 与诊断投影的单向关系固定为：

```text
sidecar/source facts
  -> #325 reconciliation
  -> main-owned run/source/checkpoint/candidate truth
  -> #324 RetryPosture/ProductOutcome decision
  -> main projection outbox
  -> #322 canonical journal/envelope/OperationEvidence
```

#325 可以传输或引用 evidence，但不能让 canonical journal 反向拥有 source/run 状态机；diagnostics service 也不能把 journal 中的事实直接提交为业务 truth。

## 7. Canonical journal 与保留预算

canonical journal 是本地 append-only evidence projection，不是主业务 queue，也不替代组件自己的 authority store。

### 7.1 写入与降级

1. main 为 operation/trace 建立 canonical context；producer 只能在该 context 下生成 event-name registry 允许的 facts，不能直接访问 canonical store。
2. main diagnostics service 先验证 schema、size、identity、authority ref 和 redaction，再以唯一 sink 身份 durable append 并分配 `journal_seq`。
3. sink durable append 成功后只返回 `JournalAppendAck`（schema `seektalent.journal-append-ack/v1`，含 event ID、journal sequence、canonical hash）；它不是 `MachineCapabilityReceipt/StartupReceipt/OperationEvidence`，也不是 #325 `AcceptedAck`。pipe/HTTP 已发送不等于 journal 已接受，sidecar 自己的 operation journal sequence 也不能冒充 `journal_seq`。
4. sink 不可用不能阻止 main 记录业务 durable truth。main projection outbox 保持 pending；恢复后 diagnostics service 可生成 `diagnostic_gap_detected` 并在 `OperationEvidence` 标记 gap。
5. journal 自身写失败时，只有 main diagnostics service 可写固定大小的 local emergency ring：最多 128 records、每条 2 KiB，仅含 event identity/component/reason/time。producer 和 raw stderr 都不是 fallback sink。
6. main diagnostics service 唯一负责 journal migration、compaction、retention 和 support-export read snapshot；schema ahead、corrupt、read-only、full 必须产生不同 capability/failure code。

### 7.2 v1 budgets

| 对象 | 上限/保留 |
|---|---|
| 单 canonical event | 16 KiB serialized JSON |
| 单 Failure Envelope/canonical receipt/`OperationEvidence` | 32 KiB serialized JSON |
| journal event rows | 50,000 |
| journal 总大小 | 64 MiB（含 WAL/SHM 计入 budget） |
| journal 时间窗 | 14 天；active operation 及其最近 evidence 不按 TTL 删除 |
| emergency ring | 128 × 2 KiB |
| 已导出 support bundle | 默认 7 天后进入现有 local-storage cleanup；用户可立即删除 |

行数、大小、时间任一先到即触发 deterministic compaction。保留优先级从高到低：active operation；Failure Envelope 与 canonical receipts；每个 terminal operation 最新 `OperationEvidence`；其 causal spine；普通 info/debug event。任何删减都增加 `dropped_by_class` 和 `oldest_retained_at`，禁止静默丢弃。核心 evidence 仍无法满足 64 MiB 时停止接受新普通 event、保留 failure/capability facts，并报告 `diagnostic_budget_exhausted`；不得删除 main business truth。

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
  redaction-report.json
  checksums.sha256
```

`manifest.json` 只含 bundle/schema/redaction version、created time、managed-copy expiry、release reference、选择的 operation/time window、文件 rows/bytes/hash、truncation、missing evidence、exporter build ref 和 deletion instructions。`checksums.sha256` 覆盖其余固定文件；文件按稳定 key 排序。

deterministic 指同一个 evidence snapshot 的 canonical projection、record order、per-file bytes/hash 与 checksums 稳定。`bundle_id`、`created_at`、managed-copy expiry、archive filename/compression metadata 等 volatile metadata 不要求整个 archive byte-identical。

### 8.2 allowlist 与禁止项

允许：本文件各 canonical schema 的 allowlisted projection、component/version/protocol/capability、safe state transition/reason code、opaque/HMAC refs、size/count bucket、DB schema/integrity、redaction summary。

默认明确禁止：

- cookies、Authorization、token、password、secret、API key、handoff/control key、browser debug endpoint；
- 原始 provider/LLM request 或 response、prompt、JD、简历、候选人姓名/公司/学校、查询词；
- DOM、HTML、inner/visible text、截图、download、clipboard；
- 完整 URL/query string、IP、SSID、proxy URL、certificate subject；
- hostname、username、绝对 home/workspace/Chrome profile path；
- `observed_provider_account_subject`、账号邮箱/用户名/显示名或其明文变体；只允许 opaque/HMAC account ref；
- raw SQLite/DB/WAL/SHM、raw stdout/stderr/log、crash dump、memory dump；
- artifact/replay/eval/debug 内容，即使 artifact metadata 标记 `support_bundle_only` 也必须先通过独立 projection。

每个 event-name/receipt schema 都有 golden forbidden-value corpus 和生成式敏感 key/value scan。corpus 必须包含当前列名 `observed_provider_account_subject`、代表性账号 subject 和大小写/嵌套变体，negative projection test 证明它们均不能进入 canonical event、receipt 或 bundle。命中禁止内容时该 record fail-closed，不进入 bundle；`redaction-report.json` 只记录 schema、field path token、rule ID 和 count，不保存命中的原值。

### 8.3 preview、大小与导出

- preview 显示时间窗、operation count、failure clusters、文件/row/bytes、被排除类别、redaction count、truncation/missing evidence、managed-copy expiry 和 deletion instructions；用户可以取消或缩小范围。
- 压缩前最大 100 MiB、压缩包最大 25 MiB。导出优先保留 receipts、failures、`OperationEvidence` 和 causal spine，再按时间从近到远纳入普通 events；所有裁剪写入 manifest。
- 核心 evidence 本身超过任一上限时导出 fail-closed，返回 `support_bundle_core_evidence_oversize`，建议缩小 operation/time window；禁止生成未声明的不完整包。
- 导出使用一致性 read snapshot，不暂停当前 operation；临时文件与最终包同目录原子 rename，失败后清理 partial archive。
- 应用管理目录中的 managed copy 按 7 天 TTL 清理；用户通过系统文件选择器保存/复制到应用管理目录之外的副本不受自动删除。manifest/preview 必须明确这一点，并提供用户手动删除副本的 instructions。
- re-import 把 archive 视为不可信输入：解压前校验 compressed/uncompressed declared size、文件数量、manifest/schema/checksum；拒绝 absolute path、`..` traversal、symlink/hardlink、duplicate/extra file、zip bomb 和超过 25 MiB compressed/100 MiB uncompressed 的包。任何 gate 失败都不得落盘解压 partial content。
- “debug/full-local” 是另一个用户显式开启的本地模式，不得伪装成默认 support bundle，也不得自动上传。

## 9. Privacy-safe failure cluster 与 reproduction recipe

### 9.1 两级指纹

`failure_cluster_id` 是以下 canonical tuple 的 SHA-256：

```text
failure-envelope schema major
redaction policy major
component
phase
domain
failure_kind
reason_code
cause_ref.code bucket
last_safe_boundary
capability gap codes
protocol compatibility bucket
```

它排除 release、随机 ID、时间、用户/机器/账号/path 和业务内容，用于跨机器聚类。`release_occurrence_key` 再加入 `release_manifest_ref + artifact platform/arch + OS/Chrome major bucket`，用于确认某一产品包回归。cluster 原始 tuple 同时保存在 bundle，避免 hash 无法解释。

### 9.2 Derived reproduction recipe

`reproduction-recipe.json` 不属于用户原始 support bundle 的固定或可选内容。它是在安全 re-import、cluster 和 support triage 后生成的 derived artifact，schema ID 为 `seektalent.reproduction-recipe/v1`，只含：

- failure cluster tuple、release/artifact ref、OS/arch/Chrome major/channel；
- profile mode、bridge component refs、capability gap codes；
- network/storage posture flags与 SQLite version/journal mode；
- operation kind、boundary facts、必要的 startup sequence；
- synthetic scenario ID 或经过批准的 real-site canary ID；
- expected Failure Envelope、`OperationEvidence` 和 invariant assertions。

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
3. supported/released posture 上出现一个 user-visible unknown cluster，就立即阻塞对应 release candidate，不是普通 backlog。诊断 `cause_ref.certainty` 可以保持 `unknown`，但 release gate 前必须给出 typed `domain + reason_code`，或明确的 unsupported/not-shipped capability classification。
4. 两个独立 installation 出现同一 cluster 会提升复现优先级，但不是 release blocking 的起点；不能以“尚未两例”或“开发机正常”放行 user-visible unknown taxonomy。
5. 单个 bundle 立即进入 triage 与 release-blocker audit；缺失历史五位用户日志不会阻塞本基线，也不能被猜测成某个原因。
6. 修复完成的 cluster 必须有 deterministic case、预期 envelope/evidence 和至少一个自动或 release gate；只修代码、不固化证据合同不算关闭。

## 10. Fault-injection matrix

每个场景都要断言四类结果：main business truth、canonical event/envelope、重启/恢复后的 integrity、用户可见 outcome/disposition reference。后者的枚举与映射来自 #324。

| ID | 注入点 | 必须观察的 evidence | 最低 gate |
|---|---|---|---|
| F01 | main 在 durable acceptance 前退出 | 无 accepted record；startup/process envelope；无虚假 operation accepted | PR process test |
| F02 | main 在 accepted commit 后、dispatch 前退出 | accepted boundary ref；重启可恢复同 operation/next attempt；无丢 job | nightly |
| F03 | dispatch intent durable 后 sidecar 未收到即退出 | dispatch boundary、attempt identity、`cause_ref.certainty=unknown`/pipe-close code；无假完成 | nightly |
| F04 | sidecar 收到后、#325 `AcceptedAck` durable 前被 kill | AcceptedAck 缺失、side-effect `unknown`、process-exit `cause_ref` | nightly |
| F05 | side effect observed 后、result persist 前 kill | observed boundary、result unknown、禁止诊断层自动重试 | release fault gate |
| F06 | result durable 后、main commit 前 kill | result ref 可恢复、main commit not observed、无重复业务提交 | nightly |
| F07 | main commit 后、cleanup 前 kill | committed fact 保留；cleanup pending/failed 单独 envelope | nightly |
| F08 | worker/sidecar pipe EOF、timeout、malformed/oversize frame | protocol reason、last good component seq、missing `cause_ref` | PR + nightly |
| F09 | wrong implementation/build/protocol/capability | `StartupReceipt` `not_ready` + 现有 typed bridge reason | PR |
| F12-R | stale `runtime_attempt_fence_ref` 写 event/result | `domain=runtime/failure_kind=authority_rejected`、rejected counter；可信 timeline 不含写入 | PR blocker |
| F12-P | stale `profile_binding_generation` | `domain=browser/failure_kind=authority_rejected`；不启动 provider action | PR blocker |
| F12-B | stale `browser_control_fence_ref` | `opencli_stale_control_fence`；用户 tab 无 mutation | PR + real browser |
| F12-C | `browser_control_scope_id` mismatch | correlation/protocol failure；不得分类为 authority failure，不接受 command | PR + real browser |
| F13 | daemon port 已被其他进程占用/daemon stale | endpoint ownership receipt、process/startup reason，不 kill 非 owned 进程 | nightly clean machine |
| F14 | extension missing/disconnected/wrong ID | capability/startup gap，不把 daemon alive 当 ready | nightly clean machine |
| F15 | MV3 service worker 在 command 前/中/后显式终止 | generation change、durable accept/complete boundary、未知项不推断 | nightly browser |
| F16 | Chrome/profile 未启动、locked、被删除或账号 binding 变化 | capability/profile generation reason；无旧账号 session 复用 | release matrix |
| F17 | host tab ambiguous/owned tab missing/user closes tab | typed browser reason、scope ownership、无用户 tab mutation | nightly browser |
| F18 | selector/DOM drift/page not ready/risk control | provider/browser domain 分离；safe page capability facts；无 DOM export | nightly synthetic + real canary |
| F19 | DNS/offline/system proxy/custom CA/TLS failure | network posture + safe OS/network code；不泄露 endpoint/证书 | release matrix |
| F20 | SQLite lock/busy timeout | extended result code、logical DB、transaction boundary、恢复 integrity | PR file test |
| F21 | SQLite full/read-only/cantopen/I/O error，第 N 次 I/O 单次及持续失败 | 不同 `cause_ref.code`、无 half commit、恢复后 integrity | nightly storage |
| F22 | SQLite corruption/schema ahead/migration 中断 | capability unsupported/indeterminate、backup/integrity refs、fail closed | release upgrade gate |
| F23 | journal 超 event/row/byte budget | oversize/budget reason、deterministic compaction counters、业务 truth 不受影响 | PR |
| F24 | canonical sink/ring 同时不可用 | `first_failure_event_id=null`、diagnostic gap reason/counter、observed boundary ref、`OperationEvidence` missing refs；无伪造 anchor/raw stderr fallback | nightly |
| F25 | LLM request timeout/cancel/process interruption，或 response schema validation 失败 | `domain=runtime/failure_kind=model_failure`；只有 structured-output parse failure 可记录现有 bounded retry evidence，其他失败不得套用该例外 | PR + nightly |
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

三行是 v1 目标覆盖清单，不自动构成未经产品决策的 simultaneous release train。每个由 #326 Release Manifest 声明为 supported/released 的平台，都必须用该平台 exact artifact 独立通过完整 matrix；未产出或未通过的平台只能标记 `unsupported/not_shipped`，不能借其他平台结果假装 PASS。只有产品明确承诺三平台同车时，任一平台失败才阻塞整个 release train。

| Target platform | 当前 code-truth status | 若 declared supported/released |
|---|---|---|
| Windows 11 x64 | 无 exact product artifact；`unsupported/not_shipped` | exact artifact 的 clean install、N-1 upgrade、rollback、offline/first start、real Liepin canary 全部 required |
| macOS arm64 | 无 exact product artifact；`unsupported/not_shipped` | 同上，且使用该 native artifact/OS runner |
| macOS x86_64 | 只有历史/不完整 builder path；0.7.49 缺 matching constraints，当前 `unsupported/not_shipped` | 补齐 #326 exact artifact 后执行完整 matrix，不能把旧 0.7.46/0.7.47 evidence 当 0.7.49 PASS |

当前主线没有可据此判定 production-releaseable 的三平台 exact artifact evidence。手动 macOS x86_64 workflow 的存在也不是当前 0.7.49 artifact PASS。

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

上述 posture cases 对每个 declared supported/released platform 独立执行。未声明的平台可以保持 target backlog，但必须在 preflight/receipt 中明确 `unsupported/not_shipped`。

real canary 只保存 operation/evidence IDs、safe counts、reason codes 和 opaque account ref；不保存页面内容、候选人或账号凭据。PASS 必须证明：该平台 exact artifact + production extension + controlled account 完成一次非敏感查询，产生 source-executed evidence，并得到允许的成功 outcome：`succeeded_with_results`，或仅在预先设计的零结果 canary/fixture 下为 `succeeded_empty`。完整记录一次 typed failure 只证明诊断有效，不算 canary PASS。dedicated profile 的 spike evidence 是生产 gate，是否最终采用 dedicated profile 由后续产品决策决定。

## 12. Gate 分层

| Gate | 运行时机 | 内容 | 阻断条件 |
|---|---|---|---|
| PR deterministic | 每个相关实现 PR；可跨平台模拟 | schema/registry validation、identity/cause DAG、reason mapping、redaction golden/property、size/budget、deterministic bundle、stale fence、SQLite file failpoints、synthetic protocol faults | 任一 invariant、privacy scan 或 deterministic case 失败 |
| nightly integration | 每个目标 native OS/build candidate | real process kill/restart、pipe faults、SQLite I/O/crash、MV3 termination、synthetic browser contract、journal degradation | 目标平台未运行、evidence 缺失、half commit、authority violation、user-visible unknown taxonomy |
| release clean-machine | 候选 release；使用 #326 exact artifacts | 每个 declared platform 独立执行 install/upgrade/rollback、posture matrix、real Liepin canary、dedicated-profile spike、support bundle preview/export/re-import | 对应平台任一 matrix/canary/signed artifact/evidence 不完整则该平台只能 `unsupported/not_shipped`；只有 simultaneous-train 承诺才全局阻塞 |
| manual incident regression | 导入用户 bundle 后 | checksum/schema/redaction 校验、cluster、recipe、复现、固定 regression | blocker cluster 无 regression case 或无明确 unsupported classification |

PR gate 必须保持快且 deterministic；不得把真实账号 secret 放入普通 PR CI。单一 controlled host 的模拟不能冒充 native platform evidence。real Liepin canary 在隔离 release environment 运行，结果只输出本文件 allowlist evidence。

当前 CI code truth：`python-quality.yml` 的 pull-request `paths` 不含 `docs/**`，所以本 docs-only PR 没有该自动 check；governance、workbench-contract、macOS Intel build 都是 `workflow_dispatch`。本文件的本地脚本结果不能写成 GitHub automatic status。后续实现 PR 必须按改动风险把对应 deterministic gate 配成 required check；nightly/release gate 则在每个目标 native OS/build candidate 上保存 evidence。

## 13. 交付顺序与验收

### 13.1 最小落地顺序

后续实现必须拆成多个独立、可回滚、各自有 gate 的 PR；不得用一张 PR 同时修改 schema、storage、runtime、UI、browser、build 和 release：

1. **Schema PR**：只实现 canonical models/registry/redaction/domain mapping/golden fixtures；不接 runtime writes。
2. **Storage PR**：只实现 main diagnostics service、projection outbox ingest、journal/`JournalAppendAck`、compaction、retention、emergency ring；证明 sink loss 不影响业务 commit。
3. **Producer PRs**：按 runtime、Liepin/source、browser 分开接 adapter；每个 PR 只发 allowlisted facts，并有 stale authority/correlation negative tests。
4. **Doctor/export PR**：实现三类 canonical object、preview/manual export、untrusted re-import 和 managed-copy lifecycle；不改 browser execution。
5. **Fault-harness PRs**：先 process/storage，再 browser/MV3；建立 PR deterministic 与各 native OS nightly gates。
6. **Release integration PR**：只消费 #326 exact artifact refs，建立逐 declared platform clean-machine/canary verdict；不反向扩大发布平台承诺。

#324 disposition/outcome 与 #325 reconciliation 先落 main-owned durable truth，再由 adapter 投影；#326 只提供 release refs。超大 runtime/browser/provider 文件不因 #322 被整体重写。

### 13.2 本契约文档 Definition of Done

- Failure Envelope 保留 reliability contract §8 的字面 canonical fields：`run_id/domain/cause_ref/current_outcome/user_action/support_action`，并证明没有 retry permission；
- `run_id/operation_id/attempt_no` 在 canonical event、Failure Envelope、`OperationEvidence` 中语义一致，当前 `runtime_run_id` 只在 adapter mapping 说明中出现；
- `domain` 是冻结 enum 的 superset，细分类进入 bounded `failure_kind/detail`，并有 exhaustive mapping gate；
- main diagnostics service 的唯一 sink/store/issuer/export ownership 与单向 business-fact projection 已冻结；
- browser scope correlation 与三类 authority refs 已分离；
- `MachineCapabilityReceipt/StartupReceipt/OperationEvidence/JournalAppendAck` 命名及 #324/#325/#326 边界闭合；
- current-code mapping 明确 0.7.49 packaging gap、docs-only CI gap 和明文 provider account subject 风险；
- local-only privacy、budgets、support archive safety、fault matrix 和逐平台 release policy 已冻结；
- #324/#325 交叉复核确认 diagnostics 不拥有 RetryPosture、ProductOutcome、Source Port state machine 或 wire DTO。

### 13.3 后续实现 program Definition of Done

- canonical event、Failure Envelope、三类 canonical object 均有 versioned schema、validation 和 golden examples；
- support bundle 默认 local-only、allowlist、可 preview/manual export，untrusted re-import fail closed，forbidden corpus scan 为零泄漏；
- journal 与 bundle 的 retention/size/truncation 行为 deterministic；
- fault matrix 的每个 scenario 有 owner、injection seam、expected evidence 和 gate；
- 每个 declared supported/released platform 在 #326 exact artifact 上生成可校验 canonical evidence；未通过平台为 `unsupported/not_shipped`；
- real Liepin canary 满足本文件 PASS 定义，不能被 fake/synthetic fixture 或“完整记录一次失败”替代；
- unknown user-visible taxonomy 在 release gate 前完成 typed classification；
- privacy-safe cluster 可生成无业务正文的 derived reproduction recipe，blocker 修复后留下 release-blocking regression；
- 以上由多个 gated PR 交付，不以本契约文档合并冒充实现完成。

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
- `chrome.runtime.onInstalled`、`activate`、profile 启动时的 `chrome.runtime.onStartup` 与 worker 被事件唤醒不是同一个生命周期事实；其中 profile 启动不会触发 service-worker lifecycle events。`StartupReceipt` 必须分别记录 extension install/update、browser/profile startup 与 worker generation，不能用单一 `started=true` 混为一谈。[Chrome：installation 与 extension startup](https://developer.chrome.com/docs/extensions/develop/concepts/service-workers/lifecycle#installation)
- `chrome.storage.session` 可跨 service-worker 休眠保留内存状态，但在扩展 disable/reload/update 或浏览器重启时清空；`chrome.storage.local` 持续到扩展被移除。两者可以分别提供“本次浏览器/扩展会话”和“跨重启安装实例”的证据边界，但写入是异步且有容量限制，不能把成功调用前的内存状态当作已持久化回执。[Chrome Storage API：storage areas](https://developer.chrome.com/docs/extensions/reference/api/storage#storage-areas)
- Chrome Storage API 默认可能把部分存储区暴露给 content scripts，并提供 `setAccessLevel()` 收紧访问。extension lifecycle evidence 应只保存有界元数据和不透明关联 ID，显式限制 content-script 访问；Cookie、页面 DOM、账号、简历、JD、聊天内容和完整 URL 不属于该证据层。[Chrome Storage API](https://developer.chrome.com/docs/extensions/reference/api/storage)
