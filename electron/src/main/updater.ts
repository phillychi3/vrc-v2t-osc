import { app, dialog } from 'electron'
import electronUpdater from 'electron-updater'
import { getMainWindow } from './window.js'
import { t } from './i18n.js'

// electron-updater is CommonJS; its named exports are not visible to ESM.
const { autoUpdater } = electronUpdater

// Releases are published by hand and their assets uploaded afterwards, so a
// check can briefly see a release without latest.yml; the next check recovers.
const CHECK_INTERVAL_MS = 4 * 60 * 60 * 1000

let started = false

/**
 * Checks GitHub Releases for a newer version, downloads it in the background
 * and offers a restart. The web installer that runs on restart reuses the
 * backend variant and emotion choice recorded by the previous install.
 */
export function startAutoUpdate(): void {
	// Only an installed build has app-update.yml next to it.
	if (started || !app.isPackaged || process.platform !== 'win32') return
	started = true

	autoUpdater.autoDownload = true
	// Installing on an ordinary quit would run a silent install the user never
	// agreed to; only the restart button installs.
	autoUpdater.autoInstallOnAppQuit = false
	autoUpdater.allowPrerelease = false

	autoUpdater.on('error', (error) => console.error('Update check failed:', error))
	autoUpdater.on('update-downloaded', (info) => void offerRestart(info.version))

	const check = () =>
		void autoUpdater
			.checkForUpdates()
			.catch((error) => console.error('Update check failed:', error))
	check()
	setInterval(check, CHECK_INTERVAL_MS).unref()
}

let offering = false

async function offerRestart(version: string): Promise<void> {
	if (offering) return
	offering = true
	try {
		const options = {
			type: 'info' as const,
			title: t('update.title'),
			message: t('update.message', { version }),
			detail: t('update.detail'),
			buttons: [t('update.restart'), t('update.later')],
			defaultId: 0,
			cancelId: 1
		}
		const window = getMainWindow()
		const { response } = window
			? await dialog.showMessageBox(window, options)
			: await dialog.showMessageBox(options)
		// Not silent: the installer shows its progress while it downloads the
		// backend, and runs the app again when it finishes.
		if (response === 0) autoUpdater.quitAndInstall(false, true)
	} finally {
		offering = false
	}
}
