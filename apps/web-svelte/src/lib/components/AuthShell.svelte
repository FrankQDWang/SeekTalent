<script lang="ts">
	let { eyebrow, title, children } = $props<{
		eyebrow: string;
		title: string;
		children: import('svelte').Snippet;
	}>();
</script>

<main class="auth-page">
	<section class="auth-panel">
		<p class="section-label">{eyebrow}</p>
		<h1>{title}</h1>
		{@render children()}
	</section>
</main>

<style>
	.auth-page {
		display: grid;
		min-height: 100vh;
		place-items: center;
		padding: 28px;
	}

	.auth-panel {
		display: grid;
		width: min(420px, 100%);
		gap: 22px;
		border: 1px solid var(--line-soft);
		border-radius: 8px;
		background: var(--surface-strong);
		padding: 28px;
	}

	.section-label {
		margin: 0;
		color: var(--text-muted);
		font-family: var(--mono);
		font-size: 11px;
		font-weight: 700;
		letter-spacing: 0;
		text-transform: uppercase;
	}

	h1 {
		margin: 0;
		color: var(--text);
		font-size: 21px;
		line-height: 1.16;
		letter-spacing: 0;
	}

	:global(.auth-form) {
		display: grid;
		gap: 15px;
	}

	:global(.field) {
		display: grid;
		gap: 7px;
	}

	:global(.field span) {
		color: var(--text-soft);
		font-size: 12px;
		font-weight: 700;
	}

	:global(.field input) {
		min-height: 38px;
		border: 1px solid var(--line);
		border-radius: 6px;
		background: var(--surface-strong);
		color: var(--text);
		padding: 0 10px;
	}

	:global(.primary-action) {
		min-height: 38px;
		border: 1px solid var(--accent);
		border-radius: 6px;
		background: var(--accent);
		color: var(--accent-ink);
		font-weight: 800;
		padding: 0 14px;
	}

	:global(.primary-action:disabled) {
		cursor: not-allowed;
		opacity: 0.62;
	}
</style>
