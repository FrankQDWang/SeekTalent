# External Execution Plane v1 — Source Execution Port wire contract

状态：**Wayfinder #325 Draft decision contract；docs-only，未实现**

Tracks: [#325](https://github.com/FrankQDWang/SeekTalent/issues/325)

主线输入：

- [Reliability Contract](./external-execution-plane-v1-reliability-contract.md)：v1 rollback-journal 决议、at-least-once + reconcile-before-retry、完整 artifact 与 release gates；
- [Runtime Topology](./external-execution-plane-v1-runtime-topology.md)：唯一 process/browser lifecycle owner、IPC 位置与 T1/T2/T3 hard cut；
- [Task Semantics](./external-execution-plane-v1-task-semantics.md)：main-owned source-operation ledger、`SourceOperationDisposition`、`RetryPosture`、`ProductOutcome`、fencing 与 reconcile-first；
- [Diagnostics and Fault Injection](./external-execution-plane-v1-diagnostics-fault-injection.md)：canonical identity、Failure Envelope、receipts、`OperationEvidence`、privacy 与 fault gates；
- 当前 `code/schema/tests/build/release scripts`。本文与代码事实冲突时，先修本文；本文不把目标状态写成已实现状态。

## 1. 决策摘要

External Execution Plane v1 在 main 内部 `RuntimeSourceLane*` application/domain boundary **下方**、Liepin worker/provider boundary **上方**冻结一个 local child-process port：

```text
main domain/runtime
  -> RuntimeSourceLaneRequest / RuntimeSourceLaneResult      (进程内，保留)
  -> Liepin provider adapter                                (进程内映射)
  -> Source Execution Port client                           (本文 wire boundary)
  == authenticated length-prefixed JSON over inherited stdio ==
  -> Liepin Execution Sidecar                               (唯一 browser owner)
  -> WTSCLI / production extension / selected Chrome profile
```

冻结结论：

1. `RuntimeSourceLanePlan/Request/Result/Event`、`RequirementSheet`、callback、候选领域对象和 provider object **永不跨 IPC**。
2. Wire 只有 operation-specific、versioned、pure-data、bounded DTO；v1 只有 `verify_session/search/cards/details/continuation/cleanup`，没有 arbitrary command。
3. Main `runtime_control` 唯一拥有 run、source operation、attempt、checkpoint、candidate truth、retry、reconcile interpretation 和 product outcome。Sidecar journal 只回答 browser effect 可能发生了什么。
4. 每个提交请求携带 main-minted `runtime_attempt_fence_token`；main port client 在发帧前验证它。`profile_binding_generation` 在 sidecar 验证。Controller-only `control_key + browser_control_fence_token` 只存在于 sidecar ↔ WTSCLI/extension command boundary。
5. `browser_control_scope_id` 仅是 correlation。它不能授权 main、sidecar 或 extension 做任何事。
6. Sidecar 在 durable `accepted` 前不 ack，在 durable `dispatch_intent` 前不产生 browser side effect，在 durable `observed` 前不发 terminal observation，在 main acknowledgement 后才写 `reconciled`。
7. EOF、timeout、malformed frame 或 sidecar exit 只是 transport fact，不是 operation failure。Main 必须 query/reconcile，之后才可应用 #324 已冻结的 posture。
8. #325 只传输 #322 canonical refs 与 #324 main-owned semantic refs；不复制或修改 Failure Envelope、receipt、support、retry 或 outcome schema。
9. T2 可以交付 contract、fake transport/journal 和不可达 sidecar，但 production routing 只在 T3 对所有 live Liepin operation 一次 hard cut；同 release 不保留旧路径 fallback。
10. V1 sidecar command journal 使用 SQLite rollback journal + `synchronous=FULL`。WAL 不是 v1 决议；只能在固定 SQLite build、WAL-reset 修复版本、三平台真实文件/多连接/crash/checkpoint matrix 和显式 ADR 全部通过后迁移。

## 2. 范围与非范围

### 2.1 本文拥有

- main ↔ sidecar handshake、framing、authentication、sequence/replay protection 与 compatibility；
- 六种 operation 的 request/result pure-data DTO；
- `AcceptedAck/Progress/Result/Failure/Query/Status/Cancel/Reconcile/RetentionRelease/Readiness/Drain` 消息；
- request identity、deadline、idempotency 与 authority transport；
- sidecar command journal 的 durable ordering、sync、generation lookup、retention 与 fail-closed 行为；
- deterministic fake transport/journal contract tests；
- T1/T2/T3 接入和旧路径删除门禁。

### 2.2 明确不拥有

- #322 的 canonical event、Failure Envelope、receipt、`OperationEvidence`、`JournalAppendAck` 或 support bundle schema；
- #324 的 `SourceOperationDisposition`、`RetryPosture`、`ProductOutcome`、run FSM、retry/reconcile permission、checkpoint 或 main commit；
- #326 的 Release Manifest、签名、platform artifact、installer、upgrade 或 rollback implementation；
- sidecar/WTSCLI/extension 的实现；
- generic workflow engine、远程 service、broker、telemetry backend 或第二个业务数据库；
- 全仓目录重构、大文件清理或无关技术债；
- 五位用户不存在的历史日志。它们不是 contract、测试或 release 前置条件。

## 3. Current code truth

本文基于 `main@beb89743d4c772e54bde866eeacc1c65c2864a0c`。以下均是当前事实，不是目标实现：

| 区域 | 当前 code truth | 本文决定 |
|---|---|---|
| Main application seam | `source_contracts/runtime_lanes.py` 的 `RuntimeSourceLaneRequest` 含 `RequirementSheet`、`source_context: object` 和 `ProgressCallback`；result 含 `ResumeCandidate`、`NormalizedResume`、provider snapshot object 和 private continuation | 保留为进程内 application boundary；provider adapter 显式映射到本文 DTO，禁止直接序列化 |
| Liepin worker seam | `providers/liepin/client.py` 的 `LiepinWorkerClient` 已有 readiness、search、session、details 等 Protocol；`ExternalHttpLiepinWorkerClient` 有 `/internal/*` bearer HTTP，`LiepinOpenCliWorkerClient` 直接调用 browser retriever | Source Port 放在该 worker/provider seam；production sidecar 替换 current HTTP/OpenCLI browser composition，不把旧 endpoint 当 v1 protocol |
| Production operation | 当前 OpenCLI path 实际覆盖 session probe、detail-backed resume search、card search、detail open、first-page continuation expand/discard 和 browser scope cleanup | v1 对应冻结 `verify_session/search/cards/details/continuation/cleanup` 六种独立 contract |
| Browser construction | `build_liepin_opencli_worker_client()` 在 main 调 `ensure_opencli_runtime()`、连接 daemon、创建 lifecycle/automation/site adapter | T3 全部迁入 sidecar；main 不再 import 或构造 browser runtime |
| Current daemon transport | 固定 `127.0.0.1:19825`、`X-OpenCLI: 1`，response 上限 1 MiB；连接异常可触发 `daemon restart` | 不复用为 main ↔ sidecar 协议；T3 删除 main direct path，sidecar 只监督 owned foreground child |
| Browser command authority | `BrowserControlScope` 含 `scope_id/control_key/fence_token`；automation 把 key/token 放进每条 daemon command | 保留并迁入 sidecar；wire 只带独立 `browser_control_scope_id` correlation，不带 controller secret |
| Browser lifecycle mirror | `browser_control.sqlite3` registry 明确为 fail-open mirror，`synchronous=NORMAL`，extension state authoritative | 可保留为 cleanup migration input，但不能冒充本文 fail-closed command journal |
| Continuation | `LiepinFirstPageContinuation` 把 query、detail URL、candidate ref 和 state 保存在 protected artifact，main 当前携带 `ProviderSearchContinuation.opaque_ref` | 私有 continuation body/path 留在 sidecar；wire 只见随机、无路径、无 authority 的 `continuation_ref` |
| Detail idempotency | 当前 detail request item 有 request/attempt/idempotency/approval/candidate identity，detail ledger区分 browser attempt、opened、terminal failed | 保留事实语义并映射到 `DetailsRequestItemV1`；不把 ledger object 或 URL 序列化 |
| Runtime fencing | 当前 lease 以 `(runtime_run_id, executor_id, attempt_no)` guard；没有 `runtime_attempt_fence_token` | 本文 wire 依赖 #324 目标 token；在 token/ledger 实现前 production port 不得启用 |
| Source-operation truth | 当前 `runtime_control` 无 main-owned source-operation ledger/outbox，也没有本文 operation phase | 必须先按 #324 slice 建 ledger/outbox；sidecar journal 不替代它 |
| Recovery | 当前 newest-to-oldest checkpoint scan 可跳过坏的新 checkpoint，Workbench runner 用 `resume_recoverable=False` | T1 必须先满足 #324 exact-newest/continuous-worker gate；本文不自行修复 |
| Packaging | version `0.7.49`；只有 macOS x86_64 offline workflow，且只有 0.7.46/0.7.47 constraints；无 current exact three-platform artifact | #326 必须把 exact main/sidecar/WTSCLI/bridge 打成同一 release；本文不把现有 wheel/workflow写成 PASS |
| CI | `python-quality.yml` PR paths 不含 `docs/**`；governance、workbench-contract、Intel build 是 manual `workflow_dispatch` | 本 docs-only PR 运行本地 contract/governance gates；不得称为远端 automatic CI PASS |

## 4. Ownership、authority 与 semantic refs

### 4.1 唯一 owner

| 事实或 authority | 唯一 owner | Source Port 行为 |
|---|---|---|
| run/source-operation ledger、checkpoint、candidate truth | main `runtime_control` | 请求/返回仅携带 identity、pure-data observation 与 main commit refs |
| `SourceOperationDisposition`、`RetryPosture`、`ProductOutcome` | main/#324 | 只携带 main-authored stable refs；sidecar 不产生、修改或解释值 |
| canonical event/Failure Envelope/receipts/`OperationEvidence` | main diagnostics service/#322 | 只携带 canonical refs 或 allowlisted producer facts；不复制 schema |
| sidecar lifecycle、browser resources、private continuation/result spool | sidecar | main 只能 query/reconcile，不获得 page/profile implementation object |
| `runtime_attempt_fence_token` | main/#324 | raw bearer 只在 authenticated request 内出现；sidecar 不持久化、不返回 |
| `runtime_attempt_fence_ref` | main 按本文 domain-separated digest 规则生成 | 可持久化的非权威引用；不是 bearer，不能授权 dispatch 或 main commit |
| `profile_binding_generation` | sidecar profile binding owner | request 必须精确匹配；stale generation 在 dispatch 前拒绝 |
| `control_key + browser_control_fence_token` | sidecar ↔ WTSCLI/extension controller | 永不进入 Source Port request/result/journal/log/evidence |
| `browser_control_scope_id` | main 生成 correlation ID，sidecar绑定 observation | 不是 authority；scope mismatch 是 protocol/correlation failure |

### 4.2 三类 reference 只引用既有契约

Wire 可携带以下 opaque ref object；字段语义来自 owner 文档，本文不创建第二份对象。Issuer 和首次出现规则是 closed contract：

| Ref | 唯一 issuer | Sidecar 首次 operation observation | 后续 wire 规则 |
|---|---|---|---|
| `FailureEnvelopeRef` | main #322 diagnostics service；`failure_id + revision` 可附 canonical hash | **必须 null** | 只能在 main 已按 #322/#324 commit 后下发；sidecar 只 echo exact ref |
| `OperationEvidenceRef` | main #322 diagnostics service；`operation_evidence_id + revision + canonical_hash` | **必须 null** | 只能 echo main 已签发并下发的 exact ref |
| `ReceiptRef` | main #322 diagnostics service；receipt ID/revision/canonical hash/kind | 只可 echo MainHello/readiness/submit 中已提供的 Machine Capability/Startup/Component receipt ref；否则 null/空集 | 始终 exact echo；component/installer/sidecar 不可签发 canonical receipt |
| `MainSemanticRefs` | main #324 source-operation/run transaction；disposition/retry posture/product outcome refs | **必须全 null** | 只能在 main commit 后由 reconcile ack 下发并 exact echo |

Sidecar永远不签发 Failure Envelope、`OperationEvidence`、receipt 或 main semantic ref。它只产生 allowlisted producer facts。Main 先把 observation 通过 expected-revision transaction 提交为 durable business truth，才可在内部 provider adapter 关联 refs，或在后续 authenticated reconcile acknowledgement 中发送 refs。Sidecar最多原样保存/回显 main 已签发的 ref；它不能从 failure、timeout、HTTP code、candidate count 或 journal state计算 enum。

## 5. Wire envelope 与 bounded types

### 5.1 每个 post-handshake frame 的固定 envelope

```text
protocol_name        = "seektalent.source-execution-port"
protocol_major       = 1
protocol_minor       = negotiated minor
session_id           = random opaque ID, 1..96 chars
direction_seq        = uint64, each direction starts at 1 and increments by exactly 1
message_id           = random opaque ID, 1..96 chars; retransmission keeps it
reply_to             = nullable message_id
message_type         = closed registry token
correlation_id       = nullable random opaque ID, 1..96 chars
payload              = one registered DTO object
auth_tag             = 64 lowercase hex HMAC-SHA256
```

Unknown top-level fields、duplicate JSON keys、NaN/Infinity、non-integer JSON numbers for integer fields、unregistered message type 或 extra DTO fields 全部拒绝。Pydantic `extra="forbid"` 是实现要求，不是把 domain model复制到 wire 的理由。

### 5.2 `OperationIdentityV1`

每个 submit request 必须携带：

| 字段 | 冻结约束 |
|---|---|
| `contract_version` | operation-specific schema ID；v1 exact match，不做猜测降级 |
| `run_id` | main-owned opaque ID，1..96 |
| `operation_id` | 同 run 内 stable logical source operation ID，1..96 |
| `attempt_no` | #324 main executor attempt，positive int；不是 sidecar/HTTP/browser retry count |
| `source` | v1 固定 `liepin` |
| `request_hash` | 64 lowercase hex SHA-256；见 §8 |
| `idempotency_key` | opaque 1..128；不得含账号、query、path、token |
| `correlation_id` | #322 语义的随机 opaque correlation，1..96 |
| `accepted_requirement_revision_id` | main 接受该 operation 时的 active requirement revision，1..96；`verify_session/cleanup` 仍绑定 run 接受时的 revision，不读取其正文 |
| `runtime_attempt_fence_token` | opaque high-entropy bearer，32..256 chars；只在 request 内，禁止 log/journal/result/evidence |
| `runtime_attempt_fence_ref` | 64 lowercase hex；见 §5.3，非 authority |
| `profile_binding_generation` | positive uint64 |
| `browser_control_scope_id` | random opaque 1..96，仅 correlation |
| `deadline` | `{value, clock, unit}`，定义见 §5.4 |
| `expected_source_operation_ledger_revision` | main #324 source-operation ledger 的 positive uint64 CAS revision |
| `expected_reconciliation_revision` | main #324 已提交 reconciliation revision；initial 为 `0`，非负 uint64 |
| `dispatch_intent` | §5.5 closed discriminated main-owned dispatch authorization |

Main 在 durable source-operation accepted/outbox transaction 之前不能发送；port client 在写 frame 前用同一个 main transaction boundary/guard 重新验证 run、attempt、raw token/ref、operation、request hash、dispatch authorization 和 expected revisions。Sidecar还要拒绝低于其已观察 run attempt high-watermark 的 request，以及同 scoped attempt/operation 下 raw token/ref 不一致的 request。它只保存非权威 ref。

### 5.3 Runtime fence ref

`runtime_attempt_fence_ref` 由 main 与 raw bearer 同时生成，sidecar 重算并 constant-time 比较后只持久化 ref：

```text
runtime_attempt_fence_ref = hex(SHA-256(
  LP("seektalent-runtime-attempt-fence-ref-v1") ||
  LP(UTF8(raw_runtime_attempt_fence_token)) ||
  LP(UTF8(run_id)) || LP(UTF8(operation_id)) || uint64_be(attempt_no) ||
  LP(hex_decode(request_hash)) ||
  uint64_be(expected_source_operation_ledger_revision) ||
  uint64_be(expected_reconciliation_revision)
))
```

`LP(x) = uint32_be(byte_length(x)) || x`。Token 必须至少 256-bit CSPRNG entropy；domain label、长度前缀与 exact operation/revision scope 防止拼接或跨 scope 复用。Ref 在没有 raw high-entropy bearer 时不可逆，不需要 sidecar 额外 key，因此可跨 sidecar generation 稳定比较，也不引入第二个 secret。Ref 不是 bearer，不能授权 dispatch、retry 或 main mutation；raw token 仍只在 authenticated submit 内出现，不进 journal/result/log/evidence。

### 5.4 Deadline

V1 唯一合法形状：

```json
{"value": 120000, "clock": "relative_monotonic", "unit": "milliseconds"}
```

- `value` 是 main 在 frame authentication 前采样的**剩余时长**，integer `1..900000`；不是 wall-clock timestamp。
- Sidecar 完成 frame/auth/schema/authority validation 后，用自己的 monotonic clock锚定 `local_deadline = now + value`。
- Queue/dispatch/browser/result-persist 都消费同一 budget；ack/progress 不延长 deadline。
- 已 `accepted` 或 in-flight 的 same-key replay 只返回 ack/status/result；sidecar 忽略 replay 中更大的 `deadline.value`，不重新锚定、不延长已有 operation。只有 §5.5 `safe_retry` 的新 durable dispatch intent 可以携带新 deadline budget。
- Wall clock、`occurred_at`、HTTP timeout 或 progress time 不能成为 authority。
- Deadline 在 durable `dispatch_intent` 前耗尽：journal 保留 accepted/no-dispatch fact；不得产生 side effect。
- Deadline 在 `dispatch_intent` 后耗尽：transport 可返回 timeout observation，但 main posture 保持 `reconcile_first`，直到 journal/extension facts得出结论。

### 5.5 Closed dispatch intent

Submit 必须携带 `DispatchIntentV1`，且 `kind` 只允许：

| `kind` | 必填 main-owned durable facts | Sidecar 行为 |
|---|---|---|
| `initial` | 新 `dispatch_intent_id/revision/digest`、`source_operation_acceptance_ref`、accepted ledger revision；`safe_retry_commit_ref=null`、reconciliation revision `0` | 可在 accepted durable 后创建首个 dispatch intent |
| `outbox_redelivery` | 与已提交 `initial` 或 `safe_retry` 的 `dispatch_intent_id/revision/digest`、authorization refs/revisions **exact 相同** | 若无 record 可完成首次交付；若已有 accepted/dispatch/observed/reconciled record，只 replay ack/status/result，绝不新增 side effect |
| `safe_retry` | 新 `dispatch_intent_id/revision/digest`、main-authored `safe_retry_commit_ref`、且 `expected_reconciliation_revision` 精确等于 #324 已 commit `safe_retry` 的 revision；同时携带该 commit 后 exact ledger revision | 只验证/承载 ref 与 revision，不解释 `RetryPosture`；通过后才可为同 logical operation 建立新 dispatch attempt |

每个 intent 还含 positive `dispatch_authorization_ordinal`：`initial=1`；`outbox_redelivery` 必须复用原 ordinal；每个 `safe_retry` 必须是 sidecar retained history 中前一 authorization ordinal + 1，缺失、不连续或 history incomplete 均拒绝。Ordinal 只是 main durable dispatch authorization 的序号，不是 RetryPosture、browser retry count 或新的 authority。

`dispatch_intent_digest = SHA-256(JCS({kind, dispatch_intent_id, dispatch_intent_revision, dispatch_authorization_ordinal, run_id, operation_id, attempt_no, request_hash, expected_source_operation_ledger_revision, expected_reconciliation_revision, source_operation_acceptance_ref, safe_retry_commit_ref}))`。它是 main outbox/CAS 事实的稳定 digest，不是 #324 `RetryPosture` schema。`safe_retry_commit_ref` 只能引用 main 已持久化的 posture decision；sidecar 不解析 ref 得出 enum。New token、higher attempt、new deadline、new profile/browser authority 中的任何一项或组合，在没有该 exact `safe_retry` durable authorization 时都永远不得 redispatch。

### 5.6 Shared bounds

| 值 | 上限 |
|---|---:|
| generic opaque/token/reason string | 256 UTF-8 bytes，除非字段更小 |
| user/business short text | 512 UTF-8 bytes |
| structured summary text | 2 KiB per field |
| list | 32 items，operation-specific 更小者优先 |
| map | 32 registered keys；禁止 arbitrary nested map |
| nested depth | 4 |
| request payload | 256 KiB |
| progress/control payload | 64 KiB |
| result/failure payload | 768 KiB |
| absolute frame hard cap | 1,048,576 bytes |

Oversize 在读完整 body、写 journal 或做 browser side effect 前拒绝。不得截断后把结果标为完整；operation-specific result超过上限必须返回 typed bounded failure observation，由 main按 #324处理。

## 6. Operation-specific request/result DTO

所有 result 是 source/business facts，不是 `RuntimeSourceLaneResult`、Failure Envelope、`OperationEvidence` 或 Product Outcome。Main provider adapter在 fenced main commit中把它们映射为 candidate truth/coverage。Candidate presence绝不能擦除 scope-incomplete、stop reason 或 possible-consumption facts。

### 6.1 Shared result facts

每个 operation-specific result 都包含：

```text
identity                         # request identity excluding raw runtime token
journal_phase = observed         # durable sidecar phase
accepted_generation              # sidecar generation that durably accepted
observed_at                      # wall time, display/correlation only
requested_scope_completed        # bool; raw source fact, not ProductOutcome
side_effect_observation          # not_started | observed | unknown
safe_counts                      # operation-registered non-negative count fields
stop_reason_code                 # nullable registered safe code
component_receipt_refs           # echo-only main-issued #322 refs
operation_evidence_ref           # MUST be null on first sidecar observation
failure_envelope_ref             # MUST be null on first sidecar observation
main_semantic_refs               # all null on first sidecar observation; §4.2
cleanup_fact                     # none | completed | incomplete, plus safe count/reason
result_payload_hash              # SHA-256 of canonical result payload
```

`side_effect_observation=unknown` 不等于 `failed`。Result 不含 `retryable/safe_to_retry/retry_after`。`stop_reason_code` 是 producer fact，只能经 #322 registry 映射；不能直接决定 state/outcome。`operation.failure` 的首次 sidecar observation 遵守同一 nullability；sidecar 不能为了填 ref 而预先伪造 Failure Envelope/Operation Evidence/Product Outcome。

### 6.2 Business record DTO（不是 diagnostic evidence）

Candidate business data可以跨本机 inherited pipe，但必须使用固定 schema；它不得进入 sidecar journal、protocol log、canonical diagnostics 或 support bundle。

`CardRecordV1` 最多 30 条：

- `source_record_ref`：随机 opaque 1..96；不是 page ref、URL、path 或 authority；
- `provider_candidate_key_hash`：64 lowercase hex；
- nullable opaque `provider_subject_ref/provider_listing_ref`；不得传明文 account subject；
- `synthetic_candidate_fingerprint/identity_confidence/extraction_source/extractor_version`；
- `pii_classification/retention_policy/access_scope/redaction_state`；
- fixed `card_facts`：display/current company/title、work years、age、gender、city/expected city、education、job intention、active status、school/major/skills/badges，以及最多 8 条 bounded experience/education preview；
- nullable `safe_summary_ref`；protected path/artifact ref 不跨 wire。

`DetailRecordV1` 最多 6 条，每条最多 64 KiB：

- 与 card 相同的 identity/privacy metadata；
- fixed scalar facts：candidate name、active/job status、gender、age、city、education、work years、current title/company；
- fixed `job_intention`：expected role/salary/city/industry；
- 最多 8 条 work experience、8 条 project experience、8 条 education experience；每条只有注册的 company/title/name/role/school/major/degree/duration/date-range/summary fields；
- 最多 32 个 skills 与最多 32 个 opaque source-reference refs；
- 不含 `raw_payload`、`normalized_text`、whole-page text、HTML/DOM、source URL、detail URL、screenshot 或 browser ref。

V1 不允许一个 generic `payload: dict[str, Any]` 逃逸口。新增 Liepin field 必须升 operation contract minor、加入 field/size/privacy tests，并在 explicit compatibility window 内协商。

### 6.3 `VerifySessionRequestV1` / `VerifySessionResultV1`

Schema：`seektalent.source.verify-session.request/v1` / `seektalent.source.verify-session.result/v1`。

Request body：

- `profile_mode=existing_profile`；dedicated mode 只有未来 product decision + #326 artifact gate 后才能新增；
- `profile_binding_ref`、nullable `provider_account_ref`；均 opaque；
- `required_capabilities`，最多 16 个 registered tokens；
- `user_interaction_policy=observe_only|headed_user_action_allowed`；
- `verify_search_surface=true`；不得携带 JD、query、候选正文或登录 secret。

Result-specific facts：

- `process/bridge/extension/profile_lock/account/search_surface/risk_state` 各为 closed readiness fact；
- `session_readiness=ready|not_ready`，仅是 verify-specific fact；
- actual opaque profile/account refs 与 binding generation；
- concrete safe user-action code/instruction key（若观察到），但 main才决定是否 required、是否进入 `needs_attention`；
- 仅 echo main 在 handshake/readiness/submit 已下发的 Machine Capability/Startup Receipt refs；sidecar 不签发；
- 不返回 `current_url`、host/user tab、page ref、cookie、login token 或 screenshot。

`verify_session` 不能写 candidate/scoring/finalization truth。

### 6.4 `SearchRequestV1` / `SearchResultV1`

Schema：`seektalent.source.search.request/v1` / `seektalent.source.search.result/v1`。它映射当前 detail-backed production `search_resumes`，不是 arbitrary workflow。

Request body：

- `query_instance_id`、`logical_round_no >= 1`、`query_role=primary|expansion`；
- `keyword_query`（1..256）与最多 16 个 `query_terms`（每个 1..64）；
- typed `provider_filters` registry（最多 16 keys；值只能是 string/int 或最多 16 个 string）；
- `target_records 0..10`、`max_cards 1..30`、`max_pages 1..5`、`max_details 0..6`；
- `RequirementProjectionV1`：job title、最多 2 个 title anchors、最多 16 个 must-have、16 个 preferred、16 个 exclusions、fixed hard-constraint/preference slots；不传 raw JD、notes、rationale、prompt 或 full `RequirementSheet` object；
- nullable detail budget/approval policy ref；sidecar不能自批 detail consumption。

Result-specific facts：

- 最多 6 条 `DetailRecordV1`；
- 最多 8 个 `{continuation_ref, continuation_kind}`，每个 ref 是随机 opaque ID，kind 只允许 `cards_page|first_page_detail_expansion`，并绑定 origin operation/query/profile generation；continuation body/provider cursor/detail URL/path 留在 sidecar；
- visible/eligible/opened/skipped/terminal-failure/raw/result counts、query exhausted fact；
- `requested_scope_completed` 与 stop reason 独立于 records 非空；
- 不返回 `SearchResult` object、provider snapshot object、private continuation、action trace path 或 raw workflow transcript。

### 6.5 `CardsRequestV1` / `CardsResultV1`

Schema：`seektalent.source.cards.request/v1` / `seektalent.source.cards.result/v1`。它映射 current card-only search route。

Request body：query instance/round/role、keyword/query terms、typed provider filters、`page_size 1..30`、`max_cards 1..30`、`max_pages 1..5`。它不携带 detail approval，不能打开 detail。

Result-specific facts：最多 30 条 `CardRecordV1`、next-page fact（`has_more`，不返回 provider cursor）、raw/visible/returned counts、scope-complete/exhausted facts和可选 `{continuation_ref, continuation_kind=cards_page}`。Provider cursor留在 sidecar；main续页必须提交 typed continuation operation，而不是传任意 cursor string。

### 6.6 `DetailsRequestV1` / `DetailsResultV1`

Schema：`seektalent.source.details.request/v1` / `seektalent.source.details.result/v1`。

Request body 最多 6 个 `DetailsRequestItemV1`：

- `item_id/request_id/idempotency_key`；item key同样执行 same-key/same-hash；
- `source_record_ref` 与 `provider_candidate_key_hash`；
- `approval_ref`、`detail_claim_ref`、`budget_policy_ref`；
- `logical_round_no/query_instance_id`；
- 不含 detail URL、page ref、DOM selector、candidate object 或 `DetailOpenClaimLedger`。

Result每 item：`request_id`、source record ref、possible-consumption fact、`observed|unknown`、nullable `DetailRecordV1`、worker command ref、safe count/reason。Batch result不能因为部分成功把其他 item 的 unknown/failed fact清掉；main独立映射每个 item再汇总 source disposition。

### 6.7 `ContinuationRequestV1` / `ContinuationResultV1`

Schema：`seektalent.source.continuation.request/v1` / `seektalent.source.continuation.result/v1`。

Request body：一个 `continuation_ref`、`continuation_kind=cards_page|first_page_detail_expansion`、origin operation/query refs和discriminated action：cards page 只能 `action=advance` 并带 `max_cards 1..30`；first-page detail expansion 只能 `action=expand` 并带 detail claim/budget/approval refs、`max_details 1..6`。Ref 只是sidecar lookup key；真正 provider cursor、keyword、candidate refs、detail URL与 per-candidate state不跨 wire。

Result是discriminated union：cards page返回最多30条`CardRecordV1`、`has_more`和下一opaque continuation ref；detail expansion返回最多6条`DetailRecordV1`及first-page visible/eligible/initial-opened/expansion-opened/skipped/terminal-failure counts。两者都返回continuation state `retained|consumed` 和 stop reason。Continuation cleanup 不用伪装成 `action=discard`；它属于独立 cleanup operation。

### 6.8 `CleanupRequestV1` / `CleanupResultV1`

Schema：`seektalent.source.cleanup.request/v1` / `seektalent.source.cleanup.result/v1`。

Request body：

- `target_operation_ids` 最多 32 个，或 `scope=current_run_owned_resources`；
- 最多 16 个 opaque `continuation_ref`；
- `reason=operation_complete|cancelled|drain|orphan_recovery`；
- `include_owned_tabs/include_continuations/include_result_spool` booleans；
- 不含 page ID、window ID、profile path、artifact path、cookie 或 generic delete path。

Result只含 requested/closed/already-missing/failed/deleted counts、per resource-class safe reason和`cleanup_fact`。Cleanup failure不改写已提交 candidate truth或 terminal Product Outcome。

## 7. Message registry

所有 control/event payload 也有 exact schema ID：

| Message type | Payload schema | 最小固定字段 |
|---|---|---|
| `operation.accepted_ack` | `seektalent.source-port.accepted-ack/v1` | identity without raw token、accepted generation、journal revision、authorization ordinal、`new_logical_operation|new_dispatch_authorization|same_intent_replay`、ack time、request hash |
| `operation.rejected` | `seektalent.source-port.operation-rejected/v1` | identity without raw token、reject stage/code、journal health/ref、no-side-effect proof when known |
| `operation.progress` | `seektalent.source-port.progress/v1` | identity、progress seq、registered phase、safe counts/reason；无business payload |
| `operation.result` | operation-specific result schema | §6 shared + discriminated result facts |
| `operation.failure` | `seektalent.source-port.operation-failure/v1` | identity、journal phase/revision、side-effect observation、safe component/reason/counts；first-observation canonical refs 按 §4.2 为 null/echo-only |
| `operation.query` / result | `seektalent.source-port.query.request/v1` / `seektalent.source-port.query.result/v1` | identity/key/hash/generation hint；lookup/completeness/conflict facts |
| `operation.status` / result | `seektalent.source-port.status.request/v1` / `seektalent.source-port.status.result/v1` | operation identity；latest durable phase/revision/result availability |
| `operation.cancel` / result | `seektalent.source-port.cancel.request/v1` / `seektalent.source-port.cancel.result/v1` | identity/expected revision/reason/deadline；cancel boundary fact |
| `operation.reconcile.inspect` / result | `seektalent.source-port.reconcile.request/v1` / `seektalent.source-port.reconcile.result/v1` | identity/generation range/main expected revisions；immutable journal facts |
| `operation.reconcile.ack` | `seektalent.source-port.reconcile-ack/v1` | journal expected revision、main ledger/reconcile revisions、main commit/semantic refs |
| `journal.retention-release.request` | `seektalent.source-port.retention-release.request/v1` | release key/digest、exact tombstone identity、main ledger-retention proof/ref、expected journal revision |
| `journal.retention-release.ack` | `seektalent.source-port.retention-release.ack/v1` | release key/digest、`released|already_released|rejected`、resulting revision/budget facts、typed reject |
| `port.readiness.query` / result | `seektalent.source-port.readiness.request/v1` / `seektalent.source-port.readiness.result/v1` | supported contracts、journal/component/profile readiness facts |
| `port.drain.request` / ack / drained | `seektalent.source-port.drain.request/v1` / `seektalent.source-port.drain.ack/v1` / `seektalent.source-port.drain.result/v1` | reason/deadline/policy；active phases；flush/cleanup facts |

Pre-accept validation失败用`operation.rejected`，不是`operation.failure`。Closed reject codes至少覆盖：`deadline_expired/idempotency_conflict/identity_conflict/request_hash_mismatch/stale_runtime_attempt/stale_profile_binding_generation/draining/not_ready/journal_full/journal_corrupt/journal_schema_mismatch/frame_or_contract_invalid`。Reject code仍只是transport/request fact，不授予retry。

### 7.1 Submit、ack、progress、terminal observation

| Message | Direction | Durable/semantic rule |
|---|---|---|
| `operation.submit.<kind>` | main → sidecar | 完整 operation-specific request + closed `DispatchIntentV1`；sidecar先验证 frame/auth/schema/authority/idempotency/expected revisions/deadline/journal health |
| `operation.accepted_ack` | sidecar → main | 只在 durable `accepted` FULL-sync 后发送；含 accepted generation、journal revision、same-key replay fact |
| `operation.progress` | sidecar → main | 可丢、可省略；含 monotonic progress seq、registered phase、safe counts；不进入 terminal、checkpoint或retry authority |
| `operation.result` | sidecar → main | 只在 durable `observed` + result hash/ref FULL-sync 后发送 conclusive business observation |
| `operation.failure` | sidecar → main | 只表示 conclusive producer failure observation；含 safe reason/component facts与nullable #322 refs；不含 retry permission |

`operation.failure` 不能用于 pipe EOF、read timeout、invalid frame 或 missing response；这些由 main transport adapter记录为 transport facts，然后走 query/reconcile。

Progress callback只在 main adapter内由 progress frame投影到现有 callback。Callback丢失、异常或进程重启不能影响 sidecar journal、main ledger、safe checkpoint 或 terminal result。

### 7.2 Query 与 status

- `operation.query`：按 `(operation_id, idempotency_key, request_hash, dispatch_authorization_ordinal|all, accepted_generation_hint)` 跨 generation 查询 identity/dedupe history。返回 `not_found|matched|conflict|history_unavailable`，以及 journal completeness/truncation facts。只有 journal integrity完整、generation/authorization range完整且目标范围无 accepted记录时，`not_found` 才能证明 sidecar未接受；否则是 unavailable，不能推出未 dispatch。
- `operation.status`：对已匹配 operation返回每个 retained authorization ordinal 的最新 durable phase `accepted|dispatch_intent|observed|reconciled`、generation、last command ordinal、result availability、cleanup fact。它不返回 `running/completed/failed` product status或 RetryPosture。

### 7.3 Cancel

`operation.cancel` 携带 operation identity、expected journal revision、cancel reason和short relative-monotonic deadline。返回：

```text
cancel_received | already_observed | no_dispatch_started | dispatch_may_have_occurred | not_found
```

Cancel 只要求sidecar停止新 command并到operation-specific safe boundary；它不声明 main run 已 `cancelled`。若 dispatch可能已发生，main仍 reconcile。Sidecar不能把cancel timeout改成operation failed，也不能授予 retry。

### 7.4 Reconcile

`operation.reconcile.inspect` 由 main no-owner recovery authority发送，不要求 active runtime bearer token；authenticated inherited-pipe session只能证明请求来自当前 main process，不是第二个 business token。Request携带 operation/key/hash、authorization ordinal/range、generation range、expected main ledger/reconciliation revisions和已知 dispatch/observation refs。

Sidecar返回 closed fact：

- `accepted_no_dispatch`：accepted存在、完整 journal range内无 dispatch intent；
- `dispatch_not_observed`：dispatch intent存在，尚无 conclusive observation；
- `observed_result` 或 `observed_failure`：返回 exact immutable result/failure hash/ref；
- `journal_history_unavailable`：missing/corrupt/truncated/schema-ahead/generation gap；
- `identity_conflict`。

这些是证据事实，不是 `safe_retry/no_retry/reconcile_first`。Main按 #324 expected-revision transaction解释并提交 posture。

Main成功消费 observation 后发送 `operation.reconcile.ack`，携带 main-authored ledger revision、reconciliation revision、`main_commit_ref`和 §4.2 semantic refs。Sidecar只有在验证 identity/hash/expected journal revision 后才 durable append `reconciled`。Ack失败只使record保持 observed，不撤销 main已提交 truth。

### 7.5 Retention release

`journal.retention-release.request` 一次只能指定一个 exact reconciled tombstone，payload 必填：

- `release_key` opaque 1..128 与 `release_digest` 64 lowercase hex；
- operation ID、idempotency key、request hash、first/last generation、tombstone ref、result hash 和 main commit ref；
- `expected_journal_revision`；
- main-authored `ledger_retention_proof_ref`、`source_operation_ledger_revision`、`reconciliation_revision`。Proof 只声明main ledger 对该 exact operation 的 retention 已结束；它不是 retry/evidence/outcome schema。

`release_digest = SHA-256(JCS(request excluding release_digest))`。Same release key + same digest 在 crash/reconnect 后返回同一 `released|already_released`；same key + different digest 必须 `retention_release_idempotency_conflict`。Delete 使用 expected-revision CAS 并与 released-marker/budget counters 在一个 FULL-sync transaction 中全有或全无。

只有 identity/key/hash/generation/result/main-commit/ref 全部 exact match、head phase 为 `reconciled`、已是 minimal tombstone、history complete 且 expected revision 相等时才能删除。Active、unreconciled、result body 尚未安全压缩、identity mismatch、history incomplete/truncated/corrupt、proof/ref/revision mismatch 全部 fail closed，返回 closed reject code，不删除任何其他 record。成功后重算 rows/bytes budget；只有实际回到 hard budget 内才能从 `journal_budget_exhausted` 恢复 readiness。

Retention-release reject code 只允许 `retention_release_idempotency_conflict/not_reconciled/not_tombstone/identity_mismatch/history_incomplete/expected_revision_mismatch/ledger_retention_proof_mismatch/journal_unhealthy`。Ack 不得返回自由文本作为机器语义；可附 bounded safe diagnostic reason ref。

### 7.6 Readiness

- `port.readiness.query/result` 只报告：sidecar lifecycle、journal health、draining flag、supported operation contracts、profile binding generation和 capability gap codes；component receipt refs 只能 exact echo main 已下发 refs。它不代替 `verify_session` 的 account/search/risk事实，也不签发 Startup/Capability receipt。

Readiness closed values：`ready|degraded|not_ready|draining`。缺 extension、profile lock、version mismatch、journal corruption/full必须有 typed gap；process alive不等于ready。

### 7.7 Drain

1. Main发 `port.drain.request`：reason、relative-monotonic deadline、是否允许已dispatch operation到observed boundary。
2. Sidecar durable设置draining并立即拒绝新 submit；返回 `port.drain.ack`，列出active operation IDs/phase，不返回 business payload。
3. Accepted但未 dispatch的operation停在可证明no-dispatch；已dispatch的operation只到observed或unknown boundary，不擅自repeat。
4. Sidecar FULL-sync journal，reclaim owned resources，停止自己创建的 WTSCLI child，返回 `port.drained`含 flush/cleanup facts后退出。
5. Deadline后main只可终止自己持有的 child tree。Kill不把operation改为failed；重启后query/reconcile。

## 8. Canonical request hash 与 idempotency

### 8.1 Hash input

`request_hash = SHA-256(JCS(canonical_intent))`；JCS使用 RFC 8785 JSON Canonicalization Scheme。`canonical_intent`只含：

- operation contract version、source、operation kind；
- `run_id/operation_id/accepted_requirement_revision_id`；
- `profile_binding_generation`，以及operation body中的opaque profile/account binding refs；binding变化不是同一idempotent intent；
- 完整 operation-specific body；
- item-level idempotency data（若有）。

明确排除：attempt number、raw runtime token/token ref、deadline、correlation ID、browser control scope ID、transport session/message/sequence、wall time、sidecar generation、main expected revisions 和 `DispatchIntentV1`。这样同一logical operation可在新runtime attempt中保持same hash，但不能跨profile/account binding generation透明续跑；业务 intent hash 与可重放的 main dispatch authorization digest 分离。每次dispatch仍需重新验证当前runtime/profile/browser authorities。

### 8.2 Required behavior

- same idempotency key + same request hash + same operation ID：返回同一 logical operation。`outbox_redelivery` 必须复用 exact dispatch intent digest；已 accepted/dispatch/observed/reconciled 时只 replay ack/status/result，不得再次产生 browser side effect。
- same key + different hash，或 same key被另一operation ID使用：`idempotency_conflict`，在dispatch前拒绝。
- same operation ID + different key/hash：identity conflict；不得猜测哪一份正确。
- Same-key transport replay 可携带当前 raw token/ref 以通过 request authentication，但 new attempt/token/deadline 不授予 dispatch。已 accepted/in-flight record 的 deadline 不延长。只有 main 已按 #324 以 expected-revision CAS commit `safe_retry`，并发送 §5.5 exact `safe_retry` intent/ref/revisions，sidecar 才能在同 logical operation 下创建新 dispatch attempt 并接受新 deadline。Binding generation变化时main先reconcile旧operation，再创建新的operation/key；不得透明换profile/account。
- `safe_retry` intent 只是载体；sidecar 验证 digest/ref/revision 和当前 authorities，不从 ref 计算 posture，不可修改 main decision。`reconcile_first`/missing/stale authorization 时任何 submit 都只能 query/replay 已有事实或拒绝。
- Result business payload已从sidecar protected spool删除时，sidecar返回immutable result hash、`main_commit_ref`和`payload_released`；main从自己的durable truth读，不重新执行。

## 9. Handshake、framing 与 authentication

### 9.1 Exact framing

每帧：

```text
4-byte unsigned big-endian payload length N
N bytes UTF-8 JSON object
```

- 无newline delimiter、BOM、compression、base64 envelope或多个JSON value；
- `N` 必须 `1..1,048,576`；读取length后超过cap立即typed protocol shutdown，不读/分配body；
- partial read必须继续直到4字节header或N字节body完成；EOF before complete frame是`truncated_frame`；
- stdout只承载frames，stderr只承载bounded sanitized logs，两条pipe持续drain；
- write在单writer queue中完成，不能交错frame bytes；backpressure超deadline进入transport fault，不改operation fact。

### 9.2 Four-step handshake

1. **MainHello**（pre-authenticated）：protocol major/minor range、`product_version/product_build_id/main_build_id`、`release_manifest_ref`、supported operation contract map、frame caps、`session_id`、main-owned random `spawn_context_id`、256-bit random `main_nonce`。
2. **SidecarHello**（pre-authenticated）：echo session/spawn context ID、exact `product_version/product_build_id`、`sidecar_build_id/executable_hash`、protocol range、operation contract map、journal schema/health/mode、sidecar generation、256-bit random `sidecar_nonce`、hash of received canonical MainHello。
3. 双方按下述 exact transcript/HKDF 生成 **direction-specific** keys。Main先发 authenticated **MainReady**：chosen minor、contract map、hello transcript hash、initial direction seq `1`。
4. Sidecar验证后发 authenticated **SidecarReady**。在双方 ready 前不得接受 operation、启动WTSCLI或产生browser side effect。

Channel/peer identity 的根是下列 out-of-band spawn invariant，不是 hello nonce：Main 在 spawn 前按 #326 installed Release Manifest 验证 exact executable hash；直接持有创建的 child process handle 和两条 anonymous pipe 的 exact endpoint；Windows 用 explicit handle allowlist，POSIX 使用 `CLOEXEC` + explicit fd allowlist；parent/child 在 spawn 后立即关闭所有不需要的 duplicate ends。V1 不允许 named endpoint、attach-to-existing-process、pipe adoption、reparented sidecar 或继承无关 handle；child handle/executable/parent/pipe inventory 任一不匹配就在 hello 前 fail closed。

Nonce-derived HMAC **不是独立 peer authentication 根**，不能抵抗已能读写该私有 pipe 的对手；它只在上述 strict inherited-handle boundary 内绑定 exact transcript、direction、session 和 sequence，并防止误路由/旧 session 重放被接受。V1 不新增 transport spawn secret；若未来 threat model 要求它，必须是仅用于 channel bootstrap 的单次 secret，不是 #324 business authority、不替代 single runtime bearer，且不得进 argv/env/log/persistence。

Canonical hello transcript 无歧义定义为：

```text
MH = JCS(MainHello)
SH = JCS(SidecarHello)
T  = LP("MainHello/v1") || LP(MH) || LP("SidecarHello/v1") || LP(SH)
transcript_hash = SHA-256(T)
IKM  = LP(hex_decode(main_nonce)) || LP(hex_decode(sidecar_nonce))
salt = SHA-256(LP("seektalent-source-port-salt-v1") ||
              LP(UTF8(product_build_id)) || LP(UTF8(session_id)) ||
              LP(UTF8(spawn_context_id)) || LP(hex_decode(executable_hash)))
PRK  = HKDF-Extract-SHA256(salt, IKM)
K_m2s = HKDF-Expand-SHA256(PRK,
        LP("seektalent-source-port-key-v1") || LP(transcript_hash) || LP("main-to-sidecar"), 32)
K_s2m = HKDF-Expand-SHA256(PRK,
        LP("seektalent-source-port-key-v1") || LP(transcript_hash) || LP("sidecar-to-main"), 32)
```

`LP` 与 §5.3 相同；nonce 是 exact 32 bytes，hex 只是 wire encoding。Raw nonce/derived key 不进 argv、environment、stderr、journal、receipt或support bundle。

### 9.3 Frame authentication/replay

Direction 分别使用 `K_m2s/K_s2m`。对每个 authenticated frame：先令 `U = JCS(envelope 删除 auth_tag)`、`M = byte_length(U)`；再令 `N` 为插入 **64 lowercase hex** `auth_tag` 后最终 JCS JSON body 的字节长度。因 tag 长度固定，可先以 64 个 `0` 计算 `N`；实际 tag 不改变 `N`。

所有 authenticated frame 的 on-wire JSON body 必须就是该对象的 exact JCS bytes；非 canonical whitespace、key order、number/string encoding 即使语义可解析也拒绝。Hello 的 JCS bytes 同样由 strict schema 生成，unknown/duplicate fields 在 transcript 计算前拒绝。

```text
auth_input = LP("seektalent-source-port-frame-auth-v1") ||
             LP(UTF8(session_id)) || LP(UTF8(direction_label)) ||
             uint64_be(direction_seq) || uint32_be(N) || uint32_be(M) || U
auth_tag = lowercase_hex(HMAC-SHA256(direction_key, auth_input))
```

Outer frame header 必须 exact `uint32_be(N)`；`N` 是**包含 auth_tag 的最终 canonical JSON body**，`M` 是**删除 auth_tag 后的 canonical JSON**。Receiver 先执行 cap/UTF-8/strict JSON/duplicate-key 检查，再按上述重算；任一 length、JCS、direction、session、sequence 或 tag mismatch 立即关闭 session，不写 journal、不返回可继续的 operation response。

每方向 `direction_seq` 必须严格+1。Duplicate seq/message ID、gap、wrong reply、bad HMAC、direction reflection或old session frame触发typed protocol failure并关闭session；不能“尝试继续”。Reconnection总是新session/nonces/keys；operation idempotency来自journal，不来自transport replay。

### 9.4 Compatibility 与 typed mismatch

| Pair | Compatible | Typed reject |
|---|---|---|
| product release | exact `product_build_id`；main/sidecar来自同一installed release | `product_build_mismatch` |
| port protocol | major exact；minor range有交集，选择双方最高共同minor | `protocol_major_mismatch` / `protocol_minor_no_overlap` |
| operation contract | 每个production operation exact schema major；minor只在双方显式field/capability registry相容时协商 | `operation_contract_mismatch` / `required_operation_missing` |
| sidecar executable | hash与 #326 manifest exact | `sidecar_integrity_mismatch` |
| WTSCLI/bridge | #326 exact component refs与现有 implementation/build/protocol/capability验证 | 引用现有typed bridge mismatch，不在本文另造同义语义 |
| journal data | supported schema/migration path且integrity通过 | `journal_schema_ahead` / `journal_migration_required` / `journal_corrupt` |

V1 required operation set六项缺一，sidecar不得ready。Unknown optional field也因`extra=forbid`拒绝；兼容新增必须通过minor negotiation，不能 silently ignore。

## 10. Sidecar Command Journal

### 10.1 角色与禁止项

Journal是sidecar私有、跨generation、bounded的 external-effect reconciliation store。它不是main source-operation ledger、candidate truth、RetryPosture、ProductOutcome、canonical diagnostic journal或release manifest。

Journal禁止保存：raw runtime token、control key/fence token、query/JD/requirement正文、candidate/card/detail payload、DOM/HTML/text、URL、cookie、screenshot、provider account subject、profile path、stdout/stderr。它只保存identity/hash、safe authority refs、command/result hashes、bounded reason/counts、canonical refs和internal protected-spool ref/hash/size。

Business result body放sidecar protected result spool：目录0700、file0600、单operation≤768KiB、atomic write+fsync；journal只存random internal ref/hash/size。它不是business truth；main commit/reconcile后可释放body但必须保留idempotency tombstone/result hash/main commit ref。

### 10.2 Durable state order

每个 logical operation 有 stable dedupe identity；每个 `dispatch_authorization_ordinal` 的 external-effect epoch 严格单向：

```text
accepted -> dispatch_intent -> observed -> reconciled
```

- `accepted`：identity/key/hash、accepted generation、`DispatchIntentV1` ID/digest/authorization ordinal/expected revisions、`runtime_attempt_fence_ref`、frame digest durable；完成后才发 Ack。Raw runtime token、deadline replay 或 higher attempt 不能单独转换 phase。
- `dispatch_intent`：必须有已验证的 `initial` 或 main-authorized `safe_retry` intent；每个browser command ordinal/ID/ref、profile generation、browser scope correlation、controller fence **ref only** durable；commit后才可发command。`outbox_redelivery` 对已有 epoch 只 replay response。
- `observed`：conclusive/unknown observation、result spool hash/ref/size、safe counts/reason durable；first sidecar observation 的 Failure Envelope/Operation Evidence/MainSemantic refs 为 null，receipt refs 只 exact echo main 已下发值；commit后才发Result/Failure。
- `reconciled`：main expected ledger/reconciliation revision、main commit ref和main-authored semantic refs durable；commit后才可压缩result body/event detail。

同一 authorization epoch 可有多个 browser command intent/observation event，但该 epoch 的 head phase 不倒退，command ordinal严格递增。只有 #324 main commit `safe_retry` 后才能创建下一 authorization ordinal；前一 epoch 必须已由完整 journal/reconcile 证明 no-dispatch 并关联 main reconciliation ref。新 epoch 重新从 durable `accepted` 开始，但 logical operation identity/key/hash 不变。Observation不一定conclusive；unknown仍是observed external fact，不能伪造result。

### 10.3 SQLite transaction 与 sync policy

本文与 #321 一致：v1 目标 journal 使用独立 sidecar SQLite file，固定 `journal_mode=DELETE` rollback journal、`synchronous=FULL`、`foreign_keys=ON`、bounded busy timeout。Startup 必须读回并验证实际 pragma；若数据库意外处于 WAL 或其他未批准 mode，不得自动转换或 ready。每个 critical transition：

1. `BEGIN IMMEDIATE`；
2. append immutable journal event；
3. expected-revision CAS update operation head/idempotency index；
4. commit；
5. 确认 database + active rollback-journal sync/commit 成功后才越过对应外部边界。

`accepted/dispatch_intent/observed/reconciled/retention_release` 全部 FULL-sync。Progress不持久化。Protected result spool必须先atomic write、fsync file、fsync parent dir，再让`observed` transaction引用它。Drain 在 rollback mode 下等待 active transaction 结束，验证无未恢复 hot journal，执行 bounded integrity/flush check 并 fsync 需要的 DB/data-root metadata；不运行 WAL checkpoint，不以删除 `-journal` 文件伪造 drained success。

WAL 是 gated future migration，不是 v1 实现自由度。只有 product 固定 actual SQLite build、该 build 包含适用的 upstream WAL-reset 修复、Windows 11 x64/macOS arm64/macOS x86_64 全部通过真实文件+多连接+process-kill/power-loss+checkpoint/reset+upgrade/rollback matrix，且独立 ADR 明确批准迁移与回滚后，才能改变 journal mode。任一条不满足就继续 rollback journal。

`synchronous=NORMAL`的current browser lifecycle mirror不能复用为command journal。实现可选非SQLite storage，前提是用同一fault suite逐边界证明等价durability；不得只靠memory buffer或async best-effort writer。

### 10.4 Generation lookup 与 completeness

- 每次sidecar startup在journal中原子分配monotonic `sidecar_generation`和random instance ID；generation不能由PID或wall time推断。
- 新generation打开同一product data root和product lock，按 operation ID/key/hash查询全部retained generations。
- Main durable dispatch outbox在发request前记录target sidecar generation hint；ack后确认accepted generation。
- Query返回`searched_generation_min/max`、`history_complete`、`oldest_retained_generation`、truncation/corruption/migration state。
- 只有目标generation范围完整且journal healthy时，absence才可支持`accepted_no_dispatch/not_accepted`事实；空lookup本身永远不是未发生证明。

### 10.5 Retention 与 compaction

Journal总hard budget：50,000 immutable transition rows、64 MiB（v1 计入 DB 与所有 active rollback-journal/super-journal 文件）、14天event detail，任一先到触发deterministic compaction。`-wal/-shm` 只在未来 ADR 已批准 WAL mode 时计入同一 budget；v1 如果发现它们则是 mode/migration 故障，不是正常 size 构成。

Retention顺序：

1. active/unreconciled operation与其causal spine不得TTL删除；
2. reconciled result body可在main ack后删除；
3. reconciled event detail满14天或budget触发时压缩为minimal idempotency tombstone；
4. tombstone含operation/key/hash、first/last generation、result hash、main commit ref、reconciled revision；只有main在其source-operation ledger retention结束后发 §7.5 authenticated retention-release，才可删除；
5. 若unreconciled/tombstone使hard budget无法满足，sidecar进入`not_ready: journal_budget_exhausted`，拒绝新operation和browser side effect；不得静默evict或reset。

Compaction保存`oldest_retained_generation/dropped_by_class/history_complete`。Delete/compaction crash必须得到旧或新完整状态，不能half tombstone。

### 10.6 Corruption、migration、disk full fail closed

- Startup先验证file ownership/permissions、schema、migration state、`PRAGMA integrity_check`与result-spool refs，再宣告ready。
- Corrupt、schema ahead、unsupported rollback、interrupted migration、read-only、full、I/O error、lock timeout分别typed，不压成`journal unavailable`。
- Migration采用new-file/copy/validate/fsync/atomic-switch并保留old backup；任何失败继续使用完整旧schema或not-ready，不自动删除/重建。V1 migration 必须保持 rollback mode；读到 WAL source/target 只能在上述 ADR gate 完成后走显式迁移，否则 typed not-ready。
- `SQLITE_FULL/IOERR/READONLY/CORRUPT/CANTOPEN/BUSY` 在accepted前：不ack、不dispatch；在dispatch intent后：保留last durable phase并返回transport/journal fact，main reconcile-first。
- Disk full时不得先执行browser command再尝试journal，也不得用stdout/raw log作fallback evidence。

## 11. Authority、privacy 与 forbidden transport

### 11.1 Stale authority rejection

- **Runtime attempt**：main port client在每次submit前用main store验证raw token；sidecar检查run attempt high-watermark和token ref consistency。Higher attempt accepted后，任何lower attempt在dispatch前`stale_runtime_attempt`。Late result到main仍须通过#324 bounded commit API，失败只能成为main-authored rejected evidence。
- **Profile binding**：request generation必须等于sidecar current binding generation；变化立即阻断新dispatch。旧generation历史journal仍可read-only reconcile，不能继续command。
- **Browser controller**：sidecar为operation activation产生controller-only key/fence，WTSCLI/extension每command验证。Stale fence拒绝；Source Port只收到non-sensitive authority ref。Main永远不能生成、查看或回放controller secret。
- `browser_control_scope_id` mismatch记录protocol/correlation failure；不能被称作authority rejection，也不能替代上述三项。

### 11.2 Business payload 与 diagnostic projection分离

Structured requirement/candidate facts是local business payload，不是support evidence。它们：

- 只在authenticated inherited pipe与protected result spool短暂存在；
- 不进入journal row、stderr、Failure Envelope detail、receipt、`OperationEvidence`或support bundle；
- main fenced commit后按现有candidate/artifact privacy policy持久化；
- crash artifact若需要保留，必须由main既有protected artifact lifecycle拥有，不能由#325发明support schema。

### 11.3 Wire hard denylist

任何 request/result/event/control DTO 都禁止：

- callback、Python/JS object、`RuntimeSourceLane*`、`RequirementSheet` instance、candidate/domain model；
- arbitrary command/action/script/eval/CDP method、DOM selector workflow、browser object、window/tab/page ref；
- cookie、Authorization/provider API token、password、验证码、handoff/control key、browser fence token；
- raw screenshot/image base64、DOM/HTML/whole-page text、clipboard/download、raw stdout/stderr；
- full URL/query string、detail URL、profile/home/workspace path；
- raw provider cursor、private continuation body/path、artifact filesystem path；
- `retryable/safe_to_retry/retry_after`、run state transition或ProductOutcome decision。

Current `LoginRelaySnapshot.image_base64/current_url`、`ProviderSearchContinuation.opaque_ref` path和generic daemon actions不是target DTO；headed Chrome user interaction取代screenshot relay跨boundary。

## 12. Deterministic contract-test harness

### 12.1 Fake transport

`FakeDuplexTransport` 必须可确定性注入：1-byte fragmentation、coalesced frames、header/body EOF、oversize length、invalid UTF-8/JSON、duplicate key、unknown field/type、blocked writer、stderr flood、seq duplicate/gap/reorder、bad HMAC、old session replay、wrong reply ID、hello field/transcript tamper、length-prefix/JCS 拼接歧义尝试、direction reflection 和各compatibility mismatch。

断言：parser有固定memory cap；任何invalid input在journal/browser前拒绝；EOF/timeout只产生transport fact；reconnect使用new nonce/session且same operation靠journal dedupe。Real child harness 还必须遍历 handle/fd allowlist，证明错误继承、duplicate pipe end 泄漏、attach/reparent、wrong executable/parent handle 全部在 ready 前 fail closed，且 parent EOF 不会因遗留 writer handle 而被无限延迟。

### 12.2 Fake clock与authority

- `FakeMonotonicClock`逐tick推进deadline，wall clock任意回退/跳跃不改变authority；
- `FakeMainAuthorityStore`验证run/attempt/token/operation/hash/revision；在frame write前、ack后、result commit前分别revocation；
- 生成并重算 §5.3 fence ref，覆盖 raw/ref mismatch、run/attempt/operation/hash/revision 任一变化、sidecar restart/generation 变化下的稳定 lookup；ref 单独无法通过 bearer validation或授权 dispatch；
- fake main ledger 产生 `initial/outbox_redelivery/safe_retry` closed intents；无 committed `safe_retry` ref/revision、只换 new token/attempt/deadline、或篡改 expected revision 都断言 browser effect count 为 0；
- profile generation与browser fence各自fake，证明三者不可替代；scope ID只影响correlation assertion；
- raw token/control secret在captured frames以外的journal/log/error/test snapshot扫描为零。

### 12.3 Real-file fake journal

在temporary real SQLite file、v1 production `journal_mode=DELETE/synchronous=FULL` 设置下对每个statement/commit/fsync前后注入exception或独立process kill：

- accepted row/Ack全有或全无；
- dispatch intent durable前fake browser effect count永远0；
- dispatch commit后crash得到`reconcile_first` input，不能redispatch；
- observed result spool/hash/ref原子可读；
- reconciled ack replay幂等；
- same key/same hash只一个logical operation，same key/different hash conflict；
- outbox redelivery 在 accepted/dispatch/observed/reconciled 每个 crash point只 replay ack/status/result；new token/attempt/deadline alone 永远不增加 dispatch ordinal；只有 exact main `safe_retry` commit ref/revisions 可建新 dispatch intent；
- new generation查询old unreconciled record；empty-complete与history-unavailable可区分；
- hot rollback journal/DB corruption、unexpected WAL mode/files、schema ahead、migration kill、FULL/READONLY/IOERR/BUSY均fail closed；故障解除后`integrity_check`通过；
- rollback-journal DB + active journal-file size 精确计入 budget；v1 不运行 WAL checkpoint。未来 WAL migration test 只在 pinned fixed build + declared-platform real-file/multi-connection/crash/checkpoint/reset matrix + ADR fixture 全部存在时才可进入；
- compaction/tombstone/retention-release 在 delete/marker/CAS/commit/fsync 每个 crash point保持全有或全无；same-key/same-digest replay幂等，different digest conflict；active/unreconciled/identity mismatch/history incomplete 不可误删；删除 exact tombstone 后 budget/readiness 只在真实回到 cap 内恢复；budget exhaustion不evict unresolved。

### 12.4 Operation contract goldens

六种request/result、全部message与每个minor compatibility组合有canonical JSON/hash golden。Property tests覆盖field/list/depth/frame limits、unknown field、request hash normalization、dispatch-intent digest、partial-with-records、zero result、per-item details partiality、cleanup failure与continuation ref binding。Negative schema corpus 必须拒绝 sidecar-originated canonical ref，并证明首次 observation 在 main commit 前 `FailureEnvelopeRef/OperationEvidenceRef/MainSemanticRefs` 为 null；component/startup receipt 只能 exact echo main 已下发 ref。

Static gates：

- wire package不能import `seektalent.source_contracts.runtime_lanes`、`seektalent.models.RequirementSheet/ResumeCandidate/NormalizedResume`或browser implementation modules；
- main non-sidecar production modules不能import `opencli_browser`、`opencli_launcher`、Liepin site adapter/automation after T3；
- operation enum exact六项，无`execute/command/workflow/browser_action` generic escape；
- DTO schema无callback/object/Any/arbitrary map、raw URL/screenshot/cookie/token fields；唯一secret exception是request header的`runtime_attempt_fence_token`。

### 12.5 Cross-contract assertions

Tests逐字段assert：

- `run_id/operation_id/attempt_no/correlation_id/browser_control_scope_id`与#322语义一致；canonical object不并存`runtime_run_id`；
- Source Operation disposition/retry/outcome只出现为main-authored refs；
- transport timeout/EOF paths没有operation failure或safe-retry mapping；
- `safe_retry` 新 dispatch 只能引用 #324 已持久化 posture ref/reconciliation revision；raw token/ref、attempt 或 deadline 不是授权；outbox redelivery 不重放 side effect；
- observed business records只有main fenced checkpoint/candidate commit后才影响coverage/outcome；
- Failure Envelope/receipt/evidence只用#322 schema IDs/refs，issuer/nullability/echo rule 与 #322 逐字段一致，本文没有shadow model；
- v1 command journal 与 #321 同为 rollback journal + FULL；不存在未经 ADR 的 WAL 默认/迁移/checkpoint path；
- T1/T2/T3 gates与runtime topology字面一致。

## 13. T1/T2/T3 hard-cut与删除门禁

### T1 — Main runtime single owner

前置完全来自#324/runtime topology：continuous lifespan worker、SQLite poll truth/wake hint、exact-newest safe checkpoint、opaque runtime fence、source-operation ledger/outbox和bounded main commit API。Gate：cold queue、second queued run、recoverable restart、duplicate wake、bounded shutdown、stale token和ledger crash tests全过。

T1不启用sidecar production routing，也不新增第二browser owner。

### T2 — Contract、sidecar与unreachable production harness

- 实现本文framing/handshake/auth、六种DTO/message、closed dispatch/retention-release contracts、rollback-mode journal、supervisor与fake/synthetic browser；
- WTSCLI先提供foreground/serve owned-child contract，或sidecar自己承载bridge server；
- `verify_session`最先纵向实现，但六种operation contract/journal/fake test都必须完成；
- production composition仍只有current owner，sidecar不可达live profile；无feature flag让用户在old/new间fallback；
- exact artifact harness覆盖main/sidecar/Node crash、pipe fault、profile lock、extension restart和journal storage fault。

### T3 — One-release browser ownership hard cut

同一release一次完成：

1. 所有live Liepin `verify_session/search/cards/details/continuation/cleanup`路由到唯一sidecar；
2. main删除direct `ensure_opencli_runtime`、daemon connect/restart、automation/lifecycle/site adapter construction与imports；
3. 删除production fixed `127.0.0.1:19825`/`X-OpenCLI:1` main path、unknown-owner restart和host-tab-is-ready branch；
4. 删除/禁用production `external_http/opencli` dual worker选择、screenshot login relay、generic command port和temporary translation；fake fixture只留test composition；
5. private continuation/detail URL/browser refs完全留sidecar；main adapter只见本文refs/records；
6. release exact-pairs main/sidecar/WTSCLI/bridge；同profile只有一个controller；
7. 回滚只切换previous complete release。当前release内不尝试old path、不dual-write、不长期feature flag。

T3 gate包括AST/import denylist、production composition test、real child ownership/parent EOF、same-profile double-controller rejection和全六operation exact-artifact smoke。任一legacy path仍可由production config到达即不通过。

## 14. Preserve / migrate / delete mapping

### 14.1 Preserve并演进

| Current asset | Target |
|---|---|
| `RuntimeSourceLane*` plan/request/result/event业务语义 | 留main进程内；provider adapter做explicit mapping |
| `runtime_control` run/event/checkpoint/candidate/lease及#324 migration方向 | main唯一truth；Source Port不写DB |
| current query/filter/budget、partial/block/possible-consumption事实 | 映射为operation-specific bounded fields；candidate presence不抹partial |
| bridge implementation/build/protocol/capability验证 | sidecar startup/receipt input |
| browser control key/fence、owned inactive tab、user/host tab不可接管、idle/reclaim | 移入sidecar controller；Source Port只见correlation/evidence refs |
| typed OpenCLI/Liepin reason codes与privacy validators | 作为#322 exhaustive mapping input，不另造同义code |
| detail claim ledger与first-page continuation行为 | state移入sidecar；wire只见approval/claim/continuation refs |
| fake workers、daemon transport/lifecycle unit seams | 复用为deterministic harness素材，不宣称production topology已满足 |

### 14.2 Migrate

| Current path | Required migration |
|---|---|
| `build_liepin_opencli_worker_client()` browser factory | 全部移动到sidecar composition root |
| `LiepinWorkerClient` method boundary | 由六个operation-specific Source Port client methods实现；无generic method |
| `ExternalHttpLiepinWorkerClient` `/internal/*` bearer HTTP | production由stdio protocol替换；不要把legacy bearer/token/framing复制到new port |
| `WorkerHealth/SessionStatus/SearchResponse/DetailResponse` | 显式映射到本文DTO；移除URL、raw payload、screenshot/private fields |
| `LiepinOpenCliResumeRequest` / `SearchRequest` | 映射为Search/Cards structured body与bounded requirement projection |
| `LiepinFirstPageContinuationStore` | sidecar private store；random `continuation_ref`代替`artifact://protected/...`跨wire |
| current detail request URL/candidate carrier | `source_record_ref + provider_candidate_key_hash + approval/claim refs`；locator只留sidecar |
| fail-open browser lifecycle registry | 可继续做cleanup mirror；新增本文separate fail-closed command journal，不能混称 |
| callback progress | main adapter从lossy progress frame调用；所有durable truth不依赖callback |
| current `(executor_id, attempt_no)` guard | #324先加single opaque token；本文只负责authenticated scoped transport |
| version 0.7.49 wheel/Intel builder | #326产出declared platform exact product artifact，包含sidecar与same-build receipts |

### 14.3 T3后delete

- main-process direct browser imports、daemon restart、fixed endpoint/header和unknown process control；
- host tab存在即session ready；
- production generic daemon/CLI/browser command exposure；
- SourceLane/domain/callback/object serialization；
- raw provider cursor、private continuation body/path、detail URL/page ref跨boundary；
- login relay screenshot/base64/current URL跨main ↔ sidecar；
- `retryable`作为execution authority，或sidecar/failure envelope决定retry/outcome；
- same-release old/new fallback、dual write、长期feature flag；
- 把PyPI wheel、legacy HTTP worker或current Intel workflow称作完整External Execution Plane product。

## 15. #326 hard constraints

#326 implementation/release必须消费本文而不能改写它：

- installed release有immutable `product_build_id`和exact main/sidecar executable hash；WTSCLI/bridge exact pair，production extension在显式compatibility window；
- sidecar以main-owned child handle启动，exact executable hash + Windows/POSIX explicit handle/fd allowlist；不允许attach/adopt/reparent；nonce不在argv/env/log；stdout protocol与stderr log独立drain；
- release/data/profile分目录；journal/result spool在product data root，不随binary rollback静默回退；migration/rollback mismatch fail closed；v1 manifest/receipt 声明 exact SQLite build 和 `journal_mode=DELETE/synchronous=FULL`；
- 每个declared supported/released platform用其exact artifact跑framing/auth/process-kill/journal/browser/upgrade matrix；未产出/未通过为`unsupported/not_shipped`；
- #326 不得把 WAL 当作v1默认或隐式upgrade。未来切换必须 pinned SQLite build 已含适用 WAL-reset fix，三平台 exact-artifact 真实文件/多连接/crash/checkpoint/reset/upgrade/rollback matrix 全过并有显式 ADR；receipt/budget/migration/rollback 同时更新；
- current main没有Windows x64、macOS arm64、macOS x86_64三者的production-ready exact evidence，不能借别的平台或旧constraints标PASS；
- T3 release前完成WTSCLI foreground/serve或sidecar-owned bridge server、dedicated-profile真实账号spike、production extension canary和previous-release whole-unit rollback；
- installer/upgrade drain active operation，rotate session/browser authorities，不同时运行两个release owner。

## 16. Cross-contract consistency verdict

| Frozen question | #321/#322 | #324 | #325 resolution |
|---|---|---|---|
| canonical failure/evidence | #322 唯一schema/issuer/store | 只在main business transaction引用 | 首次sidecar observation的Failure/OperationEvidence/semantic refs为null；receipt只exact echo main ref |
| retry/outcome | 无authority | main ledger/FSM唯一决定 | sidecar不发enum decision，只携带main-authored refs |
| transport EOF/timeout | typed diagnostic fact | ambiguous effect=`reconcile_first`，不能直接失败 | query/reconcile后才允许main应用posture |
| runtime fence | 只投影safe ref | single main-minted bearer + bounded commit API | raw token只在authenticated request，journal/result零泄漏 |
| profile/browser authority | safe refs与scope correlation分离 | claim前revalidate current authority | stale generation/fence在side effect前拒绝；scope ID非authority |
| operation phase | diagnostic只观察 | main ledger有accepted/dispatch/observed/reconciled/main_committed | sidecar journal固定accepted→dispatch_intent→observed→reconciled，main commit仍由#324拥有 |
| same-key redispatch | #322 无 retry authority | 只有 main committed `safe_retry` + expected revisions 授权 | closed dispatch intent 分开 initial/outbox redelivery/safe retry；new token/deadline alone 无效 |
| SQLite journal mode | #321 v1 冻结 rollback journal；WAL 需 pinned fix/matrix/ADR | main durable semantics 不由 sidecar storage mode 改写 | sidecar journal 使用 `DELETE + FULL`；WAL 仅 gated future migration |
| candidate/product truth | diagnostics不拥有 | fenced checkpoint/main commit后才计入outcome | business records pure-data返回，sidecar不成为candidate DB |
| lifecycle/release | receipts只记录 | run不等process | topology/#326拥有supervision/artifacts，本文只冻结wire/gates |

原稿与 #321 有一处实质冲突：无条件 `journal_mode=WAL`。本版已以 `DELETE` rollback journal + FULL 解决，WAL 只保留为 pinned-fix/matrix/ADR 后的 future migration，不存在第二个 v1 storage authority。表面张力“sidecar返回RetryPosture”按main-authored ref处理：sidecar返回external facts，main按#324提交posture并可在后续ack/内部adapter关联ref；sidecar从不授予`safe_retry`。`accepted → dispatch_intent → observed → reconciled`同时存在于main ledger与sidecar journal，但owner不同：main是business interpretation，sidecar是external-effect evidence；两者通过authenticated refs/CAS关联，不双写同一truth。

## 17. Document Definition of Done

- [x] 六个production operation有独立request/result DTO，无generic command。
- [x] request identity、relative-monotonic deadline、main expected ledger/reconciliation revisions、closed dispatch intent、三种authority/correlation边界已冻结。
- [x] ack/progress/result/failure/query/status/cancel/reconcile/retention-release/readiness/drain已冻结。
- [x] same-key/same-hash replay与same-key/different-hash conflict已冻结；outbox redelivery 不重放side effect，只有main committed `safe_retry` 可新dispatch。
- [x] journal durable order、rollback journal + FULL sync、generation lookup、retention-release CAS、corruption/migration/disk-full fail-closed已冻结；WAL 仅gated future migration。
- [x] product/main/sidecar handshake、framing、caps、nonce/HMAC、compatibility与typed mismatch已冻结。
- [x] #322 evidence与#324 semantic refs只引用不重定义；issuer/first-observation nullability/exact echo 已冻结；controller secret不进入main。
- [x] deterministic fake transport/journal、authority、privacy、crash/property gates已冻结。
- [x] T1/T2/T3 hard cut、preserve/migrate/delete和#326约束基于current code truth。
- [x] 历史五用户日志未被设为前置条件。

Implementation仍未完成，至少需要后续独立PR：

- [ ] #324 source-operation ledger/outbox、opaque runtime fence与bounded main commit API；
- [ ] wire models/parser/auth/client + fake transport；
- [ ] sidecar journal/result spool + real-file crash tests；
- [ ] 六operation fake/synthetic implementation；
- [ ] WTSCLI owned-child/bridge server与sidecar process supervisor；
- [ ] T3 one-release production composition hard cut与legacy deletion；
- [ ] #326 exact artifacts、clean-machine/canary/upgrade/rollback gates。
