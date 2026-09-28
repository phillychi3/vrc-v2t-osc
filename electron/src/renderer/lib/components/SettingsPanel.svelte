<script lang="ts">
	import VrOverlaySettings from './VrOverlaySettings.svelte'
	import Select from './ui/Select.svelte'
	import SettingRow from './ui/SettingRow.svelte'
	import Switch from './ui/Switch.svelte'
	import type { OverlayCalibration } from '$lib/stores/backend'
	import { UI_SCALES, uiScale, type UiScale } from '$lib/stores/ui'
	import { LANGUAGES } from '$lib/languages'
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
		{ id: 'audio', label: '音訊裝置', icon: 'i-carbon-headphones' },
		{ id: 'speech', label: '語音辨識', icon: 'i-carbon-machine-learning-model' },
		{ id: 'translation', label: '翻譯', icon: 'i-carbon-translate' },
		{ id: 'overlay', label: 'VR 字幕', icon: 'i-carbon-closed-caption-filled' },
		{ id: 'vrchat', label: 'VRChat', icon: 'i-carbon-chat' },
		{ id: 'interface', label: '介面', icon: 'i-carbon-text-scale' }
	]
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
				hint: device.isDefault ? '系統預設' : undefined
			}))
		// Keep a saved device that is currently unplugged visible and selected.
		if (current && !options.some((option) => option.value === current))
			options.unshift({ value: current, label: '所選裝置目前無法使用', hint: undefined })
		return options
	}

	const speechModelOptions = [
		{ value: 'auto', label: '自動', hint: 'CPU 使用 Base，有 CUDA 時使用 Large v3 Turbo' },
		{ value: 'tiny', label: 'Tiny', hint: '最快，準確度最低' },
		{ value: 'base', label: 'Base', hint: 'CPU 也能流暢使用' },
		{ value: 'small', label: 'Small' },
		{ value: 'medium', label: 'Medium', hint: '建議使用 GPU' },
		{ value: 'large-v3', label: 'Large v3', hint: '最精準，需要 GPU' },
		{ value: 'large-v3-turbo', label: 'Large v3 Turbo', hint: '精準又快，需要 GPU' }
	]

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
			hint: provider.local ? '在這台電腦上執行，不需網路' : '需要網路與 API 金鑰'
		}))
	)
	const sourceOptions = $derived(
		LANGUAGES.filter((language) => translation?.provider !== 'onnx' || language.value !== 'auto')
	)
	const targetOptions = LANGUAGES.filter((language) => language.value !== 'auto')

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
		{saving ? '儲存中…' : label}
	</button>
{/snippet}

<div class="flex h-full min-h-0 w-full">
	<nav class="w-56 shrink-0 space-y-1 border-r border-[var(--border)] p-3" aria-label="設定分類">
		{#each sections as section (section.id)}
			<button
				type="button"
				class="settings-nav-item {activeSection === section.id ? 'settings-nav-item-active' : ''}"
				aria-current={activeSection === section.id ? 'true' : undefined}
				onclick={() => showSection(section.id)}
			>
				<span class="{section.icon} size-5 shrink-0"></span>
				{section.label}
			</button>
		{/each}
	</nav>

	<div class="min-h-0 flex-1 overflow-y-auto" bind:this={scroller} onscroll={trackSection}>
		<div class="mx-auto w-full max-w-3xl space-y-5 p-6 pb-24">
			<section id="settings-audio" class="settings-card">
				<div class="flex items-start justify-between gap-4">
					<h2 class="settings-card-title">
						<span class="i-carbon-headphones size-5 text-[var(--color-accent-500)]"></span>音訊裝置
					</h2>
					<button
						type="button"
						disabled={refreshingDevices || recording}
						onclick={() => void onRefreshDevices()}
						class="btn-secondary h-10 px-4 text-sm"
						aria-label="重新整理裝置"
					>
						<span class="i-carbon-renew size-4 {refreshingDevices ? 'animate-spin' : ''}"></span>
						{refreshingDevices ? '更新中…' : '重新整理'}
					</button>
				</div>
				<div class="grid gap-4 md:grid-cols-2">
					<div>
						<label class="field-label" for="audio-device">麥克風（我的語音）</label>
						<Select
							id="audio-device"
							ariaLabel="麥克風裝置"
							value={settings?.audio.deviceId ?? 'default'}
							options={deviceOptions('microphone', settings?.audio.deviceId)}
							disabled={!settings || saving || recording || refreshingDevices}
							onChange={(deviceId) => void onUpdate({ audio: { deviceId } })}
						/>
					</div>
					<div>
						<label class="field-label" for="speaker-device">喇叭（他人語音）</label>
						<Select
							id="speaker-device"
							ariaLabel="喇叭裝置"
							value={settings?.audio.speakerDeviceId ?? 'speaker:default'}
							options={deviceOptions('speaker', settings?.audio.speakerDeviceId)}
							disabled={!settings || saving || recording || refreshingDevices}
							onChange={(speakerDeviceId) => void onUpdate({ audio: { speakerDeviceId } })}
						/>
					</div>
				</div>
				{#if recording}
					<p class="field-help">錄音中無法更換裝置，請先在主畫面關閉語音辨識。</p>
				{/if}
				<div class="mt-3">
					<SettingRow title="啟動時自動開始辨識" description="開啟 VRC2T 後自動開始辨識你的語音">
						<Switch
							label="啟動時自動開始辨識"
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
					></span>語音辨識
				</h2>
				<label class="field-label" for="speech-model">辨識模型</label>
				<Select
					id="speech-model"
					ariaLabel="語音辨識模型"
					value={settings?.speech.model ?? 'auto'}
					options={speechModelOptions}
					disabled={!settings || saving || recording || speechModelStatus === 'loading'}
					onChange={(model) => void onUpdate({ speech: { model } })}
				/>
				<p class="field-help">
					{speechModelStatus === 'loading'
						? '模型載入中，完成後才能再次切換。'
						: '較大的模型更準確，但需要更多記憶體與時間；第一次使用可能需要下載。錄音中無法切換。'}
				</p>
			</section>

			<section id="settings-translation" class="settings-card">
				<h2 class="settings-card-title">
					<span class="i-carbon-translate size-5 text-[var(--color-accent-500)]"></span>翻譯
				</h2>
				<SettingRow title="啟用翻譯" description="把你的話翻成對方的語言，也把聽到的內容翻回來">
					<Switch
						label="啟用翻譯"
						checked={translation?.enabled ?? false}
						disabled={!settings || saving || translationProviders.length === 0}
						onChange={(enabled) => void updateTranslation({ enabled })}
					/>
				</SettingRow>

				<div class="mt-3 space-y-4">
					<div>
						<label class="field-label" for="translation-provider">翻譯方式</label>
						<Select
							id="translation-provider"
							ariaLabel="翻譯方式"
							value={translation?.provider ?? 'onnx'}
							options={providerOptions}
							placeholder="沒有可用的翻譯方式"
							disabled={!settings || saving || translationProviders.length === 0}
							onChange={selectProvider}
						/>
						{#if translation?.provider === 'onnx'}
							<p class="field-help">使用 NLLB 600M 本機模型，第一次使用時會自動下載。</p>
						{/if}
					</div>

					<div class="flex items-end gap-2">
						<div class="min-w-0 flex-1">
							<label class="field-label" for="translation-source">你的語言</label>
							<Select
								id="translation-source"
								ariaLabel="你的語言"
								value={translation?.sourceLanguage ?? 'zh'}
								options={sourceOptions}
								disabled={!settings || saving}
								onChange={(sourceLanguage) => void updateTranslation({ sourceLanguage })}
							/>
						</div>
						<button
							type="button"
							class="btn-ghost size-11 shrink-0 rounded-xl"
							aria-label="交換語言"
							title="交換語言"
							disabled={!settings || saving || translation?.sourceLanguage === 'auto'}
							onclick={swapLanguages}
						>
							<span class="i-carbon-arrows-horizontal size-5"></span>
						</button>
						<div class="min-w-0 flex-1">
							<label class="field-label" for="translation-target">對方的語言</label>
							<Select
								id="translation-target"
								ariaLabel="對方的語言"
								value={translation?.targetLanguage ?? 'en'}
								options={targetOptions}
								disabled={!settings || saving}
								onChange={(targetLanguage) => void updateTranslation({ targetLanguage })}
							/>
						</div>
					</div>
					<p class="field-help !mt-2">
						你說的話會翻成「對方的語言」送到 VRChat；聽到的內容會翻回「你的語言」。
					</p>

					{#if translation?.provider === 'libretranslate'}
						<div class="grid gap-4 md:grid-cols-2">
							<div>
								<label class="field-label" for="translation-endpoint">API 位址</label>
								<input
									id="translation-endpoint"
									bind:value={translationEndpoint}
									class="field-control font-mono text-sm"
								/>
							</div>
							<div>
								<label class="field-label" for="translation-api-key">API 金鑰（選填）</label>
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
							'儲存連線設定',
							() =>
								void updateTranslation({ endpoint: translationEndpoint, apiKey: translationApiKey })
						)}
					{/if}

					{#if translation?.provider === 'deepl'}
						<div>
							<div class="field-label">DeepL API 方案</div>
							<div class="grid grid-cols-2 gap-2" role="radiogroup" aria-label="DeepL API 方案">
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
							<label class="field-label" for="deepl-api-key">DeepL API 金鑰</label>
							<input
								id="deepl-api-key"
								type="password"
								autocomplete="off"
								bind:value={deeplApiKey}
								class="field-control font-mono text-sm"
								placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx:fx"
							/>
							<p class="field-help">Free 金鑰通常以 :fx 結尾；金鑰只儲存在這台電腦。</p>
						</div>
						{@render saveButton('儲存金鑰', () => void updateTranslation({ deeplApiKey }))}
					{/if}
				</div>
			</section>

			<section id="settings-overlay" class="settings-card">
				<h2 class="settings-card-title">
					<span class="i-carbon-closed-caption-filled size-5 text-[var(--color-accent-500)]"
					></span>VR 字幕
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
				<SettingRow title="送到聊天框" description="把辨識（或翻譯）後的文字送到 VRChat 的 Chatbox">
					<Switch
						label="送到聊天框"
						checked={settings?.osc.enabled ?? false}
						disabled={!settings || saving}
						onChange={(enabled) => void onUpdate({ osc: { enabled } })}
					/>
				</SettingRow>
				<SettingRow title="情緒辨識" description="分析你說話的情緒，寫入 Avatar 參數驅動表情">
					<Switch
						label="情緒辨識"
						checked={settings?.emotion.enabled ?? false}
						disabled={!settings || saving}
						onChange={(enabled) => void onUpdate({ emotion: { enabled } })}
					/>
				</SettingRow>

				<details class="group mt-3 rounded-xl border border-[var(--border)]">
					<summary
						class="flex min-h-12 cursor-pointer list-none items-center justify-between px-4 text-sm font-semibold text-[var(--text-muted)]"
					>
						進階 OSC 設定
						<span class="i-carbon-chevron-down size-4 transition-transform group-open:rotate-180"
						></span>
					</summary>
					<div class="space-y-4 border-t border-[var(--border)] p-4">
						<div class="grid gap-4 md:grid-cols-[1fr_8rem]">
							<div>
								<label class="field-label" for="osc-host">主機</label>
								<input id="osc-host" bind:value={host} class="field-control font-mono" />
							</div>
							<div>
								<label class="field-label" for="osc-port">連接埠</label>
								<input
									id="osc-port"
									bind:value={port}
									inputmode="numeric"
									class="field-control font-mono"
								/>
							</div>
						</div>
						<div>
							<label class="field-label" for="emotion-parameter">情緒參數位址</label>
							<input
								id="emotion-parameter"
								bind:value={parameter}
								class="field-control font-mono text-sm"
							/>
						</div>
						{@render saveButton(
							'儲存進階設定',
							() => void onUpdate({ osc: { host, port: Number(port) }, emotion: { parameter } })
						)}
					</div>
				</details>
			</section>

			<section id="settings-interface" class="settings-card">
				<h2 class="settings-card-title">
					<span class="i-carbon-text-scale size-5 text-[var(--color-accent-500)]"></span>介面
				</h2>
				<div class="field-label">介面大小</div>
				<div class="grid grid-cols-3 gap-2" role="radiogroup" aria-label="介面大小">
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
							<span style="font-size: {scale.rootPx / 16}rem">{scale.label}</span>
						</button>
					{/each}
				</div>
				<p class="field-help">在 VR 中透過桌面視窗操作時，放大介面會比較容易閱讀和點選。</p>
			</section>
		</div>
	</div>
</div>
