<script lang="ts">
	import { t } from '$lib/i18n'
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
		connected = false,
		onNavigate
	}: {
		onMinimize: () => void
		onMaximize: () => void
		onClose: () => void
		view: 'home' | 'settings'
		connected?: boolean
		onNavigate: () => void
	} = $props()
</script>

<header
	class="titlebar flex h-12 shrink-0 items-center border-b border-[var(--border)] bg-[var(--surface)] pl-4"
>
	<div class="flex min-w-0 items-center gap-2.5">
		<img src="./favicon.png" alt="" class="size-6" draggable="false" />
		<span class="truncate text-sm font-bold tracking-wide">VRC2T</span>
		<span
			class="status-dot {connected ? 'bg-emerald-500' : 'bg-zinc-400'}"
			title={connected ? $t('header.connected') : $t('header.disconnected')}
		></span>
		{#if view === 'settings'}
			<span class="text-sm text-[var(--text-subtle)]">/ {$t('header.settings')}</span>
		{/if}
	</div>
	<div class="flex-1"></div>
	<button type="button" class="titlebar-nav no-drag mr-1" onclick={onNavigate}>
		{#if view === 'settings'}
			<ArrowBackIcon height="1.125rem" />
			<span>{$t('header.back')}</span>
		{:else}
			<SettingsIcon height="1.125rem" />
			<span>{$t('header.settings')}</span>
		{/if}
	</button>
	<div class="no-drag flex h-full">
		<button
			type="button"
			class="titlebar-control"
			aria-label={$t('header.minimize')}
			onclick={onMinimize}
		>
			<RemoveIcon height="1.125rem" />
		</button>
		<button
			type="button"
			class="titlebar-control"
			aria-label={$t('header.maximize')}
			onclick={onMaximize}
		>
			<SquareOutlineIcon height="1rem" />
		</button>
		<button
			type="button"
			class="titlebar-control titlebar-control-close"
			aria-label={$t('header.close')}
			onclick={onClose}
		>
			<CloseIcon height="1.125rem" />
		</button>
	</div>
</header>
