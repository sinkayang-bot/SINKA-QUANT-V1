# OKX 模擬執行器搬遷

此搬遷保留現有網頁、控制 API、Firestore 指令信箱、原始 SQLite 帳本與帳戶綁定。
新增台灣 `asia-east1-b` 的 `e2-small` VM、10 GiB 開機磁碟、10 GiB 資料磁碟及固定出口 IPv4。
兩顆磁碟均不隨 VM 刪除。systemd 管理控制器，瀏覽器或 Cloud Shell 斷線不會送出停止訊號。
每輪策略仍維持 15 分鐘；搬遷不等於已完成自然停利停損或 24 小時策略驗收。

## 費用與權限

先執行 `bash firebase/migrate-demo-vm.sh --plan`，只產生本機計畫、不讀金鑰、不建立資源。
新增付費資源必須經使用者同意後，才可執行 `--approved-cost`。
估計小型常駐主機、20 GiB 標準磁碟與使用中 IPv4 合計約每月 US$20–25；
這是預算估算而非報價或費用上限，不含既有 Cloud Run、Firestore、Secret Manager、額外流量與稅。
停止主機後，保留磁碟與固定 IP 仍計費。

VM 使用專用服務帳戶，只授予專案 Firestore 資料使用權及三個既有模擬 Secret 的版本資訊與內容讀取權，
不建立或上傳服務帳戶金鑰，不設定真實交易 Secret。SSH 僅允許 IAP 網段，沒有公網 SSH 開放。
操作者需要現有專案建立 VM／網路、管理服務帳戶與 IAM、使用 IAP／OS Login sudo 的權限。
缺少權限、API、區域配額或名稱衝突時停止；不修改或接管既有同名資源。

## 實際切換

1. 停止新進場，完成原執行器的帳戶核對與必要結算。檢查 OKX 模擬帳戶狀態。
2. 結束 Cloud Shell 控制器及子程序，確認 `pgrep` 無執行器。搬遷工具不會自動終止交易程序。
3. 在載入本版本的 Cloud Shell 執行 `bash firebase/migrate-demo-vm.sh --approved-cost`。
4. 工具取得原控制器檔案鎖、確認沒有舊交易程序，寫入來源停用標記，將原帳本與綁定設為唯讀。
5. 使用 SQLite backup API 產生包含已提交 WAL 內容的一致備份；校驗完整性及帳戶 scope。
   不重新初始化基線、不清除交易、不更改餘額、風控或歷史。
6. IAP 加密傳輸備份及程式。帳本不放入 VM metadata、公開儲存庫或映像。
7. 新資料磁碟掛載到 `/home/sinka`，容器無網路匯入原備份，重新核對 manifest SHA256、資料庫完整性及 scope。
8. 先執行 GET-only `--check`，帳戶或 IP 白名單不符合時停止，不啟動控制器。
9. systemd 啟動指令控制器，等待網頁擁有者命令。重新整理頁面核對在線心跳，先恢復既有交易。

必須保留來源的 `migrated-to-vm.json` 與唯讀原帳本，禁止再從舊副本執行交易。
切換後若失敗，來源保持停用，備份留在 `$HOME/sinka-migration-*`；不要自行刪除標記或重設帳本。
需人工確認新 VM／所有子程序已停止，才可討論反向遷移；沒有自動回滾，以免產生兩個寫入者。
安裝腳本對已有狀態的磁碟、服務或目錄一律拒絕覆寫；部分建立失敗需先檢查現有資源，不能盲目重跑。

## 驗收與維護

- 雲端建置測試確認映像包含 Node 22、Cloud CLI 及後端；預設待命、不呼叫交易端點。
- 實機需確認 `--check` 通過、控制器心跳、頁面恢復核對，以及來源停用。
- 關閉 Cloud Shell 後，觀察心跳仍更新；再做模擬自然停利／停損、歸檔及重啟恢復驗收。
- 主機重啟後載入同一資料磁碟；控制器重連會保留中斷檢查，不自動重送進場命令。
- 控制器每四小時優雅退出並由 systemd 重新連接；正在處理的交易仍沿用原有停止與恢復規則。
- 查看狀態：`gcloud compute ssh sinka-demo-runner --project=sinka-quant --zone=asia-east1-b --tunnel-through-iap --command='sudo systemctl status sinka-demo-runner --no-pager'`。
- 停止控制器可能觸發既有模擬倉位減倉，應先從網頁完成停止與核對。

此環境無法直接連入使用者 GCP／Cloud Shell；程式測試與發布不代表實機搬遷已完成。
