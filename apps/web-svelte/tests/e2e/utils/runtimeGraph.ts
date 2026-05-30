type RuntimeGraphSourceKind = 'cts' | 'liepin' | 'all';
type RuntimeGraphScopeKind = 'none' | 'round_recall' | 'final';

type RuntimeGraphNodeOptions = {
	summaryText?: string;
	stage?: string;
	lane?: string;
	roundNo?: number;
	scopeKind?: RuntimeGraphScopeKind;
	eventIds?: string[];
	detailSections?: unknown[];
};

export function runtimeGraphNode(
	nodeId: string,
	kind: string,
	label: string,
	status: string,
	sourceKind: RuntimeGraphSourceKind,
	options: RuntimeGraphNodeOptions = {}
) {
	const roundNo = options.roundNo ?? defaultRoundNo(kind);
	const scopeKind = options.scopeKind ?? defaultScopeKind(kind);
	return {
		nodeId,
		kind,
		label,
		summaryText: options.summaryText ?? label,
		status,
		stage: options.stage ?? defaultStage(kind),
		sourceKind,
		lane: options.lane ?? (sourceKind === 'all' ? 'shared' : sourceKind),
		roundNo,
		candidateScope: {
			scopeKind,
			sourceKind,
			roundNo: scopeKind === 'none' ? null : roundNo,
			reason: null
		},
		eventIds: options.eventIds ?? [],
		detailSections: options.detailSections ?? []
	};
}

function defaultStage(kind: string) {
	if (kind === 'final') return 'finalization';
	if (kind === 'job') return 'intake';
	return 'retrieval';
}

function defaultRoundNo(kind: string) {
	if (kind === 'job') return 0;
	if (kind === 'final') return 2;
	return 1;
}

function defaultScopeKind(kind: string): RuntimeGraphScopeKind {
	if (kind === 'final') return 'final';
	if (kind === 'job') return 'none';
	return 'round_recall';
}
