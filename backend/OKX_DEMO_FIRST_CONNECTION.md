# SINKA QUANT — OKX 模擬帳戶首次唯讀連線

狀態：**待使用者完成設定；尚未連線、尚未部署。**

## 第一步：建立 OKX 模擬帳戶專用 API Key

依 OKX 官方文件操作：

1. 登入 OKX。
2. **交易 → 模擬交易 → 個人中心 → 模擬交易 API → 建立模擬交易 API Key**。
3. 權限僅選 **Read／讀取**，不要選 Trade 或 Withdraw。
4. API Key、Secret Key、Passphrase 只在你控制的安全儲存空間保存，**不得貼到聊天、GitHub、GitHub Actions 日誌、GitHub Pages 前端或任何 README**。
5. 若 OKX 要求 IP 白名單，須等伺服器出口 IP 設定確定後才能填寫；不要為了方便直接開放交易權限。

官方參考：https://www.okx.com/docs-v5/

## 第二步：檢查程式自動測試

開啟 https://github.com/sinkayang-bot/SINKA-QUANT-V1/actions

找到 `Backend tests` 最近一次執行，應包含：
- `test`（Python 單元測試）
- `firestore-emulator`（Firestore 模擬器測試）

若有失敗，先修正測試，**不要直接部署**。目前程式無法從 GitHub status API 確認整套 Actions 結果。

## 第三步：安全注入憑證並跑唯讀檢查（待雲端環境建立後）

伺服器端透過 Secret Manager 注入 `OKX_API_KEY`、`OKX_API_SECRET`、`OKX_API_PASSPHRASE`，設定：

- `SINKA_OKX_DEMO=true`
- `SINKA_OKX_READONLY_SMOKE=true`

執行 `python backend/okx_smoke.py`。輸出不含密鑰、餘額、持倉或訂單 ID；回報 API 健康狀態與待成交筆數。

**重要：** 目前雲端部署與 Secret Manager 尚未設定；此時無須自行執行命令。模擬交易下單、止損及實盤下單全部未啟用。實盤下單必須在模擬驗證完成後另行明確核准。
