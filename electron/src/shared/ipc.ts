export const IpcChannel = {
	AppInfo: 'app:info',
	BackendGetState: 'backend:get-state',
	BackendRestart: 'backend:restart',
	BackendFailure: 'backend:failure',
	BackendSendText: 'backend:send-text',
	BackendListAudioDevices: 'backend:list-audio-devices',
	BackendStartRecording: 'backend:start-recording',
	BackendStopRecording: 'backend:stop-recording',
	BackendListTranslationProviders: 'backend:list-translation-providers',
	BackendTranslate: 'backend:translate',
	BackendUpdateSettings: 'backend:update-settings',
	BackendEvent: 'backend:event',
	OverlayPreview: 'overlay:preview',
	OverlayCalibrate: 'overlay:calibrate',
	WindowMinimize: 'window:minimize',
	WindowToggleMaximize: 'window:toggle-maximize',
	WindowClose: 'window:close'
} as const

export type IpcChannel = (typeof IpcChannel)[keyof typeof IpcChannel]

export type Platform = 'aix' | 'darwin' | 'freebsd' | 'linux' | 'openbsd' | 'sunos' | 'win32'

export interface AppInfo {
	name: string
	version: string
	platform: Platform
	arch: string
	isDev: boolean
	versions: {
		electron: string
		chrome: string
		node: string
		v8: string
	}
}

import type { LanguagePreference } from './i18n-core.js'

export interface BackendSettings {
	schemaVersion: 1
	audio: { deviceId: string; speakerDeviceId: string; autoStart: boolean }
	speech: { model: string; language: string }
	translation: {
		enabled: boolean
		provider: string
		model: string
		sourceLanguage: string
		targetLanguage: string
		endpoint: string
		apiKey: string
		deeplPlan: 'free' | 'pro'
		deeplApiKey: string
	}
	osc: { enabled: boolean; host: string; port: number }
	emotion: { enabled: boolean; parameter: string }
	privacy: { saveAudio: boolean; saveTranscripts: boolean }
	overlay: OverlaySettings
	ui: { language: LanguagePreference }
}

export type OverlayAnchor = 'head' | 'leftHand' | 'rightHand'

export interface OverlaySettings {
	enabled: boolean
	/** Tracked device the panel follows. */
	anchor: OverlayAnchor
	/** Panel size multiplier; 1 is 1 m wide on the head, 0.3 m on a hand. */
	scale: number
	/**
	 * Adjustments in meters along the panel's own axes, as the wearer sees it:
	 * +X right, +Y up, +Z toward the eyes (on the head, -Z is forward).
	 */
	offsetX: number
	offsetY: number
	offsetZ: number
	/** Rotation in degrees about the panel's own axes (Y, then X, then Z). */
	pitch: number
	yaw: number
	roll: number
	/**
	 * Row-major 3x4 resting pose relative to the anchor device. Identity on the
	 * head; on a hand, captured by calibration so the panel faces the wearer.
	 */
	basis: number[]
	opacity: number
	holdSeconds: number
	showMicrophone: boolean
	showSpeaker: boolean
	/**
	 * Hand anchors only: behave like a wrist watch. The panel fades in while it
	 * is looked at and keeps the latest captions.
	 */
	watchMode: boolean
}

export type OverlayStatus = 'disabled' | 'waiting' | 'connected' | 'unavailable' | 'error'

export type BackendSettingsPatch = {
	[K in Exclude<keyof BackendSettings, 'schemaVersion'>]?: Partial<BackendSettings[K]>
}

export interface AudioDevice {
	id: string
	name: string
	hostApi: string
	maxInputChannels: number
	isDefault: boolean
	source: RecordingSource
}

export type RecordingSource = 'microphone' | 'speaker'
export type RecordingState = 'idle' | 'starting' | 'listening' | 'stopping' | 'error'

export interface TranslationProviderInfo {
	id: string
	label: string
	local: boolean
}

export interface TranslationRequest {
	provider: string
	text: string
	sourceLanguage: string
	targetLanguage: string
	options?: Record<string, unknown>
	context?: Record<string, unknown>
}

export interface BackendState {
	sessionId: string
	seq: number
	backend: 'starting' | 'ready' | 'stopping' | 'stopped' | 'failed'
	recording: RecordingState
	recordingSource: RecordingSource | null
	recordings: Record<RecordingSource, RecordingState>
	models: Record<
		string,
		{ status: 'not_loaded' | 'loading' | 'ready' | 'failed'; device: string | null }
	>
	overlay: { status: OverlayStatus; message: string | null }
	settings: BackendSettings
}

export interface BackendEvent {
	v: 1
	type: 'event'
	sessionId: string
	seq: number
	event: string
	data: unknown
}

export type BackendResponse =
	| { v: 1; type: 'response'; id: string; ok: true; result: unknown }
	| {
			v: 1
			type: 'response'
			id: string | null
			ok: false
			error: { code: string; message: string; retryable: boolean }
	  }

export interface BackendRequestMap {
	'system.initialize': {
		params: {
			settings: BackendSettings
			dataPath: string
			suppressAutoStart?: boolean
			/** OS languages, so the backend can resolve `ui.language: auto`. */
			systemLanguages?: string[]
		}
		result: BackendState
	}
	'system.getState': { params: Record<string, never>; result: BackendState }
	'system.shutdown': { params: Record<string, never>; result: { state: 'stopped' } }
	'audio.listDevices': {
		params: Record<string, never>
		result: { devices: AudioDevice[] }
	}
	'recording.start': {
		params: { deviceId: string; source: RecordingSource }
		result: { state: BackendState['recording'] }
	}
	'recording.stop': {
		params: { source: RecordingSource }
		result: { state: BackendState['recording'] }
	}
	'translation.listProviders': {
		params: Record<string, never>
		result: { providers: TranslationProviderInfo[] }
	}
	'translation.translate': {
		params: TranslationRequest
		result: { jobId: string; accepted: true }
	}
	'overlay.show': { params: { path: string }; result: { accepted: true } }
	'overlay.hide': { params: Record<string, never>; result: { accepted: true } }
	'overlay.calibrate': {
		/** `update` only swaps the prompt of a calibration already running. */
		params: { path: string; update?: boolean }
		result: { accepted: true }
	}
	'text.send': { params: { text: string }; result: { utteranceId: string; accepted: boolean } }
	'settings.update': {
		params: { patch: BackendSettingsPatch }
		result: { settings: BackendSettings }
	}
}

export interface RendererApi {
	getAppInfo: () => Promise<AppInfo>
	backend: {
		getState: () => Promise<BackendState>
		restart: () => Promise<BackendState>
		onFailure: (listener: (message: string) => void) => () => void
		listAudioDevices: () => Promise<{ devices: AudioDevice[] }>
		startRecording: (
			deviceId: string,
			source: RecordingSource
		) => Promise<{ state: BackendState['recording'] }>
		stopRecording: (source: RecordingSource) => Promise<{ state: BackendState['recording'] }>
		listTranslationProviders: () => Promise<{ providers: TranslationProviderInfo[] }>
		translate: (request: TranslationRequest) => Promise<{ jobId: string; accepted: true }>
		sendText: (text: string) => Promise<{ utteranceId: string; accepted: boolean }>
		updateSettings: (patch: BackendSettingsPatch) => Promise<{ settings: BackendSettings }>
		onEvent: (listener: (event: BackendEvent) => void) => () => void
	}
	overlay: {
		/** Shows a sample caption in VR so its placement can be adjusted. */
		preview: () => Promise<void>
		/** Captures the watch pose; completion arrives as `overlay.calibration`. */
		calibrate: () => Promise<void>
	}
	window: {
		minimize: () => void
		toggleMaximize: () => void
		close: () => void
	}
}
