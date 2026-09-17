import assert from 'node:assert/strict'
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import { SettingsStore } from '../electron/src/main/settings-store.ts'

test('uses defaults when settings.json does not exist', async (context) => {
	const directory = await temporaryDirectory(context)
	const settings = await new SettingsStore(directory).load()

	assert.equal(settings.schemaVersion, 1)
	assert.equal(settings.speech.model, 'auto')
	assert.equal(settings.osc.host, '127.0.0.1')
	assert.equal(settings.osc.port, 9000)
	assert.equal(settings.translation.provider, 'onnx')
	assert.equal(settings.audio.speakerDeviceId, 'speaker:default')
	assert.equal(settings.translation.deeplPlan, 'free')
})

test('saves validated settings and can replace an existing file', async (context) => {
	const directory = await temporaryDirectory(context)
	const store = new SettingsStore(directory)
	const settings = await store.load()
	settings.osc.port = 9001
	settings.speech.model = 'large-v3-turbo'

	await store.save(settings)
	settings.osc.port = 9002
	await store.save(settings)

	assert.equal((await store.load()).osc.port, 9002)
	assert.equal((await store.load()).speech.model, 'large-v3-turbo')
	assert.equal(JSON.parse(await readFile(store.path, 'utf8')).osc.port, 9002)
})

test('rejects malformed persisted settings and falls back to defaults', async (context) => {
	const directory = await temporaryDirectory(context)
	const store = new SettingsStore(directory)
	await writeFile(store.path, '{"schemaVersion":2}\n', 'utf8')

	assert.equal((await store.load()).schemaVersion, 1)
})

async function temporaryDirectory(context) {
	const directory = await mkdtemp(join(tmpdir(), 'vrc-v2t-settings-'))
	context.after(() => rm(directory, { recursive: true, force: true }))
	return directory
}

test('unsupported persisted models reset the whole settings document', async (context) => {
	const directory = await temporaryDirectory(context)
	const store = new SettingsStore(directory)
	const settings = await store.load()
	settings.translation.model = 'unsupported-model'
	settings.osc.port = 9010
	await store.save(settings)
	const loaded = await store.load()
	assert.equal(loaded.translation.provider, 'onnx')
	assert.equal(loaded.translation.model, 'venddair/nllb-200-distilled-600M-onnx')
	assert.equal(loaded.osc.port, 9000)
})
