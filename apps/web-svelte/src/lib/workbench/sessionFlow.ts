type SourceRunLike = {
	status?: string | null;
};

const startableStatuses = new Set(['queued', 'blocked']);

export function hasStartableSourceRun(sourceRuns: SourceRunLike[]): boolean {
	return sourceRuns.some((sourceRun) => startableStatuses.has(String(sourceRun.status ?? '')));
}
