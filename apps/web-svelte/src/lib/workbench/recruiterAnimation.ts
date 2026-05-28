import type { components } from '../api/schema';

type WorkbenchSourceCard = components['schemas']['WorkbenchSourceCardResponse'];
type WorkbenchRuntimeGraphNode = components['schemas']['WorkbenchRuntimeGraphNodeResponse'];

export type SourceKind = WorkbenchSourceCard['sourceKind'];
export type WorkbenchCandidateEvidenceLevel =
	components['schemas']['WorkbenchCandidateEvidenceResponse']['evidenceLevel'];

export type RecruiterTone = 'blue' | 'teal' | 'violet' | 'amber' | 'green' | 'neutral' | 'rose';
export type RecruiterLane = 'shared' | SourceKind;

export type RecruiterCandidateEvidenceRef = {
	evidenceId: string;
	reviewItemId: string;
	sourceRunId: string;
	sourceKind: SourceKind;
	evidenceLevel: WorkbenchCandidateEvidenceLevel;
};

export type RecruiterGraphDetailPayload = {
	kind: 'runtimeGraphNode';
	node: WorkbenchRuntimeGraphNode;
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
	noteKind?: string | undefined;
	statusHint?: string | undefined;
};
