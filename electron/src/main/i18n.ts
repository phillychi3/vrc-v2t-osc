import { app } from 'electron'
import {
	resolveLocale,
	translate,
	type LanguagePreference,
	type Locale,
	type MessageKey,
	type MessageParams
} from '../shared/i18n.js'

let preference: LanguagePreference = 'auto'

/** Follows `settings.ui.language`; call whenever settings load or change. */
export function setLanguagePreference(value: LanguagePreference | undefined): void {
	preference = value ?? 'auto'
}

export function currentLocale(): Locale {
	return resolveLocale(preference, app.getPreferredSystemLanguages())
}

/** Main-process strings (dialogs, errors, the VR caption texture). */
export function t(key: MessageKey, params?: MessageParams): string {
	return translate(currentLocale(), key, params)
}
