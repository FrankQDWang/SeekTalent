---
report_id: report.role_family.llm_agent_rag_engineering.codex_synthesis_2026_04_07
report_type: role_family
domain_id: llm_agent_rag_engineering
title: RoleFamily_LLM与Agent应用研发_整合版
source_model: Codex synthesis
generated_on: 2026-04-07
language: zh-CN
confidence_summary: high
source_reports:
  - RoleFamily_LLM与Agent应用研发_知识库.md
  - 大模型工程研发报告生成.md
---

# RoleFamily_LLM与Agent应用研发_整合版

## Summary

本 domain 关注的是“把 LLM 真正接进业务系统并可稳定上线”的后端与平台工程，不是提示词写作，也不是底模训练。核心边界落在可验证的工程链路上：`RAG/检索`、`tool/function calling`、`stateful workflow orchestration`、`evaluation/tracing`、`权限与安全`、`延迟/成本/稳定性治理`。  
高质量候选人的文本证据通常表现为“系统组件 + 质量指标 + 上线语境”共现，而不是单独出现 `AI`、`LLM`、`Agent`、`OpenAI API` 之类的泛词。  
本整合版保留两份原始报告的共识部分，把时效性很强的框架站队和行业叙事降级为补充信号，不把热框架本身当作 must-have。

## Canonical Terms

| Canonical Term | 定义 |
| --- | --- |
| RAG | 用外部知识检索结果增强生成过程的系统架构，核心是“检索链路真实存在且可评估”。 |
| Agentic Workflow | 让模型在多步循环中规划、调用工具、消费工具结果并决定终止条件的工作流。 |
| Tool / Function Calling | 模型输出结构化参数去调用外部工具、API 或函数。 |
| Structured Outputs | 用 schema 约束模型输出，使响应可被程序稳定消费。 |
| Workflow Orchestration | 把检索、推理、工具调用、校验、人审和恢复组织成可追踪流程。 |
| Ingestion & Parsing | 文档加载、解析、元数据抽取、索引写入的摄取流水线。 |
| Chunking | 把原始文档切成可检索、可溯源的片段。 |
| Embeddings | 把文本映射为稠密向量，供相似度检索与召回使用。 |
| ANN / Vector Database | 支持高维向量索引、近似检索和 metadata filter 的底层检索设施。 |
| Hybrid Retrieval | 组合关键词检索和语义检索，提高覆盖与精度。 |
| Reranker | 对初召候选做二阶段精排，典型是 cross-encoder 或更强相关性模型。 |
| Retrieval / RAG Evaluation | 用 MRR、Hit@K、groundedness、faithfulness 等评价检索或答案质量。 |
| Tracing / Observability | 对检索、调用、路由、延迟和错误做链路追踪与观测。 |
| Authorization-aware Retrieval | 把 ACL/RBAC/metadata filtering 纳入检索链路，避免越权召回。 |
| Prompt Injection Defense | 针对外部文档或输入中的恶意指令做检测、隔离或防护。 |

## Alias Map

| Canonical | 常见别名 / 框架词 | Must-bind 规则 | 常见误召 |
| --- | --- | --- | --- |
| RAG | 检索增强、知识库问答、grounded QA、enterprise search | 必须绑定 `retrieval/vector db/top-k/rerank` 任一 | 只拼 prompt 的“伪 RAG” |
| Agentic Workflow | 智能体、agentic、multi-step、state graph、HITL | 必须绑定 `tool calling/schema/state/termination` | 只会聊天或插件调用的 chatbot |
| Tool / Function Calling | 工具调用、函数调用、plugin、API orchestration、MCP | 必须绑定 `structured outputs/schema` | 只会接 SDK 的 API wrapper |
| Workflow Orchestration | 状态机、graph workflow、checkpoint、handoff、LangGraph、CrewAI、AutoGen | 必须绑定 `multi-step/tool/human review` | 只画流程图，无工程实现 |
| Ingestion & Parsing | ETL、文档摄取、indexing pipeline、parser、loader | 必须绑定 `chunking/embedding/index` | 通用数据 ETL |
| Vector DB / ANN | 向量库、vector store、ANN、HNSW、Milvus、Qdrant、pgvector、FAISS | 必须绑定 `retrieval/RAG` | 推荐系统 embedding 或纯 SDK 调用 |
| Hybrid Retrieval | BM25+向量、sparse+dense、RRF、fusion | 必须绑定 `RAG/LLM` | 传统 IR/LTR 但无 LLM 链路 |
| Reranker | cross-encoder、BERT rerank、二阶段精排 | 必须绑定 `top-k retrieval` | 文本分类或相似度模型 |
| Eval / Tracing | RAGAS、TruLens、LangSmith、Phoenix、groundedness | 必须绑定 `RAG/agent/retrieval` | 通用监控/SRE |
| ACL / Security | metadata filtering、RBAC、namespace、tenant isolation、prompt injection | 必须绑定 `knowledge base/retrieval` | 纯 IAM/安全工程 |

## Positive Signals

| signal_id | 描述 | 常见文本证据 | 置信度 |
| --- | --- | --- | --- |
| LLMAGENT_PS_001 | 端到端 RAG 链路 | `RAG` + `retriever/vector db/rerank/citations` | high |
| LLMAGENT_PS_002 | 工具调用具备结构化接口 | `function calling/tool calling` + `schema/JSON/structured outputs` | high |
| LLMAGENT_PS_003 | 有状态工作流或人工审核节点 | `stateful workflow/checkpoint/HITL/handoff` | high |
| LLMAGENT_PS_004 | 检索二阶段优化 | `hybrid search`、`BM25 + vector`、`cross-encoder rerank` | high |
| LLMAGENT_PS_005 | 可评估性与可观测性 | `RAGAS/groundedness/tracing/LangSmith/Phoenix` | high |
| LLMAGENT_PS_006 | 企业权限与多租户检索 | `ACL/RBAC` + `metadata filtering/tenant/namespace` | high |
| LLMAGENT_PS_007 | 安全与注入防护 | `prompt injection`、`policy guardrail`、`doc-level auth` | medium |
| LLMAGENT_PS_008 | 工程化约束意识 | `latency`、`streaming`、`cache`、`async`、`cost control` | medium |
| LLMAGENT_PS_009 | 自研编排或不依赖热框架 | 没有流行框架名，但有 `state/tool/schema/checkpoint` 完整链路 | medium |

## Negative/Confusion Signals

| signal_id | 描述 | 为什么容易误召 | 处理建议 |
| --- | --- | --- | --- |
| LLMAGENT_NEG_001 | Prompt-only / Chatbot-only | 只写 prompt、角色设定、对话体验，无系统链路 | 强降权 |
| LLMAGENT_NEG_002 | Demo-only | 只有 PDF QA、Notebook、hackathon、教程型项目 | 强降权 |
| LLMAGENT_NEG_003 | Fine-tune-only | 只有 LoRA/预训练/蒸馏，无应用系统落地 | 强降权 |
| LLMAGENT_NEG_004 | Traditional IR only | 有 BM25/LTR/倒排，但无 LLM/工具调用 | 中到强降权 |
| LLMAGENT_NEG_005 | RecSys embedding only | 只有 CTR/召回 embedding，无检索知识库语境 | 中到强降权 |
| LLMAGENT_NEG_006 | Plugin marketing | 写“Agent 平台/插件生态”但无 schema/state/termination | 中降权 |
| LLMAGENT_NEG_007 | Enterprise KB without eval/security | 只说“知识库上线”，无评估、trace、ACL、安全 | 中降权 |
| LLMAGENT_NEG_008 | Frontend AI wrapper | 强调 React、页面、组件封装，无后端系统链路 | 中降权 |

## Seed Branch Suggestions

### Branch 1: RAG 工程化主干

- 适用场景：知识库问答、企业搜索增强、文档助手
- Query Terms：`RAG`、`vector database`、`retriever`、`embedding`
- Must-have 绑定：至少命中 `ANN/HNSW/metadata filtering/rerank`
- 主要误召风险：demo 型 PDF QA、推荐系统 embedding
- Do NOT Union：`prompt engineering`、`LoRA`、`Stable Diffusion`

### Branch 2: 混合检索与二阶段精排

- 适用场景：强调召回质量和相关性优化的 RAG/企业搜索岗位
- Query Terms：`hybrid search`、`BM25`、`RRF`、`cross-encoder`
- Must-have 绑定：至少命中 `RAG/LLM/retrieval`
- 主要误召风险：传统 IR/LTR 工程师
- Do NOT Union：`Lucene tuning only`、`pure LTR`

### Branch 3: Agent 工具调用与状态编排

- 适用场景：多步任务执行、自动化助手、业务系统操作型 Agent
- Query Terms：`tool calling`、`structured outputs`、`workflow`、`checkpoint`
- Must-have 绑定：至少命中 `schema/stateful/HITL`
- 主要误召风险：插件使用者、prompt 工程师
- Do NOT Union：`prompt template`、`chatbot`

### Branch 4: Eval / Tracing / Governance

- 适用场景：生产级 LLM 系统、治理与质量保障岗位
- Query Terms：`RAG evaluation`、`groundedness`、`tracing`、`LangSmith`
- Must-have 绑定：至少命中 `RAG/agent/retrieval`
- 主要误召风险：通用监控、测试平台、SRE
- Do NOT Union：`Prometheus only`、`Grafana only`

### Branch 5: 企业权限与安全检索

- 适用场景：多租户企业知识库、权限敏感型 Agent
- Query Terms：`metadata filtering`、`RBAC`、`prompt injection`、`multi-tenant`
- Must-have 绑定：至少命中 `retrieval/knowledge base/vector db`
- 主要误召风险：通用 IAM、SOC、安全工程
- Do NOT Union：`IAM only`、`WAF only`

## Rerank Cues

### Must-have

- 至少命中一组主链路证据：`RAG/retrieval/vector/ANN` 或 `tool calling/schema/stateful workflow`
- 至少命中一组质量/运维证据：`eval metrics/groundedness/RAGAS` 或 `tracing/observability`
- 文本里最好出现“组件 + 指标 + 上线/运行”的共现，而不是孤立 buzzword

### Preferred

- `hybrid retrieval + rerank`
- `ACL/multi-tenant/security`
- `latency/cost/cache/streaming/async`
- `citations/provenance`
- `self-built orchestration` 或深度定制而非仅用脚手架

### Risk

- `LangChain only`、`OpenAI API only`、`ChatGPT integration only`
- `FastAPI/Redis/SDK` 很多，但没有检索/工作流主链路
- 只谈“上线”但没有任何 trace、eval、压测、故障处理

### Confusion

- `BM25/LTR` 但没有 LLM/Agent
- `embedding` 但只有推荐/广告语境
- `tool/plugin` 但没有 schema/状态/终止条件
- `security/RBAC` 但不是检索过程里的授权过滤

## Open Questions

- 是否把 `MCP`、`GraphRAG`、`Semantic Cache` 作为二级 specialized terms，而不是一线 canonical core
- 对“自研编排但没用热门框架”的候选，是否要显式提高 preferred 权重
- `RAGAS/LangSmith` 这类评估与观测框架，是否必须与“评测集/实验流程”共现才做强加分
- 企业权限与安全信号在简历里常被省略，是否需要面试 notes 模板补采
- `fine-tuning` 何时算加分而不是混淆项，建议只在同时命中 RAG/Agent 主链路时加分
