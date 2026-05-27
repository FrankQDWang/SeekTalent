<script lang="ts">
	type Props = {
		canStart?: boolean;
		starting?: boolean;
		startLabel?: string;
		startDescription?: string;
		startError?: string | null;
		onStart?: () => void;
	};

	let {
		canStart = false,
		starting = false,
		startLabel = '提取需求',
		startDescription = '系统会先拆解 JD，生成可确认的检索标准。',
		startError = null,
		onStart
	}: Props = $props();
</script>

<div class="canvas-ready">
	<div class="ready-icon" aria-hidden="true">AI</div>
	<h2>准备就绪</h2>
	<p>{startDescription}</p>
	{#if onStart && (canStart || starting || startError)}
		<button class="central-start" type="button" disabled={!canStart || starting} onclick={onStart}>
			{starting ? '处理中' : startLabel}
		</button>
	{/if}
	{#if startError}
		<p class="form-error" role="alert">{startError}</p>
	{/if}
</div>
