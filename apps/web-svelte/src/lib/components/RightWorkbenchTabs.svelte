<script lang="ts">
	import type { Snippet } from 'svelte';

	let { activeTab, onActiveTabChange, notesPanel, nodePanel } = $props<{
		activeTab: 'notes' | 'node';
		onActiveTabChange: (tab: 'notes' | 'node') => void;
		notesPanel: Snippet;
		nodePanel: Snippet;
	}>();
</script>

<div class="right-workbench-tabs">
	<div class="right-tab-list" role="tablist" aria-label="Workbench detail panels">
		<button
			type="button"
			role="tab"
			aria-selected={activeTab === 'notes'}
			aria-controls="running-notes-panel"
			id="running-notes-tab"
			onclick={() => onActiveTabChange('notes')}
		>
			运行笔记
		</button>
		<button
			type="button"
			role="tab"
			aria-selected={activeTab === 'node'}
			aria-controls="node-detail-panel"
			id="node-detail-tab"
			onclick={() => onActiveTabChange('node')}
		>
			节点详情
		</button>
	</div>
	<div
		id="running-notes-panel"
		role="tabpanel"
		aria-labelledby="running-notes-tab"
		hidden={activeTab !== 'notes'}
	>
		{@render notesPanel()}
	</div>
	<div
		id="node-detail-panel"
		role="tabpanel"
		aria-labelledby="node-detail-tab"
		hidden={activeTab !== 'node'}
	>
		{@render nodePanel()}
	</div>
</div>
