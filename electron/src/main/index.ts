import { BrowserWindow, app, dialog } from 'electron'
import { registerIpcHandlers } from './ipc.js'
import { applySecurityPolicy } from './security.js'
import { closeSplashWindow, createMainWindow, createSplashWindow, getMainWindow } from './window.js'
import { BackendProcess } from './backend-process.js'
import { IpcChannel, type BackendSettings } from '../shared/ipc.js'
import { SettingsStore } from './settings-store.js'
import { resolveBackendCommand } from './backend-command.js'
import { OverlayController } from './overlay-controller.js'
import { currentLocale, setLanguagePreference, t } from './i18n.js'

const backend = new BackendProcess(resolveBackendCommand(), {
	translate: t,
	systemLanguages: () => app.getPreferredSystemLanguages()
})
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
	const settingsStore = new SettingsStore(app.getPath('userData'))
	// Settings load first so the splash screen already speaks the right language.
	const settings = await settingsStore.load()
	setLanguagePreference(settings.ui?.language)
	const splash = await createSplashWindow({
		locale: currentLocale(),
		loading: t('startup.loading'),
		busy: t('common.loading')
	})
	const overlay = new OverlayController(backend, app.getPath('userData'), async (patch) => {
		const result = await backend.request('settings.update', { patch }, 120_000)
		await settingsStore.save(result.settings)
	})
	app.once('before-quit', () => overlay.dispose())
	backend.on('event', (event) => {
		const data = event.data as { settings?: BackendSettings } | null
		if ((event.event === 'backend.ready' || event.event === 'settings.changed') && data?.settings)
			setLanguagePreference(data.settings.ui?.language)
		overlay.handleEvent(event)
		getMainWindow()?.webContents.send(IpcChannel.BackendEvent, event)
	})
	backend.on('error', (error) => {
		console.error(error)
		overlay.reset()
		getMainWindow()?.webContents.send(IpcChannel.BackendFailure, error.message)
	})
	registerIpcHandlers(backend, settingsStore, () => !quitting, {
		preview: () => overlay.preview(),
		calibrate: () => overlay.calibrate()
	})
	try {
		while (!quitting) {
			try {
				const state = await backend.start(settings, app.getPath('userData'))
				if (state.models.speech?.status !== 'ready') throw new Error(t('startup.speechFailed'))
				break
			} catch (error) {
				if (quitting) return
				await backend.stop()
				const { response } = await dialog.showMessageBox(splash, {
					type: 'error',
					title: t('startup.title'),
					message: t('startup.message'),
					detail: error instanceof Error ? error.message : String(error),
					buttons: [t('startup.retry'), t('startup.quit')],
					defaultId: 0,
					cancelId: 1
				})
				if (response === 1) {
					app.quit()
					return
				}
			}
		}
		if (quitting) return
		const window = await createMainWindow(false)
		// The offscreen caption window must not keep the app alive.
		window.once('closed', () => overlay.dispose())
		window.show()
		closeSplashWindow()
	} catch (error) {
		closeSplashWindow()
		throw error
	}

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
