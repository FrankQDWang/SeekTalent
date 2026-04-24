# SeekTalent v0.2 LLM Context Maps

> 本文档说明 `v0.2` 中 5 个 LLM 调用点的 context 分层方式：总 memory 是什么，runtime 如何从 memory 投影出各阶段 context，以及哪些信息是刻意不暴露给某个 LLM 的。

---

## 1. 总图：Runtime Truth -> Context -> LLM -> Output -> Runtime Truth

```mermaid
flowchart LR
    subgraph M["Runtime Truth"]
        IT["InputTruth<br/>- full JD<br/>- full notes<br/>- jd_sha256<br/>- notes_sha256"]
        RS["RequirementSheet<br/>- role truth<br/>- hard_constraints<br/>- preferences<br/>- initial_query_term_pool"]
        SP["ScoringPolicy<br/>- frozen scoring truth"]
        RT["RetrievalState<br/>- query_term_pool<br/>- sent_query_history<br/>- reflection histories<br/>- last_projection_result"]
        RH["RoundHistory<br/>- retrieval_plan<br/>- cts_queries<br/>- search_observation<br/>- search_attempts<br/>- reflection_advice"]
        STORES["Stores<br/>- candidate_store<br/>- normalized_store<br/>- scorecards_by_resume_id<br/>- top_pool_ids<br/>- seen_resume_ids"]
    end

    subgraph C["Context Projection Layer"]
        C_REQ["INPUT_TRUTH"]
        C_CTL["ControllerContext"]
        C_SCO["ScoringContext"]
        C_REF["ReflectionContext"]
        C_FIN["FINALIZATION_CONTEXT<br/>(actual payload)"]
    end

    subgraph L["LLM Call Points"]
        L_REQ["Requirement Extractor<br/>prompt: requirements.md"]
        L_CTL["Controller<br/>prompt: controller.md"]
        L_SCO["Resume Scorer<br/>prompt: scoring.md"]
        L_REF["Reflection Critic<br/>prompt: reflection.md"]
        L_FIN["Finalizer<br/>prompt: finalize.md"]
    end

    subgraph O["Structured Outputs"]
        O_REQ["RequirementExtractionDraft<br/>-> normalize -> RequirementSheet"]
        O_CTL["ControllerDecision"]
        O_SCO["ScoredCandidate"]
        O_REF["ReflectionAdvice"]
        O_FIN["FinalResult"]
    end

    subgraph U["Runtime Truth"]
        IT2["InputTruth<br/>- full JD<br/>- full notes<br/>- jd_sha256<br/>- notes_sha256"]
        RS2["RequirementSheet<br/>- role truth<br/>- hard_constraints<br/>- preferences<br/>- initial_query_term_pool"]
        SP2["ScoringPolicy<br/>- frozen scoring truth"]
        RT2["RetrievalState<br/>- query_term_pool<br/>- sent_query_history<br/>- reflection histories<br/>- last_projection_result"]
        RH2["RoundHistory<br/>- retrieval_plan<br/>- cts_queries<br/>- search_observation<br/>- search_attempts<br/>- reflection_advice"]
        STORES2["Stores<br/>- candidate_store<br/>- normalized_store<br/>- scorecards_by_resume_id<br/>- top_pool_ids<br/>- seen_resume_ids"]
    end

    IT --> C_REQ

    IT --> C_CTL
    RS --> C_CTL
    RT --> C_CTL
    RH --> C_CTL
    STORES --> C_CTL

    SP --> C_SCO
    STORES --> C_SCO

    IT --> C_REF
    RS --> C_REF
    RT --> C_REF
    RH --> C_REF
    STORES --> C_REF

    STORES --> C_FIN
    RT --> C_FIN

    C_REQ --> L_REQ --> O_REQ
    C_CTL --> L_CTL --> O_CTL
    C_SCO --> L_SCO --> O_SCO
    C_REF --> L_REF --> O_REF
    C_FIN --> L_FIN --> O_FIN

    O_REQ --> RS2
    O_REQ --> SP2
    O_CTL --> RH2
    O_SCO --> STORES2
    O_REF --> RH2
    O_FIN --> STORES2
```

这张图表达的是：

- `RunState` 相关对象共同构成系统总 memory
- 各个 LLM 不直接读取全部 memory
- runtime 先做 deterministic context projection，再把投影结果交给对应 LLM
- 每个 LLM 的输出也都是结构化对象，而不是自由文本
- 第 1 列和第 5 列是同一套 runtime truth 结构；左边表示调用前，右边表示吸收 structured output 之后
- 图中保留了 runtime truth 的具体组成，不把细节压扁成抽象占位框
- `LLM Call Points` 的上下顺序按实际执行顺序展示：requirements -> controller -> scoring -> reflection -> finalizer

第 5 列表达的是这些具体更新：

- 第 1 行：`RequirementExtractionDraft` 会被归一化成 `RequirementSheet`，并冻结出 `ScoringPolicy`
- 第 2 行：`ControllerDecision` 会进入 `RoundHistory`，驱动下一步执行
- 第 3 行：`ScoredCandidate` 会进入 `Stores`，并参与排序和 top pool 更新
- 第 4 行：`ReflectionAdvice` 只作为建议历史写入 `RoundHistory`；controller/runtime 负责采纳和执行
- 第 5 行：`FinalResult` 会被 runtime 接住并写出最终产物；这是终点，不再进入下一轮循环

---

## 2. 思维导图：5 个 LLM 各自看到什么

```mermaid
mindmap
  root((Run Memory / RunState))
    InputTruth
      full JD
      full notes
      hashes
    RequirementSheet
      role truth
      hard_constraints
      preferences
      initial_query_term_pool
    ScoringPolicy
      frozen scoring truth
    RetrievalState
      query_term_pool
      sent_query_history
      reflection histories
      last_projection_result
    RoundHistory
      controller_decision
      retrieval_plan
      cts_queries
      search_observation
      search_attempts
      reflection_advice
    Stores
      candidate_store
      normalized_store
      scorecards_by_resume_id
      top_pool_ids
      seen_resume_ids
    Requirement Extractor
      prompt: requirements.md
      context: INPUT_TRUTH
      output: RequirementExtractionDraft
      then: normalize to RequirementSheet
    Controller
      prompt: controller.md
      context: ControllerContext
      sees
        full JD + notes
        RequirementSheet
        RequirementDigest
        query_term_pool
        current_top_pool summary
        latest search summary
        latest reflection advice
          keyword advice
          filter advice
          stop advice
          summary and rationale
        shortage history
      output: ControllerDecision
    Resume Scorer
      prompt: scoring.md
      context: ScoringContext
      sees
        frozen ScoringPolicy
        runtime-only constraints
        one NormalizedResume
        round_no
      output: ScoredCandidate
    Reflection Critic
      prompt: reflection.md
      context: ReflectionContext
      sees
        full JD + notes
        full requirement sheet
        current runtime term bank
        current retrieval plan
        search_observation
        search_attempts
        top_candidates
        dropped_candidates
        sent_query_history
      output: ReflectionAdvice
    Finalizer
      prompt: finalize.md
      context: FINALIZATION_CONTEXT
      actual payload
        run_id
        run_dir
        rounds_executed
        stop_reason
        ranked_candidates with score, fit, must/risk, matched signals, strengths, weaknesses, risk flags
      output: FinalResult
```

这张图强调“谁看到什么”：

- `Requirement Extractor` 只看原始输入
- `Controller` 看完整业务真相，但只看运行态摘要，不看全量底层细节
- `Scorer` 看得最窄，只看含完整 hard constraints/preferences 的冻结评分标准、runtime-only constraints 和一份简历，不比较候选人
- `Reflection` 看得最宽，因为它负责复盘
- `Finalizer` 当前实际看到的是排序后的候选结果，不是全量 `RunState`

Reflection 只有建议权。Runtime 会记录建议历史，但采纳和执行由 controller/runtime 负责。

---

## 3. 对照图：哪些信息被刻意不给某个 LLM 看

```mermaid
mindmap
  root((Intentionally Hidden by Design))
    Requirement Extractor
      sees
        full JD
        full notes
      does_not_see
        retrieval history
        candidates
        scores
        reflection advice
      reason
        keep requirement truth independent from search outcomes
    Controller
      sees
        full JD + notes
        RequirementSheet
        top pool summary
        latest search summary
        latest reflection advice
      does_not_see
        full candidate_store
        raw resumes
        full search_attempt history
        direct CTS payload construction
      reason
        keep controller focused on round decisions, not low-level execution
    Resume Scorer
      sees
        ScoringPolicy
        one NormalizedResume
        round_no
      does_not_see
        JD raw text
        other candidates
        query history
        reflection
      reason
        keep scoring independent, role-specific, and non-comparative
    Reflection Critic
      sees
        full JD + notes
        full requirement sheet
        current retrieval outcome
        current runtime term bank
        sent_query_history
      does_not_see
        full candidate_store
        raw CTS tool control
        future round decisions
      reason
        keep reflection as critic, not executor
    Finalizer
      sees
        ranked_candidates
        run metadata
      does_not_see
        full RunState
        full RequirementSheet
        full search_attempt history
      reason
        keep final output generation narrow and presentation-focused
```

这张图强调“为什么不让它看”：

- 不是信息越多越好
- 而是每个 LLM 只看完成自己任务所需的最小充分集
- 这样可以减少 prompt 噪音、避免职责漂移，也更容易审计

---

## 4. 当前实现上的一个细节

`FinalizeContext` 这个模型本身比 finalizer 实际看到的 payload 更宽。

当前 runtime 会构造 `FinalizeContext`，但真正发送给 finalizer LLM 的内容是更窄的 `FINALIZATION_CONTEXT`：

- `run_id`
- `run_dir`
- `rounds_executed`
- `stop_reason`
- `ranked_candidates` with score, fit, must/risk, matched signals, strengths, weaknesses, and risk flags

补充：

- 当前 run 目录里已经会把这 5 个调用点的真实 user payload 和结构化输出分别落盘成 call snapshot
- 因此这份 context 图现在不只是概念图，也对应可回放的 run artifacts

因此如果你在看代码时发现“模型定义比实际传参宽”，这是当前实现的刻意收口，不是遗漏。
