import { render, screen } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import type { WorkbenchSession } from '$lib/workbench/types';
import SourceCard from './SourceCard.svelte';

const liepinLoginRequiredCard = {
	sourceRunId: 'src-liepin',
	sourceKind: 'liepin',
	label: 'Liepin',
	status: 'blocked',
	authState: 'login_required',
	warningCode: 'liepin_browser_login_required',
	warningMessage: '请先在本机 Chrome 登录猎聘并保持会话有效，系统会在检索时使用该登录态。',
	cardsScannedCount: 0,
	uniqueCandidatesCount: 0,
	detailOpenUsedCount: 0,
	detailOpenBlockedCount: 0,
	connectionId: null,
	connectionStatus: null,
	connectionWarningCode: null,
	connectionWarningMessage: null
} as WorkbenchSession['sourceCards'][number];

const session = {
	runtimeSourceState: {
		sources: []
	}
} as unknown as WorkbenchSession;

describe('SourceCard', () => {
	it('shows passive local Chrome Liepin login guidance without a connect action', () => {
		render(SourceCard, {
			props: {
				card: liepinLoginRequiredCard,
				session,
				triageApproved: false
			}
		});

		expect(screen.getByText('需登录猎聘')).toBeInTheDocument();
		expect(screen.getByText('使用本机 Chrome 登录态')).toBeInTheDocument();
		expect(
			screen.getByText('请先在本机 Chrome 登录猎聘并保持会话有效，系统会在检索时使用该登录态。')
		).toBeInTheDocument();
		expect(screen.queryByRole('link', { name: /连接猎聘|继续登录|probe/i })).not.toBeInTheDocument();
		expect(screen.queryByRole('button', { name: /连接猎聘|继续登录|probe/i })).not.toBeInTheDocument();
	});

	it('prefers safe Liepin reason copy over stale stored warning text', () => {
		render(SourceCard, {
			props: {
				card: {
					...liepinLoginRequiredCard,
					warningMessage: 'Liepin login is not connected yet.',
					connectionStatus: 'login_required',
					connectionWarningCode: 'login_required',
					connectionWarningMessage: 'connection not connected'
				},
				session,
				triageApproved: false
			}
		});

		expect(
			screen.getByText('请先在本机 Chrome 登录猎聘并保持会话有效，系统会在检索时使用该登录态。')
		).toBeInTheDocument();
		expect(screen.queryByText('Liepin login is not connected yet.')).not.toBeInTheDocument();
		expect(screen.queryByText('connection not connected')).not.toBeInTheDocument();
	});
});
