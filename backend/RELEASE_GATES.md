# SINKA QUANT：可用版本驗收門檻（2026-10-16）

本文件的百分比僅是工程規劃，不代表測試通過或真實交易準備就緒。

## A. 網頁觀察／本機模擬（既有 GitHub Pages）

- [x] 行情／SNR 訊號／回測／本機紙上交易模組存在。
- [ ] 重新確認目前 GitHub Pages 的功能、行動裝置 UI、錯誤提示及安全限制。
- [ ] 整理策略回測結果；回測虧損策略不得被默認為可獲利策略。

## B. OKX 模擬帳戶唯讀監控（下一個可驗收里程碑）

- [x] OKX GET-only adapter、交易所持倉與未成交委託掃描。
- [x] Firestore 訂單、持倉、成交水位和租約原型。
- [x] opt-in 唯讀 API smoke check：`backend/okx_smoke.py`。
- [ ] 在 GitHub Actions 驗證全套 Python 測試、Firestore emulator 測試通過。
- [ ] 建立 Google Cloud / Firebase 專案、IAM、Secret Manager、受保護的服務環境。
- [ ] 由使用者在 OKX 模擬帳戶建立**只讀** API Key；僅注入伺服器端 Secret Manager。
- [ ] 實際執行 smoke check，確認真實 OKX demo API 回應與規格。
- [ ] 人工核對初始持倉、成交歷史的完整性及水位；未審核不得啟用交易准入。
- [ ] 部署 worker、排程與獨立健康檢查／告警，至少驗證重啟、斷線和雙程序競爭。

## C. OKX 模擬帳戶自動下單（目前不可用）

- [ ] 全功能 demo-only 下單執行器，包含 idempotency、重送防護、超時未知狀態處理。
- [ ] OKX 逐倉模式、合約規格、張數／滑點／費率、槓桿及方向模式實際核對。
- [ ] 交易所端停損指令、成交回報、部分成交、撤單與補單的端對端測試。
- [ ] 權限隔離、每日最大虧損、異常停機、手動停用、持倉異常告警。
- [ ] 經過足夠樣本的模擬交易穩定性測試與策略績效評估。

## D. 真實資金自動交易（永久預設關閉）

- [ ] 獨立的風險、安全、策略與部署審核。
- [ ] 使用者在模擬驗證完成後另外明確確認是否啟用。
- [ ] 完成上述階段之前，禁止任何真實資金訂單。

## 連線測試入口

目前**不要求使用者操作**。準備好 demo-only 伺服器與唯讀 API 憑證之後，才以安全環境變數設定：
- `SINKA_OKX_DEMO=true`
- `SINKA_OKX_READONLY_SMOKE=true`
- `python backend/okx_smoke.py`

此命令只回報去識別化的 API 健康資訊。不可把 OKX API Key、Secret、Passphrase 提供於聊天室、前端或 GitHub。

**注意：** 此 smoke check、Firestore 同步紀錄和目前的權限檢查都不會授權真實下單；現有 Worker 尚未正式部署。
