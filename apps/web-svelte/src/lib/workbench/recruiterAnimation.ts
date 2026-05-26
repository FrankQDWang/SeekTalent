import type { components } from '../api/schema';

type WorkbenchSourceCard = components['schemas']['WorkbenchSourceCardResponse'];
type WorkbenchRuntimeSourceLaneState =
	components['schemas']['WorkbenchRuntimeSourceLaneStateResponse'];
type WorkbenchRuntimeSourceState = components['schemas']['WorkbenchRuntimeSourceStateResponse'];
type WorkbenchRuntimeGraphNode = components['schemas']['WorkbenchRuntimeGraphNodeResponse'];

export type SourceKind = WorkbenchSourceCard['sourceKind'];
export type WorkbenchAuthState = WorkbenchSourceCard['authState'];
export type WorkbenchCandidateEvidenceLevel =
	components['schemas']['WorkbenchCandidateEvidenceResponse']['evidenceLevel'];
export type RequirementSheet = components['schemas']['RequirementSheet-Output'];
export type WorkbenchSourceConnectionStatus = NonNullable<WorkbenchSourceCard['connectionStatus']>;
export type WorkbenchSourceStatus = WorkbenchSourceCard['status'];

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
			kind: 'runtimeGraphNode';
			node: WorkbenchRuntimeGraphNode;
	  }
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
			reviewStatus: 'confirmed' | 'draft' | 'runtime';
			requirementSheet: RequirementSheet | null;
			runtimeRequirementSheet: RequirementSheet | null;
			approvedAt: string | null;
	  }
	| {
			kind: 'sourceQueue';
			sourceKind: SourceKind;
			sourceRunId: string | null;
			status: WorkbenchSourceStatus | null;
			authState: WorkbenchAuthState | null;
			connectionStatus?: WorkbenchSourceConnectionStatus | null | undefined;
			cardsScannedCount: number;
			uniqueCandidatesCount: number;
			detailOpenUsedCount: number;
			detailOpenBlockedCount: number;
			warningCode: string | null;
			warningMessage: string | null;
			runtimeStatus?: WorkbenchRuntimeSourceLaneState['status'] | null | undefined;
			runtimeEventType?: string | null | undefined;
			runtimeEventSeq?: number | null | undefined;
			runtimeCardsSeenCount?: number | undefined;
			runtimeCardsFilteredCount?: number | undefined;
			runtimeCandidatesCount?: number | undefined;
			runtimeDetailRecommendationsCount?: number | undefined;
			runtimeDetailState?: WorkbenchRuntimeSourceLaneState['detailState'] | null | undefined;
	  }
	| {
			kind: 'ctsRoundQuery';
			roundNo: number;
			queryTerms: string[];
			queryLabel: string;
			executedQueries?:
				| {
						query_role: string | null;
						lane_type: string | null;
						query_terms: string[];
						keyword_query: string | null;
						query_instance_id: string | null;
						query_fingerprint: string | null;
				  }[]
				| undefined;
	  }
	| {
			kind: 'ctsRoundResults';
			roundNo: number;
			rawCandidateCount: number;
			uniqueNewCount: number;
			recallCounts?: Record<string, unknown> | null | undefined;
	  }
	| {
			kind: 'ctsRoundScoring';
			roundNo: number;
			scoredCount?: number | undefined;
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
			coverageStatus?: WorkbenchRuntimeSourceState['coverageStatus'] | null | undefined;
			finalizationRevision?: number | null | undefined;
			finalizationReasonCode?: string | null | undefined;
			identityMergeCount?: number | undefined;
			ambiguousDuplicateCount?: number | undefined;
			canonicalResumeSelectedCount?: number | undefined;
			sourceStates?: WorkbenchRuntimeSourceLaneState[] | undefined;
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
	sourceKind?: SourceKind | 'all' | undefined;
	sourceLabel?: string | undefined;
	lane?: RecruiterLane | undefined;
	detailKind?: RecruiterGraphDetailKind | undefined;
	detailPayload?: RecruiterGraphDetailPayload | undefined;
	eventIds?: string[] | undefined;
	sourceRunId?: string | null | undefined;
	candidateReviewItemIds?: string[] | undefined;
	candidateEvidenceRefs?: RecruiterCandidateEvidenceRef[] | undefined;
	detailOpenRequestIds?: string[] | undefined;
};

export type RecruiterGraphEdge = {
	from: string;
	to: string;
	tone: RecruiterTone;
	label?: string | undefined;
};

export type RecruiterLogEntry = {
	id: string;
	at: number;
	tag: 'SYS' | 'THINK' | 'PLAN' | 'SCAN' | 'HIT' | 'REFLECT' | 'DETAIL';
	text: string;
	sourceKind?: SourceKind | 'all' | undefined;
	sourceLabel?: string | undefined;
	lane?: RecruiterLane | undefined;
	relatedNodeId?: string | undefined;
};
