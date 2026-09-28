import type { Translate } from './i18n'

/**
 * Languages offered for speech recognition and translation. Each is shown in
 * its own language (an endonym), so it reads the same in every interface
 * language; only "auto" needs translating.
 */
const ENDONYMS: { value: string; label: string }[] = [
	{ value: 'zh', label: '繁體中文' },
	{ value: 'en', label: 'English' },
	{ value: 'ja', label: '日本語' },
	{ value: 'ko', label: '한국어' },
	{ value: 'fr', label: 'Français' },
	{ value: 'de', label: 'Deutsch' },
	{ value: 'es', label: 'Español' }
]

export function languageOptions(t: Translate, withAuto: boolean) {
	return withAuto ? [{ value: 'auto', label: t('language.auto') }, ...ENDONYMS] : ENDONYMS
}

export function languageLabel(code: string): string {
	return ENDONYMS.find((language) => language.value === code)?.label ?? code
}
