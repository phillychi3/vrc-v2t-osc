import { contextBridge, ipcRenderer } from 'electron'
import type { AppInfo, BackendEvent, BackendState, RendererApi } from '../shared/ipc.js'
import { IpcChannel } from '../shared/ipc.js'

/**
 * The only surface the renderer gets. Everything is explicit — no generic
 * `invoke(channel, ...args)` escape hatch, so the renderer cannot reach a
 * channel that is not listed here.
 */
const api: RendererApi = {
	getAppInfo: () => ipcRenderer.invoke(IpcChannel.AppInfo) as Promise<AppInfo>,
	backend: {
		getState: () => ipcRenderer.invoke(IpcChannel.BackendGetState) as Promise<BackendState>,
		restart: () => ipcRenderer.invoke(IpcChannel.BackendRestart),
		onFailure: (listener) => {
			const handler = (_event: Electron.IpcRendererEvent, message: string) => listener(message)
			ipcRenderer.on(IpcChannel.BackendFailure, handler)
			return () => ipcRenderer.removeListener(IpcChannel.BackendFailure, handler)
		},
		listAudioDevices: () => ipcRenderer.invoke(IpcChannel.BackendListAudioDevices),
		startRecording: (deviceId, source) =>
			ipcRenderer.invoke(IpcChannel.BackendStartRecording, deviceId, source),
		stopRecording: (source) => ipcRenderer.invoke(IpcChannel.BackendStopRecording, source),
		listTranslationProviders: () => ipcRenderer.invoke(IpcChannel.BackendListTranslationProviders),
		translate: (request) => ipcRenderer.invoke(IpcChannel.BackendTranslate, request),
		sendText: (text) => ipcRenderer.invoke(IpcChannel.BackendSendText, text),
		updateSettings: (patch) => ipcRenderer.invoke(IpcChannel.BackendUpdateSettings, patch),
		onEvent: (listener) => {
			const handler = (_event: Electron.IpcRendererEvent, payload: BackendEvent) =>
				listener(payload)
			ipcRenderer.on(IpcChannel.BackendEvent, handler)
			return () => ipcRenderer.removeListener(IpcChannel.BackendEvent, handler)
		}
	},
	window: {
		minimize: () => ipcRenderer.send(IpcChannel.WindowMinimize),
		toggleMaximize: () => ipcRenderer.send(IpcChannel.WindowToggleMaximize),
		close: () => ipcRenderer.send(IpcChannel.WindowClose)
	}
}

contextBridge.exposeInMainWorld('api', api)
