<script lang="ts">
	/** A large on/off switch that is easy to hit with a VR laser pointer. */
	let {
		checked,
		disabled = false,
		label,
		onChange
	}: {
		checked: boolean
		disabled?: boolean
		label: string
		onChange: (checked: boolean) => void
	} = $props()
</script>

<button
	type="button"
	role="switch"
	class="switch"
	aria-checked={checked}
	aria-label={label}
	{disabled}
	onclick={() => onChange(!checked)}
>
	<span class="switch-thumb"></span>
</button>
