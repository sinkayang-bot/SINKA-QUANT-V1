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
輸出 JSON，metadata 或 Job 設定未通過時結束碼為 1；未登入、SDK 不存在或權限不足
皆視為未就緒，不會輸出原始錯誤或秘密內容。

`metadata_ready: true` **只表示上述資源的 metadata 檢查通過**，不代表
Job 的容器設定已核對、執行身分具備 Secret Accessor、OKX API 權限正確或連線成功。
Secret 存取及 OKX 連線仍需後續獨立驗證。

## 測試失敗診斷

唯讀煙霧測試失敗時，JSON 的 `failed_stage` 會標示固定階段：
`configuration`、`balances`、`positions`、`instruments` 或 `pending_orders`。
任何查詢或資料驗證失敗，立即停止後續查詢；不顯示帳戶金額、持倉內容、
訂單識別碼、API 回應本文或憑證。缺少憑證也只輸出固定診斷並以非零結束碼結束。

## Job 設定核對（不執行）

部署腳本會從該次 Cloud Build 結果取得 SHA256 digest，並用不可變的 digest
部署映像，明確指定 `python okx_smoke.py`。取得不到合法 digest 時停止部署。
完成後會顯示映像參照；先核對該建置來源與內容，再執行：

```bash
python3 backend/okx_cloud_preflight.py --expected-image '審查過的完整映像@sha256:digest'
```

設定核對要求指定 Job、地區、服務帳戶、單一容器、完全相同的映像 digest、
固定入口、兩個模擬唯讀旗標、三個同名 Secret 的 latest 參照，以及單任務、
零重試、120 秒、1 CPU / 512Mi。額外環境變數、重複變數、明文秘密、掛載
或其他容器皆不通過。舊版 Job 需以新腳本重新部署才會符合明確入口的要求。

只提供標籤或未提供 `--expected-image`，`job_configuration_verified` 會保留 false。
此檢查只核對設定，不驗證映像來源、IAM 有效權限或 API 金鑰權限，亦不執行 Job。
不支援的 API 格式或跨專案 Secret 別名會視為未通過，需另行審查。

參考：[Cloud Run Job YAML](https://docs.cloud.google.com/run/docs/reference/yaml/v1)、
[Secret 參照](https://docs.cloud.google.com/run/docs/configuring/jobs/secrets)、
[Cloud Build 結果](https://docs.cloud.google.com/build/docs/api/reference/rest/v1/projects.builds#Results)。

## 核對已存在的單次測試結果（不執行 Job）

完成一次經授權的唯讀測試後，使用其確切執行名稱及事先核對的映像 digest：

```bash
python3 backend/okx_smoke_report.py \
  --execution 'sinka-okx-readonly-smoke-實際執行尾碼' \
  --expected-image '審查過的完整映像@sha256:digest'
```

工具只執行 `gcloud run jobs executions describe` 與該專案、地區、Job、
執行名稱篩選的 `gcloud logging read`。不建立或執行 Job、不讀 Secret 內容，
也不直接連線 OKX。需具備執行紀錄及日誌讀取權限；不會自行新增 IAM 權限。

必須同時符合下列條件，才輸出 `okx_connection_verified: true` 並回傳結束碼 0：

- 執行名稱、所屬 Job 及執行當時的不可變容器設定符合預期。
- 執行 Completed 為 True，成功一個任務，無失敗、取消、執行中或重試。
- 同一次執行的日誌有且只有一個完整且成功的 `demo_readonly` 回報，四個
  唯讀查詢皆為 `ok`，且 `trading_enabled` 明確為 false。

此核對查詢最近 30 天、最多 100 筆日誌。若達到上限、日誌延遲尚未出現、
回報重複或格式不明，一律視為尚未驗證，不會自動重跑測試。可稍後再次執行
核對工具，避免因日誌延遲而重複執行雲端 Job。

輸出僅含固定檢查名稱與布林結果，不重印原始日誌、帳戶金額、持倉、訂單、
秘密或 API 錯誤內容。一次通過只證明那次模擬唯讀查詢成功；不代表後續連線、
交易授權或其他對帳流程已完成。

參考：[Execution 狀態](https://docs.cloud.google.com/run/docs/reference/rest/v1/namespaces.executions)、
[Cloud Run 日誌欄位](https://docs.cloud.google.com/run/docs/logging#log_entry_fields_for_jobs)。

## 建置前檢查與固定部署目標

```bash
python3 backend/okx_cloud_preflight.py --before-build
```

`--before-build` 只要求必要 API、啟用的專用服務帳戶及三組啟用的 latest Secret
版本都能讀取 metadata；首次部署尚未建立 repository / Job 時仍可通過此階段。
輸出的 `before_build_ready` 與 `metadata_ready`、`job_configuration_verified`、
`okx_connection_verified` 分開，避免把「可以開始建置」誤認為「部署或連線成功」。
這個模式通過時結束碼為 0；未通過為 1。

部署腳本在啟用必要 API 後、建立 repository 及付費建置之前自動執行此檢查。
缺少權限、缺少 SDK、停用帳戶、停用或不存在的 latest Secret 都會停止流程。
仍需另行驗證執行身分的實際 Secret 存取權限，metadata 通過不代表已有 Accessor。

專案、地區、repository、Job、服務帳戶鎖定為本文件指定的值；環境變數若試圖
改用其他目標，腳本會在任何雲端指令前拒絕。變更部署腳本也會觸發 GitHub
後端測試，涵蓋「Secret 停用時不得建置」及「異常目標不得操作雲端」。
