<script lang="ts">
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { safeErrorMessage } from '$lib/api/errors';
	import { bootstrapAdmin } from '$lib/api/workbench';
	import AuthShell from '$lib/components/AuthShell.svelte';

	let email = $state('');
	let displayName = $state('');
	let password = $state('');
	let errorMessage = $state('');
	let isSubmitting = $state(false);

	async function submitSetup(event: SubmitEvent) {
		event.preventDefault();
		errorMessage = '';
		isSubmitting = true;
		try {
			await bootstrapAdmin({ email, password, displayName });
			await goto(resolve('/login'));
		} catch (error) {
			errorMessage = safeErrorMessage(error, '管理员创建失败');
		} finally {
			isSubmitting = false;
		}
	}
</script>

<AuthShell eyebrow="初始设置" title="创建管理员">
	<form class="auth-form" onsubmit={submitSetup}>
		<label class="field" for="setup-email">
			<span>邮箱</span>
			<input
				id="setup-email"
				aria-label="邮箱"
				autocomplete="email"
				name="email"
				type="email"
				bind:value={email}
				required
			/>
		</label>
		<label class="field" for="setup-display-name">
			<span>显示名称</span>
			<input
				id="setup-display-name"
				aria-label="显示名称"
				autocomplete="name"
				name="displayName"
				bind:value={displayName}
				required
			/>
		</label>
		<label class="field" for="setup-password">
			<span>密码</span>
			<input
				id="setup-password"
				aria-label="密码"
				autocomplete="new-password"
				name="password"
				type="password"
				minlength="8"
				bind:value={password}
				required
			/>
		</label>
		{#if errorMessage}
			<p class="form-error" role="alert">{errorMessage}</p>
		{/if}
		<button class="primary-action" type="submit" disabled={isSubmitting}>
			{isSubmitting ? '创建中' : '创建管理员'}
		</button>
	</form>
</AuthShell>
