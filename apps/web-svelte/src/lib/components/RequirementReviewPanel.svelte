<script lang="ts">
	import type { RequirementSheet, WorkbenchRequirementReview } from '$lib/workbench/types';

	let {
		review,
		saving = false,
		approving = false,
		error = null,
		onSave,
		onApprove,
		onEditingChange
	} = $props<{
		review: WorkbenchRequirementReview;
		saving?: boolean;
		approving?: boolean;
		error?: string | null;
		onSave: (sheet: RequirementSheet) => void;
		onApprove: () => void;
		onEditingChange?: (editing: boolean) => void;
	}>();

	let editing = $state(false);
	let localError = $state('');
	let draft = $state<RequirementSheet | null>(null);

	$effect(() => {
		if (!editing) draft = review.requirement_sheet;
	});

	$effect(() => {
		onEditingChange?.(editing);
	});

	const approved = $derived(review.status === 'approved');
	const hasSheet = $derived(Boolean(review.requirement_sheet));
	const mutating = $derived(saving || approving);

	function listText(values: string[] | undefined) {
		return (values ?? []).join('\n');
	}

	function lines(value: string) {
		return value
			.split('\n')
			.map((item) => item.trim())
			.filter(Boolean);
	}

	function updateList(key: keyof RequirementSheet, value: string) {
		if (!draft) return;
		draft = Object.assign({}, draft, { [key]: lines(value) });
	}

	function updateText(key: keyof RequirementSheet, value: string) {
		if (!draft) return;
		draft = Object.assign({}, draft, { [key]: value });
	}

	function updateJson(key: keyof RequirementSheet, value: string) {
		if (!draft) return;
		try {
			localError = '';
			draft = Object.assign({}, draft, { [key]: JSON.parse(value) });
		} catch {
			localError = `${String(key)} must be valid JSON.`;
		}
	}

	function save() {
		localError = '';
		if (!draft) {
			localError = '需求结构不能为空。';
			return;
		}
		onSave(draft);
		editing = false;
	}
</script>

<section class="requirement-review-panel">
	<div class="requirement-review-head">
		<div>
			<p class="section-label">需求确认</p>
			<h3>RequirementSheet</h3>
		</div>
		<span class:approved class="status-pill">{approved ? '已确认' : '待确认'}</span>
	</div>

	{#if !hasSheet}
		<p class="empty-copy">Agent 将先从岗位标题、JD 和 notes 提取结构化 RequirementSheet。</p>
	{:else if draft}
		{#if editing}
			<label class="field">
				<span>role_summary</span>
				<textarea
					rows="2"
					value={draft.role_summary}
					oninput={(event) => updateText('role_summary', event.currentTarget.value)}
				></textarea>
			</label>
			<label class="field">
				<span>title_anchor_terms</span>
				<textarea
					rows="2"
					value={listText(draft.title_anchor_terms)}
					oninput={(event) => updateList('title_anchor_terms', event.currentTarget.value)}
				></textarea>
			</label>
			<label class="field">
				<span>title_anchor_rationale</span>
				<textarea
					rows="2"
					value={draft.title_anchor_rationale}
					oninput={(event) => updateText('title_anchor_rationale', event.currentTarget.value)}
				></textarea>
			</label>
			<label class="field">
				<span>must_have_capabilities</span>
				<textarea
					rows="3"
					value={listText(draft.must_have_capabilities)}
					oninput={(event) => updateList('must_have_capabilities', event.currentTarget.value)}
				></textarea>
			</label>
			<label class="field">
				<span>preferred_capabilities</span>
				<textarea
					rows="3"
					value={listText(draft.preferred_capabilities)}
					oninput={(event) => updateList('preferred_capabilities', event.currentTarget.value)}
				></textarea>
			</label>
			<label class="field">
				<span>exclusion_signals</span>
				<textarea
					rows="2"
					value={listText(draft.exclusion_signals)}
					oninput={(event) => updateList('exclusion_signals', event.currentTarget.value)}
				></textarea>
			</label>
			<label class="field">
				<span>hard_constraints</span>
				<textarea
					rows="4"
					value={JSON.stringify(draft.hard_constraints ?? {}, null, 2)}
					oninput={(event) => updateJson('hard_constraints', event.currentTarget.value)}
				></textarea>
			</label>
			<label class="field">
				<span>preferences</span>
				<textarea
					rows="4"
					value={JSON.stringify(draft.preferences ?? {}, null, 2)}
					oninput={(event) => updateJson('preferences', event.currentTarget.value)}
				></textarea>
			</label>
			<label class="field">
				<span>initial_query_term_pool</span>
				<textarea
					rows="4"
					value={JSON.stringify(draft.initial_query_term_pool ?? [], null, 2)}
					oninput={(event) => updateJson('initial_query_term_pool', event.currentTarget.value)}
				></textarea>
			</label>
			<label class="field">
				<span>scoring_rationale</span>
				<textarea
					rows="2"
					value={draft.scoring_rationale}
					oninput={(event) => updateText('scoring_rationale', event.currentTarget.value)}
				></textarea>
			</label>
		{:else}
			<div class="runtime-criteria-summary" aria-label="RequirementSheet">
				<div class="runtime-criteria-row">
					<span>job_title</span>
					<p>{draft.job_title}</p>
				</div>
				<div class="runtime-criteria-row">
					<span>role_summary</span>
					<p>{draft.role_summary}</p>
				</div>
				<div class="runtime-criteria-row">
					<span>title_anchor_terms</span>
					<p>{draft.title_anchor_terms.join(' / ')}</p>
				</div>
				<div class="runtime-criteria-row">
					<span>title_anchor_rationale</span>
					<p>{draft.title_anchor_rationale}</p>
				</div>
				<div class="runtime-criteria-row">
					<span>must_have_capabilities</span>
					<p>{listText(draft.must_have_capabilities)}</p>
				</div>
				<div class="runtime-criteria-row">
					<span>preferred_capabilities</span>
					<p>{listText(draft.preferred_capabilities)}</p>
				</div>
				<div class="runtime-criteria-row">
					<span>exclusion_signals</span>
					<p>{listText(draft.exclusion_signals)}</p>
				</div>
				<div class="runtime-criteria-row">
					<span>hard_constraints</span>
					<p>{JSON.stringify(draft.hard_constraints ?? {})}</p>
				</div>
				<div class="runtime-criteria-row">
					<span>preferences</span>
					<p>{JSON.stringify(draft.preferences ?? {})}</p>
				</div>
				<div class="runtime-criteria-row">
					<span>initial_query_term_pool</span>
					<p>{JSON.stringify(draft.initial_query_term_pool ?? [])}</p>
				</div>
				<div class="runtime-criteria-row">
					<span>scoring_rationale</span>
					<p>{draft.scoring_rationale}</p>
				</div>
			</div>
		{/if}
	{/if}

	{#if localError || error}
		<p class="form-error" role="alert">{localError || error}</p>
	{/if}

	<div class="requirement-actions">
		{#if hasSheet && !editing}
			<button
				class="secondary-link"
				type="button"
				disabled={mutating}
				onclick={() => (editing = true)}
			>
				修改
			</button>
			<button
				class="primary-action"
				type="button"
				disabled={mutating || approved}
				onclick={onApprove}
			>
				{approving ? '确认中' : '确认需求'}
			</button>
		{:else if editing}
			<button
				class="secondary-link"
				type="button"
				disabled={mutating}
				onclick={() => (editing = false)}
			>
				取消
			</button>
			<button class="primary-action" type="button" disabled={mutating} onclick={save}>
				{saving ? '保存中' : '保存'}
			</button>
		{/if}
	</div>
</section>
