import { vitePreprocess } from '@sveltejs/vite-plugin-svelte'

/**
 * Shared Svelte config. Read by vite-plugin-svelte, svelte-check and
 * eslint-plugin-svelte — `vitePreprocess` is what makes `lang="ts"` work.
 *
 * @type {import('@sveltejs/vite-plugin-svelte').SvelteConfig}
 */
export default {
	preprocess: vitePreprocess()
}
