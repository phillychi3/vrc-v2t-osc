import { writable } from 'svelte/store'
import type { MessageKey } from '$shared/i18n'

/**
 * Interface size. In VR the window is usually read through a desktop overlay
 * (SteamVR dashboard, XSOverlay, OVR Toolkit) at some distance, so larger text
 * and targets help. Every size is rem-based, so this scales the whole UI.
 */
export type UiScale = 'standard' | 'large' | 'xlarge'

export const UI_SCALES: { value: UiScale; label: MessageKey; rootPx: number }[] = [
	{ value: 'standard', label: 'interface.scale.standard', rootPx: 16 },
	{ value: 'large', label: 'interface.scale.large', rootPx: 18 },
	{ value: 'xlarge', label: 'interface.scale.xlarge', rootPx: 20 }
]

const STORAGE_KEY = 'vrc2t.uiScale'

function load(): UiScale {
	try {
		const stored = localStorage.getItem(STORAGE_KEY)
		if (UI_SCALES.some((scale) => scale.value === stored)) return stored as UiScale
	} catch {
		// Storage can be unavailable; the standard size still works.
	}
	return 'standard'
}

function apply(scale: UiScale): void {
	const px = UI_SCALES.find((entry) => entry.value === scale)?.rootPx ?? 16
	document.documentElement.style.fontSize = `${px}px`
}

const store = writable<UiScale>(load())
store.subscribe(apply)

export const uiScale = {
	subscribe: store.subscribe,
	set(scale: UiScale): void {
		store.set(scale)
		try {
			localStorage.setItem(STORAGE_KEY, scale)
		} catch {
			// Not persisted; the choice still applies for this session.
		}
	}
}
