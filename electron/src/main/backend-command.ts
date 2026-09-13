import { execSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { join } from 'node:path'
import type { BackendCommand } from './backend-process.js'
import { appRoot, isDev } from './env.js'

/** Interpreter path inside a virtual environment root. */
function interpreterIn(environmentRoot: string): string {
	return process.platform === 'win32'
		? join(environmentRoot, 'Scripts', 'python.exe')
		: join(environmentRoot, 'bin', 'python')
}

/**
 * Poetry keeps its virtual environments outside the project unless
 * `virtualenvs.in-project` is set, so the directory has to be asked for.
 */
function poetryInterpreter(): string | null {
	try {
		// Run through a shell so the .cmd/.bat shim Poetry installs on Windows
		// resolves. The command is a fixed string, so nothing is interpolated.
		const environmentRoot = execSync('poetry env info --path', {
			cwd: appRoot,
			encoding: 'utf8',
			stdio: ['ignore', 'pipe', 'ignore']
		}).trim()
		return environmentRoot ? interpreterIn(environmentRoot) : null
	} catch {
		return null
	}
}

/**
 * Locates the Python that has the backend dependencies installed. An explicit
 * `VRC_BACKEND_PYTHON` wins, then an activated virtual environment, then the
 * conventional in-project `.venv`, then whatever Poetry reports.
 */
function resolveDevelopmentInterpreter(): string {
	const override = process.env.VRC_BACKEND_PYTHON
	if (override && existsSync(override)) return override

	const activeEnvironment = process.env.VIRTUAL_ENV
	const candidates = [
		activeEnvironment ? interpreterIn(activeEnvironment) : null,
		interpreterIn(join(appRoot, '.venv')),
		poetryInterpreter()
	]
	for (const candidate of candidates) {
		if (candidate && existsSync(candidate)) return candidate
	}

	console.warn(
		'找不到專案的 Python 虛擬環境，改用 PATH 上的 `python`。' +
			'若後端缺少套件，請執行 `poetry install` 或設定 VRC_BACKEND_PYTHON。'
	)
	return 'python'
}

export function resolveBackendCommand(): BackendCommand {
	if (!isDev) {
		return {
			command: join(process.resourcesPath, 'backend', 'vrc-v2t-backend.exe'),
			args: [],
			cwd: process.resourcesPath
		}
	}

	return {
		command: resolveDevelopmentInterpreter(),
		args: ['-u', '-m', 'backend'],
		cwd: appRoot
	}
}
