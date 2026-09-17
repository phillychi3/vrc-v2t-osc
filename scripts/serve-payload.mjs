// Serves build/installer-payload over loopback so the installer regression can
// exercise the real download path without touching a release server.
import { createReadStream } from 'node:fs'
import { stat } from 'node:fs/promises'
import { createServer } from 'node:http'
import { basename, join, resolve } from 'node:path'

const root = resolve(process.argv[2] ?? 'build/installer-payload')
const port = Number(process.argv[3] ?? 0)

const server = createServer(async (request, response) => {
	// Only ever serve a plain file name from the payload directory.
	const name = basename(decodeURIComponent(new URL(request.url, 'http://127.0.0.1').pathname))
	const file = join(root, name)
	try {
		const info = await stat(file)
		if (!info.isFile()) throw new Error('not a file')
		response.writeHead(200, {
			'content-type': 'application/octet-stream',
			'content-length': info.size
		})
		createReadStream(file).pipe(response)
	} catch {
		response.writeHead(404).end('not found')
	}
})

server.listen(port, '127.0.0.1', () => {
	console.log(`serving ${root} on ${server.address().port}`)
})
