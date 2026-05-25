import { render, screen } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import type { RequirementSheet, WorkbenchRequirementReview } from '$lib/workbench/types';
import RequirementReviewPanel from './RequirementReviewPanel.svelte';

describe('RequirementReviewPanel', () => {
	it('renders the active RequirementSheet contract', () => {
		render(RequirementReviewPanel, {
			props: {
				review: requirementReview(),
				onSave: vi.fn(),
				onApprove: vi.fn()
			}
		});

		expect(screen.getByText('role_summary')).toBeInTheDocument();
		expect(screen.getByText('title_anchor_terms')).toBeInTheDocument();
		expect(screen.getByText('title_anchor_rationale')).toBeInTheDocument();
		expect(screen.getByText('must_have_capabilities')).toBeInTheDocument();
		expect(screen.getByText('preferred_capabilities')).toBeInTheDocument();
		expect(screen.getByText('exclusion_signals')).toBeInTheDocument();
		expect(screen.getByText('hard_constraints')).toBeInTheDocument();
		expect(screen.getByText('preferences')).toBeInTheDocument();
		expect(screen.getByText('initial_query_term_pool')).toBeInTheDocument();
		expect(screen.getByText('scoring_rationale')).toBeInTheDocument();
		expect(screen.getByText('Build Svelte recruiting workflows.')).toBeInTheDocument();
		expect(screen.getByText('Svelte Workbench')).toBeInTheDocument();
		expect(screen.getByText(/recruiting agent/)).toBeInTheDocument();
	});

	it('saves edited RequirementSheet values', async () => {
		const user = userEvent.setup();
		const onSave = vi.fn();
		render(RequirementReviewPanel, {
			props: {
				review: requirementReview(),
				onSave,
				onApprove: vi.fn()
			}
		});

		await user.click(screen.getByRole('button', { name: '修改' }));
		await user.clear(screen.getByLabelText('must_have_capabilities'));
		await user.type(screen.getByLabelText('must_have_capabilities'), 'SvelteKit\nTypeScript');
		await user.click(screen.getByRole('button', { name: '保存' }));

		expect(onSave).toHaveBeenCalledWith(
			expect.objectContaining({
				must_have_capabilities: ['SvelteKit', 'TypeScript']
			})
		);
	});
});

function requirementReview(
	overrides: Partial<WorkbenchRequirementReview> = {}
): WorkbenchRequirementReview {
	return {
		session_id: 'session-1',
		status: 'draft',
		requirement_sheet: requirementSheet(),
		created_at: '2026-05-25T00:00:00Z',
		updated_at: '2026-05-25T00:00:00Z',
		approved_at: null,
		...overrides
	};
}

function requirementSheet(overrides: Partial<RequirementSheet> = {}): RequirementSheet {
	return {
		job_title: 'Svelte Engineer',
		title_anchor_terms: ['Svelte Engineer'],
		title_anchor_rationale: 'The job title anchors the sourcing lane.',
		role_summary: 'Build Svelte recruiting workflows.',
		must_have_capabilities: ['Svelte Workbench'],
		preferred_capabilities: ['Recruiting agent workflow'],
		exclusion_signals: ['No frontend delivery evidence'],
		hard_constraints: {
			experience_requirement: {
				min_years: 3,
				max_years: null,
				raw_text: '3+ years',
				pinned: false
			}
		},
		preferences: { preferred_query_terms: ['SvelteKit'] },
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
		],
		scoring_rationale: 'Prioritize shipped Svelte workflow evidence.',
		...overrides
	};
}
