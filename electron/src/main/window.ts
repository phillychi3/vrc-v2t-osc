import { BrowserWindow } from 'electron'
import { isTrustedRendererUrl } from './security-policy.js'
import serve from 'electron-serve'
import { appIcon, devServerUrl, isDev, preloadScript, rendererDist, splashHtml } from './env.js'

const loadProduction = serve({ directory: rendererDist })

let mainWindow: BrowserWindow | null = null
let splashWindow: BrowserWindow | null = null

export function getMainWindow(): BrowserWindow | null {
	return mainWindow
}

export async function createSplashWindow(): Promise<BrowserWindow> {
	const window = new BrowserWindow({
		width: 420,
		height: 260,
		frame: false,
		resizable: false,
		center: true,
		show: false,
		alwaysOnTop: true,
		icon: appIcon,
		backgroundColor: '#09090b',
		webPreferences: {
			contextIsolation: true,
			nodeIntegration: false,
			sandbox: true
		}
	})
	splashWindow = window
	window.on('closed', () => {
		if (splashWindow === window) splashWindow = null
	})
	await window.loadFile(splashHtml)
	window.show()
	return window
}

export function closeSplashWindow(): void {
	if (splashWindow && !splashWindow.isDestroyed()) splashWindow.destroy()
	splashWindow = null
}

export async function createMainWindow(showOnReady = true): Promise<BrowserWindow> {
	const window = new BrowserWindow({
		width: 1180,
		height: 760,
		minWidth: 900,
		minHeight: 600,
		frame: false,
		icon: appIcon,
		show: false,
		autoHideMenuBar: true,
		backgroundColor: '#0b0b0f',
		webPreferences: {
			preload: preloadScript,
			// Keep the renderer sandboxed: it talks to the main process only
			// through the typed bridge in `src/preload`.
			contextIsolation: true,
			nodeIntegration: false,
			sandbox: true
		}
	})

	mainWindow = window

	if (showOnReady) window.once('ready-to-show', () => window.show())
	window.webContents.once('did-finish-load', () => {
		if (isDev) console.info(`Renderer loaded: ${window.webContents.getURL()}`)
	})
	window.webContents.on('did-fail-load', (_event, code, description, url) => {
		console.error(`Renderer failed to load (${code}): ${description} — ${url}`)
	})
	window.on('closed', () => {
		if (mainWindow === window) mainWindow = null
	})

	window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }))
	window.webContents.on('will-navigate', (event, url) => {
		if (!isTrustedRendererUrl(url, isDev ? devServerUrl : undefined)) event.preventDefault()
	})

	if (isDev) {
		await window.loadURL(devServerUrl)
		window.webContents.openDevTools({ mode: 'detach' })
	} else {
		await loadProduction(window)
	}

	return window
}
