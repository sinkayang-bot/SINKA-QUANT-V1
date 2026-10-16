# OKX 唯讀監控：部署前檢查

目前只有可選擇啟動的獨立 worker 程式 `okx_readonly_worker.py`，**尚未部署，也不會自動執行**。

## 安全界線

- 預設拒絕啟動：必須設定 `SINKA_OKX_READONLY_MONITOR=true`。
- 僅允許 OKX 模擬模式：必須設定 `SINKA_OKX_DEMO=true`。
- API 憑證只在伺服器端取得，**不可**貼到 GitHub、GitHub Pages、聊天訊息或前端程式。
- OKX API Key 初期只授予 **Read（讀取）** 權限；不得授予 Trade 或 Withdraw。
- Firestore 的狀態紀錄是診斷資訊，**不能作為下單解鎖條件**。
- 已增加 SWAP 未完成委託分頁查詢及逐筆比對。只有空清單或與本地追蹤完全一致時，`orders_matched` 才可能為 true；這只是同步診斷，**不表示已通過模擬交易驗證，更不會解鎖真實下單**。目前尚未完成 OKX 訂單的獨立持久化帳本。
- 目前 worker 僅假設本機持倉為空。帳戶若持有任何部位，必須先完成正式持倉帳本才能繼續。
- worker 使用獨立 Firestore 租約，避免一般情況下多個實例同時寫入；**不等同**獨立監控告警、可靠排程或自動交易服務。

## 部署前仍需完成

1. 驗證 GitHub Actions 所有單元測試及 Firestore emulator 測試通過。
2. 驗證交易所未完成委託分頁掃描，包括大量訂單、分頁期間變動、成交回報、持倉快照及重啟恢復流程；實作獨立持久化訂單帳本。
3. 建立 GCP 專案、帳務、IAM 服務帳戶及 Secret Manager，僅在伺服器環境注入 OKX 模擬帳戶唯讀憑證。
4. 確定 Cloud Run 的執行模型與排程，另設獨立健康檢查／告警。單純設定最小實例不能保證無中斷執行。
5. 在模擬交易環境完成斷線、超時、回朔、重複指令、部分成交等測試，再由使用者決定是否展開後續交易權限開發。

## 程式入口（僅供開發環境）

`python backend/okx_readonly_worker.py`

此程式需要 Firestore 和正確環境變數及伺服器端憑證；不能直接在 GitHub Pages 執行。**目前不要求使用者執行這些命令。**
