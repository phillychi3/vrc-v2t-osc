import { BrowserWindow, type NativeImage } from 'electron'
import { mkdir, rename, writeFile } from 'node:fs/promises'
import { join } from 'node:path'
import type {
	BackendEvent,
	BackendSettings,
	BackendSettingsPatch,
	OverlaySettings,
	OverlayStatus
} from '../shared/ipc.js'
import { placementOf } from '../shared/overlay-presets.js'
import type { BackendProcess } from './backend-process.js'
import { CaptionBoard } from './overlay-captions.js'
import { overlayHtml } from './env.js'

/** Texture sizes in pixels; the panel's aspect ratio in VR follows from them. */
const CAPTION_TEXTURE = { width: 1200, height: 420 }
const WATCH_TEXTURE = { width: 900, height: 640 }
const PAINT_TIMEOUT_MS = 2_000
const LABELS = { translating: '翻譯中…', empty: '等待語音…' }
const CALIBRATION_TITLE = '像看手錶一樣抬起手臂，看著要戴手錶的位置'
const CALIBRATION_START_HINT = '保持不動約 2 秒即完成校準'

function isWatch(overlay: OverlaySettings): boolean {
	return overlay.watchMode && overlay.anchor !== 'head'
}

/**
 * Turns backend transcript events into a SteamVR caption overlay.
 *
 * Captions are laid out by Chromium in a hidden offscreen window, so CJK text,
 * emoji and font fallback render exactly like the main UI. Each frame is saved
 * as a PNG under `<userData>/overlay/` and handed to the Python backend, which
 * owns the OpenVR connection.
 */
export class OverlayController {
	private readonly backend: BackendProcess
	private readonly directory: string
	private readonly applySettings: (patch: BackendSettingsPatch) => Promise<void>
	private calibrating = false
	private pendingHint: string | null = null
	private hintPainting = false
	private readonly board = new CaptionBoard()
	private settings: BackendSettings | null = null
	private status: OverlayStatus = 'disabled'
	private window: BrowserWindow | null = null
	private windowIsWatch = false
	private windowTask: Promise<BrowserWindow> | null = null
	private rendering = false
	private renderQueued = false
	private expiryTimer: NodeJS.Timeout | null = null
	private visible = false
	private frame = 0
	private generation = 0
	private disposed = false

	constructor(
		backend: BackendProcess,
		dataPath: string,
		applySettings: (patch: BackendSettingsPatch) => Promise<void>
	) {
		this.backend = backend
		this.directory = join(dataPath, 'overlay')
		this.applySettings = applySettings
	}

	handleEvent(event: BackendEvent): void {
		if (this.disposed) return
		const data = event.data as Record<string, unknown> | null
		if (event.event === 'backend.ready') {
			const state = data as { settings?: BackendSettings; overlay?: { status?: OverlayStatus } }
			this.reset()
			this.settings = state.settings ?? null
			this.status = state.overlay?.status ?? 'disabled'
			return
		}
		if (event.event === 'settings.changed') {
			const previous = this.settings?.overlay
			this.settings = (data?.settings as BackendSettings | undefined) ?? this.settings
			const overlay = this.settings?.overlay
			if (!overlay?.enabled) {
				this.reset()
				return
			}
			// Show a sample while the wearer moves or resizes the panel.
			if (
				previous?.enabled &&
				(JSON.stringify(placementOf(previous)) !== JSON.stringify(placementOf(overlay)) ||
					JSON.stringify(previous.basis) !== JSON.stringify(overlay.basis) ||
					previous.opacity !== overlay.opacity)
			)
				this.board.preview()
			// The watch face and the caption strip use differently sized textures.
			if (this.window && this.windowIsWatch !== isWatch(overlay)) this.destroyWindow()
			this.scheduleRender()
			return
		}
		if (event.event === 'overlay.status') {
			this.status = (data?.status as OverlayStatus | undefined) ?? 'disabled'
			// SteamVR starts with no texture; resend whatever is still on screen.
			this.visible = false
			if (this.status !== 'connected') this.calibrating = false
			if (this.status === 'connected') this.scheduleRender()
			return
		}
		if (event.event === 'overlay.calibration') {
			if (data?.status === 'running') {
				// Mirror the backend's hint in the headset: the wearer cannot see
				// the settings page while holding the pose.
				if (this.calibrating && typeof data.message === 'string')
					this.queueCalibrationHint(data.message)
				return
			}
			this.calibrating = false
			this.pendingHint = null
			const basis = data?.basis
			if (data?.status === 'done' && Array.isArray(basis)) {
				// The captured pose becomes the resting pose; adjustments start over.
				const reset = { offsetX: 0, offsetY: 0, offsetZ: 0, pitch: 0, yaw: 0, roll: 0 }
				void this.applySettings({ overlay: { basis: basis as number[], ...reset } }).catch(
					(error) => console.error('Saving VR watch calibration failed:', error)
				)
			} else {
				this.scheduleRender()
			}
			return
		}
		if (!this.settings?.overlay.enabled) return
		if (this.board.handle(event, this.settings.translation.enabled)) this.scheduleRender()
	}

	/**
	 * Asks the wearer to raise the arm and look at the forearm spot where the
	 * watch should sit; the backend captures
	 * that pose and reports it as an `overlay.calibration` event.
	 */
	async calibrate(): Promise<void> {
		const overlay = this.settings?.overlay
		if (this.disposed || !overlay?.enabled) throw new Error('請先啟用 VR 字幕')
		if (overlay.anchor === 'head') throw new Error('請先把字幕放在手上')
		if (this.status !== 'connected') throw new Error('尚未連線 SteamVR')
		this.calibrating = true
		try {
			// Let an in-flight caption frame finish before reusing the renderer.
			while (this.rendering) await new Promise((resolve) => setTimeout(resolve, 20))
			const path = await this.writeCalibrationPrompt(CALIBRATION_START_HINT)
			await this.backend.request('overlay.calibrate', { path })
		} catch (error) {
			this.calibrating = false
			throw error
		}
	}

	private queueCalibrationHint(hint: string): void {
		const idle = this.pendingHint === null && !this.hintPainting
		this.pendingHint = hint
		if (idle) void this.flushCalibrationHints()
	}

	/** Repaints the prompt with the newest hint, dropping intermediate ones. */
	private async flushCalibrationHints(): Promise<void> {
		this.hintPainting = true
		try {
			while (this.pendingHint !== null && this.calibrating) {
				const hint = this.pendingHint
				this.pendingHint = null
				const path = await this.writeCalibrationPrompt(hint)
				// `update` is ignored by the backend once calibration has ended.
				if (this.calibrating)
					await this.backend.request('overlay.calibrate', { path, update: true })
			}
		} catch (error) {
			console.error('Updating the VR calibration prompt failed:', error)
		} finally {
			this.hintPainting = false
		}
	}

	private async writeCalibrationPrompt(hint: string): Promise<string> {
		const window = await this.ensureWindow(false)
		const prompt = { id: 'calibration', source: 'microphone', pending: false }
		const image = await this.paint(
			window,
			[{ ...prompt, primary: CALIBRATION_TITLE, secondary: hint }],
			false
		)
		await mkdir(this.directory, { recursive: true })
		// Alternate files so SteamVR never reads a prompt mid-write.
		const path = join(this.directory, `calibration-${this.frame++ % 2}.png`)
		await writeFile(`${path}.tmp`, image.toPNG())
		await rename(`${path}.tmp`, path)
		return path
	}

	/** Shows a sample caption so the panel can be positioned without speaking. */
	preview(): void {
		if (this.disposed || !this.settings?.overlay.enabled) return
		this.board.preview()
		this.scheduleRender()
	}

	/** Forget captions and release the offscreen window, e.g. after a backend crash. */
	reset(): void {
		this.generation++
		this.calibrating = false
		this.board.clear()
		this.visible = false
		this.status = 'disabled'
		this.clearExpiry()
		this.destroyWindow()
	}

	dispose(): void {
		this.disposed = true
		this.reset()
	}

	private scheduleRender(): void {
		if (this.rendering) {
			this.renderQueued = true
			return
		}
		this.rendering = true
		void this.render()
			.catch((error) => console.error('VR caption render failed:', error))
			.finally(() => {
				this.rendering = false
				if (this.renderQueued && !this.disposed) {
					this.renderQueued = false
					this.scheduleRender()
				}
			})
	}

	private async render(): Promise<void> {
		const overlay = this.settings?.overlay
		this.clearExpiry()
		// The backend shows the calibration prompt; captions resume afterwards.
		if (!overlay?.enabled || this.status !== 'connected' || this.calibrating) return
		const holdMs = overlay.holdSeconds * 1_000
		const watch = isWatch(overlay)
		this.board.expire(holdMs, Date.now(), watch)
		const lines = this.board.lines(overlay)
		// A watch face stays up (showing a waiting note); the strip hides when empty.
		if (lines.length === 0 && !watch) {
			if (this.visible) {
				this.visible = false
				await this.backend.request('overlay.hide', {})
			}
			return
		}
		const window = await this.ensureWindow(watch)
		const image = await this.paint(window, lines, watch)
		if (this.disposed || this.status !== 'connected') return
		await mkdir(this.directory, { recursive: true })
		// Alternate between two files so SteamVR never reads a file mid-write.
		const path = join(this.directory, `caption-${this.frame++ % 2}.png`)
		await writeFile(`${path}.tmp`, image.toPNG())
		await rename(`${path}.tmp`, path)
		await this.backend.request('overlay.show', { path })
		this.visible = true
		const next = this.board.nextExpiry(holdMs, Date.now(), watch)
		if (next !== null) this.expiryTimer = setTimeout(() => this.scheduleRender(), next + 50)
	}

	private async paint(window: BrowserWindow, lines: unknown, watch: boolean): Promise<NativeImage> {
		const contents = window.webContents
		const options = { watch }
		await contents.executeJavaScript(
			`window.renderCaptions(${JSON.stringify(lines)}, ${JSON.stringify(LABELS)}, ${JSON.stringify(options)})`
		)
		return new Promise((resolve, reject) => {
			const timer = setTimeout(() => {
				contents.off('paint', onPaint)
				reject(new Error('Offscreen caption paint timed out'))
			}, PAINT_TIMEOUT_MS)
			const onPaint = (_event: unknown, _dirty: unknown, image: NativeImage) => {
				// The first frame after a resize or invalidate can be empty.
				if (image.isEmpty()) return
				clearTimeout(timer)
				contents.off('paint', onPaint)
				resolve(image)
			}
			contents.on('paint', onPaint)
			contents.invalidate()
		})
	}

	private ensureWindow(watch: boolean): Promise<BrowserWindow> {
		if (this.window && !this.window.isDestroyed() && this.windowIsWatch === watch)
			return Promise.resolve(this.window)
		if (!this.windowTask) {
			this.destroyWindow()
			this.windowTask = this.createWindow(watch).finally(() => {
				this.windowTask = null
			})
		}
		return this.windowTask
	}

	private async createWindow(watch: boolean): Promise<BrowserWindow> {
		const generation = this.generation
		const texture = watch ? WATCH_TEXTURE : CAPTION_TEXTURE
		const window = new BrowserWindow({
			width: texture.width,
			height: texture.height,
			show: false,
			frame: false,
			transparent: true,
			backgroundColor: '#00000000',
			skipTaskbar: true,
			focusable: false,
			webPreferences: {
				offscreen: true,
				contextIsolation: true,
				nodeIntegration: false,
				sandbox: true
			}
		})
		window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }))
		window.webContents.on('will-navigate', (event) => event.preventDefault())
		window.webContents.setFrameRate(30)
		await window.loadFile(overlayHtml)
		if (this.disposed || generation !== this.generation) {
			window.destroy()
			throw new Error('VR caption renderer was reset')
		}
		this.window = window
		this.windowIsWatch = watch
		return window
	}

	private destroyWindow(): void {
		if (this.window && !this.window.isDestroyed()) this.window.destroy()
		this.window = null
	}

	private clearExpiry(): void {
		if (this.expiryTimer) clearTimeout(this.expiryTimer)
		this.expiryTimer = null
	}
}
