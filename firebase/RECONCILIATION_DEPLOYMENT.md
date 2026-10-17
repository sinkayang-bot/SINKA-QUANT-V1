# 唯讀對帳：雲端驗收交接

目前可交付的是預設關閉、只允許 GET 的一次性對帳容器；不是自動交易服務。
CI 的容器及合成資料測試不代表 GCP 已部署，也不代表實際 OKX 帳戶已驗收。

## 先盤點，避免改動既有環境

在此儲存庫根目錄，使用已登入正確 Google 帳戶的 Cloud Shell：

```sh
python3 backend/okx_reconciliation_preflight.py
python3 backend/okx_reconciliation_preflight.py --inspect
```

第一行只輸出計畫，不連雲端。第二行只讀取固定專案 `sinka-quant` 的 API、
服務帳戶、Secret 版本中繼資料、Artifact Registry、既有 smoke Job，以及
`asia-east1-b/sinka-paper-trial` 的 VM 中繼資料。不存取 Secret 值、啟動 VM、
執行 Job、變更 IAM 或呼叫 OKX。拒絕存取和資源不存在都顯示 unknown/denied，
不能推定資源已刪除。完整輸出不包含 VM startup script 或原始錯誤。

盤點成功的 exit 0 只表示可取得既有資源中繼資料，不能表示部署就緒。
`reconciliation_deployment_ready` 固定 false，直到下列項目完成獨立驗收。

## 新對帳入口尚缺的驗收

- 專用持久帳本：SQLite/WAL 必須在支援鎖定的持久本地檔案系統；驗證重啟後
  帳本、租約 fencing、歸檔序列與風控狀態保留。不要以 Cloud Run 的暫存檔案系統
  存正式帳本，也不要以 Cloud Storage FUSE 當作 SQLite 的替代磁碟。
- 既有 paper VM 保持原用途。其保留開機磁碟不等於新對帳程式已有可用帳本；
  不覆寫 paper DB，不逕自加入 service account、Secret 權限或持續執行排程。
- 預期 OKX UID 與 account scope 需在可信任的伺服器設定核對。Firebase owner UID
  是另一種身分，不能代替 OKX UID。使用既有 Secret 參照，不貼上 API 金鑰。
- 以不可變映像 digest 部署；預設維持 RUN_DEMO_RECONCILIATION=0。首次啟用只做
  唯讀基線驗收，保留 kill switch；需要明確初始化選項，不重設既有帳本。
- 完成真實環境的唯讀身分、資料完整性與重啟恢復檢查，再記錄確切映像、時間、
  狀態及失敗原因。金鑰為 read_only 時可做連線診斷，不升級金鑰權限以通過交易資格。

Google 官方說明：
[容器檔案系統生命週期](https://docs.cloud.google.com/run/docs/container-contract#filesystem)、
[Cloud Storage volume 的檔案鎖定限制](https://docs.cloud.google.com/run/docs/configuring/jobs/cloud-storage-volume-mounts#limitations)。

現階段沒有新部署命令或自動啟用步驟；尚缺持久帳本與帳戶綁定的驗收證據。
