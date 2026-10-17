# Cloud Shell 人工限時驗收

這是模擬交易的短時間人工驗收，不是 24 小時託管。不改動既有 paper VM、
唯讀服務、IAM、排程或 Secret；不建立計費 VM。Secret 存取仍依 Google Cloud
原有計費及權限執行。只使用新的三個 `OKX_DEMO_TRADE_*` Secret。

## 先只讀檢查

在已登入 `sinka-quant` 的一般 Cloud Shell，儲存庫根目錄執行：

```sh
python3 -m venv "$HOME/sinka-demo-venv"
"$HOME/sinka-demo-venv/bin/python" -m pip install httpx==0.28.1
"$HOME/sinka-demo-venv/bin/python" backend/okx_demo_cloudshell.py --check
```

不要使用 ephemeral mode。程式先檢查家目錄位於獨立的本地 ext4/xfs/btrfs
掛載磁碟，且 SQLite WAL 實際鎖定可用；不能確認就停止，不讀 Secret。
獨立驗收資料夾是 `$HOME/sinka-demo-trial`，權限 0700；不可刪除或覆寫帳本。

程式在 Cloud Shell 內用 gcloud 取得三個新 Secret 的 enabled 版本，將版本號
固定後存取內容。內容只留在 Python 程序記憶體，不列印、不作為命令列參數、
不寫入檔案或 `.env`，也不送到對話。工具不會讀取舊的唯讀 Secrets。

`--check` 使用結構上只允許 GET 的 transport，核對模擬請求、read_only+trade
權限、支援的帳戶模式、BTC-USDT-SWAP isolated 1–3 倍及完整帳戶庫存。
不自動變更帳戶模式、槓桿或金鑰權限。首次只在完全平倉且無待處理單時，
將這組由使用者設定的新憑證所對應的 UID、scope、symbol 以 0600 權限綁定在
`account.json`。這是信任首次設定的伺服器憑證，不是獨立驗證帳戶擁有權。
之後每次都必須符合原綁定；已有帳本卻缺少綁定時拒絕自動認領。

成功輸出 `phase: demo_account_verified`、`trade_permission_verified: true`、
`persistent_storage_verified: true`、`orders_enabled: false`。不顯示 UID、資產或金鑰。

若輸出 blocked，reason 會區分各個階段，不能把連線錯誤直接判定為金鑰錯誤：

| reason | 意義 |
| --- | --- |
| `demo_net_position_mode_required` | 模擬帳戶需使用 net／買賣模式 |
| `demo_futures_or_multi_currency_account_required` | 需支援的合約帳戶等級 2 或 3 |
| `demo_trade_permission_missing` | 新金鑰尚無交易權限 |
| `demo_isolated_leverage_1_to_3_required` | BTC-USDT-SWAP 逐倉槓桿資料不符合 1–3 倍／net 條件 |
| `demo_usdt_balance_missing_or_invalid` | 模擬 USDT 資產、可用餘額或現金帳本欄位不完整 |
| `demo_instrument_specification_unsupported` | 合約規格或市價／停損數量上限不支援 |
| `demo_config_connection_failed_http_401` | 帳戶設定請求未通過 HTTP 驗證；檢查伺服器上新憑證是否為對應的一組，不公開其內容 |
| `demo_full_snapshot_inconsistent_or_expired` | 各別讀取已通過，但完整雙次快照變動、過期或驗證未通過 |

其他 `*_read_failed`／`*_inventory_invalid` 代表指定的只讀資料階段未通過。
若策略單群組讀取失敗，會額外做逐類型的 GET 診斷，輸出例如
`demo_pending_algos_chase_api_rejected_code_51000` 或
`demo_pending_algos_trigger_read_failed_http_429`。這些診斷只探測第一頁以確認
拒絕類型與錯誤碼，不提供完整庫存證明。拒絕、逾時或未知類型不會被當成空清單，
也不會跳過原本的完整讀取要求。
HTTP 狀態及數字 API code 可供診斷；回應原文、標頭及憑證不輸出。程式不會為了
通過檢查而自行變更任何帳戶設定，也不會重新建立帳本或綁定。

## 通過檢查後才進行限時試跑

使用已審查的程式版本，首次建立基線並允許策略自行等待新訊號：

```sh
"$HOME/sinka-demo-venv/bin/python" backend/okx_demo_cloudshell.py \
  --run --initialize-baseline --seconds 300
```

這行**允許模擬下單、撤單與減倉**。預先需要 `--check` 已成功綁定帳戶，並有
Node 22 或 24。不要求填寫金鑰或 UID，不會為了產生測試單而偽造策略訊號。
最多 300 秒後嘗試停止新倉與減倉查詢；結束清理最多再用 15 秒。Ctrl+C 會
要求同樣的停止流程；不要直接關閉分頁或中止 Cloud Shell VM。

已有基線時後續省略 `--initialize-baseline`。長時間缺口、未結算委託或緊急退出
會阻擋新倉，必須保留帳本並處理；不能刪除資料夾或重設基線來跳過阻擋。
結果只反映該次短時間驗收。`trial_finished` 不等於已成交或已平倉，需查看
內嵌 worker phase 與結束碼。沒有訊號不下單，未確認退出不宣稱成功。

Cloud Shell 家目錄的持久儲存及回收限制：
[Google 官方說明](https://docs.cloud.google.com/shell/docs/how-cloud-shell-works)。
