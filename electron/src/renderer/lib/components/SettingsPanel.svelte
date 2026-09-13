<script lang="ts">
	import { KSelect } from '@ikun-ui/select'
	import { KSwitch } from '@ikun-ui/switch'
	import type {
		AudioDevice,
		BackendSettings,
		BackendSettingsPatch,
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
		standalone = false,
		onRefreshDevices,
		onUpdate
	}: {
		settings: BackendSettings | null
		devices?: AudioDevice[]
		translationProviders?: TranslationProviderInfo[]
		recording?: boolean
		speechModelStatus?: 'not_loaded' | 'loading' | 'ready' | 'failed'
		refreshingDevices?: boolean
		saving?: boolean
		standalone?: boolean
		onRefreshDevices: () => Promise<void>
		onUpdate: (patch: BackendSettingsPatch) => Promise<void>
	} = $props()

	const microphoneDeviceOptions = $derived(
		devices
			.filter((device) => device.source === 'microphone')
			.map((device) => ({
				id: device.id,
				label: `${device.name}${device.isDefault ? '（預設）' : ''}`
			}))
	)
	const speakerDeviceOptions = $derived(
		devices
			.filter((device) => device.source === 'speaker')
			.map((device) => ({
				id: device.id,
				label: `${device.name}${device.isDefault ? '（預設）' : ''}`
			}))
	)
	const selectedMicrophoneDevice = $derived(
		microphoneDeviceOptions.find((device) => device.id === settings?.audio.deviceId) ?? {
			id: settings?.audio.deviceId ?? '',
			label: settings?.audio.deviceId ?? ''
		}
	)
	const selectedSpeakerDevice = $derived(
		speakerDeviceOptions.find((device) => device.id === settings?.audio.speakerDeviceId) ?? {
			id: settings?.audio.speakerDeviceId ?? '',
			label: settings?.audio.speakerDeviceId ?? ''
		}
	)

	let host = $state('127.0.0.1')
	let port = $state('9000')
	let parameter = $state('/avatar/parameters/v2t_sync_emo')
	let speechModel = $state('large-v3-turbo')
	let translationProvider = $state('transformers')
	let translationModel = $state('facebook/nllb-200-distilled-600M')
	let sourceLanguage = $state('zh')
	let targetLanguage = $state('en')
	let translationEndpoint = $state('http://127.0.0.1:5000')
	let translationApiKey = $state('')
	let deeplPlan = $state<'free' | 'pro'>('free')
	let deeplApiKey = $state('')
	const languageOptions = [
		{ value: 'auto', label: '自動偵測' },
		{ value: 'zh', label: '繁體中文' },
		{ value: 'en', label: 'English' },
		{ value: 'ja', label: '日本語' },
		{ value: 'ko', label: '한국어' },
		{ value: 'fr', label: 'Français' },
		{ value: 'de', label: 'Deutsch' },
		{ value: 'es', label: 'Español' }
	]
	const speechModelOptions = [
		{ value: 'tiny', label: 'Tiny（最快）' },
		{ value: 'base', label: 'Base' },
		{ value: 'small', label: 'Small' },
		{ value: 'medium', label: 'Medium' },
		{ value: 'large-v3', label: 'Large v3（最精準）' },
		{ value: 'large-v3-turbo', label: 'Large v3 Turbo（建議）' }
	]
	const translationModelOptions = [
		{ value: 'facebook/nllb-200-distilled-600M', label: 'NLLB 200 Distilled 600M（建議）' },
		{ value: 'facebook/nllb-200-distilled-1.3B', label: 'NLLB 200 Distilled 1.3B（高品質）' }
	]

	$effect(() => {
		if (!settings) return
		host = settings.osc.host
		port = String(settings.osc.port)
		parameter = settings.emotion.parameter
		speechModel = settings.speech.model
		translationProvider = settings.translation.provider
		translationModel = settings.translation.model
		sourceLanguage = settings.translation.sourceLanguage
		targetLanguage = settings.translation.targetLanguage
		translationEndpoint = settings.translation.endpoint
		translationApiKey = settings.translation.apiKey
		deeplPlan = settings.translation.deeplPlan
		deeplApiKey = settings.translation.deeplApiKey
	})

	async function saveAdvanced(): Promise<void> {
		await onUpdate({
			osc: { host, port: Number(port) },
			emotion: { parameter }
		})
	}

	async function saveTranslation(): Promise<void> {
		await onUpdate({
			translation: {
				provider: translationProvider,
				model: translationModel,
				sourceLanguage,
				targetLanguage,
				endpoint: translationEndpoint,
				apiKey: translationApiKey,
				deeplPlan,
				deeplApiKey
			}
		})
	}

	function selectTranslationProvider(event: Event): void {
		translationProvider = (event.currentTarget as HTMLSelectElement).value
		if (translationProvider === 'transformers' && sourceLanguage === 'auto') sourceLanguage = 'zh'
	}
</script>

<aside
	class="{standalone
		? 'settings-page-panel'
		: 'settings-rail'} flex h-full shrink-0 flex-col bg-zinc-50/75 dark:bg-zinc-900/35"
>
	<div class="min-h-0 flex-1 overflow-y-auto p-5">
		<section>
			<h3 class="settings-heading">音訊</h3>
			<div class="mb-1.5 flex items-center justify-between">
				<label class="field-label !mb-0" for="audio-device">麥克風裝置</label>
				<button
					type="button"
					disabled={refreshingDevices || recording}
					onclick={() => void onRefreshDevices()}
					class="btn-ghost h-7 px-2 text-[11px] text-accent-600"
				>
					<span class="i-carbon-renew size-3 {refreshingDevices ? 'animate-spin' : ''}"></span>
					{refreshingDevices ? '更新中…' : '重新整理'}
				</button>
			</div>
			<KSelect
				value={selectedMicrophoneDevice}
				dataList={microphoneDeviceOptions}
				labelKey="label"
				valueKey="id"
				key="id"
				placeholder={refreshingDevices ? '載入中…' : '沒有可用的輸入裝置'}
				disabled={!settings || saving || recording || microphoneDeviceOptions.length === 0}
				cls="w-full"
				clsSelect="w-full"
				attrs={{ id: 'audio-device', 'aria-label': '輸入裝置' }}
				on:updateValue={(event) => {
					const option = event.detail as { id?: unknown }
					if (typeof option?.id === 'string') void onUpdate({ audio: { deviceId: option.id } })
				}}
			/>
			<div class="mt-3">
				<label class="field-label" for="speaker-device">喇叭裝置</label>
				<KSelect
					value={selectedSpeakerDevice}
					dataList={speakerDeviceOptions}
					labelKey="label"
					valueKey="id"
					key="id"
					placeholder={refreshingDevices ? '載入中…' : '沒有可用的喇叭回放裝置'}
					disabled={!settings || saving || recording || speakerDeviceOptions.length === 0}
					cls="w-full"
					clsSelect="w-full"
					attrs={{ id: 'speaker-device', 'aria-label': '喇叭裝置' }}
					on:updateValue={(event) => {
						const option = event.detail as { id?: unknown }
						if (typeof option?.id === 'string')
							void onUpdate({ audio: { speakerDeviceId: option.id } })
					}}
				/>
			</div>
			<p class="field-help">錄音時需先停止才能更換裝置</p>
			<div class="flex items-center justify-between gap-4 py-2.5">
				<div class="min-w-0 text-sm font-medium text-zinc-800 dark:text-zinc-200">
					啟動時自動錄音
				</div>
				<KSwitch
					value={settings?.audio.autoStart ?? false}
					disabled={!settings || saving}
					attrs={{ 'aria-label': '啟動時自動錄音' }}
					on:updateValue={(event) => void onUpdate({ audio: { autoStart: Boolean(event.detail) } })}
				/>
			</div>
		</section>

		<section class="settings-section">
			<h3 class="settings-heading">模型</h3>
			<div>
				<label class="field-label" for="speech-model">語音辨識模型</label>
				<select
					id="speech-model"
					bind:value={speechModel}
					class="field-control"
					disabled={!settings || saving || recording || speechModelStatus === 'loading'}
					onchange={() => void onUpdate({ speech: { model: speechModel } })}
				>
					{#each speechModelOptions as model (model.value)}
						<option value={model.value}>{model.label}</option>
					{/each}
				</select>
				<p class="field-help">
					{speechModelStatus === 'loading'
						? '模型載入中，完成後才能再次切換'
						: '較大模型更精準，但載入、記憶體與辨識時間也會增加'}
				</p>
			</div>
		</section>

		<section class="settings-section">
			<h3 class="settings-heading">翻譯</h3>
			<div class="flex items-center justify-between gap-4 py-2.5">
				<div class="min-w-0">
					<div class="text-sm font-medium text-zinc-800 dark:text-zinc-200">啟用翻譯</div>
					<div class="mt-0.5 text-xs leading-relaxed text-zinc-500">設定會套用到後續的翻譯要求</div>
				</div>
				<KSwitch
					value={settings?.translation.enabled ?? false}
					disabled={!settings || saving || translationProviders.length === 0}
					attrs={{ 'aria-label': '啟用翻譯' }}
					on:updateValue={(event) =>
						void onUpdate({ translation: { enabled: Boolean(event.detail) } })}
				/>
			</div>

			<div class="mt-2 space-y-3">
				<div>
					<label class="field-label" for="translation-provider">翻譯方式</label>
					<select
						id="translation-provider"
						bind:value={translationProvider}
						onchange={selectTranslationProvider}
						class="field-control"
						disabled={!settings || saving || translationProviders.length === 0}
					>
						{#each translationProviders as provider (provider.id)}
							<option value={provider.id}>{provider.label}{provider.local ? '（本機）' : ''}</option
							>
						{/each}
					</select>
					{#if translationProviders.length === 0}
						<p class="field-help">後端沒有提供可用的翻譯方式</p>
					{/if}
				</div>

				{#if translationProvider === 'transformers'}
					<div>
						<label class="field-label" for="translation-model">翻譯模型</label>
						<select
							id="translation-model"
							bind:value={translationModel}
							class="field-control"
							disabled={!settings || saving}
						>
							{#each translationModelOptions as model (model.value)}
								<option value={model.value}>{model.label}</option>
							{/each}
						</select>
						<p class="field-help">1.3B 需要更多記憶體，並會在第一次使用時下載</p>
					</div>
				{/if}

				<div class="grid grid-cols-2 gap-3">
					<div>
						<label class="field-label" for="translation-source">你的語言</label>
						<select id="translation-source" bind:value={sourceLanguage} class="field-control">
							{#each languageOptions as language (language.value)}
								{#if translationProvider !== 'transformers' || language.value !== 'auto'}
									<option value={language.value}>{language.label}</option>
								{/if}
							{/each}
						</select>
					</div>
					<div>
						<label class="field-label" for="translation-target">目標語言</label>
						<select id="translation-target" bind:value={targetLanguage} class="field-control">
							{#each languageOptions.filter((language) => language.value !== 'auto') as language (language.value)}
								<option value={language.value}>{language.label}</option>
							{/each}
						</select>
					</div>
				</div>
				<p class="field-help">
					麥克風會從「你的語言」翻成「目標語言」；喇叭則從「目標語言」翻回「你的語言」。
				</p>

				{#if translationProvider === 'libretranslate'}
					<div>
						<label class="field-label" for="translation-endpoint">API Endpoint</label>
						<input
							id="translation-endpoint"
							bind:value={translationEndpoint}
							class="field-control font-mono text-xs"
						/>
					</div>
					<div>
						<label class="field-label" for="translation-api-key">API Key（選填）</label>
						<input
							id="translation-api-key"
							type="password"
							autocomplete="off"
							bind:value={translationApiKey}
							class="field-control font-mono text-xs"
						/>
					</div>
				{/if}

				{#if translationProvider === 'deepl'}
					<div>
						<label class="field-label" for="deepl-plan">DeepL API 方案</label>
						<select id="deepl-plan" bind:value={deeplPlan} class="field-control">
							<option value="free">API Free</option>
							<option value="pro">API Pro</option>
						</select>
					</div>
					<div>
						<label class="field-label" for="deepl-api-key">DeepL API Key</label>
						<input
							id="deepl-api-key"
							type="password"
							autocomplete="off"
							bind:value={deeplApiKey}
							class="field-control font-mono text-xs"
							placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx:fx"
						/>
						<p class="field-help">Free 金鑰通常以 :fx 結尾；金鑰只儲存在本機設定。</p>
					</div>
				{/if}

				<button
					type="button"
					disabled={!settings || saving || translationProviders.length === 0}
					onclick={() => void saveTranslation()}
					class="btn-secondary h-9 w-full px-3 text-xs"
				>
					<span
						class="{saving ? 'i-carbon-circle-dash animate-spin' : 'i-carbon-checkmark'} size-3.5"
					></span>
					{saving ? '套用中…' : '套用翻譯設定'}
				</button>
			</div>
		</section>

		<section class="settings-section">
			<h3 class="settings-heading">VRChat OSC</h3>
			<div class="flex items-center justify-between gap-4 py-2.5">
				<div class="min-w-0">
					<div class="text-sm font-medium text-zinc-800 dark:text-zinc-200">傳送文字</div>
					<div class="mt-0.5 text-xs leading-relaxed text-zinc-500">
						將最終逐字稿送到 VRChat Chatbox
					</div>
				</div>
				<KSwitch
					value={settings?.osc.enabled ?? false}
					disabled={!settings || saving}
					attrs={{ 'aria-label': '傳送文字' }}
					on:updateValue={(event) => void onUpdate({ osc: { enabled: Boolean(event.detail) } })}
				/>
			</div>
			<div class="flex items-center justify-between gap-4 py-2.5">
				<div class="min-w-0">
					<div class="text-sm font-medium text-zinc-800 dark:text-zinc-200">情緒辨識</div>
					<div class="mt-0.5 text-xs leading-relaxed text-zinc-500">辨識結果會寫入 Avatar 參數</div>
				</div>
				<KSwitch
					value={settings?.emotion.enabled ?? false}
					disabled={!settings || saving}
					attrs={{ 'aria-label': '情緒辨識' }}
					on:updateValue={(event) => void onUpdate({ emotion: { enabled: Boolean(event.detail) } })}
				/>
			</div>
		</section>

		<details class="settings-section group">
			<summary
				class="cursor-pointer list-none text-xs font-semibold text-zinc-600 dark:text-zinc-300"
				>進階 OSC <span class="float-right transition-transform group-open:rotate-90">›</span
				></summary
			>
			<div class="mt-4 space-y-3">
				<div>
					<label class="field-label" for="osc-host">Host</label>
					<input id="osc-host" bind:value={host} class="field-control font-mono" />
				</div>
				<div>
					<label class="field-label" for="osc-port">Port</label>
					<input
						id="osc-port"
						bind:value={port}
						inputmode="numeric"
						class="field-control font-mono"
					/>
				</div>
				<div>
					<label class="field-label" for="emotion-parameter">情緒參數</label>
					<input
						id="emotion-parameter"
						bind:value={parameter}
						class="field-control font-mono text-xs"
					/>
				</div>
				<button
					type="button"
					disabled={!settings || saving}
					onclick={() => void saveAdvanced()}
					class="btn-secondary h-9 w-full px-3 text-xs"
				>
					<span
						class="{saving ? 'i-carbon-circle-dash animate-spin' : 'i-carbon-checkmark'} size-3.5"
					></span>
					{saving ? '套用中…' : '套用進階設定'}
				</button>
			</div>
		</details>
	</div>
</aside>
