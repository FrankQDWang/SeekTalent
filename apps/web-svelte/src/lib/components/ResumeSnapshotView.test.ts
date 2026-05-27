import { fireEvent, render, screen } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import type { WorkbenchGraphCandidateResumeSnapshot } from '$lib/workbench/types';
import ResumeSnapshotView from './ResumeSnapshotView.svelte';

const snapshot = {
	graphCandidateId: 'graph-candidate-1',
	status: 'ready',
	sourceCompleteness: 'cts_raw_payload',
	originalResume: {
		sourceKind: 'cts',
		sections: [
			{
				title: '基本信息',
				items: [
					{
						title: '基本信息',
						fields: [
							{ key: 'candidateName', label: '姓名', value: '原始候选人' },
							{ key: 'workYear', label: '工作年限', value: '8 年' }
						]
					}
				]
			}
		]
	},
	profile: {
		displayName: 'Normalized Name',
		headline: 'Normalized Title',
		company: 'Normalized Company',
		location: 'Normalized Location',
		summary: 'Normalized summary should not render'
	},
	workExperience: [
		{
			company: 'Normalized Work Company',
			title: 'Normalized Work Title',
			duration: null,
			summary: 'Normalized work summary should not render'
		}
	],
	education: [],
	projects: [],
	skills: ['Normalized Skill'],
	sourceEvidence: []
} satisfies WorkbenchGraphCandidateResumeSnapshot;

describe('ResumeSnapshotView', () => {
	it('renders only original resume text and keeps full resume behind an expand control', async () => {
		render(ResumeSnapshotView, {
			props: {
				graphCandidateId: 'graph-candidate-1',
				snapshot
			}
		});

		expect(screen.getByText('原始候选人')).toBeInTheDocument();
		expect(screen.getByText('展开完整简历')).toBeInTheDocument();
		expect(screen.queryByText('Normalized Name')).not.toBeInTheDocument();
		expect(screen.queryByText('Normalized summary should not render')).not.toBeInTheDocument();
		expect(screen.queryByText('Normalized Skill')).not.toBeInTheDocument();

		await fireEvent.click(screen.getByRole('button', { name: '展开完整简历' }));

		expect(screen.getByRole('button', { name: '收起' })).toBeInTheDocument();
	});

	it('renders duplicate original resume section and item titles without crashing', () => {
		render(ResumeSnapshotView, {
			props: {
				graphCandidateId: 'graph-candidate-1',
				snapshot: {
					...snapshot,
					originalResume: {
						sourceKind: 'cts',
						sections: [
							{
								title: '教育经历',
								items: [
									{
										title: '河北金融学院',
										fields: [{ key: 'schoolName', label: '学校', value: '河北金融学院' }]
									},
									{
										title: '河北金融学院',
										fields: [{ key: 'degree', label: '学历', value: '本科' }]
									}
								]
							}
						]
					}
				}
			}
		});

		expect(screen.getAllByText('河北金融学院').length).toBeGreaterThan(1);
		expect(screen.getByText('本科')).toBeInTheDocument();
	});
});
