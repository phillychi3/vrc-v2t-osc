import { defineConfig } from 'vite'

/** Electron main process. Preload is built separately so it stays one file. */
export default defineConfig({
	build: {
		ssr: true,
		outDir: 'build/electron',
		target: 'node22',
		emptyOutDir: true,
		minify: false,
		sourcemap: true,
		rollupOptions: {
			input: 'electron/src/main/index.ts',
			external: ['electron'],
			output: {
				format: 'cjs',
				entryFileNames: 'main.cjs'
			}
		}
	},
	ssr: {
		noExternal: true
	}
})
