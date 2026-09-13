<script lang="ts">
	import CloseIcon from '@iconify-svelte/material-symbols/close'
	import ArrowBackIcon from '@iconify-svelte/material-symbols/arrow-back'
	import RemoveIcon from '@iconify-svelte/material-symbols/remove'
	import SettingsIcon from '@iconify-svelte/material-symbols/settings-outline'
	import SquareOutlineIcon from '@iconify-svelte/material-symbols/square-outline'

	let {
		onMinimize,
		onMaximize,
		onClose,
		view,
		onNavigate
	}: {
		onMinimize: () => void
		onMaximize: () => void
		onClose: () => void
		view: 'home' | 'settings'
		onNavigate: () => void
	} = $props()
</script>

<header
	class="titlebar flex h-10 shrink-0 items-center border-b border-zinc-200/80 bg-white/75 pl-4 backdrop-blur-xl dark:border-zinc-800 dark:bg-zinc-950/75"
>
	<div class="flex min-w-0 items-center gap-2 text-xs font-medium text-zinc-500">
		<span class="truncate">VRC Voice to Text</span>
	</div>
	<div class="flex-1"></div>
	<button type="button" class="titlebar-nav no-drag" onclick={onNavigate}>
		{#if view === 'settings'}
			<ArrowBackIcon height="1rem" />
			<span>返回主畫面</span>
		{:else}
			<SettingsIcon height="1rem" />
			<span>設定</span>
		{/if}
	</button>
	<div class="no-drag flex h-full">
		<button type="button" class="titlebar-control" aria-label="最小化" onclick={onMinimize}>
			<RemoveIcon height="1rem" />
		</button>
		<button type="button" class="titlebar-control" aria-label="最大化" onclick={onMaximize}>
			<SquareOutlineIcon height="0.875rem" />
		</button>
		<button
			type="button"
			class="titlebar-control titlebar-control-close"
			aria-label="關閉"
			onclick={onClose}
		>
			<CloseIcon height="1rem" />
		</button>
	</div>
</header>
