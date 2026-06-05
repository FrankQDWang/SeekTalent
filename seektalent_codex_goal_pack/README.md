# SeekTalent Codex Goal Pack

本目录是一组可直接交给 Codex Goal 的目标文档，用于一次性完成 SeekTalent 的源解耦、Liepin 稳定化、前端/BFF/后端分层、代码清理、issue 修复、文档刷新和 harness 重新校准。

入口文件是：

- `00-codex-goal.md`

建议把整个目录作为目标上下文交给 Codex，并明确：这不是调研任务，不是 MVP 骨架任务，不是只铺接口的任务；最终必须是可运行、可验证、可删除旧路径的完整实现。

## 文档顺序

1. `00-codex-goal.md`：完整目标、不可妥协项、完成定义。
2. `01-current-findings.md`：当前仓库事实与主要耦合点。
3. `02-harness-recalibration.md`：先做的 AI coding harness 校准。
4. `03-target-architecture.md`：目标系统结构。
5. `04-source-contract.md`：外部数据源注册与 runtime 解耦契约。
6. `05-liepin-stability.md`：Liepin/OpenCLI/worker 稳定化。
7. `06-frontend-bff-backend.md`：前端、BFF、后端三层边界。
8. `07-cleanup-deletion.md`：删代码、去 fallback、去重复的规则。
9. `08-issues-58-69.md`：12 个 GitHub issue 的纳入计划。
10. `09-docs-refresh.md`：文档重整。
11. `10-acceptance.md`：验收标准和验证命令。
12. `11-boundaries.md`：边界、非目标、禁止事项。
13. `12-execution-sequence.md`：推荐执行顺序。
14. `13-execution-control.md`：Codex Goal 长任务执行控制、恢复协议、progress ledger 和验证证据格式。

## 使用原则

- 先校准 harness，再改产品代码。
- 以可验证边界替代繁重流程。
- 允许大范围重构，但每个边界必须有测试或机器检查。
- 不保留未上线阶段的旧兼容层。
- 不允许只建目录、接口、空类、空测试或 TODO。
- 使用 Codex Goal 前，先读 `13-execution-control.md`，不要只把 `00-codex-goal.md` 当作完整运行协议。
- 如果 Goal 在 worktree 中执行，必须确认整个 `seektalent_codex_goal_pack/` 已被带入该 worktree。
- preflight 发现的无关 dirty files 只能记录和避让，不能覆盖或顺手清理。
