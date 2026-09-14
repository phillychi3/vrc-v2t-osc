import assert from 'node:assert/strict'
import { writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { setTimeout as delay } from 'node:timers/promises'
import { app, BrowserWindow, ipcMain } from 'electron'
import serve from 'electron-serve'

// A test-only main process: production renderer and sandbox preload, fake IPC
// responses. No production test switches, model downloads, audio or OSC access.
app.setPath('userData', process.argv[2])
const loadRenderer = serve({ directory: resolve('build/renderer') })
const settings = {
	schemaVersion: 1,
	audio: { deviceId: 'default', speakerDeviceId: 'speaker:default', autoStart: false },
	speech: { model: 'auto', language: 'zh' },
	translation: {
		enabled: false,
		provider: 'transformers',
		model: 'facebook/nllb-200-distilled-600M',
		sourceLanguage: 'zh',
		targetLanguage: 'en',
		endpoint: 'http://127.0.0.1:5000',
		apiKey: '',
		deeplPlan: 'free',
		deeplApiKey: ''
	},
	osc: { enabled: true, host: '127.0.0.1', port: 9000 },
	emotion: { enabled: false, parameter: '/avatar/parameters/v2t_sync_emo' },
	privacy: { saveAudio: false, saveTranscripts: false }
}
const state = {
	sessionId: 'ui-1',
	seq: 0,
	backend: 'ready',
	recording: 'idle',
	recordingSource: null,
	recordings: { microphone: 'idle', speaker: 'idle' },
	models: { speech: { status: 'loading', device: 'cpu' } },
	settings
}
const sent = []
let restarts = 0
let window
const evidence = []
function emit(event, data) {
	window.webContents.send('backend:event', {
		v: 1,
		type: 'event',
		sessionId: state.sessionId,
		seq: ++state.seq,
		event,
		data
	})
}
function record(source, value) {
	state.recordings[source] = value
	emit('recording.state', { source, state: value })
	return { state: value }
}
ipcMain.handle('backend:get-state', () => structuredClone(state))
ipcMain.handle('backend:list-audio-devices', () => ({ devices: [] }))
ipcMain.handle('backend:list-translation-providers', () => ({
	providers: [{ id: 'transformers', label: 'Local', local: true }]
}))
ipcMain.handle('backend:start-recording', (_event, _device, source) => record(source, 'listening'))
ipcMain.handle('backend:stop-recording', (_event, source) => record(source, 'idle'))
ipcMain.handle('backend:update-settings', (_event, patch) => {
	for (const [key, value] of Object.entries(patch)) Object.assign(settings[key], value)
	return { settings }
})
ipcMain.handle('backend:send-text', (_event, text) => {
	if (text.length > 144) throw new Error('聊天文字最多 144 字')
	sent.push(text)
	const utteranceId = `manual-${sent.length}`
	emit('transcript.final', { utteranceId, text, source: 'manual' })
	emit('osc.sent', { utteranceId, kind: 'text' })
	return { utteranceId, accepted: true }
})
ipcMain.handle('backend:restart', () => {
	restarts++
	state.sessionId = 'ui-2'
	state.seq = 0
	state.recordings = { microphone: 'idle', speaker: 'idle' }
	return structuredClone(state)
})

const evaluate = (expression) => window.webContents.executeJavaScript(expression)
async function waitFor(expression) {
	const deadline = Date.now() + 5000
	do {
		if (await evaluate(expression)) return
		await delay(25)
	} while (Date.now() < deadline)
	throw new Error(`UI condition timed out: ${expression}`)
}
async function input(value, keyOptions = {}) {
	await evaluate(`(() => {
		const input = document.querySelector('textarea');
		input.value = ${JSON.stringify(value)};
		input.dispatchEvent(new Event('input', { bubbles: true }));
		input.dispatchEvent(new KeyboardEvent('keydown', {
			key: 'Enter', bubbles: true, cancelable: true, ...${JSON.stringify(keyOptions)}
		}));
	})()`)
}

async function run() {
	try {
		window = new BrowserWindow({
			width: 1180,
			height: 760,
			show: false,
			webPreferences: {
				preload: resolve('build/electron/preload.cjs'),
				sandbox: true,
				contextIsolation: true,
				nodeIntegration: false,
				backgroundThrottling: false,
				offscreen: true
			}
		})
		window.webContents.session.webRequest.onBeforeRequest((details, callback) => {
			callback({ cancel: /^https?:/.test(details.url) })
		})
		await loadRenderer(window)
		await waitFor(
			`document.querySelector('textarea') && !document.querySelector('textarea').disabled`
		)
		assert.equal(await evaluate(`document.querySelector('.feature-action').disabled`), true)
		await input('模型載入時也能送字')
		await waitFor(`document.querySelector('article')?.textContent.includes('OSC 已送出')`)
		assert.deepEqual(sent, ['模型載入時也能送字'])
		evidence.push('model loading disables recording but permits manual text; OSC status rendered')

		await evaluate(
			`document.querySelector('textarea').dispatchEvent(new CompositionEvent('compositionstart', { bubbles: true }))`
		)
		await input('正在選字')
		assert.equal(sent.length, 1)
		await evaluate(
			`document.querySelector('textarea').dispatchEvent(new CompositionEvent('compositionend', { bubbles: true }))`
		)
		await input('組字中', { isComposing: true })
		await input('相容組字', { keyCode: 229 })
		await input('第一行\n第二行', { shiftKey: true })
		assert.equal(sent.length, 1)
		await input('中文確認送出')
		await waitFor(`document.querySelector('textarea').value === ''`)
		assert.equal(sent.at(-1), '中文確認送出')
		await input('中'.repeat(145))
		await waitFor(`document.querySelector('[role=alert]')?.textContent.includes('144')`)
		assert.equal((await evaluate(`document.querySelector('textarea').value`)).length, 145)
		evidence.push(
			'composition events, isComposing, keyCode 229 and Shift+Enter do not send; rejected input retained'
		)

		state.models.speech.status = 'ready'
		emit('model.status', { model: 'speech', status: 'ready', device: 'cpu' })
		await waitFor(`!document.querySelector('.feature-action').disabled`)
		for (const selector of ['.feature-action-icon', 'strong', '.feature-action-state']) {
			await evaluate(
				`document.querySelectorAll('.feature-action')[1].querySelector('${selector}').click()`
			)
			await waitFor(
				`document.querySelectorAll('.feature-action')[1].getAttribute('aria-pressed') === 'true'`
			)
			await evaluate(`document.querySelectorAll('.feature-action')[1].click()`)
			await waitFor(
				`document.querySelectorAll('.feature-action')[1].getAttribute('aria-pressed') === 'false'`
			)
		}
		await evaluate(`document.querySelector('.titlebar-nav').click()`)
		await waitFor(`document.querySelector('#speech-model')?.value === 'auto'`)
		await evaluate(`(() => {
		const select = document.querySelector('#speech-model');
		select.value = 'small'; select.dispatchEvent(new Event('change', { bubbles: true }));
	})()`)
		await waitFor(`document.querySelector('#speech-model').value === 'small'`)
		assert.equal(settings.speech.model, 'small')
		await evaluate(`document.querySelector('.titlebar-nav').click()`)
		await waitFor(`Boolean(document.querySelector('textarea'))`)
		evidence.push(
			'speaker icon, title, badge and card click toggle the same action; settings change persists through IPC'
		)

		window.webContents.send('backend:failure', '測試後端中斷')
		await waitFor(
			`document.querySelector('textarea').disabled && Boolean(document.querySelector('[role=alert] button'))`
		)
		await evaluate(`document.querySelector('[role=alert] button').click()`)
		await waitFor(`!document.querySelector('textarea').disabled`)
		assert.equal(restarts, 1)
		assert.equal(sent.length, 2)
		assert.equal(await evaluate(`document.querySelectorAll('article').length`), 2)
		evidence.push('failure disables input; restart retains history without resending text')

		for (let i = 0; i < 1005; i++) {
			emit('transcript.final', { utteranceId: `load-${i}`, text: `紀錄 ${i}`, source: 'speaker' })
		}
		await waitFor(`document.querySelectorAll('article').length === 1000`)
		await evaluate(`document.querySelector('[aria-label="逐字稿訊息"]').scrollTop = 800`)
		await delay(50)
		const before = await evaluate(`(() => {
		const region = document.querySelector('[aria-label="逐字稿訊息"]');
		const top = region.getBoundingClientRect().top;
		const item = [...region.querySelectorAll('article')].find(item => item.getBoundingClientRect().top >= top);
		return { text: item.querySelector('p').textContent, y: item.getBoundingClientRect().top };
	})()`)
		emit('transcript.final', { utteranceId: 'newest', text: '最新喇叭文字', source: 'speaker' })
		await waitFor(`document.querySelector('article').textContent.includes('最新喇叭文字')`)
		await delay(50)
		const after = await evaluate(`(() => {
		const item = [...document.querySelectorAll('article')].find(item => item.querySelector('p').textContent === ${JSON.stringify(before.text)});
		return item.getBoundingClientRect().top;
	})()`)
		assert.ok(Math.abs(before.y - after) < 2, 'new text must preserve the visible reading position')
		assert.equal(await evaluate(`document.querySelectorAll('article').length`), 1000)
		assert.equal(
			await evaluate(`document.querySelectorAll('[aria-label="逐字稿訊息"] button').length`),
			0
		)
		evidence.push(
			'1000-entry cap; adding text preserves visible scroll anchor; history has no unrelated buttons'
		)
		window.webContents.invalidate()
		await delay(150)
		await writeFile('build/p5-ui.png', (await window.webContents.capturePage()).toPNG())
		await writeFile(
			'build/p5-ui-evidence.json',
			JSON.stringify({ passed: true, evidence }, null, 2)
		)
		console.log(`Electron UI regression passed: ${evidence.length} scenarios`)
		window.destroy()
		app.exit(0)
	} catch (error) {
		console.error(error)
		if (window && !window.isDestroyed()) {
			await writeFile('build/p5-ui-failure.png', (await window.webContents.capturePage()).toPNG())
		}
		app.exit(1)
	}
}

// Electron waits for ESM entry evaluation before emitting ready.
void app.whenReady().then(run)
