import type { BackendEvent, OverlaySettings } from '../shared/ipc.js'

type CaptionSource = 'voice' | 'speaker' | 'manual'

/** One rendered caption: `primary` is always in the wearer's own language. */
export interface CaptionLine {
	id: string
	source: 'microphone' | 'speaker'
	primary: string
	secondary: string | null
	pending: boolean
}

interface Caption {
	id: string
	source: CaptionSource
	original: string
	translated: string | null
	pending: boolean
	updatedAt: number
	/** Sample shown while adjusting placement; ignores the source filters. */
	preview?: boolean
}

const PREVIEW_ID = 'placement-preview'

/** Recent transcripts and their translations, in arrival order. */
export class CaptionBoard {
	private captions: Caption[] = []
	private readonly maxItems: number

	constructor(maxItems = 3) {
		this.maxItems = maxItems
	}

	clear(): boolean {
		const changed = this.captions.length > 0
		this.captions = []
		return changed
	}

	/** Applies one backend event; returns whether any caption changed. */
	handle(event: BackendEvent, translationEnabled: boolean, now = Date.now()): boolean {
		const data = isRecord(event.data) ? event.data : {}
		if (event.event === 'transcript.final') {
			const { utteranceId, text, source } = data
			if (typeof utteranceId !== 'string' || typeof text !== 'string') return false
			if (source !== 'voice' && source !== 'speaker' && source !== 'manual') return false
			this.captions.push({
				id: utteranceId,
				source,
				original: text,
				translated: null,
				pending: translationEnabled && source !== 'manual',
				updatedAt: now
			})
			this.captions = this.captions.slice(-this.maxItems * 4)
			return true
		}
		if (event.event === 'translation.result') {
			const caption = this.find(data.context)
			if (!caption || typeof data.text !== 'string') return false
			caption.translated = data.text
			caption.pending = false
			caption.updatedAt = now
			return true
		}
		if (event.event === 'translation.error') {
			const caption = this.find(data.context)
			if (!caption || !caption.pending) return false
			caption.pending = false
			caption.updatedAt = now
			return true
		}
		return false
	}

	/** Shows (or refreshes) a sample caption for adjusting the panel. */
	preview(now = Date.now()): void {
		this.captions = this.captions.filter((caption) => caption.id !== PREVIEW_ID)
		this.captions.push({
			id: PREVIEW_ID,
			source: 'speaker',
			original: 'This is a test caption for adjusting position and size',
			translated: '這是測試字幕，用來調整位置與大小',
			pending: false,
			updatedAt: now,
			preview: true
		})
	}

	/**
	 * Drops captions older than `holdMs`; returns whether any were dropped.
	 * With `previewOnly`, real captions are kept (a watch face keeps history).
	 */
	expire(holdMs: number, now = Date.now(), previewOnly = false): boolean {
		const before = this.captions.length
		this.captions = this.captions.filter(
			(caption) => (previewOnly && !caption.preview) || now - caption.updatedAt < holdMs
		)
		return this.captions.length !== before
	}

	/** Milliseconds until the next caption expires, or null when none will. */
	nextExpiry(holdMs: number, now = Date.now(), previewOnly = false): number | null {
		const expiring = this.captions.filter((caption) => !previewOnly || caption.preview)
		if (expiring.length === 0) return null
		const oldest = Math.min(...expiring.map((caption) => caption.updatedAt))
		return Math.max(0, oldest + holdMs - now)
	}

	lines(settings: Pick<OverlaySettings, 'showMicrophone' | 'showSpeaker'>): CaptionLine[] {
		return this.captions
			.filter(
				(caption) =>
					caption.preview ||
					(caption.source === 'speaker' ? settings.showSpeaker : settings.showMicrophone)
			)
			.slice(-this.maxItems)
			.map((caption) => {
				// Speaker audio is translated into the wearer's language, while the
				// microphone is translated out of it, so the roles swap.
				const speaker = caption.source === 'speaker'
				return {
					id: caption.id,
					source: speaker ? 'speaker' : 'microphone',
					primary: speaker ? (caption.translated ?? caption.original) : caption.original,
					secondary: speaker
						? caption.translated === null
							? null
							: caption.original
						: caption.translated,
					pending: caption.pending
				}
			})
	}

	private find(context: unknown): Caption | undefined {
		if (!isRecord(context) || typeof context.utteranceId !== 'string') return undefined
		return this.captions.find((caption) => caption.id === context.utteranceId)
	}
}

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === 'object' && value !== null && !Array.isArray(value)
}
