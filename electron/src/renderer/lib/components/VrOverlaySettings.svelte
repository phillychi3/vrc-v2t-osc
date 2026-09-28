<script lang="ts">
	import { t } from '$lib/i18n'
	import type { MessageKey } from '$shared/i18n'
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

	const presets: { id: OverlayPresetId; icon: string }[] = [
		{ id: 'head', icon: 'i-carbon-view' },
		{ id: 'leftWatch', icon: 'i-carbon-time' },
		{ id: 'rightWatch', icon: 'i-carbon-time' },
		{ id: 'leftHand', icon: 'i-carbon-touch-1' },
		{ id: 'rightHand', icon: 'i-carbon-touch-1' }
	]
	const statusTones: Record<string, string> = {
		disabled: 'bg-zinc-400',
		waiting: 'bg-amber-500 animate-pulse',
		connected: 'bg-emerald-500',
		unavailable: 'bg-red-500',
		error: 'bg-red-500'
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
	const statusKey = $derived(
		(status?.status && status.status in statusTones ? status.status : 'disabled') as string
	)
	const statusView = $derived({
		label: $t(`overlay.status.${statusKey}` as MessageKey),
		tone: statusTones[statusKey]
	})
	const connected = $derived(status?.status === 'connected')
	const disabled = $derived(!overlay || saving)
	const calibrationRunning = $derived(calibration?.status === 'running')
	// Every control moves or turns the panel along its own axes as seen by the
	// wearer, so each slider acts in one straight, predictable direction.
	const placementControls = $derived<RangeControl[]>([
		{ key: 'scale', label: $t('overlay.scale'), min: 0.2, max: 2, step: 0.05, format: percent },
		// A hand-held panel only needs centimeter moves; the head panel needs meters.
		onHead
			? {
					key: 'offsetZ',
					label: $t('overlay.distance'),
					min: -5,
					max: -0.3,
					step: 0.05,
					format: meters
				}
			: {
					key: 'offsetZ',
					label: $t('overlay.depth'),
					min: -0.2,
					max: 0.2,
					step: 0.01,
					format: meters
				},
		{
			key: 'offsetY',
			label: $t('overlay.vertical'),
			min: onHead ? -1 : -0.3,
			max: onHead ? 1 : 0.3,
			step: onHead ? 0.05 : 0.01,
			format: meters
		},
		{
			key: 'offsetX',
			label: $t('overlay.horizontal'),
			min: onHead ? -1 : -0.3,
			max: onHead ? 1 : 0.3,
			step: onHead ? 0.05 : 0.01,
			format: meters
		},
		{ key: 'pitch', label: $t('overlay.pitch'), min: -90, max: 90, step: 5, format: degrees },
		{ key: 'yaw', label: $t('overlay.yaw'), min: -90, max: 90, step: 5, format: degrees },
		{ key: 'roll', label: $t('overlay.roll'), min: -180, max: 180, step: 5, format: degrees }
	])
	const displayControls = $derived<RangeControl[]>([
		{ key: 'opacity', label: $t('overlay.opacity'), min: 0.2, max: 1, step: 0.05, format: percent },
		{
			key: 'holdSeconds',
			label: $t('overlay.hold'),
			min: 2,
			max: 60,
			step: 1,
			format: (value) => $t('overlay.seconds', { value })
		}
	])

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

<SettingRow title={$t('overlay.enable')} description={$t('overlay.enableHelp')}>
	<Switch
		label={$t('overlay.enable')}
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
		<span
			>{status?.message
				? $t('overlay.statusDetail', { status: statusView.label, message: status.message })
				: statusView.label}</span
		>
	</div>
{/if}

<div class="mt-5 space-y-5">
	<div>
		<div class="field-label">{$t('overlay.position')}</div>
		<div
			class="grid grid-cols-2 gap-2 md:grid-cols-3"
			role="radiogroup"
			aria-label={$t('overlay.position')}
		>
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
					{$t(`overlay.preset.${preset.id}`)}
				</button>
			{/each}
		</div>
		{#if overlay && !activePreset}
			<p class="field-help">{$t('overlay.custom')}</p>
		{/if}
	</div>

	{#if overlay && overlay.anchor !== 'head'}
		<div class="rounded-xl border border-[var(--border)] p-4">
			<div class="setting-title">{$t('overlay.calibrate.title')}</div>
			<p class="setting-description">
				{$t('overlay.calibrate.help', {
					hand: $t(overlay.anchor === 'leftHand' ? 'common.left' : 'common.right')
				})}
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
				{calibrationRunning ? $t('overlay.calibrate.running') : $t('overlay.calibrate.start')}
			</button>
			{#if calibration?.status === 'running' && calibration.message}
				<p class="field-help !text-amber-600 dark:!text-amber-400" role="status">
					{calibration.message}
				</p>
			{:else if calibration?.status === 'done'}
				<p class="field-help !text-emerald-600 dark:!text-emerald-400">
					{$t('overlay.calibrate.done')}
				</p>
			{:else if calibration?.status === 'failed'}
				<p class="field-help !text-red-600 dark:!text-red-400">
					{calibration.message ?? $t('overlay.calibrate.failed')}
				</p>
			{/if}
		</div>

		<SettingRow title={$t('overlay.watch')} description={$t('overlay.watchHelp')}>
			<Switch
				label={$t('overlay.watch')}
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
		{$t('overlay.preview')}
	</button>

	<details class="group rounded-xl border border-[var(--border)]">
		<summary
			class="flex min-h-12 cursor-pointer list-none items-center justify-between px-4 text-sm font-semibold text-[var(--text-muted)]"
		>
			{$t('overlay.adjust')}
			<span class="i-carbon-chevron-down size-4 transition-transform group-open:rotate-180"></span>
		</summary>
		<div class="space-y-3 border-t border-[var(--border)] p-4">
			{#each placementControls as control (control.key)}
				{@render slider(control)}
			{/each}
			<p class="field-help">{$t('overlay.adjustHelp')}</p>
		</div>
	</details>

	<div>
		<SettingRow title={$t('overlay.showSpeaker')} description={$t('overlay.showSpeakerHelp')}>
			<Switch
				label={$t('overlay.showSpeaker')}
				checked={overlay?.showSpeaker ?? true}
				{disabled}
				onChange={(showSpeaker) => void onUpdate({ showSpeaker })}
			/>
		</SettingRow>
		<SettingRow title={$t('overlay.showMicrophone')} description={$t('overlay.showMicrophoneHelp')}>
			<Switch
				label={$t('overlay.showMicrophone')}
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
		{$t('overlay.steamvrHelp')}
	</p>
</div>
