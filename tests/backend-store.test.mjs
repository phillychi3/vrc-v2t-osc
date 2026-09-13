import assert from 'node:assert/strict'
import test from 'node:test'
import { get } from 'svelte/store'
import { backend } from '../electron/src/renderer/lib/stores/backend.ts'

test('renderer preserves history across failure/restart, rejects old events and cleans subscriptions', async () => {
	let eventListener
	let failureListener
	let cleanups = 0
	let restartCalls = 0
	const snapshot = (sessionId) => ({
		sessionId,
		seq: 0,
		backend: 'ready',
		recording: 'idle',
		recordingSource: null,
		recordings: { microphone: 'idle', speaker: 'idle' },
		models: {},
		settings: { translation: { enabled: true } }
	})
	const event = (sessionId, seq, name, data) => ({
		v: 1,
		type: 'event',
		sessionId,
		seq,
		event: name,
		data
	})
	globalThis.window = {
		api: {
			backend: {
				onEvent: (listener) => {
					eventListener = listener
					return () => {
						cleanups++
					}
				},
				onFailure: (listener) => {
					failureListener = listener
					return () => {
						cleanups++
					}
				},
				getState: async () => {
					eventListener(
						event('old', 1, 'transcript.final', { utteranceId: '1', text: '你好', source: 'voice' })
					)
					return snapshot('old')
				},
				listAudioDevices: async () => ({ devices: [] }),
				listTranslationProviders: async () => ({ providers: [] }),
				restart: async () => {
					restartCalls++
					return snapshot('new')
				}
			}
		}
	}
	try {
		const disconnect = await backend.connect()
		assert.equal(get(backend.transcripts).length, 1, 'events during initial snapshot are replayed')
		failureListener('crashed')
		assert.equal(get(backend.connected), false)
		assert.equal(get(backend.failed), true)
		assert.equal(get(backend.transcripts)[0].translationPending, false)
		await Promise.all([backend.restart(), backend.restart()])
		assert.equal(restartCalls, 1)
		assert.equal(get(backend.connected), true)
		assert.equal(get(backend.transcripts)[0].text, '你好')
		eventListener(
			event('old', 100, 'transcript.final', { utteranceId: 'stale', text: 'old', source: 'voice' })
		)
		assert.equal(get(backend.transcripts).length, 1)
		for (let i = 1; i <= 1005; i++) {
			eventListener(
				event('new', i, 'transcript.final', {
					utteranceId: String(i),
					text: String(i),
					source: 'manual'
				})
			)
		}
		assert.equal(get(backend.transcripts).length, 1000)
		disconnect()
		assert.equal(cleanups, 2)
	} finally {
		delete globalThis.window
	}
})
