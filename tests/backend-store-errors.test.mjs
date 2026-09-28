import assert from 'node:assert/strict'
import test from 'node:test'
import { get } from 'svelte/store'
import { backend } from '../electron/src/renderer/lib/stores/backend.ts'

test('background errors clear themselves and can be dismissed', async (context) => {
	context.mock.timers.enable({ apis: ['setTimeout'] })
	let emit
	globalThis.window = {
		api: {
			backend: {
				onEvent: (listener) => {
					emit = listener
					return () => undefined
				},
				onFailure: () => () => undefined,
				getState: async () => ({
					sessionId: 's',
					seq: 0,
					backend: 'ready',
					recording: 'idle',
					recordingSource: null,
					recordings: { microphone: 'idle', speaker: 'idle' },
					models: {},
					overlay: { status: 'disabled', message: null },
					settings: { translation: { enabled: true } }
				}),
				listAudioDevices: async () => ({ devices: [] }),
				listTranslationProviders: async () => ({ providers: [] })
			}
		}
	}
	const disconnect = await backend.connect()
	context.after(disconnect)
	let seq = 0
	const send = (name, data) =>
		emit({ v: 1, type: 'event', sessionId: 's', seq: ++seq, event: name, data })

	send('translation.error', { jobId: 'j', message: '翻譯失敗', context: {} })
	assert.equal(get(backend.error), '翻譯失敗')
	context.mock.timers.tick(7_999)
	assert.equal(get(backend.error), '翻譯失敗')
	context.mock.timers.tick(1)
	assert.equal(get(backend.error), null)

	send('error', { code: 'VOICE_ERROR', message: '語音錯誤', retryable: true })
	assert.equal(get(backend.error), '語音錯誤')
	backend.dismissError()
	assert.equal(get(backend.error), null)
})
