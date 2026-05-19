<script lang="ts">
	import type { WorkbenchRequirementTriage } from '$lib/workbench/types';
	import type { WorkbenchRequirementTriageInput } from '$lib/workbench/recruiterAnimation';

	type TriageForm = Record<keyof WorkbenchRequirementTriageInput, string>;

	let {
		triage,
		reviewCriteria,
		saving = false,
		approving = false,
		error = null,
		onSave,
		onApprove
	} = $props<{
		triage: WorkbenchRequirementTriage;
		reviewCriteria: WorkbenchRequirementTriageInput;
		saving?: boolean;
		approving?: boolean;
		error?: string | null;
		onSave: (input: WorkbenchRequirementTriageInput) => void;
		onApprove: (input: WorkbenchRequirementTriageInput) => void;
	}>();

	let editing = $state(false);
	let dirty = $state(false);
	let form = $state<TriageForm>(triageInputToForm(emptyCriteria()));
	let localError = $state('');

	const approved = $derived(triage.status === 'approved');
	const mutating = $derived(saving || approving);
	const hasReviewCriteria = $derived(hasTriageInput(reviewCriteria));
	const rows = $derived(criteriaRows(reviewCriteria));

	$effect(() => {
		if (!editing && !dirty) {
			form = triageInputToForm(reviewCriteria);
		}
	});

	function beginEditing() {
		editing = true;
		dirty = false;
		localError = '';
		form = triageInputToForm(reviewCriteria);
	}

	function updateForm(key: keyof TriageForm, value: string) {
		dirty = true;
		form = { ...form, [key]: value };
	}

	function save(event: SubmitEvent) {
		event.preventDefault();
		localError = '';
		const input = formToTriageInput(form);
		if (!hasTriageInput(input)) {
			localError = 'Search criteria cannot be blank.';
			return;
		}
		onSave(input);
		editing = false;
		dirty = false;
	}

	function approve(input: WorkbenchRequirementTriageInput) {
		localError = '';
		if (!hasTriageInput(input)) {
			localError = 'Search criteria cannot be blank.';
			return;
		}
		onApprove(input);
	}

	function cancel() {
		editing = false;
		dirty = false;
		localError = '';
		form = triageInputToForm(reviewCriteria);
	}

	function triageInputToForm(input: WorkbenchRequirementTriageInput): TriageForm {
		return {
			mustHaves: input.mustHaves.join('\n'),
			niceToHaves: input.niceToHaves.join('\n'),
			synonyms: input.synonyms.join('\n'),
			seniorityFilters: input.seniorityFilters.join('\n'),
			exclusions: input.exclusions.join('\n'),
			generatedQueryHints: input.generatedQueryHints.join('\n')
		};
	}

	function formToTriageInput(input: TriageForm): WorkbenchRequirementTriageInput {
		return {
			mustHaves: listFromLines(input.mustHaves),
			niceToHaves: listFromLines(input.niceToHaves),
			synonyms: listFromLines(input.synonyms),
			seniorityFilters: listFromLines(input.seniorityFilters),
			exclusions: listFromLines(input.exclusions),
			generatedQueryHints: listFromLines(input.generatedQueryHints)
		};
	}

	function listFromLines(value: string) {
		return value
			.split('\n')
			.map((item) => item.trim())
			.filter(Boolean);
	}

	function hasTriageInput(input: WorkbenchRequirementTriageInput): boolean {
		return triageLists(input).some((values) => values.some((value) => value.trim().length > 0));
	}

	function criteriaRows(input: WorkbenchRequirementTriageInput): Array<[string, string[]]> {
		const rows: Array<[string, string[]]> = [
			['必须条件', input.mustHaves],
			['加分条件', input.niceToHaves],
			['同义词', input.synonyms],
			['资历过滤', input.seniorityFilters],
			['排除项', input.exclusions],
			['检索提示', input.generatedQueryHints]
		];
		return rows
			.map(
				([label, values]) => [label, values.filter((value) => value.trim())] as [string, string[]]
			)
			.filter(([, values]) => values.length > 0);
	}

	function emptyCriteria(): WorkbenchRequirementTriageInput {
		return {
			mustHaves: [],
			niceToHaves: [],
			synonyms: [],
			seniorityFilters: [],
			exclusions: [],
			generatedQueryHints: []
		};
	}

	function triageLists(input: WorkbenchRequirementTriageInput) {
		return [
			input.mustHaves,
			input.niceToHaves,
			input.synonyms,
			input.seniorityFilters,
			input.exclusions,
			input.generatedQueryHints
		];
	}
</script>

{#if !hasReviewCriteria && !editing}
	<section class="triage-gate triage-gate-placeholder">
		<div class="triage-head">
			<div>
				<p class="section-label">Requirement triage gate</p>
				<h3>Search criteria</h3>
			</div>
			<span class="status-pill">{triage.status}</span>
		</div>
		<p class="triage-empty-copy">
			Agent 将先拆解 JD，自动生成
			must-have、nice-to-have、排除项和检索提示。生成后你可以在这里审阅和微调。
		</p>
		{#if localError || error}
			<p class="form-error" role="alert">{localError || error}</p>
		{/if}
	</section>
{:else if !editing}
	<section class="triage-gate">
		<div class="triage-head">
			<div>
				<p class="section-label">Requirement triage gate</p>
				<h3>Search criteria</h3>
			</div>
			<span class:approved class="status-pill">{triage.status}</span>
		</div>
		<div class="runtime-criteria-summary" aria-label="Runtime extracted search criteria">
			<div class="runtime-criteria-head">
				<span>{approved ? '已保存标准' : 'Agent 提取'}</span>
			</div>
			{#each rows as [label, values] (label)}
				<div class="runtime-criteria-row">
					<span>{label}</span>
					<p>{values.slice(0, 4).join(' / ')}</p>
				</div>
			{/each}
		</div>
		{#if localError || error}
			<p class="form-error" role="alert">{localError || error}</p>
		{/if}
		<div class="triage-actions">
			<button class="secondary-link" type="button" disabled={mutating} onclick={beginEditing}>
				修改
			</button>
			<button
				class="primary-action"
				type="button"
				disabled={mutating || approved || !hasReviewCriteria}
				onclick={() => approve(reviewCriteria)}
			>
				{approving ? '确认中' : '确认标准'}
			</button>
		</div>
	</section>
{:else}
	<form class="triage-gate" onsubmit={save}>
		<div class="triage-head">
			<div>
				<p class="section-label">Requirement triage gate</p>
				<h3>Search criteria</h3>
			</div>
			<span class:approved class="status-pill">{triage.status}</span>
		</div>
		<label class="field triage-field">
			<span>Must-haves</span>
			<textarea
				value={form.mustHaves}
				rows="2"
				oninput={(event) => updateForm('mustHaves', event.currentTarget.value)}
			></textarea>
		</label>
		<label class="field triage-field">
			<span>Nice-to-haves</span>
			<textarea
				value={form.niceToHaves}
				rows="2"
				oninput={(event) => updateForm('niceToHaves', event.currentTarget.value)}
			></textarea>
		</label>
		<label class="field triage-field">
			<span>Synonyms</span>
			<textarea
				value={form.synonyms}
				rows="2"
				oninput={(event) => updateForm('synonyms', event.currentTarget.value)}
			></textarea>
		</label>
		<label class="field triage-field">
			<span>Seniority filters</span>
			<textarea
				value={form.seniorityFilters}
				rows="2"
				oninput={(event) => updateForm('seniorityFilters', event.currentTarget.value)}
			></textarea>
		</label>
		<label class="field triage-field">
			<span>Exclusions</span>
			<textarea
				value={form.exclusions}
				rows="2"
				oninput={(event) => updateForm('exclusions', event.currentTarget.value)}
			></textarea>
		</label>
		<label class="field triage-field">
			<span>Query hints</span>
			<textarea
				value={form.generatedQueryHints}
				rows="2"
				oninput={(event) => updateForm('generatedQueryHints', event.currentTarget.value)}
			></textarea>
		</label>
		{#if localError || error}
			<p class="form-error" role="alert">{localError || error}</p>
		{/if}
		<div class="triage-actions">
			<button class="secondary-link" type="submit" disabled={mutating}>保存标准</button>
			<button class="secondary-link" type="button" disabled={mutating} onclick={cancel}>取消</button
			>
			<button
				class="primary-action"
				type="button"
				disabled={mutating || approved}
				onclick={() => approve(formToTriageInput(form))}
			>
				{approving ? '确认中' : '确认标准'}
			</button>
		</div>
	</form>
{/if}
