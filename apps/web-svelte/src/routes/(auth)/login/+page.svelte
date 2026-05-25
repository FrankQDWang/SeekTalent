<script lang="ts">
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { safeErrorMessage } from '$lib/api/errors';
	import { login } from '$lib/api/workbench';
	import AuthShell from '$lib/components/AuthShell.svelte';

	let email = $state('');
	let password = $state('');
	let errorMessage = $state('');
	let isSubmitting = $state(false);

	async function submitLogin(event: SubmitEvent) {
		event.preventDefault();
		errorMessage = '';
		isSubmitting = true;
		try {
			await login({ email, password });
			await goto(resolve('/sessions'));
		} catch (error) {
			errorMessage = safeErrorMessage(error, '登录失败');
		} finally {
			isSubmitting = false;
		}
	}
</script>

<AuthShell eyebrow="工作台访问" title="登录">
	<form class="auth-form" onsubmit={submitLogin}>
		<label class="field" for="login-email">
			<span>邮箱</span>
			<input
				id="login-email"
				aria-label="邮箱"
				autocomplete="email"
				name="email"
				type="email"
				bind:value={email}
				required
			/>
		</label>
		<label class="field" for="login-password">
			<span>密码</span>
			<input
				id="login-password"
				aria-label="密码"
				autocomplete="current-password"
				name="password"
				type="password"
				bind:value={password}
				required
			/>
		</label>
		{#if errorMessage}
			<p class="form-error" role="alert">{errorMessage}</p>
		{/if}
		<button class="primary-action" type="submit" disabled={isSubmitting}>
			{isSubmitting ? '登录中' : '登录'}
		</button>
	</form>
</AuthShell>
