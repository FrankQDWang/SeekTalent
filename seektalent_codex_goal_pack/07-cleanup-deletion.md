# 清理与删除规则

## 背景

项目 100% AI coding，当前维护成本主要来自：

- 保守兼容
- fallback 链
- 过时路径不删除
- 重复 helper
- 防御性代码
- provider/runtime/BFF/frontend 耦合
- 文档和代码不一致

本次没有线上用户，不应为旧结构继续付费。

## 删除优先级

优先删除：

1. runtime 中 CTS/Liepin/OpenCLI 分支。
2. provider 依赖 runtime DTO 的旧 glue。
3. 旧 source lane 类型中 provider-specific 字段。
4. 未被测试或产品路径使用的 fallback。
5. 仅为旧 API 保留的兼容 alias。
6. 重复 helper。
7. 旧分层分析文档或与真实代码不符的文档。
8. “先留着以后可能有用”的接口、空类、抽象基类。

## fallback 规则

允许 fallback 的地方：

- 外部 provider 边界，把网络/浏览器/登录失败转成 structured failure。
- LLM structured output parse failure 的 bounded retry。
- 用户可见 BFF/API 错误返回。

不允许 fallback 的地方：

- runtime 内吞 provider bug。
- 为兼容旧 source id 保留多套代码。
- BFF 同时支持新旧 DTO。
- 前端同时支持旧事件格式和新事件格式，除非测试明确证明正在迁移且本 PR 最终删除旧格式。
- type/lint 失败后用 `Any` 或 ignore 粗暴绕过。

## 防御性代码规则

不要写：

- 空 `except Exception: pass`
- broad catch 后返回空结果
- “理论上不可能”的 null fallback 层层传递
- 无用的 `if x is None: return default` 链
- utils/helpers/managers 容器类
- 没有真实生命周期的 class
- 只为过类型检查而存在的 wrapper

要写：

- 明确边界验证
- 明确错误 code
- 明确测试
- 小函数、局部 helper
- 真实抽象替换真实重复

## 重复代码合并规则

合并重复时必须保持语义：

- 先加或确认覆盖测试。
- 抽取到最小合理模块。
- 保留 provider-specific 投影在 provider 本地。
- 不建全局 junk drawer。
- 抽取后删除旧实现，而不是旧实现调用新实现再留着。

## 文件大小规则

当前 governance 对普通 PR 有文件行数限制。本次可以重构大文件，但目标是减少长期复杂度。

要求：

- 新增生产代码文件默认短小。
- 大文件只允许缩小或按职责拆分。
- 不允许把 800 行旧文件拆成 5 个同样混乱的文件。
- `workbench_store.py` 若无法一次拆完，至少把本次涉及的批量写和 projection/persistence 边界理清，不继续塞新职责。

## 文档删除规则

活跃文档只保留当前事实。历史材料若不再有外部协调价值，直接删；不要默认 archive。

任何保留的历史文档必须在 `docs/README.md` 中明确不是 source of truth。
