# SeekTalent v0.3.1 交互式数据流导图

> 本页只保留当前 `HEAD` 的主链导航。

## 1. Runtime 主图

```mermaid
flowchart TD
    subgraph B["Bootstrap"]
        SIT["SearchInputTruth"] --> RED["RequirementExtractionDraft"]
        RED --> ER["ExtractRequirements"]
        ER --> RS["RequirementSheet"]
        BPP["BusinessPolicyPack"] --> RDK["RouteDomainKnowledgePack"]
        RS --> RDK
        DKP["DomainKnowledgePack(active packs)"] --> RDK
        RDK --> BRR["BootstrapRoutingResult"]
        BPP --> FSP["FreezeScoringPolicy"]
        RC["RerankerCalibration"] --> FSP
        RS --> FSP
        FSP --> SP["ScoringPolicy"]
        RS --> BKD["BootstrapKeywordDraft"]
        BRR --> BKD
        DKP --> BKD
        BKD --> GBO["GenerateBootstrapOutput"]
        RS --> GBO
        BRR --> GBO
        GBO --> BO["BootstrapOutput"]
        BO --> IFS["InitializeFrontierState"]
        IFS --> FS0["FrontierState_t"]
    end

    subgraph E["Single Expansion"]
        FS0 --> SAFN["SelectActiveFrontierNode"]
        RS --> SAFN
        SP --> SAFN
        SAFN --> SCC["SearchControllerContext_t"]
        SCC --> SCDD["SearchControllerDecisionDraft_t"]
        SCDD --> GSCD["GenerateSearchControllerDecision"]
        GSCD --> SCD["SearchControllerDecision_t"]
        FS0 --> MSEP["MaterializeSearchExecutionPlan"]
        RS --> MSEP
        SCD --> MSEP
        MSEP --> SEP["SearchExecutionPlan_t"]
        SEP --> ESP["ExecuteSearchPlan"]
        ESP --> SER["SearchExecutionResult_t"]
        SER --> SSR["ScoreSearchResults"]
        SP --> SSR
        SSR --> SSC["SearchScoringResult_t"]
        RS --> EBO["EvaluateBranchOutcome"]
        FS0 --> EBO
        SEP --> EBO
        SER --> EBO
        SSC --> EBO
        EBO --> BE["BranchEvaluation_t"]
        FS0 --> CNRB["ComputeNodeRewardBreakdown"]
        SEP --> CNRB
        SER --> CNRB
        SSC --> CNRB
        BE --> CNRB
        CNRB --> NRB["NodeRewardBreakdown_t"]
        FS0 --> UFS["UpdateFrontierState"]
        SEP --> UFS
        SSC --> UFS
        BE --> UFS
        NRB --> UFS
        UFS --> FS1["FrontierState_t1"]
        FS1 --> ESC["EvaluateStopCondition"]
    end

    subgraph F["Finalization"]
        RS --> FSR["FinalizeSearchRun"]
        FS1 --> FSR
        ESC --> FSR
        FSR --> SRR["SearchRunResult"]
        SRR --> SRB["SearchRunBundle"]
    end
```

## 2. 当前 bootstrap 入口

- [[SearchInputTruth]]
- [[RequirementExtractionDraft]]
- [[RequirementSheet]]
- [[BusinessPolicyPack]]
- [[DomainKnowledgePack]]
- [[BootstrapRoutingResult]]
- [[BootstrapKeywordDraft]]
- [[BootstrapOutput]]
- [[FrontierSeedSpecification]]
- [[FrontierState_t]]

## 3. 当前 expansion 入口

- [[SearchControllerContext_t]]
- [[SearchControllerDecisionDraft_t]]
- [[SearchControllerDecision_t]]
- [[SearchExecutionPlan_t]]
- [[SearchExecutionResult_t]]
- [[SearchScoringResult_t]]
- [[BranchEvaluationDraft_t]]
- [[BranchEvaluation_t]]
- [[NodeRewardBreakdown_t]]
- [[FrontierState_t1]]

## 4. Operator 入口

- bootstrap 链：[[ExtractRequirements]] -> [[RouteDomainKnowledgePack]] -> [[FreezeScoringPolicy]] -> [[GenerateBootstrapOutput]] -> [[InitializeFrontierState]]
- expansion 链：[[SelectActiveFrontierNode]] -> [[GenerateSearchControllerDecision]] -> [[MaterializeSearchExecutionPlan]] -> [[ExecuteSearchPlan]] -> [[ScoreSearchResults]]
- 闭环：[[EvaluateBranchOutcome]] -> [[ComputeNodeRewardBreakdown]] -> [[UpdateFrontierState]] -> [[EvaluateStopCondition]] -> [[FinalizeSearchRun]]

## 5. 已移除的旧 bootstrap 层

以下名称只保留历史意义，不再属于当前主图：

- [[RetrieveGroundingKnowledge]]
- [[GenerateGroundingOutput]]
- [[GroundingKnowledgeBaseSnapshot]]
- [[KnowledgeRetrievalResult]]
- [[GroundingDraft]]
- [[GroundingOutput]]
- [[GroundingKnowledgeCard]]
- [[GroundingEvidenceCard]]
