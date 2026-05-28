import { render, screen } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { RequirementSheet, WorkbenchSession } from '$lib/workbench/types';
import RequirementReviewPanel from './RequirementReviewPanel.svelte';
import SourceRunControlPanel from './SourceRunControlPanel.svelte';

type RunControlSession = {
	requirement_review: Pick<WorkbenchSession['requirement_review'], 'status' | 'requirement_sheet'>;
	sourceRuns: Array<Pick<WorkbenchSession['sourceRuns'][number], 'status'>>;
};

const session = {
	requirement_review: {
		status: 'draft',
		requirement_sheet: null
	},
	sourceRuns: [{ status: 'queued' }]
} satisfies RunControlSession;

describe('SourceRunControlPanel', () => {
	it('blocks source start until requirement review is approved', () => {
		render(SourceRunControlPanel, {
			props: {
				session,
				onPrepare: vi.fn(),
				onApprove: vi.fn(),
				onStart: vi.fn()
			}
		});

		expect(screen.getByRole('button', { name: '启动双源检索' })).toBeDisabled();
	});

	it('emits start when requirement review is approved', async () => {
		const user = userEvent.setup();
		const onStart = vi.fn();
		render(SourceRunControlPanel, {
			props: {
				session: {
					...session,
					requirement_review: {
						...session.requirement_review,
						status: 'approved',
						requirement_sheet: requirementSheet()
					}
				},
				onPrepare: vi.fn(),
				onApprove: vi.fn(),
				onStart
			}
		});

		await user.click(screen.getByRole('button', { name: '启动双源检索' }));

		expect(onStart).toHaveBeenCalledTimes(1);
	});

	it('renders generated RequirementSheet before approval', () => {
		render(RequirementReviewPanel, {
			props: {
				review: {
					session_id: 'session-1',
					status: 'draft',
					requirement_sheet: requirementSheet({
						role_summary: 'Build Svelte recruiting workflow',
						must_have_capabilities: ['Svelte Workbench'],
						initial_query_term_pool: [
							{
								term: 'recruiting agent',
								source: 'notes',
								category: 'domain',
								priority: 1,
								evidence: 'seed term',
								first_added_round: 0,
								active: true,
								retrieval_role: 'domain_context',
								queryability: 'admitted',
								family: 'domain.recruitingagent'
							}
						]
					}),
					created_at: '2026-05-25T00:00:00Z',
					updated_at: '2026-05-25T00:00:00Z',
					approved_at: null
				},
				onSave: vi.fn(),
				onApprove: vi.fn()
			}
		});

		expect(screen.getByText('Svelte Workbench')).toBeInTheDocument();
		expect(screen.getByText(/recruiting agent/)).toBeInTheDocument();
	});
});

function requirementSheet(overrides: Partial<RequirementSheet> = {}): RequirementSheet {
	return {
		job_title: 'Svelte Engineer',
		title_anchor_terms: ['Svelte'],
		title_anchor_rationale: 'The title anchors sourcing.',
		role_summary: 'Build Svelte apps.',
		must_have_capabilities: ['Svelte'],
		preferred_capabilities: ['Recruiting workflow'],
		exclusion_signals: [],
		hard_constraints: {},
		preferences: {},
		initial_query_term_pool: [],
		scoring_rationale: 'Prioritize Svelte experience.',
		...overrides
	};
}
