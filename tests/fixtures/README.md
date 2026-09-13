# 固定音訊基準

`backend_protocol.py` 是獨立程序的協定測試入口：執行正式 `backend.__main__.main()`，只替換 Whisper／情緒的載入方法。它禁止 import 模型套件及 socket connect，避免一般 CI 測試意外下載模型。正式 `python -m backend` 入口與實際模型載入不受影響。

P0 盤點時專案沒有可提交的語音樣本。真模型回歸需要在相同硬體與模型版本下，
以 16 kHz、單聲道、PCM 16-bit WAV 錄製並人工確認以下四種固定樣本：

- `zh_short.wav`：清楚中文短句。
- `silence.wav`：至少 3 秒靜音。
- `background_noise.wav`：固定環境噪音。
- `zh_long.wav`：10–15 秒中文長句。

音檔可能包含個資，因此加入版本控制前必須取得錄音者同意。自動化測試預設不下載
模型；真模型結果另記錄於 `遷移紀錄.md`。
