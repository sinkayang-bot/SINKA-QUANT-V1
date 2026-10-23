# Cloud Shell 延長模擬驗收

新版網頁啟動指令固定使用 `--run --resume-idle --strategy-profile expanded
--acceptance-window --scan-markets 20 --seconds 7200`。控制器仍最長連線四小時。
延長的是整輪觀察時限，不是保證交易次數或自然出場時間。

- 沒有明確的 `--acceptance-window`／`DEMO_ACCEPTANCE_WINDOW=1`，原有 900 秒上限保留。
- 最長 7200 秒，禁止搭配強制進場測試、僅恢復模式或初始化基線。
- CLI、worker、supervisor 分別檢查時限；測試進場仍最多 90 秒。
- 訊號規則、交易規模、持倉最多 60 分鐘、帳戶綁定、原生保護單核對及帳單結算規則不變。
- 正常價格觸發、單筆持倉逾時、整輪時間到期、停止指令與讀取失敗仍區分處理。
- `runtime_limit` 或 `tracked_strategy_exit_closed_and_archived` 不代表自然停利停損驗收通過。
- 只有交易所子單身分與成交、完整帳單及現金核對後的自動歸檔，才是自然出場流程證據。
- 程式持續監控並沿用原有結算後接續掃描規則；沒有新訊號就不進場。
- 「持倉中斷後恢復」仍需另外實際驗證，不能因延長時限便標為完成。

## 載入方式

已停止且沒有舊執行器時，下載新版後執行 `bash firebase/run-demo-cloudshell.sh --connect`。
它使用 tmux 工作階段，只連接指令控制器，不自行送出啟動交易命令。
若已存在舊程序，腳本不會終止或重複啟動它；需先從網頁完成停止及核對，再更新程序。
網頁重新整理後按「啟動模擬策略驗收（最長 2 小時）」。
進度中應顯示「延長驗收時限：120 分鐘」；僅更新網頁不會更新舊的 Cloud Shell 執行器。

此次不建立 VM、排程、磁碟或其他新資源，付費主機搬遷仍未執行。
既有 Cloud Run／Firestore 等服務仍依原用量計費。
tmux 只隔離終端連線，不能防止 Cloud Shell VM 回收、權限失效或程式錯誤。
