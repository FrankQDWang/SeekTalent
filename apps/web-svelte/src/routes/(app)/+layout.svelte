<script lang="ts">
	import AuthenticatedLayout from '$lib/components/AuthenticatedLayout.svelte';

	let { children } = $props<{ children: import('svelte').Snippet }>();
</script>

<AuthenticatedLayout>
	{@render children()}
</AuthenticatedLayout>
