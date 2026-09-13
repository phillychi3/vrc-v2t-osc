import { BrowserWindow, app } from 'electron'
import { registerIpcHandlers } from './ipc.js'
import { applySecurityPolicy } from './security.js'
import { closeSplashWindow, createMainWindow, createSplashWindow, getMainWindow } from './window.js'
import { BackendProcess } from './backend-process.js'
import { IpcChannel } from '../shared/ipc.js'
import { SettingsStore } from './settings-store.js'
import { resolveBackendCommand } from './backend-command.js'

const backend = new BackendProcess(resolveBackendCommand())
let quitting = false

if (!app.requestSingleInstanceLock()) {
	app.quit()
} else {
	app.on('second-instance', () => {
		const window = getMainWindow()
		if (!window) return
		if (window.isMinimized()) window.restore()
		window.focus()
	})

	void bootstrap().catch((error) => {
		console.error('Application startup failed:', error)
		app.quit()
	})
}

async function bootstrap(): Promise<void> {
	await app.whenReady()

	applySecurityPolicy()
	await createSplashWindow()
	backend.on('event', (event) => {
		for (const window of BrowserWindow.getAllWindows())
			window.webContents.send(IpcChannel.BackendEvent, event)
	})
	backend.on('error', (error) => {
		console.error(error)
		for (const window of BrowserWindow.getAllWindows())
			window.webContents.send(IpcChannel.BackendFailure, error.message)
	})
	const settingsStore = new SettingsStore(app.getPath('userData'))
	const settings = await settingsStore.load()
	registerIpcHandlers(backend, settingsStore, () => !quitting)
	const initialization = backend.start(settings, app.getPath('userData')).catch((error: Error) => {
		console.error('Backend initialization failed:', error)
		for (const window of BrowserWindow.getAllWindows())
			window.webContents.send(IpcChannel.BackendFailure, error.message)
	})
	try {
		const window = await createMainWindow(false)
		window.show()
		closeSplashWindow()
	} catch (error) {
		closeSplashWindow()
		throw error
	}
	await initialization

	app.on('activate', () => {
		if (BrowserWindow.getAllWindows().length === 0) void createMainWindow()
	})
}

app.on('window-all-closed', () => {
	if (process.platform !== 'darwin') app.quit()
})

app.on('before-quit', (event) => {
	if (quitting) return
	event.preventDefault()
	quitting = true
	void backend
		.shutdown()
		.catch((error) => console.error('Backend shutdown failed:', error))
		.finally(() => app.quit())
})
