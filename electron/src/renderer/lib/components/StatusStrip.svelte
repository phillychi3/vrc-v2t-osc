<script lang="ts">
	import type { BackendState } from '$shared/ipc'

	let { state }: { state: BackendState | null } = $props()

	function statusLabel(status?: string): string {
		return (
			{
				not_loaded: '未載入',
				loading: '載入中',
				ready: '已就緒',
				failed: '失敗'
			}[status ?? ''] ?? '等待中'
		)
	}
</script>

<div
	class="flex min-h-10 shrink-0 items-center gap-x-5 gap-y-2 overflow-x-auto border-b border-zinc-200/80 bg-zinc-50/80 px-5 py-2 text-xs dark:border-zinc-800 dark:bg-zinc-900/45"
>
	<div class="flex items-center gap-2 whitespace-nowrap">
		<span class="status-dot {state?.backend === 'ready' ? 'bg-emerald-500' : 'bg-zinc-400'}"></span>
		<span class="text-zinc-500">服務</span>
		<strong class="font-medium text-zinc-800 dark:text-zinc-200"
			>{state?.backend ?? 'offline'}</strong
		>
	</div>
	<div class="status-divider"></div>
	<div class="flex items-center gap-2 whitespace-nowrap">
		<span class="text-zinc-500">語音模型</span>
		<strong class="font-medium">{statusLabel(state?.models.speech?.status)}</strong>
	</div>
	<div class="flex items-center gap-2 whitespace-nowrap">
		<span class="text-zinc-500">情緒模型</span>
		<strong class="font-medium">{statusLabel(state?.models.emotion?.status)}</strong>
	</div>
	<div class="status-divider"></div>
	<div class="flex items-center gap-2 whitespace-nowrap">
		<span class="text-zinc-500">麥克風</span>
		<strong class="max-w-44 truncate font-medium">{state?.settings.audio.deviceId ?? '—'}</strong>
	</div>
</div>
