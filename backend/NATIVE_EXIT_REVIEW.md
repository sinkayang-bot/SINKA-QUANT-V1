# 原生保護單出場唯讀查核

`okx_demo_cloudshell.py --audit-trade` 保留原帳本，先核對完整成交、帳單與現金，再使用既有 DemoCloseReader 取得五秒內兩輪穩定的原始進場單、原生 OCO 保護單與平倉子單。

只有既有嚴格 validate_lineage 通過，且進場單、平倉單與帳單查核所用資料完全一致，現金、持倉、掛單、保護單、合約與帳戶範圍仍一致，才回報 native_exit_verified=true。原生停損或停利是哪一種觸發，這個欄位不做區分。

這是額外的唯讀來源證據，不修改 automatic_exit_verified（原有的持久化緊急減倉驗證）、測試用途旗標或結算權限，不更改帳本，不歸檔，不發送交易或取消委託，不開放進場。

native_exit_stage 只顯示固定分類：bill_evidence_required、native_lineage_reads、audited_order_match、audited_snapshot_match、verified。無法驗證不能推論是人工平倉。額外查核失敗不撤銷已成功的帳單核對。

網頁與 Cloud Shell 執行器必須各自更新。重新載入網頁不會更新現有執行器；可從新程式目錄直接執行 --audit-trade，無需重啟策略。既有控制器必須保持閒置，勿同時送出其他動作。
