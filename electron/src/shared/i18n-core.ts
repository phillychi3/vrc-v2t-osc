/**
 * Locale list and language matching, without the message catalogs, so code
 * that only validates settings (and the Node test runner) stays light.
 *
 * Adding a language: add `locales/<code>.json` (Crowdin produces it), then
 * list the code here, in LOCALE_NAMES, and in backend/settings.py.
 */
export const LOCALES = ['zh-TW', 'zh-CN', 'en', 'ja'] as const
export type Locale = (typeof LOCALES)[number]
/** `auto` follows the operating system's language. */
export type LanguagePreference = 'auto' | Locale

/** Each language's own name, shown in the language picker. */
export const LOCALE_NAMES: Record<Locale, string> = {
	'zh-TW': '繁體中文',
	'zh-CN': '简体中文',
	en: 'English',
	ja: '日本語'
}

export function isLanguagePreference(value: unknown): value is LanguagePreference {
	return value === 'auto' || LOCALES.includes(value as Locale)
}

/** Maps a BCP 47 tag such as `zh-Hant-TW` or `ja-JP` to a supported locale. */
export function matchLocale(tag: string): Locale | null {
	const lower = tag.toLowerCase()
	if (lower.startsWith('zh')) {
		// Simplified Chinese regions and scripts; any other Chinese is Traditional.
		return /^zh-(hans|cn|sg|my)\b/.test(lower) ? 'zh-CN' : 'zh-TW'
	}
	if (lower.startsWith('ja')) return 'ja'
	if (lower.startsWith('en')) return 'en'
	return null
}

export function resolveLocale(
	preference: LanguagePreference | undefined,
	systemLanguages: readonly string[]
): Locale {
	if (preference && preference !== 'auto') return preference
	for (const tag of systemLanguages) {
		const locale = matchLocale(tag)
		if (locale) return locale
	}
	// Unknown OS languages read English; with no OS information at all, keep
	// the source language (matches backend/i18n.py).
	return systemLanguages.length > 0 ? 'en' : 'zh-TW'
}

/** Fills `{name}` placeholders (an ICU MessageFormat subset Crowdin understands). */
export function format(template: string, params?: Record<string, string | number>): string {
	if (!params) return template
	return template.replace(/\{(\w+)\}/g, (match, name: string) =>
		name in params ? String(params[name]) : match
	)
}
