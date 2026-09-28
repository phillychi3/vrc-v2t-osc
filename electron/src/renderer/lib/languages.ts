/** Languages offered for speech recognition and translation. */
export const LANGUAGES: { value: string; label: string }[] = [
	{ value: 'auto', label: '自動偵測' },
	{ value: 'zh', label: '繁體中文' },
	{ value: 'en', label: 'English' },
	{ value: 'ja', label: '日本語' },
	{ value: 'ko', label: '한국어' },
	{ value: 'fr', label: 'Français' },
	{ value: 'de', label: 'Deutsch' },
	{ value: 'es', label: 'Español' }
]

export function languageLabel(code: string): string {
	return LANGUAGES.find((language) => language.value === code)?.label ?? code
}
