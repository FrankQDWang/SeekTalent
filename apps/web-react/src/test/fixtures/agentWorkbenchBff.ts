import type {
  AgentWorkbenchConversationResponse,
  AgentWorkbenchStrategyGraph,
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
        text: "已确认需求，正在按本地库和猎聘来源检索候选人。",
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
            eventId: "tool:source_search:started",
            itemId: "tool_source_search",
            kind: "sourceSearch.started",
            label: "source search",
            payload: {
              kind: "source_search",
              itemId: "tool_source_search",
              summary: "检索 CTS 和猎聘安全摘要。",
            },
            status: "running",
            summary: "检索 CTS 和猎聘安全摘要。",
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
      otherInputPrompt: "其他",
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
              text: "Python 后端平台经验",
            },
          ],
          sectionId: "must_have_capabilities",
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
        displayName: "本地人才库",
        lastCheckedAt: now,
        sourceKind: "cts",
        status: "connected",
      },
      {
        displayName: "猎聘",
        lastCheckedAt: now,
        sourceKind: "liepin",
        status: "connected",
      },
    ],
    candidates: [
      {
        candidateId: "candidate_001",
        displayName: "候选人 A",
        headline: "平台工程负责人 / 上海 / Python + RAG",
        matchSummary: "Agent 工具调用平台和 RAG 链路证据完整。",
        sourceKind: "all",
        status: "running",
      },
      {
        candidateId: "candidate_002",
        displayName: "候选人 B",
        headline: "后端工程师 / 上海 / 检索系统",
        matchSummary: "检索和后端强，Agent 平台证据需要补充。",
        sourceKind: "cts",
        status: "pending",
      },
    ],
    detailApprovals: [
      {
        approvalId: "approval_001",
        candidateId: "candidate_001",
        reason: "读取候选人 A 完整简历前需要用户确认。",
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
      label: "需求确认",
      summary: "上海 AI Agent 平台工程，Python 后端，检索系统经验。",
      status: "completed",
      sourceKind: "all",
    },
    {
      nodeId: "activity_001",
      activityId: "activity_001",
      kind: "activity",
      label: "source search",
      summary: "正在检索 CTS 和猎聘安全摘要。",
      status: "running",
      sourceKind: "all",
    },
    {
      nodeId: "candidate_001",
      kind: "candidate",
      label: "候选人 A",
      summary: "Agent 工具调用平台和 RAG 链路证据完整。",
      status: "running",
      sourceKind: "all",
    },
  ],
  edges: [
    {
      edgeId: "edge_requirement_search",
      fromNodeId: "requirements",
      label: "生成检索词",
      toNodeId: "activity_001",
    },
    {
      edgeId: "edge_search_candidate",
      fromNodeId: "activity_001",
      label: "安全摘要",
      toNodeId: "candidate_001",
    },
  ],
};

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
    conversation: { status: "needs_confirmation" },
    pendingActions: {
      allowed: ["confirm_requirements"],
      pendingMemoryReviewCount: 0,
      pendingRequirementReviewCount: 1,
      primary: "确认需求后开始检索",
    },
  });

export const agentWorkbenchRunningViewFixture: AgentWorkbenchConversationResponse =
  workbenchView();

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
            eventId: "tool:source_search:failed",
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

export const agentWorkbenchLargeGraphFixture: AgentWorkbenchStrategyGraph = {
  nodes: [
    {
      nodeId: "large_requirement",
      kind: "requirements",
      label: "需求确认",
      summary: "上海 AI Agent 平台工程, Python 后端, 检索系统经验。",
      status: "completed",
      sourceKind: "all",
    },
    {
      nodeId: "large_user_message",
      kind: "message",
      label: "用户补充",
      summary: "偏好有工作流编排、评测和企业知识库落地经验。",
      status: "completed",
      sourceKind: "all",
      messageId: "msg_user_large_001",
    },
    {
      nodeId: "large_keyword_query",
      kind: "activity",
      label: "keyword query",
      summary: "AI Agent 平台工程 上海 Python RAG workflow orchestration。",
      status: "completed",
      sourceKind: "all",
      activityId: "activity_keyword_query",
    },
    {
      nodeId: "large_cts_search",
      kind: "activity",
      label: "CTS source search",
      summary: "本地人才库命中 18 个安全摘要。",
      status: "completed",
      sourceKind: "cts",
      activityId: "activity_cts_search",
    },
    {
      nodeId: "large_liepin_search",
      kind: "activity",
      label: "Liepin source search",
      summary: "猎聘来源命中 24 个安全摘要, 详情读取需审批。",
      status: "completed",
      sourceKind: "liepin",
      activityId: "activity_liepin_search",
    },
    {
      nodeId: "large_source_result",
      kind: "activity",
      label: "source_result merge",
      summary: "合并多来源结果, 保留 safe refs 和来源边界。",
      status: "completed",
      sourceKind: "all",
      activityId: "activity_source_result",
    },
    {
      nodeId: "large_dedupe",
      kind: "activity",
      label: "去重和来源归并",
      summary: "按 person key 与履历摘要合并重复候选人。",
      status: "completed",
      sourceKind: "all",
      activityId: "activity_dedupe",
    },
    {
      nodeId: "large_scoring",
      kind: "activity",
      label: "scoring",
      summary: "12 个候选人进入评分, 3 个强匹配。",
      status: "completed",
      sourceKind: "all",
      activityId: "activity_scoring",
    },
    {
      nodeId: "large_candidate_a",
      kind: "candidate",
      label: "候选人 A",
      summary: "Agent 工具调用平台和 RAG 链路证据完整。",
      status: "completed",
      sourceKind: "all",
    },
    {
      nodeId: "large_candidate_b",
      kind: "candidate",
      label: "候选人 B",
      summary: "检索和后端强, Agent 平台证据需要补充。",
      status: "completed",
      sourceKind: "cts",
    },
    {
      nodeId: "large_candidate_c",
      kind: "candidate",
      label: "候选人 C",
      summary: "工作流编排经验强, 需要确认近期稳定性。",
      status: "completed",
      sourceKind: "liepin",
    },
    {
      nodeId: "large_observation",
      kind: "activity",
      label: "observation",
      summary: "结果偏 RAG, 工具编排和评测证据仍需加强。",
      status: "completed",
      sourceKind: "all",
      activityId: "activity_observation",
    },
    {
      nodeId: "large_reflection",
      kind: "activity",
      label: "reflection",
      summary: "下一轮加入 eval harness 和 workflow orchestration。",
      status: "running",
      sourceKind: "all",
      activityId: "activity_reflection",
    },
    {
      nodeId: "large_detail_approval",
      kind: "approval",
      label: "detail_approval",
      summary: "候选人 A 完整简历读取等待用户审批。",
      status: "pending",
      sourceKind: "liepin",
    },
    {
      nodeId: "large_final_summary",
      kind: "final",
      label: "final_summary",
      summary: "最终短名单将在审批和第二轮检索后生成。",
      status: "pending",
      sourceKind: "all",
    },
  ],
  edges: [
    {
      edgeId: "large_edge_requirement_message",
      fromNodeId: "large_requirement",
      toNodeId: "large_user_message",
      label: "补充约束",
    },
    {
      edgeId: "large_edge_message_keyword",
      fromNodeId: "large_user_message",
      toNodeId: "large_keyword_query",
      label: "生成检索词",
    },
    {
      edgeId: "large_edge_keyword_cts",
      fromNodeId: "large_keyword_query",
      toNodeId: "large_cts_search",
      label: "本地库",
    },
    {
      edgeId: "large_edge_keyword_liepin",
      fromNodeId: "large_keyword_query",
      toNodeId: "large_liepin_search",
      label: "外部来源",
    },
    {
      edgeId: "large_edge_cts_result",
      fromNodeId: "large_cts_search",
      toNodeId: "large_source_result",
      label: "18 summaries",
    },
    {
      edgeId: "large_edge_liepin_result",
      fromNodeId: "large_liepin_search",
      toNodeId: "large_source_result",
      label: "24 summaries",
    },
    {
      edgeId: "large_edge_result_dedupe",
      fromNodeId: "large_source_result",
      toNodeId: "large_dedupe",
      label: "safe refs",
    },
    {
      edgeId: "large_edge_dedupe_scoring",
      fromNodeId: "large_dedupe",
      toNodeId: "large_scoring",
      label: "12 candidates",
    },
    {
      edgeId: "large_edge_scoring_a",
      fromNodeId: "large_scoring",
      toNodeId: "large_candidate_a",
      label: "92",
    },
    {
      edgeId: "large_edge_scoring_b",
      fromNodeId: "large_scoring",
      toNodeId: "large_candidate_b",
      label: "84",
    },
    {
      edgeId: "large_edge_scoring_c",
      fromNodeId: "large_scoring",
      toNodeId: "large_candidate_c",
      label: "81",
    },
    {
      edgeId: "large_edge_candidates_observation",
      fromNodeId: "large_candidate_a",
      toNodeId: "large_observation",
      label: "强匹配证据",
    },
    {
      edgeId: "large_edge_candidate_b_observation",
      fromNodeId: "large_candidate_b",
      toNodeId: "large_observation",
      label: "补充风险",
    },
    {
      edgeId: "large_edge_observation_reflection",
      fromNodeId: "large_observation",
      toNodeId: "large_reflection",
      label: "调整策略",
    },
    {
      edgeId: "large_edge_candidate_a_detail",
      fromNodeId: "large_candidate_a",
      toNodeId: "large_detail_approval",
      label: "详情审批",
    },
    {
      edgeId: "large_edge_reflection_final",
      fromNodeId: "large_reflection",
      toNodeId: "large_final_summary",
      label: "等待完成",
    },
    {
      edgeId: "large_edge_detail_final",
      fromNodeId: "large_detail_approval",
      toNodeId: "large_final_summary",
      label: "审批结果",
    },
  ],
};
