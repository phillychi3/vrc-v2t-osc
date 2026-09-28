<script lang="ts">
	import { t } from '$lib/i18n'
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
		/** Shown instead of on/off, e.g. while something is loading. */
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
		if (value === 'starting') return $t('home.starting')
		if (value === 'stopping') return $t('home.stopping')
		if (value === 'error') return $t('home.recordingError')
		if (value === 'listening') return listening
		return ready ? idle : $t('home.modelLoading')
	}

	const translationDetail = $derived.by(() => {
		if (!translationAvailable) return $t('home.translationUnavailable')
		if (translationStatus === 'loading') return $t('home.translationLoading')
		if (translationStatus === 'failed') return $t('home.translationFailed')
		if (!translation) return ''
		const own = languageLabel(
			translation.sourceLanguage === 'auto'
				? (state?.settings.speech.language ?? 'zh')
				: translation.sourceLanguage
		)
		return `${own} ⇄ ${languageLabel(translation.targetLanguage)}`
	})

	const overlayDetail = $derived.by(() => {
		if (!overlay?.enabled) return $t('home.overlayOff')
		if (overlayStatus === 'connected')
			return overlay.watchMode && overlay.anchor !== 'head'
				? $t('home.overlayWatch')
				: $t('home.overlayConnected')
		if (overlayStatus === 'waiting') return $t('home.overlayWaiting')
		return state?.overlay?.message ?? $t('home.overlayUnavailable')
	})

	const tiles = $derived<Tile[]>([
		{
			key: 'microphone',
			title: $t('home.microphone'),
			icon: 'i-carbon-microphone-filled',
			active: microphone === 'listening',
			busy: microphone === 'starting' || microphone === 'stopping',
			disabled: !modelReady || microphone === 'starting' || microphone === 'stopping',
			detail: recordingDetail(microphone, '', '', modelReady),
			onToggle: onToggleMicrophone
		},
		{
			key: 'speaker',
			title: $t('home.speaker'),
			icon: 'i-carbon-volume-up-filled',
			active: speaker === 'listening',
			busy: speaker === 'starting' || speaker === 'stopping',
			disabled: !modelReady || speaker === 'starting' || speaker === 'stopping',
			detail: recordingDetail(speaker, '', '', modelReady),
			onToggle: onToggleSpeaker
		},
		{
			key: 'translation',
			title: $t('home.translation'),
			icon: 'i-carbon-translate',
			active: translation?.enabled ?? false,
			busy: translationStatus === 'loading',
			disabled: !translationAvailable || translationStatus === 'loading',
			detail: translationDetail,
			badge: translationStatus === 'loading' ? $t('common.loading') : undefined,
			onToggle: onToggleTranslation
		},
		{
			key: 'overlay',
			title: $t('home.overlay'),
			icon: 'i-carbon-closed-caption-filled',
			active: overlay?.enabled ?? false,
			busy: false,
			disabled: !overlay,
			detail: overlayDetail,
			onToggle: onToggleOverlay
		}
	])
</script>

<section class="feature-grid-container shrink-0 p-4" aria-label={$t('home.aria')}>
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
						<span class="feature-action-state"
							>{tile.badge ?? (tile.active ? $t('common.on') : $t('common.off'))}</span
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
