import { createInterface } from 'node:readline'

const mode = process.argv[2] ?? 'normal'
const lines = createInterface({ input: process.stdin })
let settings = {}
let suppressAutoStart = false
let sentCount = 0

lines.on('line', (line) => {
	const request = JSON.parse(line)
	if (request.method === 'system.initialize') {
		settings = request.params.settings
		suppressAutoStart = request.params.suppressAutoStart ?? false
		respond(request.id, state())
		return
	}
	if (request.method === 'system.shutdown') {
		if (mode === 'hung-shutdown') {
			setInterval(() => undefined, 1000)
			return
		}
		respond(request.id, { state: 'stopped' })
		if (mode === 'delayed-shutdown') {
			setTimeout(() => process.exit(0), 100)
		}
		process.exitCode = 0
		lines.close()
		return
	}
	if (request.method === 'text.send') sentCount++
	if (mode === 'timeout') return
	if (mode === 'delayed') {
		setTimeout(() => respond(request.id, state()), 50)
		return
	}
	if (mode === 'exit') {
		process.exit(7)
	}
	if (mode === 'malformed') {
		process.stdout.write('not-json\n')
		return
	}
	if (mode === 'null') {
		write(null)
		return
	}
	if (mode === 'missing-error') {
		write({ v: 1, type: 'response', id: request.id, ok: false })
		return
	}
	if (mode === 'invalid-event') {
		write({ v: 1, type: 'event', sessionId: 'test', seq: '1', event: 'test' })
		return
	}
	if (mode === 'split-chinese') {
		const event = Buffer.from(
			JSON.stringify({
				v: 1,
				type: 'event',
				sessionId: 'fixture-session',
				seq: 1,
				event: 'fixture.event',
				data: { text: '你好世界' }
			}) + '\n'
		)
		const split = event.indexOf(Buffer.from('你')) + 1
		process.stdout.write(event.subarray(0, split))
		setTimeout(() => {
			process.stdout.write(event.subarray(split))
			respond(request.id, state())
		}, 10)
		return
	}
	if (mode === 'event') {
		write({
			v: 1,
			type: 'event',
			sessionId: 'fixture-session',
			seq: 1,
			event: 'fixture.event',
			data: { ok: true }
		})
	}
	respond(request.id, state())
})

function respond(id, result) {
	write({ v: 1, type: 'response', id, ok: true, result })
}

function write(message) {
	process.stdout.write(`${JSON.stringify(message)}\n`)
}

function state() {
	return {
		sessionId: `fixture-${process.pid}`,
		seq: 0,
		backend: 'ready',
		recording: 'idle',
		recordingSource: null,
		recordings: { microphone: 'idle', speaker: 'idle' },
		models: {},
		settings,
		suppressAutoStart,
		sentCount
	}
}
