/* eslint-disable @typescript-eslint/no-require-imports -- CommonJS CLI entry point. */
const { createRequire } = require('node:module')
const { spawn } = require('node:child_process')
const crypto = require('node:crypto')
const fs = require('node:fs/promises')
const path = require('node:path')

// Resolve the tool shipped with our installed electron-builder under pnpm.
const builderRequire = createRequire(require.resolve('electron-builder'))
const { getPath7za } = builderRequire('app-builder-lib/out/toolsets/7zip')

const { version } = require('../package.json')

// The installer checks for each asset's entry file to confirm it really
// unpacked; the backend one is overridable so the regression can use a stand-in.
const BACKEND_ENTRY = process.env.VRC_BACKEND_ENTRY || 'vrc-v2t-backend.exe'

// The backends change with every release, so they are packed here and uploaded
// to that release's tag. The emotion model does not: it is pinned by
// scripts/emotion-asset.json and published once per model revision, so it is
// neither packed nor uploaded again. See EMOTION_MANIFEST below.
const BACKENDS = [
	{
		id: 'cpu',
		define: 'CPU',
		source: 'build/python-cpu/vrc-v2t-backend',
		name: `vrc2t-backend-cpu-${version}-x64.7z`,
		entry: BACKEND_ENTRY
	},
	{
		id: 'cu126',
		define: 'CU126',
		source: 'build/python-cu126/vrc-v2t-backend',
		name: `vrc2t-backend-cu126-${version}-x64.7z`,
		entry: BACKEND_ENTRY
	}
]
const EMOTION_MANIFEST = path.join(__dirname, 'emotion-asset.json')
// Packing a local emotion directory instead of trusting the pinned archive is
// only for the installer regression's stand-in model; a release never sets it.
const EMOTION_SOURCE = process.env.VRC_EMOTION_MODEL_DIR
// Release assets live under the tag the release workflow publishes.
const BASE_URL =
	process.env.VRC_ASSET_BASE_URL ||
	`https://github.com/phillychi3/vrc-v2t-osc/releases/download/v${version}`
// Headroom for the archive copy plus NSIS temporaries next to the unpacked tree.
const SPARE_MEGABYTES = 256

function nsisString(value) {
	// NSIS expands $ inside double-quoted strings; $$ is a literal dollar sign.
	return String(value).replace(/\$/g, '$$$$')
}

async function directorySize(root) {
	let total = 0
	for (const entry of await fs.readdir(root, { withFileTypes: true, recursive: true })) {
		if (entry.isFile()) {
			total += (await fs.stat(path.join(entry.parentPath, entry.name))).size
		}
	}
	return total
}

async function hashFile(file, algorithm) {
	const hash = crypto.createHash(algorithm)
	hash.update(await fs.readFile(file))
	return hash.digest('hex').toUpperCase()
}

function run(command, args, cwd) {
	return new Promise((resolve, reject) => {
		const child = spawn(command, args, { cwd, stdio: 'inherit', windowsHide: true })
		child.on('error', reject)
		child.on('close', (code) =>
			code === 0 ? resolve() : reject(new Error(`${path.basename(command)} failed: ${code}`))
		)
	})
}

async function packAsset(sevenZip, source, archive) {
	// 7-Zip otherwise updates an existing archive and can retain deleted files.
	await fs.rm(archive, { force: true })
	console.log(`Compressing ${path.basename(archive)}`)
	await run(
		sevenZip,
		[
			'a',
			'-t7z',
			'-mx=5',
			'-m0=LZMA2',
			'-md=32m',
			'-mmt=2',
			'-mtc=off',
			'-mta=off',
			'-mtm=off',
			archive,
			'.'
		],
		source
	)
}

// Everything the installer needs to size, fetch and verify one archive.
function describe({ id, define, name, entry, url, bytes, unpackedBytes, sha512, sha256 }) {
	return {
		id,
		define,
		name,
		entry,
		url,
		bytes,
		unpackedBytes,
		requiredMegabytes: Math.ceil((bytes + unpackedBytes) / 1024 / 1024) + SPARE_MEGABYTES,
		downloadMegabytes: Math.round(bytes / 1000 / 1000),
		sha512,
		sha256
	}
}

// Pack a directory and measure it the way the installer needs to see it.
async function measurePacked(sevenZip, source, archive, fields) {
	await packAsset(sevenZip, source, archive)
	return describe({
		...fields,
		bytes: (await fs.stat(archive)).size,
		unpackedBytes: await directorySize(source),
		sha512: await hashFile(archive, 'sha512'),
		sha256: await hashFile(archive, 'sha256')
	})
}

async function readEmotionManifest() {
	const pinned = JSON.parse(await fs.readFile(EMOTION_MANIFEST, 'utf8'))
	if (!pinned.sha512 || !pinned.bytes) {
		throw new Error(
			`${path.basename(EMOTION_MANIFEST)} has no published archive yet. Run the ` +
				'"Emotion model asset" workflow once, then commit the manifest it produces.'
		)
	}
	return pinned
}

async function prepareAssets(projectRoot) {
	const output = path.join(projectRoot, 'build/installer-payload')
	await fs.mkdir(output, { recursive: true })
	const sevenZip = await getPath7za()
	await fs.copyFile(sevenZip, path.join(output, '7za.exe'))
	const toolRoot = path.dirname(path.dirname(sevenZip))
	for (const name of ['LICENSE.txt', 'COPYING']) {
		await fs.copyFile(path.join(toolRoot, name), path.join(output, name))
	}

	const built = []
	for (const asset of BACKENDS) {
		const source = path.join(projectRoot, asset.source)
		// Fail loudly: a stale archive would be published against the new version.
		await fs.access(source)
		built.push(
			await measurePacked(sevenZip, source, path.join(output, asset.name), {
				id: asset.id,
				define: asset.define,
				name: asset.name,
				entry: asset.entry,
				url: `${BASE_URL}/${asset.name}`
			})
		)
	}

	if (EMOTION_SOURCE) {
		const pinned = JSON.parse(await fs.readFile(EMOTION_MANIFEST, 'utf8'))
		const source = path.resolve(projectRoot, EMOTION_SOURCE)
		await fs.access(source)
		built.push(
			await measurePacked(sevenZip, source, path.join(output, pinned.name), {
				id: 'emotion',
				define: 'EMOTION',
				name: pinned.name,
				entry: pinned.entry,
				url: `${BASE_URL}/${pinned.name}`
			})
		)
	} else {
		const pinned = await readEmotionManifest()
		console.log(`Using the published ${pinned.name} (${pinned.tag})`)
		built.push(describe({ id: 'emotion', define: 'EMOTION', ...pinned }))
	}

	for (const asset of built) {
		console.log(`${asset.name}: ${asset.bytes} bytes, unpacked ${asset.unpackedBytes} bytes`)
	}

	const lines = ['; Generated by scripts/prepare-assets.cjs. Do not edit.']
	for (const asset of built) {
		lines.push(
			`!define ASSET_${asset.define}_NAME "${nsisString(asset.name)}"`,
			`!define ASSET_${asset.define}_URL "${nsisString(asset.url)}"`,
			`!define ASSET_${asset.define}_ENTRY "${nsisString(asset.entry)}"`,
			`!define ASSET_${asset.define}_HASH "${asset.sha512}"`,
			`!define ASSET_${asset.define}_REQUIRED_MB "${asset.requiredMegabytes}"`,
			`!define ASSET_${asset.define}_DOWNLOAD_MB "${asset.downloadMegabytes}"`
		)
	}
	await fs.writeFile(path.join(output, 'installer-assets.nsh'), `${lines.join('\n')}\n`)
	await fs.writeFile(
		path.join(output, 'installer-assets.json'),
		`${JSON.stringify({ version, assets: built }, null, '\t')}\n`
	)
}

module.exports = {
	prepareAssets,
	describe,
	directorySize,
	hashFile,
	measurePacked,
	packAsset,
	EMOTION_MANIFEST
}

if (require.main === module) {
	prepareAssets(path.resolve(process.argv[2] || path.join(__dirname, '..'))).catch((error) => {
		console.error(error)
		process.exitCode = 1
	})
}
