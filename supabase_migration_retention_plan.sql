-- ============================================================================
-- 【僅為計畫，未經使用者確認，不得執行】
-- 戰情室 Supabase 容量保留計畫（2026-11-15 草擬，依據 db_retention.py 的 TABLES 常數）
-- 背景：資料庫 407MB，免費方案 500MB，超過即唯讀。
--
-- 使用方式：
--   1. 先由使用者決定要執行哪些動作，再逐行取消註解。
--   2. 所有刪除前，必須先用 db_retention.py --export 匯出並保存 manifest，列數與 sha256 對得上。
--   3. 刪除後不會立刻把磁碟空間還給系統，需要另行評估 VACUUM（本檔不含 VACUUM FULL）。
--   4. 整段包在 BEGIN … ROLLBACK 中，確認結果後才能把 ROLLBACK 改成 COMMIT（仍需使用者同意）。
--
-- 本檔所有語句皆已註解，直接執行不會有任何效果。
-- ============================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- A. 估算用查詢（唯讀，可先執行以確認列數與截止日）
-- ---------------------------------------------------------------------------
-- SELECT count(*), min(date), max(date) FROM public.inst_holding;
-- SELECT count(*) FROM public.inst_holding WHERE date < '2026-05-19';   -- 保留 180 天
-- SELECT count(*), min(log_date), max(log_date) FROM public.broker_flows;
-- SELECT count(*) FROM public.broker_flows WHERE log_date < '2026-08-17';   -- 保留 90 天
-- SELECT count(*), min(trade_date), max(trade_date) FROM public.twse_market_snapshot;
-- SELECT count(*) FROM public.twse_market_snapshot WHERE trade_date < '2025-10-11';   -- 保留 400 天
-- SELECT count(*), min(trade_date), max(trade_date) FROM public.market_signal_snapshot;
-- SELECT count(*) FROM public.market_signal_snapshot WHERE trade_date < '2026-09-16';   -- 保留 60 天

-- ---------------------------------------------------------------------------
-- B. 依保留期刪除舊列（需先匯出，且使用者確認）
--    截止日以 2026-11-15 計算；實際執行前請依當日重算。
-- ---------------------------------------------------------------------------
-- DELETE FROM public.inst_holding WHERE date < '2026-05-19';
-- DELETE FROM public.broker_flows WHERE log_date < '2026-08-17';
-- DELETE FROM public.twse_market_snapshot WHERE trade_date < '2025-10-11';
-- DELETE FROM public.market_signal_snapshot WHERE trade_date < '2026-09-16';

-- 注意：db_maintenance.py 已有每日自動刪除（檔頭註解指出隨 bundle_late 執行）。
-- 若該排程已在運作，上面四條可能只是補清積壓；執行前先確認 system_run_log 的清理紀錄。

-- ---------------------------------------------------------------------------
-- C. 備份表（倉庫內無參照，日期欄位 UNKNOWN，DROP 前須先確認內容與是否還需要）
--    建議先執行列數檢查，再決定是否保留匯出檔後 DROP。
-- ---------------------------------------------------------------------------
-- SELECT count(*) FROM public.backup_holiday_derived_20261014;
-- SELECT count(*) FROM public.backup_holiday_intraday_bars_20261005;
-- SELECT count(*) FROM public.backup_holiday_twse_snapshot_20261005;
-- SELECT count(*) FROM public.backup_instant_exit_artifacts_20261005;
-- DROP TABLE IF EXISTS public.backup_holiday_derived_20261014;
-- DROP TABLE IF EXISTS public.backup_holiday_intraday_bars_20261005;
-- DROP TABLE IF EXISTS public.backup_holiday_twse_snapshot_20261005;
-- DROP TABLE IF EXISTS public.backup_instant_exit_artifacts_20261005;

-- ---------------------------------------------------------------------------
-- D. 已移除功能的表（需使用者決定是否保留）
--    telegram_inbox_images 在寫入或退回後本應即刪；仍有列時先確認原因。
--    manual_feed_batches 倉庫內無參照，先確認表是否真的存在。
-- ---------------------------------------------------------------------------
-- SELECT count(*) FROM public.telegram_inbox;
-- SELECT count(*) FROM public.telegram_inbox_images;
-- SELECT count(*) FROM public.manual_feed_batches;
-- DELETE FROM public.telegram_inbox WHERE created_at < now() - interval '30 days';
-- DELETE FROM public.telegram_inbox_images WHERE created_at < now() - interval '30 days';
-- DROP TABLE IF EXISTS public.telegram_inbox_images;
-- DROP TABLE IF EXISTS public.telegram_inbox;
-- DROP TABLE IF EXISTS public.manual_feed_batches;

-- ---------------------------------------------------------------------------
-- E. 不在本計畫內（請勿加入）
--    - official_open_data：由 od_retention.py 負責分級保留與封存，避免重複處理。
--    - mops_financial_snapshot：需保留 10 季歷史，無日曆日期欄位。
--    - etf_price_daily、big_holder_weekly：保留期待使用者決定，未給天數前不動。
-- ---------------------------------------------------------------------------

ROLLBACK;  -- 預設回滾；確認後才能改為 COMMIT（未經使用者同意不得改）
