<script lang="ts">
	import type { WorkbenchGraphCandidateResumeSnapshot } from '$lib/workbench/types';

	let {
		graphCandidateId,
		snapshot,
		loading = false,
		error = null
	} = $props<{
		graphCandidateId: string;
		snapshot?: WorkbenchGraphCandidateResumeSnapshot | null;
		loading?: boolean;
		error?: string | null;
	}>();

	function snapshotStatusLabel(status: WorkbenchGraphCandidateResumeSnapshot['status']) {
		if (status === 'snapshot_forbidden') return '简历快照受限';
		if (status === 'snapshot_not_found') return '未找到简历快照';
		return '简历快照不可用';
	}
</script>

{#if loading}
	<p class="muted">正在读取安全简历快照...</p>
{:else if error}
	<p class="form-error" role="alert">{error}</p>
{:else if snapshot}
	{#if snapshot.status !== 'ready'}
		<div class="resume-snapshot">
			<strong>{snapshotStatusLabel(snapshot.status)}</strong>
			<p>{snapshot.reason ?? '该简历快照暂时不可展示。'}</p>
		</div>
	{:else}
		<div class="resume-snapshot" data-testid={`resume-snapshot-${graphCandidateId}`}>
			{#if snapshot.profile}
				<section>
					<strong>{snapshot.profile.displayName}</strong>
					<p>
						{[snapshot.profile.headline, snapshot.profile.company, snapshot.profile.location]
							.filter(Boolean)
							.join(' · ')}
					</p>
					{#if snapshot.profile.summary}
						<p>{snapshot.profile.summary}</p>
					{/if}
				</section>
			{:else}
				<p class="muted">未找到可展示的系统整理摘要。</p>
			{/if}

			{#if (snapshot.workExperience ?? []).length > 0}
				<section class="resume-snapshot-section">
					<span>工作经历</span>
					<ul>
						{#each snapshot.workExperience ?? [] as item, index (`work-${index}`)}
							<li>
								<strong
									>{[item.title, item.company, item.duration].filter(Boolean).join(' · ')}</strong
								>
								{#if item.summary}
									<p>{item.summary}</p>
								{/if}
							</li>
						{/each}
					</ul>
				</section>
			{/if}

			{#if (snapshot.education ?? []).length > 0}
				<section class="resume-snapshot-section">
					<span>教育经历</span>
					<ul>
						{#each snapshot.education ?? [] as item, index (`edu-${index}`)}
							<li>
								<strong>{item.school}</strong>
								<p>{[item.degree, item.major].filter(Boolean).join(' · ')}</p>
							</li>
						{/each}
					</ul>
				</section>
			{/if}

			{#if (snapshot.projects ?? []).length > 0}
				<section class="resume-snapshot-section">
					<span>项目</span>
					<ul>
						{#each snapshot.projects ?? [] as item, index (`project-${index}`)}
							<li>
								<strong>{item.name}</strong>
								{#if item.summary}
									<p>{item.summary}</p>
								{/if}
							</li>
						{/each}
					</ul>
				</section>
			{/if}

			{#if (snapshot.skills ?? []).length > 0}
				<div class="candidate-facts">
					<span>Skills</span>
					<p>{(snapshot.skills ?? []).slice(0, 8).join(' / ')}</p>
				</div>
			{/if}
		</div>
	{/if}
{/if}
