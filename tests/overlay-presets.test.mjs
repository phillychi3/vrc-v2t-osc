import assert from 'node:assert/strict'
import test from 'node:test'
import { OVERLAY_PRESETS, matchOverlayPreset } from '../electron/src/shared/overlay-presets.ts'

test('recognizes presets and treats adjusted placements as custom', () => {
	for (const [id, preset] of Object.entries(OVERLAY_PRESETS))
		assert.equal(matchOverlayPreset(preset), id)
	assert.equal(matchOverlayPreset({ ...OVERLAY_PRESETS.leftWatch, roll: -35 }), null)
})

test('watch presets mirror each other across the hands', () => {
	const left = OVERLAY_PRESETS.leftWatch
	const right = OVERLAY_PRESETS.rightWatch
	// Sums instead of negation: strict equality tells 0 and -0 apart.
	assert.equal(left.offsetX + right.offsetX, 0)
	assert.equal(left.roll + right.roll, 0)
	assert.equal(left.yaw + right.yaw, 0)
	assert.equal(left.pitch, right.pitch)
	assert.equal(left.offsetY, right.offsetY)
	assert.equal(left.watchMode, true)
	assert.equal(right.watchMode, true)
	assert.equal(OVERLAY_PRESETS.head.watchMode, false)
})
