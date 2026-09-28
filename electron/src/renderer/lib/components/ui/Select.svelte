<script lang="ts" generics="T extends string">
	/**
	 * A dropdown drawn inside the page. Native <select> menus open as separate
	 * OS windows, which desktop-capture overlays in VR (XSOverlay, OVR Toolkit,
	 * the SteamVR dashboard) often cannot show, so the menu would be invisible.
	 */
	interface Option {
		value: T
		label: string
		hint?: string
		disabled?: boolean
	}

	let {
		id,
		value,
		options,
		disabled = false,
		ariaLabel,
		placeholder = '請選擇',
		onChange
	}: {
		id?: string
		value: T
		options: Option[]
		disabled?: boolean
		ariaLabel?: string
		placeholder?: string
		onChange: (value: T) => void
	} = $props()

	let open = $state(false)
	let upward = $state(false)
	let active = $state(-1)
	let root = $state<HTMLDivElement>()
	let trigger = $state<HTMLButtonElement>()

	const selected = $derived(options.find((option) => option.value === value))

	function show(): void {
		if (disabled) return
		// Open upward when the menu would run past the bottom of the window.
		const rect = trigger?.getBoundingClientRect()
		upward = Boolean(rect && rect.bottom + 320 > window.innerHeight && rect.top > 320)
		active = Math.max(
			0,
			options.findIndex((option) => option.value === value)
		)
		open = true
	}

	function choose(option: Option): void {
		if (option.disabled) return
		open = false
		trigger?.focus()
		if (option.value !== value) onChange(option.value)
	}

	function move(step: number): void {
		if (options.length === 0) return
		let next = active
		for (let tries = 0; tries < options.length; tries++) {
			next = (next + step + options.length) % options.length
			if (!options[next].disabled) break
		}
		active = next
	}

	function handleKeydown(event: KeyboardEvent): void {
		if (event.key === 'Escape' && open) {
			event.preventDefault()
			open = false
		} else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
			event.preventDefault()
			if (!open) show()
			else move(event.key === 'ArrowDown' ? 1 : -1)
		} else if ((event.key === 'Enter' || event.key === ' ') && open) {
			event.preventDefault()
			const option = options[active]
			if (option) choose(option)
		}
	}

	$effect(() => {
		if (!open) return
		const close = (event: PointerEvent) => {
			if (root && !root.contains(event.target as Node)) open = false
		}
		window.addEventListener('pointerdown', close)
		return () => window.removeEventListener('pointerdown', close)
	})
</script>

<div class="relative" bind:this={root}>
	<button
		{id}
		bind:this={trigger}
		type="button"
		class="select-trigger"
		aria-haspopup="listbox"
		aria-expanded={open}
		aria-label={ariaLabel}
		data-value={value}
		{disabled}
		onclick={() => (open ? (open = false) : show())}
		onkeydown={handleKeydown}
	>
		<span class="min-w-0 flex-1 truncate text-left">{selected?.label ?? placeholder}</span>
		<span
			class="i-carbon-chevron-down size-4 shrink-0 text-zinc-400 transition-transform {open
				? 'rotate-180'
				: ''}"
		></span>
	</button>
	{#if open}
		<div
			role="listbox"
			aria-label={ariaLabel}
			class="select-menu {upward ? 'bottom-full mb-1.5' : 'top-full mt-1.5'}"
		>
			{#each options as option, index (option.value)}
				<button
					type="button"
					role="option"
					aria-selected={option.value === value}
					aria-disabled={option.disabled}
					data-value={option.value}
					class="select-option {index === active ? 'select-option-active' : ''}"
					disabled={option.disabled}
					onpointerenter={() => (active = index)}
					onclick={() => choose(option)}
				>
					<span class="min-w-0 flex-1 text-left">
						<span class="block truncate">{option.label}</span>
						{#if option.hint}
							<span class="block truncate text-xs text-zinc-500">{option.hint}</span>
						{/if}
					</span>
					{#if option.value === value}
						<span class="i-carbon-checkmark size-4 shrink-0 text-accent-500"></span>
					{/if}
				</button>
			{/each}
		</div>
	{/if}
</div>
