<script lang="ts">
	import SettingRow from './ui/SettingRow.svelte'
	import Switch from './ui/Switch.svelte'
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
		<div class="flex items-center justify-between">
			<label class="field-label !mb-0" for="overlay-{control.key}">{control.label}</label>
			<span class="font-mono text-sm text-[var(--text-subtle)]"
				>{control.format(draft[control.key])}</span
			>
		</div>
		<input
			id="overlay-{control.key}"
			type="range"
			class="w-full"
			min={control.min}
			max={control.max}
			step={control.step}
			bind:value={draft[control.key]}
			{disabled}
			onchange={() => void onUpdate({ [control.key]: draft[control.key] })}
		/>
	</div>
{/snippet}

<SettingRow title="在遊戲內顯示字幕" description="透過 SteamVR overlay 顯示即時辨識與翻譯結果">
	<Switch
		label="在遊戲內顯示字幕"
		checked={overlay?.enabled ?? false}
		{disabled}
		onChange={(enabled) => void onUpdate({ enabled })}
	/>
</SettingRow>

{#if overlay?.enabled}
	<div
		class="mt-1 flex items-center gap-2.5 rounded-xl bg-[var(--surface-muted)] px-4 py-3 text-sm"
		role="status"
	>
		<span class="status-dot {statusView.tone}"></span>
		<span>{statusView.label}{status?.message ? `：${status.message}` : ''}</span>
	</div>
{/if}

<div class="mt-5 space-y-5">
	<div>
		<div class="field-label">顯示位置</div>
		<div class="grid grid-cols-2 gap-2 md:grid-cols-3" role="radiogroup" aria-label="顯示位置">
			{#each presets as preset (preset.id)}
				<button
					type="button"
					role="radio"
					aria-checked={activePreset === preset.id}
					class="btn-secondary h-12 justify-start px-4 text-sm {activePreset === preset.id
						? '!border-[var(--color-accent-500)] !text-[var(--color-accent-500)]'
						: ''}"
					{disabled}
					onclick={() => void applyPreset(preset.id)}
				>
					<span class="{preset.icon} size-5 shrink-0"></span>
					{preset.label}
				</button>
			{/each}
		</div>
		{#if overlay && !activePreset}
			<p class="field-help">目前使用自訂位置</p>
		{/if}
	</div>

	{#if overlay && overlay.anchor !== 'head'}
		<div class="rounded-xl border border-[var(--border)] p-4">
			<div class="setting-title">校準手錶位置</div>
			<p class="setting-description">
				按下後，在 VR 中像看手錶一樣抬起{overlay.anchor === 'leftHand'
					? '左'
					: '右'}手臂，看著前臂上要戴手錶的位置，保持不動約 2
				秒。字幕會固定在你看的那個位置並正對你。
			</p>
			<button
				type="button"
				class="btn-accent mt-3 h-12 w-full px-4 text-base"
				disabled={disabled || !overlay.enabled || !connected || calibrationRunning}
				onclick={() => void onCalibrate()}
			>
				<span
					class="{calibrationRunning
						? 'i-carbon-circle-dash animate-spin'
						: 'i-carbon-time'} size-5"
				></span>
				{calibrationRunning ? '請在 VR 中抬起手臂…' : '開始校準'}
			</button>
			{#if calibration?.status === 'running' && calibration.message}
				<p class="field-help !text-amber-600 dark:!text-amber-400" role="status">
					{calibration.message}
				</p>
			{:else if calibration?.status === 'done'}
				<p class="field-help !text-emerald-600 dark:!text-emerald-400">
					校準完成，可以再用下方的微調修正位置。
				</p>
			{:else if calibration?.status === 'failed'}
				<p class="field-help !text-red-600 dark:!text-red-400">
					{calibration.message ?? '校準失敗'}
				</p>
			{/if}
		</div>

		<SettingRow title="手錶模式" description="像看手錶一樣抬起手臂才會顯示，並保留最近的字幕">
			<Switch
				label="手錶模式"
				checked={overlay.watchMode}
				{disabled}
				onChange={(watchMode) => void onUpdate({ watchMode })}
			/>
		</SettingRow>
	{/if}

	<button
		type="button"
		class="btn-secondary h-12 w-full px-4 text-sm"
		disabled={disabled || !overlay?.enabled || !connected || previewing}
		onclick={() => void preview()}
	>
		<span class="i-carbon-view size-5"></span>
		顯示測試字幕
	</button>

	<details class="group rounded-xl border border-[var(--border)]">
		<summary
			class="flex min-h-12 cursor-pointer list-none items-center justify-between px-4 text-sm font-semibold text-[var(--text-muted)]"
		>
			微調位置與角度
			<span class="i-carbon-chevron-down size-4 transition-transform group-open:rotate-180"></span>
		</summary>
		<div class="space-y-3 border-t border-[var(--border)] p-4">
			{#each placementControls as control (control.key)}
				{@render slider(control)}
			{/each}
			<p class="field-help">方向以你看著字幕時為準；調整時會在 VR 中顯示測試字幕，方便確認位置。</p>
		</div>
	</details>

	<div>
		<SettingRow title="顯示他人語音" description="遊戲裡聽到的聲音（喇叭）">
			<Switch
				label="顯示他人語音"
				checked={overlay?.showSpeaker ?? true}
				{disabled}
				onChange={(showSpeaker) => void onUpdate({ showSpeaker })}
			/>
		</SettingRow>
		<SettingRow title="顯示自己的語音" description="你對麥克風說的話">
			<Switch
				label="顯示自己的語音"
				checked={overlay?.showMicrophone ?? false}
				{disabled}
				onChange={(showMicrophone) => void onUpdate({ showMicrophone })}
			/>
		</SettingRow>
	</div>
	<div class="space-y-3">
		{#each displayControls as control (control.key)}
			{@render slider(control)}
		{/each}
	</div>
	<p class="field-help">
		需先啟動 SteamVR；本程式不會自動開啟 SteamVR，字幕會在 SteamVR 啟動後自動出現。
	</p>
</div>
