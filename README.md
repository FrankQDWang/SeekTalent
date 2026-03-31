# cv-match

`cv-match` 是一个本地 CLI 实验项目，用 `uv + Python + pydantic-ai` 实现“确定性流程 + 小 agent 模块”的多轮简历检索与评分闭环。

目标不是上线，而是把下面几件事做干净：

- 基于 `JD + 寻访须知` 提取结构化检索策略
- 按固定轮次执行 ReAct 风格检索循环
- 每轮进行 self-reflection
- 受控并发地做单简历评分
- 落盘完整 trace、events、run config 和最终答案
- 在 mock 模式下本地直接跑通

## 为什么不是一个巨大 agent

这里刻意没有做“开放式任意工具调用”的大 agent，而是拆成：

- `strategy agent`
- `scoring agent`
- `reflection agent`
- `finalize agent`
- `runner / orchestrator`

这样做的原因很直接：

- 轮次、去重、top5 保留、停止条件都由 Python 显式控制，行为稳定且可复盘
- prompt 职责单一，后续更容易替换和迭代
- tracing 不依赖隐藏 chain-of-thought，而是记录结构化摘要
- mock 模式和真实模式共用同一流程骨架

## 为什么只在评分阶段并发

项目只把并发收敛在“单简历评分 fan-out / fan-in”这一步：

- 这是最自然的独立任务边界
- 其他步骤保持同步，CLI 体验简单
- 并发上限可配置，默认 `5`
- 不引入全局异步架构、任务队列或 worker 系统

## 项目结构

```text
.
├── cts.validated.yaml
├── examples/
│   ├── jd.md
│   └── notes.md
├── pyproject.toml
├── README.md
├── runs/
└── src/cv_match/
    ├── agent/
    │   ├── finalize_agent.py
    │   ├── reflection_agent.py
    │   ├── scoring_agent.py
    │   └── strategy_agent.py
    ├── clients/
    │   ├── cts_client.py
    │   └── cts_models.py
    ├── cli.py
    ├── config.py
    ├── mock_data.py
    ├── models.py
    ├── normalization.py
    ├── prompting.py
    ├── prompts/
    │   ├── finalize.md
    │   ├── reflection.md
    │   ├── scoring.md
    │   └── strategy_extraction.md
    ├── runner.py
    └── tracing.py
```

## CTS 实现依据

当前 CTS adapter 只基于这一份本地 OpenAPI：

- `cts.validated.yaml`

这也是当前目录中唯一一份包含 `openapi / paths / components` 的正式规范，因此被选为唯一事实来源。

### 当前已实现的 CTS 边界

真实 CTS adapter 当前只映射了规范中语义明确、且本项目实际会用到的字段：

- `keyword`
- `company`
- `position`
- `school`
- `workContent`
- `location`
- `page`
- `pageSize`

下面这些字段虽然出现在规范中，但因为规范只说明“接受整数”，没有发布可安全依赖的枚举语义，所以当前刻意不自动映射：

- `degree`
- `schoolType`
- `workExperienceRange`
- `gender`
- `age`
- `active`

`exclude_ids` 在 OpenAPI 里没有发布支持，所以 runner 统一做本地 dedup 和 shortage 处理。

最重要的一条业务约束已经在 adapter 中硬限制：

- 不会把整份 `JD` 原文透传给 CTS

## 安装

```bash
uv sync
```

如果你要使用真实模型，还需要设置：

```bash
export OPENAI_API_KEY=...
```

如果不设置 `OPENAI_API_KEY`，项目会自动切到本地 deterministic fallback，保证 mock 模式仍能完整跑通。

## 环境变量

参考 `.env.example`。

关键变量：

- `CVMATCH_CTS_BASE_URL`
- `CVMATCH_CTS_TENANT_KEY`
- `CVMATCH_CTS_TENANT_SECRET`
- `CVMATCH_CTS_TIMEOUT_SECONDS`
- `CVMATCH_STRATEGY_MODEL`
- `CVMATCH_SCORING_MODEL`
- `CVMATCH_FINALIZE_MODEL`
- `CVMATCH_REFLECTION_MODEL`
- `CVMATCH_REASONING_EFFORT`
- `CVMATCH_MIN_ROUNDS`
- `CVMATCH_MAX_ROUNDS`
- `CVMATCH_SCORING_MAX_CONCURRENCY`
- `CVMATCH_MOCK_CTS`
- `CVMATCH_ENABLE_REFLECTION`

默认值已经贴合本地实验：

- `min_rounds=3`
- `max_rounds=5`
- `scoring_max_concurrency=5`
- `mock_cts=true`
- `reflection=true`

## 运行

### mock 模式

```bash
uv run python -m cv_match.cli --jd-file examples/jd.md --notes-file examples/notes.md --mock-cts
```

### 直接传文本

```bash
uv run python -m cv_match.cli --jd "Python agent engineer..." --notes "优先上海，不要纯前端" --mock-cts
```

### 真实 CTS

先配置：

- `OPENAI_API_KEY`
- `CVMATCH_CTS_TENANT_KEY`
- `CVMATCH_CTS_TENANT_SECRET`

然后运行：

```bash
uv run python -m cv_match.cli --jd-file examples/jd.md --notes-file examples/notes.md --real-cts
```

## 多轮流程

第 0 步：

- `strategy agent` 从 `JD + 寻访须知` 生成 `SearchStrategy`

进入评分前还有一个显式的纯 Python 规范化步骤：

- CTS 返回的单份检索结果先被整理成统一的 `NormalizedResume`
- scoring agent 只看 `NormalizedResume`
- 不把原始 CTS 杂乱 payload 直接暴露给 scoring prompt
- 这样后续 CTS 字段变化时，主要修改点被收敛在 normalization 层

第 1 轮：

- CTS 检索目标 `10`
- 对实际拿到的去重后候选人做并发评分
- 保留 `top5`

第 2 轮及以后：

- 每轮新增检索目标 `5`
- 评分池 = `上轮 top5 + 本轮新增`
- 重新评分并更新 `top5`

轮次规则：

- 至少 `3` 轮
- 最多 `5` 轮
- 每轮都触发 reflection
- 第 3 轮后允许 stop

## top5 如何跨轮保留

runner 会保存上一轮 `top5` 的 `resume_id` 和候选对象。

下一轮评分池构造规则：

- 第 1 轮：只评分新检索结果
- 后续轮次：先放入上一轮 `top5`
- 再补入本轮新增候选
- 最终统一排序并截断到 `top5`

排序是确定性的，规则固定为：

1. `fit_bucket` 优先
2. `overall_score` 降序
3. `must_have_match_score` 降序
4. `risk_score` 升序
5. `resume_id` 升序

因此并发返回顺序不会影响最终排名。

## 简历规范化摘要器

评分前会执行 [normalization.py](/Users/frankqdwang/Agents/cv-match/src/cv_match/normalization.py) 中的 `ResumeNormalizer`。

这一层优先做确定性数据整理，不额外调用 LLM。目标是把 CTS 检索结果整理成稳定、紧凑、可审计的评分输入。

当前 `NormalizedResume` 至少包含：

- `resume_id`
- `candidate_name`
- `headline`
- `current_title`
- `current_company`
- `years_of_experience`
- `locations`
- `education_summary`
- `skills`
- `industry_tags`
- `language_tags`
- `recent_experiences`
- `key_achievements`
- `raw_text_excerpt`
- `completeness_score`
- `missing_fields`
- `normalization_notes`

### normalization 规则

- 尽量直接做字段映射，不做复杂语义推断
- `recent_experiences` 只保留最近且有用的 2 到 4 段经历
- `skills / tags` 会去重、去空值、做简单清洗
- `raw_text_excerpt` 采用受控截断，避免超长原文直接进入评分 prompt
- `completeness_score` 反映信息完备度，不代表匹配度
- 如果 CTS 没有稳定 `resume_id`，会构造可复现的 fallback fingerprint

### scoring 输入控制

单分支评分只接收：

- 同一份 scoring prompt
- 当前轮次的 `ScoringContext`
- 一份 `NormalizedResume`

不会混入同轮其他候选人的信息，也不会在单分支里做跨候选人比较。

## 去重

优先使用稳定 `resume_id`。

如果真实 CTS 响应里没有稳定 ID，adapter 会对稳定字段做 deterministic hash，生成 fallback dedup key。

当前 dedup 行为：

- 每轮维护全局 `seen_resume_ids`
- CTS 请求里保留 `exclude_ids` 语义，但因 OpenAPI 未发布服务端支持，当前不下发给接口
- runner 在本地执行 dedup
- 如果重复导致本轮新候选不足，会记录 shortage
- 如果连续拿不到足够新候选，会收敛退出

fallback dedup key 的使用也会通过 normalization trace 暴露出来。

## 并发评分

评分阶段是单简历 fan-out / fan-in：

- 每个分支只拿到：
  - 同一份 scoring prompt
  - 同一份 `ScoringContext`
  - 一份 `NormalizedResume`
- 并发上限由 `scoring_max_concurrency` 控制
- 每个分支最多重试 `1` 次
- 失败分支会落盘 `score_branch_failed` 和 `ScoringFailure`
- 单个分支失败不会拖垮整轮

### 评分标尺

评分 prompt 明确要求：

- 先判断 `fit_bucket`
- 再给 `overall_score`
- `must-have` 直接影响 `fit_bucket`
- `preferred` 用于拉开分差
- `negative / exclusion` 会提高 `risk_score`，必要时直接判 `not_fit`
- 缺失证据不等于满足要求

当前单简历评分输出包含：

- `resume_id`
- `fit_bucket`
- `overall_score`
- `must_have_match_score`
- `preferred_match_score`
- `risk_score`
- `risk_flags`
- `matched_must_haves`
- `missing_must_haves`
- `matched_preferences`
- `negative_signals`
- `reasoning_summary`
- `evidence`
- `confidence`

## tracing / runs 目录

每次运行都会生成：

- `runs/<timestamp>_<run_id>/trace.log`
- `runs/<timestamp>_<run_id>/events.jsonl`
- `runs/<timestamp>_<run_id>/run_config.json`
- `runs/<timestamp>_<run_id>/round_summaries.json`
- `runs/<timestamp>_<run_id>/final_candidates.json`
- `runs/<timestamp>_<run_id>/final_answer.md`

`trace.log` 适合人读，`events.jsonl` 适合机器处理。

### 关键事件

当前事件流至少包括：

- `run_started`
- `strategy_extracted`
- `cts_query_built`
- `tool_called`
- `tool_succeeded`
- `tool_failed`
- `dedup_applied`
- `resume_normalization_started`
- `resume_normalized`
- `resume_normalization_warning`
- `scoring_fanout_started`
- `score_branch_started`
- `score_branch_completed`
- `score_branch_failed`
- `scoring_fanin_completed`
- `top5_updated`
- `reflection_started`
- `reflection_decision`
- `final_answer_created`
- `run_finished`

为了减少敏感信息暴露，trace 默认记录的是简历摘要和规范化结果，不直接原样写入完整长文本。

## mock 模式覆盖了什么

mock 数据集刻意包含：

- 重复简历
- 边界候选人
- 明显不匹配候选人
- 缺少 title 的简历
- 缺少 education 的简历
- 技能字段为空的简历
- 长文本被截断的简历
- 没有稳定 ID 需要 fallback 的简历
- `fail_once` 分支
- `fail_always` 分支

因此可以覆盖：

- normalization
- 去重
- shortage
- top5 跨轮更新
- reflection 调整关键词
- 并发评分
- 单分支失败不拖垮整轮
- 信息不足如何转化为风险

## 后续最适合扩展的点

- 根据更多已验证 OpenAPI 证据扩展过滤字段映射
- 为真实 LLM 路径增加更细的输出校验和 fallback
- 引入更细颗粒度的 CTS paging / search strategy adaptation
- 增加 smoke tests 和 golden trace fixtures
