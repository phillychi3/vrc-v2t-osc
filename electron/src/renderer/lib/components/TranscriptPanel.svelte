<script lang="ts">
	import type { Transcript } from '$lib/stores/backend'
	import ChatDashedIcon from '@iconify-svelte/material-symbols/chat-dashed'

	let { items }: { items: Transcript[] } = $props()

	function sourceLabel(source: string): string {
		return source === 'voice'
			? '麥克風'
			: source === 'speaker'
				? '喇叭'
				: source === 'manual'
					? '手動'
					: source
	}

	const emotionLabels = ['平淡', '關切', '開心', '憤怒', '悲傷', '疑問', '驚奇', '厭惡']
	const oscReasons: Record<string, string> = {
		disabled: 'OSC 已停用',
		overloaded: '佇列已滿，未送出',
		stale: '錄音已停止，未送出',
		closed: '後端已關閉，未送出',
		too_long: '超過 144 字，未送出',
		too_many_lines: '超過 9 行，未送出',
		send_failed: 'OSC 傳送失敗',
		empty: '空白文字，未送出'
	}

	function oscLabel(item: Transcript): string {
		if (item.source === 'speaker') return ''
		if (item.oscStatus === 'sent') return '已送出'
		if (item.oscStatus === 'pending') return '等待送出'
		if (item.oscStatus === 'skipped') return oscReasons[item.oscReason ?? ''] ?? '未送出'
		return ''
	}

	function timeLabel(value?: string): string {
		if (!value) return '剛剛'
		return new Intl.DateTimeFormat('zh-TW', { hour: '2-digit', minute: '2-digit' }).format(
			new Date(value)
		)
	}
</script>

<section class="flex min-h-0 flex-1 flex-col">
	<div
		class="min-h-0 flex-1 overflow-y-scroll overscroll-contain px-4 py-3"
		data-selectable
		role="region"
		aria-label="逐字稿訊息"
	>
		{#if items.length === 0}
			<div
				class="grid h-full min-h-48 place-items-center rounded-2xl border border-dashed border-zinc-200 bg-zinc-50/60 p-8 text-center dark:border-zinc-800 dark:bg-zinc-900/25"
			>
				<div>
					<ChatDashedIcon class="mx-auto mb-4 h-8 w-8 text-zinc-400 dark:text-zinc-500" />
				</div>
			</div>
		{:else}
			<div class="space-y-1">
				{#each items as item (item.utteranceId)}
					<article
						class="group grid grid-cols-[4rem_1fr_auto] gap-3 rounded-xl px-3 py-3 hover:bg-zinc-100/80 dark:hover:bg-zinc-900/70"
					>
						<div>
							<span
								class="rounded-md px-1.5 py-1 text-[10px] font-semibold {item.source === 'voice'
									? 'bg-orange-100 text-orange-700 dark:bg-orange-950 dark:text-orange-300'
									: 'bg-blue-100 text-blue-700 dark:bg-blue-950 dark:text-blue-300'}"
								>{sourceLabel(item.source)}</span
							>
						</div>
						<div class="min-w-0">
							<p class="whitespace-pre-wrap text-sm leading-6 text-zinc-800 dark:text-zinc-200">
								{item.text}
							</p>
							{#if item.translatedText}
								<p
									class="mt-1 whitespace-pre-wrap border-l-2 border-orange-300 pl-2 text-sm leading-6 text-zinc-600 dark:border-orange-700 dark:text-zinc-300"
								>
									{item.translatedText}
								</p>
							{:else if item.translationPending}
								<p class="mt-1 text-xs text-zinc-400">翻譯中…</p>
							{/if}
						</div>
						<div class="pt-1 text-right text-[10px] text-zinc-400">
							<time class="block">{timeLabel(item.receivedAt)}</time>
							<span class="mt-1 block">{oscLabel(item)}</span>
							{#if item.emotion !== undefined}
								<span class="mt-1 block"
									>{emotionLabels[item.emotion] ?? `情緒 ${item.emotion}`}</span
								>
							{/if}
						</div>
					</article>
				{/each}
			</div>
		{/if}
	</div>
</section>
