<script lang="ts">
	import type { WorkbenchSessionCreateInput } from '$lib/workbench/types';
	import type { SourceKind } from '$lib/workbench/recruiterAnimation';

	type Props = {
		creating?: boolean;
		error?: string | null;
		onCreate: (input: WorkbenchSessionCreateInput) => void | Promise<void>;
	};

	let { creating = false, error = null, onCreate }: Props = $props();

	let form = $state<WorkbenchSessionCreateInput>({
		jobTitle: '',
		jdText: '',
		notes: '',
		sourceKinds: ['cts', 'liepin']
	});
	let localError = $state('');

	function toggleSourceKind(sourceKind: SourceKind) {
		const sourceKinds = form.sourceKinds ?? ['cts', 'liepin'];
		form = {
			...form,
			sourceKinds: sourceKinds.includes(sourceKind)
				? sourceKinds.filter((item) => item !== sourceKind)
				: [...sourceKinds, sourceKind]
		};
	}

	function submit(event: SubmitEvent) {
		event.preventDefault();
		localError = '';
		const sourceKinds = form.sourceKinds ?? [];
		if (sourceKinds.length === 0) {
			localError = '至少选择一个检索渠道。';
			return;
		}
		void onCreate({
			jobTitle: form.jobTitle.trim(),
			jdText: form.jdText.trim(),
			notes: form.notes?.trim() ?? '',
			sourceKinds
		});
	}
</script>

<form class="create-form" onsubmit={submit}>
	<div class="panel-head">
		<p class="section-label">新建会话</p>
		<h2>创建会话</h2>
	</div>
	<label class="field">
		<span>岗位名称</span>
		<input bind:value={form.jobTitle} required />
	</label>
	<label class="field">
		<span>岗位 JD</span>
		<textarea bind:value={form.jdText} required rows="8"></textarea>
	</label>
	<label class="field">
		<span>补充说明</span>
		<textarea bind:value={form.notes} rows="4"></textarea>
	</label>
	<fieldset class="source-picker">
		<legend>检索渠道</legend>
		<label>
			<input
				type="checkbox"
				aria-label="CTS"
				checked={(form.sourceKinds ?? []).includes('cts')}
				onchange={() => toggleSourceKind('cts')}
			/>
			<span>CTS</span>
			<small>结构化简历库</small>
		</label>
		<label>
			<input
				type="checkbox"
				aria-label="猎聘"
				checked={(form.sourceKinds ?? []).includes('liepin')}
				onchange={() => toggleSourceKind('liepin')}
			/>
			<span>猎聘</span>
			<small>登录后加入检索</small>
		</label>
	</fieldset>
	{#if localError || error}
		<p class="form-error" role="alert">{localError || error}</p>
	{/if}
	<button class="primary-action" type="submit" disabled={creating}>
		{creating ? '创建中' : '创建会话'}
	</button>
</form>
