import { derived, get, writable } from 'svelte/store'
import type {
	AudioDevice,
	BackendEvent,
	BackendSettingsPatch,
	BackendState,
	RecordingSource,
	RecordingState,
	TranslationProviderInfo,
	TranslationRequest
} from '$shared/ipc'

export interface Transcript {
	utteranceId: string
	text: string
	source: string
	emotion?: number
	receivedAt?: string
	translationPending?: boolean
	translatedText?: string
	translationProvider?: string
	translationSourceLanguage?: string
	translationTargetLanguage?: string
	oscStatus?: 'pending' | 'sent' | 'skipped'
	oscReason?: string
}

const state = writable<BackendState | null>(null)
const error = writable<string | null>(null)
const failed = writable(false)
const restarting = writable(false)
const transcripts = writable<Transcript[]>([])
const audioDevices = writable<AudioDevice[]>([])
const translationProviders = writable<TranslationProviderInfo[]>([])
const translationStatuses = writable<Record<string, 'loading' | 'ready' | 'failed'>>({})
const recordingRequestPending: Record<RecordingSource, boolean> = {
	microphone: false,
	speaker: false
}

export const backend = {
	state: { subscribe: state.subscribe },
	error: { subscribe: error.subscribe },
	failed: { subscribe: failed.subscribe },
	restarting: { subscribe: restarting.subscribe },
	transcripts: { subscribe: transcripts.subscribe },
	audioDevices: { subscribe: audioDevices.subscribe },
	translationProviders: { subscribe: translationProviders.subscribe },
	translationStatuses: { subscribe: translationStatuses.subscribe },
	connected: derived(
		[state, failed, restarting],
		([$state, $failed, $restarting]) => $state?.backend === 'ready' && !$failed && !$restarting
	),
	clearTranscripts(): void {
		transcripts.set([])
	},

	async connect(): Promise<() => void> {
		const api = window.api?.backend
		if (!api) {
			error.set('Backend bridge 無法使用')
			return () => undefined
		}
		let buffering = true
		const buffered: BackendEvent[] = []
		const unsubscribe = api.onEvent((event) => {
			if (buffering) buffered.push(event)
			else applyEvent(event)
		})
		const unsubscribeFailure = api.onFailure(markFailed)
		try {
			applySnapshot(await api.getState())
			buffering = false
			for (const event of buffered) applyEvent(event)
			clearError()
			await Promise.all([
				backend.refreshAudioDevices(),
				backend.refreshTranslationProviders()
			]).catch(() => undefined)
		} catch (reason) {
			markFailed(messageOf(reason))
		} finally {
			buffering = false
			buffered.length = 0
		}
		return () => {
			unsubscribe()
			unsubscribeFailure()
		}
	},

	async restart(): Promise<void> {
		if (get(restarting)) return
		restarting.set(true)
		try {
			const api = window.api?.backend
			if (!api) throw new Error('Backend bridge 無法使用')
			const snapshot = await api.restart()
			applySnapshot(snapshot)
			failed.set(false)
			clearError()
			await Promise.all([
				backend.refreshAudioDevices(),
				backend.refreshTranslationProviders()
			]).catch(() => undefined)
		} catch (reason) {
			markFailed(messageOf(reason))
		} finally {
			restarting.set(false)
		}
	},

	async refreshTranslationProviders(): Promise<void> {
		try {
			const result = await window.api?.backend.listTranslationProviders()
			translationProviders.set(result?.providers ?? [])
			clearError()
		} catch (reason) {
			error.set(messageOf(reason))
			throw reason
		}
	},

	async translate(text: string, context: Record<string, unknown> = {}): Promise<string> {
		const current = get(state)
		if (!current) throw new Error('後端尚未連線')
		const settings = current.settings.translation
		if (!settings.enabled) throw new Error('翻譯功能尚未啟用')
		let options: TranslationRequest['options'] = { model: settings.model }
		if (settings.provider === 'libretranslate')
			options = { endpoint: settings.endpoint, apiKey: settings.apiKey }
		else if (settings.provider === 'deepl')
			options = { plan: settings.deeplPlan, apiKey: settings.deeplApiKey }
		try {
			const result = await window.api?.backend.translate({
				provider: settings.provider,
				text,
				sourceLanguage: settings.sourceLanguage,
				targetLanguage: settings.targetLanguage,
				options,
				context
			})
			if (!result) throw new Error('Backend bridge 無法使用')
			clearError()
			return result.jobId
		} catch (reason) {
			error.set(messageOf(reason))
			throw reason
		}
	},

	async refreshAudioDevices(): Promise<void> {
		try {
			const result = await window.api?.backend.listAudioDevices()
			audioDevices.set(result?.devices ?? [])
			clearError()
		} catch (reason) {
			error.set(messageOf(reason))
			throw reason
		}
	},

	async toggleRecording(source: RecordingSource): Promise<void> {
		if (get(failed) || get(restarting)) return
		if (recordingRequestPending[source]) return
		const current = get(state)
		if (!current) return
		recordingRequestPending[source] = true
		try {
			const sourceState = current.recordings[source]
			if (sourceState === 'listening' || sourceState === 'starting') {
				await window.api?.backend.stopRecording(source)
			} else {
				const deviceId =
					source === 'speaker'
						? current.settings.audio.speakerDeviceId
						: current.settings.audio.deviceId
				await window.api?.backend.startRecording(deviceId, source)
			}
			clearError()
		} catch (reason) {
			error.set(messageOf(reason))
			throw reason
		} finally {
			recordingRequestPending[source] = false
		}
	},

	async sendText(text: string): Promise<void> {
		try {
			await window.api?.backend.sendText(text)
			clearError()
		} catch (reason) {
			error.set(messageOf(reason))
			throw reason
		}
	},

	async updateSettings(patch: BackendSettingsPatch): Promise<void> {
		try {
			const result = await window.api?.backend.updateSettings(patch)
			if (result)
				state.update((current) => (current ? { ...current, settings: result.settings } : current))
			clearError()
		} catch (reason) {
			error.set(messageOf(reason))
			throw reason
		}
	}
}

function applySnapshot(snapshot: BackendState): void {
	const current = get(state)
	if (current?.sessionId === snapshot.sessionId && current.seq > snapshot.seq) return
	state.set(snapshot)
}

function clearError(): void {
	if (!get(failed)) error.set(null)
}

function markFailed(message: string): void {
	failed.set(true)
	error.set(message)
	state.update((value) =>
		value
			? {
					...value,
					backend: 'failed',
					recording: 'idle',
					recordingSource: null,
					recordings: { microphone: 'idle', speaker: 'idle' }
				}
			: value
	)
	audioDevices.set([])
	translationProviders.set([])
	translationStatuses.set({})
	transcripts.update((items) =>
		items.map((item) => ({
			...item,
			translationPending: false,
			...(item.oscStatus === 'pending'
				? { oscStatus: 'skipped' as const, oscReason: 'closed' }
				: {})
		}))
	)
}

function applyEvent(event: BackendEvent): void {
	const current = get(state)
	if (current && current.sessionId === event.sessionId && event.seq <= current.seq) return

	if (event.event === 'backend.ready' && isBackendState(event.data)) {
		failed.set(false)
		clearError()
		translationStatuses.set({})
		applySnapshot({ ...event.data, sessionId: event.sessionId, seq: event.seq })
		return
	}
	if (!current || current.sessionId !== event.sessionId || get(failed)) return
	if (event.event === 'model.status' && isModelStatus(event.data)) {
		const modelStatus = event.data
		state.update((value) =>
			value && value.sessionId === event.sessionId
				? {
						...value,
						seq: event.seq,
						models: {
							...value.models,
							[modelStatus.model]: {
								status: modelStatus.status,
								device: modelStatus.device
							}
						}
					}
				: value
		)
		return
	}
	if (event.event === 'recording.state' && isRecordingState(event.data)) {
		const recording = event.data.state
		const source = event.data.source
		state.update((value) =>
			value && value.sessionId === event.sessionId
				? updateRecordingState(value, source, recording, event.seq)
				: value
		)
		return
	}
	if (event.event === 'transcript.final' && isTranscript(event.data)) {
		const transcript: Transcript = {
			...event.data,
			oscStatus: event.data.source === 'speaker' ? undefined : 'pending',
			receivedAt: new Date().toISOString(),
			translationPending:
				Boolean(current?.settings.translation.enabled) &&
				(event.data.source === 'voice' || event.data.source === 'speaker')
		}
		transcripts.update((items) => [transcript, ...items].slice(0, 1_000))
	}
	if (
		(event.event === 'osc.sent' || event.event === 'osc.skipped') &&
		isRecord(event.data) &&
		event.data.kind === 'text' &&
		typeof event.data.utteranceId === 'string'
	) {
		const result = event.data
		const status = event.event === 'osc.sent' ? 'sent' : 'skipped'
		transcripts.update((items) =>
			items.map((item) =>
				item.utteranceId === result.utteranceId
					? {
							...item,
							oscStatus: status,
							oscReason: typeof result.reason === 'string' ? result.reason : undefined
						}
					: item
			)
		)
	}
	if (event.event === 'emotion.result' && isEmotionResult(event.data)) {
		const result = event.data
		transcripts.update((items) =>
			items.map((item) =>
				item.utteranceId === result.utteranceId ? { ...item, emotion: result.emotion } : item
			)
		)
	}
	if (event.event === 'translation.status' && isTranslationStatus(event.data)) {
		const status = event.data
		translationStatuses.update((statuses) => ({
			...statuses,
			[status.provider]: status.status
		}))
	}
	if (event.event === 'translation.result' && isTranslationResult(event.data)) {
		const result = event.data
		transcripts.update((items) =>
			items.map((item) =>
				item.utteranceId === result.context.utteranceId
					? {
							...item,
							translationPending: false,
							translatedText: result.text,
							translationProvider: result.provider,
							translationSourceLanguage: result.sourceLanguage,
							translationTargetLanguage: result.targetLanguage
						}
					: item
			)
		)
	}
	if (event.event === 'translation.error' && isTranslationError(event.data)) {
		error.set(event.data.message)
		const utteranceId = event.data.context?.utteranceId
		if (utteranceId) {
			transcripts.update((items) =>
				items.map((item) =>
					item.utteranceId === utteranceId ? { ...item, translationPending: false } : item
				)
			)
		}
	}
	if (event.event === 'error' && isBackendErrorEvent(event.data)) {
		error.set(event.data.message)
	}
	state.update((value) =>
		value && value.sessionId === event.sessionId ? { ...value, seq: event.seq } : value
	)
}

function isModelStatus(value: unknown): value is {
	model: string
	status: BackendState['models'][string]['status']
	device: string | null
} {
	return (
		isRecord(value) &&
		typeof value.model === 'string' &&
		['not_loaded', 'loading', 'ready', 'failed'].includes(String(value.status)) &&
		(value.device === null || typeof value.device === 'string')
	)
}

function isRecordingState(
	value: unknown
): value is { state: RecordingState; source: RecordingSource } {
	return (
		isRecord(value) &&
		['idle', 'starting', 'listening', 'stopping', 'error'].includes(String(value.state)) &&
		(value.source === 'microphone' || value.source === 'speaker')
	)
}

function updateRecordingState(
	value: BackendState,
	source: RecordingSource,
	sourceState: RecordingState,
	seq: number
): BackendState {
	const recordings = { ...value.recordings, [source]: sourceState }
	const activeSources = (Object.keys(recordings) as RecordingSource[]).filter(
		(name) => !['idle', 'error'].includes(recordings[name])
	)
	const states = Object.values(recordings)
	const recording: RecordingState = states.includes('listening')
		? 'listening'
		: states.includes('starting')
			? 'starting'
			: states.includes('stopping')
				? 'stopping'
				: states.includes('error')
					? 'error'
					: 'idle'
	return {
		...value,
		seq,
		recording,
		recordingSource: activeSources.length === 1 ? activeSources[0] : null,
		recordings
	}
}

function isBackendState(value: unknown): value is BackendState {
	return isRecord(value) && typeof value.sessionId === 'string' && typeof value.seq === 'number'
}

function isTranscript(value: unknown): value is Transcript {
	return (
		isRecord(value) &&
		typeof value.utteranceId === 'string' &&
		typeof value.text === 'string' &&
		typeof value.source === 'string'
	)
}

function isBackendErrorEvent(value: unknown): value is { message: string } {
	return isRecord(value) && typeof value.message === 'string'
}

function isEmotionResult(value: unknown): value is { utteranceId: string; emotion: number } {
	return (
		isRecord(value) &&
		typeof value.utteranceId === 'string' &&
		typeof value.emotion === 'number' &&
		Number.isInteger(value.emotion) &&
		value.emotion >= 0 &&
		value.emotion <= 7
	)
}

function isTranslationStatus(
	value: unknown
): value is { provider: string; status: 'loading' | 'ready' | 'failed' } {
	return (
		isRecord(value) &&
		typeof value.provider === 'string' &&
		['loading', 'ready', 'failed'].includes(String(value.status))
	)
}

function isTranslationError(value: unknown): value is {
	jobId: string
	message: string
	context?: { utteranceId?: string }
} {
	return (
		isRecord(value) &&
		typeof value.jobId === 'string' &&
		typeof value.message === 'string' &&
		(value.context === undefined ||
			(isRecord(value.context) &&
				(value.context.utteranceId === undefined || typeof value.context.utteranceId === 'string')))
	)
}

function isTranslationResult(value: unknown): value is {
	jobId: string
	provider: string
	text: string
	sourceLanguage: string
	targetLanguage: string
	context: { utteranceId: string }
} {
	return (
		isRecord(value) &&
		typeof value.jobId === 'string' &&
		typeof value.provider === 'string' &&
		typeof value.text === 'string' &&
		typeof value.sourceLanguage === 'string' &&
		typeof value.targetLanguage === 'string' &&
		isRecord(value.context) &&
		typeof value.context.utteranceId === 'string'
	)
}

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function messageOf(reason: unknown): string {
	return reason instanceof Error ? reason.message : String(reason)
}
