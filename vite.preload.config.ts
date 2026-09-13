import { defineConfig } from 'vite'

/**
 * Sandboxed preload scripts cannot require arbitrary local files. Building this
 * entry separately forces shared IPC constants into the single preload.cjs.
 */
export default defineConfig({
	build: {
		ssr: true,
		outDir: 'build/electron',
		target: 'node22',
		emptyOutDir: false,
		minify: false,
		sourcemap: true,
		rollupOptions: {
			input: 'electron/src/preload/index.ts',
			external: ['electron'],
			output: {
				format: 'cjs',
				entryFileNames: 'preload.cjs'
			}
		}
	},
	ssr: {
		noExternal: true
	}
})
