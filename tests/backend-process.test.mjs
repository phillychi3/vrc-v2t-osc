import assert from 'node:assert/strict'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import { BackendError, BackendProcess } from '../electron/src/main/backend-process.ts'

const projectRoot = dirname(dirname(fileURLToPath(import.meta.url)))
const fixture = join(projectRoot, 'tests', 'fixtures', 'backend-child.mjs')
const settings = {
	schemaVersion: 1,
	audio: { deviceId: 'default', autoStart: false },
	speech: { model: 'test', language: 'zh' },
	osc: { enabled: false, host: '127.0.0.1', port: 9000 },
	emotion: { enabled: false, parameter: '/test' },
	privacy: { saveAudio: false, saveTranscripts: false }
}

test('matches responses by id and forwards events', async () => {
	const backend = createBackend('event')
	const eventPromise = new Promise((resolve) => backend.once('event', resolve))
	await backend.start(settings, projectRoot)
	await backend.request('system.getState', {})
	assert.equal((await eventPromise).event, 'fixture.event')
	await backend.stop()
})

test('rejects timed out requests without stopping the child', async () => {
	const backend = createBackend('timeout')
	await backend.start(settings, projectRoot)
	await assert.rejects(
		backend.request('system.getState', {}, 20),
		(error) => error instanceof BackendError && error.code === 'REQUEST_TIMEOUT'
	)
	await backend.stop()
})

test('allows callers to extend the timeout while the backend is busy', async () => {
	const backend = createBackend('delayed')
	await backend.start(settings, projectRoot)
	const result = await backend.request('system.getState', {}, 1_000)
	assert.equal(result.backend, 'ready')
	await backend.stop()
})

test('rejects pending requests when the child exits', async () => {
	const backend = createBackend('exit')
	backend.on('error', () => undefined)
	await backend.start(settings, projectRoot)
	await assert.rejects(
		backend.request('system.getState', {}),
		(error) => error instanceof BackendError && error.code === 'BACKEND_EXITED'
	)
})

test('kills the child after malformed NDJSON', async () => {
	const backend = createBackend('malformed')
	const protocolError = new Promise((resolve) => backend.once('error', resolve))
	await backend.start(settings, projectRoot)
	void backend.request('system.getState', {}).catch(() => undefined)
	const error = await protocolError
	assert.ok(error instanceof BackendError)
	assert.equal(error.code, 'PROTOCOL_ERROR')
})

function createBackend(mode) {
	return new BackendProcess({
		command: process.execPath,
		args: [fixture, mode],
		cwd: projectRoot
	})
}

test('stop waits for the child to exit before allowing another start', async () => {
	const backend = createBackend('delayed-shutdown')
	await backend.start(settings, projectRoot)
	const started = Date.now()
	await Promise.all([backend.stop(), backend.stop()])
	assert.ok(Date.now() - started >= 80)
	await backend.start(settings, projectRoot)
	await backend.stop()
})

test('stop forces a hung backend to exit within its shutdown budget', async () => {
	const backend = createBackend('hung-shutdown')
	await backend.start(settings, projectRoot)
	const started = Date.now()
	await backend.stop(50)
	assert.ok(Date.now() - started < 2500)
	await assert.rejects(backend.request('system.getState', {}), { code: 'BACKEND_UNAVAILABLE' })
})

test('restart is serialized, keeps preferences, and does not replay text or recording', async () => {
	const backend = createBackend('normal')
	const preferences = { ...settings, audio: { ...settings.audio, autoStart: true } }
	const initial = await backend.start(preferences, projectRoot)
	await backend.request('text.send', { text: 'only once' })
	const [first, second] = await Promise.all([
		backend.restart(preferences, projectRoot),
		backend.restart(preferences, projectRoot)
	])
	assert.equal(first.sessionId, second.sessionId)
	assert.notEqual(first.sessionId, initial.sessionId)
	assert.equal(first.settings.audio.autoStart, true)
	assert.equal(first.suppressAutoStart, true)
	assert.equal(first.sentCount, 0)
	await backend.stop()
})

test('quitting during restart cannot launch another backend', async () => {
	const backend = createBackend('delayed-shutdown')
	await backend.start(settings, projectRoot)
	const restarting = assert.rejects(backend.restart(settings, projectRoot), {
		code: 'SHUTTING_DOWN'
	})
	await backend.shutdown()
	await restarting
	await assert.rejects(backend.start(settings, projectRoot), { code: 'SHUTTING_DOWN' })
})

for (const mode of ['null', 'missing-error', 'invalid-event']) {
	test(`rejects ${mode} messages without crashing the main process`, async () => {
		const backend = createBackend(mode)
		const protocolError = new Promise((resolve) => backend.once('error', resolve))
		await backend.start(settings, projectRoot)
		const rejected = assert.rejects(backend.request('system.getState', {}))
		assert.equal((await protocolError).code, 'PROTOCOL_ERROR')
		await rejected
	})
}

test('preserves Chinese characters split across stdout chunks', async () => {
	const backend = createBackend('split-chinese')
	try {
		await backend.start(settings, projectRoot)
		const event = new Promise((resolve) => backend.once('event', resolve))
		await backend.request('system.getState', {})
		assert.equal((await event).data.text, '你好世界')
	} finally {
		await backend.stop()
	}
})
