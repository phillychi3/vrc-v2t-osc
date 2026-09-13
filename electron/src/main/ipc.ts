import { BrowserWindow, app, ipcMain } from 'electron'
import type { AppInfo, Platform, TranslationRequest } from '../shared/ipc.js'
import { IpcChannel } from '../shared/ipc.js'
import { isDev, devServerUrl } from './env.js'
import { getMainWindow } from './window.js'
import { isTrustedRendererUrl } from './security-policy.js'
import type { BackendProcess } from './backend-process.js'
import type { SettingsStore } from './settings-store.js'

// Importing and warming the speech models can temporarily starve Python's
// command loop. These operations are safe to wait for and must not report a
// false failure while the backend is still making progress.
const MODEL_WARMUP_REQUEST_TIMEOUT_MS = 120_000

function windowOf(event: Electron.IpcMainEvent): BrowserWindow | null {
	if (!isTrustedSender(event)) return null
	return BrowserWindow.fromWebContents(event.sender)
}

function isTrustedSender(event: Electron.IpcMainEvent | Electron.IpcMainInvokeEvent): boolean {
	const window = getMainWindow()
	return Boolean(
		window &&
		!window.isDestroyed() &&
		event.sender === window.webContents &&
		event.senderFrame === window.webContents.mainFrame &&
		isTrustedRendererUrl(event.senderFrame?.url ?? '', isDev ? devServerUrl : undefined)
	)
}

const handle: typeof ipcMain.handle = (channel, listener) => {
	ipcMain.handle(channel, (event, ...args) => {
		if (!isTrustedSender(event)) throw new Error('不允許的 IPC 來源')
		return listener(event, ...args)
	})
}

export function registerIpcHandlers(
	backend: BackendProcess,
	settingsStore: SettingsStore,
	canRestart: () => boolean = () => true
): void {
	handle(IpcChannel.AppInfo, (): AppInfo => {
		if (isDev) console.info(`Renderer IPC connected: ${IpcChannel.AppInfo}`)
		return {
			name: app.getName(),
			version: app.getVersion(),
			platform: process.platform as Platform,
			arch: process.arch,
			isDev,
			versions: {
				electron: process.versions.electron,
				chrome: process.versions.chrome,
				node: process.versions.node,
				v8: process.versions.v8
			}
		}
	})

	handle(IpcChannel.BackendGetState, () =>
		backend.request('system.getState', {}, MODEL_WARMUP_REQUEST_TIMEOUT_MS)
	)
	handle(IpcChannel.BackendRestart, async () => {
		if (!canRestart()) throw new Error('應用程式正在關閉')
		const settings = await settingsStore.load()
		if (!canRestart()) throw new Error('應用程式正在關閉')
		return backend.restart(settings, app.getPath('userData'))
	})
	handle(IpcChannel.BackendListAudioDevices, () =>
		backend.request('audio.listDevices', {}, MODEL_WARMUP_REQUEST_TIMEOUT_MS)
	)
	handle(IpcChannel.BackendStartRecording, (_event, deviceId: unknown, source: unknown) => {
		if (typeof deviceId !== 'string' || !deviceId) throw new TypeError('deviceId 必須是非空字串')
		if (source !== 'microphone' && source !== 'speaker')
			throw new TypeError('source 必須是 microphone 或 speaker')
		return backend.request('recording.start', { deviceId, source })
	})
	handle(IpcChannel.BackendStopRecording, (_event, source: unknown) => {
		if (source !== 'microphone' && source !== 'speaker')
			throw new TypeError('source 必須是 microphone 或 speaker')
		return backend.request('recording.stop', { source }, MODEL_WARMUP_REQUEST_TIMEOUT_MS)
	})
	handle(IpcChannel.BackendListTranslationProviders, () =>
		backend.request('translation.listProviders', {}, MODEL_WARMUP_REQUEST_TIMEOUT_MS)
	)
	handle(IpcChannel.BackendTranslate, (_event, request: unknown) => {
		if (!isTranslationRequest(request)) throw new TypeError('翻譯要求必須是物件')
		return backend.request('translation.translate', request, MODEL_WARMUP_REQUEST_TIMEOUT_MS)
	})
	handle(IpcChannel.BackendSendText, (_event, text: unknown) => {
		if (typeof text !== 'string') throw new TypeError('text 必須是字串')
		return backend.request('text.send', { text })
	})
	handle(IpcChannel.BackendUpdateSettings, async (_event, patch: unknown) => {
		if (!patch || typeof patch !== 'object' || Array.isArray(patch))
			throw new TypeError('patch 必須是物件')
		const result = await backend.request(
			'settings.update',
			{ patch },
			MODEL_WARMUP_REQUEST_TIMEOUT_MS
		)
		await settingsStore.save(result.settings)
		return result
	})

	ipcMain.on(IpcChannel.WindowMinimize, (event) => windowOf(event)?.minimize())

	ipcMain.on(IpcChannel.WindowToggleMaximize, (event) => {
		const window = windowOf(event)
		if (!window) return
		if (window.isMaximized()) window.unmaximize()
		else window.maximize()
	})

	ipcMain.on(IpcChannel.WindowClose, (event) => windowOf(event)?.close())
}

function isTranslationRequest(value: unknown): value is TranslationRequest {
	if (!value || typeof value !== 'object' || Array.isArray(value)) return false
	const request = value as Record<string, unknown>
	return (
		['provider', 'text', 'sourceLanguage', 'targetLanguage'].every(
			(key) => typeof request[key] === 'string' && Boolean((request[key] as string).trim())
		) &&
		(request.options === undefined || isRecord(request.options)) &&
		(request.context === undefined || isRecord(request.context))
	)
}

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === 'object' && value !== null && !Array.isArray(value)
}
