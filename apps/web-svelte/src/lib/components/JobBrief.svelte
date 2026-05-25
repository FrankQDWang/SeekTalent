<script lang="ts">
	import type { WorkbenchSession } from '$lib/workbench/types';

	let { session, onCollapseColumn } = $props<{
		session: WorkbenchSession;
		onCollapseColumn?: () => void;
	}>();

	let expanded = $state(false);
	const sourceCount = $derived(session.sourceCards.length);
	const sourceLabel = $derived(`${String(sourceCount)} 个渠道`);

	function sessionStatusLabel(status: string) {
		if (status === 'draft') return '草稿';
		if (status === 'running') return '运行中';
		if (status === 'completed') return '已完成';
		if (status === 'failed') return '失败';
		return status;
	}
</script>

<section class:expanded class="job-brief-card" data-testid="job-brief-card">
	<div class="job-brief-head">
		<div class="panel-heading">
			<p class="section-label">岗位简报</p>
			<h2 data-testid="active-session-title">{session.jobTitle || '未命名职位'}</h2>
		</div>
		{#if onCollapseColumn}
			<button
				class="minimal-icon-button"
				type="button"
				aria-label="收起岗位简报列"
				onclick={onCollapseColumn}
			>
				‹
			</button>
		{/if}
	</div>
	<div class="jd-pills">
		<span>{sourceCount > 1 ? '多源' : '单源'}</span>
		<span>{sessionStatusLabel(session.status)}</span>
		<span>{sourceLabel}</span>
	</div>
	<div class="job-brief-body">
		<section class="job-brief-section">
			<div class="job-brief-section-head">
				<span>JD</span>
				<button
					class="text-inline-button"
					type="button"
					aria-expanded={expanded}
					onclick={() => {
						expanded = !expanded;
					}}
				>
					{expanded ? '收起' : '展开'}
				</button>
			</div>
			<p class:job-brief-preview={!expanded}>{session.jdText}</p>
		</section>
		{#if session.notes?.trim()}
			<section class="job-brief-section">
				<span>补充说明</span>
				<p class:job-brief-preview={!expanded} class:short={!expanded}>{session.notes}</p>
			</section>
		{/if}
	</div>
</section>
