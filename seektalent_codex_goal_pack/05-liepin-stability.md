# Liepin 稳定化目标

## 核心判断

Liepin 反复破坏平衡的根因不是单个 selector，而是边界错误：

- runtime 参与 Liepin backend mode 判断。
- runtime 携带 OpenCLI reason code。
- Liepin provider 依赖 runtime DTO。
- worker 每次请求启动 browser。
- card/detail/session/login/approval/error/budget 多类职责互相穿透。
- 为修 bug 加了过多 fallback 和兼容分支。

本目标要把 Liepin 的复杂性关在 Liepin 边界内。

## 目标边界

### Runtime

runtime 只知道：

- 有一个 source id。
- source 返回 candidates/evidence/events。
- source 可 blocked/partial/failed/completed。
- source 可请求 detail approval。
- source 有公共 reason code。

runtime 不知道：

- Liepin
- OpenCLI
- Playwright
- worker mode
- connection id
- browser context
- selector
- detail URL
- approval HMAC 结构

### Liepin provider

`src/seektalent/providers/liepin/` 负责：

- source adapter
- runtime-neutral request/result 映射
- worker client
- worker mode/opencli/managed_local/external_http 决策
- Liepin filter compiler
- Liepin detail approval mapping
- Liepin reason code mapping
- Liepin persistence
- Liepin-specific tests

### Liepin worker

`apps/liepin-worker/` 负责：

- Playwright browser lifecycle
- session storage
- login relay
- card search
- detail open
- worker HTTP contract
- worker boundary/type tests

## Browser lifecycle

修复 issue `#60` 时，不要只把 `chromium.launch()` 挪到另一个函数。必须完整实现托管生命周期。

要求：

- worker 进程内有 managed browser singleton 或短 TTL pool。
- card search 和 detail open 复用 browser。
- 每个 session/request 使用隔离 context。
- context 使用对应 session storage state。
- context 结束后必须 close。
- browser 发生 fatal error 时可重建。
- shutdown/idle TTL cleanup 明确。
- 多 session 隔离有测试。
- repeated search/detail 测试证明不会每次启动 browser。

不要求 live Liepin 网站 e2e 进入 CI；需要 worker contract/fake Playwright 层面覆盖生命周期。

## OpenCLI 处理

OpenCLI 是 Liepin backend 的一种模式，不是 runtime 概念。

要求：

- 所有 `opencli` 字符串从 runtime 生产代码移出。
- OpenCLI reason code 只在 Liepin provider/worker 边界。
- OpenCLI 模式需要顺序执行时，由 Liepin source adapter 内部调度。
- runtime 只看到 source lane 的公共状态和公共 reason code。

## Error semantics

不要把所有异常吞成 fallback。

规则：

- provider 边界可以把外部错误转换成 structured provider failure。
- internal bug 应 fail fast，让测试暴露。
- worker request invalid 返回明确 4xx。
- login_required、risk_challenge、timeout、filter_unapplied 等映射到公共 code。
- raw error 不进入 public event；写 artifact/store 时必须 redacted。

## Filter compiler

修复 issue `#67` 和 `#68` 时：

- 共享 range overlap helper。
- canonical filter plan 只在 runtime-neutral filter module 中出现一次。
- Liepin filter compiler 只负责把通用 filter intent 投影到 Liepin native filters。
- CTS filter projection 只负责 CTS native 投影。
- 不允许 CTS/Liepin 复制 truth-value 和 freeform normalization 逻辑。

## Duplicate helpers

修复 issue `#66` 时：

- `objectPayload()`、`stringPayloadValue()` 提到 Liepin worker 本地 utility。
- 保持 null、array、blank string、non-string 行为不变。
- 不要引入全局 Utils 包。

## Liepin 验收

必须通过：

```bash
cd apps/liepin-worker
bun test
bun run typecheck
bun run boundary-check
bun run compatibility-gate
```

以及 Python 侧：

```bash
uv run pytest tests/test_liepin_provider_adapter.py tests/test_liepin_runtime_source_lane.py tests/test_liepin_worker_client.py tests/test_liepin_opencli_retriever.py
```

如果测试名重构，必须保留同等覆盖，并在 PR summary 中列出替代命令。
