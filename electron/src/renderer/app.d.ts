import type { RendererApi } from '$shared/ipc'

declare global {
	interface Window {
		/** Exposed by the sandboxed preload bridge. */
		api?: RendererApi
	}
}

export {}
