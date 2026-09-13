import { session } from 'electron'
import { isDev } from './env.js'
import { productionCsp } from './security-policy.js'

/**
 * A strict Content-Security-Policy for the packaged app.
 *
 * It is skipped in development because the Vite dev server needs `eval` and a
 * websocket connection for hot module replacement.
 */
export function applySecurityPolicy(): void {
	if (isDev) return

	session.defaultSession.webRequest.onHeadersReceived((details, callback) => {
		callback({
			responseHeaders: {
				...details.responseHeaders,
				'Content-Security-Policy': [productionCsp]
			}
		})
	})
}
