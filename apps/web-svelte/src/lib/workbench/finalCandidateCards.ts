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
	whySelected: string;
	sourceRound: number | null;
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
	weaknesses: string[];
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
			whySelected: candidate.whySelected,
			sourceRound: candidate.sourceRound ?? null,
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
			matchedMustHaves: candidate.matchedMustHaves,
			matchedPreferences: candidate.matchedPreferences,
			missingRisks: candidate.riskFlags,
			strengths: candidate.strengths,
			weaknesses: candidate.weaknesses,
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
		return '合并记录存在不同状态和备注。';
	}
	if (statuses.size > 1) {
		return '合并记录存在不同状态。';
	}
	if (notes.size > 1) {
		return '合并记录存在不同备注。';
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
		return 'CTS 与猎聘详情证据已合并。';
	}
	if (hasCts && hasLiepinCard) {
		return '猎聘卡片证据已保留，详情仍需审批。';
	}
	if (hasCts && !hasLiepin && coverageStatus === 'degraded') {
		return '猎聘受阻或降级，本候选人仅来自 CTS。';
	}
	if (hasCts && hasLiepin) {
		return '本身份已合并多源证据。';
	}
	return '最终 Top 10 身份级排序。';
}

function mergeExplanation(candidate: WorkbenchFinalTopCandidate) {
	if (candidate.mergedReviewItemIds.length <= 1) {
		return null;
	}
	return `${String(candidate.mergedReviewItemIds.length)} 条来源记录已合并为同一身份。`;
}

function canonicalResumeHint(candidate: WorkbenchFinalTopCandidate) {
	if (candidate.mergedReviewItemIds.length <= 1) {
		return null;
	}
	return '已选择一份规范简历作为展示来源。';
}

function hasSource(candidate: WorkbenchFinalTopCandidate, sourceKind: 'cts' | 'liepin') {
	return candidate.sourceEvidence.some((evidence) => evidence.sourceKind === sourceKind);
}
