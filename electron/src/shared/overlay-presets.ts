import type { OverlaySettings } from './ipc.js'

export type OverlayPlacement = Pick<
	OverlaySettings,
	'anchor' | 'offsetX' | 'offsetY' | 'offsetZ' | 'pitch' | 'yaw' | 'roll' | 'scale' | 'watchMode'
>

export type OverlayPresetId = 'head' | 'leftWatch' | 'rightWatch' | 'leftHand' | 'rightHand'

/** Row-major 3x4 identity: the panel rests on the anchor's own axes. */
export const IDENTITY_BASIS = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0]

/**
 * Starting points for the caption panel. Offsets are meters and rotations are
 * degrees along the panel's own axes. Watch presets have no adjustments: the
 * pose comes from calibration, where the wearer raises the arm and looks at
 * it, so the panel faces them regardless of the controller model.
 */
export const OVERLAY_PRESETS: Record<OverlayPresetId, OverlayPlacement> = {
	head: {
		anchor: 'head',
		offsetX: 0,
		offsetY: -0.35,
		offsetZ: -1.5,
		pitch: 0,
		yaw: 0,
		roll: 0,
		scale: 1,
		watchMode: false
	},
	leftWatch: {
		anchor: 'leftHand',
		offsetX: 0,
		offsetY: 0,
		offsetZ: 0,
		pitch: 0,
		yaw: 0,
		roll: 0,
		scale: 0.6,
		watchMode: true
	},
	rightWatch: {
		anchor: 'rightHand',
		offsetX: 0,
		offsetY: 0,
		offsetZ: 0,
		pitch: 0,
		yaw: 0,
		roll: 0,
		scale: 0.6,
		watchMode: true
	},
	// Floats above the controller, facing back toward the wearer.
	leftHand: {
		anchor: 'leftHand',
		offsetX: 0,
		offsetY: 0.12,
		offsetZ: 0.05,
		pitch: -20,
		yaw: 0,
		roll: 0,
		scale: 1,
		watchMode: false
	},
	rightHand: {
		anchor: 'rightHand',
		offsetX: 0,
		offsetY: 0.12,
		offsetZ: 0.05,
		pitch: -20,
		yaw: 0,
		roll: 0,
		scale: 1,
		watchMode: false
	}
}

const PLACEMENT_KEYS = Object.keys(OVERLAY_PRESETS.head) as (keyof OverlayPlacement)[]

export function matchOverlayPreset(settings: OverlayPlacement): OverlayPresetId | null {
	for (const [id, preset] of Object.entries(OVERLAY_PRESETS) as [
		OverlayPresetId,
		OverlayPlacement
	][]) {
		if (
			PLACEMENT_KEYS.every((key) =>
				typeof preset[key] === 'number'
					? Math.abs((settings[key] as number) - (preset[key] as number)) < 1e-6
					: settings[key] === preset[key]
			)
		)
			return id
	}
	return null
}

export function placementOf(settings: OverlaySettings): OverlayPlacement {
	return Object.fromEntries(PLACEMENT_KEYS.map((key) => [key, settings[key]])) as OverlayPlacement
}
