import { derived, get } from 'svelte/store'
import { backend, setMessageTranslator } from './stores/backend'
import {
	resolveLocale,
	translate,
	type Locale,
	type MessageKey,
	type MessageParams
} from '$shared/i18n'

/** The operating system's language, used when the preference is `auto`. */
export const systemLocale: Locale = resolveLocale('auto', navigator.languages)

/** The interface language: the saved preference, or the system language. */
export const locale = derived(backend.state, ($state) =>
	resolveLocale($state?.settings.ui?.language, navigator.languages)
)

locale.subscribe((value) => {
	document.documentElement.lang = value
})

setMessageTranslator((key) => translate(get(locale), key))

export type Translate = (key: MessageKey, params?: MessageParams) => string

/** Use as `$t('key')` in components; re-renders when the language changes. */
export const t = derived(
	locale,
	($locale): Translate =>
		(key, params) =>
			translate($locale, key, params)
)
