import { defineConfig, presetIcons, presetWind3 } from 'unocss'
import {
	iconShortcuts,
	getCSSPreflights,
	popoverShortcuts,
	presetIkun,
	selectShortcuts,
	switchShortcuts,
	virtualListShortcuts
} from '@ikun-ui/preset'

const ikunControlSafelist = [
	...Object.keys(iconShortcuts),
	...Object.keys(popoverShortcuts),
	...Object.keys(selectShortcuts),
	...Object.keys(switchShortcuts),
	...Object.keys(virtualListShortcuts),
	'i-carbon-chevron-down',
	'i-carbon-circle-dash',
	'i-carbon-close-outline',
	'rotate-180'
]

export default defineConfig({
	presets: [presetWind3({ dark: 'media' }), presetIcons(), presetIkun('ikun-ui', '#f97316')],
	preflights: [{ getCSS: () => `:root{${getCSSPreflights()}}` }],
	safelist: ikunControlSafelist,
	theme: {
		colors: {
			accent: {
				400: 'oklch(0.79 0.14 44)',
				500: 'oklch(0.71 0.18 40)',
				600: 'oklch(0.63 0.19 38)'
			}
		}
	},
	shortcuts: {
		'btn-base':
			'inline-flex select-none items-center justify-center gap-2 rounded-lg font-medium outline-none transition-all duration-150 active:translate-y-0.5 focus-visible:ring-3 focus-visible:ring-orange-500/20 disabled:pointer-events-none disabled:opacity-40',
		'btn-primary':
			'btn-base bg-zinc-900 text-white shadow-sm hover:bg-zinc-800 dark:bg-white dark:text-zinc-950 dark:hover:bg-zinc-200',
		'btn-accent':
			'btn-base bg-accent-500 text-white shadow-sm shadow-orange-500/15 hover:bg-accent-600',
		'btn-danger':
			'btn-base bg-red-500 text-white shadow-sm shadow-red-500/15 hover:bg-red-600 focus-visible:ring-red-500/20',
		'btn-secondary':
			'btn-base border border-zinc-300 bg-white text-zinc-700 shadow-sm hover:border-zinc-400 hover:bg-zinc-50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200 dark:hover:bg-zinc-800',
		'btn-ghost':
			'btn-base text-zinc-500 hover:bg-zinc-100 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-900 dark:hover:text-white',
		'stat-card':
			'rounded-xl border border-zinc-200 bg-white/70 px-4 py-3 dark:border-zinc-800 dark:bg-zinc-900/50'
	}
})
