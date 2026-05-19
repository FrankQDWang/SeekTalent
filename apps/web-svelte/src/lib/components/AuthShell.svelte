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
		border: 1px solid #dedbd0;
		border-radius: 8px;
		background: #fffefb;
		padding: 28px;
	}

	.section-label {
		margin: 0;
		color: #7d7568;
		font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
		font-size: 11px;
		font-weight: 700;
		letter-spacing: 0;
		text-transform: uppercase;
	}

	h1 {
		margin: 0;
		color: #25231f;
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
		color: #5e584f;
		font-size: 12px;
		font-weight: 700;
	}

	:global(.field input) {
		min-height: 38px;
		border: 1px solid #d4d0c5;
		border-radius: 6px;
		background: #fffefb;
		color: #25231f;
		padding: 0 10px;
	}

	:global(.primary-action) {
		min-height: 38px;
		border: 1px solid #3c5a4a;
		border-radius: 6px;
		background: #3c5a4a;
		color: #fffefb;
		font-weight: 800;
		padding: 0 14px;
	}

	:global(.primary-action:disabled) {
		cursor: not-allowed;
		opacity: 0.62;
	}
</style>
