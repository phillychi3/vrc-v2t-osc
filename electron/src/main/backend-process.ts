import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process'
import { EventEmitter } from 'node:events'
import { StringDecoder } from 'node:string_decoder'
import { join } from 'node:path'
import type {
	BackendEvent,
	BackendRequestMap,
	BackendResponse,
	BackendSettings,
	BackendState
} from '../shared/ipc.js'

const PROTOCOL_VERSION = 1
const MAX_LINE_BYTES = 1024 * 1024
const DEFAULT_TIMEOUT_MS = 5_000
const INITIALIZATION_TIMEOUT_MS = 15 * 60_000

interface PendingRequest {
	resolve: (value: unknown) => void
	reject: (reason: Error) => void
	timer: NodeJS.Timeout
}

export class BackendError extends Error {
	readonly code: string
	readonly retryable: boolean

	constructor(message: string, code: string, retryable = false) {
		super(message)
		this.name = 'BackendError'
		this.code = code
		this.retryable = retryable
	}
}

export interface BackendProcessEvents {
	event: [event: BackendEvent]
	error: [error: Error]
}

export interface BackendCommand {
	command: string
	args: string[]
	cwd: string
}

/** Owns the Python child process and the NDJSON request/response transport. */
export class BackendProcess extends EventEmitter<BackendProcessEvents> {
	private readonly command: BackendCommand
	private child: ChildProcessWithoutNullStreams | null = null
	private pending = new Map<string, PendingRequest>()
	private nextRequestId = 0
	private stdoutBuffer = Buffer.alloc(0)
	private decoder = new StringDecoder('utf8')
	private stopping = false
	private stopTask: Promise<void> | null = null
	private restartTask: Promise<BackendState> | null = null
	private shutdownRequested = false
	private protocolFailed = false

	constructor(command: BackendCommand) {
		super()
		this.command = command
	}

	async start(
		settings: BackendSettings,
		dataPath: string,
		suppressAutoStart = false
	): Promise<BackendState> {
		if (this.shutdownRequested) throw new BackendError('應用程式正在關閉', 'SHUTTING_DOWN')
		if (this.child) throw new BackendError('後端已經啟動', 'ALREADY_STARTED')
		const { command, args, cwd } = this.command
		const child = spawn(command, args, {
			cwd,
			windowsHide: true,
			shell: false,
			stdio: ['pipe', 'pipe', 'pipe']
		})
		this.child = child
		this.stopping = false
		this.protocolFailed = false
		child.stdout.on('data', (chunk: Buffer) => {
			if (this.child === child) this.consumeStdout(chunk)
		})
		child.stderr.setEncoding('utf8')
		child.stderr.on('data', (chunk: string) => console.error(`[backend] ${chunk.trimEnd()}`))
		child.stdin.on('error', () => undefined) // write callbacks reject the matching requests
		child.once('error', (error) => {
			if (this.child === child) this.handleTermination(error)
		})
		child.once('exit', (code, signal) => {
			const suffix = signal ? `signal ${signal}` : `code ${code ?? 'unknown'}`
			if (this.child === child)
				this.handleTermination(new BackendError(`後端已結束（${suffix}）`, 'BACKEND_EXITED', true))
		})

		try {
			return await this.request(
				'system.initialize',
				{ settings, dataPath, suppressAutoStart },
				INITIALIZATION_TIMEOUT_MS
			)
		} catch (error) {
			await this.stop().catch(() => undefined)
			throw error
		}
	}

	restart(settings: BackendSettings, dataPath: string): Promise<BackendState> {
		if (!this.restartTask) {
			this.restartTask = (async () => {
				await this.stop()
				return this.start(settings, dataPath, true)
			})().finally(() => {
				this.restartTask = null
			})
		}
		return this.restartTask
	}

	shutdown(): Promise<void> {
		this.shutdownRequested = true
		return this.stop()
	}

	request<M extends keyof BackendRequestMap>(
		method: M,
		params: BackendRequestMap[M]['params'],
		timeoutMs = DEFAULT_TIMEOUT_MS
	): Promise<BackendRequestMap[M]['result']> {
		const child = this.child
		if (!child || child.stdin.destroyed || (this.stopping && method !== 'system.shutdown'))
			return Promise.reject(new BackendError('後端未執行', 'BACKEND_UNAVAILABLE', true))

		const id = `main-${++this.nextRequestId}`
		// `pending` is heterogeneous — each entry resolves a different result
		// type — so the promise is built as `unknown` and narrowed on return.
		return new Promise<unknown>((resolve, reject) => {
			const timer = setTimeout(() => {
				this.pending.delete(id)
				reject(new BackendError(`後端要求逾時: ${method}`, 'REQUEST_TIMEOUT', true))
			}, timeoutMs)
			this.pending.set(id, { resolve, reject, timer })
			const line =
				JSON.stringify({ v: PROTOCOL_VERSION, type: 'request', id, method, params }) + '\n'
			child.stdin.write(line, 'utf8', (error) => {
				if (!error) return
				const pending = this.pending.get(id)
				if (!pending) return
				clearTimeout(pending.timer)
				this.pending.delete(id)
				pending.reject(error)
			})
		}) as Promise<BackendRequestMap[M]['result']>
	}

	stop(timeoutMs = DEFAULT_TIMEOUT_MS): Promise<void> {
		if (!this.stopTask) {
			this.stopTask = this.stopChild(timeoutMs).finally(() => {
				this.stopTask = null
			})
		}
		return this.stopTask
	}

	private async stopChild(timeoutMs: number): Promise<void> {
		const child = this.child
		if (!child) return
		this.stopping = true
		let timer: NodeJS.Timeout | undefined
		const exited = new Promise<void>((resolve) => {
			if (child.exitCode !== null || child.signalCode !== null) resolve()
			else child.once('exit', () => resolve())
		})
		const deadline = new Promise<'timeout'>((resolve) => {
			timer = setTimeout(() => resolve('timeout'), timeoutMs)
		})
		try {
			void this.request('system.shutdown', {}, timeoutMs)
				.catch(() => undefined)
				.finally(() => child.stdin.end())
			if ((await Promise.race([exited, deadline])) === 'timeout') {
				this.killTree(child)
				await new Promise<void>((resolve, reject) => {
					const killTimer = setTimeout(
						() => reject(new BackendError('無法終止後端', 'STOP_FAILED')),
						2000
					)
					void exited.then(() => {
						clearTimeout(killTimer)
						resolve()
					})
				})
			}
		} finally {
			clearTimeout(timer)
		}
	}

	private consumeStdout(chunk: Buffer): void {
		this.stdoutBuffer = Buffer.concat([this.stdoutBuffer, chunk])
		if (this.stdoutBuffer.length > MAX_LINE_BYTES && !this.stdoutBuffer.includes(0x0a)) {
			this.failProtocol('後端訊息超過 1 MiB')
			return
		}

		let newline: number
		while ((newline = this.stdoutBuffer.indexOf(0x0a)) >= 0) {
			const raw = this.stdoutBuffer.subarray(0, newline)
			this.stdoutBuffer = this.stdoutBuffer.subarray(newline + 1)
			if (raw.length > MAX_LINE_BYTES) {
				this.failProtocol('後端訊息超過 1 MiB')
				return
			}
			const line = this.decoder.write(raw).replace(/\r$/, '')
			if (line) this.consumeMessage(line)
			if (this.protocolFailed) return
		}
	}

	private consumeMessage(line: string): void {
		let message: BackendResponse | BackendEvent
		try {
			message = JSON.parse(line) as BackendResponse | BackendEvent
		} catch {
			this.failProtocol('後端輸出了無效 JSON')
			return
		}
		if (!message || typeof message !== 'object' || Array.isArray(message)) {
			this.failProtocol('後端訊息必須是物件')
			return
		}
		if (message.v !== PROTOCOL_VERSION) {
			this.failProtocol('後端協定版本不相容')
			return
		}
		if (message.type === 'event') {
			if (
				typeof message.sessionId !== 'string' ||
				!message.sessionId ||
				!Number.isSafeInteger(message.seq) ||
				message.seq < 0 ||
				typeof message.event !== 'string' ||
				!message.event ||
				!('data' in message)
			) {
				this.failProtocol('後端事件格式不正確')
				return
			}
			this.emit('event', message)
			return
		}
		if (message.type !== 'response' || typeof message.id !== 'string') {
			this.failProtocol('後端輸出了未知訊息')
			return
		}
		if (
			typeof message.ok !== 'boolean' ||
			(message.ok && !('result' in message)) ||
			(!message.ok &&
				(!message.error ||
					typeof message.error.code !== 'string' ||
					typeof message.error.message !== 'string' ||
					typeof message.error.retryable !== 'boolean'))
		) {
			this.failProtocol('後端回應格式不正確')
			return
		}
		const pending = this.pending.get(message.id)
		if (!pending) return
		clearTimeout(pending.timer)
		this.pending.delete(message.id)
		if (message.ok) pending.resolve(message.result)
		else
			pending.reject(
				new BackendError(message.error.message, message.error.code, message.error.retryable)
			)
	}

	private failProtocol(message: string): void {
		if (this.protocolFailed) return
		this.protocolFailed = true
		const error = new BackendError(message, 'PROTOCOL_ERROR')
		this.emit('error', error)
		this.stopping = true
		if (this.child) this.killTree(this.child)
	}

	private killTree(child: ChildProcessWithoutNullStreams): void {
		if (child.exitCode !== null || child.signalCode !== null) return
		if (process.platform !== 'win32' || !child.pid) {
			child.kill('SIGKILL')
			return
		}
		const killer = spawn(
			join(process.env.SystemRoot ?? 'C:\\Windows', 'System32', 'taskkill.exe'),
			['/PID', String(child.pid), '/T', '/F'],
			{ windowsHide: true, shell: false, stdio: 'ignore' }
		)
		const fallback = () => {
			if (child.exitCode === null && child.signalCode === null) child.kill('SIGKILL')
		}
		killer.once('error', fallback)
		killer.once('exit', (code) => {
			if (code !== 0) fallback()
		})
	}

	private handleTermination(error: Error): void {
		if (!this.child) return
		this.child = null
		this.stdoutBuffer = Buffer.alloc(0)
		this.decoder = new StringDecoder('utf8')
		for (const pending of this.pending.values()) {
			clearTimeout(pending.timer)
			pending.reject(error)
		}
		this.pending.clear()
		if (!this.stopping) this.emit('error', error)
	}
}
