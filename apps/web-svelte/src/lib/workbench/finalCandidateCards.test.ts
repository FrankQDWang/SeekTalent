import { describe, expect, it } from 'vitest';
import { buildFinalCandidateCards } from './finalCandidateCards';
import type {
	WorkbenchCandidateReviewItem,
	WorkbenchFinalTopCandidate,
	WorkbenchFinalTopCandidateListResponse
} from './types';

type CandidateEvidence = WorkbenchCandidateReviewItem['evidence'][number];
type ReviewItemOverrides = Partial<Omit<WorkbenchCandidateReviewItem, 'evidence'>> & {
	evidence?: Array<Pick<CandidateEvidence, 'sourceKind' | 'evidenceLevel'>>;
};

const baseFinalTop = {
	reviewItemId: 'review-cts',
	runtimeIdentityId: 'identity-1',
	canonicalReviewItemId: 'review-cts',
	mergedReviewItemIds: ['review-cts', 'review-liepin-card'],
	rank: 1,
	displayName: 'Lin Qian',
	title: 'VP Product',
	company: 'SearchCo',
	location: 'Shanghai',
	summary: 'Canonical safe summary.',
	aggregateScore: 94,
	fitBucket: 'fit',
	sourceBadges: ['CTS final', 'Liepin card', 'Multiple sources'],
	evidenceLevel: 'final',
	sourceEvidence: [
		{
			evidenceId: 'e-cts',
			sourceRunId: 'run-cts',
			sourceKind: 'cts',
			evidenceLevel: 'final',
			score: 94,
			fitBucket: 'fit'
		},
		{
			evidenceId: 'e-liepin',
			sourceRunId: 'run-liepin',
			sourceKind: 'liepin',
			evidenceLevel: 'card',
			score: 92,
			fitBucket: 'fit'
		}
	]
} satisfies WorkbenchFinalTopCandidate;

const baseReviewItem = {
	sessionId: 'session-1',
	graphCandidateId: null,
	canExpandResume: false,
	status: 'new',
	note: '',
	displayName: 'Lin Qian',
	title: 'VP Product',
	company: 'SearchCo',
	location: 'Shanghai',
	summary: 'Review item summary.',
	aggregateScore: 90,
	fitBucket: 'fit',
	sourceBadges: [],
	evidenceLevel: 'card',
	matchedMustHaves: ['AI workflow'],
	matchedPreferences: [],
	missingRisks: [],
	strengths: ['Built recruiting workflow products.'],
	weaknesses: [],
	evidence: [],
	createdAt: '2026-05-18T00:00:00Z',
	updatedAt: '2026-05-18T00:00:00Z'
} satisfies Omit<WorkbenchCandidateReviewItem, 'reviewItemId'>;

describe('buildFinalCandidateCards', () => {
	it('keeps ranking/display fields from final-top10 and joins review items only for actions', () => {
		const cards = buildFinalCandidateCards({
			finalTop: finalTopList([baseFinalTop]),
			reviewItems: [
				reviewItem('review-cts', {
					sourceBadges: ['CTS final'],
					evidenceLevel: 'final',
					status: 'promising',
					note: 'Canonical note',
					graphCandidateId: 'graph-cts',
					canExpandResume: true,
					evidence: [{ sourceKind: 'cts', evidenceLevel: 'final' }]
				}),
				reviewItem('review-liepin-card', {
					sourceBadges: ['Liepin card'],
					evidenceLevel: 'card',
					status: 'new',
					note: 'Liepin card note',
					graphCandidateId: 'graph-liepin',
					canExpandResume: true,
					evidence: [{ sourceKind: 'liepin', evidenceLevel: 'card' }]
				})
			]
		});

		expect(cards).toHaveLength(1);
		const card = expectSingle(cards);
		expect(card).toMatchObject({
			rank: 1,
			displayName: 'Lin Qian',
			runtimeIdentityId: 'identity-1',
			canonicalReviewItemId: 'review-cts',
			actionReviewItemId: 'review-cts',
			detailActionReviewItemId: 'review-liepin-card',
			providerActionReviewItemId: null,
			canOpenProviderAction: false,
			status: 'promising',
			note: 'Canonical note',
			resumeGraphCandidateId: 'graph-cts'
		});
		expect(card.sourceBadges).toEqual(['CTS final', 'Liepin card', 'Multiple sources']);
		expect(card.mergedStateHint).toBe('合并记录存在不同状态和备注。');
		expect(card.mergeExplanation).toContain('2 条来源记录已合并');
	});

	it('does not expose detail request action when no joined Liepin card lacks detail evidence', () => {
		const cards = buildFinalCandidateCards({
			finalTop: finalTopList([
				{
					...baseFinalTop,
					mergedReviewItemIds: ['review-cts', 'review-liepin-card'],
					sourceBadges: ['CTS final', 'Liepin detail', 'Multiple sources'],
					evidenceLevel: 'detail',
					sourceEvidence: [
						...baseFinalTop.sourceEvidence.slice(0, 1),
						{
							evidenceId: 'e-liepin-detail',
							sourceRunId: 'run-liepin',
							sourceKind: 'liepin',
							evidenceLevel: 'detail',
							score: 95,
							fitBucket: 'fit'
						}
					]
				}
			]),
			reviewItems: [
				reviewItem('review-cts', { sourceBadges: ['CTS final'], evidenceLevel: 'final' }),
				reviewItem('review-liepin-card', {
					sourceBadges: ['Liepin detail'],
					evidenceLevel: 'detail',
					evidence: [{ sourceKind: 'liepin', evidenceLevel: 'detail' }]
				})
			]
		});

		const card = expectSingle(cards);
		expect(card.detailActionReviewItemId).toBeNull();
		expect(card.providerActionReviewItemId).toBe('review-liepin-card');
		expect(card.canRequestLiepinDetail).toBe(false);
		expect(card.canOpenProviderAction).toBe(true);
	});

	it('uses the merged Liepin detail review item for provider open when canonical is CTS', () => {
		const cards = buildFinalCandidateCards({
			finalTop: finalTopList([
				{
					...baseFinalTop,
					mergedReviewItemIds: ['review-cts', 'review-liepin-detail'],
					sourceBadges: ['CTS final', 'Liepin detail', 'Multiple sources'],
					evidenceLevel: 'detail',
					sourceEvidence: [
						...baseFinalTop.sourceEvidence.slice(0, 1),
						{
							evidenceId: 'e-liepin-detail',
							sourceRunId: 'run-liepin',
							sourceKind: 'liepin',
							evidenceLevel: 'detail',
							score: 95,
							fitBucket: 'fit'
						}
					]
				}
			]),
			reviewItems: [
				reviewItem('review-cts', {
					sourceBadges: ['CTS final'],
					evidenceLevel: 'final',
					evidence: [{ sourceKind: 'cts', evidenceLevel: 'final' }]
				}),
				reviewItem('review-liepin-detail', {
					sourceBadges: ['Liepin detail'],
					evidenceLevel: 'detail',
					evidence: [{ sourceKind: 'liepin', evidenceLevel: 'detail' }]
				})
			]
		});

		const card = expectSingle(cards);
		expect(card.actionReviewItemId).toBe('review-cts');
		expect(card.providerActionReviewItemId).toBe('review-liepin-detail');
		expect(card.canOpenProviderAction).toBe(true);
	});

	it('does not copy raw provider payload, cookies, tokens, or protected paths into card text', () => {
		const cards = buildFinalCandidateCards({
			finalTop: finalTopList([{ ...baseFinalTop, summary: 'Safe summary without internals.' }]),
			reviewItems: [
				reviewItem('review-cts', {
					note: 'Cookie: secret-token /tmp/protected/provider.json'
				})
			]
		});

		const serialized = JSON.stringify(cards);
		expect(serialized).not.toMatch(/secret-token|\/tmp\/protected|provider\.json/i);
	});
});

function finalTopList(items: WorkbenchFinalTopCandidate[]): WorkbenchFinalTopCandidateListResponse {
	return { items, coverageStatus: 'complete', finalizationRevision: 1 };
}

function reviewItem(
	reviewItemId: string,
	overrides: ReviewItemOverrides = {}
): WorkbenchCandidateReviewItem {
	return {
		...baseReviewItem,
		reviewItemId,
		...overrides,
		evidence: (overrides.evidence ?? []).map((evidence, index) => ({
			evidenceId: `evidence-${reviewItemId}-${String(index)}`,
			sourceRunId: `run-${evidence.sourceKind ?? 'cts'}`,
			sourceKind: evidence.sourceKind ?? 'cts',
			evidenceLevel: evidence.evidenceLevel ?? 'card',
			score: null,
			fitBucket: null,
			matchedMustHaves: [],
			matchedPreferences: [],
			missingRisks: [],
			strengths: [],
			weaknesses: [],
			createdAt: '2026-05-18T00:00:00Z'
		}))
	};
}

function expectSingle<T>(items: T[]): T {
	const item = items[0];
	expect(item).toBeDefined();
	if (!item) {
		throw new Error('Expected one item');
	}
	return item;
}
