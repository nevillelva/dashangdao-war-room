-- 【2026-10-13】濾網回測結果的「前瞻天數」標示。
-- 欄位 win_rate_3d／avg_return_3d 的名稱沿用，但 R98續124 起內容語意已改成「5 個交易日」；
-- 舊資料的語意無法從資料庫或 git（歷史已壓縮成單一提交）判斷，所以分開看待：
--   horizon_days = 5    確定是 5 日語意（寫入時間在 repo 最早提交 2026-10-02 12:46+08 之後；之後排程會自行寫入 5）
--   horizon_days IS NULL 語意未驗證（可能是 3 日），畫面不使用
alter table public.filter_backtest_weekly_results add column if not exists horizon_days smallint;
update public.filter_backtest_weekly_results
   set horizon_days = 5
 where horizon_days is null and created_at >= '2026-10-02 04:46:09+00';
comment on column public.filter_backtest_weekly_results.horizon_days is
  '前瞻交易日數：5＝win_rate_3d／avg_return_3d 欄位內容為 5 日；NULL＝語意未驗證（2026-10-02 前的舊列，可能是 3 日）';
