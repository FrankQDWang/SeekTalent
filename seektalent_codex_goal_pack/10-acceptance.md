# 验收标准与验证命令

## 必须满足的架构验收

### Source 解耦

- `src/seektalent/runtime/**` 不 import `seektalent.providers.*`。
- `src/seektalent/runtime/**` 不 import CTS/Liepin transport/client/worker。
- `src/seektalent/providers/**` 不 import `seektalent.runtime.*`。
- runtime 生产代码没有 `SourceKind = Literal["cts", "liepin"]`。
- runtime 生产代码没有 `if source == "cts"` / `if source == "liepin"` 分支。
- runtime 生产代码没有 `opencli` / `liepin_opencli`。
- `tach.toml` 不再允许 provider/runtime 双向依赖。
- 新增第三测试源可通过 registry 执行，不改 runtime。

### Liepin

- Liepin source adapter 封装 worker mode、OpenCLI、reason mapping。
- worker 复用 managed browser。
- context/session 隔离有测试。
- card/detail 路径测试通过。
- OpenCLI 顺序执行语义保留在 Liepin 边界。
- Liepin failure 不被 runtime fallback 掩盖。

### Frontend/BFF/backend

- BFF contract/projection 层清晰。
- 前端 components 不直接消费 generated OpenAPI schema 类型。
- SSE event append 增量化。
- graph layout 缓存/去 O(n²) 扫描。
- backend model 改动主要由 BFF mapper 吸收。

### Cleanup

- 旧 runtime source glue 删除。
- duplicate helper 删除。
- stale docs 删除或归档。
- 没有新增防御性 fallback spam。
- 没有空接口、空 adapter、TODO 骨架。

## Python 验证命令

必须运行：

```bash
uv run ruff check src tests experiments
uv run ty check src tests
uv run pytest
uv run python tools/check_arch_imports.py
uv run python tools/check_tach_baseline.py
uv run python tools/check_privacy_gate.py --base origin/main
uv run python tools/check_ai_bad_smells.py --base origin/main
```

新增后还必须运行：

```bash
uv run python tools/check_source_boundaries.py
scripts/verify-source-decoupling.sh
```

如果新增脚本名称不同，必须在 PR summary 中写明等价脚本。

## Red-zone 验证

必须运行：

```bash
scripts/verify-red-zone.sh
```

如果该脚本因本次 harness 调整被更新，更新后的脚本仍必须覆盖 runtime、provider、Liepin worker、privacy、AI bad-smell、architecture checks。

## Workbench/BFF/frontend 验证

必须运行：

```bash
scripts/verify-dev-workbench.sh
```

以及：

```bash
cd apps/web-svelte
bun run test
bun run test:e2e
bun run build
```

## Liepin worker 验证

必须运行：

```bash
cd apps/liepin-worker
bun test
bun run typecheck
bun run boundary-check
bun run compatibility-gate
```

## Issue 专项验证

必须运行或用等价替代覆盖：

```bash
uv run pytest tests/test_runtime_candidate_identity.py tests/test_runtime_state_flow.py tests/test_runtime_multi_source_round_dispatch.py
uv run pytest tests/test_llm_prf.py
uv run pytest tests/test_workbench_semantic_guardrails.py tests/test_workbench_api.py
uv run pytest tests/test_workbench_runtime_owned_execution.py tests/test_liepin_runtime_source_lane.py tests/test_corpus_store.py tests/test_corpus_runtime.py
uv run pytest tests/test_workbench_security_audit.py
uv run pytest tests/test_query_plan.py tests/test_cts_provider_adapter.py tests/test_liepin_provider_adapter.py
uv run pytest tests/test_cli.py tests/test_claude_code_baseline.py tests/test_jd_text_baseline.py tests/test_openclaw_baseline.py
```

## Grep/AST 验收

新增 source boundary check 可以用 AST 实现，至少覆盖以下逻辑：

```bash
! grep -R "from seektalent.providers" src/seektalent/runtime
! grep -R "import seektalent.providers" src/seektalent/runtime
! grep -R "from seektalent.runtime" src/seektalent/providers
! grep -R "liepin_opencli\|opencli" src/seektalent/runtime
! grep -R "Literal\[\"cts\", \"liepin\"\]" src/seektalent/runtime src/seektalent/sources
```

不要只靠 grep；最终最好用 AST，因为 import 可以换行。

## Execution-control 验收

最终必须存在并更新：

- `docs/governance/agent-goals/source-decoupling-2026-06-progress.md`

该 ledger 必须记录：

- run identity；
- start HEAD、`origin/main`、merge-base 和 dirty-state 处理决策；
- 每个 execution phase 的状态；
- 每个失败命令和修复后的重跑结果；
- 所有验收命令的结果；
- 删除清单；
- 已知风险。

如果 Goal 因暂停、上下文压缩或中断恢复，PR summary 必须说明恢复点来自哪一条 ledger 记录。

## PR summary 必须包含

- Harness 调整摘要。
- Source 解耦摘要。
- Liepin 稳定化摘要。
- BFF/frontend 摘要。
- Issue `#58`-`#69` 完成清单。
- 删除清单。
- 文档更新清单。
- 验证命令结果。
- Progress ledger 路径和恢复记录摘要。
- 已知风险；如果没有，写“无已知未覆盖风险”。
