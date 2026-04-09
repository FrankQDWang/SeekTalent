---
report_id: report.company_background.search_ranking_retrieval_engineering.codex_synthesis_2026_04_07
report_type: company_background
domain_id: search_ranking_retrieval_engineering
title: CompanyBackground_搜推平台公司背景_整合版
source_model: Codex synthesis
generated_on: 2026-04-07
language: zh-CN
confidence_summary: medium
source_reports:
  - 搜推平台公司背景样本.md
  - 推荐与检索平台招聘画像.md
---

# CompanyBackground_搜推平台公司背景_整合版

## Summary

本稿只保留能稳定支持 `search_ranking_retrieval_engineering` 招聘检索的公司背景信号。核心不是公司名字本身，而是 `搜推平台 / 广告平台 / 招聘搜索 / 相关性平台` 这类业务语境。

## Canonical Terms

| Canonical Term | 定义 |
| --- | --- |
| Search Platform | 提供召回、排序、相关性优化的平台型业务。 |
| Ranking Platform | 持续优化排序策略和重排效果的业务环境。 |
| Recommendation-adjacent Platform | 与排序基础设施强相关、可迁移到搜推链路的平台背景。 |

## Alias Map

| Canonical | 常见别名 | Must-bind 规则 | 常见误召 |
| --- | --- | --- | --- |
| Search Platform | relevance platform、search infra | 必须绑定 `search/ranking` | 站内工具搜索 |
| Ranking Platform | recommendation platform、ranking infra | 最好绑定 `ranking/rerank` | 推荐运营 |

## Positive Signals

| signal_id | 描述 | 常见文本证据 | 置信度 |
| --- | --- | --- | --- |
| SRRE_BG_001 | 搜推平台背景 | `search platform`、`relevance platform` | medium |
| SRRE_BG_002 | 推荐/排序平台背景 | `ranking platform`、`recommendation platform` | medium |
| SRRE_BG_003 | 招聘搜索或广告相关性背景 | `job search`、`ads ranking` | medium |

## Negative/Confusion Signals

| signal_id | 描述 | 为什么容易误召 | 处理建议 |
| --- | --- | --- | --- |
| SRRE_BG_NEG_001 | 纯运营平台 | 有平台词，但无召回/排序链路 | 强降权 |
| SRRE_BG_NEG_002 | BI/报表平台 | 也有 ranking 词，但不是相关性平台 | 强降权 |

## Seed Branch Suggestions

- Query Terms：`search platform`、`recommendation`、`ranking`
- Must-have 绑定：`retrieval or ranking experience`
- Preferred 绑定：`observability`、`to-b delivery`

## Rerank Cues

- `search platform` 或 `ranking platform`
- `relevance`、`recommendation`、`retrieval`
- 避免把 `pure operation` 当成公司背景强信号

## Open Questions

- `广告平台` 是否应在某些 JD 下默认降权而不是加分
- `recommendation platform` 在纯推荐业务里是否要单独拆背景桶
