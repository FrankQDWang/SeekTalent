# Harness 重新校准

本目标的第一步必须是重新校准 harness。先改护栏，再大改代码。

目标不是把 agent 变成流水线工人，而是用少量高价值边界约束防止重大漂移。不要给 Codex 逐文件指令；给它不变量、验收标准和可运行检查。

## 当前 harness 的强项

保留以下方向：

- `AGENTS.md` 的务实原则：简单、直接、少抽象、少 fallback。
- red-zone/yellow-zone 概念。
- `scripts/verify-red-zone.sh` 和 `scripts/verify-dev-workbench.sh`。
- `tools/check_arch_imports.py` 防止 core import UI/experiments。
- `tools/check_pr_governance.py` 对普通 PR 的文件数、层数、red-zone 约束。
- `tools/check_tach_baseline.py` 对架构漂移的 radar 作用。
- privacy gate、AI bad-smell gate、worker boundary check。

## 当前 harness 过强处：本次要临时削弱

本次是故意跨层的大重构，普通规则会错误阻挡目标完成。

需要增加一个明确的“major refactor goal mode”，只对本目标生效：

- 允许超过 15 个非生成文件。
- 允许同时触及 runtime、provider、BFF、frontend、governance、docs、tests。
- 允许 red-zone 文件变更。
- 不要求把一个完整架构重构切成多个 PR。
- 不用“每个 PR 只能一层”的普通规则阻止本目标。

但削弱必须有条件：

- 必须存在 goal manifest。
- manifest 必须列出 goal id、触及层、red-zone 文件、验收命令、删除目标、风险说明。
- manifest 不能覆盖 dependency/config/prompt 变更，除非明确列为必要并单独说明。
- manifest 不能跳过安全、隐私、边界、测试 gate。

建议新增或扩展：

- `docs/governance/agent-goals/source-decoupling-2026-06.md`
- `docs/governance/red-zone/source-decoupling-2026-06.json`
- `tools/check_pr_governance.py` 支持 `change_type="major_refactor"` 或 `goal_mode=true`
- `scripts/verify-source-decoupling.sh`

## 当前 harness 过弱处：本次要增强

### 1. Provider/runtime 边界不够强

新增机器检查：

- runtime 不得 import `seektalent.providers.*`
- runtime 不得 import `seektalent.clients.*` 中 provider transport 细节
- providers 不得 import `seektalent.runtime.*`
- providers 只能依赖 source contract、core retrieval contract、models、clients
- `tach.toml` 不得允许 provider/runtime 双向依赖

建议检查命令：

```bash
uv run python tools/check_source_boundaries.py
uv run tach check
uv run python tools/check_tach_baseline.py
```

### 2. Provider-specific strings 不应进入 runtime

新增 grep 或 AST 检查，禁止 runtime 中出现：

- `liepin_opencli`
- `opencli`
- `SourceKind = Literal["cts", "liepin"]`
- `if source == "cts"`
- `if source == "liepin"`
- `from seektalent.providers`
- `from seektalent.clients.cts_client`

允许例外只可存在于：

- 测试 fixture
- migration/compat 删除前的短期中间文件；最终 PR 不允许保留
- 文档说明

### 3. BFF contract 不够强

新增检查：

- frontend component 不直接依赖 generated OpenAPI schema 类型。
- BFF contract/mapper 层必须有 contract tests。
- SSE 事件 schema 必须有稳定版本和 incremental event tests。
- 前端 query invalidation 不得全量重拉 session events。

### 4. 删除行为不够强

新增“删除清单”要求：

- 被新源契约替代的旧 runtime/provider glue 必须删除。
- 被 BFF mapper 替代的重复 projection helper 必须删除。
- 旧文档不得留作活跃文档。
- 删除清单必须写入 goal manifest 或 PR summary。

### 5. Liepin 稳定性检查不够强

新增 worker 生命周期测试：

- 多 session 隔离。
- 同一 browser process 下 context 隔离。
- repeated card/detail 请求不重复启动 browser。
- shutdown/TTL cleanup。
- OpenCLI 模式下仍保持必要顺序执行。
- worker error code 到 public source reason 的映射只在 Liepin 边界测试。

## 推荐 harness 改动验收

完成后必须能说明：

- 为什么本次大重构被允许跨层。
- 哪些边界仍被机器检查保护。
- 哪些限制被临时放宽，是否只对本 goal 生效。
- 哪些普通 PR 规则保持不变。
- 如何防止 Codex 随意扩展范围。

## 不要做的 harness

- 不要写超长逐文件 checklist。
- 不要要求每个函数先写设计文档。
- 不要要求 agent 每一步人工确认。
- 不要为每个目录创建 owner/approval 流程。
- 不要把目标拆成只能产出半成品的阶段。
