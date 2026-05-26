export const workbenchKeys = {
	me: ['auth', 'me'] as const,
	devModeStatus: ['workbench', 'dev-mode-status'] as const,
	settings: () => ['workbench', 'settings'] as const,
	sourceConnections: ['workbench', 'source-connections'] as const,
	sourceConnectionRoot: () => ['workbench', 'source-connections'] as const,
	sourceConnection: (connectionId: string) =>
		['workbench', 'source-connections', connectionId] as const,
	detailOpenRequests: (sessionId?: string) =>
		sessionId
			? (['workbench', 'detail-open-requests', sessionId] as const)
			: (['workbench', 'detail-open-requests'] as const),
	sessions: ['workbench', 'sessions'] as const,
	session: (sessionId: string) => ['workbench', 'sessions', sessionId] as const,
	runtimeGraph: (sessionId: string) => ['workbench', 'sessions', sessionId, 'runtime-graph'] as const,
	candidates: (sessionId: string) => ['workbench', 'sessions', sessionId, 'candidates'] as const,
	finalTop10: (sessionId: string) => ['workbench', 'sessions', sessionId, 'final-top10'] as const,
	liepinPolicy: (sessionId: string) =>
		['workbench', 'sessions', sessionId, 'liepin-policy'] as const,
	sourcePolicy: (sessionId: string) =>
		['workbench', 'sessions', sessionId, 'source-policy'] as const,
	globalEvents: (afterSeq = 0) => ['workbench', 'events', afterSeq] as const,
	sessionEvents: (sessionId: string, afterSeq = 0) =>
		['workbench', 'sessions', sessionId, 'events', afterSeq] as const,
	graphCandidatesRoot: (sessionId: string) =>
		['workbench', 'sessions', sessionId, 'graph-candidates'] as const,
	graphCandidates: (sessionId: string, nodeId: string) =>
		['workbench', 'sessions', sessionId, 'graph-candidates', nodeId] as const,
	resumeSnapshotRoot: (sessionId: string) =>
		['workbench', 'sessions', sessionId, 'resume-snapshot'] as const,
	resumeSnapshot: (sessionId: string, graphCandidateId: string) =>
		['workbench', 'sessions', sessionId, 'resume-snapshot', graphCandidateId] as const
};
