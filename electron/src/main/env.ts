import { join } from 'node:path'
import { app } from 'electron'

/** `true` while running from source through the Vite dev server. */
export const isDev = !app.isPackaged

/**
 * Root of the application.
 *
 * `__dirname` points at `build/electron`, so two levels up is the project root
 * in development and the asar root once packaged. Both layouts contain
 * `build/`, which keeps every path below identical in either mode.
 */
export const appRoot = join(__dirname, '..', '..')

export const rendererDist = join(appRoot, 'build', 'renderer')
export const preloadScript = join(__dirname, 'preload.cjs')
export const splashHtml = isDev
	? join(appRoot, 'electron', 'static', 'splash.html')
	: join(rendererDist, 'splash.html')

/** Matches `server.host` / `server.port` in `vite.config.ts`. */
export const devServerUrl = process.env.VITE_DEV_SERVER_URL ?? 'http://127.0.0.1:5173'

/**
 * Window icon. `electron/static/` is Vite's `publicDir`, so its contents are
 * copied into the renderer output and the packaged app reads it from there.
 */
export const appIcon = isDev
	? join(appRoot, 'electron', 'static', 'favicon.png')
	: join(rendererDist, 'favicon.png')
