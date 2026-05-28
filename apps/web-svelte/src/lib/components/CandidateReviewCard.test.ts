import { render, screen } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import type { FinalCandidateViewModel } from '$lib/workbench/finalCandidateCards';
import CandidateReviewCardHarness from './CandidateReviewCard.test.svelte';

const card = {
	reviewItemId: 'review-final',
	runtimeIdentityId: 'identity-1',
	canonicalReviewItemId: 'review-final',
	mergedReviewItemIds: ['review-final'],
	rank: 1,
	displayName: 'Lin Qian',
	title: 'Senior Backend Engineer',
	company: 'SearchCo',
	location: 'Shanghai',
	summary: 'Runtime match summary.',
	aggregateScore: 94,
	fitBucket: 'fit',
	whySelected: 'Runtime selected this candidate for agent workflow depth.',
	sourceRound: 2,
	sourceBadges: ['CTS final', 'Liepin detail'],
	evidenceLevel: 'detail',
	sourceEvidence: [],
	actionReviewItemId: 'review-final',
	detailActionReviewItemId: null,
	providerActionReviewItemId: null,
	canRequestLiepinDetail: false,
	canOpenProviderAction: false,
	status: 'promising',
	note: '',
	mergedStateHint: null,
	resumeGraphCandidateId: null,
	canExpandResume: false,
	matchedMustHaves: ['Python backend', 'distributed systems'],
	matchedPreferences: ['agent tooling', 'recruiter workflow'],
	missingRisks: ['management scope unclear'],
	strengths: ['Strong backend systems'],
	weaknesses: ['Needs leadership calibration'],
	coverageExplanation: 'CTS and Liepin detail evidence are both available.',
	mergeExplanation: null,
	canonicalResumeHint: null
} satisfies FinalCandidateViewModel;

describe('CandidateReviewCard', () => {
	it('visibly renders runtime final-top10 business fields', () => {
		render(CandidateReviewCardHarness, { props: { sessionId: 'session-1', card } });

		expect(screen.getByText('选择理由')).toBeInTheDocument();
		expect(
			screen.getByText('Runtime selected this candidate for agent workflow depth.')
		).toBeInTheDocument();
		expect(screen.getByText('硬性匹配')).toBeInTheDocument();
		expect(screen.getByText('Python backend / distributed systems')).toBeInTheDocument();
		expect(screen.getByText('偏好匹配')).toBeInTheDocument();
		expect(screen.getByText('agent tooling / recruiter workflow')).toBeInTheDocument();
		expect(screen.getByText('优势')).toBeInTheDocument();
		expect(screen.getByText('Strong backend systems')).toBeInTheDocument();
		expect(screen.getByText('弱项')).toBeInTheDocument();
		expect(screen.getByText('Needs leadership calibration')).toBeInTheDocument();
		expect(screen.getByText('风险')).toBeInTheDocument();
		expect(screen.getByText('management scope unclear')).toBeInTheDocument();
		expect(screen.getByText('第 2 轮')).toBeInTheDocument();
	});
});
