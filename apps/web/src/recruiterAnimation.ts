import type {
  SourceKind,
  WorkbenchAuthState,
  WorkbenchCandidateEvidenceLevel,
  WorkbenchRequirementTriageInput,
  WorkbenchSourceConnectionStatus,
  WorkbenchSourceStatus,
} from './types';

export type RecruiterTone = 'blue' | 'teal' | 'violet' | 'amber' | 'green' | 'neutral' | 'rose';
export type RecruiterLane = 'shared' | SourceKind;
export type RecruiterGraphDetailKind =
  | 'job'
  | 'requirements'
  | 'sourceQueue'
  | 'ctsRoundQuery'
  | 'ctsRoundResults'
  | 'ctsRoundScoring'
  | 'reflection'
  | 'liepinCardSearch'
  | 'liepinCardCandidates'
  | 'liepinDetailApproval'
  | 'aggregation';

export type RecruiterCandidateEvidenceRef = {
  evidenceId: string;
  reviewItemId: string;
  sourceRunId: string;
  sourceKind: SourceKind;
  evidenceLevel: WorkbenchCandidateEvidenceLevel;
};

type RecruiterDetailRequestFields = {
  detailOpenRequestIds: string[];
  requestIds: string[];
  requestSummaries: string[];
  budgetText: string | null;
};

export type RecruiterGraphDetailPayload =
  | {
      kind: 'job';
      sessionId: string;
      jobTitle: string;
      jdText: string;
      notes: string;
      sourceKinds: SourceKind[];
    }
  | {
      kind: 'requirements';
      triageStatus: 'confirmed' | 'draft' | 'runtime';
      criteria: WorkbenchRequirementTriageInput;
      runtimeCriteria: WorkbenchRequirementTriageInput;
      approvedAt: string | null;
    }
  | {
      kind: 'sourceQueue';
      sourceKind: SourceKind;
      sourceRunId: string | null;
      status: WorkbenchSourceStatus | null;
      authState: WorkbenchAuthState | null;
      connectionStatus?: WorkbenchSourceConnectionStatus | null;
      cardsScannedCount: number;
      uniqueCandidatesCount: number;
      detailOpenUsedCount: number;
      detailOpenBlockedCount: number;
      warningCode: string | null;
      warningMessage: string | null;
    }
  | {
      kind: 'ctsRoundQuery';
      roundNo: number;
      queryTerms: string[];
      queryLabel: string;
      executedQueries?: {
        query_role: string | null;
        lane_type: string | null;
        query_terms: string[];
        keyword_query: string | null;
        query_instance_id: string | null;
        query_fingerprint: string | null;
      }[];
    }
  | {
      kind: 'ctsRoundResults';
      roundNo: number;
      rawCandidateCount: number;
      uniqueNewCount: number;
      recallCounts?: Record<string, unknown> | null;
    }
  | {
      kind: 'ctsRoundScoring';
      roundNo: number;
      scoredCount?: number;
      newlyScoredCount: number;
      fitCount: number;
      notFitCount: number;
    }
  | {
      kind: 'reflection';
      roundNo: number;
      summary: string;
      rationale: string;
      nextDirection: string;
    }
  | ({
      kind: 'liepinCardSearch';
      cardsScannedCount: number;
      uniqueCandidatesCount: number;
    } & RecruiterDetailRequestFields)
  | ({
      kind: 'liepinCardCandidates';
      candidateReviewItemIds: string[];
      candidateEvidenceRefs: RecruiterCandidateEvidenceRef[];
      bestScore: number | null;
    } & RecruiterDetailRequestFields)
  | ({
      kind: 'liepinDetailApproval';
    } & RecruiterDetailRequestFields)
  | {
      kind: 'aggregation';
      candidateCount: number;
      bestScore: number | null;
      finalReport: string | null;
      stopReason: string | null;
    };

export type RecruiterGraphNode = {
  id: string;
  at: number;
  kind: '岗位' | '拆解' | '检索' | '命中' | '评分' | '反思' | '详情审批' | '排序';
  label: string;
  detail: string;
  x: number;
  y: number;
  tone: RecruiterTone;
  sourceKind?: SourceKind | 'all';
  sourceLabel?: string;
  lane?: RecruiterLane;
  detailKind?: RecruiterGraphDetailKind;
  detailPayload?: RecruiterGraphDetailPayload;
  eventIds?: string[];
  sourceRunId?: string | null;
  candidateReviewItemIds?: string[];
  candidateEvidenceRefs?: RecruiterCandidateEvidenceRef[];
  detailOpenRequestIds?: string[];
};

export type RecruiterGraphEdge = {
  from: string;
  to: string;
  tone: RecruiterTone;
  label?: string;
};

export type RecruiterLogEntry = {
  id: string;
  at: number;
  tag: 'SYS' | 'THINK' | 'PLAN' | 'SCAN' | 'HIT' | 'REFLECT' | 'DETAIL';
  text: string;
  sourceKind?: SourceKind | 'all';
  sourceLabel?: string;
  lane?: RecruiterLane;
  relatedNodeId?: string;
};
