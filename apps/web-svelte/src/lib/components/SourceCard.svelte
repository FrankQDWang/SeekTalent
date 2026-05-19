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

	let { card, session, triageApproved } = $props<{
		card: WorkbenchSourceCard;
		session: WorkbenchSession;
		triageApproved: boolean;
	}>();

	const runtimeLane = $derived.by<RuntimeLaneState | null>(() => {
		return (
			session.runtimeSourceState?.sources.find(
				(source: RuntimeLaneState) => source.sourceKind === card.sourceKind
			) ?? null
		);
	});
	const displayStatus = $derived(runtimeLane?.status ?? card.status);
	const scannedCount = $derived(runtimeLane?.cardsSeenCount ?? card.cardsScannedCount ?? 0);
	const hitCount = $derived(runtimeLane?.candidatesCount ?? card.uniqueCandidatesCount ?? 0);
	const detailRecommendationCount = $derived(
		runtimeLane?.detailRecommendationsCount ?? card.detailOpenUsedCount ?? 0
	);
	const detailBlockedCount = $derived(card.detailOpenBlockedCount ?? 0);
	const statusTone = $derived(sourceStatusTone(displayStatus, card));
	const warning = $derived(sourceWarningMessage(card, runtimeLane?.reasonCode, triageApproved));

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

	function sourceStatusText(status: string, sourceCard: WorkbenchSourceCard) {
		const liepinLoginReasonCodes = new Set([
			'login_required',
			'liepin_browser_login_required',
			'liepin_connection_not_connected'
		]);
		if (
			sourceCard.sourceKind === 'liepin' &&
			(liepinLoginReasonCodes.has(sourceCard.connectionWarningCode ?? '') ||
				liepinLoginReasonCodes.has(sourceCard.warningCode ?? '') ||
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

	function sourceAccessLabel(sourceCard: WorkbenchSourceCard) {
		if (sourceCard.sourceKind === 'cts') return '本地库';
		if (sourceCard.connectionStatus === 'connected') return '账号已连接';
		if (sourceCard.connectionStatus === 'login_in_progress') return '登录中';
		if (sourceCard.connectionStatus === 'verification_required') return '待验证';
		return '等待 Chrome 登录态';
	}

	function sourceWarningMessage(
		sourceCard: WorkbenchSourceCard,
		runtimeReasonCode: string | null | undefined,
		approved: boolean
	) {
		const reasonCode = runtimeReasonCode ?? sourceCard.warningCode ?? sourceCard.connectionWarningCode;
		const reason = sourceReasonLabel(reasonCode);
		if (sourceCard.sourceKind === 'liepin' && reason) return reason;
		if (sourceCard.warningMessage) return sourceCard.warningMessage;
		if (sourceCard.connectionWarningMessage) return sourceCard.connectionWarningMessage;
		if (reason) return reason;
		if (sourceCard.sourceKind === 'liepin' && sourceCard.connectionStatus !== 'connected') {
			return '请先在本机 Chrome 登录猎聘并保持会话有效，系统会在检索时使用该登录态。';
		}
		if (!approved && !['queued', 'running', 'completed', 'failed'].includes(sourceCard.status)) {
			return '确认 Search criteria 后可启动本次检索。';
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
		<span class={`source-status-pill ${statusTone}`}>{sourceStatusText(displayStatus, card)}</span>
		<span>
			扫描 <strong>{scannedCount}</strong> · 命中 <strong>{hitCount}</strong>
		</span>
	</div>
	<div class="source-card-signal" aria-label={`${card.label} source state`}>
		<span>{sourceAccessLabel(card)}</span>
		<span>{card.sourceKind === 'cts' ? '批量检索' : '顺序查看'}</span>
		<span>{card.sourceKind === 'cts' ? '可回放' : '额度保护'}</span>
	</div>
	{#if card.sourceKind === 'liepin'}
		<dl class="source-state-strip detail-ledger-strip" aria-label="Liepin detail budget state">
			<div>
				<dt>DETAIL</dt>
				<dd>{detailRecommendationCount}</dd>
			</div>
			<div>
				<dt>BLOCK</dt>
				<dd>{detailBlockedCount}</dd>
			</div>
		</dl>
	{/if}
	{#if warning}
		<p class="source-warning">{warning}</p>
	{/if}
</article>
