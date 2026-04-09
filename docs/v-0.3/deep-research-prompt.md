# SeekTalent v0.3 外部 Deep Research 提示词

以下提示词只保留为离线研究参考，不是当前 runtime contract。当前运行时只直接消费 knowledge packs，不再消费 reviewed reports 或 compiled cards。

```text
你是招聘搜索知识库研究员。请围绕以下目标 domain 产出一份可编译进内部知识库的结构化研究报告，不要写代码，不要输出 JSON，只输出 Markdown。

目标：
- report_type: {{REPORT_TYPE}}
- report_id: {{REPORT_ID}}
- knowledge_pack_id: {{KNOWLEDGE_PACK_ID}}
- 研究主题: {{DOMAIN_TOPIC}}
- 当前招聘场景: {{HIRING_CONTEXT}}
- 目标语言: 中文
- 输出用途: 用于离线人工研究与 pack 编写参考，不直接进入 runtime
- 约束: 只保留“能从 JD、notes、简历文本里观察到”的信号；禁止泛泛而谈；禁止空泛招聘建议；必须指出误召来源和边界

请严格按以下结构输出：

---
report_id: {{REPORT_ID}}
report_type: {{REPORT_TYPE}}
knowledge_pack_id: {{KNOWLEDGE_PACK_ID}}
title: {{TITLE}}
source_model: {{MODEL_NAME}}
generated_on: {{DATE}}
language: zh-CN
confidence_summary: high|medium|low
source_reports:
  - {{SOURCE_REPORT_ID_1}}
---

# Summary
用 5-10 句总结该 domain 对招聘搜索的意义、典型角色边界、容易混淆的方向。

# Canonical Terms
列出最重要的 10-20 个 canonical terms，每个 term 给一句定义。

# Alias Map
为每个重要 canonical term 列出常见别名、中文说法、英文写法、缩写、易混淆写法。

# Positive Signals
列出可观察的正向信号。每条都必须包含：
- signal_id
- 描述
- 常见文本证据
- 对应 role/vertical 的相关性
- 置信度 high|medium|low

# Negative/Confusion Signals
列出易误召词、伪相关背景、相似但不相同的 title/能力。每条都必须包含：
- signal_id
- 描述
- 为什么容易误召
- 应如何在检索或 rerank 时降权

# Seed Branch Suggestions
给出 3-5 个可用于第一轮搜索的 seed branches。每个 branch 必须包含：
- branch_name
- 适用场景
- 2-4 个 query terms
- 推荐与哪些 must-have 绑定
- 主要误召风险
- 不应与哪些词直接并集

# Rerank Cues
列出供人工整理 knowledge pack 时参考的 rerank 线索。按 must-have、preferred、risk、confusion 四类组织。

# Open Questions
列出仍然不确定、需要人工确认或多模型交叉验证的点。

输出要求：
- 内容必须服务招聘搜索，不是通用行业科普。
- 优先给出术语、别名、误召边界、可观察信号、短 seed branch。
- `Rerank Cues` 是 report 审核内容，不要把它写成“runtime 会直接读取的线上字段”。
- 如果某个说法在不同公司/行业差异很大，必须显式写出适用边界。
- 不要引用长段原文，不要输出链接列表，不要省略误召分析。
```
