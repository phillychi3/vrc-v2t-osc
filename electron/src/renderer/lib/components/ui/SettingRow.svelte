<script lang="ts">
	import type { Snippet } from 'svelte'

	/** One labelled setting: title and help text on the left, control on the right. */
	let { title, description, children }: { title: string; description?: string; children: Snippet } =
		$props()
</script>

<div class="setting-row">
	<div class="min-w-0 flex-1">
		<div class="setting-title">{title}</div>
		{#if description}
			<div class="setting-description">{description}</div>
		{/if}
	</div>
	<div class="shrink-0">{@render children()}</div>
</div>
