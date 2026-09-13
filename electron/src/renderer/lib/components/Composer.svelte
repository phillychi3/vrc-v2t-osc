<script lang="ts">
	let {
		disabled = false,
		onSend
	}: { disabled?: boolean; onSend: (text: string) => Promise<void> } = $props()

	let text = $state('')
	let sending = $state(false)
	let composing = false

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
		if (event.key !== 'Enter' || event.shiftKey || composing) return
		event.preventDefault()
		void send()
	}
</script>

<div
	class="shrink-0 border-t border-zinc-200/80 bg-white/80 p-4 backdrop-blur dark:border-zinc-800 dark:bg-zinc-950/80"
>
	<div
		class="flex items-end gap-2 rounded-2xl border border-zinc-200 bg-zinc-50 p-2 shadow-sm focus-within:border-accent-500 focus-within:ring-3 focus-within:ring-orange-500/10 dark:border-zinc-800 dark:bg-zinc-900"
	>
		<textarea
			bind:value={text}
			rows="1"
			maxlength="1000"
			{disabled}
			onkeydown={handleKeydown}
			oncompositionstart={() => (composing = true)}
			oncompositionend={() => (composing = false)}
			class="max-h-32 min-h-10 flex-1 resize-none bg-transparent px-2 py-2 text-sm leading-6 outline-none placeholder:text-zinc-400 disabled:cursor-not-allowed"
		></textarea>
		<button
			type="button"
			disabled={disabled || sending || !text.trim()}
			onclick={() => void send()}
			class="btn-accent size-10 shrink-0 rounded-xl"
			aria-label="傳送文字"
		>
			<span
				class="{sending
					? 'i-carbon-circle-dash animate-spin'
					: 'i-carbon-send-alt-filled'} size-4.5"
			></span>
		</button>
	</div>
</div>
