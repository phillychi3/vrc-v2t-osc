import { spawn } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import electron from 'electron'

const userData = await mkdtemp(join(tmpdir(), 'vrc-p5-ui-'))
const env = { ...process.env }
delete env.ELECTRON_RUN_AS_NODE
const child = spawn(electron, ['tests/fixtures/renderer-e2e.mjs', userData], {
	env,
	stdio: 'inherit',
	windowsHide: true
})
const timeout = setTimeout(() => child.kill(), 60_000)
try {
	const code = await new Promise((resolve, reject) => {
		child.once('error', reject)
		child.once('exit', resolve)
	})
	if (code !== 0) throw new Error(`Electron UI regression failed: ${code}`)
} finally {
	clearTimeout(timeout)
	// mkdtemp returned this exact, task-owned directory, never a user profile.
	await rm(userData, { recursive: true, force: true, maxRetries: 3 }).catch(() => undefined)
}
