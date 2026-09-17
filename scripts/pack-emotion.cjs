/* eslint-disable @typescript-eslint/no-require-imports -- CommonJS CLI entry point. */
// Pack the exported emotion model into the one archive every release points at.
//
// This runs once per model revision, not once per release: the archive is
// published under its own tag and scripts/emotion-asset.json is committed with
// the measurements the installer is compiled against. Normal builds read that
// manifest and neither export nor pack the model again.
const { createRequire } = require('node:module')
const fs = require('node:fs/promises')
const path = require('node:path')

const builderRequire = createRequire(require.resolve('electron-builder'))
const { getPath7za } = builderRequire('app-builder-lib/out/toolsets/7zip')

const { directorySize, hashFile, packAsset, EMOTION_MANIFEST } = require('./prepare-assets.cjs')

const REPOSITORY = process.env.VRC_ASSET_REPOSITORY || 'https://github.com/phillychi3/vrc-v2t-osc'

async function packEmotion(projectRoot) {
	const pinned = JSON.parse(await fs.readFile(EMOTION_MANIFEST, 'utf8'))
	const source = path.resolve(
		projectRoot,
		process.env.VRC_EMOTION_MODEL_DIR || 'build/models/emotion'
	)
	// The export records what it actually produced; refuse to publish an archive
	// under a pin that describes a different model.
	const exported = JSON.parse(await fs.readFile(path.join(source, 'manifest.json'), 'utf8'))
	if (exported.revision !== pinned.revision || exported.format !== pinned.format) {
		throw new Error(
			`Exported ${exported.revision} (${exported.format}) does not match the pin ` +
				`${pinned.revision} (${pinned.format}). Bump modelVersion, tag and name first.`
		)
	}
	await fs.access(path.join(source, pinned.entry))

	const output = path.join(projectRoot, 'build/installer-payload')
	await fs.mkdir(output, { recursive: true })
	const archive = path.join(output, pinned.name)
	await packAsset(await getPath7za(), source, archive)

	const updated = {
		...pinned,
		url: `${REPOSITORY}/releases/download/${pinned.tag}/${pinned.name}`,
		bytes: (await fs.stat(archive)).size,
		unpackedBytes: await directorySize(source),
		sha512: await hashFile(archive, 'sha512'),
		sha256: await hashFile(archive, 'sha256')
	}
	await fs.writeFile(EMOTION_MANIFEST, `${JSON.stringify(updated, null, '\t')}\n`)
	console.log(`${archive}\n${JSON.stringify(updated, null, '\t')}`)
	return { archive, manifest: updated }
}

module.exports = { packEmotion }

if (require.main === module) {
	packEmotion(path.resolve(process.argv[2] || path.join(__dirname, '..'))).catch((error) => {
		console.error(error)
		process.exitCode = 1
	})
}
