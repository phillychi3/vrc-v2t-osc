/**
 * Interface translations. The catalogs are flat JSON files in `/locales`,
 * managed through Crowdin (see `crowdin.yml`); `zh-TW.json` is the source.
 * A string missing from a translation falls back to Traditional Chinese.
 */
import en from '../../../locales/en.json'
import ja from '../../../locales/ja.json'
import zhCN from '../../../locales/zh-CN.json'
import zhTW from '../../../locales/zh-TW.json'
import { format, type Locale } from './i18n-core.js'

export * from './i18n-core.js'

export type MessageKey = keyof typeof zhTW
export type MessageParams = Record<string, string | number>

const CATALOGS: Record<Locale, Partial<Record<MessageKey, string>>> = {
	'zh-TW': zhTW,
	'zh-CN': zhCN,
	en,
	ja
}

export function translate(locale: Locale, key: MessageKey, params?: MessageParams): string {
	return format(CATALOGS[locale][key] || zhTW[key] || key, params)
}
