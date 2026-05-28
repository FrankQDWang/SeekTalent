# 架构依赖观察报告

本文记录 Tach 第二阶段后的 `src/` 依赖事实，用于决定后续是否收紧模块边界。当前报告只描述事实和建议，不代表 CI 门禁。

## 当前边界配置

`tach.toml` 只覆盖 `src/`，暂不检查 `tests/` 和 `experiments/`。当前显式模块是 package-folder 级别的粗边界：

- `seektalent.clients`
- `seektalent.candidate_feedback`
- `seektalent.company_discovery`
- `seektalent.controller`
- `seektalent.finalize`
- `seektalent.reflection`
- `seektalent.requirements`
- `seektalent.retrieval`
- `seektalent.runtime`
- `seektalent.scoring`
- `seektalent_ui`

`root_module = "ignore"` 仍然保留。`seektalent.models`、`seektalent.config`、`seektalent.api`、`seektalent.cli`、`seektalent.evaluation`、`seektalent.llm` 等顶层文件暂时作为共享观察区，不在本阶段强制治理。

## Tach 状态

`uv run tach check` 当前是 advisory，不是干净门禁。当前已知 drift：

```text
src/seektalent/requirements/normalization.py cannot depend on seektalent.retrieval.query_compiler.compile_query_term_pool
```

下一步有两种小修方式：如果这个方向是有意的，就把 `tach.toml` 的 `seektalent.requirements` 依赖同步到 `seektalent.retrieval`；如果不是有意的，就把 query term pool 编译调用移出 requirements 层。

## 依赖图事实

`seektalent.runtime` 是编排中心。它依赖 `seektalent.clients`、`seektalent.candidate_feedback`、`seektalent.company_discovery`、`seektalent.controller`、`seektalent.finalize`、`seektalent.reflection`、`seektalent.requirements`、`seektalent.retrieval`、`seektalent.scoring`，并被 `seektalent_ui` 使用。

`seektalent_ui` 依赖 `seektalent.runtime` 和共享 core 文件，主要依赖来自 UI server、Workbench store、final-top10 projection 和 UI models：

- `seektalent_ui.server` 依赖 `seektalent.runtime`、`seektalent.config` 和 UI models。
- `seektalent_ui.final_top_candidates` 直接投影 Workbench 持久化的 Runtime finalization 字段。

当前 `src/seektalent` 没有显式依赖 `seektalent_ui` 或 `experiments`。验证命令：

```bash
rg -n "from seektalent_ui|import seektalent_ui|from experiments|import experiments" src/seektalent
```

当前无输出。

## 健康依赖方向

UI 依赖 core，core 不依赖 UI。这个方向健康，后续应优先保护。

runtime 依赖各 agent 阶段和 retrieval/client 模块，各 agent 阶段没有反向依赖 runtime。这个方向符合当前编排模型。

基础共享文件被多个模块依赖，但没有看到它们反向依赖 runtime 或 UI。当前不需要为了工具把这些文件拆开。

## 风险观察

高扇入文件：

- `src/seektalent/models.py` 被 17 个文件依赖。
- `src/seektalent/config.py` 被 13 个文件依赖。
- `src/seektalent/llm.py` 被 7 个文件依赖。
- `src/seektalent/prompting.py` 被 7 个文件依赖。

这些文件是稳定性关键点。修改它们时，Ruff/ty/pytest 之外，应额外关注下游调用者是否都同步更新。

高扇出文件：

- `src/seektalent/runtime/orchestrator.py` 依赖 15 个本地文件，是当前最大编排中心。
- `src/seektalent/clients/cts_client.py` 依赖 6 个本地文件。
- `src/seektalent/requirements/extractor.py`、`src/seektalent/controller/react_controller.py`、`src/seektalent/scoring/scorer.py` 各依赖 5 个本地文件。
- `src/seektalent_ui/server.py` 依赖 4 个本地文件。

这些文件的风险不是“依赖数量本身错误”，而是后续容易吸入跨层职责。新增 import 时应确认方向仍然是编排层向下依赖，而不是基础层反向依赖编排层。

## 暂不治理项

暂不拆 `models.py`。它扇入高，但当前仍是显式共享模型中心；过早拆分会带来大面积 churn。

暂不治理 `root_module = "ignore"` 下的顶层文件。`api.py`、`cli.py`、`evaluation.py`、`llm.py`、`config.py` 等文件仍处在合理共享区，先观察比配置化更稳。

暂不启用 Tach public interfaces、layers、check-external 或 tach test。这些功能会引入更多架构决策，本阶段证据还不足。

暂不把 `tests/` 和 `experiments/` 纳入 Tach。测试有大量 monkeypatch/stub，实验区本来就应保留较高自由度。

## 下一阶段候选动作

优先考虑最小 CI gate：只防止 `src/seektalent` 依赖 `seektalent_ui` 或 `experiments`。这类规则价值高、误报低，符合当前 AI coding 风险。

如果后续继续增长，再评估是否将 `models/config/llm/prompting` 设为 foundation 观察模块。但不要为了 Tach 先拆文件。

如果 `runtime/orchestrator.py` 继续扩大，优先做删除和局部抽取，而不是引入 manager/helper 层。抽取标准应是降低可读性负担或隔离真实复用逻辑。

Tach 进入 CI 的建议顺序：

1. 先只加反向依赖搜索或最小 `tach check`。
2. 保持 `root_module = "ignore"`。
3. 连续几轮低噪音后，再考虑更细的 foundation 边界。
