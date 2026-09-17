import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { createServer } from 'node:net'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'

const executable = resolve(process.argv[2] ?? 'release/win-unpacked/VRC2T.exe')
const userData = await mkdtemp(`${tmpdir()}\\vrc-v2t-renderer-smoke-`)
const port = await availablePort()
const logs = []
const electronEnvironment = { ...process.env }
delete electronEnvironment.ELECTRON_RUN_AS_NODE
const child = spawn(
	executable,
	[`--remote-debugging-port=${port}`, `--user-data-dir=${userData}`, '--enable-logging=stderr'],
	{ env: electronEnvironment, stdio: ['ignore', 'ignore', 'pipe'], windowsHide: true }
)
child.stderr.setEncoding('utf8')
child.stderr.on('data', (chunk) => logs.push(chunk))

try {
	await waitForTarget(port, 30_000, (item) => item.title.includes('正在啟動'))
	const target = await waitForTarget(port, 30_000, (item) => item.title === 'VRC Voice to Text')
	const cdp = await connectCdp(target.webSocketDebuggerUrl)
	try {
		await waitFor(async () => {
			const state = await cdp.evaluate(`({
				ready: document.readyState,
				api: Boolean(window.api?.backend && window.api?.window),
				settingsButton: Boolean(document.querySelector('.titlebar-nav'))
			})`)
			return state.ready === 'complete' && state.api && state.settingsButton
		}, 15_000)

		const iconState = await cdp.evaluate(`Array.from(document.querySelectorAll('svg path')).map(
			(path) => getComputedStyle(path).getPropertyValue('d')
		)`)
		assert.ok(iconState.length >= 5, 'expected the bundled SVG icons to be rendered')
		assert.ok(
			iconState.every((path) => path && path !== 'none'),
			'an SVG icon has no path data'
		)

		await waitFor(async () => {
			const modelState = await cdp.evaluate(`({
				ready: !document.querySelector('.feature-action')?.disabled,
				error: document.querySelector('[role="alert"]')?.textContent?.trim()
			})`)
			if (modelState.error) throw new Error(modelState.error)
			return modelState.ready
		}, 180_000)

		await cdp.evaluate(`document.querySelector('.titlebar-nav').click()`)
		await waitFor(
			async () =>
				(await cdp.evaluate(`document.querySelector('.titlebar-nav')?.textContent`))?.includes(
					'返回主畫面'
				),
			5_000
		)

		const modelOptions = await cdp.evaluate(`({
			selectedSpeech: document.querySelector('#speech-model')?.value,
			speech: Array.from(document.querySelectorAll('#speech-model option')).map(
				(option) => option.value
			),
			translation: Array.from(document.querySelectorAll('#translation-model option')).map(
				(option) => option.value
			)
		})`)
		assert.ok(modelOptions.speech.includes('large-v3-turbo'), 'speech model selector is missing')
		assert.ok(modelOptions.speech.includes('auto'), 'automatic speech model option is missing')
		assert.equal(modelOptions.selectedSpeech, 'auto', 'fresh installs should use automatic speech')
		assert.ok(
			modelOptions.translation.includes('venddair/nllb-200-distilled-600M-onnx'),
			'translation model selector is missing'
		)
		// A small speech model can be ready before device enumeration finishes.
		await waitFor(async () => {
			const label = await cdp.evaluate(
				`document.querySelector('#speaker-device input')?.value?.trim() ?? ''`
			)
			return label.includes('系統預設喇叭')
		}, 15_000)
		const integrationState = await cdp.evaluate(`({
			speakerDevice: Boolean(document.querySelector('#speaker-device')),
			speakerDeviceText:
				document.querySelector('#speaker-device input')?.value?.trim() ?? '',
			providers: Array.from(document.querySelectorAll('#translation-provider option')).map(
				(option) => option.value
			)
		})`)
		assert.ok(integrationState.speakerDevice, 'speaker device selector is missing')
		assert.ok(
			integrationState.speakerDeviceText.includes('系統預設喇叭'),
			`default speaker loopback device was not listed: ${integrationState.speakerDeviceText}`
		)
		assert.ok(integrationState.providers.includes('deepl'), 'DeepL provider is missing')

		await cdp.evaluate(`document.querySelector('.titlebar-control-close').click()`)
	} finally {
		cdp.close()
	}
	await waitForExit(child, 15_000)
	console.log(`packaged renderer smoke test passed: ${executable}`)
} catch (error) {
	child.kill()
	throw new Error(`${error.message}\n${logs.join('').slice(-8_000)}`, { cause: error })
} finally {
	await rm(userData, { recursive: true, force: true }).catch(() => undefined)
}

async function availablePort() {
	const server = createServer()
	await new Promise((resolve, reject) => {
		server.once('error', reject)
		server.listen(0, '127.0.0.1', resolve)
	})
	const address = server.address()
	const port = typeof address === 'object' && address ? address.port : 0
	await new Promise((resolve, reject) =>
		server.close((error) => (error ? reject(error) : resolve()))
	)
	return port
}

async function waitForTarget(port, timeout, matches = () => true) {
	let latestError
	const deadline = Date.now() + timeout
	while (Date.now() < deadline) {
		try {
			const response = await fetch(`http://127.0.0.1:${port}/json/list`)
			const targets = await response.json()
			const target = targets.find((item) => item.type === 'page' && matches(item))
			if (target) return target
		} catch (error) {
			latestError = error
		}
		await delay(100)
	}
	throw latestError ?? new Error('timed out waiting for the Electron renderer')
}

async function connectCdp(url) {
	const socket = new WebSocket(url)
	await new Promise((resolve, reject) => {
		socket.addEventListener('open', resolve, { once: true })
		socket.addEventListener('error', reject, { once: true })
	})
	let nextId = 0
	const pending = new Map()
	socket.addEventListener('message', ({ data }) => {
		const message = JSON.parse(data)
		const entry = pending.get(message.id)
		if (!entry) return
		pending.delete(message.id)
		if (message.error) entry.reject(new Error(message.error.message))
		else entry.resolve(message.result)
	})
	return {
		async evaluate(expression) {
			const id = ++nextId
			const response = new Promise((resolve, reject) => pending.set(id, { resolve, reject }))
			socket.send(
				JSON.stringify({
					id,
					method: 'Runtime.evaluate',
					params: { expression, awaitPromise: true, returnByValue: true }
				})
			)
			const result = await response
			if (result.exceptionDetails) throw new Error(result.exceptionDetails.text)
			return result.result.value
		},
		close() {
			socket.close()
		}
	}
}

async function waitFor(check, timeout) {
	const deadline = Date.now() + timeout
	while (Date.now() < deadline) {
		if (await check()) return
		await delay(100)
	}
	throw new Error('condition did not become true before timeout')
}

async function waitForExit(process, timeout) {
	if (process.exitCode !== null) return
	await Promise.race([
		new Promise((resolve) => process.once('exit', resolve)),
		new Promise((_, reject) =>
			setTimeout(() => reject(new Error('application did not close')), timeout)
		)
	])
}

function delay(milliseconds) {
	return new Promise((resolve) => setTimeout(resolve, milliseconds))
}
