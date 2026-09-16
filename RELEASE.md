# Windows release

1. 更新 `package.json` 的版本，執行 `pnpm version:sync`，將修改提交。
2. 建立並推送對應 tag，例如 `v0.1.0`。tag 必須與 `package.json` 完全一致。
3. `Windows release` 會呼叫既有 Windows workflow，執行音訊測試、Node/Python 回歸、型別檢查、UI 回歸、CPU 後端打包與 clean-install。
4. 全部成功後建立 GitHub Release **草稿**，附上 Windows x64 NSIS 安裝檔、`SHA256SUMS.txt` 和此次驗證的 run URL。檢查實機驗收結果後再手動發布。

也可在 Actions 手動執行 `Windows release`，但必須選擇既有版本 tag，不能選 branch。重跑只更新草稿附件；已發布的 release 不會被覆蓋。

安裝檔目前未簽章。語音模型在首次啟動時下載，主介面會等模型成功載入才顯示；失敗時可重試或退出。啟動下載最多等待 15 分鐘。

裝置選擇現在使用名稱、音訊 API 與來源的識別資訊，避免拔插後重用數字編號而選錯裝置。舊版 `index:` 選擇需停止錄音後重新整理並重選；系統預設選項不受影響。同名且無法唯一識別的裝置會報錯，不會任選一台。

發布前仍需完成實機退出／復原測試、固定語音 fixture 比較與 VRChat 行為驗證；CI 並未代替這些實機測試。
