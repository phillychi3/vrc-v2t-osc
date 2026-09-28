<script lang="ts">
	import VrOverlaySettings from './VrOverlaySettings.svelte'
	import Select from './ui/Select.svelte'
	import SettingRow from './ui/SettingRow.svelte'
	import Switch from './ui/Switch.svelte'
	import type { OverlayCalibration } from '$lib/stores/backend'
	import { UI_SCALES, uiScale, type UiScale } from '$lib/stores/ui'
	import { languageOptions } from '$lib/languages'
	import { systemLocale, t } from '$lib/i18n'
	import { LOCALES, LOCALE_NAMES, type LanguagePreference } from '$shared/i18n'
	import type {
		AudioDevice,
		BackendSettings,
		BackendSettingsPatch,
		BackendState,
		TranslationProviderInfo
	} from '$shared/ipc'

	let {
		settings,
		devices = [],
		translationProviders = [],
		recording = false,
		speechModelStatus = 'not_loaded',
		refreshingDevices = false,
		saving = false,
		overlayStatus = null,
		onRefreshDevices,
		onUpdate,
		onPreviewOverlay,
		onCalibrateOverlay,
		overlayCalibration = null
	}: {
		settings: BackendSettings | null
		devices?: AudioDevice[]
		translationProviders?: TranslationProviderInfo[]
		recording?: boolean
		speechModelStatus?: 'not_loaded' | 'loading' | 'ready' | 'failed'
		refreshingDevices?: boolean
		saving?: boolean
		overlayStatus?: BackendState['overlay'] | null
		onRefreshDevices: () => Promise<void>
		onUpdate: (patch: BackendSettingsPatch) => Promise<void>
		onPreviewOverlay: () => Promise<void>
		onCalibrateOverlay: () => Promise<void>
		overlayCalibration?: OverlayCalibration | null
	} = $props()

	const sections = [
		{ id: 'audio', icon: 'i-carbon-headphones' },
		{ id: 'speech', icon: 'i-carbon-machine-learning-model' },
		{ id: 'translation', icon: 'i-carbon-translate' },
		{ id: 'overlay', icon: 'i-carbon-closed-caption-filled' },
		{ id: 'vrchat', icon: 'i-carbon-chat' },
		{ id: 'interface', icon: 'i-carbon-text-scale' }
	] as const
	let activeSection = $state('audio')
	let scroller = $state<HTMLDivElement>()

	function showSection(id: string): void {
		activeSection = id
		document.getElementById(`settings-${id}`)?.scrollIntoView({ behavior: 'smooth' })
	}

	/** Highlights the last section whose top has scrolled past the top edge. */
	function trackSection(): void {
		if (!scroller) return
		const atBottom = scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - 4
		if (atBottom) {
			activeSection = sections[sections.length - 1].id
			return
		}
		const top = scroller.getBoundingClientRect().top + 96
		for (const section of sections) {
			const element = document.getElementById(`settings-${section.id}`)
			if (element && element.getBoundingClientRect().top <= top) activeSection = section.id
		}
	}

	function deviceOptions(source: AudioDevice['source'], current: string | undefined) {
		const options = devices
			.filter((device) => device.source === source)
			.map((device) => ({
				value: device.id,
				label: device.name,
				hint: device.isDefault ? $t('audio.systemDefault') : undefined
			}))
		// Keep a saved device that is currently unplugged visible and selected.
		if (current && !options.some((option) => option.value === current))
			options.unshift({ value: current, label: $t('audio.unavailable'), hint: undefined })
		return options
	}

	const speechModelOptions = $derived([
		{ value: 'auto', label: $t('speech.auto'), hint: $t('speech.autoHint') },
		{ value: 'tiny', label: 'Tiny', hint: $t('speech.tinyHint') },
		{ value: 'base', label: 'Base', hint: $t('speech.baseHint') },
		{ value: 'small', label: 'Small' },
		{ value: 'medium', label: 'Medium', hint: $t('speech.mediumHint') },
		{ value: 'large-v3', label: 'Large v3', hint: $t('speech.largeHint') },
		{ value: 'large-v3-turbo', label: 'Large v3 Turbo', hint: $t('speech.turboHint') }
	])
	const interfaceLanguageOptions = $derived([
		{
			value: 'auto' as LanguagePreference,
			label: $t('interface.languageAuto', { language: LOCALE_NAMES[systemLocale] })
		},
		...LOCALES.map((locale) => ({
			value: locale as LanguagePreference,
			label: LOCALE_NAMES[locale]
		}))
	])

	let host = $state('127.0.0.1')
	let port = $state('9000')
	let parameter = $state('/avatar/parameters/v2t_sync_emo')
	let translationEndpoint = $state('http://127.0.0.1:5000')
	let translationApiKey = $state('')
	let deeplApiKey = $state('')

	$effect(() => {
		if (!settings) return
		host = settings.osc.host
		port = String(settings.osc.port)
		parameter = settings.emotion.parameter
		translationEndpoint = settings.translation.endpoint
		translationApiKey = settings.translation.apiKey
		deeplApiKey = settings.translation.deeplApiKey
	})

	const translation = $derived(settings?.translation)
	const providerOptions = $derived(
		translationProviders.map((provider) => ({
			value: provider.id,
			label: provider.label,
			hint: provider.local ? $t('translation.localHint') : $t('translation.remoteHint')
		}))
	)
	// The local model cannot detect the language by itself.
	const sourceOptions = $derived(languageOptions($t, translation?.provider !== 'onnx'))
	const targetOptions = $derived(languageOptions($t, false))

	function updateTranslation(patch: Partial<BackendSettings['translation']>): Promise<void> {
		return onUpdate({ translation: patch })
	}

	function selectProvider(provider: string): void {
		// The local model cannot detect the language by itself.
		const fixSource = provider === 'onnx' && translation?.sourceLanguage === 'auto'
		void updateTranslation({ provider, ...(fixSource ? { sourceLanguage: 'zh' } : {}) })
	}

	function swapLanguages(): void {
		if (!translation || translation.sourceLanguage === 'auto') return
		void updateTranslation({
			sourceLanguage: translation.targetLanguage,
			targetLanguage: translation.sourceLanguage
		})
	}
</script>

{#snippet saveButton(label: string, onclick: () => void)}
	<button
		type="button"
		disabled={!settings || saving}
		{onclick}
		class="btn-secondary h-11 px-5 text-sm"
	>
		<span class="{saving ? 'i-carbon-circle-dash animate-spin' : 'i-carbon-checkmark'} size-4"
		></span>
		{saving ? $t('common.saving') : label}
	</button>
{/snippet}

<div class="flex h-full min-h-0 w-full">
	<nav
		class="w-56 shrink-0 space-y-1 border-r border-[var(--border)] p-3"
		aria-label={$t('settings.nav')}
	>
		{#each sections as section (section.id)}
			<button
				type="button"
				class="settings-nav-item {activeSection === section.id ? 'settings-nav-item-active' : ''}"
				aria-current={activeSection === section.id ? 'true' : undefined}
				onclick={() => showSection(section.id)}
			>
				<span class="{section.icon} size-5 shrink-0"></span>
				{$t(`settings.section.${section.id}`)}
			</button>
		{/each}
	</nav>

	<div class="min-h-0 flex-1 overflow-y-auto" bind:this={scroller} onscroll={trackSection}>
		<div class="mx-auto w-full max-w-3xl space-y-5 p-6 pb-24">
			<section id="settings-audio" class="settings-card">
				<div class="flex items-start justify-between gap-4">
					<h2 class="settings-card-title">
						<span class="i-carbon-headphones size-5 text-[var(--color-accent-500)]"></span>{$t(
							'settings.section.audio'
						)}
					</h2>
					<button
						type="button"
						disabled={refreshingDevices || recording}
						onclick={() => void onRefreshDevices()}
						class="btn-secondary h-10 px-4 text-sm"
						aria-label={$t('audio.refreshAria')}
					>
						<span class="i-carbon-renew size-4 {refreshingDevices ? 'animate-spin' : ''}"></span>
						{refreshingDevices ? $t('audio.refreshing') : $t('audio.refresh')}
					</button>
				</div>
				<div class="grid gap-4 md:grid-cols-2">
					<div>
						<label class="field-label" for="audio-device">{$t('audio.microphone')}</label>
						<Select
							id="audio-device"
							ariaLabel={$t('audio.microphoneAria')}
							value={settings?.audio.deviceId ?? 'default'}
							options={deviceOptions('microphone', settings?.audio.deviceId)}
							disabled={!settings || saving || recording || refreshingDevices}
							onChange={(deviceId) => void onUpdate({ audio: { deviceId } })}
						/>
					</div>
					<div>
						<label class="field-label" for="speaker-device">{$t('audio.speaker')}</label>
						<Select
							id="speaker-device"
							ariaLabel={$t('audio.speakerAria')}
							value={settings?.audio.speakerDeviceId ?? 'speaker:default'}
							options={deviceOptions('speaker', settings?.audio.speakerDeviceId)}
							disabled={!settings || saving || recording || refreshingDevices}
							onChange={(speakerDeviceId) => void onUpdate({ audio: { speakerDeviceId } })}
						/>
					</div>
				</div>
				{#if recording}
					<p class="field-help">{$t('audio.recordingLocked')}</p>
				{/if}
				<div class="mt-3">
					<SettingRow title={$t('audio.autoStart')} description={$t('audio.autoStartHelp')}>
						<Switch
							label={$t('audio.autoStart')}
							checked={settings?.audio.autoStart ?? false}
							disabled={!settings || saving}
							onChange={(autoStart) => void onUpdate({ audio: { autoStart } })}
						/>
					</SettingRow>
				</div>
			</section>

			<section id="settings-speech" class="settings-card">
				<h2 class="settings-card-title">
					<span class="i-carbon-machine-learning-model size-5 text-[var(--color-accent-500)]"
					></span>{$t('settings.section.speech')}
				</h2>
				<label class="field-label" for="speech-model">{$t('speech.model')}</label>
				<Select
					id="speech-model"
					ariaLabel={$t('speech.modelAria')}
					value={settings?.speech.model ?? 'auto'}
					options={speechModelOptions}
					disabled={!settings || saving || recording || speechModelStatus === 'loading'}
					onChange={(model) => void onUpdate({ speech: { model } })}
				/>
				<p class="field-help">
					{speechModelStatus === 'loading' ? $t('speech.loading') : $t('speech.help')}
				</p>
			</section>

			<section id="settings-translation" class="settings-card">
				<h2 class="settings-card-title">
					<span class="i-carbon-translate size-5 text-[var(--color-accent-500)]"></span>{$t(
						'settings.section.translation'
					)}
				</h2>
				<SettingRow title={$t('translation.enable')} description={$t('translation.enableHelp')}>
					<Switch
						label={$t('translation.enable')}
						checked={translation?.enabled ?? false}
						disabled={!settings || saving || translationProviders.length === 0}
						onChange={(enabled) => void updateTranslation({ enabled })}
					/>
				</SettingRow>

				<div class="mt-3 space-y-4">
					<div>
						<label class="field-label" for="translation-provider"
							>{$t('translation.provider')}</label
						>
						<Select
							id="translation-provider"
							ariaLabel={$t('translation.provider')}
							value={translation?.provider ?? 'onnx'}
							options={providerOptions}
							placeholder={$t('translation.noProvider')}
							disabled={!settings || saving || translationProviders.length === 0}
							onChange={selectProvider}
						/>
						{#if translation?.provider === 'onnx'}
							<p class="field-help">{$t('translation.onnxHelp')}</p>
						{/if}
					</div>

					<div class="flex items-end gap-2">
						<div class="min-w-0 flex-1">
							<label class="field-label" for="translation-source">{$t('translation.source')}</label>
							<Select
								id="translation-source"
								ariaLabel={$t('translation.source')}
								value={translation?.sourceLanguage ?? 'zh'}
								options={sourceOptions}
								disabled={!settings || saving}
								onChange={(sourceLanguage) => void updateTranslation({ sourceLanguage })}
							/>
						</div>
						<button
							type="button"
							class="btn-ghost size-11 shrink-0 rounded-xl"
							aria-label={$t('translation.swap')}
							title={$t('translation.swap')}
							disabled={!settings || saving || translation?.sourceLanguage === 'auto'}
							onclick={swapLanguages}
						>
							<span class="i-carbon-arrows-horizontal size-5"></span>
						</button>
						<div class="min-w-0 flex-1">
							<label class="field-label" for="translation-target">{$t('translation.target')}</label>
							<Select
								id="translation-target"
								ariaLabel={$t('translation.target')}
								value={translation?.targetLanguage ?? 'en'}
								options={targetOptions}
								disabled={!settings || saving}
								onChange={(targetLanguage) => void updateTranslation({ targetLanguage })}
							/>
						</div>
					</div>
					<p class="field-help !mt-2">
						{$t('translation.help')}
					</p>

					{#if translation?.provider === 'libretranslate'}
						<div class="grid gap-4 md:grid-cols-2">
							<div>
								<label class="field-label" for="translation-endpoint"
									>{$t('translation.endpoint')}</label
								>
								<input
									id="translation-endpoint"
									bind:value={translationEndpoint}
									class="field-control font-mono text-sm"
								/>
							</div>
							<div>
								<label class="field-label" for="translation-api-key"
									>{$t('translation.apiKey')}</label
								>
								<input
									id="translation-api-key"
									type="password"
									autocomplete="off"
									bind:value={translationApiKey}
									class="field-control font-mono text-sm"
								/>
							</div>
						</div>
						{@render saveButton(
							$t('translation.saveConnection'),
							() =>
								void updateTranslation({ endpoint: translationEndpoint, apiKey: translationApiKey })
						)}
					{/if}

					{#if translation?.provider === 'deepl'}
						<div>
							<div class="field-label">{$t('translation.deeplPlan')}</div>
							<div
								class="grid grid-cols-2 gap-2"
								role="radiogroup"
								aria-label={$t('translation.deeplPlan')}
							>
								{#each [{ value: 'free', label: 'API Free' }, { value: 'pro', label: 'API Pro' }] as const as plan (plan.value)}
									<button
										type="button"
										role="radio"
										aria-checked={translation.deeplPlan === plan.value}
										class="btn-secondary h-11 text-sm {translation.deeplPlan === plan.value
											? '!border-[var(--color-accent-500)] !text-[var(--color-accent-500)]'
											: ''}"
										disabled={saving}
										onclick={() => void updateTranslation({ deeplPlan: plan.value })}
									>
										{plan.label}
									</button>
								{/each}
							</div>
						</div>
						<div>
							<label class="field-label" for="deepl-api-key">{$t('translation.deeplKey')}</label>
							<input
								id="deepl-api-key"
								type="password"
								autocomplete="off"
								bind:value={deeplApiKey}
								class="field-control font-mono text-sm"
								placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx:fx"
							/>
							<p class="field-help">{$t('translation.deeplHelp')}</p>
						</div>
						{@render saveButton(
							$t('translation.saveKey'),
							() => void updateTranslation({ deeplApiKey })
						)}
					{/if}
				</div>
			</section>

			<section id="settings-overlay" class="settings-card">
				<h2 class="settings-card-title">
					<span class="i-carbon-closed-caption-filled size-5 text-[var(--color-accent-500)]"
					></span>{$t('settings.section.overlay')}
				</h2>
				<VrOverlaySettings
					overlay={settings?.overlay}
					status={overlayStatus}
					{saving}
					onUpdate={(patch) => onUpdate({ overlay: patch })}
					onPreview={onPreviewOverlay}
					calibration={overlayCalibration}
					onCalibrate={onCalibrateOverlay}
				/>
			</section>

			<section id="settings-vrchat" class="settings-card">
				<h2 class="settings-card-title">
					<span class="i-carbon-chat size-5 text-[var(--color-accent-500)]"></span>VRChat
				</h2>
				<SettingRow title={$t('vrchat.osc')} description={$t('vrchat.oscHelp')}>
					<Switch
						label={$t('vrchat.osc')}
						checked={settings?.osc.enabled ?? false}
						disabled={!settings || saving}
						onChange={(enabled) => void onUpdate({ osc: { enabled } })}
					/>
				</SettingRow>
				<SettingRow title={$t('vrchat.emotion')} description={$t('vrchat.emotionHelp')}>
					<Switch
						label={$t('vrchat.emotion')}
						checked={settings?.emotion.enabled ?? false}
						disabled={!settings || saving}
						onChange={(enabled) => void onUpdate({ emotion: { enabled } })}
					/>
				</SettingRow>

				<details class="group mt-3 rounded-xl border border-[var(--border)]">
					<summary
						class="flex min-h-12 cursor-pointer list-none items-center justify-between px-4 text-sm font-semibold text-[var(--text-muted)]"
					>
						{$t('vrchat.advanced')}
						<span class="i-carbon-chevron-down size-4 transition-transform group-open:rotate-180"
						></span>
					</summary>
					<div class="space-y-4 border-t border-[var(--border)] p-4">
						<div class="grid gap-4 md:grid-cols-[1fr_8rem]">
							<div>
								<label class="field-label" for="osc-host">{$t('vrchat.host')}</label>
								<input id="osc-host" bind:value={host} class="field-control font-mono" />
							</div>
							<div>
								<label class="field-label" for="osc-port">{$t('vrchat.port')}</label>
								<input
									id="osc-port"
									bind:value={port}
									inputmode="numeric"
									class="field-control font-mono"
								/>
							</div>
						</div>
						<div>
							<label class="field-label" for="emotion-parameter">{$t('vrchat.parameter')}</label>
							<input
								id="emotion-parameter"
								bind:value={parameter}
								class="field-control font-mono text-sm"
							/>
						</div>
						{@render saveButton(
							$t('vrchat.saveAdvanced'),
							() => void onUpdate({ osc: { host, port: Number(port) }, emotion: { parameter } })
						)}
					</div>
				</details>
			</section>

			<section id="settings-interface" class="settings-card">
				<h2 class="settings-card-title">
					<span class="i-carbon-text-scale size-5 text-[var(--color-accent-500)]"></span>{$t(
						'settings.section.interface'
					)}
				</h2>
				<label class="field-label" for="interface-language">{$t('interface.language')}</label>
				<Select
					id="interface-language"
					ariaLabel={$t('interface.language')}
					value={settings?.ui?.language ?? 'auto'}
					options={interfaceLanguageOptions}
					disabled={!settings || saving}
					onChange={(language) => void onUpdate({ ui: { language } })}
				/>
				<div class="field-label mt-5">{$t('interface.scale')}</div>
				<div class="grid grid-cols-3 gap-2" role="radiogroup" aria-label={$t('interface.scale')}>
					{#each UI_SCALES as scale (scale.value)}
						<button
							type="button"
							role="radio"
							aria-checked={$uiScale === scale.value}
							class="btn-secondary h-12 {$uiScale === scale.value
								? '!border-[var(--color-accent-500)] !text-[var(--color-accent-500)]'
								: ''}"
							onclick={() => uiScale.set(scale.value as UiScale)}
						>
							<span style="font-size: {scale.rootPx / 16}rem">{$t(scale.label)}</span>
						</button>
					{/each}
				</div>
				<p class="field-help">{$t('interface.scaleHelp')}</p>
			</section>
		</div>
	</div>
</div>
