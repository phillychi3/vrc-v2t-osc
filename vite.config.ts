import { svelte } from '@sveltejs/vite-plugin-svelte'
import { fileURLToPath } from 'node:url'
import UnoCSS from 'unocss/vite'
import { defineConfig } from 'vite'

const fromRoot = (path: string) => fileURLToPath(new URL(path, import.meta.url))

/** Renderer. The Electron main and preload processes have their own configs. */
export default defineConfig({
	root: 'electron/src/renderer',
	// Served at `app://-/` by electron-serve in production, so every asset
	// reference has to be relative rather than absolute.
	base: './',
	// Copied verbatim into the renderer output: favicon.png and splash.html are
	// both read from there by `electron/src/main/env.ts` once packaged.
	publicDir: fromRoot('./electron/static'),
	// `root` is the renderer directory, so the plugin has to be pointed back at
	// the shared Svelte config in the project root.
	plugins: [UnoCSS(), svelte({ configFile: fromRoot('./svelte.config.js') })],
	resolve: {
		alias: {
			$lib: fromRoot('./electron/src/renderer/lib'),
			$shared: fromRoot('./electron/src/shared')
		}
	},
	build: {
		outDir: fromRoot('./build/renderer'),
		emptyOutDir: true,
		sourcemap: true
	},
	server: {
		// Pinned to IPv4: `localhost` can resolve to ::1 on Windows, which makes
		// the HTTP readiness check in the `dev` script miss the server entirely.
		host: '127.0.0.1',
		port: 5173,
		strictPort: true,
		fs: {
			// The renderer imports from `electron/src/shared`, outside of `root`.
			allow: [fromRoot('.')]
		}
	}
})
