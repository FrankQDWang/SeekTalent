# 推荐执行顺序

本顺序是为了降低回归风险，不是把目标拆成半成品阶段。最终交付必须一次完整。

## 0. Goal run setup

先做：

- 读取 `13-execution-control.md`。
- 运行 preflight commands。
- 创建 progress ledger。
- 记录 HEAD、`origin/main`、merge-base、dirty state 和 stash inventory。
- 不覆盖未关联本目标的 dirty files；如果当前阶段必须编辑已 dirty 的产品文件，先暂停并询问。
- 确认 `seektalent_codex_goal_pack/` 在当前 checkout/worktree 中完整存在。
- 记录当前 active docs 是 current-state 输入，不是目标架构真相。

此时不要修改产品代码。

## 1. 校准 harness

先做：

- 新增 major refactor goal manifest。
- 调整 `tools/check_pr_governance.py`，允许本目标跨层。
- 新增或更新 source boundary checks。
- 调整 `tach.toml` 的目标依赖方向。
- 更新 red-zone verification。
- 加入删除清单要求。

此时不要开始大规模产品代码迁移。

## 2. 建立 source-neutral contract

做：

- 新增/重构 `src/seektalent/sources/`。
- 定义 source contracts、registry、公共 reason code。
- 把 runtime source DTO 中 provider-specific 字段剥离。
- 新增第三测试源 fixture，证明 runtime 不需要知道 source id。

不要只建空接口；fixture source 必须能完整跑到 result merge。

## 3. 迁移 CTS

做：

- 把 CTS planning/search/projection 从 runtime 移到 CTS source adapter。
- runtime 不再构造 `CTSQuery`。
- CTS adapter 调用现有 retrieval/core/client 能力。
- 保留 CTS behavior tests。
- 删除 runtime CTS 分支。

## 4. 迁移 Liepin

做：

- 把 `run_liepin_source_lane` 相关 runtime DTO 依赖改成 source contract。
- OpenCLI/worker mode/error mapping 只留在 Liepin provider。
- detail lease 泛化，Liepin-specific approval 留 source-local。
- 删除 runtime Liepin 分支和 OpenCLI reason code。
- 保留 card/detail/partial/blocked 行为测试。

## 5. 修 Liepin worker lifecycle

做：

- managed browser singleton 或 short TTL pool。
- context isolation。
- shutdown/cleanup。
- repeated request 测试。
- multi-session 测试。
- helper consolidation。

## 6. 修 runtime 性能和 identity

做：

- issue `#58` reverse indexes。
- issue `#63` connected components。
- 确保 source result merge 与 identity merge 行为稳定。

## 7. 修 PRF、SQLite、duplicates

做：

- issue `#61` negative support index。
- issue `#64` batch writes。
- issue `#65` UI privacy helpers。
- issue `#67` range overlap。
- issue `#68` filter canonicalization。
- issue `#69` text reader consolidation。

## 8. 整理 BFF/frontend

做：

- BFF contract/projection 层。
- frontend view model 层。
- issue `#59` incremental SSE。
- issue `#62` graph layout cache/bucket collision。
- 减少初始 over-fetch 和闪烁。
- 保持 Workbench tests/e2e。

## 9. 删除旧路径和旧文档

做：

- 删除旧 source glue。
- 删除旧 fallback/compat。
- 删除重复 helper。
- 删除或归档旧文档。
- 更新 active docs。

## 10. 全量验证

运行 `10-acceptance.md` 中所有命令。

失败处理：

- 修真正原因。
- 不新增防御性 fallback 掩盖失败。
- 不降低 gate。
- 不把失败测试标 skip，除非测试本身已与新架构冲突，并有替代测试覆盖。
