import type {
	WorkbenchCandidateReviewItem,
	WorkbenchFinalTopCandidate,
	WorkbenchFinalTopCandidateListResponse
} from './types';

export type FinalCandidateViewModel = {
	reviewItemId: string;
	runtimeIdentityId: string;
	canonicalReviewItemId: string;
	mergedReviewItemIds: string[];
	rank: number;
	displayName: string;
	title: string;
	company: string;
	location: string;
	summary: string;
	aggregateScore: number | null;
	fitBucket: string | null;
	sourceBadges: string[];
	evidenceLevel: WorkbenchFinalTopCandidate['evidenceLevel'];
	sourceEvidence: WorkbenchFinalTopCandidate['sourceEvidence'];
	actionReviewItemId: string | null;
	detailActionReviewItemId: string | null;
	providerActionReviewItemId: string | null;
	canRequestLiepinDetail: boolean;
	canOpenProviderAction: boolean;
	status: WorkbenchCandidateReviewItem['status'] | null;
	note: string;
	mergedStateHint: string | null;
	resumeGraphCandidateId: string | null;
	canExpandResume: boolean;
	matchedMustHaves: string[];
	matchedPreferences: string[];
	missingRisks: string[];
	strengths: string[];
	coverageExplanation: string;
	mergeExplanation: string | null;
	canonicalResumeHint: string | null;
};

export type BuildFinalCandidateCardsInput = {
	finalTop: WorkbenchFinalTopCandidateListResponse;
	reviewItems: WorkbenchCandidateReviewItem[];
};

export function buildFinalCandidateCards({
	finalTop,
	reviewItems
}: BuildFinalCandidateCardsInput): FinalCandidateViewModel[] {
	const reviewItemById = new Map(reviewItems.map((item) => [item.reviewItemId, item]));

	return finalTop.items.map((candidate) => {
		const joinedItems = candidate.mergedReviewItemIds
			.map((reviewItemId) => reviewItemById.get(reviewItemId))
			.filter((item): item is WorkbenchCandidateReviewItem => Boolean(item));
		const canonicalItem = reviewItemById.get(candidate.canonicalReviewItemId) ?? null;
		const actionItem =
			canonicalItem ?? firstJoinedItem(candidate.mergedReviewItemIds, reviewItemById);
		const detailActionItem = joinedItems.find(hasLiepinCardWithoutDetail) ?? null;
		const resumeItem =
			actionItem?.canExpandResume && actionItem.graphCandidateId ? actionItem : null;
		const providerActionItem = joinedItems.find(hasLiepinDetailEvidence) ?? null;

		return {
			reviewItemId: candidate.reviewItemId,
			runtimeIdentityId: candidate.runtimeIdentityId,
			canonicalReviewItemId: candidate.canonicalReviewItemId,
			mergedReviewItemIds: candidate.mergedReviewItemIds,
			rank: candidate.rank,
			displayName: candidate.displayName,
			title: candidate.title,
			company: candidate.company,
			location: candidate.location,
			summary: candidate.summary,
			aggregateScore: candidate.aggregateScore ?? null,
			fitBucket: candidate.fitBucket ?? null,
			sourceBadges: candidate.sourceBadges,
			evidenceLevel: candidate.evidenceLevel,
			sourceEvidence: candidate.sourceEvidence,
			actionReviewItemId: actionItem?.reviewItemId ?? null,
			detailActionReviewItemId: detailActionItem?.reviewItemId ?? null,
			providerActionReviewItemId: providerActionItem?.reviewItemId ?? null,
			canRequestLiepinDetail: Boolean(detailActionItem),
			canOpenProviderAction: Boolean(providerActionItem),
			status: canonicalItem?.status ?? null,
			note: safeUserNote(canonicalItem?.note ?? ''),
			mergedStateHint: mergedStateHint(joinedItems, canonicalItem),
			resumeGraphCandidateId: resumeItem?.graphCandidateId ?? null,
			canExpandResume: Boolean(resumeItem),
			matchedMustHaves: canonicalItem?.matchedMustHaves ?? [],
			matchedPreferences: canonicalItem?.matchedPreferences ?? [],
			missingRisks: canonicalItem?.missingRisks ?? [],
			strengths: canonicalItem?.strengths ?? [],
			coverageExplanation: coverageExplanation(finalTop.coverageStatus, candidate),
			mergeExplanation: mergeExplanation(candidate),
			canonicalResumeHint: canonicalResumeHint(candidate)
		};
	});
}

function firstJoinedItem(
	reviewItemIds: string[],
	reviewItemById: ReadonlyMap<string, WorkbenchCandidateReviewItem>
) {
	for (const reviewItemId of reviewItemIds) {
		const item = reviewItemById.get(reviewItemId);
		if (item) {
			return item;
		}
	}
	return null;
}

function hasLiepinCardWithoutDetail(item: WorkbenchCandidateReviewItem) {
	return hasLiepinEvidence(item) && !hasLiepinDetailEvidence(item);
}

function hasLiepinEvidence(item: WorkbenchCandidateReviewItem) {
	return (
		item.sourceBadges.some((badge) => /liepin/i.test(badge)) ||
		item.evidence.some((evidence) => evidence.sourceKind === 'liepin')
	);
}

function hasLiepinDetailEvidence(item: WorkbenchCandidateReviewItem) {
	return (
		item.evidenceLevel === 'detail' ||
		item.sourceBadges.some((badge) => /liepin detail/i.test(badge)) ||
		item.evidence.some(
			(evidence) => evidence.sourceKind === 'liepin' && evidence.evidenceLevel === 'detail'
		)
	);
}

function mergedStateHint(
	joinedItems: WorkbenchCandidateReviewItem[],
	canonicalItem: WorkbenchCandidateReviewItem | null
) {
	if (!canonicalItem || joinedItems.length < 2) {
		return null;
	}
	const statuses = new Set(joinedItems.map((item) => item.status));
	const notes = new Set(joinedItems.map((item) => safeUserNote(item.note)).filter(Boolean));
	if (statuses.size > 1 && notes.size > 1) {
		return 'Merged review items have different status and notes.';
	}
	if (statuses.size > 1) {
		return 'Merged review items have different statuses.';
	}
	if (notes.size > 1) {
		return 'Merged review items have different notes.';
	}
	return null;
}

function safeUserNote(note: string) {
	if (containsSensitiveText(note)) {
		return '';
	}
	return note;
}

function containsSensitiveText(value: string) {
	return /(cookie|token|auth header|authorization|storage state|provider\.json|artifact|\/tmp\/|\/var\/|\/Users\/|protected)/i.test(
		value
	);
}

function coverageExplanation(
	coverageStatus: WorkbenchFinalTopCandidateListResponse['coverageStatus'],
	candidate: WorkbenchFinalTopCandidate
) {
	const hasCts = hasSource(candidate, 'cts');
	const hasLiepin = hasSource(candidate, 'liepin');
	const hasLiepinCard = candidate.sourceEvidence.some(
		(evidence) => evidence.sourceKind === 'liepin' && evidence.evidenceLevel === 'card'
	);
	const hasLiepinDetail = candidate.sourceEvidence.some(
		(evidence) => evidence.sourceKind === 'liepin' && evidence.evidenceLevel === 'detail'
	);

	if (hasCts && hasLiepinDetail) {
		return 'Dual-source complete with Liepin detail evidence.';
	}
	if (hasCts && hasLiepinCard) {
		return 'Liepin partial: card evidence preserved, detail remains gated.';
	}
	if (hasCts && !hasLiepin && coverageStatus === 'degraded') {
		return 'CTS-only shortlist because Liepin is blocked or degraded.';
	}
	if (hasCts && hasLiepin) {
		return 'Dual-source evidence merged for this identity.';
	}
	return 'Final Top 10 identity-level ranking.';
}

function mergeExplanation(candidate: WorkbenchFinalTopCandidate) {
	if (candidate.mergedReviewItemIds.length <= 1) {
		return null;
	}
	return `${String(candidate.mergedReviewItemIds.length)} source rows merged into one identity.`;
}

function canonicalResumeHint(candidate: WorkbenchFinalTopCandidate) {
	if (candidate.mergedReviewItemIds.length <= 1) {
		return null;
	}
	return `Canonical resume: ${candidate.canonicalReviewItemId}.`;
}

function hasSource(candidate: WorkbenchFinalTopCandidate, sourceKind: 'cts' | 'liepin') {
	return candidate.sourceEvidence.some((evidence) => evidence.sourceKind === sourceKind);
}
