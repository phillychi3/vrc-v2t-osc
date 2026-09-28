<script lang="ts">
	import { t } from '$lib/i18n'
	import type { Transcript } from '$lib/stores/backend'
	import { locale } from '$lib/i18n'
	import type { MessageKey } from '$shared/i18n'

	let { items }: { items: Transcript[] } = $props()

	const sources: Record<string, { label: MessageKey; icon: string; tone: string }> = {
		voice: {
			label: 'transcript.source.voice',
			icon: 'i-carbon-microphone-filled',
			tone: 'bg-orange-100 text-orange-700 dark:bg-orange-950/70 dark:text-orange-300'
		},
		speaker: {
			label: 'transcript.source.speaker',
			icon: 'i-carbon-volume-up-filled',
			tone: 'bg-sky-100 text-sky-700 dark:bg-sky-950/70 dark:text-sky-300'
		},
		manual: {
			label: 'transcript.source.manual',
			icon: 'i-carbon-keyboard',
			tone: 'bg-zinc-200 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300'
		}
	}

	const oscReasons = new Set([
		'disabled',
		'overloaded',
		'stale',
		'closed',
		'too_long',
		'too_many_lines',
		'send_failed',
		'empty'
	])

	function emotionLabel(emotion: number): string {
		return emotion >= 0 && emotion <= 7 ? $t(`emotion.${emotion}` as MessageKey) : String(emotion)
	}

	function oscStatus(item: Transcript): { label: string; tone: string } | null {
		if (item.source === 'speaker') return null
		if (item.oscStatus === 'sent')
			return { label: $t('osc.sent'), tone: 'text-emerald-600 dark:text-emerald-400' }
		if (item.oscStatus === 'pending') return { label: $t('osc.pending'), tone: '' }
		if (item.oscStatus === 'skipped')
			return {
				label: oscReasons.has(item.oscReason ?? '')
					? $t(`osc.reason.${item.oscReason}` as MessageKey)
					: $t('osc.skipped'),
				tone: 'text-amber-600 dark:text-amber-400'
			}
		return null
	}

	function timeLabel(value?: string): string {
		if (!value) return $t('transcript.justNow')
		return new Intl.DateTimeFormat($locale, { hour: '2-digit', minute: '2-digit' }).format(
			new Date(value)
		)
	}
</script>

<section class="flex min-h-0 flex-1 flex-col">
	<div
		class="min-h-0 flex-1 overflow-y-scroll overscroll-contain px-4 pb-4"
		data-selectable
		role="region"
		aria-label={$t('transcript.aria')}
	>
		{#if items.length === 0}
			<div class="grid h-full min-h-48 place-items-center rounded-2xl p-8 text-center">
				<div class="max-w-md">
					<span class="i-carbon-chat mx-auto mb-4 block size-10 text-[var(--text-subtle)]"></span>
					<p class="text-lg font-semibold">{$t('transcript.empty')}</p>
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
							<span class="{source.icon} size-3.5"></span>{$t(source.label)}
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
								<p class="mt-1 text-sm italic text-[var(--text-subtle)]">
									{$t('transcript.translating')}
								</p>
							{/if}
							<div
								class="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-[var(--text-subtle)]"
							>
								<time>{timeLabel(item.receivedAt)}</time>
								{#if osc}
									<span class={osc.tone}>{osc.label}</span>
								{/if}
								{#if item.emotion !== undefined}
									<span>{$t('transcript.emotion', { emotion: emotionLabel(item.emotion) })}</span>
								{/if}
							</div>
						</div>
					</article>
				{/each}
			</div>
		{/if}
	</div>
</section>
