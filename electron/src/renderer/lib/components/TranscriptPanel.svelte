<script lang="ts">
	import type { Transcript } from '$lib/stores/backend'

	let { items }: { items: Transcript[] } = $props()

	const sources: Record<string, { label: string; icon: string; tone: string }> = {
		voice: {
			label: '我',
			icon: 'i-carbon-microphone-filled',
			tone: 'bg-orange-100 text-orange-700 dark:bg-orange-950/70 dark:text-orange-300'
		},
		speaker: {
			label: '他人',
			icon: 'i-carbon-volume-up-filled',
			tone: 'bg-sky-100 text-sky-700 dark:bg-sky-950/70 dark:text-sky-300'
		},
		manual: {
			label: '輸入',
			icon: 'i-carbon-keyboard',
			tone: 'bg-zinc-200 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300'
		}
	}

	const emotionLabels = ['平淡', '關切', '開心', '憤怒', '悲傷', '疑問', '驚奇', '厭惡']
	const oscReasons: Record<string, string> = {
		disabled: 'OSC 已停用，未送出',
		overloaded: '佇列已滿，未送出',
		stale: '錄音已停止，未送出',
		closed: '後端已關閉，未送出',
		too_long: '超過 144 字，未送出',
		too_many_lines: '超過 9 行，未送出',
		send_failed: 'OSC 傳送失敗',
		empty: '空白文字，未送出'
	}

	function oscStatus(item: Transcript): { label: string; tone: string } | null {
		if (item.source === 'speaker') return null
		if (item.oscStatus === 'sent')
			return { label: '已送到聊天框', tone: 'text-emerald-600 dark:text-emerald-400' }
		if (item.oscStatus === 'pending') return { label: '等待送出', tone: '' }
		if (item.oscStatus === 'skipped')
			return {
				label: oscReasons[item.oscReason ?? ''] ?? '未送出',
				tone: 'text-amber-600 dark:text-amber-400'
			}
		return null
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
		class="min-h-0 flex-1 overflow-y-scroll overscroll-contain px-4 pb-4"
		data-selectable
		role="region"
		aria-label="逐字稿訊息"
	>
		{#if items.length === 0}
			<div class="grid h-full min-h-48 place-items-center rounded-2xl p-8 text-center">
				<div class="max-w-md">
					<span class="i-carbon-chat mx-auto mb-4 block size-10 text-[var(--text-subtle)]"></span>
					<p class="text-lg font-semibold">還沒有字幕</p>
				</div>
			</div>
		{:else}
			<div class="space-y-2">
				{#each items as item (item.utteranceId)}
					{@const source = sources[item.source] ?? sources.manual}
					{@const osc = oscStatus(item)}
					<article
						class="flex gap-3 rounded-2xl border border-[var(--border)] bg-[var(--surface)] px-4 py-3"
					>
						<span
							class="mt-0.5 inline-flex h-7 shrink-0 items-center gap-1.5 rounded-lg px-2 text-xs font-bold {source.tone}"
						>
							<span class="{source.icon} size-3.5"></span>{source.label}
						</span>
						<div class="min-w-0 flex-1">
							<p class="whitespace-pre-wrap text-base font-medium leading-7">{item.text}</p>
							{#if item.translatedText}
								<p
									class="mt-1 whitespace-pre-wrap border-l-3 border-[var(--color-accent-500)] pl-3 text-base leading-7 text-[var(--text-muted)]"
								>
									{item.translatedText}
								</p>
							{:else if item.translationPending}
								<p class="mt-1 text-sm italic text-[var(--text-subtle)]">翻譯中…</p>
							{/if}
							<div
								class="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-[var(--text-subtle)]"
							>
								<time>{timeLabel(item.receivedAt)}</time>
								{#if osc}
									<span class={osc.tone}>{osc.label}</span>
								{/if}
								{#if item.emotion !== undefined}
									<span>情緒：{emotionLabels[item.emotion] ?? item.emotion}</span>
								{/if}
							</div>
						</div>
					</article>
				{/each}
			</div>
		{/if}
	</div>
</section>
