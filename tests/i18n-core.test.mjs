import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import {
	LOCALES,
	format,
	isLanguagePreference,
	resolveLocale
} from '../electron/src/shared/i18n-core.ts'

test('resolves the interface language like the backend does', () => {
	assert.equal(resolveLocale('auto', ['zh-Hant-TW']), 'zh-TW')
	assert.equal(resolveLocale('auto', ['zh-HK']), 'zh-TW')
	assert.equal(resolveLocale('auto', ['zh-CN']), 'zh-CN')
	assert.equal(resolveLocale('auto', ['zh-Hans-SG']), 'zh-CN')
	assert.equal(resolveLocale('auto', ['ko-KR', 'ja-JP']), 'ja')
	assert.equal(resolveLocale('auto', ['ko-KR']), 'en')
	assert.equal(resolveLocale('auto', []), 'zh-TW')
	assert.equal(resolveLocale('en', ['ja-JP']), 'en')
	assert.equal(resolveLocale(undefined, ['en-GB']), 'en')
})

test('fills placeholders and leaves unknown ones visible', () => {
	assert.equal(format('{a} / {b}', { a: 1, b: 'x' }), '1 / x')
	assert.equal(format('{missing}', {}), '{missing}')
	assert.equal(format('plain'), 'plain')
})

test('accepts only supported language preferences', () => {
	assert.ok(isLanguagePreference('auto'))
	for (const locale of LOCALES) assert.ok(isLanguagePreference(locale))
	assert.equal(isLanguagePreference('fr'), false)
})

test('every locale has a catalog file with no keys outside the source', async () => {
	const load = async (locale) =>
		JSON.parse(await readFile(new URL(`../locales/${locale}.json`, import.meta.url), 'utf8'))
	const source = await load('zh-TW')
	for (const locale of LOCALES) {
		const catalog = await load(locale)
		assert.deepEqual(
			Object.keys(catalog).filter((key) => !(key in source)),
			[],
			`${locale} has keys the source does not`
		)
	}
})
