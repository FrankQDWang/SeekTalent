<script lang="ts">
	import { resolve } from '$app/paths';

	let {
		displayName,
		sessionCount,
		loggingOut = false,
		onLogout
	} = $props<{
		displayName: string;
		sessionCount: number;
		loggingOut?: boolean;
		onLogout: () => void;
	}>();

	const sessionLabel = $derived(`${sessionCount} 个会话`);
	const avatarInitial = $derived((displayName || 'User').slice(0, 1));
</script>

<header class="topbar">
	<div class="brand-cluster">
		<a href={resolve('/sessions')} class="brand-mark" aria-label="新建会话">+</a>
		<div>
			<strong>简历智能检索</strong>
			<span>本地招聘工作台</span>
		</div>
	</div>
	<div class="run-cluster">
		<span class="mono-label">工作台</span>
		<span class="source-dot" aria-hidden="true"></span>
		<span class="mono-label status-text">{sessionLabel}</span>
		<span class="topbar-divider" aria-hidden="true"></span>
		<span class="avatar">{avatarInitial}</span>
		<span>{displayName || 'User'}</span>
		<a href={resolve('/settings/sources')} class="utility-link">渠道</a>
		<span class="utility-separator" aria-hidden="true"></span>
		<button class="utility-link" type="button" disabled={loggingOut} onclick={onLogout}>退出</button
		>
	</div>
</header>

<style>
	.topbar {
		grid-column: 1 / -1;
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 20px;
		border-bottom: 1px solid var(--line);
		background: var(--surface);
		padding: 0 18px;
	}

	.brand-cluster,
	.run-cluster {
		display: flex;
		align-items: center;
	}

	.brand-cluster {
		gap: 12px;
	}

	.brand-mark {
		display: grid;
		width: 28px;
		height: 28px;
		place-items: center;
		border-radius: 5px;
		color: var(--accent);
		font-size: 20px;
		font-weight: 800;
	}

	.brand-mark:hover,
	.utility-link:hover {
		background: var(--accent-soft);
	}

	.brand-cluster div {
		display: grid;
		gap: 1px;
	}

	.brand-cluster strong {
		color: var(--text);
		font-size: 13px;
	}

	.brand-cluster span,
	.mono-label {
		font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
	}

	.brand-cluster span {
		color: var(--text-muted);
		font-size: 11px;
	}

	.run-cluster {
		gap: 10px;
		color: var(--text-soft);
		font-size: 12px;
	}

	.source-dot {
		width: 7px;
		height: 7px;
		border-radius: 50%;
		background: var(--line-strong);
	}

	.status-text {
		color: var(--text-soft);
	}

	.topbar-divider,
	.utility-separator {
		width: 1px;
		background: var(--line);
	}

	.topbar-divider {
		height: 22px;
	}

	.utility-separator {
		height: 12px;
	}

	.avatar {
		display: grid;
		width: 22px;
		height: 22px;
		place-items: center;
		border-radius: 50%;
		background: var(--accent-soft);
		color: var(--accent);
		font-size: 11px;
		font-weight: 800;
	}

	.utility-link {
		border: 0;
		background: transparent;
		color: var(--text-muted);
		font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
		font-size: 10.5px;
		font-weight: 500;
		padding: 0;
	}

	button.utility-link {
		cursor: pointer;
	}

	button.utility-link:disabled {
		cursor: not-allowed;
		opacity: 0.62;
	}

	@media (max-width: 760px) {
		.topbar {
			align-items: flex-start;
			flex-direction: column;
			height: auto;
			padding: 12px;
		}

		.run-cluster {
			flex-wrap: wrap;
		}
	}
</style>
