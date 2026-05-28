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
	it('uses runtime running status while preserving source-card cumulative counts', () => {
		render(SourceCard, {
			props: {
				card: {
					...liepinLoginRequiredCard,
					status: 'completed',
					authState: 'not_required',
					warningCode: null,
					warningMessage: null,
					cardsScannedCount: 30,
					uniqueCandidatesCount: 30,
					connectionStatus: 'connected'
				},
				session: {
					runtimeSourceState: {
						sources: [
							{
								sourceKind: 'liepin',
								status: 'running',
								eventType: 'source_lane_running',
								eventSeq: 2,
								reasonCode: null,
								cardsSeenCount: 0,
								cardsFilteredCount: 0,
								candidatesCount: 0,
								detailRecommendationsCount: 0,
								detailState: null
							}
						]
					}
				} as unknown as WorkbenchSession,
				requirementApproved: true
			}
		});

		expect(screen.getByText('检索中')).toBeInTheDocument();
		expect(screen.getByTestId('source-card-liepin')).toHaveTextContent('扫描 30 · 命中 30');
	});

	it('shows passive local Chrome Liepin login guidance without a connect action', () => {
		render(SourceCard, {
			props: {
				card: liepinLoginRequiredCard,
				session,
				requirementApproved: false
			}
		});

		expect(screen.getByText('需登录猎聘')).toBeInTheDocument();
		expect(screen.getByText('使用本机 Chrome 登录态')).toBeInTheDocument();
		expect(
			screen.getByText('请先在本机 Chrome 登录猎聘并保持会话有效，系统会在检索时使用该登录态。')
		).toBeInTheDocument();
		expect(
			screen.queryByRole('link', { name: /连接猎聘|继续登录|probe/i })
		).not.toBeInTheDocument();
		expect(
			screen.queryByRole('button', { name: /连接猎聘|继续登录|probe/i })
		).not.toBeInTheDocument();
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
				requirementApproved: false
			}
		});

		expect(
			screen.getByText('请先在本机 Chrome 登录猎聘并保持会话有效，系统会在检索时使用该登录态。')
		).toBeInTheDocument();
		expect(screen.queryByText('Liepin login is not connected yet.')).not.toBeInTheDocument();
		expect(screen.queryByText('connection not connected')).not.toBeInTheDocument();
	});

	it('shows browser-channel unavailable status before stale login state', () => {
		render(SourceCard, {
			props: {
				card: {
					...liepinLoginRequiredCard,
					connectionStatus: 'login_required',
					connectionWarningCode: 'login_required'
				},
				session: {
					runtimeSourceState: {
						sources: [
							{
								sourceKind: 'liepin',
								status: 'blocked',
								eventType: 'source_lane_blocked',
								eventSeq: 2,
								reasonCode: 'liepin_browser_probe_unavailable',
								cardsSeenCount: 0,
								cardsFilteredCount: 0,
								candidatesCount: 0,
								detailRecommendationsCount: 0,
								detailState: null
							}
						]
					}
				} as unknown as WorkbenchSession,
				requirementApproved: true
			}
		});

		expect(screen.getByText('通道不可用')).toBeInTheDocument();
		expect(screen.queryByText('需登录猎聘')).not.toBeInTheDocument();
		expect(
			screen.getByText('浏览器检索通道暂不可用，请确认本机应用和浏览器助手正常后重试。')
		).toBeInTheDocument();
	});

	it('shows runtime provider failure before stale connection login state', () => {
		render(SourceCard, {
			props: {
				card: {
					...liepinLoginRequiredCard,
					connectionStatus: 'login_required',
					connectionWarningCode: 'login_required'
				},
				session: {
					runtimeSourceState: {
						sources: [
							{
								sourceKind: 'liepin',
								status: 'blocked',
								eventType: 'source_lane_blocked',
								eventSeq: 2,
								reasonCode: 'source_provider_failed',
								cardsSeenCount: 0,
								cardsFilteredCount: 0,
								candidatesCount: 0,
								detailRecommendationsCount: 0,
								detailState: null
							}
						]
					}
				} as unknown as WorkbenchSession,
				requirementApproved: true
			}
		});

		expect(screen.queryByText('需登录猎聘')).not.toBeInTheDocument();
		expect(screen.getByText('本轮未完成')).toBeInTheDocument();
		expect(screen.getByText('检索源返回错误。')).toBeInTheDocument();
	});

	it('shows public browser-channel reason before stale login state', () => {
		render(SourceCard, {
			props: {
				card: {
					...liepinLoginRequiredCard,
					warningCode: 'source_browser_extension_disconnected',
					connectionStatus: 'login_required',
					connectionWarningCode: 'login_required'
				},
				session,
				requirementApproved: true
			}
		});

		expect(screen.getByText('通道不可用')).toBeInTheDocument();
		expect(screen.getByText('通道未就绪')).toBeInTheDocument();
		expect(screen.queryByText('需登录猎聘')).not.toBeInTheDocument();
	});

	it('shows browser-channel setup state from source card warning before login wording', () => {
		render(SourceCard, {
			props: {
				card: {
					...liepinLoginRequiredCard,
					warningCode: 'liepin_opencli_extension_disconnected',
					warningMessage: '请先在本机 Chrome 登录猎聘并保持会话有效，系统会在检索时使用该登录态。',
					connectionStatus: 'login_required',
					connectionWarningCode: 'login_required'
				},
				session: {
					runtimeSourceState: {
						sources: [
							{
								sourceKind: 'liepin',
								status: 'blocked',
								eventType: 'source_lane_blocked',
								eventSeq: 2,
								reasonCode: 'liepin_browser_login_required',
								cardsSeenCount: 0,
								cardsFilteredCount: 0,
								candidatesCount: 0,
								detailRecommendationsCount: 0,
								detailState: null
							}
						]
					}
				} as unknown as WorkbenchSession,
				requirementApproved: false
			}
		});

		expect(screen.getByText('通道不可用')).toBeInTheDocument();
		expect(screen.getByText('通道未就绪')).toBeInTheDocument();
		expect(screen.queryByText('需登录猎聘')).not.toBeInTheDocument();
		expect(
			screen.getByText('浏览器检索通道未连接，请确认本机浏览器助手已启用后重试。')
		).toBeInTheDocument();
	});

	it('shows latest Liepin workflow step when present', () => {
		render(SourceCard, {
			props: {
				card: {
					...liepinLoginRequiredCard,
					status: 'running',
					authState: 'not_required',
					warningCode: null,
					warningMessage: null,
					connectionStatus: 'connected'
				},
				session: {
					runtimeSourceState: {
						sources: [
							{
								sourceKind: 'liepin',
								status: 'running',
								eventType: 'source_workflow_step_completed',
								eventSeq: 3,
								reasonCode: null,
								cardsSeenCount: 6,
								cardsFilteredCount: 0,
								candidatesCount: 1,
								detailRecommendationsCount: 0,
								detailState: null,
								latestWorkflowStep: {
									eventType: 'source_workflow_step_completed',
									stepName: 'capture_detail',
									status: 'completed',
									safeCounts: { details_opened: 1 },
									safeReasonCode: null
								}
							}
						]
					}
				} as unknown as WorkbenchSession,
				requirementApproved: true
			}
		});

		expect(screen.getByText(/capture_detail/)).toBeInTheDocument();
		expect(screen.getByText(/details_opened=1/)).toBeInTheDocument();
	});
});
