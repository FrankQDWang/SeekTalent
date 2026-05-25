import { render, screen } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import type { WorkbenchFinalTopCandidate } from '$lib/workbench/types';
import CandidateQueue from './CandidateQueue.svelte';

const items = [
	{
		reviewItemId: 'review-liepin',
		runtimeIdentityId: 'identity-1',
		canonicalReviewItemId: 'review-liepin',
		mergedReviewItemIds: ['review-cts', 'review-liepin'],
		rank: 1,
		displayName: 'Lin Qian',
		title: 'Senior Svelte Engineer',
		company: 'SearchCo',
		location: 'Shanghai',
		summary: 'Dual-source match with recent Liepin detail.',
		aggregateScore: 93,
		fitBucket: 'fit',
		whySelected: 'Runtime selected this candidate for agent workflow depth.',
		riskFlags: ['management scope unclear'],
		matchedMustHaves: ['Python backend', 'distributed systems'],
		matchedPreferences: ['agent tooling'],
		strengths: ['Strong backend systems'],
		weaknesses: ['Needs leadership calibration'],
		sourceRound: 2,
		sourceBadges: ['CTS final', 'Liepin detail', 'Multiple sources'],
		evidenceLevel: 'detail',
		sourceEvidence: [
			{
				evidenceId: 'e-cts',
				sourceRunId: 'run-cts',
				sourceKind: 'cts',
				evidenceLevel: 'final',
				score: 91,
				fitBucket: 'fit'
			},
			{
				evidenceId: 'e-liepin',
				sourceRunId: 'run-liepin',
				sourceKind: 'liepin',
				evidenceLevel: 'detail',
				score: 93,
				fitBucket: 'fit'
			}
		]
	}
] satisfies WorkbenchFinalTopCandidate[];

describe('CandidateQueue', () => {
	it('renders identity-level top candidates with source badges', () => {
		render(CandidateQueue, { props: { items } });

		expect(screen.getByText('Lin Qian')).toBeInTheDocument();
		expect(screen.getByText('CTS final')).toBeInTheDocument();
		expect(screen.getByText('Liepin detail')).toBeInTheDocument();
		expect(screen.getByText('Multiple sources')).toBeInTheDocument();
		expect(screen.getByText('93')).toBeInTheDocument();
	});

	it('shows an empty state instead of slicing local candidates', () => {
		render(CandidateQueue, { props: { items: [] } });

		expect(screen.getByText('检索完成后会在这里显示统一排序候选人。')).toBeInTheDocument();
	});
});
