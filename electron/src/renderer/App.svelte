<script lang="ts">
	import { onMount } from 'svelte'
	import AppHeader from '$lib/components/AppHeader.svelte'
	import Composer from '$lib/components/Composer.svelte'
	import FeatureControls from '$lib/components/FeatureControls.svelte'
	import SettingsPanel from '$lib/components/SettingsPanel.svelte'
	import TranscriptPanel from '$lib/components/TranscriptPanel.svelte'
	import { backend } from '$lib/stores/backend'
	import type { BackendSettingsPatch } from '$shared/ipc'

	let savingSettings = $state(false)
	let refreshingDevices = $state(false)
	let currentView = $state<'home' | 'settings'>('home')

	const backendState = backend.state
	const backendError = backend.error
	const backendFailed = backend.failed
	const backendRestarting = backend.restarting
	const transcripts = backend.transcripts
	const audioDevices = backend.audioDevices
	const translationProviders = backend.translationProviders
	const translationStatuses = backend.translationStatuses
	const backendConnected = backend.connected

	onMount(() => {
		let disconnect: (() => void) | undefined
		let disposed = false
		void backend.connect().then((cleanup) => {
			if (disposed) cleanup()
			else disconnect = cleanup
		})
		return () => {
			disposed = true
			disconnect?.()
		}
	})

	async function updateSettings(patch: BackendSettingsPatch): Promise<void> {
		savingSettings = true
		try {
			await backend.updateSettings(patch)
		} catch {
			// The store owns the user-facing error state.
		} finally {
			savingSettings = false
		}
	}

	async function refreshDevices(): Promise<void> {
		refreshingDevices = true
		try {
			await backend.refreshAudioDevices()
		} catch {
			// The store owns the user-facing error state.
		} finally {
			refreshingDevices = false
		}
	}
</script>

<div class="app-shell flex h-screen min-h-0 flex-col overflow-hidden bg-white dark:bg-zinc-950">
	<AppHeader
		view={currentView}
		onNavigate={() => (currentView = currentView === 'home' ? 'settings' : 'home')}
		onMinimize={() => window.api?.window.minimize()}
		onMaximize={() => window.api?.window.toggleMaximize()}
		onClose={() => window.api?.window.close()}
	/>

	{#if $backendError}
		<div
			role="alert"
			class="flex shrink-0 items-center gap-3 border-b border-red-200 bg-red-50 px-5 py-2.5 text-xs text-red-700 dark:border-red-950 dark:bg-red-950/35 dark:text-red-300"
		>
			<span class="grid size-5 place-items-center rounded-full bg-red-100 font-bold dark:bg-red-950"
				>!</span
			>
			<span class="flex-1">{$backendError}</span>
			{#if $backendFailed}
				<button
					type="button"
					class="btn-accent min-h-10 px-4"
					disabled={$backendRestarting}
					onclick={() => void backend.restart()}
				>
					{$backendRestarting ? '重新啟動中…' : '重新啟動後端'}
				</button>
			{/if}
		</div>
	{/if}

	{#if currentView === 'settings'}
		<main class="flex min-h-0 flex-1 overflow-hidden bg-zinc-50/75 dark:bg-zinc-900/35">
			<SettingsPanel
				standalone
				settings={$backendState?.settings ?? null}
				devices={$audioDevices}
				translationProviders={$translationProviders}
				recording={Boolean($backendState && !['idle', 'error'].includes($backendState.recording))}
				speechModelStatus={$backendState?.models.speech?.status ?? 'not_loaded'}
				{refreshingDevices}
				saving={savingSettings}
				onRefreshDevices={refreshDevices}
				onUpdate={updateSettings}
			/>
		</main>
	{:else}
		<main class="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-white dark:bg-zinc-950">
			<FeatureControls
				state={$backendState}
				translationAvailable={$translationProviders.some(
					(provider) => provider.id === $backendState?.settings.translation.provider
				)}
				translationStatus={$backendState
					? $translationStatuses[$backendState.settings.translation.provider]
					: undefined}
				onToggleMicrophone={() => void backend.toggleRecording('microphone')}
				onToggleSpeaker={() => void backend.toggleRecording('speaker')}
				onToggleTranslation={() =>
					void updateSettings({
						translation: { enabled: !$backendState?.settings.translation.enabled }
					})}
			/>
			<TranscriptPanel items={$transcripts} />
			<Composer disabled={!$backendConnected} onSend={backend.sendText} />
		</main>
	{/if}
</div>
