# SINKA QUANT — OKX Demo 唯讀 Cloud Run Job

只部署「執行一次即結束」的唯讀煙霧測試。無公開 HTTP 網站、無 OKX 下單程式、無真實下單授權、無自動排程。建立 Job **不會執行**，第一次測試須人工確認。

已知設定：專案 `sinka-quant`；地區 `asia-east1`；服務帳戶 `sinka-okx-readonly@sinka-quant.iam.gserviceaccount.com`；Secret Manager 秘密名稱 `OKX_API_KEY`、`OKX_API_SECRET`、`OKX_API_PASSPHRASE`。只讀 OKX 模擬 API 權限不可啟用 Trade / Withdraw。

## Cloud Shell 單次部署（先檢視內容再執行）

1. 打開 [Google Cloud Shell](https://console.cloud.google.com/?cloudshell=true&project=sinka-quant)，確認選取專案。
2. 取得自己的 GitHub 程式碼：
   ```bash
   git clone --depth=1 https://github.com/sinkayang-bot/SINKA-QUANT-V1.git
   cd SINKA-QUANT-V1
   cat firebase/deploy-okx-smoke-job.sh
   bash firebase/deploy-okx-smoke-job.sh
   ```
3. 輸入 `DEPLOY` 後，腳本才開始啟用必要 API、建立 Artifact Registry（若無）、建置 image 以及部署 Cloud Run Job。**腳本不會執行 Job**。
4. 如出現 Cloud Build 預設服務帳戶缺少 Artifact Registry 寫入權限，停下來確認授權範圍；不要使用 Owner / Editor 解決。
5. Job 建立成功後應先核對服務帳戶與三組 Secret 的資源層級 Accessor 授權，再另行手動執行首次測試。

此步驟可能產生 Cloud Build / Artifact Registry / Cloud Run 等費用。建議先設定 [Cloud Billing 預算及通知](https://console.cloud.google.com/billing/budgets?project=sinka-quant)。預算通知不是強制停用的支出上限。

## 安全界線

- image 中無任何憑證；所有秘密以 Cloud Run 的 Secret Manager 參考注入，未複製到 GitHub / Docker image / logs。
- 使用專用服務帳戶，僅有先前授予的 Firestore Datastore User 及每一組 Secret Accessor（煙霧測試本身不使用 Firestore）。
- `Dockerfile.okx-smoke` 的程式只允許經過白名單的 GET，且必須帶 OKX `x-simulated-trading: 1`。
- 預設不執行，不排程，也不部署長駐 worker；真正模擬下單與真實資金下單均仍鎖定。
- 一次成功的唯讀 smoke test **不代表**成交歷史或風控對帳完成，更不代表獲利能力或下單安全性。

## 唯讀部署前檢查

在 repository 根目錄執行：

```bash
python3 backend/okx_cloud_preflight.py
```

此工具固定使用 `sinka-quant` / `asia-east1`，只讀取必要 API、服務帳戶、
三組 Secret 的 latest 版本狀態、Docker Standard repository 與 Job 是否存在。
不讀取 Secret 內容、不修改 IAM、不建置、不部署，也不執行 Job。
輸出 JSON，`metadata_ready: false` 時結束碼為 1；未登入、SDK 不存在或權限不足
皆視為未就緒，不會輸出原始錯誤或秘密內容。

`metadata_ready: true` **只表示上述資源的 metadata 檢查通過**，不代表
Job 的容器設定已核對、執行身分具備 Secret Accessor、OKX API 權限正確或連線成功。
這些項目分別保留 `false`，需後續獨立驗證。

## 測試失敗診斷

唯讀煙霧測試失敗時，JSON 的 `failed_stage` 會標示固定階段：
`configuration`、`balances`、`positions`、`instruments` 或 `pending_orders`。
任何查詢或資料驗證失敗，立即停止後續查詢；不顯示帳戶金額、持倉內容、
訂單識別碼、API 回應本文或憑證。缺少憑證也只輸出固定診斷並以非零結束碼結束。
