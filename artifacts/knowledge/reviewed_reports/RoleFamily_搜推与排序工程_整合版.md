---
report_id: report.role_family.search_ranking_retrieval_engineering.codex_synthesis_2026_04_07
report_type: role_family
domain_id: search_ranking_retrieval_engineering
title: RoleFamily_搜推与排序工程_整合版
source_model: Codex synthesis
generated_on: 2026-04-07
language: zh-CN
confidence_summary: high
source_reports:
  - RoleFamily_搜推与排序工程_知识库.md
  - 搜推排序工程知识库生成.md
---

# RoleFamily_搜推与排序工程_整合版

## Summary

本 domain 的核心是“召回/检索 + 排序/重排”的多阶段漏斗工程，而不是泛“搜索经验”或泛“机器学习经验”。  
判断一个候选人是否真做过搜推排序，最可靠的文本证据不是框架名，而是以下四类信号的组合：`召回/候选生成`、`排序/重排/LTR`、`评测指标(NDCG/MRR/MAP)`、`线上实验或延迟预算`。  
本整合版以结构化知识库稿为主，保留长稿里对 `SEO`、`ELK`、`BI 排名`、`广告出价` 等误召域的补充，但不把 `RAG/LLM`、`Query Understanding`、`PyTorch/JAX/TensorRT` 这类容易扩域或过时效的内容抬成一线核心定义。

## Canonical Terms

| Canonical Term | 定义 |
| --- | --- |
| Candidate Generation | 从全量库中高效选出候选集，为后续排序节省预算。 |
| Dense Retrieval / Vector Search | 用 embedding 做语义召回。 |
| Sparse Retrieval / BM25 | 用关键词和倒排索引做词面匹配召回。 |
| Inverted Index | 稀疏检索的底层索引结构。 |
| ANN | 用近似最近邻搜索支撑大规模向量召回。 |
| HNSW / FAISS / ScaNN | 常见 ANN 算法或库，属于高区分度工程信号。 |
| Hybrid Retrieval | 组合稀疏与稠密召回，提高覆盖和鲁棒性。 |
| Learning to Rank | 以排序指标为目标训练排序模型。 |
| LambdaMART | 工业界常见的 LTR / GBDT 排序器实现。 |
| Re-ranking | 对初召候选做更重的二阶段排序。 |
| Cross-Encoder | 对 query-doc 做交互式重排的常见模型形态。 |
| NDCG / MRR / MAP | 搜推排序里最常见的离线评测指标。 |
| Reciprocal Rank Fusion | 多路召回结果融合的实用方法。 |
| Online Controlled Experiments | 用 A/B test 验证排序策略的线上效果。 |
| Implicit Feedback / Position Bias | 点击日志、偏置修正和去偏排序学习的关键概念。 |

## Alias Map

| Canonical | 常见别名 | Must-bind 规则 | 常见误召 |
| --- | --- | --- | --- |
| Candidate Generation | 召回、matching、first-stage retrieval、候选生成 | 最好绑定 `ranking/rerank` | 只写“搜索经验” |
| Dense Retrieval | 向量召回、embedding retrieval、semantic retrieval、two-tower、dual encoder、DSSM | 必须绑定 `ANN/top-k/retrieval` | RAG 应用层或泛 NLP embedding |
| Sparse Retrieval / BM25 | BM25、关键词检索、lexical retrieval、倒排检索、relevance scoring | 必须绑定 `search/ranking` | ELK、日志检索、站内搜索接入 |
| ANN | ANNS、nearest neighbor、HNSW、FAISS、ScaNN | 必须绑定 `vector retrieval` | 通用近邻算法或非检索场景 |
| Learning to Rank | LTR、排序学习、GBDT ranker、LambdaRank、rank:ndcg、qid | 必须绑定 `NDCG/MRR/MAP` | BI 排名、风控 GBDT |
| Re-ranking | rerank、精排、final ranker、second-stage ranking | 必须绑定 `top-k retrieval` | 文本分类或纯 NLP 匹配 |
| Online Experiment | A/B test、online experiment、分桶、bucket、灰度 | 最好绑定 `ranking strategy` | 增长实验、投放优化 |
| CTR/CVR Ranking | CTR prediction、DeepFM、DIN、Wide&Deep | 必须绑定 `粗排/精排/ranking` | 广告出价或增长建模 |
| Query Understanding | QU、意图识别、query rewriting、纠错、NER | 必须绑定 `search engine/query` | 客服机器人、通用 NLP |

## Positive Signals

| signal_id | 描述 | 常见文本证据 | 置信度 |
| --- | --- | --- | --- |
| SRRE_PS_001 | 同段出现召回与排序/重排 | `candidate generation` + `ranking/rerank` | high |
| SRRE_PS_002 | 检索实现抓手明确 | `BM25/倒排` 或 `ANN/HNSW/FAISS/ScaNN` | high |
| SRRE_PS_003 | 排序指标明确 | `NDCG`、`MRR`、`MAP`、`Precision@K` | high |
| SRRE_PS_004 | 线上闭环存在 | `A/B test`、`online experiment`、`bucket` | high |
| SRRE_PS_005 | LTR 工程细节明确 | `LambdaMART/qid/rank:ndcg/grouped dataset` | medium |
| SRRE_PS_006 | 二阶段重排成本意识 | `top-k rerank`、`cross-encoder expensive`、`latency budget` | medium |
| SRRE_PS_007 | 混合召回或融合策略 | `BM25 + embedding`、`hybrid retrieval`、`RRF` | medium |
| SRRE_PS_008 | 偏差与反馈意识 | `click logs`、`position bias`、`debias/unbiased LTR` | medium |
| SRRE_PS_009 | 推荐排序可迁移但证据充足 | `CTR/CVR/DeepFM/DIN` 且明确绑定 `粗排/精排` | medium |

## Negative/Confusion Signals

| signal_id | 描述 | 为什么容易误召 | 处理建议 |
| --- | --- | --- | --- |
| SRRE_NEG_001 | SEO / SERP | 用 ranking 词，但不是自研排序链路 | 强降权 |
| SRRE_NEG_002 | ELK / 日志检索 | 只会 ES/DSL 或日志分析 | 强降权 |
| SRRE_NEG_003 | BI / 报表排名 | 也会写排序、TopN、报表指标 | 强降权 |
| SRRE_NEG_004 | RAG-only / LLM app | 有检索词，但缺排序指标与线上闭环 | 中到强降权 |
| SRRE_NEG_005 | Ads bidding | CTR/CVR 强，但核心是 RTB/出价 | 中到强降权 |
| SRRE_NEG_006 | Frontend search UI | 搜索框、联想词、过滤器 UI | 中降权 |
| SRRE_NEG_007 | Pure ML without IR loop | 只有 PyTorch/TF/模型训练，无召回/排序链路 | 中降权 |
| SRRE_NEG_008 | DB/OS retrieval ambiguity | `retrieval` 出现在数据库/系统领域，不是检索排序 | 中降权 |

## Seed Branch Suggestions

### Branch 1: 双塔向量召回与 ANN

- 适用场景：语义召回、高并发向量检索、海量候选集
- Query Terms：`two-tower`、`dual encoder`、`HNSW`、`FAISS`
- Must-have 绑定：至少命中 `retrieval/topK/ANN/vector search`
- 主要误召风险：泛 NLP embedding、RAG 应用层
- Do NOT Union：`RAG`、`prompt`、`chatbot`

### Branch 2: LTR 精排与 GBDT 排序

- 适用场景：精排、相关性优化、排序策略主岗
- Query Terms：`learning to rank`、`LambdaMART`、`NDCG`、`qid`
- Must-have 绑定：至少命中 `LTR/rerank/MRR/MAP`
- 主要误召风险：风控评分、通用 GBDT 建模
- Do NOT Union：`forecast`、`credit score`、`BI`

### Branch 3: Cross-Encoder / BERT Rerank

- 适用场景：语义相关性强、top-k 重排、二阶段排序
- Query Terms：`cross-encoder`、`BERT reranker`、`re-ranking`、`MRR@10`
- Must-have 绑定：至少命中 `rerank/topK/BM25 + rerank`
- 主要误召风险：文本分类、相似度匹配、RAG-only
- Do NOT Union：`sentiment`、`NER`、`text classification`

### Branch 4: BM25 / 倒排 / 混合检索

- 适用场景：主搜、相关性优化、搜索基础设施与混合召回
- Query Terms：`BM25`、`inverted index`、`relevance scoring`、`hybrid retrieval`
- Must-have 绑定：至少命中 `ranking/LTR/rerank`
- 主要误召风险：日志检索、ES 接入后端
- Do NOT Union：`ELK`、`log search`、`observability`

### Branch 5: 条件分支

- 主搜岗位用：`query understanding`、`query rewriting`、`NER`
- 推荐排序岗位用：`CTR prediction`、`DeepFM`、`DIN`
- Must-have 绑定：主搜必须绑 `search/query`；推荐必须绑 `粗排/精排/ranking`
- 主要误召风险：通用 NLP 或广告投放

## Rerank Cues

### Must-have

- `召回 + 排序/重排` 在同一项目或同一段落共现
- 至少一个检索实现抓手：`BM25/倒排` 或 `ANN/HNSW/FAISS/ScaNN`
- 至少一个排序评测指标：`NDCG/MRR/MAP/Precision@K`
- 至少一个线上闭环或工程约束：`A/B`、`latency budget`、`online experiment`

### Preferred

- `LambdaMART/qid/rank:ndcg`
- `hybrid retrieval/RRF`
- `position bias/debias`
- `topK rerank/cross-encoder cost`
- 推荐排序里的 `CTR/CVR` 但已明确挂在粗排/精排链路上

### Risk

- 只写“推荐/搜索经验”没有 IR/LTR 专有名词
- 只写 Elasticsearch 但无相关性优化
- 只写 CTR 模型，没有在线排序链路
- 只有框架词，没有指标和实验

### Confusion

- `SEO/SERP`
- `ELK/log search`
- `RAG-only`
- `ads bidding/RTB/eCPM`
- `frontend search UI`

## Open Questions

- `Query Understanding` 应该作为主搜子域扩展，还是进入全域 canonical core
- 推荐排序背景在招聘/搜索岗位中的可迁移阈值应该设多高
- `RAG` 相关词在部分团队是否应视为相邻可迁移背景，而不是默认强混淆
- `CTR/CVR` 候选人如果没有 `query/retrieval` 词，是否允许进候选池要看 JD 是否接受 ads-to-search 转岗
- 对企业内网搜索背景，是否需要单独加一个“流量规模/延迟级别”校正项
