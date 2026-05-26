<script lang="ts">
	import type {
		HardConstraintSlots,
		PreferenceSlots,
		QueryTermCandidate,
		RequirementSheet,
		WorkbenchRequirementReview
	} from '$lib/workbench/types';

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

	const visibleKeywordLimit = 8;

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
	const keywordTerms = $derived(draft ? queryTermTexts(draft) : []);
	const hardConstraintItems = $derived(draft ? hardConstraintText(draft.hard_constraints) : []);
	const preferenceItems = $derived(draft ? preferenceText(draft.preferences) : []);
	const visibleKeywords = $derived(keywordTerms.slice(0, visibleKeywordLimit));
	const hiddenKeywordCount = $derived(Math.max(keywordTerms.length - visibleKeywordLimit, 0));

	function listText(values: string[] | undefined) {
		return (values ?? []).join('\n');
	}

	function lines(value: string) {
		return value
			.split('\n')
			.map((item) => item.trim())
			.filter(Boolean);
	}

	function uniqueLines(value: string) {
		return uniqueTexts(lines(value));
	}

	function updateText(key: keyof RequirementSheet, value: string) {
		if (!draft) return;
		draft = { ...draft, [key]: value };
	}

	function updateList(key: keyof RequirementSheet, value: string) {
		if (!draft) return;
		draft = { ...draft, [key]: uniqueLines(value) };
	}

	function updateHardConstraintList(
		key: 'locations' | 'school_names' | 'company_names',
		value: string
	) {
		if (!draft) return;
		draft = {
			...draft,
			hard_constraints: {
				...(draft.hard_constraints ?? {}),
				[key]: uniqueLines(value)
			}
		};
	}

	function updateExperienceRequirement(value: string) {
		if (!draft) return;
		const text = value.trim();
		const previous = draft.hard_constraints?.experience_requirement;
		draft = {
			...draft,
			hard_constraints: {
				...(draft.hard_constraints ?? {}),
				experience_requirement: text
					? {
							...(previous ?? { min_years: null, max_years: null }),
							raw_text: text,
							pinned: previous?.pinned ?? false
						}
					: null
			}
		};
	}

	function updateDegreeRequirement(value: string) {
		if (!draft) return;
		const text = value.trim();
		const previous = draft.hard_constraints?.degree_requirement;
		draft = {
			...draft,
			hard_constraints: {
				...(draft.hard_constraints ?? {}),
				degree_requirement: text
					? {
							...(previous ?? {}),
							canonical_degree: text,
							raw_text: text,
							pinned: previous?.pinned ?? false
						}
					: null
			}
		};
	}

	function updatePreferenceList(key: keyof PreferenceSlots, value: string) {
		if (!draft) return;
		draft = {
			...draft,
			preferences: {
				...(draft.preferences ?? {}),
				[key]: uniqueLines(value)
			}
		};
	}

	function updateQueryTerms(value: string) {
		if (!draft) return;
		const nextTerms = uniqueLines(value);
		const existingByTerm = new Map(
			(draft.initial_query_term_pool ?? []).map((candidate) => [
				normalizeTerm(candidate.term),
				candidate
			])
		);
		const initial_query_term_pool = nextTerms.map((term, index) =>
			queryTermCandidate(term, index, existingByTerm.get(normalizeTerm(term)))
		);
		draft = {
			...draft,
			initial_query_term_pool,
			preferences: {
				...(draft.preferences ?? {}),
				preferred_query_terms: nextTerms.slice(0, 4)
			}
		};
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

	function queryTermTexts(sheet: RequirementSheet) {
		return uniqueTexts([
			...(sheet.initial_query_term_pool ?? [])
				.filter((candidate) => candidate.active !== false)
				.map((candidate) => candidate.term),
			...(sheet.preferences?.preferred_query_terms ?? [])
		]);
	}

	function queryTermCandidate(
		term: string,
		index: number,
		existing: QueryTermCandidate | undefined
	): QueryTermCandidate {
		return {
			term,
			source: existing?.source ?? 'notes',
			category: existing?.category ?? 'domain',
			priority: index + 1,
			evidence: existing?.evidence ?? '用户编辑的检索关键词。',
			first_added_round: existing?.first_added_round ?? 0,
			active: true,
			retrieval_role: existing?.retrieval_role ?? 'domain_context',
			queryability: existing?.queryability ?? 'admitted',
			family: existing?.family ?? `domain.${familyKey(term)}`
		};
	}

	function hardConstraintText(hardConstraints: HardConstraintSlots | undefined) {
		if (!hardConstraints) return [];
		return [
			...prefixedItems('地点', hardConstraints.locations),
			...prefixedItems('学校', hardConstraints.school_names),
			...prefixedItems('公司', hardConstraints.company_names),
			textWithLabel('经验', hardConstraints.experience_requirement?.raw_text),
			textWithLabel(
				'学历',
				hardConstraints.degree_requirement?.raw_text ??
					hardConstraints.degree_requirement?.canonical_degree
			),
			textWithLabel('学校类型', hardConstraints.school_type_requirement?.raw_text),
			textWithLabel('性别', hardConstraints.gender_requirement?.raw_text),
			textWithLabel('年龄', hardConstraints.age_requirement?.raw_text)
		].filter((item): item is string => Boolean(item));
	}

	function preferenceText(preferences: PreferenceSlots | undefined) {
		if (!preferences) return [];
		return [
			...prefixedItems('优先地点', preferences.preferred_locations),
			...prefixedItems('优先公司', preferences.preferred_companies),
			...prefixedItems('优先领域', preferences.preferred_domains),
			...prefixedItems('优先背景', preferences.preferred_backgrounds)
		];
	}

	function prefixedItems(label: string, values: string[] | undefined) {
		return (values ?? []).map((value) => textWithLabel(label, value)).filter(Boolean) as string[];
	}

	function textWithLabel(label: string, value: string | null | undefined) {
		const text = typeof value === 'string' ? value.trim() : '';
		if (!text || text === '不限') return null;
		return `${label}: ${text}`;
	}

	function uniqueTexts(values: string[]) {
		const seen: string[] = [];
		const result: string[] = [];
		for (const value of values) {
			const text = value.trim();
			const key = normalizeTerm(text);
			if (!text || seen.includes(key)) continue;
			seen.push(key);
			result.push(text);
		}
		return result;
	}

	function normalizeTerm(value: string) {
		return value.trim().toLocaleLowerCase();
	}

	function familyKey(value: string) {
		return normalizeTerm(value).replace(/[^\p{L}\p{N}]+/gu, '');
	}
</script>

<section class="requirement-review-panel">
	<div class="requirement-review-head">
		<div>
			<p class="section-label">需求确认</p>
			<h3>检索标准</h3>
		</div>
		<span class:approved class="status-pill">{approved ? '已确认' : '待确认'}</span>
	</div>

	{#if !hasSheet}
		<p class="empty-copy">点击策略图中央按钮后，系统会先提取可确认的检索标准。</p>
	{:else if draft}
		{#if editing}
			<label class="field">
				<span>岗位摘要</span>
				<textarea
					rows="2"
					value={draft.role_summary}
					oninput={(event) => updateText('role_summary', event.currentTarget.value)}
				></textarea>
			</label>
			<label class="field">
				<span>职位锚点</span>
				<textarea
					rows="2"
					value={listText(draft.title_anchor_terms)}
					oninput={(event) => updateList('title_anchor_terms', event.currentTarget.value)}
				></textarea>
			</label>
			<label class="field">
				<span>必须满足</span>
				<textarea
					rows="4"
					value={listText(draft.must_have_capabilities)}
					oninput={(event) => updateList('must_have_capabilities', event.currentTarget.value)}
				></textarea>
			</label>
			<label class="field">
				<span>加分项</span>
				<textarea
					rows="3"
					value={listText(draft.preferred_capabilities)}
					oninput={(event) => updateList('preferred_capabilities', event.currentTarget.value)}
				></textarea>
			</label>
			<label class="field">
				<span>排除信号</span>
				<textarea
					rows="2"
					value={listText(draft.exclusion_signals)}
					oninput={(event) => updateList('exclusion_signals', event.currentTarget.value)}
				></textarea>
			</label>
			<label class="field">
				<span>检索关键词</span>
				<textarea
					rows="5"
					value={listText(keywordTerms)}
					oninput={(event) => updateQueryTerms(event.currentTarget.value)}
				></textarea>
			</label>
			<label class="field">
				<span>工作地点</span>
				<textarea
					rows="2"
					value={listText(draft.hard_constraints?.locations)}
					oninput={(event) => updateHardConstraintList('locations', event.currentTarget.value)}
				></textarea>
			</label>
			<label class="field">
				<span>经验要求</span>
				<textarea
					rows="2"
					value={draft.hard_constraints?.experience_requirement?.raw_text ?? ''}
					oninput={(event) => updateExperienceRequirement(event.currentTarget.value)}
				></textarea>
			</label>
			<label class="field">
				<span>学历要求</span>
				<textarea
					rows="2"
					value={draft.hard_constraints?.degree_requirement?.raw_text ?? ''}
					oninput={(event) => updateDegreeRequirement(event.currentTarget.value)}
				></textarea>
			</label>
			<label class="field">
				<span>目标公司</span>
				<textarea
					rows="2"
					value={listText(draft.preferences?.preferred_companies)}
					oninput={(event) =>
						updatePreferenceList('preferred_companies', event.currentTarget.value)}
				></textarea>
			</label>
			<label class="field">
				<span>目标领域</span>
				<textarea
					rows="2"
					value={listText(draft.preferences?.preferred_domains)}
					oninput={(event) => updatePreferenceList('preferred_domains', event.currentTarget.value)}
				></textarea>
			</label>
			<label class="field">
				<span>评分理由</span>
				<textarea
					rows="2"
					value={draft.scoring_rationale}
					oninput={(event) => updateText('scoring_rationale', event.currentTarget.value)}
				></textarea>
			</label>
		{:else}
			<div class="runtime-criteria-summary" aria-label="检索标准">
				<div class="runtime-criteria-row">
					<span>岗位</span>
					<p>{draft.job_title}</p>
				</div>
				<div class="runtime-criteria-row">
					<span>岗位摘要</span>
					<p>{draft.role_summary}</p>
				</div>
				{#if draft.title_anchor_terms.length}
					<div class="runtime-criteria-row">
						<span>职位锚点</span>
						<p>{draft.title_anchor_terms.join(' / ')}</p>
					</div>
				{/if}
				<div class="runtime-criteria-row">
					<span>必须满足</span>
					<ul class="compact-text-list">
						{#each draft.must_have_capabilities ?? [] as item (item)}
							<li>{item}</li>
						{/each}
					</ul>
				</div>
				{#if (draft.preferred_capabilities ?? []).length}
					<div class="runtime-criteria-row">
						<span>加分项</span>
						<ul class="compact-text-list">
							{#each draft.preferred_capabilities ?? [] as item (item)}
								<li>{item}</li>
							{/each}
						</ul>
					</div>
				{/if}
				{#if hardConstraintItems.length || preferenceItems.length}
					<div class="runtime-criteria-row">
						<span>筛选偏好</span>
						<ul class="compact-text-list">
							{#each [...hardConstraintItems, ...preferenceItems] as item (item)}
								<li>{item}</li>
							{/each}
						</ul>
					</div>
				{/if}
				{#if visibleKeywords.length}
					<div class="runtime-criteria-row">
						<span>检索关键词</span>
						<div class="keyword-chip-list">
							{#each visibleKeywords as term (term)}
								<span class="keyword-chip">{term}</span>
							{/each}
							{#if hiddenKeywordCount > 0}
								<span class="keyword-chip muted-chip">+{hiddenKeywordCount}</span>
							{/if}
						</div>
					</div>
				{/if}
				{#if draft.exclusion_signals?.length}
					<div class="runtime-criteria-row">
						<span>排除信号</span>
						<ul class="compact-text-list">
							{#each draft.exclusion_signals as item (item)}
								<li>{item}</li>
							{/each}
						</ul>
					</div>
				{/if}
				<div class="runtime-criteria-row">
					<span>评分理由</span>
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
