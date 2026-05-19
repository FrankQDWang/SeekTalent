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
			errorMessage = safeErrorMessage(error, 'Could not create admin.');
		} finally {
			isSubmitting = false;
		}
	}
</script>

<AuthShell eyebrow="Initial setup" title="Create admin">
	<form class="auth-form" onsubmit={submitSetup}>
		<label class="field" for="setup-email">
			<span>Email</span>
			<input
				id="setup-email"
				aria-label="Email"
				autocomplete="email"
				name="email"
				type="email"
				bind:value={email}
				required
			/>
		</label>
		<label class="field" for="setup-display-name">
			<span>Display name</span>
			<input
				id="setup-display-name"
				aria-label="Display name"
				autocomplete="name"
				name="displayName"
				bind:value={displayName}
				required
			/>
		</label>
		<label class="field" for="setup-password">
			<span>Password</span>
			<input
				id="setup-password"
				aria-label="Password"
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
			{isSubmitting ? 'Creating admin' : 'Create admin'}
		</button>
	</form>
</AuthShell>
