<script lang="ts">
	import { createMutation, useQueryClient } from '@tanstack/svelte-query';
	import { safeErrorMessage } from '$lib/api/errors';
	import { approveDetailOpenRequest, rejectDetailOpenRequest } from '$lib/api/workbench';
	import { workbenchKeys } from '$lib/query/keys';
	import type { WorkbenchDetailOpenRequest } from '$lib/workbench/types';

	let {
		sessionId,
		requests = [],
		loading = false,
		error = null
	} = $props<{
		sessionId: string;
		requests?: WorkbenchDetailOpenRequest[];
		loading?: boolean;
		error?: string | null;
	}>();

	let actionError = $state('');
	let providerMessage = $state('');
	const queryClient = useQueryClient();
	const pendingRequests = $derived(
		requests.filter((request: WorkbenchDetailOpenRequest) => request.status === 'pending')
	);
	const visibleRequests = $derived(
		pendingRequests.length > 0 ? pendingRequests : requests.slice(-4).reverse()
	);
	const pendingLabel = $derived(`${pendingRequests.length} 个待处理`);

	const approveMutation = createMutation(() => ({
		mutationFn: (requestId: string) => approveDetailOpenRequest(requestId),
		onSuccess: async () => {
			actionError = '';
			providerMessage = '';
			await refreshDetailState();
		},
		onError: (caught) => {
			actionError = safeErrorMessage(caught, '详情批准失败');
		}
	}));

	const rejectMutation = createMutation(() => ({
		mutationFn: (requestId: string) =>
			rejectDetailOpenRequest(requestId, 'Rejected from Svelte workbench queue.'),
		onSuccess: async () => {
			actionError = '';
			providerMessage = '';
			await refreshDetailState();
		},
		onError: (caught) => {
			actionError = safeErrorMessage(caught, '详情拒绝失败');
		}
	}));

	async function refreshDetailState() {
		await Promise.all([
			queryClient.invalidateQueries({ queryKey: workbenchKeys.detailOpenRequests(sessionId) }),
			queryClient.invalidateQueries({ queryKey: workbenchKeys.graphCandidatesRoot(sessionId) }),
			queryClient.invalidateQueries({ queryKey: workbenchKeys.resumeSnapshotRoot(sessionId) }),
			queryClient.invalidateQueries({ queryKey: workbenchKeys.session(sessionId) }),
			queryClient.invalidateQueries({ queryKey: workbenchKeys.sessions })
		]);
	}

	function detailBudgetBadgeText(request: WorkbenchDetailOpenRequest) {
		if (request.status === 'pending') return '批准后占用 1 次详情额度';
		if (
			request.status === 'approved' ||
			request.ledger?.status === 'leased' ||
			request.ledger?.status === 'opened'
		) {
			return '详情额度已预留';
		}
		if (request.status === 'rejected') return '已跳过，不占用额度';
		if (request.status === 'blocked') {
			return request.blockedReason ? `阻塞 · ${request.blockedReason}` : '详情打开已阻塞';
		}
		if (request.status === 'bypassed') return '绕过确认，后台已按策略处理';
		return `详情状态 · ${request.status}`;
	}

	function statusLabel(status: string) {
		const labels: Record<string, string> = {
			pending: '待处理',
			approved: '已批准',
			rejected: '已拒绝',
			blocked: '已阻塞',
			bypassed: '已跳过'
		};
		return labels[status] ?? status;
	}

	function ledgerLabel(status: string | null | undefined) {
		const labels: Record<string, string> = {
			leased: '已预留',
			opened: '已打开',
			released: '已释放'
		};
		return status ? (labels[status] ?? status) : null;
	}
</script>

{#if loading || error || visibleRequests.length > 0 || actionError}
	<div class="detail-request-panel">
		<div class="queue-heading">
			<span>详情审批</span>
			<strong>{pendingLabel}</strong>
		</div>
		{#if loading}
			<p class="muted">正在加载详情请求</p>
		{/if}
		{#if error}
			<p class="form-error" role="alert">{error}</p>
		{/if}
		{#if actionError}
			<p class="form-error" role="alert">{actionError}</p>
		{/if}
		{#if providerMessage}
			<p class="candidate-action-message">{providerMessage}</p>
		{/if}
		{#if visibleRequests.length > 0}
			<ol class="detail-request-list">
				{#each visibleRequests as request (request.requestId)}
					<li>
						<div class="detail-request-main">
							<div>
								<strong>{request.candidate?.displayName ?? '猎聘候选人'}</strong>
								<span>
									{[
										request.candidate?.title,
										request.candidate?.company,
										request.candidate?.location
									]
										.filter(Boolean)
										.join(' · ') || '等待补充候选人信息'}
								</span>
							</div>
							<span class:approved={request.status === 'approved'} class="status-pill">
								{statusLabel(request.status)}
							</span>
						</div>
						{#if request.decisionNote}
							<p class="detail-request-reason">{request.decisionNote}</p>
						{/if}
						<div class="detail-request-evidence">
							{#each request.candidate?.matchedMustHaves.slice(0, 3) ?? [] as value (value)}
								<span class="source-badge">必须 · {value}</span>
							{/each}
							<span class="source-badge amber-badge">{detailBudgetBadgeText(request)}</span>
						</div>
						{#if request.status === 'pending'}
							<div class="detail-request-actions">
								<button
									class="primary-action compact"
									type="button"
									disabled={approveMutation.isPending || rejectMutation.isPending}
									onclick={() => approveMutation.mutate(request.requestId)}
								>
									批准打开
								</button>
								<button
									class="secondary-link compact"
									type="button"
									disabled={approveMutation.isPending || rejectMutation.isPending}
									onclick={() => rejectMutation.mutate(request.requestId)}
								>
									暂不打开
								</button>
							</div>
						{:else}
							<div class="detail-request-actions">
								{#if request.providerAction}
									<button
										class="secondary-link compact"
										type="button"
										onclick={() => {
											providerMessage = request.providerAction?.message ?? '';
										}}
									>
										查看安全动作
									</button>
								{/if}
								<span class="source-badge muted-badge">
									{ledgerLabel(request.ledger?.status) ??
										request.blockedReason ??
										request.detailOpenMode}
								</span>
							</div>
						{/if}
					</li>
				{/each}
			</ol>
		{/if}
	</div>
{/if}
