# 边界、非目标与禁止事项

## 本目标必须做

- 完成 source contract 和 registry。
- CTS/Liepin 迁移到 source adapter。
- runtime 删除 concrete provider 分支。
- Liepin worker browser lifecycle 修复。
- 前端/BFF/backend contract 重新整理。
- 修复 issues `#58`-`#69`。
- 删除旧代码和旧文档。
- 更新 harness。
- 更新 active docs。

## 本目标不做

除非现有测试明确要求，否则不要做：

- GraphQL。
- 新数据库。
- 新消息队列。
- 微服务拆分。
- 多租户权限模型重写。
- 新设计系统。
- prompt 大改。
- LLM provider 切换。
- dependency 大升级。
- live Liepin 网站 CI e2e。
- PyPI release 流程重写。

## 允许的破坏性变更

项目未上线，无用户。允许：

- 删除旧内部 API。
- 删除旧 DTO。
- 改测试 fixture。
- 改 BFF response shape。
- 改 active docs。
- 改 governance gate 以支持本目标。

但必须：

- 保留当前产品功能。
- 更新前端/BFF/runtime 调用方。
- 更新测试。
- 不留下旧兼容层。

## 禁止的“看似完成”

以下不算完成：

- 新建 `sources/contracts.py`，但 runtime 仍 import Liepin/CTS。
- registry 仍然 `if source == "cts"` / `if source == "liepin"` 硬编码。
- adapter 只是薄 wrapper，provider 仍依赖 runtime DTO。
- Liepin worker 只是把 `chromium.launch()` 包成 helper。
- BFF 只是把旧 response 改个名字。
- 前端仍在组件层直接依赖 generated schema。
- issue 只改代码不加测试。
- 文档只新增，不删除旧错误文档。
- 验证只跑单测，不跑 red-zone/workbench/worker gate。

## 处理不确定性的规则

Codex 不应因为局部不确定而停止。处理方式：

1. 读取代码和测试。
2. 找最小真实边界。
3. 写或更新 characterization test。
4. 完整实现。
5. 删除旧路径。
6. 运行验收命令。
7. 在 PR summary 中写出风险和证据。

不要用 TODO 或“后续再做”替代实现。
