# OKX 模擬策略交易：授權與啟用驗收

2026-10-18 04:43（台灣時間），使用者明確授權模擬帳戶自動下單、撤單及
停損停利。真實帳戶仍禁止。這項授權允許開發與後續模擬驗收，不代表既有
唯讀憑證已有交易權限，也不代表雲端已部署或交易已驗收。

## 已實作的執行路徑

- `okx_demo_trading_worker.py` 是獨立入口，預設不讀憑證、不連網、不開帳本。
- 兩個交易旗標 `RUN_DEMO_TRADING=1`、`DEMO_TRADING_AUTHORIZED=1` 才能建立
  demo transport。真正新倉還需要 `DEMO_ARM_ENTRIES=1`、有效租約、可信任的
  完整帳戶及帳本證明、kill switch 解除、策略新訊號與日風控通過。
- `okx_demo_strategy_source.py` 只執行儲存庫內固定 JS 程式；子程序不繼承
  OKX 憑證、代理設定或 NODE_OPTIONS。沿用 SNR-5M-1 引擎與公開市場來源，
  不重播啟動前訊號，不為了測試而強制產生交易。
- 帳戶資產、可用餘額、合約規格與槓桿取自已核對 UID 的 reader，不能由
  策略訊號覆寫。維持單一持久帳本、單一活動委託、最多 3 倍、單筆模型
  損失 0.5%、保證金 10%、每日最多 6 次、連虧 3 次／日回撤 2% 停止、
  結算後冷卻 15 分鐘等限制。價格跳空與市場成交不保證損失被模型上限封頂。
- 每次 POST 固定 OKX origin 與 `x-simulated-trading: 1`；沒有真實交易模式。
  市價入場必須附帶固定 2R TP/SL。送單前先落盤，ACK 不當作成交證據。
- 送單結果不明：查原 client ID，從不重送。部分成交：一次取消剩餘量，
  再查實際成交；撤單時剛好全成交也以查詢後數量處理。
- 保護單不足且能確認終態主單、同向持倉與數量一致時，只發 `reduceOnly=true`
  的反向市價減倉。退出請求同樣先落盤、只送一次，重啟只查詢。身分／持倉／
  algo／請求結果不明時保留帳本與 kill switch，要求後續對帳，不猜測成功。
- 原生保護單持續檢查與 entry fill audit 每 2–10 秒執行；正常原生退出交給
  既有唯讀 close/bill reconciliation 原子結算及歸檔。
- 最長持倉 60 分鐘。首次 worker 預設 300 秒，允許 1–900 秒；SIGINT／SIGTERM
  或時間到時禁止新倉，最多再用 15 秒嘗試減倉及查詢。硬斷電、斷網、重複訊號
  與外部停止不保證一定收到最後一次請求；必須保留磁碟與既有原生保護單。

## 目前仍不能標示「已可交易」

實際雲端的舊 smoke Job `sinka-okx-readonly-smoke-rwkn7` 已核對成功；其報告
證明的是該次 demo 唯讀查詢及唯讀權限，不是新版執行器已驗收。

1. **獨立的模擬交易憑證**：現有三個 `OKX_API_*` Secrets 供唯讀服務使用，
   請勿修改其內容或權限。新的模擬 API 應只有讀取＋交易，不授予提款權限，
   並使用獨立的 `OKX_DEMO_TRADE_API_KEY`、`OKX_DEMO_TRADE_API_SECRET`、
   `OKX_DEMO_TRADE_API_PASSPHRASE` Secret。值只在雲端安全設定，不放到對話、
   GitHub、命令列歷史或映像中。新 worker 內部的 `OKX_API_*` 環境變數應參照
   新的三個 Secrets；不要誤用舊唯讀 Secrets。
2. **可信任的帳戶設定**：伺服器設定 `OKX_EXPECTED_UID`（不是 Firebase UID）、
   `DEMO_ACCOUNT_SCOPE`、`DEMO_SYMBOL`。reader 嚴格驗證 net mode、支援的帳戶
   等級、isolated 1–3 倍與 read_only+trade 權限；不自動修改帳戶模式或槓桿。
3. **持久帳本與部署**：設定絕對路徑 `DEMO_LEDGER_PATH`，在支援 SQLite/WAL
   鎖定與持久化的獨立本地磁碟上。不能使用 Cloud Run 容器暫存磁碟或將
   Cloud Storage FUSE 當 SQLite 磁碟。既有無 service account 的 paper VM
   與 paper DB 維持原用途；本次程式提交沒有新增 VM、IAM 或 Secret 綁定。
4. **首次驗收**：先保持 `DEMO_ARM_ENTRIES=0`，只用
   `DEMO_INITIALIZE_BASELINE=1` 明確建立一次完整平倉基線；已有基線不能重設。
   配置完整並通過診斷、保留磁碟檢查後，再進行限時模擬試跑。沒有策略訊號時
   不下單，不能把這種空跑當成下單驗收。
5. **實際成交與重啟**：核對下單、成交、原生 TP/SL、退出、費用與 funding
   帳單，以及同一磁碟重啟後不重送、不遺失日風控。合成測試不能代替這一步。

## 已知停止條件

緊急市價退出、取消後的部分成交、孤兒 algo、未知回報、歷史缺口或帳單未齊
均不自動歸檔或重新啟用。`demo_emergency_exit` 是跨週期保留的一次性紀錄；
出現後禁止再開新倉，待核對退出成交、剩餘保護單與帳單後處理。這版未提供
自動清理孤兒保護單、緊急退出結算後自動解鎖、24 小時部署或故障自動重啟。
`automatic_runtime_ready` 保持 false，直到實際部署驗收完成。

獨立映像：從儲存庫根目錄建置（不要傳入憑證）：

```sh
docker build -f backend/Dockerfile.okx-demo-trading -t sinka-demo-trading:review .
docker run --rm --network none --read-only sinka-demo-trading:review
```

第二行應輸出 `phase: disabled`、`live_trading_enabled: false`。這不會執行交易。

官方 API 語義：[模擬交易與附帶 TP/SL](https://www.okx.com/docs-v5/en/)。
