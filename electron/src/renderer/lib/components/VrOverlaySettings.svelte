<script lang="ts">
	import { KSwitch } from '@ikun-ui/switch'
	import type { BackendState, OverlaySettings } from '$shared/ipc'
	import type { OverlayCalibration } from '$lib/stores/backend'
	import {
		IDENTITY_BASIS,
		OVERLAY_PRESETS,
		matchOverlayPreset,
		placementOf,
		type OverlayPresetId
	} from '$shared/overlay-presets'

	let {
		overlay,
		status = null,
		saving = false,
		calibration = null,
		onUpdate,
		onPreview,
		onCalibrate
	}: {
		overlay: OverlaySettings | undefined
		status?: BackendState['overlay'] | null
		saving?: boolean
		calibration?: OverlayCalibration | null
		onUpdate: (patch: Partial<OverlaySettings>) => Promise<void>
		onPreview: () => Promise<void>
		onCalibrate: () => Promise<void>
	} = $props()

	type NumericKey =
		| 'offsetX'
		| 'offsetY'
		| 'offsetZ'
		| 'pitch'
		| 'roll'
		| 'yaw'
		| 'scale'
		| 'opacity'
		| 'holdSeconds'
	interface RangeControl {
		key: NumericKey
		label: string
		min: number
		max: number
		step: number
		format: (value: number) => string
	}

	const presets: { id: OverlayPresetId; label: string; icon: string }[] = [
		{ id: 'head', label: '視線前方', icon: 'i-carbon-view' },
		{ id: 'leftWatch', label: '左手臂（手錶）', icon: 'i-carbon-time' },
		{ id: 'rightWatch', label: '右手臂（手錶）', icon: 'i-carbon-time' },
		{ id: 'leftHand', label: '左手上方', icon: 'i-carbon-touch-1' },
		{ id: 'rightHand', label: '右手上方', icon: 'i-carbon-touch-1' }
	]
	const statusLabels: Record<string, { label: string; tone: string }> = {
		disabled: { label: '未啟用', tone: 'bg-zinc-400' },
		waiting: { label: '等待 SteamVR 啟動', tone: 'bg-amber-500 animate-pulse' },
		connected: { label: '已連線 SteamVR', tone: 'bg-emerald-500' },
		unavailable: { label: '無法使用', tone: 'bg-red-500' },
		error: { label: '發生錯誤，稍後自動重試', tone: 'bg-red-500' }
	}
	const meters = (value: number) => `${value > 0 ? '+' : ''}${value.toFixed(2)} m`
	const degrees = (value: number) => `${Math.round(value)}°`
	const percent = (value: number) => `${Math.round(value * 100)}%`

	let draft = $state<Record<NumericKey, number>>({
		offsetX: 0,
		offsetY: -0.35,
		offsetZ: -1.5,
		pitch: 0,
		roll: 0,
		yaw: 0,
		scale: 1,
		opacity: 0.9,
		holdSeconds: 8
	})
	let previewing = $state(false)

	const onHead = $derived((overlay?.anchor ?? 'head') === 'head')
	const activePreset = $derived(overlay ? matchOverlayPreset(placementOf(overlay)) : null)
	const statusView = $derived(statusLabels[status?.status ?? 'disabled'] ?? statusLabels.disabled)
	const connected = $derived(status?.status === 'connected')
	const disabled = $derived(!overlay || saving)
	const calibrationRunning = $derived(calibration?.status === 'running')
	// Every control moves or turns the panel along its own axes as seen by the
	// wearer, so each slider acts in one straight, predictable direction.
	const placementControls = $derived<RangeControl[]>([
		{ key: 'scale', label: '大小', min: 0.2, max: 2, step: 0.05, format: percent },
		// A hand-held panel only needs centimeter moves; the head panel needs meters.
		onHead
			? {
					key: 'offsetZ',
					label: '距離（負值＝前方）',
					min: -5,
					max: -0.3,
					step: 0.05,
					format: meters
				}
			: {
					key: 'offsetZ',
					label: '遠近（正值＝靠近臉）',
					min: -0.2,
					max: 0.2,
					step: 0.01,
					format: meters
				},
		{
			key: 'offsetY',
			label: '上下',
			min: onHead ? -1 : -0.3,
			max: onHead ? 1 : 0.3,
			step: onHead ? 0.05 : 0.01,
			format: meters
		},
		{
			key: 'offsetX',
			label: '左右',
			min: onHead ? -1 : -0.3,
			max: onHead ? 1 : 0.3,
			step: onHead ? 0.05 : 0.01,
			format: meters
		},
		{ key: 'pitch', label: '上下仰角', min: -90, max: 90, step: 5, format: degrees },
		{ key: 'yaw', label: '左右轉向', min: -90, max: 90, step: 5, format: degrees },
		{ key: 'roll', label: '畫面旋轉', min: -180, max: 180, step: 5, format: degrees }
	])
	const displayControls: RangeControl[] = [
		{ key: 'opacity', label: '不透明度', min: 0.2, max: 1, step: 0.05, format: percent },
		{
			key: 'holdSeconds',
			label: '字幕停留時間',
			min: 2,
			max: 60,
			step: 1,
			format: (value) => `${value} 秒`
		}
	]

	$effect(() => {
		if (!overlay) return
		draft = {
			offsetX: overlay.offsetX,
			offsetY: overlay.offsetY,
			offsetZ: overlay.offsetZ,
			pitch: overlay.pitch,
			roll: overlay.roll,
			yaw: overlay.yaw,
			scale: overlay.scale,
			opacity: overlay.opacity,
			holdSeconds: overlay.holdSeconds
		}
	})

	async function applyPreset(id: OverlayPresetId): Promise<void> {
		await onUpdate({ ...OVERLAY_PRESETS[id], basis: [...IDENTITY_BASIS] })
		// A watch has no fixed pose: capture it from the wearer's own arm.
		if (OVERLAY_PRESETS[id].watchMode && connected) await onCalibrate()
	}

	async function preview(): Promise<void> {
		previewing = true
		try {
			await onPreview()
		} finally {
			previewing = false
		}
	}
</script>

{#snippet slider(control: RangeControl)}
	<div>
		<div class="mb-1 flex items-center justify-between">
			<label class="field-label !mb-0" for="overlay-{control.key}">{control.label}</label>
			<span class="font-mono text-xs text-zinc-500">{control.format(draft[control.key])}</span>
		</div>
		<input
			id="overlay-{control.key}"
			type="range"
			class="w-full"
			style="accent-color: var(--color-accent-500)"
			min={control.min}
			max={control.max}
			step={control.step}
			bind:value={draft[control.key]}
			{disabled}
			onchange={() => void onUpdate({ [control.key]: draft[control.key] })}
		/>
	</div>
{/snippet}

<div class="flex items-center justify-between gap-4 py-2.5">
	<div class="min-w-0">
		<div class="text-sm font-medium text-zinc-800 dark:text-zinc-200">在遊戲內顯示字幕</div>
		<div class="mt-0.5 text-xs leading-relaxed text-zinc-500">
			透過 SteamVR overlay 顯示即時辨識與翻譯結果
		</div>
	</div>
	<KSwitch
		value={overlay?.enabled ?? false}
		{disabled}
		attrs={{ 'aria-label': '在遊戲內顯示字幕' }}
		on:updateValue={(event) => void onUpdate({ enabled: Boolean(event.detail) })}
	/>
</div>

{#if overlay?.enabled}
	<div
		class="mb-3 flex items-start gap-2 rounded-lg bg-zinc-100 px-3 py-2 text-xs text-zinc-600 dark:bg-zinc-800/60 dark:text-zinc-300"
		role="status"
	>
		<span class="mt-1 size-2 shrink-0 rounded-full {statusView.tone}"></span>
		<span>{statusView.label}{status?.message ? `：${status.message}` : ''}</span>
	</div>
{/if}

<div class="space-y-3">
	<div>
		<div class="field-label">顯示位置</div>
		<div class="grid grid-cols-2 gap-2" role="radiogroup" aria-label="顯示位置">
			{#each presets as preset (preset.id)}
				<button
					type="button"
					role="radio"
					aria-checked={activePreset === preset.id}
					class="btn-secondary h-10 justify-start px-3 text-xs {activePreset === preset.id
						? '!border-accent-500 !text-accent-600'
						: ''}"
					{disabled}
					onclick={() => void applyPreset(preset.id)}
				>
					<span class="{preset.icon} size-4 shrink-0"></span>
					{preset.label}
				</button>
			{/each}
		</div>
		{#if overlay && !activePreset}
			<p class="field-help">目前使用自訂位置</p>
		{/if}
	</div>

	{#if overlay && overlay.anchor !== 'head'}
		<div class="rounded-lg border border-zinc-200 p-3 dark:border-zinc-800">
			<div class="text-sm font-medium text-zinc-800 dark:text-zinc-200">校準手錶位置</div>
			<p class="mt-1 text-xs leading-relaxed text-zinc-500">
				按下後，在 VR 中像看手錶一樣抬起{overlay.anchor === 'leftHand'
					? '左'
					: '右'}手臂，看著前臂上要戴手錶的位置，保持不動約 2
				秒。字幕會固定在你看的那個位置並正對你。
			</p>
			<button
				type="button"
				class="btn-accent mt-2.5 h-9 w-full px-3 text-xs"
				disabled={disabled || !overlay.enabled || !connected || calibrationRunning}
				onclick={() => void onCalibrate()}
			>
				<span
					class="{calibrationRunning
						? 'i-carbon-circle-dash animate-spin'
						: 'i-carbon-time'} size-3.5"
				></span>
				{calibrationRunning ? '請在 VR 中抬起手臂…' : '開始校準'}
			</button>
			{#if calibration?.status === 'running' && calibration.message}
				<p class="field-help text-amber-600 dark:text-amber-400" role="status">
					{calibration.message}
				</p>
			{:else if calibration?.status === 'done'}
				<p class="field-help text-emerald-600 dark:text-emerald-400">
					校準完成，可以再用下方的微調修正位置。
				</p>
			{:else if calibration?.status === 'failed'}
				<p class="field-help text-red-600 dark:text-red-400">{calibration.message ?? '校準失敗'}</p>
			{/if}
		</div>

		<div class="flex items-center justify-between gap-4 py-1">
			<div class="min-w-0">
				<div class="text-sm font-medium text-zinc-800 dark:text-zinc-200">手錶模式</div>
				<div class="mt-0.5 text-xs leading-relaxed text-zinc-500">
					像看手錶一樣抬起手臂才會顯示，並保留最近的字幕
				</div>
			</div>
			<KSwitch
				value={overlay.watchMode}
				{disabled}
				attrs={{ 'aria-label': '手錶模式' }}
				on:updateValue={(event) => void onUpdate({ watchMode: Boolean(event.detail) })}
			/>
		</div>
	{/if}

	<button
		type="button"
		class="btn-secondary h-9 w-full px-3 text-xs"
		disabled={disabled || !overlay?.enabled || !connected || previewing}
		onclick={() => void preview()}
	>
		<span class="i-carbon-view size-3.5"></span>
		顯示測試字幕
	</button>

	<details class="group">
		<summary
			class="cursor-pointer list-none text-xs font-semibold text-zinc-600 dark:text-zinc-300"
		>
			微調位置與角度 <span class="float-right transition-transform group-open:rotate-90">›</span>
		</summary>
		<div class="mt-3 space-y-3">
			{#each placementControls as control (control.key)}
				{@render slider(control)}
			{/each}
			<p class="field-help">方向以你看著字幕時為準；調整時會在 VR 中顯示測試字幕，方便確認位置。</p>
		</div>
	</details>

	<div class="flex items-center justify-between gap-4 py-1">
		<div class="text-sm font-medium text-zinc-800 dark:text-zinc-200">顯示他人語音（喇叭）</div>
		<KSwitch
			value={overlay?.showSpeaker ?? true}
			{disabled}
			attrs={{ 'aria-label': '顯示他人語音' }}
			on:updateValue={(event) => void onUpdate({ showSpeaker: Boolean(event.detail) })}
		/>
	</div>
	<div class="flex items-center justify-between gap-4 py-1">
		<div class="text-sm font-medium text-zinc-800 dark:text-zinc-200">顯示自己的語音（麥克風）</div>
		<KSwitch
			value={overlay?.showMicrophone ?? false}
			{disabled}
			attrs={{ 'aria-label': '顯示自己的語音' }}
			on:updateValue={(event) => void onUpdate({ showMicrophone: Boolean(event.detail) })}
		/>
	</div>
	{#each displayControls as control (control.key)}
		{@render slider(control)}
	{/each}
	<p class="field-help">
		需先啟動 SteamVR；本程式不會自動開啟 SteamVR。字幕會在 SteamVR 啟動後自動出現。
	</p>
</div>
