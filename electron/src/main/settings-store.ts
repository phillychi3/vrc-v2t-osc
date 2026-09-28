import { mkdir, readFile, rename, writeFile } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import type { BackendSettings, OverlaySettings } from '../shared/ipc.js'
// A .ts specifier so the Node test runner can load this module directly.
import { IDENTITY_BASIS, OVERLAY_PRESETS } from '../shared/overlay-presets.ts'
import { isLanguagePreference } from '../shared/i18n-core.ts'

const DEFAULT_SETTINGS: BackendSettings = {
	schemaVersion: 1,
	audio: { deviceId: 'default', speakerDeviceId: 'speaker:default', autoStart: false },
	speech: { model: 'auto', language: 'zh' },
	translation: {
		enabled: false,
		provider: 'onnx',
		model: 'venddair/nllb-200-distilled-600M-onnx',
		sourceLanguage: 'zh',
		targetLanguage: 'en',
		endpoint: 'http://127.0.0.1:5000',
		apiKey: '',
		deeplPlan: 'free',
		deeplApiKey: ''
	},
	osc: { enabled: true, host: '127.0.0.1', port: 9000 },
	emotion: { enabled: false, parameter: '/avatar/parameters/v2t_sync_emo' },
	privacy: { saveAudio: false, saveTranscripts: false },
	overlay: {
		enabled: false,
		...OVERLAY_PRESETS.head,
		opacity: 0.9,
		holdSeconds: 8,
		showMicrophone: false,
		showSpeaker: true,
		basis: [...IDENTITY_BASIS]
	},
	ui: { language: 'auto' }
}

export class SettingsStore {
	readonly path: string
	private writeQueue = Promise.resolve()

	constructor(userDataPath: string) {
		this.path = join(userDataPath, 'settings.json')
	}

	async load(): Promise<BackendSettings> {
		try {
			const settings = migrate(JSON.parse(await readFile(this.path, 'utf8')))
			if (!isSettings(settings)) throw new Error('設定格式或版本不相容')
			return settings
		} catch (error) {
			if (isMissingFile(error)) return structuredClone(DEFAULT_SETTINGS)
			console.error(`無法讀取設定，改用預設值: ${this.path}`, error)
			return structuredClone(DEFAULT_SETTINGS)
		}
	}

	save(settings: BackendSettings): Promise<void> {
		const snapshot = structuredClone(settings)
		const operation = this.writeQueue.then(async () => {
			await mkdir(dirname(this.path), { recursive: true })
			const temporaryPath = `${this.path}.tmp`
			await writeFile(temporaryPath, `${JSON.stringify(snapshot, null, 2)}\n`, 'utf8')
			await rename(temporaryPath, this.path)
		})
		this.writeQueue = operation.catch(() => undefined)
		return operation
	}
}

/** Fills sections added after a settings file was written. */
function migrate(value: unknown): unknown {
	if (!isRecord(value) || value.schemaVersion !== 1) return value
	if (!('ui' in value)) value = { ...value, ui: structuredClone(DEFAULT_SETTINGS.ui) }
	return migrateOverlay(value as Record<string, unknown>)
}

function migrateOverlay(value: Record<string, unknown>): unknown {
	if (!('overlay' in value)) return { ...value, overlay: structuredClone(DEFAULT_SETTINGS.overlay) }
	const overlay = value.overlay
	if (!isRecord(overlay)) return value
	if (!('distance' in overlay))
		return {
			...value,
			overlay: { watchMode: false, basis: [...IDENTITY_BASIS], ...overlay }
		}
	// Early overlay builds stored only a head distance and height.
	const { distance, verticalOffset, ...rest } = overlay
	const placement =
		overlay.anchor === 'leftHand' || overlay.anchor === 'rightHand'
			? { ...OVERLAY_PRESETS[overlay.anchor], scale: overlay.scale }
			: {
					...OVERLAY_PRESETS.head,
					scale: overlay.scale,
					offsetY: verticalOffset,
					offsetZ: typeof distance === 'number' ? -distance : distance
				}
	return { ...value, overlay: { ...rest, ...placement, basis: [...IDENTITY_BASIS] } }
}

function isSettings(value: unknown): value is BackendSettings {
	if (!isRecord(value) || value.schemaVersion !== 1) return false
	const { audio, speech, translation, osc, emotion, privacy, overlay, ui } = value
	return (
		hasExactKeys(audio, ['deviceId', 'speakerDeviceId', 'autoStart']) &&
		typeof audio.deviceId === 'string' &&
		audio.deviceId.trim().length > 0 &&
		typeof audio.speakerDeviceId === 'string' &&
		audio.speakerDeviceId.trim().length > 0 &&
		typeof audio.autoStart === 'boolean' &&
		hasExactKeys(speech, ['model', 'language']) &&
		typeof speech.model === 'string' &&
		speech.model.trim().length > 0 &&
		typeof speech.language === 'string' &&
		speech.language.trim().length > 0 &&
		hasExactKeys(translation, [
			'enabled',
			'provider',
			'model',
			'sourceLanguage',
			'targetLanguage',
			'endpoint',
			'apiKey',
			'deeplPlan',
			'deeplApiKey'
		]) &&
		typeof translation.enabled === 'boolean' &&
		typeof translation.provider === 'string' &&
		['onnx', 'libretranslate', 'deepl'].includes(translation.provider) &&
		typeof translation.model === 'string' &&
		translation.model === DEFAULT_SETTINGS.translation.model &&
		typeof translation.sourceLanguage === 'string' &&
		translation.sourceLanguage.trim().length > 0 &&
		typeof translation.targetLanguage === 'string' &&
		translation.targetLanguage.trim().length > 0 &&
		typeof translation.endpoint === 'string' &&
		translation.endpoint.trim().length > 0 &&
		typeof translation.apiKey === 'string' &&
		(translation.deeplPlan === 'free' || translation.deeplPlan === 'pro') &&
		typeof translation.deeplApiKey === 'string' &&
		hasExactKeys(osc, ['enabled', 'host', 'port']) &&
		typeof osc.enabled === 'boolean' &&
		typeof osc.host === 'string' &&
		osc.host.trim().length > 0 &&
		Number.isInteger(osc.port) &&
		typeof osc.port === 'number' &&
		osc.port >= 1 &&
		osc.port <= 65_535 &&
		hasExactKeys(emotion, ['enabled', 'parameter']) &&
		typeof emotion.enabled === 'boolean' &&
		typeof emotion.parameter === 'string' &&
		emotion.parameter.startsWith('/') &&
		!emotion.parameter.includes(' ') &&
		hasExactKeys(privacy, ['saveAudio', 'saveTranscripts']) &&
		typeof privacy.saveAudio === 'boolean' &&
		typeof privacy.saveTranscripts === 'boolean' &&
		isOverlaySettings(overlay) &&
		hasExactKeys(ui, ['language']) &&
		isLanguagePreference(ui.language) &&
		Object.keys(value).length === 9
	)
}

function isOverlaySettings(value: unknown): value is OverlaySettings {
	return (
		hasExactKeys(value, Object.keys(DEFAULT_SETTINGS.overlay)) &&
		typeof value.enabled === 'boolean' &&
		(value.anchor === 'head' || value.anchor === 'leftHand' || value.anchor === 'rightHand') &&
		inRange(value.scale, 0.2, 2) &&
		inRange(value.offsetX, -1, 1) &&
		inRange(value.offsetY, -1, 1) &&
		inRange(value.offsetZ, -5, 1) &&
		inRange(value.pitch, -180, 180) &&
		inRange(value.yaw, -180, 180) &&
		inRange(value.roll, -180, 180) &&
		inRange(value.opacity, 0.2, 1) &&
		Number.isInteger(value.holdSeconds) &&
		inRange(value.holdSeconds, 2, 60) &&
		typeof value.showMicrophone === 'boolean' &&
		typeof value.showSpeaker === 'boolean' &&
		typeof value.watchMode === 'boolean' &&
		Array.isArray(value.basis) &&
		value.basis.length === 12 &&
		value.basis.every((entry) => inRange(entry, -10, 10))
	)
}

function inRange(value: unknown, minimum: number, maximum: number): value is number {
	return typeof value === 'number' && value >= minimum && value <= maximum
}

function hasExactKeys(value: unknown, keys: string[]): value is Record<string, unknown> {
	return (
		isRecord(value) &&
		Object.keys(value).length === keys.length &&
		keys.every((key) => key in value)
	)
}

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isMissingFile(error: unknown): boolean {
	return isRecord(error) && error.code === 'ENOENT'
}
