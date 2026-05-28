import { render, screen } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import type { RequirementSheet, WorkbenchRequirementReview } from '$lib/workbench/types';
import RequirementReviewPanel from './RequirementReviewPanel.svelte';

describe('RequirementReviewPanel', () => {
	it('renders generated requirements as editable recruiter-facing text, not raw contract fields', () => {
		render(RequirementReviewPanel, {
			props: {
				review: requirementReview(),
				onSave: vi.fn(),
				onApprove: vi.fn()
			}
		});

		expect(screen.getByRole('heading', { name: '检索标准' })).toBeInTheDocument();
		expect(screen.getByText('岗位摘要')).toBeInTheDocument();
		expect(screen.getByText('必须满足')).toBeInTheDocument();
		expect(screen.getByText('加分项')).toBeInTheDocument();
		expect(screen.getByText('检索关键词')).toBeInTheDocument();
		expect(screen.queryByText('RequirementSheet')).not.toBeInTheDocument();
		expect(screen.queryByText('initial_query_term_pool')).not.toBeInTheDocument();
		expect(screen.queryByText('hard_constraints')).not.toBeInTheDocument();
		expect(screen.queryByText(/\{"experience_requirement"/)).not.toBeInTheDocument();
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
		await user.clear(screen.getByLabelText('必须满足'));
		await user.type(screen.getByLabelText('必须满足'), 'SvelteKit\nTypeScript');
		await user.click(screen.getByRole('button', { name: '保存' }));

		expect(onSave).toHaveBeenCalledWith(
			expect.objectContaining({
				must_have_capabilities: ['SvelteKit', 'TypeScript']
			})
		);
	});

	it('saves edited search keywords back into the backend RequirementSheet shape', async () => {
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
		await user.clear(screen.getByLabelText('检索关键词'));
		await user.type(screen.getByLabelText('检索关键词'), 'SvelteKit\nTypeScript');
		await user.click(screen.getByRole('button', { name: '保存' }));

		expect(onSave).toHaveBeenCalledWith(
			expect.objectContaining({
				initial_query_term_pool: [
					expect.objectContaining({ term: 'SvelteKit', active: true }),
					expect.objectContaining({ term: 'TypeScript', active: true })
				]
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
