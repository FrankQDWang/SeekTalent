import {
  agentWorkbenchFixture,
  agentWorkbenchTranscriptFailedToolFixture,
  type AgentWorkbenchConversationResponse,
  type AgentWorkbenchRuntimeStageStatus,
  type AgentWorkbenchTranscriptGroup,
} from './agentWorkbench';

function withStages(
  stages: AgentWorkbenchRuntimeStageStatus[],
  status: AgentWorkbenchRuntimeStageStatus['status'],
): AgentWorkbenchRuntimeStageStatus[] {
  return stages.map((stage) => ({ ...stage, status }));
}

export const agentWorkbenchEmptyFixture: AgentWorkbenchConversationResponse = {
  ...agentWorkbenchFixture,
  conversation: {
    ...agentWorkbenchFixture.conversation,
    title: '新的候选人检索',
    status: 'empty',
    activeRunId: null,
    summary: '',
  },
  messages: [],
  activities: [],
  transcriptGroups: [],
  requirementDraft: {
    status: 'empty',
    targetRole: '',
    location: '',
    seniority: '',
    mustHaves: [],
    preferences: [],
    exclusions: [],
    reviewQuestions: [],
    updatedAt: null,
  },
  runtime: {
    runId: null,
    status: 'idle',
    currentStage: null,
    activeRound: null,
    progressPercent: 0,
    startedAt: null,
    completedAt: null,
    stages: withStages(agentWorkbenchFixture.runtime.stages, 'pending').map((stage) => ({
      ...stage,
      progressPercent: 0,
      startedAt: null,
      completedAt: null,
    })),
  },
  strategyGraph: {
    signature: 'strategy_graph_empty',
    layoutStatus: 'empty',
    nodes: [],
    edges: [],
  },
  thinkingProcess: {
    selectedRoundId: null,
    rounds: [],
  },
  candidates: {
    items: [],
    totalCount: 0,
    selectedCandidateId: null,
    lastUpdatedAt: null,
  },
  detailApprovals: [],
  reviewArtifacts: {
    items: [],
    lastUpdatedAt: null,
  },
  finalSummary: {
    status: 'not_started',
    title: '',
    narrative: '',
    generatedAt: null,
    shortlist: [],
    exportArtifactIds: [],
  },
  pendingActions: [],
  streamCursor: {
    ...agentWorkbenchFixture.streamCursor,
    latestSeq: 0,
    firstAvailableSeq: 0,
    isConnected: false,
    hasGap: false,
    disconnectedReason: null,
  },
};

export const agentWorkbenchNeedsReviewFixture: AgentWorkbenchConversationResponse = {
  ...agentWorkbenchFixture,
  conversation: {
    ...agentWorkbenchFixture.conversation,
    status: 'needs_review',
    summary: '需要确认地点、薪资范围和排除条件。',
  },
  requirementDraft: {
    ...agentWorkbenchFixture.requirementDraft,
    status: 'needs_review',
    reviewQuestions: [
      {
        id: 'question_location_scope',
        text: '是否只看上海本地，还是接受长三角远程？',
        severity: 'blocking',
      },
      {
        id: 'question_compensation',
        text: '薪资范围是否需要作为强过滤条件？',
        severity: 'recommended',
      },
    ],
  },
  runtime: {
    ...agentWorkbenchFixture.runtime,
    status: 'waiting_for_user',
    currentStage: 'requirement',
    progressPercent: 12,
  },
  pendingActions: [
    {
      id: 'pending_confirm_requirement',
      kind: 'confirm_requirement',
      label: '确认检索需求',
      priority: 'high',
      status: 'pending',
      createdAt: '2026-06-13T09:28:00.000Z',
      relatedCandidateId: null,
      relatedSourceConnectionId: null,
    },
  ],
};

export const agentWorkbenchRunningFixture = agentWorkbenchFixture;

export const agentWorkbenchDisconnectedFixture: AgentWorkbenchConversationResponse = {
  ...agentWorkbenchFixture,
  conversation: {
    ...agentWorkbenchFixture.conversation,
    status: 'disconnected',
    summary: '连接已断开，保留当前快照并等待恢复。',
  },
  streamCursor: {
    ...agentWorkbenchFixture.streamCursor,
    isConnected: false,
    hasGap: true,
    disconnectedReason: 'server_gap',
    reconnectAfterMs: 1500,
  },
  pendingActions: [
    ...agentWorkbenchFixture.pendingActions,
    {
      id: 'pending_resolve_gap',
      kind: 'resolve_stream_gap',
      label: '恢复流式事件缺口',
      priority: 'high',
      status: 'pending',
      createdAt: '2026-06-13T09:31:00.000Z',
      relatedCandidateId: null,
      relatedSourceConnectionId: null,
    },
  ],
};

export const agentWorkbenchPermissionDeniedFixture: AgentWorkbenchConversationResponse = {
  ...agentWorkbenchFixture,
  conversation: {
    ...agentWorkbenchFixture.conversation,
    status: 'permission_denied',
    access: 'permission_denied',
    summary: '来源连接需要重新授权。',
  },
  sourceConnections: agentWorkbenchFixture.sourceConnections.map((source) =>
    source.id === 'source_liepin'
      ? {
          ...source,
          status: 'permission_denied',
          statusSummary: '授权已过期，需要重新登录。',
        }
      : source,
  ),
  pendingActions: [
    {
      id: 'pending_reauth_source',
      kind: 'reauth_source',
      label: '重新授权猎聘来源',
      priority: 'high',
      status: 'pending',
      createdAt: '2026-06-13T09:31:00.000Z',
      relatedCandidateId: null,
      relatedSourceConnectionId: 'source_liepin',
    },
  ],
  streamCursor: {
    ...agentWorkbenchFixture.streamCursor,
    isConnected: false,
    disconnectedReason: 'permission_denied',
  },
};

const failedTranscriptGroups: AgentWorkbenchTranscriptGroup[] =
  agentWorkbenchFixture.transcriptGroups.map((group) =>
    group.groupId === 'group_run_001'
      ? {
          ...group,
          status: 'failed',
          events: group.events.map((event) =>
            event.eventId === 'evt_source_search_001'
              ? agentWorkbenchTranscriptFailedToolFixture
              : event,
          ),
        }
      : group,
  );

export const agentWorkbenchFailedFixture: AgentWorkbenchConversationResponse = {
  ...agentWorkbenchFixture,
  conversation: {
    ...agentWorkbenchFixture.conversation,
    status: 'failed',
    summary: '来源检索失败，未暴露原始 provider payload。',
  },
  runtime: {
    ...agentWorkbenchFixture.runtime,
    status: 'failed',
    progressPercent: 34,
    stages: agentWorkbenchFixture.runtime.stages.map((stage) =>
      stage.name === 'source_search'
        ? { ...stage, status: 'failed', progressPercent: 100 }
        : stage,
    ),
  },
  transcriptGroups: failedTranscriptGroups,
  pendingActions: [
    {
      id: 'pending_review_failed_tool',
      kind: 'review_failed_tool',
      label: '查看失败来源并重试',
      priority: 'high',
      status: 'pending',
      createdAt: '2026-06-13T09:31:00.000Z',
      relatedCandidateId: null,
      relatedSourceConnectionId: 'source_liepin',
    },
  ],
};

export const agentWorkbenchCompletedFixture: AgentWorkbenchConversationResponse = {
  ...agentWorkbenchFixture,
  conversation: {
    ...agentWorkbenchFixture.conversation,
    status: 'completed',
    summary: '最终候选人名单已生成。',
  },
  runtime: {
    ...agentWorkbenchFixture.runtime,
    status: 'completed',
    currentStage: 'final_summary',
    progressPercent: 100,
    completedAt: '2026-06-13T09:45:00.000Z',
    stages: withStages(agentWorkbenchFixture.runtime.stages, 'succeeded').map((stage) => ({
      ...stage,
      progressPercent: 100,
      completedAt: stage.completedAt ?? '2026-06-13T09:45:00.000Z',
    })),
  },
  transcriptGroups: agentWorkbenchFixture.transcriptGroups.map((group) => ({
    ...group,
    status: 'succeeded',
    completedAt: group.completedAt ?? '2026-06-13T09:45:00.000Z',
  })),
  finalSummary: {
    status: 'ready',
    title: '上海 AI Agent 平台工程候选人 shortlist',
    narrative: '第一轮推荐 2 位候选人，候选人 A 为强匹配。',
    generatedAt: '2026-06-13T09:45:00.000Z',
    shortlist: [
      {
        candidateId: 'candidate_001',
        rank: 1,
        displayName: '候选人 A',
        reason: 'Agent 工具调用平台、RAG 和 Python 后端经验同时匹配。',
        risks: ['近期稳定性需要确认'],
      },
      {
        candidateId: 'candidate_002',
        rank: 2,
        displayName: '候选人 B',
        reason: '后端和检索经验强，Agent 平台证据需要补充。',
        risks: ['Agent 工具调用证据不足'],
      },
    ],
    exportArtifactIds: ['artifact_final_shortlist'],
  },
  pendingActions: [
    {
      id: 'pending_export_final',
      kind: 'export_final_summary',
      label: '导出最终名单',
      priority: 'normal',
      status: 'pending',
      createdAt: '2026-06-13T09:45:00.000Z',
      relatedCandidateId: null,
      relatedSourceConnectionId: null,
    },
  ],
};

export const agentWorkbenchArchivedFixture: AgentWorkbenchConversationResponse = {
  ...agentWorkbenchCompletedFixture,
  conversation: {
    ...agentWorkbenchCompletedFixture.conversation,
    status: 'archived',
    archivedAt: '2026-06-13T10:00:00.000Z',
    summary: '已归档，只读查看。',
  },
  pendingActions: [],
};

export const agentWorkbenchStateFixtures = [
  agentWorkbenchEmptyFixture,
  agentWorkbenchNeedsReviewFixture,
  agentWorkbenchRunningFixture,
  agentWorkbenchDisconnectedFixture,
  agentWorkbenchPermissionDeniedFixture,
  agentWorkbenchFailedFixture,
  agentWorkbenchCompletedFixture,
  agentWorkbenchArchivedFixture,
];
