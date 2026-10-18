# Cloud Shell 人工限時驗收

## 已人工平倉的測試單結案

`--audit-trade` 也可核對程式自動減倉：減倉委託必須符合原有 durable reservation、
原始 client ID、已知 exchange order ID（若有）、reduce-only 參數與完整帳務，
才輸出 `automatic_exit_verified: true`、`closure_source: tracked_emergency_exit`。
`test_roundtrip_verified` 仍為 false；自動減倉不等於原生止盈止損驗證成功。
唯讀核對會另查兩次附掛策略單詳情，只輸出狀態、類型、缺失欄位與符合與否，
不顯示委託 ID 或原始回應。詳情查不到不會推斷曾有保護；歷史詳情即使吻合，
也不代表退出前已驗證為 live。這些查詢不下新單，不改帳本。

若 `--close-reviewed-test` 辨識為程式既有減倉，其正式封存來源會記為
`tracked_emergency_exit`，輸出 `tracked_test_exit_closed_and_archived`，並保存
完整減倉 reservation 證據。停單、風控及原生保護尚未通過的標記都保留。

`--audit-trade` 只讀核對進場、人工平倉、完整雙次帳單及原始現金差額，不改帳本。
`--close-reviewed-test` 會重新取得雙次完整證據；只有固定 BTC 模擬測試單、
完整同向進場與反向 reduce-only 平倉、成交與帳單逐筆一致、原始合約規格及
損益算式一致、現金吻合且逐倉現金全數退回才允許本地結案。只使用 GET，
不建立交易 transport、不啟動策略、不修改雲端資源。

```sh
"$HOME/sinka-demo-venv/bin/python" backend/okx_demo_cloudshell.py --close-reviewed-test
```

結案在原有 SQLite 租約內以單一交易完成：原始帳本、現金基線、成交、完整帳單、
原始進出場查詢證據和手動結案來源封存在既有雜湊鏈；風控進場次數保留，
淨損益列入風控與冷卻期。中途失敗回滾，不留下半套結案，不刪帳本重來。
停單開關仍開啟，`automatic_exit_verified` 和 `new_entry_allowed` 仍為 false。
成功的 `manual_test_closed_and_archived` 只表示這筆手動平倉結案。

後續明確執行 `--run --test-entry --seconds 90`，才允許下一筆最小規模模擬測試。
測試識別碼取自已驗證的封存序號，每個週期固定一個，不接受隨機值或呼叫者
指定序號繞過防重送；未結案先前委託仍只查詢恢復。日內次數、虧損、冷卻限制
不會因測試而清除。封存前保留的緊急減倉 tombstone 也不刪除、不重送。

這是模擬交易的短時間人工驗收，不是 24 小時託管。不改動既有 paper VM、
唯讀服務、IAM、排程或 Secret；不建立計費 VM。Secret 存取仍依 Google Cloud
原有計費及權限執行。只使用新的三個 `OKX_DEMO_TRADE_*` Secret。

## 先只讀檢查

在已登入 `sinka-quant` 的一般 Cloud Shell，使用 Python 3.11 以上，儲存庫根目錄執行：

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
若各類第一頁 API 均成功但整體庫存驗證失敗，`algo_diagnostics` 一次提供 Python
版本、固定錯誤類別、各類型的第一頁／完整掃描數量、狀態分類、缺失欄位及跨類型
重複數。不輸出任何 UID、委託 ID、產品 ID、數量、價格或原始回應字串。
官方合法的 pending 狀態 `pause`、`partially_failed` 與 `partially_effective`
會被保留為未解決庫存，不能被當作有效保護或平倉基線；不會因讀取成功而允許新倉。
Python 的 TaskGroup／timeout 功能若不可用，會在讀 Secret 前停止，顯示
`python_3_11_or_newer_required`。
完整帳戶快照共用一次性 HTTP 連線池，同時最多兩個 GET；策略單類型依序掃描，
不再同時為每類建立新連線。每個請求仍獨立簽章，不重用 cookie、不重試、不跟隨
重新導向、不使用環境代理。完整雙次讀取的 5 秒時效、所有分頁及庫存驗證均保留。
連線池內禁止 POST，唯讀 transport 的底層請求入口亦維持禁止寫入；取消、逾時
或讀取失敗會關閉連線池並保留阻擋狀態。
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

### Runtime progress and restart limitation

The Cloud Shell helper enables fixed-label JSON progress. Status changes print
immediately; unchanged statuses print every six observed ticks. Final worker
output includes `tick_counts` and `ticks_observed`. These count supervisor results,
not orders or fills. No account fields, raw upstream messages or credentials are
printed. A broken progress output stream does not interrupt recovery.

Normal idle monitoring still requires audit continuity within 60 seconds.
Explicit `--resume-idle` permits GET-only reconciliation of a never-traded first
cycle whose ORIGINAL baseline is at most 12 hours old. It requires no entry
reservations, execution history, closes, archives or emergency exits; current
account snapshots must be stable, flat and free of orders/algos, recent fills
must exactly match known baseline fills with the original anchor retained,
both complete USDT bill scans from the original baseline must be empty, and
cash must be unchanged. Any failed evidence blocks entries.

A successful recovery atomically appends a hashed-evidence receipt to
`demo_idle_resume` and refreshes the audit checkpoint in the current lease;
it does not change the baseline, risk counters, account binding or kill switch.
The trading supervisor separately requires explicit entry arming. This does not
recover ledgers with prior trading or baselines older than 12 hours.
Never delete the ledger, edit audit timestamps or initialize a second baseline.

After a no-order first trial, resume using the same pinned binding and ledger:

```bash
"$HOME/sinka-demo-venv/bin/python" "$SINKA_SETUP_DIR/backend/okx_demo_cloudshell.py" --run --resume-idle --seconds 900
```

This is a bounded **demo-order-enabled** trial, not a 24/7 deployment. It may
place simulated orders when a new strategy signal and all gates pass.

### Explicit 20-market mode

Use `--scan-markets 20` with the existing `--run --resume-idle --seconds 900`
trial. The default remains one market. The pinned account binding and shared
ledger are retained; do not create 20 account baselines or run 20 workers.

Candidate USDT linear swaps: BTC, ETH, SOL, XRP, DOGE, ADA, AVAX, LINK, DOT,
LTC, BCH, TRX, TON, SUI, NEAR, APT, ATOM, UNI, ETC, FIL. These are candidates,
not a claim that all 20 are currently listed, liquid, or price-stable. Public
instrument/ticker GETs verify at each scan: live linear USDT settlement, listing
age at least 90 days, valid sizing/tick limits, no scheduled metadata change,
approximate 24h USDT turnover at least 10 million (`volCcy24h * last`), bid/ask
spread at most 0.1%, and 24h high/low range at most 20%. Missing/invalid data
excludes a candidate. Fewer than 10 eligible markets is possible; filters are
never weakened to fill a quota. Thresholds are implementation choices, not
exchange guarantees or profitability claims.

Up to four public snapshot tasks run concurrently. The scanner has a bounded
eight-second request budget and a ten-second subprocess deadline; the child
process receives PATH only. Scan results report eligible/checked/no_signal/
expired/prestart/fetch_failed/signals counts. New candidates are chosen in
descending approximate turnover order, not expected return. Signals must still
pass the original 20-second age and 15-second quote limits at entry.

When flat, the supervisor checks the selected contract and its EXISTING isolated
net leverage (1..3x) using a GET-only reader. Unsupported account settings skip
that candidate for this run, reporting `market_preflight_blocked`; the worker
does not change leverage. Readers switch together under the same account lease
and reconciliation lock. Entry then repeats the full cash/fill/bill risk audit.
Any active intent stops further market scans, so the entire account still has
at most one strategy intent/position and one shared daily risk budget. A restart
in 20-market mode reads the active instrument from its durable intent BEFORE
recovery, not from the last public scanner output. The BTC field in the original
account binding remains the bootstrap market; it is never rewritten.

A 900-second test can end by reducing an owned position and require emergency
settlement review. That outcome is not 24/7 readiness. Intercepted tests exercise
altcoin entry, native protection, duplicate prevention and reduce-only shutdown;
actual OKX demo fills remain separate operator acceptance evidence.

```bash
"$HOME/sinka-demo-venv/bin/python" "$SINKA_SETUP_DIR/backend/okx_demo_cloudshell.py" --run --resume-idle --scan-markets 20 --seconds 900
```

### One-shot entry/exit acceptance through the SAME worker

`--run --resume-idle --test-entry --seconds 90` explicitly opts into a single
minimum-size BTC demo execution test without waiting for an SNR breakout. It
uses the existing account binding, SQLite ledger, execution lease, daily risk
budget, entry transport, native protection assessment and reduce-only shutdown.
No separate account, key, strategy database, deployment or service is created.
Do not run it alongside a scan trial; the shared lease prevents concurrent entry.

The source is labelled `DEMO-EXECUTION-TEST-1`; its plan carries
`purpose=execution_test`, and the stable account-scoped test identity is `once`.
The fixed long BTC size is the smallest valid contract quantity. Extra admission
caps: modelled notional <=50 USDT, margin <=min(10 USDT, 1% equity), modelled
fee/slippage-inclusive loss <=min(1 USDT, 0.1% equity). Stop is tick-rounded
approximately 1% below the quote and target exactly 2R. These are sizing models,
not guaranteed market fill/loss limits. Existing exchange/account risk checks
still apply. Both session and authority need explicit test admission; the
default worker cannot force entry. A normal existing strategy intent is preserved.

After the test entry result, the supervisor stops new entries and immediately
uses the normal bounded shutdown/recovery path. One-shot entry and exit
reservations survive crashes; unknown responses are queried, never resubmitted.
`test_roundtrip_verified=true` requires BOTH verified native protection and
filled tracked reduce-only exit with stable flat account and no pending orders
or algos. It does not fire either price trigger, prove strategy profitability,
or complete emergency fee/cash settlement. `flat_verified` refers to that
tracked exit proof, not simply an acknowledgement.

A successful test can return `flat_requires_emergency_settlement_review` with
`test_roundtrip_verified=true` and exit code 0: connectivity/roundtrip passed,
but strategy entry remains paused until the tracked emergency exit is settled.
The original baseline is NOT reset and the test is not a strategy-performance
sample. The actual account entry still counts against shared daily risk.
Do not delete the ledger or repeat the test to bypass an unresolved result.

```bash
"$HOME/sinka-demo-venv/bin/python" "$SINKA_SETUP_DIR/backend/okx_demo_cloudshell.py" --run --resume-idle --test-entry --seconds 90
```

### Recovery-only snapshots and interrupted test recovery

After a real demo entry, unrealized PnL can change USDT equity and available
margin between reads without changing the position. Recovery snapshots now
exclude only those two mark-sensitive values from equality checks. They still
require valid positive/bounded amounts, identical UID/config, leverage, specs,
position identities/quantities, fills, cash, orders and algos, and the original
five-second evidence budget. They carry `recovery_only=true`; the entry
authority expressly rejects them for any new order admission. Flat pre-entry
and idle/settlement readers retain their original strict equality checks.

`--run --recover-only --seconds 90` uses the existing account binding and tracked
intent. It never fetches an entry signal, initializes a baseline, arms entries
or calls `session.enter`. It keeps the kill switch on and uses bounded existing
owned-intent shutdown/recovery, including only an already-owned cancellation or
reduce-only exit where fresh quantity evidence permits it. Unknown writes are
never retried. With no active intent it reports `no_active_intent_no_submission`.
A manual close is not reported as a verified tracked reduce-only exit and still
requires fill/cash settlement review. Do not run an entry test after manually
closing to bypass the retained intent. Use `--check` for a GET-only flat/clean
account check, and keep all original records.
