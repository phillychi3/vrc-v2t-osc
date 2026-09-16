# VRC Voice to Text OSC

Windows 桌面版使用 Electron／Svelte 介面與 `backend/` Python 後端。

開發環境安裝依賴後啟動：

```powershell
poetry install
pnpm install --frozen-lockfile
pnpm dev
```

建立 Windows 安裝檔：`pnpm dist`。發行流程見 [RELEASE.md](RELEASE.md)。
