export const productionCsp = [
	"default-src 'self'",
	// The renderer bundle is emitted as a single external module script, so no
	// inline script needs to be allowed here.
	"script-src 'self'",
	// Svelte still writes inline styles at runtime (transitions, `style:`).
	"style-src 'self' 'unsafe-inline'",
	"img-src 'self' data:",
	"font-src 'self' data:",
	"connect-src 'self'",
	"object-src 'none'",
	"frame-src 'none'"
].join('; ')

export function isTrustedRendererUrl(value: string, developmentUrl?: string): boolean {
	try {
		const url = new URL(value)
		if (url.username || url.password) return false
		if (developmentUrl) {
			const expected = new URL(developmentUrl)
			return ['http:', 'https:'].includes(url.protocol) && url.origin === expected.origin
		}
		return url.protocol === 'app:' && url.hostname === '-' && url.port === ''
	} catch {
		return false
	}
}
