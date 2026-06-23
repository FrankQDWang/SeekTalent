import type {
  AgentWorkbenchCandidateDetailResponse,
  AgentWorkbenchCandidateSummary,
  AgentWorkbenchConversationSummary,
  AgentWorkbenchConversationResponse,
  AgentWorkbenchStrategyGraph,
  AgentWorkbenchThinkingProcess,
} from "../../lib/api/agentWorkbenchTypes";

const now = "2026-06-13T09:30:00.000Z";

type ViewOverrides = Partial<
  Omit<
    AgentWorkbenchConversationResponse,
    "conversation" | "pendingActions" | "streamCursor"
  >
> & {
  conversation?: Partial<AgentWorkbenchConversationResponse["conversation"]>;
  pendingActions?: Partial<
    AgentWorkbenchConversationResponse["pendingActions"]
  >;
  streamCursor?: Partial<AgentWorkbenchConversationResponse["streamCursor"]>;
};

type CandidateSummaryOverrides = Partial<AgentWorkbenchCandidateSummary> &
  Pick<AgentWorkbenchCandidateSummary, "candidateId" | "displayName" | "rank">;

export const wtsStoryConversationSummariesFixture: AgentWorkbenchConversationSummary[] =
  [
    {
      conversationId: "agent_conv_001",
      isArchived: false,
      runtimeRunId: "runtime_001",
      status: "running",
      title: "高级 Python Agent 平台工程师",
      updatedAt: now,
      workbenchSessionId: "session_001",
      workflowStartReasonCode: null,
      workflowStartState: "running",
    },
    {
      conversationId: "agent_conv_002",
      isArchived: false,
      runtimeRunId: "runtime_002",
      status: "completed",
      title: "搜索推荐算法负责人",
      updatedAt: "2026-06-13T08:50:00.000Z",
      workbenchSessionId: "session_002",
      workflowStartReasonCode: null,
      workflowStartState: "running",
    },
    {
      conversationId: "agent_conv_003",
      isArchived: false,
      runtimeRunId: "runtime_003",
      status: "pending",
      title: "RAG 工具链后端工程师",
      updatedAt: "2026-06-12T18:20:00.000Z",
      workbenchSessionId: "session_003",
      workflowStartReasonCode: null,
      workflowStartState: "not_started",
    },
  ];

function candidateSummary(
  overrides: CandidateSummaryOverrides,
): AgentWorkbenchCandidateSummary {
  return {
    headline: null,
    company: null,
    location: null,
    education: null,
    experienceYears: null,
    sourceKinds: ["liepin"],
    matchScore: null,
    matchSummary: null,
    status: "pending",
    detailAvailability: "redacted",
    accessState: "redacted",
    evidenceLevel: "summary",
    ...overrides,
  };
}

function workbenchView(
  overrides: ViewOverrides = {},
): AgentWorkbenchConversationResponse {
  const { conversation, pendingActions, streamCursor, ...topLevelOverrides } =
    overrides;
  return {
    schemaVersion: "agent.workbench.view.v2",
    messages: [
      {
        createdAt: now,
        messageId: "msg_001",
        messageType: "submitJd",
        payload: {
          kind: "job_request",
          jobTitle: "AI Agent 平台工程师",
        },
        role: "user",
        seq: 1,
        text: "帮我找上海 AI Agent 平台工程师，要求 Python 后端和检索系统经验。",
      },
      {
        createdAt: "2026-06-13T09:30:12.000Z",
        messageId: "msg_002",
        messageType: "assistant",
        payload: { kind: "empty" },
        role: "assistant",
        seq: 2,
        text: "已确认需求，正在按猎聘来源检索候选人。",
      },
    ],
    activities: [
      {
        activityId: "activity_001",
        activityType: "runtime_event",
        payload: {
          kind: "runtime_round",
          keywordQuery: "AI Agent 平台工程师 上海 Python RAG workflow",
          queryTerms: ["AI Agent", "Python", "RAG", "workflow"],
          roundNo: 1,
          status: "running",
        },
        seq: 1,
        sourceRuntimeRunId: "runtime_001",
        status: "running",
        summary: "正在检索候选人",
        title: "第一轮检索",
        updatedAt: now,
      },
    ],
    transcriptGroups: [
      {
        completedAt: null,
        events: [
          {
            createdAt: now,
            eventId: "message:msg_001:completed",
            itemId: "msg_001",
            kind: "message.completed",
            label: "User message",
            payload: {
              kind: "message",
              messageId: "msg_001",
              summary: "帮我找上海 AI Agent 平台工程师。",
            },
            status: "completed",
            summary: "帮我找上海 AI Agent 平台工程师。",
          },
          {
            createdAt: "2026-06-13T09:30:12.000Z",
            eventId: "operation:source_search:started",
            itemId: "tool_source_search",
            kind: "sourceSearch.started",
            label: "source search",
            payload: {
              kind: "source_search",
              itemId: "tool_source_search",
              summary: "检索猎聘安全摘要。",
            },
            status: "running",
            summary: "检索猎聘安全摘要。",
          },
          {
            createdAt: "2026-06-13T09:30:18.000Z",
            eventId: "activity:activity_001:upserted",
            itemId: "activity_001",
            kind: "activity.upserted",
            label: "第一轮检索",
            payload: {
              activityId: "activity_001",
              activitySeq: 1,
              activityType: "runtime_event",
              kind: "activity",
              sourceRuntimeRunId: "runtime_001",
              summary: "正在检索候选人",
            },
            status: "running",
            summary: "正在检索候选人",
          },
        ],
        groupId: "conversation:agent_conv_001:segment:1",
        startedAt: now,
        status: "running",
        title: "已处理",
      },
      {
        completedAt: "2026-06-13T09:31:00.000Z",
        events: [
          {
            createdAt: "2026-06-13T09:31:00.000Z",
            eventId: "context:compact_001:compacted",
            itemId: "compact_001",
            kind: "context.compacted",
            label: "上下文已压缩",
            payload: {
              kind: "context",
              itemId: "compact_001",
              summary: "token_budget",
            },
            status: "completed",
            summary: "token_budget",
          },
        ],
        groupId: "context:compact_001",
        startedAt: "2026-06-13T09:31:00.000Z",
        status: "completed",
        title: "上下文已压缩",
      },
    ],
    requirementDraft: {
      canConfirm: true,
      draftRevisionId: "draft_001",
      otherInputPrompt: "其他补充要求",
      sections: [
        {
          backendField: "must_have_capabilities",
          displayName: "必须满足",
          items: [
            {
              allowedActions: ["set_selected", "edit_text"],
              editable: true,
              enabled: true,
              itemId: "item_001",
              sectionId: "must_have_capabilities",
              selected: true,
              source: "extracted",
              status: "resolved",
              text: "交互设计功底扎实",
            },
            {
              allowedActions: ["set_selected", "edit_text"],
              editable: true,
              enabled: true,
              itemId: "item_002",
              sectionId: "must_have_capabilities",
              selected: false,
              source: "extracted",
              status: "resolved",
              text: "具备视觉表现能力",
            },
            {
              allowedActions: ["set_selected", "edit_text"],
              editable: true,
              enabled: true,
              itemId: "item_003",
              sectionId: "must_have_capabilities",
              selected: false,
              source: "extracted",
              status: "resolved",
              text: "基于用户需求和商业目标独立输出高质量设计方案",
            },
            {
              allowedActions: ["set_selected", "edit_text"],
              editable: true,
              enabled: true,
              itemId: "item_004",
              sectionId: "must_have_capabilities",
              selected: true,
              source: "extracted",
              status: "resolved",
              text: "能独立负责大型复杂项目的设计",
            },
            {
              allowedActions: ["set_selected", "edit_text"],
              editable: true,
              enabled: true,
              itemId: "item_005",
              sectionId: "must_have_capabilities",
              selected: false,
              source: "extracted",
              status: "resolved",
              text: "良好的跨部门沟通与推动能力",
            },
          ],
          sectionId: "must_have_capabilities",
        },
        {
          backendField: "nice_to_have",
          displayName: "加分项",
          items: [
            {
              allowedActions: ["set_selected", "edit_text"],
              editable: true,
              enabled: true,
              itemId: "item_006",
              sectionId: "nice_to_have",
              selected: true,
              source: "extracted",
              status: "resolved",
              text: "具备产品思维和行业视角",
            },
            {
              allowedActions: ["set_selected", "edit_text"],
              editable: true,
              enabled: true,
              itemId: "item_007",
              sectionId: "nice_to_have",
              selected: false,
              source: "extracted",
              status: "resolved",
              text: "善于通过数据和用户反馈驱动设计迭代",
            },
          ],
          sectionId: "nice_to_have",
        },
        {
          backendField: "hard_filters",
          displayName: "硬性筛选条件",
          items: [
            {
              allowedActions: ["set_selected", "edit_text"],
              editable: true,
              enabled: true,
              itemId: "item_008",
              sectionId: "hard_filters",
              selected: false,
              source: "extracted",
              status: "resolved",
              text: "公司: 字节",
            },
            {
              allowedActions: ["set_selected", "edit_text"],
              editable: true,
              enabled: true,
              itemId: "item_009",
              sectionId: "hard_filters",
              selected: true,
              source: "extracted",
              status: "resolved",
              text: "经验: 3年以上",
            },
            {
              allowedActions: ["set_selected", "edit_text"],
              editable: true,
              enabled: true,
              itemId: "item_010",
              sectionId: "hard_filters",
              selected: true,
              source: "extracted",
              status: "resolved",
              text: "学历: 本科及以上",
            },
            {
              allowedActions: ["set_selected", "edit_text"],
              editable: true,
              enabled: true,
              itemId: "item_011",
              sectionId: "hard_filters",
              selected: false,
              source: "extracted",
              status: "resolved",
              text: "地点: 上海",
            },
          ],
          sectionId: "hard_filters",
        },
      ],
      status: "needs_review",
      summary: "上海 / 资深 / Python 后端 / RAG / workflow orchestration",
      title: "AI Agent 平台工程师",
      unresolvedReviewItemCount: 0,
    },
    runtime: {
      currentRound: 1,
      currentStage: "source_search",
      latestEventSeq: 7,
      runtimeRunId: "runtime_001",
      status: "running",
    },
    strategyGraph: baseStrategyGraph,
    thinkingProcess: {
      activeRoundNo: 1,
      rounds: [
        {
          cards: [
            {
              terms: ["AI Agent", "Python", "RAG", "workflow"],
              text: "AI Agent 平台工程师 上海 Python RAG workflow orchestration",
              title: "关键词",
            },
            {
              terms: ["searched: 42", "scored: 12"],
              text: "结果覆盖面较好，强匹配集中在工具平台和 RAG 工程。",
              title: "observation",
            },
            {
              terms: ["eval harness", "workflow orchestration"],
              text: "下一轮加入 eval harness 和 workflow orchestration，保留 Python 后端约束。",
              title: "反思和下一轮变更",
            },
          ],
          roundNo: 1,
          status: "running",
        },
      ],
    },
    sourceConnections: [
      {
        displayName: "猎聘",
        lastCheckedAt: now,
        sourceKind: "liepin",
        status: "connected",
      },
    ],
    candidates: [
      candidateSummary({
        candidateId: "candidate_001",
        rank: 1,
        displayName: "吴所谓",
        headline: "资深体验设计工程师 · 平安集团",
        company: "平安集团",
        location: "上海",
        education: "本科",
        experienceYears: 10,
        sourceKinds: ["liepin"],
        matchScore: 92,
        matchSummary:
          "可独立主导 0-1 产品体验搭建，擅长拆解复杂 B 端业务流程。",
        status: "running",
        detailAvailability: "available",
        accessState: "allowed",
        evidenceLevel: "detail",
      }),
      candidateSummary({
        candidateId: "candidate_002",
        rank: 2,
        displayName: "候选人 B",
        headline: "高级体验设计师 · 上海",
        company: "互联网平台",
        location: "上海",
        education: "硕士",
        experienceYears: 8,
        sourceKinds: ["liepin"],
        matchScore: 84,
        matchSummary: "复杂 B 端流程经验明确，跨团队推动力需要补充验证。",
        status: "pending",
        detailAvailability: "redacted",
        accessState: "redacted",
        evidenceLevel: "summary",
      }),
    ],
    detailApprovals: [
      {
        approvalId: "approval_001",
        candidateId: "candidate_001",
        reason: "读取吴所谓完整简历前需要用户确认。",
        status: "pending",
      },
      {
        approvalId: "approval_002",
        candidateId: "candidate_002",
        reason: "候选人 B 详情快照已应用到证据面板。",
        status: "applied",
      },
    ],
    reviewArtifacts: [
      {
        artifactId: "artifact_requirement_001",
        artifactKind: "approval",
        safeSummary: "需求已确认，来源边界清晰。",
        title: "需求确认记录",
      },
    ],
    finalSummary: null,
    reasonCode: null,
    ...topLevelOverrides,
    conversation: {
      conversationId: "agent_conv_001",
      isArchived: false,
      runtimeRunId: "runtime_001",
      status: "running",
      title: "AI Agent 平台工程师",
      updatedAt: now,
      workbenchSessionId: "session_001",
      workflowStartState: "running",
      workflowStartReasonCode: null,
      ...conversation,
    },
    pendingActions: {
      allowed: ["submit_message"],
      pendingCommandCount: 0,
      pendingMemoryReviewCount: 1,
      pendingRequirementReviewCount: 0,
      primary: "等待候选人详情审批",
      ...pendingActions,
    },
    streamCursor: {
      latestActivitySeq: 1,
      latestMessageSeq: 2,
      latestRuntimeEventSeq: 7,
      latestStreamSeq: 12,
      snapshotSeq: 12,
      viewRevision: 12,
      ...streamCursor,
    },
  };
}

const baseStrategyGraph: AgentWorkbenchStrategyGraph = {
  nodes: [
    {
      nodeId: "requirements",
      kind: "requirements",
      label: "需求拆解",
      summary: "已确认需求：AI Agent 平台工程，Python 后端，检索系统经验。",
      status: "completed",
      sourceKind: "all",
    },
    ...roundGraphNodes(1, "completed", {
      query: "第 1 轮查询策略已生成。",
      source: "猎聘返回 10 份原始简历，形成 10 位候选人。",
      scoring: "第 1 轮评分完成，10 位候选人进入 Top Pool。",
      feedback: "第 1 轮复盘完成，准备下一轮策略。",
    }),
    ...roundGraphNodes(2, "completed", {
      query: "第 2 轮查询策略已生成。",
      source: "猎聘返回 16 份原始简历，形成 7 位候选人。",
      scoring: "第 2 轮评分完成，10 位候选人进入 Top Pool。",
      feedback: "第 2 轮复盘完成，准备下一轮策略。",
    }),
    ...roundGraphNodes(3, "running", {
      query: "第 3 轮查询策略已生成。",
      source: "猎聘正在返回安全摘要。",
      scoring: "第 3 轮 Top Pool 等待评分。",
      feedback: "等待第 3 轮结果后生成下一轮策略。",
    }),
    ...roundGraphNodes(4, "pending", {
      query: "第 4 轮查询包等待后端生成。",
      source: "猎聘检索等待第 4 轮启动。",
      scoring: "Top Pool 等待第 4 轮评分。",
      feedback: "等待前序轮次完成后更新策略。",
    }),
  ],
  edges: [
    {
      edgeId: "requirements->round:1:phase:round_query:all",
      fromNodeId: "requirements",
      label: "生成检索词",
      toNodeId: "round:1:phase:round_query:all",
    },
    ...roundGraphEdges(1, 2),
    ...roundGraphEdges(2, 3),
    ...roundGraphEdges(3, 4),
    ...roundGraphEdges(4, null),
  ],
};

function roundGraphNodes(
  roundNo: number,
  status: "completed" | "running" | "pending",
  summaries: {
    feedback: string;
    query: string;
    scoring: string;
    source: string;
  },
): AgentWorkbenchStrategyGraph["nodes"] {
  const roundId = String(roundNo);
  const sourceStatus = status === "running" ? "running" : status;
  const laterStatus = status === "running" ? "pending" : status;
  return [
    {
      nodeId: `round:${roundId}`,
      kind: "round",
      label: `第 ${roundId} 轮`,
      phase: "round",
      roundNo,
      stage: "round_summary",
      summary: `第 ${roundId} 轮猎聘检索`,
      status,
      sourceKind: "all",
    },
    {
      nodeId: `round:${roundId}:phase:round_query:all`,
      kind: "phase",
      label: "round_query",
      phase: "query",
      roundNo,
      stage: "round_query",
      summary: summaries.query,
      status: status === "pending" ? "pending" : "completed",
      sourceKind: "all",
    },
    {
      nodeId: `round:${roundId}:phase:source_result:liepin`,
      kind: "phase",
      label: "liepin source_result",
      phase: "source",
      roundNo,
      stage: "source_result",
      summary: summaries.source,
      status: sourceStatus,
      sourceKind: "liepin",
    },
    {
      nodeId: `round:${roundId}:phase:scoring:all`,
      kind: "phase",
      label: "scoring",
      phase: "scoring",
      roundNo,
      stage: "scoring",
      summary: summaries.scoring,
      status: laterStatus,
      sourceKind: "all",
    },
    {
      nodeId: `round:${roundId}:phase:feedback:all`,
      kind: "phase",
      label: "feedback",
      phase: "feedback",
      roundNo,
      stage: "feedback",
      summary: summaries.feedback,
      status: laterStatus,
      sourceKind: "all",
    },
  ];
}

function roundGraphEdges(
  roundNo: number,
  nextRoundNo: number | null,
): AgentWorkbenchStrategyGraph["edges"] {
  const roundId = String(roundNo);
  const query = `round:${roundId}:phase:round_query:all`;
  const source = `round:${roundId}:phase:source_result:liepin`;
  const scoring = `round:${roundId}:phase:scoring:all`;
  const feedback = `round:${roundId}:phase:feedback:all`;
  const edges: AgentWorkbenchStrategyGraph["edges"] = [
    {
      edgeId: `${query}->${source}`,
      fromNodeId: query,
      label: "猎聘检索",
      toNodeId: source,
    },
    {
      edgeId: `${source}->${scoring}`,
      fromNodeId: source,
      label: "安全摘要",
      toNodeId: scoring,
    },
    {
      edgeId: `${scoring}->${feedback}`,
      fromNodeId: scoring,
      label: "评分结果",
      toNodeId: feedback,
    },
  ];
  if (nextRoundNo !== null) {
    const nextRoundId = String(nextRoundNo);
    edges.push({
      edgeId: `${feedback}->round:${nextRoundId}:phase:round_query:all`,
      fromNodeId: feedback,
      label: "下一轮策略",
      toNodeId: `round:${nextRoundId}:phase:round_query:all`,
    });
  }
  return edges;
}

const emptyStrategyGraph: AgentWorkbenchStrategyGraph = {
  nodes: [],
  edges: [],
};

export const agentWorkbenchInitialViewFixture: AgentWorkbenchConversationResponse =
  workbenchView({
    activities: [],
    candidates: [],
    conversation: {
      runtimeRunId: null,
      status: "empty",
      title: "新建找候选人任务",
    },
    detailApprovals: [],
    messages: [],
    pendingActions: {
      allowed: ["submit_message"],
      pendingMemoryReviewCount: 0,
      primary: null,
    },
    requirementDraft: null,
    reviewArtifacts: [],
    runtime: null,
    sourceConnections: [],
    strategyGraph: emptyStrategyGraph,
    thinkingProcess: { activeRoundNo: null, rounds: [] },
    transcriptGroups: [],
  });

export const agentWorkbenchRequirementReviewViewFixture: AgentWorkbenchConversationResponse =
  workbenchView({
    candidates: [],
    conversation: {
      runtimeRunId: null,
      status: "needs_confirmation",
      workflowStartState: "not_started",
    },
    detailApprovals: [],
    finalSummary: null,
    pendingActions: {
      allowed: ["confirm_requirements"],
      pendingMemoryReviewCount: 0,
      pendingRequirementReviewCount: 1,
      primary: "确认需求后开始检索",
    },
    reviewArtifacts: [],
    runtime: null,
    strategyGraph: emptyStrategyGraph,
    thinkingProcess: { activeRoundNo: null, rounds: [] },
  });

export const agentWorkbenchRunningViewFixture: AgentWorkbenchConversationResponse =
  workbenchView();

export const multiRoundThinkingProcessFixture: AgentWorkbenchThinkingProcess = {
  activeRoundNo: 3,
  rounds: [
    {
      cards: [
        {
          terms: ["AI Agent", "Python", "RAG"],
          text: "锁定 AI Agent 平台、Python 后端、RAG 工程三个主关键词。",
          title: "关键词",
        },
        {
          terms: ["searched: 10", "scored: 10"],
          text: "猎聘第一轮覆盖面较宽，强匹配集中在工具平台与检索工程。",
          title: "observation",
        },
        {
          terms: ["工具平台", "workflow"],
          text: "下一轮收窄到工具平台和 workflow orchestration，保留 Python 约束。",
          title: "反思和下一轮变更",
        },
      ],
      roundNo: 1,
      status: "completed",
    },
    {
      cards: [
        {
          terms: ["workflow orchestration", "agent runtime"],
          text: "加入 workflow orchestration 与 agent runtime，排除纯算法岗位。",
          title: "关键词",
        },
        {
          terms: ["searched: 16", "scored: 7"],
          text: "候选人质量提升，但 RAG 评测和线上可观测性经验仍不够明确。",
          title: "observation",
        },
        {
          terms: ["eval harness", "observability"],
          text: "第三轮补充 eval harness、observability，继续从猎聘读取安全摘要。",
          title: "反思和下一轮变更",
        },
      ],
      roundNo: 2,
      status: "completed",
    },
    {
      cards: [
        {
          terms: ["eval harness", "observability", "平台工程"],
          text: "当前轮正在验证评测体系、运行时可观测性和平台工程交集。",
          title: "关键词",
        },
        {
          terms: ["searched: 24", "scored: 12"],
          text: "猎聘返回的安全摘要显示三位候选人与运行时控制面高度相关。",
          title: "observation",
        },
        {
          terms: ["Top Pool", "强匹配"],
          text: "先完成 Top Pool 评分，再决定是否需要第四轮补漏。",
          title: "反思和下一轮变更",
        },
      ],
      roundNo: 3,
      status: "running",
    },
    {
      cards: [
        {
          terms: ["补漏轮", "待生成"],
          text: "等待第三轮评分后，由后端 runtime projection 决定是否生成补漏查询。",
          title: "关键词",
        },
      ],
      roundNo: 4,
      status: "pending",
    },
  ],
};

export const agentWorkbenchMultiRoundThinkingViewFixture: AgentWorkbenchConversationResponse =
  workbenchView({
    runtime: {
      currentRound: 3,
      currentStage: "scoring",
      latestEventSeq: 19,
      runtimeRunId: "runtime_001",
      status: "running",
    },
    thinkingProcess: multiRoundThinkingProcessFixture,
  });

export const agentWorkbenchCandidateDetailFixture: AgentWorkbenchCandidateDetailResponse =
  {
    accessState: "allowed",
    candidateId: "candidate_001",
    detailAvailability: "available",
    displayName: "吴所谓",
    evidence: [
      "可独立主导 0-1 产品体验搭建，擅长拆解复杂 B 端业务流程。",
      "多次通过流程重构提升任务完成率。",
    ],
    evidenceLevel: "detail",
    headline: "资深体验设计工程师 · 平安集团",
    matchScore: 92,
    reasonCode: null,
    sections: [
      {
        title: "匹配程度",
        items: [
          "推荐理由：可独立主导 0-1 产品体验搭建，擅长拆解复杂 B 端业务流程，通过用户调研、行为数据定位核心痛点，输出可量化的体验优化策略。",
          "候选人强项：擅长通过定量 + 定性调研挖掘企业用户真实痛点，搭建可量化体验度量体系。",
          "候选人弱项：AI 产品体验设计项目未在简历中明确体现。",
        ],
      },
      {
        title: "求职意向",
        items: [
          "期望岗位：高端设计职位，设计，设计经理/主管",
          "期望行业：互联网，其他",
          "期望地点：上海",
          "期望薪资：20-24k*14薪",
        ],
      },
      {
        title: "工作经历",
        items: [
          "2019.06-至今（7年）平安好医 | 用户体验设计专家。",
          "提供 B 端及 C 端体验设计方案，负责商城小程序、会员积分商城小程序设计。",
          "通过参与需求分析、拆解产品目标、竞品分析，制定设计策略。",
        ],
      },
    ],
    sourceKinds: ["liepin"],
  };

export const agentWorkbenchCandidateApprovalRequiredDetailFixture: AgentWorkbenchCandidateDetailResponse =
  {
    ...agentWorkbenchCandidateDetailFixture,
    accessState: "approval_required",
    detailAvailability: "approval_required",
    evidence: [],
    evidenceLevel: "summary",
    reasonCode: "candidate_detail_requires_approval",
    sections: [],
  };

export const agentWorkbenchSourceExpiredViewFixture: AgentWorkbenchConversationResponse =
  workbenchView({
    conversation: { status: "disconnected" },
    pendingActions: {
      allowed: [],
      primary: "事件流断开，等待恢复",
    },
    reasonCode: "stream_disconnected",
    sourceConnections: [
      {
        displayName: "猎聘",
        lastCheckedAt: now,
        sourceKind: "liepin",
        status: "expired",
      },
    ],
  });

export const agentWorkbenchPermissionDeniedViewFixture: AgentWorkbenchConversationResponse =
  workbenchView({
    conversation: { status: "permission_denied" },
    pendingActions: {
      allowed: [],
      primary: "来源连接已过期，请重新授权后继续检索。",
    },
    reasonCode: "permission_denied",
    sourceConnections: [
      {
        displayName: "猎聘",
        lastCheckedAt: now,
        sourceKind: "liepin",
        status: "expired",
      },
    ],
  });

export const agentWorkbenchFailedViewFixture: AgentWorkbenchConversationResponse =
  workbenchView({
    conversation: { status: "failed" },
    pendingActions: {
      allowed: ["submit_message"],
      primary: "检索失败，可调整需求后重试。",
    },
    reasonCode: "source_search_failed",
    transcriptGroups: [
      {
        completedAt: now,
        events: [
          {
            createdAt: now,
            eventId: "operation:source_search:failed",
            itemId: "tool_source_search",
            kind: "sourceSearch.failed",
            label: "source search",
            payload: {
              kind: "source_search",
              itemId: "tool_source_search",
              summary: "来源检索失败，已保留安全错误原因。",
            },
            status: "failed",
            summary: "来源检索失败，已保留安全错误原因。",
          },
        ],
        groupId: "conversation:agent_conv_001:segment:failed",
        startedAt: now,
        status: "failed",
        title: "已处理",
      },
    ],
  });

export const agentWorkbenchCompletedViewFixture: AgentWorkbenchConversationResponse =
  workbenchView({
    conversation: { status: "completed" },
    finalSummary: {
      summaryId: "final_summary_001",
      text: "第一轮推荐 2 位候选人，候选人 A 为强匹配。",
    },
    pendingActions: {
      allowed: ["submit_message", "export_final_summary"],
      pendingMemoryReviewCount: 0,
      primary: "最终名单可导出",
    },
    reviewArtifacts: [
      {
        artifactId: "artifact_final_001",
        artifactKind: "final_output",
        safeSummary: "第一轮推荐 2 位候选人，候选人 A 为强匹配。",
        title: "最终安全摘要",
      },
    ],
    runtime: {
      currentRound: 1,
      currentStage: "final_summary",
      latestEventSeq: 12,
      runtimeRunId: "runtime_001",
      status: "completed",
    },
  });

export const agentWorkbenchArchivedViewFixture: AgentWorkbenchConversationResponse =
  workbenchView({
    conversation: {
      isArchived: true,
      status: "archived",
    },
    pendingActions: {
      allowed: [],
      pendingMemoryReviewCount: 0,
      primary: "此任务当前为只读查看。",
    },
  });

export const agentWorkbenchSearchStrategyGraphFixture: AgentWorkbenchStrategyGraph =
  agentWorkbenchRunningViewFixture.strategyGraph;

export const agentWorkbenchEmptyStrategyGraphFixture: AgentWorkbenchStrategyGraph =
  agentWorkbenchInitialViewFixture.strategyGraph;

export const agentWorkbenchLargeGraphFixture: AgentWorkbenchStrategyGraph =
  baseStrategyGraph;
