# VRC Voice to Text OSC

Windows 桌面語音轉文字工具，以 Electron、Svelte 與 Python 提供麥克風辨識、喇叭辨識、雙向翻譯及 VRChat OSC 文字／表情輸出。

目前進入 P5 回歸驗收，尚未完成正式切換。Electron 是目前開發入口；舊 Textual 入口與依賴保留作回退。詳見 [P5 驗收紀錄](docs/p5-acceptance.md)。

## Windows 開發

使用 Python **3.12**、Poetry、Node.js 24 與 `package.json` 指定的 pnpm 版本。音訊使用 Windows PyAudioWPatch；目前不承諾 macOS／Linux 可用。

```powershell
poetry env use 3.12
poetry install
pnpm install --frozen-lockfile
pnpm dev
```

Electron 會啟動 Python 後端。解譯器依序查找 `VRC_BACKEND_PYTHON`、`VIRTUAL_ENV`、專案 `.venv`、Poetry 環境，最後使用 PATH 的 `python`。缺少 `pythonosc` 時，先以 `poetry env info --path` 檢查解譯器，避免往全域 Python 安裝套件。

開發環境 Torch 來源為 CUDA 12.6；CPU 發行包使用獨立 Python 3.12 建置環境。音訊降混與重採樣改用 pydub，直接處理 PCM，不需要 FFmpeg。pydub 本身仍依賴 audioop，因此 Python 3.13 以上會安裝 `audioop-lts`（[上游相容性議題](https://github.com/jiaaro/pydub/issues/867)、[audioop-lts](https://pypi.org/project/audioop-lts/)）；完整語音／模型套件目前仍以 Python 3.12 驗證。

## 操作與資料

1. 等待語音模型就緒；首次使用會下載權重。
2. 在設定頁選擇麥克風、喇叭、辨識模型與你的語言。
3. 點選主畫面的麥克風或喇叭功能卡開始辨識；兩者可同時啟用、分別停止。
4. 翻譯可選本機 NLLB、LibreTranslate 相容 API 或 DeepL。API 方式需設定端點／方案與金鑰。本機翻譯在第一次工作時載入模型。
5. 麥克風由你的語言翻至目標語言；喇叭反向翻譯。紀錄保留原文與譯文；翻譯失敗時用原文送出 OSC。
6. 手動輸入 Enter 傳送、Shift+Enter 換行；手動文字不經情緒分析。

OSC 預設開啟，送往 `127.0.0.1:9000`，聊天位址 `/chatbox/input`。表情參數預設 `/avatar/parameters/v2t_sync_emo`，需 avatar 有對應設定。UDP 送出不代表 VRChat 已收到；可在設定頁停用 OSC 單獨檢查辨識。

設定位於 Electron `userData` 下的 `settings.json`，Windows 通常是 `%APPDATA%\vrc2t`，啟動參數可覆寫路徑。API 金鑰目前保存在此 JSON，分享診斷資料前請移除金鑰。預設不存錄音；逐字稿只保留最近 1,000 筆於記憶體，關閉後不保留。

Whisper 與 Hugging Face 模型沿用各套件的使用者快取，沒有內嵌權重或統一移到 `userData`。離線使用需先下載完整模型；API 翻譯仍需連線。模型失敗可檢查網路、磁碟與記憶體，再關閉並重開程式。後端崩潰時可按錯誤列的「重新啟動後端」；既有紀錄與設定保留，不重送文字，也不自動恢復錄音。自動錄音設定本身不會因此被關閉，下次開啟應用仍照原設定執行。

## 驗證與打包

```powershell
poetry run python -m unittest discover -s tests -t .
pnpm test:node
pnpm typecheck
pnpm exec eslint .
pnpm build
```

單元測試使用替身；目前後端程序整合測試會啟動實際後端，可能碰到本機模型快取。打包檢查另行執行：

```powershell
pnpm build:backend
pnpm test:packaged-backend
pnpm dist
pnpm test:packaged-renderer
```

後端位於 `build/python/vrc-v2t-backend`，Electron 未安裝版本位於 `release/win-unpacked`，NSIS 安裝器輸出至 `release`。`pnpm dist` 會重建前端與 CPU 後端，首次需要下載依賴。安裝包設計上不需額外安裝 Python 或 Node，乾淨 Windows 驗收仍待完成。

`scripts/smoke-capture-stop.py` 可量測真實喇叭擷取啟停，略過辨識模型，不等同完整語音驗收。固定音檔規格見 [tests/fixtures/README.md](tests/fixtures/README.md)。

## GitHub Actions 乾淨安裝測試

`.github/workflows/windows.yml` 在 PR、推送 main／master 或手動執行時觸發：

- Python 3.12／3.13 分別測試 pydub 串流轉換。
- Windows CPU 建置執行 Python／Node 回歸、型別檢查、ESLint、PyInstaller 自測與 NSIS 打包。
- 另一台 Windows runner 下載安裝器，隱藏 PATH 上的 Python／Node／Poetry，以獨立目錄靜默安裝。實際執行內嵌 CPU runtime、tiny 首次下載與靜音推論，再用程式限定的防火牆規則封鎖後端網路，確認快取模型可離線推論。最後開啟 Electron 主視窗並檢查正常退出後沒有後端殘留。
- artifacts 保存安裝器、SHA256 與成功／失敗日誌七天；workflow 不會發布 GitHub Release。

Hosted runner 預裝開發工具，本測試以隔離 PATH 驗證安裝產物的獨立性，並非移除 runner 上所有軟體。靜音推論檢查執行能力；辨識品質、真實裝置、VRChat 與長時間錄音仍需另外驗收。新增 workflow 不等於雲端已執行通過，結果以 Actions run 為準。

## 回退

正式驗收前保留 `main.py`、`ui.py`、`voice.py`、`emo.py` 與 Textual 依賴。舊入口為 `poetry run python main.py`，其舊版行為不代表 Electron 的功能或裝置設定。回退不刪除設定、模型快取或錄音；完整切換條件見 [遷移實作書](遷移實作書.md)。
