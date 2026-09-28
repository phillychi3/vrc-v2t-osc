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
	const overlayCalibration = backend.overlayCalibration
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

<div class="app-shell flex h-screen min-h-0 flex-col overflow-hidden bg-[var(--bg)]">
	<AppHeader
		view={currentView}
		connected={$backendConnected}
		onNavigate={() => (currentView = currentView === 'home' ? 'settings' : 'home')}
		onMinimize={() => window.api?.window.minimize()}
		onMaximize={() => window.api?.window.toggleMaximize()}
		onClose={() => window.api?.window.close()}
	/>

	{#if $backendError}
		<div
			role="alert"
			class="flex shrink-0 items-center gap-3 border-b border-red-200 bg-red-50 px-5 py-3 text-sm text-red-700 dark:border-red-950 dark:bg-red-950/40 dark:text-red-200"
		>
			<span class="i-carbon-warning-alt size-5 shrink-0"></span>
			<span class="flex-1">{$backendError}</span>
			{#if $backendFailed}
				<button
					type="button"
					class="btn-accent h-10 px-4 text-sm"
					disabled={$backendRestarting}
					onclick={() => void backend.restart()}
				>
					{$backendRestarting ? '重新啟動中…' : '重新啟動後端'}
				</button>
			{:else}
				<button
					type="button"
					class="btn-ghost size-10 shrink-0 p-0 text-red-600 dark:text-red-300"
					aria-label="關閉訊息"
					onclick={() => backend.dismissError()}
				>
					<span class="i-carbon-close size-5"></span>
				</button>
			{/if}
		</div>
	{/if}

	{#if currentView === 'settings'}
		<main class="flex min-h-0 flex-1 overflow-hidden">
			<SettingsPanel
				settings={$backendState?.settings ?? null}
				devices={$audioDevices}
				translationProviders={$translationProviders}
				recording={Boolean($backendState && !['idle', 'error'].includes($backendState.recording))}
				speechModelStatus={$backendState?.models.speech?.status ?? 'not_loaded'}
				{refreshingDevices}
				saving={savingSettings}
				overlayStatus={$backendState?.overlay ?? null}
				onRefreshDevices={refreshDevices}
				onUpdate={updateSettings}
				onPreviewOverlay={() => window.api?.overlay.preview() ?? Promise.resolve()}
				onCalibrateOverlay={backend.calibrateOverlay}
				overlayCalibration={$overlayCalibration}
			/>
		</main>
	{:else}
		<main class="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
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
				onToggleOverlay={() =>
					void updateSettings({
						overlay: { enabled: !$backendState?.settings.overlay.enabled }
					})}
			/>
			<TranscriptPanel items={$transcripts} />
			<Composer disabled={!$backendConnected} onSend={backend.sendText} />
		</main>
	{/if}
</div>
