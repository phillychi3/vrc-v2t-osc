<script lang="ts">
	import type { BackendState } from '$shared/ipc'

	let {
		state,
		translationAvailable = false,
		translationStatus,
		onToggleMicrophone,
		onToggleSpeaker,
		onToggleTranslation
	}: {
		state: BackendState | null
		translationAvailable?: boolean
		translationStatus?: 'loading' | 'ready' | 'failed'
		onToggleMicrophone: () => void
		onToggleSpeaker: () => void
		onToggleTranslation: () => void
	} = $props()

	const microphoneActive = $derived(state?.recordings.microphone === 'listening')
	const speakerActive = $derived(state?.recordings.speaker === 'listening')
	const microphoneChanging = $derived(
		state?.recordings.microphone === 'starting' || state?.recordings.microphone === 'stopping'
	)
	const speakerChanging = $derived(
		state?.recordings.speaker === 'starting' || state?.recordings.speaker === 'stopping'
	)
	const microphoneReady = $derived(
		state?.backend === 'ready' && state?.models.speech?.status === 'ready'
	)
	const translationEnabled = $derived(state?.settings.translation.enabled ?? false)
</script>

<section
	class="grid shrink-0 gap-3 border-b border-zinc-200/80 p-4 md:grid-cols-3 dark:border-zinc-800"
>
	<button
		type="button"
		class="feature-action {microphoneActive ? 'feature-action-active' : ''}"
		disabled={!microphoneReady || microphoneChanging}
		aria-pressed={microphoneActive}
		onclick={onToggleMicrophone}
	>
		<span class="feature-action-icon">
			<span
				class="{microphoneChanging
					? 'i-carbon-circle-dash animate-spin'
					: 'i-carbon-microphone-filled'} size-5"
			></span>
		</span>
		<span class="min-w-0 flex-1 text-left">
			<strong class="block text-sm">語音轉文字</strong>
			<span class="mt-1 block text-xs leading-5 text-zinc-500 dark:text-zinc-400">
				{microphoneChanging
					? '正在切換麥克風狀態…'
					: microphoneActive
						? '正在聆聽麥克風，按下此區域即可停止。'
						: microphoneReady
							? ''
							: '語音模型載入完成後即可使用。'}
			</span>
		</span>
	</button>

	<button
		type="button"
		class="feature-action {speakerActive ? 'feature-action-active' : ''}"
		disabled={!microphoneReady || speakerChanging}
		aria-pressed={speakerActive}
		onclick={onToggleSpeaker}
	>
		<span class="feature-action-icon"><span class="i-carbon-volume-up-filled size-5"></span></span>
		<span class="min-w-0 flex-1 text-left">
			<strong class="block text-sm">喇叭轉文字</strong>
		</span>
		<span class="feature-action-state">{speakerActive ? '使用中' : '啟用'}</span>
	</button>

	<button
		type="button"
		class="feature-action {translationEnabled ? 'feature-action-active' : ''}"
		disabled={!translationAvailable || translationStatus === 'loading'}
		aria-pressed={translationEnabled}
		onclick={onToggleTranslation}
	>
		<span class="feature-action-icon"
			><span
				class="{translationStatus === 'loading'
					? 'i-carbon-circle-dash animate-spin'
					: 'i-carbon-translate'} size-5"
			></span></span
		>
		<span class="min-w-0 flex-1 text-left">
			<strong class="block text-sm">翻譯</strong>
		</span>
		<span class="feature-action-state">
			{translationStatus === 'loading'
				? '載入中'
				: translationStatus === 'failed'
					? '失敗'
					: translationEnabled
						? '使用中'
						: '啟用'}
		</span>
	</button>
</section>
