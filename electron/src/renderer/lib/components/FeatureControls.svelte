<script lang="ts">
	import type { BackendState } from '$shared/ipc'
	import { languageLabel } from '$lib/languages'

	let {
		state,
		translationAvailable = false,
		translationStatus,
		onToggleMicrophone,
		onToggleSpeaker,
		onToggleTranslation,
		onToggleOverlay
	}: {
		state: BackendState | null
		translationAvailable?: boolean
		translationStatus?: 'loading' | 'ready' | 'failed'
		onToggleMicrophone: () => void
		onToggleSpeaker: () => void
		onToggleTranslation: () => void
		onToggleOverlay: () => void
	} = $props()

	interface Tile {
		key: string
		title: string
		icon: string
		active: boolean
		busy: boolean
		disabled: boolean
		detail: string
		/** Shown instead of 開啟/關閉, e.g. while something is loading. */
		badge?: string
		onToggle: () => void
	}

	const modelReady = $derived(
		state?.backend === 'ready' && state?.models.speech?.status === 'ready'
	)
	const microphone = $derived(state?.recordings.microphone ?? 'idle')
	const speaker = $derived(state?.recordings.speaker ?? 'idle')
	const translation = $derived(state?.settings.translation)
	const overlay = $derived(state?.settings.overlay)
	const overlayStatus = $derived(state?.overlay?.status ?? 'disabled')

	function recordingDetail(value: string, listening: string, idle: string, ready: boolean): string {
		if (value === 'starting') return '正在啟動…'
		if (value === 'stopping') return '正在停止…'
		if (value === 'error') return '發生錯誤，按下重試'
		if (value === 'listening') return listening
		return ready ? idle : '語音模型載入中…'
	}

	const translationDetail = $derived.by(() => {
		if (!translationAvailable) return '沒有可用的翻譯方式'
		if (translationStatus === 'loading') return '翻譯模型載入中…'
		if (translationStatus === 'failed') return '翻譯模型載入失敗'
		if (!translation) return ''
		const own = languageLabel(
			translation.sourceLanguage === 'auto'
				? (state?.settings.speech.language ?? 'zh')
				: translation.sourceLanguage
		)
		return `${own} ⇄ ${languageLabel(translation.targetLanguage)}`
	})

	const overlayDetail = $derived.by(() => {
		if (!overlay?.enabled) return '在遊戲內顯示字幕'
		if (overlayStatus === 'connected')
			return overlay.watchMode && overlay.anchor !== 'head' ? '已連線' : '已連線 SteamVR'
		if (overlayStatus === 'waiting') return '等待 SteamVR 啟動…'
		return state?.overlay?.message ?? '無法連線 SteamVR'
	})

	const tiles = $derived<Tile[]>([
		{
			key: 'microphone',
			title: '我的語音',
			icon: 'i-carbon-microphone-filled',
			active: microphone === 'listening',
			busy: microphone === 'starting' || microphone === 'stopping',
			disabled: !modelReady || microphone === 'starting' || microphone === 'stopping',
			detail: recordingDetail(microphone, '', '', modelReady),
			onToggle: onToggleMicrophone
		},
		{
			key: 'speaker',
			title: '他人語音',
			icon: 'i-carbon-volume-up-filled',
			active: speaker === 'listening',
			busy: speaker === 'starting' || speaker === 'stopping',
			disabled: !modelReady || speaker === 'starting' || speaker === 'stopping',
			detail: recordingDetail(speaker, '', '', modelReady),
			onToggle: onToggleSpeaker
		},
		{
			key: 'translation',
			title: '翻譯',
			icon: 'i-carbon-translate',
			active: translation?.enabled ?? false,
			busy: translationStatus === 'loading',
			disabled: !translationAvailable || translationStatus === 'loading',
			detail: translationDetail,
			badge: translationStatus === 'loading' ? '載入中' : undefined,
			onToggle: onToggleTranslation
		},
		{
			key: 'overlay',
			title: 'VR 字幕',
			icon: 'i-carbon-closed-caption-filled',
			active: overlay?.enabled ?? false,
			busy: false,
			disabled: !overlay,
			detail: overlayDetail,
			onToggle: onToggleOverlay
		}
	])
</script>

<section class="feature-grid-container shrink-0 p-4" aria-label="主要功能">
	<div class="feature-grid">
		{#each tiles as tile (tile.key)}
			<button
				type="button"
				class="feature-action {tile.active ? 'feature-action-active' : ''}"
				disabled={tile.disabled}
				aria-pressed={tile.active}
				onclick={tile.onToggle}
			>
				<span class="feature-action-icon">
					<span class="{tile.busy ? 'i-carbon-circle-dash animate-spin' : tile.icon} size-6"></span>
				</span>
				<span class="min-w-0 flex-1">
					<span class="flex items-center justify-between gap-2">
						<strong class="truncate text-base font-bold">{tile.title}</strong>
						<span class="feature-action-state">{tile.badge ?? (tile.active ? '開啟' : '關閉')}</span
						>
					</span>
					<span class="mt-1 line-clamp-2 block text-sm leading-5 text-[var(--text-subtle)]"
						>{tile.detail}</span
					>
				</span>
			</button>
		{/each}
	</div>
</section>
