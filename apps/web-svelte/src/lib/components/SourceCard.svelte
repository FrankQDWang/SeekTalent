<script lang="ts">
	import {
		sourceLabel,
		sourceReasonLabel,
		sourceStatusLabel as runtimeStatusLabel
	} from '$lib/workbench/sourceDisplay';
	import type { WorkbenchSession } from '$lib/workbench/types';

	type WorkbenchSourceCard = WorkbenchSession['sourceCards'][number];
	type RuntimeLaneState = NonNullable<
		NonNullable<WorkbenchSession['runtimeSourceState']>['sources']
	>[number];

	let { card, session, requirementApproved } = $props<{
		card: WorkbenchSourceCard;
		session: WorkbenchSession;
		requirementApproved: boolean;
	}>();

	const runtimeLane = $derived.by<RuntimeLaneState | null>(() => {
		return (
			session.runtimeSourceState?.sources.find(
				(source: RuntimeLaneState) => source.sourceKind === card.sourceKind
			) ?? null
		);
	});
	const displayStatus = $derived(runtimeLane?.status ?? card.status);
	const scannedCount = $derived(
		Math.max(runtimeLane?.cardsSeenCount ?? 0, card.cardsScannedCount ?? 0)
	);
	const hitCount = $derived(
		Math.max(runtimeLane?.candidatesCount ?? 0, card.uniqueCandidatesCount ?? 0)
	);
	const detailRecommendationCount = $derived(
		Math.max(runtimeLane?.detailRecommendationsCount ?? 0, card.detailOpenUsedCount ?? 0)
	);
	const detailBlockedCount = $derived(card.detailOpenBlockedCount ?? 0);
	const statusTone = $derived(sourceStatusTone(displayStatus, card));
	const warning = $derived(
		sourceWarningMessage(card, runtimeLane?.reasonCode, requirementApproved)
	);

	function sourceStatusTone(status: string, sourceCard: WorkbenchSourceCard) {
		if (sourceCard.sourceKind === 'liepin' && sourceCard.connectionStatus !== 'connected') {
			return 'blocked';
		}
		if (status === 'running') return 'running';
		if (status === 'completed') return 'done';
		if (status === 'failed') return 'failed';
		if (status === 'blocked' || status === 'partial') return 'blocked';
		return 'ready';
	}

	function sourceStatusText(
		status: string,
		sourceCard: WorkbenchSourceCard,
		runtimeReasonCode: string | null | undefined
	) {
		const reasonCode = sourceDisplayReason(sourceCard, runtimeReasonCode);
		if (sourceCard.sourceKind === 'liepin') {
			if (isLiepinBrowserChannelReason(reasonCode)) {
				return '通道不可用';
			}
			if (isLiepinAccountMismatchReason(reasonCode)) {
				return '账号不一致';
			}
			if (isLiepinLoginReason(reasonCode)) {
				return '需登录猎聘';
			}
			if (runtimeReasonCode) {
				return runtimeStatusLabel(status);
			}
		}
		if (
			sourceCard.sourceKind === 'liepin' &&
			(isLiepinLoginReason(sourceCard.connectionWarningCode) ||
				isLiepinLoginReason(sourceCard.warningCode) ||
				String(sourceCard.connectionStatus ?? '') === 'needs_login' ||
				String(sourceCard.connectionStatus ?? '') === 'login_required')
		) {
			return '需登录猎聘';
		}
		return runtimeStatusLabel(status);
	}

	function sourceSubtitle(sourceCard: WorkbenchSourceCard) {
		if (sourceCard.sourceKind === 'cts') {
			return '结构化简历库';
		}
		if (sourceCard.connectionStatus === 'connected') {
			return '猎聘账号通道';
		}
		return '使用本机 Chrome 登录态';
	}

	function sourceAccessLabel(
		sourceCard: WorkbenchSourceCard,
		runtimeReasonCode: string | null | undefined
	) {
		if (sourceCard.sourceKind === 'cts') return '本地库';
		const reasonCode = sourceDisplayReason(sourceCard, runtimeReasonCode);
		if (isLiepinBrowserChannelReason(reasonCode)) return '通道未就绪';
		if (reasonCode === 'source_provider_failed' || reasonCode === 'source_partial') {
			return '本轮未完成';
		}
		if (sourceCard.connectionStatus === 'connected') return '账号已连接';
		if (sourceCard.connectionStatus === 'login_in_progress') return '登录中';
		if (sourceCard.connectionStatus === 'verification_required') return '待验证';
		return '等待 Chrome 登录态';
	}

	function isLiepinBrowserChannelReason(reasonCode: string | null | undefined) {
		return (
			reasonCode === 'blocked_backend_unavailable' ||
			reasonCode === 'liepin_browser_probe_unavailable' ||
			reasonCode === 'source_browser_backend_unavailable' ||
			reasonCode === 'source_browser_extension_disconnected' ||
			reasonCode === 'source_browser_timeout' ||
			reasonCode === 'source_browser_policy_blocked' ||
			reasonCode === 'source_browser_interaction_required' ||
			String(reasonCode ?? '').startsWith('liepin_pi_')
		);
	}

	function isLiepinLoginReason(reasonCode: string | null | undefined) {
		return (
			reasonCode === 'login_required' ||
			reasonCode === 'liepin_browser_login_required' ||
			reasonCode === 'liepin_connection_not_connected' ||
			reasonCode === 'liepin_opencli_login_required' ||
			reasonCode === 'source_login_required'
		);
	}

	function isLiepinAccountMismatchReason(reasonCode: string | null | undefined) {
		return (
			reasonCode === 'liepin_browser_account_mismatch' || reasonCode === 'source_account_mismatch'
		);
	}

	function sourceDisplayReason(
		sourceCard: WorkbenchSourceCard,
		runtimeReasonCode: string | null | undefined
	) {
		const storedReasonCode = sourceCard.warningCode ?? sourceCard.connectionWarningCode;
		if (sourceCard.sourceKind === 'liepin' && isLiepinBrowserChannelReason(storedReasonCode)) {
			return storedReasonCode;
		}
		return runtimeReasonCode ?? storedReasonCode;
	}

	function sourceWarningMessage(
		sourceCard: WorkbenchSourceCard,
		runtimeReasonCode: string | null | undefined,
		approved: boolean
	) {
		const reasonCode = sourceDisplayReason(sourceCard, runtimeReasonCode);
		const reason = sourceReasonLabel(reasonCode);
		if (sourceCard.sourceKind === 'liepin' && reason) return reason;
		if (sourceCard.warningMessage) return sourceCard.warningMessage;
		if (sourceCard.connectionWarningMessage) return sourceCard.connectionWarningMessage;
		if (reason) return reason;
		if (sourceCard.sourceKind === 'liepin' && sourceCard.connectionStatus !== 'connected') {
			return '请先在本机 Chrome 登录猎聘并保持会话有效，系统会在检索时使用该登录态。';
		}
		if (!approved && !['queued', 'running', 'completed', 'failed'].includes(sourceCard.status)) {
			return '确认检索标准后可启动本次检索。';
		}
		return null;
	}
</script>

<article class="source-card" data-testid={`source-card-${card.sourceKind}`}>
	<div class="source-card-head">
		<div class="source-identity">
			<span class={`source-icon ${card.sourceKind}`} aria-hidden="true"></span>
			<div>
				<strong>{card.label || sourceLabel(card.sourceKind)}</strong>
				<span>{sourceSubtitle(card)}</span>
			</div>
		</div>
		<span class={`source-dot ${statusTone}`} aria-hidden="true"></span>
	</div>
	<div class="source-progress-row">
		<span class={`source-status-pill ${statusTone}`}
			>{sourceStatusText(displayStatus, card, runtimeLane?.reasonCode)}</span
		>
		<span>
			扫描 <strong>{scannedCount}</strong> · 命中 <strong>{hitCount}</strong>
		</span>
	</div>
	<div class="source-card-signal" aria-label={`${card.label} source state`}>
		<span>{sourceAccessLabel(card, runtimeLane?.reasonCode)}</span>
		<span>{card.sourceKind === 'cts' ? '批量检索' : '顺序查看'}</span>
		<span>{card.sourceKind === 'cts' ? '可回放' : '额度保护'}</span>
	</div>
	{#if card.sourceKind === 'liepin'}
		<dl class="source-state-strip detail-ledger-strip" aria-label="Liepin detail budget state">
			<div>
				<dt>详情</dt>
				<dd>{detailRecommendationCount}</dd>
			</div>
			<div>
				<dt>阻塞</dt>
				<dd>{detailBlockedCount}</dd>
			</div>
		</dl>
	{/if}
	{#if warning}
		<p class="source-warning">{warning}</p>
	{/if}
</article>
