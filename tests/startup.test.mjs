import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { stripTypeScriptTypes } from 'node:module'
import { EventEmitter } from 'node:events'
import { setImmediate } from 'node:timers/promises'
import { runInNewContext } from 'node:vm'
import test from 'node:test'

// Execute the production bootstrap with OS/UI boundaries replaced. No Electron
// window, Python process, model download or audio device is touched.
const source = readFileSync(new URL('../electron/src/main/index.ts', import.meta.url), 'utf8')

async function boot(response = 1, sourceText = source) {
	const script = stripTypeScriptTypes(sourceText).replace(/^import .*\r?\n/gm, '')
	const starts = []
	const calls = { main: 0, shown: 0, splashClosed: 0, dialogs: 0, stopped: 0, quit: 0 }
	const instances = {}
	class FakeBackend extends EventEmitter {
		constructor() {
			super()
			instances.backend = this
		}
		start() {
			return new Promise((resolve, reject) => starts.push({ resolve, reject }))
		}
		async stop() {
			calls.stopped++
		}
		async shutdown() {}
	}
	const app = new EventEmitter()
	Object.assign(app, {
		requestSingleInstanceLock: () => true,
		whenReady: async () => undefined,
		getPath: () => 'test-data',
		quit: () => calls.quit++
	})
	runInNewContext(script, {
		app,
		console,
		process,
		BackendProcess: FakeBackend,
		resolveBackendCommand: () => ({}),
		SettingsStore: class {
			async load() {
				return {}
			}
		},
		registerIpcHandlers: () => undefined,
		applySecurityPolicy: () => undefined,
		createSplashWindow: async () => undefined,
		createMainWindow: async () => {
			calls.main++
			return { show: () => calls.shown++ }
		},
		closeSplashWindow: () => calls.splashClosed++,
		getMainWindow: () => null,
		BrowserWindow: { getAllWindows: () => [] },
		IpcChannel: {},
		dialog: {
			showMessageBox: async () => {
				calls.dialogs++
				return { response }
			}
		}
	})
	await setImmediate()
	return { starts, calls, backend: instances.backend }
}

test('bootstrap harness supports Windows CRLF source files', async () => {
	const { starts, calls } = await boot(1, source.replace(/\r?\n/g, '\r\n'))
	starts[0].resolve({ models: { speech: { status: 'ready' } } })
	await setImmediate()
	assert.equal(calls.main, 1)
	assert.equal(calls.shown, 1)
	assert.equal(calls.splashClosed, 1)
})

test('main window waits for successful speech initialization, not backend.ready', async () => {
	const { starts, calls, backend } = await boot()
	backend.emit('event', { event: 'backend.ready' })
	await setImmediate()
	assert.equal(calls.main, 0)
	assert.equal(calls.splashClosed, 0)
	starts[0].resolve({ models: { speech: { status: 'ready' } } })
	await setImmediate()
	assert.equal(calls.main, 1)
	assert.equal(calls.shown, 1)
	assert.equal(calls.splashClosed, 1)
})

test('failed speech load offers retry without revealing main window', async () => {
	const { starts, calls } = await boot(0)
	starts[0].resolve({ models: { speech: { status: 'failed' } } })
	await setImmediate()
	assert.equal(calls.main, 0)
	assert.equal(calls.dialogs, 1)
	assert.equal(calls.stopped, 1)
	assert.equal(starts.length, 2)
	starts[1].resolve({ models: { speech: { status: 'ready' } } })
	await setImmediate()
	assert.equal(calls.shown, 1)
})

test('initialization failure can exit without opening main window', async () => {
	const { starts, calls } = await boot(1)
	starts[0].reject(new Error('Backend exited'))
	await setImmediate()
	assert.equal(calls.main, 0)
	assert.equal(calls.dialogs, 1)
	assert.equal(calls.quit, 1)
})
