# SeekTalent v0.3.1 Operator Spec Style

> 本页规定 `operators/` 中算子文档的推荐展示格式。
> 目标是让每个算子一开头就能回答三件事：
> 1. 输入输出是什么
> 2. 具体怎么变换
> 3. 默认系数、阈值和黑盒边界在哪里

## 1. 总原则

- 每个 operator 文档都以 `Signature` 开头。
- `Signature` 只描述 canonical operator 的输入输出，不把内部 LLM draft layer 当成 operator 本体。
- 所有 output payload 字段都应该能在 `Transformation` 或 `Field-Level Output Assembly` 中找到来源表达式。
- 能写成白盒公式的地方，直接写公式。
- 不能白盒化的外部调用，例如 `SearchControllerDecisionLLM(...)`、`RerankService(...)`、`CTS.search(...)`，必须显式命名为中间变量，不伪装成 deterministic 公式。
- 默认系数和默认阈值只能写已经有 owner 的值；不得臆造“平台默认值”。

## 2. 推荐骨架

每个算子文档按下面顺序组织：

1. `Signature`
2. `Notation Legend`
3. `Input Projection`
4. `Primitive Predicates / Matching Rules`
5. `Transformation`
6. `Field-Level Output Assembly`
7. `Defaults / Thresholds Used Here`
8. `Read Set`
9. `Write Set`
10. `输入 payload`
11. `输出 payload`
12. `External Boundary / Notes`
13. `相关`

允许保留现有的 `Read Set / Write Set / 输入 payload / 输出 payload / 相关` 段，不要求重起一套文档体系。

## 3. Signature 规则

推荐写法：

```text
ScoreSearchResults : (SearchExecutionResult_t, ScoringPolicy) -> SearchScoringResult_t
GenerateSearchControllerDecision : SearchControllerContext_t -> SearchControllerDecision_t
```

约束：

- operator 层统一使用 `->`
- 不使用 `->_llm`
- LLM 或外部服务介入时，在 `Transformation` 中显式写：

```text
prompt_t = ...
draft_t = SomeLLM(prompt_t)
result_t = normalize(draft_t, ...)
```

原因：

- `operators/` 中的 canonical owner 仍然是 operator，不是内部粉色 LLM 节点
- 这样能把“黑盒发生在哪里”和“runtime 如何收口”同时写清楚

## 4. Input Projection 规则

`Input Projection` 只投影本 operator 实际读取的字段，不要求把整个 payload 全量抄一遍。

推荐写法：

```text
C_t = x_t.scoring_candidates
k_explain = P.top_n_for_explanation
```

如果某些默认范围或阈值是由上游 runtime policy 冻结后传入，也可以在这里显式命名：

```text
term_budget_range_t = SearchControllerContext_t.term_budget_range
```

## 5. Fully Expanded 规则

除下面两类以外，不允许 unresolved helper 停留在 operator 文档里：

1. 明确标记的外部黑盒：
   - `SomeLLM(...)`
   - `RerankService(...)`
   - `CTS.search(...)`
2. 基础原语与局部原子谓词：
   - 算术：`+ - * / min max round`
   - 条件：`if / else`
   - 集合与计数：`| | Σ intersect union`
   - 序列：`stable_sort_desc slice append index_by`
   - 清洗：`deduplicate drop_empty trim normalize_text normalized clip coalesce`
   - 白名单：`whitelist whitelist_or_null`
   - 当前文档中显式定义的 `*_hit_*`、`*_match_*`、`*_fit_*`、`*_rank_*` 原子谓词

如果某个符号不是上面两类，就必须在当前 operator 文档继续展开。

## 6. Primitive Predicates / Matching Rules 规则

当公式需要读者能直接理解，但继续展开会把正文撕裂成大量重复条件时，允许先定义本页局部原子谓词，例如：

```text
capability_hit_i(m) =
  1 if capability m is matched by c_i.scoring_text
      or normalized skill/title tokens
      or stable structured tags
  else 0
```

约束：

- 原子谓词必须在当前文档显式定义
- 原子谓词只能表达单个局部判断，不应再包一层完整业务流程
- 原子谓词定义完后，后面的 `Transformation` 与 `Field-Level Output Assembly` 只能调用这些已定义谓词或基础原语

## 7. Transformation 规则

### 7.1 确定性算子

直接按 phase 展开公式：

```text
Phase 1: ...
Phase 2: ...
Phase 3: ...
```

要求：

- 中间变量命名稳定
- 输出字段逐项回填
- 默认系数在 `where` 或 `Defaults / Thresholds Used Here` 写清楚

### 7.2 含 LLM draft 的算子

固定写成三段：

```text
prompt_t = ...
draft_t = SomeLLM(prompt_t)
normalized_t = ...
```

要求：

- `prompt_t` 必须写清楚真正暴露给模型的字段
- `draft_t` 是唯一黑盒
- `draft_t` 必须有独立 payload owner；不允许只在 operator 里出现匿名草稿变量
- 默认 contract 固定为 provider-native structured output + strict schema + `retries=0` + `output_retries=1`
- 不允许退回 prompted JSON、自由文本解析、tool fallback 或 fallback model chain
- 若允许额外 `output_validator + ModelRetry`，必须在 `External Boundary / Notes` 明确写出“只补充哪些 schema 之外的业务约束”
- 进入 output payload 前必须展示 deterministic normalization / whitelist / clamp / fallback

### 7.3 外部服务调用

若调用 reranker、CTS 或其他 runtime service，必须显式命名 request：

```text
request_t = ...
raw_result_t = ExternalService(request_t)
normalized_result_t = ...
```

## 8. Field-Level Output Assembly 规则

若 output payload 有多个稳定字段，建议单独列一段，逐字段写回：

```text
y_t.scored_candidates = ranked_score_cards_t
y_t.node_shortlist_candidate_ids = ...
y_t.explanation_candidate_ids = ...
```

目标是让读者不必在长公式里倒推 output payload 的来源。

## 9. Defaults / Thresholds 规则

这一段只写本算子真正用到的默认值、默认权重、默认阈值，并注明 owner。

推荐写法：

```text
α = P.fusion_weights.rerank                (default 0.55 from FreezeScoringPolicy)
term_budget_range default comes from RuntimeTermBudgetPolicy:
  high = [2, 6], medium = [2, 5], low = [2, 4]
```

禁止：

- 给 `RerankerCalibration` 这类 registry 参数硬写“全局默认值”，除非 owner 文档明确写死
- 把 trace 里的 worked example 数值冒充默认值

## 10. External Boundary / Notes 规则

这一段只写边界，不重复公式。

优先写：

- 哪一步是黑盒
- 哪一步不允许自由改写
- 哪些 default 来自 upstream frozen policy，而不是算子自己决定

## 11. 适配建议

最适合完全公式化展示的算子：

- `ScoreSearchResults`
- `ComputeNodeRewardBreakdown`
- `EvaluateStopCondition`
- `SelectActiveFrontierNode`
- `MaterializeSearchExecutionPlan`
- `UpdateFrontierState`

适合“prompt/draft/normalize”格式的算子：

- `GenerateSearchControllerDecision`
- `EvaluateBranchOutcome`
- `FinalizeSearchRun`

适合“draft payload -> deterministic normalization”格式的算子：

- `ExtractRequirements`
- `GenerateBootstrapOutput`

## 相关

- [[design]]
- [[operator-map]]
- [[weights-and-thresholds-index]]
