import assert from 'node:assert/strict'
import test from 'node:test'

import { productionCsp, isTrustedRendererUrl } from '../electron/src/main/security-policy.ts'

test('blocks inline and remote scripts in production', () => {
	assert.match(productionCsp, /script-src 'self'(?! 'unsafe-inline')/)
	assert.doesNotMatch(productionCsp, /https?:/)
})

test('only accepts the packaged app or exact development origin', () => {
	assert.equal(isTrustedRendererUrl('app://-/'), true)
	assert.equal(isTrustedRendererUrl('app://-/settings'), true)
	for (const value of [
		'https://example.com',
		'app://evil/',
		'file:///index.html',
		'about:blank',
		'app://user@-/',
		'invalid'
	]) {
		assert.equal(isTrustedRendererUrl(value), false)
	}
	const development = 'http://127.0.0.1:5173'
	assert.equal(isTrustedRendererUrl(`${development}/settings`, development), true)
	assert.equal(isTrustedRendererUrl('http://127.0.0.1:5174/', development), false)
	assert.equal(isTrustedRendererUrl('http://127.0.0.1.evil:5173/', development), false)
})
