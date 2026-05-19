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
			localError = 'Select at least one source.';
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
		<p class="section-label">New session</p>
		<h2>Create session</h2>
	</div>
	<label class="field">
		<span>Job title</span>
		<input bind:value={form.jobTitle} required />
	</label>
	<label class="field">
		<span>JD</span>
		<textarea bind:value={form.jdText} required rows="8"></textarea>
	</label>
	<label class="field">
		<span>Notes</span>
		<textarea bind:value={form.notes} rows="4"></textarea>
	</label>
	<fieldset class="source-picker">
		<legend>Sources</legend>
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
				aria-label="Liepin"
				checked={(form.sourceKinds ?? []).includes('liepin')}
				onchange={() => toggleSourceKind('liepin')}
			/>
			<span>Liepin</span>
			<small>登录后加入检索</small>
		</label>
	</fieldset>
	{#if localError || error}
		<p class="form-error" role="alert">{localError || error}</p>
	{/if}
	<button class="primary-action" type="submit" disabled={creating}>
		{creating ? 'Creating' : 'Create session'}
	</button>
</form>
