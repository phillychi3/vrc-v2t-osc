<script lang="ts">
	import { t } from '$lib/i18n'
	let {
		disabled = false,
		onSend
	}: { disabled?: boolean; onSend: (text: string) => Promise<void> } = $props()

	/** VRChat's chatbox limit, counted in UTF-16 units like the backend. */
	const CHATBOX_LIMIT = 144

	let text = $state('')
	let sending = $state(false)
	let composing = false

	const length = $derived(text.trim().length)
	const tooLong = $derived(length > CHATBOX_LIMIT)

	async function send(): Promise<void> {
		const value = text.trim()
		if (!value || sending || disabled) return
		sending = true
		try {
			await onSend(value)
			text = ''
		} catch {
			// The backend store exposes the actionable error through its error state.
		} finally {
			sending = false
		}
	}

	function handleKeydown(event: KeyboardEvent): void {
		if (
			event.key !== 'Enter' ||
			event.shiftKey ||
			composing ||
			event.isComposing ||
			event.keyCode === 229
		)
			return
		event.preventDefault()
		void send()
	}
</script>

<div class="shrink-0 border-t border-[var(--border)] bg-[var(--surface)] px-4 pt-3 pb-4">
	<div
		class="flex items-end gap-2 rounded-2xl border-2 border-[var(--border)] bg-[var(--bg)] p-2 focus-within:border-[var(--color-accent-500)]"
	>
		<textarea
			bind:value={text}
			aria-label={$t('composer.aria')}
			rows="1"
			maxlength="1000"
			placeholder={$t('composer.placeholder')}
			{disabled}
			onkeydown={handleKeydown}
			oncompositionstart={() => (composing = true)}
			oncompositionend={() => (composing = false)}
			class="max-h-40 min-h-12 flex-1 resize-none bg-transparent px-2 py-2.5 text-base leading-7 outline-none placeholder:text-[var(--text-subtle)] disabled:cursor-not-allowed"
		></textarea>
		<button
			type="button"
			disabled={disabled || sending || !text.trim()}
			onclick={() => void send()}
			class="btn-accent size-12 shrink-0 rounded-xl"
			aria-label={$t('composer.send')}
		>
			<span
				class="{sending ? 'i-carbon-circle-dash animate-spin' : 'i-carbon-send-alt-filled'} size-5"
			></span>
		</button>
	</div>
	<div class="mt-1.5 flex justify-between px-2 text-xs text-[var(--text-subtle)]">
		<span>{$t('composer.newline')}</span>
		<span class={tooLong ? 'font-semibold text-red-500' : ''}>
			{length} / {CHATBOX_LIMIT}{tooLong ? ` · ${$t('composer.overLimit')}` : ''}
		</span>
	</div>
</div>
