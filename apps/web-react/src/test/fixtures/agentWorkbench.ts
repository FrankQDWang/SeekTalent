export type IsoDateTimeString = string;
export type AgentWorkbenchSchemaVersion = 'agent-workbench.v1';
export type AgentWorkbenchConversationId = string;
export type AgentWorkbenchRunId = string;
export type AgentWorkbenchMessageId = string;
export type AgentWorkbenchActivityId = string;
export type AgentWorkbenchGroupId = string;
export type AgentWorkbenchEventId = string;
export type AgentWorkbenchSourceKind = 'cts' | 'liepin' | 'internal_memory' | 'web';

export type AgentWorkbenchStatus =
  | 'pending'
  | 'running'
  | 'waiting_for_user'
  | 'succeeded'
  | 'failed'
  | 'cancelled'
  | 'superseded';

export type AgentWorkbenchConversationStatus =
  | 'empty'
  | 'drafting'
  | 'needs_review'
  | 'running'
  | 'disconnected'
  | 'permission_denied'
  | 'failed'
  | 'completed'
  | 'archived';

export type AgentWorkbenchRuntimeStage =
  | 'requirement'
  | 'source_search'
  | 'scoring'
  | 'reflection'
  | 'detail_approval'
  | 'final_summary';

export type AgentWorkbenchStreamKind =
  | 'item.started'
  | 'item.completed'
  | 'message.created'
  | 'message.delta'
  | 'message.completed'
  | 'activity.upserted'
  | 'requirement.updated'
  | 'runtime.eventProjected'
  | 'strategyGraph.changed'
  | 'tool.started'
  | 'tool.outputDelta'
  | 'tool.completed'
  | 'tool.failed'
  | 'sourceSearch.started'
  | 'sourceSearch.completed'
  | 'sourceSearch.failed'
  | 'webSearch.started'
  | 'webSearch.completed'
  | 'command.started'
  | 'command.outputDelta'
  | 'command.completed'
  | 'command.failed'
  | 'runtime.stageChanged'
  | 'candidate.upserted'
  | 'detailApproval.changed'
  | 'finalSummary.updated'
  | 'pendingAction.changed'
  | 'sourceConnection.changed'
  | 'context.compacted'
  | 'transcript.groupCollapsed'
  | 'thinkingProcess.changed'
  | 'stream.gap';

export interface AgentWorkbenchConversation {
  id: AgentWorkbenchConversationId;
  title: string;
  status: AgentWorkbenchConversationStatus;
  access: 'available' | 'permission_denied';
  activeRunId: AgentWorkbenchRunId | null;
  createdAt: IsoDateTimeString;
  updatedAt: IsoDateTimeString;
  archivedAt: IsoDateTimeString | null;
  summary: string;
}

export interface AgentWorkbenchSafeAttachment {
  id: string;
  name: string;
  kind: 'image' | 'document' | 'artifact_ref';
  mimeType: string;
  sizeLabel: string;
  thumbnailUrl: string | null;
  artifactRef: string | null;
}

export interface AgentWorkbenchMessage {
  id: AgentWorkbenchMessageId;
  role: 'user' | 'assistant' | 'system';
  status: AgentWorkbenchStatus;
  text: string;
  createdAt: IsoDateTimeString;
  completedAt: IsoDateTimeString | null;
  attachments: AgentWorkbenchSafeAttachment[];
}

export interface AgentWorkbenchActivity {
  id: AgentWorkbenchActivityId;
  kind:
    | 'requirement'
    | 'runtime'
    | 'source_search'
    | 'tool'
    | 'candidate'
    | 'detail_approval'
    | 'final_summary'
    | 'system';
  status: AgentWorkbenchStatus;
  title: string;
  summary: string;
  sourceKind: AgentWorkbenchSourceKind | 'all' | null;
  occurredAt: IsoDateTimeString;
  relatedRunId: AgentWorkbenchRunId | null;
  relatedCandidateId: string | null;
}

export interface AgentWorkbenchTranscriptDetailLine {
  id: string;
  kind: 'input' | 'output' | 'summary' | 'evidence' | 'error';
  label: string;
  text: string;
  timestamp: IsoDateTimeString;
}

export interface AgentWorkbenchTranscriptMessagePayload {
  kind: 'message';
  messageId: AgentWorkbenchMessageId;
  role: 'user' | 'assistant' | 'system';
  text: string;
  attachments: AgentWorkbenchSafeAttachment[];
}

export interface AgentWorkbenchTranscriptToolPayload {
  kind: 'tool';
  toolCallId: string;
  toolKind:
    | 'file_read'
    | 'web_search'
    | 'source_search'
    | 'page_load'
    | 'candidate_scoring'
    | 'resume_detail';
  label: string;
  inputSummary: string;
  outputPreview: string;
  detailLines: AgentWorkbenchTranscriptDetailLine[];
  errorMessage: string | null;
}

export interface AgentWorkbenchTranscriptCommandPayload {
  kind: 'command';
  commandId: string;
  label: string;
  commandSummary: string;
  outputLines: string[];
  exitCode: number | null;
  errorMessage: string | null;
}

export interface AgentWorkbenchTranscriptRuntimePayload {
  kind: 'runtime';
  stage: AgentWorkbenchRuntimeStage;
  sourceKind: AgentWorkbenchSourceKind | 'all' | null;
  summary: string;
}

export interface AgentWorkbenchTranscriptContextPayload {
  kind: 'contextDivider';
  compactedMessageCount: number;
  summary: string;
}

export type AgentWorkbenchTranscriptPayload =
  | AgentWorkbenchTranscriptMessagePayload
  | AgentWorkbenchTranscriptToolPayload
  | AgentWorkbenchTranscriptCommandPayload
  | AgentWorkbenchTranscriptRuntimePayload
  | AgentWorkbenchTranscriptContextPayload;

export interface AgentWorkbenchTranscriptEvent {
  eventId: AgentWorkbenchEventId;
  groupId: AgentWorkbenchGroupId;
  kind: AgentWorkbenchStreamKind;
  status: AgentWorkbenchStatus;
  title: string;
  sequence: number;
  occurredAt: IsoDateTimeString;
  completedAt: IsoDateTimeString | null;
  durationLabel: string | null;
  payload: AgentWorkbenchTranscriptPayload;
}

export interface AgentWorkbenchTranscriptGroup {
  groupId: AgentWorkbenchGroupId;
  conversationId: AgentWorkbenchConversationId;
  runId: AgentWorkbenchRunId | null;
  title: string;
  status: AgentWorkbenchStatus;
  collapsedByDefault: boolean;
  startedAt: IsoDateTimeString;
  completedAt: IsoDateTimeString | null;
  durationLabel: string;
  events: AgentWorkbenchTranscriptEvent[];
}

export interface AgentWorkbenchRequirementCriterion {
  id: string;
  label: string;
  status: 'draft' | 'confirmed' | 'needs_review';
  source: 'user' | 'assistant' | 'system';
}

export interface AgentWorkbenchRequirementQuestion {
  id: string;
  text: string;
  severity: 'blocking' | 'recommended' | 'optional';
}

export interface AgentWorkbenchRequirementDraft {
  status: 'empty' | 'draft' | 'needs_review' | 'confirmed';
  targetRole: string;
  location: string;
  seniority: string;
  mustHaves: AgentWorkbenchRequirementCriterion[];
  preferences: AgentWorkbenchRequirementCriterion[];
  exclusions: AgentWorkbenchRequirementCriterion[];
  reviewQuestions: AgentWorkbenchRequirementQuestion[];
  updatedAt: IsoDateTimeString | null;
}

export interface AgentWorkbenchRuntimeStageStatus {
  id: string;
  name: AgentWorkbenchRuntimeStage;
  label: string;
  status: AgentWorkbenchStatus;
  progressPercent: number;
  startedAt: IsoDateTimeString | null;
  completedAt: IsoDateTimeString | null;
}

export interface AgentWorkbenchRuntime {
  runId: AgentWorkbenchRunId | null;
  status: 'idle' | 'running' | 'waiting_for_user' | 'blocked' | 'failed' | 'completed';
  currentStage: AgentWorkbenchRuntimeStage | null;
  activeRound: number | null;
  progressPercent: number;
  startedAt: IsoDateTimeString | null;
  completedAt: IsoDateTimeString | null;
  stages: AgentWorkbenchRuntimeStageStatus[];
}

export interface AgentWorkbenchGraphMetric {
  label: string;
  value: string;
  tone: 'neutral' | 'positive' | 'warning' | 'danger';
}

export interface AgentWorkbenchGraphNode {
  id: string;
  kind:
    | 'requirement'
    | 'source_search'
    | 'source_result'
    | 'scoring'
    | 'reflection'
    | 'detail_approval'
    | 'final_summary';
  stage: AgentWorkbenchRuntimeStage;
  status: AgentWorkbenchStatus;
  title: string;
  summary: string;
  lane: 'left' | 'center' | 'right' | 'shared';
  sourceKind: AgentWorkbenchSourceKind | 'all' | null;
  round: number | null;
  metrics: AgentWorkbenchGraphMetric[];
}

export interface AgentWorkbenchGraphEdge {
  id: string;
  source: string;
  target: string;
  label: string;
  status: 'planned' | 'active' | 'completed' | 'blocked';
}

export interface AgentWorkbenchStrategyGraph {
  signature: string;
  layoutStatus: 'empty' | 'ready' | 'stale';
  nodes: AgentWorkbenchGraphNode[];
  edges: AgentWorkbenchGraphEdge[];
}

export interface AgentWorkbenchThinkingKeywords {
  queryTerms: string[];
  keywordQuery: string;
  executedQueries: string[];
}

export interface AgentWorkbenchThinkingObservation {
  searchedCount: number;
  scoredCount: number;
  resumeQualityComment: string;
}

export interface AgentWorkbenchThinkingReflection {
  summary: string;
  rationale: string;
  suggestedActivateTerms: string[];
  suggestedKeepTerms: string[];
  suggestedDeprioritizeTerms: string[];
  suggestedDropTerms: string[];
}

export interface AgentWorkbenchThinkingRound {
  id: string;
  round: number;
  status: AgentWorkbenchStatus;
  title: string;
  keywords: AgentWorkbenchThinkingKeywords;
  observation: AgentWorkbenchThinkingObservation;
  reflection: AgentWorkbenchThinkingReflection;
  startedAt: IsoDateTimeString;
  completedAt: IsoDateTimeString | null;
}

export interface AgentWorkbenchThinkingProcess {
  selectedRoundId: string | null;
  rounds: AgentWorkbenchThinkingRound[];
}

export interface AgentWorkbenchSourceConnection {
  id: string;
  sourceKind: AgentWorkbenchSourceKind;
  displayName: string;
  status: 'connected' | 'disconnected' | 'permission_denied' | 'syncing';
  statusSummary: string;
  lastSyncAt: IsoDateTimeString | null;
  evidenceBadges: string[];
}

export interface AgentWorkbenchCandidateEvidence {
  id: string;
  sourceKind: AgentWorkbenchSourceKind;
  label: string;
  summary: string;
  confidence: 'low' | 'medium' | 'high';
}

export interface AgentWorkbenchCandidate {
  id: string;
  displayName: string;
  title: string;
  company: string;
  location: string;
  summary: string;
  score: number;
  fitBucket: 'strong_fit' | 'fit' | 'stretch' | 'risk';
  status: 'new' | 'reviewing' | 'shortlisted' | 'rejected';
  sourceBadges: string[];
  matchedMustHaves: string[];
  risks: string[];
  evidence: AgentWorkbenchCandidateEvidence[];
  canRequestDetail: boolean;
  canOpenProvider: boolean;
}

export interface AgentWorkbenchCandidateQueue {
  items: AgentWorkbenchCandidate[];
  totalCount: number;
  selectedCandidateId: string | null;
  lastUpdatedAt: IsoDateTimeString | null;
}

export interface AgentWorkbenchDetailApproval {
  id: string;
  candidateId: string;
  status: 'not_required' | 'pending' | 'approved' | 'denied' | 'failed';
  requestedAt: IsoDateTimeString | null;
  resolvedAt: IsoDateTimeString | null;
  requestReason: string;
  decisionSummary: string | null;
}

export interface AgentWorkbenchReviewArtifact {
  id: string;
  kind: 'requirement_review' | 'detail_snapshot' | 'final_shortlist' | 'stream_recovery';
  title: string;
  status: 'draft' | 'ready' | 'failed';
  safeSummary: string;
  artifactRef: string;
  createdAt: IsoDateTimeString;
}

export interface AgentWorkbenchReviewArtifacts {
  items: AgentWorkbenchReviewArtifact[];
  lastUpdatedAt: IsoDateTimeString | null;
}

export interface AgentWorkbenchFinalSummaryCandidate {
  candidateId: string;
  rank: number;
  displayName: string;
  reason: string;
  risks: string[];
}

export interface AgentWorkbenchFinalSummary {
  status: 'not_started' | 'drafting' | 'ready' | 'failed';
  title: string;
  narrative: string;
  generatedAt: IsoDateTimeString | null;
  shortlist: AgentWorkbenchFinalSummaryCandidate[];
  exportArtifactIds: string[];
}

export interface AgentWorkbenchPendingAction {
  id: string;
  kind:
    | 'confirm_requirement'
    | 'start_workflow'
    | 'approve_detail'
    | 'resolve_stream_gap'
    | 'reauth_source'
    | 'review_failed_tool'
    | 'export_final_summary';
  label: string;
  priority: 'low' | 'normal' | 'high';
  status: 'pending' | 'completed' | 'blocked';
  createdAt: IsoDateTimeString;
  relatedCandidateId: string | null;
  relatedSourceConnectionId: string | null;
}

export interface AgentWorkbenchStreamCursor {
  schemaVersion: AgentWorkbenchSchemaVersion;
  conversationId: AgentWorkbenchConversationId;
  latestSeq: number;
  firstAvailableSeq: number;
  streamUrl: string;
  isConnected: boolean;
  hasGap: boolean;
  disconnectedReason: 'network' | 'permission_denied' | 'server_gap' | null;
  reconnectAfterMs: number | null;
}

export interface AgentWorkbenchConversationResponse {
  schemaVersion: AgentWorkbenchSchemaVersion;
  conversation: AgentWorkbenchConversation;
  messages: AgentWorkbenchMessage[];
  activities: AgentWorkbenchActivity[];
  transcriptGroups: AgentWorkbenchTranscriptGroup[];
  requirementDraft: AgentWorkbenchRequirementDraft;
  runtime: AgentWorkbenchRuntime;
  strategyGraph: AgentWorkbenchStrategyGraph;
  thinkingProcess: AgentWorkbenchThinkingProcess;
  sourceConnections: AgentWorkbenchSourceConnection[];
  candidates: AgentWorkbenchCandidateQueue;
  detailApprovals: AgentWorkbenchDetailApproval[];
  reviewArtifacts: AgentWorkbenchReviewArtifacts;
  finalSummary: AgentWorkbenchFinalSummary;
  pendingActions: AgentWorkbenchPendingAction[];
  streamCursor: AgentWorkbenchStreamCursor;
}

export interface AgentWorkbenchMessageDeltaPayload {
  groupId: AgentWorkbenchGroupId;
  eventId: AgentWorkbenchEventId;
  messageId: AgentWorkbenchMessageId;
  textDelta: string;
  accumulatedText: string;
}

export interface AgentWorkbenchToolOutputDeltaPayload {
  groupId: AgentWorkbenchGroupId;
  eventId: AgentWorkbenchEventId;
  toolCallId: string;
  detailLine: AgentWorkbenchTranscriptDetailLine;
  outputPreview: string;
}

export interface AgentWorkbenchCommandOutputDeltaPayload {
  groupId: AgentWorkbenchGroupId;
  eventId: AgentWorkbenchEventId;
  commandId: string;
  outputLine: string;
  outputLines: string[];
}

export interface AgentWorkbenchStreamGapPayload {
  requestedAfterSeq: number;
  firstAvailableSeq: number;
  latestSeq: number;
  reason: 'retained_ledger_gap' | 'sequence_discontinuity';
  recoveryConversationUrl: string;
}

export interface AgentWorkbenchStreamPayloads {
  'item.started': AgentWorkbenchTranscriptEvent;
  'item.completed': AgentWorkbenchTranscriptEvent;
  'message.created': AgentWorkbenchTranscriptEvent;
  'message.delta': AgentWorkbenchMessageDeltaPayload;
  'message.completed': AgentWorkbenchTranscriptEvent;
  'activity.upserted': AgentWorkbenchActivity;
  'requirement.updated': AgentWorkbenchRequirementDraft;
  'runtime.eventProjected': AgentWorkbenchActivity;
  'strategyGraph.changed': AgentWorkbenchStrategyGraph;
  'tool.started': AgentWorkbenchTranscriptEvent;
  'tool.outputDelta': AgentWorkbenchToolOutputDeltaPayload;
  'tool.completed': AgentWorkbenchTranscriptEvent;
  'tool.failed': AgentWorkbenchTranscriptEvent;
  'sourceSearch.started': AgentWorkbenchTranscriptEvent;
  'sourceSearch.completed': AgentWorkbenchTranscriptEvent;
  'sourceSearch.failed': AgentWorkbenchTranscriptEvent;
  'webSearch.started': AgentWorkbenchTranscriptEvent;
  'webSearch.completed': AgentWorkbenchTranscriptEvent;
  'command.started': AgentWorkbenchTranscriptEvent;
  'command.outputDelta': AgentWorkbenchCommandOutputDeltaPayload;
  'command.completed': AgentWorkbenchTranscriptEvent;
  'command.failed': AgentWorkbenchTranscriptEvent;
  'runtime.stageChanged': AgentWorkbenchRuntime;
  'candidate.upserted': AgentWorkbenchCandidate;
  'detailApproval.changed': AgentWorkbenchDetailApproval;
  'finalSummary.updated': AgentWorkbenchFinalSummary;
  'pendingAction.changed': AgentWorkbenchPendingAction;
  'sourceConnection.changed': AgentWorkbenchSourceConnection;
  'context.compacted': AgentWorkbenchTranscriptEvent;
  'transcript.groupCollapsed': { groupId: AgentWorkbenchGroupId; collapsed: boolean };
  'thinkingProcess.changed': AgentWorkbenchThinkingProcess;
  'stream.gap': AgentWorkbenchStreamGapPayload;
}

export type AgentWorkbenchStreamEnvelope = {
  [Kind in AgentWorkbenchStreamKind]: {
    schemaVersion: AgentWorkbenchSchemaVersion;
    conversationId: AgentWorkbenchConversationId;
    seq: number;
    kind: Kind;
    payload: AgentWorkbenchStreamPayloads[Kind];
    createdAt: IsoDateTimeString;
  };
}[AgentWorkbenchStreamKind];

const conversationId = 'agent_conv_001';
const runId = 'agent_run_001';
const now = '2026-06-13T09:30:00.000Z';

const userMessage: AgentWorkbenchMessage = {
  id: 'msg_user_001',
  role: 'user',
  status: 'succeeded',
  text: '帮我找在上海做 AI Agent 平台工程的候选人，重点看后端和检索经验。',
  createdAt: '2026-06-13T09:28:00.000Z',
  completedAt: '2026-06-13T09:28:02.000Z',
  attachments: [],
};

const assistantMessage: AgentWorkbenchMessage = {
  id: 'msg_assistant_001',
  role: 'assistant',
  status: 'succeeded',
  text: '我会先确认硬性条件，再启动第一轮检索并记录策略变化。',
  createdAt: '2026-06-13T09:28:03.000Z',
  completedAt: '2026-06-13T09:28:06.000Z',
  attachments: [],
};

const requirementEvent: AgentWorkbenchTranscriptEvent = {
  eventId: 'evt_requirement_confirmed',
  groupId: 'group_run_001',
  kind: 'requirement.updated',
  status: 'succeeded',
  title: '确认检索需求',
  sequence: 1,
  occurredAt: '2026-06-13T09:28:05.000Z',
  completedAt: '2026-06-13T09:28:08.000Z',
  durationLabel: '3s',
  payload: {
    kind: 'runtime',
    stage: 'requirement',
    sourceKind: 'all',
    summary: '目标岗位、地点、经验和排除条件已确认。',
  },
};

const sourceSearchToolPayload: AgentWorkbenchTranscriptToolPayload = {
  kind: 'tool',
  toolCallId: 'tool_source_search_001',
  toolKind: 'source_search',
  label: 'Read 42 profiles from connected sources',
  inputSummary: 'AI Agent 平台工程, 上海, Python, RAG, 后端',
  outputPreview: '42 profiles found, 12 moved to scoring.',
  errorMessage: null,
  detailLines: [
    {
      id: 'detail_source_query',
      kind: 'input',
      label: 'Query',
      text: 'AI Agent 平台工程 上海 Python RAG 后端',
      timestamp: '2026-06-13T09:28:10.000Z',
    },
    {
      id: 'detail_source_result',
      kind: 'summary',
      label: 'Result',
      text: '42 safe profile summaries matched source filters.',
      timestamp: '2026-06-13T09:29:18.000Z',
    },
  ],
};

const sourceSearchEvent: AgentWorkbenchTranscriptEvent = {
  eventId: 'evt_source_search_001',
  groupId: 'group_run_001',
  kind: 'sourceSearch.completed',
  status: 'succeeded',
  title: '检索候选人来源',
  sequence: 2,
  occurredAt: '2026-06-13T09:28:09.000Z',
  completedAt: '2026-06-13T09:29:18.000Z',
  durationLabel: '1m 9s',
  payload: sourceSearchToolPayload,
};

const scoringEvent: AgentWorkbenchTranscriptEvent = {
  eventId: 'evt_scoring_001',
  groupId: 'group_run_001',
  kind: 'tool.completed',
  status: 'succeeded',
  title: '评分并生成 observation',
  sequence: 3,
  occurredAt: '2026-06-13T09:29:20.000Z',
  completedAt: '2026-06-13T09:29:55.000Z',
  durationLabel: '35s',
  payload: {
    kind: 'tool',
    toolCallId: 'tool_scoring_001',
    toolKind: 'candidate_scoring',
    label: 'Scored 12 candidates',
    inputSummary: 'Requirement criteria and safe profile summaries.',
    outputPreview: '3 strong fits, 5 fits, 4 stretch candidates.',
    errorMessage: null,
    detailLines: [
      {
        id: 'detail_scoring_result',
        kind: 'summary',
        label: 'Observation',
        text: '候选人覆盖后端、检索和 Agent 平台经验，简历质量整体较高。',
        timestamp: '2026-06-13T09:29:55.000Z',
      },
    ],
  },
};

const assistantTranscriptEvent: AgentWorkbenchTranscriptEvent = {
  eventId: 'evt_assistant_001',
  groupId: 'group_run_001',
  kind: 'message.completed',
  status: 'succeeded',
  title: 'Agent response',
  sequence: 4,
  occurredAt: assistantMessage.createdAt,
  completedAt: assistantMessage.completedAt,
  durationLabel: null,
  payload: {
    kind: 'message',
    messageId: assistantMessage.id,
    role: 'assistant',
    text: assistantMessage.text,
    attachments: [],
  },
};

const contextDividerEvent: AgentWorkbenchTranscriptEvent = {
  eventId: 'evt_context_compacted_001',
  groupId: 'group_context_001',
  kind: 'context.compacted',
  status: 'succeeded',
  title: '上下文已压缩',
  sequence: 5,
  occurredAt: '2026-06-13T09:30:10.000Z',
  completedAt: '2026-06-13T09:30:10.000Z',
  durationLabel: null,
  payload: {
    kind: 'contextDivider',
    compactedMessageCount: 8,
    summary: '已保留需求、检索策略、候选人摘要和审批状态。',
  },
};

const runtimeStages: AgentWorkbenchRuntimeStageStatus[] = [
  {
    id: 'stage_requirement',
    name: 'requirement',
    label: '需求确认',
    status: 'succeeded',
    progressPercent: 100,
    startedAt: '2026-06-13T09:28:03.000Z',
    completedAt: '2026-06-13T09:28:08.000Z',
  },
  {
    id: 'stage_source_search',
    name: 'source_search',
    label: '来源检索',
    status: 'succeeded',
    progressPercent: 100,
    startedAt: '2026-06-13T09:28:09.000Z',
    completedAt: '2026-06-13T09:29:18.000Z',
  },
  {
    id: 'stage_scoring',
    name: 'scoring',
    label: '候选人评分',
    status: 'succeeded',
    progressPercent: 100,
    startedAt: '2026-06-13T09:29:20.000Z',
    completedAt: '2026-06-13T09:29:55.000Z',
  },
  {
    id: 'stage_reflection',
    name: 'reflection',
    label: '反思和下一轮',
    status: 'running',
    progressPercent: 60,
    startedAt: '2026-06-13T09:29:56.000Z',
    completedAt: null,
  },
  {
    id: 'stage_detail_approval',
    name: 'detail_approval',
    label: '详情审批',
    status: 'pending',
    progressPercent: 0,
    startedAt: null,
    completedAt: null,
  },
  {
    id: 'stage_final_summary',
    name: 'final_summary',
    label: '最终名单',
    status: 'pending',
    progressPercent: 0,
    startedAt: null,
    completedAt: null,
  },
];

const detailApproval: AgentWorkbenchDetailApproval = {
  id: 'approval_candidate_001',
  candidateId: 'candidate_001',
  status: 'pending',
  requestedAt: '2026-06-13T09:30:00.000Z',
  resolvedAt: null,
  requestReason: '读取完整简历详情以确认最近项目。',
  decisionSummary: null,
};

export const agentWorkbenchFixture: AgentWorkbenchConversationResponse = {
  schemaVersion: 'agent-workbench.v1',
  conversation: {
    id: conversationId,
    title: '上海 AI Agent 平台工程',
    status: 'running',
    access: 'available',
    activeRunId: runId,
    createdAt: '2026-06-13T09:28:00.000Z',
    updatedAt: now,
    archivedAt: null,
    summary: '第一轮检索已完成，正在总结 observation 和下一轮策略。',
  },
  messages: [userMessage, assistantMessage],
  activities: [
    {
      id: 'activity_requirement',
      kind: 'requirement',
      status: 'succeeded',
      title: '需求已确认',
      summary: '目标岗位和硬性条件已进入检索策略。',
      sourceKind: 'all',
      occurredAt: '2026-06-13T09:28:08.000Z',
      relatedRunId: runId,
      relatedCandidateId: null,
    },
    {
      id: 'activity_source_search',
      kind: 'source_search',
      status: 'succeeded',
      title: '来源检索完成',
      summary: '42 个候选人摘要进入初筛。',
      sourceKind: 'all',
      occurredAt: '2026-06-13T09:29:18.000Z',
      relatedRunId: runId,
      relatedCandidateId: null,
    },
  ],
  transcriptGroups: [
    {
      groupId: 'group_run_001',
      conversationId,
      runId,
      title: '已处理 2m',
      status: 'running',
      collapsedByDefault: false,
      startedAt: '2026-06-13T09:28:03.000Z',
      completedAt: null,
      durationLabel: '2m',
      events: [requirementEvent, sourceSearchEvent, scoringEvent, assistantTranscriptEvent],
    },
    {
      groupId: 'group_context_001',
      conversationId,
      runId,
      title: '上下文已压缩',
      status: 'succeeded',
      collapsedByDefault: true,
      startedAt: '2026-06-13T09:30:10.000Z',
      completedAt: '2026-06-13T09:30:10.000Z',
      durationLabel: '',
      events: [contextDividerEvent],
    },
  ],
  requirementDraft: {
    status: 'confirmed',
    targetRole: 'AI Agent 平台工程师',
    location: '上海',
    seniority: '5 年以上',
    mustHaves: [
      { id: 'must_python', label: 'Python 后端', status: 'confirmed', source: 'user' },
      { id: 'must_rag', label: 'RAG 或检索系统经验', status: 'confirmed', source: 'assistant' },
      { id: 'must_agent', label: 'Agent 平台或工具调用经验', status: 'confirmed', source: 'assistant' },
    ],
    preferences: [
      { id: 'pref_startup', label: '创业公司或平台团队背景', status: 'confirmed', source: 'user' },
    ],
    exclusions: [
      { id: 'exclusion_frontend_only', label: '纯前端候选人排除', status: 'confirmed', source: 'assistant' },
    ],
    reviewQuestions: [],
    updatedAt: '2026-06-13T09:28:08.000Z',
  },
  runtime: {
    runId,
    status: 'running',
    currentStage: 'reflection',
    activeRound: 1,
    progressPercent: 68,
    startedAt: '2026-06-13T09:28:03.000Z',
    completedAt: null,
    stages: runtimeStages,
  },
  strategyGraph: {
    signature: 'strategy_graph_round_1',
    layoutStatus: 'ready',
    nodes: [
      {
        id: 'node_requirement',
        kind: 'requirement',
        stage: 'requirement',
        status: 'succeeded',
        title: '需求确认',
        summary: 'AI Agent 平台工程, 上海, 5 年以上。',
        lane: 'left',
        sourceKind: 'all',
        round: 1,
        metrics: [{ label: '硬性条件', value: '3', tone: 'positive' }],
      },
      {
        id: 'node_source_search',
        kind: 'source_search',
        stage: 'source_search',
        status: 'succeeded',
        title: '来源检索',
        summary: '多来源检索安全摘要。',
        lane: 'center',
        sourceKind: 'all',
        round: 1,
        metrics: [{ label: '候选人', value: '42', tone: 'neutral' }],
      },
      {
        id: 'node_reflection',
        kind: 'reflection',
        stage: 'reflection',
        status: 'running',
        title: '反思和下一轮',
        summary: '正在调整关键词和来源优先级。',
        lane: 'right',
        sourceKind: 'all',
        round: 1,
        metrics: [{ label: '强匹配', value: '3', tone: 'positive' }],
      },
    ],
    edges: [
      { id: 'edge_requirement_search', source: 'node_requirement', target: 'node_source_search', label: '生成关键词', status: 'completed' },
      { id: 'edge_search_reflection', source: 'node_source_search', target: 'node_reflection', label: '汇总 observation', status: 'active' },
    ],
  },
  thinkingProcess: {
    selectedRoundId: 'round_1',
    rounds: [
      {
        id: 'round_1',
        round: 1,
        status: 'running',
        title: '第一轮检索',
        keywords: {
          queryTerms: ['AI Agent', 'RAG', 'Python 后端', '工具调用'],
          keywordQuery: 'AI Agent 平台工程 上海 Python RAG',
          executedQueries: ['AI Agent 平台工程 上海', 'RAG 后端 Python 工具调用'],
        },
        observation: {
          searchedCount: 42,
          scoredCount: 12,
          resumeQualityComment: '覆盖面较好，强匹配候选人集中在平台后端和检索工程方向。',
        },
        reflection: {
          summary: '下一轮应增加工作流编排和评测相关关键词。',
          rationale: '现有结果偏 RAG，Agent 工具编排证据仍需加强。',
          suggestedActivateTerms: ['workflow orchestration', 'eval harness'],
          suggestedKeepTerms: ['AI Agent', 'RAG', 'Python'],
          suggestedDeprioritizeTerms: ['prompt engineer'],
          suggestedDropTerms: ['纯前端'],
        },
        startedAt: '2026-06-13T09:28:09.000Z',
        completedAt: null,
      },
    ],
  },
  sourceConnections: [
    {
      id: 'source_cts',
      sourceKind: 'cts',
      displayName: '本地人才库',
      status: 'connected',
      statusSummary: '已连接，安全摘要可用。',
      lastSyncAt: '2026-06-13T09:20:00.000Z',
      evidenceBadges: ['local', 'safe-summary'],
    },
    {
      id: 'source_liepin',
      sourceKind: 'liepin',
      displayName: '猎聘',
      status: 'connected',
      statusSummary: '已连接，详情读取需要审批。',
      lastSyncAt: '2026-06-13T09:21:00.000Z',
      evidenceBadges: ['provider', 'approval-required'],
    },
  ],
  candidates: {
    totalCount: 3,
    selectedCandidateId: 'candidate_001',
    lastUpdatedAt: '2026-06-13T09:29:55.000Z',
    items: [
      {
        id: 'candidate_001',
        displayName: '候选人 A',
        title: '平台后端负责人',
        company: '某 AI Infra 公司',
        location: '上海',
        summary: '有 Agent 工具调用平台和 RAG 检索链路经验。',
        score: 92,
        fitBucket: 'strong_fit',
        status: 'reviewing',
        sourceBadges: ['local', 'liepin'],
        matchedMustHaves: ['Python 后端', 'RAG', 'Agent 平台'],
        risks: ['近期稳定性需要确认'],
        evidence: [
          {
            id: 'evidence_001',
            sourceKind: 'cts',
            label: '项目摘要',
            summary: '主导内部 Agent 平台工具调用和检索链路。',
            confidence: 'high',
          },
        ],
        canRequestDetail: true,
        canOpenProvider: false,
      },
      {
        id: 'candidate_002',
        displayName: '候选人 B',
        title: '高级后端工程师',
        company: '某企业协作产品',
        location: '上海',
        summary: 'RAG 和搜索经验强，Agent 平台经验较少。',
        score: 84,
        fitBucket: 'fit',
        status: 'new',
        sourceBadges: ['local'],
        matchedMustHaves: ['Python 后端', 'RAG'],
        risks: ['Agent 工具调用证据不足'],
        evidence: [],
        canRequestDetail: true,
        canOpenProvider: false,
      },
    ],
  },
  detailApprovals: [detailApproval],
  reviewArtifacts: {
    lastUpdatedAt: '2026-06-13T09:30:00.000Z',
    items: [
      {
        id: 'artifact_requirement',
        kind: 'requirement_review',
        title: '需求确认摘要',
        status: 'ready',
        safeSummary: '岗位、地点、经验和排除条件已确认。',
        artifactRef: 'artifact://requirement/001',
        createdAt: '2026-06-13T09:28:08.000Z',
      },
    ],
  },
  finalSummary: {
    status: 'not_started',
    title: '',
    narrative: '',
    generatedAt: null,
    shortlist: [],
    exportArtifactIds: [],
  },
  pendingActions: [
    {
      id: 'pending_detail_001',
      kind: 'approve_detail',
      label: '审批候选人 A 的完整详情读取',
      priority: 'high',
      status: 'pending',
      createdAt: '2026-06-13T09:30:00.000Z',
      relatedCandidateId: 'candidate_001',
      relatedSourceConnectionId: 'source_liepin',
    },
  ],
  streamCursor: {
    schemaVersion: 'agent-workbench.v1',
    conversationId,
    latestSeq: 128,
    firstAvailableSeq: 1,
    streamUrl: `/api/agent-workbench/conversations/${conversationId}/stream`,
    isConnected: true,
    hasGap: false,
    disconnectedReason: null,
    reconnectAfterMs: null,
  },
};

export const agentWorkbenchStreamFixtures: AgentWorkbenchStreamEnvelope[] = [
  {
    schemaVersion: 'agent-workbench.v1',
    conversationId,
    seq: 126,
    kind: 'sourceSearch.completed',
    payload: sourceSearchEvent,
    createdAt: sourceSearchEvent.completedAt ?? sourceSearchEvent.occurredAt,
  },
  {
    schemaVersion: 'agent-workbench.v1',
    conversationId,
    seq: 127,
    kind: 'thinkingProcess.changed',
    payload: agentWorkbenchFixture.thinkingProcess,
    createdAt: now,
  },
  {
    schemaVersion: 'agent-workbench.v1',
    conversationId,
    seq: 128,
    kind: 'detailApproval.changed',
    payload: detailApproval,
    createdAt: now,
  },
];

export const agentWorkbenchStreamGapFixture: AgentWorkbenchStreamEnvelope = {
  schemaVersion: 'agent-workbench.v1',
  conversationId,
  seq: 140,
  kind: 'stream.gap',
  payload: {
    requestedAfterSeq: 128,
    firstAvailableSeq: 134,
    latestSeq: 140,
    reason: 'retained_ledger_gap',
    recoveryConversationUrl: `/api/agent-workbench/conversations/${conversationId}`,
  },
  createdAt: '2026-06-13T09:31:00.000Z',
};

export const agentWorkbenchTranscriptToolFixture = sourceSearchEvent;
export const agentWorkbenchTranscriptFailedToolFixture: AgentWorkbenchTranscriptEvent = {
  ...sourceSearchEvent,
  eventId: 'evt_source_search_failed',
  kind: 'sourceSearch.failed',
  status: 'failed',
  title: '来源检索失败',
  completedAt: '2026-06-13T09:29:18.000Z',
  payload: {
    ...sourceSearchToolPayload,
    errorMessage: 'Source token expired before safe summary retrieval.',
    outputPreview: 'No raw provider data was exposed.',
    detailLines: [
      ...sourceSearchToolPayload.detailLines,
      {
        id: 'detail_source_error',
        kind: 'error',
        label: 'Error',
        text: 'source_connection_expired',
        timestamp: '2026-06-13T09:29:18.000Z',
      },
    ],
  },
};

export const agentWorkbenchTranscriptCollapsedFixture: AgentWorkbenchTranscriptGroup[] =
  agentWorkbenchFixture.transcriptGroups.map((group) => ({
    ...group,
    collapsedByDefault: true,
  }));
